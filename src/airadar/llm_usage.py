from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import db
from .pricing import PricingCatalog, PricingEntry, get_pricing, resolve_price

logger = logging.getLogger(__name__)

_usage_db_path: ContextVar[str | Path | None] = ContextVar("airadar_usage_db_path", default=None)
USAGE_DB_ENV = "AI_RADAR_LLM_USAGE_DB"
DEFAULT_USAGE_DB_PATH = db.PROJECT_ROOT / "data" / "llm_usage.db"
LEGACY_USAGE_MIGRATION_ID = "001_copy_main_llm_usage"
INTERPRET_STAGE_MIGRATION_ID = "002_add_interpret_stage"
NULL_DEPRECATED_COST_MIGRATION_ID = "003_null_deprecated_cost"
ROLLOUT_COST_COMPAT_MIGRATION_ID = "004_rollout_cost_compat"
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
  cost_usd REAL DEFAULT NULL,
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
    attribution: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True)
class DerivedCost:
    cost_usd: float | None
    status: str
    freshness: str | None
    quote: PricingEntry | None
    cached_input_tokens: int | None
    uncached_input_tokens: int | None


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
          total_tokens, input_item_count, input_char_count, NULL,
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
          total_tokens, input_item_count, input_char_count, NULL,
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


def _apply_null_deprecated_cost_migration(conn: sqlite3.Connection) -> None:
    if _usage_migration_applied(conn, NULL_DEPRECATED_COST_MIGRATION_ID):
        return
    cost_column = next(
        (row for row in conn.execute("PRAGMA table_info(llm_usage)") if row[1] == "cost_usd"),
        None,
    )
    if cost_column is not None and int(cost_column[3]) != 0:
        _rebuild_llm_usage_with_current_schema(conn)
    conn.execute("UPDATE llm_usage SET cost_usd = NULL")
    _mark_usage_migration(conn, NULL_DEPRECATED_COST_MIGRATION_ID)


def _apply_rollout_cost_compat_migration(conn: sqlite3.Connection) -> None:
    if _usage_migration_applied(conn, ROLLOUT_COST_COMPAT_MIGRATION_ID):
        return
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='llm_usage'"
    ).fetchone()
    if row is not None and "CHECK (cost_usd IS NULL)" in str(row[0] or ""):
        _rebuild_llm_usage_with_current_schema(conn)
    _mark_usage_migration(conn, ROLLOUT_COST_COMPAT_MIGRATION_ID)


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
        _apply_null_deprecated_cost_migration(conn)
        _apply_rollout_cost_compat_migration(conn)
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


def _row_value(row: object, field_name: str, default: object = None) -> object:
    if isinstance(row, Mapping):
        return row.get(field_name, default)
    try:
        return row[field_name]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        return getattr(row, field_name, default)


def _non_negative_row_int(value: object) -> int:
    try:
        return max(0, int(str(value or 0)))
    except (TypeError, ValueError):
        return 0


def _nested_value(value: object, field_name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def cached_input_tokens_from_usage(usage: object | None) -> tuple[int | None, str | None]:
    if usage is None:
        return None, None
    for field_name in ("prompt_cache_hit_tokens", "cache_read_input_tokens"):
        raw = _nested_value(usage, field_name)
        if raw is not None:
            return _non_negative_row_int(raw), field_name
    for details_name in ("prompt_tokens_details", "input_tokens_details"):
        details = _nested_value(usage, details_name)
        if details is None:
            continue
        raw = _nested_value(details, "cached_tokens")
        if raw is not None:
            return _non_negative_row_int(raw), f"{details_name}.cached_tokens"
    return None, None


def cache_usage_attribution(usage: object | None) -> dict[str, object]:
    cached, source_field = cached_input_tokens_from_usage(usage)
    if cached is None:
        return {}
    return {"cached_input_tokens": cached, "cached_input_tokens_source": source_field}


def _attribution_cached_input_tokens(row: object) -> int | None:
    raw = _row_value(row, "attribution_json", None)
    if not raw:
        return None
    try:
        attribution = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(attribution, dict) or attribution.get("cached_input_tokens") is None:
        return None
    return _non_negative_row_int(attribution["cached_input_tokens"])


def derive_cost_usd(row: object, *, catalog: PricingCatalog | None = None) -> DerivedCost:
    active_catalog = catalog or get_pricing()
    provider = str(_row_value(row, "provider", "") or "")
    model = str(_row_value(row, "model", "") or "")
    created_at = _row_value(row, "created_at", None)
    quote = resolve_price(
        provider,
        model,
        active_catalog,
        effective_at=str(created_at) if created_at else None,
    )
    input_tokens = _non_negative_row_int(_row_value(row, "input_tokens", 0))
    output_tokens = _non_negative_row_int(_row_value(row, "output_tokens", 0))
    cached_raw = _row_value(row, "cached_input_tokens", None)
    cached_input_tokens = (
        _attribution_cached_input_tokens(row)
        if cached_raw is None
        else _non_negative_row_int(cached_raw)
    )
    if cached_input_tokens is not None:
        cached_input_tokens = min(input_tokens, cached_input_tokens)
    uncached_input_tokens = (
        input_tokens - cached_input_tokens if cached_input_tokens is not None else None
    )
    if quote is None:
        return DerivedCost(
            cost_usd=None,
            status="unpriced",
            freshness=None,
            quote=None,
            cached_input_tokens=cached_input_tokens,
            uncached_input_tokens=uncached_input_tokens,
        )
    if cached_input_tokens is None:
        cost = (
            input_tokens * quote.input_cost_per_token
            + output_tokens * quote.output_cost_per_token
        )
    else:
        cost = (
            (input_tokens - cached_input_tokens) * quote.input_cost_per_token
            + cached_input_tokens * quote.cache_read_input_token_cost
            + output_tokens * quote.output_cost_per_token
        )
    return DerivedCost(
        cost_usd=round(cost, 12),
        status="nominal" if quote.nominal else "priced",
        freshness=quote.freshness,
        quote=quote,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=uncached_input_tokens,
    )


def record_llm_usage(record: LlmUsageRecord, *, db_path: str | Path | None = None) -> None:
    created_at = record.created_at or utc_now_iso()
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
                None,  # Deprecated storage column; costs are derived by derive_cost_usd().
                json.dumps(
                    record.attribution,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                created_at,
            ),
        )
        conn.commit()


def record_llm_usage_best_effort(
    record: LlmUsageRecord,
    *,
    db_path: str | Path | None = None,
) -> bool:
    """Record usage without turning a paid model result into a provider failure."""
    try:
        record_llm_usage(record, db_path=db_path)
    except sqlite3.Error as exc:
        logger.error(
            "llm_usage_metering_failure stage=%s provider=%s model=%s item_id=%s error=%s:%s",
            record.stage,
            record.provider,
            record.model,
            record.item_id,
            type(exc).__name__,
            exc,
        )
        return False
    return True
