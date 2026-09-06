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
