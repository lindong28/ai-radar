from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db
from ..admin.alerts import AlertRuleResult, AlertSender, run_alert_results_state_machine
from .browser_probe import measure_browser_journey

WARM_SAMPLES = 20
CONFIRMATION_WINDOWS = 3
RETENTION_DAYS = 14
SAME_HOST_SCOPE = "same-host provisional; not a regional SLO"
DEFAULT_SAMPLE_PATH = db.PROJECT_ROOT / "logs" / "performance" / "journey-samples.jsonl"
DEFAULT_ALERT_STATE_PATH = db.PROJECT_ROOT / "logs" / "performance" / "alert-state.json"
DEFAULT_EVIDENCE_DIR = db.PROJECT_ROOT / "logs" / "performance" / "evidence"
DEFAULT_BROWSER_LOCK_PATH = db.PROJECT_ROOT / "logs" / "performance" / "browser.lock"
DEFAULT_PIPELINE_LOCK_DIR = db.PROJECT_ROOT / ".pipeline.lock"
PERF_BUSY_ROLLUP_RULE_ID = "PERF:rollup:busy"
CRONTAB_SAMPLE = (
    "17 * * * * cd /path/to/ai-radar && "
    "./run.sh performance-probe >> logs/performance-probe-cron.log 2>&1"
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
    pipeline_lock_dir: Path
    browser_lock_path: Path
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


def classify_pipeline_load(lock_dir: Path) -> str:
    if not lock_dir.exists():
        return "idle"
    try:
        raw_pid = (lock_dir / "pid").read_text(encoding="utf-8").strip()
        pid = int(raw_pid)
        if pid <= 0:
            return "unknown"
        os.kill(pid, 0)
    except (OSError, ValueError):
        return "idle" if not lock_dir.exists() else "unknown"
    return "busy"


def _interval_load_class(before: str, after: str) -> str:
    return before if before == after and before in {"idle", "busy"} else "unknown"


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
            before = classify_pipeline_load(runtime.pipeline_lock_dir)
            measurement = measure_browser_journey(
                base_url=base_url,
                target=spec.target,
                detail_slug=detail_slug,
                timeout_seconds=spec.timeout_seconds,
                lock_path=runtime.browser_lock_path,
                expected=_probe_expectation(runtime.db_path, spec.target, detail_slug),
            )
            after = classify_pipeline_load(runtime.pipeline_lock_dir)
            samples.append(
                JourneySample(
                    schema_version=1,
                    observed_at=observed_text,
                    journey=spec.journey,
                    target=spec.target,
                    vantage=vantage,
                    provisional=True,
                    load_class=_interval_load_class(before, after),
                    value_ms=float(measurement.value_ms),
                    hard_failure=measurement.hard_failure or measurement.outcome != "observed",
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
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def store_samples(
    path: Path,
    samples: list[JourneySample] | list[dict[str, object]],
    *,
    now: datetime | None = None,
) -> None:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = current - timedelta(days=RETENTION_DAYS)
    payloads = _load_samples(path)
    payloads.extend(asdict(sample) if isinstance(sample, JourneySample) else sample for sample in samples)
    retained = [
        payload
        for payload in payloads
        if (observed := _parse_timestamp(payload.get("observed_at"))) is None or observed >= cutoff
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for payload in retained:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _load_samples(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("journey sample must be a JSON object")
        rows.append(payload)
    return rows


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


def _numeric_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("PERF result value must be numeric")
    return float(value)


def _with_firing_message(result: AlertRuleResult, *, gate_reason: str) -> AlertRuleResult:
    vantage = result.values["vantage"]
    if gate_reason == "idle_clean":
        impact = (
            "同机合成探针，与 pipeline 并发、疑似主机 CPU 争用；"
            "idle 视角正常，用户大概率无感"
        )
        urgency = "否——除非 idle 视角也超标"
    elif gate_reason == "idle_firing":
        impact = "同机合成探针在 busy 与同视角 idle 均超标，已确认同视角真实退化，用户可能受影响"
        urgency = "是"
    elif gate_reason == "idle_cell" and vantage == "same_host_public":
        impact = "同机公网路径 idle 合成探针超标，已确认公网路径退化，用户可能受影响"
        urgency = "是"
    elif gate_reason == "idle_cell":
        impact = "同机 origin 路径 idle 合成探针超标，已确认同视角真实退化，用户可能受影响"
        urgency = "是"
    elif gate_reason in {"idle_absent", "idle_insufficient"}:
        evidence_state = "当前无 idle 样本" if gate_reason == "idle_absent" else "当前 idle 样本不足"
        impact = (
            f"影响未知：缺少足量同视角 idle 背书（{evidence_state}），"
            "无法排除用户影响，故保守 page；请补采/核查 idle evidence"
        )
        urgency = "是"
    else:
        raise ValueError(f"unrecognized PERF gate_reason: {gate_reason}")
    return replace(
        result,
        impact=impact,
        urgency=urgency,
        values={**result.values, "gate_reason": gate_reason},
    )


def evaluate_performance_rules(samples: list[dict[str, object]]) -> list[AlertRuleResult]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for sample in samples:
        journey = sample.get("journey")
        vantage = sample.get("vantage")
        load_class = sample.get("load_class")
        value = sample.get("value_ms")
        if (
            journey not in _SPEC_BY_JOURNEY
            or vantage not in {"same_host_origin", "same_host_public"}
            or load_class not in {"idle", "busy"}
            or isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            continue
        grouped[(str(journey), str(vantage), str(load_class))].append(sample)

    results_by_cell: dict[tuple[str, str, str], AlertRuleResult] = {}
    for (journey, vantage, load_class), rows in sorted(grouped.items()):
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
                "核对近期样本、CPU、pipeline 与同视角 idle 观测。"
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
        )

    minimum_firing_samples = WARM_SAMPLES + CONFIRMATION_WINDOWS - 1
    results: list[AlertRuleResult] = []
    for (journey, vantage, load_class), result in results_by_cell.items():
        if not result.firing:
            results.append(result)
            continue
        if load_class == "idle":
            results.append(_with_firing_message(result, gate_reason="idle_cell"))
            continue

        idle_key = (journey, vantage, "idle")
        idle_result = results_by_cell.get(idle_key)
        if idle_result is None:
            gate_reason = "idle_absent"
        elif len(grouped[idle_key]) < minimum_firing_samples:
            gate_reason = "idle_insufficient"
        elif idle_result.firing:
            gate_reason = "idle_firing"
        else:
            results.append(
                _with_firing_message(
                    replace(result, severity="notice"),
                    gate_reason="idle_clean",
                )
            )
            continue
        results.append(_with_firing_message(result, gate_reason=gate_reason))
    return results


def _load_alert_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _announced_firing_entry(entry: object) -> bool:
    if not isinstance(entry, dict) or entry.get("state") != "firing":
        return False
    last_notified = _parse_timestamp(entry.get("last_notified"))
    since = _parse_timestamp(entry.get("since"))
    return last_notified is not None and (since is None or last_notified >= since)


def _roll_up_notice_busy_results(
    results: list[AlertRuleResult],
    *,
    previous_state: dict[str, Any],
) -> list[AlertRuleResult]:
    notice_busy = [
        result
        for result in results
        if result.firing
        and result.severity == "notice"
        and result.values.get("load_class") == "busy"
    ]
    suppressed_rule_ids = {result.rule_id for result in notice_busy}
    rolled_up = [result for result in results if result.rule_id not in suppressed_rule_ids]

    if notice_busy:
        cells = [
            {
                "journey": result.values["journey"],
                "vantage": result.values["vantage"],
                "p75_ms": result.values["p75_ms"],
                "p95_ms": result.values["p95_ms"],
                "p75_budget_ms": result.values["p75_budget_ms"],
                "p95_budget_ms": result.values["p95_budget_ms"],
            }
            for result in notice_busy
        ]
        most_severe = max(
            range(len(cells)),
            key=lambda index: _numeric_value(cells[index]["p95_ms"])
            / max(_numeric_value(cells[index]["p95_budget_ms"]), 1.0),
        )
        cells[most_severe]["most_severe"] = True
        itemized = []
        for index, cell in enumerate(cells):
            marker = "（最严重）" if index == most_severe else ""
            itemized.append(
                f"- {cell['journey']} [{cell['vantage']}]："
                f"p95 实测 {_format_milliseconds(_numeric_value(cell['p95_ms']))} vs 预算 "
                f"{_format_milliseconds(_numeric_value(cell['p95_budget_ms']))}{marker}"
            )
        impact = (
            "同机合成探针，与 pipeline 并发、疑似主机 CPU 争用；"
            "idle 视角正常，用户大概率无感"
        )
        rolled_up.append(
            AlertRuleResult(
                rule_id=PERF_BUSY_ROLLUP_RULE_ID,
                title=f"性能自测：pipeline 运行期间 {len(cells)} 条 busy 旅程探针超预算",
                firing=True,
                detail=f"{impact}；明细：\n" + "\n".join(itemized),
                action="查看 logs/performance/evidence/ 中最新合成证据，并核对主机 load。",
                values={"load_class": "busy", "cells": cells},
                severity="notice",
                impact=impact,
                urgency="否——除非 idle 视角也超标",
            )
        )
    elif isinstance(previous_state.get(PERF_BUSY_ROLLUP_RULE_ID), dict) and previous_state[
        PERF_BUSY_ROLLUP_RULE_ID
    ].get("state") == "firing":
        rolled_up.append(
            AlertRuleResult(
                rule_id=PERF_BUSY_ROLLUP_RULE_ID,
                title="性能自测 busy 旅程探针",
                firing=False,
                detail="本轮已无 notice 级 busy PERF 子项；合成告警恢复",
                action="none",
                values={"load_class": "busy", "cells": []},
                severity="notice",
            )
        )

    for result in notice_busy:
        entry = previous_state.get(result.rule_id)
        if not _announced_firing_entry(entry):
            continue
        assert isinstance(entry, dict)
        rolled_up.append(
            AlertRuleResult(
                rule_id=result.rule_id,
                title=result.title,
                firing=False,
                detail="已迁移到 PERF:rollup:busy；旧个体 busy 告警恢复",
                action="none",
                severity="notice" if entry.get("severity") == "notice" else "page",
            )
        )
    return rolled_up


def _diagnostics(pipeline_lock_dir: Path) -> dict[str, object]:
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
            "status": classify_pipeline_load(pipeline_lock_dir),
            "lock_dir": str(pipeline_lock_dir),
        },
    }


def _write_firing_evidence(
    *,
    result: AlertRuleResult,
    samples: list[dict[str, object]],
    evidence_dir: Path,
    pipeline_lock_dir: Path,
    now: datetime,
) -> Path:
    values = result.values
    raw_cells = values.get("cells")
    if result.rule_id == PERF_BUSY_ROLLUP_RULE_ID and isinstance(raw_cells, list):
        cells = [cell for cell in raw_cells if isinstance(cell, dict)]
        recent = []
        for cell in cells:
            recent.extend(
                [
                    sample
                    for sample in samples
                    if sample.get("journey") == cell.get("journey")
                    and sample.get("vantage") == cell.get("vantage")
                    and sample.get("load_class") == "busy"
                ][-(WARM_SAMPLES + CONFIRMATION_WINDOWS - 1) :]
            )
        identity: dict[str, object] = {
            "load_class": "busy",
            "cells": cells,
        }
    else:
        recent = [
            sample
            for sample in samples
            if sample.get("journey") == values["journey"]
            and sample.get("vantage") == values["vantage"]
            and sample.get("load_class") == values["load_class"]
        ][-(WARM_SAMPLES + CONFIRMATION_WINDOWS - 1) :]
        identity = {
            "journey": values["journey"],
            "vantage": values["vantage"],
            "load_class": values["load_class"],
        }
    payload = {
        "schema_version": 1,
        "created_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "rule_id": result.rule_id,
        **identity,
        "provisional": True,
        "regional_slo_claim": False,
        "recent_samples": recent,
        "diagnostics": _diagnostics(pipeline_lock_dir),
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
    pipeline_lock_dir: Path,
    now: datetime | None = None,
    send: AlertSender | None = None,
    enabled_vantages: frozenset[str] | None = None,
) -> dict[str, object]:
    current = now or datetime.now(UTC)
    samples = _load_samples(sample_path)
    if enabled_vantages is not None:
        samples = [sample for sample in samples if sample.get("vantage") in enabled_vantages]
    results = evaluate_performance_rules(samples)
    previous_state = _load_alert_state(state_path)
    results = _roll_up_notice_busy_results(results, previous_state=previous_state)
    if enabled_vantages is not None:
        # A vantage disabled after firing would otherwise leave its alert state
        # stuck: retained samples age out, the rule stops appearing in results,
        # and the state machine never resolves it. Emit an explicit non-firing
        # result for such rules so they resolve instead of going stale.
        known_rule_ids = {result.rule_id for result in results}
        for rule_id, entry in previous_state.items():
            if rule_id in known_rule_ids or not rule_id.startswith("PERF:"):
                continue
            if not isinstance(entry, dict) or entry.get("state") != "firing":
                continue
            parts = rule_id.split(":")
            if len(parts) == 4 and parts[2] not in enabled_vantages:
                results.append(
                    AlertRuleResult(
                        rule_id=rule_id,
                        title=f"{parts[1]} {parts[2]} {parts[3]}",
                        firing=False,
                        detail="vantage disabled (public URL unset); auto-resolved",
                        action="none",
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
                pipeline_lock_dir=pipeline_lock_dir,
                now=current,
            )
            result = replace(result, detail=f"{result.detail}; evidence={evidence_path}")
        evidenced.append(result)
    return run_alert_results_state_machine(
        evidenced,
        state_path=state_path,
        now=current,
        send=send,
    )


def run_journey_monitor(
    *,
    origin_url: str,
    public_url: str,
    sample_path: Path = DEFAULT_SAMPLE_PATH,
    state_path: Path = DEFAULT_ALERT_STATE_PATH,
    evidence_dir: Path = DEFAULT_EVIDENCE_DIR,
    pipeline_lock_dir: Path = DEFAULT_PIPELINE_LOCK_DIR,
    browser_lock_path: Path = DEFAULT_BROWSER_LOCK_PATH,
    db_path: Path | None = None,
) -> dict[str, object]:
    runtime = JourneyMonitorRuntime(
        origin_url=origin_url,
        public_url=public_url,
        pipeline_lock_dir=pipeline_lock_dir,
        browser_lock_path=browser_lock_path,
        db_path=db_path or db.resolve_db_path(),
    )
    observed_at = datetime.now(UTC)
    samples = probe_journeys(runtime, observed_at=observed_at)
    store_samples(sample_path, samples, now=observed_at)
    enabled = {"same_host_origin"}
    if runtime.public_url:
        enabled.add("same_host_public")
    alerts = run_performance_alerts(
        sample_path=sample_path,
        state_path=state_path,
        evidence_dir=evidence_dir,
        pipeline_lock_dir=pipeline_lock_dir,
        now=observed_at,
        enabled_vantages=frozenset(enabled),
    )
    return {
        "scope": SAME_HOST_SCOPE,
        "samples": [asdict(sample) for sample in samples],
        "sample_path": str(sample_path),
        "alerts": alerts,
    }
