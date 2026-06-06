from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .. import db
from .calibration import SCHEMA_ERROR_RE, _is_upstream_error
from .metrics import SHANGHAI_TZ, _parse_dt, collect_metrics
from .thresholds import ALERT_THRESHOLDS

RULESET = ("A1", "A2", "A3", "A4")
DEFAULT_STATE_PATH = db.PROJECT_ROOT / "data" / "alert-state.json"
COOLDOWN = timedelta(minutes=30)


@dataclass
class AlertSignals:
    upstream_sample_size: int
    upstream_error_rate: float
    upstream_schema_error_rate: float
    stage_error_rate: dict[str, float]
    stage_p95_latency_ms: dict[str, int]
    minutes_since_successful_pipeline: int
    consecutive_skip_logs: int
    server_error_rate: float
    health_failures: int
    fetch_failed_ratio: float
    items_today: int


@dataclass(frozen=True)
class AlertRuleResult:
    rule_id: str
    title: str
    firing: bool
    detail: str
    action: str
    values: dict[str, object] = field(default_factory=dict)


def _threshold_section(thresholds: dict[str, object], key: str) -> dict[str, Any]:
    value = thresholds.get(key, {})
    return value if isinstance(value, dict) else {}


def _float_threshold(section: dict[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    return float(value) if value is not None else default


def _int_threshold(section: dict[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    return int(value) if value is not None else default


def evaluate_rules(
    signals: AlertSignals,
    thresholds: dict[str, object] | None = None,
) -> list[AlertRuleResult]:
    active_thresholds = thresholds or ALERT_THRESHOLDS
    a1 = _threshold_section(active_thresholds, "a1")
    a2 = _threshold_section(active_thresholds, "a2")
    a3 = _threshold_section(active_thresholds, "a3")
    a4 = _threshold_section(active_thresholds, "a4")

    a1_min_samples = _int_threshold(a1, "min_samples", 5)
    a1_rate = _float_threshold(a1, "upstream_error_rate", 0.5)
    a1_firing = signals.upstream_sample_size >= a1_min_samples and signals.upstream_error_rate > a1_rate

    stage_error_thresholds = a2.get("stage_error_rate", {})
    stage_p95_thresholds = a2.get("stage_p95_latency_ms", {})
    stage_reasons: list[str] = []
    if isinstance(stage_error_thresholds, dict):
        for stage, observed in sorted(signals.stage_error_rate.items()):
            threshold = float(stage_error_thresholds.get(stage, 0.3))
            if observed > threshold:
                stage_reasons.append(f"{stage} 错误率 {observed:.1%} > {threshold:.1%}")
    if isinstance(stage_p95_thresholds, dict):
        for stage, observed in sorted(signals.stage_p95_latency_ms.items()):
            threshold = stage_p95_thresholds.get(stage)
            if threshold is not None and observed > int(threshold):
                stage_reasons.append(f"{stage} P95 {observed}ms > {int(threshold)}ms")
    no_success_minutes = _int_threshold(a2, "no_success_minutes", 45)
    if signals.minutes_since_successful_pipeline > no_success_minutes:
        stage_reasons.append(f"最近成功 pipeline 已超过 {signals.minutes_since_successful_pipeline} 分钟")
    skip_logs = _int_threshold(a2, "skip_logs", 3)
    if signals.consecutive_skip_logs >= skip_logs:
        stage_reasons.append(f"连续 SKIP 日志 {signals.consecutive_skip_logs} 个")

    a3_rate = _float_threshold(a3, "server_error_rate", 0.05)
    health_failures = _int_threshold(a3, "health_failures", 2)
    a3_firing = signals.server_error_rate > a3_rate or signals.health_failures >= health_failures

    a4_fetch_ratio = _float_threshold(a4, "fetch_failed_ratio", 0.4)
    daily_inserted_floor = _int_threshold(a4, "daily_inserted_floor", 0)
    a4_firing = signals.fetch_failed_ratio > a4_fetch_ratio or signals.items_today < daily_inserted_floor

    return [
        AlertRuleResult(
            rule_id="A1",
            title="上游模型不可用",
            firing=a1_firing,
            detail=(
                f"上游错误率 {signals.upstream_error_rate:.1%}，样本 {signals.upstream_sample_size}；"
                f"schema 噪声率 {signals.upstream_schema_error_rate:.1%} 已排除"
            ),
            action="检查 DeepSeek/模型供应商余额、模型权限与 provider endpoint；欠费会伪装成 404/InvalidEndpoint。",
            values={
                "upstream_error_rate": signals.upstream_error_rate,
                "upstream_sample_size": signals.upstream_sample_size,
            },
        ),
        AlertRuleResult(
            rule_id="A2",
            title="阶段错误率/耗时异常",
            firing=bool(stage_reasons),
            detail="；".join(stage_reasons) if stage_reasons else "各阶段错误率、耗时与 pipeline 心跳在阈值内",
            action="查看 pipeline 最新日志，定位异常 stage；若无成功轮次或连续 SKIP，检查 pipeline 锁和长任务。",
            values={
                "stage_error_rate": signals.stage_error_rate,
                "stage_p95_latency_ms": signals.stage_p95_latency_ms,
                "minutes_since_successful_pipeline": signals.minutes_since_successful_pipeline,
                "consecutive_skip_logs": signals.consecutive_skip_logs,
            },
        ),
        AlertRuleResult(
            rule_id="A3",
            title="网站用户侧异常",
            firing=a3_firing,
            detail=f"5xx 率 {signals.server_error_rate:.1%}，healthz 连续失败 {signals.health_failures} 次",
            action="先探测 /api/v1/healthz 与 serve 日志；若 healthz 正常但 5xx 上升，查看最近部署和 upstream API。",
            values={"server_error_rate": signals.server_error_rate, "health_failures": signals.health_failures},
        ),
        AlertRuleResult(
            rule_id="A4",
            title="文章摄取骤降",
            firing=a4_firing,
            detail=f"最近 fetch 失败率 {signals.fetch_failed_ratio:.1%}，今日 items 增量 {signals.items_today}",
            action="查看最近一轮 fetch 源健康；微信源连接失败先检查 wewe-rss/bridge，其余源按 source error 分组处理。",
            values={"fetch_failed_ratio": signals.fetch_failed_ratio, "items_today": signals.items_today},
        ),
    ]


def _format_firing(result: AlertRuleResult) -> str:
    return (
        f"🔴 {result.rule_id} {result.title}\n"
        f"故障类别：{result.title}\n"
        f"具体故障对象/数值：{result.detail}\n"
        f"处置方向：{result.action}"
    )


def _format_resolved(result: AlertRuleResult, since: str | None) -> str:
    suffix = f"（since {since}）" if since else ""
    return f"✅ {result.rule_id} {result.title} 已恢复{suffix}"


def _load_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_state(path: Path, state: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def run_alert_state_machine(
    signals: AlertSignals,
    *,
    state_path: str | Path = DEFAULT_STATE_PATH,
    now: datetime | None = None,
    send: Callable[[str], object] | None = None,
    thresholds: dict[str, object] | None = None,
) -> dict[str, object]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    path = Path(state_path)
    state = _load_state(path)
    sender = send or (lambda text: send_feishu_message(os.environ.get("FEISHU_GENERAL_ALERT_WEBHOOK"), text))
    results = evaluate_rules(signals, thresholds=thresholds)
    sent: list[dict[str, object]] = []

    for result in results:
        entry = state.get(result.rule_id, {"state": "ok"})
        previous_state = str(entry.get("state", "ok"))
        last_notified = _parse_dt(entry.get("last_notified"))
        if result.firing:
            should_notify = previous_state != "firing" or last_notified is None or current - last_notified >= COOLDOWN
            if should_notify:
                text = _format_firing(result)
                send_result = sender(text)
                sent.append({"rule_id": result.rule_id, "type": "firing", "text": text, "send_result": send_result})
            state[result.rule_id] = {
                "state": "firing",
                "since": entry.get("since") if previous_state == "firing" else current.isoformat(),
                "last_notified": current.isoformat() if should_notify else entry.get("last_notified"),
                "detail": result.detail,
            }
        else:
            if previous_state == "firing":
                text = _format_resolved(result, str(entry.get("since") or ""))
                send_result = sender(text)
                sent.append({"rule_id": result.rule_id, "type": "resolved", "text": text, "send_result": send_result})
            state[result.rule_id] = {
                "state": "ok",
                "since": None,
                "last_notified": entry.get("last_notified"),
                "detail": result.detail,
            }

    _write_state(path, state)
    return {
        "ruleset": list(RULESET),
        "sent_count": len(sent),
        "sent": sent,
        "results": [asdict(result) for result in results],
        "state_path": str(path),
    }


def send_feishu_message(webhook_url: str | None, text: str) -> dict[str, object]:
    if not webhook_url:
        return {"skipped": True, "reason": "FEISHU_GENERAL_ALERT_WEBHOOK is not set"}
    payload = {"msg_type": "text", "content": {"text": text}}
    response = httpx.post(webhook_url, json=payload, timeout=10.0)
    response.raise_for_status()
    return {"skipped": False, "status_code": response.status_code, "text": response.text}


def _recent_upstream_stats(db_path: str | Path | None, since: datetime) -> tuple[int, float, float]:
    with db.get_conn(db_path) as conn:
        rows = conn.execute("SELECT error, evaluated_at FROM item_evaluations ORDER BY evaluated_at").fetchall()
    total = 0
    upstream = 0
    schema = 0
    for row in rows:
        evaluated_at = _parse_dt(row["evaluated_at"])
        if evaluated_at is None or evaluated_at < since:
            continue
        total += 1
        error = row["error"]
        if _is_upstream_error(error):
            upstream += 1
        if error and SCHEMA_ERROR_RE.search(str(error)):
            schema += 1
    return total, upstream / total if total else 0.0, schema / total if total else 0.0


def collect_alert_signals(
    *,
    db_path: str | Path | None = None,
    pipeline_log_dir: str | Path | None = None,
    access_log_paths: list[str | Path] | None = None,
    now: datetime | None = None,
) -> AlertSignals:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    a1 = _threshold_section(ALERT_THRESHOLDS, "a1")
    window_minutes = _int_threshold(a1, "window_minutes", 15)
    recent_since = current - timedelta(minutes=window_minutes)
    sample_size, upstream_rate, schema_rate = _recent_upstream_stats(db_path, recent_since)

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=pipeline_log_dir,
        access_log_paths=access_log_paths,
        now=current,
    )
    pipeline = metrics.get("pipeline", {})
    assert isinstance(pipeline, dict)
    stages = pipeline.get("stages", {})
    assert isinstance(stages, dict)
    stage_error_rate: dict[str, float] = {}
    stage_p95_latency_ms: dict[str, int] = {}
    for stage in ("prefilter", "scoring", "enrich"):
        row = stages.get(stage, {})
        if not isinstance(row, dict):
            continue
        stage_error_rate[stage] = float(row.get("error_rate") or 0.0)
        p95 = row.get("p95_latency_ms")
        stage_p95_latency_ms[stage] = int(p95) if p95 is not None else 0

    recent_runs = pipeline.get("recent_runs", [])
    last_success: datetime | None = None
    consecutive_skip_logs = 0
    if isinstance(recent_runs, list):
        for run in reversed(recent_runs):
            if not isinstance(run, dict):
                continue
            if run.get("skip") and last_success is None:
                consecutive_skip_logs += 1
            started_at = run.get("started_at")
            if run.get("status") == "done" and int(str(run.get("failed") or 0)) == 0 and isinstance(started_at, datetime):
                last_success = started_at
                break
    minutes_since_success = int((current - last_success).total_seconds() / 60) if last_success else 10_000

    users = metrics["users"]
    ingestion = metrics["ingestion"]
    assert isinstance(users, dict)
    assert isinstance(ingestion, dict)
    latest_fetch = ingestion.get("latest_fetch", {})
    attempted = int(latest_fetch.get("attempted", 0)) if isinstance(latest_fetch, dict) else 0
    failed = int(latest_fetch.get("failed", 0)) if isinstance(latest_fetch, dict) else 0

    return AlertSignals(
        upstream_sample_size=sample_size,
        upstream_error_rate=upstream_rate,
        upstream_schema_error_rate=schema_rate,
        stage_error_rate=stage_error_rate,
        stage_p95_latency_ms=stage_p95_latency_ms,
        minutes_since_successful_pipeline=minutes_since_success,
        consecutive_skip_logs=consecutive_skip_logs,
        server_error_rate=_server_error_rate(users),
        health_failures=0,
        fetch_failed_ratio=failed / attempted if attempted else 0.0,
        items_today=int(ingestion.get("items_today") or 0),
    )


def _server_error_rate(users: dict[str, object]) -> float:
    pv = int(str(users.get("pv") or 0))
    raw_status_counts = users.get("status_counts", {})
    if not isinstance(raw_status_counts, dict) or not pv:
        return 0.0
    errors = 0
    for status, count in raw_status_counts.items():
        if 500 <= int(status) <= 599:
            errors += int(str(count))
    return errors / pv
