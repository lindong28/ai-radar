from __future__ import annotations

import json
import re
import sys
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from airadar import db
from airadar.admin.alerts import AlertRuleResult, run_alert_results_state_machine
from airadar.performance.browser_probe import BrowserMeasurement, _measure_browser_journey_inner
from airadar.performance.journey_monitor import (
    CONFIRMATION_WINDOWS,
    RETENTION_DAYS,
    WARM_SAMPLES,
    JourneyMonitorRuntime,
    _load_alert_state,
    _probe_expectation,
    _with_firing_message,
    classify_pipeline_load,
    evaluate_performance_rules,
    probe_journeys,
    run_performance_alerts,
    store_samples,
)

MIN_CONFIRMABLE_SAMPLES = WARM_SAMPLES + CONFIRMATION_WINDOWS - 1


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
        evaluate=lambda *_args: True,
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
        lock_path=tmp_path / "browser.lock",
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
        lock_path=tmp_path / "browser.lock",
        expected={"item_ids": expected_ids},
    )

    assert mismatch.outcome == "observed"
    assert mismatch.hard_failure is True


def test_pipeline_lock_classification_distinguishes_idle_busy_and_unknown(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    lock_dir = tmp_path / ".pipeline.lock"
    assert classify_pipeline_load(lock_dir) == "idle"

    lock_dir.mkdir()
    (lock_dir / "pid").write_text("4242\n", encoding="utf-8")
    monkeypatch.setattr("airadar.performance.journey_monitor.os.kill", lambda pid, signal: None)
    assert classify_pipeline_load(lock_dir) == "busy"

    def dead_process(pid: int, signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr("airadar.performance.journey_monitor.os.kill", dead_process)
    assert classify_pipeline_load(lock_dir) == "unknown"
    (lock_dir / "pid").write_text("not-a-pid\n", encoding="utf-8")
    assert classify_pipeline_load(lock_dir) == "unknown"


def test_probe_runs_four_journeys_against_origin_and_public_and_stores_samples(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    lock_dir = tmp_path / ".pipeline.lock"
    runtime = JourneyMonitorRuntime(
        origin_url="http://origin.invalid",
        public_url="https://public.invalid",
        pipeline_lock_dir=lock_dir,
        browser_lock_path=tmp_path / "browser.lock",
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
    store_samples(sample_path, samples)

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
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        browser_lock_path=tmp_path / "browser.lock",
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
        lambda text, *, severity="page": {"skipped": False},
    )

    run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        evidence_dir=evidence_dir,
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
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
        "outcome": "observed",
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


def _notice_busy_batch(started: datetime, journeys: tuple[str, ...]) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for index, journey in enumerate(journeys):
        samples.extend(
            _cell_samples(
                started=started,
                journey=journey,
                load_class="busy",
                value_ms=4000,
                minute_offset=index * 100,
            )
        )
        samples.extend(
            _cell_samples(
                started=started,
                journey=journey,
                load_class="idle",
                value_ms=100,
                minute_offset=500 + index * 100,
            )
        )
    return samples


def test_performance_alert_requires_three_advanced_warm_windows_and_resolves(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "journey-alert-state.json"
    evidence_dir = tmp_path / "evidence"
    lock_dir = tmp_path / ".pipeline.lock"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    sent: list[str] = []

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        sent.append(text)
        return {"skipped": False}

    monkeypatch.setattr("airadar.admin.alerts.send_alert_message", sender)

    high = [_sample(observed_at=started + timedelta(minutes=index), value_ms=4000) for index in range(20)]
    store_samples(sample_path, high)
    first = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=lock_dir,
        now=started + timedelta(minutes=20),
    )
    assert first["sent_count"] == 0

    store_samples(sample_path, [_sample(observed_at=started + timedelta(minutes=20), value_ms=4000)])
    second = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=lock_dir,
        now=started + timedelta(minutes=21),
    )
    assert second["sent_count"] == 0

    store_samples(sample_path, [_sample(observed_at=started + timedelta(minutes=21), value_ms=4000)])
    confirmed = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=lock_dir,
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
    )
    unchanged = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=lock_dir,
        now=started + timedelta(minutes=23),
    )
    assert unchanged["sent_count"] == 0

    low = [
        _sample(observed_at=started + timedelta(minutes=23 + index), value_ms=100)
        for index in range(22)
    ]
    store_samples(sample_path, low)
    resolved = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=lock_dir,
        now=started + timedelta(minutes=46),
    )
    assert resolved["sent_count"] == 1
    assert resolved["sent"][0]["type"] == "resolved"
    assert len(sent) == 2


