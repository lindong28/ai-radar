"""Fixed dev-fit mapping, immutable source checks and zero-call replay."""
import copy
import json
from pathlib import Path

import pytest

from evals._shared import assets
from evals._shared import score_calibration as calibration
from evals._shared.metrics import score


def make_run(path, *, split="dev", failure=False, empty=False, identity=None):
    values = [] if empty else [20, 40, 60, 80]
    cases = [{"case_id": str(i), "split": split, "input": {"title": f"title {i}"},
              "reference": {"score": value / 2 + 5}} for i, value in enumerate(values)]
    predictions = [{"case_id": str(i), "status": "error" if failure and i == 1 else "ok",
                    "output": None if failure and i == 1 else {"score": value},
                    "raw": {"content": f"original {i}"}, "reason": f"reason {i}"}
                   for i, value in enumerate(values)]
    metadata = {"target": calibration.TARGET, "benchmark": calibration.BENCHMARK, "version": "v1",
                "split": split, "case_identity": assets.digest(cases), "case_ids": [c["case_id"] for c in cases],
                "object_identity": {"model": "fixture", "prompt": "frozen"} if identity is None else identity}
    assets.write_json(path / "started.json", metadata)
    assets.write_jsonl(path / "cases.jsonl", cases)
    assets.write_jsonl(path / "predictions.jsonl", predictions)
    assets.write_json(path / "scores.json", score("O2", cases, predictions))
    return path


def result_root(root):
    destination = root / f"evals/{calibration.TARGET}/{calibration.BENCHMARK}/metrics.json"
    destination.parent.mkdir(parents=True)
    destination.write_bytes((assets.ROOT / f"evals/{calibration.TARGET}/{calibration.BENCHMARK}/metrics.json").read_bytes())
    return root


def test_fit_known_linear_error_and_apply_regression_without_copying_raw(tmp_path):
    dev = make_run(tmp_path / "dev")
    regression = make_run(tmp_path / "regression", split="regression")
    mapping_path = tmp_path / "mapping.json"
    mapping = calibration.fit(dev, mapping_path)
    assert mapping["scale"] == .5 and mapping["offset"] == 5
    assert mapping["fit_mae"] == 0
    assert len(mapping["scale_grid"]) == 61
    assert mapping["fit_case_ids"] == ["0", "1", "2", "3"]
    result = calibration.apply(regression, mapping_path, label="linear", root=result_root(tmp_path / "result"))
    assert result["complete"] and result["metrics"]["mae"]["value"] == 0 and result["new_model_calls"] == 0
    assert assets.read_json(regression / "scores.json")["metrics"]["mae"]["value"] == 20
    destination = Path(result["run"])
    assert assets.read_jsonl(destination / "cases.jsonl") == assets.read_jsonl(regression / "cases.jsonl")
    rows = assets.read_jsonl(destination / "predictions.jsonl")
    assert [row["output"]["score"] for row in rows] == [15, 25, 35, 45]
    assert all("raw" not in row and "reason" not in row for row in rows)
    for row in rows:
        ref = row["source_prediction"]
        assert ref["sha256"] == assets.file_digest(Path(ref["file"]))
        original = next(p for p in assets.read_jsonl(Path(ref["file"])) if p["case_id"] == ref["case_id"])
        assert original["reason"] == f"reason {ref['case_id']}"
    metadata = assets.read_json(destination / "started.json")
    assert metadata["mapping"]["fit_split"] == "dev"
    assert metadata["source_run"] == str(regression.resolve())


@pytest.mark.parametrize("settings", [{"split": "regression"}, {"failure": True}, {"empty": True}])
def test_fit_rejects_regression_incomplete_and_empty(tmp_path, settings):
    source = make_run(tmp_path / "source", **settings)
    with pytest.raises(ValueError):
        calibration.fit(source, tmp_path / "mapping.json")
    assert not (tmp_path / "mapping.json").exists()


@pytest.mark.parametrize("value,scale,offset,expected", [
    (20, 1, -40, 0), (80, 1.4, 10, 100), (25, .5, 0, 13), (21, .5, 0, 11), (0, 1, .5, 1),
])
def test_transform_saturates_and_uses_js_rounding(value, scale, offset, expected):
    assert calibration.transform(value, scale, offset) == expected


