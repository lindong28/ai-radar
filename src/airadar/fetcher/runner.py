from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db
from ..sources.loader import SourceConfig, load_sources
from ..sources.sync import load_enabled_sources_from_db, sync_to_db
from .dedup import FetchedItem, _normalized_url, upsert_item
from .http_client import fetch_feed
from .rss import parse_feed
from .wechat import normalize_wechat_avatar_url, scrape_article

logger = logging.getLogger(__name__)
WECHAT_AVATAR_NEGATIVE_CACHE_TTL = timedelta(days=2)
WECHAT_AVATAR_BACKFILL_LIMIT = 1


@dataclass(frozen=True)
class SourceFetchSummary:
    source_id: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None


@dataclass(frozen=True)
class FetchSummary:
    sources: list[SourceFetchSummary] = field(default_factory=list)

    @property
    def inserted(self) -> int:
        return sum(source.inserted for source in self.sources)

    @property
    def attempted(self) -> int:
        return len(self.sources)

    @property
    def failed(self) -> int:
        return sum(1 for source in self.sources if source.error)


def default_sources_path() -> Path:
    return db.PROJECT_ROOT / "data" / "sources.toml"


def reload_sources(conn: sqlite3.Connection, path: Path | None = None) -> list[SourceConfig]:
    sources = load_sources(path or default_sources_path())
    sync_to_db(sources, conn)
    return sources


def fetch_source(conn: sqlite3.Connection, source: SourceConfig) -> SourceFetchSummary:
    try:
        response = fetch_feed(source, conn)
        if response.not_modified:
            if source.kind == "wechat":
                _backfill_wechat_avatar_cache(conn, source.slug)
                conn.commit()
            return SourceFetchSummary(source_id=source.slug)
        items = parse_feed(source, response.body)
        if source.kind == "wechat":
            items = _enrich_wechat_bodies(conn, items)
        inserted = 0
        for item in items:
            inserted += 1 if upsert_item(conn, item) else 0
        conn.commit()
        return SourceFetchSummary(source_id=source.slug, fetched=len(items), inserted=inserted)
    except Exception as exc:
        conn.rollback()
        return SourceFetchSummary(source_id=source.slug, error=f"{type(exc).__name__}: {exc}")


def _stored_wechat_body(conn: sqlite3.Connection, item: FetchedItem) -> tuple[str, str | None] | None:
    row = conn.execute(
        """
        SELECT content_text, content_html
        FROM items
        WHERE source_id=? AND lower(rtrim(url, '/'))=?
        ORDER BY length(content_text) DESC
        LIMIT 1
        """,
        (item.source_id, _normalized_url(item.url)),
    ).fetchone()
    if row is None:
        return None
    return str(row[0] or ""), row[1]


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _wechat_account(value: str | None) -> str | None:
    account = str(value or "").strip()
    return account or None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _wechat_avatar_cache_is_fresh(
    conn: sqlite3.Connection,
    account: str,
    *,
    now: datetime | None = None,
) -> bool:
    row = conn.execute(
        "SELECT avatar_url, checked_at FROM wechat_account_avatars WHERE account=?",
        (account,),
    ).fetchone()
    if row is None:
        return False
    if row[0]:
        return True
    checked_at = _parse_utc(row[1])
    if checked_at is None:
        return False
    return ((now or datetime.now(UTC)) - checked_at) < WECHAT_AVATAR_NEGATIVE_CACHE_TTL


