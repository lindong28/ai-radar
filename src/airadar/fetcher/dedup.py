from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any


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


def upsert_item(conn: sqlite3.Connection, item: FetchedItem) -> bool:
    item_content_hash = content_hash(item.content_text)
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