def test_transform_has_no_reference_input_and_replay_keeps_failures(tmp_path):
    dev = make_run(tmp_path / "dev")
    mapping_path = tmp_path / "mapping.json"
    calibration.fit(dev, mapping_path)
    failed = make_run(tmp_path / "failed", split="regression", failure=True)
    result = calibration.apply(failed, mapping_path, label="incomplete", root=result_root(tmp_path / "result"))
    assert not result["complete"] and result["metrics"]["mae"]["value"] is None
    rows = assets.read_jsonl(Path(result["run"]) / "predictions.jsonl")
    assert rows[1]["status"] == "error" and rows[1]["output"] is None
    assert len(rows) == 4
    with pytest.raises(TypeError):
        calibration.transform(50, .5, 5, reference=20)


@pytest.mark.parametrize("target", ["cases", "predictions", "mapping", "fit_source", "object_identity"])
def test_tampering_and_object_mismatch_are_rejected(tmp_path, target):
    dev = make_run(tmp_path / "dev")
    mapping_path = tmp_path / "mapping.json"
    calibration.fit(dev, mapping_path)
    regression = make_run(tmp_path / "regression", split="regression")
    if target == "cases":
        rows = assets.read_jsonl(regression / "cases.jsonl")
        rows[0]["input"]["title"] = "changed"
        (regression / "cases.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    elif target == "predictions":
        rows = assets.read_jsonl(regression / "predictions.jsonl")
        rows[0]["output"]["score"] = 90
        (regression / "predictions.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    elif target == "mapping":
        mapping = assets.read_json(mapping_path)
        mapping["offset"] += 1
        mapping_path.write_text(json.dumps(mapping))
    elif target == "fit_source":
        (dev / "predictions.jsonl").write_text((dev / "predictions.jsonl").read_text() + "\n")
    else:
        metadata = assets.read_json(regression / "started.json")
        metadata["object_identity"] = {"model": "different"}
        (regression / "started.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        calibration.apply(regression, mapping_path, label="tampered", root=tmp_path / "result")
    assert not (tmp_path / "result/runs").exists()


def test_fit_tie_break_is_deterministic_and_does_not_modify_source(tmp_path):
    run = make_run(tmp_path / "dev")
    before = {name: (run / name).read_bytes() for name in calibration.SOURCE_FILES}
    first = calibration.fit(run, tmp_path / "first.json")
    second = calibration.fit(run, tmp_path / "second.json")
    assert (first["scale"], first["offset"]) == (second["scale"], second["offset"])
    assert before == {name: (run / name).read_bytes() for name in calibration.SOURCE_FILES}
    with pytest.raises(FileExistsError):
        calibration.fit(run, tmp_path / "first.json")


def test_cli_fit_and_apply(tmp_path, monkeypatch):
    dev = make_run(tmp_path / "dev")
    path = tmp_path / "mapping.json"
    assert calibration.main(["fit", "--run", str(dev), "--output", str(path)]) == 0
    apply = calibration.apply
    root = result_root(tmp_path / "result")
    monkeypatch.setattr(calibration, "apply", lambda *args, **kw: apply(*args, **kw, root=root))
    assert calibration.main(["apply", "--run", str(dev), "--mapping", str(path), "--label", "cli"]) == 0


def test_fixed_mapping_predictions_do_not_depend_on_regression_gold(tmp_path):
    dev = make_run(tmp_path / "dev")
    path = tmp_path / "mapping.json"
    calibration.fit(dev, path)
    first = make_run(tmp_path / "first", split="regression")
    second = make_run(tmp_path / "second", split="regression")
    rows = copy.deepcopy(assets.read_jsonl(second / "cases.jsonl"))
    for row in rows:
        row["reference"]["score"] = 100 - row["reference"]["score"]
    (second / "cases.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
    metadata = assets.read_json(second / "started.json")
    metadata["case_identity"] = assets.digest(rows)
    (second / "started.json").write_text(json.dumps(metadata))
    (second / "scores.json").write_text(json.dumps(score("O2", rows, assets.read_jsonl(second / "predictions.jsonl"))))
    results = [calibration.apply(run, path, label="gold-control", root=result_root(tmp_path / f"result-{i}"))
               for i, run in enumerate((first, second))]
    outputs = [[row["output"] for row in assets.read_jsonl(Path(result["run"]) / "predictions.jsonl")] for result in results]
    assert outputs[0] == outputs[1]
    assert results[0]["metrics"]["mae"]["value"] != results[1]["metrics"]["mae"]["value"]
