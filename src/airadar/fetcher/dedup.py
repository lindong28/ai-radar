from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..wechat_text import wechat_identity_title

# Two providers serving the same WeChat article agree on its publish time to
# within a minute (measured: 118 shared articles, max |delta| 58s, median 13s),
# while the closest genuine same-title repost from one account is 3.3 hours
# apart (measured: 26 such pairs across 3272 production items). The window sits
# in that gap, far from both edges.
WECHAT_IDENTITY_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True)
class FetchedItem:
    source_id: str
    url: str
    title: str
    author: str | None
    published_at: str
    fetched_at: str
    content_text: str
    content_html: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_content(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def content_hash(text: str) -> str:
    return hashlib.sha1(normalize_content(text).encode("utf-8")).hexdigest()[:16]


def _item_id(item: FetchedItem) -> str:
    base = f"{item.source_id}\n{item.url.strip().lower()}\n{normalize_content(item.title)}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _normalized_url(value: str) -> str:
    return value.strip().rstrip("/").lower()


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def wechat_duplicate_id(conn: sqlite3.Connection, item: FetchedItem) -> str | None:
    """Return the id of an already-stored WeChat item that is this same article.

    Running two WeChat feeds side by side means the same article arrives twice
    under unrelated URLs and with different bodies, so neither the URL nor the
    content hash above can see that they are one article. Account plus title
    inside a short publish-time window can. Returns None when this article is
    new to every WeChat source, which is what makes the dual-run a union rather
    than a duplication.
    """
    account = (item.author or "").strip()
    published = _parse_utc(item.published_at)
    wanted = wechat_identity_title(item.title)
    if not account or published is None or not wanted:
        return None
    rows = conn.execute(
        """
        SELECT i.id, i.title
        FROM items i
        JOIN sources s ON s.id = i.source_id
        WHERE COALESCE(s.kind, 'feed')='wechat'
          AND i.author = ?
          AND i.published_at BETWEEN ? AND ?
        """,
        (
            account,
            _iso_z(published - WECHAT_IDENTITY_WINDOW),
            _iso_z(published + WECHAT_IDENTITY_WINDOW),
        ),
    ).fetchall()
    for row in rows:
        if wechat_identity_title(row[1]) == wanted:
            return str(row[0])
    return None


def upsert_item(conn: sqlite3.Connection, item: FetchedItem, *, wechat: bool = False) -> bool:
    x_post_id = str(item.extra.get("x_post_id") or "")
    identity_text = f"{item.content_text}\n{_normalized_url(item.url)}"
    if x_post_id:
        identity_text = f"{identity_text}\n{x_post_id}"
    item_content_hash = content_hash(identity_text)
    extra_json = json.dumps(item.extra, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    existing = conn.execute(
        "SELECT id FROM items WHERE source_id=? AND content_hash=?",
        (item.source_id, item_content_hash),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE items
            SET fetched_at=?, extra_json=?
            WHERE id=?
            """,
            (
                item.fetched_at,
                extra_json,
                existing[0],
            ),
        )
        return False
    existing_url = conn.execute(
        "SELECT id FROM items WHERE source_id=? AND lower(rtrim(url, '/'))=?",
        (item.source_id, _normalized_url(item.url)),
    ).fetchone()
    if existing_url:
        try:
            conn.execute(
                """
                UPDATE items
                SET url=?, title=?, author=?, published_at=?, fetched_at=?,
                    content_text=?, content_html=?, content_hash=?, extra_json=?
                WHERE id=?
                """,
                (
                    item.url,
                    item.title,
                    item.author,
                    item.published_at,
                    item.fetched_at,
                    item.content_text,
                    item.content_html,
                    item_content_hash,
                    extra_json,
                    existing_url[0],
                ),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                """
                UPDATE items
                SET fetched_at=?, extra_json=?
                WHERE id=?
                """,
                (item.fetched_at, extra_json, existing_url[0]),
            )
        return False
    if wechat and wechat_duplicate_id(conn, item) is not None:
        return False
    try:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _item_id(item),
                item.source_id,
                item.url,
                item.title,
                item.author,
                item.published_at,
                item.fetched_at,
                item.content_text,
                item.content_html,
                item_content_hash,
                extra_json,
            ),
        )
    except sqlite3.IntegrityError:
        return False
    return True
