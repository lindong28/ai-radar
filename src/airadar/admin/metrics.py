from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import db
from .access_log import aggregate_access_log, parse_access_log_line
from .thresholds import ALERT_THRESHOLDS

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
STAGE_ORDER = ("fetch", "prefilter", "scoring", "enrich", "curate")
DB_STAGES = ("prefilter", "scoring", "enrich")
EVAL_P95_WINDOW = timedelta(hours=2)
# How far ahead of "now" a fetch round's completed_at may sit before the round
# is treated as unusable evidence (clock skew tolerance).
FUTURE_COMPLETED_AT_TOLERANCE_MINUTES = 5
LOG_STAGE_TO_DASHBOARD = {"score": "scoring"}
PIPELINE_FILE_RE = re.compile(r"pipeline-(?P<stamp>\d{8}-\d{6})\.log$")
STAGE_EVENT_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+===\s+(?P<stage>[a-z_]+)\s+(?P<event>START|OK|FAIL)(?:\s+\(exit\s+\d+\))?\s+==="
)
FETCH_OK_RE = re.compile(r"^OK\s+(?P<source>\S+)\s+fetched=(?P<fetched>\d+)\s+inserted=(?P<inserted>\d+)")
FETCH_FAIL_RE = re.compile(r"^FAIL\s+(?P<source>\S+)\s+(?P<error>.+)$")
FETCH_HTTP_STATUS_RE = re.compile(r"Client error '(?P<status>\d{3})")
FETCH_SUMMARY_RE = re.compile(
    r"^===\s+attempted=(?P<attempted>\d+)\s+inserted=(?P<inserted>\d+)\s+failed=(?P<failed>\d+)"
)
STAGE_SUMMARY_RE = re.compile(r"^(?P<stage>prefilter|score|scoring|enrich)\s+processed=(?P<processed>\d+)[, ]+errors=(?P<errors>\d+)")
PIPELINE_DONE_RE = re.compile(
    r"^(?:\[[^\]]+\]\s+)?===\s+PIPELINE DONE\s+\(failed=(?P<failed>\d+)\)\s+==="
)
# The pid suffix disappeared when the lock moved to a kernel flock (ADR-052):
# the holder is no longer named in the message. Both spellings are accepted so
# that logs written before and after that change parse the same way. A skip run
# that fails to parse as a skip is not inert — it is counted as a real run with
# zero sources, and `latest_run` then reports attempted=0, which reads as a 0%
# fetch failure rate no matter what the actual pipeline did.
SKIP_RE = re.compile(
    r"^(?:\[[^\]]+\]\s+)?===\s+pipeline SKIP: already running(?: pid=(?P<pid>\d+))?\s+==="
)


def _parse_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(SHANGHAI_TZ)


def _today_bounds(now: datetime | None) -> tuple[datetime, datetime]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _normalize_now(now: datetime | None) -> datetime:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    return current.astimezone(SHANGHAI_TZ)


def _in_window(value: object, start: datetime, end: datetime) -> bool:
    parsed = _parse_dt(value)
    return parsed is not None and start <= parsed < end


def _percentile_ms(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if percentile == 0.5:
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return int(ordered[middle])
        return int((ordered[middle - 1] + ordered[middle]) / 2)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return int(ordered[index])


def _pipeline_start_from_path(path: Path) -> datetime | None:
    match = PIPELINE_FILE_RE.match(path.name)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI_TZ)


def _parse_pipeline_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def _dashboard_stage(stage: str) -> str:
    return LOG_STAGE_TO_DASHBOARD.get(stage, stage)


