from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from .. import db
from .access_log import aggregate_access_log
from .metrics import SHANGHAI_TZ, _load_pipeline_runs, _parse_dt, _percentile_ms

UPSTREAM_ERROR_RE = re.compile(r"(endpoints failed|invalidendpoint|404|insufficient)", re.IGNORECASE)
SCHEMA_ERROR_RE = re.compile(r"schema validation failed", re.IGNORECASE)


def _round_rate(value: float) -> float:
    return round(value, 4)


def _clamp(value: float, floor: float, ceiling: float) -> float:
    return min(ceiling, max(floor, value))


def _float_value(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(str(value))


def _window_start(now: datetime | None, days: int) -> datetime:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    return current.astimezone(SHANGHAI_TZ) - timedelta(days=days)


def _is_upstream_error(value: object) -> bool:
    if not value:
        return False
    text = str(value)
    return bool(UPSTREAM_ERROR_RE.search(text)) and not SCHEMA_ERROR_RE.search(text)


def _evaluation_baselines(db_path: str | Path | None, start: datetime) -> tuple[dict[str, object], dict[str, object]]:
    stage_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    all_rows: list[dict[str, object]] = []
    with db.get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT stage, latency_ms, error, evaluated_at FROM item_evaluations ORDER BY evaluated_at"
        ).fetchall()
    for row in rows:
        parsed = _parse_dt(row["evaluated_at"])
        if parsed is None or parsed < start:
            continue
        row_dict = dict(row)
        all_rows.append(row_dict)
        stage_rows[str(row["stage"])].append(row_dict)

    upstream_errors = sum(1 for row in all_rows if _is_upstream_error(row.get("error")))
    a1: dict[str, object] = {
        "window_days": None,
        "sample_size": len(all_rows),
        "upstream_errors": upstream_errors,
        "upstream_error_rate": _round_rate(upstream_errors / len(all_rows)) if all_rows else 0.0,
    }
    stages: dict[str, dict[str, object]] = {}
    for stage in ("prefilter", "scoring", "enrich"):
        rows_for_stage = stage_rows.get(stage, [])
        latencies = [int(str(row["latency_ms"])) for row in rows_for_stage]
        errors = sum(1 for row in rows_for_stage if row.get("error"))
        processed = len(rows_for_stage)
        stages[stage] = {
            "processed": processed,
            "errors": errors,
            "error_rate": _round_rate(errors / processed) if processed else 0.0,
            "p95_latency_ms": _percentile_ms(latencies, 0.95),
        }
    return a1, {"stages": stages}


def _access_baseline(access_log_paths: list[Path]) -> dict[str, object]:
    lines: list[str] = []
    for path in access_log_paths:
        if path.exists():
            lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    summary = aggregate_access_log(lines)
    server_errors = sum(count for status, count in summary.status_counts.items() if 500 <= status <= 599)
    return {
        "time_basis": "all_available_access_log_lines",
        "pv": summary.pv,
        "uv": summary.uv,
        "server_errors": server_errors,
        "server_error_rate": _round_rate(server_errors / summary.pv) if summary.pv else 0.0,
    }


def _pipeline_baseline(pipeline_log_dir: Path, start: datetime) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    for run in _load_pipeline_runs(pipeline_log_dir):
        started_at = run.get("started_at")
        if isinstance(started_at, datetime) and started_at >= start and not run.get("skip"):
            runs.append(run)
    fetch_ratios: list[float] = []
    inserted_by_day: dict[str, int] = defaultdict(int)
    successful_runs = 0
    for run in runs:
        if run.get("status") == "done":
            successful_runs += 1
        fetch = run.get("fetch")
        if not isinstance(fetch, dict):
            continue
        attempted = int(str(fetch.get("attempted", 0)))
        failed = int(str(fetch.get("failed", 0)))
        inserted = int(str(fetch.get("inserted", 0)))
        if attempted:
            fetch_ratios.append(failed / attempted)
        started_at = run.get("started_at")
        if isinstance(started_at, datetime):
            inserted_by_day[started_at.date().isoformat()] += inserted

    daily_inserted_values = list(inserted_by_day.values())
    avg_inserted = sum(daily_inserted_values) / len(daily_inserted_values) if daily_inserted_values else 0.0
    return {
        "runs": len(runs),
        "successful_runs": successful_runs,
        "fetch_failed_ratio_avg": _round_rate(sum(fetch_ratios) / len(fetch_ratios)) if fetch_ratios else 0.0,
        "fetch_failed_ratio_p95": _round_rate(_percentile_float(fetch_ratios, 0.95)) if fetch_ratios else 0.0,
        "daily_inserted_avg": round(avg_inserted, 2),
        "daily_inserted_days": len(daily_inserted_values),
    }


