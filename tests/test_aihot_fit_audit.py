from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

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

    assert result["l1"]["2_questions"]["status"] in {"located", "missing"}
    assert result["l3"]["object_identity"]["status"] == "executable"
    assert "identity preflight wired=True" in result["l3"]["object_identity"]["evidence"]
    assert result["l3"]["caller_wiring"]["status"] == "partial"
    assert "build/validate/run/judge/report/audit are callable" in result["l3"]["caller_wiring"]["evidence"]
    assert result["l3"]["attribution_admission"]["status"] == "manual"
    assert result["workflow"]["legacy_eval"]["status"] == "unverified"
    rendered = render_audit(result)
    assert "AIHOT eval system audit (read-only; no LLM calls)" in rendered
    assert "title-specific validation remains intentionally absent" in rendered


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
