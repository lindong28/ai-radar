from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import db
from .access_log import aggregate_access_log

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
STAGE_ORDER = ("fetch", "prefilter", "scoring", "enrich", "curate")
DB_STAGES = ("prefilter", "scoring", "enrich")
EVAL_P95_WINDOW = timedelta(hours=2)
LOG_STAGE_TO_DASHBOARD = {"score": "scoring"}
PIPELINE_FILE_RE = re.compile(r"pipeline-(?P<stamp>\d{8}-\d{6})\.log$")
STAGE_EVENT_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+===\s+(?P<stage>[a-z_]+)\s+(?P<event>START|OK|FAIL)(?:\s+\(exit\s+\d+\))?\s+==="
)
FETCH_OK_RE = re.compile(r"^OK\s+(?P<source>\S+)\s+fetched=(?P<fetched>\d+)\s+inserted=(?P<inserted>\d+)")
FETCH_FAIL_RE = re.compile(r"^FAIL\s+(?P<source>\S+)\s+(?P<error>.+)$")
FETCH_SUMMARY_RE = re.compile(
    r"^===\s+attempted=(?P<attempted>\d+)\s+inserted=(?P<inserted>\d+)\s+failed=(?P<failed>\d+)"
)
STAGE_SUMMARY_RE = re.compile(r"^(?P<stage>prefilter|score|scoring|enrich)\s+processed=(?P<processed>\d+)[, ]+errors=(?P<errors>\d+)")
PIPELINE_DONE_RE = re.compile(
    r"^(?:\[[^\]]+\]\s+)?===\s+PIPELINE DONE\s+\(failed=(?P<failed>\d+)\)\s+==="
)
SKIP_RE = re.compile(r"^(?:\[[^\]]+\]\s+)?===\s+pipeline SKIP: already running pid=(?P<pid>\d+)\s+===")


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
    parsed = _parse_dt(value)
    if parsed is not None:
        return parsed
    try:
        return datetime.fromisoformat(value).replace(tzinfo=SHANGHAI_TZ)
    except ValueError:
        return None


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
            "ok_sources": 0,
            "failed_sources": [],
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
            run["skip_pid"] = int(skip_match.group("pid"))
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
            source = {
                "source_id": fail_match.group("source"),
                "fetched": 0,
                "inserted": 0,
                "error": fail_match.group("error"),
            }
            assert isinstance(fetch["failed_sources"], list)
            assert isinstance(fetch["sources"], list)
            fetch["failed_sources"].append(fail_match.group("source"))
            fetch["sources"].append(source)
            continue
        if summary_match := FETCH_SUMMARY_RE.match(line):
            fetch = run["fetch"]
            assert isinstance(fetch, dict)
            fetch["attempted"] = int(summary_match.group("attempted"))
            fetch["inserted"] = int(summary_match.group("inserted"))
            fetch["failed"] = int(summary_match.group("failed"))
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
            "cost_usd": 0.0,
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
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    p95_latencies: dict[str, list[int]] = defaultdict(list)
    effective_end = min(end, current)
    p95_start = max(start, effective_end - EVAL_P95_WINDOW)
    with db.get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT stage, latency_ms, cost_usd, error, evaluated_at FROM item_evaluations ORDER BY evaluated_at"
        ).fetchall()
    for row in rows:
        evaluated_at = _parse_dt(row["evaluated_at"])
        if evaluated_at is None or not start <= evaluated_at < effective_end:
            continue
        row_dict = dict(row)
        stage = str(row["stage"])
        grouped[stage].append(row_dict)
        if stage in DB_STAGES and p95_start <= evaluated_at < effective_end:
            p95_latencies[stage].append(int(str(row["latency_ms"])))

    metrics: dict[str, dict[str, object]] = {}
    for stage in DB_STAGES:
        rows_for_stage = grouped.get(stage, [])
        latencies = [int(str(row["latency_ms"])) for row in rows_for_stage]
        errors = sum(1 for row in rows_for_stage if row.get("error"))
        processed = len(rows_for_stage)
        cost_total = 0.0
        for row in rows_for_stage:
            cost = row.get("cost_usd")
            cost_total += float(str(cost)) if cost is not None else 0.0
        metrics[stage] = {
            "processed": processed,
            "errors": errors,
            "error_rate": errors / processed if processed else 0.0,
            "p50_latency_ms": _percentile_ms(latencies, 0.5),
            "p95_latency_ms": _percentile_ms(p95_latencies.get(stage, []), 0.95),
            "cost_usd": round(cost_total, 6),
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


def collect_metrics(
    *,
    db_path: str | Path | None = None,
    pipeline_log_dir: str | Path | None = None,
    access_log_paths: list[str | Path] | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    current = _normalize_now(now)
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

    access_summary = aggregate_access_log(_read_access_lines(access_paths))
    runs = _load_pipeline_runs(log_dir)
    latest_run = next((run for run in reversed(runs) if not run.get("skip")), runs[-1] if runs else None)
    default_fetch: dict[str, object] = {
        "attempted": 0,
        "inserted": 0,
        "failed": 0,
        "ok_sources": 0,
        "failed_sources": [],
        "sources": [],
    }
    latest_fetch_obj = latest_run.get("fetch") if latest_run else default_fetch
    latest_fetch = latest_fetch_obj if isinstance(latest_fetch_obj, dict) else default_fetch

    stages = _base_stage_metrics()
    for stage, values in _evaluation_stage_metrics(db_path, start, end, current).items():
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
                processed = int(str(latest_fetch.get("attempted", 0))) if stage == "fetch" else 0
                errors = int(str(latest_fetch.get("failed", 0))) if stage == "fetch" else 0
                stages[stage]["processed"] = processed
                stages[stage]["errors"] = errors
                stages[stage]["error_rate"] = errors / processed if processed else 0.0
                stages[stage]["p50_latency_ms"] = values.get("duration_ms")
                stages[stage]["p95_latency_ms"] = values.get("duration_ms")

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
        },
        "pipeline": {
            "stages": stages,
            "latest_run": latest_run,
            "recent_runs": runs[-10:],
        },
        "alerts": {
            "firing": [],
        },
    }
