from __future__ import annotations

import copy
from pathlib import Path

import pytest

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS
from evals._shared import assets
from evals._shared.category_metrics import score_categories
from evals._shared.category_rescore import rescore


def cohort(gold, outputs):
    cases = [{"case_id": str(i), "input": {"title": f"news {i}"}, "split": "dev",
              "reference": {"category": label}} for i, label in enumerate(gold)]
    predictions = [{"case_id": str(i), "status": "ok", "output": {"category": label}}
                   for i, label in enumerate(outputs)]
    return cases, predictions


def test_all_six_classes_perfect_and_rotated_negative_control():
    labels = list(PRIMARY_CATEGORY_SLUGS.values())
    cases, predictions = cohort(labels, labels)
    result = score_categories(cases, predictions)
    assert len(result["metrics"]) == 13
    assert all(m["value"] == 1 for m in result["metrics"].values())
    _, rotated = cohort(labels, labels[1:] + labels[:1])
    result = score_categories(cases, rotated)
    assert all(m["value"] == 0 for m in result["metrics"].values())
    assert all(tally == {"tp": 0, "fp": 1, "fn": 1} for tally in result["category_counts"].values())


def test_asymmetric_confusion_missing_and_invalid_predictions():
    cases, predictions = cohort(["opinion"] * 3 + ["paper"] * 3 + ["industry"],
                                ["opinion", "opinion", "paper", "opinion", "paper", "invalid"])
    original = copy.deepcopy(predictions)
    result = score_categories(cases, predictions)
    assert predictions == original
    assert result["metrics"]["category_accuracy"]["value"] == 3 / 7
    assert result["category_counts"]["opinion"] == {"tp": 2, "fp": 1, "fn": 1}
    assert result["category_counts"]["paper"] == {"tp": 1, "fp": 1, "fn": 2}
    assert result["metrics"]["category_paper_precision"]["value"] == 1 / 2
    assert result["metrics"]["category_paper_recall"]["value"] == 1 / 3
    assert result["metrics"]["category_industry_precision"]["value"] is None
    assert result["metrics"]["category_industry_recall"]["value"] == 0
    assert result["metrics"]["category_model_recall"]["status"] == "not_computed"
    assert result["metrics"]["category_model_precision"]["denominator"] == 0
    assert result["complete"] is False
    assert result["counts"]["failed_cases"] == 2


@pytest.mark.parametrize("output", [None, {}, {"category": []}, {"category": "unknown"}])
def test_invalid_output_is_fn_not_an_extra_category(output):
    cases, predictions = cohort(["opinion"], ["opinion"])
    predictions[0]["output"] = output
    result = score_categories(cases, predictions)
    assert result["category_counts"]["opinion"] == {"tp": 0, "fp": 0, "fn": 1}
    assert result["complete"] is False


def test_failed_call_with_stale_output_does_not_count_as_prediction():
    cases, predictions = cohort(["paper"], ["opinion"])
    predictions[0]["status"] = "error"
    result = score_categories(cases, predictions)
    assert result["category_counts"]["opinion"]["fp"] == 0
    assert result["category_counts"]["paper"]["fn"] == 1


def test_predicted_but_absent_reference_class_has_zero_precision_undefined_recall():
    result = score_categories(*cohort(["opinion"], ["paper"]))
    assert result["metrics"]["category_paper_precision"]["value"] == 0
    assert result["metrics"]["category_paper_recall"]["value"] is None
    assert result["metrics"]["category_opinion_precision"]["value"] is None
    assert result["metrics"]["category_opinion_recall"]["value"] == 0


def test_empty_cohort_has_no_computed_metrics():
    result = score_categories([], [])
    assert result["complete"] is False
    assert all(m["value"] is None for m in result["metrics"].values())


@pytest.mark.parametrize("defect", ["duplicate-case", "duplicate-prediction", "foreign-prediction", "missing-input", "bad-gold"])
def test_bad_identity_or_gold_is_rejected(defect):
    cases, predictions = cohort(["opinion"], ["opinion"])
    if defect == "duplicate-case":
        cases.append(cases[0])
    elif defect == "duplicate-prediction":
        predictions.append(predictions[0])
    elif defect == "foreign-prediction":
        predictions[0]["case_id"] = "foreign"
    elif defect == "missing-input":
        cases[0]["input"] = None
    else:
        cases[0]["reference"]["category"] = "unknown"
    with pytest.raises(ValueError):
        score_categories(cases, predictions)


