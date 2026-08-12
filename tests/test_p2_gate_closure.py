from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airadar import db
from airadar.admin.alerts import (
    AlertRuleResult,
    AlertSignals,
    _a6_measurement_in_progress,
    _correlate_alert_results,
    _format_resolved,
    evaluate_rules,
    run_alert_results_state_machine,
    run_pricing_notifications,
)
from airadar.admin.cost_report import (
    _pipeline_activity,
    build_cost_report,
    evaluate_a6_cost,
    format_cost_report,
)
from airadar.admin.metrics import collect_metrics
from airadar.admin.usage import collect_usage
from airadar.pricing import get_pricing


def _catalog(tmp_path: Path):  # noqa: ANN202
    return get_pricing(
        cache_path=tmp_path / "catalog.json",
        fetcher=lambda: {
            "deepseek/model": {
                "input_cost_per_token": 1e-3,
                "cache_read_input_token_cost": 1e-4,
                "output_cost_per_token": 1e-3,
            }
        },
        persist=False,
    )


def _row(
    created: datetime,
    *,
    provider: str = "deepseek",
    model: str = "model",
    stage: str = "interpret",
    tokens: int = 100,
    cached: int | None = None,
) -> dict[str, object]:
    return {
        "stage": stage,
        "provider": provider,
        "model": model,
        "input_tokens": tokens,
        "cached_input_tokens": cached,
        "output_tokens": 0,
        "input_char_count": tokens,
        "attribution_json": "{}",
        "created_at": created.isoformat(),
    }


def _signals(**overrides: object) -> AlertSignals:
    values: dict[str, object] = {
        "upstream_sample_size": 0,
        "upstream_error_rate": 0.0,
        "upstream_schema_error_rate": 0.0,
        "stage_error_rate": {},
        "stage_p95_latency_ms": {},
        "minutes_since_successful_pipeline": 1,
        "consecutive_skip_logs": 0,
        "server_error_rate": 0.0,
        "fetch_failed_ratio": 0.0,
        "items_today": 100,
    }
    values.update(overrides)
    return AlertSignals(**values)  # type: ignore[arg-type]


