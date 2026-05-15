from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .. import db
from ..sources.loader import SourceConfig, load_sources
from ..sources.sync import load_enabled_sources_from_db, sync_to_db
from .dedup import upsert_item
from .http_client import fetch_feed
from .rss import parse_feed


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
    return db.PROJECT_ROOT / "apps" / "ai-radar" / "data" / "sources.toml"


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
        inserted = 0
        for item in items:
            inserted += 1 if upsert_item(conn, item) else 0
        conn.commit()
        return SourceFetchSummary(source_id=source.slug, fetched=len(items), inserted=inserted)
    except Exception as exc:
        conn.rollback()
        return SourceFetchSummary(source_id=source.slug, error=f"{type(exc).__name__}: {exc}")


def fetch_all(path: Path | None = None, db_path: Path | None = None) -> FetchSummary:
    db.migrate(db_path)
    with db.get_conn(db_path) as conn:
        reload_sources(conn, path)
        sources = load_enabled_sources_from_db(conn)
        summaries = [fetch_source(conn, source) for source in sources]
    return FetchSummary(summaries)
