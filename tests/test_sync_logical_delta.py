"""Producer-side base-only shipping replica behavior."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "deploy" / "sync" / "logical_delta.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_logical_delta", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ld = _load_module()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_snapshot(path: Path, *, padded: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA page_size=4096;
            CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content_text TEXT NOT NULL,
                author TEXT,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                payload BLOB NOT NULL
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
            CREATE TRIGGER items_ai_fts AFTER INSERT ON items BEGIN
              INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
              VALUES (
                new.id,
                new.title,
                new.content_text,
                COALESCE((SELECT name FROM sources WHERE id = new.source_id), ''),
                COALESCE(new.author, ''),
                ''
              );
            END;
            CREATE TRIGGER items_au_fts AFTER UPDATE ON items BEGIN
              UPDATE items_fts
              SET title = new.title,
                  content_text = new.content_text,
                  source_name = COALESCE((SELECT name FROM sources WHERE id = new.source_id), ''),
                  author = COALESCE(new.author, '')
              WHERE item_id = old.id;
            END;
            CREATE TRIGGER items_ad_fts AFTER DELETE ON items BEGIN
              DELETE FROM items_fts WHERE item_id = old.id;
            END;
            """
        )
        connection.execute("INSERT INTO sources VALUES ('s1', 'Source Unique')")
        connection.executemany(
            "INSERT INTO items VALUES (?, 's1', ?, ?, ?, '2026-01-01T00:00:00Z')",
            [
                ("i1", "First title", "First content", "Alice"),
                ("i2", "Second title", "Second content", "Bob"),
            ],
        )
        connection.executemany(
            "INSERT INTO events(item_id, payload) VALUES (?, ?)",
            [("i1", b"one"), ("i2", b"two")],
        )
        if padded:
            connection.executemany(
                "INSERT INTO events(item_id, payload) VALUES (?, ?)",
                [
                    (f"padding-{index}", bytes([index % 251]) * 16_384)
                    for index in range(256)
                ],
            )
        connection.commit()
    finally:
        connection.close()


def _fts_objects(path: Path) -> list[tuple[str, str]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name = 'items_fts' OR name LIKE 'items_fts_%' "
            "OR (type = 'trigger' AND lower(sql) LIKE '%items_fts%') "
            "ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()


def test_bootstrap_then_incremental_update_insert_delete_and_sequence(tmp_path: Path) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    _create_snapshot(live)
    shutil.copyfile(live, snapshot)
    live_before = _sha256(live)

    first = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)

    assert first.mode == "bootstrap"
    assert not _fts_objects(replica)
    assert _sha256(live) == live_before
    ld.reconcile_replica(snapshot=snapshot, replica=replica)

    connection = sqlite3.connect(snapshot)
    try:
        connection.execute("DELETE FROM items WHERE id = 'i1'")
        connection.execute(
            "UPDATE items SET title = 'Second title updated', fetched_at = '2026-01-02T00:00:00Z' "
            "WHERE id = 'i2'"
        )
        connection.execute(
            "INSERT INTO items VALUES "
            "('i3', 's1', 'Third title', 'Third content', 'Carol', '2026-01-02T00:00:00Z')"
        )
        connection.execute("DELETE FROM events WHERE id = 1")
        connection.execute("INSERT INTO events(item_id, payload) VALUES ('i3', x'7468726565')")
        connection.commit()
        expected_sequence = connection.execute(
            "SELECT name, seq FROM sqlite_sequence ORDER BY name"
        ).fetchall()
    finally:
        connection.close()

    second = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)

    assert second.mode == "incremental"
    assert second.operations >= 5
    ld.reconcile_replica(snapshot=snapshot, replica=replica)
    with sqlite3.connect(replica) as connection:
        assert connection.execute("SELECT id, title FROM items ORDER BY id").fetchall() == [
            ("i2", "Second title updated"),
            ("i3", "Third title"),
        ]
        assert connection.execute(
            "SELECT name, seq FROM sqlite_sequence ORDER BY name"
        ).fetchall() == expected_sequence
    assert not _fts_objects(replica)
    assert _sha256(live) == live_before


