from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import conftest as fixture_module
import pytest
from conftest import _prepare_session_db, _serve_environment, base_url, historical_date

from airadar import db


def test_playwright_session_db_is_a_migrated_snapshot_and_serve_uses_it(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    source = tmp_path / "production-sentinel.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('do-not-touch')")
    before = (source.stat().st_mtime_ns, source.stat().st_size, source.read_bytes())

    session_db = tmp_path / "playwright-session.db"
    _prepare_session_db(source, session_db)

    after = (source.stat().st_mtime_ns, source.stat().st_size, source.read_bytes())
    assert after == before
    assert session_db != source
    with sqlite3.connect(session_db) as connection:
        assert connection.execute("SELECT value FROM sentinel").fetchone() == ("do-not-touch",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_cache_generations'"
        ).fetchone() == (1,)

    monkeypatch.setenv("AI_RADAR_DB", str(source))
    environment = _serve_environment(session_db)
    assert environment["AI_RADAR_DB"] == str(session_db.resolve())
    assert environment["AI_RADAR_DB"] != os.environ["AI_RADAR_DB"]
    assert db.resolve_db_path(environment["AI_RADAR_DB"]) == session_db.resolve()

    captured: dict[str, object] = {}

    class FakeProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            captured["terminated"] = True

        def wait(self, timeout: float) -> int:
            captured["wait_timeout"] = timeout
            return 0

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        captured["command"] = command
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(fixture_module, "_free_port", lambda: 43210)
    monkeypatch.setattr(fixture_module, "_wait_for_health", lambda url, process: None)
    monkeypatch.setattr(fixture_module.subprocess, "Popen", fake_popen)
    fixture = base_url.__wrapped__(session_db)
    assert next(fixture) == "http://127.0.0.1:43210"
    with pytest.raises(StopIteration):
        next(fixture)
    serve_env = captured["env"]
    assert isinstance(serve_env, dict)
    assert serve_env["AI_RADAR_DB"] == str(session_db.resolve())
    command = captured["command"]
    assert isinstance(command, list)
    assert "--pre-migrated-db" in command
    assert captured["terminated"] is True

    with sqlite3.connect(session_db) as connection:
        connection.execute(
            "INSERT INTO sources (id,name,url,tier,synced_at) "
            "VALUES ('s','S','https://s.invalid','T1','2026-07-18')"
        )
        connection.execute(
            """
            INSERT INTO items (
              id,source_id,url,title,published_at,fetched_at,content_text,content_hash
            ) VALUES ('i','s','https://i.invalid','I','2026-07-17T20:00:00Z',
                      '2026-07-17T20:00:00Z','body','hash')
            """
        )
        connection.execute(
            """
            INSERT INTO curation_runs (
              id,ruleset_version,weights_json,threshold,input_eval_ids,output_curated_ids,created_at
            ) VALUES ('r','r1','{}',0,'[]','[\"i\"]','2026-07-18T00:00:00Z')
            """
        )
        connection.execute("INSERT INTO curated_items VALUES ('r','i',1,1,'{}',NULL)")
    assert historical_date.__wrapped__(session_db) == "2026-07-18"
