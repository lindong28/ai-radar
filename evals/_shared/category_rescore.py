"""Append category metrics from frozen predictions; never invoke a model."""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS

from . import assets
from .category_metrics import check_metric_definitions, score_categories
from .score_type_study import preflight

TARGET = "content-enrichment"
BENCHMARK = assets.CATEGORY_NAVIGATION


def rescore(source_run: Path, *, source_root: Path = assets.ROOT,
            root: Path = assets.ROOT) -> dict:
    source_root, source_run = source_root.resolve(), source_run.resolve()
    suffix = source_run.relative_to(source_root / "runs")
    source_metadata = source_root / "experiments" / suffix / "metadata.json"
    files = {name: source_run / name for name in ("cases.jsonl", "predictions.jsonl", "scores.json")}
    files["metadata.json"] = source_metadata

    def identity():
        return {"code": {p: assets.file_digest(assets.ROOT / p) for p in (
                    "evals/_shared/category_rescore.py", "evals/_shared/category_metrics.py",
                    "evals/_shared/metrics.py", "src/airadar/enrich/classification.py",
                    "evals/content-enrichment/aihot-category-navigation/metrics.json")},
                "behavior": {"metric_registry": check_metric_definitions(root),
                             "mode": "frozen-predictions", "gold": "source-run-frozen",
                             "categories": PRIMARY_CATEGORY_SLUGS},
                "inputs": {name: assets.file_digest(path) for name, path in files.items()}}

    frozen = identity()
    meta = assets.read_json(source_metadata)
    if (meta.get("target") != TARGET or meta.get("benchmark") != BENCHMARK
            or suffix.parts[:3] != (TARGET, BENCHMARK, meta.get("version"))
            or meta.get("identity_unchanged") is not True):
        raise ValueError("source category identity is not verified")
    if meta.get("run_kind") == "metric_recompute":
        raise ValueError("rescore the original inference run, not another metric recomputation")
    cases, predictions = (assets.read_jsonl(files[name]) for name in ("cases.jsonl", "predictions.jsonl"))
    if (assets.digest(cases) != meta["case_identity"]
            or meta["object_identity"]["inputs"]["cases"] != meta["case_identity"]
            or [c["case_id"] for c in cases] != meta["case_ids"]):
        raise ValueError("source cases differ from frozen inference inputs")
    for prediction in predictions:
        if prediction != assets.read_json(source_run / "items" / (assets.slug(prediction["case_id"]) + ".json")):
            raise ValueError("source predictions differ from archived inference items")
    result = score_categories(cases, predictions)
    previous = assets.read_json(files["scores.json"])
    if result["metrics"]["category_accuracy"] != previous["metrics"]["category_accuracy"]:
        raise ValueError("recomputed accuracy differs from source run; investigate before archiving")
    run, experiment = assets.create_run(root, TARGET, meta["version"], benchmark=BENCHMARK)
    preflight(frozen, identity(), run, run_name="category-metric-recompute",
              baseline_id=str(suffix), authority="user-requested per-category precision and recall")
    # Small run snapshots, not another benchmark version; originals stay immutable.
    assets.write_jsonl(run / "cases.jsonl", cases)
    assets.write_jsonl(run / "predictions.jsonl", predictions)
    if frozen != identity():
        raise ValueError("source or metric implementation changed during recomputation")
    metadata = {key: meta[key] for key in (
        "target", "benchmark", "version", "label", "split", "smoke", "field", "dataset",
        "case_identity", "case_ids", "object_identity", "selection", "human_application")}
    metadata.update(run_kind="metric_recompute", source_run=str(source_run),
                    source_sha256=frozen["inputs"], metric_identity=frozen,
                    additional_model_calls=0, started_at=assets.utc_now(), ended_at=assets.utc_now(),
                    cost={"value": 0, "reason": "incremental recomputation only; historical cost belongs to source_run"},
                    status="complete" if result["complete"] else "incomplete", identity_unchanged=True)
    assets.archive_metrics(root, run, experiment, result, metadata)
    assets.rebuild_index(root)
    return {"run": str(run), "source_run": metadata["source_run"],
            "complete": result["complete"], "additional_model_calls": 0,
            "metrics": result["metrics"], "category_counts": result["category_counts"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="从分类运行的冻结预测补算逐类指标；零新增模型调用，原件不覆盖。")
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=assets.ROOT)
    parser.add_argument("--output-root", type=Path, default=assets.ROOT)
    parser.add_argument("--json", action="store_true", help="机器结果到 stdout，身份诊断到 stderr")
    args = parser.parse_args(argv)
    with contextlib.redirect_stdout(sys.stderr):
        result = rescore(args.source_run, source_root=args.source_root, root=args.output_root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"已补算分类指标；新增模型调用 0；结果：{result['run']}")
        print(f"复用源运行：{result['source_run']}（不是新的模型评测）")
        if not result["complete"]:
            print("源预测不完整：失败题仍计 FN，详见 scores.json 的 per_case；未重试模型。")
        for name in ("category_accuracy", *[f"category_{c}_{m}" for c in PRIMARY_CATEGORY_SLUGS
                                           for m in ("precision", "recall")]):
            metric = result["metrics"][name]
            value = "未计算" if metric["value"] is None else f"{metric['value']:.2%}"
            print(f"{name}: {value}（分母 {metric['denominator']}）")
    # An inference failure remains incomplete even when its metric recomputation succeeds.
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
