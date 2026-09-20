from copy import deepcopy

import pytest

from evals._shared.assets import file_digest, read_json, read_jsonl, write_json, write_jsonl
from evals._shared.human_labels import apply_labels, import_prefilter_review, input_identity, load_annotations


def case(key="one", **reference):
    return {"case_id": key, "split": "dev", "reference": reference,
            "input": {"title": "New model!", "content_text": "Measured AI release.",
                      "source_id": "x_lab", "source_kind": "x", "author": "lab",
                      "url": "https://x.com/lab/status/123", "extra": {}}}


def annotation(row, target, field, value):
    return {"case_id": row["case_id"], "target": target, "field": field, "value": value,
            "provenance": "user", "reason": "用户裁决", "ballot_sha256": "a" * 64,
            "input_identity": input_identity(row, target)}


@pytest.mark.parametrize("target,field,old,new", [
    ("news-admission", "member", False, True),
    ("visible-score", "score", 75, 88),
    ("content-enrichment", "category", "other", "model"),
    ("content-enrichment", "tags", ["old"], ["AI", "research"]),
    ("content-enrichment", "title", "Old title", "Human title"),
    ("content-enrichment", "summary", "Old summary", "Human summary"),
    ("content-enrichment", "reason", "Old reason", "Human reason"),
    ("featured-members", "featured", False, True),
])
def test_human_wins_per_field_without_mutating_inputs(target, field, old, new):
    row = case(**{field: old})
    result, report = apply_labels([row], [annotation(row, target, field, new)], target)
    assert result[0]["reference"][field] == new
    assert row["reference"][field] == old
    assert result[0]["input"] == row["input"]
    assert report["human_case_count"] == report["changed_field_count"] == 1


def test_confirmation_retains_priority_when_automatic_label_changes():
    row = case(member=False)
    vote = annotation(row, "news-admission", "member", False)
    row["reference"]["member"] = True
    result, _ = apply_labels([row], [vote, vote], "news-admission")
    assert result[0]["reference"]["member"] is False
    assert result[0]["provenance"]["human_reference"]["member"]["original_reference"] is True


def test_material_change_and_human_conflict_stop_instead_of_falling_back():
    row = case(member=False)
    vote = annotation(row, "news-admission", "member", True)
    with pytest.raises(ValueError, match="conflicting human"):
        apply_labels([row], [vote, {**vote, "value": False}], "news-admission")
    row["input"]["content_text"] = "Unrelated film release"
    with pytest.raises(ValueError, match="input changed"):
        apply_labels([row], [vote], "news-admission")


def test_nonmaterial_metadata_does_not_discard_human_label():
    row = case(member=False)
    vote = annotation(row, "news-admission", "member", True)
    row["input"].update(published_at="2026-09-20", content_html="<p>Changed HTML</p>",
                        title="New model", source_name="New display label",
                        url="https://twitter.com/lab/status/123")
    result, _ = apply_labels([row], [vote], "news-admission")
    assert result[0]["reference"]["member"] is True


def test_target_isolation_and_missing_case_reporting():
    row = case(member=False)
    wrong = annotation(row, "visible-score", "score", 90)
    absent = {**annotation(row, "news-admission", "member", True), "case_id": "absent"}
    result, report = apply_labels([row], [wrong, absent], "news-admission")
    assert result == [row]
    assert report["absent_case_ids"] == ["absent"]


def test_score_tier_is_material_but_date_only_change_is_not():
    row = case(score=75)
    row["input"]["tier"] = "T1.5"
    vote = annotation(row, "visible-score", "score", 85)
    row["input"]["published_at"] = "2026-09-20"
    result, _ = apply_labels([row], [vote], "visible-score")
    assert result[0]["reference"]["score"] == 85
    row["input"]["tier"] = "T3"
    with pytest.raises(ValueError, match="input changed"):
        apply_labels([row], [vote], "visible-score")


@pytest.fixture
def review(tmp_path):
    run = tmp_path / "2026-09-20/01-19-03"
    rows = [case(str(i), member=(i % 2 == 0)) for i in range(5)]
    predictions = [{"case_id": c["case_id"], "status": "ok", "output": {"member": not c["reference"]["member"]}}
                   for c in rows]
    write_jsonl(run / "cases.jsonl", rows)
    write_jsonl(run / "predictions.jsonl", predictions)
    write_json(run / "review/public/errors.json", [{"case": c} for c in rows])
    write_json(run / "review/public/prompts.json", {c["case_id"]: {"reason": "diagnostic"} for c in rows})
    ballot = {"format": "ai-radar-prefilter-human-review-v1", "source_run": "2026-09-20/01-19-03",
              "source_cases_sha256": file_digest(run / "cases.jsonl"),
              "source_predictions_sha256": file_digest(run / "predictions.jsonl"),
              "target_prediction": "original_c11", "label_semantics": "human_override_of_observed_membership",
              "judgments": [{"case_id": str(i), "judgment": v, "reason": f"原文理由 {i}\u2028保留"}
                            for i, v in enumerate(["keep", "positive", "negative", "pending", "uncertain"])]}
    feedback = tmp_path / "feedback.json"
    write_json(feedback, ballot)
    return run, feedback, ballot


def test_import_all_vote_states_integrity_and_immutable_history(review, tmp_path):
    run, feedback, ballot = review
    output = tmp_path / "batch"
    receipt = import_prefilter_review(feedback, run, output)
    assert receipt["coverage"]["human_case_count"] == 3
    assert receipt["coverage"]["changed_field_count"] == 2
    assert len(load_annotations(output)) == 3
    assert file_digest(output / "feedback.json") == file_digest(feedback)
    assert file_digest(run / "cases.jsonl") == ballot["source_cases_sha256"]
    assert read_jsonl(output / "annotations.jsonl")[0]["reason"] == ballot["judgments"][0]["reason"]
    assert read_json(output / "human-priority-scores.json")["metrics"]["precision"]["value"] == 0.5
    with pytest.raises(FileExistsError):
        import_prefilter_review(feedback, run, output)
    (output / "annotations.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="integrity mismatch"):
        load_annotations(output)


@pytest.mark.parametrize("mutation", ["hash", "duplicate", "unknown", "invalid"])
def test_invalid_ballot_is_rejected_before_writes(review, tmp_path, mutation):
    run, feedback, ballot = review
    bad = deepcopy(ballot)
    if mutation == "hash":
        bad["source_cases_sha256"] = "bad"
    elif mutation == "duplicate":
        bad["judgments"].append(bad["judgments"][0])
    elif mutation == "unknown":
        bad["judgments"][0]["case_id"] = "not-displayed"
    else:
        bad["judgments"][0]["judgment"] = "approve"
    other = tmp_path / "bad.json"
    write_json(other, bad)
    with pytest.raises(ValueError):
        import_prefilter_review(other, run, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()
