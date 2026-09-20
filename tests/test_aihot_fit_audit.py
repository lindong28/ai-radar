from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from airadar.eval.aihot_fit.audit import _dispatches_command, _has_direct_call, audit_eval_system, render_audit


def test_direct_caller_probe_distinguishes_present_and_absent_calls(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "def caller():\n    target()\n\ndef disconnected():\n    return 1\n",
        encoding="utf-8",
    )
    assert _has_direct_call(source, "caller", "target") is True
    assert _has_direct_call(source, "disconnected", "target") is False


def test_command_dispatch_probe_uses_the_requested_subcommand_field(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        "def run(args):\n"
        "    if args.eval_fit_command == 'build':\n"
        "        return build_evalset()\n"
        "    if args.command == 'build':\n"
        "        return legacy_build()\n",
        encoding="utf-8",
    )

    assert _dispatches_command(
        source,
        enclosing="run",
        command_field="eval_fit_command",
        command="build",
        target="build_evalset",
    )
    assert not _dispatches_command(
        source,
        enclosing="run",
        command_field="command",
        command="build",
        target="build_evalset",
    )


def test_repository_audit_reports_real_entry_and_known_unconnected_governance() -> None:
    root = Path(__file__).resolve().parents[1]
    result = audit_eval_system(project_root=root, runs_dir=root / "data/eval-fit/runs")

    assert result["l1"]["2_questions"]["status"] in {"located", "missing"}
    assert result["l3"]["object_identity"]["status"] == "executable"
    assert "identity preflight wired=True" in result["l3"]["object_identity"]["evidence"]
    assert result["l1"]["4_metrics"]["status"] == "located"
    assert result["l3"]["caller_wiring"]["status"] == "executable"
    assert "eval-fit command dispatch=6/6" in result["l3"]["caller_wiring"]["evidence"]
    assert result["workflow"]["optimization"]["status"] == "partial"
    assert "replay_real_runs.py" in result["workflow"]["optimization"]["path"]
    assert "direct archive counterfactual remains unavailable" in result["workflow"]["optimization"]["evidence"]
    assert "archive ledger caller=True" in result["l3"]["caller_wiring"]["evidence"]
    assert result["l3"]["attribution_admission"]["status"] == "manual"
    assert result["workflow"]["legacy_eval"]["status"] == "located"
    assert "role=separate legacy snapshot comparison/reporting tool" in result["workflow"]["legacy_eval"]["evidence"]
    rendered = render_audit(result)
    assert "AIHOT eval system audit (read-only; no LLM calls)" in rendered
    assert "title-specific validation remains intentionally absent" in rendered


def test_legacy_eval_status_requires_the_complete_static_output_chain(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src/airadar/eval/aihot_fit").mkdir(parents=True)
    cli = root / "src/airadar/cli.py"
    judge = root / "src/airadar/eval/judge.py"
    judge.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text(
        """
def add_commands(subparsers):
    subparsers.add_parser("eval")

def _eval(args):
    artifacts = run_eval(args)
    print(artifacts.report_path, artifacts.compare_path)

def main(args):
    if args.command == "eval":
        return _eval(args)
""",
        encoding="utf-8",
    )
    judge.write_text(
        """
def run_eval(output_dir):
    report_path = output_dir / "report.md"
    compare_path = output_dir / "compare.html"
    report_path.write_text("report")
    compare_path.write_text("compare")
    return EvaluationArtifacts(report_path=report_path, compare_path=compare_path, metrics={})
""",
        encoding="utf-8",
    )

    connected = audit_eval_system(project_root=root, runs_dir=root / "runs")
    assert connected["workflow"]["legacy_eval"]["status"] == "located"

    judge.write_text("def run_eval(output_dir):\n    return None\n", encoding="utf-8")
    disconnected = audit_eval_system(project_root=root, runs_dir=root / "runs")
    assert disconnected["workflow"]["legacy_eval"]["status"] == "invalid"
    assert "output_contract=False" in disconnected["workflow"]["legacy_eval"]["evidence"]


def test_legacy_reports_are_preserved_as_non_comparable_instead_of_awaiting_recompute(tmp_path: Path) -> None:
    root = tmp_path
    fit = root / "src/airadar/eval/aihot_fit"
    fit.mkdir(parents=True)
    (fit / "cli.py").write_text(
        "def run_eval_fit(args):\n"
        "    if args.eval_fit_command == 'report':\n"
        "        return compute_metrics()\n",
        encoding="utf-8",
    )
    (fit / "metrics.py").write_text(
        "def compute_metrics():\n    judge_acceptance()\n    compare_to_baseline()\n",
        encoding="utf-8",
    )
    run_dir = root / "runs/legacy"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"questions_sha256": "q" * 64}), encoding="utf-8")
    (run_dir / "outputs.jsonl").write_text('{}\n', encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps({"questions_sha256": "q" * 64}), encoding="utf-8")

    result = audit_eval_system(project_root=root, runs_dir=root / "runs")

    assert result["l3"]["comparison_window"]["status"] == "executable"
    assert "governed_runs=0/1" in result["l3"]["comparison_window"]["evidence"]
    assert "governed_reports=0/1" in result["l3"]["comparison_window"]["evidence"]
    assert "legacy assets are preserved as non-comparable" in result["l3"]["comparison_window"]["evidence"]
    assert "recomput" not in result["l3"]["comparison_window"]["evidence"]


