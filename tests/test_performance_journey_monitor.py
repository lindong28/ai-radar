from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from airadar import db
from airadar.admin.alerts import AlertRuleResult, run_alert_results_state_machine
from airadar.egress import SelectorPolicy
from airadar.performance import browser_probe, journey_monitor
from airadar.performance.browser_probe import BrowserMeasurement, _measure_browser_journey_inner
from airadar.performance.journey_monitor import (
    CONFIRMATION_WINDOWS,
    RETENTION_DAYS,
    WARM_SAMPLES,
    JourneyMonitorRuntime,
    _load_alert_state,
    _probe_expectation,
    classify_pipeline_load,
    evaluate_performance_rules,
    probe_journeys,
    run_journey_monitor,
    run_performance_alerts,
    store_samples,
)

MIN_CONFIRMABLE_SAMPLES = WARM_SAMPLES + CONFIRMATION_WINDOWS - 1


@pytest.fixture(autouse=True)
def _healthy_selector_status(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = SelectorPolicy(
        agent_proxy="http://selector.invalid:1",
        policy_id="domain-routing-v1",
        policy_sha256="a" * 64,
    )
    monkeypatch.setattr("airadar.egress.require_selector_policy", lambda: policy)


def _delayed_site_timeout_worker(
    sender: object,
    kwargs: dict[str, object],
) -> None:
    os.setsid()
    try:
        timeout_seconds = float(kwargs["timeout_seconds"])
        time.sleep(timeout_seconds + 0.15)
        sender.send(  # type: ignore[attr-defined]
            BrowserMeasurement(
                request_url=str(kwargs["base_url"]),
                value_ms=(timeout_seconds + 0.15) * 1000,
                hard_failure=True,
            )
        )
    finally:
        sender.close()  # type: ignore[attr-defined]


def _site_timeout_then_stuck_cleanup_worker(
    sender: object,
    kwargs: dict[str, object],
) -> None:
    os.setsid()
    expected = kwargs.get("expected")
    if isinstance(expected, dict):
        Path(str(expected["pid_path"])).write_text(str(os.getpid()), encoding="utf-8")
    try:
        sender.send(  # type: ignore[attr-defined]
            BrowserMeasurement(
                request_url=str(kwargs["base_url"]),
                value_ms=float(kwargs["timeout_seconds"]) * 1000,
                hard_failure=True,
            )
        )
        time.sleep(60)
    finally:
        sender.close()  # type: ignore[attr-defined]


def _cutoff_site_result_worker(
    sender: object,
    kwargs: dict[str, object],
) -> None:
    os.setsid()
    expected = kwargs.get("expected")
    if not isinstance(expected, dict):
        raise TypeError("cutoff worker requires expected timing")
    try:
        time.sleep(float(expected["publish_delay"]))
        sender.send(  # type: ignore[attr-defined]
            BrowserMeasurement(
                request_url=str(kwargs["base_url"]),
                value_ms=float(kwargs["timeout_seconds"]) * 1000,
                hard_failure=True,
            )
        )
    finally:
        sender.close()  # type: ignore[attr-defined]


def _silent_browser_worker(
    sender: object,
    _kwargs: dict[str, object],
) -> None:
    os.setsid()
    try:
        time.sleep(60)
    finally:
        sender.close()  # type: ignore[attr-defined]


def test_performance_probe_help_points_to_launchd_installer() -> None:
    assert journey_monitor.LAUNCHD_INSTALL_HINT == "./install.sh performance-probe"
    result = subprocess.run(
        ["./run.sh", "performance-probe", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "launchd install: ./install.sh performance-probe" in result.stdout
    assert "crontab example:" not in result.stdout


def test_journey_state_reader_sees_page_after_double_firing_state_is_projected(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "double-firing-alert-state.json"
    started = "2026-07-22T08:00:00+08:00"
    state_path.write_text(
        json.dumps(
            {
                "PERF:double": {
                    "state": "firing",
                    "since": started,
                    "last_notified": started,
                    "detail": "notice projection",
                    "severity": "notice",
                    "announced": True,
                    "lifecycles": {
                        "page": {
                            "state": "firing",
                            "since": started,
                            "last_notified": started,
                            "detail": "confirmed page",
                            "announced": True,
                        },
                        "notice": {
                            "state": "firing",
                            "since": started,
                            "last_notified": started,
                            "detail": "notice projection",
                            "announced": True,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    run_alert_results_state_machine(
        [],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        send=lambda text, *, severity="page": pytest.fail("normalization must not send"),
    )
    entry = _load_alert_state(state_path)["PERF:double"]

    assert entry["state"] == "firing"
    assert entry["severity"] == "page"
    assert entry["detail"] == "confirmed page"


def test_probe_expectation_does_not_import_app_or_run_migrations(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    db_path = tmp_path / "expectation.db"
    db.migrate(db_path)
    sys.modules.pop("airadar.web.app", None)

    def forbidden_migration(path: object = None) -> None:
        raise AssertionError(f"probe attempted migration for {path}")

    monkeypatch.setattr(db, "migrate", forbidden_migration)

    assert _probe_expectation(db_path, "homepage", "unavailable") == {"item_ids": []}


def test_homepage_browser_identity_accepts_expected_prepaint_prefix_when_more_rows_render(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    expected_ids = [str(item_id) for item_id in range(1, 13)]
    rendered_ids = expected_ids + [str(item_id) for item_id in range(13, 41)]

    locator = SimpleNamespace(
        wait_for=lambda **_kwargs: None,
        inner_text=lambda: "first card",
        count=lambda: 1,
        is_visible=lambda: True,
    )
    locators = SimpleNamespace(
        first=locator,
        evaluate_all=lambda _script: rendered_ids,
    )
    page = SimpleNamespace(
        set_default_timeout=lambda _timeout: None,
        goto=lambda *_args, **_kwargs: None,
        locator=lambda _selector: locators,
        wait_for_function=lambda *_args, **_kwargs: None,
    )
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: browser))
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: nullcontext(playwright))

    measurement = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": expected_ids},
    )

    assert measurement.outcome == "observed"
    assert measurement.hard_failure is False

    rendered_ids[0] = "unexpected"
    mismatch = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": expected_ids},
    )

    assert mismatch.outcome == "observed"
    assert mismatch.hard_failure is True


def test_browser_launch_failure_is_non_firing_probe_infra_outcome(
    monkeypatch,
) -> None:  # noqa: ANN001
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(
            launch=lambda **_kwargs: (_ for _ in ()).throw(
                PlaywrightError("Executable doesn't exist")
            )
        )
    )
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: nullcontext(playwright),
    )

    measurement = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "probe_infra_failure"
    assert measurement.incompatible_reason == "browser_runtime:launch_failed"
    assert measurement.hard_failure is False


@pytest.mark.parametrize(
    "page_error",
    [
        PlaywrightTimeoutError("page did not load"),
        PlaywrightError("navigation failed"),
    ],
    ids=["timeout", "load-error"],
)
def test_page_failure_after_browser_launch_remains_observed_site_failure(
    monkeypatch,
    page_error: Exception,
) -> None:  # noqa: ANN001
    page = SimpleNamespace(
        set_default_timeout=lambda _timeout: None,
        goto=lambda *_args, **_kwargs: (_ for _ in ()).throw(page_error),
    )
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **_kwargs: browser)
    )
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: nullcontext(playwright),
    )

    measurement = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "observed"
    assert measurement.hard_failure is True


def test_browser_site_operations_share_one_inner_timeout_budget(
    monkeypatch,
) -> None:  # noqa: ANN001
    configured_timeouts: list[float] = []
    locator = SimpleNamespace(
        wait_for=lambda **_kwargs: time.sleep(0.02),
        inner_text=lambda: "first card",
        count=lambda: 1,
        is_visible=lambda: True,
    )
    locators = SimpleNamespace(
        first=locator,
        evaluate_all=lambda _script: ["1"],
    )
    page = SimpleNamespace(
        set_default_timeout=lambda timeout: configured_timeouts.append(timeout),
        goto=lambda *_args, **_kwargs: time.sleep(0.02),
        locator=lambda _selector: locators,
        wait_for_function=lambda *_args, **_kwargs: None,
    )
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **_kwargs: browser)
    )
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: nullcontext(playwright),
    )

    measurement = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=0.1,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "observed"
    assert measurement.hard_failure is False
    assert len(configured_timeouts) >= 3
    assert configured_timeouts[-1] < configured_timeouts[0] - 20


def test_browser_target_loss_after_launch_is_probe_infra_failure(
    monkeypatch,
) -> None:  # noqa: ANN001
    page = SimpleNamespace(
        set_default_timeout=lambda _timeout: None,
        goto=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PlaywrightError("Target page, context or browser has been closed")
        ),
    )
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **_kwargs: browser)
    )
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: nullcontext(playwright),
    )

    measurement = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "probe_infra_failure"
    assert measurement.incompatible_reason == "browser_runtime:crashed"
    assert measurement.hard_failure is False


def test_browser_driver_broken_pipe_after_launch_is_probe_infra_failure(
    monkeypatch,
) -> None:  # noqa: ANN001
    page = SimpleNamespace(
        set_default_timeout=lambda _timeout: None,
        goto=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            BrokenPipeError("playwright driver pipe closed")
        ),
    )
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **_kwargs: browser)
    )
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: nullcontext(playwright),
    )

    measurement = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "probe_infra_failure"
    assert measurement.incompatible_reason == "browser_runtime:crashed"
    assert measurement.hard_failure is False


def test_real_browser_wrapper_waits_for_inner_site_timeout_result(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        browser_probe,
        "_browser_worker",
        _delayed_site_timeout_worker,
    )

    measurement = browser_probe.measure_browser_journey(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=0.1,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "observed"
    assert measurement.hard_failure is True


def test_inner_publishes_site_result_before_browser_cleanup(
    monkeypatch,
) -> None:  # noqa: ANN001
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    delivered: list[BrowserMeasurement] = []
    page = SimpleNamespace(
        set_default_timeout=lambda _timeout: None,
        goto=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PlaywrightTimeoutError("site timed out")
        ),
    )

    def close_context() -> None:
        cleanup_started.set()
        assert release_cleanup.wait(timeout=2)

    context = SimpleNamespace(new_page=lambda: page, close=close_context)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **_kwargs: browser)
    )
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: nullcontext(playwright),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _measure_browser_journey_inner,
            base_url="https://public.invalid",
            target="homepage",
            detail_slug="unused",
            timeout_seconds=0.1,
            expected={"item_ids": ["1"]},
            _result_callback=delivered.append,
        )
        assert cleanup_started.wait(timeout=1)
        assert len(delivered) == 1
        assert delivered[0].outcome == "observed"
        assert delivered[0].hard_failure is True
        release_cleanup.set()
        assert future.result(timeout=1) == delivered[0]


