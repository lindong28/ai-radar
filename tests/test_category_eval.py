from __future__ import annotations

from pathlib import Path

import pytest

from airadar.enrich.category import category_output, render_category_prompt
from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS
from evals._shared.category_eval import category_cases, diagnostics
from evals._shared.metrics import score


@pytest.mark.parametrize("category,slug", PRIMARY_CATEGORY_SLUGS.items())
def test_six_category_output(category, slug):
    assert category_output({"reason": "原文的主要信息", "primary_category": category}) == {"category": slug}


@pytest.mark.parametrize("payload", [
    {"primary_category": "opinion", "reason": "后置"},
    {"reason": "", "primary_category": "model"},
    {"reason": "依据", "primary_category": "other"},
    {"reason": "依据", "primary_category": "model", "score": 80},
])
def test_invalid_response_is_rejected(payload):
    with pytest.raises(ValueError):
        category_output(payload)


def test_prompt_uses_only_original_title_and_body():
    raw = {"title": "original title", "content_text": "b" * 6000,
           "reference": {"category": "SECRET"}, "summary": "SECRET",
           "source_id": "SECRET", "tags": ["SECRET"]}
    p = render_category_prompt(raw)
    assert "SECRET" not in str(p)
    assert p["user"] == "Title: original title\n\nContent:\n" + "b" * 5000


def test_only_eligible_category_field_is_scored_and_failures_stay_in_denominator():
    cases = [{"case_id": "a", "split": "dev", "input": {"title": "a"},
              "reference": {"category": "opinion", "tags": []}},
             {"case_id": "b", "split": "regression", "input": {"title": "b"},
              "reference": {"category": "paper"}},
             {"case_id": "c", "input": {}, "reference": {"title": "missing category"}}]
    selected = category_cases(cases)
    assert len(selected) == 2
    assert selected[0]["reference"] == {"category": "opinion"}
    predictions = [{"case_id": "a", "status": "ok", "output": {"category": "opinion"}},
                   {"case_id": "b", "status": "error", "output": {}}]
    result = score("O3", selected, predictions)
    assert result["metrics"]["category_accuracy"]["value"] == .5
    assert result["metrics"]["category_accuracy"]["denominator"] == 2
    assert result["complete"] is False
    assert diagnostics(selected, predictions)["majority_baseline"] == .5
    assert result["metrics"]["tags_exact_set_accuracy"]["value"] is None
    predictions[0]["output"]["category"] = "paper"
    assert score("O3", selected, predictions)["metrics"]["category_accuracy"]["value"] == 0


@pytest.mark.parametrize("bad", [float("nan"), "not-a-category"])
def test_real_runner_archives_invalid_response_and_full_denominator(tmp_path, bad):
    from evals._shared import assets
    from evals._shared.category_eval import evaluate

    dataset = tmp_path / "data/content-enrichment/aihot-category-navigation/v1"
    cases = [{"case_id": key, "split": "dev", "input": {"title": key, "content_text": "article"},
              "reference": {"category": "opinion"}} for key in ("good", "bad")]
    assets.write_jsonl(dataset / "cases.jsonl", cases)
    assets.write_json(dataset / "manifest.json", {"schema_version": 2, "target": "content-enrichment",
        "benchmark": "aihot-category-navigation", "version": "v1", "evaluation_mode": "pointwise",
        "case_count": 2, "files": {"cases.jsonl": assets.file_digest(dataset / "cases.jsonl")},
        "shared_evidence": ".", "evidence_files": {}})
    definitions = assets.read_json(assets.ROOT / "evals/content-enrichment/aihot-category-navigation/metrics.json")
    assets.write_json(tmp_path / "evals/content-enrichment/aihot-category-navigation/metrics.json", definitions)

    def factory(_):
        return lambda key: lambda **kwargs: {"json": {"reason": "原文判断", "primary_category": bad if key == "bad" else "opinion"}}

    result = evaluate(dataset, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
                      split="dev", limit=None, seed="fixture", label="invalid", chat_factory=factory,
                      workers=2, root=tmp_path)
    assert result["complete"] is False
    assert result["category_accuracy"]["value"] == .5
    assert result["category_accuracy"]["denominator"] == 2
    predictions = assets.read_jsonl(Path(result["run"]) / "predictions.jsonl")
    assert predictions[0]["status"] == "error"
    assert "response_json" in predictions[0]