def test_busy_and_idle_performance_compliance_streams_are_separate(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = [
        _sample(observed_at=started + timedelta(minutes=index), value_ms=4000, load_class="busy")
        for index in range(22)
    ] + [
        _sample(observed_at=started + timedelta(minutes=100 + index), value_ms=100, load_class="idle")
        for index in range(22)
    ]
    store_samples(sample_path, samples)

    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(minutes=200),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    firing = [row for row in result["results"] if row["firing"]]
    assert len(firing) == 1
    assert firing[0]["values"]["load_class"] == "busy"


def test_notice_busy_cells_roll_up_once_with_complete_values_and_most_severe_detail(
    tmp_path: Path,
) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    journeys = ("homepage.first_card", "wechat.pagination.settle")
    store_samples(sample_path, _notice_busy_batch(started, journeys), now=started + timedelta(days=1))

    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert [receipt["rule_id"] for receipt in result["sent"]] == ["PERF:rollup:busy"]
    assert result["sent"][0]["effective_severity"] == "notice"
    assert not any(rule_id.endswith(":busy") and rule_id != "PERF:rollup:busy" for rule_id in result["ruleset"])
    rollup = next(row for row in result["results"] if row["rule_id"] == "PERF:rollup:busy")
    assert rollup["firing"] is True
    assert rollup["severity"] == "notice"
    assert rollup["values"]["load_class"] == "busy"
    assert rollup["values"]["cells"] == [
        {
            "journey": "homepage.first_card",
            "vantage": "same_host_origin",
            "p75_ms": 4000.0,
            "p95_ms": 4000.0,
            "p75_budget_ms": 2000.0,
            "p95_budget_ms": 3000.0,
        },
        {
            "journey": "wechat.pagination.settle",
            "vantage": "same_host_origin",
            "p75_ms": 4000.0,
            "p95_ms": 4000.0,
            "p75_budget_ms": 1000.0,
            "p95_budget_ms": 1500.0,
            "most_severe": True,
        },
    ]
    assert sum(cell.get("most_severe") is True for cell in rollup["values"]["cells"]) == 1
    assert "wechat.pagination.settle" in rollup["detail"]
    assert "wechat.pagination.settle [same_host_origin]：p95 实测 4000ms vs 预算 1500ms（最严重）" in rollup[
        "detail"
    ]
    assert rollup["detail"].startswith(rollup["impact"])
    assert "logs/performance/evidence/" in rollup["action"]


def test_notice_busy_rollup_does_not_merge_independent_idle_page(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = _notice_busy_batch(started, ("homepage.first_card", "wechat.list.first_card"))
    samples.extend(
        _cell_samples(
            started=started,
            journey="wechat.pagination.settle",
            load_class="idle",
            value_ms=4000,
            minute_offset=900,
        )
    )
    store_samples(sample_path, samples, now=started + timedelta(days=1))

    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert {
        (receipt["rule_id"], receipt["effective_severity"], receipt["type"])
        for receipt in result["sent"]
    } == {
        ("PERF:rollup:busy", "notice", "firing"),
        ("PERF:wechat.pagination.settle:same_host_origin:idle", "page", "firing"),
    }
    rollup = next(row for row in result["results"] if row["rule_id"] == "PERF:rollup:busy")
    assert {cell["journey"] for cell in rollup["values"]["cells"]} == {
        "homepage.first_card",
        "wechat.list.first_card",
    }


def test_notice_busy_rollup_keeps_real_page_gate_reasons_on_individual_keys(
    tmp_path: Path,
) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = _notice_busy_batch(started, ("homepage.first_card",))
    samples.extend(
        _cell_samples(
            started=started,
            journey="wechat.list.first_card",
            load_class="busy",
            value_ms=4000,
            minute_offset=1000,
        )
    )
    samples.extend(
        _cell_samples(
            started=started,
            journey="wechat.list.first_card",
            load_class="idle",
            value_ms=4000,
            minute_offset=1100,
        )
    )
    samples.extend(
        _cell_samples(
            started=started,
            journey="wechat.detail.readable",
            load_class="busy",
            value_ms=4000,
            minute_offset=1200,
        )
    )
    samples.extend(
        _cell_samples(
            started=started,
            journey="wechat.pagination.settle",
            load_class="busy",
            value_ms=4000,
            minute_offset=1300,
        )
    )
    samples.extend(
        _sample(
            observed_at=started + timedelta(minutes=1400 + index),
            value_ms=100,
            load_class="idle",
            journey="wechat.pagination.settle",
            vantage="same_host_origin",
        )
        for index in range(WARM_SAMPLES - 1)
    )
    store_samples(sample_path, samples, now=started + timedelta(days=2))

    result = run_performance_alerts(
        sample_path=sample_path,
        state_path=tmp_path / "state.json",
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=2),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert {
        (receipt["rule_id"], receipt["effective_severity"], receipt["channel"])
        for receipt in result["sent"]
    } == {
        ("PERF:rollup:busy", "notice", "NOTIFICATION"),
        ("PERF:wechat.list.first_card:same_host_origin:busy", "page", "ALERT"),
        ("PERF:wechat.list.first_card:same_host_origin:idle", "page", "ALERT"),
        ("PERF:wechat.detail.readable:same_host_origin:busy", "page", "ALERT"),
        ("PERF:wechat.pagination.settle:same_host_origin:busy", "page", "ALERT"),
    }
    firing_by_id = {row["rule_id"]: row for row in result["results"] if row["firing"]}
    assert firing_by_id["PERF:wechat.list.first_card:same_host_origin:busy"]["values"][
        "gate_reason"
    ] == "idle_firing"
    assert firing_by_id["PERF:wechat.detail.readable:same_host_origin:busy"]["values"][
        "gate_reason"
    ] == "idle_absent"
    assert firing_by_id["PERF:wechat.pagination.settle:same_host_origin:busy"]["values"][
        "gate_reason"
    ] == "idle_insufficient"
    assert [
        cell["journey"]
        for cell in firing_by_id["PERF:rollup:busy"]["values"]["cells"]
    ] == ["homepage.first_card"]


def test_notice_busy_rollup_resolves_once_when_the_whole_batch_recovers(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    journeys = ("homepage.first_card", "wechat.pagination.settle")
    store_samples(sample_path, _notice_busy_batch(started, journeys), now=started + timedelta(days=1))
    first = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert [(row["rule_id"], row["type"]) for row in first["sent"]] == [
        ("PERF:rollup:busy", "firing")
    ]

    recovered: list[dict[str, object]] = []
    for index, journey in enumerate(journeys):
        recovered.extend(
            _cell_samples(
                started=started,
                journey=journey,
                load_class="busy",
                value_ms=100,
                minute_offset=1200 + index * 100,
            )
        )
    store_samples(sample_path, recovered, now=started + timedelta(days=2))
    cleared = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=2),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert [
        (row["rule_id"], row["type"], row["effective_severity"], row["channel"])
        for row in cleared["sent"]
    ] == [
        ("PERF:rollup:busy", "resolved", "notice", "NOTIFICATION")
    ]
    rollup = next(row for row in cleared["results"] if row["rule_id"] == "PERF:rollup:busy")
    assert rollup["firing"] is False


def test_notice_busy_rollup_does_not_repeat_non_firing_after_second_clear(
    tmp_path: Path,
) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    store_samples(
        sample_path,
        _notice_busy_batch(started, ("homepage.first_card",)),
        now=started + timedelta(days=1),
    )
    fired = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert [(row["rule_id"], row["type"]) for row in fired["sent"]] == [
        ("PERF:rollup:busy", "firing")
    ]

    store_samples(
        sample_path,
        _cell_samples(
            started=started,
            journey="homepage.first_card",
            load_class="busy",
            value_ms=100,
            minute_offset=1200,
        ),
        now=started + timedelta(days=2),
    )
    first_clear = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=2),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert [(row["rule_id"], row["type"]) for row in first_clear["sent"]] == [
        ("PERF:rollup:busy", "resolved")
    ]
    assert sum(row["rule_id"] == "PERF:rollup:busy" for row in first_clear["results"]) == 1

    second_clear = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=2, minutes=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert second_clear["sent"] == []
    assert not any(row["rule_id"] == "PERF:rollup:busy" for row in second_clear["results"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["PERF:rollup:busy"]["state"] == "ok"


@pytest.mark.parametrize(
    ("legacy_severity", "expected_resolve_severity"),
    [(None, "page"), ("notice", "notice")],
)
def test_rollup_migrates_announced_legacy_busy_key_once(
    tmp_path: Path,
    legacy_severity: str | None,
    expected_resolve_severity: str,
) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    legacy_rule_id = "PERF:homepage.first_card:same_host_origin:busy"
    legacy_entry = {
        "state": "firing",
        "since": (started - timedelta(hours=2)).isoformat(),
        "last_notified": (started - timedelta(hours=1)).isoformat(),
        "detail": "legacy individual busy",
    }
    if legacy_severity is not None:
        legacy_entry["severity"] = legacy_severity
    state_path.write_text(json.dumps({legacy_rule_id: legacy_entry}), encoding="utf-8")
    store_samples(
        sample_path,
        _notice_busy_batch(started, ("homepage.first_card",)),
        now=started + timedelta(days=1),
    )

    migrated = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert {
        (receipt["rule_id"], receipt["effective_severity"], receipt["type"])
        for receipt in migrated["sent"]
    } == {
        ("PERF:rollup:busy", "notice", "firing"),
        (legacy_rule_id, expected_resolve_severity, "resolved"),
    }
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state[legacy_rule_id]["state"] == "ok"

    repeated = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=tmp_path / "evidence",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=1, minutes=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert repeated["sent"] == []


def test_rollup_evidence_uses_synthetic_episode_identity(tmp_path: Path) -> None:
    sample_path = tmp_path / "journey-samples.jsonl"
    state_path = tmp_path / "state.json"
    evidence_dir = tmp_path / "evidence"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    journeys = ("homepage.first_card", "wechat.pagination.settle")
    store_samples(sample_path, _notice_busy_batch(started, journeys), now=started + timedelta(days=1))

    for minute in (0, 1):
        run_performance_alerts(
            sample_path=sample_path,
            state_path=state_path,
            evidence_dir=evidence_dir,
            pipeline_lock_dir=tmp_path / ".pipeline.lock",
            now=started + timedelta(days=1, minutes=minute),
            send=lambda text, *, severity="page": {"skipped": False},
        )
        assert len(list(evidence_dir.glob("*.json"))) == 1

    first_evidence = json.loads(next(evidence_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert first_evidence["rule_id"] == "PERF:rollup:busy"
    assert len(first_evidence["cells"]) == 2
    assert sum(cell.get("most_severe") is True for cell in first_evidence["cells"]) == 1
    assert next(cell for cell in first_evidence["cells"] if cell.get("most_severe") is True)[
        "journey"
    ] == "wechat.pagination.settle"
    assert len(first_evidence["recent_samples"]) == 2 * MIN_CONFIRMABLE_SAMPLES

    recovered: list[dict[str, object]] = []
    for index, journey in enumerate(journeys):
        recovered.extend(
            _cell_samples(
                started=started,
                journey=journey,
                load_class="busy",
                value_ms=100,
                minute_offset=1200 + index * 100,
            )
        )
    store_samples(sample_path, recovered, now=started + timedelta(days=2))
    run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=2),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert len(list(evidence_dir.glob("*.json"))) == 1

    refiring: list[dict[str, object]] = []
    for index, journey in enumerate(journeys):
        refiring.extend(
            _cell_samples(
                started=started,
                journey=journey,
                load_class="busy",
                value_ms=4000,
                minute_offset=1800 + index * 100,
            )
        )
    store_samples(sample_path, refiring, now=started + timedelta(days=3))
    run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        now=started + timedelta(days=3),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert len(list(evidence_dir.glob("*.json"))) == 2


@pytest.mark.parametrize("vantage", ["same_host_origin", "same_host_public"])
@pytest.mark.parametrize(
    ("idle_condition", "expected_severity", "expected_gate_reason"),
    [
        ("clean", "notice", "idle_clean"),
        ("firing", "page", "idle_firing"),
        ("absent", "page", "idle_absent"),
        ("insufficient", "page", "idle_insufficient"),
    ],
)
def test_busy_performance_severity_uses_same_vantage_idle_truth_table(
    vantage: str,
    idle_condition: str,
    expected_severity: str,
    expected_gate_reason: str,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=4000,
            load_class="busy",
            vantage=vantage,
        )
        for index in range(WARM_SAMPLES + 2)
    ]
    idle_count = {
        "clean": MIN_CONFIRMABLE_SAMPLES,
        "firing": MIN_CONFIRMABLE_SAMPLES,
        "absent": 0,
        "insufficient": WARM_SAMPLES - 1,
    }[idle_condition]
    idle_value_ms = 4000 if idle_condition == "firing" else 100
    samples.extend(
        _sample(
            observed_at=started + timedelta(minutes=100 + index),
            value_ms=idle_value_ms,
            load_class="idle",
            vantage=vantage,
        )
        for index in range(idle_count)
    )

    if vantage == "same_host_public":
        origin_value_ms = 4000 if idle_condition == "clean" else 100
        origin_count = MIN_CONFIRMABLE_SAMPLES
        samples.extend(
            _sample(
                observed_at=started + timedelta(minutes=200 + index),
                value_ms=origin_value_ms,
                load_class="idle",
                vantage="same_host_origin",
            )
            for index in range(origin_count)
        )

    results = evaluate_performance_rules(samples)
    busy = next(
        result
        for result in results
        if result.rule_id == f"PERF:homepage.first_card:{vantage}:busy"
    )

    assert busy.firing is True
    assert busy.severity == expected_severity
    assert busy.values["gate_reason"] == expected_gate_reason


def _evaluate_busy_with_idle(
    *,
    vantage: str,
    idle_count: int,
    idle_value_ms: float,
    idle_hard_failure: bool = False,
) -> tuple[AlertRuleResult, AlertRuleResult]:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=4000,
            load_class="busy",
            vantage=vantage,
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    samples.extend(
        _sample(
            observed_at=started + timedelta(minutes=100 + index),
            value_ms=idle_value_ms,
            load_class="idle",
            vantage=vantage,
            hard_failure=idle_hard_failure,
        )
        for index in range(idle_count)
    )
    results = evaluate_performance_rules(samples)
    busy = next(
        result
        for result in results
        if result.rule_id == f"PERF:homepage.first_card:{vantage}:busy"
    )
    idle = next(
        result
        for result in results
        if result.rule_id == f"PERF:homepage.first_card:{vantage}:idle"
    )
    return busy, idle


@pytest.mark.parametrize("vantage", ["same_host_origin", "same_host_public"])
@pytest.mark.parametrize("idle_count", [WARM_SAMPLES, MIN_CONFIRMABLE_SAMPLES - 1])
def test_over_budget_idle_without_confirmation_windows_keeps_busy_page(
    vantage: str,
    idle_count: int,
) -> None:
    busy, idle = _evaluate_busy_with_idle(
        vantage=vantage,
        idle_count=idle_count,
        idle_value_ms=4000,
    )

    assert idle.firing is False
    assert busy.severity == "page"
    assert busy.values["gate_reason"] == "idle_insufficient"


@pytest.mark.parametrize("vantage", ["same_host_origin", "same_host_public"])
def test_hard_failure_idle_without_confirmation_windows_keeps_busy_page(vantage: str) -> None:
    busy, idle = _evaluate_busy_with_idle(
        vantage=vantage,
        idle_count=WARM_SAMPLES,
        idle_value_ms=100,
        idle_hard_failure=True,
    )

    assert idle.firing is False
    assert busy.severity == "page"
    assert busy.values["gate_reason"] == "idle_insufficient"


@pytest.mark.parametrize("vantage", ["same_host_origin", "same_host_public"])
def test_firing_capable_clean_idle_downgrades_busy_to_notice(vantage: str) -> None:
    busy, idle = _evaluate_busy_with_idle(
        vantage=vantage,
        idle_count=MIN_CONFIRMABLE_SAMPLES + 3,
        idle_value_ms=100,
    )

    assert idle.firing is False
    assert busy.severity == "notice"
    assert busy.values["gate_reason"] == "idle_clean"


@pytest.mark.parametrize("vantage", ["same_host_origin", "same_host_public"])
@pytest.mark.parametrize(
    ("idle_count", "expected_severity", "expected_gate_reason"),
    [
        (MIN_CONFIRMABLE_SAMPLES - 1, "page", "idle_insufficient"),
        (MIN_CONFIRMABLE_SAMPLES, "notice", "idle_clean"),
    ],
)
def test_idle_gate_uses_confirmation_window_sample_boundary(
    vantage: str,
    idle_count: int,
    expected_severity: str,
    expected_gate_reason: str,
) -> None:
    assert MIN_CONFIRMABLE_SAMPLES == WARM_SAMPLES + CONFIRMATION_WINDOWS - 1
    busy, idle = _evaluate_busy_with_idle(
        vantage=vantage,
        idle_count=idle_count,
        idle_value_ms=100,
    )

    assert idle.firing is False
    assert busy.severity == expected_severity
    assert busy.values["gate_reason"] == expected_gate_reason


@pytest.mark.parametrize("vantage", ["same_host_origin", "same_host_public"])
def test_firing_idle_performance_cell_is_always_page(vantage: str) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=4000,
            load_class="idle",
            vantage=vantage,
        )
        for index in range(WARM_SAMPLES + 2)
    ]

    results = evaluate_performance_rules(samples)
    idle = next(
        result
        for result in results
        if result.rule_id == f"PERF:homepage.first_card:{vantage}:idle"
    )

    assert idle.firing is True
    assert idle.severity == "page"
    assert idle.values["gate_reason"] == "idle_cell"


def test_perf_firing_detail_uses_decision_precision_and_stable_evidence_action() -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=2367.249083,
            load_class="busy",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ] + [
        _sample(
            observed_at=started + timedelta(minutes=100 + index),
            value_ms=100,
            load_class="idle",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]

    busy = next(
        result
        for result in evaluate_performance_rules(samples)
        if result.rule_id == "PERF:homepage.first_card:same_host_origin:busy"
    )

    assert re.search(r"\d+\.\d{3,}ms", busy.detail) is None
    assert "samples=" not in busy.detail
    assert "advanced_window_streak" not in busy.detail
    assert "p75 实测 2367ms vs 预算 2000ms" in busy.detail
    assert "p95 实测 2367ms vs 预算 3000ms" in busy.detail
    assert "logs/performance/evidence/" in busy.action
    assert busy.values["sample_count"] == MIN_CONFIRMABLE_SAMPLES
    assert busy.values["advanced_window_streak"] == CONFIRMATION_WINDOWS


@pytest.mark.parametrize("vantage", ["same_host_origin", "same_host_public"])
def test_perf_notice_explains_host_contention_and_no_immediate_action(vantage: str) -> None:
    busy, idle = _evaluate_busy_with_idle(
        vantage=vantage,
        idle_count=MIN_CONFIRMABLE_SAMPLES,
        idle_value_ms=100,
    )

    assert idle.firing is False
    assert busy.severity == "notice"
    assert busy.values["gate_reason"] == "idle_clean"
    assert busy.impact == (
        "同机合成探针，与 pipeline 并发、疑似主机 CPU 争用；"
        "idle 视角正常，用户大概率无感"
    )
    assert busy.urgency == "否——除非 idle 视角也超标"


def test_firing_message_rejects_unrecognized_gate_reason() -> None:
    result = AlertRuleResult(
        rule_id="PERF:future",
        title="future PERF gate",
        firing=True,
        detail="detail",
        action="action",
        values={"vantage": "same_host_origin"},
    )

    with pytest.raises(ValueError, match="unrecognized PERF gate_reason: future_reason"):
        _with_firing_message(result, gate_reason="future_reason")


def test_perf_page_with_firing_idle_states_confirmed_same_vantage_degradation() -> None:
    busy, idle = _evaluate_busy_with_idle(
        vantage="same_host_origin",
        idle_count=MIN_CONFIRMABLE_SAMPLES,
        idle_value_ms=4000,
    )

    assert idle.firing is True
    assert busy.severity == "page"
    assert busy.values["gate_reason"] == "idle_firing"
    assert "已确认同视角真实退化" in busy.impact
    assert "影响未知" not in busy.impact
    assert busy.urgency == "是"


@pytest.mark.parametrize(
    ("idle_condition", "expected_evidence_state"),
    [("absent", "当前无 idle 样本"), ("insufficient", "当前 idle 样本不足")],
)
def test_perf_page_without_enough_idle_evidence_states_unknown_conservative_impact(
    idle_condition: str,
    expected_evidence_state: str,
) -> None:
    started = datetime(2026, 7, 18, tzinfo=UTC)
    samples = [
        _sample(
            observed_at=started + timedelta(minutes=index),
            value_ms=4000,
            load_class="busy",
            vantage="same_host_origin",
        )
        for index in range(MIN_CONFIRMABLE_SAMPLES)
    ]
    if idle_condition == "insufficient":
        samples.extend(
            _sample(
                observed_at=started + timedelta(minutes=100 + index),
                value_ms=100,
                load_class="idle",
                vantage="same_host_origin",
            )
            for index in range(WARM_SAMPLES - 1)
        )

    busy = next(
        result
        for result in evaluate_performance_rules(samples)
        if result.rule_id == "PERF:homepage.first_card:same_host_origin:busy"
    )

    assert busy.severity == "page"
    assert busy.values["gate_reason"] == f"idle_{idle_condition}"
    assert "影响未知" in busy.impact
    assert "缺少足量同视角 idle 背书" in busy.impact
    assert expected_evidence_state in busy.impact
    assert "无法排除用户影响，故保守 page" in busy.impact
    assert "补采/核查 idle evidence" in busy.impact
    assert "已确认" not in busy.impact
    assert busy.urgency == "是"


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
        for result in evaluate_performance_rules(samples)
        if result.rule_id == f"PERF:homepage.first_card:{vantage}:idle"
    )

    assert idle.severity == "page"
    assert idle.values["gate_reason"] == "idle_cell"
    assert expected_impact in idle.impact
    assert idle.urgency == "是"


