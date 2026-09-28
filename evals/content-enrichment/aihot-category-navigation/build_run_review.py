"""Snapshot the human-labelled errors of one frozen category run; no inference."""
from __future__ import annotations

import argparse
from pathlib import Path

from human_review import LABELS, RUN_FORMAT, read, sha, validate, write_new
from evals._shared.assets import digest, read_jsonl, utc_now
from evals._shared.category_human import score_human_categories


def build(run: Path, metadata: Path, candidate: str, batch_id: str) -> dict:
    cases = read_jsonl(run / "cases.jsonl")
    predictions = read_jsonl(run / "predictions.jsonl")
    prompts = {x["case_id"]: x["prompt"] for x in read_jsonl(run / "prompts.jsonl")}
    scores = read(run / "human-priority-scores.json")
    reviews = read(run / "human-reviews.json")
    meta = read(metadata)
    actual = score_human_categories(cases, predictions, run / "human-reviews.json")
    if actual["per_case"] != scores["per_case"]:
        raise ValueError("human scores disagree with source cases/predictions/reviews")
    selected = {x["case_id"] for x in scores["per_case"]
                if x["reference_source"] == "human" and not x["correct"]}
    prediction_map = {x["case_id"]: x for x in predictions}
    records = []
    rid = f"{run.parent.name}/{run.name}"
    for c in cases:
        cid = c["case_id"]
        if cid not in selected:
            continue
        p, prompt = prediction_map[cid], prompts[cid]
        prior = []
        for batch in reviews["batches"]:
            for j in batch["data"].get("category_judgments", []):
                if j["case_id"] == cid and j["input_identity"] == digest(c["input"]):
                    prior.append({"batch_id": batch["metadata"]["batch_id"],
                                  "input_sha256": j["input_identity"],
                                  "acceptable_labels": j["value"], "reason": j["reason"]})
        observation = {"run_id": rid, "reason": p.get("reason"),
                       "label": p.get("output", {}).get("category"),
                       "status": p["status"], "prompt_sha256": digest(prompt)}
        if p["status"] != "ok":
            observation["error_type"] = p.get("error_type")
            observation["attempt_id"] = p.get("attempt_id")
            attempt = run / "attempts" / f"{p['attempt_id']}.json"
            observation["attempt_sha256"] = sha(attempt)
            evidence = read(attempt)
            observation["attempt_evidence"] = {
                k: evidence.get(k) for k in ("status", "error_type", "error_code", "actual_model")}
            observation["attempt_evidence"]["choices"] = evidence.get("raw", {}).get("choices", [])
        records.append({"case_id": cid, "input": c["input"],
                        "input_sha256": digest(c["input"]),
                        "aihot": {"label": c["reference"]["category"],
                                  "reason": None, "reason_status": "not_recorded"},
                        "prompts": {digest(prompt): prompt},
                        "candidate_observations": [observation], "prior_human_reviews": prior})
    names = ["cases.jsonl", "predictions.jsonl", "prompts.jsonl",
             "human-priority-scores.json", "human-reviews.json"]
    material = {"metadata": {"format": RUN_FORMAT, "batch_id": batch_id,
        "created_at": utc_now(), "target": meta["target"], "benchmark": meta["benchmark"],
        "version": meta["version"], "scope": "single-run-human-labelled-errors",
        "label_semantics": "acceptable_alternatives_for_single_category",
        "authority": "explicit_user_review_over_nonhuman_reference_for_same_input_and_field"},
        "labels": LABELS, "source": {"candidate": {"id": candidate, "label": meta["label"]},
            "runs": {rid: {"model": meta["object_identity"]["behavior"]["request"]["model"],
                "source_path": "/".join(run.parts[-6:]), "hashes": {n: sha(run / n) for n in names},
                "metadata_sha256": sha(metadata)}}}, "cases": records, "model_reviews": []}
    validate(material)
    return material


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    write_new(args.output, build(args.run, args.metadata, args.candidate, args.batch_id))