def test_ordinary_fts_prefixed_table_and_trigger_mention_are_preserved(tmp_path: Path) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    _create_snapshot(snapshot)
    with sqlite3.connect(snapshot) as connection:
        connection.executescript(
            """
            CREATE TABLE items_fts_audit (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            INSERT INTO items_fts_audit VALUES (1, 'before');
            CREATE TRIGGER ordinary_trigger_mentions_fts
            AFTER INSERT ON items_fts_audit BEGIN
              SELECT 'INSERT INTO items_fts is diagnostic text, not executable DML';
              SELECT "replace" "items_fts";
            END;
            """
        )
    shutil.copyfile(snapshot, live)

    first = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)

    assert first.mode == "bootstrap"
    with sqlite3.connect(replica) as connection:
        assert connection.execute("SELECT * FROM items_fts_audit").fetchall() == [
            (1, "before")
        ]
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='ordinary_trigger_mentions_fts'"
        ).fetchone() == (1,)
    ld.reconcile_replica(snapshot=snapshot, replica=replica)

    with sqlite3.connect(snapshot) as connection:
        connection.execute("UPDATE items_fts_audit SET payload='after' WHERE id=1")
    second = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)

    assert second.mode == "incremental"
    with sqlite3.connect(replica) as connection:
        assert connection.execute("SELECT * FROM items_fts_audit").fetchall() == [(1, "after")]
    ld.reconcile_replica(snapshot=snapshot, replica=replica)


def test_dynamically_named_trigger_that_writes_fts_is_not_copied(tmp_path: Path) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    _create_snapshot(snapshot)
    with sqlite3.connect(snapshot) as connection:
        connection.executescript(
            """
            CREATE TABLE comments (
              id TEXT PRIMARY KEY,
              body TEXT NOT NULL
            );
            CREATE TRIGGER comments_ai_fts AFTER INSERT ON comments BEGIN
              INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
              VALUES (new.id, '', new.body, '', '', '');
            END;
            INSERT INTO comments VALUES ('comment-1', 'first comment');
            """
        )
    shutil.copyfile(snapshot, live)

    result = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)

    assert result.mode == "bootstrap"
    with sqlite3.connect(replica) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='comments_ai_fts'"
        ).fetchone() is None
        connection.execute("INSERT INTO comments VALUES ('comment-2', 'second comment')")
        assert connection.execute("SELECT COUNT(*) FROM comments").fetchone() == (2,)
        connection.rollback()
    ld.reconcile_replica(snapshot=snapshot, replica=replica)


def test_duplicate_null_primary_keys_use_safe_table_fallback_without_self_heal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    with sqlite3.connect(snapshot) as connection:
        connection.executescript(
            """
            CREATE TABLE nullable_keys (
              id TEXT PRIMARY KEY,
              group_key TEXT NOT NULL,
              payload TEXT NOT NULL
            );
            INSERT INTO nullable_keys VALUES (NULL, 'k', 'x'), (NULL, 'k', 'y');
            """
        )
    shutil.copyfile(snapshot, live)
    assert ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica).mode == "bootstrap"
    capsys.readouterr()

    unchanged = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)
    unchanged_log = capsys.readouterr().err

    assert unchanged.mode == "incremental"
    assert unchanged.operations == 0
    assert "nullable-primary-key" in unchanged_log
    assert "SELF-HEAL" not in unchanged_log
    with sqlite3.connect(replica) as connection:
        assert connection.execute(
            "SELECT id, group_key, payload FROM nullable_keys ORDER BY payload"
        ).fetchall() == [(None, "k", "x"), (None, "k", "y")]
    ld.reconcile_replica(snapshot=snapshot, replica=replica)

    with sqlite3.connect(snapshot) as connection:
        connection.execute("DELETE FROM nullable_keys WHERE payload='x'")
        connection.execute("INSERT INTO nullable_keys VALUES (NULL, 'k', 'z')")
    changed = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)
    changed_log = capsys.readouterr().err

    assert changed.mode == "incremental"
    assert changed.operations > 0
    assert "nullable-primary-key" in changed_log
    assert "SELF-HEAL" not in changed_log
    with sqlite3.connect(replica) as connection:
        assert connection.execute(
            "SELECT id, group_key, payload FROM nullable_keys ORDER BY payload"
        ).fetchall() == [(None, "k", "y"), (None, "k", "z")]
    ld.reconcile_replica(snapshot=snapshot, replica=replica)


