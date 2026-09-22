import pytest

from evals._shared.category_review import review_prompt, routing_output, routing_prompt


@pytest.mark.parametrize("flag", [True, False])
def test_boolean_route_and_category(flag):
    payload = {"reason": "Evidence", "needs_review": flag, "primary_category": "model"}
    assert routing_output(payload) == {"category": "ai-models"}


@pytest.mark.parametrize("payload", [
    {"reason": "why", "primary_category": "model"},
    {"reason": "why", "needs_review": "false", "primary_category": "model"},
    {"reason": "why", "needs_review": 0, "primary_category": "model"},
    {"reason": "why", "needs_review": True, "primary_category": "unknown"},
    {"primary_category": "model", "reason": "why", "needs_review": True},
    {"reason": "", "needs_review": True, "primary_category": "model"},
])
def test_invalid_routing_is_not_silently_accepted(payload):
    with pytest.raises(ValueError):
        routing_output(payload)


def test_prompt_projection_preserves_original_and_first_decision():
    original = {"system": "rubric", "user": "original article"}
    first = {"reason": "evidence", "needs_review": True, "primary_category": "paper"}
    assert routing_prompt(original)["user"] == original["user"]
    reviewed = review_prompt(original, first)
    assert reviewed["user"].startswith(original["user"])
    assert '"primary_category": "paper"' in reviewed["user"]
    assert original == {"system": "rubric", "user": "original article"}


@pytest.mark.parametrize("guidance", ["", "local evidence guidance"])
def test_blind_review_ignores_first_decision_and_preserves_original(guidance):
    original = {"system": "rubric", "user": "original article"}
    first = {"reason": "INITIAL_SENTINEL", "needs_review": True, "primary_category": "paper"}
    result = review_prompt(original, first, blind=True, guidance=guidance)
    other = review_prompt(original, {"reason": "DIFFERENT", "primary_category": "model"},
                          blind=True, guidance=guidance)
    assert result == other
    assert result["user"] == original["user"]
    assert "INITIAL_SENTINEL" not in str(result)
    assert result["system"].startswith(original["system"])
    if guidance:
        assert result["system"].endswith(guidance)
    assert original == {"system": "rubric", "user": "original article"}
