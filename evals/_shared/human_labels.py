"""Explicit human-reference overlays; never mutate historical AIHOT evidence.

Only an explicit import of an actual user ballot creates human authority.
The overlay is applied after building/selecting cases, never inside inference.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .assets import digest, file_digest, read_json, read_jsonl, write_json, write_jsonl
from .identity import substantive_hash
from .metrics import _valid, score

FIELDS = {
    "news-admission": ("member",),
    "visible-score": ("score",),
    "content-enrichment": ("category", "tags", "title", "summary", "reason"),
    "featured-members": ("featured",),
}
POLICY = "human-reference-priority-v1"


def input_identity(case: dict, target: str) -> str:
    raw = case["input"]
    if not isinstance(raw, dict):
        raise ValueError("human reference requires an executable input")
    # Featured decisions can depend on the complete candidate group / scores.
    if target == "featured-members":
        return digest(raw)
    identity = {"content": substantive_hash(raw, target)}
    if target == "visible-score":
        identity["tier"] = raw.get("tier")
    if target == "news-admission":
        refs = (raw.get("extra") or {}).get("referenced_tweets") or []
        identity["conversation"] = refs
        identity["source_kind"] = raw.get("source_kind")
        identity["tier"] = raw.get("tier")
    return digest(identity)


def _index(rows: list[dict]) -> dict:
    indexed = {row["case_id"]: row for row in rows}
    if len(indexed) != len(rows) or not all(isinstance(k, str) and k for k in indexed):
        raise ValueError("duplicate or invalid case_id")
    return indexed


def apply_labels(cases: list[dict], annotations: list[dict], target: str) -> tuple[list[dict], dict]:
    """Human values win per field, including explicit confirmation of old gold.

    Conflicting human votes and changed inputs require adjudication, not an
    arbitrary last-write-wins rule. Missing cases remain in the annotation bank.
    """
    fields = FIELDS[target]
    indexed = _index(cases)
    selected = {}
    unused = set()
    for row in annotations:
        if row["target"] != target:
            continue
        key = (row["case_id"], row["field"])
        if row.get("provenance") != "user" or key[1] not in fields or not _valid(key[1], row["value"]):
            raise ValueError("invalid human reference annotation")
        if not isinstance(row.get("reason"), str) or not row.get("ballot_sha256"):
            raise ValueError("human annotation lacks its original ballot/reason")
        if key[0] not in indexed:
            unused.add(key[0])
            continue
        case = indexed[key[0]]
        if input_identity(case, target) != row["input_identity"]:
            raise ValueError(f"human input changed; adjudication required: {key[0]}")
        if key in selected and selected[key]["value"] != row["value"]:
            raise ValueError(f"conflicting human labels; adjudication required: {key}")
        selected[key] = row
    result, changed = deepcopy(cases), 0
    for case in result:
        for field in fields:
            row = selected.get((case["case_id"], field))
            if row is None:
                continue
            original = case["reference"].get(field)
            changed += original != row["value"]
            case["reference"][field] = deepcopy(row["value"])
            case.setdefault("provenance", {}).setdefault("human_reference", {})[field] = {
                "original_reference": original, "annotation": deepcopy(row),
            }
    return result, {"policy": POLICY, "case_count": len(cases),
                    "human_case_count": len({k[0] for k in selected}),
                    "human_field_count": len(selected), "changed_field_count": changed,
                    "absent_case_ids": sorted(unused)}


def import_prefilter_review(feedback: Path, run: Path, output: Path) -> dict:
    """Import the original-C11 review export and freeze its displayed context."""
    ballot = read_json(feedback)
    expected = {"format": "ai-radar-prefilter-human-review-v1",
                "target_prediction": "original_c11",
                "label_semantics": "human_override_of_observed_membership"}
    if any(ballot.get(k) != v for k, v in expected.items()):
        raise ValueError("unsupported review contract")
    if ballot["source_run"] != "/".join(run.parts[-2:]):
        raise ValueError("review source run mismatch")
    for name in ("cases", "predictions"):
        if file_digest(run / f"{name}.jsonl") != ballot[f"source_{name}_sha256"]:
            raise ValueError(f"review {name} hash mismatch")
    cases, predictions = read_jsonl(run / "cases.jsonl"), read_jsonl(run / "predictions.jsonl")
    case_map, pred_map = _index(cases), _index(predictions)
    errors = read_json(run / "review/public/errors.json")
    prompts = read_json(run / "review/public/prompts.json")
    review_ids = {e["case"]["case_id"] for e in errors}
    votes = _index(ballot["judgments"])
    if set(votes) != review_ids or len(review_ids) != len(errors):
        raise ValueError("review must cover exactly the displayed cases")
    for error in errors:
        key = error["case"]["case_id"]
        if error["case"] != case_map[key] or key not in prompts:
            raise ValueError("displayed case does not match frozen run")
        if pred_map[key]["output"]["member"] == case_map[key]["reference"]["member"]:
            raise ValueError("review case is not an original prediction error")
    annotations = []
    for key, vote in votes.items():
        judgment = vote["judgment"]
        if judgment not in {"keep", "positive", "negative", "pending", "uncertain"}:
            raise ValueError("invalid judgment")
        if not isinstance(vote.get("reason"), str):
            raise ValueError("reason must preserve the user's text")
        if judgment in {"pending", "uncertain"}:
            continue
        case = case_map[key]
        value = case["reference"]["member"] if judgment == "keep" else judgment == "positive"
        annotations.append({"target": "news-admission", "case_id": key, "field": "member",
                            "value": value, "input_identity": input_identity(case, "news-admission"),
                            "provenance": "user", "judgment": judgment, "reason": vote["reason"],
                            "ballot_sha256": file_digest(feedback)})
    effective, coverage = apply_labels(cases, annotations, "news-admission")
    original_scores = score("O1", cases, predictions)
    effective_scores = score("O1", effective, predictions)
    # Validate completely before creating an append-only batch directory.
    output.mkdir(parents=True, exist_ok=False)
    with (output / "feedback.json").open("xb") as stream:
        stream.write(feedback.read_bytes())
    write_json(output / "review-context.json", {"errors": errors, "prompts": prompts})
    write_jsonl(output / "source-cases.jsonl", cases)
    write_jsonl(output / "source-predictions.jsonl", predictions)
    write_jsonl(output / "annotations.jsonl", annotations)
    write_jsonl(output / "effective-cases.jsonl", effective)
    write_json(output / "original-scores.json", original_scores)
    write_json(output / "human-priority-scores.json", effective_scores)
    manifest = {"policy": POLICY, "source_run": str(run.resolve()),
                "source_cases_sha256": ballot["source_cases_sha256"],
                "source_predictions_sha256": ballot["source_predictions_sha256"],
                "ballot_sha256": file_digest(feedback),
                "user_authority": "explicit user message with exported ballot, 2026-09-20",
                "scope": "original C11 dev300 label-only rescore, not a new model run or holdout",
                "judgment_counts": dict(Counter(v["judgment"] for v in votes.values())),
                "coverage": coverage,
                "files": {p.name: file_digest(p) for p in sorted(output.iterdir())}}
    write_json(output / "manifest.json", manifest)
    return manifest


def load_annotations(batch: Path) -> list[dict]:
    manifest = read_json(batch / "manifest.json")
    if manifest["policy"] != POLICY or "annotations.jsonl" not in manifest["files"]:
        raise ValueError("not a human-reference batch")
    for name, expected in manifest["files"].items():
        path = (batch / name).resolve()
        if not path.is_relative_to(batch.resolve()) or file_digest(path) != expected:
            raise ValueError(f"human batch integrity mismatch: {name}")
    return read_jsonl(batch / "annotations.jsonl")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("import-prefilter")
    ingest.add_argument("--feedback", type=Path, required=True)
    ingest.add_argument("--run", type=Path, required=True)
    ingest.add_argument("--output", type=Path, required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--cases", type=Path, required=True)
    apply.add_argument("--target", choices=list(FIELDS), required=True)
    apply.add_argument("--batch", type=Path, action="append", required=True)
    apply.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "import-prefilter":
        import_prefilter_review(args.feedback, args.run, args.output)
    else:
        annotations = [r for batch in args.batch for r in load_annotations(batch)]
        cases, coverage = apply_labels(read_jsonl(args.cases), annotations, args.target)
        args.output.mkdir(parents=True, exist_ok=False)
        write_jsonl(args.output / "cases.jsonl", cases)
        write_json(args.output / "manifest.json", {
            **coverage, "target": args.target,
            "source_cases_sha256": file_digest(args.cases),
            "batches": [{"path": str(b.resolve()), "manifest_sha256": file_digest(b / "manifest.json")}
                        for b in args.batch],
            "files": {"cases.jsonl": file_digest(args.output / "cases.jsonl")},
        })


if __name__ == "__main__":
    main()
