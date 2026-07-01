from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from airadar.admin.alerts import (
    AlertSignals,
    evaluate_rules,
    run_alert_state_machine,
    send_feishu_message,
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
    sent: list[str] = []
    now = datetime.fromisoformat("2026-06-09T08:00:00+08:00")
    calls: list[tuple[str, float]] = []

    def healthz_down(url: str, timeout: float) -> bool:
        calls.append((url, timeout))
        return False

    first = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now,
        send=lambda text: sent.append(text),
        healthz_probe=healthz_down,
    )
    second = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=5),
        send=lambda text: sent.append(text),
        healthz_probe=healthz_down,
    )
    recovered = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=10),
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )

    assert calls == [
        ("http://127.0.0.1:8000/api/v1/healthz", 2.0),
        ("http://127.0.0.1:8000/api/v1/healthz", 2.0),
    ]
    assert first["results"][2]["firing"] is False
    assert second["results"][2]["firing"] is True
    assert recovered["results"][2]["firing"] is False
    assert "🔴 A3" in sent[0]
    assert "✅ A3" in sent[1]


def test_alert_state_machine_sends_firing_once_during_cooldown_then_resolved(tmp_path: Path) -> None:
    state_path = tmp_path / "alert-state.json"
    sent: list[str] = []
    now = datetime.fromisoformat("2026-06-02T08:00:00+08:00")
    firing = _normal_signals()
    firing.upstream_error_rate = 0.8

    first = run_alert_state_machine(
        firing,
        state_path=state_path,
        now=now,
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )
    second = run_alert_state_machine(
        firing,
        state_path=state_path,
        now=now + timedelta(minutes=10),
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )
    third = run_alert_state_machine(
        firing,
        state_path=state_path,
        now=now + timedelta(minutes=31),
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )
    resolved = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=40),
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == 1
    assert second["sent_count"] == 0
    assert third["sent_count"] == 1
    assert resolved["sent_count"] == 1
    assert len(sent) == 3
    assert sent[0].startswith("【AI Radar】")
    assert sent[-1].startswith("【AI Radar】")
    assert "🔴 A1" in sent[0]
    assert "故障类别" in sent[0]
    assert "处置方向" in sent[0]
    assert "✅ A1" in sent[-1]


def _a4_firing() -> AlertSignals:
    signals = _normal_signals()
    signals.fetch_failed_ratio = 0.8  # > a4 fetch_failed_ratio threshold (0.4)
    return signals


def test_a4_debounce_absorbs_transient_flap(tmp_path: Path) -> None:
    # nitter.net flaps for a single fetch round (~15 min) then recovers. With the
    # 30-min debounce, A4 must stay completely silent — no firing, no resolved —
    # so a transient that self-heals never reaches the on-call channel.
    state_path = tmp_path / "alert-state.json"
    sent: list[str] = []
    now = datetime.fromisoformat("2026-06-09T16:31:00+08:00")

    first = run_alert_state_machine(
        _a4_firing(),
        state_path=state_path,
        now=now,
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )
    recovered = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=15),
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == 0  # within debounce window → not yet confirmed
    assert recovered["sent_count"] == 0  # recovered before confirmation → silently absorbed
    assert sent == []


def test_a4_debounce_fires_after_sustained_outage_then_resolves(tmp_path: Path) -> None:
    # A genuine outage that outlasts the debounce window must fire once, and the
    # later recovery must send a resolved (because a firing was actually delivered).
    state_path = tmp_path / "alert-state.json"
    sent: list[str] = []
    now = datetime.fromisoformat("2026-06-09T16:31:00+08:00")

    first = run_alert_state_machine(
        _a4_firing(),
        state_path=state_path,
        now=now,
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )
    confirmed = run_alert_state_machine(
        _a4_firing(),
        state_path=state_path,
        now=now + timedelta(minutes=31),
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )
    resolved = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        now=now + timedelta(minutes=50),
        send=lambda text: sent.append(text),
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == 0  # debounced
    assert confirmed["sent_count"] == 1  # sustained past 30 min → fires
    assert resolved["sent_count"] == 1  # resolved after a real firing
    assert "🔴 A4" in sent[0]
    assert "✅ A4" in sent[1]


def test_send_feishu_message_posts_text_payload(monkeypatch) -> None:  # noqa: ANN001
    calls: list[tuple[str, dict[str, object], float]] = []

    class Response:
        status_code = 200
        text = "ok"

        def raise_for_status(self) -> None:
            return None

    def fake_post(url: str, json: dict[str, object], timeout: float) -> Response:
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr("airadar.admin.alerts.httpx.post", fake_post)

    result = send_feishu_message("https://example.test/webhook", "hello")

    assert result["status_code"] == 200
    assert calls == [
        (
            "https://example.test/webhook",
            {"msg_type": "text", "content": {"text": "hello"}},
            10.0,
        )
    ]
