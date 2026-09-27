
import pytest

from evals._shared import assets
from evals._shared.category_compare import collect, compare


def run_fixture(root, name, cases, predictions, *, prompts=None, behavior=None):
    run = root / "runs/content-enrichment/aihot-category-navigation/v1/2026-09-27" / name
    prompts = prompts or {c["case_id"]: {"system": "same", "user": c["input"]["title"]} for c in cases}
    identity = {"inputs": {"cases": assets.digest(cases), "prompts": assets.digest(prompts)},
                "behavior": behavior or {"request": {"model": "fixture"}}}
    assets.write_json(run / "started.json", {"object_identity": identity})
    assets.write_json(root / "experiments" / run.relative_to(root / "runs") / "metadata.json",
                      {"object_identity": identity, "identity_unchanged": True})
    assets.write_jsonl(run / "cases.jsonl", cases)
    assets.write_jsonl(run / "prompts.jsonl", [{"case_id": k, "prompt": v} for k, v in prompts.items()])
    assets.write_jsonl(run / "predictions.jsonl", predictions)
    for row in predictions:
        assets.write_json(run / "items" / (row["case_id"] + ".json"), row)
    return run


def test_failure_recovery_keeps_original_and_rejects_success_resampling(tmp_path):
    cases = [{"case_id": "a", "input": {"title": "a"}, "reference": {"category": "paper"}}]
    failed = [{"case_id": "a", "status": "error", "output": {}}]
    correct = [{"case_id": "a", "status": "ok", "output": {"category": "paper"}}]
    first = run_fixture(tmp_path, "01", cases, failed)
    second = run_fixture(tmp_path, "02", cases, correct)
    original = (first / "predictions.jsonl").read_bytes()
    assert collect([first, second])[1]["a"] == correct[0]
    assert (first / "predictions.jsonl").read_bytes() == original
    with pytest.raises(ValueError, match="successful"):
        collect([second, second])
    changed = run_fixture(tmp_path, "03", cases, correct, prompts={"a": {"system": "new", "user": "a"}})
    with pytest.raises(ValueError, match="prompt"):
        collect([first, changed])


def test_pairing_requires_same_cases_and_reports_fix(tmp_path):
    cases = [{"case_id": "a", "input": {"title": "a"}, "reference": {"category": "paper"}}]
    wrong = [{"case_id": "a", "status": "ok", "output": {"category": "opinion"}}]
    correct = [{"case_id": "a", "status": "ok", "output": {"category": "paper"}}]
    baseline = run_fixture(tmp_path, "01", cases, wrong)
    candidate = run_fixture(tmp_path, "02", cases, correct)
    book = tmp_path / "reviews.json"
    assets.write_json(book, {"metadata": {"format": "ai-radar-human-reviews-v1", "target": "content-enrichment"}, "batches": []})
    result = compare([baseline], [candidate], reviews=book)
    assert result["candidate_vs_baseline"]["all"]["fixes"] == ["a"]
    assert result["candidate"]["human_priority"]["metrics"]["category_accuracy"]["value"] == 1
    meta = tmp_path / "experiments" / candidate.relative_to(tmp_path / "runs") / "metadata.json"
    data = assets.read_json(meta)
    data["identity_unchanged"] = False
    assets.replace_json(meta, data)
    with pytest.raises(ValueError, match="terminal identity"):
        collect([candidate])


def test_quote_subset_recovery_validates_each_resolution(tmp_path):
    cases = [{"case_id": k, "input": {"title": k}, "reference": {"category": "paper"}} for k in ("a", "b")]
    first_quotes = [{"case_id": c["case_id"], "quotes": []} for c in cases]
    retry_quotes = first_quotes[1:]
    def behavior(rows):
        return {"quote_context": {"manifest_sha256": "same", "raw_inputs_sha256": "same",
                                  "resolved_sha256": assets.digest(rows)}}
    first = run_fixture(tmp_path, "01", cases, [
        {"case_id": "a", "status": "ok", "output": {"category": "paper"}},
        {"case_id": "b", "status": "error", "output": {}}], behavior=behavior(first_quotes))
    second = run_fixture(tmp_path, "02", cases[1:], [
        {"case_id": "b", "status": "ok", "output": {"category": "paper"}}], behavior=behavior(retry_quotes))
    assets.write_jsonl(first / "quote-context.jsonl", first_quotes)
    assets.write_jsonl(second / "quote-context.jsonl", retry_quotes)
    assert len(collect([first, second])[1]) == 2
    (second / "quote-context.jsonl").write_text("[]\n")
    with pytest.raises(ValueError, match="quote context identity"):
        collect([first, second])


def test_body_missing_prediction_cannot_reuse_parent_success(tmp_path):
    cases = [{"case_id": "a", "input": {"title": "a"}, "reference": {"category": "paper"}}]
    correct = [{"case_id": "a", "status": "ok", "output": {"category": "paper"}}]
    baseline = run_fixture(tmp_path, "01", cases, correct)
    candidate = run_fixture(tmp_path, "02", cases, correct)
    body = run_fixture(tmp_path, "03", cases, [])
    supplement = tmp_path / "body.jsonl"
    assets.write_jsonl(supplement, [{"case_id": "a", "status": "available"}])
    for meta in (body / "started.json", tmp_path / "experiments" / body.relative_to(tmp_path / "runs") / "metadata.json"):
        data = assets.read_json(meta)
        data["object_identity"]["inputs"]["body_context"] = assets.file_digest(supplement)
        assets.replace_json(meta, data)
    book = tmp_path / "reviews.json"
    assets.write_json(book, {"metadata": {"format": "ai-radar-human-reviews-v1", "target": "content-enrichment"}, "batches": []})
    with pytest.raises(ValueError, match="lacks predictions"):
        compare([baseline], [candidate], reviews=book, body_runs=[body], body_context=supplement)
