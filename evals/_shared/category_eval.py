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
from .category_metrics import check_metric_definitions, score_categories
from .category_review import review_prompt, routing_output, routing_prompt
from .cli import transport_factory
from .human_labels import apply_labels
from .human_store import read_reviews
from .prefilter_eval import select_cases
from .score_type_study import preflight
from .quote_context import QuoteContext, render_quotes

TARGET = "content-enrichment"
BENCHMARK = assets.CATEGORY_NAVIGATION

QUOTE_CONTRIBUTION = """引用关系：先识别当前帖自身交付了什么。当前帖有独立的实测、步骤或论证时，按当前贡献分类，引用只交代背景；当前帖仅简短转述、赞同或指代而没有独立贡献时，用引用原帖补足所报道的事实，再按该事实分类。不要因为引用更长就让它覆盖当前主体，也不要因为当前帖短就忽略已提供的引用。reason说明实际采用的主体证据。"""


def source_context(raw: dict) -> str:
    """Expose existing original-source facts, never gold or enrichment fields."""
    fields = {key: raw[key] for key in ("url", "author", "source_kind", "source_name")
              if isinstance(raw.get(key), str) and raw[key].strip()}
    if not fields:
        return ""
    return ("\n\nCollected source context (author may be a feed submitter; "
            "source_name is the collection channel):\n" + json.dumps(fields, ensure_ascii=False))


def quoted_inputs(cases: list[dict], dataset: Path, source: Path) -> tuple[list[dict], dict]:
    """Resolve original quotes only from a hash-bound parent dataset's raw archive."""
    manifest = assets.read_json(dataset / "manifest.json")
    source_manifest = assets.read_json(source / "manifest.json")
    source_sha = assets.file_digest(source / "manifest.json")
    if source_sha not in {row["sha256"] for row in manifest.get("source_datasets", [])}:
        raise ValueError("quote source must be a frozen direct input parent")
    raw_path = source / source_manifest["shared_evidence"] / "raw-inputs.jsonl"
    if assets.file_digest(raw_path) != source_manifest.get("evidence_files", {}).get("raw-inputs.jsonl"):
        raise ValueError("quote source raw evidence hash mismatch")
    index = QuoteContext(raw_path)
    rows = [{"case_id": c["case_id"], "quotes": index.resolve(c["input"], c["provenance"]["observed_at"])}
            for c in cases]
    return rows, {"manifest_sha256": source_sha, "raw_inputs_sha256": index.sha256,
                  "resolved_sha256": assets.digest(rows)}


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
             smoke: bool = False, root: Path = assets.ROOT, quote_source: Path | None = None,
             body_limit: int | None = 5000, quote_contribution: bool = False,
             conditional_review: bool = False, include_source_context: bool = False,
             blind_review: bool = False, review_guidance: str = "") -> dict:
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8 for the shared offline API pool")
    if (blind_review or review_guidance) and not conditional_review:
        raise ValueError("blind review and review guidance require conditional review")
    check_metric_definitions(root)
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
    prompts = {c["case_id"]: render_category_prompt(c["input"], rubric, body_limit=body_limit) for c in cases}
    if include_source_context:
        for case in cases:
            prompts[case["case_id"]]["user"] += source_context(case["input"])
    contexts, context_identity = [], None
    context_paths = []
    if quote_source is not None:
        contexts, context_identity = quoted_inputs(cases, dataset, quote_source)
        context_paths = [quote_source / "manifest.json", quote_source /
                         assets.read_json(quote_source / "manifest.json")["shared_evidence"] / "raw-inputs.jsonl"]
        for row in contexts:
            prompts[row["case_id"]]["user"] += render_quotes(row["quotes"])
            if quote_contribution and any(q["status"] == "available" for q in row["quotes"]):
                prompts[row["case_id"]]["system"] += "\n" + QUOTE_CONTRIBUTION
    base_prompts = prompts
    if conditional_review:
        prompts = {key: routing_prompt(prompt) for key, prompt in base_prompts.items()}
    request = {"model": config["models"]["category"], "temperature": 0, "max_tokens": 700}
    paths = ("src/airadar/enrich/category.py", "src/airadar/enrich/classification.py",
             "src/airadar/provider/judgment.py", "evals/_shared/category_eval.py",
             "evals/_shared/metrics.py", "evals/_shared/category_metrics.py",
             "evals/content-enrichment/aihot-category-navigation/metrics.json",
             "evals/_shared/transport.py", "evals/_shared/cli.py", "src/airadar/provider/llm_gateway.py",
             "evals/_shared/human_labels.py", "evals/_shared/human_store.py",
             "evals/_shared/score_type_study.py", "evals/_shared/quote_context.py",
             "evals/_shared/identity.py", "evals/_shared/dataset.py", "evals/_shared/category_review.py")

    def identity():
        return {"code": {p: assets.file_digest(assets.ROOT / p) for p in paths},
                "behavior": {"metric_registry": check_metric_definitions(root),
                             "request": request, "transport": config["transport_identity"],
                             "rubric": rubric, "thinking": "disabled", "retry_count": 0,
                             "quote_context": context_identity, "body_limit": body_limit,
                             "quote_contribution": quote_contribution, "conditional_review": conditional_review,
                             "blind_review": blind_review, "review_guidance": review_guidance,
                             "include_source_context": include_source_context},
                "inputs": {"dataset": assets.file_digest(dataset / "manifest.json"),
                           "quote_sources": {str(p): assets.file_digest(p) for p in context_paths},
                           "cases": assets.digest(cases), "prompts": assets.digest(prompts),
                           "human_reviews": assets.file_digest(book_path) if book_path.exists() else None}}

    run, experiment = assets.create_run(root, TARGET, manifest["version"], benchmark=BENCHMARK)
    frozen = identity()
    assets.write_json(run / "config.json", config)
    assets.write_json(run / "prompt.json", {"rubric": rubric})
    assets.write_jsonl(run / "cases.jsonl", cases)
    assets.write_jsonl(run / "prompts.jsonl", [{"case_id": k, "prompt": v} for k, v in prompts.items()])
    if quote_source is not None:
        assets.write_jsonl(run / "quote-context.jsonl", contexts)
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
            row["output"] = (routing_output if conditional_review else category_output)(response["json"])
            row["reason"] = response["json"]["reason"]
            row["status"] = "ok"
            if conditional_review:
                row["needs_review"] = response["json"]["needs_review"]
                row["first_pass"] = dict(row)
                if row["needs_review"]:
                    # Includes prompt construction/archival failures, not only API errors.
                    row.update(status="error", output={})
                    second_prompt = review_prompt(base_prompts[key], response["json"],
                                                  blind=blind_review, guidance=review_guidance)
                    assets.write_json(run / "review-prompts" / (key + ".json"), second_prompt)
                    # A failed review is a failed workflow, never an implicit fallback.
                    second = chat(key)(stage="category_review", prompt=second_prompt, request=request)
                    row["review_response_json"] = json.dumps(second, ensure_ascii=False)
                    row["output"] = category_output(second["json"])
                    row["reason"] = second["json"]["reason"]
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
    result = score_categories(cases, predictions)
    first_result = None
    if conditional_review:
        first_predictions = [p.get("first_pass", p) for p in predictions]
        assets.write_jsonl(run / "first-pass-predictions.jsonl", first_predictions)
        first_result = score_categories(cases, first_predictions)
    unchanged = frozen == identity()
    if not unchanged:
        for invalid in [result] + ([first_result] if first_result is not None else []):
            invalid["complete"] = False
            for metric in invalid["metrics"].values():
                metric.update(value=None, status="not_computed", reason="object identity changed")
    if first_result is not None:
        assets.write_json(run / "first-pass-scores.json", first_result)
    assets.write_json(run / "diagnostics.json", diagnostics(cases, predictions))
    meta.update(status="complete" if result["complete"] else "incomplete",
                ended_at=assets.utc_now(), elapsed_seconds=time.monotonic() - start,
                identity_unchanged=unchanged)
    assets.archive_metrics(root, run, experiment, result, meta)
    assets.rebuild_index(root)
    return {"run": str(run), "complete": result["complete"],
            "category_accuracy": result["metrics"]["category_accuracy"], "metrics": result["metrics"]}


