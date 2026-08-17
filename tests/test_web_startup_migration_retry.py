"""Startup-migration retry on a locked database (ADR-053).

The fault-injection test builds a genuine historical schema (migrations
001..K applied and persisted, sentinel business data written), lets an
independent connection take the write lock, and proves that the next real
migration SQL fails with "database is locked" — then runs the real startup
helper while the lock is still held, releases the lock mid-window from
another thread, and checks the retried migrate converges to the same
semantic state as a single uninterrupted migrate from the same history.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from airadar import db
from airadar.web import app as web_app

# Applying 001..K and stopping models a real historical checkout that only
# shipped the first K migrations; every statement inside them has been
# persisted (DDL commits implicitly), so the later interrupted migrate
# resumes over partially-applied durable state, not over a staged rollback.
HISTORY_MIGRATIONS = 5


def _apply_history(db_path: Path) -> None:
    conn = db.get_conn(db_path)
    try:
        for migration in sorted(db.MIGRATIONS_DIR.glob("*.sql"))[:HISTORY_MIGRATIONS]:
            db._execute_migration_idempotent(conn, migration.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO sources (id, name, url, tier, synced_at)"
            " VALUES ('s1', 'sentinel source', 'https://example.invalid/feed', 'T1', '2026-08-17T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO items (id, source_id, url, title, published_at, fetched_at,"
            " content_text, content_hash)"
            " VALUES ('i1', 's1', 'https://example.invalid/a', 'sentinel item',"
            " '2026-08-17T00:00:00Z', '2026-08-17T00:00:00Z', 'sentinel body', 'hash-1')"
        )
        conn.commit()
    finally:
        conn.close()


def _semantic_state(db_path: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        schema = {
            (row[0], row[1], row[2], db._normalize_ddl(row[3]))
            for row in conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
            # SQLite generates FTS5 shadow-table DDL itself; its exact shape is
            # an implementation detail, membership is what the oracle needs.
            if not row[1].startswith("items_fts_")
        }
        shadow_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'items_fts_%'"
            )
        }
        migration_ids = {
            row[0] for row in conn.execute("SELECT id FROM airadar_migrations")
        }
        sentinel = conn.execute(
            "SELECT i.id, i.title, i.content_text, s.name FROM items i JOIN sources s ON s.id=i.source_id"
        ).fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {
        "schema": schema,
        "shadow_tables": shadow_tables,
        "migration_ids": migration_ids,
        "sentinel": sentinel,
        "integrity": integrity,
    }


def _fts_integrity_ok(db_path: Path) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()
    return True


def test_interrupted_migrate_recovers_to_single_run_state(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    direct_path = tmp_path / "direct.db"
    _apply_history(direct_path)
    db.migrate(direct_path)
    oracle = _semantic_state(direct_path)

    injected_path = tmp_path / "injected.db"
    _apply_history(injected_path)

    # check_same_thread=False: the release timer thread must be allowed to
    # end this connection's transaction while the main thread waits inside
    # the retry helper.
    blocker = sqlite3.connect(injected_path, timeout=0.1, check_same_thread=False)
    release_timer: threading.Timer | None = None
    try:
        blocker.execute("PRAGMA journal_mode=WAL")
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute(
            "UPDATE items SET title='sentinel item' WHERE id='i1'"
        )
        # Positive control for the injection: the next real migration write
        # must actually observe the external lock (this attempt burns the
        # connection's 5s busy_timeout, proving the lock is real).
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            db.migrate(injected_path)

        # Now run the REAL startup helper while the lock is still held, and
        # release it mid-window from another thread: the helper must observe
        # locked on its first attempt, sleep, retry, and then succeed — the
        # exact locked -> backoff -> unlocked -> rerun path ADR-053 promises.
        def _release() -> None:
            blocker.rollback()

        release_timer = threading.Timer(6.5, _release)
        release_timer.start()
        with caplog.at_level("WARNING", logger=web_app.__name__):
            web_app._migrate_with_retry(injected_path)
        # The helper must actually have taken the retry branch: the release
        # (6.5s) lands beyond the first attempt's 5s busy_timeout, so at
        # least one locked -> warning -> backoff round must be on record.
        assert any(
            record.name == web_app.__name__
            and record.getMessage().startswith("startup migration hit a locked database")
            for record in caplog.records
        ), "helper succeeded without ever entering the retry branch"
    finally:
        if release_timer is not None:
            release_timer.cancel()
        try:
            blocker.rollback()
        except sqlite3.ProgrammingError:
            pass
        blocker.close()

    assert _semantic_state(injected_path) == oracle
    assert _fts_integrity_ok(injected_path)
    assert _fts_integrity_ok(direct_path)


def test_startup_retry_retries_locked_then_succeeds_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[float] = []
    real_migrate = db.migrate
    db_path = tmp_path / "retry.db"

    def flaky_migrate(path: object) -> None:
        calls.append(0.0)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        real_migrate(path)

    monkeypatch.setattr(web_app.db, "migrate", flaky_migrate)
    monkeypatch.setattr(web_app.time, "sleep", lambda seconds: calls.__setitem__(-1, seconds))

    with caplog.at_level("WARNING", logger=web_app.__name__):
        web_app._migrate_with_retry(db_path)

    assert len(calls) == 3
    # Exponential backoff from 0.5s.
    assert calls[0] == 0.5
    assert calls[1] == 1.0
    retry_logs = [record for record in caplog.records if "locked" in record.getMessage()]
    assert len(retry_logs) == 2
    assert "window" in retry_logs[0].getMessage()


def test_startup_retry_reraises_original_error_after_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raised: list[sqlite3.OperationalError] = []
    sleeps: list[float] = []

    def always_locked(path: object) -> None:
        error = sqlite3.OperationalError("database is locked")
        raised.append(error)
        raise error

    fake_now = [0.0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_now[0] += seconds

    monkeypatch.setattr(web_app.db, "migrate", always_locked)
    monkeypatch.setattr(web_app.time, "monotonic", lambda: fake_now[0])
    monkeypatch.setattr(web_app.time, "sleep", fake_sleep)

    with pytest.raises(sqlite3.OperationalError, match="database is locked") as excinfo:
        web_app._migrate_with_retry(None)

    # The propagated exception is the ORIGINAL last-raised object, not a
    # re-created lookalike. (Object identity only; traceback frame shape is
    # deliberately not asserted.)
    assert excinfo.value is raised[-1]
    assert len(raised) > 1
    # Backoff doubles from 0.5s and is capped at 5s.
    assert sleeps[0] == 0.5
    assert max(sleeps) == 5.0
    assert all(second <= 5.0 for second in sleeps)
    # The loop stopped because the window was exhausted, not because of a cap
    # on attempts: the total simulated wait stayed within the window.
    assert fake_now[0] <= web_app.MIGRATE_RETRY_WINDOW_SECONDS


def test_startup_retry_does_not_swallow_other_operational_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    def broken_migrate(path: object) -> None:
        attempts.append(1)
        raise sqlite3.OperationalError("no such table: items")

    monkeypatch.setattr(web_app.db, "migrate", broken_migrate)

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        web_app._migrate_with_retry(None)

    assert attempts == [1]
