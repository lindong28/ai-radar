from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import db

_usage_db_path: ContextVar[str | Path | None] = ContextVar("airadar_usage_db_path", default=None)
USAGE_DB_ENV = "AI_RADAR_LLM_USAGE_DB"
DEFAULT_USAGE_DB_PATH = db.PROJECT_ROOT / "data" / "llm_usage.db"
LEGACY_USAGE_MIGRATION_ID = "001_copy_main_llm_usage"
INTERPRET_STAGE_MIGRATION_ID = "002_add_interpret_stage"
LLM_USAGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'score', 'enrich', 'interpret')),
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  item_id TEXT,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  input_item_count INTEGER NOT NULL DEFAULT 1,
  input_char_count INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  attribution_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_created_model
ON llm_usage(created_at, model);

CREATE INDEX IF NOT EXISTS idx_llm_usage_stage_created
ON llm_usage(stage, created_at);

CREATE INDEX IF NOT EXISTS idx_llm_usage_item
ON llm_usage(item_id);

CREATE TABLE IF NOT EXISTS airadar_usage_migrations (
  id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class LlmUsageRecord:
    stage: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    item_id: str | None = None
    input_item_count: int = 1
    input_char_count: int = 0
    cost_usd: float = 0.0
    attribution: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@contextmanager
def usage_db_path(path: str | Path | None) -> Iterator[None]:
    token = _usage_db_path.set(path)
    try:
        yield
    finally:
        _usage_db_path.reset(token)


def db_path_from_connection(conn: sqlite3.Connection) -> str | None:
    for row in conn.execute("PRAGMA database_list").fetchall():
        name = row[1]
        path = row[2]
        if name == "main" and path:
            return str(path)
    return None


def _resolve_usage_db_path(path: str | Path | None = None) -> Path:
    configured = path or os.environ.get(USAGE_DB_ENV) or DEFAULT_USAGE_DB_PATH
    usage_path = Path(configured)
    if not usage_path.is_absolute():
        usage_path = db.PROJECT_ROOT / usage_path
    return usage_path


def active_usage_db_path(explicit_path: str | Path | None = None) -> Path:
    return _resolve_usage_db_path(explicit_path or _usage_db_path.get())


def _execute_usage_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(LLM_USAGE_SCHEMA_SQL)


def _legacy_usage_table_exists(main_db_path: Path) -> bool:
    if not main_db_path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{main_db_path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_usage'").fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def _legacy_migration_applied(conn: sqlite3.Connection) -> bool:
    return _usage_migration_applied(conn, LEGACY_USAGE_MIGRATION_ID)


def _usage_migration_applied(conn: sqlite3.Connection, migration_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM airadar_usage_migrations WHERE id=?",
        (migration_id,),
    ).fetchone()
    return row is not None


def _mark_usage_migration(conn: sqlite3.Connection, migration_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO airadar_usage_migrations(id, applied_at) VALUES (?, ?)",
        (migration_id, utc_now_iso()),
    )


def _copy_legacy_usage_rows(
    conn: sqlite3.Connection,
    *,
    main_db_path: Path,
    usage_db_path: Path,
) -> None:
    if main_db_path == usage_db_path or not _legacy_usage_table_exists(main_db_path):
        return
    conn.execute("ATTACH DATABASE ? AS legacy_main", (str(main_db_path),))
    conn.execute(
        """
        INSERT OR IGNORE INTO llm_usage (
          id, stage, provider, model, item_id, input_tokens, output_tokens,
          total_tokens, input_item_count, input_char_count, cost_usd,
          attribution_json, created_at
        )
        SELECT
          id, stage, provider, model, item_id, input_tokens, output_tokens,
          total_tokens, input_item_count, input_char_count, cost_usd,
          attribution_json, created_at
        FROM legacy_main.llm_usage
        """
    )


def _llm_usage_allows_interpret(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='llm_usage'",
    ).fetchone()
    return row is not None and "interpret" in str(row[0] or "")


def _rebuild_llm_usage_with_current_schema(conn: sqlite3.Connection) -> None:
    for index_name in (
        "idx_llm_usage_created_model",
        "idx_llm_usage_stage_created",
        "idx_llm_usage_item",
    ):
        conn.execute(f"DROP INDEX IF EXISTS {index_name}")
    conn.execute("ALTER TABLE llm_usage RENAME TO llm_usage_old")
    _execute_usage_schema(conn)
    conn.execute(
        """
        INSERT INTO llm_usage (
          id, stage, provider, model, item_id, input_tokens, output_tokens,
          total_tokens, input_item_count, input_char_count, cost_usd,
          attribution_json, created_at
        )
        SELECT
          id, stage, provider, model, item_id, input_tokens, output_tokens,
          total_tokens, input_item_count, input_char_count, cost_usd,
          attribution_json, created_at
        FROM llm_usage_old
        """
    )
    conn.execute("DROP TABLE llm_usage_old")


def _apply_interpret_stage_migration(conn: sqlite3.Connection) -> None:
    if _usage_migration_applied(conn, INTERPRET_STAGE_MIGRATION_ID):
        return
    if not _llm_usage_allows_interpret(conn):
        _rebuild_llm_usage_with_current_schema(conn)
    _mark_usage_migration(conn, INTERPRET_STAGE_MIGRATION_ID)


def migrate_usage_db(
    *,
    usage_db_path: str | Path | None = None,
    main_db_path: str | Path | None = None,
) -> Path:
    usage_path = _resolve_usage_db_path(usage_db_path)
    main_path = db.resolve_db_path(main_db_path)
    with db.get_conn(usage_path) as conn:
        _execute_usage_schema(conn)
        if not _legacy_migration_applied(conn):
            _copy_legacy_usage_rows(conn, main_db_path=main_path, usage_db_path=usage_path)
            _mark_usage_migration(conn, LEGACY_USAGE_MIGRATION_ID)
        _apply_interpret_stage_migration(conn)
        conn.commit()
    return usage_path


def usage_int(usage: object | None, field_name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        raw = usage.get(field_name, 0)
    else:
        raw = getattr(usage, field_name, 0)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    raw = os.environ.get("AI_RADAR_LLM_PRICING_JSON", "").strip()
    if not raw:
        return 0.0
    try:
        pricing = json.loads(raw)
    except json.JSONDecodeError:
        return 0.0
    if not isinstance(pricing, dict):
        return 0.0
    model_pricing = pricing.get(model) or pricing.get("*")
    if not isinstance(model_pricing, dict):
        return 0.0
    try:
        input_rate = float(
            model_pricing.get("input_per_million_tokens_usd", model_pricing.get("input_per_million", 0.0))
        )
        output_rate = float(
            model_pricing.get("output_per_million_tokens_usd", model_pricing.get("output_per_million", 0.0))
        )
    except (TypeError, ValueError):
        return 0.0
    return round(((input_tokens * input_rate) + (output_tokens * output_rate)) / 1_000_000, 8)


def record_llm_usage(record: LlmUsageRecord, *, db_path: str | Path | None = None) -> None:
    created_at = record.created_at or utc_now_iso()
    try:
        path = migrate_usage_db(
            usage_db_path=active_usage_db_path(db_path),
            main_db_path=db_path if db_path is not None else None,
        )
        with db.get_conn(path) as conn:
            conn.execute(
                """
                INSERT INTO llm_usage (
                  stage, provider, model, item_id, input_tokens, output_tokens,
                  total_tokens, input_item_count, input_char_count, cost_usd,
                  attribution_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.stage,
                    record.provider,
                    record.model,
                    record.item_id,
                    record.input_tokens,
                    record.output_tokens,
                    record.total_tokens,
                    record.input_item_count,
                    record.input_char_count,
                    record.cost_usd,
                    json.dumps(record.attribution, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    created_at,
                ),
            )
            conn.commit()
    except sqlite3.Error:
        return
