"""Experimental pointwise news-value scoring; not wired to production."""
from airadar.provider.judgment import require_reason_first

# Integer coefficients map three 0..10 judgments directly onto 0..100.
SEMANTIC_WEIGHTS = {"impact": 5, "information_gain": 3, "evidence": 2}


def semantic_score(payload: dict) -> int:
    """Validate model-emitted reasons, then combine without learned calibration."""
    require_reason_first(payload, "impact")
    if set(payload) != {"reason", *SEMANTIC_WEIGHTS}:
        raise ValueError("semantic response requires reason and exactly three dimensions")
    total = 0
    for dimension, weight in SEMANTIC_WEIGHTS.items():
        judgment = payload[dimension]
        require_reason_first(judgment, "score")
        if set(judgment) != {"reason", "score"}:
            raise ValueError(f"{dimension} requires only reason and score")
        value = judgment["score"]
        if type(value) is not int or not 0 <= value <= 10:
            raise ValueError(f"{dimension}.score must be an integer in 0..10")
        total += weight * value
    return total
