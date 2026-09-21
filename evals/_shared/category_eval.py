"""Offline category field runner; no generated AIHOT text enters inference."""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from airadar.enrich.category import RUBRIC, category_output, render_category_prompt
from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS

from . import assets
from .cli import transport_factory
from .human_labels import apply_labels
from .human_store import read_reviews
from .metrics import score
from .prefilter_eval import select_cases
from .score_type_study import preflight

TARGET = "content-enrichment"
BENCHMARK = assets.CATEGORY_NAVIGATION


def category_cases(cases: list[dict]) -> list[dict]:
    result = []
    for c in cases:
        if c.get("input") is None or "category" not in c["reference"]:
            continue
        if c["reference"]["category"] not in PRIMARY_CATEGORY_SLUGS.values():
            raise ValueError(f"unknown reference category: {c['reference']['category']}")
        result.append({**c, "reference": {"category": c["reference"]["category"]}})
    return result


def diagnostics(cases: list[dict], predictions: list[dict]) -> dict:
    by_id = {p["case_id"]: p for p in predictions}
    matrix = Counter()
    for c in cases:
        p = by_id.get(c["case_id"], {})
        value = p.get("output", {}).get("category") if p.get("status") == "ok" else "error"
        matrix[(c["reference"]["category"], value)] += 1
    counts = Counter(c["reference"]["category"] for c in cases)
    return {"majority_baseline": max(counts.values()) / len(cases),
            "reference_counts": dict(counts),
            "missing_reference_classes": sorted(set(PRIMARY_CATEGORY_SLUGS.values()) - counts.keys()),
            "confusion": [{"reference": a, "prediction": b, "count": n} for (a, b), n in sorted(matrix.items())]}


def evaluate(dataset: Path, *, config: dict, split: str, limit: int | None, seed: str,
             label: str, chat_factory, rubric: str = RUBRIC, workers: int = 8,
             smoke: bool = False, root: Path = assets.ROOT) -> dict:
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8 for the shared offline API pool")
    manifest, all_cases = assets.load_dataset(dataset, TARGET)
    if manifest["benchmark"] != BENCHMARK:
        raise ValueError("category runner requires website navigation gold, not legacy five-class API gold")
    book_path = root / "human-evals" / TARGET / "reviews.json"
    human = None
    if book_path.exists():
        book = read_reviews(book_path)
        if book["metadata"]["target"] != TARGET:
            raise ValueError("human review target mismatch")
        all_cases, human = apply_labels(all_cases, [a for b in book["batches"]
            for a in b["data"]["annotations"]], TARGET)
    cases = select_cases(category_cases(all_cases), split, limit, seed)
    prompts = {c["case_id"]: render_category_prompt(c["input"], rubric) for c in cases}
    request = {"model": config["models"]["category"], "temperature": 0, "max_tokens": 700}
    paths = ("src/airadar/enrich/category.py", "src/airadar/enrich/classification.py",
             "src/airadar/provider/judgment.py", "evals/_shared/category_eval.py",
             "evals/_shared/metrics.py", "evals/_shared/transport.py", "evals/_shared/cli.py",
             "evals/_shared/human_labels.py", "evals/_shared/human_store.py",
             "evals/_shared/score_type_study.py")

    def identity():
        return {"code": {p: assets.file_digest(assets.ROOT / p) for p in paths},
                "behavior": {"request": request, "transport": config["transport_identity"],
                             "rubric": rubric, "thinking": "disabled", "retry_count": 0},
                "inputs": {"dataset": assets.file_digest(dataset / "manifest.json"),
                           "cases": assets.digest(cases), "prompts": assets.digest(prompts),
                           "human_reviews": assets.file_digest(book_path) if book_path.exists() else None}}

    run, experiment = assets.create_run(root, TARGET, manifest["version"], benchmark=BENCHMARK)
    frozen = identity()
    assets.write_json(run / "config.json", config)
    assets.write_json(run / "prompt.json", {"rubric": rubric})
    assets.write_jsonl(run / "cases.jsonl", cases)
    assets.write_jsonl(run / "prompts.jsonl", [{"case_id": k, "prompt": v} for k, v in prompts.items()])
    meta = {"target": TARGET, "benchmark": BENCHMARK, "version": manifest["version"],
            "label": assets.slug(label), "split": split, "smoke": smoke, "field": "category",
            "dataset": str(dataset), "case_identity": assets.digest(cases),
            "case_ids": [c["case_id"] for c in cases], "object_identity": frozen,
            "selection": {"seed": seed, "limit": limit, "method": "label-blind stable hash within split"},
            "human_application": human, "status": "running", "started_at": assets.utc_now(),
            "workers": workers, "cost": {"value": None, "reason": "unpriced; attempts retain usage"}}
    assets.write_json(run / "started.json", meta)
    # Existing mechanical identity gate, now bound to this field-specific object.
    preflight(frozen, identity(), run, run_name="category",
              baseline_id="original-news-category", authority="ADR e38b; offline classification")
    chat = chat_factory(run / "attempts")
    start = time.monotonic()

    def one(case):
        key = case["case_id"]
        row = {"case_id": key, "status": "error", "output": {}}
        try:
            response = chat(key)(stage="category", prompt=prompts[key], request=request)
            row["response_json"] = json.dumps(response, ensure_ascii=False)
            row["output"] = category_output(response["json"])
            row["reason"] = response["json"]["reason"]
            row["status"] = "ok"
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            row["attempt_id"] = getattr(exc, "attempt_id", None)
        assets.write_json(run / "items" / (key + ".json"), row)
        return row

    predictions = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(one, c) for c in cases]
        for f in as_completed(futures):
            predictions.append(f.result())
            if len(predictions) % 25 == 0 or len(predictions) == len(cases):
                print(f"分类已返回 {len(predictions)}/{len(cases)}；失败 {sum(p['status'] != 'ok' for p in predictions)}", flush=True)
    predictions.sort(key=lambda p: p["case_id"])
    assets.write_jsonl(run / "predictions.jsonl", predictions)
    result = score("O3", cases, predictions)
    unchanged = frozen == identity()
    if not unchanged:
        result["complete"] = False
        for metric in result["metrics"].values():
            metric.update(value=None, status="not_computed", reason="object identity changed")
    assets.write_json(run / "diagnostics.json", diagnostics(cases, predictions))
    meta.update(status="complete" if result["complete"] else "incomplete",
                ended_at=assets.utc_now(), elapsed_seconds=time.monotonic() - start,
                identity_unchanged=unchanged)
    assets.archive_metrics(root, run, experiment, result, meta)
    assets.rebuild_index(root)
    return {"run": str(run), "complete": result["complete"], "category_accuracy": result["metrics"]["category_accuracy"]}


def main(argv=None):
    p = argparse.ArgumentParser(description="O3 分类字段离线评测；不执行标签或文本生成、不修改生产。")
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--env-file", type=Path)
    p.add_argument("--rubric", type=Path, help="UTF-8 candidate rubric; default is shared six-category A0 rubric")
    p.add_argument("--split", choices=["dev", "regression"], required=True)
    p.add_argument("--limit", type=int)
    p.add_argument("--seed", default="category-development")
    p.add_argument("--label", required=True)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--output-root", type=Path, default=assets.ROOT)
    a = p.parse_args(argv)
    config = assets.read_json(a.config)
    result = evaluate(a.dataset, config=config, split=a.split, limit=a.limit, seed=a.seed,
                      label=a.label, chat_factory=transport_factory(config, a.env_file),
                      rubric=a.rubric.read_text() if a.rubric else RUBRIC, workers=a.workers,
                      smoke=a.smoke, root=a.output_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
