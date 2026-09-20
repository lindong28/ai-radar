"""Real O2 rescore/CLI/archive paths with frozen synthetic inputs, no transport."""
import json
from pathlib import Path

import pytest

from evals._shared import assets, cli, score_eval, score_rescore
from evals._shared.metrics import score
from evals._shared.score_calibration import load_source


def source_run(root, *, legacy=True, failure=False):
    dataset = root / "dataset/visible-score/aihot-score-pointwise/v1"
    cases = [{"case_id": str(i), "split": "dev", "input": {"title": f"Input {i}"},
              "reference": {"score": gold}} for i, gold in enumerate([10, 20, 40])]
    predictions = [{"case_id": str(i), "status": "error" if failure and i == 1 else "ok",
                    "output": {"score": value}, "reason": "original reason", "usage": {"total_tokens": 25}}
                   for i, value in enumerate([20, 40, 30])]
    assets.write_jsonl(dataset / "cases.jsonl", cases)
    manifest = {"schema_version": 2, "target": "visible-score", "benchmark": "aihot-score-pointwise",
                "version": "v1", "evaluation_mode": "pointwise", "case_count": len(cases),
                "files": {"cases.jsonl": assets.file_digest(dataset / "cases.jsonl")},
                "shared_evidence": ".", "evidence_files": {}}
    assets.write_json(dataset / "manifest.json", manifest)
    run = root / "original"
    meta = {"target": manifest["target"], "benchmark": manifest["benchmark"], "version": "v1",
            "dataset": str(dataset), "dataset_manifest_sha256": assets.file_digest(dataset / "manifest.json"),
            "case_identity": assets.digest(cases), "case_ids": [c["case_id"] for c in cases],
            "object_identity": {"model": "original", "prompt": "frozen"}, "split": "dev",
            "started_at": "2026-09-20T00:00:00Z"}
    assets.write_json(run / "started.json", meta)
    assets.write_jsonl(run / "cases.jsonl", cases)
    assets.write_jsonl(run / "predictions.jsonl", predictions)
    result = score("O2", cases, predictions)
    if legacy:
        del result["metrics"]["spearman"]
    assets.write_json(run / "scores.json", result)
    return run


def result_root(root):
    relative = "evals/visible-score/aihot-score-pointwise/metrics.json"
    assets.write_json(root / relative, assets.read_json(assets.ROOT / relative))
    return root


@pytest.mark.parametrize("legacy,failure", [(True, False), (False, False), (True, True)])
def test_rescore_retains_original_inputs_predictions_and_all_metrics(tmp_path, legacy, failure):
    source = source_run(tmp_path, legacy=legacy, failure=failure)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    result = score_rescore.rescore(source, label="rescore", root=result_root(tmp_path / "result"))
    assert result["new_api_attempts"] == 0 and result["complete"] is not failure
    destination = Path(result["run"])
    for name in ("cases.jsonl", "predictions.jsonl"):
        assert (destination / name).read_bytes() == before[name]
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    meta = assets.read_json(destination / "started.json")
    assert meta["object_identity"] == assets.read_json(source / "started.json")["object_identity"]
    assert meta["kind"] == "metric-rescore" and meta["cost_usd"] == 0
    assert meta["source_started_at"] == "2026-09-20T00:00:00Z"
    assert meta["source_sha256"]["predictions.jsonl"] == assets.file_digest(source / "predictions.jsonl")
    assert not (destination / "attempts").exists()
    rows = assets.read_json(tmp_path / "result/experiments/metrics/summary.json")
    assert {r["metric_name"] for r in rows} == {"mae", "spearman"}
    assert all(r["metric_value"] == result["metrics"][r["metric_name"]]["value"] for r in rows)
    if not failure:
        assert result["metrics"]["mae"]["value"] == pytest.approx(40 / 3)
        assert result["metrics"]["spearman"]["value"] == .5
    load_source(destination)


@pytest.mark.parametrize("change", ["mae", "spearman", "missing_mae", "unknown", "per_case", "predictions", "dataset"])
def test_rescore_rejects_changes_before_writing(tmp_path, change):
    source = source_run(tmp_path, legacy=change != "spearman")
    result = assets.read_json(source / "scores.json")
    if change in {"mae", "spearman"}:
        result["metrics"][change]["value"] = 0
    elif change == "missing_mae":
        del result["metrics"]["mae"]
    elif change == "unknown":
        result["metrics"]["fake"] = result["metrics"]["mae"]
    elif change == "per_case":
        result["per_case"][0]["fields"]["score"]["absolute_error"] = 99
    elif change == "predictions":
        predictions = assets.read_jsonl(source / "predictions.jsonl")
        predictions[0]["output"]["score"] = 99
        (source / "predictions.jsonl").write_text("\n".join(json.dumps(row) for row in predictions))
    else:
        (tmp_path / "dataset/visible-score/aihot-score-pointwise/v1/cases.jsonl").write_text("[]")
    (source / "scores.json").write_text(json.dumps(result))
    with pytest.raises(ValueError):
        score_rescore.rescore(source, label="bad", root=result_root(tmp_path / "result"))
    assert not (tmp_path / "result/runs").exists()


@pytest.mark.parametrize("machine", [True, False])
def test_cli_rescore_never_constructs_model_transport(tmp_path, monkeypatch, capsys, machine):
    source = source_run(tmp_path)
    root = result_root(tmp_path / "result")
    actual = score_rescore.rescore
    monkeypatch.setattr(score_rescore, "rescore", lambda *a, **kw: actual(*a, **kw, root=root))
    def reject(*a, **kw):
        pytest.fail("rescore must not construct model transport")
    monkeypatch.setattr(cli, "transport_factory", reject)
    args = ["rescore", "--source-run", str(source), "--label", "cli"] + (["--json"] if machine else [])
    assert score_eval.main(args) == 0
    output = capsys.readouterr().out
    if machine:
        result = json.loads(output)
        assert result["new_api_attempts"] == 0 and result["metrics"]["spearman"]["value"] == .5
    else:
        assert "新增模型调用 0" in output and "spearman: 0.500000" in output and "不是新推理" in output


def test_rescore_rejects_missing_manifest_identity_before_writing(tmp_path):
    source = source_run(tmp_path)
    metadata = assets.read_json(source / "started.json")
    del metadata["dataset_manifest_sha256"]
    (source / "started.json").write_text(json.dumps(metadata))
    root = result_root(tmp_path / "result")
    with pytest.raises(ValueError, match="no frozen dataset manifest identity"):
        score_rescore.rescore(source, label="missing-identity", root=root)
    assert not (root / "runs").exists()


def test_repeated_rescore_preserves_original_inference_time(tmp_path):
    source = source_run(tmp_path)
    first = score_rescore.rescore(source, label="first", root=result_root(tmp_path / "first"))
    second = score_rescore.rescore(Path(first["run"]), label="second", root=result_root(tmp_path / "second"))
    first_meta = assets.read_json(Path(first["run"]) / "started.json")
    second_meta = assets.read_json(Path(second["run"]) / "started.json")
    assert first_meta["started_at"] != "2026-09-20T00:00:00Z"
    assert second_meta["source_started_at"] == first_meta["source_started_at"] == "2026-09-20T00:00:00Z"
    assert second_meta["source_run"] == first["run"]
    assert first["metrics"] == second["metrics"]
