"""Deterministic six-category metrics, including failed predictions."""
from __future__ import annotations

from pathlib import Path

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS

from . import assets
from .metrics import _metric, score

DEFINITIONS = "evals/content-enrichment/aihot-category-navigation/metrics.json"


def check_metric_definitions(root: Path) -> str:
    """The archive consumer must use the same registry as this scorer checkout."""
    actual = assets.file_digest(root / DEFINITIONS)
    if actual != assets.file_digest(assets.ROOT / DEFINITIONS):
        raise ValueError("output-root category metric definitions differ from scorer checkout")
    return actual


def score_categories(cases: list[dict], predictions: list[dict]) -> dict:
    """Score an already selected, executable category-only cohort, without filtering."""
    labels = set(PRIMARY_CATEGORY_SLUGS.values())
    for case in cases:
        reference = case.get("reference")
        if (not isinstance(case.get("input"), dict) or not isinstance(reference, dict)
                or set(reference) != {"category"}
                or not isinstance(reference["category"], str)
                or reference["category"] not in labels):
            raise ValueError("category scoring requires executable six-class category-only cases")
    # Unknown enum values are failed outputs, not a seventh predicted class.
    normalized = []
    for prediction in predictions:
        output = prediction.get("output")
        valid = (isinstance(output, dict) and isinstance(output.get("category"), str)
                 and output["category"] in labels)
        normalized.append(prediction if valid else {**prediction, "status": "error"})
    result = score("O3", cases, normalized)
    result["metrics"] = {"category_accuracy": result["metrics"]["category_accuracy"]}
    by_id = {p["case_id"]: p for p in normalized}
    counts = {slug: {"tp": 0, "fp": 0, "fn": 0} for slug in PRIMARY_CATEGORY_SLUGS.values()}
    for case, trace in zip(cases, result["per_case"], strict=True):
        gold = case["reference"]["category"]
        predicted = (by_id[case["case_id"]]["output"]["category"]
                     if trace["fields"]["category"]["prediction_valid"] else None)
        trace["fields"]["category"].update(reference=gold, prediction=predicted)
        if predicted == gold:
            counts[gold]["tp"] += 1
        else:
            counts[gold]["fn"] += 1
            if predicted is not None:
                counts[predicted]["fp"] += 1
    result["category_counts"] = counts
    for name, slug in PRIMARY_CATEGORY_SLUGS.items():
        tally = counts[slug]
        for metric, denominator in (("precision", tally["tp"] + tally["fp"]),
                                    ("recall", tally["tp"] + tally["fn"])):
            result["metrics"][f"category_{name}_{metric}"] = _metric(
                tally["tp"] / denominator if denominator else None,
                denominator, "zero denominator" if not denominator else "")
    return result