def test_malformed_run_metadata_is_not_silently_classified_as_legacy(tmp_path: Path) -> None:
    root = tmp_path
    fit = root / "src/airadar/eval/aihot_fit"
    fit.mkdir(parents=True)
    (fit / "cli.py").write_text("def run_eval_fit():\n    return None\n", encoding="utf-8")
    run_dir = root / "runs/malformed"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("[]", encoding="utf-8")

    result = audit_eval_system(project_root=root, runs_dir=root / "runs")

    assert result["l3"]["comparison_window"]["status"] == "invalid"
    assert result["details"]["report_identity_inventory"]["invalid"] == 1


def test_optimization_is_partial_without_the_real_run_replay_entry(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src/airadar/eval/aihot_fit").mkdir(parents=True)

    result = audit_eval_system(project_root=root, runs_dir=root / "runs")

    assert result["workflow"]["optimization"]["status"] == "partial"
    assert "replay entry wired=False" in result["workflow"]["optimization"]["evidence"]


def test_current_ballot_provenance_uses_the_same_observed_state_in_l2_and_l3(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src/airadar/eval/aihot_fit").mkdir(parents=True)
    ballot = root / ".label-serve/quota-accept"
    ballot.mkdir(parents=True)

    present = audit_eval_system(project_root=root, runs_dir=root / "runs")
    assert present["l2"]["4_human_evaluations"]["status"] == "located"
    assert present["l3"]["provenance"]["status"] == "located"
    assert "current ballot asset is present" in present["l3"]["provenance"]["evidence"]

    ballot.rmdir()
    ballot.parent.rmdir()
    absent = audit_eval_system(project_root=root, runs_dir=root / "runs")
    assert absent["l2"]["4_human_evaluations"]["status"] == "unverified"
    assert absent["l3"]["provenance"]["status"] == "partial"
    assert "current ballot asset was not located" in absent["l3"]["provenance"]["evidence"]


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
    (root / "data/aihot-reference").mkdir(parents=True)
    (root / ".gitmodules").write_text(
        '[submodule "benchmarks/aihot"]\n\tpath = data/aihot-reference\n\turl = ../aihot-benchmark\n',
        encoding="utf-8",
    )

    result = audit_eval_system(project_root=root, runs_dir=root / "data/eval-fit/runs")

    assert result["l1"]["2_questions"]["status"] == "missing"
    assert "gitlink present but submodule not initialized" in result["l1"]["2_questions"]["evidence"]


def test_audit_does_not_modify_ledger_or_archive_history(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src/airadar/eval/aihot_fit").mkdir(parents=True)
    (root / "scripts/eval").mkdir(parents=True)
    ledger = root / "scripts/eval/aihot-fit-history.jsonl"
    history = root / "scripts/eval/composition-history.jsonl"
    ledger.write_text(
        '{"event_id":"event-1","attempt_id":"attempt-1","event":"started"}\n',
        encoding="utf-8",
    )
    history.write_text(
        '{"record_id":"record-1","surface":"archive"}\n',
        encoding="utf-8",
    )
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (ledger, history)}

    audit_eval_system(project_root=root, runs_dir=root / "data/eval-fit/runs", ledger_path=ledger)

    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (ledger, history)}
    assert after == before


def test_audit_recomputes_the_archive_row_digest_instead_of_trusting_record_id(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src/airadar/eval/aihot_fit").mkdir(parents=True)
    (root / "scripts/eval").mkdir(parents=True)
    ledger = root / "scripts/eval/aihot-fit-history.jsonl"
    history = root / "scripts/eval/composition-history.jsonl"
    archive_row = {"record_id": "record-1", "surface": "archive", "inside_count": 3}
    encoded = json.dumps(archive_row, ensure_ascii=False)
    history.write_text(encoded + "\n", encoding="utf-8")
    attempt_id = str(uuid4())
    common = {
        "schema_version": "aihot-fit-ledger-event-v1",
        "attempt_id": attempt_id,
        "round_id": "round-1",
        "stage": "archive",
        "recorded_at": "2026-09-14T00:00:00Z",
        "artifacts": {},
        "relations": {},
    }
    rows = [
        {**common, "event_id": str(uuid4()), "event": "started", "status": "incomplete"},
        {
            **common,
            "event_id": str(uuid4()),
            "event": "completed",
            "status": "succeeded",
            "artifacts": {"composition_history": "scripts/eval/composition-history.jsonl"},
            "relations": {
                "composition_record_id": "record-1",
                "composition_row_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            },
        },
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    before = audit_eval_system(project_root=root, runs_dir=root / "runs", ledger_path=ledger)
    assert before["l3"]["archive_index"]["status"] == "located"

    archive_row["inside_count"] = 5
    history.write_text(json.dumps(archive_row, ensure_ascii=False) + "\n", encoding="utf-8")
    after = audit_eval_system(project_root=root, runs_dir=root / "runs", ledger_path=ledger)

    assert after["l3"]["archive_index"]["status"] == "invalid"
    assert after["details"]["invalid_archive_index_refs"] == ["record-1:row_sha256"]


def test_pre_contract_archive_rows_do_not_degrade_the_current_index(tmp_path: Path) -> None:
    root = tmp_path
    (root / "src/airadar/eval/aihot_fit").mkdir(parents=True)
    (root / "scripts/eval").mkdir(parents=True)
    history = root / "scripts/eval/composition-history.jsonl"
    ledger = root / "scripts/eval/aihot-fit-history.jsonl"
    legacy = json.dumps({"surface": "archive", "inside_count": 3})
    current_row = {"record_id": "record-1", "surface": "archive", "inside_count": 2}
    current = json.dumps(current_row)
    history.write_text(f"{legacy}\n{current}\n", encoding="utf-8")
    attempt_id = str(uuid4())
    common = {
        "schema_version": "aihot-fit-ledger-event-v1",
        "attempt_id": attempt_id,
        "round_id": "round-1",
        "stage": "archive",
        "recorded_at": "2026-09-14T00:00:00Z",
        "artifacts": {},
        "relations": {},
    }
    rows = [
        {**common, "event_id": str(uuid4()), "event": "started", "status": "incomplete"},
        {
            **common,
            "event_id": str(uuid4()),
            "event": "completed",
            "status": "succeeded",
            "artifacts": {"composition_history": "scripts/eval/composition-history.jsonl"},
            "relations": {
                "composition_record_id": "record-1",
                "composition_row_sha256": hashlib.sha256(current.encode("utf-8")).hexdigest(),
            },
        },
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = audit_eval_system(project_root=root, runs_dir=root / "runs", ledger_path=ledger)

    assert result["l3"]["archive_index"]["status"] == "located"
    assert "pre_contract_legacy=1" in result["l3"]["archive_index"]["evidence"]
