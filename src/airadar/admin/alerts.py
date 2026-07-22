from __future__ import annotations

import json
import logging
import math
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from .. import db
from .calibration import SCHEMA_ERROR_RE, _is_upstream_error
from .metrics import SHANGHAI_TZ, _parse_dt, collect_metrics
from .thresholds import ALERT_THRESHOLDS

RULESET = ("A1", "A2", "A3", "A4")
DEFAULT_STATE_PATH = db.PROJECT_ROOT / "data" / "alert-state.json"
COOLDOWN = timedelta(minutes=30)
MINUTES_PER_DAY = 24 * 60
HEALTHZ_STATE_KEY = "healthz_probe"
# src/airadar/web/routes/health.py registers /healthz and create_app mounts it
# under the app API prefix /api/v1.
DEFAULT_HEALTHZ_URL = "http://127.0.0.1:8000/api/v1/healthz"
DEFAULT_HEALTHZ_TIMEOUT_SECONDS = 2.0
# Project label prefixed on every alert so a shared Feishu webhook (used by
# multiple projects) lets the reader tell which project an alert came from.
ALERT_SOURCE = "AI Radar"
AlertSeverity = Literal["page", "notice"]
PAGE_SEVERITY: Literal["page"] = "page"
NOTICE_SEVERITY: Literal["notice"] = "notice"
ALERT_CHANNEL = "ALERT"
NOTIFICATION_CHANNEL = "NOTIFICATION"
LOGGER = logging.getLogger(__name__)


class AlertSender(Protocol):
    def __call__(self, text: str, *, severity: AlertSeverity = PAGE_SEVERITY) -> object: ...


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
    fetch_failed_ratio: float
    items_today: int
    minutes_elapsed_today: int = MINUTES_PER_DAY
    healthz_consecutive_failures: int = 0
    stage_sample_count: dict[str, int] = field(default_factory=dict)
    server_pv: int = 0


@dataclass(frozen=True)
class AlertRuleResult:
    rule_id: str
    title: str
    firing: bool
    detail: str
    action: str
    values: dict[str, object] = field(default_factory=dict)
    severity: AlertSeverity = PAGE_SEVERITY
    impact: str = ""
    urgency: str = ""


def _threshold_section(thresholds: dict[str, object], key: str) -> dict[str, Any]:
    value = thresholds.get(key, {})
    return value if isinstance(value, dict) else {}


