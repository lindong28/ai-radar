import copy

import pytest
from jinja2 import StrictUndefined, Template

from airadar.scorer.semantic import semantic_score
from evals._shared.assets import ROOT, read_json
from evals._shared.score_eval import project_score, prompt_context


def response(impact=0, information_gain=0, evidence=0):
    return {"reason": "Reported event and information", **{
        key: {"reason": f"Evidence for {key}", "score": value}
        for key, value in zip(("impact", "information_gain", "evidence"),
                              (impact, information_gain, evidence), strict=True)}}


@pytest.mark.parametrize("values,expected", [
    ((0, 0, 0), 0), ((10, 10, 10), 100), ((10, 0, 0), 50),
    ((0, 10, 0), 30), ((0, 0, 10), 20), ((7, 4, 6), 59),
])
def test_fixed_combination_all_dimensions_contribute_without_source_multiplier(values, expected):
    payload = response(*values)
    before = copy.deepcopy(payload)
    assert semantic_score(payload) == expected
    for tier in ("T1", "T1.5", "T2"):
        assert project_score(payload, "semantic", tier) == {"score": expected}
    assert payload == before


@pytest.mark.parametrize("field", ["impact", "information_gain", "evidence"])
@pytest.mark.parametrize("value", [-1, 11, 2.5, 5.0, True, "5", None, float("nan"), float("inf")])
def test_invalid_dimension_scores_fail(field, value):
    payload = response(5, 5, 5)
    payload[field]["score"] = value
    with pytest.raises(ValueError):
        semantic_score(payload)


@pytest.mark.parametrize("bad", [{"score": 5, "reason": "late"}, {"score": 5},
                                  {"reason": " ", "score": 5}, None,
                                  {"reason": "ok", "score": 5, "extra": 1}])
def test_each_dimension_requires_original_reason_first(bad):
    payload = response(5, 5, 5)
    payload["evidence"] = bad
    with pytest.raises(ValueError):
        semantic_score(payload)


def test_top_level_order_missing_dimension_and_extra_total_fail():
    payload = response(5, 5, 5)
    for bad in ({**{k: v for k, v in payload.items() if k != "reason"}, "reason": "late"},
                {k: v for k, v in payload.items() if k != "impact"},
                {**payload, "score": 50}):
        with pytest.raises(ValueError):
            semantic_score(bad)


@pytest.mark.parametrize("name", ["semantic-news-value-v1", "semantic-news-value-v2", "semantic-direct-control-v1"])
@pytest.mark.parametrize("body", ["Short concrete announcement", "x" * 5000 + "HIDDEN_TAIL"])
def test_shipped_prompts_render_real_provider_item_without_gold(name, body):
    prompt = read_json(ROOT / f"evals/visible-score/prompts/{name}.json")
    template = Template(prompt["user_template"], undefined=StrictUndefined)
    raw = {"item_id": "test", "source_id": "source", "tier": "T2", "url": "https://example.org/",
           "title": "Title", "content_text": body, "published_at": "2026-09-20", "reference": "SECRET"}
    rendered = template.render(**prompt_context(raw))
    assert rendered.endswith(body[:5000])
    assert "HIDDEN_TAIL" not in rendered and "SECRET" not in rendered
