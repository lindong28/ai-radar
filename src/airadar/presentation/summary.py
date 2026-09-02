from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..curator.score import weighted_score
from ..curator.weights import DEFAULT_WEIGHTS
from ..enrich.classification import classification_projection
from ..enrich.normalizers.production_enrich_provider_output_v2 import topic_tags_v2
from ..enrich.schema import EnrichOutput
from ..enrich.schema_v2 import EnrichOutputV2
from ..topics import topic_tags
from .media import _visible_media_assets, proxy_image_url
from .related import related_discussions

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _has_cjk(value: str | None) -> bool:
    return bool(CJK_RE.search(value or ""))


def _fallback_visible_reason(row: sqlite3.Row) -> str:
    source = row["source_name"] if "source_name" in row.keys() and row["source_name"] else "该来源"
    return f"{source} 发布的新动态包含 AI 产品、模型或工程信号；摘要可帮助先判断是否需要打开原文深读。"


def _visible_reason_from_payload(payload: dict[str, Any] | None, row: sqlite3.Row) -> str | None:
    if not payload:
        return None
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else payload
    raw_reason = scores.get("reasoning") if isinstance(scores, dict) else None
    if isinstance(raw_reason, str) and _has_cjk(raw_reason):
        return raw_reason
    return _fallback_visible_reason(row)


def content_preview(row: sqlite3.Row, preview_query: str | None = None) -> str | None:
    if "content_text" not in row.keys() or not row["content_text"]:
        return None
    text = row["content_text"]
    query = (preview_query or "").strip().lower()
    if query:
        index = text.lower().find(query)
        if index >= 0:
            start = max(0, index - 120)
            end = min(len(text), index + len(query) + 220)
            prefix = "..." if start else ""
            suffix = "..." if end < len(text) else ""
            return f"{prefix}{text[start:end]}{suffix}"
    return text[:320]


def latest_enrichment(conn: sqlite3.Connection | None, item_id: str) -> EnrichOutput | EnrichOutputV2 | None:
    if conn is None:
        return None
    row = conn.execute(
        """
        SELECT output_json
        FROM item_evaluations
        WHERE item_id=?
          AND stage='enrich'
          AND error IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return parse_enrichment(row["output_json"])


def parse_enrichment(value: str | None) -> EnrichOutput | EnrichOutputV2 | None:
    parsed = json_loads(value, None)
    if parsed is None:
        return None
    try:
        if isinstance(parsed, dict) and ("primary_category" in parsed or "is_opinion" in parsed):
            return EnrichOutputV2.model_validate(parsed)
        return EnrichOutput.model_validate(parsed)
    except ValueError:
        return None


def item_summary(
    row: sqlite3.Row,
    preview_query: str | None = None,
    conn: sqlite3.Connection | None = None,
    include_related: bool = True,
    enrichment: EnrichOutput | EnrichOutputV2 | None = None,
    enrichment_loaded: bool = False,
) -> dict[str, Any]:
    row_keys = row.keys()
    source_kind = row["source_kind"] if "source_kind" in row_keys else "feed"
    preview = None if source_kind == "wechat" else content_preview(row, preview_query)
    enrichment = enrichment if enrichment is not None or enrichment_loaded else latest_enrichment(conn, row["id"])
    classification = classification_projection(enrichment)
    tag_renderer = topic_tags_v2 if isinstance(enrichment, EnrichOutputV2) else topic_tags
    enriched_tags = (
        tag_renderer(
            enrichment.tags,
            source_id=row["source_id"],
            source_name=row["source_name"],
            url=row["url"],
            title=row["title"],
            content_text=row["content_text"] if "content_text" in row_keys else None,
        )
        if enrichment
        else []
    )
    item = {
        "id": row["id"],
        "source_id": row["source_id"],
        "source_name": row["source_name"],
        "source_kind": source_kind,
        "source_homepage_url": row["source_homepage_url"] if "source_homepage_url" in row_keys else None,
        "source_icon_url": proxy_image_url(row["source_icon_url"] if "source_icon_url" in row_keys else None),
        "author_avatar_url": proxy_image_url(row["author_avatar_url"] if "author_avatar_url" in row_keys else None),
        "tier": row["tier"],
        "url": row["url"],
        "title": row["title"],
        "title_zh": enrichment.title_zh if enrichment else row["title"],
        "author": row["author"],
        "published_at": row["published_at"],
        "fetched_at": row["fetched_at"],
        "content_preview": preview,
        "summary_zh": enrichment.summary_zh if enrichment else None,
        "why_recommend": enrichment.why_recommend if enrichment else None,
        "enriched_tags": enriched_tags,
        "topic_tags": enriched_tags,
        "primary_category": classification.primary_category,
        "is_opinion": classification.is_opinion,
        "classification_projection_status": classification.projection_status,
        "classification_projection_authority": classification.authority,
        "classification_projection_evidence": classification.evidence,
        "reasoning": enrichment.why_recommend if enrichment else None,
        "related_discussions": related_discussions(conn, row) if include_related else [],
        "media_assets": _visible_media_assets(row),
    }
    fallback_reason = None
    if "reason_json" in row_keys and row["reason_json"]:
        fallback_reason = _visible_reason_from_payload(json_loads(row["reason_json"], {}), row)
    if item["source_kind"] == "x" and "content_text" in row_keys:
        item["content_text"] = row["content_text"]
    if "numeric_json" in row.keys() and row["numeric_json"]:
        numeric = json_loads(row["numeric_json"], None)
        if numeric:
            item["weighted_score"] = weighted_score(numeric, DEFAULT_WEIGHTS, row["tier"])
            item["scores"] = numeric
            if not item["reasoning"]:
                item["reasoning"] = _visible_reason_from_payload(numeric, row)
    if not item["reasoning"] and fallback_reason:
        item["reasoning"] = fallback_reason
    if item["reasoning"] and not item["why_recommend"]:
        item["why_recommend"] = item["reasoning"]
    return item
