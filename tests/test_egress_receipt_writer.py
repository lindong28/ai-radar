from __future__ import annotations

import json
from pathlib import Path

import pytest

from airadar.egress import EgressPreflightError, SelectorPolicy

POLICY_SHA = "a" * 64
OTHER_POLICY_SHA = "b" * 64
BACKUP_TIMESTAMP = "20260905-123456"


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "ai-assistant"
    files = {
        "agents/summary-agent/summarize.sh": "#!/usr/bin/env bash\n",
        "agents/summary-agent/run.sh": "#!/usr/bin/env bash\n",
        "agents/summary-agent/src/summarizer.py": "VALUE = 1\n",
        "shared/project_env.py": "VALUE = 2\n",
        "pyproject.toml": "[project]\nname = 'fixture'\n",
        "uv.lock": "version = 1\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _policy(sha: str) -> SelectorPolicy:
    return SelectorPolicy(
        agent_proxy="http://127.0.0.1:59521",
        policy_id="domain-routing-v2",
        policy_sha256=sha,
    )


def test_writer_rejects_policy_changed_after_attestation_without_touching_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret import receipt_writer

    root = _assistant_root(tmp_path)
    receipt = root / "ai-radar-egress-contract-v2.json"
    original = b'{"previous":true}\n'
    receipt.write_bytes(original)
    tested_implementation_sha = receipt_writer.egress_implementation_sha256(root)
    cache_resets: list[bool] = []
    monkeypatch.setattr(receipt_writer, "reset_selector_policy_cache", lambda: cache_resets.append(True))
    monkeypatch.setattr(receipt_writer, "require_selector_policy", lambda: _policy(OTHER_POLICY_SHA))

    with pytest.raises(receipt_writer.PolicyChangedError, match="changed after attestation"):
        receipt_writer.write_selector_compatibility_receipt(
            assistant_root=root,
            tested_policy_sha256=POLICY_SHA,
            tested_implementation_sha256=tested_implementation_sha,
        )

    assert cache_resets == [True]
    assert receipt.read_bytes() == original
    assert list(root.glob("ai-radar-egress-contract-v2.json.bak-*")) == []


def test_writer_rejects_preflight_failure_without_touching_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret import receipt_writer

    root = _assistant_root(tmp_path)
    receipt = root / "ai-radar-egress-contract-v2.json"
    original = b'{"previous":true}\n'
    receipt.write_bytes(original)
    tested_implementation_sha = receipt_writer.egress_implementation_sha256(root)
    monkeypatch.setattr(receipt_writer, "reset_selector_policy_cache", lambda: None)

    def fail_preflight() -> SelectorPolicy:
        raise EgressPreflightError("synthetic unavailable selector")

    monkeypatch.setattr(receipt_writer, "require_selector_policy", fail_preflight)

    with pytest.raises(EgressPreflightError, match="synthetic unavailable selector"):
        receipt_writer.write_selector_compatibility_receipt(
            assistant_root=root,
            tested_policy_sha256=POLICY_SHA,
            tested_implementation_sha256=tested_implementation_sha,
        )

    assert receipt.read_bytes() == original
    assert list(root.glob("ai-radar-egress-contract-v2.json.bak-*")) == []


def test_writer_rejects_implementation_changed_after_attestation_without_preflight_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret import receipt_writer

    root = _assistant_root(tmp_path)
    receipt = root / "ai-radar-egress-contract-v2.json"
    original = b'{"previous":true}\n'
    receipt.write_bytes(original)
    preflight_calls: list[bool] = []
    monkeypatch.setattr(receipt_writer, "require_selector_policy", lambda: preflight_calls.append(True))

    with pytest.raises(receipt_writer.ImplementationChangedError, match="changed after attestation"):
        receipt_writer.write_selector_compatibility_receipt(
            assistant_root=root,
            tested_policy_sha256=POLICY_SHA,
            tested_implementation_sha256="0" * 64,
        )

    assert preflight_calls == []
    assert receipt.read_bytes() == original
    assert list(root.glob("ai-radar-egress-contract-v2.json.bak-*")) == []


def test_writer_backs_up_old_receipt_and_writes_attested_live_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret import receipt_writer
    from airadar.interpret.runner import expected_selector_compatibility_receipt

    root = _assistant_root(tmp_path)
    receipt = root / "ai-radar-egress-contract-v2.json"
    original = b'{"previous":true}\n'
    receipt.write_bytes(original)
    tested_implementation_sha = receipt_writer.egress_implementation_sha256(root)
    monkeypatch.setattr(receipt_writer, "reset_selector_policy_cache", lambda: None)
    monkeypatch.setattr(receipt_writer, "require_selector_policy", lambda: _policy(POLICY_SHA))
    monkeypatch.setattr(receipt_writer, "_receipt_backup_timestamp", lambda: BACKUP_TIMESTAMP)

    result = receipt_writer.write_selector_compatibility_receipt(
        assistant_root=root,
        tested_policy_sha256=POLICY_SHA,
        tested_implementation_sha256=tested_implementation_sha,
    )

    backup = root / f"ai-radar-egress-contract-v2.json.bak-{BACKUP_TIMESTAMP}"
    assert result.receipt_path == receipt
    assert result.backup_path == backup
    assert result.policy_sha256 == POLICY_SHA
    assert result.egress_implementation_sha256 == tested_implementation_sha
    assert backup.read_bytes() == original
    assert json.loads(receipt.read_text(encoding="utf-8")) == expected_selector_compatibility_receipt(
        policy_sha256=POLICY_SHA,
        egress_implementation_sha256=tested_implementation_sha,
    )


def test_writer_rejects_existing_backup_path_without_touching_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret import receipt_writer

    root = _assistant_root(tmp_path)
    receipt = root / "ai-radar-egress-contract-v2.json"
    original_receipt = b'{"previous":true}\n'
    receipt.write_bytes(original_receipt)
    backup = root / f"ai-radar-egress-contract-v2.json.bak-{BACKUP_TIMESTAMP}"
    original_backup = b'{"older":true}\n'
    backup.write_bytes(original_backup)
    tested_implementation_sha = receipt_writer.egress_implementation_sha256(root)
    monkeypatch.setattr(receipt_writer, "reset_selector_policy_cache", lambda: None)
    monkeypatch.setattr(receipt_writer, "require_selector_policy", lambda: _policy(POLICY_SHA))
    monkeypatch.setattr(receipt_writer, "_receipt_backup_timestamp", lambda: BACKUP_TIMESTAMP)

    with pytest.raises(FileExistsError, match="receipt backup already exists"):
        receipt_writer.write_selector_compatibility_receipt(
            assistant_root=root,
            tested_policy_sha256=POLICY_SHA,
            tested_implementation_sha256=tested_implementation_sha,
        )

    assert receipt.read_bytes() == original_receipt
    assert backup.read_bytes() == original_backup


def test_writer_cli_reports_policy_race_as_blocked_with_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from airadar.interpret import receipt_writer

    monkeypatch.setattr(
        receipt_writer,
        "write_selector_compatibility_receipt",
        lambda **_kwargs: (_ for _ in ()).throw(receipt_writer.PolicyChangedError(POLICY_SHA, OTHER_POLICY_SHA)),
    )

    exit_code = receipt_writer.main(
        [
            "--assistant-root",
            str(tmp_path),
            "--tested-policy-sha",
            POLICY_SHA,
            "--tested-implementation-sha",
            "c" * 64,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "Receipt not written: production policy changed after attestation." in captured.err
    assert f"Attested policy SHA-256: {POLICY_SHA}" in captured.err
    assert f"Live policy SHA-256: {OTHER_POLICY_SHA}" in captured.err
    assert "Impact: receipt was not modified" in captured.err
    assert "Next: rerun the full attestation" in captured.err
