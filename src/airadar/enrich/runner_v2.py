from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..provider.base import ProviderItem
from ..provider.base_v2 import EnrichProviderV2, EnrichResultV2
from ..provider.deepseek_v4_flash_v2 import DeepSeekV4FlashEnricherV2
from ..provider.deepseek_v4_pro_v2 import DeepSeekV4ProEnricherV2
from ..ruleset import current_version_v2
from ..stage_common import DETERMINISTIC_ENRICH_ERROR_PREFIXES as _DETERMINISTIC_PREFIXES
from ..stage_common import failed_retry_cutoff as _failed_retry_cutoff
from ..stage_common import insert_evaluation
from ..stage_common import parse_since as _parse_since
from ..stage_common import provider_item_from_row as _to_provider_item
from .normalizers.production_enrich_provider_output_v2 import normalize
from .prompts_v2 import render_enrich_prompt
from .schema_v2 import EnrichOutputV2


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


def _provider_from_env() -> EnrichProviderV2:
    name = os.environ.get("AI_RADAR_ENRICHER", "deepseek_v4_pro")
    if name == "deepseek_v4_flash":
        return DeepSeekV4FlashEnricherV2()
    if name == "deepseek_v4_pro":
        return DeepSeekV4ProEnricherV2()
    raise ValueError(f"unknown AI_RADAR_ENRICHER provider: {name}")


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
    backoff_filter = ""
    if item_ids is None:
        # Explicit ids are operator intent (targeted backfill) and never back off.
        prefix_clauses = " OR ".join("failed.error LIKE ?" for _ in _DETERMINISTIC_PREFIXES)
        backoff_filter = f"""
        AND NOT EXISTS (
          SELECT 1 FROM item_evaluations failed
          WHERE failed.item_id=i.id
            AND failed.stage='enrich'
            AND failed.ruleset_version=?
            AND failed.evaluated_at >= ?
            AND ({prefix_clauses})
        )"""
        params.append(ruleset_version)
        params.append(_failed_retry_cutoff())
        params.extend(f"{prefix}%" for prefix in _DETERMINISTIC_PREFIXES)
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
        {backoff_filter}
      ORDER BY i.fetched_at DESC, i.published_at DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _output_from_result(result: EnrichResultV2, item: ProviderItem) -> dict[str, Any]:
    normalized = normalize(
        {
            "title_zh": result.title_zh,
            "summary_zh": result.summary_zh,
            "why_recommend": result.why_recommend,
            "tags": list(result.tags),
            "primary_category": result.primary_category,
            "is_opinion": result.is_opinion,
        },
        item=item,
    )
    return {
        **normalized,
        "raw": result.raw,
    }


def _evaluate_item(
    provider: EnrichProviderV2, item: ProviderItem
) -> tuple[EnrichOutputV2 | None, dict[str, Any], str | None, int]:
    start = time.monotonic()
    attempts = 0
    last_output: dict[str, Any] = {}
    last_raw: Any = None
    last_error: str | None = None
    while attempts < 2:
        attempts += 1
        try:
            result = provider.enrich(item)
            # Captured before normalisation, which raises on a tag list it will not accept. Without
            # this the rejected rows carry no model output at all, and the one question worth
            # asking about them -- what did the model actually emit -- has no answer on disk.
            last_raw = result.raw
            output = _output_from_result(result, item)
            last_output = output
            enriched = EnrichOutputV2.model_validate({key: value for key, value in output.items() if key != "raw"})
            latency_ms = int((time.monotonic() - start) * 1000)
            return enriched, output, None, latency_ms
        except ValidationError as exc:
            last_error = f"schema validation failed after retry: {exc}" if attempts == 2 else str(exc)
        except (ValueError, TypeError) as exc:
            # Deterministic output rejection (normalizer / vocabulary), not a transport failure.
            last_error = f"output rejected after retry: {exc}" if attempts == 2 else str(exc)
        except Exception as exc:
            last_error = f"enrich failed after retry: {exc}" if attempts == 2 else str(exc)
    latency_ms = int((time.monotonic() - start) * 1000)
    failed = {**last_output, "attempts": attempts}
    if "raw" not in failed and last_raw is not None:
        failed["raw"] = last_raw
    return None, failed, last_error, latency_ms


def _insert_evaluation(
    conn: sqlite3.Connection,
    item: ProviderItem,
    provider: EnrichProviderV2,
    ruleset_version: str,
    enriched: EnrichOutputV2 | None,
    output: dict[str, Any],
    error: str | None,
    latency_ms: int,
) -> None:
    insert_evaluation(
        conn,
        item_id=item.id,
        stage="enrich",
        ruleset_version=ruleset_version,
        model_id=provider.model_id,
        input_data=render_enrich_prompt(item),
        output_data=enriched.model_dump() if enriched else output,
        numeric_data=None,
        latency_ms=latency_ms,
        error=error,
    )


def run_enrich(
    conn: sqlite3.Connection,
    *,
    provider: EnrichProviderV2 | None = None,
    since: str = "24h",
    limit: int | None = None,
    item_ids: Sequence[str] | None = None,
    workers: int = 1,
    progress_callback: Callable[[EnrichProgress], None] | None = None,
) -> EnrichRunSummary:
    selected_provider = provider or _provider_from_env()
    selected_ruleset = current_version_v2()
    rows = _candidate_rows(conn, since, selected_ruleset, limit, item_ids)
    items = [_to_provider_item(row) for row in rows]
    total = len(items)
    selected_workers = max(1, min(workers, total or 1))
    processed = 0
    errors = 0

    def evaluate_with_usage_context(
        item: ProviderItem,
    ) -> tuple[EnrichOutputV2 | None, dict[str, Any], str | None, int]:
        return _evaluate_item(selected_provider, item)

    def record(
        item: ProviderItem,
        enriched: EnrichOutputV2 | None,
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
            enriched, output, error, latency_ms = evaluate_with_usage_context(item)
            record(item, enriched, output, error, latency_ms)
        return EnrichRunSummary(processed=processed, errors=errors)

    with ThreadPoolExecutor(max_workers=selected_workers) as executor:
        future_to_item = {executor.submit(evaluate_with_usage_context, item): item for item in items}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            enriched, output, error, latency_ms = future.result()
            record(item, enriched, output, error, latency_ms)
    return EnrichRunSummary(processed=processed, errors=errors)
