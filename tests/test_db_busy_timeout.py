from __future__ import annotations

import pytest

from airadar import db


def test_busy_timeout_defaults_and_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_RADAR_SQLITE_BUSY_TIMEOUT_MS", raising=False)
    assert db._busy_timeout_ms() == db.DEFAULT_BUSY_TIMEOUT_MS
    monkeypatch.setenv("AI_RADAR_SQLITE_BUSY_TIMEOUT_MS", "120000")
    assert db._busy_timeout_ms() == 120000


@pytest.mark.parametrize("value", ["", "abc", "0", "-1"])
def test_an_unusable_value_falls_back_rather_than_disabling_the_wait(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    # "0" matters most: SQLite reads it as "do not wait at all", so a typo would turn every
    # contended write into an immediate failure -- the opposite of what the setting is for.
    monkeypatch.setenv("AI_RADAR_SQLITE_BUSY_TIMEOUT_MS", value)
    assert db._busy_timeout_ms() == db.DEFAULT_BUSY_TIMEOUT_MS


def test_the_pragma_actually_carries_the_value(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("AI_RADAR_SQLITE_BUSY_TIMEOUT_MS", "45000")
    conn = db.get_conn(tmp_path / "t.db")
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 45000
    finally:
        conn.close()


def test_the_timeout_survives_running_the_migrations(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Migrations must not quietly reset it.

    001_init.sql set `PRAGMA busy_timeout=5000` and re-runs on every CLI invocation, so a batch
    job that raised the timeout got it for one statement and then lost it. The pragma read back
    correctly before the migrations and wrongly after, which is why nothing caught it: every
    check anyone would think to write happens on a fresh connection.
    """
    monkeypatch.setenv("AI_RADAR_SQLITE_BUSY_TIMEOUT_MS", "90000")
    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    try:
        # Run the migrations and read the pragma back WITHOUT re-asserting it. Re-asserting here
        # first -- which the first draft of this test did -- makes it pass whether or not a
        # migration reset anything, because the assertion then only checks the line above it.
        db._apply_pending_migrations(conn)
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 90000
    finally:
        conn.close()

    # And migrate() leaves a caller's connection with the value it asked for.
    with db.get_conn(db_path) as after:
        db.migrate(db_path)
        assert after.execute("PRAGMA busy_timeout").fetchone()[0] == 90000