def _float_threshold(section: dict[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    return float(value) if value is not None else default


def _int_threshold(section: dict[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    return int(value) if value is not None else default


def _debounce_window(
    thresholds: dict[str, object],
    rule_id: str,
    severity: AlertSeverity,
) -> timedelta:
    section = _threshold_section(thresholds, rule_id.lower())
    by_severity = section.get("debounce_minutes_by_severity")
    if isinstance(by_severity, dict) and severity in by_severity:
        return timedelta(minutes=_int_threshold(by_severity, severity, 0))
    return timedelta(minutes=_int_threshold(section, "debounce_minutes", 0))


def _minutes_elapsed_today(now: datetime) -> int:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = int((now - start).total_seconds() // 60)
    return max(0, min(MINUTES_PER_DAY, elapsed))


def _daily_inserted_floor_elapsed(daily_floor: int, minutes_elapsed_today: int) -> int:
    elapsed = max(0, min(MINUTES_PER_DAY, minutes_elapsed_today))
    return int(daily_floor * elapsed / MINUTES_PER_DAY)


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
    stage_min_samples = a2.get("min_samples", {})
    stage_p95_thresholds = a2.get("stage_p95_latency_ms", {})
    stage_reasons: list[str] = []
    if isinstance(stage_error_thresholds, dict):
        for stage, observed in sorted(signals.stage_error_rate.items()):
            threshold = float(stage_error_thresholds.get(stage, 0.3))
            default_min_samples = math.ceil(1 / threshold) if threshold > 0 else 1
            min_samples = (
                int(stage_min_samples.get(stage, default_min_samples))
                if isinstance(stage_min_samples, dict)
                else default_min_samples
            )
            sample_count = signals.stage_sample_count.get(stage, 0)
            if sample_count >= min_samples and observed > threshold:
                stage_reasons.append(f"{stage} 错误率 {observed:.1%} > {threshold:.1%}")
    if isinstance(stage_p95_thresholds, dict):
        for stage, observed in sorted(signals.stage_p95_latency_ms.items()):
            threshold = stage_p95_thresholds.get(stage)
            if threshold is not None and observed > int(threshold):
                stage_reasons.append(f"{stage} P95 {observed}ms > {int(threshold)}ms")
    # A SKIP log means "pipeline already running" — a run is in progress, which is
    # liveness, not a fault. So skip count is never a standalone trigger; it only
    # rides along as context when the heartbeat itself has genuinely gone stale.
    no_success_minutes = _int_threshold(a2, "no_success_minutes", 120)
    if signals.minutes_since_successful_pipeline > no_success_minutes:
        skip_note = (
            f"（期间连续 SKIP {signals.consecutive_skip_logs} 次，疑似卡死/僵尸锁）"
            if signals.consecutive_skip_logs
            else ""
        )
        stage_reasons.append(
            f"最近成功 pipeline 已超过 {signals.minutes_since_successful_pipeline} 分钟{skip_note}"
        )

    a3_rate = _float_threshold(a3, "server_error_rate", 0.05)
    a3_min_pv = _int_threshold(a3, "min_pv", math.ceil(1 / a3_rate) if a3_rate > 0 else 1)
    a3_healthz_failure_threshold = _int_threshold(a3, "healthz_consecutive_failures", 2)
    a3_reasons: list[str] = []
    if signals.server_pv >= a3_min_pv and signals.server_error_rate > a3_rate:
        a3_reasons.append(f"用户侧 5xx 率 {signals.server_error_rate:.1%} > {a3_rate:.1%}")
    if (
        a3_healthz_failure_threshold > 0
        and signals.healthz_consecutive_failures >= a3_healthz_failure_threshold
    ):
        a3_reasons.append(
            f"healthz 连续失败 {signals.healthz_consecutive_failures} 次 >= {a3_healthz_failure_threshold} 次"
        )
    a3_firing = bool(a3_reasons)

    a4_fetch_ratio = _float_threshold(a4, "fetch_failed_ratio", 0.4)
    daily_inserted_floor = _int_threshold(a4, "daily_inserted_floor", 0)
    daily_inserted_floor_elapsed = _daily_inserted_floor_elapsed(
        daily_inserted_floor,
        signals.minutes_elapsed_today,
    )
    a4_fetch_failed = signals.fetch_failed_ratio > a4_fetch_ratio
    a4_items_low = signals.items_today < daily_inserted_floor_elapsed
    a4_firing = a4_fetch_failed or a4_items_low
    a4_severity: AlertSeverity = (
        PAGE_SEVERITY if a4_items_low or not a4_fetch_failed else NOTICE_SEVERITY
    )
    a4_reasons: list[str] = []
    if a4_fetch_failed:
        a4_reasons.append(f"最近 fetch 失败率 {signals.fetch_failed_ratio:.1%} > {a4_fetch_ratio:.1%}")
    if a4_items_low:
        a4_reasons.append(
            f"今日 items 增量 {signals.items_today} < 按日内进度 floor "
            f"{daily_inserted_floor_elapsed}/{daily_inserted_floor}"
        )
    if a4_items_low:
        a4_impact = "文章更新可能停滞"
        a4_urgency = "是——需立即核查"
    elif a4_fetch_failed:
        a4_impact = "当前摄取量正常，fetch 失败主要反映结构性源站波动"
        a4_urgency = "否——当前摄取量正常，无需立即处置"
    else:
        a4_impact = ""
        a4_urgency = ""

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
                "stage_sample_count": signals.stage_sample_count,
                "stage_p95_latency_ms": signals.stage_p95_latency_ms,
                "minutes_since_successful_pipeline": signals.minutes_since_successful_pipeline,
                "consecutive_skip_logs": signals.consecutive_skip_logs,
            },
        ),
        AlertRuleResult(
            rule_id="A3",
            title="网站用户侧异常",
            firing=a3_firing,
            detail=(
                "；".join(a3_reasons)
                if a3_reasons
                else (
                    f"用户侧 5xx 率 {signals.server_error_rate:.1%}，"
                    f"healthz 连续失败 {signals.healthz_consecutive_failures} 次"
                )
            ),
            action="先探测 /api/v1/healthz 与 serve 日志；若 healthz 正常但 5xx 上升，查看最近部署和 upstream API。",
            values={
                "server_error_rate": signals.server_error_rate,
                "server_pv": signals.server_pv,
                "healthz_consecutive_failures": signals.healthz_consecutive_failures,
            },
        ),
        AlertRuleResult(
            rule_id="A4",
            title="文章摄取骤降",
            firing=a4_firing,
            detail=(
                "；".join(a4_reasons)
                if a4_reasons
                else (
                    f"最近 fetch 失败率 {signals.fetch_failed_ratio:.1%}，"
                    f"今日 items 增量 {signals.items_today}，按日内进度 floor "
                    f"{daily_inserted_floor_elapsed}/{daily_inserted_floor}，均在阈值内"
                )
            ),
            action="查看最近一轮各源健康并按 source error 分组：X(nitter) 源整批 SSL/超时多为公共实例瞬态（已加 30min 去抖，持续才告警）；微信源走 Mp2RSS；其余源按错误类型分别处理。",
            values={
                "fetch_failed_ratio": signals.fetch_failed_ratio,
                "items_today": signals.items_today,
                "minutes_elapsed_today": signals.minutes_elapsed_today,
                "daily_inserted_floor": daily_inserted_floor,
                "daily_inserted_floor_elapsed": daily_inserted_floor_elapsed,
            },
            severity=a4_severity,
            impact=a4_impact,
            urgency=a4_urgency,
        ),
    ]


def _format_firing(result: AlertRuleResult) -> str:
    severity = _normalize_severity(result.severity)
    emoji = "🟡" if severity == NOTICE_SEVERITY else "🔴"
    lines = [f"【{ALERT_SOURCE}】{emoji} {result.rule_id} {result.title}"]
    if result.impact:
        lines.append(f"影响：{result.impact}")
    if result.urgency:
        lines.append(f"需否立即处置：{result.urgency}")
    lines.extend(
        (
            f"故障类别：{result.title}",
            f"具体故障对象/数值：{result.detail}",
            f"处置方向：{result.action}",
        )
    )
    return "\n".join(lines)


def _format_resolved(result: AlertRuleResult, since: str | None) -> str:
    suffix = f"（since {since}）" if since else ""
    return f"【{ALERT_SOURCE}】✅ {result.rule_id} {result.title} 已恢复{suffix}"


def _normalize_severity(value: object) -> AlertSeverity:
    return NOTICE_SEVERITY if value == NOTICE_SEVERITY else PAGE_SEVERITY


def _severity_channel(severity: AlertSeverity) -> str:
    return NOTIFICATION_CHANNEL if severity == NOTICE_SEVERITY else ALERT_CHANNEL


def _delivery_succeeded(send_result: object) -> bool:
    return isinstance(send_result, Mapping) and send_result.get("skipped") is False


def _entry_announced(entry: dict[str, object]) -> bool:
    if isinstance(entry.get("announced"), bool):
        return entry["announced"] is True
    last_notified = _parse_dt(entry.get("last_notified"))
    since = _parse_dt(entry.get("since"))
    return last_notified is not None and (since is None or last_notified >= since)


def _normalized_lifecycle(entry: dict[str, object]) -> dict[str, object]:
    state = "firing" if entry.get("state") == "firing" else "ok"
    return {
        "state": state,
        "since": entry.get("since") if state == "firing" else None,
        "last_notified": entry.get("last_notified"),
        "detail": entry.get("detail"),
        "announced": state == "firing" and _entry_announced(entry),
    }


def _normalize_lifecycles(entry: dict[str, object]) -> dict[AlertSeverity, dict[str, object]]:
    raw_lifecycles = entry.get("lifecycles")
    lifecycles: dict[AlertSeverity, dict[str, object]] = {}
    if isinstance(raw_lifecycles, dict):
        for severity in (PAGE_SEVERITY, NOTICE_SEVERITY):
            raw = raw_lifecycles.get(severity)
            if isinstance(raw, dict):
                lifecycles[severity] = _normalized_lifecycle(raw)
    if lifecycles:
        return lifecycles
    severity = _normalize_severity(entry.get("severity"))
    lifecycles[severity] = _normalized_lifecycle(entry)
    return lifecycles


def _ok_lifecycle(lifecycle: dict[str, object], detail: str) -> dict[str, object]:
    return {
        "state": "ok",
        "since": None,
        "last_notified": lifecycle.get("last_notified"),
        "detail": detail,
        "announced": False,
    }


def _project_lifecycles(
    lifecycles: dict[AlertSeverity, dict[str, object]],
    *,
    preferred_severity: AlertSeverity,
) -> dict[str, object]:
    # PAGE is the fail-closed projection whenever malformed state contains more
    # than one firing lifecycle; preferred severity only breaks non-page ties.
    active_severity = next(
        (
            severity
            for severity in (PAGE_SEVERITY, preferred_severity, NOTICE_SEVERITY)
            if severity in lifecycles and lifecycles[severity].get("state") == "firing"
        ),
        None,
    )
    projected_severity = active_severity or (
        preferred_severity if preferred_severity in lifecycles else next(iter(lifecycles))
    )
    projected = lifecycles[projected_severity]
    return {
        "state": "firing" if active_severity is not None else "ok",
        "since": projected.get("since") if active_severity is not None else None,
        "last_notified": projected.get("last_notified"),
        "detail": projected.get("detail"),
        "severity": projected_severity,
        "announced": active_severity is not None and _entry_announced(projected),
        "lifecycles": lifecycles,
    }


def _transition_since(
    *,
    outgoing_since: object,
    current: datetime,
    debounce: timedelta,
) -> str:
    outgoing_since_dt = _parse_dt(outgoing_since)
    confirmed_since = current - debounce
    if outgoing_since_dt is not None and outgoing_since_dt <= confirmed_since:
        return str(outgoing_since)
    return confirmed_since.isoformat()


def _invoke_sender(
    *,
    result: AlertRuleResult,
    event_type: Literal["firing", "resolved"],
    text: str,
    severity: AlertSeverity,
    sender: AlertSender,
) -> dict[str, object]:
    send_result = sender(text, severity=severity)
    return {
        "rule_id": result.rule_id,
        "type": event_type,
        "effective_severity": severity,
        "channel": _severity_channel(severity),
        "text": text,
        "send_result": send_result,
    }


def _load_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    state = payload
    for rule_id, entry in list(state.items()):
        if rule_id == HEALTHZ_STATE_KEY or not isinstance(entry, dict):
            continue
        lifecycles = _normalize_lifecycles(entry)
        state[rule_id] = _project_lifecycles(
            lifecycles,
            preferred_severity=_normalize_severity(entry.get("severity")),
        )
    return state


def _write_state(path: Path, state: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _probe_healthz(url: str, timeout: float) -> bool:
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    return payload.get("success") is True and isinstance(data, dict) and data.get("ok") is True


def _record_healthz_probe(
    state: dict[str, dict[str, object]],
    *,
    current: datetime,
    thresholds: dict[str, object],
    healthz_probe: Callable[[str, float], bool] | None,
) -> int:
    a3 = _threshold_section(thresholds, "a3")
    url = str(a3.get("healthz_url") or DEFAULT_HEALTHZ_URL)
    timeout = _float_threshold(a3, "healthz_timeout_seconds", DEFAULT_HEALTHZ_TIMEOUT_SECONDS)
    probe = healthz_probe or _probe_healthz
    last_error: str | None = None
    try:
        ok = bool(probe(url, timeout))
    except Exception as exc:  # noqa: BLE001 - health probes must fail closed.
        ok = False
        last_error = f"{type(exc).__name__}: {exc}"

    entry = state.get(HEALTHZ_STATE_KEY, {})
    previous_failures = _int_threshold(entry, "consecutive_failures", 0) if isinstance(entry, dict) else 0
    consecutive_failures = 0 if ok else previous_failures + 1
    next_entry: dict[str, object] = {
        "consecutive_failures": consecutive_failures,
        "last_checked": current.isoformat(),
        "last_ok": ok,
        "url": url,
    }
    if last_error:
        next_entry["last_error"] = last_error
    state[HEALTHZ_STATE_KEY] = next_entry
    return consecutive_failures


def _apply_alert_results(
    state: dict[str, dict[str, object]],
    results: list[AlertRuleResult],
    *,
    current: datetime,
    sender: AlertSender,
    thresholds: dict[str, object],
) -> list[dict[str, object]]:
    sent: list[dict[str, object]] = []
    for result in results:
        entry = state.get(
            result.rule_id,
            {"state": "ok", "severity": _normalize_severity(result.severity)},
        )
        if not isinstance(entry, dict):
            entry = {"state": "ok", "severity": _normalize_severity(result.severity)}
        lifecycles = _normalize_lifecycles(entry)
        projected_severity = _normalize_severity(entry.get("severity"))
        if result.firing:
            effective_severity = _normalize_severity(result.severity)
            debounce = _debounce_window(thresholds, result.rule_id, effective_severity)
            outgoing_announced = False
            outgoing_since: object = None
            pending_since: object = None
            for severity in (PAGE_SEVERITY, NOTICE_SEVERITY):
                if severity == effective_severity:
                    continue
                lifecycle = lifecycles.get(severity)
                if lifecycle is None or lifecycle.get("state") != "firing":
                    continue
                announced = _entry_announced(lifecycle)
                if announced:
                    outgoing_announced = True
                    outgoing_since = lifecycle.get("since")
                    sent.append(
                        _invoke_sender(
                            result=result,
                            event_type="resolved",
                            text=_format_resolved(result, str(lifecycle.get("since") or "")),
                            severity=severity,
                            sender=sender,
                        )
                    )
                elif pending_since is None:
                    pending_since = lifecycle.get("since")
                lifecycles[severity] = _ok_lifecycle(lifecycle, result.detail)

            lifecycle = lifecycles.get(
                effective_severity,
                {
                    "state": "ok",
                    "since": None,
                    "last_notified": None,
                    "detail": result.detail,
                    "announced": False,
                },
            )
            lifecycle_was_firing = lifecycle.get("state") == "firing"
            previously_announced = lifecycle_was_firing and _entry_announced(lifecycle)
            if lifecycle_was_firing:
                since = lifecycle.get("since")
            elif outgoing_announced:
                since = _transition_since(
                    outgoing_since=outgoing_since,
                    current=current,
                    debounce=debounce,
                )
            elif pending_since is not None:
                since = pending_since
            else:
                since = current.isoformat()
            since_dt = _parse_dt(since)
            confirmed = since_dt is None or current - since_dt >= debounce
            last_notified = _parse_dt(lifecycle.get("last_notified"))
            should_notify = confirmed and (
                last_notified is None or current - last_notified >= COOLDOWN
            )
            delivery_succeeded = False
            if should_notify:
                text = _format_firing(result)
                receipt = _invoke_sender(
                    result=result,
                    event_type="firing",
                    text=text,
                    severity=effective_severity,
                    sender=sender,
                )
                sent.append(receipt)
                delivery_succeeded = _delivery_succeeded(receipt["send_result"])
            lifecycles[effective_severity] = {
                "state": "firing",
                "since": since,
                "last_notified": (
                    current.isoformat() if delivery_succeeded else lifecycle.get("last_notified")
                ),
                "detail": result.detail,
                "announced": previously_announced or delivery_succeeded,
            }
            state[result.rule_id] = _project_lifecycles(
                lifecycles,
                preferred_severity=effective_severity,
            )
        else:
            for severity in (PAGE_SEVERITY, NOTICE_SEVERITY):
                lifecycle = lifecycles.get(severity)
                if lifecycle is None or lifecycle.get("state") != "firing":
                    continue
                if _entry_announced(lifecycle):
                    sent.append(
                        _invoke_sender(
                            result=result,
                            event_type="resolved",
                            text=_format_resolved(result, str(lifecycle.get("since") or "")),
                            severity=severity,
                            sender=sender,
                        )
                    )
                lifecycles[severity] = _ok_lifecycle(lifecycle, result.detail)
            if projected_severity not in lifecycles:
                projected_severity = _normalize_severity(result.severity)
                lifecycles[projected_severity] = _ok_lifecycle({}, result.detail)
            elif lifecycles[projected_severity].get("state") == "ok":
                lifecycles[projected_severity] = {
                    **lifecycles[projected_severity],
                    "detail": result.detail,
                }
            state[result.rule_id] = _project_lifecycles(
                lifecycles,
                preferred_severity=projected_severity,
            )
    return sent


def run_alert_results_state_machine(
    results: list[AlertRuleResult],
    *,
    state_path: str | Path,
    now: datetime | None = None,
    send: AlertSender | None = None,
    thresholds: dict[str, object] | None = None,
) -> dict[str, object]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    path = Path(state_path)
    state = _load_state(path)
    sent = _apply_alert_results(
        state,
        results,
        current=current,
        sender=send or send_alert_message,
        thresholds=thresholds or {},
    )
    _write_state(path, state)
    return {
        "ruleset": [result.rule_id for result in results],
        "sent_count": len(sent),
        "sent": sent,
        "results": [asdict(result) for result in results],
        "state_path": str(path),
    }


def run_alert_state_machine(
    signals: AlertSignals,
    *,
    state_path: str | Path = DEFAULT_STATE_PATH,
    now: datetime | None = None,
    send: AlertSender | None = None,
    thresholds: dict[str, object] | None = None,
    healthz_probe: Callable[[str, float], bool] | None = None,
) -> dict[str, object]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    path = Path(state_path)
    state = _load_state(path)
    sender = send or send_alert_message
    active_thresholds = thresholds or ALERT_THRESHOLDS
    healthz_consecutive_failures = _record_healthz_probe(
        state,
        current=current,
        thresholds=active_thresholds,
        healthz_probe=healthz_probe,
    )
    signals = replace(signals, healthz_consecutive_failures=healthz_consecutive_failures)
    results = evaluate_rules(signals, thresholds=active_thresholds)
    sent = _apply_alert_results(
        state,
        results,
        current=current,
        sender=sender,
        thresholds=active_thresholds,
    )

    _write_state(path, state)
    return {
        "ruleset": list(RULESET),
        "sent_count": len(sent),
        "sent": sent,
        "results": [asdict(result) for result in results],
        "state_path": str(path),
    }


def send_alert_message(text: str, *, severity: AlertSeverity = PAGE_SEVERITY) -> dict[str, object]:
    effective_severity = _normalize_severity(severity)
    command = ["im-notify", text]
    if effective_severity == PAGE_SEVERITY:
        command.insert(1, "--alert")
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = f"im-notify failed: {type(exc).__name__}: {exc}"
        LOGGER.error("im-notify alert delivery failed: %s", reason)
        return {"skipped": True, "reason": reason}
    if completed.returncode != 0:
        reason = f"im-notify exited with status {completed.returncode}"
        LOGGER.error("im-notify alert delivery failed: %s; stderr=%s", reason, completed.stderr.strip())
        return {"skipped": True, "reason": reason}
    return {"skipped": False, "returncode": completed.returncode}


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
    a2 = _threshold_section(ALERT_THRESHOLDS, "a2")
    a3 = _threshold_section(ALERT_THRESHOLDS, "a3")
    a1_window_minutes = _int_threshold(a1, "window_minutes", 15)
    recent_since = current - timedelta(minutes=a1_window_minutes)
    stage_since = current - timedelta(minutes=_int_threshold(a2, "window_minutes", 15))
    access_since = current - timedelta(minutes=_int_threshold(a3, "window_minutes", 15))
    sample_size, upstream_rate, schema_rate = _recent_upstream_stats(db_path, recent_since)

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=pipeline_log_dir,
        access_log_paths=access_log_paths,
        now=current,
        stage_since=stage_since,
        access_since=access_since,
    )
    pipeline = metrics.get("pipeline", {})
    assert isinstance(pipeline, dict)
    stages = pipeline.get("stages", {})
    assert isinstance(stages, dict)
    stage_error_rate: dict[str, float] = {}
    stage_sample_count: dict[str, int] = {}
    stage_p95_latency_ms: dict[str, int] = {}
    for stage in ("prefilter", "scoring", "enrich"):
        row = stages.get(stage, {})
        if not isinstance(row, dict):
            continue
        stage_error_rate[stage] = float(row.get("error_rate") or 0.0)
        stage_sample_count[stage] = int(row.get("processed") or 0)
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
        fetch_failed_ratio=failed / attempted if attempted else 0.0,
        items_today=int(ingestion.get("items_today") or 0),
        minutes_elapsed_today=_minutes_elapsed_today(current),
        stage_sample_count=stage_sample_count,
        server_pv=int(users.get("pv") or 0),
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
