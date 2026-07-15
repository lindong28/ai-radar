from __future__ import annotations

import importlib.util
import inspect
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from airadar.presentation import related
from airadar.web.routes import curated, curated_archive, curated_digest


@contextmanager
def _connection_context(conn: sqlite3.Connection):
    yield conn


def _run() -> dict[str, str]:
    return {
        "id": "run-1",
        "created_at": "2026-07-14T00:00:00Z",
        "ruleset_version": "test.r1",
    }


def test_curated_helpers_live_in_mode_modules() -> None:
    assert curated_archive._compute_archive_page.__module__ == curated_archive.__name__
    assert curated_archive._compute_archive_for_date.__module__ == curated_archive.__name__
    assert curated_archive.prewarm_curated_archive_total_cache.__module__ == curated_archive.__name__
    assert curated_digest._load_precomputed.__module__ == curated_digest.__name__
    assert curated_digest._compute_items.__module__ == curated_digest.__name__
    assert related._batch_related_discussions.__module__ == related.__name__
    assert not hasattr(curated, "_compute_archive_page")
    assert not hasattr(curated, "_load_precomputed")


def test_curated_router_stays_a_small_explicit_dispatcher() -> None:
    assert len(inspect.getsource(curated).splitlines()) <= 200
    assert importlib.util.find_spec("airadar.web.routes." + "common") is None


def test_batch_related_discussions_queries_each_direction_once() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT, kind TEXT);
        CREATE TABLE items (
          id TEXT PRIMARY KEY, source_id TEXT, url TEXT, author TEXT,
          content_text TEXT, published_at TEXT, fetched_at TEXT
        );
        CREATE TABLE items_fts (item_id TEXT, content_text TEXT);
        INSERT INTO sources VALUES ('s', 'Source', 'feed');
        INSERT INTO items VALUES
          ('current-1', 's', 'https://example.com/current-1', 'Ada',
           'See https://example.com/linked', '2026-07-14T04:00:00Z', '2026-07-14T04:00:00Z'),
          ('current-2', 's', 'https://example.com/current-2', 'Ben',
           'No outgoing link', '2026-07-14T03:00:00Z', '2026-07-14T03:00:00Z'),
          ('linked', 's', 'https://example.com/linked', 'Lin',
           'Linked discussion', '2026-07-14T02:00:00Z', '2026-07-14T02:00:00Z'),
          ('reverse', 's', 'https://example.com/reverse', 'Rin',
           'Reply to https://example.com/current-2', '2026-07-14T01:00:00Z', '2026-07-14T01:00:00Z');
        INSERT INTO items_fts VALUES
          ('current-1', 'See https://example.com/linked'),
          ('current-2', 'No outgoing link'),
          ('linked', 'Linked discussion'),
          ('reverse', 'Reply to https://example.com/current-2');
        """
    )
    rows = conn.execute("SELECT * FROM items WHERE id LIKE 'current-%' ORDER BY id").fetchall()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    result = related._batch_related_discussions(conn, rows)

    assert [item["url"] for item in result["current-1"]] == ["https://example.com/linked"]
    assert [item["url"] for item in result["current-2"]] == ["https://example.com/reverse"]
    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    assert sum("FROM items i" in statement and "items_fts" not in statement for statement in selects) == 1
    assert sum("FROM items_fts f" in statement for statement in selects) == 1
    conn.close()


def test_run_id_mode_delegates_to_digest(monkeypatch) -> None:  # noqa: ANN001
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE curation_runs (id TEXT, created_at TEXT, ruleset_version TEXT)"
    )
    run = _run()
    conn.execute(
        "INSERT INTO curation_runs VALUES (?, ?, ?)",
        (run["id"], run["created_at"], run["ruleset_version"]),
    )
    monkeypatch.setattr(curated, "conn_from_request", lambda _request: _connection_context(conn))
    calls: list[tuple[Any, ...]] = []

    def fake_digest(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append((*args, kwargs))
        return [{"id": "digest-item"}]

    monkeypatch.setattr(curated_digest, "compute_digest_items", fake_digest)
    monkeypatch.setattr(
        curated_archive,
        "_compute_archive_for_date",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("archive date must not run")),
    )
    monkeypatch.setattr(
        curated_archive,
        "_compute_archive_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("archive page must not run")),
    )

    response = curated.curated(SimpleNamespace(), run_id="run-1", q="needle")  # type: ignore[arg-type]

    assert response["data"]["items"] == [{"id": "digest-item"}]
    assert len(calls) == 1
    assert calls[0][-1]["q"] == "needle"
    conn.close()


def test_date_mode_delegates_to_cross_run_archive(monkeypatch) -> None:  # noqa: ANN001
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(curated, "conn_from_request", lambda _request: _connection_context(conn))
    monkeypatch.setattr(curated_archive, "_latest_run", lambda _conn: _run())
    calls: list[dict[str, Any]] = []

    def fake_archive_date(*_args, **kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)
        return [{"id": "date-item"}]

    monkeypatch.setattr(curated_archive, "_compute_archive_for_date", fake_archive_date)
    monkeypatch.setattr(
        curated_digest,
        "compute_digest_items",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("digest must not run")),
    )

    response = curated.curated(SimpleNamespace(), date="2026-07-01", category="paper")  # type: ignore[arg-type]

    assert response["data"]["items"] == [{"id": "date-item"}]
    assert calls == [
        {
            "selected_date": "2026-07-01",
            "normalized_category": "paper",
            "q": None,
        }
    ]
    conn.close()


def test_default_mode_delegates_to_archive_page(monkeypatch) -> None:  # noqa: ANN001
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(curated, "conn_from_request", lambda _request: _connection_context(conn))
    monkeypatch.setattr(curated_archive, "_latest_run", lambda _conn: _run())
    monkeypatch.setattr(
        curated_archive,
        "_compute_archive_page",
        lambda *_args, **_kwargs: ([{"id": "archive-item", "published_at": "2026-07-01T00:00:00Z"}], 1, 1),
    )
    monkeypatch.setattr(curated_archive, "_archive_response_date", lambda *_args: "2026-07-01")
    monkeypatch.setattr(
        curated_digest,
        "compute_digest_items",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("digest must not run")),
    )

    response = curated.curated(SimpleNamespace(), page=5, limit=10)  # type: ignore[arg-type]

    assert response["data"] == {
        "run_id": "run-1",
        "ruleset_version": "test.r1",
        "items": [{"id": "archive-item", "published_at": "2026-07-01T00:00:00Z"}],
        "date": "2026-07-01",
        "count": 1,
        "total": 1,
        "page": 1,
        "limit": 10,
    }
    conn.close()