def _parse_pipeline_log(path: Path) -> dict[str, object]:
    run: dict[str, object] = {
        "path": str(path),
        "name": path.name,
        "started_at": _pipeline_start_from_path(path),
        "status": "unknown",
        "failed": None,
        "skip": False,
        "stages": {},
        "fetch": {
            "attempted": 0,
            "inserted": 0,
            "failed": 0,
            "summary_seen": False,
            "completed_at": None,
            "ok_sources": 0,
            "failed_sources": [],
            "failed_by_status": {},
            "failed_sources_by_status": {},
            "sources": [],
        },
    }
    stage_starts: dict[str, datetime] = {}
    stages: dict[str, dict[str, object]] = {}

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if skip_match := SKIP_RE.match(line):
            run["status"] = "skip"
            run["skip"] = True
            skip_pid = skip_match.group("pid")
            run["skip_pid"] = int(skip_pid) if skip_pid else None
            continue
        if done_match := PIPELINE_DONE_RE.match(line):
            run["status"] = "done"
            run["failed"] = int(done_match.group("failed"))
            continue
        if event_match := STAGE_EVENT_RE.match(line):
            stage = _dashboard_stage(event_match.group("stage"))
            event = event_match.group("event").lower()
            timestamp = _parse_pipeline_timestamp(event_match.group("ts"))
            stage_entry = stages.setdefault(stage, {"status": "unknown", "duration_ms": None})
            if event == "start":
                if timestamp is not None:
                    stage_starts[stage] = timestamp
                stage_entry["status"] = "running"
            else:
                stage_entry["status"] = "ok" if event == "ok" else "fail"
                if timestamp is not None and stage in stage_starts:
                    stage_entry["duration_ms"] = int((timestamp - stage_starts[stage]).total_seconds() * 1000)
                if stage == "fetch":
                    fetch = run["fetch"]
                    assert isinstance(fetch, dict)
                    if fetch.get("summary_seen") and timestamp is not None:
                        fetch["completed_at"] = timestamp
            continue
        if ok_match := FETCH_OK_RE.match(line):
            fetch = run["fetch"]
            assert isinstance(fetch, dict)
            source = {
                "source_id": ok_match.group("source"),
                "fetched": int(ok_match.group("fetched")),
                "inserted": int(ok_match.group("inserted")),
                "error": None,
            }
            fetch["ok_sources"] = int(fetch["ok_sources"]) + 1
            assert isinstance(fetch["sources"], list)
            fetch["sources"].append(source)
            continue
        if fail_match := FETCH_FAIL_RE.match(line):
            fetch = run["fetch"]
            assert isinstance(fetch, dict)
            error = fail_match.group("error")
            status_match = FETCH_HTTP_STATUS_RE.search(error)
            status = int(status_match.group("status")) if status_match else None
            source = {
                "source_id": fail_match.group("source"),
                "fetched": 0,
                "inserted": 0,
                "error": error,
            }
            assert isinstance(fetch["failed_sources"], list)
            assert isinstance(fetch["sources"], list)
            fetch["failed_sources"].append(fail_match.group("source"))
            fetch["sources"].append(source)
            if status is not None:
                failed_by_status = fetch["failed_by_status"]
                failed_sources_by_status = fetch["failed_sources_by_status"]
                assert isinstance(failed_by_status, dict)
                assert isinstance(failed_sources_by_status, dict)
                failed_by_status[status] = int(failed_by_status.get(status, 0)) + 1
                status_sources = failed_sources_by_status.setdefault(status, [])
                assert isinstance(status_sources, list)
                status_sources.append(fail_match.group("source"))
            continue
        if summary_match := FETCH_SUMMARY_RE.match(line):
            fetch = run["fetch"]
            assert isinstance(fetch, dict)
            fetch["attempted"] = int(summary_match.group("attempted"))
            fetch["inserted"] = int(summary_match.group("inserted"))
            fetch["failed"] = int(summary_match.group("failed"))
            fetch["summary_seen"] = True
            continue
        if stage_summary_match := STAGE_SUMMARY_RE.match(line):
            stage = _dashboard_stage(stage_summary_match.group("stage"))
            stage_entry = stages.setdefault(stage, {"status": "unknown", "duration_ms": None})
            stage_entry["processed"] = int(stage_summary_match.group("processed"))
            stage_entry["errors"] = int(stage_summary_match.group("errors"))

    run["stages"] = stages
    return run


def _load_pipeline_runs(pipeline_log_dir: Path) -> list[dict[str, object]]:
    if not pipeline_log_dir.exists():
        return []
    runs = [_parse_pipeline_log(path) for path in sorted(pipeline_log_dir.glob("pipeline-*.log"))]
    return runs


def _base_stage_metrics() -> dict[str, dict[str, object]]:
    return {
        stage: {
            "processed": 0,
            "errors": 0,
            "error_rate": 0.0,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "latest_run_status": None,
            "latest_run_duration_ms": None,
        }
        for stage in STAGE_ORDER
    }


