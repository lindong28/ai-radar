"""Human-priority classification view; never mutates AIHOT references."""
from __future__ import annotations

from pathlib import Path

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS

from . import assets
from .category_metrics import score_categories
from .human_store import read_reviews
from .metrics import _metric


def accepted_categories(path: Path) -> dict[tuple[str, str], frozenset[str]]:
    book = read_reviews(path)
    if book["metadata"]["target"] != "content-enrichment":
        raise ValueError("not content-enrichment reviews")
    resolved = {}
    for batch in book["batches"]:
        if batch["metadata"].get("kind") != "category-acceptable-labels":
            continue
        for row in batch["data"]["category_judgments"]:
            values = row["value"]
            if (row["provenance"] != "user" or row["field"] != "acceptable_categories"
                    or not isinstance(values, list) or not values
                    or any(not isinstance(v, str) or v not in PRIMARY_CATEGORY_SLUGS.values() for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError("not an explicit valid human category judgment")
            key = (row["case_id"], row["input_identity"])
            value = frozenset(values)
            if key in resolved and resolved[key] != value:
                raise ValueError(f"conflicting human category sets: {row['case_id']}")
            resolved[key] = value
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
