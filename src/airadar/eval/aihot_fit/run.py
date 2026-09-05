"""Run the production stages (prefilter / score / enrich-v2) over the evalset."""

from __future__ import annotations

import random
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ... import db
from ...curator.score import weighted_score
from ...curator.weights import DEFAULT_WEIGHTS
from ...enrich import runner_v2 as enrich_runner
from ...prefilter import runner as prefilter_runner
from ...provider.base import ProviderItem
from ...ruleset import current_version, current_version_v2
from ...scorer import runner as scorer_runner
from .common import (
    DEFAULT_WORKERS,
    REFERENCE_FIELD,
    RUN_SCHEMA_VERSION,
    git_identity,
    has_reference,
    is_stop_signal,
    load_questions,
    model_selection_env,
    redact,
    require_ark_only,
    sha256_file,
    utc_now,
    write_json,
    write_jsonl,
)

STAGES: tuple[str, ...] = ("prefilter", "score", "enrich")
_PROMPT_FILES = {
    "prefilter": "src/airadar/prefilter/prompts.py",
    "score": "src/airadar/scorer/prompts.py",
    "enrich": "src/airadar/enrich/prompts_v2.py",
}


def default_run_id(questions_sha256: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{questions_sha256[:8]}"


def sample_questions(questions: list[dict[str, Any]], limit: int | None, seed: int) -> list[dict[str, Any]]:
    if limit is None or limit >= len(questions):
        return list(questions)
    ordered = sorted(questions, key=lambda question: str(question["question_id"]))
    picked = random.Random(seed).sample(ordered, limit)
    return sorted(picked, key=lambda question: str(question["question_id"]))


def provider_item(question: dict[str, Any]) -> ProviderItem:
    source = question["input"]
    return ProviderItem(
        id=str(source["item_id"]),
        title=str(source["title"]),
        url=str(source["url"]),
        source_id=str(source["source_id"]),
        tier=str(source.get("tier") or "unknown"),
        author=source.get("author"),
        published_at=str(source["published_at"]),
        content_text=str(source.get("content_text") or ""),
    )


def stage_identity(providers: dict[str, Any]) -> dict[str, Any]:
    root = db.PROJECT_ROOT
    rulesets = {"prefilter": current_version(), "score": current_version(), "enrich": current_version_v2()}
    return {
        stage: {
            "ruleset_version": rulesets[stage],
            "model_id": getattr(providers[stage], "model_id", None),
            "provider_class": type(providers[stage]).__name__,
            "prompt_file": _PROMPT_FILES[stage],
            "prompt_sha256": sha256_file(root / _PROMPT_FILES[stage]),
        }
        for stage in STAGES
        if stage in providers
    }


def _cash_signal(output: dict[str, Any]) -> str | None:
    raw = output.get("raw") if isinstance(output, dict) else None
    provider = raw.get("provider") if isinstance(raw, dict) else None
    if provider is not None and provider != "ark":
        return f"provider={provider} is a pay-per-token path"
    return None


def _evaluate(stage: str, provider: Any, item: ProviderItem, tier: str) -> dict[str, Any]:
    record: dict[str, Any]
    if stage == "prefilter":
        _, output, error, latency_ms = prefilter_runner._evaluate_item(provider, item)
        record = {"output": output, "error": error, "latency_ms": latency_ms}
    elif stage == "score":
        scored, output, error, latency_ms = scorer_runner._evaluate_item(provider, item)
        record = {"output": output, "error": error, "latency_ms": latency_ms}
        if scored is not None:
            record["weighted_score"] = weighted_score(scored.model_dump(), DEFAULT_WEIGHTS, tier)
    else:
        _, output, error, latency_ms = enrich_runner._evaluate_item(provider, item)
        record = {"output": output, "error": error, "latency_ms": latency_ms}
    if record["error"]:
        record["error"] = redact(str(record["error"]))
    return record


def run_stages(
    *,
    questions_path: Path,
    out_dir: Path,
    limit: int | None = None,
    seed: int = 0,
    stages: tuple[str, ...] = STAGES,
    workers: int = DEFAULT_WORKERS,
    require_reference: str | None = None,
) -> dict[str, Any]:
    unknown = [stage for stage in stages if stage not in STAGES]
    if unknown:
        raise ValueError(f"unknown stages: {unknown}")
    if require_reference is not None and require_reference not in REFERENCE_FIELD:
        raise ValueError(f"unknown reference dimension: {require_reference}")
    credentials = require_ark_only()
    questions_sha256 = sha256_file(questions_path)
    pool = load_questions(questions_path)
    pool_total = len(pool)
    if require_reference is not None:
        pool = [question for question in pool if has_reference(question, require_reference)]
    questions = sample_questions(pool, limit, seed)
    providers: dict[str, Any] = {}
    if "prefilter" in stages:
        providers["prefilter"] = prefilter_runner._provider_from_env()
    if "score" in stages:
        providers["score"] = scorer_runner._provider_from_env()
    if "enrich" in stages:
        providers["enrich"] = enrich_runner._provider_from_env()

    stop = threading.Event()
    stop_reasons: list[str] = []
    lock = threading.Lock()

    def evaluate_question(question: dict[str, Any]) -> dict[str, Any]:
        row: dict[str, Any] = {"question_id": question["question_id"], "item_id": question["input"]["item_id"]}
        if stop.is_set():
            row["skipped"] = "stopped"
            return row
        item = provider_item(question)
        for stage in stages:
            if stop.is_set():
                row[stage] = {"output": None, "error": None, "latency_ms": None, "skipped": "stopped"}
                continue
            try:
                record = _evaluate(stage, providers[stage], item, item.tier)
            except Exception as exc:  # provider raised outside the runner's own handling
                record = {"output": None, "error": redact(f"{type(exc).__name__}: {exc}"), "latency_ms": None}
            cash = _cash_signal(record.get("output") or {})
            reason = None
            if cash:
                reason = f"{stage}: cash signal: {cash}"
            elif is_stop_signal(record.get("error")):
                reason = f"{stage}: {record['error']}"
            if reason:
                with lock:
                    stop_reasons.append(reason)
                stop.set()
            row[stage] = record
        return row

    started_at = utc_now()
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(questions) or 1))) as executor:
        futures = [executor.submit(evaluate_question, question) for question in questions]
        for future in as_completed(futures):
            rows.append(future.result())
    finished_at = utc_now()
    rows.sort(key=lambda row: str(row["question_id"]))

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "outputs.jsonl", rows)

    def latencies(stage: str) -> list[int]:
        return [
            int(row[stage]["latency_ms"])
            for row in rows
            if isinstance(row.get(stage), dict) and row[stage].get("latency_ms") is not None
        ]

    stage_summary: dict[str, Any] = {}
    for stage in stages:
        values = latencies(stage)
        stage_summary[stage] = {
            "completed": sum(1 for row in rows if isinstance(row.get(stage), dict) and "skipped" not in row[stage]),
            "ok": sum(
                1
                for row in rows
                if isinstance(row.get(stage), dict) and row[stage].get("output") and not row[stage].get("error")
            ),
            "errors": sum(1 for row in rows if isinstance(row.get(stage), dict) and row[stage].get("error")),
            "skipped": sum(1 for row in rows if isinstance(row.get(stage), dict) and row[stage].get("skipped")),
            "latency_ms_median": statistics.median(values) if values else None,
        }
    run_json = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": out_dir.name,
        "questions_path": str(questions_path),
        "questions_sha256": questions_sha256,
        "n": len(questions),
        "limit": limit,
        "seed": seed,
        "require_reference": require_reference,
        "pool_total": pool_total,
        "pool_eligible": len(pool),
        "stages": list(stages),
        "stage_gating": "none (every stage runs on every question; production gates score/enrich on prefilter)",
        "workers": workers,
        "started_at": started_at,
        "finished_at": finished_at,
        "item_ids": sorted(str(question["input"]["item_id"]) for question in questions),
        "stopped_early": stop.is_set(),
        "stop_reasons": stop_reasons,
        "stage_summary": stage_summary,
        "identity": {
            "stages": stage_identity(providers),
            "git": git_identity(),
            "model_selection_env": model_selection_env(),
            "credentials": credentials,
            "weights": DEFAULT_WEIGHTS.as_dict(),
        },
    }
    write_json(out_dir / "run.json", run_json)
    return run_json
