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
