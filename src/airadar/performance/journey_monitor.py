from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import math
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db
from ..admin.alerts import (
    DEFAULT_EVENT_PATH,
    AlertRuleResult,
    AlertSender,
    _normalize_firing_basis,
    _normalize_lifecycles,
    _normalize_severity,
    _project_lifecycles,
    reserve_alert_evaluation_sequence,
    run_alert_results_state_machine,
)
from ..pipeline_lock import DEFAULT_PIPELINE_LOCK_PATH, pipeline_lock_is_held
from .browser_probe import (
    PROBE_INFRA_FAILURE_OUTCOME,
    measure_browser_journey,
    terminate_active_browser_workers,
)

WARM_SAMPLES = 20
CONFIRMATION_WINDOWS = 3
RETENTION_DAYS = 14
SAME_HOST_SCOPE = "same-host provisional; not a regional SLO"
DEFAULT_SAMPLE_PATH = db.PROJECT_ROOT / "logs" / "performance" / "journey-samples.jsonl"
DEFAULT_ALERT_STATE_PATH = db.PROJECT_ROOT / "logs" / "performance" / "alert-state.json"
DEFAULT_EVIDENCE_DIR = db.PROJECT_ROOT / "logs" / "performance" / "evidence"
PROBE_OVERALL_TIMEOUT_SECONDS = 15 * 60
PROBE_EXTERNAL_TIMEOUT_SECONDS = 16 * 60
PROBE_EXTERNAL_KILL_AFTER_SECONDS = 5.0
PROBE_TIMEOUT_EXIT_CODE = 124
WATCHDOG_PARENT_POLL_SECONDS = 0.05
LAUNCHD_INSTALL_HINT = "./install.sh performance-probe"
_LOGGER = logging.getLogger(__name__)
INFRA_HOLD_OUTCOMES = frozenset(
    {
        PROBE_INFRA_FAILURE_OUTCOME,
        "incompatible",
    }
)


@dataclass(frozen=True, slots=True)
class JourneySpec:
    journey: str
    target: str
    p75_budget_ms: float
    p95_budget_ms: float
    timeout_seconds: float = 20.0


JOURNEY_SPECS = (
    JourneySpec("homepage.first_card", "homepage", 2000.0, 3000.0),
    JourneySpec("wechat.list.first_card", "wechat_list", 2000.0, 3000.0),
    JourneySpec("wechat.detail.readable", "wechat_detail", 2000.0, 3000.0),
    JourneySpec("wechat.pagination.settle", "wechat_pagination", 1000.0, 1500.0),
)
_SPEC_BY_JOURNEY = {spec.journey: spec for spec in JOURNEY_SPECS}


@dataclass(frozen=True, slots=True)
class JourneyMonitorRuntime:
    origin_url: str
    public_url: str
    pipeline_lock_path: Path
    db_path: Path


@dataclass(frozen=True, slots=True)
class JourneySample:
    schema_version: int
    observed_at: str
    journey: str
    target: str
    vantage: str
    provisional: bool
    load_class: str
    value_ms: float
    hard_failure: bool
    outcome: str
    request_url: str


@dataclass(frozen=True, slots=True)
class _PipelineActivitySnapshot:
    load_class: str
    generation: bytes | None
    trustworthy: bool


def classify_pipeline_load(lock_path: Path) -> str:
    held = pipeline_lock_is_held(lock_path)
    if held is None:
        return "unknown"
    return "busy" if held else "idle"


def _interval_load_class(before: str, after: str) -> str:
    return before if before == after and before in {"idle", "busy"} else "unknown"


def _pipeline_activity_path(lock_path: Path) -> Path:
    return lock_path.with_suffix(".activity")


def _read_pipeline_activity(lock_path: Path) -> _PipelineActivitySnapshot:
    marker_path = _pipeline_activity_path(lock_path)
    try:
        generation_before = marker_path.read_bytes()
    except FileNotFoundError:
        generation_before = None
    except OSError:
        return _PipelineActivitySnapshot("unknown", None, False)
    load_class = classify_pipeline_load(lock_path)
    try:
        generation_after = marker_path.read_bytes()
    except FileNotFoundError:
        generation_after = None
    except OSError:
        return _PipelineActivitySnapshot("unknown", None, False)
    return _PipelineActivitySnapshot(
        load_class if generation_before == generation_after else "unknown",
        generation_after,
        generation_before == generation_after,
    )


