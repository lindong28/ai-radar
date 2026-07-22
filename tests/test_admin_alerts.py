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
    evaluate_rules,
    run_alert_results_state_machine,
    run_alert_state_machine,
    send_alert_message,
)


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
    healthz_down.healthz_consecutive_failures = 2
    a3 = evaluate_rules(healthz_down)[2]
    assert a3.firing is True
    assert "healthz" in a3.detail


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
    assert "🔴 A4" in deliveries[0][0]
    assert "✅ A4" in deliveries[1][0]


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


def test_single_rule_cooldown_is_not_bypassed_by_severity_change(tmp_path: Path) -> None:
    state_path = tmp_path / "single-cooldown.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []

    run_alert_results_state_machine(
        [AlertRuleResult("TEST", "page", True, "firing", "action")],
        state_path=state_path,
        now=current,
        send=_recording_sender(calls),
    )
    suppressed = run_alert_results_state_machine(
        [
            AlertRuleResult(
                "TEST",
                "notice",
                True,
                "still firing",
                "action",
                severity="notice",
            )
        ],
        state_path=state_path,
        now=current + timedelta(minutes=1),
        send=_recording_sender(calls),
    )

    assert suppressed["sent_count"] == 0
    assert len(calls) == 1
    assert calls[0][1] == "page"


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
