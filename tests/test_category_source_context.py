"""Source context is an opt-in projection, not a source-to-label lookup."""
import json
from pathlib import Path

import pytest

from airadar.enrich.category import render_category_prompt
from evals._shared import assets
from evals._shared.category_eval import evaluate, source_context
from tests.test_category_quotes import dataset as dataset


@pytest.mark.parametrize("raw,expected", [
    ({}, {}),
    ({"url": None, "author": [], "source_name": " "}, {}),
    ({"url": "https://arxiv.org/abs/2609.1", "author": "A", "source_kind": "web",
      "source_name": "Papers", "category": "GOLD", "extra": {"tags": ["GOLD"]},
      "tier": "GOLD", "score": "GOLD"},
     {"url": "https://arxiv.org/abs/2609.1", "author": "A", "source_kind": "web", "source_name": "Papers"}),
    ({"url": "https://example.org/a", "author": "B"}, {"url": "https://example.org/a", "author": "B"}),
])
def test_projection_is_whitelisted(raw, expected):
    projected = source_context(raw)
    assert (json.loads(projected.split(":\n", 1)[1]) if projected else {}) == expected
    assert "GOLD" not in projected


@pytest.mark.parametrize("enabled", [False, True])
def test_real_runner_projects_context_and_binds_identity(dataset, tmp_path, enabled):
    leaf, _, cases = dataset
    captured = {}

    def factory(_):
        def for_case(key):
            def chat(**kwargs):
                captured[key] = kwargs["prompt"]
                return {"json": {"reason": "input evidence", "primary_category": "model"}}
            return chat
        return for_case

    result = evaluate(leaf, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
                      split="dev", limit=None, seed="source-test", label="source-context",
                      chat_factory=factory, root=tmp_path, workers=2, include_source_context=enabled)
    run = Path(result["run"])
    for case in cases:
        expected = render_category_prompt(case["input"])
        if enabled:
            expected["user"] += source_context(case["input"])
        assert captured[case["case_id"]] == expected
    meta = assets.read_json(tmp_path / "experiments" / run.relative_to(tmp_path / "runs") / "metadata.json")
    assert meta["object_identity"]["behavior"]["include_source_context"] is enabled
    assert meta["object_identity"]["inputs"]["prompts"] == assets.digest(captured)
    assert result["complete"] and meta["identity_unchanged"]
    assert "DO_NOT_LEAK" not in str(captured)
