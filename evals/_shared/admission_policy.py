"""Explicit hybrid prefilter candidate; project saved LLM results, never labels."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .assets import (ROOT, OBSERVED_ADMISSION, archive_metrics, create_run, digest, file_digest, load_dataset,
                     read_json, read_jsonl, rebuild_index, utc_now, write_json, write_jsonl)
from .metrics import score
from .prefilter_eval import BENCHMARK, TARGET, prompt_context

POLICY = "hn100-standalone-body-v1"


def rejection_reasons(raw: dict) -> list[str]:
    """Fixed development-derived policy; receives no case/reference fields."""
    reasons = []
    if raw["source_id"] == "buzzing_hn":
        points = re.search(r"(\d+) HN Points", raw["content_text"][:4000])
        if points is None or int(points[1]) < 100:
            reasons.append("hn_points_missing_or_below_100")
    context = prompt_context(raw)
    if context["is_reply"]:
        reasons.append("conversation_reply")
    if context["is_title_only_web"]:
        reasons.append("title_only_web_listing")
    return reasons


def apply_policy(raw: dict, prediction: dict) -> dict:
    reasons = rejection_reasons(raw)
    # A failed model request remains missing, even when a rule would reject it.
    output = (None if prediction["status"] != "ok" else
              {"member": prediction["output"]["member"] and not reasons})
    return {**prediction, "output": output, "model_output": prediction["output"],
            "admission_policy": {"name": POLICY, "rejection_reasons": reasons}}


def project(source: Path, *, label: str, root: Path = ROOT) -> dict:
    old = read_json(source / "started.json")
    if old["target"] != TARGET or old["benchmark"] not in {BENCHMARK, OBSERVED_ADMISSION}:
        raise ValueError("requires an independent prefilter run")
    dataset = Path(old["dataset"])
    _, pool = load_dataset(dataset, TARGET)
    if file_digest(dataset / "manifest.json") != old["dataset_manifest_sha256"]:
        raise ValueError("source dataset identity changed")
    cases = read_jsonl(source / "cases.jsonl")
    by_id = {c["case_id"]: c for c in cases}
    original = {c["case_id"]: c for c in pool}
    if (digest(cases) != old["case_identity"] or len(by_id) != len(cases)
            or any(original.get(key) != case for key, case in by_id.items())):
        raise ValueError("source cases differ from frozen dataset")
    predictions = read_jsonl(source / "predictions.jsonl")
    if len(predictions) != len(cases) or {r["case_id"] for r in predictions} != set(by_id):
        raise ValueError("source must contain one prediction per selected case")
    for row in predictions:
        if "admission_policy" in row or set(row["stage_results"]) != {"prefilter"}:
            raise ValueError("source must be unprojected prefilter-only output")
        if row["status"] == "ok" and row["output"]["member"] is not row["stage_results"]["prefilter"]["output"]["is_ai_related"]:
            raise ValueError("source output differs from model result")
    projected = [apply_policy(by_id[r["case_id"]]["input"], r) for r in predictions]
    run, experiment = create_run(root, TARGET, old["version"], benchmark=old["benchmark"])
    identity = {"baseline": "hybrid-prefilter", "model_component": old["object_identity"],
                "policy": POLICY, "policy_code_sha256": file_digest(Path(__file__)),
                "context_code_sha256": file_digest(ROOT / "evals/_shared/prefilter_eval.py")}
    now = utc_now()
    metadata = {**old, "label": label, "object_identity": identity, "started_at": now,
                "ended_at": now, "directory_timestamp_utc": "/".join(run.parts[-2:]),
                "directory_time_source": "run_created", "reuse_run": None,
                "source_run": str(source.resolve()),
                "source_predictions_sha256": file_digest(source / "predictions.jsonl"),
                "scorer_identity": {p: file_digest(ROOT / p) for p in
                    ("evals/_shared/metrics.py", f"evals/news-admission/{old['benchmark']}/metrics.json")},
                "new_api_attempts": 0, "cost_usd": 0,
                "cost_reason": "zero-call projection only; model cost belongs to source_run"}
    result = score("O1", cases, projected)
    metadata["status"] = "complete" if result["complete"] else "incomplete"
    metadata.pop("elapsed_seconds", None)
    write_json(run / "started.json", metadata)
    write_jsonl(run / "cases.jsonl", cases)
    write_jsonl(run / "predictions.jsonl", projected)
    archive_metrics(root, run, experiment, result, metadata)
    rebuild_index(root)
    return {"run": str(run), "experiment": str(experiment), "complete": result["complete"],
            "metrics": result["metrics"], "new_api_attempts": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="离线混合prefilter候选：保留全部题，只改变预测；零新增模型调用。")
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    result = project(args.source_run, label=args.label)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