def test_probe_requires_origin_url(tmp_path: Path) -> None:
    runtime = JourneyMonitorRuntime(
        origin_url="",
        public_url="https://public.invalid",
        pipeline_lock_dir=tmp_path / ".pipeline.lock",
        browser_lock_path=tmp_path / "browser.lock",
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
    lock_dir = tmp_path / ".pipeline.lock"
    started = datetime(2026, 7, 18, tzinfo=UTC)
    sent: list[str] = []

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        sent.append(text)
        return {"skipped": False}

    monkeypatch.setattr("airadar.admin.alerts.send_alert_message", sender)

    store_samples(
        sample_path,
        [_sample(observed_at=started + timedelta(minutes=index), value_ms=4000) for index in range(20)],
    )
    confirmed: dict[str, object] = {}
    for now_minute in (20, 21, 22):
        if now_minute > 20:
            store_samples(
                sample_path,
                [_sample(observed_at=started + timedelta(minutes=now_minute - 1), value_ms=4000)],
            )
        confirmed = run_performance_alerts(
            sample_path=sample_path,
            state_path=state_path,
            evidence_dir=evidence_dir,
            pipeline_lock_dir=lock_dir,
            now=started + timedelta(minutes=now_minute),
        )
    assert confirmed["sent_count"] == 1

    # Public vantage is disabled afterwards (AI_RADAR_PUBLIC_URL unset): stale
    # public samples must not keep the rule firing, and the stale firing state
    # must resolve explicitly instead of going permanently stale.
    resolved_round = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=lock_dir,
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
        evidence_dir=evidence_dir,
        pipeline_lock_dir=lock_dir,
        now=started + timedelta(minutes=31),
        enabled_vantages=frozenset({"same_host_origin"}),
        send=sender,
    )
    assert not any(
        receipt["type"] == "resolved" and "same_host_public" in receipt["rule_id"]
        for receipt in second_clear["sent"]
    )
