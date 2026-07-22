from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from types import MappingProxyType

import pytest

from airadar.admin.alerts import (
    AlertRuleResult,
    AlertSignals,
    _project_lifecycles,
    evaluate_rules,
    run_alert_results_state_machine,
    run_alert_state_machine,
    send_alert_message,
)
from airadar.admin.thresholds import ALERT_THRESHOLDS


def _normal_signals() -> AlertSignals:
    return AlertSignals(
        upstream_sample_size=20,
        upstream_error_rate=0.0,
        upstream_schema_error_rate=0.8,
        stage_error_rate={"prefilter": 0.0, "scoring": 0.0, "enrich": 0.0},
        stage_p95_latency_ms={"prefilter": 1000, "scoring": 2000, "enrich": 3000},
        minutes_since_successful_pipeline=10,
        consecutive_skip_logs=0,
        server_error_rate=0.0,
        fetch_failed_ratio=0.0,
        items_today=300,
        stage_sample_count={"prefilter": 20, "scoring": 20, "enrich": 20},
        server_pv=100,
    )


def _healthz_ok(url: str, timeout: float) -> bool:
    assert url == "http://127.0.0.1:8000/api/v1/healthz"
    assert timeout <= 2.0
    return True


def _recording_sender(sent: list[tuple[str, str]]):
    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        sent.append((text, severity))
        return {"skipped": False}

    return sender


def test_evaluate_rules_covers_all_alerts_and_negative_schema_noise() -> None:
    normal = evaluate_rules(_normal_signals())

    assert [result.rule_id for result in normal] == ["A1", "A2", "A3", "A4"]
    assert all(not result.firing for result in normal)

    upstream = _normal_signals()
    upstream.upstream_error_rate = 0.8
    assert evaluate_rules(upstream)[0].firing is True
    assert "上游模型不可用" in evaluate_rules(upstream)[0].title

    stage = _normal_signals()
    stage.stage_error_rate["scoring"] = 0.8
    stage.stage_p95_latency_ms["prefilter"] = 26000
    stage.minutes_since_successful_pipeline = 60
    assert evaluate_rules(stage)[1].firing is True

    website = _normal_signals()
    website.server_error_rate = 0.2
    assert evaluate_rules(website)[2].firing is True

    ingestion = _normal_signals()
    ingestion.fetch_failed_ratio = 0.8
    ingestion.items_today = 10
    assert evaluate_rules(ingestion)[3].firing is True


def test_a2_heartbeat_tolerates_in_progress_runs_and_only_fires_on_real_stall() -> None:
    # A SKIP log means "pipeline already running" — that is liveness, not a fault.
    # A long run with many piled-up skips must NOT fire A2 on its own.
    busy = _normal_signals()
    busy.consecutive_skip_logs = 6
    busy.minutes_since_successful_pipeline = 60  # below the recalibrated bound
    assert evaluate_rules(busy)[1].firing is False

    # A genuine stall (no successful pipeline far beyond normal cadence) fires,
    # and folds the skip count in as diagnostic context.
    stalled = _normal_signals()
    stalled.stage_sample_count = {}
    stalled.minutes_since_successful_pipeline = 130
    stalled.consecutive_skip_logs = 8
    a2 = evaluate_rules(stalled)[1]
    assert a2.firing is True
    assert "130 分钟" in a2.detail
    assert "SKIP" in a2.detail


def test_a2_prefilter_latency_below_breakage_floor_does_not_page() -> None:
    # prefilter P95 是后台外部 LLM 调用的尾延迟，小样本下噪声大且总能自愈。
    # 真实「变慢但无害」的水平（如上游 provider 抖动到 ~12s）绝不能分页——
    # 只有持续到「真挂起」地板（25s）才触发。回归 8478 失准导致的反复贴线 flap。
    elevated = _normal_signals()
    elevated.stage_p95_latency_ms["prefilter"] = 12000
    assert evaluate_rules(elevated)[1].firing is False

    hung = _normal_signals()
    hung.stage_p95_latency_ms["prefilter"] = 26000
    a2 = evaluate_rules(hung)[1]
    assert a2.firing is True
    assert "prefilter P95" in a2.detail


def test_a3_fires_on_server_error_rate_or_confirmed_healthz_failures() -> None:
    healthy = _normal_signals()
    assert evaluate_rules(healthy)[2].firing is False

    errors = _normal_signals()
    errors.server_error_rate = 0.2
    a3 = evaluate_rules(errors)[2]
    assert a3.firing is True

    healthz_down = _normal_signals()
    healthz_down.server_pv = 0
    healthz_down.healthz_consecutive_failures = 2
    a3 = evaluate_rules(healthz_down)[2]
    assert a3.firing is True
    assert "healthz" in a3.detail


def test_a2_a3_minimum_sample_thresholds_are_fixed_closed_form_values() -> None:
    a2 = ALERT_THRESHOLDS["a2"]
    a3 = ALERT_THRESHOLDS["a3"]

    assert isinstance(a2, dict)
    assert isinstance(a3, dict)
    assert a2["min_samples"] == {"prefilter": 4, "scoring": 4, "enrich": 2}
    assert a3["min_pv"] == 20


