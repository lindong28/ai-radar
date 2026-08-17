"""Behavioral contract for the Mac-side database producer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "sync" / "sync-db-to-server.sh"
SNAPSHOT_HELPER = REPO_ROOT / "deploy" / "sync" / "snapshot_db.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_snapshot_helper() -> ModuleType:
    assert SNAPSHOT_HELPER.is_file(), "tracked snapshot helper is missing"
    spec = importlib.util.spec_from_file_location("snapshot_db_fixture", SNAPSHOT_HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _logical_digest(path: Path) -> str:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        dump = "\n".join(connection.iterdump()).encode("utf-8")
    finally:
        connection.close()
    return hashlib.sha256(dump).hexdigest()


def _assert_snapshot_consistent(source: Path, snapshot: Path) -> None:
    assert snapshot.is_file()
    assert _logical_digest(snapshot) == _logical_digest(source)
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def _create_live_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE sources (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              kind TEXT NOT NULL DEFAULT 'feed'
            );
            CREATE TABLE items (
              id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              url TEXT NOT NULL,
              title TEXT NOT NULL,
              content_text TEXT NOT NULL,
              author TEXT NOT NULL,
              published_at TEXT NOT NULL,
              fetched_at TEXT NOT NULL
            );
            CREATE TABLE item_evaluations (
              id INTEGER PRIMARY KEY,
              item_id TEXT NOT NULL,
              stage TEXT NOT NULL,
              numeric_json TEXT,
              error TEXT
            );
            CREATE VIRTUAL TABLE items_fts USING fts5(
              item_id UNINDEXED,
              title,
              content_text,
              source_name,
              author,
              title_zh,
              tokenize='trigram'
            );
            INSERT INTO sources (id, name, enabled, kind)
            VALUES ('generic', 'Generic Source', 1, 'feed');
            INSERT INTO sources (id, name, enabled, kind)
            VALUES ('exclusive', 'SourceOnlyCedar', 1, 'feed');
            """
        )
        rows = [
            ("i-title", "TitleOnlyBeacon", "generic body", "Generic Source", "Generic Author", "通用译名甲"),
            ("i-content", "generic title", "ContentOnlyHarbor", "Generic Source", "Generic Author", "通用译名乙"),
            ("i-source", "generic title", "generic body", "SourceOnlyCedar", "Generic Author", "通用译名丙"),
            ("i-author", "generic title", "generic body", "Generic Source", "AuthorOnlyQuartz", "通用译名丁"),
            ("i-zh", "generic title", "generic body", "Generic Source", "Generic Author", "中文独有灯塔词"),
        ]
        connection.executemany(
            "INSERT INTO items "
            "(id, source_id, url, title, content_text, author, published_at, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z')",
            [
                (
                    row[0],
                    "exclusive" if row[0] == "i-source" else "generic",
                    f"https://example.invalid/{row[0]}",
                    row[1],
                    row[2],
                    row[4],
                )
                for row in rows
            ],
        )
        connection.executemany(
            "INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_tools(root: Path) -> tuple[Path, Path]:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    call_log = root / "calls.log"
    _write_executable(
        fake_bin / "sqlite3",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'SQLITE3' >> "$FAKE_CALL_LOG"
printf ' <%s>' "$@" >> "$FAKE_CALL_LOG"
printf '\n' >> "$FAKE_CALL_LOG"
exec "$FAKE_REAL_SQLITE" "$@"
""",
    )
    _write_executable(
        fake_bin / "rsync",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'rsync  version 3.4.1  protocol version 32\n'
  printf 'Compress list: zstd lz4 zlibx zlib none\n'
  exit 0
fi
printf 'RSYNC' >> "$FAKE_CALL_LOG"
printf ' <%s>' "$@" >> "$FAKE_CALL_LOG"
printf '\n' >> "$FAKE_CALL_LOG"
printf 'Total bytes sent: 12,345\n'
""",
    )
    _write_executable(
        fake_bin / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'SSH' >> "$FAKE_CALL_LOG"
printf ' <%s>' "$@" >> "$FAKE_CALL_LOG"
printf '\n' >> "$FAKE_CALL_LOG"
if [[ "$*" == *'rsync --version'* ]]; then
  printf 'rsync  version 3.2.7  protocol version 31\n'
  if [[ "${FAKE_REMOTE_ZSTD:-1}" == "1" ]]; then
    printf 'Compress list: zstd lz4 zlibx zlib none\n'
  else
    printf 'Compress list: zlibx zlib none\n'
  fi
fi
if [[ "${FAKE_MANIFEST_CONFLICT:-0}" == "1" && "$*" == *'manifest identity conflict'* ]]; then
  exit 42
fi
if [[ -n "${FAKE_APPLY_TRIGGER_COMMAND:-}" && "$*" == *"$FAKE_APPLY_TRIGGER_COMMAND"* ]]; then
  "$FAKE_REAL_PYTHON" - \
    "$FAKE_APPLY_STATUS" "$AI_RADAR_SYNC_MANIFEST" \
    "$FAKE_REMOTE_JOURNAL" "$FAKE_REMOTE_RECEIPT" <<'PY'
import hashlib
import json
import pathlib
import sys

status, manifest_path, journal_path, receipt_path = sys.argv[1:]
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))
snapshot_id = manifest["snapshot_id"]
manifest_sha256 = manifest["manifest_sha256"]
journal = pathlib.Path(journal_path)
receipt = pathlib.Path(receipt_path)
journal.parent.mkdir(parents=True, exist_ok=True)
if status == "committed":
    journal.write_text(json.dumps({
        "journal_schema_version": 2,
        "state": "committed",
        "snapshot_id": snapshot_id,
        "manifest_sha256": manifest_sha256,
        "serving_port": "19001",
    }))
    receipt.write_text(json.dumps({
        "receipt_schema_version": 2,
        "snapshot_id": snapshot_id,
        "manifest_sha256": manifest_sha256,
        "serving_port": "19001",
    }))
