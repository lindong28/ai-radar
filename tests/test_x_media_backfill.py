from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from airadar import db
from airadar.admin import x_media_backfill
from airadar.admin.x_media_backfill import backfill_x_media, candidate_rows


def _conn(tmp_path) -> sqlite3.Connection:  # noqa: ANN001
    db_path = str(tmp_path / "t.db")
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,kind,homepage_url,icon_url,synced_at)"
        " VALUES ('x_s','X','https://x.com/s','T1.5',1,'x','https://x.com/s','',datetime('now'))"
    )
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,kind,homepage_url,icon_url,synced_at)"
        " VALUES ('rss_s','R','https://r.example','T1.5',1,'feed','https://r.example','',datetime('now'))"
    )
    return conn


def _item(conn, item_id: str, source: str, extra: dict | None) -> None:  # noqa: ANN001
    conn.execute(
        "INSERT INTO items (id,source_id,url,title,author,published_at,fetched_at,content_text,"
        "content_html,content_hash,extra_json) VALUES (?,?,?,?,?,?,?,?,NULL,?,?)",
        (item_id, source, f"https://x.com/{item_id}", "t", "@a", "2026-08-18T00:00:00Z",
         "2026-08-18T00:01:00Z", "text", item_id, json.dumps(extra if extra is not None else {})),
    )


def test_candidates_are_x_items_missing_media_only(tmp_path) -> None:  # noqa: ANN001
    conn = _conn(tmp_path)
    _item(conn, "a", "x_s", {"x_post_id": "1"})                       # candidate
    _item(conn, "b", "x_s", {"x_post_id": "2", "x_media": []})        # already done (empty is a result)
    _item(conn, "c", "x_s", {"x_post_id": "3", "x_media": [{"url": "u"}]})  # already done
    _item(conn, "d", "x_s", {})                                       # extra with no post id
    _item(conn, "e", "rss_s", {"x_post_id": "9"})                     # not an X source
    conn.commit()
    assert [r["id"] for r in candidate_rows(conn)] == ["a"]
    conn.close()


def test_backfill_writes_media_and_reconciles(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    conn = _conn(tmp_path)
    _item(conn, "a", "x_s", {"x_post_id": "1"})
    _item(conn, "b", "x_s", {"x_post_id": "2"})
    _item(conn, "c", "x_s", {"x_post_id": "3"})  # will not come back
    conn.commit()
    monkeypatch.setattr("airadar.admin.x_media_backfill.read_value", lambda _k: "token")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "data": [
                {"id": "1", "attachments": {"media_keys": ["3_a"]}},
                {"id": "2"},  # returned but carries no media
            ],
            "includes": {"media": [
                {"media_key": "3_a", "type": "photo", "url": "https://pbs.twimg.com/media/a.jpg"},
            ]},
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    receipt = backfill_x_media(conn, client=client).as_dict()

    assert receipt["candidates"] == 3
    assert receipt["requested"] == 3
    assert receipt["returned"] == 2
    assert receipt["with_media"] == 1
    assert receipt["updated"] == 2
    assert receipt["unresolved_count"] == 1 and receipt["unresolved_sample"] == ["3"]
    # requested == returned + unresolved: a silent no-op cannot pass this.
    assert receipt["reconciled"] is True
    assert "attachments.media_keys" in captured["params"]["expansions"]

    rows = {r["id"]: json.loads(r["extra_json"]) for r in conn.execute("SELECT id, extra_json FROM items")}
    assert rows["a"]["x_media"][0]["url"] == "https://pbs.twimg.com/media/a.jpg"
    assert rows["b"]["x_media"] == []          # recorded as "looked up, has none"
    assert "x_media" not in rows["c"]          # left for a later run
    # Re-running skips what is already resolved.
    assert [r["id"] for r in candidate_rows(conn)] == ["c"]
    conn.close()


def test_backfill_never_touches_source_checkpoints(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """The incremental fetcher must behave as if the backfill had not run."""
    conn = _conn(tmp_path)
    conn.execute("UPDATE sources SET meta_json = ? WHERE id='x_s'",
                 (json.dumps({"x_since_id": "999", "x_cursor_state": "checkpointed"}),))
    _item(conn, "a", "x_s", {"x_post_id": "1"})
    conn.commit()
    before = conn.execute("SELECT meta_json FROM sources WHERE id='x_s'").fetchone()[0]
    monkeypatch.setattr("airadar.admin.x_media_backfill.read_value", lambda _k: "token")

    client = httpx.Client(transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"data": [{"id": "1"}]})))
    backfill_x_media(conn, client=client)

    assert conn.execute("SELECT meta_json FROM sources WHERE id='x_s'").fetchone()[0] == before
    conn.close()


