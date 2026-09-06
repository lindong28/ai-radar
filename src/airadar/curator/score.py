from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .weights import CORE_DIMENSIONS, Weights

TIER_MULTIPLIERS = {"T1": 1.25, "T1.5": 1.0, "T2": 0.75}


@dataclass(frozen=True)
class ScoredCandidate:
    eval_id: int
    item_id: str
    content_hash: str
    url: str
    published_at: str
    weighted_score: float
    reason: dict[str, Any]
    source_id: str = ""
    kind: str = "feed"


def tier_multiplier(tier: str) -> float:
    return TIER_MULTIPLIERS.get(tier, 1.0)


def weighted_score(numeric: dict[str, Any], weights: Weights, tier: str) -> float:
    """Weight one item's signals, rescaling over whichever of them the row actually carries.

    Every curation run recomputes this from each stored `numeric_json`, including rows written
    years before a dimension existed. Reading a missing dimension as 0.0 is a different claim
    from reading it as absent, and the difference is not small: give a new signal a third of the
    weight and the entire archive sinks below anything scored after it, on every run, with no
    test able to notice because each new row looks correct. So a dimension the row does not
    carry is dropped and the surviving weights are scaled back up to their original total.

    CORE_DIMENSIONS stay required. Production has emitted all five since the first scoring run,
    so a row missing one of them is a defect rather than an old row, and it should still raise.
    """
    weighted = {dimension: weight for dimension, weight in weights.as_dict().items() if weight > 0}
    # Every core dimension, not just the weighted ones. Guarding only the weighted set made the
    # check depend on the vector in force: under the fitted vector it required density and
    # authority alone, and a provider that stopped emitting relevance, recency or engineering
    # would have gone unnoticed -- which is the failure this guard exists to catch.
    for dimension in CORE_DIMENSIONS:
        if numeric.get(dimension) is None:
            raise KeyError(dimension)
    present = {dimension: float(numeric[dimension]) for dimension in weighted if numeric.get(dimension) is not None}
    kept = sum(weighted[dimension] for dimension in present)
    if kept <= 0:
        raise ValueError("no weighted dimension present on this row")
    raw = sum(present[dimension] * weighted[dimension] for dimension in present) * (sum(weighted.values()) / kept)
    return round(raw * (tier_multiplier(tier) if weights.uses_tier_multiplier else 1.0), 4)