elif status in {"quarantined", "quarantined_other_snapshot"}:
    failure_id = "f" * 32
    failure_path = journal.parent / f"failure.{failure_id}.json"
    failure_path.write_text(json.dumps({
        "failure_schema_version": 1,
        "failure_id": failure_id,
        "snapshot_id": snapshot_id if status == "quarantined" else "0" * 64,
        "message": "fixture deterministic gate failure",
    }))
    failure_sha256 = hashlib.sha256(failure_path.read_bytes()).hexdigest()
    journal.write_text(json.dumps({
        "journal_schema_version": 2,
        "state": "quarantined",
        "failure_id": failure_id,
        "failure_path": str(failure_path),
        "failure_sha256": failure_sha256,
    }))
elif status in {
    "retry_blocked_verifier_changed",
    "rollback_blocked_invalid_oracle",
    "finalize_blocked_invalid_authority",
}:
    journal.write_text(json.dumps({
        "journal_schema_version": 2,
        "state": status,
        "snapshot_id": snapshot_id,
        "manifest_sha256": manifest_sha256,
        "candidate_port": "19001",
    }))
elif status != "pending":
    raise SystemExit(f"unknown fixture status: {status}")
PY
fi
if [[ "$*" == *'AI_RADAR_TERMINAL_POLL=1'* \
      || "$*" == *'AI_RADAR_JOURNAL_GENERATION=1'* ]]; then
  remote_command="${@: -1}"
  exec bash -c "$remote_command"
