from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from .loader import SourceConfig
from .x_state import X_RUNTIME_META_KEYS, X_RUNTIME_SCHEMA_VERSION, validate_x_runtime_meta


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sync_to_db(sources: list[SourceConfig], conn: sqlite3.Connection) -> None:
    synced_at = _utc_now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        planned_meta: dict[str, dict[str, object]] = {}
        for source in sources:
            meta = dict(source.meta)
            if source.kind == "x" and meta.get("adapter") == "x_api":
                if X_RUNTIME_META_KEYS & meta.keys():
                    raise ValueError(f"invalid x_api configuration for {source.slug}: runtime state is internal")
                existing = conn.execute("SELECT meta_json FROM sources WHERE id=?", (source.slug,)).fetchone()
                if existing is None:
                    meta["x_state_schema_version"] = X_RUNTIME_SCHEMA_VERSION
                    meta["x_cursor_state"] = "identity_pending"
                    meta["x_reference_status"] = "pending"
                else:
                    try:
                        existing_meta = json.loads(existing[0] or "{}")
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"invalid X metadata for {source.slug}: malformed JSON") from exc
                    if not isinstance(existing_meta, dict):
                        raise ValueError(f"invalid X metadata for {source.slug}: expected object")
                    existing_runtime_keys = X_RUNTIME_META_KEYS & existing_meta.keys()
                    if existing_runtime_keys:
                        existing_username = existing_meta.get("username")
                        if (
                            existing_meta.get("adapter") != "x_api"
                            or not isinstance(existing_username, str)
                            or not existing_username
                        ):
                            raise ValueError(f"invalid X identity for {source.slug}: runtime state has no owner")
                        validate_x_runtime_meta(existing_meta, context=source.slug)
                    same_identity = (
                        existing_meta.get("adapter") == meta.get("adapter")
                        and str(existing_meta.get("username") or "").casefold()
                        == str(meta.get("username") or "").casefold()
                    )
                    if same_identity:
                        validated_runtime = validate_x_runtime_meta(existing_meta, context=source.slug)
                        meta.update(validated_runtime)
                    else:
                        meta["x_state_schema_version"] = X_RUNTIME_SCHEMA_VERSION
                        meta["x_cursor_state"] = "identity_pending"
                        meta["x_reference_status"] = "pending"
            planned_meta[source.slug] = meta

        for source in sources:
            meta = planned_meta[source.slug]
            conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at, public_url_override, optional, required_env, wechat_only)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              url=excluded.url,
              tier=excluded.tier,
              enabled=excluded.enabled,
              kind=excluded.kind,
              homepage_url=excluded.homepage_url,
              icon_url=excluded.icon_url,
              meta_json=excluded.meta_json,
              synced_at=excluded.synced_at,
              public_url_override=excluded.public_url_override,
              optional=excluded.optional,
              required_env=excluded.required_env,
              wechat_only=excluded.wechat_only
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
                _json(meta),
                synced_at,
                source.public_url_override,
                1 if source.optional else 0,
                source.required_env,
                1 if source.wechat_only else 0,
            ),
            )
        if sources:
            ids = [source.slug for source in sources]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"UPDATE sources SET enabled=0 WHERE id NOT IN ({placeholders})", ids)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def load_enabled_sources_from_db(conn: sqlite3.Connection) -> list[SourceConfig]:
    rows = conn.execute(
        "SELECT id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, public_url_override, optional, required_env, wechat_only "
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
                public_url_override=row[9],
                optional=bool(row[10]),
                required_env=row[11],
                wechat_only=bool(row[12]),
            )
        )
    return sources
