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
@pytest.mark.parametrize("layout,grounded", [("legacy", False), ("documents", False), ("documents", True)])
@pytest.mark.parametrize("temperature", [0, 0.5])
@pytest.mark.parametrize("max_attempts", [1, 3])
def test_real_runner_archives_invalid_response_and_full_denominator(tmp_path, bad, layout, grounded, temperature, max_attempts):
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

    requests = []
    def factory(_):
        def for_case(key):
            def chat(**kwargs):
                requests.append(kwargs)
                return {"json": {"reason": "原文判断", "primary_category": bad if key == "bad" else "opinion"}}
            return chat
        return for_case

    result = evaluate(dataset, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
                      split="dev", limit=None, seed="fixture", label="invalid", chat_factory=factory,
                      workers=2, root=tmp_path, body_limit=None,
                      material_layout=layout, evidence_reason=grounded, temperature=temperature, max_attempts=max_attempts)
    assert len(requests) == 1 + max_attempts
    assert all(r["request"]["temperature"] == temperature for r in requests)
    meta = assets.read_json(Path(result["run"]) / "started.json")
    assert meta["object_identity"]["behavior"]["request"]["temperature"] == temperature
    assert result["complete"] is False
    assert result["category_accuracy"]["value"] == .5
    assert result["category_accuracy"]["denominator"] == 2
    predictions = assets.read_jsonl(Path(result["run"]) / "predictions.jsonl")
    assert predictions[0]["status"] == "error"
    assert "response_json" in predictions[0]
    assert len(predictions[0]["attempts"]) == max_attempts
    assert len(predictions[1]["attempts"]) == 1
    prompts = assets.read_jsonl(Path(result["run"]) / "prompts.jsonl")
    assert ("Source materials" in prompts[0]["prompt"]["user"]) == (layout == "documents")
    assert ("reason 用简短的证据链" in prompts[0]["prompt"]["system"]) == grounded


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), -0.1, 2.1])
def test_invalid_temperature_fails_before_reading_dataset(temperature):
    from evals._shared.category_eval import evaluate
    with pytest.raises(ValueError, match="temperature"):
        evaluate(Path("absent"), config={}, split="dev", limit=None, seed="test",
                 label="test", chat_factory=None, temperature=temperature)


@pytest.mark.parametrize("failures", [0, 1, 2, 3])
@pytest.mark.parametrize("error_type", ["fixture_failure", "OSError", "PermissionError"])
@pytest.mark.parametrize("evidence_first", [False, True])
def test_category_retry_stops_on_success_or_third_failure(tmp_path, failures, error_type, evidence_first):
    from evals._shared import assets
    from evals._shared.category_eval import evaluate
    from evals._shared.transport import TransportError

    dataset = tmp_path / "data/content-enrichment/aihot-category-navigation/v1"
    assets.write_jsonl(dataset / "cases.jsonl", [{"case_id": "one", "split": "dev",
        "input": {"title": "Original"}, "reference": {"category": "paper"}}])
    assets.write_json(dataset / "manifest.json", {"schema_version": 2, "target": "content-enrichment",
        "benchmark": "aihot-category-navigation", "version": "v1", "evaluation_mode": "pointwise",
        "case_count": 1, "files": {"cases.jsonl": assets.file_digest(dataset / "cases.jsonl")},
        "shared_evidence": ".", "evidence_files": {}})
    definitions = Path("evals/content-enrichment/aihot-category-navigation/metrics.json")
    assets.write_json(tmp_path / definitions, assets.read_json(assets.ROOT / definitions))
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) <= failures:
            raise TransportError(str(len(calls)), error_type)
        if kwargs["stage"] == "category_evidence":
            assert "primary_category" not in kwargs["prompt"]["system"]
            return {"attempt_id": str(len(calls)), "json": {
                "reason": "Source facts", "contributions": ["A research finding"], "relationship": "No quotation"}}
        return {"attempt_id": str(len(calls)), "json": {"reason": "Research finding", "primary_category": "paper"}}
    result = evaluate(dataset, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
        split="dev", limit=None, seed="test", label="retry", chat_factory=lambda _: lambda _: chat, root=tmp_path,
        evidence_first=evidence_first)
    storage_failure = failures > 0 and error_type != "fixture_failure"
    succeeds = failures < 3 and not storage_failure
    assert len(calls) == ((1 if storage_failure else min(failures + 1, 3)) + int(evidence_first and succeeds))
    stage_calls = [call for call in calls if call["stage"] == calls[0]["stage"]]
    assert all(call == stage_calls[0] for call in stage_calls)  # Retries preserve input.
    row = assets.read_jsonl(Path(result["run"]) / "predictions.jsonl")[0]
    assert len(row["attempts"]) == len(calls)
    if evidence_first and succeeds:
        assert row["evidence"]["contributions"] == ["A research finding"]
        decision = assets.read_json(Path(result["run"]) / "decision-prompts/one.json")
        assert "Original" in decision["user"] and "A research finding" in decision["user"]
    elif evidence_first:
        assert not (Path(result["run"]) / "decision-prompts/one.json").exists()
    assert result["complete"] == succeeds
    assert result["category_accuracy"]["value"] == (1 if succeeds else 0)
