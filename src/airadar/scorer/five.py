"""Author-inspired five-dimensional hypothesis, offline only; not AIHOT's formula."""
import math

from airadar.provider.judgment import require_reason_first

FIVE_WEIGHTS = {"impact": 35, "novelty": 20, "substance": 25, "authority": 10, "relevance": 10}


def validated_weights(weights: dict | None = None) -> dict:
    """All five contributions are explicit percentages, with no posthoc mapping."""
    weights = FIVE_WEIGHTS if weights is None else weights
    if not isinstance(weights, dict) or set(weights) != set(FIVE_WEIGHTS):
        raise ValueError("five_weights requires exactly the five dimension names")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError("five_weights must be finite nonnegative percentages")
    if not math.isclose(sum(weights.values()), 100, rel_tol=0, abs_tol=1e-9):
        raise ValueError("five_weights percentages must sum to 100")
    return dict(weights)


def five_score(payload: dict, weights: dict | None = None) -> int:
    weights = validated_weights(weights)
    require_reason_first(payload, "impact")
    if set(payload) != {"reason", *FIVE_WEIGHTS}:
        raise ValueError("five response requires reason and exactly five dimensions")
    for field in FIVE_WEIGHTS:
        if type(payload[field]) is not int or not 0 <= payload[field] <= 10:
            raise ValueError(f"{field} must be an integer in 0..10")
    return math.floor(sum(payload[k] * v for k, v in weights.items()) / 10 + 0.5)