def test_wrapper_kills_worker_that_stalls_during_cleanup_after_result(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    pid_path = tmp_path / "cleanup-worker.pid"
    monkeypatch.setattr(
        browser_probe,
        "_browser_worker",
        _site_timeout_then_stuck_cleanup_worker,
    )
    worker_pid: int | None = None
    try:
        measurement = browser_probe.measure_browser_journey(
            base_url="https://public.invalid",
            target="homepage",
            detail_slug="unused",
            timeout_seconds=0.1,
            expected={"pid_path": str(pid_path)},
        )
        worker_pid = int(pid_path.read_text(encoding="utf-8"))

        assert measurement.outcome == "observed"
        assert measurement.hard_failure is True
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
    finally:
        if worker_pid is not None:
            try:
                os.kill(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_wrapper_collects_site_result_published_after_primary_cutoff(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        browser_probe,
        "_browser_worker",
        _cutoff_site_result_worker,
    )
    monkeypatch.setattr(
        browser_probe,
        "BROWSER_STARTUP_GRACE_SECONDS",
        0.0,
    )
    monkeypatch.setattr(
        browser_probe,
        "BROWSER_WORKER_EXIT_GRACE_SECONDS",
        0.5,
        raising=False,
    )

    measurement = browser_probe.measure_browser_journey(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=0.05,
        expected={"publish_delay": 0.2},
    )

    assert measurement.outcome == "observed"
    assert measurement.hard_failure is True


def test_wrapper_classifies_truly_silent_worker_as_probe_infra(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        browser_probe,
        "_browser_worker",
        _silent_browser_worker,
    )
    monkeypatch.setattr(
        browser_probe,
        "BROWSER_STARTUP_GRACE_SECONDS",
        0.0,
    )
    monkeypatch.setattr(
        browser_probe,
        "BROWSER_WORKER_EXIT_GRACE_SECONDS",
        0.1,
        raising=False,
    )

    measurement = browser_probe.measure_browser_journey(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=0.05,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "probe_infra_failure"
    assert measurement.incompatible_reason == "browser_runtime:worker_unavailable"
    assert measurement.hard_failure is False


def test_finish_publishes_only_first_result_when_cleanup_raises(
    monkeypatch,
) -> None:  # noqa: ANN001
    published: list[BrowserMeasurement] = []
    locator = SimpleNamespace(
        wait_for=lambda **_kwargs: None,
        inner_text=lambda: "first card",
        count=lambda: 1,
        is_visible=lambda: True,
    )
    locators = SimpleNamespace(first=locator, evaluate_all=lambda _script: ["1"])
    page = SimpleNamespace(
        set_default_timeout=lambda _timeout: None,
        goto=lambda *_args, **_kwargs: None,
        locator=lambda _selector: locators,
        wait_for_function=lambda *_args, **_kwargs: None,
    )
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=lambda **_kwargs: browser)
    )

    @contextmanager
    def cleanup_fails() -> object:
        yield playwright
        raise PlaywrightError("cleanup failed")

    monkeypatch.setattr("playwright.sync_api.sync_playwright", cleanup_fails)

    measurement = _measure_browser_journey_inner(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": ["1"]},
        _result_callback=published.append,
    )

    assert len(published) == 1
    assert published[0].outcome == "observed"
    assert published[0].hard_failure is False
    assert measurement == published[0]


def test_browser_worker_start_failure_is_probe_infra_outcome(
    monkeypatch,
) -> None:  # noqa: ANN001
    receiver = SimpleNamespace(close=lambda: None)
    sender = SimpleNamespace(close=lambda: None)

    def fail_start() -> None:
        raise OSError("worker process unavailable")

    process = SimpleNamespace(start=fail_start)
    context = SimpleNamespace(
        Pipe=lambda **_kwargs: (receiver, sender),
        Process=lambda **_kwargs: process,
    )
    monkeypatch.setattr(
        browser_probe.multiprocessing,
        "get_context",
        lambda _method: context,
    )

    measurement = browser_probe.measure_browser_journey(
        base_url="https://public.invalid",
        target="homepage",
        detail_slug="unused",
        timeout_seconds=1,
        expected={"item_ids": ["1"]},
    )

    assert measurement.outcome == "probe_infra_failure"
    assert measurement.incompatible_reason == "browser_runtime:worker_start_failed"
    assert measurement.hard_failure is False


def test_pipeline_lock_classification_distinguishes_idle_busy_and_unknown(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    lock_path = tmp_path / ".pipeline.flock"
    # No anchor file yet: a pipeline that never ran is not running.
    assert classify_pipeline_load(lock_path) == "idle"

    lock_path.touch()
    assert classify_pipeline_load(lock_path) == "idle"

    with open(lock_path, "w", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        assert classify_pipeline_load(lock_path) == "busy"
    assert classify_pipeline_load(lock_path) == "idle"

    monkeypatch.setattr(
        "airadar.pipeline_lock.pipeline_lock_is_held",
        lambda _path: None,
    )
    monkeypatch.setattr(
        "airadar.performance.journey_monitor.pipeline_lock_is_held",
        lambda _path: None,
    )
    assert classify_pipeline_load(lock_path) == "unknown"


def test_pipeline_lock_probe_does_not_hold_the_lock(tmp_path: Path) -> None:
    # The observer probe must release immediately: after classification an
    # exclusive lock must still be acquirable (a probe that keeps a shared
    # lock would make the pipeline skip spuriously).
    lock_path = tmp_path / ".pipeline.flock"
    lock_path.touch()
    assert classify_pipeline_load(lock_path) == "idle"
    with open(lock_path, "w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_probe_runs_four_journeys_against_origin_and_public_and_stores_samples(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    lock_dir = tmp_path / ".pipeline.flock"
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="https://public.invalid",
        pipeline_lock_path=lock_dir,
        db_path=tmp_path / "radar.db",
    )
    calls: list[tuple[str, str]] = []

    def fake_measure(**kwargs: object) -> BrowserMeasurement:
        calls.append((str(kwargs["base_url"]), str(kwargs["target"])))
        return BrowserMeasurement(
            request_url=f"{kwargs['base_url']}/{kwargs['target']}",
            value_ms=float(len(calls) * 10),
            hard_failure=False,
        )

    monkeypatch.setattr("airadar.performance.journey_monitor.measure_browser_journey", fake_measure)
    samples = probe_journeys(runtime, observed_at=datetime(2026, 7, 18, tzinfo=UTC))
    sample_path = tmp_path / "journey-samples.jsonl"
    store_samples(sample_path, samples, now=datetime(2026, 7, 18, tzinfo=UTC))

    assert len(samples) == 8
    assert {sample.journey for sample in samples} == {
        "homepage.first_card",
        "wechat.list.first_card",
        "wechat.detail.readable",
        "wechat.pagination.settle",
    }
    assert {sample.vantage for sample in samples} == {"same_host_origin", "same_host_public"}
    assert all(sample.provisional is True for sample in samples)
    assert all(sample.load_class == "idle" for sample in samples)
    assert all(sample.value_ms > 0 for sample in samples)
    assert all("east_asia" not in sample.vantage for sample in samples)
    stored = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()]
    assert len(stored) == 8
    assert {row["journey"] for row in stored} == {sample.journey for sample in samples}


def test_probe_skips_public_vantage_when_public_url_empty(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )

    def fake_measure(**kwargs: object) -> BrowserMeasurement:
        return BrowserMeasurement(
            request_url=f"{kwargs['base_url']}/{kwargs['target']}",
            value_ms=10.0,
            hard_failure=False,
        )

    monkeypatch.setattr("airadar.performance.journey_monitor.measure_browser_journey", fake_measure)
    samples = probe_journeys(runtime, observed_at=datetime(2026, 7, 18, tzinfo=UTC))

    assert len(samples) == 4
    assert {sample.vantage for sample in samples} == {"same_host_origin"}


@pytest.mark.parametrize("load_class", ["busy", "unknown"])
def test_probe_skips_non_idle_load_before_measurement(
    monkeypatch,
    tmp_path: Path,
    load_class: str,
) -> None:  # noqa: ANN001
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )
    calls: list[object] = []
    monkeypatch.setattr(
        "airadar.performance.journey_monitor.classify_pipeline_load",
        lambda _path: load_class,
    )
    monkeypatch.setattr(
        "airadar.performance.journey_monitor.measure_browser_journey",
        lambda **kwargs: calls.append(kwargs),
    )

    samples = probe_journeys(runtime, observed_at=datetime(2026, 7, 18, tzinfo=UTC))

    assert samples == []
    assert calls == []


def test_probe_discards_interval_that_stops_being_idle(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )
    classifications = 0

    def classify(_path: Path) -> str:
        nonlocal classifications
        classifications += 1
        return "idle" if classifications == 1 else "busy"

    monkeypatch.setattr(
        "airadar.performance.journey_monitor.classify_pipeline_load",
        classify,
    )
    monkeypatch.setattr(
        "airadar.performance.journey_monitor.measure_browser_journey",
        lambda **kwargs: BrowserMeasurement(
            request_url=str(kwargs["base_url"]),
            value_ms=10.0,
            hard_failure=False,
        ),
    )

    samples = probe_journeys(runtime, observed_at=datetime(2026, 7, 18, tzinfo=UTC))

    assert samples == []


def test_probe_discards_skipped_overlap_instead_of_recording_hard_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )
    monkeypatch.setattr(
        "airadar.performance.journey_monitor.measure_browser_journey",
        lambda **kwargs: BrowserMeasurement(
            request_url=str(kwargs["base_url"]),
            value_ms=10.0,
            hard_failure=False,
            outcome="skipped_overlap",
        ),
    )

    samples = probe_journeys(runtime, observed_at=datetime(2026, 7, 18, tzinfo=UTC))

    assert samples == []


