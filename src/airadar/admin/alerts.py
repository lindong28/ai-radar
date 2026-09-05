from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import plistlib
import shlex
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from .. import db
from ..egress import direct_subprocess_env, selector_httpx_client
from ..pipeline_lock import DEFAULT_PIPELINE_LOCK_PATH, pipeline_lock_is_held
from ..sources.x_state import validate_x_runtime_meta
from .calibration import SCHEMA_ERROR_RE, _is_upstream_error
from .cost_report import (
    _load_usage_rows,
    _pipeline_activity,
    _window_metering,
    evaluate_a6_cost,
)
from .metrics import SHANGHAI_TZ, _parse_dt, collect_metrics
from .thresholds import ALERT_THRESHOLDS

RULESET = ("A1", "A2", "A3", "A4", "A5", "A6", "A7")
DEFAULT_STATE_PATH = db.PROJECT_ROOT / "data" / "alert-state.json"
DEFAULT_EVENT_PATH = db.PROJECT_ROOT / "data" / "alert-events.jsonl"
COOLDOWN = timedelta(minutes=30)
RETENTION_DAYS = 14
MAX_LEDGER_BYTES = 64 * 1024 * 1024
LEDGER_LOCK_TIMEOUT_SECONDS = 1.0
LEDGER_LOCK_RETRY_SECONDS = 0.025
STATE_LOCK_TIMEOUT_SECONDS = 1.0
MINUTES_PER_DAY = 24 * 60
HEALTHZ_STATE_KEY = "healthz_probe"
EVALUATION_SEQUENCE_STATE_KEY = "evaluation_sequence"
# src/airadar/web/routes/health.py registers /healthz and create_app mounts it
# under the app API prefix /api/v1.
DEFAULT_HEALTHZ_URL = "http://127.0.0.1:8000/api/v1/healthz"
DEFAULT_HEALTHZ_TIMEOUT_SECONDS = 2.0
DEFAULT_SERVE_LAUNCH_AGENT_PATH = (
    Path.home() / "Library" / "LaunchAgents" / "live.aiplanet.ai-radar.serve.plist"
)
# Project label prefixed on every alert so a shared Feishu webhook (used by
# multiple projects) lets the reader tell which project an alert came from.
ALERT_SOURCE = "AI Radar"
AlertSeverity = Literal["page", "notice"]
EvaluationState = Literal["healthy", "degraded", "in_progress", "scope_limited"]
FiringBasis = Literal["observed"]
PAGE_SEVERITY: Literal["page"] = "page"
NOTICE_SEVERITY: Literal["notice"] = "notice"
ALERT_CHANNEL = "ALERT"
NOTIFICATION_CHANNEL = "NOTIFICATION"
INTERNAL_CHANNEL = "INTERNAL"
LOGGER = logging.getLogger(__name__)


class LedgerOversizeError(RuntimeError):
    pass


class LedgerLockTimeoutError(TimeoutError):
    pass


class LedgerNonRegularFileError(RuntimeError):
    pass


class AlertStateLockTimeoutError(TimeoutError):
    pass


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
    # A4 fetch dimension. `fetch_evaluated` is False when the log directory has
    # no complete fetch round (summary line followed by a `=== fetch OK|FAIL ===`
    # terminal line) or the newest complete round is older than
    # a4.fetch_stale_minutes — the fetch ratio is then unknown, not 0%.
    fetch_evaluated: bool = True
    fetch_stale_minutes: int | None = 0
    # Why the newest complete round is not usable: "expired" | "future_timestamp".
    fetch_stale_reason: str | None = None
    # None = count not carried by this signal set (synthetic signals); 0 = a real
    # complete round that tried no source, which the rule treats as unevaluated.
    fetch_attempted: int | None = None
    # HTTP status → failed source count / source ids, from `FAIL <src> ...
    # Client error '<status> ...'` lines of the newest complete round.
    failed_by_status: dict[int, int] = field(default_factory=dict)
    failed_sources_by_status: dict[int, list[str]] = field(default_factory=dict)
    # Newest-first summaries ({completed_at, attempted, failed_by_status}) of the
    # most recent complete rounds; the account-layer page resolves only after two
    # rounds with distinct completed_at are back under the ratio threshold.
    recent_complete_fetches: list[dict[str, object]] = field(default_factory=list)
    healthz_consecutive_failures: int = 0
    stage_sample_count: dict[str, int] = field(default_factory=dict)
    server_pv: int = 0
    hours_since_successful_interpretation: float | None = 0.0
    wechat_pending_count: int = 0
    wechat_frozen_count: int = 0
    oldest_wechat_pending_title: str | None = None
    a5_enabled: bool = True
    a6_evaluable: bool = False
    a6_measurement_in_progress: bool = False
    a6_current_cost_cny: float = 0.0
    a6_baseline_median_cny: float | None = None
    a6_threshold_cny: float | None = None
    a6_page_threshold_cny: float | None = None
    a6_baseline_days: int = 0
    a6_excluded_coverage_days: int = 0
    a6_top_driver: str | None = None
    a6_unpriced_calls: int = 0
    a6_pricing_freshness: str = "unknown"
    a6_metering_complete: bool = True
    a6_metering_failure_count: int = 0
    # A7: per-source silence. (source_id, display name, hours silent, threshold
    # used). Rolled up into one rule result — a shared upstream failure takes
    # every source down at once, and one page per source is exactly the fatigue
    # that gets an alert muted.
    silent_sources: list[tuple[str, str, float, float]] = field(default_factory=list)
    # Sources whose own history is too sparse to say whether silence is
    # anomalous. Reported rather than silently passed: an unevaluated source
    # looks identical to a healthy one in the rule's output otherwise.
    unevaluable_sources: int = 0
    evaluated_sources: int = 0
    # The half of `unevaluable_sources` whose silence has outrun the cadence of
    # its own newest few items — as opposed to sources that simply never
    # published enough to be characterised. Kept separate because leaving the
    # evaluated set means something different for each: failure for the first,
    # youth for the second, and only the first must block a ✅.
    faded_sources: list[tuple[str, str, float]] = field(default_factory=list)
    # X sources whose local item age crossed the cadence threshold, but whose
    # most recent successful timeline fetch reached a terminal checkpoint.
    # They remain visible in A7's evidence while staying out of the actionable
    # page set.
    quiet_x_sources: list[tuple[str, str, float, float]] = field(default_factory=list)


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
    firing_basis: FiringBasis | None = None
    evaluation_state: EvaluationState = "healthy"
    suppressed_by: str | None = None
    suppression_reason: str | None = None


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


A4_ACCOUNT_RUNBOOK_SECTION = "A4 账户层失败（401/402）的处置与恢复判定"
# Push bodies are one screen: list this many per-group lines / group names and
# summarise the rest as a count (same pattern as A7's silent-source list).
A4_ACCOUNT_DETAIL_GROUPS = 5
A4_ACCOUNT_IMPACT_GROUPS = 3
A4_ACCOUNT_STATUS_LABELS: dict[int, str] = {
    401: "401（凭证被拒）",
    402: "402 Payment Required（付费层/额度）",
}


def _a4_source_group(source_id: str) -> str:
    """Aggregate source ids by slug prefix for the message.

    `x_*` sources all share one X API account (one bearer token), so that group
    is a real account. Every other prefix is only a naming convention — e.g.
    `google_*` are unrelated public feeds — and must not be described as one.
    """
    prefix = source_id.split("_", 1)[0]
    return "X API" if prefix == "x" else prefix


def _a4_group_is_account(group: str) -> bool:
    return group == "X API"


def _a4_group_label(group: str) -> str:
    if _a4_group_is_account(group):
        return group
    return f"来源组 {group}（按 slug 前缀聚合，非账户身份）"


