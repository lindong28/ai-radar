from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..provider.base import PrefilterProvider, PrefilterResult, ProviderItem
from ..provider.deepseek_v32 import DeepSeekV32Prefilter
from ..provider.glm import GLMPrefilter
from ..ruleset import current_version
from ..stage_common import insert_evaluation
from ..stage_common import parse_since as _parse_since
from ..stage_common import provider_item_from_row as _to_provider_item
from .prompts import render_prefilter_prompt


class PrefilterNumeric(BaseModel):
    is_ai_related: bool
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class PrefilterRunSummary:
    processed: int = 0
    errors: int = 0


def _provider_from_env() -> PrefilterProvider:
    name = os.environ.get("AI_RADAR_PREFILTER", "deepseek_v32")
    if name == "glm":
        return GLMPrefilter()
    if name == "deepseek_v32":
        return DeepSeekV32Prefilter()
    raise ValueError(f"unknown AI_RADAR_PREFILTER provider: {name}")


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
    insert_evaluation(
        conn,
        item_id=item.id,
        stage="prefilter",
        ruleset_version=ruleset_version,
        model_id=provider.model_id,
        input_data=prompt,
        output_data=output,
        numeric_data=numeric.model_dump() if numeric else None,
        latency_ms=latency_ms,
        error=error,
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
    for row in rows:
        item = _to_provider_item(row)
        numeric, output, error, latency_ms = _evaluate_item(selected_provider, item)
        errors += 1 if error else 0
        _insert_evaluation(conn, item, selected_provider, selected_ruleset, numeric, output, error, latency_ms)
        processed += 1
    conn.commit()
    return PrefilterRunSummary(processed=processed, errors=errors)
