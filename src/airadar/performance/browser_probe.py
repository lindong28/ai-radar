from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import wait as wait_for_multiprocessing
from time import perf_counter_ns
from urllib.parse import parse_qsl, urljoin, urlsplit


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
PROBE_INFRA_FAILURE_OUTCOME = "probe_infra_failure"
# Spawn plus Chromium/context/page setup happens before the page-level timeout starts.
# Keep enough bounded grace for that startup; even eight silent 20-second journeys
# remain bounded at 520 seconds, below the 15/16-minute whole-probe watchdogs.
BROWSER_STARTUP_GRACE_SECONDS = 45.0
# After the primary site+startup budget, wait for the OS to report either a
# published pipe result or worker exit. This is bounded independently so a
# silent live worker still cannot consume the whole-probe watchdog budget.
BROWSER_WORKER_EXIT_GRACE_SECONDS = 1.0
_BROWSER_RUNTIME_LOSS_MARKERS = (
    "target page, context or browser has been closed",
    "target closed",
    "browser closed",
    "page has been closed",
    "context has been closed",
    "browser has been closed",
)


def browser_journey_contract(target: str) -> BrowserJourneyContract:
    return _CONTRACTS[target]


def browser_stop_predicate(*, attached: bool, visible: bool, text: str, next_frame_visible: bool) -> bool:
    return attached and visible and bool(text.strip()) and next_frame_visible


@dataclass(frozen=True, slots=True)
class BrowserMeasurement:
    request_url: str
    value_ms: float
    hard_failure: bool
    outcome: str = "observed"
    incompatible_reason: str | None = None


def _probe_infra_failure(
    request_url: str,
    *,
    started: int,
    reason: str,
) -> BrowserMeasurement:
    return BrowserMeasurement(
        request_url,
        (perf_counter_ns() - started) / 1_000_000,
        False,
        PROBE_INFRA_FAILURE_OUTCOME,
        reason,
    )


def _is_browser_runtime_loss(error: BaseException) -> bool:
    if isinstance(error, BrokenPipeError | ConnectionResetError):
        return True
    message = str(error).casefold()
    return any(marker in message for marker in _BROWSER_RUNTIME_LOSS_MARKERS)


