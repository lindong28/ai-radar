#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from airadar.admin.access_log import aggregate_access_log
from airadar.db import DEFAULT_DB_PATH, PROJECT_ROOT

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
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
PIPELINE_DONE_RE = re.compile(r"^(?:\[[^\]]+\]\s+)?===\s+PIPELINE DONE\s+\(failed=(?P<failed>\d+)\)\s+===")
SKIP_RE = re.compile(r"^(?:\[[^\]]+\]\s+)?===\s+pipeline SKIP: already running pid=(?P<pid>\d+)\s+===")
STAGE_ORDER = ("fetch", "prefilter", "scoring", "enrich", "curate")
DB_STAGES = ("prefilter", "scoring", "enrich")


def parse_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(SHANGHAI_TZ)


def percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if pct == 0.5:
        mid = len(ordered) // 2
        return ordered[mid] if len(ordered) % 2 else int((ordered[mid - 1] + ordered[mid]) / 2)
    index = max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))
    return ordered[index]


def dashboard_stage(stage: str) -> str:
    return "scoring" if stage == "score" else stage


def parse_pipeline_start(path: Path) -> datetime | None:
    match = PIPELINE_FILE_RE.match(path.name)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI_TZ)


def parse_pipeline_ts(value: str) -> datetime | None:
    parsed = parse_dt(value)
    if parsed is not None:
        return parsed
    try:
        return datetime.fromisoformat(value).replace(tzinfo=SHANGHAI_TZ)
    except ValueError:
        return None


def parse_pipeline_log(path: Path) -> dict[str, Any]:
    run: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "started_at": parse_pipeline_start(path),
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
    starts: dict[str, datetime] = {}
    stages: dict[str, dict[str, Any]] = {}
    fetch = run["fetch"]
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
            stage = dashboard_stage(event_match.group("stage"))
            event = event_match.group("event").lower()
            ts = parse_pipeline_ts(event_match.group("ts"))
            entry = stages.setdefault(stage, {"status": "unknown", "duration_ms": None})
            if event == "start":
                entry["status"] = "running"
                if ts is not None:
                    starts[stage] = ts
            else:
                entry["status"] = "ok" if event == "ok" else "fail"
                if ts is not None and stage in starts:
                    entry["duration_ms"] = int((ts - starts[stage]).total_seconds() * 1000)
            continue
        if ok_match := FETCH_OK_RE.match(line):
            source = {
                "source_id": ok_match.group("source"),
                "fetched": int(ok_match.group("fetched")),
                "inserted": int(ok_match.group("inserted")),
                "error": None,
            }
            fetch["ok_sources"] += 1
            fetch["sources"].append(source)
            continue
        if fail_match := FETCH_FAIL_RE.match(line):
            source = {
                "source_id": fail_match.group("source"),
                "fetched": 0,
                "inserted": 0,
                "error": fail_match.group("error"),
            }
            fetch["failed_sources"].append(fail_match.group("source"))
            fetch["sources"].append(source)
            continue
        if summary_match := FETCH_SUMMARY_RE.match(line):
            fetch["attempted"] = int(summary_match.group("attempted"))
            fetch["inserted"] = int(summary_match.group("inserted"))
            fetch["failed"] = int(summary_match.group("failed"))
            continue
        if stage_match := STAGE_SUMMARY_RE.match(line):
            stage = dashboard_stage(stage_match.group("stage"))
            entry = stages.setdefault(stage, {"status": "unknown", "duration_ms": None})
            entry["processed"] = int(stage_match.group("processed"))
            entry["errors"] = int(stage_match.group("errors"))
    run["stages"] = stages
    return run


def load_pipeline_runs(log_dir: Path) -> list[dict[str, Any]]:
    if not log_dir.exists():
        return []
    return [parse_pipeline_log(path) for path in sorted(log_dir.glob("pipeline-*.log"))]