@pytest.mark.parametrize(
    ("stage", "below_count", "at_count", "below_rate", "at_rate"),
    [
        ("prefilter", 3, 4, 1 / 3, 2 / 4),
        ("scoring", 3, 4, 1 / 3, 2 / 4),
        ("enrich", 1, 2, 1 / 1, 2 / 2),
    ],
)
def test_a2_stage_error_rate_requires_fixed_minimum_samples(
    stage: str,
    below_count: int,
    at_count: int,
    below_rate: float,
    at_rate: float,
) -> None:
    below_gate = _normal_signals()
    below_gate.stage_error_rate[stage] = below_rate
    below_gate.stage_sample_count[stage] = below_count

    at_gate = _normal_signals()
    at_gate.stage_error_rate[stage] = at_rate
    at_gate.stage_sample_count[stage] = at_count

    assert evaluate_rules(below_gate)[1].firing is False
    assert evaluate_rules(at_gate)[1].firing is True


def test_a3_server_error_rate_requires_twenty_page_views() -> None:
    below_gate = _normal_signals()
    below_gate.server_pv = 19
    below_gate.server_error_rate = 1 / 19

    at_gate = _normal_signals()
    at_gate.server_pv = 20
    at_gate.server_error_rate = 2 / 20

    assert evaluate_rules(below_gate)[2].firing is False
    assert evaluate_rules(at_gate)[2].firing is True


def test_a4_daily_insert_floor_is_time_proportional() -> None:
    early = _normal_signals()
    early.items_today = 3
    early.minutes_elapsed_today = 30

    a4 = evaluate_rules(early)[3]

    assert a4.firing is False
    assert a4.values["daily_inserted_floor"] == 127
    assert a4.values["daily_inserted_floor_elapsed"] == 2

    lagging = _normal_signals()
    lagging.items_today = 0
    lagging.minutes_elapsed_today = 720

    a4 = evaluate_rules(lagging)[3]

    assert a4.firing is True
    assert a4.values["daily_inserted_floor_elapsed"] == 63


def test_a3_active_healthz_probe_persists_failures_and_recovers(tmp_path: Path) -> None:
    state_path = tmp_path / "alert-state.json"
    deliveries: list[tuple[str, str]] = []
    now = datetime.fromisoformat("2026-06-09T08:00:00+08:00")
    calls: list[tuple[str, float]] = []

    def healthz_down(url: str, timeout: float) -> bool:
        calls.append((url, timeout))
        return False

    first = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=healthz_down,
    )
    second = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=5),
        send=_recording_sender(deliveries),
        healthz_probe=healthz_down,
    )
    recovered = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=10),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )

    assert calls == [
        ("http://127.0.0.1:8000/api/v1/healthz", 2.0),
        ("http://127.0.0.1:8000/api/v1/healthz", 2.0),
    ]
    assert first["results"][2]["firing"] is False
    assert second["results"][2]["firing"] is True
    assert recovered["results"][2]["firing"] is False
    assert "🔴 A3" in deliveries[0][0]
    assert "✅ A3" in deliveries[1][0]


