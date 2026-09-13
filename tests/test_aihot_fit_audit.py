from __future__ import annotations

from pathlib import Path

from airadar.eval.aihot_fit.audit import _has_direct_call, audit_eval_system, render_audit


def test_direct_caller_probe_distinguishes_present_and_absent_calls(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "def caller():\n    target()\n\ndef disconnected():\n    return 1\n",
        encoding="utf-8",
    )
    assert _has_direct_call(source, "caller", "target") is True
    assert _has_direct_call(source, "disconnected", "target") is False


def test_repository_audit_reports_real_entry_and_known_unconnected_governance() -> None:
    root = Path(__file__).resolve().parents[1]
    result = audit_eval_system(project_root=root, runs_dir=root / "data/eval-fit/runs")

    assert result["l1"]["2_questions"]["status"] == "located"
    assert result["l3"]["object_identity"]["status"] == "partial"
    assert "no eval-identity-generated receipt" in result["l3"]["object_identity"]["evidence"]
    assert result["l3"]["caller_wiring"]["status"] == "partial"
    assert "static syntax only" in result["l3"]["caller_wiring"]["evidence"]
    assert result["l3"]["attribution_admission"]["status"] == "missing"
    assert result["workflow"]["legacy_eval"]["status"] == "unverified"
    rendered = render_audit(result)
    assert "AIHOT eval system audit (read-only; no LLM calls)" in rendered
    assert "title and final 62-92 score lack direct metrics" in rendered


def test_uninitialized_submodule_is_not_reported_as_no_historical_asset(tmp_path: Path) -> None:
    root = tmp_path
    fit = root / "src/airadar/eval/aihot_fit"
    fit.mkdir(parents=True)
    (fit / "cli.py").write_text(
        "def run_eval_fit():\n    audit_eval_system()\n    compute_metrics()\n",
        encoding="utf-8",
    )
    (fit / "metrics.py").write_text(
        "def compute_metrics():\n    judge_acceptance()\n    compare_to_baseline()\n",
        encoding="utf-8",
    )
    (root / "benchmarks/aihot").mkdir(parents=True)
    (root / ".gitmodules").write_text(
        '[submodule "benchmarks/aihot"]\n\tpath = benchmarks/aihot\n\turl = ../aihot-benchmark\n',
        encoding="utf-8",
    )

    result = audit_eval_system(project_root=root, runs_dir=root / "data/eval-fit/runs")

    assert result["l1"]["2_questions"]["status"] == "missing"
    assert "gitlink present but submodule not initialized" in result["l1"]["2_questions"]["evidence"]
