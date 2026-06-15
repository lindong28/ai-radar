from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ..provider.base import EnrichProvider, EnrichResult, ProviderItem
from ..provider.deepseek_v4_flash import DeepSeekV4FlashEnricher
from ..provider.deepseek_v4_pro import DeepSeekV4ProEnricher
from ..ruleset import current_version
from ..topics import topic_tags
from .prompts import render_enrich_prompt
from .schema import EnrichOutput


@dataclass(frozen=True)
class EnrichRunSummary:
    processed: int = 0
    errors: int = 0


@dataclass(frozen=True)
class EnrichProgress:
    total: int
    completed: int
    errors: int
    item_id: str
    error: str | None
    latency_ms: int


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


def _provider_from_env() -> EnrichProvider:
    name = os.environ.get("AI_RADAR_ENRICHER", "deepseek_v4_pro")
    if name == "deepseek_v4_flash":
        return DeepSeekV4FlashEnricher()
    if name == "deepseek_v4_pro":
        return DeepSeekV4ProEnricher()
    raise ValueError(f"unknown AI_RADAR_ENRICHER provider: {name}")


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
    item_ids: Sequence[str] | None = None,
) -> list[sqlite3.Row | tuple[Any, ...]]:
    cutoff = _parse_since(since).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    params: list[Any] = []
    item_filter = ""
    if item_ids is not None:
        if not item_ids:
            return []
        placeholders = ",".join("?" for _ in item_ids)
        item_filter = f"i.id IN ({placeholders})"
        params.extend(item_ids)
    else:
        item_filter = "i.fetched_at >= ?"
        params.append(cutoff)
    params.append(ruleset_version)
    sql = f"""
      SELECT i.id, i.title, i.url, i.source_id, s.tier, i.author, i.published_at, i.content_text
      FROM items i
      JOIN sources s ON s.id=i.source_id
      WHERE {item_filter}
        AND EXISTS (
          SELECT 1 FROM item_evaluations pre
          WHERE pre.item_id=i.id
            AND pre.stage='prefilter'
            AND pre.error IS NULL
            AND json_extract(pre.numeric_json, '$.is_ai_related') = 1
        )
        AND NOT EXISTS (
          SELECT 1 FROM item_evaluations enriched
          WHERE enriched.item_id=i.id
            AND enriched.stage='enrich'
            AND enriched.ruleset_version=?
            AND enriched.error IS NULL
        )
      ORDER BY i.fetched_at DESC, i.published_at DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _controlled_tags(result: EnrichResult, item: ProviderItem) -> list[str]:
    tags = topic_tags(
        list(result.tags),
        source_id=item.source_id,
        url=item.url,
        title=item.title,
        content_text=item.content_text,
    )
    for fallback in ["行业动态", "模型发布", "教程/实践"]:
        if len(tags) >= 2:
            break
        if fallback not in tags:
            tags.append(fallback)
    return tags[:4]


def _output_from_result(result: EnrichResult, item: ProviderItem) -> dict[str, Any]:
    return {
        "title_zh": result.title_zh,
        "summary_zh": result.summary_zh,
        "why_recommend": result.why_recommend,
        "tags": _controlled_tags(result, item),
        "raw": result.raw,
    }


def _evaluate_item(
    provider: EnrichProvider, item: ProviderItem
) -> tuple[EnrichOutput | None, dict[str, Any], str | None, int]:
    start = time.monotonic()
    attempts = 0
    last_output: dict[str, Any] = {}
    last_error: str | None = None
    while attempts < 2:
        attempts += 1
        try:
            result = provider.enrich(item)
            output = _output_from_result(result, item)
            last_output = output
            enriched = EnrichOutput.model_validate(output)
            latency_ms = int((time.monotonic() - start) * 1000)
            return enriched, output, None, latency_ms
        except ValidationError as exc:
            last_error = f"schema validation failed after retry: {exc}" if attempts == 2 else str(exc)
        except Exception as exc:
            last_error = f"enrich failed after retry: {exc}" if attempts == 2 else str(exc)
    latency_ms = int((time.monotonic() - start) * 1000)
    return None, {**last_output, "attempts": attempts}, last_error, latency_ms


def _insert_evaluation(
    conn: sqlite3.Connection,
    item: ProviderItem,
    provider: EnrichProvider,
    ruleset_version: str,
    enriched: EnrichOutput | None,
    output: dict[str, Any],
    error: str | None,
    latency_ms: int,
) -> None:
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, 'enrich', ?, ?, ?, ?, NULL, ?, 0, ?, ?)
        """,
        (
            item.id,
            ruleset_version,
            provider.model_id,
            _json(render_enrich_prompt(item)),
            _json(enriched.model_dump() if enriched else output),
            latency_ms,
            _utc_now(),
            error,
        ),
    )


def run_enrich(
    conn: sqlite3.Connection,
    *,
    provider: EnrichProvider | None = None,
    since: str = "24h",
    limit: int | None = None,
    ruleset_version: str | None = None,
    item_ids: Sequence[str] | None = None,
    workers: int = 1,
    progress_callback: Callable[[EnrichProgress], None] | None = None,
) -> EnrichRunSummary:
    selected_provider = provider or _provider_from_env()
    selected_ruleset = ruleset_version or current_version()
    rows = _candidate_rows(conn, since, selected_ruleset, limit, item_ids)
    items = [_to_provider_item(row) for row in rows]
    total = len(items)
    selected_workers = max(1, min(workers, total or 1))
    processed = 0
    errors = 0

    def record(
        item: ProviderItem,
        enriched: EnrichOutput | None,
        output: dict[str, Any],
        error: str | None,
        latency_ms: int,
    ) -> None:
        nonlocal errors, processed
        errors += 1 if error else 0
        _insert_evaluation(conn, item, selected_provider, selected_ruleset, enriched, output, error, latency_ms)
        processed += 1
        conn.commit()
        if progress_callback:
            progress_callback(
                EnrichProgress(
                    total=total,
                    completed=processed,
                    errors=errors,
                    item_id=item.id,
                    error=error,
                    latency_ms=latency_ms,
                )
            )

    if selected_workers == 1:
        for item in items:
            enriched, output, error, latency_ms = _evaluate_item(selected_provider, item)
            record(item, enriched, output, error, latency_ms)
        return EnrichRunSummary(processed=processed, errors=errors)

    with ThreadPoolExecutor(max_workers=selected_workers) as executor:
        future_to_item = {executor.submit(_evaluate_item, selected_provider, item): item for item in items}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            enriched, output, error, latency_ms = future.result()
            record(item, enriched, output, error, latency_ms)
    return EnrichRunSummary(processed=processed, errors=errors)
