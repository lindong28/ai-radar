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


# A failed enrich attempt is retried at most once per this window. items.fetched_at is
# bumped every time a feed re-lists an item, so a failed item never ages out of the
# `--since` window on its own; without this backoff every failure is retried every round
# and, under a per-round `--limit`, crowds out never-attempted items.
ENRICH_FAILED_RETRY_BACKOFF_HOURS = 24
# Only deterministic rejections (schema / controlled-vocabulary / normalizer errors) back
# off: a transient provider failure (connection error, 5xx, rate limit) should be retried
# on the next round. The prefixes are written by the enrich runners' error classifier.
# The third prefix is legacy: rows written before 2026-09-03 wrapped normalizer rejections
# as ``enrich failed after retry: tags ...``; provider failures never start with "tags".
DETERMINISTIC_ENRICH_ERROR_PREFIXES = (
    "schema validation failed",
    "output rejected",
    "enrich failed after retry: tags",
)


def failed_retry_cutoff() -> str:
    cutoff = datetime.now(UTC) - timedelta(hours=ENRICH_FAILED_RETRY_BACKOFF_HOURS)
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
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
