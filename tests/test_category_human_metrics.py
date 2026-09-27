from copy import deepcopy

import pytest

from evals._shared import assets
from evals._shared.category_human import score_human_categories
from evals._shared.human_store import append_batch


def setup_book(tmp_path):
    labels = ["ai-models", "ai-products", "industry", "paper", "tip", "opinion"]
    cases = [{"case_id": str(i), "input": {"title": str(i)}, "reference": {"category": label}}
             for i, label in enumerate(labels)]
    path = tmp_path / "reviews.json"
    annotations = [{"case_id": "0", "input_identity": assets.digest(cases[0]["input"]),
                    "provenance": "user", "field": "acceptable_categories", "value": ["ai-models", "tip"]},
                   {"case_id": "5", "input_identity": assets.digest(cases[5]["input"]),
                    "provenance": "user", "field": "acceptable_categories", "value": ["industry"]}]
    append_batch(path, "content-enrichment", {"metadata": {"batch_id": "fixture", "kind": "category-acceptable-labels"},
                                              "data": {"annotations": [], "category_judgments": annotations}})
    return cases, path


def test_set_accuracy_fixed_pr_cohort_and_original_unchanged(tmp_path):
    cases, path = setup_book(tmp_path)
    before = deepcopy(cases)
    predictions = [{"case_id": c["case_id"], "status": "ok", "output": dict(c["reference"])} for c in cases]
    predictions[0]["output"]["category"] = "tip"
    predictions[5]["output"]["category"] = "industry"
    result = score_human_categories(cases, predictions, path)
    assert result["metrics"]["category_accuracy"]["value"] == 1
    assert result["human_reviewed"] == {"count": 2, "correct": 2, "accuracy": 1}
    assert result["precision_recall_scope"]["case_count"] == 5
    assert result["category_counts"]["tip"] == {"tp": 1, "fp": 0, "fn": 0}
    assert result["category_counts"]["industry"]["tp"] == 2
    assert cases == before
    predictions[0]["output"]["category"] = "paper"
    changed = score_human_categories(cases, predictions, path)
    assert changed["metrics"]["category_accuracy"]["value"] == 5 / 6
    assert changed["precision_recall_scope"] == result["precision_recall_scope"]
    assert changed["category_counts"] == result["category_counts"]


def test_missing_failed_predictions_and_identity_mismatch(tmp_path):
    cases, path = setup_book(tmp_path)
    result = score_human_categories(cases, [], path)
    assert not result["complete"]
    assert result["metrics"]["category_accuracy"]["value"] == 0
    assert result["category_counts"]["industry"]["fn"] == 2
    cases[0]["input"]["title"] = "changed material"
    with pytest.raises(ValueError, match="input mismatch"):
        score_human_categories(cases, [], path)


def test_real_runner_keeps_gold_separate_and_supplements_original_input(tmp_path):
    from pathlib import Path

    from evals._shared.category_eval import evaluate

    cases, book = setup_book(tmp_path)
    for c in cases:
        c["split"] = "dev"
        c["input"]["url"] = "https://example.org/" + c["case_id"]
    # Rebuild the fixture book's input keys after adding URLs.
    data = assets.read_json(book)
    for j in data["batches"][0]["data"]["category_judgments"]:
        j["input_identity"] = assets.digest(cases[int(j["case_id"])]["input"])
    batch = data["batches"][0]
    batch["sha256"] = assets.digest({k: v for k, v in batch.items() if k != "sha256"})
    assets.replace_json(book, data)
    dataset = tmp_path / "data/content-enrichment/aihot-category-navigation/v1"
    assets.write_jsonl(dataset / "cases.jsonl", cases)
    assets.write_json(dataset / "manifest.json", {"schema_version": 2, "target": "content-enrichment",
        "benchmark": "aihot-category-navigation", "version": "v1", "evaluation_mode": "pointwise",
        "case_count": 6, "files": {"cases.jsonl": assets.file_digest(dataset / "cases.jsonl")},
        "shared_evidence": ".", "evidence_files": {}})
    definitions = Path("evals/content-enrichment/aihot-category-navigation/metrics.json")
    assets.write_json(tmp_path / definitions, assets.read_json(assets.ROOT / definitions))
    body = tmp_path / "body.jsonl"
    assets.write_jsonl(body, [{"case_id": "5", "url": cases[5]["input"]["url"],
        "original_input_sha256": assets.digest(cases[5]["input"]), "status": "available",
        "content_text": "Retrieved article evidence", "fetched_at": "2026-09-27T00:00:00Z"}])
    seen = []
    def factory(_):
        def chat(key):
            def call(**kwargs):
                seen.append(kwargs["prompt"])
                return {"json": {"reason": "Evidence", "primary_category": "industry"}}
            return call
        return chat
    result = evaluate(dataset, config={"models": {"category": "fixture"}, "transport_identity": "fixture"},
        split="dev", limit=None, seed="test", label="test", chat_factory=factory, root=tmp_path,
        human_reviews=book, body_context=body, case_ids={"5"}, request_interval=0.001)
    run = Path(result["run"])
    assert result["category_accuracy"]["value"] == 0
    assert assets.read_json(run / "human-priority-scores.json")["metrics"]["category_accuracy"]["value"] == 1
    assert assets.read_jsonl(run / "cases.jsonl") == [cases[5]]
    assert "Title: 5" in seen[0]["user"] and "Retrieved article evidence" in seen[0]["user"]
    assert "acceptable_categories" not in str(seen)
