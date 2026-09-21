"""Analytic LP oracle plus the real archive/replay/metric consumer."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from airadar.scorer.five import FIVE_WEIGHTS, five_score
from evals._shared import assets
from evals._shared import score_weights as weights
from evals._shared.metrics import score


def payloads():
    return [{"reason": "fixture evidence", **{k: 10 if j == i else 0 for j, k in enumerate(FIVE_WEIGHTS)}}
            for i in range(5)]


def make_run(path, *, split="dev", overlap=None, object_change=False):
    cases = [{"case_id": f"{split}-{i}", "split": split,
              "input": {"url": f"https://fixture/{split}/{i}", "content_text": f"{split} body {i}"},
              "reference": {"score": y}} for i, y in enumerate([30, 25, 20, 15, 10])]
    if overlap:
        if overlap == "case_id":
            cases[0]["case_id"] = "dev-0"
        else:
            cases[0]["input"][overlap] = "https://fixture/dev/0" if overlap == "url" else "dev body 0"
    predictions = [{"case_id": c["case_id"], "status": "ok", "output": {"score": five_score(p)},
                    "response_json": json.dumps(p)} for c, p in zip(cases, payloads(), strict=True)]
    metadata = {"target": weights.TARGET, "benchmark": weights.BENCHMARK, "version": "v1",
                "mode": "five", "split": split, "case_identity": assets.digest(cases),
                "case_ids": [c["case_id"] for c in cases],
                "object_identity": {"mode": "five", "prompt": "other" if object_change else "frozen",
                                    "mapping": {"weights_percent": FIVE_WEIGHTS}}}
    assets.write_json(path / "started.json", metadata)
    assets.write_jsonl(path / "cases.jsonl", cases)
    assets.write_jsonl(path / "predictions.jsonl", predictions)
    assets.write_json(path / "scores.json", score("O2", cases, predictions))
    return path


def result_root(root):
    path = Path("evals") / weights.TARGET / weights.BENCHMARK / "metrics.json"
    assets.write_json(root / path, assets.read_json(assets.ROOT / path))
    return root


def test_analytic_optimum_and_real_rounding():
    # Each input isolates one weight. These labels imply a unique zero-error solution.
    solution = weights.solve(payloads(), [30, 25, 20, 15, 10])
    assert list(solution["weights_percent"].values()) == pytest.approx([30, 25, 20, 15, 10])
    assert solution["unrounded_mae"] == pytest.approx(0)
    assert solution["rounded_mae"] == 0
    assert [five_score(p, solution["weights_percent"]) for p in payloads()] == [30, 25, 20, 15, 10]


def test_grid_and_lp_have_different_objectives():
    refs = [30.1, 24.9, 20.2, 14.8, 10]
    lp = weights.solve(payloads(), refs)
    grid = weights.solve(payloads(), refs, method="grid")
    assert lp["unrounded_mae"] < grid["unrounded_mae"]
    assert lp["rounded_mae"] == pytest.approx(grid["rounded_mae"])
    assert grid["candidate_count"] == 3701
    assert lp["objective"] == "unrounded_mae" and grid["objective"] == "rounded_mae"


@pytest.mark.parametrize("changes", [
    {"lower": 21}, {"upper": 19}, {"lower": -1}, {"upper": 101},
    {"lower": float("nan")}, {"method": "unknown"}, {"method": "grid", "lower": 0},
])
def test_invalid_constraints(changes):
    with pytest.raises(ValueError):
        weights.solve(payloads(), [30, 25, 20, 15, 10], **changes)


@pytest.mark.parametrize("refs", [[], [30], [float("nan")] * 5, [float("inf")] * 5, [True] * 5, [-1] * 5])
def test_bad_references(refs):
    with pytest.raises(ValueError):
        weights.solve(payloads(), refs)


def test_solver_failure_is_not_an_optimum(monkeypatch):
    import scipy.optimize

    monkeypatch.setattr(scipy.optimize, "linprog", lambda *a, **kw: SimpleNamespace(
        success=False, status=1, message="time limit"))
    with pytest.raises(ValueError, match="optimization failed"):
        weights.solve(payloads(), [30, 25, 20, 15, 10])


def test_fit_freeze_replay_and_index(tmp_path):
    dev = make_run(tmp_path / "dev")
    reg = make_run(tmp_path / "reg", split="regression")
    original = {str(p): assets.file_digest(p) for run in (dev, reg) for p in run.iterdir()}
    mapping = tmp_path / "weights.json"
    weights.fit(dev, mapping)
    result = weights.replay(reg, mapping, label="fixture", root=result_root(tmp_path / "out"))
    assert result["complete"] and result["new_model_calls"] == 0
    assert result["metrics"]["mae"]["value"] == 0
    assert result["metrics"]["spearman"]["value"] == 1
    assert all(assets.file_digest(Path(p)) == sha for p, sha in original.items())
    run = Path(result["run"])
    assert assets.read_jsonl(run / "cases.jsonl") == assets.read_jsonl(reg / "cases.jsonl")
    assert len(assets.read_json(run.parents[5] / "experiments/metrics/summary.json")) == 2
    assert not (run / "attempts").exists()
    assert all("response_json" not in row and row["source_prediction"]["sha256"] == original[str(reg / "predictions.jsonl")]
               for row in assets.read_jsonl(run / "predictions.jsonl"))
    with pytest.raises(FileExistsError):
        weights.fit(dev, mapping)


@pytest.mark.parametrize("overlap", ["case_id", "url", "content_text"])
def test_regression_must_not_overlap_fit(tmp_path, overlap):
    dev = make_run(tmp_path / "dev")
    reg = make_run(tmp_path / "reg", split="regression", overlap=overlap)
    mapping = tmp_path / "weights.json"
    weights.fit(dev, mapping)
    with pytest.raises(ValueError, match="overlaps"):
        weights.replay(reg, mapping, label="bad", root=tmp_path)


def test_regression_cannot_fit_and_object_cannot_change(tmp_path):
    reg = make_run(tmp_path / "reg", split="regression", object_change=True)
    mapping = tmp_path / "weights.json"
    with pytest.raises(ValueError, match="requires dev"):
        weights.fit(reg, mapping)
    weights.fit(make_run(tmp_path / "dev"), mapping)
    with pytest.raises(ValueError, match="object differs"):
        weights.replay(reg, mapping, label="bad", root=tmp_path)


def test_archive_mutation_is_detected(tmp_path):
    dev = make_run(tmp_path / "dev")
    mapping = tmp_path / "weights.json"
    weights.fit(dev, mapping)
    original = assets.read_json(mapping)
    changed = copy.deepcopy(original)
    changed["optimization"]["weights_percent"]["impact"] += 1
    mapping.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="identity mismatch"):
        weights.load_mapping(mapping)
    mapping.write_text(json.dumps(original))
    rows = assets.read_jsonl(dev / "predictions.jsonl")
    payload = json.loads(rows[0]["response_json"])
    payload["impact"] = 0
    rows[0]["response_json"] = json.dumps(payload)
    (dev / "predictions.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="do not reproduce"):
        weights.load_mapping(mapping)


def test_cli_failure_and_success_are_distinct(tmp_path, capsys):
    source = make_run(tmp_path / "source")
    output = tmp_path / "weights.json"
    argv = ["fit", "--run", str(source), "--output", str(output)]
    assert weights.main(argv) == 0
    assert "新增模型调用0" in capsys.readouterr().out
    assert weights.main(argv) == 1
    assert "权重实验失败" in capsys.readouterr().err