def main(argv=None):
    p = argparse.ArgumentParser(description="O3 分类字段离线评测；不执行标签或文本生成、不修改生产。")
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--env-file", type=Path)
    p.add_argument("--rubric", type=Path, help="UTF-8 candidate rubric; default is shared six-category A0 rubric")
    p.add_argument("--quote-source", type=Path, help="Frozen direct parent dataset for as-of original quotes; offline ablation only")
    p.add_argument("--body-limit", type=int, default=5000, help="Frozen body characters; 0 means full body (offline only)")
    p.add_argument("--quote-contribution", action="store_true", help="Separate current-post contribution from available quoted background")
    p.add_argument("--source-context", action="store_true", help="Append existing original URL, author, source kind/name; no network retrieval")
    p.add_argument("--conditional-review", action="store_true", help="Ask for semantic uncertainty and review only flagged cases; preserve same-call control")
    p.add_argument("--blind-review", action="store_true", help="With --conditional-review, withhold the first decision and reason from the second call")
    p.add_argument("--review-guidance", type=Path, help="With --conditional-review, append this UTF-8 guidance only to the second call")
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
                      smoke=a.smoke, root=a.output_root, quote_source=a.quote_source,
                      body_limit=None if a.body_limit == 0 else a.body_limit,
                      quote_contribution=a.quote_contribution, conditional_review=a.conditional_review,
                      include_source_context=a.source_context, blind_review=a.blind_review,
                      review_guidance=a.review_guidance.read_text() if a.review_guidance else "")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
