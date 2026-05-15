from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .weights import DIMENSIONS, Weights

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


def tier_multiplier(tier: str) -> float:
    return TIER_MULTIPLIERS.get(tier, 1.0)


def weighted_score(numeric: dict[str, Any], weights: Weights, tier: str) -> float:
    values = {dimension: float(numeric[dimension]) for dimension in DIMENSIONS}
    raw = sum(values[dimension] * weights.as_dict()[dimension] for dimension in DIMENSIONS)
    return round(raw * tier_multiplier(tier), 4)
