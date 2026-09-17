"""One frozen input pool, reusable stage outputs, four independently scored objects."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS

from .assets import (
    BENCHMARKS,
    ROOT,
    archive_metrics,
    code_identity,
    create_run,
    digest,
    file_digest,
    load_dataset,
    read_json,
    read_jsonl,
    rebuild_index,
    utc_now,
    validate_layout,
    write_json,
    write_jsonl,
)
from .inference import identity, predict_one, project_pool
from .metrics import score

OBJECTS = dict(zip(BENCHMARKS, ("O1", "O2", "O3", "O4"), strict=True))


def dataset_paths(primary: Path) -> dict[str, Path]:
    manifest, _ = load_dataset(primary)
    base = primary.resolve().parents[2]
    return {target: base / benchmark / target / manifest["version"] for target, benchmark in BENCHMARKS.items()}


def run_pool(cases: list[dict], config: dict, *, chat_factory, cache: Path, workers: int = 8) -> tuple[list[dict], dict]:
    """Cache successes only. Fixed source/model/input identity prevents cross-version reuse."""
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    object_identity = identity(config)
    stage_identity = {"sources": object_identity["source_sha256"], "requests": object_identity["requests"],
                      "transport": config["transport_identity"]}
    lock = threading.Lock()
    active = peak = calls = hits = 0
    started = time.monotonic()

    def process(case):
        nonlocal active, peak, calls, hits
        raw = case["input"]
        stages = {}
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            def stage(name):
                nonlocal calls, hits
                key = digest({"stage": name, "identity": stage_identity, "input": raw})
                path = cache / name / f"{key}.json"
                if path.exists():
                    record = read_json(path)
                    if record["key"] != key or digest(record["result"]) != record["result_sha256"]:
                        raise ValueError("stage cache integrity mismatch")
                    if record["result"]["status"] != "ok":
                        raise ValueError("failed result cannot enter reusable cache")
                    with lock:
                        hits += 1
                    return record["result"]
                with lock:
                    calls += 1
                result = predict_one(name, raw, {**config, "chat": chat_factory(case["case_id"])})["stage_results"][name]
                if result["status"] == "ok":
                    write_json(path, {"key": key, "result": result, "result_sha256": digest(result)})
                return result

            stages["prefilter"] = stage("prefilter")
            pre = stages["prefilter"]
            admitted = pre["status"] == "ok" and pre["output"]["is_ai_related"]
            # Independent O2/O3 questions do not disappear because O1 rejected them.
            if admitted or "score" in case["reference"]:
                stages["score"] = stage("score")
            if admitted or any(f in case["reference"] for f in ("category", "tags", "title", "summary", "reason")):
                stages["enrich"] = stage("enrich")
            return {"input": raw, "stage_results": stages}
        finally:
            with lock:
                active -= 1

    inputs = [case for case in cases if case["input"] is not None]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(process, inputs))
    return rows, {"elapsed_seconds": time.monotonic() - started, "clock": "monotonic",
                  "configured_workers": workers, "observed_peak_workers": peak,
                  "work_unit": "raw news item; its stages sequential", "stage_calls": calls, "cache_hits": hits,
                  "started_items": len(inputs)}


def evaluate(primary: Path, *, config: dict, chat_factory, root: Path = ROOT, smoke: int | None = None,
             workers: int = 8, fixed_pool: Path | None = None, label: str = "baseline") -> dict:
    validate_layout(root)
    paths = dataset_paths(primary)
    manifest, all_cases = load_dataset(paths["content-enrichment"])
    cases = all_cases
    if smoke is not None:
        if smoke < 1:
            raise ValueError("smoke size must be positive")
        executable = [c for c in all_cases if c["input"] is not None]
        # Exercise observed fields first; never present this selection as unbiased benchmark performance.
        executable.sort(key=lambda c: (-len(c["reference"]), c["case_id"]))
        cases = executable[:smoke]
    configuration = {**config, "now": manifest["window"]["end_exclusive"], "pool_complete": True, "archive_initial": []}
    original_identity = identity(configuration)
    instant = datetime.now(UTC)
    partitions = {target: create_run(root, target, manifest["version"], created_at=instant) for target in BENCHMARKS}
    primary_run = partitions["news-admission"][0]
    write_json(primary_run / "input.json", {"dataset": str(primary.resolve()), "manifest_sha256": file_digest(primary / "manifest.json"),
                                          "case_ids": [c["case_id"] for c in cases], "smoke": smoke})
    write_json(primary_run / "config.json", {k: v for k, v in configuration.items() if k != "chat"})
    write_json(primary_run / "identity.json", original_identity)
    started_at = utc_now()
    for target, (run, experiment) in partitions.items():
        write_json(experiment / "started.json", {
            "target": target, "benchmark": BENCHMARKS[target], "version": manifest["version"],
            "label": label, "started_at": started_at, "dataset": str(paths[target]),
            "run": str(run.relative_to(root)), "pool_run": str(primary_run.relative_to(root)),
            "status": "started", "completion_record": "metadata.json",
            "incomplete_rule": "Missing metadata.json means unfinished, never a successful evaluation.",
        })
    # The factory receives the real partition before any attempted call.
    factory = chat_factory(primary_run / "attempts") if chat_factory is not None else None
    if fixed_pool is not None:
        saved = read_json(fixed_pool.parent / "input.json")
        if saved["manifest_sha256"] != file_digest(primary / "manifest.json") or saved["case_ids"] != [c["case_id"] for c in cases]:
            raise ValueError("fixed pool belongs to another dataset/subset")
        previous = read_json(fixed_pool.parent / "identity.json")
        for key in ("source_sha256", "requests", "now", "archive_initial"):
            if previous[key] != original_identity[key]:
                raise ValueError(f"fixed pool inference identity mismatch: {key}")
        rows = read_jsonl(fixed_pool)
        receipt = read_json(fixed_pool.parent / "pool-receipt.json")
        if receipt["sha256"] != file_digest(fixed_pool):
            raise ValueError("fixed prediction pool was modified")
        timing = {"stage_calls": 0, "cache_hits": 0, "elapsed_seconds": 0, "configured_workers": 0,
                  "observed_peak_workers": 0, "work_unit": "fixed pool replay", "source": str(fixed_pool.resolve())}
    else:
        if factory is None:
            raise ValueError("a transport factory is required for new inference")
        rows, timing = run_pool(cases, configuration, chat_factory=factory, cache=root / ".local/eval-cache", workers=workers)
    if identity(configuration) != original_identity:
        raise ValueError("object changed while inference was running; output is not comparable")
    write_jsonl(primary_run / "pool.jsonl", rows)
    write_json(primary_run / "pool-receipt.json", {"sha256": file_digest(primary_run / "pool.jsonl")})
    projected = project_pool(rows, configuration)
    write_jsonl(primary_run / "projected.jsonl", projected)
    output = {}
    for target, (run, experiment) in partitions.items():
        target_manifest, target_cases = load_dataset(paths[target], target)
        if target != "news-admission" and digest(target_cases) != digest(all_cases):
            raise ValueError("shared object question pools disagree")
        subset_ids = {c["case_id"] for c in cases}
        target_cases = [c for c in target_cases if c["case_id"] in subset_ids]
        target_ids = {c["case_id"] for c in target_cases}
        predictions = []
        for row in projected:
            if row["case_id"] not in target_ids:
                continue
            if target == "news-admission":
                pre = row["stage_results"].get("prefilter", {})
                predictions.append({"case_id": row["case_id"], "status": pre.get("status", "error"),
                                    "output": {"member": (pre.get("output") or {}).get("is_ai_related")}})
            elif target == "content-enrichment":
                enrich = row["stage_results"].get("enrich", {})
                values = dict(row["output"])
                values["category"] = PRIMARY_CATEGORY_SLUGS.get(values.get("category"), values.get("category"))
                # Classification/title/summary/tags do not depend on another
                # article's admission. Visible reason can be overridden by curation.
                if row.get("pool_error_item_ids"):
                    values["reason"] = None
                predictions.append({"case_id": row["case_id"], "status": enrich.get("status", "error"), "output": values})
            else:
                predictions.append({"case_id": row["case_id"], "status": row["target_status"][target], "output": row["output"]})
        write_jsonl(run / "predictions.jsonl", predictions)
        write_json(run / "prediction-receipt.json", {"sha256": file_digest(run / "predictions.jsonl")})
        result = score(OBJECTS[target], target_cases, predictions)
        metadata = {"target": target, "benchmark": BENCHMARKS[target], "version": manifest["version"], "label": label,
                    "dataset": str(paths[target]), "dataset_manifest_sha256": file_digest(paths[target] / "manifest.json"),
                    "case_ids": [c["case_id"] for c in target_cases], "case_identity": digest(target_cases),
                    "split": "smoke" if smoke else "all", "smoke": smoke,
                    "started_at": started_at, "ended_at": utc_now(), "timing": timing,
                    "object_identity": original_identity, "scorer_identity": code_identity(root),
                    "pool_run": str(primary_run.relative_to(root)), "status": "complete" if result["complete"] else "incomplete",
                    "scope": "diagnostic subset only" if smoke else "full frozen pool; empty-state current-source replay",
                    "text_calibration": "missing user labels; not trusted", "cost_usd": None,
                    "cost_reason": "usage in attempts; authoritative prices not supplied"}
        archive_metrics(root, run, experiment, result, metadata)
        output[target] = {"run": str(run), "experiment": str(experiment), "metrics": result["metrics"], "complete": result["complete"]}
    rebuild_index(root)
    return output


def compare(baseline: Path, candidate: Path, *, root: Path = ROOT) -> dict:
    """No aggregate substitute for individual metrics; untrusted values cannot win."""
    left, right = read_json(baseline / "metadata.json"), read_json(candidate / "metadata.json")
    for key in ("target", "benchmark", "version", "case_identity", "split", "scorer_identity"):
        if left[key] != right[key]:
            raise ValueError(f"comparison condition differs: {key}")
    if left["target"] == "content-enrichment":
        for key in ("judge_identity", "calibration_identity"):
            if left.get(key) != right.get(key):
                raise ValueError(f"comparison measurement identity differs: {key}")
    if left["smoke"] or right["smoke"]:
        raise ValueError("smoke cannot drive acceptance")
    def verified_rows(directory):
        rows = read_json(directory / "metrics/summary.json")
        for row in rows:
            source = read_json(root / row["source"])
            observed = source["metrics"][row["metric_name"]]
            if observed["value"] != row["metric_value"] or observed["status"] != row["status"]:
                raise ValueError("metric projection integrity mismatch with original scorer")
        return rows

    a = verified_rows(baseline)
    b = {v["metric_name"]: v for v in verified_rows(candidate)}
    definitions = read_json(root / "evals" / left["target"] / left["benchmark"] / "metrics.json")
    directions = {d["metric_name"]: d["direction"] for d in definitions["metrics"]}
    improved, regressed, unknown = [], [], []
    differences = []
    for row in a:
        name = row["metric_name"]
        other = b[name]
        if (directions.get(name) not in {"higher", "lower"} or row["status"] != "trusted"
                or other["status"] != "trusted" or row["metric_value"] is None or other["metric_value"] is None):
            unknown.append(name)
            continue
        delta = other["metric_value"] - row["metric_value"]
        signed = delta * (1 if directions[name] == "higher" else -1)
        if signed > 0:
            improved.append(name)
        elif signed < 0:
            regressed.append(name)
        differences.append({"metric": name, "baseline": row["metric_value"], "candidate": other["metric_value"], "delta": delta})
    accepted = bool(improved) and not regressed and not unknown and left["status"] == right["status"] == "complete"
    return {"accepted": accepted, "improved": improved, "regressed": regressed, "untrusted_or_missing": unknown,
            "differences": differences, "claim": "same cases, individual metrics; not significance or overall parity"}
