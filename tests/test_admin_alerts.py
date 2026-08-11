from __future__ import annotations

import json
import logging
import os
import plistlib
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from datetime import datetime, timedelta
from pathlib import Path
from subprocess import CompletedProcess
from types import MappingProxyType

import pytest

from airadar.admin import alerts as alerts_module
from airadar.admin.alerts import (
    AlertRuleResult,
    AlertSignals,
    _project_lifecycles,
    evaluate_rules,
    reserve_alert_evaluation_sequence,
    run_alert_results_state_machine,
    run_alert_state_machine,
    run_pricing_notifications,
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


def _state_without_evaluation_metadata(serialized: str) -> dict[str, object]:
    state = json.loads(serialized)
    state.pop("evaluation_sequence", None)
    for entry in state.values():
        if isinstance(entry, dict):
            entry.pop("last_evaluation_sequence", None)
    return state


def test_evaluate_rules_covers_all_alerts_and_negative_schema_noise() -> None:
    normal = evaluate_rules(_normal_signals())

    assert [result.rule_id for result in normal] == ["A1", "A2", "A3", "A4", "A5", "A6"]
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


def test_a5_requires_old_eligible_pending_and_no_recent_success() -> None:
    fresh = _normal_signals()
    fresh.hours_since_successful_interpretation = 5
    fresh.wechat_pending_count = 0
    assert evaluate_rules(fresh)[4].firing is False

    stalled = _normal_signals()
    stalled.hours_since_successful_interpretation = 4
    stalled.wechat_pending_count = 2
    stalled.oldest_wechat_pending_title = "等待四小时的文章"
    a5 = evaluate_rules(stalled)[4]
    assert a5.firing is True
    assert "等待文章 2 篇" in a5.detail
    assert "ark-breaker.json" in a5.action
    assert "402 表示余额不足" in a5.action
    assert "AccountQuotaExceeded" in a5.action


def test_a5_collector_covers_fresh_equal_recent_success_and_frozen_boundaries(tmp_path: Path) -> None:
    db_path = tmp_path / "a5.db"
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sources(id TEXT PRIMARY KEY, kind TEXT, enabled INTEGER);
            CREATE TABLE items(id TEXT PRIMARY KEY, source_id TEXT, title TEXT, fetched_at TEXT);
            CREATE TABLE wechat_interpretations(
              item_id TEXT PRIMARY KEY, error TEXT, error_retry_count INTEGER, processed_at TEXT
            );
            INSERT INTO sources VALUES ('wechat', 'wechat', 1);
            """
        )
        conn.execute("INSERT INTO items VALUES ('fresh','wechat','fresh',?)", ((now - timedelta(hours=4) + timedelta(seconds=1)).isoformat(),))
        conn.execute("INSERT INTO items VALUES ('equal','wechat','equal',?)", ((now - timedelta(hours=4)).isoformat(),))
        conn.execute("INSERT INTO items VALUES ('frozen','wechat','frozen',?)", ((now - timedelta(hours=8)).isoformat(),))
        conn.execute("INSERT INTO wechat_interpretations VALUES ('frozen','failed',8,?)", ((now - timedelta(hours=8)).isoformat(),))
    hours, count, frozen, title = alerts_module._wechat_interpretation_signal(db_path, now, 4)
    assert hours is None
    assert frozen == 1
    assert (count, title) == (1, "equal")
    signals = _normal_signals()
    signals.hours_since_successful_interpretation = hours
    signals.wechat_pending_count = count
    signals.oldest_wechat_pending_title = title
    assert evaluate_rules(signals)[4].firing is True

    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO wechat_interpretations VALUES ('equal',NULL,0,?)", ((now - timedelta(hours=1)).isoformat(),))
    hours, count, frozen, _ = alerts_module._wechat_interpretation_signal(db_path, now, 4)
    recent = _normal_signals()
    recent.hours_since_successful_interpretation = hours
    recent.wechat_pending_count = count
    assert count == 0
    assert frozen == 1
    assert evaluate_rules(recent)[4].firing is False


def test_a6_is_unarmed_with_fewer_than_three_comparable_days_and_qualifies_amount() -> None:
    signals = _normal_signals()
    signals.a6_current_cost_cny = 99
    signals.a6_baseline_days = 2
    signals.a6_excluded_coverage_days = 12
    signals.a6_unpriced_calls = 3
    signals.a6_pricing_freshness = "stale"
    a6 = evaluate_rules(signals)[5]
    assert a6.firing is False
    assert "当前不可评估" in a6.detail
    assert "未定价调用" in a6.detail
    assert "并非账单实付" in a6.detail


def test_d3_notification_dedup_clear_and_identical_recurrence(tmp_path: Path) -> None:
    state = tmp_path / "d3.json"
    sent: list[tuple[str, str, str]] = []
    cleared: list[str] = []

    def sender(text: str, *, severity: str, dedup_key: str, dedup_text: str) -> dict[str, object]:
        assert "--alert" not in text
        assert dedup_text == "unpriced:x/y"
        sent.append((text, severity, dedup_key))
        return {"skipped": False}

    def clear(key: str) -> dict[str, object]:
        cleared.append(key)
        return {"cleared": True}

    base = {"unpriced": [], "pricing_freshness": [], "pricing_table": []}
    firing = {**base, "unpriced": [{"provider": "x", "model": "y", "calls": 1}]}
    run_pricing_notifications(firing, state_path=state, send=sender, clear=clear)
    run_pricing_notifications(firing, state_path=state, send=sender, clear=clear)
    run_pricing_notifications(base, state_path=state, send=sender, clear=clear)
    run_pricing_notifications(firing, state_path=state, send=sender, clear=clear)

    assert len(sent) == 2
    assert sent[0][0] == sent[1][0]
    assert {severity for _, severity, _ in sent} == {"notice"}
    assert cleared == ["ai-radar:d3:unpriced:x/y"]


def test_d3_price_changed_sends_only_on_each_new_tariff(tmp_path: Path) -> None:
    state = tmp_path / "prices.json"
    sent: list[str] = []

    def sender(text: str, **kwargs: object) -> dict[str, object]:
        sent.append(text)
        return {"skipped": False}

    def report(price: float) -> dict[str, object]:
        return {
            "unpriced": [],
            "pricing_freshness": ["fresh"],
            "pricing_table": [{
                "provider": "deepseek", "model": "m", "matched_key": "deepseek/m",
                "input_per_million_tokens_usd": price,
                "cache_read_per_million_tokens_usd": price,
                "output_per_million_tokens_usd": price,
                "effective_from": None, "effective_to": None,
            }],
        }

    run_pricing_notifications(report(1), state_path=state, send=sender)
    run_pricing_notifications(report(1), state_path=state, send=sender)
    run_pricing_notifications(report(2), state_path=state, send=sender)
    run_pricing_notifications(report(2), state_path=state, send=sender)
    run_pricing_notifications(report(3), state_path=state, send=sender)
    assert len(sent) == 2
    assert all("price" not in text.lower() or "目录价" in text for text in sent)


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


def test_a3_active_healthz_probe_uses_installed_serve_port_and_recovers(tmp_path: Path) -> None:
    state_path = tmp_path / "alert-state.json"
    serve_plist_path = tmp_path / "live.aiplanet.ai-radar.serve.plist"
    serve_plist_path.write_bytes(
        plistlib.dumps(
            {
                "ProgramArguments": [
                    "/bin/bash",
                    "-lc",
                    "uv run python -m airadar.cli serve --host 0.0.0.0 --port 8010",
                ]
            }
        )
    )
    deliveries: list[tuple[str, str]] = []
    now = datetime.fromisoformat("2026-06-09T08:00:00+08:00")
    calls: list[tuple[str, float]] = []

    def healthz_down(url: str, timeout: float) -> bool:
        calls.append((url, timeout))
        return False

    def healthz_up(url: str, timeout: float) -> bool:
        calls.append((url, timeout))
        return True

    first = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=healthz_down,
        serve_plist_path=serve_plist_path,
    )
    second = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=5),
        send=_recording_sender(deliveries),
        healthz_probe=healthz_down,
        serve_plist_path=serve_plist_path,
    )
    recovered = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=10),
        send=_recording_sender(deliveries),
        healthz_probe=healthz_up,
        serve_plist_path=serve_plist_path,
    )

    assert calls == [
        ("http://127.0.0.1:8010/api/v1/healthz", 2.0),
        ("http://127.0.0.1:8010/api/v1/healthz", 2.0),
        ("http://127.0.0.1:8010/api/v1/healthz", 2.0),
    ]
    assert first["results"][2]["firing"] is False
    assert second["results"][2]["firing"] is True
    assert recovered["results"][2]["firing"] is False
    assert "🔴 A3" in deliveries[0][0]
    assert "✅ A3" in deliveries[1][0]
    assert "恢复证据：用户侧 5xx 率 0.0%，healthz 连续失败 0 次" in deliveries[1][0]


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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    second = run_alert_state_machine(
        firing,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=10),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    third = run_alert_state_machine(
        firing,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=31),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    resolved = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(tmp_path / f"a4-{expected_severity}.json").with_name("alert-events.jsonl"),
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    recovered = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now,
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    confirmed = run_alert_state_machine(
        _a4_firing(),
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=31),
        send=_recording_sender(deliveries),
        healthz_probe=_healthz_ok,
    )
    resolved = run_alert_state_machine(
        _normal_signals(),
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(tmp_path / "items-floor.json").with_name("alert-events.jsonl"),
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
        firing_basis="observed",
    )

    with pytest.raises(FrozenInstanceError):
        result.severity = "page"  # type: ignore[misc]
    assert json.loads(json.dumps(asdict(result))) == asdict(result)


def test_state_machine_persists_firing_basis_in_projection_and_lifecycle(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "firing-basis.json"
    result = AlertRuleResult(
        rule_id="PERF:homepage.first_card:same_host_origin:idle",
        title="site performance",
        firing=True,
        detail="observed site failure",
        action="inspect",
        firing_basis="observed",
    )

    run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=lambda _text, *, severity="page": {"skipped": True},
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))[result.rule_id]

    assert state["firing_basis"] == "observed"
    assert state["lifecycles"]["page"]["firing_basis"] == "observed"


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
        [firing],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=now,
        send=_recording_sender(calls),
    )
    resolved = run_alert_results_state_machine(
        [recovered],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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

    failed = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=now,
        send=sender,
    )
    failed_state = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    retried = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=5),
        send=sender,
    )
    successful_state = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    resolved = run_alert_results_state_machine(
        [recovered],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=6),
        send=sender,
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now,
        send=sender,
        healthz_probe=_healthz_ok,
    )
    retried = run_alert_state_machine(
        signals,
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=5),
        send=sender,
        healthz_probe=_healthz_ok,
    )

    assert first["sent_count"] == retried["sent_count"] == 1
    assert [severity for _text, severity in calls] == ["page", "page"]
    assert all(f"🔴 {rule_id}" in text for text, _severity in calls)


def test_unannounced_episode_closes_silently_and_failed_resolve_retries(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    firing = AlertRuleResult("TEST", "delivery", True, "detail", "action")
    recovered = AlertRuleResult("TEST", "delivery", False, "ok", "action")

    never_announced_path = tmp_path / "never-announced.json"
    failed_calls: list[tuple[str, str]] = []

    def failing_fire(text: str, *, severity: str = "page") -> dict[str, object]:
        failed_calls.append((text, severity))
        return {"skipped": True}

    run_alert_results_state_machine(
        [firing],
        state_path=never_announced_path,
        event_path=never_announced_path.with_name("alert-events.jsonl"),
        now=now,
        send=failing_fire,
    )
    closed = run_alert_results_state_machine(
        [recovered],
        state_path=never_announced_path,
        event_path=Path(never_announced_path).with_name("alert-events.jsonl"),
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
        return {"skipped": False} if len(resolve_calls) in {1, 3} else resolve_failure

    run_alert_results_state_machine(
        [firing],
        state_path=announced_path,
        event_path=announced_path.with_name("alert-events.jsonl"),
        now=now,
        send=success_then_fail,
    )
    failed_resolve = run_alert_results_state_machine(
        [recovered],
        state_path=announced_path,
        event_path=Path(announced_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=1),
        send=success_then_fail,
    )
    next_ok = run_alert_results_state_machine(
        [recovered],
        state_path=announced_path,
        event_path=Path(announced_path).with_name("alert-events.jsonl"),
        now=now + timedelta(minutes=2),
        send=success_then_fail,
    )
    final_state = json.loads(announced_path.read_text(encoding="utf-8"))["TEST"]

    assert failed_resolve["sent_count"] == 1
    assert failed_resolve["sent"][0]["effective_severity"] == "page"
    assert failed_resolve["sent"][0]["channel"] == "ALERT"
    assert failed_resolve["sent"][0]["send_result"] is resolve_failure
    assert next_ok["sent_count"] == 1
    assert next_ok["sent"][0]["delivered"] is True
    assert len(resolve_calls) == 3
    assert final_state["state"] == "ok"
    assert final_state["announced"] is False


def test_partial_resolve_exception_persists_delivered_lifecycle_and_retries_pending(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "partial-resolve.json"
    event_path = tmp_path / "alert-events.jsonl"
    now = datetime.fromisoformat("2026-07-24T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "TEST": {
                    "state": "firing",
                    "severity": "page",
                    "lifecycles": {
                        "page": {
                            "state": "firing",
                            "since": now.isoformat(),
                            "last_notified": now.isoformat(),
                            "detail": "page firing",
                            "announced": True,
                        },
                        "notice": {
                            "state": "firing",
                            "since": now.isoformat(),
                            "last_notified": now.isoformat(),
                            "detail": "notice firing",
                            "announced": True,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def partial_sender(text: str, *, severity: str = "page") -> dict[str, object]:
        calls.append(severity)
        if severity == "notice":
            raise RuntimeError("notice transport crashed")
        return {"skipped": False}

    recovered = AlertRuleResult("TEST", "partial", False, "ok", "none")
    with pytest.raises(RuntimeError, match="notice transport crashed"):
        run_alert_results_state_machine(
            [recovered],
            state_path=state_path,
            event_path=event_path,
            now=now + timedelta(minutes=1),
            send=partial_sender,
        )

    partial = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    assert partial["lifecycles"]["page"]["state"] == "ok"
    assert partial["lifecycles"]["notice"]["state"] == "firing"

    retried = run_alert_results_state_machine(
        [recovered],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=2),
        send=lambda text, *, severity="page": {"skipped": False},
    )
    repeated = run_alert_results_state_machine(
        [recovered],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=3),
        send=lambda text, *, severity="page": pytest.fail("resolved lifecycle resent"),
    )

    assert calls == ["page", "notice"]
    assert [
        (receipt["effective_severity"], receipt["type"])
        for receipt in retried["sent"]
    ] == [("notice", "resolved")]
    assert repeated["sent"] == []


def test_concurrent_resolve_state_machine_delivers_once(tmp_path: Path) -> None:
    state_path = tmp_path / "concurrent-resolve.json"
    event_path = tmp_path / "alert-events.jsonl"
    now = datetime.fromisoformat("2026-07-24T08:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "TEST": {
                    "state": "firing",
                    "since": now.isoformat(),
                    "last_notified": now.isoformat(),
                    "detail": "firing",
                    "severity": "page",
                    "announced": True,
                }
            }
        ),
        encoding="utf-8",
    )
    sender_entered = threading.Event()
    release_sender = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        nonlocal calls
        with calls_lock:
            calls += 1
        sender_entered.set()
        assert release_sender.wait(timeout=2)
        return {"skipped": False}

    result = AlertRuleResult("TEST", "concurrent", False, "ok", "none")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            run_alert_results_state_machine,
            [result],
            state_path=state_path,
            event_path=event_path,
            now=now + timedelta(minutes=1),
            send=sender,
        )
        assert sender_entered.wait(timeout=1)
        second = executor.submit(
            run_alert_results_state_machine,
            [result],
            state_path=state_path,
            event_path=event_path,
            now=now + timedelta(minutes=1),
            send=sender,
        )
        time.sleep(0.05)
        release_sender.set()
        payloads = [first.result(timeout=2), second.result(timeout=2)]

    assert calls == 1
    assert sorted(payload["sent_count"] for payload in payloads) == [0, 1]


def test_older_evaluation_cannot_resolve_newer_firing_state(tmp_path: Path) -> None:
    state_path = tmp_path / "ordered-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    newer = datetime.fromisoformat("2026-07-24T09:00:00+08:00")
    older = newer - timedelta(hours=1)
    calls: list[str] = []

    run_alert_results_state_machine(
        [AlertRuleResult("TEST", "ordered", True, "new firing", "inspect")],
        state_path=state_path,
        event_path=event_path,
        now=newer,
        evaluation_sequence=2,
        send=lambda text, *, severity="page": calls.append(text) or {"skipped": False},
    )
    stale = run_alert_results_state_machine(
        [AlertRuleResult("TEST", "ordered", False, "old recovery", "none")],
        state_path=state_path,
        event_path=event_path,
        now=older,
        evaluation_sequence=1,
        send=lambda text, *, severity="page": pytest.fail(
            f"older evaluation sent {severity}: {text}"
        ),
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    entry = state["TEST"]
    assert stale["sent"] == []
    assert entry["state"] == "firing"
    assert entry["detail"] == "new firing"
    assert entry["last_evaluated_at"] == newer.isoformat()
    assert len(calls) == 1


def test_newer_sequence_accepts_firing_after_small_clock_rollback(tmp_path: Path) -> None:
    state_path = tmp_path / "sequence-rollback-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    first = datetime.fromisoformat("2026-07-24T09:00:00+08:00")
    rolled_back = first - timedelta(hours=1)
    calls: list[str] = []

    run_alert_results_state_machine(
        [AlertRuleResult("TEST", "sequence", False, "healthy", "none")],
        state_path=state_path,
        event_path=event_path,
        now=first,
        evaluation_sequence=1,
        send=lambda text, *, severity="page": pytest.fail(
            f"healthy baseline sent {severity}: {text}"
        ),
    )
    payload = run_alert_results_state_machine(
        [AlertRuleResult("TEST", "sequence", True, "new firing", "inspect")],
        state_path=state_path,
        event_path=event_path,
        now=rolled_back,
        evaluation_sequence=2,
        send=lambda text, *, severity="page": calls.append(text) or {"skipped": False},
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    entry = state["TEST"]
    assert payload["sent_count"] == 1
    assert len(calls) == 1
    assert entry["state"] == "firing"
    assert entry["last_evaluation_sequence"] == 2
    assert state["evaluation_sequence"]["last_reserved"] == 2


def test_older_sequence_cannot_resolve_newer_state_despite_large_wall_clock_lead(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "sequence-late-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    newer_wall_time = datetime.fromisoformat("2026-07-24T09:00:00+08:00")
    late_old_wall_time = newer_wall_time + timedelta(hours=12)
    older_sequence = reserve_alert_evaluation_sequence(state_path=state_path)
    newer_sequence = reserve_alert_evaluation_sequence(state_path=state_path)

    run_alert_results_state_machine(
        [AlertRuleResult("TEST", "sequence", True, "new firing", "inspect")],
        state_path=state_path,
        event_path=event_path,
        now=newer_wall_time,
        evaluation_sequence=newer_sequence,
        send=lambda text, *, severity="page": {"skipped": False},
    )
    payload = run_alert_results_state_machine(
        [AlertRuleResult("TEST", "sequence", False, "late old recovery", "none")],
        state_path=state_path,
        event_path=event_path,
        now=late_old_wall_time,
        evaluation_sequence=older_sequence,
        send=lambda text, *, severity="page": pytest.fail(
            f"older sequence sent {severity}: {text}"
        ),
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    entry = state["TEST"]
    assert payload["sent"] == []
    assert entry["state"] == "firing"
    assert entry["detail"] == "new firing"
    assert entry["last_evaluation_sequence"] == 2
    assert state["evaluation_sequence"]["last_reserved"] == 2


def test_equal_sequence_recovery_cannot_overwrite_firing_state(tmp_path: Path) -> None:
    state_path = tmp_path / "equal-timestamp-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    current = datetime.fromisoformat("2026-07-24T09:00:00+08:00")

    run_alert_results_state_machine(
        [AlertRuleResult("TEST", "ordered", True, "new firing", "inspect")],
        state_path=state_path,
        event_path=event_path,
        now=current,
        evaluation_sequence=1,
        send=lambda text, *, severity="page": {"skipped": False},
    )
    stale = run_alert_results_state_machine(
        [AlertRuleResult("TEST", "ordered", False, "same-time recovery", "none")],
        state_path=state_path,
        event_path=event_path,
        now=current,
        evaluation_sequence=1,
        send=lambda text, *, severity="page": pytest.fail(
            f"equal-timestamp evaluation sent {severity}: {text}"
        ),
    )

    entry = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    assert stale["sent"] == []
    assert entry["state"] == "firing"
    assert entry["detail"] == "new firing"


@pytest.mark.parametrize(
    ("initial_firing", "next_firing"),
    [(True, False), (False, True)],
)
def test_implausible_future_ordering_fence_does_not_freeze_state(
    tmp_path: Path,
    initial_firing: bool,
    next_firing: bool,
) -> None:
    state_path = tmp_path / f"future-fence-{initial_firing}.json"
    event_path = tmp_path / "alert-events.jsonl"
    current = datetime.fromisoformat("2026-07-24T09:00:00+08:00")
    future = current + timedelta(days=30)
    state_path.write_text(
        json.dumps(
            {
                "TEST": {
                    "state": "firing" if initial_firing else "ok",
                    "since": current.isoformat() if initial_firing else None,
                    "last_notified": current.isoformat() if initial_firing else None,
                    "detail": "clock-skewed state",
                    "severity": "page",
                    "announced": initial_firing,
                    "last_evaluated_at": future.isoformat(),
                }
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    payload = run_alert_results_state_machine(
        [
            AlertRuleResult(
                "TEST",
                "rollback",
                next_firing,
                "fresh result after clock rollback",
                "inspect",
            )
        ],
        state_path=state_path,
        event_path=event_path,
        now=current,
        send=lambda text, *, severity="page": calls.append(text) or {"skipped": False},
    )

    entry = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]
    assert payload["sent_count"] == 1
    assert len(calls) == 1
    assert entry["state"] == ("firing" if next_firing else "ok")
    assert entry["last_evaluated_at"] == current.isoformat()


def test_state_replace_failure_preserves_previous_complete_json(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    original = {
        "KEEP": {
            "state": "ok",
            "since": None,
            "last_notified": None,
            "detail": "complete",
            "severity": "page",
            "announced": False,
        }
    }
    state_path.write_text(json.dumps(original), encoding="utf-8")
    real_replace = alerts_module.os.replace

    def fail_state_replace(source: object, destination: object) -> None:
        if Path(destination) == state_path:
            raise OSError("injected state replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(alerts_module.os, "replace", fail_state_replace)

    with pytest.raises(OSError, match="state replace failure"):
        run_alert_results_state_machine(
            [AlertRuleResult("NEW", "atomic", True, "detail", "action")],
            state_path=state_path,
            event_path=event_path,
            now=datetime.fromisoformat("2026-07-24T09:00:00+08:00"),
            send=lambda text, *, severity="page": {"skipped": False},
        )

    assert json.loads(state_path.read_text(encoding="utf-8")) == original


def _install_deduplicating_fake_im_notify(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sender = fake_bin / "im-notify"
    fake_sender.write_text(
        """#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
key = args[args.index("--dedup-key") + 1]
identity = args[args.index("--dedup-text") + 1]
root = Path(os.environ["FAKE_IM_NOTIFY_STATE"])
root.mkdir(parents=True, exist_ok=True)
(root / "attempts.jsonl").open("a", encoding="utf-8").write(
    json.dumps({"key": key, "identity": identity}) + "\\n"
)
signature = hashlib.sha256(identity.encode()).hexdigest()
signature_path = root / (hashlib.sha256(key.encode()).hexdigest() + ".sig")
if signature_path.exists() and signature_path.read_text() == signature:
    print("im-notify: suppressed")
    raise SystemExit(0)
(root / "visible.jsonl").open("a", encoding="utf-8").write(json.dumps(identity) + "\\n")
signature_path.write_text(signature)
print("im-notify: sent")
""",
        encoding="utf-8",
    )
    fake_sender.chmod(0o755)
    transport_state = tmp_path / "transport-state"
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_IM_NOTIFY_STATE", str(transport_state))
    return transport_state


def test_resolve_retry_after_state_persist_crash_is_transport_deduplicated(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    transport_state = _install_deduplicating_fake_im_notify(monkeypatch, tmp_path)

    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    since = datetime.fromisoformat("2026-07-24T07:00:00+08:00")
    state_path.write_text(
        json.dumps(
            {
                "TEST": {
                    "state": "firing",
                    "since": since.isoformat(),
                    "last_notified": since.isoformat(),
                    "detail": "firing",
                    "severity": "page",
                    "announced": True,
                }
            }
        ),
        encoding="utf-8",
    )
    recovered = AlertRuleResult("TEST", "dedup", False, "ok", "none")
    real_write_state = alerts_module._write_state
    fail_once = True

    def crash_after_send(path: Path, state: dict[str, dict[str, object]]) -> None:
        nonlocal fail_once
        attempts = transport_state / "attempts.jsonl"
        if fail_once and attempts.exists():
            fail_once = False
            raise OSError("injected crash after send")
        real_write_state(path, state)

    monkeypatch.setattr(alerts_module, "_write_state", crash_after_send)
    with pytest.raises(OSError, match="crash after send"):
        run_alert_results_state_machine(
            [recovered],
            state_path=state_path,
            event_path=event_path,
            now=since + timedelta(minutes=1),
        )
    retried = run_alert_results_state_machine(
        [recovered],
        state_path=state_path,
        event_path=event_path,
        now=since + timedelta(minutes=2),
    )

    attempts = [
        json.loads(line)
        for line in (transport_state / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    visible = (transport_state / "visible.jsonl").read_text(encoding="utf-8").splitlines()
    assert retried["sent"][0]["delivered"] is True
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert attempts[0]["key"] == "ai-radar:TEST:page:resolved:1"
    assert since.isoformat() in attempts[0]["identity"]
    assert len(visible) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["TEST"]["state"] == "ok"


def test_fresh_firing_retry_after_persist_crash_reuses_notification_nonce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport_state = _install_deduplicating_fake_im_notify(monkeypatch, tmp_path)
    state_path = tmp_path / "fresh-state.json"
    event_path = tmp_path / "fresh-events.jsonl"
    started = datetime.fromisoformat("2026-07-24T07:00:00+08:00")
    firing = AlertRuleResult("TEST", "fresh", True, "firing", "inspect")
    real_write_state = alerts_module._write_state
    fail_once = True

    def crash_after_send(path: Path, state: dict[str, dict[str, object]]) -> None:
        nonlocal fail_once
        attempts = transport_state / "attempts.jsonl"
        if fail_once and attempts.exists():
            fail_once = False
            raise OSError("injected crash after fresh firing send")
        real_write_state(path, state)

    monkeypatch.setattr(alerts_module, "_write_state", crash_after_send)
    with pytest.raises(OSError, match="crash after fresh firing send"):
        run_alert_results_state_machine(
            [firing],
            state_path=state_path,
            event_path=event_path,
            now=started,
        )
    pending = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]["lifecycles"]["page"]
    pending_nonce = pending["pending_notification"]["nonce"]

    retried = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=1),
    )

    attempts = [
        json.loads(line)
        for line in (transport_state / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    visible = (transport_state / "visible.jsonl").read_text(encoding="utf-8").splitlines()
    final = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]["lifecycles"]["page"]
    assert retried["sent_count"] == 1
    assert attempts[0] == attempts[1]
    assert str(pending_nonce) in attempts[0]["key"]
    assert str(pending_nonce) in attempts[0]["identity"]
    assert len(visible) == 1
    assert final["notification_sequence"] == pending_nonce
    assert final["pending_notification"] is None


def test_cooldown_repage_allocates_new_notification_nonce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport_state = _install_deduplicating_fake_im_notify(monkeypatch, tmp_path)
    state_path = tmp_path / "cooldown-state.json"
    event_path = tmp_path / "cooldown-events.jsonl"
    started = datetime.fromisoformat("2026-07-24T07:00:00+08:00")
    firing = AlertRuleResult("TEST", "cooldown", True, "firing", "inspect")

    run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=event_path,
        now=started,
    )
    reminded = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=31),
    )

    attempts = [
        json.loads(line)
        for line in (transport_state / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    visible = (transport_state / "visible.jsonl").read_text(encoding="utf-8").splitlines()
    lifecycle = json.loads(state_path.read_text(encoding="utf-8"))["TEST"]["lifecycles"]["page"]
    assert reminded["sent_count"] == 1
    assert len(attempts) == len(visible) == 2
    assert attempts[0] != attempts[1]
    assert lifecycle["notification_sequence"] == 2


def test_page_incident_ignores_notice_deescalation_and_allocates_reminder_nonce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport_state = _install_deduplicating_fake_im_notify(monkeypatch, tmp_path)
    state_path = tmp_path / "round-trip-state.json"
    event_path = tmp_path / "round-trip-events.jsonl"
    started = datetime.fromisoformat("2026-07-24T07:00:00+08:00")
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 0}}}

    run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        thresholds=thresholds,
    )
    run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=1),
        thresholds=thresholds,
    )
    returned = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=31),
        thresholds=thresholds,
    )

    attempts = [
        json.loads(line)
        for line in (transport_state / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    page_firings = [
        row
        for row in attempts
        if ":page:firing:" in row["key"]
    ]
    visible = (transport_state / "visible.jsonl").read_text(encoding="utf-8").splitlines()
    assert returned["sent_count"] == 1
    assert len(page_firings) == 2
    assert page_firings[0] != page_firings[1]
    assert len(visible) == len(attempts)


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
        [incoming],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=current,
        send=sender,
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=10),
        send=_recording_sender(calls),
    )
    retried = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        [],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=started + timedelta(minutes=5),
        send=_recording_sender(calls),
    )
    first_saved = state_path.read_text(encoding="utf-8")
    second = run_alert_results_state_machine(
        [],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=started + timedelta(minutes=5),
        send=_recording_sender(calls),
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
    assert _state_without_evaluation_metadata(
        first_saved
    ) == _state_without_evaluation_metadata(second_saved)


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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current,
        send=_recording_sender(calls),
        thresholds={"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}},
    )
    upgraded = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current,
        send=sender,
        thresholds=thresholds,
    )
    failed_page = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=15),
        send=sender,
        thresholds=thresholds,
    )
    failed_state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    retried = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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


def test_announced_notice_to_page_escalates_without_recovery_message(tmp_path: Path) -> None:
    state_path = tmp_path / "announced-notice-to-page.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}}

    run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    announced = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=31),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    upgraded = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=32),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )

    assert _receipt_identities(announced) == [("A4", "notice", "firing")]
    assert _receipt_identities(upgraded) == [("A4", "page", "firing")]
    assert [receipt["channel"] for receipt in upgraded["sent"]] == ["ALERT"]
    assert [severity for _text, severity in calls] == ["notice", "page"]
    assert all("✅" not in text and "已恢复" not in text for text, _severity in calls)


def test_announced_page_holds_through_notice_tier_until_true_recovery(tmp_path: Path) -> None:
    state_path = tmp_path / "page-to-notice.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}}

    run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    transitioned = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=1),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    held_severities = _firing_lifecycle_severities(state_path)

    recovered = run_alert_results_state_machine(
        [_severity_result("notice", firing=False)],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=2),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )

    assert transitioned["sent"] == []
    assert held_severities == {"page"}
    assert _firing_lifecycle_severities(state_path) == set()
    assert _receipt_identities(recovered) == [("A4", "page", "resolved")]
    assert [severity for _text, severity in calls] == ["page", "page"]
    assert "✅" not in calls[0][0]
    assert "✅" in calls[1][0]


def test_old_flat_announced_page_migrates_without_false_notice_recovery(
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=1),
        send=_recording_sender(calls),
        thresholds={"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 30}}},
    )

    assert transitioned["sent"] == []
    assert calls == []
    assert _firing_lifecycle_severities(state_path) == {"page"}


def test_notice_escalation_and_deescalation_stay_one_page_incident_until_recovery(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "notice-page-notice.json"
    current = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    calls: list[tuple[str, str]] = []
    thresholds = {"a4": {"debounce_minutes_by_severity": {"page": 0, "notice": 0}}}

    first = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == {"notice"}
    second = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=1),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == {"page"}
    deescalated = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=2),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == {"page"}
    recovered = run_alert_results_state_machine(
        [_severity_result("notice", firing=False)],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=3),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    assert _firing_lifecycle_severities(state_path) == set()
    assert _receipt_identities(first) == [("A4", "notice", "firing")]
    assert _receipt_identities(second) == [("A4", "page", "firing")]
    assert deescalated["sent"] == []
    assert _receipt_identities(recovered) == [("A4", "page", "resolved")]
    assert [(severity, "✅" in text) for text, severity in calls] == [
        ("notice", False),
        ("page", False),
        ("page", True),
    ]


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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=started + timedelta(minutes=15),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    first_saved = state_path.read_text(encoding="utf-8")
    reloaded = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=started + timedelta(minutes=15),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    second_saved = state_path.read_text(encoding="utf-8")
    announced = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=started + timedelta(minutes=31),
        send=_recording_sender(calls),
        thresholds=thresholds,
    )

    assert pending["sent_count"] == reloaded["sent_count"] == 0
    assert _state_without_evaluation_metadata(
        first_saved
    ) == _state_without_evaluation_metadata(second_saved)
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        [],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=started + timedelta(minutes=5),
        send=_recording_sender(calls),
    )
    first_saved = state_path.read_text(encoding="utf-8")
    second = run_alert_results_state_machine(
        [],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=started + timedelta(minutes=5),
        send=_recording_sender(calls),
    )
    second_saved = state_path.read_text(encoding="utf-8")
    state = json.loads(second_saved)

    assert first["sent_count"] == second["sent_count"] == 0
    assert calls == []
    assert _state_without_evaluation_metadata(
        first_saved
    ) == _state_without_evaluation_metadata(second_saved)
    assert state["A4"]["severity"] == "page"
    assert state["A4"]["announced"] is True
    assert state["A4"]["lifecycles"]["page"] == {
        "state": "firing",
        "since": started.isoformat(),
        "last_notified": (started + timedelta(minutes=1)).isoformat(),
        "detail": "legacy announced page",
        "announced": True,
        "notification_sequence": 0,
        "pending_notification": None,
        "evaluation_state": "healthy",
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current,
        send=_recording_sender(calls),
        thresholds=thresholds,
    )
    announced = run_alert_results_state_machine(
        [_severity_result("notice")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
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
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current,
        send=sender,
    )
    transitioned = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=1),
        send=sender,
    )
    failed_state = json.loads(state_path.read_text(encoding="utf-8"))["A4"]
    retried = run_alert_results_state_machine(
        [_severity_result("page")],
        state_path=state_path,
        event_path=Path(state_path).with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=2),
        send=sender,
    )

    assert _receipt_identities(transitioned) == [("A4", "page", "firing")]
    assert transitioned["sent"][0]["send_result"] == {"skipped": True}
    assert failed_state["lifecycles"]["page"]["announced"] is False
    assert failed_state["lifecycles"]["page"]["last_notified"] is None
    assert _receipt_identities(retried) == [("A4", "page", "firing")]
    assert all("✅" not in text for text, _severity in calls)


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
        [firing],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=10),
        send=_recording_sender(calls),
    )
    first_saved = state_path.read_text(encoding="utf-8")
    second = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=10),
        send=_recording_sender(calls),
    )
    second_saved = state_path.read_text(encoding="utf-8")
    after_cooldown = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=state_path.with_name("alert-events.jsonl"),
        now=current + timedelta(minutes=31),
        send=_recording_sender(calls),
    )

    assert first["sent_count"] == second["sent_count"] == 0
    assert _state_without_evaluation_metadata(
        first_saved
    ) == _state_without_evaluation_metadata(second_saved)
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


def _read_ledger(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _ledger_result(
    rule_id: str,
    *,
    firing: bool = True,
    severity: str = "page",
) -> AlertRuleResult:
    return AlertRuleResult(
        rule_id=rule_id,
        title=f"{rule_id} title",
        firing=firing,
        detail=f"{rule_id} detail" if firing else f"{rule_id} recovered",
        action="inspect evidence",
        values={"identity": rule_id, "nested": [1, 2]},
        severity=severity,  # type: ignore[arg-type]
    )


def test_notification_ledger_records_exact_successful_firing_resolved_cycle(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")

    fired = run_alert_results_state_machine(
        [_ledger_result("TEST", severity="notice")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=lambda text, *, severity="page": {"skipped": False},
    )
    resolved = run_alert_results_state_machine(
        [_ledger_result("TEST", firing=False)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=1),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert _receipt_identities(fired) == [("TEST", "notice", "firing")]
    assert _receipt_identities(resolved) == [("TEST", "notice", "resolved")]
    rows = _read_ledger(event_path)
    assert len(rows) == 2
    assert [
        (row["rule_id"], row["severity"], row["type"], row["channel"])
        for row in rows
    ] == [
        ("TEST", "notice", "firing", "NOTIFICATION"),
        ("TEST", "notice", "resolved", "NOTIFICATION"),
    ]
    assert all(
        set(row)
        == {
            "ts",
            "rule_id",
            "severity",
            "type",
            "detail",
            "values",
            "channel",
            "episode_since",
            "notification_nonce",
        }
        for row in rows
    )
    assert rows[0]["detail"] == "TEST detail"
    assert rows[0]["values"] == {"identity": "TEST", "nested": [1, 2]}
    assert event_path.with_suffix(".lock").exists()


def test_notification_ledger_success_adds_one_and_skipped_adds_zero(tmp_path: Path) -> None:
    event_path = tmp_path / "alert-events.jsonl"
    state_path = tmp_path / "state.json"
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    run_alert_results_state_machine(
        [_ledger_result("DELIVERED")],
        state_path=state_path,
        event_path=event_path,
        now=now,
        send=lambda text, *, severity="page": {"skipped": False},
    )
    before = _read_ledger(event_path)

    skipped = run_alert_results_state_machine(
        [_ledger_result("SKIPPED")],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=1),
        send=lambda text, *, severity="page": {"skipped": True},
    )

    assert len(before) == len(_read_ledger(event_path)) == 1
    assert skipped["sent"][0]["send_result"] == {"skipped": True}


def test_a1_a4_public_path_writes_notification_ledger(tmp_path: Path) -> None:
    signals = _normal_signals()
    signals.upstream_error_rate = 0.8
    event_path = tmp_path / "alert-events.jsonl"

    payload = run_alert_state_machine(
        signals,
        state_path=tmp_path / "state.json",
        event_path=event_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=lambda text, *, severity="page": {"skipped": False},
        healthz_probe=_healthz_ok,
    )

    assert _receipt_identities(payload) == [("A1", "page", "firing")]
    assert [(row["rule_id"], row["type"]) for row in _read_ledger(event_path)] == [
        ("A1", "firing")
    ]


def test_notification_ledger_filters_sender_outcomes_by_success_receipt_multiset(
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "alert-events.jsonl"
    outcomes: list[object] = [MappingProxyType({"skipped": False}), {"skipped": True}, None]

    def sender(text: str, *, severity: str = "page") -> object:
        return outcomes.pop(0)

    payload = run_alert_results_state_machine(
        [_ledger_result("ONE"), _ledger_result("TWO", severity="notice"), _ledger_result("THREE")],
        state_path=tmp_path / "state.json",
        event_path=event_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=sender,
    )

    successful_receipts = Counter(
        (row["rule_id"], row["effective_severity"], row["type"])
        for row in payload["sent"]
        if isinstance(row["send_result"], Mapping) and row["send_result"].get("skipped") is False
    )
    actual = Counter(
        (row["rule_id"], row["severity"], row["type"])
        for row in _read_ledger(event_path)
    )
    assert len(payload["sent"]) == 3
    assert successful_receipts == actual == Counter({("ONE", "page", "firing"): 1})


@pytest.mark.parametrize(
    ("from_severity", "to_severity"),
    [("notice", "page"), ("page", "notice")],
)
def test_notification_ledger_records_only_new_firing_on_severity_transition(
    tmp_path: Path,
    from_severity: str,
    to_severity: str,
) -> None:
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    sender = lambda text, *, severity="page": {"skipped": False}  # noqa: E731
    run_alert_results_state_machine(
        [_ledger_result("A4", severity=from_severity)],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=sender,
    )

    before = len(_read_ledger(event_path))
    transitioned = run_alert_results_state_machine(
        [_ledger_result("A4", severity=to_severity)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=1),
        send=sender,
    )
    batch = _read_ledger(event_path)[before:]

    expected = [] if from_severity == "page" else [("A4", "page", "firing")]
    assert _receipt_identities(transitioned) == expected
    assert [
        (row["rule_id"], row["severity"], row["type"], row["channel"])
        for row in batch
    ] == [
        (rule_id, severity, event_type, "ALERT")
        for rule_id, severity, event_type in expected
    ]


def test_notification_ledger_transition_omits_failed_new_firing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    run_alert_results_state_machine(
        [_ledger_result("A4", severity="notice")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=lambda text, *, severity="page": {"skipped": False},
    )
    transitioned = run_alert_results_state_machine(
        [_ledger_result("A4", severity="page")],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=1),
        send=lambda text, *, severity="page": {"skipped": True},
    )

    assert len(transitioned["sent"]) == 1
    assert _read_ledger(event_path)[1:] == []


def test_notification_ledger_retains_only_events_within_fourteen_days_on_write(
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "alert-events.jsonl"
    now = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    existing = [
        {
            "ts": (now - timedelta(days=14, seconds=1)).isoformat(),
            "rule_id": "OLD",
            "severity": "page",
            "type": "firing",
            "detail": "old",
            "values": {},
            "channel": "ALERT",
        },
        {
            "ts": (now - timedelta(days=14)).isoformat(),
            "rule_id": "BOUNDARY",
            "severity": "notice",
            "type": "firing",
            "detail": "new enough",
            "values": {},
            "channel": "NOTIFICATION",
        },
    ]
    event_path.write_text(
        "".join(json.dumps(row) + "\n" for row in existing),
        encoding="utf-8",
    )

    run_alert_results_state_machine(
        [_ledger_result("NEW")],
        state_path=tmp_path / "state.json",
        event_path=event_path,
        now=now,
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert [row["rule_id"] for row in _read_ledger(event_path)] == ["BOUNDARY", "NEW"]


def test_notification_ledger_two_process_writers_preserve_exact_identity_set(
    tmp_path: Path,
) -> None:
    event_path = tmp_path / "alert-events.jsonl"
    start_path = tmp_path / "start"
    per_process = 20
    script = """
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from airadar.admin.alerts import AlertRuleResult, run_alert_results_state_machine

prefix, count, state_path, event_path, start_path = sys.argv[1:]
while not Path(start_path).exists():
    time.sleep(0.005)
results = [
    AlertRuleResult(f\"{prefix}-{index}\", \"concurrent\", True, \"detail\", \"action\", values={\"writer\": prefix})
    for index in range(int(count))
]
run_alert_results_state_machine(
    results,
    state_path=Path(state_path),
    event_path=Path(event_path),
    now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    send=lambda text, *, severity=\"page\": {\"skipped\": False},
)
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                prefix,
                str(per_process),
                str(tmp_path / f"{prefix}-state.json"),
                str(event_path),
                str(start_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    filter(None, (str(Path.cwd() / "src"), os.environ.get("PYTHONPATH")))
                ),
            },
        )
        for prefix in ("writer-a", "writer-b")
    ]
    start_path.touch()
    completed = [process.communicate(timeout=10) for process in processes]

    assert [(process.returncode, stderr) for process, (_stdout, stderr) in zip(processes, completed)] == [
        (0, ""),
        (0, ""),
    ]
    rows = _read_ledger(event_path)
    expected = {
        (f"{prefix}-{index}", "page", "firing")
        for prefix in ("writer-a", "writer-b")
        for index in range(per_process)
    }
    assert len(rows) == 2 * per_process
    assert {(row["rule_id"], row["severity"], row["type"]) for row in rows} == expected


