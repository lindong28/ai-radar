from __future__ import annotations

import fcntl
import multiprocessing
import os
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from urllib.parse import parse_qsl, urljoin, urlsplit


class BrowserProbeBusy(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserJourneyContract:
    selector: str
    action_selector: str | None = None


_CONTRACTS = {
    "homepage": BrowserJourneyContract(".item-row"),
    "wechat_list": BrowserJourneyContract(".wechat-card[data-detail-url]"),
    "wechat_detail": BrowserJourneyContract(".wechat-detail[data-item-id]"),
    "wechat_pagination": BrowserJourneyContract(".wechat-card[data-detail-url]", '#pagination [rel="next"]'),
}


def browser_journey_contract(target: str) -> BrowserJourneyContract:
    return _CONTRACTS[target]


def browser_stop_predicate(*, attached: bool, visible: bool, text: str, next_frame_visible: bool) -> bool:
    return attached and visible and bool(text.strip()) and next_frame_visible


@contextmanager
def browser_singleflight(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BrowserProbeBusy("skipped_overlap") from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class BrowserMeasurement:
    request_url: str
    value_ms: float
    hard_failure: bool
    outcome: str = "observed"
    incompatible_reason: str | None = None


def _measure_browser_journey_inner(
    *,
    base_url: str,
    target: str,
    detail_slug: str,
    timeout_seconds: float,
    lock_path: Path,
    expected: dict[str, object] | None = None,
) -> BrowserMeasurement:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    paths = {
        "homepage": "/",
        "wechat_list": "/wechat",
        "wechat_detail": f"/wechat/{detail_slug}",
        "wechat_pagination": "/wechat?page=1",
    }
    request_url = urljoin(base_url.rstrip("/") + "/", paths[target].lstrip("/"))
    contract = browser_journey_contract(target)
    timeout_ms = timeout_seconds * 1000
    started = perf_counter_ns()
    try:
        with browser_singleflight(lock_path), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            try:
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(request_url, wait_until="domcontentloaded", timeout=timeout_ms)
                locators = page.locator(contract.selector)
                locator = locators.first
                if contract.action_selector:
                    previous = locator.get_attribute("data-detail-url") or locator.inner_text()
                    page.locator(contract.action_selector).click()
                    page.wait_for_function(
                        "([selector, previous]) => { const e=document.querySelector(selector); "
                        "return e && e.offsetParent !== null && (e.dataset.detailUrl || e.textContent.trim()) !== previous; }",
                        arg=[contract.selector, previous],
                    )
                    locators = page.locator(contract.selector)
                    locator = locators.first
                locator.wait_for(state="visible")
                text = locator.inner_text()
                next_frame_visible = page.evaluate(
                    "selector => new Promise(resolve => requestAnimationFrame(() => { const e=document.querySelector(selector); "
                    "resolve(Boolean(e && e.offsetParent !== null)); }))",
                    contract.selector,
                )
                hard_failure = not browser_stop_predicate(
                    attached=locator.count() > 0,
                    visible=locator.is_visible(),
                    text=text,
                    next_frame_visible=bool(next_frame_visible),
                )
                if target in {"wechat_list", "wechat_pagination"}:
                    expected_slugs = None if expected is None else expected.get("slugs")
                    detail_urls = locators.evaluate_all(
                        "elements => elements.map(element => element.dataset.detailUrl || '')"
                    )
                    actual_slugs = [str(value).rsplit("/", 1)[-1].split("?", 1)[0] for value in detail_urls]
                    hard_failure = hard_failure or actual_slugs != expected_slugs
                elif target == "wechat_detail":
                    expected_id = None if expected is None else expected.get("item_id")
                    hard_failure = hard_failure or expected_id is None or locator.get_attribute("data-item-id") != str(expected_id)
                elif target == "homepage":
                    expected_ids = None if expected is None else expected.get("item_ids")
                    actual_ids = locators.evaluate_all(
                        "elements => elements.map(element => element.dataset.itemId || '')"
                    )
                    hard_failure = (
                        hard_failure
                        or not isinstance(expected_ids, list)
                        or not expected_ids
                        or actual_ids[: len(expected_ids)] != expected_ids
                    )
                if target == "wechat_pagination":
                    page_values = [
                        value
                        for key, value in parse_qsl(
                            urlsplit(page.url).query, keep_blank_values=True
                        )
                        if key == "page"
                    ]
                    hard_failure = hard_failure or page_values != ["2"]
                return BrowserMeasurement(request_url, (perf_counter_ns() - started) / 1_000_000, hard_failure)
            finally:
                context.close()
                browser.close()
    except BrowserProbeBusy:
        return BrowserMeasurement(
            request_url, (perf_counter_ns() - started) / 1_000_000, False, "skipped_overlap"
        )
    except PlaywrightTimeoutError:
        return BrowserMeasurement(request_url, (perf_counter_ns() - started) / 1_000_000, True)
    except (OSError, PlaywrightError, AttributeError):
        return BrowserMeasurement(
            request_url,
            (perf_counter_ns() - started) / 1_000_000,
            False,
            "incompatible",
            "browser_runtime:missing",
        )


def _browser_worker(sender: object, kwargs: dict[str, object]) -> None:
    try:
        result = _measure_browser_journey_inner(**kwargs)  # type: ignore[arg-type]
        sender.send(result)  # type: ignore[attr-defined]
    finally:
        sender.close()  # type: ignore[attr-defined]


def _kill_process_tree(root_pid: int) -> None:
    rows = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid="],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    parsed: list[tuple[int, int]] = []
    for row in rows:
        try:
            pid, parent = (int(value) for value in row.split())
        except (ValueError, TypeError):
            continue
        parsed.append((pid, parent))
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parsed:
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    for pid in sorted(descendants, reverse=True):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def measure_browser_journey(
    *,
    base_url: str,
    target: str,
    detail_slug: str,
    timeout_seconds: float,
    lock_path: Path,
    expected: dict[str, object] | None = None,
) -> BrowserMeasurement:
    request_url = urljoin(
        base_url.rstrip("/") + "/",
        {
            "homepage": "/",
            "wechat_list": "/wechat",
            "wechat_detail": f"/wechat/{detail_slug}",
            "wechat_pagination": "/wechat?page=1",
        }[target].lstrip("/"),
    )
    started = perf_counter_ns()
    receiver, sender = multiprocessing.get_context("spawn").Pipe(duplex=False)
    process = multiprocessing.get_context("spawn").Process(
        target=_browser_worker,
        args=(
            sender,
            {
                "base_url": base_url,
                "target": target,
                "detail_slug": detail_slug,
                "timeout_seconds": timeout_seconds,
                "lock_path": lock_path,
                "expected": expected,
            },
        ),
    )
    process.start()
    sender.close()
    try:
        if receiver.poll(timeout_seconds):
            result = receiver.recv()
            process.join(timeout=1)
            if isinstance(result, BrowserMeasurement):
                return result
        if process.is_alive():
            if process.pid is not None:
                _kill_process_tree(process.pid)
            else:
                process.kill()
        process.join(timeout=2)
        return BrowserMeasurement(
            request_url,
            (perf_counter_ns() - started) / 1_000_000,
            True,
        )
    finally:
        receiver.close()


def browser_runtime_available() -> bool:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            try:
                return True
            finally:
                context.close()
                browser.close()
    except Exception:
        return False
