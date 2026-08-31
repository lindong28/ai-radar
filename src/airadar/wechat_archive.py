from __future__ import annotations

import sqlite3

ARCHIVE_SOURCE_ID = "wx_ai_assistant_kb_archive"
ARCHIVE_SOURCE_NAME = "AI Assistant 微信知识库归档"
ARCHIVE_SOURCE_URL = "internal://ai-assistant-kb"


def wechat_visibility_sql(source_alias: str = "s") -> str:
    return (
        f"(COALESCE({source_alias}.kind, 'feed')='wechat' AND "
        f"({source_alias}.enabled=1 OR {source_alias}.id='{ARCHIVE_SOURCE_ID}'))"
    )


def public_source_sql(source_alias: str = "s") -> str:
    return f"{source_alias}.id<>'{ARCHIVE_SOURCE_ID}'"


def ensure_archive_source(conn: sqlite3.Connection, *, synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, meta_json, synced_at, kind,
          homepage_url, icon_url, public_url_override, optional, required_env, wechat_only
        )
        VALUES (?, ?, ?, 'T2', 0, '{}', ?, 'wechat', NULL, NULL, NULL, 1, NULL, 1)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          url=excluded.url,
          tier=excluded.tier,
          enabled=0,
          meta_json=excluded.meta_json,
          synced_at=excluded.synced_at,
          kind=excluded.kind,
          homepage_url=NULL,
          icon_url=NULL,
          public_url_override=NULL,
          optional=1,
          required_env=NULL,
          wechat_only=1
        """,
        (ARCHIVE_SOURCE_ID, ARCHIVE_SOURCE_NAME, ARCHIVE_SOURCE_URL, synced_at),
    )
