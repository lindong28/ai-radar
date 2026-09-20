"""Replay a frozen sample and retain both observed and human-priority scores."""
from __future__ import annotations

import argparse
from pathlib import Path

from evals._shared.admission_policy import apply_policy
from evals._shared.assets import ROOT, digest, file_digest, load_dataset, read_json, read_jsonl, write_json, write_jsonl
from evals._shared.cli import transport_factory
from evals._shared.human_labels import apply_labels, load_annotations
from evals._shared.human_store import read_reviews
from evals._shared.metrics import score
from evals._shared.prefilter_eval import evaluate, select_cases


def summarize(run: Path, reviews: Path, batch_ids: list[str]) -> dict:
    cases, predictions = read_jsonl(run / "cases.jsonl"), read_jsonl(run / "predictions.jsonl")
    effective, application = apply_labels(cases, load_annotations(reviews, batch_ids=batch_ids), "news-admission")
    by_id = {c["case_id"]: c for c in cases}
    projected = [apply_policy(by_id[p["case_id"]]["input"], p) for p in predictions]
    human_ids = {c["case_id"] for c in effective if c.get("provenance", {}).get("human_reference")}
    reviewed = [c for c in effective if c["case_id"] in human_ids]
    result = {
        "label_semantics": "human_override_of_observed_membership",
        "not_independent_holdout": True,
        "reviews": str(reviews.resolve()),
        "batches": [{"batch_id": b["metadata"]["batch_id"], "sha256": b["sha256"]}
                    for b in read_reviews(reviews)["batches"] if b["metadata"]["batch_id"] in batch_ids],
        "cases_sha256": file_digest(run / "cases.jsonl"),
        "predictions_sha256": file_digest(run / "predictions.jsonl"),
        "application": application,
        "views": {},
    }
    for name, rows in (("model_only", predictions), ("with_existing_policy", projected)):
        result["views"][name] = {
            "observed": score("O1", cases, rows),
            "human_priority": score("O1", effective, rows),
            "human_only": score("O1", reviewed, [p for p in rows if p["case_id"] in human_ids]),
        }
    write_jsonl(run / "human-effective-cases.jsonl", effective)
    write_jsonl(run / "policy-predictions.jsonl", projected)
    result["human_effective_cases_sha256"] = file_digest(run / "human-effective-cases.jsonl")
    result["policy_predictions_sha256"] = file_digest(run / "policy-predictions.jsonl")
    write_json(run / "human-feedback-scores.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--prompt", required=True, type=Path)
    parser.add_argument("--reviews", type=Path, default=ROOT / "human-evals/news-admission/reviews.json")
    parser.add_argument("--batch-id", action="append", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--reuse", type=Path, help="Reuse successful predictions of the same frozen identity")
    args = parser.parse_args()
    source = read_json(args.source_run / "started.json")
    cases = read_jsonl(args.source_run / "cases.jsonl")
    dataset = Path(source["dataset"])
    _, pool = load_dataset(dataset, "news-admission")
    exclusions = tuple(Path(row["run"]) for row in source["selection"]["exclusions"])
    excluded = frozenset(c["case_id"] for path in exclusions for c in read_jsonl(path / "cases.jsonl"))
    selected = select_cases(pool, source["split"], source["selection"]["limit"], source["selection"]["seed"], excluded)
    if digest(sorted(cases, key=lambda c: c["case_id"])) != digest(sorted(selected, key=lambda c: c["case_id"])):
        raise ValueError("source sample cannot be reproduced; no model call made")
    # Validate human input bindings before any chargeable calls.
    apply_labels(cases, load_annotations(args.reviews, batch_ids=args.batch_id), "news-admission")
    config = read_json(args.config)
    result = evaluate(dataset, config=config, split=source["split"], limit=source["selection"]["limit"],
                      seed=source["selection"]["seed"], chat_factory=transport_factory(config, args.env_file),
                      label=args.label, workers=args.workers, prompt=read_json(args.prompt),
                      exclude_runs=exclusions, benchmark=source["benchmark"], reuse=args.reuse)
    run = Path(result["run"])
    summary = summarize(run, args.reviews, args.batch_id)
    print({"run": str(run), "complete": result["complete"],
           "human_priority": {k: v["human_priority"]["metrics"] for k, v in summary["views"].items()}})
    if not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