def _assert_ledger_failure_isolated(
    *,
    state_path: Path,
    event_path: Path,
    now: datetime,
) -> None:
    payload = run_alert_results_state_machine(
        [_ledger_result("FAIL-OPEN")],
        state_path=state_path,
        event_path=event_path,
        now=now,
        send=lambda text, *, severity="page": {"skipped": False},
    )
    assert _receipt_identities(payload) == [("FAIL-OPEN", "page", "firing")]
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["FAIL-OPEN"]
    assert persisted["state"] == "firing"
    assert persisted["announced"] is True
    repeated = run_alert_results_state_machine(
        [_ledger_result("FAIL-OPEN")],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=1),
        send=lambda text, *, severity="page": pytest.fail("persisted state must prevent resend"),
    )
    assert repeated["sent"] == []


def test_corrupt_notification_ledger_fails_open_without_overwrite(
    tmp_path: Path,
    caplog,
) -> None:  # noqa: ANN001
    event_path = tmp_path / "alert-events.jsonl"
    original = b'{"ts": broken\n'
    event_path.write_bytes(original)

    _assert_ledger_failure_isolated(
        state_path=tmp_path / "state.json",
        event_path=event_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
    )

    assert event_path.read_bytes() == original
    assert str(event_path) in caplog.text
    assert "JSONDecodeError" in caplog.text


