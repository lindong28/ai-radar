from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..provider.base import ProviderItem, ScoringProvider, ScoringResult
from ..provider.codex_gpt_mini import CodexGptMiniScorer
from ..provider.deepseek_v4_flash import DeepSeekV4FlashScorer
from ..provider.deepseek_v4_pro import DeepSeekV4ProScorer
from ..ruleset import current_version
from ..stage_common import insert_evaluation
from ..stage_common import parse_since as _parse_since
from ..stage_common import provider_item_from_row as _to_provider_item
from .prompts import render_scoring_prompt
from .schema import ScoringNumeric


@dataclass(frozen=True)
class ScoringRunSummary:
    processed: int = 0
    errors: int = 0


def _provider_from_env() -> ScoringProvider:
    name = os.environ.get("AI_RADAR_SCORER", "deepseek_v4_flash")
    if name == "codex_gpt_mini":
        return CodexGptMiniScorer()
    if name == "deepseek_v4_flash":
        return DeepSeekV4FlashScorer()
    if name == "deepseek_v4_pro":
        return DeepSeekV4ProScorer()
    raise ValueError(f"unknown AI_RADAR_SCORER provider: {name}")


def _candidate_rows(
    conn: sqlite3.Connection,
    since: str,
    ruleset_version: str,
    limit: int | None,
    force: bool,
) -> list[sqlite3.Row | tuple[Any, ...]]:
    cutoff = _parse_since(since).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params: list[Any] = [cutoff]
    skip_existing = (
        ""
        if force
        else """
      AND NOT EXISTS (
        SELECT 1 FROM item_evaluations scored
        WHERE scored.item_id=i.id AND scored.stage='scoring' AND scored.ruleset_version=?
      )
    """
    )
    if not force:
        params.append(ruleset_version)
    sql = f"""
      SELECT i.id, i.title, i.url, i.source_id, s.tier, i.author, i.published_at, i.content_text
      FROM items i
      JOIN sources s ON s.id=i.source_id
      WHERE i.fetched_at >= ?
        AND EXISTS (
          SELECT 1 FROM item_evaluations pre
          WHERE pre.item_id=i.id
            AND pre.stage='prefilter'
            AND pre.error IS NULL
            AND json_extract(pre.numeric_json, '$.is_ai_related') = 1
        )
      {skip_existing}
      ORDER BY i.fetched_at DESC, i.published_at DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _evaluate_item(
    provider: ScoringProvider, item: ProviderItem
) -> tuple[ScoringNumeric | None, dict[str, Any], str | None, int]:
    start = time.monotonic()
    if os.environ.get("AI_RADAR_FAKE_OUT_OF_RANGE"):
        output = {
            "relevance": 99,
            "density": 5,
            "recency": 5,
            "authority": 5,
            "engineering": 5,
            "reasoning": "forced invalid score",
        }
    else:
        result: ScoringResult = provider.score_5d(item)
        output = {
            "relevance": result.relevance,
            "density": result.density,
            "recency": result.recency,
            "authority": result.authority,
            "engineering": result.engineering,
            "reasoning": result.reasoning[:200],
            "topics": list(result.topics)[:4],
            "raw": result.raw,
        }
    try:
        numeric = ScoringNumeric.model_validate(output)
        error = None
    except ValidationError as exc:
        numeric = None
        error = f"schema validation failed after retry: {exc}"
    latency_ms = int((time.monotonic() - start) * 1000)
    return numeric, output, error, latency_ms


def _insert_evaluation(
    conn: sqlite3.Connection,
    item: ProviderItem,
    provider: ScoringProvider,
    ruleset_version: str,
    numeric: ScoringNumeric | None,
    output: dict[str, Any],
    error: str | None,
    latency_ms: int,
) -> None:
    insert_evaluation(
        conn,
        item_id=item.id,
        stage="scoring",
        ruleset_version=ruleset_version,
        model_id=provider.model_id,
        input_data=render_scoring_prompt(item),
        output_data=output,
        numeric_data=numeric.model_dump() if numeric else None,
        latency_ms=latency_ms,
        error=error,
    )


def run_scoring(
    conn: sqlite3.Connection,
    *,
    provider: ScoringProvider | None = None,
    since: str = "24h",
    limit: int | None = None,
    ruleset_version: str | None = None,
) -> ScoringRunSummary:
    selected_provider = provider or _provider_from_env()
    selected_ruleset = ruleset_version or current_version()
    force = bool(os.environ.get("AI_RADAR_FAKE_OUT_OF_RANGE"))
    rows = _candidate_rows(conn, since, selected_ruleset, limit, force)
    processed = 0
    errors = 0
    for row in rows:
        item = _to_provider_item(row)
        numeric, output, error, latency_ms = _evaluate_item(selected_provider, item)
        errors += 1 if error else 0
        _insert_evaluation(conn, item, selected_provider, selected_ruleset, numeric, output, error, latency_ms)
        processed += 1
    conn.commit()
    return ScoringRunSummary(processed=processed, errors=errors)
