from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from airadar.audit.completeness_oracle import enumerate_web
from airadar.audit.receipts import canonical_payload_sha256, validate_non_x_receipt
from scripts.audit_non_x_retrieval import audit, ensure_new_db_path, sets_equal

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/fixtures/aihot_sources.json"


def test_audit_cli_help_runs_without_installed_package() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/audit_non_x_retrieval.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_refuses_existing_or_default_db(tmp_path: Path) -> None:
    existing = tmp_path / "exists.db"
    existing.touch()
    with pytest.raises(ValueError, match="existing"):
        ensure_new_db_path(existing)
    from airadar.db import DEFAULT_DB_PATH
    with pytest.raises(ValueError, match="default"):
        ensure_new_db_path(DEFAULT_DB_PATH)


@pytest.mark.parametrize("failure_case", ["existing_db", "missing_config"])
def test_cli_setup_failures_still_write_a_valid_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_case: str,
) -> None:
    from scripts import audit_non_x_retrieval as module

    config = ROOT / "data/sources.toml"
    db_path = tmp_path / "audit.db"
    if failure_case == "existing_db":
        db_path.touch()
    else:
        config = tmp_path / "missing.toml"
    output = tmp_path / "failure.json"
    monkeypatch.setattr(
        "sys.argv",
        ["audit_non_x_retrieval.py", "--config", str(config), "--db", str(db_path), "--output", str(output)],
    )

    assert module.main() == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    validate_non_x_receipt(
        payload,
        config_path=config,
        contract_path=CONTRACT,
        repository_root=ROOT,
    )


def test_independent_set_mismatch_fails() -> None:
    with pytest.raises(ValueError, match="mismatch"):
        sets_equal({("https://example.test/a", "A")}, {("https://example.test/b", "B")}, "fixture")


def test_exact_independent_set_passes() -> None:
    expected = {("https://example.test/a", "A")}
    sets_equal(expected, expected, "fixture")


def test_independent_google_research_oracle_rejects_navigation() -> None:
    body = b"""
        <a href="/blog/real-research/"><h2>Real research</h2></a>
        <a href="javascript(0):void">Go to page 1, first page</a>
        <a href="/blog/rss">Follow us on rss</a>
    """

    assert enumerate_web("google_research", body, "https://research.google/blog/") == {
        ("https://research.google/blog/real-research", "Real research"),
    }


def test_membership_failure_writes_the_same_structured_receipt_envelope(tmp_path: Path) -> None:
    config = tmp_path / "sources.toml"
    config.write_text(
        """schema_version = 2
[[source]]
slug = "only_feed"
name = "Only feed"
fetch_url = "https://example.test/feed"
tier = "T2"
enabled = true
paused = false
kind = "feed"
homepage_url = "https://example.test/"
icon_url = "https://example.test/favicon.ico"
""",
        encoding="utf-8",
    )
    output = tmp_path / "receipt.json"

    report = audit(config, tmp_path / "new.db", output)

    assert report["status"] == "failed"
    assert report["phase"] == "membership"
    assert "run_id" not in report
    assert report["failures"] == [{
        "source_id": None,
        "error_class": "ValueError",
        "error_message": "incomplete non-X source membership",
        "request_attempt_count": 0,
        "http_status_code": None,
    }]
    validate_non_x_receipt(report, config_path=config, contract_path=CONTRACT, repository_root=ROOT)
    assert output.is_file()

    report["config_sha256"] = "0" * 64
    report["report_payload_sha256"] = canonical_payload_sha256(report)
    with pytest.raises(ValueError, match="current config"):
        validate_non_x_receipt(report, config_path=config, contract_path=CONTRACT, repository_root=ROOT)
