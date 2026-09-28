"""Human-priority classification view; never mutates AIHOT references."""
from __future__ import annotations

from pathlib import Path

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS

from . import assets
from .category_metrics import score_categories
from .human_store import read_reviews
from .metrics import _metric


def accepted_categories(path: Path) -> dict[tuple[str, str], frozenset[str]]:
    return resolve_categories(read_reviews(path))


def resolve_categories(book: dict) -> dict[tuple[str, str], frozenset[str]]:
    """Resolve explicit, input-bound revisions without inferring authority from time."""
    if book["metadata"]["target"] != "content-enrichment":
        raise ValueError("not content-enrichment reviews")
    resolved, owners, previous = {}, {}, {}
    for batch in book["batches"]:
        if batch["metadata"].get("kind") != "category-acceptable-labels":
            continue
        bid = batch["metadata"]["batch_id"]
        supersedes = batch["metadata"].get("supersedes", [])
        replaced = set()
        for ref in supersedes:
            if (not isinstance(ref, dict) or set(ref) != {"batch_id", "sha256"}
                    or previous.get(ref["batch_id"]) != ref["sha256"]):
                raise ValueError("supersedes must bind a preceding category batch and its hash")
            replaced.add(ref["batch_id"])
        for row in batch["data"]["category_judgments"]:
            values = row["value"]
            if (row["provenance"] != "user" or row["field"] != "acceptable_categories"
                    or not isinstance(values, list) or not values
                    or any(not isinstance(v, str) or v not in PRIMARY_CATEGORY_SLUGS.values() for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError("not an explicit valid human category judgment")
            key = (row["case_id"], row["input_identity"])
            value = frozenset(values)
            active = owners.get(key, set()) - replaced
            if key in resolved and resolved[key] != value and active:
                raise ValueError(f"conflicting human category sets: {row['case_id']}")
            resolved[key] = value
            owners[key] = active | {bid}
        previous[bid] = batch["sha256"]
    return resolved


def score_human_categories(cases: list[dict], predictions: list[dict], path: Path) -> dict:
    original = score_categories(cases, predictions)  # Validates IDs, enums and failures.
    accepted = accepted_categories(path)
    human_ids = {key[0] for key in accepted}
    singleton, traces = [], []
    for case, trace in zip(cases, original["per_case"], strict=True):
        key = (case["case_id"], assets.digest(case["input"]))
        if case["case_id"] in human_ids and key not in accepted:
            raise ValueError(f"human category input mismatch: {case['case_id']}")
        labels = accepted.get(key, frozenset([case["reference"]["category"]]))
        predicted = trace["fields"]["category"]["prediction"]
        traces.append({"case_id": case["case_id"], "reference": sorted(labels),
                       "reference_source": "human" if key in accepted else "aihot",
                       "prediction": predicted, "correct": predicted in labels})
        if len(labels) == 1:
            singleton.append({**case, "reference": {"category": next(iter(labels))}})
    selected = {c["case_id"] for c in singleton}
    result = score_categories(singleton, [p for p in predictions if p["case_id"] in selected])
    human = [t for t in traces if t["reference_source"] == "human"]
    result.update(complete=original["complete"], counts=original["counts"], per_case=traces,
                  label_policy="human acceptable set first; otherwise original AIHOT",
                  reviews_sha256=assets.file_digest(path),
                  precision_recall_scope={"case_count": len(singleton), "case_ids": sorted(selected),
                                          "excluded_multilabel": len(cases) - len(singleton)})
    result["metrics"]["category_accuracy"] = _metric(
        sum(t["correct"] for t in traces) / len(traces) if traces else None, len(traces))
    result["human_reviewed"] = {"count": len(human), "correct": sum(t["correct"] for t in human),
                                "accuracy": sum(t["correct"] for t in human) / len(human) if human else None}
    return result