def read_access_lines(paths: list[Path]) -> list[str]:
    lines: list[str] = []
    for path in paths:
        if path.exists():
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def fetch_metrics(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("admin metrics response did not contain an object payload")
    return data


def db_rows(db_path: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def in_window(value: object, start: datetime, end: datetime) -> bool:
    parsed = parse_dt(value)
    return parsed is not None and start <= parsed < end


def count_rows_in_window(db_path: Path, table: str, column: str, start: datetime, end: datetime) -> int:
    return sum(1 for row in db_rows(db_path, f"SELECT {column} FROM {table}") if in_window(row[column], start, end))


def evaluation_stage_metrics(db_path: Path, start: datetime, end: datetime) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in db_rows(db_path, "SELECT stage, latency_ms, cost_usd, error, evaluated_at FROM item_evaluations"):
        if in_window(row["evaluated_at"], start, end):
            grouped[str(row["stage"])].append(row)
    expected: dict[str, dict[str, Any]] = {}
    for stage in DB_STAGES:
        rows = grouped.get(stage, [])
        latencies = [int(row["latency_ms"]) for row in rows]
        errors = sum(1 for row in rows if row["error"])
        processed = len(rows)
        cost = round(sum(float(row["cost_usd"] or 0.0) for row in rows), 6)
        expected[stage] = {
            "processed": processed,
            "errors": errors,
            "error_rate": errors / processed if processed else 0.0,
            "p50_latency_ms": percentile(latencies, 0.5),
            "p95_latency_ms": percentile(latencies, 0.95),
            "cost_usd": cost,
        }
    return expected


def latest_non_skip_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in reversed(runs):
        if not run.get("skip"):
            return run
    return runs[-1] if runs else None


def today_pipeline_inserted(runs: list[dict[str, Any]], start: datetime, end: datetime) -> int:
    total = 0
    for run in runs:
        started_at = run.get("started_at")
        fetch = run.get("fetch")
        if isinstance(started_at, datetime) and start <= started_at < end and isinstance(fetch, dict):
            total += int(fetch.get("inserted", 0))
    return total


def normalize(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list | tuple):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize(val) for key, val in sorted(value.items())}
    return value


def diff_value(expected: Any, actual: Any) -> Any:
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return round(float(actual) - float(expected), 6)
    return 0 if normalize(expected) == normalize(actual) else {"expected": normalize(expected), "actual": normalize(actual)}


def check(name: str, expected: Any, actual: Any, failures: list[str]) -> None:
    diff = diff_value(expected, actual)
    ok = diff == 0
    if not ok:
        failures.append(name)
    print(
        f"{'PASS' if ok else 'FAIL'} {name} "
        f"expected={json.dumps(normalize(expected), ensure_ascii=False, sort_keys=True)} "
        f"actual={json.dumps(normalize(actual), ensure_ascii=False, sort_keys=True)} "
        f"diff={json.dumps(diff, ensure_ascii=False, sort_keys=True)}"
    )


def source_inserted_sum(fetch: dict[str, Any]) -> int:
    sources = fetch.get("sources", [])
    if not isinstance(sources, list):
        return 0
    return sum(int(source.get("inserted", 0)) for source in sources if isinstance(source, dict))


def emit_fetch_consistency(fetch: dict[str, Any], items_today: int, pipeline_inserted: int, failures: list[str]) -> None:
    sources = fetch.get("sources", [])
    ok_lines = sum(1 for source in sources if isinstance(source, dict) and source.get("error") in (None, ""))
    fail_lines = sum(1 for source in sources if isinstance(source, dict) and source.get("error"))
    attempted = int(fetch.get("attempted", 0))
    failed = int(fetch.get("failed", 0))
    inserted = int(fetch.get("inserted", 0))
    inserted_sum = source_inserted_sum(fetch)
    check("fetch.source_count.attempted_eq_ok_plus_fail", ok_lines + fail_lines, attempted, failures)
    check("fetch.source_count.failed_eq_fail_lines", fail_lines, failed, failures)
    check("fetch.item_count.summary_inserted_eq_source_sum", inserted_sum, inserted, failures)
    check("fetch.items_today.pipeline_inserted_le_items_today", True, pipeline_inserted <= items_today, failures)
    gap = items_today - pipeline_inserted
    print(
        "PASS fetch.items_today.non_pipeline_gap "
        f"expected=items_today({items_today})-pipeline_inserted({pipeline_inserted}) "
        f"actual={gap} diff=0 note=WeWe bridge/recovery/manual backfill upper-bound gap"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1/admin/metrics")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--pipeline-log-dir", type=Path, default=PROJECT_ROOT / "logs")
    parser.add_argument("--access-log", action="append", type=Path)
    args = parser.parse_args()

    access_paths = args.access_log or [PROJECT_ROOT / "logs" / "serve-access.log", Path("/tmp/ai-radar-serve.log")]
    access_lines_snapshot = read_access_lines(access_paths)
    actual = fetch_metrics(args.url)
    start = parse_dt(actual["window"]["today_start"])
    end = parse_dt(actual["window"]["today_end"])
    if start is None or end is None:
        raise ValueError("admin metrics response did not contain parseable window bounds")

    failures: list[str] = []
    access_summary = asdict(aggregate_access_log(access_lines_snapshot))
    actual_users = actual["users"]
    for key in (
        "pv",
        "uv",
        "raw_unique_ips",
        "filtered_ip_count",
        "bot_requests",
        "top_pages",
        "status_counts",
        "status_class_counts",
    ):
        check(f"users.{key}", access_summary[key], actual_users[key], failures)
    raw = int(access_summary["raw_unique_ips"])
    filtered = int(access_summary["uv"])
    diff = int(access_summary["filtered_ip_count"])
    check("users.uv_upper_bound.raw_ge_filtered", True, raw >= filtered, failures)
    check("users.uv_upper_bound.diff_eq_filtered_ip_count", raw - filtered, diff, failures)

    check("ingestion.items_today", count_rows_in_window(args.db, "items", "fetched_at", start, end), actual["ingestion"]["items_today"], failures)
    check(
        "ingestion.curation_runs_today",
        count_rows_in_window(args.db, "curation_runs", "created_at", start, end),
        actual["ingestion"]["curation_runs_today"],
        failures,
    )

    runs = load_pipeline_runs(args.pipeline_log_dir)
    latest = latest_non_skip_run(runs)
    expected_fetch = latest["fetch"] if latest else {"attempted": 0, "inserted": 0, "failed": 0, "ok_sources": 0, "failed_sources": [], "sources": []}
    check("ingestion.latest_fetch", expected_fetch, actual["ingestion"]["latest_fetch"], failures)
    emit_fetch_consistency(expected_fetch, int(actual["ingestion"]["items_today"]), today_pipeline_inserted(runs, start, end), failures)

    expected_db_stages = evaluation_stage_metrics(args.db, start, end)
    actual_stages = actual["pipeline"]["stages"]
    for stage, expected in expected_db_stages.items():
        for key, expected_value in expected.items():
            check(f"pipeline.stages.{stage}.{key}", expected_value, actual_stages[stage][key], failures)

    expected_base = {stage: {"latest_run_status": None, "latest_run_duration_ms": None} for stage in STAGE_ORDER}
    if latest:
        for stage, values in latest.get("stages", {}).items():
            if isinstance(values, dict) and stage in expected_base:
                expected_base[stage] = {
                    "latest_run_status": values.get("status"),
                    "latest_run_duration_ms": values.get("duration_ms"),
                }
    for stage, expected in expected_base.items():
        for key, expected_value in expected.items():
            check(f"pipeline.stages.{stage}.{key}", expected_value, actual_stages[stage][key], failures)

    if latest:
        check("pipeline.latest_run.status", latest.get("status"), actual["pipeline"]["latest_run"]["status"], failures)
        check("pipeline.latest_run.name", latest.get("name"), actual["pipeline"]["latest_run"]["name"], failures)

    if failures:
        print(f"SUMMARY fail count={len(failures)} names={','.join(failures)}")
        return 1
    print("SUMMARY pass all_admin_metric_checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
