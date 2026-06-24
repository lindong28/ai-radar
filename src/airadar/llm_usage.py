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


def active_usage_db_path(explicit_path: str | Path | None = None) -> str | Path | None:
    return explicit_path or _usage_db_path.get() or os.environ.get("AI_RADAR_DB")


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
    path = active_usage_db_path(db_path)
    if path is None:
        path = db.DEFAULT_DB_PATH
    created_at = record.created_at or utc_now_iso()
    try:
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