def test_whole_table_fallback_does_not_replay_ordinary_triggers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    with sqlite3.connect(snapshot) as connection:
        connection.executescript(
            """
            CREATE TABLE a_side_effects (id INTEGER PRIMARY KEY, count INTEGER NOT NULL);
            CREATE TABLE z_nullable_keys (
              id TEXT PRIMARY KEY,
              payload TEXT NOT NULL
            );
            INSERT INTO a_side_effects VALUES (1, 0);
            INSERT INTO z_nullable_keys VALUES (NULL, 'x'), (NULL, 'y');
            CREATE TRIGGER z_nullable_keys_ad AFTER DELETE ON z_nullable_keys BEGIN
              UPDATE a_side_effects SET count = count + 1 WHERE id = 1;
            END;
            CREATE TRIGGER z_nullable_keys_ai AFTER INSERT ON z_nullable_keys BEGIN
              UPDATE a_side_effects SET count = count + 1 WHERE id = 1;
            END;
            """
        )
    shutil.copyfile(snapshot, live)
    assert ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica).mode == "bootstrap"
    capsys.readouterr()

    with sqlite3.connect(snapshot) as connection:
        connection.execute("DELETE FROM z_nullable_keys WHERE payload='x'")
        connection.execute("INSERT INTO z_nullable_keys VALUES (NULL, 'z')")
    result = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)
    captured = capsys.readouterr().err

    assert result.mode == "incremental"
    assert "SELF-HEAL" not in captured
    with sqlite3.connect(replica) as connection:
        assert connection.execute("SELECT count FROM a_side_effects WHERE id=1").fetchone() == (2,)
        assert connection.execute(
            "SELECT payload FROM z_nullable_keys ORDER BY payload"
        ).fetchall() == [("y",), ("z",)]
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
            "AND name IN ('z_nullable_keys_ad', 'z_nullable_keys_ai')"
        ).fetchone() == (2,)
    ld.reconcile_replica(snapshot=snapshot, replica=replica)


def test_normal_incremental_apply_does_not_replay_ordinary_triggers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    connection = sqlite3.connect(snapshot)
    try:
        connection.executescript(
            """
            CREATE TABLE a_meta (id INTEGER PRIMARY KEY, generation INTEGER NOT NULL);
            CREATE TABLE z_items (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TRIGGER z_items_au AFTER UPDATE ON z_items BEGIN
              UPDATE a_meta SET generation = generation + 1 WHERE id = 1;
            END;
            INSERT INTO a_meta VALUES (1, 0);
            INSERT INTO z_items VALUES (1, 'before');
            """
        )
        connection.commit()
    finally:
        connection.close()
    shutil.copyfile(snapshot, live)
    assert ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica).mode == "bootstrap"

    connection = sqlite3.connect(snapshot)
    try:
        connection.execute("UPDATE z_items SET value = 'after' WHERE id = 1")
        connection.execute("UPDATE a_meta SET generation = 0 WHERE id = 1")
        connection.commit()
    finally:
        connection.close()

    result = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)
    captured = capsys.readouterr().err

    assert result.mode == "incremental"
    assert "SELF-HEAL" not in captured
    ld.reconcile_replica(snapshot=snapshot, replica=replica)
    with sqlite3.connect(replica) as connection:
        assert connection.execute("SELECT * FROM a_meta").fetchall() == [(1, 0)]
        assert connection.execute("SELECT * FROM z_items").fetchall() == [(1, "after")]
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='z_items_au'"
        ).fetchone() == (1,)