def test_probe_infra_failure_is_persisted_and_never_fires_site_alert(
    monkeypatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:  # noqa: ANN001
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )
    monkeypatch.setattr(
        "airadar.performance.journey_monitor.measure_browser_journey",
        lambda **kwargs: BrowserMeasurement(
            request_url=str(kwargs["base_url"]),
            value_ms=10.0,
            hard_failure=False,
            outcome="probe_infra_failure",
            incompatible_reason="browser_runtime:launch_failed",
        ),
    )
    started = datetime(2026, 7, 18, tzinfo=UTC)

    with caplog.at_level("ERROR"):
        probed = probe_journeys(runtime, observed_at=started)

    assert len(probed) == len(journey_monitor.JOURNEY_SPECS)
    assert all(sample.outcome == "probe_infra_failure" for sample in probed)
    assert all(sample.hard_failure is False for sample in probed)
    assert "browser_runtime:launch_failed" in caplog.text

    infra_rows = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=10,
            hard_failure=False,
            outcome="probe_infra_failure",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    sample_path = tmp_path / "journey-samples.jsonl"
    store_samples(
        sample_path,
        infra_rows,
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
    )
    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        event_path=tmp_path / "alert-events.jsonl",
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=runtime.pipeline_lock_path,
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
        send=lambda _text, *, severity="page", **_kwargs: pytest.fail(
            "probe infra outcome must not send a site-performance alert"
        ),
    )

    assert result["sent_count"] == 0
    assert result["results"] == []
    stored = [
        json.loads(line)
        for line in sample_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {row["outcome"] for row in stored} == {"probe_infra_failure"}


def test_probe_discards_real_pipeline_activity_hidden_between_idle_endpoints(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    pipeline_script = tmp_path / "pipeline.sh"
    shutil.copy(Path.cwd() / "pipeline.sh", pipeline_script)
    runner = tmp_path / "run.sh"
    runner.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    runner.chmod(0o755)
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )

    def fake_measure(**kwargs: object) -> BrowserMeasurement:
        completed = subprocess.run(
            [str(pipeline_script)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert completed.returncode == 0, completed.stderr
        # The anchor file persists after a run; what proves the pipeline is
        # gone is that its exclusive flock has been released.
        assert journey_monitor.pipeline_lock_is_held(runtime.pipeline_lock_path) is False
        assert (tmp_path / ".pipeline.activity").exists()
        return BrowserMeasurement(
            request_url=str(kwargs["base_url"]),
            value_ms=10.0,
            hard_failure=False,
        )

    monkeypatch.setattr(
        "airadar.performance.journey_monitor.measure_browser_journey",
        fake_measure,
    )

    samples = probe_journeys(runtime, observed_at=datetime(2026, 7, 18, tzinfo=UTC))

    assert samples == []


def test_legacy_browser_lock_holder_does_not_suppress_measurement(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    browser_lock_path = tmp_path / "browser.lock"
    browser_lock_path.touch()
    held_lock = browser_lock_path.open("a+")
    fcntl.flock(held_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    locator = SimpleNamespace(
        wait_for=lambda **_kwargs: None,
        inner_text=lambda: "first card",
        count=lambda: 1,
        is_visible=lambda: True,
    )
    locators = SimpleNamespace(first=locator, evaluate_all=lambda _script: ["1"])
    page = SimpleNamespace(
        set_default_timeout=lambda _timeout: None,
        goto=lambda *_args, **_kwargs: None,
        locator=lambda _selector: locators,
        wait_for_function=lambda *_args, **_kwargs: None,
    )
    context = SimpleNamespace(new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda: context, close=lambda: None)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: browser))
    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: nullcontext(playwright))
    try:
        measurement = _measure_browser_journey_inner(
            base_url="http://origin.invalid",
            target="homepage",
            detail_slug="unused",
            timeout_seconds=1,
            expected={"item_ids": ["1"]},
        )
    finally:
        fcntl.flock(held_lock, fcntl.LOCK_UN)
        held_lock.close()

    assert measurement.outcome == "observed"
    assert measurement.hard_failure is False


def test_store_samples_trims_rows_older_than_fourteen_days(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    existing = [
        _sample(observed_at=now - timedelta(days=RETENTION_DAYS, seconds=1), value_ms=10),
        _sample(observed_at=now - timedelta(days=RETENTION_DAYS), value_ms=20),
        _sample(observed_at=now - timedelta(days=1), value_ms=30),
    ]
    sample_path.write_text(
        "".join(json.dumps(row) + "\n" for row in existing),
        encoding="utf-8",
    )

    store_samples(sample_path, [_sample(observed_at=now, value_ms=40)], now=now)

    rows = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()]
    assert [row["value_ms"] for row in rows] == [20, 30, 40]


def test_store_samples_skips_corrupt_input_line(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    valid = _sample(observed_at=now, value_ms=10)
    sample_path.write_text(
        json.dumps(valid) + "\n}\n" + json.dumps(valid) + "\n",
        encoding="utf-8",
    )

    store_samples(sample_path, [_sample(observed_at=now, value_ms=20)], now=now)

    rows = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()]
    assert [row["value_ms"] for row in rows] == [10, 10, 20]


def test_store_and_evaluator_drop_malformed_and_future_timestamps(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    malformed = _sample(observed_at=now, value_ms=4000)
    malformed["observed_at"] = "not-a-timestamp"
    naive = _sample(observed_at=now, value_ms=4000)
    naive["observed_at"] = "2026-07-24T12:00:00"
    future = _sample(observed_at=now + timedelta(days=30), value_ms=4000)
    valid = _sample(observed_at=now, value_ms=100)

    store_samples(sample_path, [malformed, naive, future, valid], now=now)

    rows = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [valid]
    assert evaluate_performance_rules([malformed, naive, future], now=now) == []


def test_store_samples_concurrent_writers_keep_all_rows_and_valid_json(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    writers = 8
    barrier = threading.Barrier(writers)
    stale = _sample(observed_at=now - timedelta(days=RETENTION_DAYS, seconds=1), value_ms=-1)
    sample_path.write_text(
        (json.dumps(stale) + "\n") * 20_000 + "{corrupt\n",
        encoding="utf-8",
    )

    def write(index: int) -> tuple[int, bool | None]:
        barrier.wait()
        return (
            index,
            store_samples(
                sample_path,
                [_sample(observed_at=now + timedelta(seconds=index), value_ms=float(index))],
                now=now + timedelta(seconds=writers),
            ),
        )

    with ThreadPoolExecutor(max_workers=writers) as executor:
        futures = [executor.submit(write, index) for index in range(writers)]
        outcomes = [future.result() for future in futures]

    for index, outcome in outcomes:
        if outcome is not None:
            continue
        assert (
            store_samples(
                sample_path,
                [_sample(observed_at=now + timedelta(seconds=index), value_ms=float(index))],
                now=now + timedelta(seconds=writers),
            )
            is not None
        )

    rows = [json.loads(line) for line in sample_path.read_text(encoding="utf-8").splitlines()]
    assert sorted(row["value_ms"] for row in rows) == [float(index) for index in range(writers)]


def test_store_samples_retries_short_append_writes(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    sample_path = tmp_path / "journey-samples.jsonl"
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    original_write = os.write
    calls = 0

    def short_once(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            prefix = payload[: max(1, len(payload) // 2)]
            return original_write(descriptor, prefix)
        return original_write(descriptor, payload)

    monkeypatch.setattr(journey_monitor.os, "write", short_once)

    store_samples(sample_path, [_sample(observed_at=now, value_ms=10)], now=now)

    assert calls >= 2
    assert [json.loads(line)["value_ms"] for line in sample_path.read_text().splitlines()] == [10]


def test_evaluator_orders_overlapping_round_samples_by_observed_at() -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    older_high = [
        _sample(observed_at=started + timedelta(minutes=index), value_ms=4000)
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    newer_low = [
        _sample(
            observed_at=started + timedelta(hours=1, minutes=index),
            value_ms=100,
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]

    [result] = evaluate_performance_rules(
        [*newer_low, *older_high],
        now=started + timedelta(hours=2),
    )

    assert result.firing is False
    assert result.values["p95_ms"] == 100


def test_writing_firing_evidence_removes_evidence_older_than_fourteen_days(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    sample_path = tmp_path / "journey-samples.jsonl"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    old = evidence_dir / "old.json"
    old.write_text(
        json.dumps({"created_at": (now - timedelta(days=RETENTION_DAYS, seconds=1)).isoformat()}),
        encoding="utf-8",
    )
    boundary = evidence_dir / "boundary.json"
    boundary.write_text(
        json.dumps({"created_at": (now - timedelta(days=RETENTION_DAYS)).isoformat()}),
        encoding="utf-8",
    )
    rows = [
        _sample(observed_at=now - timedelta(minutes=21 - index), value_ms=4000)
        for index in range(22)
    ]
    store_samples(sample_path, rows, now=now)
    monkeypatch.setattr(
        "airadar.admin.alerts.send_alert_message",
        lambda text, *, severity="page", **_kwargs: {"skipped": False},
    )

    run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        event_path=Path(tmp_path / "state.json").with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now,
    )

    assert not old.exists()
    assert boundary.exists()
    assert len(list(evidence_dir.glob("*.json"))) == 2


def _sample(
    *,
    observed_at: datetime,
    value_ms: float,
    load_class: str = "idle",
    journey: str = "homepage.first_card",
    vantage: str = "same_host_public",
    hard_failure: bool = False,
    outcome: str = "observed",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "journey": journey,
        "target": "homepage",
        "vantage": vantage,
        "provisional": True,
        "load_class": load_class,
        "value_ms": value_ms,
        "hard_failure": hard_failure,
        "outcome": outcome,
        "request_url": "https://public.invalid/",
    }


def _cell_samples(
    *,
    started: datetime,
    journey: str,
    load_class: str,
    value_ms: float,
    minute_offset: int = 0,
    vantage: str = "same_host_origin",
) -> list[dict[str, object]]:
    return [
        _sample(
            observed_at=started + timedelta(minutes=minute_offset + index),
            value_ms=value_ms,
            load_class=load_class,
            journey=journey,
            vantage=vantage,
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]



def test_performance_alert_requires_three_advanced_warm_windows_and_resolves(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "journey-alert-state.json"
    evidence_dir = tmp_path / "evidence"
    lock_dir = tmp_path / ".pipeline.flock"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    sent: list[str] = []

    def sender(
        text: str,
        *,
        severity: str = "page",
        **_kwargs: object,
    ) -> dict[str, object]:
        sent.append(text)
        return {"skipped": False}

    monkeypatch.setattr("airadar.admin.alerts.send_alert_message", sender)

    high = [_sample(observed_at=started + timedelta(minutes=index), value_ms=4000) for index in range(20)]
    store_samples(sample_path, high, now=started + timedelta(minutes=20))
    first = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=lock_dir,
        now=started + timedelta(minutes=20),
    )
    assert first["sent_count"] == 0

    store_samples(
        sample_path,
        [_sample(observed_at=started + timedelta(minutes=20), value_ms=4000)],
        now=started + timedelta(minutes=21),
    )
    second = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=lock_dir,
        now=started + timedelta(minutes=21),
    )
    assert second["sent_count"] == 0

    store_samples(
        sample_path,
        [_sample(observed_at=started + timedelta(minutes=21), value_ms=4000)],
        now=started + timedelta(minutes=22),
    )
    confirmed = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=lock_dir,
        now=started + timedelta(minutes=22),
    )
    assert confirmed["sent_count"] == 1
    assert "firing" in confirmed["sent"][0]["type"]
    assert len(list(evidence_dir.glob("*.json"))) == 1
    evidence = json.loads(next(evidence_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert evidence["load_class"] == "idle"
    assert len(evidence["recent_samples"]) == 22
    assert {"git_sha", "git_dirty", "host_cpu_percent", "pipeline"} <= set(evidence["diagnostics"])

    # Unknown samples never enter a compliance stream or advance its windows.
    store_samples(
        sample_path,
        [_sample(observed_at=started + timedelta(minutes=22), value_ms=100, load_class="unknown")],
        now=started + timedelta(minutes=23),
    )
    unchanged = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=lock_dir,
        now=started + timedelta(minutes=23),
    )
    assert unchanged["sent_count"] == 0

    low = [
        _sample(observed_at=started + timedelta(minutes=23 + index), value_ms=100)
        for index in range(22)
    ]
    store_samples(sample_path, low, now=started + timedelta(minutes=46))
    resolved = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=lock_dir,
        now=started + timedelta(minutes=46),
    )
    assert resolved["sent_count"] == 1
    assert resolved["sent"][0]["type"] == "resolved"
    assert len(sent) == 2


def test_performance_rules_emit_only_idle_cells_and_never_busy_rollup(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = _cell_samples(
        started=started,
        journey="homepage.first_card",
        load_class="busy",
        value_ms=4000,
    ) + _cell_samples(
        started=started,
        journey="homepage.first_card",
        load_class="idle",
        value_ms=100,
        minute_offset=100,
    )
    store_samples(sample_path, samples, now=started + timedelta(minutes=200))

    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        event_path=Path(tmp_path / "state.json").with_name("alert-events.jsonl"),
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=started + timedelta(minutes=200),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert result["sent"] == []
    assert result["ruleset"] == ["PERF:homepage.first_card:same_host_origin:idle"]
    assert all(row["values"]["load_class"] == "idle" for row in result["results"])
    assert not any(":busy" in row["rule_id"] for row in result["results"])
    assert "PERF:rollup:busy" not in result["ruleset"]

    idle_firing = evaluate_performance_rules(
        _cell_samples(
            started=started,
            journey="homepage.first_card",
            load_class="idle",
            value_ms=4000,
        )
        + _cell_samples(
            started=started,
            journey="homepage.first_card",
            load_class="busy",
            value_ms=4000,
            minute_offset=100,
        ),
        now=started + timedelta(minutes=200),
    )
    assert len(idle_firing) == 1
    assert idle_firing[0].rule_id == "PERF:homepage.first_card:same_host_origin:idle"
    assert idle_firing[0].firing is True
    assert idle_firing[0].severity == "page"


def test_observed_site_measurement_failures_still_fire_performance_page() -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    site_failures = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=10,
            hard_failure=True,
            outcome="observed",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]

    [result] = evaluate_performance_rules(
        site_failures,
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
    )

    assert result.rule_id == "PERF:homepage.first_card:same_host_origin:idle"
    assert result.firing is True
    assert result.severity == "page"


def test_latest_probe_infra_outcome_holds_existing_site_firing_state(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    site_failures = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=10,
            hard_failure=True,
            outcome="observed",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    store_samples(
        sample_path,
        site_failures,
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
    )
    sent: list[str] = []
    kwargs = {
        "sample_path": sample_path,
        "state_path": state_path,
        "event_path": tmp_path / "alert-events.jsonl",
        "evidence_dir": tmp_path / "evidence",
        "pipeline_lock_path": tmp_path / ".pipeline.flock",
        "send": (
            lambda text, *, severity="page", **_kwargs: (
                sent.append(text) or {"skipped": False}
            )
        ),
    }
    firing = run_performance_alerts(
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
        **kwargs,
    )
    assert firing["sent_count"] == 1

    store_samples(
        sample_path,
        [
            _sample(
                observed_at=started
                + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES + 1),
                value_ms=10,
                hard_failure=False,
                outcome="probe_infra_failure",
                vantage="same_host_origin",
            )
        ],
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES + 2),
    )
    held = run_performance_alerts(
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES + 2),
        **kwargs,
    )

    assert held["sent_count"] == 0
    assert len(sent) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "PERF:homepage.first_card:same_host_origin:idle"
    ]["state"] == "firing"


def test_probe_infra_hold_retries_pending_site_firing_delivery(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    legacy_incompatible = _sample(
        observed_at=started - timedelta(minutes=1),
        value_ms=10,
        hard_failure=True,
        outcome="incompatible",
        vantage="same_host_origin",
    )
    site_failures = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=10,
            hard_failure=True,
            outcome="observed",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    store_samples(
        sample_path,
        [legacy_incompatible, *site_failures],
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
    )
    deliveries = iter(
        [
            {"skipped": True},
            {"skipped": True},
            {"skipped": False},
        ]
    )
    kwargs = {
        "sample_path": sample_path,
        "state_path": state_path,
        "event_path": tmp_path / "alert-events.jsonl",
        "evidence_dir": tmp_path / "evidence",
        "pipeline_lock_path": tmp_path / ".pipeline.flock",
        "send": lambda _text, *, severity="page", **_kwargs: next(deliveries),
    }

    pending = run_performance_alerts(
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
        **kwargs,
    )
    assert pending["sent_count"] == 1
    assert pending["sent"][0]["delivered"] is False
    assert json.loads(state_path.read_text(encoding="utf-8"))[rule_id][
        "firing_basis"
    ] == "observed"

    infra_started = started + timedelta(days=2)
    latest_infra = _sample(
        observed_at=infra_started,
        value_ms=10,
        hard_failure=False,
        outcome="incompatible",
        vantage="same_host_origin",
    )
    store_samples(sample_path, [latest_infra], now=infra_started)
    first_infra_retry = run_performance_alerts(
        now=infra_started + timedelta(minutes=1),
        **kwargs,
    )
    assert first_infra_retry["sent_count"] == 1
    assert first_infra_retry["sent"][0]["delivered"] is False

    after_retention = started + timedelta(days=15)
    store_samples(sample_path, [], now=after_retention)
    held_after_retention = run_performance_alerts(
        now=after_retention,
        **kwargs,
    )

    assert held_after_retention["sent_count"] == 1
    assert held_after_retention["sent"][0]["type"] == "firing"
    assert held_after_retention["sent"][0]["delivered"] is True
    retained = json.loads(state_path.read_text(encoding="utf-8"))[
        "PERF:homepage.first_card:same_host_origin:idle"
    ]
    assert retained["state"] == "firing"
    assert retained["announced"] is True
    assert retained["firing_basis"] == "observed"


def test_legacy_incompatible_samples_hold_existing_site_firing_state(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    site_failures = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=10,
            hard_failure=True,
            outcome="observed",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    store_samples(
        sample_path,
        site_failures,
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
    )
    sent: list[str] = []
    kwargs = {
        "sample_path": sample_path,
        "state_path": state_path,
        "event_path": tmp_path / "alert-events.jsonl",
        "evidence_dir": tmp_path / "evidence",
        "pipeline_lock_path": tmp_path / ".pipeline.flock",
        "send": (
            lambda text, *, severity="page", **_kwargs: (
                sent.append(text) or {"skipped": False}
            )
        ),
    }
    firing = run_performance_alerts(
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
        **kwargs,
    )
    assert firing["sent_count"] == 1

    legacy_infra = [
        _sample(
            observed_at=started
            + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES + 1 + index),
            value_ms=10,
            hard_failure=False,
            outcome="incompatible",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    store_samples(
        sample_path,
        legacy_infra,
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES * 2 + 2),
    )
    held = run_performance_alerts(
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES * 2 + 2),
        **kwargs,
    )

    assert held["sent_count"] == 0
    assert len(sent) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))[
        "PERF:homepage.first_card:same_host_origin:idle"
    ]["state"] == "firing"


def test_resolved_no_basis_firing_rearms_from_fresh_observed_samples(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    current = started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    run_alert_results_state_machine(
        [
            AlertRuleResult(
                rule_id=rule_id,
                title="unstamped historical alert",
                firing=True,
                detail="unstamped historical firing",
                action="inspect",
            )
        ],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=lambda _text, *, severity="page": {"skipped": False},
    )
    store_samples(
        sample_path,
        [
            _sample(
                observed_at=current,
                value_ms=10,
                hard_failure=False,
                outcome="incompatible",
                vantage="same_host_origin",
            )
        ],
        now=current,
    )
    retired = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=current + timedelta(minutes=1),
        send=lambda _text, *, severity="page", **_kwargs: {"skipped": False},
    )

    assert [receipt["type"] for receipt in retired["sent"]] == ["resolved"]
    assert json.loads(state_path.read_text(encoding="utf-8"))[rule_id][
        "state"
    ] == "ok"
    fresh_started = current + timedelta(minutes=2)
    store_samples(
        sample_path,
        [
            _sample(
                observed_at=fresh_started + timedelta(minutes=index),
                value_ms=10,
                hard_failure=True,
                outcome="observed",
                vantage="same_host_origin",
            )
            for index in range(MIN_CONFIRMABLE_SAMPLES)
        ],
        now=fresh_started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
    )

    rearmed = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=fresh_started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
        send=lambda _text, *, severity="page", **_kwargs: {"skipped": False},
    )

    assert [receipt["type"] for receipt in rearmed["sent"]] == ["firing"]
    state = json.loads(state_path.read_text(encoding="utf-8"))[rule_id]
    assert state["state"] == "firing"
    assert state["firing_basis"] == "observed"
    assert state["lifecycles"]["page"]["firing_basis"] == "observed"


@pytest.mark.parametrize(
    "initial_delivered",
    [False, True],
    ids=["pending", "announced"],
)
@pytest.mark.parametrize(
    "new_probe_marker",
    [False, True],
    ids=["incompatible-latest", "new-infra-latest"],
)
def test_no_basis_firing_intent_is_cleared_without_replay(
    tmp_path: Path,
    initial_delivered: bool,
    new_probe_marker: bool,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    current = started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    observed_history = [
        _sample(
            observed_at=started - timedelta(minutes=MIN_CONFIRMABLE_SAMPLES - index),
            value_ms=10,
            hard_failure=True,
            outcome="observed",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    legacy_infra = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=10,
            hard_failure=True,
            outcome="incompatible",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    store_samples(sample_path, [*observed_history, *legacy_infra], now=current)
    unstamped_firing = AlertRuleResult(
        rule_id=rule_id,
        title="旅程性能退化 homepage.first_card",
        firing=True,
        detail="unstamped historical performance alert",
        action="inspect probe runtime",
        severity="page",
    )
    seeded = run_alert_results_state_machine(
        [unstamped_firing],
        state_path=state_path,
        event_path=tmp_path / "alert-events.jsonl",
        now=current,
        send=lambda _text, *, severity="page": {
            "skipped": not initial_delivered
        },
    )
    assert seeded["sent_count"] == 1
    assert seeded["sent"][0]["delivered"] is initial_delivered
    if new_probe_marker:
        store_samples(
            sample_path,
            [
                _sample(
                    observed_at=current + timedelta(seconds=30),
                    value_ms=10,
                    hard_failure=False,
                    outcome="probe_infra_failure",
                    vantage="same_host_origin",
                )
            ],
            now=current + timedelta(seconds=30),
        )
    deploy_receipts: list[str] = []

    deployed = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=tmp_path / "alert-events.jsonl",
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=current + timedelta(minutes=1),
        send=lambda _text, *, severity="page", **_kwargs: (
            deploy_receipts.append(severity) or {"skipped": False}
        ),
    )

    assert not any(receipt["type"] == "firing" for receipt in deployed["sent"])
    assert deploy_receipts == (["page"] if initial_delivered else [])
    assert json.loads(state_path.read_text(encoding="utf-8"))[rule_id][
        "state"
    ] == "ok"


def test_no_basis_firing_without_evaluation_metadata_is_retired(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    current = started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    seeded = run_alert_results_state_machine(
        [
            AlertRuleResult(
                rule_id=rule_id,
                title="legacy performance alert",
                firing=True,
                detail="pre-field firing",
                action="inspect",
            )
        ],
        state_path=state_path,
        event_path=event_path,
        now=current,
        send=lambda _text, *, severity="page": {"skipped": False},
    )
    assert seeded["sent_count"] == 1
    legacy_state = json.loads(state_path.read_text(encoding="utf-8"))
    legacy_state[rule_id].pop("last_evaluated_at", None)
    legacy_state[rule_id].pop("last_evaluation_sequence", None)
    state_path.write_text(json.dumps(legacy_state), encoding="utf-8")
    store_samples(
        sample_path,
        [
            _sample(
                observed_at=current,
                value_ms=10,
                hard_failure=False,
                outcome="incompatible",
                vantage="same_host_origin",
            )
        ],
        now=current,
    )
    receipts: list[str] = []

    deployed = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=current + timedelta(minutes=1),
        send=lambda _text, *, severity="page", **_kwargs: (
            receipts.append(severity) or {"skipped": False}
        ),
    )

    assert [receipt["type"] for receipt in deployed["sent"]] == ["resolved"]
    assert receipts == ["page"]
    assert json.loads(state_path.read_text(encoding="utf-8"))[rule_id][
        "state"
    ] == "ok"


def test_historical_journey_samples_replay_smoke_is_idle_only_end_to_end(
    tmp_path: Path,
) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "alert-state.json"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    replayed_samples = _cell_samples(
        started=started,
        journey="homepage.first_card",
        load_class="idle",
        value_ms=4000,
    ) + _cell_samples(
        started=started,
        journey="homepage.first_card",
        load_class="busy",
        value_ms=4000,
        minute_offset=100,
    )
    store_samples(
        sample_path,
        replayed_samples,
        now=started + timedelta(minutes=200),
    )

    replay = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=tmp_path / "alert-events.jsonl",
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=started + timedelta(minutes=200),
        send=lambda _text, *, severity="page", **_kwargs: {"skipped": False},
    )

    assert replay["ruleset"] == ["PERF:homepage.first_card:same_host_origin:idle"]
    assert [
        (receipt["rule_id"], receipt["effective_severity"], receipt["type"])
        for receipt in replay["sent"]
    ] == [
        (
            "PERF:homepage.first_card:same_host_origin:idle",
            "page",
            "firing",
        )
    ]
    assert not any(":busy" in result["rule_id"] for result in replay["results"])
    assert "PERF:rollup:busy" not in replay["ruleset"]


def _legacy_firing_lifecycle(
    *,
    severity: str,
    announced: bool,
    started: datetime,
) -> dict[str, object]:
    return {
        "state": "firing",
        "since": started.isoformat(),
        "last_notified": started.isoformat() if announced else None,
        "detail": f"legacy {severity}",
        "announced": announced,
    }


def test_idle_only_migration_resolves_announced_legacy_busy_lifecycles_once(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    dual_rule = "PERF:homepage.first_card:same_host_origin:busy"
    pending_rule = "PERF:wechat.list.first_card:same_host_origin:busy"
    rollup_rule = "PERF:rollup:busy"
    state_path.write_text(
        json.dumps(
            {
                dual_rule: {
                    "state": "firing",
                    "severity": "page",
                    "lifecycles": {
                        "page": _legacy_firing_lifecycle(
                            severity="page",
                            announced=True,
                            started=started,
                        ),
                        "notice": _legacy_firing_lifecycle(
                            severity="notice",
                            announced=True,
                            started=started,
                        ),
                    },
                },
                pending_rule: {
                    "state": "firing",
                    "severity": "page",
                    **_legacy_firing_lifecycle(
                        severity="page",
                        announced=False,
                        started=started,
                    ),
                },
                rollup_rule: {
                    "state": "firing",
                    "severity": "notice",
                    **_legacy_firing_lifecycle(
                        severity="notice",
                        announced=True,
                        started=started,
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    kwargs = {
        "sample_path": tmp_path / "journey-samples.jsonl",
        "state_path": state_path,
        "event_path": state_path.with_name("alert-events.jsonl"),
        "evidence_dir": tmp_path / "evidence",
        "pipeline_lock_path": tmp_path / ".pipeline.flock",
        "send": lambda text, *, severity="page": {"skipped": False},
    }

    migrated = run_performance_alerts(now=started + timedelta(minutes=1), **kwargs)

    assert {
        (receipt["rule_id"], receipt["effective_severity"], receipt["type"])
        for receipt in migrated["sent"]
    } == {
        (dual_rule, "page", "resolved"),
        (dual_rule, "notice", "resolved"),
        (rollup_rule, "notice", "resolved"),
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert all(state[rule_id]["state"] == "ok" for rule_id in (dual_rule, pending_rule, rollup_rule))
    assert all(
        lifecycle["state"] == "ok"
        for rule_id in (dual_rule, pending_rule, rollup_rule)
        for lifecycle in state[rule_id]["lifecycles"].values()
    )

    repeated = run_performance_alerts(now=started + timedelta(minutes=2), **kwargs)

    assert repeated["sent"] == []
    assert not any(":busy" in row["rule_id"] for row in repeated["results"])


def test_idle_only_migration_retries_only_skipped_real_sender_delivery(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    state_path = tmp_path / "state.json"
    started = datetime(2026, 7, 24, tzinfo=UTC)
    rule_id = "PERF:homepage.first_card:same_host_origin:busy"
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "firing",
                    "severity": "page",
                    "lifecycles": {
                        "page": _legacy_firing_lifecycle(
                            severity="page",
                            announced=True,
                            started=started,
                        ),
                        "notice": _legacy_firing_lifecycle(
                            severity="notice",
                            announced=True,
                            started=started,
                        ),
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    outcomes = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 7, "", "notice unavailable"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
    )
    commands: list[list[str]] = []

    def run_sender(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert timeout == 15.0
        assert all(
            name not in env
            for name in (
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
            )
        )
        commands.append(command)
        return next(outcomes)

    monkeypatch.setattr("airadar.admin.alerts.subprocess.run", run_sender)
    kwargs = {
        "sample_path": tmp_path / "journey-samples.jsonl",
        "state_path": state_path,
        "event_path": state_path.with_name("alert-events.jsonl"),
        "evidence_dir": tmp_path / "evidence",
        "pipeline_lock_path": tmp_path / ".pipeline.flock",
    }

    first = run_performance_alerts(now=started + timedelta(minutes=1), **kwargs)
    after_first = json.loads(state_path.read_text(encoding="utf-8"))[rule_id]
    second = run_performance_alerts(now=started + timedelta(minutes=2), **kwargs)
    after_second = json.loads(state_path.read_text(encoding="utf-8"))[rule_id]
    third = run_performance_alerts(now=started + timedelta(minutes=3), **kwargs)

    assert [
        (receipt["effective_severity"], receipt["delivered"])
        for receipt in first["sent"]
    ] == [("page", True), ("notice", False)]
    assert after_first["lifecycles"]["page"]["state"] == "ok"
    assert after_first["lifecycles"]["notice"]["state"] == "firing"
    assert [
        (receipt["effective_severity"], receipt["delivered"])
        for receipt in second["sent"]
    ] == [("notice", True)]
    assert after_second["state"] == "ok"
    assert third["sent"] == []
    assert sum("--alert" in command for command in commands) == 1


def test_idle_only_migration_send_exception_preserves_legacy_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    rule_id = "PERF:homepage.first_card:same_host_origin:busy"
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "firing",
                    "severity": "page",
                    **_legacy_firing_lifecycle(
                        severity="page",
                        announced=True,
                        started=started,
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    def fail_send(_text: str, *, severity: str = "page") -> dict[str, object]:
        raise RuntimeError(f"{severity} unavailable")

    with pytest.raises(RuntimeError, match="page unavailable"):
        run_performance_alerts(
            sample_path=tmp_path / "journey-samples.jsonl",
            state_path=state_path,
            event_path=state_path.with_name("alert-events.jsonl"),
            evidence_dir=tmp_path / "evidence",
            pipeline_lock_path=tmp_path / ".pipeline.flock",
            now=started + timedelta(minutes=1),
            send=fail_send,
        )

    retained = json.loads(state_path.read_text(encoding="utf-8"))[rule_id]
    assert retained["state"] == "firing"
    lifecycle = retained["lifecycles"]["page"]
    assert lifecycle["state"] == "firing"
    assert lifecycle["pending_notification"] == {
        "nonce": 1,
        "event_type": "resolved",
        "episode_since": started.isoformat(),
    }


def test_round_without_new_samples_trims_stale_rows_and_resolves_stale_page(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    now = datetime.now(UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    stale_rows = [
        _sample(
            observed_at=now - timedelta(days=30, minutes=index),
            value_ms=4000,
            load_class="idle",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    sample_path.write_text(
        "".join(json.dumps(row) + "\n" for row in stale_rows),
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "firing",
                    "severity": "page",
                    "firing_basis": "observed",
                    **_legacy_firing_lifecycle(
                        severity="page",
                        announced=True,
                        started=now - timedelta(days=30),
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "airadar.performance.journey_monitor.classify_pipeline_load",
        lambda _path: "busy",
    )
    sent: list[tuple[str, str]] = []

    def sender(
        text: str,
        *,
        severity: str = "page",
        **_kwargs: object,
    ) -> dict[str, object]:
        sent.append((text, severity))
        return {"skipped": False}

    monkeypatch.setattr("airadar.admin.alerts.send_alert_message", sender)

    result = run_journey_monitor(
        origin_url="http://origin.invalid",
        public_url="",
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )

    rows = [line for line in sample_path.read_text(encoding="utf-8").splitlines() if line]
    assert rows == []
    assert result["samples"] == []
    assert [
        (receipt["rule_id"], receipt["type"])
        for receipt in result["alerts"]["sent"]
    ] == [(rule_id, "resolved")]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state[rule_id]["state"] == "ok"
    assert len(sent) == 1


def test_corrupt_sample_window_holds_existing_firing_state(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    sample_path.write_text(
        "{corrupt\n"
        + json.dumps(
            _sample(
                observed_at=now,
                value_ms=100,
                vantage="same_host_origin",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "firing",
                    "severity": "page",
                    "firing_basis": "observed",
                    **_legacy_firing_lifecycle(
                        severity="page",
                        announced=True,
                        started=now - timedelta(hours=1),
                    ),
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now,
        send=lambda text, *, severity="page": pytest.fail(
            f"corrupt window must hold state, got {severity}: {text}"
        ),
    )

    assert result["sent"] == []
    assert result["corrupt_input"] is True
    assert json.loads(state_path.read_text(encoding="utf-8"))[rule_id]["state"] == "firing"


def test_corrupt_hold_survives_compaction_until_a_fresh_sample_arrives(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    low_rows = [
        _sample(
            observed_at=now - timedelta(minutes=MIN_CONFIRMABLE_SAMPLES - index),
            value_ms=100,
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    sample_path.write_text(
        "".join(json.dumps(row) + "\n" for row in low_rows) + "{corrupt\n",
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "firing",
                    "severity": "page",
                    "firing_basis": "observed",
                    **_legacy_firing_lifecycle(
                        severity="page",
                        announced=True,
                        started=now - timedelta(hours=1),
                    ),
                }
            }
        ),
        encoding="utf-8",
    )

    first = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now,
        send=lambda text, *, severity="page": pytest.fail(
            f"corrupt round resolved {severity}: {text}"
        ),
    )
    assert first["corrupt_input"] is True
    assert store_samples(sample_path, [], now=now + timedelta(minutes=1)) is True

    held = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now + timedelta(minutes=1),
        send=lambda text, *, severity="page": pytest.fail(
            f"compacted corrupt hold resolved {severity}: {text}"
        ),
    )
    assert held["sent"] == []
    assert held["corrupt_input"] is True
    assert json.loads(state_path.read_text(encoding="utf-8"))[rule_id]["state"] == "firing"

    store_samples(
        sample_path,
        [
            _sample(
                observed_at=now - timedelta(hours=2),
                value_ms=100,
                vantage="same_host_origin",
            )
        ],
        now=now + timedelta(minutes=1),
    )
    still_held = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now + timedelta(minutes=1),
        send=lambda text, *, severity="page": pytest.fail(
            f"pre-corruption sample cleared hold {severity}: {text}"
        ),
    )
    assert still_held["corrupt_input"] is True

    store_samples(
        sample_path,
        [
            _sample(
                observed_at=now + timedelta(minutes=2),
                value_ms=100,
                vantage="same_host_origin",
            )
        ],
        now=now + timedelta(minutes=2),
    )
    sent: list[str] = []
    recovered = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now + timedelta(minutes=2),
        send=lambda text, *, severity="page": sent.append(text) or {"skipped": False},
    )
    assert [(row["rule_id"], row["type"]) for row in recovered["sent"]] == [
        (rule_id, "resolved")
    ]
    assert len(sent) == 1


def test_fresh_sample_for_other_cell_does_not_clear_corrupt_hold(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    held_rule_id = "PERF:homepage.first_card:same_host_origin:idle"
    low_rows = [
        _sample(
            observed_at=now - timedelta(minutes=MIN_CONFIRMABLE_SAMPLES - index),
            value_ms=100,
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    sample_path.write_text(
        "".join(json.dumps(row) + "\n" for row in low_rows) + "{corrupt\n",
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                held_rule_id: {
                    "state": "firing",
                    "severity": "page",
                    "firing_basis": "observed",
                    **_legacy_firing_lifecycle(
                        severity="page",
                        announced=True,
                        started=now - timedelta(hours=1),
                    ),
                }
            }
        ),
        encoding="utf-8",
    )

    run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now,
        send=lambda text, *, severity="page": pytest.fail(
            f"corrupt round resolved {severity}: {text}"
        ),
    )
    assert store_samples(sample_path, [], now=now + timedelta(minutes=1)) is True
    store_samples(
        sample_path,
        [
            _sample(
                observed_at=now + timedelta(minutes=2),
                value_ms=100,
                journey="wechat.list.first_card",
                vantage="same_host_origin",
            )
        ],
        now=now + timedelta(minutes=2),
    )

    held = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now + timedelta(minutes=2),
        send=lambda text, *, severity="page": pytest.fail(
            f"other cell cleared hold and sent {severity}: {text}"
        ),
    )

    assert held["corrupt_input"] is True
    assert held["sent"] == []
    assert json.loads(state_path.read_text(encoding="utf-8"))[held_rule_id]["state"] == "firing"


def test_sample_snapshot_gets_sequence_after_interleaved_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = datetime(2026, 7, 24, 10, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    initial = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=4000,
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES - 1)
    ]
    store_samples(
        sample_path,
        initial,
        now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES - 1),
    )
    original_load = journey_monitor._load_samples
    first_load_entered = threading.Event()
    resume_first_load = threading.Event()

    def pause_first_load(*args: object, **kwargs: object):
        if not first_load_entered.is_set():
            first_load_entered.set()
            assert resume_first_load.wait(timeout=3)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(journey_monitor, "_load_samples", pause_first_load)
    common = {
        "sample_path": sample_path,
        "state_path": state_path,
        "event_path": event_path,
        "evidence_dir": tmp_path / "evidence",
        "pipeline_lock_path": tmp_path / ".pipeline.flock",
        "now": started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
    }
    calls: list[str] = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        resumed_round = executor.submit(
            run_performance_alerts,
            **common,
            send=lambda text, *, severity="page": calls.append(text)
            or {"skipped": False},
        )
        assert first_load_entered.wait(timeout=2)
        older_snapshot = run_performance_alerts(
            **common,
            send=lambda text, *, severity="page": pytest.fail(
                f"21-sample snapshot sent {severity}: {text}"
            ),
        )
        assert older_snapshot["sent_count"] == 0
        store_samples(
            sample_path,
            [
                _sample(
                    observed_at=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES - 1),
                    value_ms=4000,
                )
            ],
            now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
        )
        resume_first_load.set()
        resumed = resumed_round.result(timeout=3)

    rule_id = "PERF:homepage.first_card:same_host_public:idle"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert resumed["sent_count"] == 1
    assert len(calls) == 1
    assert state[rule_id]["state"] == "firing"
    assert state[rule_id]["last_evaluation_sequence"] == 2


def test_disabled_vantage_resolves_even_when_cell_has_corrupt_hold(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 24, 12, tzinfo=UTC)
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    rule_id = "PERF:homepage.first_card:same_host_public:idle"
    sample_path.write_text(
        json.dumps(_sample(observed_at=now, value_ms=4000)) + "\n{corrupt\n",
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "firing",
                    "severity": "page",
                    "firing_basis": "observed",
                    **_legacy_firing_lifecycle(
                        severity="page",
                        announced=True,
                        started=now - timedelta(hours=1),
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    common = {
        "sample_path": sample_path,
        "state_path": state_path,
        "event_path": event_path,
        "evidence_dir": tmp_path / "evidence",
        "pipeline_lock_path": tmp_path / ".pipeline.flock",
        "now": now,
    }
    held = run_performance_alerts(
        **common,
        enabled_vantages=frozenset({"same_host_public"}),
        send=lambda text, *, severity="page": pytest.fail(
            f"corrupt hold unexpectedly sent {severity}: {text}"
        ),
    )
    assert held["corrupt_input"] is True
    sent: list[str] = []

    disabled = run_performance_alerts(
        **{**common, "now": now + timedelta(minutes=1)},
        enabled_vantages=frozenset({"same_host_origin"}),
        send=lambda text, *, severity="page": sent.append(text) or {"skipped": False},
    )

    assert [(row["rule_id"], row["type"]) for row in disabled["sent"]] == [
        (rule_id, "resolved")
    ]
    assert any(
        row["rule_id"] == rule_id
        and row["firing"] is False
        and "vantage disabled" in row["detail"]
        for row in disabled["results"]
    )
    assert len(sent) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))[rule_id]["state"] == "ok"


def test_orphan_sample_store_lock_skips_without_blocking_then_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    lock_path = sample_path.with_suffix(sample_path.suffix + ".lock")
    ready_path = tmp_path / "orphan-ready"
    pid_path = tmp_path / "orphan.pid"
    launcher = tmp_path / "orphan-lock-holder.py"
    launcher.write_text(
        """
import fcntl
import os
import time
from pathlib import Path

lock_path = Path(os.environ["LOCK_PATH"])
ready_path = Path(os.environ["READY_PATH"])
pid_path = Path(os.environ["PID_PATH"])
child = os.fork()
if child:
    pid_path.write_text(str(child))
    raise SystemExit(0)
os.setsid()
with lock_path.open("a+") as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    ready_path.write_text("locked")
    time.sleep(60)
""",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "LOCK_PATH": str(lock_path),
        "READY_PATH": str(ready_path),
        "PID_PATH": str(pid_path),
    }
    subprocess.run([sys.executable, str(launcher)], check=True, env=environment, timeout=2)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not ready_path.exists():
        time.sleep(0.01)
    assert ready_path.exists()
    orphan_pid = int(pid_path.read_text(encoding="utf-8"))
    sample = _sample(
        observed_at=datetime(2026, 7, 24, 12, tzinfo=UTC),
        value_ms=100,
        vantage="same_host_origin",
    )
    contender = (
        "import json,sys;"
        "from datetime import datetime;"
        "from pathlib import Path;"
        "from airadar.performance.journey_monitor import store_samples;"
        "result=store_samples(Path(sys.argv[1]),[json.loads(sys.argv[2])],"
        "now=datetime.fromisoformat(sys.argv[3]));"
        "print(json.dumps(result))"
    )
    command = [
        sys.executable,
        "-c",
        contender,
        str(sample_path),
        json.dumps(sample),
        "2026-07-24T12:00:00+00:00",
    ]
    try:
        skipped = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1,
            env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
        )
        assert skipped.returncode == 0, skipped.stdout + skipped.stderr
        assert skipped.stdout.strip() == "null"
        assert not sample_path.exists()
        monkeypatch.setattr(
            journey_monitor,
            "probe_journeys",
            lambda runtime, observed_at=None: [
                journey_monitor.JourneySample(**sample)
            ],
        )
        skipped_round = run_journey_monitor(
            origin_url="http://origin.invalid",
            public_url="",
            sample_path=sample_path,
            state_path=tmp_path / "state.json",
            evidence_dir=tmp_path / "evidence",
            pipeline_lock_path=tmp_path / ".pipeline.flock",
            db_path=tmp_path / "radar.db",
        )
        assert skipped_round["samples"] == []
        assert skipped_round["sample_store_skipped"] is True
        assert skipped_round["alerts"]["sent_count"] == 0
    finally:
        try:
            os.kill(orphan_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    recovered = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=2,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert recovered.stdout.strip() == "false"
    assert len(sample_path.read_text(encoding="utf-8").splitlines()) == 1


def test_external_watchdog_kills_sigstop_holder_and_next_round_samples(
    tmp_path: Path,
) -> None:
    helper = tmp_path / "stopped-probe.py"
    lock_path = tmp_path / "probe-state.lock"
    stopped_once = tmp_path / "stopped-once"
    sample_path = tmp_path / "sampled"
    helper.write_text(
        """
import fcntl
import os
import signal
import sys
from pathlib import Path

lock_path, stopped_once, sample_path = map(Path, sys.argv[1:])
with lock_path.open("a+") as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    if not stopped_once.exists():
        stopped_once.write_text("ready")
        os.kill(os.getpid(), signal.SIGSTOP)
    sample_path.write_text("sampled")
""",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-m",
        "airadar.performance.journey_monitor",
        "--external-watchdog",
        "--timeout-seconds",
        "0.3",
        "--kill-after-seconds",
        "0.1",
        "--",
        sys.executable,
        str(helper),
        str(lock_path),
        str(stopped_once),
        str(sample_path),
    ]
    started = time.monotonic()

    timed_out = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=3,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )
    elapsed = time.monotonic() - started
    recovered = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=3,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )

    assert timed_out.returncode == 124, timed_out.stdout + timed_out.stderr
    assert elapsed < 2
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert sample_path.read_text(encoding="utf-8") == "sampled"


def test_killing_watchdog_terminates_its_probe_child(tmp_path: Path) -> None:
    helper = tmp_path / "long-probe.py"
    child_pid_path = tmp_path / "probe-child.pid"
    sample_path = tmp_path / "journey-samples.jsonl"
    sample_lock_path = sample_path.with_suffix(sample_path.suffix + ".lock")
    helper.write_text(
        """
import fcntl
import os
import time
from pathlib import Path

with Path(os.environ["SAMPLE_LOCK_PATH"]).open("a+") as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    Path(os.environ["CHILD_PID_PATH"]).write_text(str(os.getpid()))
    time.sleep(60)
""",
        encoding="utf-8",
    )
    watchdog = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "airadar.performance.journey_monitor",
            "--external-watchdog",
            "--timeout-seconds",
            "60",
            "--kill-after-seconds",
            "1",
            "--",
            sys.executable,
            str(helper),
        ],
        env={
            **os.environ,
            "PYTHONPATH": str(Path.cwd() / "src"),
            "CHILD_PID_PATH": str(child_pid_path),
            "SAMPLE_LOCK_PATH": str(sample_lock_path),
        },
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not child_pid_path.exists():
            time.sleep(0.01)
        assert child_pid_path.exists()
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        os.kill(watchdog.pid, signal.SIGKILL)
        watchdog.wait(timeout=2)

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = subprocess.run(
                ["/bin/ps", "-p", str(child_pid), "-o", "stat="],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"probe child {child_pid} survived watchdog death")
        assert (
            store_samples(
                sample_path,
                [
                    _sample(
                        observed_at=datetime(2026, 7, 24, 12, tzinfo=UTC),
                        value_ms=100,
                    )
                ],
                now=datetime(2026, 7, 24, 12, tzinfo=UTC),
            )
            is False
        )
    finally:
        if watchdog.poll() is None:
            watchdog.kill()
            watchdog.wait(timeout=2)
        if child_pid_path.exists():
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_run_sh_wraps_performance_probe_in_external_watchdog(tmp_path: Path) -> None:
    launcher = tmp_path / "run.sh"
    shutil.copy2(Path.cwd() / "run.sh", launcher)
    launcher.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured = tmp_path / "uv-args"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > {str(captured)!r}\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    completed = subprocess.run(
        [str(launcher), "performance-probe", "--origin-url", "http://origin.invalid"],
        capture_output=True,
        text=True,
        timeout=3,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert completed.returncode == 0
    arguments = captured.read_text(encoding="utf-8")
    assert "python -m airadar.performance.journey_monitor --external-watchdog" in arguments
    assert "--timeout-seconds 960 --kill-after-seconds 5" in arguments
    assert (
        "-- python -m airadar.cli performance-probe "
        "--origin-url http://origin.invalid"
    ) in arguments
    assert "-- uv run python -m airadar.cli performance-probe" not in arguments


def test_run_sh_watchdog_parent_death_kills_real_probe_and_releases_lock(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "run.sh"
    shutil.copy2(Path.cwd() / "run.sh", launcher)
    launcher.chmod(0o755)
    (tmp_path / "src").symlink_to(Path.cwd() / "src", target_is_directory=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_python = sys.executable
    real_uv = shutil.which("uv")
    assert real_uv is not None
    probe_pid_path = tmp_path / "real-probe.pid"
    sample_path = tmp_path / "journey-samples.jsonl"
    sample_lock_path = sample_path.with_suffix(sample_path.suffix + ".lock")
    helper = tmp_path / "real-probe.py"
    helper.write_text(
        """
import fcntl
import os
import time
from pathlib import Path

with Path(os.environ["SAMPLE_LOCK_PATH"]).open("a+") as stream:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    Path(os.environ["PROBE_PID_PATH"]).write_text(str(os.getpid()))
    time.sleep(60)
""",
        encoding="utf-8",
    )
    fake_python = fake_bin / "python"
    fake_python.write_text(
        f"""#!/usr/bin/env bash
set -eu
if [[ "$*" == *"airadar.cli performance-probe"* ]]; then
  exec {real_python!r} {str(helper)!r}
fi
exec {real_python!r} "$@"
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        f"""#!/usr/bin/env bash
set -eu
[[ "${{1:-}}" == "run" ]] || exec {real_uv!r} "$@"
shift
if [[ "$*" == *"airadar.performance.journey_monitor"* ]]; then
  exec "$@"
fi
"$@" &
wait $!
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    watchdog = subprocess.Popen(
        [
            str(launcher),
            "performance-probe",
            "--origin-url",
            "http://origin.invalid",
            "--samples-path",
            str(sample_path),
            "--state-path",
            str(tmp_path / "state.json"),
            "--evidence-dir",
            str(tmp_path / "evidence"),
            "--pipeline-lock",
            str(tmp_path / ".pipeline.flock"),
            "--db-path",
            str(tmp_path / "radar.db"),
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PROBE_PID_PATH": str(probe_pid_path),
            "SAMPLE_LOCK_PATH": str(sample_lock_path),
        },
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not probe_pid_path.exists():
            time.sleep(0.01)
        assert probe_pid_path.exists()
        probe_pid = int(probe_pid_path.read_text(encoding="utf-8"))
        ancestry: list[tuple[int, int, str]] = []
        cursor = probe_pid
        while cursor > 1:
            row = subprocess.run(
                ["/bin/ps", "-p", str(cursor), "-o", "pid=,ppid=,command="],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if not row:
                break
            pid_text, parent_text, command = row.split(maxsplit=2)
            ancestry.append((int(pid_text), int(parent_text), command))
            cursor = int(parent_text)
        assert watchdog.pid in {pid for pid, _parent, _command in ancestry}, ancestry
        assert not any(
            str(fake_uv) in command and "airadar.cli performance-probe" in command
            for _pid, _parent, command in ancestry
        ), ancestry

        os.kill(watchdog.pid, signal.SIGKILL)
        watchdog.wait(timeout=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = subprocess.run(
                ["/bin/ps", "-p", str(probe_pid), "-o", "stat="],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"real probe {probe_pid} survived watchdog death")
        assert (
            store_samples(
                sample_path,
                [
                    _sample(
                        observed_at=datetime(2026, 7, 24, 12, tzinfo=UTC),
                        value_ms=100,
                    )
                ],
                now=datetime(2026, 7, 24, 12, tzinfo=UTC),
            )
            is False
        )
    finally:
        if watchdog.poll() is None:
            watchdog.kill()
            watchdog.wait(timeout=2)
        if probe_pid_path.exists():
            try:
                os.kill(int(probe_pid_path.read_text(encoding="utf-8")), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_probe_process_deadline_restores_shorter_timer_minus_elapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, lambda _signum, _frame: None)
    signal.setitimer(signal.ITIMER_REAL, 5.0, 0.25)
    monkeypatch.setattr(journey_monitor, "PROBE_OVERALL_TIMEOUT_SECONDS", 60)
    try:
        with journey_monitor._probe_process_deadline():
            time.sleep(0.2)
        remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

    assert 4.5 < remaining < 4.9
    assert interval == pytest.approx(0.25)


def test_probe_process_deadline_fails_clearly_outside_main_thread() -> None:
    failures: list[BaseException] = []

    def invoke() -> None:
        try:
            with journey_monitor._probe_process_deadline():
                pass
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=invoke)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], RuntimeError)
    assert "main thread" in str(failures[0]).lower()


def test_browser_worker_kill_covers_start_before_setsid_window() -> None:
    worker = subprocess.Popen(["/bin/sleep", "60"])
    assert worker.pid is not None
    browser_probe._ACTIVE_BROWSER_WORKER_PIDS.add(worker.pid)
    try:
        browser_probe.terminate_active_browser_workers()
        worker.wait(timeout=2)
    finally:
        browser_probe._ACTIVE_BROWSER_WORKER_PIDS.discard(worker.pid)
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=2)

    assert worker.returncode == -signal.SIGKILL


def test_probe_process_deadline_releases_stuck_state_lock(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    script = f"""
import time
from pathlib import Path
from airadar.admin.alerts import _alert_state_lock
from airadar.performance import journey_monitor

journey_monitor.PROBE_OVERALL_TIMEOUT_SECONDS = 0.25
journey_monitor.probe_journeys = lambda runtime, observed_at=None: []
journey_monitor.store_samples = lambda *args, **kwargs: False
def stuck_alerts(**kwargs):
    with _alert_state_lock(Path(kwargs["state_path"])):
        print("state-lock-held", flush=True)
        time.sleep(60)
journey_monitor.run_performance_alerts = stuck_alerts
journey_monitor.run_journey_monitor(
    origin_url="http://origin.invalid",
    public_url="",
    sample_path=Path({str(tmp_path / "samples.jsonl")!r}),
    state_path=Path({str(state_path)!r}),
    evidence_dir=Path({str(tmp_path / "evidence")!r}),
    pipeline_lock_path=Path({str(tmp_path / ".pipeline.flock")!r}),
    db_path=Path({str(tmp_path / "radar.db")!r}),
)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=3,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )

    assert completed.stdout.splitlines() == ["state-lock-held"]
    assert completed.returncode == 124
    run_alert_results_state_machine(
        [],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
    )


def test_probe_process_deadline_kills_registered_browser_worker_group(
    tmp_path: Path,
) -> None:
    worker_pid_path = tmp_path / "worker.pid"
    script = f"""
import subprocess
import time
from pathlib import Path
from airadar.performance import browser_probe, journey_monitor

journey_monitor.PROBE_OVERALL_TIMEOUT_SECONDS = 0.25
worker = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
browser_probe._ACTIVE_BROWSER_WORKER_PIDS.add(worker.pid)
Path({str(worker_pid_path)!r}).write_text(str(worker.pid))
journey_monitor.probe_journeys = lambda runtime, observed_at=None: time.sleep(60)
journey_monitor.run_journey_monitor(
    origin_url="http://origin.invalid",
    public_url="",
    sample_path=Path({str(tmp_path / "samples.jsonl")!r}),
    state_path=Path({str(tmp_path / "state.json")!r}),
    evidence_dir=Path({str(tmp_path / "evidence")!r}),
    pipeline_lock_path=Path({str(tmp_path / ".pipeline.flock")!r}),
    db_path=Path({str(tmp_path / "radar.db")!r}),
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=3,
        env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
    )
    worker_pid = int(worker_pid_path.read_text())
    try:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            status = subprocess.run(
                ["/bin/ps", "-p", str(worker_pid), "-o", "stat="],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"browser worker {worker_pid} survived probe deadline")
    finally:
        try:
            os.killpg(worker_pid, 9)
        except ProcessLookupError:
            pass

    assert completed.returncode == 124


def test_migration_uses_nested_lifecycle_projection_when_top_level_is_ok(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    now = datetime.now(UTC)
    rule_id = "PERF:homepage.first_card:same_host_origin:busy"
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "ok",
                    "severity": "page",
                    "announced": False,
                    "lifecycles": {
                        "page": {
                            "state": "ok",
                            "since": None,
                            "last_notified": None,
                            "detail": "page ok",
                            "announced": False,
                        },
                        "notice": _legacy_firing_lifecycle(
                            severity="notice",
                            announced=True,
                            started=now - timedelta(hours=1),
                        ),
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    migrated = run_performance_alerts(
        sample_path=tmp_path / "journey-samples.jsonl",
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=now,
        send=lambda _text, *, severity="page": {"skipped": False},
    )

    assert [
        (receipt["rule_id"], receipt["effective_severity"], receipt["type"])
        for receipt in migrated["sent"]
    ] == [(rule_id, "notice", "resolved")]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state[rule_id]["state"] == "ok"
    assert state[rule_id]["lifecycles"]["notice"]["state"] == "ok"



@pytest.mark.parametrize(
    ("vantage", "expected_impact"),
    [
        ("same_host_origin", "已确认同视角真实退化"),
        ("same_host_public", "已确认公网路径退化"),
    ],
)
def test_firing_idle_cell_explains_path_impact_and_immediate_action(
    vantage: str,
    expected_impact: str,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=4000,
            load_class="idle",
            vantage=vantage,
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]

    idle = next(
        result
        for result in evaluate_performance_rules(
            samples,
            now=started + timedelta(minutes=MIN_CONFIRMABLE_SAMPLES),
        )
        if result.rule_id == f"PERF:homepage.first_card:{vantage}:idle"
    )

    assert idle.severity == "page"
    assert "gate_reason" not in idle.values
    assert expected_impact in idle.impact
    assert idle.urgency == "是"


def test_probe_requires_origin_url(tmp_path: Path) -> None:
    runtime = JourneyMonitorRuntime(
        origin_url="",
        public_url="https://public.invalid",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        db_path=tmp_path / "radar.db",
    )
    try:
        probe_journeys(runtime, observed_at=datetime(2026, 7, 18, tzinfo=UTC))
    except ValueError as error:
        assert "origin_url" in str(error)
    else:
        raise AssertionError("probe_journeys must reject empty origin_url")


def test_disabled_public_vantage_resolves_stale_firing_state(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "journey-alert-state.json"
    evidence_dir = tmp_path / "evidence"
    lock_dir = tmp_path / ".pipeline.flock"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    sent: list[str] = []

    def sender(
        text: str,
        *,
        severity: str = "page",
        **_kwargs: object,
    ) -> dict[str, object]:
        sent.append(text)
        return {"skipped": False}

    monkeypatch.setattr("airadar.admin.alerts.send_alert_message", sender)

    store_samples(
        sample_path,
        [_sample(observed_at=started + timedelta(minutes=index), value_ms=4000) for index in range(20)],
        now=started + timedelta(minutes=20),
    )
    confirmed: dict[str, object] = {}
    for now_minute in (20, 21, 22):
        if now_minute > 20:
            store_samples(
                sample_path,
                [_sample(observed_at=started + timedelta(minutes=now_minute - 1), value_ms=4000)],
                now=started + timedelta(minutes=now_minute),
            )
        confirmed = run_performance_alerts(
            sample_path=sample_path,
            state_path=state_path,
            event_path=Path(state_path).with_name("alert-events.jsonl"),
            evidence_dir=evidence_dir,
            pipeline_lock_path=lock_dir,
            now=started + timedelta(minutes=now_minute),
        )
    assert confirmed["sent_count"] == 1

    # Public vantage is disabled afterwards (AI_RADAR_PUBLIC_URL unset): stale
    # public samples must not keep the rule firing, and the stale firing state
    # must resolve explicitly instead of going permanently stale.
    resolved_round = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=lock_dir,
        now=started + timedelta(minutes=30),
        enabled_vantages=frozenset({"same_host_origin"}),
    )
    synthetic = [
        row
        for row in resolved_round["results"]
        if "same_host_public" in row["rule_id"] and not row["firing"]
    ]
    assert synthetic, "disabled vantage must emit an explicit non-firing result"
    assert any("resolved" in entry["type"] for entry in resolved_round["sent"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    public_states = {
        rule_id: entry.get("state")
        for rule_id, entry in state.items()
        if isinstance(entry, dict) and "same_host_public" in rule_id
    }
    assert public_states and all(value != "firing" for value in public_states.values())
    assert all(
        "lifecycles" in entry
        for rule_id, entry in state.items()
        if isinstance(entry, dict) and "same_host_public" in rule_id
    )

    second_clear = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        evidence_dir=evidence_dir,
        pipeline_lock_path=lock_dir,
        now=started + timedelta(minutes=31),
        enabled_vantages=frozenset({"same_host_origin"}),
        send=sender,
    )
    assert not any(
        receipt["type"] == "resolved" and "same_host_public" in receipt["rule_id"]
        for receipt in second_clear["sent"]
    )


def test_performance_entry_persists_state_when_notification_ledger_is_corrupt(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "journey-alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    original_ledger = "{not-json\n"
    event_path.write_text(original_ledger, encoding="utf-8")
    store_samples(
        sample_path,
        _cell_samples(
            started=started,
            journey="homepage.first_card",
            load_class="idle",
            value_ms=4000,
        ),
        now=started + timedelta(days=1),
    )

    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        event_path=event_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_path=tmp_path / ".pipeline.flock",
        now=started + timedelta(days=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert [(receipt["rule_id"], receipt["type"]) for receipt in result["sent"]] == [
        ("PERF:homepage.first_card:same_host_origin:idle", "firing")
    ]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    idle_state = state["PERF:homepage.first_card:same_host_origin:idle"]
    assert idle_state["state"] == "firing"
    assert idle_state["announced"] is True
    assert event_path.read_text(encoding="utf-8") == original_ledger
    assert str(event_path) in caplog.text
    assert "JSONDecodeError" in caplog.text