fi
""",
    )
    return fake_bin, call_log


def _producer_env(tmp_path: Path, live: Path, *, remote_zstd: bool = True) -> dict[str, str]:
    real_sqlite = shutil.which("sqlite3")
    assert real_sqlite is not None
    fake_bin, call_log = _fake_tools(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_CALL_LOG": str(call_log),
            "FAKE_REAL_SQLITE": real_sqlite,
            "FAKE_REAL_PYTHON": sys.executable,
            "FAKE_REMOTE_ZSTD": "1" if remote_zstd else "0",
            "FAKE_APPLY_STATUS": "committed",
            "FAKE_APPLY_TRIGGER_COMMAND": (
                "sudo systemctl start ai-radar-db-apply.service --no-block"
            ),
            "AI_RADAR_RSYNC": str(fake_bin / "rsync"),
            "AI_RADAR_PYTHON": sys.executable,
            "AI_RADAR_SYNC_DB": str(live),
            "AI_RADAR_SYNC_SERVER": "fixture-server",
            "AI_RADAR_SYNC_REMOTE_DATA": "fixture/data",
            "AI_RADAR_SYNC_SNAPSHOT": str(tmp_path / "snapshot.db"),
            "AI_RADAR_SYNC_REPLICA": str(tmp_path / "shipping.db"),
            "AI_RADAR_SYNC_MANIFEST": str(tmp_path / "shipping.manifest.json"),
            "AI_RADAR_SYNC_LOCK": str(tmp_path / ".sync.lock"),
            "AI_RADAR_SYNC_SSH_OPTS": "-o BatchMode=yes",
            "AI_RADAR_SYNC_REMOTE_JOURNAL": str(tmp_path / "remote-journal.json"),
            "AI_RADAR_SYNC_REMOTE_RECEIPT": str(tmp_path / "remote-receipt.json"),
            "AI_RADAR_SYNC_REMOTE_PYTHON": sys.executable,
            "AI_RADAR_SYNC_APPLY_TIMEOUT_S": "1",
            "AI_RADAR_SYNC_APPLY_POLL_INTERVAL_S": "0",
        }
    )
    env["FAKE_REMOTE_JOURNAL"] = env["AI_RADAR_SYNC_REMOTE_JOURNAL"]
    env["FAKE_REMOTE_RECEIPT"] = env["AI_RADAR_SYNC_REMOTE_RECEIPT"]
    return env


def _run_producer(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_snapshot_helper_enables_and_verifies_query_only_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = _load_snapshot_helper()
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE guarded (value TEXT)")
        connection.execute("INSERT INTO guarded VALUES ('original')")
        connection.commit()
    finally:
        connection.close()

    statements: list[str] = []
    real_connect = sqlite3.connect

    class TrackingSource(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /):  # noqa: ANN201
            statements.append(sql)
            return super().execute(sql, parameters)

        def backup(self, target: sqlite3.Connection, **kwargs: Any) -> None:
            assert self.execute("PRAGMA query_only").fetchone() == (1,)
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                self.execute("INSERT INTO guarded VALUES ('forbidden')")
            super().backup(target, **kwargs)

    def tracking_connect(database: Any, *args: Any, **kwargs: Any):  # noqa: ANN202
        if str(database).startswith("file:") and "mode=rw" in str(database):
            kwargs["factory"] = TrackingSource
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(helper.sqlite3, "connect", tracking_connect)

    helper.backup_database(source, snapshot)

    assert statements[:2] == ["PRAGMA query_only=ON", "PRAGMA query_only"]
    _assert_snapshot_consistent(source, snapshot)


def test_wal_snapshot_succeeds_without_sidecars_and_preserves_source_bytes(
    tmp_path: Path,
) -> None:
    helper = _load_snapshot_helper()
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    _create_live_db(source)
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute(
            "UPDATE items SET fetched_at='2026-02-03T04:05:06Z' WHERE id='i-title'"
        )
        connection.commit()
    finally:
        connection.close()
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()
    expected_digest = _logical_digest(source)
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()
    source_inode = source.stat().st_ino
    source_sha256 = _sha256(source)

    helper.backup_database(source, snapshot)

    assert source.stat().st_ino == source_inode
    assert _sha256(source) == source_sha256
    assert _logical_digest(source) == expected_digest
    _assert_snapshot_consistent(source, snapshot)


def test_wal_snapshot_succeeds_with_active_writer_sidecars(tmp_path: Path) -> None:
    helper = _load_snapshot_helper()
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    _create_live_db(source)
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "UPDATE items SET fetched_at='2026-03-04T05:06:07Z' WHERE id='i-title'"
        )
        writer.commit()
        assert Path(f"{source}-wal").stat().st_size > 0
        assert Path(f"{source}-shm").is_file()
        expected_digest = _logical_digest(source)

        helper.backup_database(source, snapshot)

        assert _logical_digest(source) == expected_digest
        _assert_snapshot_consistent(source, snapshot)
    finally:
        writer.close()


def test_wal_snapshot_is_consistent_when_writer_starts_mid_backup(
    tmp_path: Path,
) -> None:
    helper = _load_snapshot_helper()
    source = tmp_path / "source.db"
    snapshot = tmp_path / "snapshot.db"
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE payload (id INTEGER PRIMARY KEY, value BLOB)")
        connection.executemany(
            "INSERT INTO payload(value) VALUES (?)",
            [(b"x" * 2048,) for _ in range(3000)],
        )
        connection.commit()
    finally:
        connection.close()
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()
    writer_start = threading.Event()
    writer_done = threading.Event()
    writer_errors: list[BaseException] = []

    def write_transaction() -> None:
        try:
            assert writer_start.wait(timeout=5)
            with sqlite3.connect(source, timeout=5) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO payload(value) VALUES (?)", (b"first",))
                writer.execute("INSERT INTO payload(value) VALUES (?)", (b"second",))
                writer.commit()
        except BaseException as exc:  # preserve thread failures for the test thread
            writer_errors.append(exc)
        finally:
            writer_done.set()

    writer = threading.Thread(target=write_transaction)
    writer.start()
    started_mid_backup = False

    def start_writer(_status: int, remaining: int, _total: int) -> None:
        nonlocal started_mid_backup
        if not started_mid_backup and remaining > 0:
            started_mid_backup = True
            writer_start.set()
            assert writer_done.wait(timeout=5)

    try:
        helper.backup_database(source, snapshot, pages=1, progress=start_writer)
    finally:
        writer_start.set()
        writer.join(timeout=5)

    assert not writer.is_alive()
    assert not writer_errors
    assert started_mid_backup
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT COUNT(*) FROM payload").fetchone() == (3002,)
    _assert_snapshot_consistent(source, snapshot)


def test_producer_default_apply_timeout_has_measured_headroom() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'APPLY_TIMEOUT_S="${AI_RADAR_SYNC_APPLY_TIMEOUT_S:-3600}"' in source


def test_bootstrap_then_incremental_publish_content_addressed_manifest_first(
    tmp_path: Path,
) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    live_before = _sha256(live)

    first = _run_producer(env)

    assert first.returncode == 0, first.stderr + first.stdout
    assert "mode=bootstrap" in first.stdout
    assert _sha256(live) == live_before
    replica = Path(env["AI_RADAR_SYNC_REPLICA"])
    manifest_path = Path(env["AI_RADAR_SYNC_MANIFEST"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["snapshot_id"] == _sha256(replica)
    first_snapshot_id = payload["snapshot_id"]

    connection = sqlite3.connect(live)
    try:
        connection.execute(
            "UPDATE items SET fetched_at = '2026-01-02T00:00:00Z' WHERE id = 'i-title'"
        )
        connection.commit()
    finally:
        connection.close()
    live_second_before = _sha256(live)
    second = _run_producer(env)

    assert second.returncode == 0, second.stderr + second.stdout
    assert "mode=incremental" in second.stdout
    assert _sha256(live) == live_second_before
    second_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert second_payload["snapshot_id"] == _sha256(replica)
    second_snapshot_id = second_payload["snapshot_id"]

    calls = Path(env["FAKE_CALL_LOG"]).read_text(encoding="utf-8").splitlines()
    manifest_uploads = [index for index, line in enumerate(calls) if line.startswith("RSYNC") and "fts-manifest" in line]
    manifest_publishes = [index for index, line in enumerate(calls) if line.startswith("SSH") and "fts-manifest" in line and "mv -n" in line]
    database_uploads = [index for index, line in enumerate(calls) if line.startswith("RSYNC") and "radar.db.upload" in line]
    database_publishes = [index for index, line in enumerate(calls) if line.startswith("SSH") and "radar.db.incoming" in line]
    assert len(manifest_uploads) == len(manifest_publishes) == 2
    assert len(database_uploads) == len(database_publishes) == 2
    for round_index, snapshot_id in enumerate((first_snapshot_id, second_snapshot_id)):
        assert manifest_uploads[round_index] < manifest_publishes[round_index]
        assert manifest_publishes[round_index] < database_uploads[round_index]
        assert database_uploads[round_index] < database_publishes[round_index]
        assert snapshot_id in calls[manifest_publishes[round_index]]
        assert "cmp -s" in calls[manifest_publishes[round_index]]

    assert sum(
        "sudo systemctl start ai-radar-db-apply.service --no-block" in call
        for call in calls
    ) == 2
    assert sum("AI_RADAR_TERMINAL_POLL=1" in call for call in calls) == 2

    transfer_lines = [line for line in calls if line.startswith("RSYNC")]
    assert all("<--no-whole-file>" in line for line in transfer_lines)
    assert all("<--block-size=4096>" in line for line in transfer_lines)
    assert all("<--compress-choice=zstd>" in line for line in transfer_lines)
    assert all("<--compress-level=3>" in line for line in transfer_lines)


def test_capability_gate_falls_back_to_uncompressed_4096_blocks(tmp_path: Path) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live, remote_zstd=False)

    completed = _run_producer(env)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "falling back to uncompressed" in completed.stdout
    transfer_lines = [
        line
        for line in Path(env["FAKE_CALL_LOG"]).read_text(encoding="utf-8").splitlines()
        if line.startswith("RSYNC")
    ]
    assert transfer_lines
    assert all("<--no-whole-file>" in line for line in transfer_lines)
    assert all("<--block-size=4096>" in line for line in transfer_lines)
    assert all("compress-choice" not in line for line in transfer_lines)
    assert all("compress-level" not in line for line in transfer_lines)


def test_custom_apply_trigger_is_honored_and_committed_maps_to_success(
    tmp_path: Path,
) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    trigger = "fixture-isolated-apply --env-file fixture-preflight.env"
    env["AI_RADAR_SYNC_REMOTE_APPLY_TRIGGER"] = trigger
    env["FAKE_APPLY_TRIGGER_COMMAND"] = trigger

    completed = _run_producer(env)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "terminal state committed" in completed.stdout
    calls = Path(env["FAKE_CALL_LOG"]).read_text(encoding="utf-8").splitlines()
    assert any(trigger in call for call in calls if call.startswith("SSH"))
    assert not any(
        "sudo systemctl start ai-radar-db-apply.service --no-block" in call
        for call in calls
    )


@pytest.mark.parametrize(
    ("terminal_state", "message"),
    [
        ("quarantined", "terminal state quarantined"),
        ("retry_blocked_verifier_changed", "terminal state blocked"),
        ("rollback_blocked_invalid_oracle", "terminal state blocked"),
        ("finalize_blocked_invalid_authority", "terminal state blocked"),
    ],
)
def test_snapshot_bound_rejected_terminals_fail_the_producer(
    tmp_path: Path, terminal_state: str, message: str
) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    env["FAKE_APPLY_STATUS"] = terminal_state

    completed = _run_producer(env)

    assert completed.returncode == 1
    assert message in completed.stderr


def test_same_round_terminal_poll_timeout_fails_the_producer(tmp_path: Path) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    env["FAKE_APPLY_STATUS"] = "pending"
    env["AI_RADAR_SYNC_APPLY_TIMEOUT_S"] = "0"

    completed = _run_producer(env)

    assert completed.returncode == 1
    assert "timed out waiting for terminal state" in completed.stderr


def test_terminal_parser_ignores_quarantine_for_another_snapshot(
    tmp_path: Path,
) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    env["FAKE_APPLY_STATUS"] = "quarantined_other_snapshot"
    env["AI_RADAR_SYNC_APPLY_TIMEOUT_S"] = "0"

    completed = _run_producer(env)

    assert completed.returncode == 1
    assert "timed out waiting for terminal state" in completed.stderr
    assert "terminal state quarantined" not in completed.stderr


def test_terminal_parser_does_not_reuse_same_snapshot_terminal_from_prior_round(
    tmp_path: Path,
) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    env["FAKE_APPLY_STATUS"] = "quarantined"

    first = _run_producer(env)

    assert first.returncode == 1
    assert "terminal state quarantined" in first.stderr
    env["FAKE_APPLY_STATUS"] = "pending"
    env["AI_RADAR_SYNC_APPLY_TIMEOUT_S"] = "0"

    second = _run_producer(env)

    assert second.returncode == 1
    assert "timed out waiting for terminal state" in second.stderr
    assert "terminal state quarantined" not in second.stderr


def test_manifest_identity_conflict_refuses_database_commit_marker(tmp_path: Path) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    env["FAKE_MANIFEST_CONFLICT"] = "1"

    completed = _run_producer(env)

    assert completed.returncode != 0
    assert "immutable manifest publish failed" in completed.stderr
    calls = Path(env["FAKE_CALL_LOG"]).read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("RSYNC") and "fts-manifest" in line for line in calls)
    assert not any(line.startswith("RSYNC") and "radar.db.upload" in line for line in calls)
    assert not any("radar.db.incoming" in line for line in calls)


def test_live_path_alias_is_refused_before_snapshot_removal(tmp_path: Path) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    env = _producer_env(tmp_path, live)
    env["AI_RADAR_SYNC_SNAPSHOT"] = str(live)
    live_before = _sha256(live)

    completed = _run_producer(env)

    assert completed.returncode != 0
    assert "share path" in completed.stderr
    assert live.exists()
    assert _sha256(live) == live_before
    call_log = Path(env["FAKE_CALL_LOG"])
    calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
    assert "radar.db.upload" not in calls


@pytest.mark.parametrize("keep_writer_open", [False, True])
def test_producer_snapshots_wal_with_or_without_sidecars(
    tmp_path: Path, keep_writer_open: bool
) -> None:
    if shutil.which("sqlite3") is None:
        pytest.skip("sqlite3 CLI is not installed")
    live = tmp_path / "live.db"
    _create_live_db(live)
    writer: sqlite3.Connection | None = sqlite3.connect(live)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "UPDATE items SET fetched_at='2026-02-03T04:05:06Z' WHERE id='i-title'"
        )
        writer.commit()
        if keep_writer_open:
            assert Path(f"{live}-wal").stat().st_size > 0
            assert Path(f"{live}-shm").is_file()
        else:
            writer.close()
            writer = None
            assert not Path(f"{live}-wal").exists()
            assert not Path(f"{live}-shm").exists()
        env = _producer_env(tmp_path, live)

        completed = _run_producer(env)

        assert completed.returncode == 0, completed.stderr + completed.stdout
        snapshot = Path(env["AI_RADAR_SYNC_SNAPSHOT"])
        with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as connection:
            assert connection.execute(
                "SELECT fetched_at FROM items WHERE id='i-title'"
            ).fetchone() == ("2026-02-03T04:05:06Z",)
        sqlite_calls = [
            line
            for line in Path(env["FAKE_CALL_LOG"]).read_text(encoding="utf-8").splitlines()
            if line.startswith("SQLITE3")
        ]
        assert sqlite_calls == []
    finally:
        if writer is not None:
            writer.close()