def _percentile_float(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * percentile + 0.999999) - 1)))
    return ordered[index]


def _thresholds_from_baselines(
    a1: dict[str, object],
    a2: dict[str, object],
    a3: dict[str, object],
    a4: dict[str, object],
) -> dict[str, object]:
    stages_obj = a2.get("stages")
    stages = cast(dict[str, object], stages_obj) if isinstance(stages_obj, dict) else {}
    stage_error_rates: dict[str, float] = {}
    stage_p95_latency_ms: dict[str, int | None] = {}
    for stage, values in stages.items():
        if not isinstance(values, dict):
            continue
        baseline_error_rate = _float_value(values.get("error_rate"))
        p95 = values.get("p95_latency_ms")
        stage_error_rates[str(stage)] = _round_rate(_clamp(baseline_error_rate * 3, 0.3, 0.95))
        stage_p95_latency_ms[str(stage)] = int(p95) * 3 if p95 is not None else None

    return {
        "a1": {
            "window_minutes": 15,
            "min_samples": 5,
            "upstream_error_rate": _round_rate(_clamp(_float_value(a1.get("upstream_error_rate")) * 2, 0.5, 0.95)),
        },
        "a2": {
            "window_minutes": 15,
            "stage_error_rate": stage_error_rates,
            "stage_p95_latency_ms": stage_p95_latency_ms,
            "latency_multiplier": 3.0,
            "no_success_minutes": 120,
        },
        "a3": {
            "window_minutes": 15,
            "server_error_rate": _round_rate(_clamp(_float_value(a3.get("server_error_rate")) * 3, 0.05, 0.5)),
            "healthz_timeout_seconds": 2.0,
            "healthz_consecutive_failures": 2,
        },
        "a4": {
            "fetch_failed_ratio": _round_rate(
                _clamp(_float_value(a4.get("fetch_failed_ratio_avg")) * 3, 0.4, 0.95)
            ),
            "daily_inserted_floor": int(_float_value(a4.get("daily_inserted_avg")) * 0.3),
        },
    }


def calibrate_thresholds(
    *,
    db_path: str | Path | None = None,
    pipeline_log_dir: str | Path | None = None,
    access_log_paths: list[str | Path] | None = None,
    now: datetime | None = None,
    days: int = 7,
) -> dict[str, object]:
    start = _window_start(now, days)
    log_dir = Path(pipeline_log_dir) if pipeline_log_dir is not None else db.PROJECT_ROOT / "logs"
    if access_log_paths is None:
        access_paths = [db.PROJECT_ROOT / "logs" / "serve-access.log", Path("/tmp/ai-radar-serve.log")]
    else:
        access_paths = [Path(path) for path in access_log_paths]

    a1, a2 = _evaluation_baselines(db_path, start)
    a1["window_days"] = days
    a3 = _access_baseline(access_paths)
    a4 = _pipeline_baseline(log_dir, start)
    baselines = {"a1": a1, "a2": a2, "a3": a3, "a4": a4}
    return {
        "generated_at": (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ).isoformat(),
        "window_days": days,
        "baselines": baselines,
        "thresholds": _thresholds_from_baselines(a1, a2, a3, a4),
    }
