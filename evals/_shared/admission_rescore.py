"""Re-score immutable responses after gold migration, without claiming a new model run."""
from __future__ import annotations

import argparse
from pathlib import Path

from .assets import (ROOT, OBSERVED_ADMISSION, archive_metrics, create_run, digest, file_digest,
                     load_dataset, read_json, read_jsonl, rebuild_index, utc_now, write_json, write_jsonl)
from .metrics import score


def rescore(source: Path, dataset: Path, *, label: str, root: Path = ROOT) -> dict:
    old = read_json(source / "started.json")
    if old["target"] != "news-admission":
        raise ValueError("source is not an admission run")
    original_path = Path(old["dataset"])
    _, original = load_dataset(original_path, "news-admission")
    if file_digest(original_path / "manifest.json") != old["dataset_manifest_sha256"]:
        raise ValueError("source dataset identity mismatch")
    selected = read_jsonl(source / "cases.jsonl")
    originals = {c["case_id"]: c for c in original}
    if digest(selected) != old["case_identity"] or any(originals.get(c["case_id"]) != c for c in selected):
        raise ValueError("source run case identity mismatch")
    predictions = read_jsonl(source / "predictions.jsonl")
    by_id = {p["case_id"]: p for p in predictions}
    if len(by_id) != len(predictions) or set(by_id) != {c["case_id"] for c in selected}:
        raise ValueError("source must preserve one result (including failures) per case")
    manifest, pool = load_dataset(dataset, "news-admission")
    if manifest["benchmark"] != OBSERVED_ADMISSION:
        raise ValueError("destination must use observed-membership gold")
    current = {c["case_id"]: c for c in pool}
    cases, omitted = [], []
    for before in selected:
        after = current.get(before["case_id"])
        if after is None:
            omitted.append({"case_id": before["case_id"], "reason": "not_in_new_main"})
        elif before["input"] != after["input"]:
            omitted.append({"case_id": before["case_id"], "reason": "input_changed_no_reuse"})
        else:
            cases.append(after)
    if not cases:
        raise ValueError("no exact-input overlap in the new main set")
    saved = [by_id[c["case_id"]] for c in cases]
    result = score("O1", cases, saved)
    run, experiment = create_run(root, "news-admission", manifest["version"], benchmark=OBSERVED_ADMISSION)
    now = utc_now()
    meta = {**old, "benchmark": OBSERVED_ADMISSION, "version": manifest["version"], "label": label,
            "dataset": str(dataset.resolve()), "dataset_manifest_sha256": file_digest(dataset / "manifest.json"),
            "case_identity": digest(cases), "case_ids": [c["case_id"] for c in cases],
            "started_at": now, "ended_at": now, "directory_timestamp_utc": "/".join(run.parts[-2:]),
            "directory_time_source": "run_created", "status": "complete" if result["complete"] else "incomplete",
            "source_run": str(source.resolve()), "source_predictions_sha256": file_digest(source / "predictions.jsonl"),
            "source_started_sha256": file_digest(source / "started.json"), "source_case_count": len(selected),
            "selection": {"method": "exact-input intersection of archived selection and new main", "source_selection": old.get("selection")},
            "scope": "gold migration diagnostic on archived responses; not a new or unseen model evaluation",
            "scorer_identity": {p: file_digest(ROOT / p) for p in ("evals/_shared/metrics.py",
                f"evals/news-admission/{OBSERVED_ADMISSION}/metrics.json")},
            "new_api_attempts": 0, "cost_usd": 0, "cost_reason": "rescoring only; model cost belongs to source_run"}
    meta.pop("elapsed_seconds", None)
    meta.pop("reuse_run", None)
    write_json(run / "started.json", meta)
    write_jsonl(run / "cases.jsonl", cases)
    write_jsonl(run / "predictions.jsonl", saved)
    write_jsonl(run / "omitted.jsonl", omitted)
    archive_metrics(root, run, experiment, result, meta)
    rebuild_index(root)
    return {"run": str(run), "case_count": len(cases), "omitted": len(omitted),
            "complete": result["complete"], "metrics": result["metrics"], "new_api_attempts": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    result = rescore(args.source_run, args.dataset, label=args.label)
    print(f"Gold 重评分已归档：{result['case_count']} 题，未复用 {result['omitted']} 题；新增模型调用 0。")
    print(f"{result['run']}\n指标：{result['metrics']}；完整响应：{result['complete']}。仅迁移诊断，不是模型优化收益。")
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