def test_known_per_call_denominator_excludes_unpriced(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    usage = collect_usage(
        days=1,
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=[
            _row(now - timedelta(hours=1), tokens=100),
            _row(now - timedelta(hours=1), provider="unknown", model="missing", tokens=100),
        ],
    )
    interpret = next(row for row in usage["stage_costs"] if row["stage"] == "interpret")
    assert interpret["calls"] == 2
    assert interpret["known_calls"] == 1
    assert interpret["unpriced_calls"] == 1
    assert interpret["known_cost_per_call_cny"] == interpret["known_cost_cny"]


def test_report_uses_durable_items_to_identify_stall_without_old_logs(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    rows = [
        _row(datetime.fromisoformat("2026-08-09T12:00:00+08:00")),
        _row(datetime.fromisoformat("2026-08-02T12:00:00+08:00")),
    ]
    fetched = {"2026-08-08": 273, "2026-08-01": 10}
    metering = {
        "2026-08-08": {"complete": True, "failures": 0, "pipeline_runs": 96, "fetch_inserted": 269},
        "2026-08-01": {"complete": False, "failures": 0, "pipeline_runs": 0, "fetch_inserted": 0},
    }
    report = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=rows,
        fetched_counts_snapshot=fetched,
        metering_snapshot=metering,
    )
    stalled = next(row for row in report["daily"] if row["date"] == "2026-08-08")
    assert stalled["activity_state"] == "processing_stall"
    assert report["comparison"]["reason"] == "processing_exposure_gap"
    text = format_cost_report(report)
    assert "异常：2026-08-08 处理停滞" in text
    assert "本窗含处理停滞日 2026-08-08" in text


def test_report_and_a6_surface_metering_loss(tmp_path: Path) -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    rows = [_row(now - timedelta(hours=1))]
    rows.extend(
        _row(now.replace(hour=0, minute=0) - timedelta(days=days) + timedelta(hours=1))
        for days in range(1, 15)
    )
    a6 = evaluate_a6_cost(
        rows,
        now=now,
        catalog=_catalog(tmp_path),
        metering_complete=False,
        metering_failure_count=2,
    )
    assert a6["evaluable"] is False
    rules = evaluate_rules(
        _signals(
            a6_evaluable=False,
            a6_metering_complete=False,
            a6_metering_failure_count=2,
            a6_baseline_days=14,
        )
    )
    a6_rule = next(rule for rule in rules if rule.rule_id == "A6")
    assert a6_rule.evaluation_state == "degraded"
    assert "至少 2 次计量写入失败" in a6_rule.detail


def test_a5_and_a6_degraded_resolve_do_not_claim_recovery() -> None:
    a5 = next(
        rule
        for rule in evaluate_rules(
            _signals(
                hours_since_successful_interpretation=None,
                wechat_pending_count=0,
                wechat_frozen_count=152,
            )
        )
        if rule.rule_id == "A5"
    )
    assert a5.evaluation_state == "degraded"
    assert "尚无成功记录" in a5.detail
    assert "冻结 152 篇" in a5.detail
    text = _format_resolved(a5, "2026-08-08T00:00:00+08:00")
    assert "转为不可评估" in text
    assert "已恢复" not in text
    assert a5.detail in text

    scoped = AlertRuleResult(
        "A6",
        "成本突变",
        False,
        "近 24 小时已记录行金额低于阈值",
        "无需动作",
        evaluation_state="scope_limited",
    )
    resolved = _format_resolved(scoped, None)
    assert "记录行金额已回落" in resolved
    assert "近 24 小时已记录行金额低于阈值" in resolved
    assert "已恢复" not in resolved
    assert "恢复证据" not in resolved


def test_a6_notice_and_page_tiers_and_consistent_driver_disposition() -> None:
    notice = next(
        rule
        for rule in evaluate_rules(
            _signals(
                a6_evaluable=True,
                a6_current_cost_cny=80.0,
                a6_baseline_median_cny=20.0,
                a6_threshold_cny=60.0,
                a6_page_threshold_cny=120.0,
                a6_baseline_days=14,
            )
        )
        if rule.rule_id == "A6"
    )
    page = next(
        rule
        for rule in evaluate_rules(
            _signals(
                a6_evaluable=True,
                a6_current_cost_cny=130.0,
                a6_baseline_median_cny=20.0,
                a6_threshold_cny=60.0,
                a6_page_threshold_cny=120.0,
                a6_baseline_days=14,
            )
        )
        if rule.rule_id == "A6"
    )
    assert notice.firing and notice.severity == "notice"
    assert page.firing and page.severity == "page"
    assert "A6 cache 中性已知成本聚合" in page.action
    assert "未定价调用另见 /admin/usage" in page.action
    assert "sqlite3" not in page.action
    assert "cache 中性" in page.detail
    assert page.impact and page.urgency


def test_a6_notice_to_page_is_one_incident_and_only_recovers_after_condition_clears(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "a6-severity.json"
    event_path = tmp_path / "a6-events.jsonl"
    started = datetime.fromisoformat("2026-08-11T08:00:00+08:00")
    calls: list[tuple[str, str]] = []

    def result(cost: float) -> AlertRuleResult:
        return next(
            rule
            for rule in evaluate_rules(
                _signals(
                    a6_evaluable=True,
                    a6_current_cost_cny=cost,
                    a6_baseline_median_cny=20.0,
                    a6_threshold_cny=60.0,
                    a6_page_threshold_cny=120.0,
                    a6_baseline_days=14,
                )
            )
            if rule.rule_id == "A6"
        )

    sender = lambda text, *, severity="page": calls.append((text, severity)) or {  # noqa: E731
        "skipped": False
    }
    notice = run_alert_results_state_machine(
        [result(80.0)], state_path=state_path, event_path=event_path, now=started, send=sender
    )
    escalated = run_alert_results_state_machine(
        [result(130.0)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=1),
        send=sender,
    )

    assert [(row["type"], row["effective_severity"]) for row in notice["sent"] + escalated["sent"]] == [
        ("firing", "notice"),
        ("firing", "page"),
    ]
    assert [severity for _text, severity in calls] == ["notice", "page"]
    assert all("✅" not in text and "已恢复" not in text for text, _severity in calls)
    assert notice["sent"][0]["episode_since"] == escalated["sent"][0]["episode_since"]

    recovered = run_alert_results_state_machine(
        [result(20.0)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=2),
        send=sender,
    )
    assert [(row["type"], row["effective_severity"]) for row in recovered["sent"]] == [
        ("resolved", "page")
    ]
    assert "✅ A6" in calls[-1][0]
    assert "记录行金额已回落" in calls[-1][0]
    assert "已恢复" not in calls[-1][0]
    assert "恢复证据" not in calls[-1][0]


def test_a6_firing_episode_survives_in_flight_measurement_gap(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "a6-in-flight.json"
    event_path = tmp_path / "a6-in-flight-events.jsonl"
    started = datetime.fromisoformat("2026-08-11T16:12:00+08:00")
    messages: list[tuple[str, str]] = []

    def rule(*, cost: float, evaluable: bool, in_progress: bool = False) -> AlertRuleResult:
        return next(
            result
            for result in evaluate_rules(
                _signals(
                    a6_evaluable=evaluable,
                    a6_measurement_in_progress=in_progress,
                    a6_current_cost_cny=cost,
                    a6_baseline_median_cny=20.0,
                    a6_threshold_cny=60.0,
                    a6_page_threshold_cny=120.0,
                    a6_baseline_days=14,
                )
            )
            if result.rule_id == "A6"
        )

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        messages.append((text, severity))
        return {"skipped": False}

    notice = run_alert_results_state_machine(
        [rule(cost=80.0, evaluable=True)],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=sender,
    )
    escalated = run_alert_results_state_machine(
        [rule(cost=130.0, evaluable=False, in_progress=True)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=5),
        send=sender,
    )
    held = run_alert_results_state_machine(
        [rule(cost=20.0, evaluable=False, in_progress=True)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=10),
        send=sender,
    )
    held_state = json.loads(state_path.read_text(encoding="utf-8"))["A6"]
    recovered = run_alert_results_state_machine(
        [rule(cost=20.0, evaluable=True)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=15),
        send=sender,
    )

    assert held["sent"] == []
    assert held_state["state"] == "firing"
    assert held_state["since"] == started.isoformat()
    assert held_state["evaluation_state"] == "in_progress"
    receipts = notice["sent"] + escalated["sent"] + recovered["sent"]
    assert [(row["type"], row["effective_severity"]) for row in receipts] == [
        ("firing", "notice"),
        ("firing", "page"),
        ("resolved", "page"),
    ]
    assert {row["episode_since"] for row in receipts} == {started.isoformat()}
    assert [severity for _text, severity in messages] == ["notice", "page", "page"]
    assert all("已恢复" not in text and "✅" not in text for text, _ in messages[:2])
    assert "未封口下界" in messages[1][0]
    assert "暂缓评估" not in messages[1][0]
    assert "✅ A6" in messages[-1][0]
    assert "记录行金额已回落" in messages[-1][0]
    assert "已恢复" not in messages[-1][0]
    assert "恢复证据" not in messages[-1][0]


def test_a6_in_flight_lower_bound_can_first_page(tmp_path: Path) -> None:
    state_path = tmp_path / "a6-in-flight-first-page.json"
    event_path = tmp_path / "a6-in-flight-first-page-events.jsonl"
    now = datetime.fromisoformat("2026-08-11T16:17:00+08:00")
    messages: list[tuple[str, str]] = []
    result = next(
        rule
        for rule in evaluate_rules(
            _signals(
                a6_evaluable=False,
                a6_measurement_in_progress=True,
                a6_current_cost_cny=130.0,
                a6_baseline_median_cny=20.0,
                a6_threshold_cny=60.0,
                a6_page_threshold_cny=120.0,
                a6_baseline_days=14,
            )
        )
        if rule.rule_id == "A6"
    )

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        messages.append((text, severity))
        return {"skipped": False}

    outcome = run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=event_path,
        now=now,
        send=sender,
    )

    assert result.firing is True
    assert result.severity == "page"
    assert result.evaluation_state == "in_progress"
    assert [(row["type"], row["effective_severity"]) for row in outcome["sent"]] == [
        ("firing", "page")
    ]
    assert outcome["sent"][0]["episode_since"] == now.isoformat()
    assert [severity for _text, severity in messages] == ["page"]
    assert "未封口下界" in messages[0][0]
    assert "已恢复" not in messages[0][0]


def test_a6_identifies_only_live_current_day_run_as_measurement_in_progress(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "pipeline-20260810-170000.log").write_text(
        "[2026-08-10T17:00:00+08:00] === fetch START ===\n"
        "[2026-08-10T17:01:00+08:00] === fetch OK ===\n"
        "[2026-08-10T17:10:00+08:00] === PIPELINE DONE (failed=0) ===\n",
        encoding="utf-8",
    )
    (log_dir / "pipeline-20260811-161500.log").write_text(
        "[2026-08-11T16:15:00+08:00] === fetch START ===\n",
        encoding="utf-8",
    )
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    pid = os.getpid()
    process_start = " ".join(
        subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
    )
    (lock_dir / "owner").write_text(
        f"token=test\npid={pid}\nprocess_start={process_start}\n",
        encoding="utf-8",
    )
    now = datetime.fromisoformat("2026-08-11T16:17:00+08:00")
    start = now - timedelta(hours=24)
    activity = _pipeline_activity(log_dir, start, now)

    assert _a6_measurement_in_progress(
        activity, start=start, end=now, lock_dir=lock_dir
    ) is True
    activity.pop("2026-08-10")
    assert _a6_measurement_in_progress(
        activity, start=start, end=now, lock_dir=lock_dir
    ) is False
    (lock_dir / "owner").write_text(
        "token=stale\npid=999999\nprocess_start=stale\n", encoding="utf-8"
    )
    assert _a6_measurement_in_progress(
        _pipeline_activity(log_dir, start, now),
        start=start,
        end=now,
        lock_dir=lock_dir,
    ) is False


def test_a6_genuine_missing_measurement_still_closes_firing_episode(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "a6-missing.json"
    event_path = tmp_path / "a6-missing-events.jsonl"
    started = datetime.fromisoformat("2026-08-11T16:12:00+08:00")

    def result(evaluable: bool) -> AlertRuleResult:
        return next(
            rule
            for rule in evaluate_rules(
                _signals(
                    a6_evaluable=evaluable,
                    a6_measurement_in_progress=False,
                    a6_current_cost_cny=80.0,
                    a6_baseline_median_cny=20.0,
                    a6_threshold_cny=60.0,
                    a6_page_threshold_cny=120.0,
                    a6_baseline_days=14,
                )
            )
            if rule.rule_id == "A6"
        )

    sender = lambda text, *, severity="page": {"skipped": False}  # noqa: E731
    fired = run_alert_results_state_machine(
        [result(True)], state_path=state_path, event_path=event_path, now=started, send=sender
    )
    missing = run_alert_results_state_machine(
        [result(False)],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=5),
        send=sender,
    )

    assert fired["sent"][0]["type"] == "firing"
    assert missing["sent"][0]["type"] == "resolved"
    assert "转为不可评估" in missing["sent"][0]["text"]


def test_d3_retries_send_and_clear_preserves_intermittent_price_history(tmp_path: Path) -> None:
    state_path = tmp_path / "d3.json"
    ledger = tmp_path / "events.jsonl"
    report: dict[str, object] = {
        "totals": {"calls": 20},
        "unpriced": [{"provider": "p", "model": "m", "calls": 3}],
        "pricing_freshness": [],
        "pricing_table": [],
    }
    attempts: list[str] = []

    def fail_once(text: str, **kwargs: object) -> dict[str, object]:
        attempts.append(text)
        return {"skipped": len(attempts) == 1}

    run_pricing_notifications(report, state_path=state_path, event_path=ledger, send=fail_once)
    run_pricing_notifications(report, state_path=state_path, event_path=ledger, send=fail_once)
    assert len(attempts) == 2
    assert "3/20 次" in attempts[-1]

    clears = 0

    def fail_clear(key: str) -> dict[str, object]:
        nonlocal clears
        clears += 1
        return {"cleared": clears > 1}

    run_pricing_notifications(
        {**report, "unpriced": []},
        state_path=state_path,
        event_path=ledger,
        send=fail_once,
        clear=fail_clear,
    )
    run_pricing_notifications(
        {**report, "unpriced": []},
        state_path=state_path,
        event_path=ledger,
        send=fail_once,
        clear=fail_clear,
    )
    assert clears == 2
    history = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert any(row["rule_id"].startswith("D3:") and row["type"] == "firing" for row in history)
    assert any(row["rule_id"].startswith("D3:") and row["type"] == "resolved" for row in history)


def test_d3_rearms_when_condition_recurs_after_clear_failure(tmp_path: Path) -> None:
    state_path = tmp_path / "d3-recur.json"
    report: dict[str, object] = {
        "totals": {"calls": 20},
        "unpriced": [{"provider": "p", "model": "m", "calls": 3}],
        "pricing_freshness": [],
        "pricing_table": [],
    }
    sends: list[str] = []
    dedup_texts: list[str] = []

    def sender(text: str, **kwargs: object) -> dict[str, object]:
        sends.append(text)
        dedup_texts.append(str(kwargs["dedup_text"]))
        return {"skipped": len(sends) == 2}

    run_pricing_notifications(report, state_path=state_path, send=sender)
    run_pricing_notifications(
        {**report, "unpriced": []},
        state_path=state_path,
        send=sender,
        clear=lambda _key: {"cleared": False},
    )
    first_recur = run_pricing_notifications(report, state_path=state_path, send=sender)
    retried_recur = run_pricing_notifications(report, state_path=state_path, send=sender)

    assert len(sends) == 3
    assert first_recur["sent"][0]["delivered"] is False
    assert retried_recur["sent"][0]["delivered"] is True
    assert dedup_texts[0] != dedup_texts[1]
    assert dedup_texts[1] == dedup_texts[2]


def test_d3_count_change_does_not_resend_and_price_change_keeps_old_value(tmp_path: Path) -> None:
    state_path = tmp_path / "d3.json"
    sent: list[str] = []

    def sender(text: str, **kwargs: object) -> dict[str, object]:
        sent.append(text)
        return {"skipped": False}

    base: dict[str, object] = {
        "totals": {"calls": 20},
        "unpriced": [{"provider": "p", "model": "m", "calls": 3}],
        "pricing_freshness": ["fresh"],
        "pricing_table": [{
            "provider": "deepseek", "model": "model", "matched_key": "deepseek/model",
            "freshness": "fresh", "input_per_million_tokens_usd": 1,
            "cache_read_per_million_tokens_usd": 0.1, "output_per_million_tokens_usd": 2,
            "effective_from": None, "effective_to": None,
        }],
    }
    run_pricing_notifications(base, state_path=state_path, send=sender)
    changed_count = {**base, "unpriced": [{"provider": "p", "model": "m", "calls": 4}]}
    run_pricing_notifications(changed_count, state_path=state_path, send=sender)
    assert len(sent) == 1

    absent = {**base, "unpriced": [], "pricing_table": []}
    run_pricing_notifications(absent, state_path=state_path, send=sender)
    changed = json.loads(json.dumps(absent))
    changed["pricing_table"] = [dict(base["pricing_table"][0], input_per_million_tokens_usd=3)]  # type: ignore[index]
    run_pricing_notifications(changed, state_path=state_path, send=sender)
    assert "旧值：input/cache/output USD per 1M=1/0.1/2" in sent[-1]
    assert "新值：input/cache/output USD per 1M=3/0.1/2" in sent[-1]


def test_correlation_is_heartbeat_gated_and_suppression_is_ledgered(tmp_path: Path) -> None:
    firing = [
        AlertRuleResult("A1", "上游", True, "供应商错误", "查供应商"),
        AlertRuleResult("A2", "阶段", True, "prefilter 错误", "查日志"),
        AlertRuleResult("A5", "停滞", True, "解读停滞", "查解释链路"),
    ]
    fresh = _correlate_alert_results(firing, heartbeat_fresh=True)
    assert next(row for row in fresh if row.rule_id == "A5").suppressed_by is None
    assert next(row for row in fresh if row.rule_id == "A1").suppressed_by == "A5"
    stale = _correlate_alert_results(firing, heartbeat_fresh=False)
    assert next(row for row in stale if row.rule_id == "A5").suppressed_by == "A2"

    event_path = tmp_path / "events.jsonl"
    run_alert_results_state_machine(
        fresh,
        state_path=tmp_path / "state.json",
        event_path=event_path,
        now=datetime.fromisoformat("2026-08-11T09:17:00+08:00"),
        send=lambda text, **kwargs: {"skipped": False},
    )
    rows = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    suppressed = [row for row in rows if row["type"] == "suppressed"]
    assert {row["rule_id"] for row in suppressed} == {"A1", "A2"}
    assert all(row["channel"] == "INTERNAL" and row["carrier"] == "A5" for row in suppressed)


def test_admin_metrics_surfaces_degraded_rules(tmp_path: Path) -> None:
    state_path = tmp_path / "alert-state.json"
    state_path.write_text(
        json.dumps({"A6": {"state": "ok", "evaluation_state": "degraded", "detail": "基线不足"}}),
        encoding="utf-8",
    )
    db.migrate(tmp_path / "radar.db")
    metrics = collect_metrics(
        db_path=tmp_path / "radar.db",
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[],
        alert_state_path=state_path,
    )
    assert metrics["alerts"]["firing"] == []
    assert metrics["alerts"]["degraded"] == ["A6 基线不足"]
