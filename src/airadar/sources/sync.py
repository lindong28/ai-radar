from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from .loader import SourceConfig


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sync_to_db(sources: list[SourceConfig], conn: sqlite3.Connection) -> None:
    synced_at = _utc_now()
    for source in sources:
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              url=excluded.url,
              tier=excluded.tier,
              enabled=excluded.enabled,
              kind=excluded.kind,
              homepage_url=excluded.homepage_url,
              icon_url=excluded.icon_url,
              meta_json=excluded.meta_json,
              synced_at=excluded.synced_at
            """,
            (
                source.slug,
                source.name,
                source.url,
                source.tier,
                1 if source.enabled else 0,
                source.kind,
                source.homepage_url,
                source.icon_url,
                _json(source.meta),
                synced_at,
            ),
        )
    if sources:
        ids = [source.slug for source in sources]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE sources SET enabled=0 WHERE id NOT IN ({placeholders})", ids)
    conn.commit()


def load_enabled_sources_from_db(conn: sqlite3.Connection) -> list[SourceConfig]:
    rows = conn.execute(
        "SELECT id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json "
        "FROM sources WHERE enabled=1 ORDER BY id"
    ).fetchall()
    sources: list[SourceConfig] = []
    for row in rows:
        meta_raw = row[8] or "{}"
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError:
            meta = {}
        sources.append(
            SourceConfig(
                slug=row[0],
                name=row[1],
                url=row[2],
                tier=row[3],
                enabled=bool(row[4]),
                kind=row[5] or "feed",
                homepage_url=row[6],
                icon_url=row[7],
                meta=meta,
            )
        )
    return sources