def _evaluation_stage_metrics(
    db_path: str | Path | None,
    start: datetime,
    end: datetime,
    current: datetime,
    stage_since: datetime | None = None,
) -> dict[str, dict[str, object]]:
    daily_grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    rate_grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    p95_latencies: dict[str, list[int]] = defaultdict(list)
    effective_end = min(end, current)
    p95_start = max(start, effective_end - EVAL_P95_WINDOW)
    rate_start = stage_since if stage_since is not None else start
    with db.get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT stage, latency_ms, error, evaluated_at FROM item_evaluations ORDER BY evaluated_at"
        ).fetchall()
    for row in rows:
        evaluated_at = _parse_dt(row["evaluated_at"])
        if evaluated_at is None:
            continue
        row_dict = dict(row)
        stage = str(row["stage"])
        if start <= evaluated_at < effective_end:
            daily_grouped[stage].append(row_dict)
        if rate_start <= evaluated_at < current:
            rate_grouped[stage].append(row_dict)
        if stage in DB_STAGES and p95_start <= evaluated_at < effective_end:
            p95_latencies[stage].append(int(str(row["latency_ms"])))

    metrics: dict[str, dict[str, object]] = {}
    for stage in DB_STAGES:
        daily_rows = daily_grouped.get(stage, [])
        rate_rows = rate_grouped.get(stage, [])
        latencies = [int(str(row["latency_ms"])) for row in daily_rows]
        errors = sum(1 for row in rate_rows if row.get("error"))
        processed = len(rate_rows)
        metrics[stage] = {
            "processed": processed,
            "errors": errors,
            "error_rate": errors / processed if processed else 0.0,
            "p50_latency_ms": _percentile_ms(latencies, 0.5),
            "p95_latency_ms": _percentile_ms(p95_latencies.get(stage, []), 0.95),
        }
    return metrics


def _count_rows_in_window(db_path: str | Path | None, table: str, column: str, start: datetime, end: datetime) -> int:
    with db.get_conn(db_path) as conn:
        rows = conn.execute(f"SELECT {column} FROM {table}").fetchall()
    return sum(1 for row in rows if _in_window(row[column], start, end))