def test_dry_run_issues_no_request_and_still_reports_scale(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    conn = _conn(tmp_path)
    for i in range(5):
        _item(conn, f"i{i}", "x_s", {"x_post_id": str(i)})
    conn.commit()
    monkeypatch.setattr("airadar.admin.x_media_backfill.read_value", lambda _k: "token")

    def _boom(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("dry run must not issue a request")

    receipt = backfill_x_media(conn, dry_run=True,
                               client=httpx.Client(transport=httpx.MockTransport(_boom))).as_dict()
    # The caller learns the cost before paying it: X bills per returned post.
    assert receipt["candidates"] == 5
    assert receipt["requested"] == 0
    conn.close()


class _LockingConn:
    """Raises `database is locked` for the first `fail_times` write attempts."""

    def __init__(self, inner: sqlite3.Connection, fail_times: int, exc: Exception | None = None) -> None:
        self._inner, self._left = inner, fail_times
        self._exc = exc or sqlite3.OperationalError("database is locked")
        self.attempts = 0

    def executemany(self, *a):  # noqa: ANN002, ANN201
        self.attempts += 1
        if self._left > 0:
            self._left -= 1
            raise self._exc
        return self._inner.executemany(*a)

    def __getattr__(self, name):  # noqa: ANN001, ANN204
        return getattr(self._inner, name)


def test_locked_write_is_retried_rather_than_dropping_a_paid_batch(tmp_path) -> None:  # noqa: ANN001
    """The pipeline holds write transactions longer than busy_timeout=5000.

    By the time we write, X has already billed for these posts, so losing the
    write means paying twice for the same rows. Observed for real: two runs of
    --limit 100 both died here, ~200 posts billed and nothing persisted.
    """
    conn = _conn(tmp_path)
    _item(conn, "a", "x_s", {"x_post_id": "1"})
    conn.commit()
    locking = _LockingConn(conn, fail_times=3)
    slept: list[float] = []

    x_media_backfill._persist(locking, [("{}", "a")], sleep=slept.append, monotonic=lambda: 0.0)

    assert locking.attempts == 4          # 3 refusals then the real write
    assert len(slept) == 3 and slept == sorted(slept)  # backs off rather than spinning
    conn.close()


def test_non_lock_write_errors_are_not_retried(tmp_path) -> None:  # noqa: ANN001
    """A schema/disk fault must surface now, not after a 90-second stall."""
    conn = _conn(tmp_path)
    locking = _LockingConn(conn, fail_times=1, exc=sqlite3.OperationalError("no such column: extra_json"))
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        x_media_backfill._persist(locking, [("{}", "a")], sleep=lambda _s: None, monotonic=lambda: 0.0)
    assert locking.attempts == 1
    conn.close()


def test_receipt_does_not_claim_rows_a_failed_write_never_landed(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """`updated` must mean "in the database", or it hides a billed-but-lost batch."""
    conn = _conn(tmp_path)
    _item(conn, "a", "x_s", {"x_post_id": "1"})
    conn.commit()
    monkeypatch.setattr("airadar.admin.x_media_backfill.read_value", lambda _k: "token")
    monkeypatch.setattr("airadar.admin.x_media_backfill._WRITE_RETRY_BUDGET_SECONDS", 0.0)

    client = httpx.Client(transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"data": [{"id": "1"}]})))
    with pytest.raises(sqlite3.OperationalError):
        backfill_x_media(_LockingConn(conn, fail_times=1), client=client)

    assert "x_media" not in json.loads(
        conn.execute("SELECT extra_json FROM items WHERE id='a'").fetchone()[0]
    )
    conn.close()


def test_missing_bearer_token_fails_loudly(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    conn = _conn(tmp_path)
    _item(conn, "a", "x_s", {"x_post_id": "1"})
    conn.commit()
    monkeypatch.setattr("airadar.admin.x_media_backfill.read_value", lambda _k: "")
    with pytest.raises(RuntimeError, match="X_BEARER_TOKEN"):
        backfill_x_media(conn)
    conn.close()


def test_non_positive_limit_is_refused_rather_than_silently_uncapping(tmp_path) -> None:  # noqa: ANN001
    """SQLite reads `LIMIT -1` as no limit, so a typo would uncap a paid run."""
    conn = _conn(tmp_path)
    for i in range(5):
        _item(conn, f"i{i}", "x_s", {"x_post_id": str(i)})
    conn.commit()
    assert len(candidate_rows(conn, limit=2)) == 2
    for bad in (-1, 0):
        with pytest.raises(ValueError, match="limit must be >= 1"):
            candidate_rows(conn, limit=bad)
    conn.close()
