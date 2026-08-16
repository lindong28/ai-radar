from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from airadar.audit.receipts import canonical_payload_sha256, validate_x_probe_receipt
from airadar.fetcher.runner import SourceFetchSummary
from scripts.generate_x_offline_proof import generate
from scripts.probe_x_source import probe, validate_probe_target


def test_probe_restricts_source_and_requires_new_nondefault_db(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="x_openai"):
        validate_probe_target("x_other", tmp_path / "new.db")
    existing = tmp_path / "existing.db"
    existing.touch()
    with pytest.raises(ValueError, match="existing"):
        validate_probe_target("x_openai", existing)


def test_controlled_offline_proof_is_written_only_after_real_pytest_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "proof.json"
    monkeypatch.setattr(
        "scripts.generate_x_offline_proof.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"failed", b"stderr"),
    )
    with pytest.raises(RuntimeError, match="pytest gate failed"):
        generate(output)
    assert not output.exists()

    monkeypatch.setattr(
        "scripts.generate_x_offline_proof.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"4 passed", b""),
    )
    report = generate(output)
    assert report["status"] == "success"
    assert output.is_file()


def test_probe_persists_structured_401_blocked_state_without_string_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.probe_x_source.read_value", lambda _key: "present")
    monkeypatch.setattr(
        "scripts.probe_x_source._offline_proof",
        lambda: {
            "status": "verified",
            "receipt_path_relative_to_repository_root": "artifacts/test-proof.json",
            "receipt_sha256": "a" * 64,
        },
    )

    def finish_without_repository_file_check(output: Path, report: dict[str, object]) -> dict[str, object]:
        if report["status"] == "failed":
            report["offline_proof"] = {
                "status": "not_evaluated",
                "receipt_path_relative_to_repository_root": None,
                "receipt_sha256": None,
            }
        report["report_payload_sha256"] = canonical_payload_sha256(report)
        root = Path(__file__).resolve().parents[1]
        validate_x_probe_receipt(
            report,
            config_path=root / "data/sources.toml",
            contract_path=root / "tests/fixtures/aihot_sources.json",
            repository_root=root,
        )
        output.write_text(json.dumps(report), encoding="utf-8")
        return report

    monkeypatch.setattr("scripts.probe_x_source._finish", finish_without_repository_file_check)
    monkeypatch.setattr(
        "scripts.probe_x_source.runner.fetch_source",
        lambda _conn, source: SourceFetchSummary(source_id=source.slug, error="opaque", http_status_class="4xx", http_status_code=401),
    )
    output = tmp_path / "receipt.json"

    report = probe("x_openai", tmp_path / "new.db", output)

    assert report["status"] == "failed"
    assert report["request_counts"] == {
        "identity_http_request_count": 1,
        "timeline_http_request_count": 0,
    }
    assert report["http_responses"] == {
        "identity_http_status_code": 401,
        "timeline_http_status_code": None,
    }
    assert report["failures"][0]["reason"] == "authentication_rejected"
    assert "failure_stage" not in report["failures"][0]
    assert report["phase"] == "identity_request"
    assert report["database_state_after_probe"]["x_reference_status"] == "blocked"
    assert report["state_scope"] is None
    assert "run_id" not in report
    assert json.loads(output.read_text())["report_payload_sha256"] == report["report_payload_sha256"]
