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
