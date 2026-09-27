"""Compare frozen category runs; only failed calls may be replaced on replay."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import assets
from .category_human import score_human_categories
from .category_metrics import score_categories


def stable_behavior(identity: dict) -> dict:
    behavior = {k: v for k, v in identity["behavior"].items() if k != "request_interval"}
    if behavior.get("quote_context"):
        behavior["quote_context"] = {k: v for k, v in behavior["quote_context"].items()
                                     if k != "resolved_sha256"}
    return behavior


def collect(runs: list[Path]) -> tuple[dict, dict, dict, list]:
    cases, predictions, prompts, sources, behavior = {}, {}, {}, [], None
    for run in runs:
        meta = assets.read_json(run / "started.json")
        root = run.parents[5]
        terminal = assets.read_json(root / "experiments" / run.relative_to(root / "runs") / "metadata.json")
        if not terminal.get("identity_unchanged") or terminal["object_identity"] != meta["object_identity"]:
            raise ValueError("run lacks unchanged terminal identity")
        expected = meta["object_identity"]
        current_behavior = stable_behavior(expected)
        quote_identity = expected["behavior"].get("quote_context")
        if quote_identity and assets.digest(assets.read_jsonl(run / "quote-context.jsonl")) != quote_identity["resolved_sha256"]:
            raise ValueError("run quote context identity drift")
        if behavior is not None and current_behavior != behavior:
            raise ValueError("merged runs change inference behavior")
        behavior = current_behavior
        rows = assets.read_jsonl(run / "cases.jsonl")
        prompt_rows = assets.read_jsonl(run / "prompts.jsonl")
        run_prompts = {r["case_id"]: r["prompt"] for r in prompt_rows}
        if assets.digest(rows) != expected["inputs"]["cases"] or assets.digest(run_prompts) != expected["inputs"]["prompts"]:
            raise ValueError("run input or prompt identity drift")
        results = assets.read_jsonl(run / "predictions.jsonl")
        score_categories(rows, results)  # Reject duplicate/foreign output identities.
        for c in rows:
            key = c["case_id"]
            if key in cases and (cases[key] != c or prompts[key] != run_prompts[key]):
                raise ValueError("retry changes case or prompt")
            cases[key], prompts[key] = c, run_prompts[key]
        for p in results:
            key = p["case_id"]
            if key in predictions and predictions[key]["status"] == "ok":
                raise ValueError("refuse to resample an already successful prediction")
            item = run / "items" / (key + ".json")
            if not item.exists() or assets.read_json(item) != p:
                raise ValueError("prediction differs from archived response item")
            predictions[key] = p
        sources.append({"run": str(run), "files": {name: assets.file_digest(run / name)
            for name in ("started.json", "cases.jsonl", "prompts.jsonl", "predictions.jsonl")}})
    return cases, predictions, prompts, sources


def scores(cases, predictions, reviews):
    ordered = [cases[k] for k in sorted(cases)]
    outputs = [predictions[k] for k in sorted(predictions)]
    return {"aihot": score_categories(ordered, outputs),
            "human_priority": score_human_categories(ordered, outputs, reviews)}


def paired(before, after, ids=None):
    a = {r["case_id"]: r for r in before["human_priority"]["per_case"]}
    b = {r["case_id"]: r for r in after["human_priority"]["per_case"]}
    selected = set(a) if ids is None else set(ids)
    return {"count": len(selected), "before_correct": sum(a[k]["correct"] for k in selected),
            "after_correct": sum(b[k]["correct"] for k in selected),
            "fixes": [k for k in sorted(selected) if not a[k]["correct"] and b[k]["correct"]],
            "regressions": [k for k in sorted(selected) if a[k]["correct"] and not b[k]["correct"]]}


def compare(baseline, candidate, *, reviews: Path, body_runs=None, body_context=None):
    bc, bp, _, bs = collect(baseline)
    cc, cp, _, cs = collect(candidate)
    if bc != cc:
        raise ValueError("comparison requires identical full cohorts")
    result = {"format": "ai-radar-category-human-comparison-v1", "created_at": assets.utc_now(),
              "reviews_sha256": assets.file_digest(reviews),
              "baseline": {"sources": bs, **scores(bc, bp, reviews)},
              "candidate": {"sources": cs, **scores(cc, cp, reviews)}}
    human_ids = {r["case_id"] for r in result["candidate"]["human_priority"]["per_case"] if r["reference_source"] == "human"}
    result["candidate_vs_baseline"] = {
        "all": paired(result["baseline"], result["candidate"]),
        "human_reviewed": paired(result["baseline"], result["candidate"], human_ids),
        "remaining": paired(result["baseline"], result["candidate"], set(cc) - human_ids)}
    if body_runs:
        parent_identity = assets.read_json(candidate[0] / "started.json")["object_identity"]
        for run in body_runs:
            child_identity = assets.read_json(run / "started.json")["object_identity"]
            if (stable_behavior(child_identity) != stable_behavior(parent_identity)
                    or child_identity["inputs"].get("body_context") != assets.file_digest(body_context)):
                raise ValueError("body ablation changes behavior or uses another supplement")
        dc, dp, _, ds = collect(body_runs)
        changed = {r["case_id"] for r in assets.read_jsonl(body_context) if r["status"] == "available"}
        if set(dc) != changed or any(cc.get(k) != c for k, c in dc.items()):
            raise ValueError("body ablation must rerun exactly the changed-input cohort")
        if set(dp) != changed:
            raise ValueError("body ablation lacks predictions; refuse fallback to parent results")
        result["body_candidate"] = {"sources": ds, "reused_from": "candidate", "reused_cases": len(cc) - len(dc),
            "body_context_sha256": assets.file_digest(body_context), **scores(cc, {**cp, **dp}, reviews)}
        result["body_vs_candidate"] = {"all": paired(result["candidate"], result["body_candidate"]),
            "changed_input": paired(result["candidate"], result["body_candidate"], changed),
            "reused_unchanged": paired(result["candidate"], result["body_candidate"], set(cc) - changed)}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline-runs", nargs="+", type=Path, required=True)
    p.add_argument("--candidate-runs", nargs="+", type=Path, required=True)
    p.add_argument("--body-runs", nargs="+", type=Path)
    p.add_argument("--body-context", type=Path)
    p.add_argument("--human-reviews", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.body_runs and not a.body_context:
        p.error("body runs require frozen --body-context")
    result = compare(a.baseline_runs, a.candidate_runs, reviews=a.human_reviews,
                     body_runs=a.body_runs, body_context=a.body_context)
    assets.write_json(a.output, result)
    print(a.output)


if __name__ == "__main__":
    main()