def _cache_wechat_avatar_result(conn: sqlite3.Connection, account: str, avatar_url: object) -> None:
    normalized = normalize_wechat_avatar_url(avatar_url)
    checked_at = _utc_now()
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(account) DO UPDATE SET
          avatar_url=COALESCE(excluded.avatar_url, wechat_account_avatars.avatar_url),
          checked_at=excluded.checked_at,
          updated_at=CASE
            WHEN excluded.avatar_url IS NOT NULL THEN excluded.updated_at
            ELSE wechat_account_avatars.updated_at
          END
        """,
        (account, normalized, checked_at, checked_at),
    )


def _wechat_avatar_account_to_fetch(
    conn: sqlite3.Connection,
    item: FetchedItem,
    attempted_accounts: set[str],
) -> str | None:
    account = _wechat_account(item.author)
    if account is None or account in attempted_accounts:
        return None
    if _wechat_avatar_cache_is_fresh(conn, account):
        return None
    return account


def _cache_wechat_avatar_from_article(
    conn: sqlite3.Connection,
    account: str | None,
    article: dict[str, Any],
) -> None:
    if account is None:
        return
    _cache_wechat_avatar_result(conn, account, article.get("author_avatar_url"))


def refresh_wechat_avatar(conn: sqlite3.Connection, account: str) -> str | None:
    normalized_account = _wechat_account(account)
    if normalized_account is None:
        raise ValueError("account must not be empty")

    row = conn.execute(
        """
        SELECT i.url
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE COALESCE(s.kind, 'feed')='wechat'
          AND i.author=?
          AND i.url IS NOT NULL
          AND trim(i.url) != ''
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
        LIMIT 1
        """,
        (normalized_account,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no WeChat article found for account: {normalized_account}")

    article = scrape_article(str(row[0]))
    _cache_wechat_avatar_from_article(conn, normalized_account, article)
    cached = conn.execute(
        "SELECT avatar_url FROM wechat_account_avatars WHERE account=?",
        (normalized_account,),
    ).fetchone()
    if cached is None or not cached[0]:
        return None
    return str(cached[0])


def _backfill_wechat_avatar_cache(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    limit: int = WECHAT_AVATAR_BACKFILL_LIMIT,
) -> int:
    rows = conn.execute(
        """
        SELECT author, url
        FROM (
          SELECT i.author, i.url,
                 ROW_NUMBER() OVER (
                   PARTITION BY i.author
                   ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                 ) AS rn
          FROM items i
          LEFT JOIN wechat_account_avatars wa ON wa.account=i.author
          WHERE i.source_id=?
            AND i.author IS NOT NULL
            AND trim(i.author) != ''
            AND (wa.account IS NULL OR wa.avatar_url IS NULL)
        )
        WHERE rn=1
        LIMIT ?
        """,
        (source_id, limit),
    ).fetchall()
    checked = 0
    for row in rows:
        account = _wechat_account(row[0])
        if account is None or _wechat_avatar_cache_is_fresh(conn, account):
            continue
        article = scrape_article(str(row[1]))
        _cache_wechat_avatar_from_article(conn, account, article)
        checked += 1
    return checked


def _enrich_wechat_bodies(conn: sqlite3.Connection, items: list[FetchedItem]) -> list[FetchedItem]:
    enriched: list[FetchedItem] = []
    attempted_avatar_accounts: set[str] = set()
    for item in items:
        stored_body = _stored_wechat_body(conn, item)
        stored_text = stored_body[0] if stored_body is not None else ""
        stored_html = stored_body[1] if stored_body is not None else None
        use_stored_body = stored_body is not None and len(stored_text) > len(item.content_text)
        avatar_account = _wechat_avatar_account_to_fetch(conn, item, attempted_avatar_accounts)
        if use_stored_body and avatar_account is None:
            enriched.append(replace(item, content_text=stored_text, content_html=stored_html))
            continue

        article = scrape_article(item.url)
        if avatar_account is not None:
            attempted_avatar_accounts.add(avatar_account)
            _cache_wechat_avatar_from_article(conn, avatar_account, article)
        content_text = str(article.get("content_text") or "")
        if article.get("success") is True and content_text and not use_stored_body:
            enriched.append(
                replace(
                    item,
                    content_text=content_text,
                    content_html=str(article.get("content_html") or ""),
                )
            )
            continue
        if use_stored_body:
            enriched.append(replace(item, content_text=stored_text, content_html=stored_html))
            continue

        logger.warning(
            "Failed to scrape WeChat article; using RSS item only source_id=%s url=%s error=%s",
            item.source_id,
            item.url,
            article.get("error"),
        )
        enriched.append(item)
    return enriched


def fetch_all(path: Path | None = None, db_path: Path | None = None) -> FetchSummary:
    db.migrate(db_path)
    with db.get_conn(db_path) as conn:
        reload_sources(conn, path)
        sources = load_enabled_sources_from_db(conn)
        summaries = [fetch_source(conn, source) for source in sources]
    return FetchSummary(summaries)
