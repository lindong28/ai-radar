"""Category retrieval ablation: parent binding, time boundary and real runner."""
from pathlib import Path

import pytest

from evals._shared import assets
from evals._shared.category_eval import evaluate, quoted_inputs
from tests.test_quote_context import raw, variant


@pytest.fixture
def dataset(tmp_path):
    source = tmp_path / "parent"
    assets.write_jsonl(source / "raw-inputs.jsonl", [{"variants": {"v": variant()}}])
    assets.write_json(source / "manifest.json", {"shared_evidence": ".", "evidence_files": {
        "raw-inputs.jsonl": assets.file_digest(source / "raw-inputs.jsonl")}})
    leaf = tmp_path / "content-enrichment/aihot-category-navigation/v1"
    cases = [{"case_id": key, "split": "dev", "input": {**raw(), "title": "This is the way", "content_text": ""},
              "provenance": {"observed_at": at}, "reference": {"category": "ai-models"}}
             for key, at in [("available", "2026-09-16T12:00:00Z"), ("future", "2026-09-16T09:00:00Z")]]
    assets.write_jsonl(leaf / "cases.jsonl", cases)
    assets.write_json(leaf / "manifest.json", {"schema_version": 2, "target": "content-enrichment",
        "benchmark": "aihot-category-navigation", "version": "v1", "evaluation_mode": "pointwise",
        "case_count": 2, "files": {"cases.jsonl": assets.file_digest(leaf / "cases.jsonl")},
        "shared_evidence": ".", "evidence_files": {}, "source_datasets": [
            {"sha256": assets.file_digest(source / "manifest.json")} ]})
    definitions = "evals/content-enrichment/aihot-category-navigation/metrics.json"
    assets.write_json(tmp_path / definitions, assets.read_json(assets.ROOT / definitions))
    return leaf, source, cases


@pytest.mark.parametrize("defect", ["none", "parent", "raw"])
def test_parent_hash_and_raw_integrity(dataset, defect):
    leaf, source, cases = dataset
    if defect == "parent":
        assets.replace_json(source / "manifest.json", {"shared_evidence": ".", "evidence_files": {}})
    elif defect == "raw":
        assets.replace_json(source / "raw-inputs.jsonl", {"changed": True})
    if defect != "none":
        with pytest.raises(ValueError):
            quoted_inputs(cases, leaf, source)
    else:
        rows, identity = quoted_inputs(cases, leaf, source)
        assert [r["quotes"][0]["status"] for r in rows] == ["available", "missing_as_of"]
        assert identity["resolved_sha256"] == assets.digest(rows)


@pytest.mark.parametrize("mode", ["disabled", "enabled", "drift"])
def test_runner_context_gold_and_terminal_identity(dataset, tmp_path, mode):
    leaf, source, cases = dataset
    original = (leaf / "cases.jsonl").read_bytes()
    captured = {}

    def factory(_):
        def for_case(key):
            def chat(**kwargs):
                captured[key] = kwargs["prompt"]
                if mode == "drift" and key == "available":
                    assets.replace_json(source / "raw-inputs.jsonl", {"changed": True})
                return {"json": {"reason": "source evidence", "primary_category": "model"}}
            return chat
        return for_case

    result = evaluate(leaf, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
                      split="dev", limit=None, seed="fixture", label=mode, chat_factory=factory,
                      workers=2, root=tmp_path, quote_source=None if mode == "disabled" else source)
    run = Path(result["run"])
    assert (leaf / "cases.jsonl").read_bytes() == original
    assert sorted(assets.read_jsonl(run / "cases.jsonl"), key=lambda c: c["case_id"]) == cases
    assert "A new model" not in captured["future"]["user"]
    assert ("A new model" in captured["available"]["user"]) == (mode != "disabled")
    assert "DO_NOT_LEAK" not in str(captured)
    assert (run / "quote-context.jsonl").exists() == (mode != "disabled")
    assert result["complete"] == (mode != "drift")
    assert result["category_accuracy"]["value"] == (None if mode == "drift" else 1)