@pytest.fixture
def archived(tmp_path):
    source_root, output_root = tmp_path / "source", tmp_path / "output"
    suffix = Path("content-enrichment/aihot-category-navigation/v1/2026-09-21/00-00-01")
    run = source_root / "runs" / suffix
    cases, predictions = cohort(["opinion", "paper"], ["opinion", "opinion"])
    assets.write_jsonl(run / "cases.jsonl", cases)
    assets.write_jsonl(run / "predictions.jsonl", predictions)
    for p in predictions:
        assets.write_json(run / "items" / (p["case_id"] + ".json"), p)
    assets.write_json(run / "scores.json", score_categories(cases, predictions))
    metadata = {"target": "content-enrichment", "benchmark": "aihot-category-navigation", "version": "v1",
                "label": "fixture", "split": "dev", "smoke": False, "field": "category", "dataset": "fixture",
                "case_identity": assets.digest(cases), "case_ids": [c["case_id"] for c in cases],
                "object_identity": {"inputs": {"cases": assets.digest(cases)}},
                "selection": {}, "human_application": None, "identity_unchanged": True}
    assets.write_json(source_root / "experiments" / suffix / "metadata.json", metadata)
    definitions = "evals/content-enrichment/aihot-category-navigation/metrics.json"
    assets.write_json(output_root / definitions, assets.read_json(assets.ROOT / definitions))
    return source_root, output_root, run, suffix


def test_rescore_archives_thirteen_metrics_preserves_source_and_calls_no_models(archived):
    source_root, output_root, run, suffix = archived
    before = {p: p.read_bytes() for p in source_root.rglob("*") if p.is_file()}
    result = rescore(run, source_root=source_root, root=output_root)
    assert result["additional_model_calls"] == 0
    assert result["metrics"]["category_accuracy"]["value"] == .5
    assert {p: p.read_bytes() for p in source_root.rglob("*") if p.is_file()} == before
    rows = assets.read_json(output_root / "experiments/metrics/summary.json")
    assert len(rows) == 13
    metadata = assets.read_json(output_root / rows[0]["metadata"])
    assert metadata["run_kind"] == "metric_recompute"
    assert metadata["source_run"] == str(run)
    assert Path(metadata["source_run"]).is_dir()
    assert metadata["source_sha256"]["predictions.jsonl"] == assets.file_digest(run / "predictions.jsonl")
    assert metadata["identity_unchanged"] is True
    assert not list(output_root.rglob("attempts"))


@pytest.mark.parametrize("defect", ["cases", "predictions", "identity", "accuracy"])
def test_rescore_rejects_drift_before_creating_run(archived, defect):
    source_root, output_root, run, suffix = archived
    if defect in ("cases", "predictions"):
        path = run / f"{defect}.jsonl"
        rows = assets.read_jsonl(path)
        rows[0]["extra"] = "changed"
        path.write_text("\n".join(__import__("json").dumps(row) for row in rows) + "\n")
    elif defect == "identity":
        path = source_root / "experiments" / suffix / "metadata.json"
        metadata = assets.read_json(path)
        metadata["identity_unchanged"] = False
        assets.replace_json(path, metadata)
    else:
        path = run / "scores.json"
        scores = assets.read_json(path)
        scores["metrics"]["category_accuracy"]["value"] = 1
        assets.replace_json(path, scores)
    with pytest.raises(ValueError):
        rescore(run, source_root=source_root, root=output_root)
    assert not (output_root / "runs").exists()


def test_rescore_rejects_old_output_registry_before_archiving(archived):
    source_root, output_root, run, _ = archived
    path = output_root / "evals/content-enrichment/aihot-category-navigation/metrics.json"
    definitions = assets.read_json(path)
    definitions["metrics"] = definitions["metrics"][:1]
    assets.replace_json(path, definitions)
    with pytest.raises(ValueError, match="metric definitions differ"):
        rescore(run, source_root=source_root, root=output_root)
    assert not (output_root / "runs").exists()