def test_oversized_notification_ledger_fails_open_without_read_or_overwrite(
    tmp_path: Path,
    caplog,
) -> None:  # noqa: ANN001
    event_path = tmp_path / "alert-events.jsonl"
    with event_path.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    original_size = event_path.stat().st_size

    _assert_ledger_failure_isolated(
        state_path=tmp_path / "state.json",
        event_path=event_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
    )

    assert event_path.stat().st_size == original_size
    assert str(event_path) in caplog.text
    assert "LedgerOversizeError" in caplog.text


def test_notification_ledger_lock_timeout_is_bounded_and_state_still_persists(
    tmp_path: Path,
    caplog,
) -> None:  # noqa: ANN001
    event_path = tmp_path / "alert-events.jsonl"
    lock_path = tmp_path / "alert-events.lock"
    original = b""
    event_path.write_bytes(original)
    locker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys,time; "
                "f=open(sys.argv[1], 'a+'); "
                "fcntl.flock(f.fileno(), fcntl.LOCK_EX); "
                "print('locked', flush=True); time.sleep(2.5)"
            ),
            str(lock_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert locker.stdout is not None
    assert locker.stdout.readline().strip() == "locked"
    started = time.monotonic()
    try:
        _assert_ledger_failure_isolated(
            state_path=tmp_path / "state.json",
            event_path=event_path,
            now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        )
    finally:
        locker.terminate()
        locker.communicate(timeout=5)
    elapsed = time.monotonic() - started

    assert 0.9 <= elapsed < 1.6
    assert event_path.read_bytes() == original
    assert str(event_path) in caplog.text
    assert "LedgerLockTimeoutError" in caplog.text


def test_notification_ledger_replace_failure_preserves_original_and_state(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:  # noqa: ANN001
    event_path = tmp_path / "alert-events.jsonl"
    original = (
        json.dumps(
            {
                "ts": "2026-07-22T07:00:00+08:00",
                "rule_id": "ORIGINAL",
                "severity": "page",
                "type": "firing",
                "detail": "original",
                "values": {},
                "channel": "ALERT",
            }
        )
        + "\n"
    ).encode()
    event_path.write_bytes(original)

    real_replace = alerts_module.os.replace

    def fail_replace(source: object, destination: object) -> None:
        if Path(destination) == event_path:
            raise OSError("injected replace failure")
        real_replace(source, destination)

    monkeypatch.setattr("airadar.admin.alerts.os.replace", fail_replace)
    _assert_ledger_failure_isolated(
        state_path=tmp_path / "state.json",
        event_path=event_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
    )

    assert event_path.read_bytes() == original
    assert str(event_path) in caplog.text
    assert "OSError" in caplog.text


@pytest.mark.parametrize("skipped_values", [(True, False), (False, True)])
def test_notification_ledger_snapshots_mutated_shared_sender_result_at_call_time(
    tmp_path: Path,
    skipped_values: tuple[bool, bool],
) -> None:
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    started = datetime.fromisoformat("2026-07-22T08:00:00+08:00")
    shared_result: dict[str, object] = {}
    outcomes = iter(skipped_values)

    def mutating_sender(text: str, *, severity: str = "page") -> dict[str, object]:
        shared_result["skipped"] = next(outcomes)
        return shared_result

    delivered = run_alert_results_state_machine(
        [
            _ledger_result("ONE", severity="notice"),
            _ledger_result("TWO", severity="page"),
        ],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=mutating_sender,
    )
    batch = _read_ledger(event_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    expected_delivered = [skipped is False for skipped in skipped_values]

    assert [receipt["send_result"] is shared_result for receipt in delivered["sent"]] == [
        True,
        True,
    ]
    assert [receipt["delivered"] for receipt in delivered["sent"]] == expected_delivered
    assert state["ONE"]["lifecycles"]["notice"]["announced"] is expected_delivered[0]
    assert state["TWO"]["lifecycles"]["page"]["announced"] is expected_delivered[1]
    expected_ledger = [
        identity
        for identity, delivered in zip(
            [("ONE", "notice", "firing"), ("TWO", "page", "firing")],
            expected_delivered,
        )
        if delivered
    ]
    assert [(row["rule_id"], row["severity"], row["type"]) for row in batch] == expected_ledger


def test_notification_ledger_fifo_returns_promptly_and_persists_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    os.mkfifo(event_path)
    script = """
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from airadar.admin.alerts import AlertRuleResult, run_alert_results_state_machine

logging.basicConfig(level=logging.ERROR)
state_path, event_path = map(Path, sys.argv[1:])
payload = run_alert_results_state_machine(
    [AlertRuleResult("FIFO", "fifo", True, "detail", "action")],
    state_path=state_path,
    event_path=event_path,
    now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    send=lambda text, *, severity="page": {"skipped": False},
)
print(json.dumps({"sent": payload["sent_count"], "state_path": payload["state_path"]}))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(state_path), str(event_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                filter(None, (str(Path.cwd() / "src"), os.environ.get("PYTHONPATH")))
            ),
        },
    )
    started = time.monotonic()
    try:
        stdout, stderr = process.communicate(timeout=1.5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)
        pytest.fail("FIFO ledger blocked the public alert entry beyond 1.5 seconds")
    elapsed = time.monotonic() - started

    assert process.returncode == 0, stderr
    assert elapsed < 1.0
    assert json.loads(stdout)["sent"] == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))["FIFO"]
    assert state["state"] == "firing"
    assert state["announced"] is True
    assert str(event_path) in stderr
    assert "LedgerNonRegularFileError" in stderr


def test_notification_ledger_failure_survives_raising_logging_handler(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    class RaisingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise RuntimeError("hostile logging handler")

    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    event_path.write_text("", encoding="utf-8")
    hostile_logger = logging.Logger("phase6-hostile-ledger-logger")
    hostile_logger.addHandler(RaisingHandler())

    real_replace = alerts_module.os.replace

    def fail_replace(source: object, destination: object) -> None:
        if Path(destination) == event_path:
            raise OSError("injected replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(alerts_module.os, "replace", fail_replace)
    monkeypatch.setattr(alerts_module, "LOGGER", hostile_logger)
    payload = run_alert_results_state_machine(
        [_ledger_result("LOGGER")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=lambda text, *, severity="page": {"skipped": False},
    )

    assert _receipt_identities(payload) == [("LOGGER", "page", "firing")]
    state = json.loads(state_path.read_text(encoding="utf-8"))["LOGGER"]
    assert state["state"] == "firing"
    assert state["announced"] is True


@pytest.mark.parametrize("alias_case", ["same", "state-is-lock", "event-is-own-lock"])
def test_notification_ledger_rejects_state_event_and_lock_path_aliases(
    tmp_path: Path,
    alias_case: str,
) -> None:
    event_path = tmp_path / "alert-events.jsonl"
    state_path = tmp_path / "state.json"
    if alias_case == "same":
        event_path = state_path
    elif alias_case == "state-is-lock":
        state_path = event_path.with_suffix(".lock")
    else:
        event_path = tmp_path / "alert-events.lock"
    calls: list[tuple[str, str]] = []

    with pytest.raises(ValueError, match="state_path, event_path, and ledger lock path must be distinct"):
        run_alert_results_state_machine(
            [_ledger_result("ALIAS")],
            state_path=state_path,
            event_path=event_path,
            now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
            send=_recording_sender(calls),
        )

    assert calls == []
    assert not state_path.exists()


def test_a1_a4_entry_persists_state_when_notification_ledger_is_corrupt(
    tmp_path: Path,
    caplog,
) -> None:  # noqa: ANN001
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "alert-events.jsonl"
    original = b'{"ts": broken\n'
    event_path.write_bytes(original)
    signals = _normal_signals()
    signals.upstream_error_rate = 0.8

    payload = run_alert_state_machine(
        signals,
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-07-22T08:00:00+08:00"),
        send=lambda text, *, severity="page": {"skipped": False},
        healthz_probe=_healthz_ok,
    )

    assert _receipt_identities(payload) == [("A1", "page", "firing")]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["A1"]["state"] == "firing"
    assert state["A1"]["announced"] is True
    assert event_path.read_bytes() == original
    assert str(event_path) in caplog.text
    assert "JSONDecodeError" in caplog.text
