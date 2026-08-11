from __future__ import annotations

import json
import re
import statistics
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db
from ..llm_usage import derive_cost_usd, migrate_usage_db
from ..pricing import PricingCatalog, get_pricing, usd_cny_rate
from .metrics import _load_pipeline_runs
from .usage import SHANGHAI_TZ, _parse_dt, collect_usage

METERING_FAILURE_RE = re.compile(r"\bllm_usage_metering_failure\b")


def report_window(now: datetime, window_days: int | None) -> tuple[datetime, datetime, str]:
    current = now if now.tzinfo is not None else now.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ).replace(microsecond=0)
    if window_days is not None:
        if window_days <= 0:
            raise ValueError("window_days must be positive")
        return current - timedelta(days=window_days), current, f"rolling-{window_days}d"
    monday = (current - timedelta(days=current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday - timedelta(days=7), monday, "previous-shanghai-week"


def _load_usage_rows(
    *, db_path: str | Path | None, usage_db_path: str | Path | None, start: datetime, end: datetime
) -> list[Any]:
    path = migrate_usage_db(usage_db_path=usage_db_path, main_db_path=db_path)
    with db.get_conn(path) as conn:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(llm_usage)")}
        cached = "cached_input_tokens" if "cached_input_tokens" in columns else "NULL AS cached_input_tokens"
        return conn.execute(
            f"""
            SELECT id, stage, provider, model, item_id, input_tokens, {cached},
                   output_tokens, input_char_count, attribution_json, created_at
            FROM llm_usage
            WHERE created_at >= ? AND created_at <= ?
            ORDER BY created_at, id
            """,
            (
                start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            ),
        ).fetchall()


def build_cost_report(
    *,
    db_path: str | Path | None = None,
    usage_db_path: str | Path | None = None,
    window_days: int | None = None,
    now: datetime | None = None,
    pricing_catalog: PricingCatalog | None = None,
    rows_snapshot: Sequence[Any] | None = None,
    pipeline_log_dir: str | Path | None = None,
    fetched_counts_snapshot: dict[str, int] | None = None,
    wechat_fetched_counts_snapshot: dict[str, int] | None = None,
    processed_counts_snapshot: dict[str, int | dict[str, int]] | None = None,
    metering_snapshot: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(SHANGHAI_TZ)
    start, end, kind = report_window(current, window_days)
    days = int((end - start).total_seconds() // 86400)
    previous_start = start - (end - start)
    rows = list(rows_snapshot) if rows_snapshot is not None else _load_usage_rows(
        db_path=db_path, usage_db_path=usage_db_path, start=previous_start, end=end
    )
    usage = collect_usage(
        db_path=db_path,
        usage_db_path=usage_db_path,
        days=days,
        now=end,
        priced_at=current,
        pricing_catalog=pricing_catalog,
        rows_snapshot=rows,
    )
    window = usage["window"]
    assert isinstance(window, dict)
    window["kind"] = kind
    activity = (
        {day: dict(values) for day, values in metering_snapshot.items()}
        if metering_snapshot is not None
        else (
            _pipeline_activity(Path(pipeline_log_dir), previous_start, end)
            if pipeline_log_dir is not None
            else {}
        )
    )
    fetched_counts = (
        dict(fetched_counts_snapshot)
        if fetched_counts_snapshot is not None
        else (
            _fetched_counts(db_path, previous_start, end)
            if rows_snapshot is None
            else {}
        )
    )
    wechat_fetched_counts = (
        dict(wechat_fetched_counts_snapshot)
        if wechat_fetched_counts_snapshot is not None
        else (
            _fetched_counts(db_path, previous_start, end, source_kind="wechat")
            if rows_snapshot is None
            else {}
        )
    )
    processed_counts = (
        dict(processed_counts_snapshot)
        if processed_counts_snapshot is not None
        else (
            _durable_processing_counts(db_path, previous_start, end)
            if rows_snapshot is None
            else {}
        )
    )
    usage["daily"] = _complete_daily_series(
        usage.get("daily", []),
        start=start,
        end=end,
        activity=activity,
        fetched_counts=fetched_counts,
        wechat_fetched_counts=wechat_fetched_counts,
        processed_counts=processed_counts,
    )
    calls_by_day = _usage_calls_by_day(rows, previous_start, end)
    current_gaps, current_unknown, current_gap_stages = _processing_exposure_days(
        activity,
        fetched_counts,
        wechat_fetched_counts,
        processed_counts,
        calls_by_day,
        start=start,
        end=end,
    )
    previous_gaps, previous_unknown, previous_gap_stages = _processing_exposure_days(
        activity,
        fetched_counts,
        wechat_fetched_counts,
        processed_counts,
        calls_by_day,
        start=previous_start,
        end=start,
    )
    comparison = usage["comparison"]
    assert isinstance(comparison, dict)
    comparison["processing_gap_days"] = current_gaps
    comparison["previous_processing_gap_days"] = previous_gaps
    comparison["processing_gap_stages"] = current_gap_stages
    comparison["previous_processing_gap_stages"] = previous_gap_stages
    comparison["processing_unknown_days"] = current_unknown
    comparison["previous_processing_unknown_days"] = previous_unknown
    comparison_metering = _window_metering(activity, previous_start, end)
    comparison["metering_log_expected_days"] = comparison_metering["expected_days"]
    comparison["metering_log_observed_days"] = comparison_metering["observed_days"]
    comparison["processing_evidence"] = "durable"
    if current_unknown or previous_unknown:
        comparison["available"] = False
        comparison["reason"] = "processing_exposure_unknown"
    elif current_gaps or previous_gaps:
        comparison["available"] = False
        comparison["reason"] = "processing_exposure_gap"
    usage["metering"] = _window_metering(activity, start, end)
    return usage


def _fetched_counts(
    db_path: str | Path | None,
    start: datetime,
    end: datetime,
    *,
    source_kind: str | None = None,
) -> dict[str, int]:
    source_join = "JOIN sources s ON s.id=i.source_id" if source_kind is not None else ""
    source_filter = "AND COALESCE(s.kind, 'feed')=?" if source_kind is not None else ""
    params: list[object] = [
        start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    ]
    if source_kind is not None:
        params.append(source_kind)
    with db.get_conn(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT date(datetime(fetched_at, '+8 hours')) AS day, COUNT(*) AS count
            FROM items i
            {source_join}
            WHERE fetched_at >= ? AND fetched_at < ?
            {source_filter}
            GROUP BY 1
            """,
            params,
        ).fetchall()
    return {str(row["day"]): int(row["count"] or 0) for row in rows if row["day"]}


def _durable_processing_counts(
    db_path: str | Path | None, start: datetime, end: datetime
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    lower = start.astimezone(UTC).isoformat().replace("+00:00", "Z")
    upper = end.astimezone(UTC).isoformat().replace("+00:00", "Z")
    with db.get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT evaluated_at AS processed_at, stage, numeric_json, error
            FROM item_evaluations
            WHERE evaluated_at >= ? AND evaluated_at < ?
            UNION ALL
            SELECT processed_at, 'interpret' AS stage, NULL AS numeric_json, error
            FROM wechat_interpretations
            WHERE processed_at >= ? AND processed_at < ?
            """,
            (lower, upper, lower, upper),
        ).fetchall()
    for row in rows:
        processed_at = _parse_dt(row["processed_at"])
        if processed_at is None:
            continue
        day = processed_at.astimezone(SHANGHAI_TZ).date().isoformat()
        target = counts.setdefault(
            day,
            {
                "prefilter": 0,
                "prefilter_candidates": 0,
                "score": 0,
                "enrich": 0,
                "interpret": 0,
            },
        )
        if row["error"] is not None:
            continue
        stage = "score" if row["stage"] == "scoring" else str(row["stage"])
        if stage not in {"prefilter", "score", "enrich", "interpret"}:
            continue
        target[stage] += 1
        if stage == "prefilter":
            try:
                numeric = json.loads(str(row["numeric_json"] or "{}"))
            except json.JSONDecodeError:
                numeric = {}
            if numeric.get("is_ai_related") is True:
                target["prefilter_candidates"] += 1
    return counts


def _pipeline_activity(
    log_dir: Path, start: datetime, end: datetime
) -> dict[str, dict[str, int]]:
    activity: dict[str, dict[str, int]] = {}
    for run in _load_pipeline_runs(log_dir):
        started = run.get("started_at")
        if not isinstance(started, datetime):
            continue
        started = started.astimezone(SHANGHAI_TZ)
        if not start <= started < end:
            continue
        day = started.date().isoformat()
        target = activity.setdefault(
            day,
            {
                "pipeline_runs": 0,
                "completed_runs": 0,
                "fetch_inserted": 0,
                "failures": 0,
                "complete": False,
            },
        )
        target["pipeline_runs"] += 1
        if run.get("status") in {"done", "skip"}:
            target["completed_runs"] += 1
        fetch = run.get("fetch")
        if isinstance(fetch, dict):
            target["fetch_inserted"] += int(fetch.get("inserted") or 0)
        path = run.get("path")
        if isinstance(path, str):
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            target["failures"] += len(METERING_FAILURE_RE.findall(text))
        target["complete"] = bool(
            target["pipeline_runs"]
            and target["completed_runs"] == target["pipeline_runs"]
        )
    return activity


def _window_metering(
    activity: dict[str, dict[str, Any]], start: datetime, end: datetime
) -> dict[str, Any]:
    failures = 0
    expected_days = 0
    observed_days = 0
    incomplete_days = 0
    if end <= start:
        covered_days: list[str] = []
    else:
        first_day = start.astimezone(SHANGHAI_TZ).date()
        last_day = (end.astimezone(SHANGHAI_TZ) - timedelta(microseconds=1)).date()
        covered_days = []
        cursor = first_day
        while cursor <= last_day:
            covered_days.append(cursor.isoformat())
            cursor += timedelta(days=1)
    for day in covered_days:
        expected_days += 1
        row = activity.get(day)
        if row is not None:
            observed_days += 1
            failures += int(row.get("failures") or 0)
            if not bool(row.get("complete")):
                incomplete_days += 1
    return {
        "complete": (
            expected_days > 0
            and observed_days == expected_days
            and incomplete_days == 0
            and failures == 0
        ),
        "failure_count": failures,
        "expected_days": expected_days,
        "observed_days": observed_days,
        "incomplete_days": incomplete_days,
    }


def _usage_calls_by_day(
    rows: Sequence[Any], start: datetime, end: datetime
) -> dict[str, int]:
    calls: dict[str, int] = defaultdict(int)
    for row in rows:
        created = _parse_dt(row["created_at"])
        if created is not None and start <= created < end:
            calls[created.date().isoformat()] += 1
    return dict(calls)


def _processing_exposure_days(
    activity: dict[str, dict[str, Any]],
    fetched_counts: dict[str, int],
    wechat_fetched_counts: dict[str, int],
    processed_counts: Mapping[str, int | Mapping[str, int]],
    calls_by_day: dict[str, int],
    *,
    start: datetime,
    end: datetime,
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    gaps: list[str] = []
    unknown: list[str] = []
    gap_stages: dict[str, list[str]] = {}
    cursor = start
    while cursor < end:
        day = cursor.date().isoformat()
        observed = activity.get(day)
        fetched = fetched_counts.get(day, 0)
        processed = _stage_counts(processed_counts.get(day, 0))
        calls = calls_by_day.get(day, 0)
        stalled_stages = _stalled_stages(
            fetched=fetched,
            wechat_fetched=wechat_fetched_counts.get(day, 0),
            processed=processed,
        )
        if not fetched and not any(processed.values()) and not calls:
            cursor += timedelta(days=1)
            continue
        if observed is not None and int(observed.get("failures") or 0) > 0:
            unknown.append(day)
        elif stalled_stages:
            gaps.append(day)
            gap_stages[day] = stalled_stages
        cursor += timedelta(days=1)
    return gaps, unknown, gap_stages


def _stage_counts(raw: int | Mapping[str, int]) -> dict[str, int]:
    if isinstance(raw, Mapping):
        return {
            "prefilter": int(raw.get("prefilter", 0)),
            "prefilter_candidates": int(raw.get("prefilter_candidates", 0)),
            "score": int(raw.get("score", 0)),
            "enrich": int(raw.get("enrich", 0)),
            "interpret": int(raw.get("interpret", 0)),
        }
    count = int(raw)
    return {
        "prefilter": count,
        "prefilter_candidates": count,
        "score": count,
        "enrich": count,
        "interpret": count,
    }


def _stalled_stages(
    *, fetched: int, wechat_fetched: int, processed: dict[str, int]
) -> list[str]:
    stalled: list[str] = []
    if fetched > 0 and processed["prefilter"] == 0:
        stalled.append("prefilter")
    if processed["prefilter_candidates"] > 0:
        if processed["score"] == 0:
            stalled.append("score")
        if processed["enrich"] == 0:
            stalled.append("enrich")
    if wechat_fetched > 0 and processed["interpret"] == 0:
        stalled.append("interpret")
    return stalled


def _complete_daily_series(
    raw_daily: object,
    *,
    start: datetime,
    end: datetime,
    activity: dict[str, dict[str, Any]],
    fetched_counts: dict[str, int],
    wechat_fetched_counts: dict[str, int],
    processed_counts: Mapping[str, int | Mapping[str, int]],
) -> list[dict[str, Any]]:
    existing = {
        str(row["date"]): dict(row)
        for row in raw_daily
        if isinstance(row, dict) and row.get("date")
    } if isinstance(raw_daily, list) else {}
    completed: list[dict[str, Any]] = []
    cursor = start
    while cursor < end:
        day = cursor.date().isoformat()
        row = existing.get(day, {"date": day, "calls": 0, "known_cost_usd": 0.0, "known_cost_cny": 0.0})
        observed = activity.get(day, {})
        runs = int(observed.get("pipeline_runs", 0))
        inserted = int(observed.get("fetch_inserted", 0))
        fetched = int(fetched_counts.get(day, 0))
        stage_counts = _stage_counts(processed_counts.get(day, 0))
        processed = sum(
            stage_counts[stage] for stage in ("prefilter", "score", "enrich", "interpret")
        )
        stalled_stages = _stalled_stages(
            fetched=fetched,
            wechat_fetched=wechat_fetched_counts.get(day, 0),
            processed=stage_counts,
        )
        failures = int(observed.get("failures", 0))
        complete = bool(observed.get("complete"))
        calls = int(row.get("calls") or 0)
        if failures:
            state = "metering_incomplete"
        elif stalled_stages:
            state = "processing_stall"
        elif calls:
            state = "active"
        elif fetched or processed:
            state = "measurement_unknown"
        elif runs:
            state = "no_llm_activity"
        else:
            state = "no_pipeline_evidence"
        row.update(
            pipeline_runs=runs,
            fetch_inserted=inserted if runs else fetched,
            fetched_items=fetched,
            wechat_fetched_items=int(wechat_fetched_counts.get(day, 0)),
            durable_processed=processed,
            durable_stage_successes=stage_counts,
            stalled_stages=stalled_stages,
            metering_failure_count=failures,
            metering_complete=complete,
            activity_state=state,
        )
        completed.append(row)
        cursor += timedelta(days=1)
    return completed


def _coverage_text(coverage: dict[str, Any]) -> str:
    ratio = coverage.get("ratio")
    return "无数据" if ratio in (None, 0, 0.0) else f"{float(ratio):.1%}"


def format_cost_report(report: dict[str, Any], *, sample_label: str | None = None) -> str:
    totals = report["totals"]
    window = report["window"]
    nominal_share = report.get("nominal_share")
    qualification = (
        "其中 nominal 目录价估算约 "
        f"¥{float(totals.get('nominal_cost_usd') or 0) * float(report['exchange_rate_usd_cny']):.2f}"
        f"（{float(nominal_share):.1%}），并非账单实付"
        if nominal_share is not None
        else "当前没有可定价金额；这不是零成本"
    )
    cache_hit_rate = totals["cache_hit_rate"]
    cache_hit_text = "无数据" if cache_hit_rate is None else f"{float(cache_hit_rate):.1%}"
    title = "【AI Radar】LLM 每周成本报表"
    if sample_label:
        title += f"（样例：{sample_label}）"
    lines = [
        title,
        f"窗口：{window['start']} → {window['end']}（{window.get('kind', 'rolling')}）",
        f"结论：已知成本约 ¥{float(totals['known_cost_cny']):.2f}；{qualification}。",
        (
            f"用量：{totals['calls']} 次，输入 {totals['input_tokens']} tokens，"
            f"输出 {totals['output_tokens']} tokens；cache 测量覆盖 {_coverage_text(totals['cache_split_coverage'])}，"
            f"命中率 {cache_hit_text}。"
        ),
    ]
    metering = report.get("metering", {})
    if isinstance(metering, dict):
        failures = int(metering.get("failure_count") or 0)
        if failures:
            lines.append(f"计量完整性：至少 {failures} 次写入失败，已知成本可能低估。")
        elif not bool(metering.get("complete")):
            lines.append("计量完整性：pipeline 日志覆盖不完整，无法证明已知成本没有漏记。")
        else:
            lines.append("计量完整性：窗口内 pipeline 日志完整，未见计量写入失败。")
    comparison = report["comparison"]
    if comparison["available"]:
        delta = comparison["known_cost_change_ratio"]
        delta_text = "前窗为零，无法计算百分比" if delta is None else f"较前一等长窗口 {float(delta):+.1%}"
        qualifiers = ["两窗均按当前费率、cache 未命中重算", "处理暴露由持久数据确认"]
        expected_log_days = int(comparison.get("metering_log_expected_days") or 0)
        observed_log_days = int(comparison.get("metering_log_observed_days") or 0)
        if observed_log_days < expected_log_days:
            qualifiers.append(
                f"计量失败日志覆盖 {observed_log_days}/{expected_log_days} 日，"
                "未覆盖日的漏记风险未完全排除"
            )
        lines.append(f"环比：{delta_text}（{'；'.join(qualifiers)}）。")
    elif comparison.get("reason") == "processing_exposure_unknown":
        previous_unknown = ",".join(comparison.get("previous_processing_unknown_days", []))
        current_unknown = ",".join(comparison.get("processing_unknown_days", []))
        scope = []
        if current_unknown:
            scope.append(f"本窗 {current_unknown} 的停滞状态未知")
        if previous_unknown:
            scope.append(f"前窗停滞状态未知（{previous_unknown}）")
        lines.append("环比：不可用——" + "；".join(scope) + "，不能证明两窗暴露量相等。")
    elif comparison.get("reason") == "processing_exposure_gap":
        current_gaps = ",".join(comparison.get("processing_gap_days", []))
        previous_gaps = ",".join(comparison.get("previous_processing_gap_days", []))
        scope = []
        if current_gaps:
            scope.append(f"本窗含处理停滞日 {current_gaps}")
        if previous_gaps:
            scope.append(f"前窗含处理停滞日 {previous_gaps}")
        lines.append(
            "环比：不可用——" + "；".join(scope)
            + "，避免把停滞与积压回补误报成成本变化。"
        )
    else:
        lines.append("环比：不可用——前窗或本窗没有可定价调用。")
    stages = report.get("stage_costs", [])
    if stages:
        lines.append(
            "阶段：" + "；".join(
                f"{row['stage']} ¥{float(row['known_cost_cny']):.2f}/{row['calls']} 次"
                for row in stages
            )
        )
    groups = report.get("cost_groups", [])[:3]
    lines.append(
        "Top 驱动：" + (
            "；".join(
                f"{row['provider']}/{row['model']} ¥{float(row['known_cost_cny']):.2f}/{row['calls']} 次"
                for row in groups
            )
            if groups else "无调用"
        )
    )
    interpret = next((row for row in stages if row["stage"] == "interpret"), None)
    if interpret and interpret.get("comparison", {}).get("current_known_cost_per_call_cny") is not None:
        interpret_comparison = interpret.get("comparison", {})
        interpret_text = (
            "cache 中性目录价估算 "
            f"¥{float(interpret_comparison['current_known_cost_per_call_cny']):.4f}/次"
        )
        if interpret_comparison.get("available"):
            previous = interpret_comparison.get("previous_known_cost_per_call_cny")
            delta = interpret_comparison.get("known_cost_per_call_change_ratio")
            delta_text = "前窗为零，百分比不可用" if delta is None else f"{float(delta):+.1%}"
            interpret_text += (
                f"；前一等长窗口 ¥{float(previous):.4f}（{delta_text}；"
                "两窗均按 cache 未命中重算）"
            )
        else:
            interpret_text += "；前窗参考不可用（前窗无 interpret 调用）"
        interpret_text += (
            f"（本窗 {interpret['known_calls']}/{interpret['calls']} 次有价格的成功调用）"
        )
    else:
        interpret_text = "无成功调用或无可定价金额"
    lines.append("单篇解读：" + interpret_text)
    abnormal = [row for row in report.get("daily", []) if row.get("activity_state") != "active"]
    actionable = [
        row for row in abnormal
        if row.get("activity_state") in {"processing_stall", "measurement_unknown", "metering_incomplete"}
    ]
    if actionable:
        lines.insert(4, "异常：" + "；".join(_format_abnormal_summary(row) for row in actionable))
    daily = report.get("daily", [])
    lines.append(
        "日序列：" + (
            "；".join(_format_daily_row(row) for row in daily)
            if daily else "无调用"
        )
    )
    unpriced = report.get("unpriced", [])
    lines.append(
        "未定价：" + (
            "；".join(f"{row['provider']}/{row['model']} {row['calls']} 次" for row in unpriced)
            if unpriced else "无"
        )
    )
    freshness = report.get("pricing_freshness", [])
    lines.append(
        f"价格口径：状态 {','.join(freshness) if freshness else '无已报价模型'}；"
        f"USD/CNY={float(report['exchange_rate_usd_cny']):.4f}。stale/due-review 需人工复核；unpriced 不计入金额。"
    )
    lines.append("下一步：先看 Top 驱动组；若出现未定价或 stale/due-review，先核价，再据金额做资源决策。")
    return "\n".join(lines)


def _format_daily_row(row: dict[str, Any]) -> str:
    base = f"{row['date']} ¥{float(row['known_cost_cny']):.2f}"
    state = row.get("activity_state")
    if state == "processing_stall":
        stalled = "/".join(str(stage) for stage in row.get("stalled_stages", []))
        if int(row.get("calls") or 0):
            return (
                f"{base}（pipeline {row['pipeline_runs']} 轮，fetch 新增 {row['fetch_inserted']}，"
                f"LLM {row['calls']} 次：{stalled or '部分 stage'} 成功产出停滞）"
            )
        return (
            f"{base}（pipeline {row['pipeline_runs']} 轮，fetch 新增 {row['fetch_inserted']}，"
            "LLM 0 次：处理停滞）"
        )
    if state == "measurement_unknown":
        return f"{base}（入库 {row['fetched_items']} 篇、LLM 0 次：计量/日志不完整，状态未知）"
    if state == "metering_incomplete":
        return f"{base}（至少 {row['metering_failure_count']} 次计量写入失败，金额可能低估）"
    if state == "no_llm_activity":
        return (
            f"{base}（pipeline {row['pipeline_runs']} 轮，fetch 新增 0，LLM 0 次）"
        )
    if state == "no_pipeline_evidence" and not int(row.get("calls") or 0):
        return f"{base}（无 pipeline 记录，完整性未确认）"
    return base


def _format_abnormal_summary(row: dict[str, Any]) -> str:
    state = row.get("activity_state")
    if state == "processing_stall":
        stalled = "/".join(str(stage) for stage in row.get("stalled_stages", []))
        if int(row.get("calls") or 0):
            return (
                f"{row['date']} {stalled or '部分 stage'} 成功产出停滞"
                f"（pipeline {row['pipeline_runs']} 轮，fetch 新增 {row['fetch_inserted']}，"
                f"LLM {row['calls']} 次）"
            )
        return (
            f"{row['date']} 处理停滞（pipeline {row['pipeline_runs']} 轮，"
            f"fetch 新增 {row['fetch_inserted']}，LLM 0 次）"
        )
    if state == "measurement_unknown":
        return f"{row['date']} 状态未知（入库 {row['fetched_items']} 篇，计量/日志不完整）"
    return (
        f"{row['date']} 计量不完整（至少 {row['metering_failure_count']} 次写入失败，"
        "金额可能低估）"
    )


def deliver_cost_report(text: str) -> dict[str, object]:
    try:
        completed = subprocess.run(["im-notify", text], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "sent": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def evaluate_a6_cost(
    rows: Sequence[Any],
    *,
    now: datetime,
    catalog: PricingCatalog | None = None,
    daily_floor_cny: float = 20.0,
    spike_multiplier: float = 3.0,
    page_floor_cny: float = 100.0,
    page_multiplier: float = 6.0,
    metering_complete: bool = True,
    metering_failure_count: int = 0,
) -> dict[str, Any]:
    active_catalog = catalog or get_pricing()
    rate = usd_cny_rate()
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    current_start = current - timedelta(hours=24)

    def summarize(start: datetime, end: datetime, *, left_open: bool) -> dict[str, Any]:
        cost_usd = 0.0
        calls = 0
        split = 0
        unpriced = 0
        groups: dict[tuple[str, str], float] = defaultdict(float)
        for raw in rows:
            created = _parse_dt(raw["created_at"])
            if created is None:
                continue
            created_utc = created.astimezone(UTC)
            inside = start < created_utc <= end if left_open else start <= created_utc < end
            if not inside:
                continue
            calls += 1
            row = dict(raw)
            row["created_at"] = current.isoformat()
            row["cached_input_tokens"] = 0
            derived = derive_cost_usd(row, catalog=active_catalog)
            split += 1
            if derived.cost_usd is None:
                unpriced += 1
                continue
            cost_usd += derived.cost_usd
            groups[(str(raw["provider"]), str(raw["model"]))] += derived.cost_usd * rate
        top = max(groups.items(), key=lambda item: item[1], default=None)
        return {
            "known_cost_cny": round(cost_usd * rate, 6),
            "coverage": {"calls_with_split": split, "calls_total": calls},
            "unpriced_calls": unpriced,
            "top_driver": (
                {"provider": top[0][0], "model": top[0][1], "known_cost_cny": round(top[1], 6)}
                if top else None
            ),
        }

    current_summary = summarize(current_start, current, left_open=True)
    eligible: list[float] = []
    for offset in range(14, 0, -1):
        day_start = today - timedelta(days=offset)
        day = summarize(day_start, day_start + timedelta(days=1), left_open=False)
        eligible.append(float(day["known_cost_cny"]))
    median = statistics.median(eligible) if len(eligible) >= 3 else None
    threshold = max(daily_floor_cny, spike_multiplier * median) if median is not None else None
    page_threshold = max(page_floor_cny, page_multiplier * median) if median is not None else None
    evaluable = median is not None and metering_complete and metering_failure_count == 0
    return {
        **current_summary,
        "evaluable": evaluable,
        "baseline_days": len(eligible),
        "excluded_coverage_days": 0,
        "baseline_median_cny": round(median, 6) if median is not None else None,
        "threshold_cny": round(threshold, 6) if threshold is not None else None,
        "page_threshold_cny": round(page_threshold, 6) if page_threshold is not None else None,
        "firing": evaluable and threshold is not None and current_summary["known_cost_cny"] > threshold,
        "pricing_freshness": active_catalog.freshness,
        "cohort": "evaluation-time priced+nominal; cache all-miss",
        "cache_basis": "all-miss",
        "metering_complete": metering_complete,
        "metering_failure_count": metering_failure_count,
    }


def compact_branch_samples(report: dict[str, Any]) -> dict[str, str]:
    samples: dict[str, str] = {}
    for label in ("nominal", "unpriced", "stale", "cache coverage=0"):
        clone = json.loads(json.dumps(report))
        if label == "nominal":
            clone["nominal_share"] = 0.876
            clone["totals"]["nominal_cost_usd"] = (
                float(clone["totals"]["known_cost_usd"]) * 0.876
            )
        elif label == "unpriced":
            clone["unpriced"] = [{"provider": "example", "model": "unknown-model", "calls": 3}]
        elif label == "stale":
            clone["pricing_freshness"] = ["stale"]
        else:
            clone["totals"]["cache_split_coverage"] = {"calls_with_split": 0, "calls_total": 12, "ratio": 0.0}
            clone["totals"]["cache_hit_rate"] = None
        full_lines = format_cost_report(clone, sample_label=label).splitlines()
        prefixes: tuple[str, ...]
        if label == "nominal":
            prefixes = ("【", "结论：", "价格口径：", "下一步：")
        elif label == "unpriced":
            prefixes = ("【", "结论：", "未定价：", "价格口径：", "下一步：")
        elif label == "stale":
            prefixes = ("【", "结论：", "价格口径：", "下一步：")
        else:
            prefixes = ("【", "用量：", "环比：", "价格口径：")
        samples[label] = "\n".join(
            line for prefix in prefixes for line in full_lines if line.startswith(prefix)
        )
    return samples
