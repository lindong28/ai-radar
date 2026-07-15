from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .provider.base import ProviderItem


def json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_since(value: str) -> datetime:
    value = value.strip()
    unit = value[-1:].lower()
    if unit == "h":
        return datetime.now(UTC) - timedelta(hours=float(value[:-1]))
    if unit == "d":
        return datetime.now(UTC) - timedelta(days=float(value[:-1]))
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def provider_item_from_row(row: sqlite3.Row | tuple[Any, ...]) -> ProviderItem:
    return ProviderItem(
        id=row[0],
        title=row[1],
        url=row[2],
        source_id=row[3],
        tier=row[4],
        author=row[5],
        published_at=row[6],
        content_text=row[7],
    )


def insert_evaluation(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    stage: str,
    ruleset_version: str,
    model_id: str,
    input_data: object,
    output_data: object,
    numeric_data: object | None,
    latency_ms: int,
    error: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            item_id,
            stage,
            ruleset_version,
            model_id,
            json_dumps(input_data),
            json_dumps(output_data),
            json_dumps(numeric_data) if numeric_data is not None else None,
            latency_ms,
            utc_now(),
            error,
        ),
    )
