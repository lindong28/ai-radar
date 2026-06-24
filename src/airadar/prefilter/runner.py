from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..llm_usage import db_path_from_connection, usage_db_path
from ..provider.base import PrefilterProvider, PrefilterResult, ProviderItem
from ..provider.deepseek_v32 import DeepSeekV32Prefilter
from ..provider.glm import GLMPrefilter
from ..ruleset import current_version
from .prompts import render_prefilter_prompt


class PrefilterNumeric(BaseModel):
    is_ai_related: bool
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class PrefilterRunSummary:
    processed: int = 0
    errors: int = 0


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_since(value: str) -> datetime:
    value = value.strip()
    unit = value[-1:].lower()
    if unit == "h":
        return datetime.now(UTC) - timedelta(hours=float(value[:-1]))
    if unit == "d":
        return datetime.now(UTC) - timedelta(days=float(value[:-1]))
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _provider_from_env() -> PrefilterProvider:
    name = os.environ.get("AI_RADAR_PREFILTER", "deepseek_v32")
    if name == "glm":
        return GLMPrefilter()
    if name == "deepseek_v32":
        return DeepSeekV32Prefilter()
    raise ValueError(f"unknown AI_RADAR_PREFILTER provider: {name}")


def _to_provider_item(row: sqlite3.Row | tuple[Any, ...]) -> ProviderItem:
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


def _candidate_rows(
    conn: sqlite3.Connection,
    since: str,
    ruleset_version: str,
    limit: int | None,
    force: bool,
    item_ids: Sequence[str] | None = None,
) -> list[sqlite3.Row | tuple[Any, ...]]:
    cutoff = _parse_since(since).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params: list[Any] = []
    if item_ids is not None:
        if not item_ids:
            return []
        item_filter = f"i.id IN ({','.join('?' for _ in item_ids)})"
        params.extend(item_ids)
    else:
        item_filter = "i.fetched_at >= ?"
        params.append(cutoff)
    skip_existing = (
        ""
        if force
        else """
      AND NOT EXISTS (
        SELECT 1 FROM item_evaluations e
        WHERE e.item_id=i.id AND e.stage='prefilter' AND e.ruleset_version=?
      )
    """
    )
    if not force:
        params.append(ruleset_version)
    sql = f"""
      SELECT i.id, i.title, i.url, i.source_id, s.tier, i.author, i.published_at, i.content_text
      FROM items i
      JOIN sources s ON s.id=i.source_id
      WHERE {item_filter}
      {skip_existing}
      ORDER BY i.fetched_at DESC, i.published_at DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _evaluate_item(
    provider: PrefilterProvider, item: ProviderItem
) -> tuple[PrefilterNumeric | None, dict[str, Any], str | None, int]:
    start = time.monotonic()
    if os.environ.get("AI_RADAR_FAKE_BAD_JSON"):
        time.sleep(0)
        latency_ms = int((time.monotonic() - start) * 1000)
        return None, {"raw": "{not valid json", "attempts": 2}, "json parse failed after retry", latency_ms

    result: PrefilterResult = provider.is_ai_related(item)
    output = {
        "is_ai_related": result.is_ai_related,
        "confidence": result.confidence,
        "raw": result.raw,
    }
    try:
        numeric = PrefilterNumeric.model_validate(output)
        error = None
    except ValidationError as exc:
        numeric = None
        error = f"json parse failed after retry: {exc}"
    latency_ms = int((time.monotonic() - start) * 1000)
    return numeric, output, error, latency_ms


def _insert_evaluation(
    conn: sqlite3.Connection,
    item: ProviderItem,
    provider: PrefilterProvider,
    ruleset_version: str,
    numeric: PrefilterNumeric | None,
    output: dict[str, Any],
    error: str | None,
    latency_ms: int,
) -> None:
    prompt = render_prefilter_prompt(item)
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, 'prefilter', ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            item.id,
            ruleset_version,
            provider.model_id,
            _json(prompt),
            _json(output),
            _json(numeric.model_dump()) if numeric else None,
            latency_ms,
            _utc_now(),
            error,
        ),
    )


def run_prefilter(
    conn: sqlite3.Connection,
    *,
    provider: PrefilterProvider | None = None,
    since: str = "24h",
    limit: int | None = None,
    ruleset_version: str | None = None,
    item_ids: Sequence[str] | None = None,
) -> PrefilterRunSummary:
    selected_provider = provider or _provider_from_env()
    selected_ruleset = ruleset_version or current_version()
    force = bool(os.environ.get("AI_RADAR_FAKE_BAD_JSON"))
    rows = _candidate_rows(conn, since, selected_ruleset, limit, force, item_ids)
    processed = 0
    errors = 0
    with usage_db_path(db_path_from_connection(conn)):
        for row in rows:
            item = _to_provider_item(row)
            numeric, output, error, latency_ms = _evaluate_item(selected_provider, item)
            errors += 1 if error else 0
            _insert_evaluation(conn, item, selected_provider, selected_ruleset, numeric, output, error, latency_ms)
            processed += 1
    conn.commit()
    return PrefilterRunSummary(processed=processed, errors=errors)