def _activity_interval_load_class(
    before: _PipelineActivitySnapshot,
    after: _PipelineActivitySnapshot,
) -> str:
    endpoint_class = _interval_load_class(before.load_class, after.load_class)
    if (
        endpoint_class != "idle"
        or not before.trustworthy
        or not after.trustworthy
        or before.generation != after.generation
    ):
        return "unknown"
    return "idle"


def _detail_slug(db_path: Path) -> str:
    if not db_path.exists():
        return "unavailable"
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        try:
            row = connection.execute(
                """
                SELECT wi.slug
                FROM wechat_interpretations wi
                JOIN items i ON i.id=wi.item_id
                WHERE wi.save_decision=1
                ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return "unavailable"
    return str(row[0]) if row is not None else "unavailable"


def _probe_expectation(db_path: Path, target: str, detail_slug: str) -> dict[str, object] | None:
    if not db_path.exists():
        return None
    from ..web.routes import curated_archive
    from ..web.routes.wechat import get_wechat_detail, list_wechat_items

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            if target in {"wechat_list", "wechat_pagination"}:
                page = 2 if target == "wechat_pagination" else 1
                payload = list_wechat_items(connection, page=page, limit=50)
                return {
                    "page": payload["page"],
                    "limit": 50,
                    "total": payload["total"],
                    "slugs": [str(item["slug"]) for item in payload["items"]],
                }
            if target == "wechat_detail":
                return {"item_id": get_wechat_detail(connection, detail_slug)["id"]}
            run = curated_archive._latest_run(connection)
            if run is None:
                return {"item_ids": []}
            items, _total, _page = curated_archive._compute_archive_page(
                connection,
                page=1,
                limit=40,
                normalized_category=None,
                q=None,
            )
            return {"item_ids": [str(item["id"]) for item in items[:12]]}
        finally:
            connection.close()
    except (sqlite3.Error, KeyError):
        return None


def probe_journeys(
    runtime: JourneyMonitorRuntime,
    *,
    observed_at: datetime | None = None,
) -> list[JourneySample]:
    if not runtime.origin_url:
        raise ValueError("origin_url is required for journey probes")
    observed = observed_at or datetime.now(UTC)
    observed_text = observed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    detail_slug = _detail_slug(runtime.db_path)
    samples: list[JourneySample] = []
    for vantage, base_url in (
        ("same_host_origin", runtime.origin_url),
        ("same_host_public", runtime.public_url),
    ):
        if vantage == "same_host_public" and not base_url:
            continue
        for spec in JOURNEY_SPECS:
            before = _read_pipeline_activity(runtime.pipeline_lock_path)
            if before.load_class != "idle" or not before.trustworthy:
                continue
            measurement = measure_browser_journey(
                base_url=base_url,
                target=spec.target,
                detail_slug=detail_slug,
                timeout_seconds=spec.timeout_seconds,
                expected=_probe_expectation(runtime.db_path, spec.target, detail_slug),
            )
            after = _read_pipeline_activity(runtime.pipeline_lock_path)
            if (
                _activity_interval_load_class(before, after) != "idle"
                or measurement.outcome == "skipped_overlap"
            ):
                continue
            if measurement.outcome != "observed":
                _LOGGER.error(
                    "performance probe infrastructure failure: journey=%s "
                    "vantage=%s outcome=%s reason=%s",
                    spec.journey,
                    vantage,
                    measurement.outcome,
                    measurement.incompatible_reason or "unknown",
                )
            samples.append(
                JourneySample(
                    schema_version=1,
                    observed_at=observed_text,
                    journey=spec.journey,
                    target=spec.target,
                    vantage=vantage,
                    provisional=True,
                    load_class="idle",
                    value_ms=float(measurement.value_ms),
                    hard_failure=measurement.hard_failure,
                    outcome=measurement.outcome,
                    request_url=measurement.request_url,
                )
            )
    return samples


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


@contextmanager
def _sample_store_lock(path: Path) -> Iterator[bool]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _corrupt_hold_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".corrupt-hold")


def _sample_cell_key(payload: dict[str, object]) -> str | None:
    journey = payload.get("journey")
    vantage = payload.get("vantage")
    load_class = payload.get("load_class")
    if not all(isinstance(value, str) and value for value in (journey, vantage, load_class)):
        return None
    return json.dumps([journey, vantage, load_class], separators=(",", ":"))


def _rule_cell_key(rule_id: str) -> str | None:
    parts = rule_id.split(":")
    if len(parts) != 4 or parts[0] != "PERF":
        return None
    return json.dumps(parts[1:], separators=(",", ":"))


def _load_corrupt_hold(path: Path) -> tuple[datetime | None, dict[str, datetime]]:
    hold_path = _corrupt_hold_path(path)
    if not hold_path.exists():
        return None, {}
    try:
        payload = json.loads(hold_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, {}
    if not isinstance(payload, dict):
        return None, {}
    observed_at = _parse_timestamp(payload.get("corrupt_input_observed_at"))
    raw_recovered = payload.get("recovered_cells")
    recovered: dict[str, datetime] = {}
    if isinstance(raw_recovered, dict):
        for key, value in raw_recovered.items():
            parsed = _parse_timestamp(value)
            if isinstance(key, str) and parsed is not None:
                recovered[key] = parsed
    return observed_at, recovered


def _persist_corrupt_hold(
    path: Path,
    *,
    observed_at: datetime,
    recovered_cells: dict[str, datetime] | None = None,
) -> None:
    hold_path = _corrupt_hold_path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{hold_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(
                {
                    "corrupt_input_observed_at": observed_at.astimezone(UTC).isoformat(),
                    "recovered_cells": {
                        key: value.astimezone(UTC).isoformat()
                        for key, value in sorted((recovered_cells or {}).items())
                    },
                },
                stream,
                sort_keys=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, hold_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _record_recovered_cells(
    path: Path,
    payloads: list[dict[str, object]],
    *,
    current: datetime,
) -> None:
    hold_observed_at, recovered = _load_corrupt_hold(path)
    if hold_observed_at is None:
        return
    changed = False
    fresh_cutoff = current - timedelta(minutes=1)
    for payload in payloads:
        cell_key = _sample_cell_key(payload)
        observed_at = _parse_timestamp(payload.get("observed_at"))
        if (
            cell_key is None
            or observed_at is None
            or observed_at < fresh_cutoff
            or observed_at > current
            or payload.get("outcome", "observed") != "observed"
        ):
            continue
        if cell_key not in recovered:
            recovered[cell_key] = current
            changed = True
    if changed:
        _persist_corrupt_hold(
            path,
            observed_at=hold_observed_at,
            recovered_cells=recovered,
        )


def store_samples(
    path: Path,
    samples: list[JourneySample] | list[dict[str, object]],
    *,
    now: datetime | None = None,
) -> bool | None:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = current - timedelta(days=RETENTION_DAYS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _sample_store_lock(path) as acquired:
        if not acquired:
            return None
        existing, corrupt_input = _read_samples(path)
        retained_existing = [
            payload
            for payload in existing
            if (observed := _parse_timestamp(payload.get("observed_at"))) is not None
            and cutoff <= observed <= current
        ]
        retained_new = [
            payload
            for sample in samples
            if (
                observed := _parse_timestamp(
                    (payload := asdict(sample) if isinstance(sample, JourneySample) else sample).get(
                        "observed_at"
                    )
                )
            )
            is not None
            and cutoff <= observed <= current
        ]
        if corrupt_input:
            _persist_corrupt_hold(path, observed_at=current)
        needs_compaction = corrupt_input or len(retained_existing) != len(existing)
        if needs_compaction:
            _replace_sample_file(path, [*retained_existing, *retained_new])
            if not corrupt_input and retained_new:
                _record_recovered_cells(path, retained_new, current=current)
            return corrupt_input
        if retained_new:
            encoded = "".join(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
                for payload in retained_new
            ).encode()
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
            try:
                offset = 0
                while offset < len(encoded):
                    written = os.write(descriptor, encoded[offset:])
                    if written <= 0:
                        raise OSError("short write while appending performance samples")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _record_recovered_cells(path, retained_new, current=current)
        else:
            path.touch(exist_ok=True)
    return corrupt_input


def _replace_sample_file(path: Path, payloads: list[dict[str, object]]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            for payload in payloads:
                stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class _SampleLoadResult:
    rows: list[dict[str, object]]
    corrupt_input: bool
    hold_observed_at: datetime | None
    recovered_cells: dict[str, datetime]
    lock_acquired: bool
    evaluation_sequence: int | None


def _load_samples(
    path: Path,
    *,
    now: datetime | None = None,
    reserve_sequence: Callable[[], int] | None = None,
) -> _SampleLoadResult:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _sample_store_lock(path) as acquired:
        if not acquired:
            return _SampleLoadResult([], False, None, {}, False, None)
        rows, corrupt_input = _read_samples(path)
        if corrupt_input:
            _persist_corrupt_hold(path, observed_at=now or datetime.now(UTC))
        hold_observed_at, recovered_cells = _load_corrupt_hold(path)
        evaluation_sequence = reserve_sequence() if reserve_sequence is not None else None
        return _SampleLoadResult(
            rows,
            corrupt_input,
            hold_observed_at,
            recovered_cells,
            True,
            evaluation_sequence,
        )


def _read_samples(path: Path) -> tuple[list[dict[str, object]], bool]:
    if not path.exists():
        return [], False
    rows: list[dict[str, object]] = []
    corrupt_input = False
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                corrupt_input = True
                continue
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                corrupt_input = True
    return rows, corrupt_input


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _window_violation(rows: list[dict[str, object]], spec: JourneySpec) -> tuple[bool, float, float]:
    values: list[float] = []
    for row in rows:
        value = row["value_ms"]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("validated performance sample has a non-numeric value")
        values.append(float(value))
    p75 = _nearest_rank(values, 0.75)
    p95 = _nearest_rank(values, 0.95)
    hard_failure = any(bool(row.get("hard_failure")) for row in rows)
    return hard_failure or p75 > spec.p75_budget_ms or p95 > spec.p95_budget_ms, p75, p95


def _format_milliseconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}ms"


def _with_firing_message(result: AlertRuleResult) -> AlertRuleResult:
    vantage = result.values["vantage"]
    if vantage == "same_host_public":
        impact = "同机公网路径 idle 合成探针超标，已确认公网路径退化，用户可能受影响"
    else:
        impact = "同机 origin 路径 idle 合成探针超标，已确认同视角真实退化，用户可能受影响"
    return replace(result, impact=impact, urgency="是")


def evaluate_performance_rules(
    samples: list[dict[str, object]],
    *,
    now: datetime | None = None,
) -> list[AlertRuleResult]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = current - timedelta(days=RETENTION_DAYS)
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for sample in samples:
        journey = sample.get("journey")
        vantage = sample.get("vantage")
        load_class = sample.get("load_class")
        value = sample.get("value_ms")
        observed = _parse_timestamp(sample.get("observed_at"))
        if (
            journey not in _SPEC_BY_JOURNEY
            or vantage not in {"same_host_origin", "same_host_public"}
            or load_class != "idle"
            or sample.get("outcome", "observed") != "observed"
            or observed is None
            or not cutoff <= observed <= current
            or isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            continue
        grouped[(str(journey), str(vantage), str(load_class))].append(sample)

    results_by_cell: dict[tuple[str, str, str], AlertRuleResult] = {}
    for (journey, vantage, load_class), rows in sorted(grouped.items()):
        rows.sort(
            key=lambda row: _parse_timestamp(row.get("observed_at"))
            or datetime.min.replace(tzinfo=UTC)
        )
        spec = _SPEC_BY_JOURNEY[journey]
        available_windows = max(0, len(rows) - WARM_SAMPLES + 1)
        streak = 0
        p75: float | None = None
        p95: float | None = None
        for offset in range(min(CONFIRMATION_WINDOWS, available_windows)):
            end = len(rows) - offset
            violation, window_p75, window_p95 = _window_violation(
                rows[end - WARM_SAMPLES : end],
                spec,
            )
            if offset == 0:
                p75, p95 = window_p75, window_p95
            if not violation:
                break
            streak += 1
        firing = streak >= CONFIRMATION_WINDOWS
        detail = (
            f"{journey} {vantage} {load_class}: "
            f"p75 实测 {_format_milliseconds(p75)} vs 预算 "
            f"{_format_milliseconds(spec.p75_budget_ms)}；"
            f"p95 实测 {_format_milliseconds(p95)} vs 预算 "
            f"{_format_milliseconds(spec.p95_budget_ms)}；{SAME_HOST_SCOPE}"
        )
        results_by_cell[(journey, vantage, load_class)] = AlertRuleResult(
            rule_id=f"PERF:{journey}:{vantage}:{load_class}",
            title=f"旅程性能退化 {journey}",
            firing=firing,
            detail=detail,
            action=(
                "查看 logs/performance/evidence/ 中最新证据，"
                "核对近期 idle 样本、CPU 与服务状态。"
            ),
            values={
                "journey": journey,
                "vantage": vantage,
                "load_class": load_class,
                "sample_count": len(rows),
                "warm_samples": WARM_SAMPLES,
                "advanced_window_streak": streak,
                "confirmation_windows": CONFIRMATION_WINDOWS,
                "p75_ms": p75,
                "p95_ms": p95,
                "p75_budget_ms": spec.p75_budget_ms,
                "p95_budget_ms": spec.p95_budget_ms,
                "provisional": True,
            },
            firing_basis="observed" if firing else None,
        )

    results: list[AlertRuleResult] = []
    for result in results_by_cell.values():
        results.append(_with_firing_message(result) if result.firing else result)
    return results


def _load_alert_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_retired_busy_rule(rule_id: str) -> bool:
    parts = rule_id.split(":")
    return rule_id == "PERF:rollup:busy" or (
        len(parts) == 4 and parts[0] == "PERF" and parts[-1] == "busy"
    )


def _project_previous_alert_entry(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    preferred_severity = _normalize_severity(entry.get("severity"))
    return _project_lifecycles(
        _normalize_lifecycles(entry),
        preferred_severity=preferred_severity,
    )


def _pending_firing_result(
    rule_id: str,
    entry: object,
) -> AlertRuleResult | None:
    if not isinstance(entry, dict):
        return None
    parts = rule_id.split(":")
    if len(parts) != 4 or parts[0] != "PERF" or parts[-1] != "idle":
        return None
    for severity, lifecycle in _normalize_lifecycles(entry).items():
        pending = lifecycle.get("pending_notification")
        if (
            lifecycle.get("state") != "firing"
            or not isinstance(pending, dict)
            or pending.get("event_type") != "firing"
        ):
            continue
        detail = lifecycle.get("detail")
        result = AlertRuleResult(
            rule_id=rule_id,
            title=f"旅程性能退化 {parts[1]}",
            firing=True,
            detail=detail if isinstance(detail, str) else "性能告警待投递",
            action=(
                "查看 logs/performance/evidence/ 中最新证据，"
                "核对近期 idle 样本、CPU 与服务状态。"
            ),
            values={
                "journey": parts[1],
                "vantage": parts[2],
                "load_class": parts[3],
            },
            severity=_normalize_severity(severity),
            firing_basis=_normalize_firing_basis(lifecycle.get("firing_basis")),
        )
        return _with_firing_message(result)
    return None


def _retired_busy_results(previous_state: dict[str, Any]) -> list[AlertRuleResult]:
    results: list[AlertRuleResult] = []
    for rule_id, entry in previous_state.items():
        projected = _project_previous_alert_entry(entry)
        if not _is_retired_busy_rule(rule_id) or projected is None:
            continue
        if projected.get("state") != "firing":
            continue
        results.append(
            AlertRuleResult(
                rule_id=rule_id,
                title="性能自测旧 busy 告警",
                firing=False,
                detail="idle-only 探针已启用；旧 busy/rollup 告警恢复",
                action="none",
                severity=_normalize_severity(projected.get("severity")),
            )
        )
    return results


def _diagnostics(pipeline_lock_path: Path) -> dict[str, object]:
    try:
        git_sha = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            cwd=db.PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        git_dirty = bool(
            subprocess.run(
                ["/usr/bin/git", "status", "--porcelain"],
                cwd=db.PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        git_sha = "unknown"
        git_dirty = True
    try:
        cpu_rows = subprocess.run(
            ["/bin/ps", "-A", "-o", "%cpu="],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.split()
        host_cpu: float | str = min(
            100.0,
            sum(float(value) for value in cpu_rows) / max(1, os.cpu_count() or 1),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        host_cpu = "unknown"
    return {
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "host_cpu_percent": host_cpu,
        "host_load_1m": os.getloadavg()[0] if hasattr(os, "getloadavg") else "unknown",
        "pipeline": {
            "status": classify_pipeline_load(pipeline_lock_path),
            "lock_path": str(pipeline_lock_path),
        },
    }


def _write_firing_evidence(
    *,
    result: AlertRuleResult,
    samples: list[dict[str, object]],
    evidence_dir: Path,
    pipeline_lock_path: Path,
    now: datetime,
) -> Path:
    values = result.values
    recent = [
        sample
        for sample in samples
        if sample.get("journey") == values["journey"]
        and sample.get("vantage") == values["vantage"]
        and sample.get("load_class") == "idle"
    ][-(WARM_SAMPLES + CONFIRMATION_WINDOWS - 1) :]
    identity = {
        "journey": values["journey"],
        "vantage": values["vantage"],
        "load_class": "idle",
    }
    payload = {
        "schema_version": 1,
        "created_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "rule_id": result.rule_id,
        **identity,
        "provisional": True,
        "regional_slo_claim": False,
        "recent_samples": recent,
        "diagnostics": _diagnostics(pipeline_lock_path),
    }
    evidence_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(result.rule_id.encode()).hexdigest()[:12]
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = evidence_dir / f"{stamp}-{digest}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    _prune_evidence(evidence_dir, now=now)
    return path


def _prune_evidence(evidence_dir: Path, *, now: datetime) -> None:
    cutoff = now.astimezone(UTC) - timedelta(days=RETENTION_DAYS)
    for path in evidence_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        created_at = _parse_timestamp(payload.get("created_at")) if isinstance(payload, dict) else None
        if created_at is not None and created_at < cutoff:
            path.unlink(missing_ok=True)


def run_performance_alerts(
    *,
    sample_path: Path,
    state_path: Path,
    evidence_dir: Path,
    pipeline_lock_path: Path,
    event_path: Path = DEFAULT_EVENT_PATH,
    now: datetime | None = None,
    send: AlertSender | None = None,
    enabled_vantages: frozenset[str] | None = None,
    known_corrupt_input: bool = False,
) -> dict[str, object]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = current - timedelta(days=RETENTION_DAYS)
    loaded = _load_samples(
        sample_path,
        now=current,
        reserve_sequence=lambda: reserve_alert_evaluation_sequence(
            state_path=state_path
        ),
    )
    if not loaded.lock_acquired:
        return {
            "ruleset": [],
            "sent_count": 0,
            "sent": [],
            "results": [],
            "state_path": str(state_path),
            "corrupt_input": False,
            "sample_store_skipped": True,
        }
    evaluation_sequence = loaded.evaluation_sequence
    if evaluation_sequence is None:
        raise RuntimeError("sample snapshot did not receive an evaluation sequence")
    samples = [
        sample
        for sample in loaded.rows
        if (observed := _parse_timestamp(sample.get("observed_at"))) is not None
        and cutoff <= observed <= current
    ]
    samples.sort(
        key=lambda sample: _parse_timestamp(sample.get("observed_at"))
        or datetime.min.replace(tzinfo=UTC)
    )
    if enabled_vantages is not None:
        samples = [sample for sample in samples if sample.get("vantage") in enabled_vantages]
    latest_outcomes_by_cell: dict[str, str] = {}
    for sample in samples:
        cell_key = _sample_cell_key(sample)
        if cell_key is not None:
            latest_outcomes_by_cell[cell_key] = str(
                sample.get("outcome", "observed")
            )
    infra_cells = {
        cell_key
        for cell_key, outcome in latest_outcomes_by_cell.items()
        if outcome in INFRA_HOLD_OUTCOMES
    }
    previous_state = _load_alert_state(state_path)
    relevant_cells = {
        cell_key
        for sample in samples
        if (cell_key := _sample_cell_key(sample)) is not None
    }
    relevant_cells.update(
        cell_key
        for rule_id, entry in previous_state.items()
        if (cell_key := _rule_cell_key(rule_id)) is not None
        and isinstance(entry, dict)
        and (_project_previous_alert_entry(entry) or {}).get("state") == "firing"
    )
    held_cells = {
        cell_key
        for cell_key in relevant_cells
        if loaded.hold_observed_at is not None
        and cell_key not in loaded.recovered_cells
    }
    current_corrupt_input = loaded.corrupt_input or known_corrupt_input
    evaluated_results = (
        [] if current_corrupt_input else evaluate_performance_rules(samples, now=current)
    )
    previous_observed_firing_rule_ids = {
        rule_id
        for rule_id, entry in previous_state.items()
        if (_project_previous_alert_entry(entry) or {}).get("firing_basis")
        == "observed"
    }
    results = [
        result
        for result in evaluated_results
        if (cell_key := _rule_cell_key(result.rule_id)) is None
        or (cell_key not in held_cells and cell_key not in infra_cells)
    ]
    result_rule_ids = {result.rule_id for result in results}
    for rule_id, entry in previous_state.items():
        if (
            rule_id in result_rule_ids
            or rule_id not in previous_observed_firing_rule_ids
            or (cell_key := _rule_cell_key(rule_id)) is None
            or cell_key not in infra_cells
        ):
            continue
        pending_result = _pending_firing_result(rule_id, entry)
        if pending_result is not None:
            results.append(pending_result)
            result_rule_ids.add(rule_id)
    corrupt_input = current_corrupt_input or bool(held_cells)
    if not corrupt_input:
        results.extend(_retired_busy_results(previous_state))
    known_rule_ids = {result.rule_id for result in results}
    for rule_id, entry in previous_state.items():
        if rule_id in known_rule_ids or not rule_id.startswith("PERF:"):
            continue
        projected = _project_previous_alert_entry(entry)
        if projected is None or projected.get("state") != "firing":
            continue
        parts = rule_id.split(":")
        if len(parts) != 4 or parts[-1] != "idle":
            continue
        disabled = enabled_vantages is not None and parts[2] not in enabled_vantages
        rule_cell_key = _rule_cell_key(rule_id)
        stored_firing_basis = projected.get("firing_basis")
        trusted_observed_firing = stored_firing_basis == "observed"
        if (
            not disabled
            and trusted_observed_firing
            and (
                current_corrupt_input
                or (
                    rule_cell_key is not None
                    and (
                        rule_cell_key in held_cells
                        or rule_cell_key in infra_cells
                    )
                )
            )
        ):
            continue
        detail = (
            "vantage disabled (public URL unset); auto-resolved"
            if disabled
            else (
                "unstamped performance alert retired; awaiting fresh observed samples"
                if not trusted_observed_firing
                else "no retained fresh idle samples; stale performance alert auto-resolved"
            )
        )
        results.append(
            AlertRuleResult(
                rule_id=rule_id,
                title=f"{parts[1]} {parts[2]} {parts[3]}",
                firing=False,
                detail=detail,
                action="none",
                severity=_normalize_severity(projected.get("severity")),
            )
        )
    evidenced: list[AlertRuleResult] = []
    for result in results:
        entry = previous_state.get(result.rule_id, {})
        if result.firing and (not isinstance(entry, dict) or entry.get("state") != "firing"):
            evidence_path = _write_firing_evidence(
                result=result,
                samples=samples,
                evidence_dir=evidence_dir,
                pipeline_lock_path=pipeline_lock_path,
                now=current,
            )
            result = replace(result, detail=f"{result.detail}; evidence={evidence_path}")
        evidenced.append(result)
    payload = run_alert_results_state_machine(
        evidenced,
        state_path=state_path,
        event_path=event_path,
        now=current,
        send=send,
        evaluation_sequence=evaluation_sequence,
    )
    payload["corrupt_input"] = corrupt_input
    payload["sample_store_skipped"] = False
    return payload


@contextmanager
def _probe_process_deadline() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("performance probe process deadline requires the main thread")
    previous_handler = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()

    def deadline_expired(_signum: int, _frame: object) -> None:
        terminate_active_browser_workers()
        os._exit(PROBE_TIMEOUT_EXIT_CODE)

    signal.signal(signal.SIGALRM, deadline_expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, PROBE_OVERALL_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            remaining = max(0.000001, previous_timer[0] - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


def _process_tree_pids(root_pid: int) -> set[int]:
    try:
        rows = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return {root_pid}
    parsed: list[tuple[int, int]] = []
    for row in rows:
        try:
            pid, parent = (int(value) for value in row.split())
        except (TypeError, ValueError):
            continue
        parsed.append((pid, parent))
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parsed:
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def _signal_external_probe_tree(root_pid: int, signum: int) -> None:
    for pid in sorted(_process_tree_pids(root_pid), reverse=True):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass
    try:
        os.killpg(root_pid, signum)
    except (PermissionError, ProcessLookupError):
        pass


def run_external_probe_watchdog(
    command: list[str],
    *,
    timeout_seconds: float = PROBE_EXTERNAL_TIMEOUT_SECONDS,
    kill_after_seconds: float = PROBE_EXTERNAL_KILL_AFTER_SECONDS,
) -> int:
    if not command:
        raise ValueError("external probe watchdog requires a command")
    # Deliberately sustained SIGSTOP of this watchdog and its entire child tree
    # cannot be defeated in-tree. Phase 3 operations documentation must expose
    # that monitoring limitation; ordinary child hangs/stops and parent death are
    # bounded here without reintroducing age-based lock stealing.
    child_command = [
        sys.executable,
        "-m",
        "airadar.performance.journey_monitor",
        "--watchdog-child",
        "--parent-pid",
        str(os.getpid()),
        "--",
        *command,
    ]
    process = subprocess.Popen(child_command, start_new_session=True)
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _signal_external_probe_tree(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=kill_after_seconds)
        except subprocess.TimeoutExpired:
            _signal_external_probe_tree(process.pid, signal.SIGKILL)
            process.wait()
        return PROBE_TIMEOUT_EXIT_CODE


def _enable_linux_parent_death_signal(parent_pid: int) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            return False
    except (AttributeError, OSError):
        return False
    if os.getppid() != parent_pid:
        os.kill(os.getpid(), signal.SIGKILL)
    return True


def _run_watchdog_child(command: list[str], *, parent_pid: int) -> int:
    if not command:
        raise ValueError("watchdog child requires a command")
    if _enable_linux_parent_death_signal(parent_pid):
        os.execvp(command[0], command)
        raise AssertionError("os.execvp returned unexpectedly")

    process = subprocess.Popen(command)
    while True:
        try:
            return process.wait(timeout=WATCHDOG_PARENT_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            if os.getppid() == parent_pid:
                continue
            _signal_external_probe_tree(process.pid, signal.SIGKILL)
            process.wait()
            return PROBE_TIMEOUT_EXIT_CODE


def _watchdog_main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--external-watchdog", action="store_true")
    mode.add_argument("--watchdog-child", action="store_true")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=PROBE_EXTERNAL_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--kill-after-seconds",
        type=float,
        default=PROBE_EXTERNAL_KILL_AFTER_SECONDS,
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(arguments)
    command = parsed.command[1:] if parsed.command[:1] == ["--"] else parsed.command
    if parsed.watchdog_child:
        if parsed.parent_pid is None or parsed.parent_pid <= 0:
            parser.error("--watchdog-child requires a positive --parent-pid")
        return _run_watchdog_child(command, parent_pid=parsed.parent_pid)
    if parsed.timeout_seconds <= 0 or parsed.kill_after_seconds <= 0:
        parser.error("watchdog timeouts must be positive")
    return run_external_probe_watchdog(
        command,
        timeout_seconds=parsed.timeout_seconds,
        kill_after_seconds=parsed.kill_after_seconds,
    )


def run_journey_monitor(
    *,
    origin_url: str,
    public_url: str,
    sample_path: Path = DEFAULT_SAMPLE_PATH,
    state_path: Path = DEFAULT_ALERT_STATE_PATH,
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR,
    pipeline_lock_path: Path = DEFAULT_PIPELINE_LOCK_PATH,
    db_path: Path | None = None,
) -> dict[str, object]:
    with _probe_process_deadline():
        observed_at = datetime.now(UTC)
        runtime = JourneyMonitorRuntime(
            origin_url=origin_url,
            public_url=public_url,
            pipeline_lock_path=pipeline_lock_path,
            db_path=db_path or db.resolve_db_path(),
        )
        samples = probe_journeys(runtime, observed_at=observed_at)
        corrupt_input = store_samples(sample_path, samples, now=observed_at)
        if corrupt_input is None:
            return {
                "scope": SAME_HOST_SCOPE,
                "samples": [],
                "sample_path": str(sample_path),
                "alerts": {
                    "ruleset": [],
                    "sent_count": 0,
                    "sent": [],
                    "results": [],
                    "state_path": str(state_path),
                    "corrupt_input": False,
                    "sample_store_skipped": True,
                },
                "sample_store_skipped": True,
                "skipped_overlap": False,
            }
        enabled = {"same_host_origin"}
        if runtime.public_url:
            enabled.add("same_host_public")
        alerts = run_performance_alerts(
            sample_path=sample_path,
            state_path=state_path,
            evidence_dir=evidence_dir,
            pipeline_lock_path=pipeline_lock_path,
            now=observed_at,
            enabled_vantages=frozenset(enabled),
            known_corrupt_input=corrupt_input,
        )
        return {
            "scope": SAME_HOST_SCOPE,
            "samples": [asdict(sample) for sample in samples],
            "sample_path": str(sample_path),
            "alerts": alerts,
            "sample_store_skipped": False,
            "skipped_overlap": False,
        }


if __name__ == "__main__":
    raise SystemExit(_watchdog_main(sys.argv[1:]))