def test_quote_contribution_changes_only_available_prompts(dataset, tmp_path):
    from evals._shared.category_eval import QUOTE_CONTRIBUTION
    leaf, source, _ = dataset
    captured = {}

    def factory(_):
        def for_case(key):
            def chat(**kwargs):
                captured[key] = kwargs["prompt"]
                return {"json": {"reason": "source evidence", "primary_category": "model"}}
            return chat
        return for_case

    evaluate(leaf, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
             split="dev", limit=None, seed="fixture", label="quote-role", chat_factory=factory,
             workers=2, root=tmp_path, quote_source=source, quote_contribution=True)
    assert QUOTE_CONTRIBUTION in captured["available"]["system"]
    assert QUOTE_CONTRIBUTION not in captured["future"]["system"]


@pytest.mark.parametrize("length", [10, 5000, 5001, 12000])
def test_body_limit_projection(length):
    from airadar.enrich.category import render_category_prompt
    body = "x" * length
    raw_input = {"title": "original", "content_text": body, "category": "DO_NOT_LEAK"}
    default = render_category_prompt(raw_input)
    full = render_category_prompt(raw_input, body_limit=None)
    assert default["user"].endswith("Content:\n" + body[:5000])
    assert full["user"].endswith("Content:\n" + body)
    assert default["system"] == full["system"]
    assert "DO_NOT_LEAK" not in str(full)
    with pytest.raises(ValueError):
        render_category_prompt(raw_input, body_limit=-1)


@pytest.mark.parametrize("blind", [False, True])
@pytest.mark.parametrize("failure_mode", ["none", "api", "archive"])
def test_conditional_review_pairs_same_first_pass_and_keeps_failures(dataset, tmp_path, monkeypatch, failure_mode, blind):
    leaf, source, cases = dataset
    calls = []
    review_failure = failure_mode != "none"
    if failure_mode == "archive":
        original_write = assets.write_json

        def fail_review_archive(path, value):
            if path.parent.name == "review-prompts":
                raise OSError("fixture archive failure")
            return original_write(path, value)

        monkeypatch.setattr(assets, "write_json", fail_review_archive)

    def factory(_):
        def for_case(key):
            def chat(**kwargs):
                calls.append((key, kwargs["stage"]))
                if kwargs["stage"] == "category_review":
                    assert key == "available"
                    assert ('"needs_review"' not in kwargs["prompt"]["user"]) is blind
                    assert kwargs["prompt"]["system"].endswith("SECOND_ONLY_GUIDANCE")
                    if review_failure:
                        raise RuntimeError("fixture failed review")
                    return {"json": {"reason": "revised evidence", "primary_category": "model"}}
                assert "SECOND_ONLY_GUIDANCE" not in str(kwargs["prompt"])
                return {"json": {"reason": "initial evidence", "needs_review": key == "available",
                                 "primary_category": "product" if key == "available" else "model"}}
            return chat
        return for_case

    result = evaluate(leaf, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
                      split="dev", limit=None, seed="fixture", label="conditional", chat_factory=factory,
                      workers=2, root=tmp_path, quote_source=source, conditional_review=True,
                      blind_review=blind, review_guidance="SECOND_ONLY_GUIDANCE")
    run = Path(result["run"])
    behavior = assets.read_json(run / "started.json")["object_identity"]["behavior"]
    assert behavior["blind_review"] is blind
    assert behavior["review_guidance"] == "SECOND_ONLY_GUIDANCE"
    expected = [("available", "category"), ("future", "category")]
    if failure_mode != "archive":
        expected.append(("available", "category_review"))
    assert sorted(calls) == sorted(expected)
    assert assets.read_json(run / "first-pass-scores.json")["metrics"]["category_accuracy"]["value"] == .5
    assert result["category_accuracy"]["value"] == (.5 if review_failure else 1.)
    assert result["category_accuracy"]["denominator"] == 2
    assert result["complete"] is not review_failure
    assert (run / "review-prompts/available.json").exists() == (failure_mode != "archive")
    assert not (run / "review-prompts/future.json").exists()
    p = {p["case_id"]: p for p in assets.read_jsonl(run / "predictions.jsonl")}["available"]
    assert p["first_pass"]["output"] == {"category": "ai-products"}
    if review_failure:
        assert p["status"] == "error" and p["output"] == {}


@pytest.mark.parametrize("options", [{"blind_review": True}, {"review_guidance": "rule"}])
def test_review_options_require_route_before_loading_or_calls(tmp_path, options):
    with pytest.raises(ValueError, match="require conditional review"):
        evaluate(tmp_path / "missing", config={}, split="dev", limit=None, seed="test",
                 label="invalid", chat_factory=None, **options)