def _read_access_lines(paths: list[Path]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        if path.exists():
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def _access_lines_in_window(lines: list[str], start: datetime, end: datetime) -> list[str]:
    filtered: list[str] = []
    for line in lines:
        try:
            entry = parse_access_log_line(line)
        except ValueError:
            continue
        if entry is None:
            continue
        timestamp = _parse_dt(entry.timestamp)
        if timestamp is not None and start <= timestamp < end:
            filtered.append(line)
    return filtered


def collect_metrics(
    *,
    db_path: str | Path | None = None,
    pipeline_log_dir: str | Path | None = None,
    access_log_paths: list[str | Path] | None = None,
    now: datetime | None = None,
    stage_since: datetime | None = None,
    access_since: datetime | None = None,
    alert_state_path: str | Path | None = None,
) -> dict[str, object]:
    current = _normalize_now(now)
    normalized_stage_since = _normalize_now(stage_since) if stage_since is not None else None
    normalized_access_since = _normalize_now(access_since) if access_since is not None else None
    start, end = _today_bounds(current)
    log_dir = Path(pipeline_log_dir) if pipeline_log_dir is not None else db.PROJECT_ROOT / "logs"
    if access_log_paths is None:
        access_paths = [
            db.PROJECT_ROOT / "logs" / "serve-access.log",
            db.PROJECT_ROOT / "logs" / "serve-access.err.log",
            Path("/tmp/ai-radar-serve.log"),
        ]
    else:
        access_paths = [Path(path) for path in access_log_paths]

    access_lines = _read_access_lines(access_paths)
    if normalized_access_since is not None:
        access_lines = _access_lines_in_window(access_lines, normalized_access_since, current)
    access_summary = aggregate_access_log(access_lines)
    runs = _load_pipeline_runs(log_dir)
    latest_run = next((run for run in reversed(runs) if not run.get("skip")), runs[-1] if runs else None)
    a4_thresholds = ALERT_THRESHOLDS.get("a4", {})
    a4_thresholds = a4_thresholds if isinstance(a4_thresholds, dict) else {}
    resolve_rounds = max(1, int(a4_thresholds.get("account_resolve_rounds", 2) or 2))
    stale_limit_minutes = int(a4_thresholds.get("fetch_stale_minutes", 90) or 90)
    complete_fetches: list[dict[str, object]] = []
    completed_at_seen: set[str] = set()
    for run in reversed(runs):
        fetch_obj = run.get("fetch")
        if not isinstance(fetch_obj, dict) or not fetch_obj.get("summary_seen"):
            continue
        completed_at = fetch_obj.get("completed_at")
        if not isinstance(completed_at, datetime):
            continue
        completed_key = completed_at.isoformat()
        if completed_key in completed_at_seen:
            continue
        completed_at_seen.add(completed_key)
        complete_fetches.append(fetch_obj)
        if len(complete_fetches) == resolve_rounds:
            break

    latest_fetch: dict[str, object] | None = None
    if complete_fetches:
        completed_at = complete_fetches[0]["completed_at"]
        assert isinstance(completed_at, datetime)
        age_seconds = (current - completed_at).total_seconds()
        stale_minutes = max(0, int(age_seconds / 60))
        # A round that "completed" in the future (clock skew, malformed log)
        # is not fresh evidence either: clamping its age to 0 would let it pass
        # as brand-new until the clock catches up. Allow a small tolerance.
        future_minutes = max(0, int(-age_seconds / 60))
        stale_reason: str | None = None
        if future_minutes > FUTURE_COMPLETED_AT_TOLERANCE_MINUTES:
            stale_reason = "future_timestamp"
        elif stale_minutes > stale_limit_minutes:
            stale_reason = "expired"
        latest_fetch = {
            **complete_fetches[0],
            "stale_minutes": stale_minutes,
            "stale_limit_minutes": stale_limit_minutes,
            "stale": stale_reason is not None,
            "stale_reason": stale_reason,
        }
    recent_complete_fetches: list[dict[str, object]] = []
    for fetch in complete_fetches:
        failed_by_status = fetch.get("failed_by_status")
        recent_complete_fetches.append(
            {
                "completed_at": fetch["completed_at"],
                "attempted": int(str(fetch.get("attempted", 0))),
                "failed_by_status": dict(failed_by_status) if isinstance(failed_by_status, dict) else {},
            }
        )

    stages = _base_stage_metrics()
    for stage, values in _evaluation_stage_metrics(
        db_path,
        start,
        end,
        current,
        normalized_stage_since,
    ).items():
        stages[stage].update(values)
    if latest_run:
        latest_stages_obj = latest_run.get("stages", {})
        latest_stages = latest_stages_obj if isinstance(latest_stages_obj, dict) else {}
        for stage, values in latest_stages.items():
            if stage not in stages or not isinstance(values, dict):
                continue
            stages[stage]["latest_run_status"] = values.get("status")
            stages[stage]["latest_run_duration_ms"] = values.get("duration_ms")
            if stage in {"fetch", "curate"}:
                processed = (
                    int(str(latest_fetch.get("attempted", 0)))
                    if stage == "fetch" and latest_fetch is not None
                    else 0
                )
                errors = (
                    int(str(latest_fetch.get("failed", 0)))
                    if stage == "fetch" and latest_fetch is not None
                    else 0
                )
                stages[stage]["processed"] = processed
                stages[stage]["errors"] = errors
                stages[stage]["error_rate"] = errors / processed if processed else 0.0
                stages[stage]["p50_latency_ms"] = values.get("duration_ms")
                stages[stage]["p95_latency_ms"] = values.get("duration_ms")

    alert_path = (
        Path(alert_state_path)
        if alert_state_path is not None
        else db.PROJECT_ROOT / "data" / "alert-state.json"
    )
    alert_summary = _load_alert_summary(alert_path)
    return {
        "generated_at": current.isoformat(),
        "timezone": "Asia/Shanghai",
        "window": {
            "today_start": start.isoformat(),
            "today_end": end.isoformat(),
        },
        "users": asdict(access_summary),
        "ingestion": {
            "items_today": _count_rows_in_window(db_path, "items", "fetched_at", start, end),
            "curation_runs_today": _count_rows_in_window(db_path, "curation_runs", "created_at", start, end),
            "latest_fetch": latest_fetch,
            "recent_complete_fetches": recent_complete_fetches,
        },
        "pipeline": {
            "stages": stages,
            "latest_run": latest_run,
            "recent_runs": runs[-10:],
        },
        "alerts": alert_summary,
    }


def _load_alert_summary(path: Path) -> dict[str, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"firing": [], "degraded": ["告警状态文件不可读"]}
    if not isinstance(payload, dict):
        return {"firing": [], "degraded": ["告警状态格式无效"]}
    firing: list[str] = []
    degraded: list[str] = []
    for rule_id, raw in sorted(payload.items()):
        if not isinstance(raw, dict) or not str(rule_id).startswith("A"):
            continue
        detail = str(raw.get("detail") or "无详情")
        if raw.get("state") == "firing":
            firing.append(f"{rule_id} {detail}")
        elif raw.get("evaluation_state") in {"degraded", "in_progress"}:
            degraded.append(f"{rule_id} {detail}")
    return {"firing": firing, "degraded": degraded}
