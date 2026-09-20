import pytest

from airadar.scorer.five import FIVE_WEIGHTS, five_score, validated_weights


@pytest.mark.parametrize("value", [0, 1, 5, 10])
def test_full_domain_uniform_scores(value):
    assert five_score({"reason": "test evidence", **dict.fromkeys(FIVE_WEIGHTS, value)}) == value * 10


def test_different_dimensions_and_explicit_weight_change():
    payload = {"reason": "test evidence", "impact": 8, "novelty": 3,
               "substance": 2, "authority": 9, "relevance": 10}
    assert five_score(payload) == 58
    assert five_score(payload, {**dict.fromkeys(FIVE_WEIGHTS, 0), "impact": 100}) == 80
    assert five_score({**payload, "impact": 1, "novelty": 0, "substance": 0, "authority": 0, "relevance": 0}) == 4


@pytest.mark.parametrize("field", list(FIVE_WEIGHTS))
@pytest.mark.parametrize("bad", [True, 5.5, -1, 11, "5", None, float("nan")])
def test_reject_invalid_dimension(field, bad):
    with pytest.raises(ValueError):
        five_score({"reason": "evidence", **dict.fromkeys(FIVE_WEIGHTS, 5), field: bad})


@pytest.mark.parametrize("payload", [dict.fromkeys(FIVE_WEIGHTS, 5),
    {**dict.fromkeys(FIVE_WEIGHTS, 5), "reason": "late"},
    {"reason": "", **dict.fromkeys(FIVE_WEIGHTS, 5)},
    {"reason": "ok", **dict.fromkeys(FIVE_WEIGHTS, 5), "score": 50}])
def test_reason_order_and_exact_fields(payload):
    with pytest.raises(ValueError):
        five_score(payload)


@pytest.mark.parametrize("weights", [[], {}, {"impact": 100},
    {**FIVE_WEIGHTS, "impact": -1}, {**FIVE_WEIGHTS, "impact": True},
    {**FIVE_WEIGHTS, "impact": float("inf")}, {**FIVE_WEIGHTS, "impact": 36}])
def test_reject_invalid_weights(weights):
    with pytest.raises(ValueError):
        validated_weights(weights)