@pytest.mark.parametrize("rule_id", ["A1", "A3"])
def test_fixed_page_rules_preserve_success_cooldown_and_resolve_timing(
    tmp_path: Path,
    rule_id: str,
) -> None:
    state_path = tmp_path / "alert-state.json"
    deliveries: list[tuple[str, str]] = []
    now = datetime.fromisoformat("2026-06-02T08:00:00+08:00")
    firing = _normal_signals()
    if rule_id == "A1":
        firing.upstream_error_rate = 0.8
    else:
        firing.server_error_rate = 0.2

    first = run_alert_state_machine(
        firing,
        state_path=state_path,
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    second = run_alert_state_machine(
        firing,
        state_path=state_path,
        now=now + timedelta(minutes=10),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    third = run_alert_state_machine(
        firing,
        state_path=state_path,
        now=now + timedelta(minutes=31),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    resolved = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=40),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == 1
    assert second["sent_count"] == 0
    assert third["sent_count"] == 1
    assert resolved["sent_count"] == 1
    assert len(deliveries) == 3
    assert deliveries[0][0].startswith("【AI Radar】")
    assert deliveries[-1][0].startswith("【AI Radar】")
    assert f"🔴 {rule_id}" in deliveries[0][0]
    assert "故障类别" in deliveries[0][0]
    assert "处置方向" in deliveries[0][0]
    assert f"✅ {rule_id}" in deliveries[-1][0]


def _a4_firing() -> AlertSignals:
    signals = _normal_signals()
    signals.fetch_failed_ratio = 0.8  # > a4 fetch_failed_ratio threshold (0.4)
    return signals


def _a4_items_floor_firing() -> AlertSignals:
    signals = _normal_signals()
    signals.items_today = 0
    signals.minutes_elapsed_today = 720
    return signals


@pytest.mark.parametrize(
    ("fetch_failed_ratio", "items_today", "expected_severity", "impact", "urgency", "detail"),
    [
        (0.8, 300, "notice", "当前摄取量正常", "无需立即处置", "fetch 失败率"),
        (0.0, 0, "page", "文章更新可能停滞", "需立即核查", "items 增量"),
        (0.8, 0, "page", "文章更新可能停滞", "需立即核查", "fetch 失败率"),
    ],
)
def test_a4_branches_choose_severity_channel_and_operator_message(
    tmp_path: Path,
    fetch_failed_ratio: float,
    items_today: int,
    expected_severity: str,
    impact: str,
    urgency: str,
    detail: str,
) -> None:
    signals = _normal_signals()
    signals.fetch_failed_ratio = fetch_failed_ratio
    signals.items_today = items_today
    signals.minutes_elapsed_today = 720
    a4 = evaluate_rules(signals)[3]
    calls: list[tuple[str, str]] = []

    payload = run_alert_results_state_machine(
        [a4],
        state_path=tmp_path / f"a4-{expected_severity}.json",
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=_recording_sender(calls),
        thresholds={
            "a4": {
                "debounce_minutes_by_severity": {"page": 0, "notice": 0},
            }
        },
    )

    assert a4.firing is True
    assert a4.severity == expected_severity
    assert impact in a4.impact
    assert urgency in a4.urgency
    assert detail in a4.detail
    assert "X(nitter)" in a4.action
    assert "Mp2RSS" in a4.action
    assert "evidence" not in a4.action.lower()
    assert [(row["effective_severity"], row["channel"]) for row in payload["sent"]] == [
        (expected_severity, "NOTIFICATION" if expected_severity == "notice" else "ALERT")
    ]
    assert calls[0][1] == expected_severity


def test_a4_debounce_absorbs_transient_flap(tmp_path: Path) -> None:
    # nitter.net flaps for a single fetch round (~15 min) then recovers. With the
    # 30-min debounce, A4 must stay completely silent — no firing, no resolved —
    # so a transient that self-heals never reaches the on-call channel.
    state_path = tmp_path / "alert-state.json"
    deliveries: list[tuple[str, str]] = []
    now = datetime.fromisoformat("2026-06-09T16:31:00+08:00")

    first = run_alert_state_machine(
        _a4_firing(),
        state_path=state_path,
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    recovered = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=15),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == 0  # within debounce window → not yet confirmed
    assert recovered["sent_count"] == 0  # recovered before confirmation → silently absorbed
    assert deliveries == []


def test_a4_debounce_fires_after_sustained_outage_then_resolves(tmp_path: Path) -> None:
    # A genuine outage that outlasts the debounce window must fire once, and the
    # later recovery must send a resolved (because a firing was actually delivered).
    state_path = tmp_path / "alert-state.json"
    deliveries: list[tuple[str, str]] = []
    now = datetime.fromisoformat("2026-06-09T16:31:00+08:00")

    first = run_alert_state_machine(
        _a4_firing(),
        state_path=state_path,
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    confirmed = run_alert_state_machine(
        _a4_firing(),
        state_path=state_path,
        now=now + timedelta(minutes=31),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    resolved = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=50),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == 0  # debounced
    assert confirmed["sent_count"] == 1  # sustained past 30 min → fires
    assert resolved["sent_count"] == 1  # resolved after a real firing
    assert "🟡 A4" in deliveries[0][0]
    assert deliveries[0][1] == "notice"
    assert "✅ A4" in deliveries[1][0]


def test_a4_items_floor_pages_on_the_first_round(tmp_path: Path) -> None:
    deliveries: list[tuple[str, str]] = []

    first = run_alert_state_machine(
        _a4_items_floor_firing(),
        state_path=tmp_path / "items-floor.json",
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == 1
    assert deliveries[0][1] == "page"
    assert "🔴 A4" in deliveries[0][0]


def test_alert_rule_result_is_frozen_and_serializable_with_message_slots() -> None:
    result = AlertRuleResult(
        rule_id="TEST",
        title="test",
        firing=True,
        detail="detail",
        action="action",
        severity="notice",
        impact="users unaffected",
        urgency="no",
    )

    with pytest.raises(FrozenInstanceError):
        result.severity = "page"  # type: ignore[misc]
    assert json.loads(json.dumps(asdict(result))) == asdict(result)


@pytest.mark.parametrize(
    ("severity", "emoji", "channel"),
    [("page", "🔴", "ALERT"), ("notice", "🟡", "NOTIFICATION")],
)
def test_state_machine_routes_fire_and_resolve_on_persisted_episode_severity(
    tmp_path: Path,
    severity: str,
    emoji: str,
    channel: str,
) -> None:
    state_path = tmp_path / f"{severity}.json"
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    firing = AlertRuleResult(
        rule_id="TEST",
        title="route",
        firing=True,
        detail="detail",
        action="action",
        severity=severity,  # type: ignore[arg-type]
        impact="impact",
        urgency="urgency",
    )
    recovered = AlertRuleResult(
        rule_id="TEST",
        title="route",
        firing=False,
        detail="ok",
        action="action",
    )

    fired = run_alert_results_state_machine(
        [firing], state_path=state_path, now=now, send=_recording_sender(calls)
    )
    resolved = run_alert_results_state_machine(
        [recovered],
        state_path=state_path,
        now=now + timedelta(minutes=5),
        send=_recording_sender(calls),
    )

    receipts = [*fired["sent"], *resolved["sent"]]
    assert len(receipts) == len(calls) == 2
    assert [(receipt["type"], receipt["effective_severity"], receipt["channel"]) for receipt in receipts] == [
        ("firing", severity, channel),
        ("resolved", severity, channel),
    ]
    assert calls[0][1] == calls[1][1] == severity
    assert f"{emoji} TEST" in calls[0][0]
    assert "影响：impact" in calls[0][0]
    assert "需否立即处置：urgency" in calls[0][0]


def test_legacy_flat_state_without_severity_recovers_with_page_resolved(tmp_path: Path) -> None:
    state_path = tmp_path / "legacy.json"
    state_path.write_text(
        json.dumps(
            {
                "TEST": {
                    "state": "firing",
                    "since": "2026-07-22T07:00:00+08:00",
                    "last_notified": "2026-07-22T07:01:00+08:00",
                    "detail": "legacy",
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    payload = run_alert_results_state_machine(
        [AlertRuleResult("TEST", "legacy", False, "ok", "none")],
        state_path=state_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=_recording_sender(calls),
    )

    assert len(calls) == 1
    assert calls[0][1] == "page"
    assert payload["sent"][0]["effective_severity"] == "page"
    assert payload["sent"][0]["channel"] == "ALERT"


def test_failed_firing_retries_until_success_then_allows_resolve(tmp_path: Path) -> None:
    state_path = tmp_path / "retry.json"
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    outcomes = iter([{"skipped": True}, {"skipped": False}, {"skipped": False}])
    calls: list[tuple[str, str]] = []

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        calls.append((text, severity))
        return next(outcomes)

    firing = AlertRuleResult("TEST", "retry", True, "detail", "action")
    recovered = AlertRuleResult("TEST", "retry", False, "ok", "action")

    failed = run_alert_results_state_machine([firing], state_path=state_path, now=now, send=sender)
    failed_state = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    retried = run_alert_results_state_machine(
        [firing], state_path=state_path, now=now + timedelta(minutes=5), send=sender
    )
    successful_state = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    resolved = run_alert_results_state_machine(
        [recovered], state_path=state_path, now=now + timedelta(minutes=6), send=sender
    )

    assert failed["sent_count"] == retried["sent_count"] == resolved["sent_count"] == 1
    assert failed_state["state"] == "firing"
    assert failed_state["last_notified"] is None
    assert failed_state["announced"] is False
    assert successful_state["last_notified"] == (now + timedelta(minutes=5)).isoformat()
    assert successful_state["announced"] is True
    assert ["✅" in text for text, _severity in calls] == [False, False, True]


@pytest.mark.parametrize("rule_id", ["A1", "A3"])
def test_fixed_page_rules_retry_failed_firing_without_cooldown(
    tmp_path: Path,
    rule_id: str,
) -> None:
    state_path = tmp_path / f"{rule_id}.json"
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    signals = _normal_signals()
    if rule_id == "A1":
        signals.upstream_error_rate = 0.8
    else:
        signals.server_error_rate = 0.2
    calls: list[tuple[str, str]] = []

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        calls.append((text, severity))
        return {"skipped": len(calls) == 1}

    first = run_alert_state_machine(
        signals,
        state_path=state_path,
        now=now,
        send=sender,
        healthz_probe=_healthz_ok,
    )
    retried = run_alert_state_machine(
        signals,
        state_path=state_path,
        now=now + timedelta(minutes=5),
        send=sender,
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == retried["sent_count"] == 1
    assert [severity for _text, severity in calls] == ["page", "page"]
    assert all(f"🔴 {rule_id}" in text for text, _severity in calls)


def test_unannounced_episode_closes_silently_and_failed_resolve_is_not_retried(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    firing = AlertRuleResult("TEST", "delivery", True, "detail", "action")
    recovered = AlertRuleResult("TEST", "delivery", False, "ok", "action")

    never_announced_path = tmp_path / "never-announced.json"
    failed_calls: list[tuple[str, str]] = []

    def failing_fire(text: str, *, severity: str = "page") -> dict[str, object]:
        failed_calls.append((text, severity))
        return {"skipped": True}

    run_alert_results_state_machine([firing], state_path=never_announced_path, now=now, send=failing_fire)
    closed = run_alert_results_state_machine(
        [recovered],
        state_path=never_announced_path,
        now=now + timedelta(minutes=1),
        send=failing_fire,
    )
    assert closed["sent_count"] == 0
    assert len(failed_calls) == 1

    announced_path = tmp_path / "announced.json"
    resolve_calls: list[tuple[str, str]] = []
    resolve_failure = {"skipped": True}

    def success_then_fail(text: str, *, severity: str = "page") -> dict[str, object]:
        resolve_calls.append((text, severity))
        return {"skipped": False} if len(resolve_calls) == 1 else resolve_failure

    run_alert_results_state_machine([firing], state_path=announced_path, now=now, send=success_then_fail)
    failed_resolve = run_alert_results_state_machine(
        [recovered],
        state_path=announced_path,
        now=now + timedelta(minutes=1),
        send=success_then_fail,
    )
    next_ok = run_alert_results_state_machine(
        [recovered],
        state_path=announced_path,
        now=now + timedelta(minutes=2),
        send=success_then_fail,
    )
    final_state = json.loads(announced_path.read_text(encoding="utf-8"))["TEST"]

    assert failed_resolve["sent_count"] == 1
    assert failed_resolve["sent"][0]["effective_severity"] == "page"
    assert failed_resolve["sent"][0]["channel"] == "ALERT"
    assert failed_resolve["sent"][0]["send_result"] is resolve_failure
    assert next_ok["sent_count"] == 0
    assert len(resolve_calls) == 2
    assert final_state["state"] == "ok"
    assert final_state["announced"] is False


def _delivery_outcome(case: str) -> object:
    return {
        "success_dict": {"skipped": False},
        "success_mapping": MappingProxyType({"skipped": False}),
        "skipped_true": {"skipped": True},
        "skipped_zero": {"skipped": 0},
        "missing_skipped": {"status": "sent"},
        "none": None,
        "non_mapping": ["malformed"],
    }[case]


@pytest.mark.parametrize(
    "outcome_case",
    [
        "success_dict",
        "success_mapping",
        "skipped_true",
        "skipped_zero",
        "missing_skipped",
        "none",
        "non_mapping",
    ],
)
@pytest.mark.parametrize("followup", ["still_firing", "recover"])
def test_fresh_fixed_severity_delivery_outcome_matrix(
    tmp_path: Path,
    outcome_case: str,
    followup: str,
) -> None:
    state_path = tmp_path / "matrix.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    incoming = AlertRuleResult("TEST", "matrix", True, "current firing", "action")
    recovered = AlertRuleResult("TEST", "matrix", False, "ok", "action")

    outcome = _delivery_outcome(outcome_case)
    calls: list[tuple[str, str, object]] = []

    def sender(text: str, *, severity: str = "page") -> object:
        send_result = outcome if not calls else {"skipped": False}
        calls.append((text, severity, send_result))
        return send_result

    attempted = run_alert_results_state_machine(
        [incoming], state_path=state_path, now=current, send=sender
    )
    attempted_state = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    succeeded = outcome_case in {"success_dict", "success_mapping"}

    assert attempted["sent_count"] == len(calls) == 1
    receipt = attempted["sent"][0]
    assert receipt["effective_severity"] == "page"
    assert receipt["channel"] == "ALERT"
    assert receipt["send_result"] is outcome

    if succeeded:
        assert attempted_state["announced"] is True
        assert attempted_state["last_notified"] == current.isoformat()
    else:
        assert attempted_state["announced"] is False
        assert attempted_state["last_notified"] is None
    assert attempted_state["severity"] == "page"
    assert "delivery_success_recorded" not in attempted_state
    assert "pending_severity" not in attempted_state

    followup_result = incoming if followup == "still_firing" else recovered
    followed = run_alert_results_state_machine(
        [followup_result],
        state_path=state_path,
        now=current + timedelta(minutes=1),
        send=sender,
    )

    if followup == "still_firing":
        expected_followup_attempts = 0 if succeeded else 1
        assert followed["sent_count"] == expected_followup_attempts
        assert len(calls) == 1 + expected_followup_attempts
        if not succeeded:
            assert followed["sent"][0]["effective_severity"] == "page"
            assert followed["sent"][0]["channel"] == "ALERT"
            assert followed["sent"][0]["send_result"] == {"skipped": False}
    else:
        if succeeded:
            assert followed["sent_count"] == 1
            assert followed["sent"][0]["type"] == "resolved"
            assert followed["sent"][0]["effective_severity"] == "page"
            assert followed["sent"][0]["channel"] == "ALERT"
            assert followed["sent"][0]["send_result"] == {"skipped": False}
        else:
            assert followed["sent_count"] == 0
            assert len(calls) == 1


def test_legacy_last_notified_uses_single_cooldown_before_retry(tmp_path: Path) -> None:
    state_path = tmp_path / "legacy-cooldown.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "TEST": {
                    "state": "firing",
                    "since": "2026-07-22T07:00:00+08:00",
                    "last_notified": current.isoformat(),
                    "detail": "legacy",
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []
    firing = AlertRuleResult("TEST", "legacy", True, "still firing", "action")

    suppressed = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        now=current + timedelta(minutes=10),
        send=_recording_sender(calls),
    )
    retried = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        now=current + timedelta(minutes=31),
        send=_recording_sender(calls),
    )

    assert suppressed["sent_count"] == 0
    assert retried["sent_count"] == 1
    assert calls[0][1] == "page"


def _severity_result(severity: str, *, firing: bool = True) -> AlertRuleResult:
    return AlertRuleResult(
        "A4",
        f"{severity} condition",
        firing,
        f"{severity} detail" if firing else "ok",
        "action",
        severity=severity,  # type: ignore[arg-type]
    )


def _receipt_identities(payload: dict[str, object]) -> list[tuple[str, str, str]]:
    sent = payload["sent"]
    assert isinstance(sent, list)
    return [
        (str(receipt["rule_id"]), str(receipt["effective_severity"]), str(receipt["type"]))
        for receipt in sent
    ]


def _firing_lifecycle_severities(state_path: Path, rule_id: str = "A4") -> set[str]:
    entry = json.loads(state_path.read_text(encoding="utf-8"))[rule_id]
    return {
        severity
        for severity, lifecycle in entry["lifecycles"].items()
        if lifecycle["state"] == "firing"
    }


def test_double_firing_current_state_projects_page_without_silent_healing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "double-firing.json"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "PERF:double": {
                    "state": "firing",
                    "since": (started + timedelta(minutes=1)).isoformat(),
                    "last_notified": (started + timedelta(minutes=1)).isoformat(),
                    "detail": "notice projection",
                    "severity": "notice",
                    "announced": True,
                    "lifecycles": {
                        "page": {
                            "state": "firing",
                            "since": started.isoformat(),
                            "last_notified": started.isoformat(),
                            "detail": "confirmed page",
                            "announced": True,
                        },
                        "notice": {
                            "state": "firing",
                            "since": (started + timedelta(minutes=1)).isoformat(),
                            "last_notified": (started + timedelta(minutes=1)).isoformat(),
                            "detail": "notice projection",
                            "announced": True,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    first = run_alert_results_state_machine(
        [], state_path=state_path, now=started + timedelta(minutes=5), send=_recording_sender(calls)
    )
    first_saved = state_path.read_text(encoding="utf-8")
    second = run_alert_results_state_machine(
        [], state_path=state_path, now=started + timedelta(minutes=5), send=_recording_sender(calls)
    )
    second_saved = state_path.read_text(encoding="utf-8")
    entry = json.loads(second_saved)["PERF:double"]

    assert first["sent_count"] == second["sent_count"] == 0
    assert calls == []
    assert entry["state"] == "firing"
    assert entry["severity"] == "page"
    assert entry["detail"] == "confirmed page"
    assert _firing_lifecycle_severities(state_path, "PERF:double") == {"page", "notice"}
    assert entry["lifecycles"]["notice"]["state"] == "firing"
    assert entry["lifecycles"]["notice"]["announced"] is True
    assert first_saved == second_saved


def test_flat_projection_prefers_firing_page_before_notice_preference() -> None:
    projected = _project_lifecycles(
        {
            "page": {
                "state": "firing",
                "since": "2026-07-22T08:00:00+08:00",
                "last_notified": "2026-07-22T08:00:00+08:00",
                "detail": "confirmed page",
                "announced": True,
            },
            "notice": {
                "state": "firing",
                "since": "2026-07-22T08:01:00+08:00",
                "last_notified": "2026-07-22T08:01:00+08:00",
                "detail": "notice preference",
                "announced": True,
            },
        },
        preferred_severity="notice",
    )

    assert projected["state"] == "firing"
    assert projected["severity"] == "page"
    assert projected["detail"] == "confirmed page"


def test_pending_notice_to_page_closes_silently_and_pages_immediately(tmp_path: Path) -> None:
    state_path = tmp_path / "pending-notice-to-page.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []

    pending = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current,
        send=_recording_sender(calls),
        thresholds={"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}},
    )
    upgraded = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=current + timedelta(minutes=15),
        send=_recording_sender(calls),
        thresholds={"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}},
    )

    assert pending["sent_count"] == 0
    assert _receipt_identities(upgraded) == [("A4", "page", "firing")]
    assert len(calls) == 1
    assert calls[0][1] == "page"
    assert "✅" not in calls[0][0]
    state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    assert state["state"] == "firing"
    assert state["severity"] == "page"
    assert state["lifecycles"]["notice"]["state"] == "ok"
    assert state["lifecycles"]["page"]["state"] == "firing"


def test_pending_notice_to_page_failed_send_retries_without_fake_resolve(tmp_path: Path) -> None:
    state_path = tmp_path / "pending-notice-failed-page.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    outcomes = iter([{"skipped": True}, {"skipped": False}])

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        calls.append((text, severity))
        return next(outcomes)

    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}}
    pending = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current,
        send=sender,
        thresholds=thresholds,
    )
    failed_page = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=current + timedelta(minutes=15),
        send=sender,
        thresholds=thresholds,
    )
    failed_state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    retried = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=current + timedelta(minutes=16),
        send=sender,
        thresholds=thresholds,
    )

    assert pending["sent_count"] == 0
    assert _receipt_identities(failed_page) == [("A4", "page", "firing")]
    assert failed_page["sent"][0]["send_result"] == {"skipped": True}
    assert failed_state["announced"] is False
    assert failed_state["lifecycles"]["notice"]["state"] == "ok"
    assert failed_state["lifecycles"]["page"]["announced"] is False
    assert _receipt_identities(retried) == [("A4", "page", "firing")]
    assert all("✅" not in text for text, _severity in calls)


def test_announced_notice_to_page_orders_resolve_before_firing(tmp_path: Path) -> None:
    state_path = tmp_path / "announced-notice-to-page.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}}

    run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    announced = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current + timedelta(minutes=31),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    upgraded = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=current + timedelta(minutes=32),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )

    assert _receipt_identities(announced) == [("A4", "notice", "firing")]
    assert _receipt_identities(upgraded) == [
        ("A4", "notice", "resolved"),
        ("A4", "page", "firing"),
    ]
    assert [receipt["channel"] for receipt in upgraded["sent"]] == [
        "NOTIFICATION",
        "ALERT",
    ]
    assert [(severity, "✅" in text) for text, severity in calls[-2:]] == [
        ("notice", True),
        ("page", False),
    ]


def test_page_to_first_notice_orders_resolve_then_bypasses_notice_debounce(tmp_path: Path) -> None:
    state_path = tmp_path / "page-to-notice.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}}

    run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    transitioned = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current + timedelta(minutes=1),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )

    assert _receipt_identities(transitioned) == [
        ("A4", "page", "resolved"),
        ("A4", "notice", "firing"),
    ]
    assert [severity for _text, severity in calls] == ["page", "page", "notice"]


def test_old_flat_announced_page_migrates_and_transitions_to_notice_in_order(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "old-flat-page-to-notice.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "A4": {
                    "state": "firing",
                    "since": current.isoformat(),
                    "last_notified": current.isoformat(),
                    "detail": "legacy announced page",
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    transitioned = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current + timedelta(minutes=1),
        send=_recording_sender(calls),
        thresholds={"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}},
    )

    assert _receipt_identities(transitioned) == [
        ("A4", "page", "resolved"),
        ("A4", "notice", "firing"),
    ]
    assert [(severity, "✅" in text) for text, severity in calls] == [
        ("page", True),
        ("notice", False),
    ]
    assert _firing_lifecycle_severities(state_path) == {"notice"}


@pytest.mark.parametrize(("first_severity", "second_severity"), [("notice", "page"), ("page", "notice")])
def test_round_trip_severity_uses_only_target_cooldown(
    tmp_path: Path,
    first_severity: str,
    second_severity: str,
) -> None:
    state_path = tmp_path / f"{first_severity}-{second_severity}-{first_severity}.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 0}}}

    first = run_alert_results_state_machine(
        [_severity_result(first_severity)],
        state_path=state_path,
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == {first_severity}
    second = run_alert_results_state_machine(
        [_severity_result(second_severity)],
        state_path=state_path,
        now=current + timedelta(minutes=1),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == {second_severity}
    returned_early = run_alert_results_state_machine(
        [_severity_result(first_severity)],
        state_path=state_path,
        now=current + timedelta(minutes=2),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == {first_severity}
    returned_after_cooldown = run_alert_results_state_machine(
        [_severity_result(first_severity)],
        state_path=state_path,
        now=current + timedelta(minutes=31),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == {first_severity}

    assert _receipt_identities(first) == [("A4", first_severity, "firing")]
    assert _receipt_identities(second) == [
        ("A4", first_severity, "resolved"),
        ("A4", second_severity, "firing"),
    ]
    assert _receipt_identities(returned_early) == [("A4", second_severity, "resolved")]
    assert _receipt_identities(returned_after_cooldown) == [("A4", first_severity, "firing")]
    state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    assert set(state["lifecycles"]) == {"page", "notice"}
    assert state["lifecycles"][first_severity]["last_notified"] == (
        current + timedelta(minutes=31)
    ).isoformat()
    assert state["lifecycles"][second_severity]["last_notified"] == (
        current + timedelta(minutes=1)
    ).isoformat()


def test_clear_resolves_only_announced_lifecycle(tmp_path: Path) -> None:
    state_path = tmp_path / "clear-announced-only.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "A4": {
                    "state": "firing",
                    "since": current.isoformat(),
                    "last_notified": current.isoformat(),
                    "detail": "page announced",
                    "severity": "page",
                    "announced": True,
                    "lifecycles": {
                        "page": {
                            "state": "firing",
                            "since": current.isoformat(),
                            "last_notified": current.isoformat(),
                            "detail": "page announced",
                            "announced": True,
                        },
                        "notice": {
                            "state": "firing",
                            "since": current.isoformat(),
                            "last_notified": None,
                            "detail": "notice pending",
                            "announced": False,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    cleared = run_alert_results_state_machine(
        [_severity_result("page", firing=False)],
        state_path=state_path,
        now=current + timedelta(minutes=5),
        send=_recording_sender(calls),
    )

    assert _receipt_identities(cleared) == [("A4", "page", "resolved")]
    state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    assert state["state"] == "ok"
    assert all(lifecycle["state"] == "ok" for lifecycle in state["lifecycles"].values())


def test_legacy_pending_a4_inherits_since_when_condition_becomes_notice(tmp_path: Path) -> None:
    state_path = tmp_path / "legacy-pending-a4.json"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "A4": {
                    "state": "firing",
                    "since": started.isoformat(),
                    "last_notified": None,
                    "detail": "legacy pending page",
                    "announced": False,
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}}

    pending = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=started + timedelta(minutes=15),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    first_saved = state_path.read_text(encoding="utf-8")
    reloaded = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=started + timedelta(minutes=15),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    second_saved = state_path.read_text(encoding="utf-8")
    announced = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=started + timedelta(minutes=31),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )

    assert pending["sent_count"] == reloaded["sent_count"] == 0
    assert first_saved == second_saved
    assert _receipt_identities(announced) == [("A4", "notice", "firing")]
    state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    assert state["lifecycles"]["notice"]["since"] == started.isoformat()


def test_legacy_unannounced_transition_does_not_fake_resolve_or_cross_throttle(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "legacy-unannounced-transition.json"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "A4": {
                    "state": "firing",
                    "since": started.isoformat(),
                    "last_notified": started.isoformat(),
                    "detail": "legacy failed notice",
                    "severity": "notice",
                    "announced": False,
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    upgraded = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=started + timedelta(minutes=1),
        send=_recording_sender(calls),
    )

    assert _receipt_identities(upgraded) == [("A4", "page", "firing")]
    assert len(calls) == 1
    assert "✅" not in calls[0][0]


def test_load_save_normalizes_legacy_rule_entries_without_touching_healthz(tmp_path: Path) -> None:
    state_path = tmp_path / "normalize-all.json"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    healthz = {
        "consecutive_failures": 1,
        "last_checked": started.isoformat(),
        "last_ok": False,
        "url": "http://127.0.0.1:8000/api/v1/healthz",
    }
    state_path.write_text(
        json.dumps(
            {
                "A4": {
                    "state": "firing",
                    "since": started.isoformat(),
                    "last_notified": (started + timedelta(minutes=1)).isoformat(),
                    "detail": "legacy announced page",
                },
                "healthz_probe": healthz,
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    first = run_alert_results_state_machine(
        [], state_path=state_path, now=started + timedelta(minutes=5), send=_recording_sender(calls)
    )
    first_saved = state_path.read_text(encoding="utf-8")
    second = run_alert_results_state_machine(
        [], state_path=state_path, now=started + timedelta(minutes=5), send=_recording_sender(calls)
    )
    second_saved = state_path.read_text(encoding="utf-8")
    state = json.loads(second_saved)

    assert first["sent_count"] == second["sent_count"] == 0
    assert calls == []
    assert first_saved == second_saved
    assert state["A4"]["severity"] == "page"
    assert state["A4"]["announced"] is True
    assert state["A4"]["lifecycles"]["page"] == {
        "state": "firing",
        "since": started.isoformat(),
        "last_notified": (started + timedelta(minutes=1)).isoformat(),
        "detail": "legacy announced page",
        "announced": True,
    }
    assert state["healthz_probe"] == healthz


def test_severity_debounce_map_falls_back_to_legacy_single_value(tmp_path: Path) -> None:
    state_path = tmp_path / "debounce-fallback.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    thresholds = {
        "a4": {
            "debounce_minutes": 10,
            "debounce_minutes_by_severity": {"page": 0},
        }
    }

    pending = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    announced = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current + timedelta(minutes=11),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )

    assert pending["sent_count"] == 0
    assert _receipt_identities(announced) == [("A4", "notice", "firing")]


def test_failed_firing_after_severity_transition_retries_without_cooldown(tmp_path: Path) -> None:
    state_path = tmp_path / "transition-retry.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    outcomes = iter(
        [
            {"skipped": False},
            {"skipped": False},
            {"skipped": True},
            {"skipped": False},
        ]
    )
    calls: list[tuple[str, str]] = []

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        calls.append((text, severity))
        return next(outcomes)

    run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        now=current,
        send=sender,
    )
    transitioned = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=current + timedelta(minutes=1),
        send=sender,
    )
    failed_state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    retried = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        now=current + timedelta(minutes=2),
        send=sender,
    )

    assert _receipt_identities(transitioned) == [
        ("A4", "notice", "resolved"),
        ("A4", "page", "firing"),
    ]
    assert transitioned["sent"][1]["send_result"] == {"skipped": True}
    assert failed_state["lifecycles"]["page"]["announced"] is False
    assert failed_state["lifecycles"]["page"]["last_notified"] is None
    assert _receipt_identities(retried) == [("A4", "page", "firing")]


@pytest.mark.parametrize("rule_id", ["A1", "A3"])
def test_legacy_fixed_page_cooldown_migrates_idempotently(tmp_path: Path, rule_id: str) -> None:
    state_path = tmp_path / f"legacy-{rule_id}.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                rule_id: {
                    "state": "firing",
                    "since": (current - timedelta(minutes=5)).isoformat(),
                    "last_notified": current.isoformat(),
                    "detail": "legacy announced",
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []
    firing = AlertRuleResult(rule_id, "legacy", True, "still firing", "action")

    first = run_alert_results_state_machine(
        [firing], state_path=state_path, now=current + timedelta(minutes=10), send=_recording_sender(calls)
    )
    first_saved = state_path.read_text(encoding="utf-8")
    second = run_alert_results_state_machine(
        [firing], state_path=state_path, now=current + timedelta(minutes=10), send=_recording_sender(calls)
    )
    second_saved = state_path.read_text(encoding="utf-8")
    after_cooldown = run_alert_results_state_machine(
        [firing], state_path=state_path, now=current + timedelta(minutes=31), send=_recording_sender(calls)
    )

    assert first["sent_count"] == second["sent_count"] == 0
    assert first_saved == second_saved
    assert _receipt_identities(after_cooldown) == [(rule_id, "page", "firing")]
    state = json.loads(state_path.read_text(encoding="utf-8"))[rule_id]
    assert set(state["lifecycles"]) == {"page"}


def test_send_alert_message_calls_im_notify_alert_without_dedup(monkeypatch) -> None:  # noqa: ANN001
    calls: list[tuple[list[str], bool, bool]] = []

    def fake_run(command: list[str], *, capture_output: bool, text: bool, timeout: float) -> CompletedProcess[str]:
        assert timeout == 15.0
        calls.append((command, capture_output, text))
        return CompletedProcess(command, 0, stdout="sent\n", stderr="")

    monkeypatch.setattr("airadar.admin.alerts.subprocess.run", fake_run)

    result = send_alert_message("【AI Radar】\nhello")

    assert result == {"skipped": False, "returncode": 0}
    assert calls == [(["im-notify", "--alert", "【AI Radar】\nhello"], True, True)]
    assert "--dedup-key" not in calls[0][0]


def test_send_alert_message_routes_notice_without_alert_flag(monkeypatch) -> None:  # noqa: ANN001
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, capture_output: bool, text: bool, timeout: float) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, stdout="sent\n", stderr="")

    monkeypatch.setattr("airadar.admin.alerts.subprocess.run", fake_run)

    result = send_alert_message("notice", severity="notice")

    assert result == {"skipped": False, "returncode": 0}
    assert calls == [["im-notify", "notice"]]


def test_send_alert_message_logs_failure_without_raising(monkeypatch, caplog) -> None:  # noqa: ANN001
    def fake_run(command: list[str], *, capture_output: bool, text: bool, timeout: float) -> CompletedProcess[str]:
        assert timeout == 15.0
        return CompletedProcess(command, 7, stdout="", stderr="delivery unavailable\n")

    monkeypatch.setattr("airadar.admin.alerts.subprocess.run", fake_run)

    result = send_alert_message("hello")

    assert result == {"skipped": True, "reason": "im-notify exited with status 7"}
    assert "im-notify alert delivery failed" in caplog.text
    assert "delivery unavailable" in caplog.text