def _measure_browser_journey_inner(
    *,
    base_url: str,
    target: str,
    detail_slug: str,
    timeout_seconds: float,
    expected: dict[str, object] | None = None,
    _result_callback: Callable[[BrowserMeasurement], None] | None = None,
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
    finished_result: BrowserMeasurement | None = None

    def finish(result: BrowserMeasurement) -> BrowserMeasurement:
        nonlocal finished_result
        if finished_result is not None:
            return finished_result
        finished_result = result
        if _result_callback is not None:
            _result_callback(result)
        return result

    try:
        with sync_playwright() as playwright:
            browser = None
            context = None
            try:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(timeout_ms)
            except (OSError, PlaywrightError, AttributeError):
                if context is not None:
                    try:
                        context.close()
                    except (OSError, PlaywrightError):
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except (OSError, PlaywrightError):
                        pass
                return finish(
                    _probe_infra_failure(
                        request_url,
                        started=started,
                        reason="browser_runtime:launch_failed",
                    )
                )
            site_deadline_ns = perf_counter_ns() + int(timeout_seconds * 1_000_000_000)

            def remaining_site_timeout_ms() -> float:
                remaining_ms = (site_deadline_ns - perf_counter_ns()) / 1_000_000
                if remaining_ms <= 0:
                    raise PlaywrightTimeoutError("browser journey site timeout")
                page.set_default_timeout(remaining_ms)
                return remaining_ms

            try:
                page.goto(
                    request_url,
                    wait_until="domcontentloaded",
                    timeout=remaining_site_timeout_ms(),
                )
                locators = page.locator(contract.selector)
                locator = locators.first
                if contract.action_selector:
                    remaining_site_timeout_ms()
                    previous = locator.get_attribute("data-detail-url") or locator.inner_text()
                    remaining_site_timeout_ms()
                    page.locator(contract.action_selector).click()
                    page.wait_for_function(
                        "([selector, previous]) => { const e=document.querySelector(selector); "
                        "return e && e.offsetParent !== null && (e.dataset.detailUrl || e.textContent.trim()) !== previous; }",
                        arg=[contract.selector, previous],
                        timeout=remaining_site_timeout_ms(),
                    )
                    locators = page.locator(contract.selector)
                    locator = locators.first
                remaining_site_timeout_ms()
                locator.wait_for(state="visible")
                remaining_site_timeout_ms()
                text = locator.inner_text()
                page.wait_for_function(
                    "selector => new Promise(resolve => requestAnimationFrame(() => { const e=document.querySelector(selector); "
                    "resolve(Boolean(e && e.offsetParent !== null)); }))",
                    arg=contract.selector,
                    timeout=remaining_site_timeout_ms(),
                )
                next_frame_visible = True
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
                return finish(
                    BrowserMeasurement(
                        request_url,
                        (perf_counter_ns() - started) / 1_000_000,
                        hard_failure,
                    )
                )
            except PlaywrightTimeoutError:
                return finish(
                    BrowserMeasurement(
                        request_url,
                        (perf_counter_ns() - started) / 1_000_000,
                        True,
                    )
                )
            except PlaywrightError as error:
                if _is_browser_runtime_loss(error):
                    return finish(
                        _probe_infra_failure(
                            request_url,
                            started=started,
                            reason="browser_runtime:crashed",
                        )
                    )
                return finish(
                    BrowserMeasurement(
                        request_url,
                        (perf_counter_ns() - started) / 1_000_000,
                        True,
                    )
                )
            except OSError as error:
                if _is_browser_runtime_loss(error):
                    return finish(
                        _probe_infra_failure(
                            request_url,
                            started=started,
                            reason="browser_runtime:crashed",
                        )
                    )
                return finish(
                    BrowserMeasurement(
                        request_url,
                        (perf_counter_ns() - started) / 1_000_000,
                        True,
                    )
                )
            except AttributeError:
                return finish(
                    _probe_infra_failure(
                        request_url,
                        started=started,
                        reason="browser_runtime:invalid_api",
                    )
                )
            finally:
                try:
                    context.close()
                except (OSError, PlaywrightError):
                    pass
                try:
                    browser.close()
                except (OSError, PlaywrightError):
                    pass
    except (OSError, PlaywrightError, AttributeError):
        return finish(
            _probe_infra_failure(
                request_url,
                started=started,
                reason="browser_runtime:launch_failed",
            )
        )


def _browser_worker(sender: object, kwargs: dict[str, object]) -> None:
    os.setsid()
    sent = False

    def send_result(result: BrowserMeasurement) -> None:
        nonlocal sent
        sender.send(result)  # type: ignore[attr-defined]
        sent = True

    try:
        raw_expected = kwargs.get("expected")
        raw_timeout_seconds = kwargs["timeout_seconds"]
        if isinstance(raw_timeout_seconds, bool) or not isinstance(
            raw_timeout_seconds,
            int | float,
        ):
            raise TypeError("browser worker timeout_seconds must be numeric")
        result = _measure_browser_journey_inner(
            base_url=str(kwargs["base_url"]),
            target=str(kwargs["target"]),
            detail_slug=str(kwargs["detail_slug"]),
            timeout_seconds=float(raw_timeout_seconds),
            expected=raw_expected if isinstance(raw_expected, dict) else None,
            _result_callback=send_result,
        )
        if not sent:
            send_result(result)
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


_ACTIVE_BROWSER_WORKER_PIDS: set[int] = set()


def terminate_active_browser_workers() -> None:
    for pid in tuple(_ACTIVE_BROWSER_WORKER_PIDS):
        _kill_process_tree(pid)
        try:
            os.killpg(pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass


def measure_browser_journey(
    *,
    base_url: str,
    target: str,
    detail_slug: str,
    timeout_seconds: float,
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
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_browser_worker,
        args=(
            sender,
            {
                "base_url": base_url,
                "target": target,
                "detail_slug": detail_slug,
                "timeout_seconds": timeout_seconds,
                "expected": expected,
            },
        ),
    )
    try:
        process.start()
    except (OSError, RuntimeError):
        sender.close()
        receiver.close()
        return _probe_infra_failure(
            request_url,
            started=started,
            reason="browser_runtime:worker_start_failed",
        )
    if process.pid is not None:
        _ACTIVE_BROWSER_WORKER_PIDS.add(process.pid)
    sender.close()
    try:
        result: object = None
        if receiver.poll(timeout_seconds + BROWSER_STARTUP_GRACE_SECONDS):
            try:
                result = receiver.recv()
            except (EOFError, OSError):
                result = None
        else:
            # Accepted bounded-liveness tradeoff: a genuine site failure published
            # more than BROWSER_WORKER_EXIT_GRACE_SECONDS after this cutoff can
            # still be classified as worker_unavailable. That requires a scheduler
            # pause at the timeout boundary; waiting without a bound would violate
            # the 960s external and 15-minute in-process watchdog deadlines.
            wait_for_multiprocessing(
                [receiver, process.sentinel],
                timeout=BROWSER_WORKER_EXIT_GRACE_SECONDS,
            )
            if receiver.poll(0):
                try:
                    result = receiver.recv()
                except (EOFError, OSError):
                    result = None
        if result is not None:
            process.join(timeout=1)
            if isinstance(result, BrowserMeasurement):
                if process.is_alive():
                    if process.pid is not None:
                        _kill_process_tree(process.pid)
                    else:
                        process.kill()
                    process.join(timeout=2)
                return result
        if process.is_alive():
            if process.pid is not None:
                _kill_process_tree(process.pid)
            else:
                process.kill()
        process.join(timeout=2)
        return _probe_infra_failure(
            request_url,
            started=started,
            reason="browser_runtime:worker_unavailable",
        )
    finally:
        if process.pid is not None:
            _ACTIVE_BROWSER_WORKER_PIDS.discard(process.pid)
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