def _int_keyed_counts(value: object) -> dict[int, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[int, int] = {}
    for key, count in value.items():
        try:
            counts[int(key)] = int(count)
        except (TypeError, ValueError):
            continue
    return counts


def _account_status_codes(section: dict[str, Any]) -> list[int]:
    raw = section.get("account_status_codes", [401, 402])
    if not isinstance(raw, (list, tuple)):
        return [401, 402]
    return [int(code) for code in raw]


def _account_failed_count(failed_by_status: object, codes: list[int]) -> int:
    counts = _int_keyed_counts(failed_by_status)
    return sum(counts.get(code, 0) for code in codes)


def _a4_prefix_groups_action(status: int, groups: list[str]) -> str:
    """One action for all prefix groups hit by `status`.

    A prefix group is not an account: the remedy is to look at the sources
    themselves (credentials only exist where a source declares required_env), so
    the remedy is identical across groups and is written once.
    """
    remedy = "检查对应环境变量" if status == 401 else "检查凭证/付费层"
    names = "、".join(groups[:A4_ACCOUNT_DETAIL_GROUPS])
    if len(groups) > A4_ACCOUNT_DETAIL_GROUPS:
        names += f" 等 {len(groups)} 组"
    return f"核对来源组 {names} 各来源的配置与响应；来源有 required_env 时按 data/sources.toml {remedy}后重跑 fetch"


def _a4_account_action(status: int, group: str) -> str:
    if not _a4_group_is_account(group):
        return _a4_prefix_groups_action(status, [group])
    if status == 402:
        return f"为 {group} 的 API 账户充值/恢复付费层后重跑 fetch"
    if status == 401:
        return "更换/确认 X_BEARER_TOKEN 后重跑 fetch"
    return f"核查 {group} 的 API 账户状态（HTTP {status}）后重跑 fetch"


def _a4_healthy_complete_rounds(
    recent_complete_fetches: list[dict[str, object]],
    *,
    codes: list[int],
    ratio_threshold: float,
    max_gap_minutes: int = 90,
) -> tuple[int, int]:
    """Return (distinct complete rounds, leading rounds under the account ratio).

    Rounds sharing a `completed_at` are one round: the same log evaluated twice
    must not count as two recoveries. Rounds more than `max_gap_minutes` older
    than the newest usable round are not consulted either.
    """
    seen: set[str] = set()
    distinct = 0
    healthy_leading = 0
    streak_alive = True
    newest_completed_at: datetime | None = None
    for summary in recent_complete_fetches:
        if not isinstance(summary, dict):
            continue
        completed_at = summary.get("completed_at")
        key = completed_at.isoformat() if isinstance(completed_at, datetime) else str(completed_at)
        if key in seen:
            continue
        seen.add(key)
        attempted = int(str(summary.get("attempted", 0) or 0))
        if attempted <= 0:
            # A round that tried no source is no evidence either way: it must
            # not count as a recovery round (0/0 is not 0%), nor break a streak.
            continue
        if isinstance(completed_at, datetime):
            if newest_completed_at is None:
                newest_completed_at = completed_at
            elif (newest_completed_at - completed_at) > timedelta(minutes=max_gap_minutes):
                # After a long outage the previous complete round may predate it
                # by hours; a stale round is not evidence of a sustained recovery.
                break
        distinct += 1
        account_failed = _account_failed_count(summary.get("failed_by_status"), codes)
        ratio = account_failed / attempted
        if streak_alive and ratio <= ratio_threshold:
            healthy_leading += 1
        else:
            streak_alive = False
    return distinct, healthy_leading


def evaluate_rules(
    signals: AlertSignals,
    thresholds: dict[str, object] | None = None,
) -> list[AlertRuleResult]:
    active_thresholds = thresholds or ALERT_THRESHOLDS
    a1 = _threshold_section(active_thresholds, "a1")
    a2 = _threshold_section(active_thresholds, "a2")
    a3 = _threshold_section(active_thresholds, "a3")
    a4 = _threshold_section(active_thresholds, "a4")
    a5 = _threshold_section(active_thresholds, "a5")
    a6 = _threshold_section(active_thresholds, "a6")

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
        elapsed_text = (
            "尚无成功 pipeline 记录"
            if signals.minutes_since_successful_pipeline >= 10_000
            else f"最近成功 pipeline 已超过 {signals.minutes_since_successful_pipeline} 分钟"
        )
        stage_reasons.append(f"{elapsed_text}{skip_note}")

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
    a4_stale_limit = _int_threshold(a4, "fetch_stale_minutes", 90)
    a4_account_codes = _account_status_codes(a4)
    a4_resolve_rounds = max(1, _int_threshold(a4, "account_resolve_rounds", 2))
    # A complete round that attempted nothing carries no ratio: 0/0 is "unknown",
    # not 0%. Treat it like a missing round for the fetch dimension.
    a4_zero_attempted = signals.fetch_evaluated and signals.fetch_attempted == 0
    a4_fetch_evaluated = signals.fetch_evaluated and not a4_zero_attempted
    a4_attempted = signals.fetch_attempted or 0
    # Fetch dimension is three-state: an unevaluated round (no complete round,
    # or the newest complete round is stale) must not read as 0% failures.
    a4_fetch_failed = a4_fetch_evaluated and signals.fetch_failed_ratio > a4_fetch_ratio
    a4_items_low = signals.items_today < daily_inserted_floor_elapsed
    a4_account_failed = _account_failed_count(signals.failed_by_status, a4_account_codes)
    a4_account_ratio = a4_account_failed / a4_attempted if a4_attempted else 0.0
    a4_account_breached = a4_fetch_evaluated and a4_account_ratio > a4_fetch_ratio
    # Stateless hysteresis for the account-layer page: the newest two complete
    # rounds with distinct completed_at must both be under the ratio before the
    # rule reports healthy (which is what lets the page resolve).
    a4_distinct_rounds, a4_healthy_rounds = _a4_healthy_complete_rounds(
        signals.recent_complete_fetches,
        codes=a4_account_codes,
        ratio_threshold=a4_fetch_ratio,
        max_gap_minutes=a4_stale_limit,
    )
    a4_account_pending = (
        a4_fetch_evaluated
        and not a4_account_breached
        and a4_distinct_rounds > 0
        and a4_healthy_rounds < a4_resolve_rounds
    )
    if a4_healthy_rounds >= a4_distinct_rounds:
        # Every usable round is healthy but there are too few of them: that is
        # missing evidence, not a recovery from a breach we never saw.
        a4_pending_text = (
            f"仅有 {a4_distinct_rounds} 个可用完整 fetch 轮，账户层恢复证据不足"
            f"（需 {a4_resolve_rounds} 个 completed_at 不同的完整轮）"
        )
    else:
        a4_pending_text = (
            f"账户层失败已回落到 {a4_account_ratio:.1%}，"
            f"等待第 {a4_healthy_rounds + 1} 个完整 fetch 轮确认"
            f"（已确认 {a4_healthy_rounds}/{a4_resolve_rounds} 轮）后才 resolve"
        )
    a4_firing = a4_fetch_failed or a4_items_low or a4_account_breached
    a4_severity: AlertSeverity = (
        PAGE_SEVERITY
        if a4_items_low or a4_account_breached or not a4_fetch_failed
        else NOTICE_SEVERITY
    )
    a4_reasons: list[str] = []
    a4_account_lines: list[tuple[str, str]] = []
    a4_account_actions: list[str] = []
    a4_account_groups: list[str] = []
    if a4_account_breached:
        for status in a4_account_codes:
            status_sources = signals.failed_sources_by_status.get(status, [])
            status_count = _int_keyed_counts(signals.failed_by_status).get(status, 0)
            per_group: dict[str, int] = {}
            for source_id in status_sources:
                group = _a4_source_group(str(source_id))
                per_group[group] = per_group.get(group, 0) + 1
            if not per_group and status_count:
                per_group["未知来源组"] = status_count
            label = A4_ACCOUNT_STATUS_LABELS.get(status, f"{status}（账户层）")
            status_prefix_groups: list[str] = []
            for group, count in per_group.items():
                a4_account_lines.append(
                    (group, f"{_a4_group_label(group)}{'' if not _a4_group_is_account(group) else ' '}{count}/{a4_attempted} 源返回 {label}")
                )
                if _a4_group_is_account(group):
                    action = _a4_account_action(status, group)
                    if action not in a4_account_actions:
                        a4_account_actions.append(action)
                else:
                    status_prefix_groups.append(group)
                if group not in a4_account_groups:
                    a4_account_groups.append(group)
            if status_prefix_groups:
                # Same remedy for every prefix group: one sentence naming them all.
                a4_account_actions.append(_a4_prefix_groups_action(status, status_prefix_groups))
        # A shared-host outage can hit dozens of one-source groups at once; the
        # push body is one screen, so list the first few and count the rest
        # (by group: one group can contribute a 401 line and a 402 line).
        a4_shown_lines = a4_account_lines[:A4_ACCOUNT_DETAIL_GROUPS]
        a4_reasons.extend(text for _group, text in a4_shown_lines)
        a4_shown_groups = {group for group, _text in a4_shown_lines}
        a4_hidden_groups = len([group for group in a4_account_groups if group not in a4_shown_groups])
        if a4_hidden_groups > 0:
            a4_reasons.append(f"另有 {a4_hidden_groups} 组同此")
    if a4_fetch_failed:
        a4_reasons.append(f"最近 fetch 失败率 {signals.fetch_failed_ratio:.1%} > {a4_fetch_ratio:.1%}")
    if a4_items_low:
        a4_reasons.append(
            f"今日 items 增量 {signals.items_today} < 按日内进度 floor "
            f"{daily_inserted_floor_elapsed}/{daily_inserted_floor}"
        )
    if a4_fetch_evaluated:
        a4_fetch_state_text = ""
    elif a4_zero_attempted:
        a4_fetch_state_text = "最近完整 fetch 轮 attempted=0（没有任何来源被尝试），fetch 维度未评估"
    elif signals.fetch_stale_reason == "future_timestamp":
        a4_fetch_state_text = "最近完整 fetch 的时间戳在未来（时钟异常），fetch 维度未评估"
    elif signals.fetch_stale_minutes is None:
        a4_fetch_state_text = "无完整 fetch（日志里没有含汇总行与 fetch OK/FAIL 终态行的轮次），fetch 维度未评估"
    else:
        a4_fetch_state_text = (
            f"最近完整 fetch 已过期 {signals.fetch_stale_minutes} 分钟"
            f"（> {a4_stale_limit} 分钟），fetch 维度未评估"
        )
    if a4_account_breached:
        a4_items_suffix = "；同时今日 items 增量已低于日内 floor" if a4_items_low else ""
        a4_true_accounts = [group for group in a4_account_groups if _a4_group_is_account(group)]
        a4_prefix_groups = [group for group in a4_account_groups if not _a4_group_is_account(group)]

        def _group_list(groups: list[str], prefix: str = "") -> str:
            # Same shape as _a4_prefix_groups_action: the prefix is written once.
            text = prefix + "、".join(groups[:A4_ACCOUNT_IMPACT_GROUPS])
            if len(groups) > A4_ACCOUNT_IMPACT_GROUPS:
                text += f" 等 {len(groups)} 组"
            return text

        if a4_true_accounts:
            # A real account (X API) is a definite verdict; prefix groups riding
            # along must not water it down into "please check".
            a4_impact = (
                f"{_group_list(a4_true_accounts)} 账户层失败使 "
                f"{a4_account_failed}/{a4_attempted} 个来源无法抓取文章"
                + (
                    f"；另有{_group_list(a4_prefix_groups, '来源组 ')} 返回同类状态码（非账户身份，先核对配置与响应）"
                    if a4_prefix_groups
                    else ""
                )
                + a4_items_suffix
            )
            a4_urgency = "是——账户层失败不会自愈，需人工恢复凭证/付费层后重跑 fetch"
        else:
            a4_impact = (
                f"{_group_list(a4_prefix_groups, '来源组 ')} 返回账户层状态码（401/402），"
                f"{a4_account_failed}/{a4_attempted} 个来源无法抓取文章"
                f"（前缀组非账户身份，先核对配置与响应）{a4_items_suffix}"
            )
            a4_urgency = "是——需人工核对来源配置与响应；若确为凭证/付费问题则不会自愈"
    elif a4_items_low:
        a4_impact = "文章更新可能停滞"
        a4_urgency = "是——需立即核查"
    elif a4_fetch_failed:
        # Do not name a cause here. This line renders above the action, so a
        # benign attribution ("源站波动") is what the reader acts on — and it
        # silently contradicts the action's egress-outage branch below.
        a4_impact = "今日累计入库仍在 floor 之上；失败集中在哪些源、什么原因均未判定"
        a4_urgency = "否——今日 items 未跌破 floor（该指标看不到单源或部分源死亡），可按处置方向定位失败源"
    else:
        a4_impact = ""
        a4_urgency = ""

    a5_hours = _float_threshold(a5, "no_success_hours", 4.0)
    a5_stalled = (
        signals.hours_since_successful_interpretation is None
        or signals.hours_since_successful_interpretation >= a5_hours
    )
    a5_firing = signals.a5_enabled and a5_stalled and signals.wechat_pending_count > 0
    a5_degraded = bool(
        signals.a5_enabled
        and a5_stalled
        and not signals.wechat_pending_count
    )
    a5_elapsed_text = (
        "尚无成功记录"
        if signals.hours_since_successful_interpretation is None
        else f"已 {signals.hours_since_successful_interpretation:.1f} 小时无成功解读"
    )
    a7_firing = bool(signals.silent_sources)
    a7_named = sorted(signals.silent_sources, key=lambda row: -row[2])
    a7_faded = sorted(signals.faded_sources, key=lambda row: -row[2])
    a7_quiet_x = sorted(signals.quiet_x_sources, key=lambda row: -row[2])
    # The unmonitored remainder matters most while A7 is firing: that is when
    # someone acts on it, and a named-source list reads as the whole picture.
    # Disclosing it only in the non-firing branch told the operator the scope
    # was limited exactly when nothing was wrong, and stayed silent otherwise.
    a7_scope_note = (
        f"；另有 {signals.unevaluable_sources} 个源历史过稀无法评估，不在本次判定内"
        if signals.unevaluable_sources
        else ""
    )
    a7_quiet_detail = (
        "；".join(
            f"{name} 上游未更新，最近一次 X 读取已追平（{age_minutes:.0f} 分钟前）"
            for _sid, name, _hours, age_minutes in a7_quiet_x[:5]
        )
        + (f"；另有 {len(a7_quiet_x) - 5} 个 X 来源同此" if len(a7_quiet_x) > 5 else "")
    )
    a7_quiet_note = f"；无需处置：{a7_quiet_detail}" if a7_quiet_detail else ""
    a7_detail = (
        "；".join(
            f"{name} 静默 {hours:.1f}h（阈值 {limit:.0f}h）"
            for _sid, name, hours, limit in a7_named[:5]
        )
        + (f"；另有 {len(a7_named) - 5} 个源" if len(a7_named) > 5 else "")
        + a7_quiet_note
        + a7_scope_note
        if a7_firing
        else (
            # A faded source leaving the evaluated set empties `silent_sources`
            # exactly like a recovery does. Say which one happened, and name
            # them: "no source is past its threshold" is true in both cases and
            # so distinguishes nothing.
            "；".join(
                f"{name} 已静默 {hours:.1f}h 且历史已稀疏到无法评估"
                for _sid, name, hours in a7_faded[:5]
            )
            + (f"；另有 {len(a7_faded) - 5} 个源同此" if len(a7_faded) > 5 else "")
            + a7_quiet_note
            if a7_faded
            else (
                a7_quiet_detail
                + a7_scope_note
                if a7_quiet_x
                else f"{signals.evaluated_sources} 个源均在各自节奏内"
                + (
                    f"，{signals.unevaluable_sources} 个源历史过稀无法评估"
                    if signals.unevaluable_sources
                    else ""
                )
            )
        )
    )

    a6_floor = _float_threshold(a6, "daily_floor_cny", 20.0)
    a6_multiplier = _float_threshold(a6, "spike_multiplier", 3.0)
    a6_lower_bound_evaluable = bool(
        signals.a6_measurement_in_progress
        and signals.a6_threshold_cny is not None
    )
    a6_firing = (
        (signals.a6_evaluable or a6_lower_bound_evaluable)
        and signals.a6_threshold_cny is not None
        and signals.a6_current_cost_cny > signals.a6_threshold_cny
    )
    a6_notes: list[str] = []
    if signals.a6_unpriced_calls:
        a6_notes.append(f"另有 {signals.a6_unpriced_calls} 次已记录但未定价调用未计入金额")
    if signals.a6_pricing_freshness in {"stale", "due-review"}:
        a6_notes.append(f"价格状态 {signals.a6_pricing_freshness}，金额需复核")
    if signals.a6_metering_failure_count:
        a6_notes.append(f"至少 {signals.a6_metering_failure_count} 次计量写入失败，金额可能低估")
    if signals.a6_measurement_in_progress:
        a6_notes.append("pipeline 正在运行；当前金额为未封口下界，只用于越阈值的正向判断")
    elif not signals.a6_metering_complete:
        a6_notes.append("近 24 小时计量日志不完整")
    a6_degraded = not signals.a6_evaluable and not signals.a6_measurement_in_progress
    a6_severity: AlertSeverity = (
        PAGE_SEVERITY
        if signals.a6_page_threshold_cny is not None
        and signals.a6_current_cost_cny > signals.a6_page_threshold_cny
        else NOTICE_SEVERITY
    )

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
                "；".join(a4_reasons + ([a4_fetch_state_text] if a4_fetch_state_text else []))
                if a4_reasons
                else (
                    f"{a4_fetch_state_text}；今日 items 增量 {signals.items_today}，"
                    f"按日内进度 floor {daily_inserted_floor_elapsed}/{daily_inserted_floor}"
                    if a4_fetch_state_text
                    else (
                        f"{a4_pending_text}；"
                        f"今日 items 增量 {signals.items_today}，按日内进度 floor "
                        f"{daily_inserted_floor_elapsed}/{daily_inserted_floor}"
                        if a4_account_pending
                        else (
                            f"最近 fetch 失败率 {signals.fetch_failed_ratio:.1%}，"
                            f"今日 items 增量 {signals.items_today}，按日内进度 floor "
                            f"{daily_inserted_floor_elapsed}/{daily_inserted_floor}，均在阈值内"
                        )
                    )
                )
            ),
            action=(
                "；".join(a4_account_actions)
                + f"。详见 docs/operations/monitoring-alerting.md 的「{A4_ACCOUNT_RUNBOOK_SECTION}」。"
                if a4_account_breached
                else (
                    "先确认 pipeline 是否仍在跑（A2 心跳），再看 logs/pipeline-*.log 最新一轮"
                    "是否跑完 fetch；preflight FAIL 的轮不产生完整 fetch，按 runbook 检查 selector。"
                    + (
                        "items 亦已跌破 floor：若 pipeline 与 preflight 正常，再按最新完整轮的 FAIL 行分流处置。"
                        if a4_items_low
                        else ""
                    )
                    + "详见 docs/operations/monitoring-alerting.md 的「出网 selector 的 preflight 与实际 route」。"
                    if not a4_fetch_evaluated
                    else (
                        "先按 error 分组读 logs/pipeline-*.log 最新一轮的 FAIL 行。"
                        "整批 ConnectError 多为出网链路：先看 === egress preflight FAIL/OK ===；"
                        "FAIL 时按 runbook 检查 selector，OK 但请求仍失败时按 hostname 查 route audit。"
                        "X 源走官方 api.x.com（另查 bearer token 与配额），微信源走 Mp2RSS。"
                        "详见 docs/operations/monitoring-alerting.md 的「出网 selector 的 preflight 与实际 route」。"
                    )
                )
            ),
            values={
                "fetch_failed_ratio": signals.fetch_failed_ratio,
                "fetch_evaluated": a4_fetch_evaluated,
                "fetch_stale_minutes": signals.fetch_stale_minutes,
                "fetch_attempted": signals.fetch_attempted,
                "failed_by_status": dict(_int_keyed_counts(signals.failed_by_status)),
                "account_failed_ratio": a4_account_ratio,
                "account_healthy_rounds": a4_healthy_rounds,
                "account_resolve_rounds": a4_resolve_rounds,
                "items_today": signals.items_today,
                "minutes_elapsed_today": signals.minutes_elapsed_today,
                "daily_inserted_floor": daily_inserted_floor,
                "daily_inserted_floor_elapsed": daily_inserted_floor_elapsed,
            },
            severity=a4_severity,
            impact=a4_impact,
            urgency=a4_urgency,
            evaluation_state=(
                "in_progress"
                if not a4_firing and (not a4_fetch_evaluated or a4_account_pending)
                else "healthy"
            ),
        ),
        AlertRuleResult(
            rule_id="A5",
            title="微信解读产出停滞",
            firing=a5_firing,
            detail=(
                "解读功能未启用，本规则不评估"
                if not signals.a5_enabled
                else (
                f"{a5_elapsed_text}；"
                f"等待文章 {signals.wechat_pending_count} 篇；"
                f"最老待处理：{signals.oldest_wechat_pending_title or '未知标题'}；"
                f"重试耗尽冻结 {signals.wechat_frozen_count} 篇"
                if a5_firing
                else (
                    (
                        "尚无成功记录；"
                        if signals.hours_since_successful_interpretation is None
                        else f"距成功解读 {signals.hours_since_successful_interpretation:.1f} 小时；"
                    )
                    + f"满足年龄与重试资格的等待文章 {signals.wechat_pending_count} 篇；"
                    + f"重试耗尽冻结 {signals.wechat_frozen_count} 篇"
                )
                )
            ),
            action=(
                "先查近 4 小时 pipeline 与 interpret 日志中的成功/错误；再核对 DeepSeek/ARK 余额和配额。"
                "data/ark-breaker.json 仅在 opened_at 仍处于 2 小时 cooldown 内时可作当前故障证据；"
                "若仍在 cooldown，402 表示余额不足，429 AccountQuotaExceeded 表示方舟配额。"
            ),
            values={
                "hours_since_successful_interpretation": signals.hours_since_successful_interpretation,
                "pending_count": signals.wechat_pending_count,
                "frozen_count": signals.wechat_frozen_count,
                "oldest_pending_title": signals.oldest_wechat_pending_title,
                "no_success_hours": a5_hours,
            },
            impact="微信文章解读停止更新，待处理或冻结文章不会产出",
            urgency="是——有合格积压时立即核查；无合格积压时先恢复可评估性",
            evaluation_state="degraded" if a5_degraded else "healthy",
        ),
        AlertRuleResult(
            rule_id="A6",
            title="已记录 LLM 调用近 24 小时成本突变",
            firing=a6_firing,
            detail=(
                f"近 24 小时 cache 中性目录价估算 ¥{signals.a6_current_cost_cny:.2f}；"
                + (
                    f"14 UTC 日中 {signals.a6_baseline_days} 个可比日中位数 "
                    f"¥{float(signals.a6_baseline_median_cny):.2f}，阈值 ¥{float(signals.a6_threshold_cny):.2f}"
                    if (signals.a6_evaluable or a6_lower_bound_evaluable)
                    and signals.a6_baseline_median_cny is not None
                    and signals.a6_threshold_cny is not None
                    else (
                        (
                            f"已有 {signals.a6_baseline_days} 个基线日，pipeline 运行中，暂缓评估"
                            if signals.a6_measurement_in_progress
                            else f"已有 {signals.a6_baseline_days} 个基线日，但当前不可评估"
                        )
                        if signals.a6_baseline_days >= 3
                        else f"仅 {signals.a6_baseline_days} 个基线日（少于 3），当前不可评估"
                    )
                )
                + (f"；Top 驱动 {signals.a6_top_driver}" if signals.a6_top_driver else "")
                + (f"；{'；'.join(a6_notes)}" if a6_notes else "")
                + "。两侧均按当前费率和 cache 未命中重算；金额含 nominal 目录价估算，并非账单实付；"
                "金额/次数只统计 llm_usage 记录行，不是付费调用总额。\n"
                "  未写入 llm_usage 的付费调用均不在内，因此该数只能作为下界"
                "（例如失败链路或未接入计量的调用点）。"
            ),
            action=(
                "按本消息 Top 驱动（A6 cache 中性已知成本聚合）核查对应调用；"
                "未定价调用另见 /admin/usage"
            ),
            values={
                "current_cost_cny": signals.a6_current_cost_cny,
                "baseline_median_cny": signals.a6_baseline_median_cny,
                "threshold_cny": signals.a6_threshold_cny,
                "page_threshold_cny": signals.a6_page_threshold_cny,
                "baseline_days": signals.a6_baseline_days,
                "daily_floor_cny": a6_floor,
                "spike_multiplier": a6_multiplier,
                "cohort": "已记录且评估时可定价的实价与目录价调用，cache 统一按未命中",
            },
            severity=a6_severity,
            impact="近 24 小时已记录 LLM 调用的目录价估算显著高于近期已记录基线",
            urgency=(
                "是——超过高档阈值，立即核查"
                if a6_severity == PAGE_SEVERITY and a6_firing
                else "否——先核查 Top 驱动与已记录调用量，确认后再调整资源"
            ),
            evaluation_state=(
                "in_progress"
                if signals.a6_measurement_in_progress
                else ("degraded" if a6_degraded else "scope_limited")
            ),
        ),
        AlertRuleResult(
            rule_id="A7",
            title="来源静默",
            firing=a7_firing,
            detail=a7_detail,
            action=(
                "按源逐个核对：先看 logs/pipeline-*.log 里该来源的 OK/FAIL 行。"
                "整批同时静默多为出网链路：先看 === egress preflight FAIL/OK ===；"
                "FAIL 时按 runbook 检查 selector，OK 但请求仍失败时按 hostname 查 route audit。"
                "单源静默则查该源站点或其上游订阅服务。"
                "详见 docs/operations/monitoring-alerting.md 的「出网 selector 的 preflight 与实际 route」。"
            ),
            values={
                "silent_sources": [
                    {"source_id": sid, "name": name, "hours": hours, "threshold_hours": limit}
                    for sid, name, hours, limit in a7_named
                ],
                "silent_count": len(a7_named),
                "evaluated_sources": signals.evaluated_sources,
                "unevaluable_sources": signals.unevaluable_sources,
                "faded_sources": [
                    {"source_id": sid, "name": name, "hours": hours}
                    for sid, name, hours in a7_faded
                ],
                "quiet_x_sources": [
                    {
                        "source_id": sid,
                        "name": name,
                        "hours": hours,
                        "receipt_age_minutes": receipt_age_minutes,
                    }
                    for sid, name, hours, receipt_age_minutes in a7_quiet_x
                ],
            },
            severity=PAGE_SEVERITY,
            impact=(
                f"{len(a7_named)} 个来源已停止产出，站点上这些来源的内容正在变旧"
                if a7_firing
                else ""
            ),
            urgency="是——需立即核查" if a7_firing else "",
            # A firing episode that ends because its sources faded out has not
            # resolved — nothing recovered, the rule just lost the ability to
            # see them. `degraded` routes that to the 🟡 "转为不可评估" close
            # instead of a ✅, which would otherwise report a source as healthy
            # at the moment it is most thoroughly dead.
            evaluation_state=(
                "degraded"
                if a7_faded and not a7_firing
                else "scope_limited"
                if signals.unevaluable_sources and not a7_firing
                else "healthy"
            ),
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


# `scope_limited` was minted for one limitation only — A6's recorded-row cost
# scope — so its resolve copy was written inline. A7 then reused the state for
# an unrelated limitation (part of the source set is unmonitored), and inherited
# A6's sentence: every A7 resolve announced that a cost had come back down.
# Keying the copy by rule keeps the state meaning "scope is limited" while each
# rule says *which* scope, and makes a new rule land on the neutral fallback
# instead of silently borrowing another rule's wording.
# (headline, caveat, evidence label). The caveat trails the `since` suffix so
# the two never nest into stacked parentheses.
_SCOPE_LIMITED_RESOLVED_COPY: dict[str, tuple[str, str, str]] = {
    "A6": ("记录行金额已回落", "", "记录行证据"),
    "A7": ("已恢复", "；部分来源不在评估范围内", "恢复证据"),
}
_SCOPE_LIMITED_RESOLVED_FALLBACK = ("已恢复", "；评估范围受限", "恢复证据")
_RESOLVED_EVIDENCE_POINTERS = {
    "A4": (
        "无需立即处置；恢复结论只覆盖当前抓取，断流期间的缺口按 runbook 判是否补抓。"
        "复核入口：logs/pipeline-*.log 最近完整轮的 FAIL 行与 /api/v1/admin/metrics 的 ingestion.latest_fetch；"
        f"runbook：401/402 账户层见 docs/operations/monitoring-alerting.md 的「{A4_ACCOUNT_RUNBOOK_SECTION}」，"
        "整批失败/出网见「出网 selector 的 preflight 与实际 route」。"
    ),
    "A7": (
        "复核入口：logs/pipeline-*.log；runbook：docs/operations/monitoring-alerting.md "
        "的「出网 selector 的 preflight 与实际 route」。"
    )
}


def _format_resolved(result: AlertRuleResult, since: str | None) -> str:
    suffix = f"（since {since}）" if since else ""
    evidence_pointer = _RESOLVED_EVIDENCE_POINTERS.get(result.rule_id)
    pointer_suffix = f"\n{evidence_pointer}" if evidence_pointer else ""
    if result.evaluation_state == "degraded":
        return (
            f"【{ALERT_SOURCE}】🟡 {result.rule_id} {result.title} 转为不可评估{suffix}\n"
            f"当前证据：{result.detail}\n处置方向：{result.action}"
        )
    if result.evaluation_state == "scope_limited":
        headline, caveat, evidence_label = _SCOPE_LIMITED_RESOLVED_COPY.get(
            result.rule_id, _SCOPE_LIMITED_RESOLVED_FALLBACK
        )
        return (
            f"【{ALERT_SOURCE}】✅ {result.rule_id} {result.title}：{headline}{suffix}{caveat}\n"
            f"{evidence_label}：{result.detail}{pointer_suffix}"
        )
    return (
        f"【{ALERT_SOURCE}】✅ {result.rule_id} {result.title} 已恢复{suffix}\n"
        f"恢复证据：{result.detail}{pointer_suffix}"
    )


def _correlate_alert_results(
    results: list[AlertRuleResult], *, heartbeat_fresh: bool
) -> list[AlertRuleResult]:
    by_id = {result.rule_id: result for result in results}
    if not (by_id.get("A5") and by_id["A5"].firing):
        return results
    carrier = "A5" if heartbeat_fresh else "A2"
    carrier_result = by_id.get(carrier)
    if carrier_result is None or not carrier_result.firing:
        return results
    suppressed_ids = (
        {"A1", "A2"}
        if heartbeat_fresh
        else {"A5"}
    )
    correlated = []
    related = [rule_id for rule_id in sorted(suppressed_ids) if by_id.get(rule_id) and by_id[rule_id].firing]
    for result in results:
        if result.rule_id == carrier and related:
            correlated.append(
                replace(result, detail=f"{result.detail}；已合并关联信号 {','.join(related)}")
            )
        elif result.rule_id in related:
            correlated.append(
                replace(
                    result,
                    suppressed_by=carrier,
                    suppression_reason=(
                        "pipeline 心跳新鲜，同一 provider/阶段事故由 A5 合并通知"
                        if heartbeat_fresh
                        else "pipeline 心跳已过期，宿主/流水线事故由 A2 合并通知"
                    ),
                )
            )
        else:
            correlated.append(result)
    return correlated


def _normalize_severity(value: object) -> AlertSeverity:
    return NOTICE_SEVERITY if value == NOTICE_SEVERITY else PAGE_SEVERITY


def _normalize_firing_basis(value: object) -> FiringBasis | None:
    if value == "observed":
        return "observed"
    return None


def _severity_channel(severity: AlertSeverity) -> str:
    return NOTIFICATION_CHANNEL if severity == NOTICE_SEVERITY else ALERT_CHANNEL


def _delivery_succeeded(send_result: object) -> bool:
    return isinstance(send_result, Mapping) and send_result.get("skipped") is False


def _snapshot_delivery_succeeded(send_result: object) -> bool:
    try:
        return _delivery_succeeded(send_result)
    except BaseException:  # A malformed sender result must not break state continuity.
        return False


def _entry_announced(entry: dict[str, object]) -> bool:
    if isinstance(entry.get("announced"), bool):
        return entry["announced"] is True
    last_notified = _parse_dt(entry.get("last_notified"))
    since = _parse_dt(entry.get("since"))
    return last_notified is not None and (since is None or last_notified >= since)


def _normalized_lifecycle(entry: dict[str, object]) -> dict[str, object]:
    state = "firing" if entry.get("state") == "firing" else "ok"
    raw_sequence = entry.get("notification_sequence")
    notification_sequence = (
        raw_sequence if isinstance(raw_sequence, int) and raw_sequence >= 0 else 0
    )
    raw_pending = entry.get("pending_notification")
    pending_notification: dict[str, object] | None = None
    if isinstance(raw_pending, dict):
        nonce = raw_pending.get("nonce")
        event_type = raw_pending.get("event_type")
        if (
            isinstance(nonce, int)
            and nonce > 0
            and event_type in {"firing", "resolved"}
        ):
            pending_notification = {
                "nonce": nonce,
                "event_type": event_type,
                "episode_since": raw_pending.get("episode_since"),
            }
    normalized = {
        "state": state,
        "since": entry.get("since") if state == "firing" else None,
        "last_notified": entry.get("last_notified"),
        "detail": entry.get("detail"),
        "announced": state == "firing" and _entry_announced(entry),
        "notification_sequence": notification_sequence,
        "pending_notification": pending_notification,
        "evaluation_state": (
            entry.get("evaluation_state")
            if entry.get("evaluation_state")
            in {"degraded", "in_progress", "scope_limited"}
            else "healthy"
        ),
    }
    if (firing_basis := _normalize_firing_basis(entry.get("firing_basis"))) is not None:
        normalized["firing_basis"] = firing_basis
    return normalized


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


def _ok_lifecycle(
    lifecycle: dict[str, object],
    detail: str,
    evaluation_state: EvaluationState = "healthy",
) -> dict[str, object]:
    return {
        "state": "ok",
        "since": None,
        "last_notified": lifecycle.get("last_notified"),
        "detail": detail,
        "announced": False,
        "notification_sequence": lifecycle.get("notification_sequence", 0),
        "pending_notification": None,
        "evaluation_state": evaluation_state,
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
    projection = {
        "state": "firing" if active_severity is not None else "ok",
        "since": projected.get("since") if active_severity is not None else None,
        "last_notified": projected.get("last_notified"),
        "detail": projected.get("detail"),
        "severity": projected_severity,
        "announced": active_severity is not None and _entry_announced(projected),
        "lifecycles": lifecycles,
        "evaluation_state": projected.get("evaluation_state", "healthy"),
    }
    if active_severity is not None and (
        firing_basis := _normalize_firing_basis(projected.get("firing_basis"))
    ) is not None:
        projection["firing_basis"] = firing_basis
    return projection


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
    episode_since: object,
    notification_nonce: int,
    transport_dedup: bool,
) -> dict[str, object]:
    send_result: object
    if transport_dedup:
        dedup_key = (
            f"ai-radar:{result.rule_id}:{severity}:{event_type}:{notification_nonce}"
        )
        dedup_text = "\n".join(
            (
                result.rule_id,
                severity,
                event_type,
                str(notification_nonce),
                str(episode_since or ""),
            )
        )
        send_result = send_alert_message(
            text,
            severity=severity,
            dedup_key=dedup_key,
            dedup_text=dedup_text,
        )
    else:
        send_result = sender(text, severity=severity)
    delivered = _snapshot_delivery_succeeded(send_result)
    return {
        "rule_id": result.rule_id,
        "type": event_type,
        "effective_severity": severity,
        "channel": _severity_channel(severity),
        "text": text,
        "send_result": send_result,
        "delivered": delivered,
        "notification_nonce": notification_nonce,
        "episode_since": episode_since,
    }


def _ledger_lock_path(event_path: Path) -> Path:
    return event_path.with_suffix(".lock")


def _validate_alert_paths(*, state_path: Path, event_path: Path) -> None:
    paths = (state_path, event_path, _ledger_lock_path(event_path))
    resolved = tuple(path.resolve(strict=False) for path in paths)
    aliased = len(set(resolved)) != len(resolved)
    if not aliased:
        for index, left in enumerate(paths):
            for right in paths[index + 1 :]:
                try:
                    if os.path.samefile(left, right):
                        aliased = True
                        break
                except FileNotFoundError:
                    continue
            if aliased:
                break
    if aliased:
        raise ValueError("state_path, event_path, and ledger lock path must be distinct")


def _require_regular_file_if_present(path: Path, *, label: str) -> os.stat_result | None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        raise LedgerNonRegularFileError(f"notification ledger {label} is not a regular file: {path}")
    return file_stat


def _check_ledger_size(event_path: Path) -> None:
    file_stat = _require_regular_file_if_present(event_path, label="path")
    if file_stat is None:
        return
    size = file_stat.st_size
    if size > MAX_LEDGER_BYTES:
        raise LedgerOversizeError(
            f"notification ledger is {size} bytes; limit is {MAX_LEDGER_BYTES} bytes"
        )


def _acquire_ledger_lock(lock_file: object, *, event_path: Path) -> None:
    deadline = time.monotonic() + LEDGER_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            return
        except BlockingIOError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LedgerLockTimeoutError(
                    f"timed out locking notification ledger after "
                    f"{LEDGER_LOCK_TIMEOUT_SECONDS:.1f}s: {event_path}"
                ) from exc
            time.sleep(min(LEDGER_LOCK_RETRY_SECONDS, remaining))


def _read_ledger_rows(event_path: Path) -> list[dict[str, object]]:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor: int | None = os.open(event_path, flags)
    except FileNotFoundError:
        return []
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise LedgerNonRegularFileError(
                f"notification ledger path is not a regular file: {event_path}"
            )
        if file_stat.st_size > MAX_LEDGER_BYTES:
            raise LedgerOversizeError(
                f"notification ledger is {file_stat.st_size} bytes; "
                f"limit is {MAX_LEDGER_BYTES} bytes"
            )
        with os.fdopen(descriptor, encoding="utf-8") as ledger_file:
            descriptor = None
            ledger_text = ledger_file.read()
    finally:
        if descriptor is not None:
            os.close(descriptor)
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(ledger_text.splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"notification ledger line {line_number} is not an object")
        rows.append(row)
    return rows


def _write_ledger_rows(event_path: Path, rows: list[dict[str, object]]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=event_path.parent,
            prefix=f".{event_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            for row in rows:
                temporary.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temporary_path, event_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _record_event_rows(
    event_path: str | Path,
    *,
    current: datetime,
    new_rows: list[dict[str, object]],
) -> None:
    try:
        if not new_rows:
            return
        path = Path(event_path)
        _check_ledger_size(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = _ledger_lock_path(path)
        _require_regular_file_if_present(lock_path, label="lock path")
        lock_flags = os.O_RDWR | os.O_CREAT | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            lock_flags |= os.O_NOFOLLOW
        lock_descriptor: int | None = os.open(lock_path, lock_flags, 0o666)
        try:
            lock_stat = os.fstat(lock_descriptor)
            if not stat.S_ISREG(lock_stat.st_mode):
                raise LedgerNonRegularFileError(
                    f"notification ledger lock path is not a regular file: {lock_path}"
                )
            lock_file = os.fdopen(lock_descriptor, mode="r+")
            lock_descriptor = None
        finally:
            if lock_descriptor is not None:
                os.close(lock_descriptor)
        with lock_file:
            _acquire_ledger_lock(lock_file, event_path=path)
            try:
                _check_ledger_size(path)
                rows = _read_ledger_rows(path)
                rows.extend(new_rows)
                cutoff = current - timedelta(days=RETENTION_DAYS)
                retained = [
                    row
                    for row in rows
                    if (timestamp := _parse_dt(row.get("ts"))) is None or timestamp >= cutoff
                ]
                _write_ledger_rows(path, retained)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except BaseException as exc:  # The ledger must never block alert state persistence.
        try:
            LOGGER.error(
                "notification ledger write failed event_path=%s exception=%s: %s",
                event_path,
                type(exc).__name__,
                exc,
            )
        except BaseException:
            pass


def _record_notification_events(
    event_path: str | Path,
    *,
    current: datetime,
    delivered: list[tuple[dict[str, object], AlertRuleResult]],
) -> None:
    _record_event_rows(
        event_path,
        current=current,
        new_rows=[
            {
                "ts": current.isoformat(),
                "rule_id": receipt["rule_id"],
                "severity": receipt["effective_severity"],
                "type": receipt["type"],
                "detail": result.detail,
                "values": result.values,
                "channel": receipt["channel"],
                "episode_since": receipt.get("episode_since"),
                "notification_nonce": receipt.get("notification_nonce"),
            }
            for receipt, result in delivered
            if receipt.get("delivered") is True
        ],
    )


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
        if rule_id in {HEALTHZ_STATE_KEY, EVALUATION_SEQUENCE_STATE_KEY} or not isinstance(
            entry, dict
        ):
            continue
        lifecycles = _normalize_lifecycles(entry)
        projected = _project_lifecycles(
            lifecycles,
            preferred_severity=_normalize_severity(entry.get("severity")),
        )
        if _parse_dt(entry.get("last_evaluated_at")) is not None:
            projected["last_evaluated_at"] = entry["last_evaluated_at"]
        if (
            isinstance(entry.get("last_evaluation_sequence"), int)
            and int(entry["last_evaluation_sequence"]) > 0
        ):
            projected["last_evaluation_sequence"] = entry["last_evaluation_sequence"]
        state[rule_id] = projected
    return state


def _claim_evaluation_sequence(
    state: dict[str, dict[str, object]],
    requested: int | None = None,
) -> int:
    metadata = state.get(EVALUATION_SEQUENCE_STATE_KEY, {})
    raw_last_reserved = metadata.get("last_reserved") if isinstance(metadata, dict) else None
    last_reserved = (
        raw_last_reserved
        if isinstance(raw_last_reserved, int) and raw_last_reserved >= 0
        else 0
    )
    if requested is None:
        sequence = last_reserved + 1
    else:
        if requested <= 0:
            raise ValueError("evaluation_sequence must be positive")
        sequence = requested
    state[EVALUATION_SEQUENCE_STATE_KEY] = {
        "last_reserved": max(last_reserved, sequence),
    }
    return sequence


def reserve_alert_evaluation_sequence(*, state_path: str | Path) -> int:
    path = Path(state_path)
    with _alert_state_lock(path):
        state = _load_state(path)
        sequence = _claim_evaluation_sequence(state)
        _write_state(path, state)
    return sequence


def _write_state(path: Path, state: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(state, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def _alert_state_lock(state_path: Path) -> Iterator[None]:
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o666)
    try:
        with os.fdopen(descriptor, mode="r+") as lock_file:
            descriptor = -1
            if not stat.S_ISREG(os.fstat(lock_file.fileno()).st_mode):
                raise AlertStateLockTimeoutError(
                    f"alert state lock is not a regular file: {lock_path}"
                )
            deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AlertStateLockTimeoutError(
                            f"timed out locking alert state after "
                            f"{STATE_LOCK_TIMEOUT_SECONDS:.1f}s: {state_path}"
                        ) from exc
                    time.sleep(min(LEDGER_LOCK_RETRY_SECONDS, remaining))
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _probe_healthz(url: str, timeout: float) -> bool:
    try:
        with selector_httpx_client(
            callsite_id="admin.alerts.healthz",
            request_url=url,
            timeout=timeout,
        ) as client:
            response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    return payload.get("success") is True and isinstance(data, dict) and data.get("ok") is True


def _serve_port_from_tokens(tokens: list[str]) -> int | None:
    try:
        serve_index = tokens.index("serve")
    except ValueError:
        return None
    for index, token in enumerate(tokens[serve_index + 1 :], start=serve_index + 1):
        raw_port: str | None = None
        if token == "--port" and index + 1 < len(tokens):
            raw_port = tokens[index + 1]
        elif token.startswith("--port="):
            raw_port = token.removeprefix("--port=")
        if raw_port is None:
            continue
        try:
            port = int(raw_port)
        except ValueError:
            return None
        return port if 1 <= port <= 65535 else None
    return None


def _serve_port_from_plist(path: str | Path) -> int | None:
    try:
        with Path(path).open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return None
    arguments = payload.get("ProgramArguments") if isinstance(payload, dict) else None
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        return None
    token_sets = [arguments]
    for argument in arguments:
        try:
            token_sets.append(shlex.split(argument))
        except ValueError:
            continue
    for tokens in token_sets:
        port = _serve_port_from_tokens(tokens)
        if port is not None:
            return port
    return None


def _resolve_healthz_url(
    a3: dict[str, object],
    *,
    serve_plist_path: str | Path | None,
) -> str:
    configured_url = a3.get("healthz_url")
    if configured_url:
        return str(configured_url)
    if serve_plist_path is not None:
        serve_port = _serve_port_from_plist(serve_plist_path)
        if serve_port is not None:
            return f"http://127.0.0.1:{serve_port}/api/v1/healthz"
    return DEFAULT_HEALTHZ_URL


def _record_healthz_probe(
    state: dict[str, dict[str, object]],
    *,
    current: datetime,
    thresholds: dict[str, object],
    healthz_probe: Callable[[str, float], bool] | None,
    serve_plist_path: str | Path | None,
) -> int:
    a3 = _threshold_section(thresholds, "a3")
    url = _resolve_healthz_url(a3, serve_plist_path=serve_plist_path)
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
    state_path: Path,
    current: datetime,
    evaluation_sequence: int,
    sender: AlertSender,
    transport_dedup: bool,
    thresholds: dict[str, object],
    event_path: str | Path,
) -> list[dict[str, object]]:
    sent: list[dict[str, object]] = []
    delivered: list[tuple[dict[str, object], AlertRuleResult]] = []
    suppressed_events: list[dict[str, object]] = []
    for result in results:
        entry = state.get(
            result.rule_id,
            {"state": "ok", "severity": _normalize_severity(result.severity)},
        )
        if not isinstance(entry, dict):
            entry = {"state": "ok", "severity": _normalize_severity(result.severity)}
        last_evaluation_sequence = entry.get("last_evaluation_sequence")
        if (
            isinstance(last_evaluation_sequence, int)
            and evaluation_sequence <= last_evaluation_sequence
        ):
            continue
        if result.firing and result.suppressed_by:
            suppressed_events.append(
                {
                    "ts": current.isoformat(),
                    "rule_id": result.rule_id,
                    "severity": result.severity,
                    "type": "suppressed",
                    "detail": result.detail,
                    "values": result.values,
                    "channel": INTERNAL_CHANNEL,
                    "carrier": result.suppressed_by,
                    "suppressed": result.rule_id,
                    "reason": result.suppression_reason,
                    "heartbeat_fresh": "心跳新鲜" in str(result.suppression_reason),
                }
            )
        lifecycles = _normalize_lifecycles(entry)
        projected_severity = _normalize_severity(entry.get("severity"))

        def project(preferred_severity: AlertSeverity) -> dict[str, object]:
            projected = _project_lifecycles(
                lifecycles,
                preferred_severity=preferred_severity,
            )
            projected["last_evaluated_at"] = current.isoformat()
            projected["last_evaluation_sequence"] = evaluation_sequence
            projected["evaluation_state"] = result.evaluation_state
            return projected

        def prepare_notification(
            lifecycle: dict[str, object],
            *,
            severity: AlertSeverity,
            event_type: Literal["firing", "resolved"],
            episode_since: object,
            preferred_severity: AlertSeverity,
        ) -> tuple[dict[str, object], int]:
            pending = lifecycle.get("pending_notification")
            episode_identity = str(episode_since or "")
            if (
                isinstance(pending, dict)
                and pending.get("event_type") == event_type
                and pending.get("episode_since") == episode_identity
                and isinstance(pending.get("nonce"), int)
                and int(pending["nonce"]) > 0
            ):
                nonce = int(pending["nonce"])
            else:
                sequence = lifecycle.get("notification_sequence")
                nonce = (sequence if isinstance(sequence, int) and sequence >= 0 else 0) + 1
                pending = {
                    "nonce": nonce,
                    "event_type": event_type,
                    "episode_since": episode_identity,
                }
            existing_sequence = lifecycle.get("notification_sequence")
            if not isinstance(existing_sequence, int) or existing_sequence < 0:
                existing_sequence = 0
            prepared = {
                **lifecycle,
                "notification_sequence": max(nonce, existing_sequence),
                "pending_notification": pending,
            }
            lifecycles[severity] = prepared
            state[result.rule_id] = project(preferred_severity)
            _write_state(state_path, state)
            return prepared, nonce

        if not result.firing and result.evaluation_state == "in_progress":
            if not lifecycles:
                projected_severity = _normalize_severity(result.severity)
                lifecycles[projected_severity] = _ok_lifecycle(
                    {}, result.detail, result.evaluation_state
                )
            for severity, lifecycle in tuple(lifecycles.items()):
                lifecycles[severity] = {
                    **lifecycle,
                    "detail": result.detail,
                    "evaluation_state": result.evaluation_state,
                }
            state[result.rule_id] = project(projected_severity)
            continue

        if result.firing:
            effective_severity = _normalize_severity(result.severity)
            announced_page = lifecycles.get(PAGE_SEVERITY)
            if (
                effective_severity == NOTICE_SEVERITY
                and announced_page is not None
                and announced_page.get("state") == "firing"
                and _entry_announced(announced_page)
            ):
                lifecycles[PAGE_SEVERITY] = {
                    **announced_page,
                    "detail": result.detail,
                    "evaluation_state": result.evaluation_state,
                }
                state[result.rule_id] = project(PAGE_SEVERITY)
                continue
            debounce = _debounce_window(thresholds, result.rule_id, effective_severity)
            outgoing_announced = False
            outgoing_since: object = None
            pending_since: object = None
            announced_outgoing: list[tuple[AlertSeverity, dict[str, object]]] = []
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
                    announced_outgoing.append((severity, lifecycle))
                elif pending_since is None:
                    pending_since = lifecycle.get("since")
                if not announced:
                    lifecycles[severity] = _ok_lifecycle(
                        lifecycle, result.detail, result.evaluation_state
                    )

            lifecycle = lifecycles.get(
                effective_severity,
                {
                    "state": "ok",
                    "since": None,
                    "last_notified": None,
                    "detail": result.detail,
                    "announced": False,
                    "notification_sequence": 0,
                    "pending_notification": None,
                    "evaluation_state": result.evaluation_state,
                },
            )
            lifecycle_was_firing = lifecycle.get("state") == "firing"
            previously_announced = lifecycle_was_firing and _entry_announced(lifecycle)
            existing_firing_basis = _normalize_firing_basis(
                lifecycle.get("firing_basis")
            )
            firing_basis = result.firing_basis or existing_firing_basis
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
            should_notify = result.suppressed_by is None and confirmed and (
                last_notified is None or current - last_notified >= COOLDOWN
            )
            lifecycle = {
                **lifecycle,
                "state": "firing",
                "since": since,
                "detail": result.detail,
                "announced": previously_announced,
                "evaluation_state": result.evaluation_state,
            }
            if firing_basis is not None:
                lifecycle["firing_basis"] = firing_basis
            else:
                lifecycle.pop("firing_basis", None)
            lifecycles[effective_severity] = lifecycle
            state[result.rule_id] = project(effective_severity)
            delivery_succeeded = False
            if should_notify:
                text = _format_firing(result)
                lifecycle, notification_nonce = prepare_notification(
                    lifecycle,
                    severity=effective_severity,
                    event_type="firing",
                    episode_since=since,
                    preferred_severity=effective_severity,
                )
                receipt = _invoke_sender(
                    result=result,
                    event_type="firing",
                    text=text,
                    severity=effective_severity,
                    sender=sender,
                    episode_since=since,
                    notification_nonce=notification_nonce,
                    transport_dedup=transport_dedup,
                )
                sent.append(receipt)
                delivered.append((receipt, result))
                delivery_succeeded = receipt["delivered"] is True
            if delivery_succeeded or previously_announced:
                for severity, outgoing in announced_outgoing:
                    lifecycles[severity] = _ok_lifecycle(
                        outgoing, result.detail, result.evaluation_state
                    )
            lifecycles[effective_severity] = {
                **lifecycle,
                "state": "firing",
                "since": since,
                "last_notified": (
                    current.isoformat() if delivery_succeeded else lifecycle.get("last_notified")
                ),
                "detail": result.detail,
                "announced": previously_announced or delivery_succeeded,
                "pending_notification": (
                    None if delivery_succeeded else lifecycle.get("pending_notification")
                ),
                "evaluation_state": result.evaluation_state,
            }
            state[result.rule_id] = project(effective_severity)
            if should_notify:
                _write_state(state_path, state)
        else:
            for severity in (PAGE_SEVERITY, NOTICE_SEVERITY):
                lifecycle = lifecycles.get(severity)
                if lifecycle is None or lifecycle.get("state") != "firing":
                    continue
                if _entry_announced(lifecycle):
                    lifecycle, notification_nonce = prepare_notification(
                        lifecycle,
                        severity=severity,
                        event_type="resolved",
                        episode_since=lifecycle.get("since"),
                        preferred_severity=projected_severity,
                    )
                    receipt = _invoke_sender(
                        result=result,
                        event_type="resolved",
                        text=_format_resolved(result, str(lifecycle.get("since") or "")),
                        severity=severity,
                        sender=sender,
                        episode_since=lifecycle.get("since"),
                        notification_nonce=notification_nonce,
                        transport_dedup=transport_dedup,
                    )
                    sent.append(receipt)
                    delivered.append((receipt, result))
                    if receipt["delivered"] is True:
                        lifecycles[severity] = _ok_lifecycle(
                            lifecycle, result.detail, result.evaluation_state
                        )
                    state[result.rule_id] = project(projected_severity)
                    _write_state(state_path, state)
                else:
                    lifecycles[severity] = _ok_lifecycle(
                        lifecycle, result.detail, result.evaluation_state
                    )
            if projected_severity not in lifecycles:
                projected_severity = _normalize_severity(result.severity)
                lifecycles[projected_severity] = _ok_lifecycle(
                    {}, result.detail, result.evaluation_state
                )
            elif lifecycles[projected_severity].get("state") == "ok":
                lifecycles[projected_severity] = {
                    **lifecycles[projected_severity],
                    "detail": result.detail,
                    "evaluation_state": result.evaluation_state,
                }
            state[result.rule_id] = project(projected_severity)
    try:
        _record_notification_events(event_path, current=current, delivered=delivered)
        _record_event_rows(event_path, current=current, new_rows=suppressed_events)
    except BaseException:
        pass
    return sent


def run_alert_results_state_machine(
    results: list[AlertRuleResult],
    *,
    state_path: str | Path,
    event_path: str | Path = DEFAULT_EVENT_PATH,
    now: datetime | None = None,
    send: AlertSender | None = None,
    thresholds: dict[str, object] | None = None,
    evaluation_sequence: int | None = None,
) -> dict[str, object]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    path = Path(state_path)
    ledger_path = Path(event_path)
    _validate_alert_paths(state_path=path, event_path=ledger_path)
    with _alert_state_lock(path):
        state = _load_state(path)
        active_evaluation_sequence = _claim_evaluation_sequence(
            state,
            evaluation_sequence,
        )
        sender = send or send_alert_message
        sent = _apply_alert_results(
            state,
            results,
            state_path=path,
            current=current,
            evaluation_sequence=active_evaluation_sequence,
            sender=sender,
            transport_dedup=send is None,
            thresholds=thresholds or {},
            event_path=ledger_path,
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
    event_path: str | Path = DEFAULT_EVENT_PATH,
    now: datetime | None = None,
    send: AlertSender | None = None,
    thresholds: dict[str, object] | None = None,
    healthz_probe: Callable[[str, float], bool] | None = None,
    serve_plist_path: str | Path | None = None,
) -> dict[str, object]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    path = Path(state_path)
    ledger_path = Path(event_path)
    _validate_alert_paths(state_path=path, event_path=ledger_path)
    with _alert_state_lock(path):
        state = _load_state(path)
        sender = send or send_alert_message
        active_thresholds = thresholds or ALERT_THRESHOLDS
        healthz_consecutive_failures = _record_healthz_probe(
            state,
            current=current,
            thresholds=active_thresholds,
            healthz_probe=healthz_probe,
            serve_plist_path=serve_plist_path,
        )
        signals = replace(signals, healthz_consecutive_failures=healthz_consecutive_failures)
        results = evaluate_rules(signals, thresholds=active_thresholds)
        results = _correlate_alert_results(
            results,
            heartbeat_fresh=(
                signals.minutes_since_successful_pipeline
                <= _int_threshold(
                    _threshold_section(active_thresholds, "a2"),
                    "no_success_minutes",
                    120,
                )
            ),
        )
        evaluation_sequence = _claim_evaluation_sequence(state)
        sent = _apply_alert_results(
            state,
            results,
            state_path=path,
            current=current,
            evaluation_sequence=evaluation_sequence,
            sender=sender,
            transport_dedup=send is None,
            thresholds=active_thresholds,
            event_path=ledger_path,
        )
        _write_state(path, state)
    return {
        "ruleset": list(RULESET),
        "sent_count": len(sent),
        "sent": sent,
        "results": [asdict(result) for result in results],
        "state_path": str(path),
    }


def send_alert_message(
    text: str,
    *,
    severity: AlertSeverity = PAGE_SEVERITY,
    dedup_key: str | None = None,
    dedup_text: str | None = None,
) -> dict[str, object]:
    effective_severity = _normalize_severity(severity)
    command = ["im-notify"]
    if effective_severity == PAGE_SEVERITY:
        command.append("--alert")
    if dedup_key is not None:
        command.extend(("--dedup-key", dedup_key))
    if dedup_text is not None:
        command.extend(("--dedup-text", dedup_text))
    command.append(text)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15.0,
            env=direct_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = f"im-notify failed: {type(exc).__name__}: {exc}"
        LOGGER.error("im-notify alert delivery failed: %s", reason)
        return {"skipped": True, "reason": reason}
    if completed.returncode != 0:
        reason = f"im-notify exited with status {completed.returncode}"
        LOGGER.error("im-notify alert delivery failed: %s; stderr=%s", reason, completed.stderr.strip())
        return {"skipped": True, "reason": reason}
    return {"skipped": False, "returncode": completed.returncode}


def _clear_notification_dedup(key: str) -> dict[str, object]:
    try:
        completed = subprocess.run(
            ["im-notify", "--dedup-clear", key],
            capture_output=True,
            text=True,
            timeout=15.0,
            env=direct_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        reason = f"im-notify --dedup-clear failed: {type(exc).__name__}: {exc}"
        LOGGER.error(reason)
        return {"cleared": False, "reason": reason}
    if completed.returncode:
        reason = f"im-notify --dedup-clear exited with status {completed.returncode}: {completed.stderr.strip()}"
        LOGGER.error(reason)
        return {"cleared": False, "reason": reason}
    return {"cleared": True, "returncode": 0}


def _price_description(signature: str) -> str:
    try:
        values = json.loads(signature)
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(signature)
    if not isinstance(values, list) or len(values) < 3:
        return str(signature)
    return f"input/cache/output USD per 1M={values[0]}/{values[1]}/{values[2]}"


def run_pricing_notifications(
    usage_report: Mapping[str, object],
    *,
    state_path: str | Path,
    event_path: str | Path | None = None,
    now: datetime | None = None,
    send: Callable[..., object] | None = None,
    clear: Callable[[str], object] | None = None,
    message_prefix: str = "",
) -> dict[str, object]:
    """Deliver retryable D3 notices and append successful lifecycle events."""
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    path = Path(state_path)
    ledger_path = Path(event_path) if event_path is not None else path.with_name("alert-events.jsonl")
    try:
        raw_state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        raw_state = {}
    state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
    sender = send or send_alert_message
    clearer = clear or _clear_notification_dedup

    total_calls = 0
    totals = usage_report.get("totals")
    if isinstance(totals, Mapping):
        total_calls = int(totals.get("calls") or 0)
    desired: dict[str, dict[str, str]] = {}
    unpriced = usage_report.get("unpriced", [])
    if isinstance(unpriced, list):
        for raw in unpriced:
            if not isinstance(raw, dict):
                continue
            pair = f"{raw.get('provider')}/{raw.get('model')}"
            calls = int(raw.get("calls") or 0)
            event = f"unpriced:{pair}"
            desired[event] = {
                "dedup_text": event,
                "text": (
                    f"{message_prefix}【AI Radar】🟡 D3 出现已记录未定价 LLM 调用\n"
                    f"具体对象/数值：{pair} {calls}/{total_calls or '?'} 次已记录调用；"
                    "金额未知，不得按 ¥0 处理。\n"
                    "处置方向：在 src/airadar/pricing.py 补齐并核实该 provider/model 的权威单价；"
                    "补价前不要据已知金额做总成本结论。"
                ),
            }

    current_prices: dict[str, str] = {}
    pricing_rows: dict[str, dict[str, object]] = {}
    pricing_table = usage_report.get("pricing_table", [])
    if isinstance(pricing_table, list):
        for raw in pricing_table:
            if not isinstance(raw, dict):
                continue
            pair = f"{raw.get('provider')}/{raw.get('model')}"
            pricing_rows[pair] = raw
            if raw.get("matched_key") is not None:
                current_prices[pair] = json.dumps(
                    [
                        raw.get("input_per_million_tokens_usd"),
                        raw.get("cache_read_per_million_tokens_usd"),
                        raw.get("output_per_million_tokens_usd"),
                        raw.get("effective_from"),
                        raw.get("effective_to"),
                    ],
                    separators=(",", ":"),
                )
            status = raw.get("freshness")
            if status in {"stale", "due-review"}:
                event = f"{status}:{pair}"
                desired[event] = {
                    "dedup_text": event,
                    "text": (
                        f"{message_prefix}【AI Radar】🟡 D3 {pair} 价格状态为 {status}\n"
                        "影响：该模型的目录价估算可能已过期；这不是账单实付。\n"
                        "处置方向：对照权威计费页复核 src/airadar/pricing.py 中的价格与 verified_at。"
                    ),
                }

    raw_previous_prices = state.get("price_signatures", {})
    previous_prices = (
        {str(key): str(value) for key, value in raw_previous_prices.items()}
        if isinstance(raw_previous_prices, dict)
        else {}
    )
    next_prices = dict(previous_prices)
    changed_events: dict[str, tuple[str, str]] = {}
    for pair, signature in current_prices.items():
        old = previous_prices.get(pair)
        if old is None or old == signature:
            next_prices[pair] = signature
            continue
        event = f"price-changed:{pair}"
        changed_events[event] = (old, signature)
        desired[event] = {
            "dedup_text": event,
            "text": (
                f"{message_prefix}【AI Radar】🟡 D3 LLM 目录价发生变化\n"
                f"具体对象：{pair}；旧值：{_price_description(old)}；"
                f"新值：{_price_description(signature)}。\n"
                "影响：历史 usage 未固定逐行价格；A6 已按同一现行费率重算两窗，"
                "纯调价不会被当作量结构突变。\n"
                "处置方向：在 src/airadar/pricing.py 核实权威来源、生效区间与 verified_at；"
                "周报金额为目录价估算，并非账单实付。"
            ),
        }

    raw_previous_active = state.get("active", {})
    previous_active = raw_previous_active if isinstance(raw_previous_active, dict) else {}
    retained_active: dict[str, object] = {}
    sent: list[dict[str, object]] = []
    ledger_rows: list[dict[str, object]] = []
    for event, payload in desired.items():
        previous_payload = previous_active.get(event)
        rearm_after_clear_failure = bool(
            isinstance(previous_payload, dict)
            and previous_payload.get("rearm_after_clear_failure") is True
        )
        if event in previous_active and not rearm_after_clear_failure:
            retained_active[event] = previous_active[event]
            continue
        key = f"ai-radar:d3:{event}"
        dedup_text = str(payload["dedup_text"])
        if rearm_after_clear_failure:
            assert isinstance(previous_payload, dict)
            dedup_text = str(
                previous_payload.get("rearm_dedup_text")
                or f"{dedup_text}:recurred:{current.isoformat()}"
            )
        result = sender(
            payload["text"],
            severity=NOTICE_SEVERITY,
            dedup_key=key,
            dedup_text=dedup_text,
        )
        delivered = _snapshot_delivery_succeeded(result)
        receipt = {
            "event": event,
            "channel": NOTIFICATION_CHANNEL,
            "text": payload["text"],
            "send_result": result,
            "delivered": delivered,
        }
        sent.append(receipt)
        if not delivered:
            if rearm_after_clear_failure:
                retained_active[event] = {
                    **payload,
                    "rearm_after_clear_failure": True,
                    "rearm_dedup_text": dedup_text,
                }
            continue
        retained_active[event] = payload
        if event in changed_events:
            next_prices[event.removeprefix("price-changed:")] = changed_events[event][1]
        ledger_rows.append(
            {
                "ts": current.isoformat(),
                "rule_id": f"D3:{event}",
                "severity": NOTICE_SEVERITY,
                "type": "firing",
                "detail": payload["text"],
                "values": {"event": event},
                "channel": NOTIFICATION_CHANNEL,
                "episode_since": current.isoformat(),
            }
        )

    cleared: list[dict[str, object]] = []
    for event in sorted(set(previous_active) - set(desired)):
        key = f"ai-radar:d3:{event}"
        result = clearer(key)
        cleared_ok = isinstance(result, Mapping) and result.get("cleared") is True
        cleared.append({"event": event, "result": result, "cleared": cleared_ok})
        if not cleared_ok:
            previous_payload = previous_active[event]
            prior = previous_payload if isinstance(previous_payload, dict) else {}
            retained_active[event] = {
                **prior,
                "rearm_after_clear_failure": True,
                "rearm_dedup_text": str(
                    prior.get("rearm_dedup_text")
                    or f"{event}:recurred:{current.isoformat()}"
                ),
            }
            LOGGER.error("D3 dedup clear failed event=%s key=%s result=%r", event, key, result)
            continue
        ledger_rows.append(
            {
                "ts": current.isoformat(),
                "rule_id": f"D3:{event}",
                "severity": NOTICE_SEVERITY,
                "type": "resolved",
                "detail": "条件已解除并清除 transport dedup",
                "values": {"event": event},
                "channel": NOTIFICATION_CHANNEL,
                "episode_since": None,
            }
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    price_state: dict[str, object] = dict(next_prices)
    next_state: dict[str, dict[str, object]] = {
        "active": retained_active,
        "price_signatures": price_state,
    }
    _write_state(path, next_state)
    _record_event_rows(ledger_path, current=current, new_rows=ledger_rows)
    return {"sent": sent, "cleared": cleared, "state_path": str(path)}


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


def _silent_source_signal(
    db_path: str | Path | None,
    current: datetime,
    *,
    floor_hours: float = 6.0,
    lookback_days: int = 30,
    min_history: int = 5,
    x_receipt_fresh_minutes: int = 120,
) -> tuple[
    list[tuple[str, str, float, float]],
    int,
    int,
    list[tuple[str, str, float]],
    list[tuple[str, str, float, float]],
]:
    """Find enabled sources that have stopped producing items.

    Exists because the aggregate ingestion signal cannot see a single source
    die: when WeChat produced nothing for 73 hours the other ~160 sources kept
    the site-wide item count above its floor, so the volume branch stayed
    healthy the whole time.

    The threshold is per source, not global. A flat 6h would page constantly
    for the accounts that publish every few days, and an alert that pages on
    healthy behaviour gets muted — at which point it protects nothing. So 6h is
    the floor and each source's own recent cadence widens it from there.

    Sources with too little history to characterise are returned as a count
    rather than assumed healthy: silently passing them would make "never
    checked" indistinguishable from "checked and fine".

    Faded sources are that count's dangerous half, separated out. A source that
    dies stays silent long enough for its items to age out of the window, at
    which point `recent_count` drops below the minimum and it leaves the
    evaluated set — silently, and looking exactly like a source that never
    warmed up. `silent_sources` then empties and the rule reads as resolved,
    so a source announces recovery at the moment it is most thoroughly dead.
    What tells the two apart is the source's newest few items: they outlive the
    window, so the cadence it last held stays measurable after the window has
    forgotten it, and a source that never held one has no such gaps to show.

    The faded test is deliberately the same shape as the evaluable one — twice
    the source's own typical gap, floored — differing only in what it measures
    that gap over. Two cheaper baselines were tried and both misfire: the flat
    floor alone calls a source dead the moment its fifth-oldest item ages out,
    which for anything publishing every few days happens while it is behaving
    normally; and the mean gap over all time is dominated by a single stale row
    — a revived source, a backfilled item from years back — widening the
    threshold past any silence that could ever trip it.

    Returns (silent, evaluated_count, unevaluable_count, faded, quiet_x) where each
    silent row is (source_id, name, hours_silent, threshold_hours) and each
    faded row is (source_id, name, hours_silent). Each quiet_x row adds the
    terminal receipt age in minutes after hours_silent.
    """
    window_hours = lookback_days * 24.0
    cutoff = (current - timedelta(days=lookback_days)).isoformat().replace("+00:00", "Z")
    silent: list[tuple[str, str, float, float]] = []
    faded: list[tuple[str, str, float]] = []
    quiet_x: list[tuple[str, str, float, float]] = []
    evaluated = 0
    unevaluable = 0
    with db.get_conn(db_path) as conn:
        # Pin the item history and runtime receipt to one SQLite snapshot. The
        # fetcher commits them atomically; splitting these reads across commits
        # would throw away that guarantee on the evaluator side.
        conn.execute("BEGIN")
        rows = conn.execute(
            """
            SELECT s.*,
                   MAX(i.fetched_at) AS last_fetched,
                   COUNT(i.id) AS total_count,
                   SUM(CASE WHEN i.fetched_at >= ? THEN 1 ELSE 0 END) AS recent_count
            FROM sources s
            LEFT JOIN items i ON i.source_id = s.id
            WHERE s.enabled = 1
            GROUP BY s.id, s.name
            """,
            (cutoff,),
        ).fetchall()

        def recent_cadence_hours(source_id: str) -> float | None:
            """Typical gap across this source's newest `min_history` items.

            Deliberately counted in items rather than measured over a fixed
            span: a source that stopped publishing has no items in any recent
            time window, so a time-based baseline for it is either empty or
            filled with whatever came before. Bounding it to the newest few
            also keeps one stale row — a long-dormant source that was revived,
            or a single backfilled item from years earlier — from averaging the
            gap out to something no recent silence can ever exceed.
            """
            stamps = [
                str(entry["fetched_at"])
                for entry in conn.execute(
                    """
                    SELECT fetched_at FROM items
                    WHERE source_id = ? AND fetched_at IS NOT NULL
                    ORDER BY fetched_at DESC LIMIT ?
                    """,
                    (source_id, min_history),
                ).fetchall()
            ]
            if len(stamps) < 2:
                return None
            try:
                newest = datetime.fromisoformat(stamps[0].replace("Z", "+00:00"))
                oldest = datetime.fromisoformat(stamps[-1].replace("Z", "+00:00"))
            except ValueError:
                return None
            if newest.tzinfo is None:
                newest = newest.replace(tzinfo=UTC)
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=UTC)
            return (newest - oldest).total_seconds() / 3600.0 / (len(stamps) - 1)

        cadence_by_source = {
            str(row["id"]): recent_cadence_hours(str(row["id"]))
            for row in rows
            if int(row["total_count"] or 0) >= min_history
            and int(row["recent_count"] or 0) < min_history
        }
    for row in rows:
        recent = int(row["recent_count"] or 0)
        last_fetched = row["last_fetched"]
        try:
            last = (
                datetime.fromisoformat(str(last_fetched).replace("Z", "+00:00"))
                if last_fetched
                else None
            )
        except ValueError:
            last = None
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        hours_silent = (current - last).total_seconds() / 3600.0 if last is not None else None
        if recent < min_history or last is None:
            unevaluable += 1
            cadence_hours = cadence_by_source.get(str(row["id"]))
            if cadence_hours is not None and hours_silent is not None:
                if hours_silent > max(floor_hours, 2.0 * cadence_hours):
                    faded.append((str(row["id"]), str(row["name"]), hours_silent))
            continue
        evaluated += 1
        # Coarse cadence: the average gap over the window. Doubling it keeps a
        # source that merely skipped one publishing slot out of the alert.
        typical_gap_hours = window_hours / recent
        threshold = max(floor_hours, 2.0 * typical_gap_hours)
        if hours_silent is not None and hours_silent > threshold:
            source_id = str(row["id"])
            source_name = str(row["name"])
            row_keys = set(row.keys())
            kind = str(row["kind"] or "") if "kind" in row_keys else ""
            meta_json = row["meta_json"] if "meta_json" in row_keys else None
            receipt_age_minutes: float | None = None
            if kind == "x" and isinstance(meta_json, str):
                try:
                    meta = json.loads(meta_json)
                    if isinstance(meta, dict) and meta.get("adapter") == "x_api":
                        runtime = validate_x_runtime_meta(meta, context=source_id)
                        validated_at = _parse_dt(runtime.get("x_reference_validated_at"))
                        if (
                            runtime.get("x_reference_status") == "verified"
                            and runtime.get("x_cursor_state") == "checkpointed"
                            and validated_at is not None
                        ):
                            receipt_age_minutes = (current - validated_at).total_seconds() / 60.0
                except (TypeError, ValueError, json.JSONDecodeError):
                    receipt_age_minutes = None
            if (
                receipt_age_minutes is not None
                and 0.0 <= receipt_age_minutes <= float(x_receipt_fresh_minutes)
            ):
                quiet_x.append((source_id, source_name, hours_silent, receipt_age_minutes))
            else:
                silent.append((source_id, source_name, hours_silent, threshold))
    return silent, evaluated, unevaluable, faded, quiet_x


def _wechat_interpretation_signal(
    db_path: str | Path | None, current: datetime, no_success_hours: float
) -> tuple[float | None, int, int, str | None]:
    from ..interpret.runner import ERROR_RETRY_BASE_MINUTES, ERROR_RETRY_MAX

    cutoff = current - timedelta(hours=no_success_hours)
    with db.get_conn(db_path) as conn:
        latest = conn.execute(
            "SELECT MAX(processed_at) AS processed_at FROM wechat_interpretations WHERE error IS NULL"
        ).fetchone()
        rows = conn.execute(
            """
            SELECT i.title, i.fetched_at, wi.error, wi.error_retry_count, wi.processed_at
            FROM items i
            JOIN sources s ON s.id=i.source_id
            LEFT JOIN wechat_interpretations wi ON wi.item_id=i.id
            WHERE COALESCE(s.kind, 'feed')='wechat' AND s.enabled=1
              AND (wi.item_id IS NULL OR (wi.error IS NOT NULL AND wi.error_retry_count < ?))
            ORDER BY i.fetched_at, i.id
            """,
            (ERROR_RETRY_MAX,),
        ).fetchall()
        frozen = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM items i
            JOIN sources s ON s.id=i.source_id
            JOIN wechat_interpretations wi ON wi.item_id=i.id
            WHERE COALESCE(s.kind, 'feed')='wechat' AND s.enabled=1
              AND wi.error IS NOT NULL AND wi.error_retry_count >= ?
            """,
            (ERROR_RETRY_MAX,),
        ).fetchone()
    processed = _parse_dt(latest["processed_at"]) if latest else None
    hours_since = max(0.0, (current - processed).total_seconds() / 3600) if processed else None
    eligible: list[Any] = []
    for row in rows:
        fetched = _parse_dt(row["fetched_at"])
        if fetched is None or fetched > cutoff:
            continue
        if row["error"] is not None:
            retry_count = int(row["error_retry_count"] or 0)
            processed_at = _parse_dt(row["processed_at"])
            backoff_minutes = ERROR_RETRY_BASE_MINUTES * (1 << retry_count)
            if processed_at is not None and current < processed_at + timedelta(minutes=backoff_minutes):
                continue
        eligible.append(row)
    return (
        hours_since,
        len(eligible),
        int(frozen["count"] or 0) if frozen else 0,
        str(eligible[0]["title"] or "（无标题）") if eligible else None,
    )


def _a6_measurement_in_progress(
    activity: dict[str, dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
    lock_path: Path,
) -> bool:
    # A running pipeline is whoever holds the kernel flock (ADR-052); an
    # unknown probe result must not suppress the alert, so only a positive
    # "held" counts as measurement in progress.
    if pipeline_lock_is_held(lock_path) is not True:
        return False
    problem_days: list[str] = []
    cursor = start.astimezone(SHANGHAI_TZ).date()
    last_day = (end.astimezone(SHANGHAI_TZ) - timedelta(microseconds=1)).date()
    while cursor <= last_day:
        day = cursor.isoformat()
        row = activity.get(day)
        if row is None or not bool(row.get("complete")):
            problem_days.append(day)
        if row is not None and int(row.get("failures") or 0) > 0:
            return False
        cursor += timedelta(days=1)
    return problem_days == [end.astimezone(SHANGHAI_TZ).date().isoformat()]


def collect_alert_signals(
    *,
    db_path: str | Path | None = None,
    pipeline_log_dir: str | Path | None = None,
    access_log_paths: list[str | Path] | None = None,
    usage_db_path: str | Path | None = None,
    pricing_catalog: Any | None = None,
    now: datetime | None = None,
    pipeline_lock_path: str | Path | None = None,
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
    latest_fetch = ingestion.get("latest_fetch")
    a4_thresholds = _threshold_section(ALERT_THRESHOLDS, "a4")
    fetch_stale_limit = _int_threshold(a4_thresholds, "fetch_stale_minutes", 90)
    attempted = 0
    failed = 0
    fetch_stale_minutes: int | None = None
    fetch_stale_reason: str | None = None
    fetch_evaluated = False
    failed_by_status: dict[int, int] = {}
    failed_sources_by_status: dict[int, list[str]] = {}
    if isinstance(latest_fetch, dict):
        attempted = int(str(latest_fetch.get("attempted", 0) or 0))
        failed = int(str(latest_fetch.get("failed", 0) or 0))
        fetch_stale_minutes = int(str(latest_fetch.get("stale_minutes", 0) or 0))
        # metrics owns the staleness predicate (expiry and future-timestamp
        # tolerance); the rule only consumes its verdict.
        fetch_evaluated = not bool(latest_fetch.get("stale", fetch_stale_minutes > fetch_stale_limit))
        raw_reason = latest_fetch.get("stale_reason")
        fetch_stale_reason = str(raw_reason) if raw_reason else None
        failed_by_status = _int_keyed_counts(latest_fetch.get("failed_by_status"))
        raw_sources_by_status = latest_fetch.get("failed_sources_by_status")
        if isinstance(raw_sources_by_status, dict):
            for status, sources in raw_sources_by_status.items():
                if isinstance(sources, list):
                    failed_sources_by_status[int(status)] = [str(source) for source in sources]
    recent_complete_fetches: list[dict[str, object]] = []
    raw_recent = ingestion.get("recent_complete_fetches", [])
    if isinstance(raw_recent, list):
        for summary in raw_recent:
            if not isinstance(summary, dict):
                continue
            recent_complete_fetches.append(
                {
                    "completed_at": summary.get("completed_at"),
                    "attempted": int(str(summary.get("attempted", 0) or 0)),
                    "failed_by_status": _int_keyed_counts(summary.get("failed_by_status")),
                }
            )
    a7 = _threshold_section(ALERT_THRESHOLDS, "a7")
    (
        silent_sources,
        evaluated_sources,
        unevaluable_sources,
        faded_sources,
        quiet_x_sources,
    ) = _silent_source_signal(
        db_path,
        current,
        floor_hours=_float_threshold(a7, "silence_floor_hours", 6.0),
        x_receipt_fresh_minutes=_int_threshold(a2, "no_success_minutes", 120),
    )
    a5 = _threshold_section(ALERT_THRESHOLDS, "a5")
    no_success_hours = _float_threshold(a5, "no_success_hours", 4.0)
    hours_since_interpret, pending_count, frozen_count, pending_title = _wechat_interpretation_signal(
        db_path, current, no_success_hours
    )
    a6 = _threshold_section(ALERT_THRESHOLDS, "a6")
    usage_rows = _load_usage_rows(
        db_path=db_path,
        usage_db_path=usage_db_path,
        start=current.astimezone(UTC) - timedelta(days=15),
        end=current.astimezone(UTC),
    )
    log_dir = Path(pipeline_log_dir) if pipeline_log_dir is not None else db.PROJECT_ROOT / "logs"
    metering_start = current - timedelta(hours=24)
    a6_activity = _pipeline_activity(log_dir, metering_start, current)
    metering = _window_metering(a6_activity, metering_start, current)
    lock_path = (
        Path(pipeline_lock_path)
        if pipeline_lock_path is not None
        else DEFAULT_PIPELINE_LOCK_PATH
    )
    measurement_in_progress = (
        not bool(metering["complete"])
        and _a6_measurement_in_progress(
            a6_activity,
            start=metering_start,
            end=current,
            lock_path=lock_path,
        )
    )
    a6_signal = evaluate_a6_cost(
        usage_rows,
        now=current,
        catalog=pricing_catalog,
        daily_floor_cny=_float_threshold(a6, "daily_floor_cny", 20.0),
        spike_multiplier=_float_threshold(a6, "spike_multiplier", 3.0),
        page_floor_cny=_float_threshold(a6, "page_floor_cny", 100.0),
        page_multiplier=_float_threshold(a6, "page_multiplier", 6.0),
        metering_complete=bool(metering["complete"]),
        metering_failure_count=int(metering["failure_count"]),
    )
    top = a6_signal["top_driver"]

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
        fetch_evaluated=fetch_evaluated,
        fetch_stale_minutes=fetch_stale_minutes,
        fetch_stale_reason=fetch_stale_reason,
        fetch_attempted=attempted,
        failed_by_status=failed_by_status,
        failed_sources_by_status=failed_sources_by_status,
        recent_complete_fetches=recent_complete_fetches,
        stage_sample_count=stage_sample_count,
        server_pv=int(users.get("pv") or 0),
        hours_since_successful_interpretation=hours_since_interpret,
        wechat_pending_count=pending_count,
        wechat_frozen_count=frozen_count,
        oldest_wechat_pending_title=pending_title,
        a5_enabled=os.environ.get("AI_RADAR_ENABLE_INTERPRET", "").strip().lower()
        in {"1", "true", "yes"},
        a6_evaluable=bool(a6_signal["evaluable"]),
        a6_measurement_in_progress=measurement_in_progress,
        a6_current_cost_cny=float(a6_signal["known_cost_cny"]),
        a6_baseline_median_cny=a6_signal["baseline_median_cny"],
        a6_threshold_cny=a6_signal["threshold_cny"],
        a6_page_threshold_cny=a6_signal["page_threshold_cny"],
        a6_baseline_days=int(a6_signal["baseline_days"]),
        a6_excluded_coverage_days=int(a6_signal["excluded_coverage_days"]),
        a6_top_driver=(
            f"{top['provider']}/{top['model']} ¥{float(top['known_cost_cny']):.2f}"
            if isinstance(top, dict) else None
        ),
        a6_unpriced_calls=int(a6_signal["unpriced_calls"]),
        a6_pricing_freshness=str(a6_signal["pricing_freshness"]),
        silent_sources=silent_sources,
        evaluated_sources=evaluated_sources,
        unevaluable_sources=unevaluable_sources,
        faded_sources=faded_sources,
        quiet_x_sources=quiet_x_sources,
        a6_metering_complete=bool(a6_signal["metering_complete"]),
        a6_metering_failure_count=int(a6_signal["metering_failure_count"]),
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
