from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import db
from ..sources.loader import SourceConfig, load_sources
from ..sources.sync import load_enabled_sources_from_db, sync_to_db
from .dedup import FetchedItem, _normalized_url, upsert_item
from .http_client import fetch_feed
from .rss import parse_feed
from .wechat import scrape_article

logger = logging.getLogger(__name__)


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


def _enrich_wechat_bodies(conn: sqlite3.Connection, items: list[FetchedItem]) -> list[FetchedItem]:
    enriched: list[FetchedItem] = []
    for item in items:
        stored_body = _stored_wechat_body(conn, item)
        if stored_body is not None:
            stored_text, stored_html = stored_body
            if len(stored_text) > len(item.content_text):
                enriched.append(replace(item, content_text=stored_text, content_html=stored_html))
                continue

        article = scrape_article(item.url)
        content_text = str(article.get("content_text") or "")
        if article.get("success") is True and content_text:
            enriched.append(
                replace(
                    item,
                    content_text=content_text,
                    content_html=str(article.get("content_html") or ""),
                )
            )
            continue

        logger.warning(
            "Failed to scrape WeChat article; using RSS item only source_id=%s url=%s error=%s",
            item.source_id,
            item.url,
            article.get("error"),
        )
        if stored_body is not None:
            stored_text, stored_html = stored_body
            if len(stored_text) > len(item.content_text):
                enriched.append(replace(item, content_text=stored_text, content_html=stored_html))
                continue
        enriched.append(item)
    return enriched


def fetch_all(path: Path | None = None, db_path: Path | None = None) -> FetchSummary:
    db.migrate(db_path)
    with db.get_conn(db_path) as conn:
        reload_sources(conn, path)
        sources = load_enabled_sources_from_db(conn)
        summaries = [fetch_source(conn, source) for source in sources]
    return FetchSummary(summaries)