def test_reconciliation_mismatch_self_heals_with_fresh_base_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    with sqlite3.connect(snapshot) as connection:
        connection.executescript(
            """
            CREATE TABLE values_table (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO values_table VALUES (1, 'before');
            """
        )
    shutil.copyfile(snapshot, live)
    assert ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica).mode == "bootstrap"
    with sqlite3.connect(snapshot) as connection:
        connection.execute("UPDATE values_table SET value='after' WHERE id=1")

    original_apply = ld._apply_delta

    def corrupt_after_apply(snapshot_path: Path, replica_path: Path) -> int:
        operations = original_apply(snapshot_path, replica_path)
        with sqlite3.connect(replica_path) as connection:
            connection.execute("UPDATE values_table SET value='corrupt' WHERE id=1")
        return operations

    monkeypatch.setattr(ld, "_apply_delta", corrupt_after_apply)
    result = ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica)

    assert result.mode == "self-heal"
    assert "reconciliation" in result.reason.lower()
    with sqlite3.connect(replica) as connection:
        assert connection.execute("SELECT * FROM values_table").fetchall() == [(1, "after")]


def test_live_database_path_and_inode_aliases_are_refused_before_writes(tmp_path: Path) -> None:
    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    manifest = tmp_path / "shipping.manifest.json"
    _create_snapshot(live)
    shutil.copyfile(live, snapshot)

    with pytest.raises(ld.SafetyError, match="path"):
        ld.assert_safe_artifacts(
            live_db=live,
            snapshot=live,
            replica=replica,
            manifest=manifest,
        )

    os.link(live, replica)
    live_before = _sha256(live)
    with pytest.raises(ld.SafetyError, match="inode"):
        ld.assert_safe_artifacts(
            live_db=live,
            snapshot=snapshot,
            replica=replica,
            manifest=manifest,
        )
    assert _sha256(live) == live_before


def _gnu_rsync() -> str | None:
    for candidate in ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"):
        if Path(candidate).is_file():
            first_line = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, check=True
            ).stdout.splitlines()[0]
            if first_line.startswith("rsync  version"):
                return candidate
    return None


def test_fixture_bootstrap_then_incremental_rsync_delta_is_small(tmp_path: Path) -> None:
    rsync = _gnu_rsync()
    if rsync is None:
        pytest.skip("GNU rsync is not installed")

    live = tmp_path / "live.db"
    snapshot = tmp_path / "snapshot.db"
    replica = tmp_path / "shipping.db"
    destination = tmp_path / "destination" / "radar.db.upload"
    destination.parent.mkdir()
    _create_snapshot(live, padded=True)
    shutil.copyfile(live, snapshot)
    assert ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica).mode == "bootstrap"
    shutil.copyfile(replica, destination)
    # Production rounds are hours apart. Force that same quick-check condition
    # in a sub-second fixture so rsync exercises its delta path instead of
    # treating equal size+mtime as unchanged.
    os.utime(destination, (1, 1))

    connection = sqlite3.connect(snapshot)
    try:
        connection.execute(
            "UPDATE items SET fetched_at = '2026-01-02T00:00:00Z' WHERE id = 'i2'"
        )
        connection.execute("INSERT INTO events(item_id, payload) VALUES ('i2', x'6e6577')")
        connection.commit()
    finally:
        connection.close()
    assert ld.sync_replica(live_db=live, snapshot=snapshot, replica=replica).mode == "incremental"

    completed = subprocess.run(
        [
            rsync,
            "--archive",
            "--no-whole-file",
            "--block-size=4096",
            "--stats",
            str(replica),
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"Total bytes sent:\s+([0-9,]+)", completed.stdout)
    assert match, completed.stdout
    sent_bytes = int(match.group(1).replace(",", ""))
    print(
        f"fixture_rsync_sent_bytes={sent_bytes} "
        f"artifact_bytes={replica.stat().st_size}"
    )

    assert sent_bytes < 512_000
    assert sent_bytes < replica.stat().st_size // 2
    assert _sha256(destination) == _sha256(replica)
