"""Append current metrics for immutable O2 predictions, without model calls."""
from __future__ import annotations

from pathlib import Path

from .assets import (
    ROOT,
    archive_metrics,
    create_run,
    file_digest,
    load_dataset,
    rebuild_index,
    utc_now,
    write_json,
    write_jsonl,
)
from .relocations import resolve_asset_path
from .score_calibration import BENCHMARK, TARGET, load_source


def rescore(source: Path, *, label: str, root: Path = ROOT) -> dict:
    source, old, cases, predictions, result, hashes = load_source(source, root=root)
    if not old.get("dataset_manifest_sha256"):
        raise ValueError("source has no frozen dataset manifest identity; "
                         "legacy calibration runs without this identity cannot be rescored")
    dataset = resolve_asset_path(Path(old["dataset"]), root=root)
    manifest, pool = load_dataset(dataset, TARGET)
    if (manifest["benchmark"], manifest["version"]) != (BENCHMARK, old["version"]):
        raise ValueError("source dataset contract mismatch")
    if file_digest(dataset / "manifest.json") != old["dataset_manifest_sha256"]:
        raise ValueError("source dataset identity mismatch")
    originals = {case["case_id"]: case for case in pool}
    if any(originals.get(case["case_id"]) != case for case in cases):
        raise ValueError("source cases differ from frozen dataset")

    run, experiment = create_run(root, TARGET, old["version"], benchmark=BENCHMARK)
    now = utc_now()
    metadata = {
        **{key: old[key] for key in (
            "target", "benchmark", "version", "dataset", "dataset_manifest_sha256",
            "case_identity", "case_ids", "split", "object_identity", "selection", "mode", "smoke",
        ) if key in old},
        "label": label, "kind": "metric-rescore", "source_run": str(source),
        "source_sha256": hashes,
        "source_started_at": old.get("source_started_at", old.get("started_at")),
        "started_at": now, "ended_at": now,
        "directory_timestamp_utc": "/".join(run.parts[-2:]), "directory_time_source": "run_created",
        "scorer_identity": {p: file_digest(ROOT / p) for p in (
            "evals/_shared/metrics.py", f"evals/{TARGET}/{BENCHMARK}/metrics.json",
        )},
        "rescore_source_sha256": {p: file_digest(ROOT / p) for p in (
            "evals/_shared/score_rescore.py", "evals/_shared/score_calibration.py",
        )},
        "scope": "same cases, gold and predictions; metric update only, not new or unseen inference",
        "status": "complete" if result["complete"] else "incomplete",
        "new_api_attempts": 0, "cost_usd": 0,
        "cost_reason": "metrics only; all model usage and costs belong to source_run",
    }
    write_json(run / "started.json", metadata)
    write_jsonl(run / "cases.jsonl", cases)
    write_jsonl(run / "predictions.jsonl", predictions)
    archive_metrics(root, run, experiment, result, metadata)
    rebuild_index(root)
    return {"run": str(run), "experiment": str(experiment), "source_run": str(source),
            "complete": result["complete"], "metrics": result["metrics"], "new_api_attempts": 0}
