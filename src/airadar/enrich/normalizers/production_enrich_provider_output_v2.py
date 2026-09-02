from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ...provider.base import ProviderItem
from .production_enrich_provider_output_v1 import (
    CONTROLLED_VOCABULARY,
    deterministic_tags,
)

NORMALIZER_ID = "production_enrich_provider_output_v2"
AUTHORITY_PATH = "src/airadar/enrich/normalizers/production_enrich_provider_output_v2.py"
TAG_MAP_VERSION = "aihot-radar-tag-map-v2"
AIHOT_TO_RADAR_TAG_MAP: dict[str, str] = {
    "Agent": "智能体",
    "MCP/工具调用": "MCP/工具",
    "arXiv": "论文/研究",
}
RADAR_TO_AIHOT_TAG_MAP: dict[str, str] = {
    radar: aihot for aihot, radar in AIHOT_TO_RADAR_TAG_MAP.items()
}
AIHOT_VOCABULARY_ADDITIONS: tuple[str, ...] = (
    "DeepMind",
    "DeepSeek",
    "Google",
    "Hugging Face",
    "Meta",
    "Microsoft",
    "RAG",
    "xAI",
)
CONTROLLED_VOCABULARY_V2 = (*CONTROLLED_VOCABULARY, *AIHOT_VOCABULARY_ADDITIONS)
_VOCABULARY_SET = frozenset(CONTROLLED_VOCABULARY_V2)


def canonical_tag_map_bytes() -> bytes:
    payload = {
        "schema_version": "aihot-radar-tag-map-v1",
        "version": TAG_MAP_VERSION,
        "aihot_to_radar": AIHOT_TO_RADAR_TAG_MAP,
        "radar_to_aihot": RADAR_TO_AIHOT_TAG_MAP,
        "radar_vocabulary": list(CONTROLLED_VOCABULARY_V2),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


TAG_MAP_SHA256 = hashlib.sha256(canonical_tag_map_bytes()).hexdigest()


def is_in_v2_vocabulary(tag: str) -> bool:
    return tag in _VOCABULARY_SET


def topic_tags_v2(
    enriched_tags: list[str],
    *,
    source_id: str | None = None,
    source_name: str | None = None,
    url: str | None = None,
    title: str | None = None,
    content_text: str | None = None,
    limit: int = 4,
) -> list[str]:
    merged: list[str] = []
    for tag in [
        *enriched_tags,
        *deterministic_tags(
            source_id=source_id,
            source_name=source_name,
            url=url,
            title=title,
            content_text=content_text,
        ),
    ]:
        if tag in _VOCABULARY_SET and tag not in merged:
            merged.append(tag)
    return merged[:limit]


def adapt_raw(value: Mapping[str, object]) -> dict[str, Any]:
    raw_tags = value.get("tags")
    if not isinstance(raw_tags, (list, tuple)):
        raise TypeError("tags must be an array")
    primary_category = value.get("primary_category")
    if not isinstance(primary_category, str):
        raise TypeError("primary_category must be one explicit string")
    is_opinion = value.get("is_opinion")
    if type(is_opinion) is not bool:
        raise TypeError("is_opinion must be a boolean")
    return {
        "title_zh": str(value.get("title_zh", "")),
        "summary_zh": str(value.get("summary_zh", "")),
        "why_recommend": str(value.get("why_recommend", "")),
        "tags": [str(tag) for tag in raw_tags],
        "primary_category": primary_category,
        "is_opinion": is_opinion,
    }


def normalize(value: Mapping[str, object], *, item: ProviderItem) -> dict[str, Any]:
    normalized = adapt_raw(value)
    raw_tags = normalized["tags"]
    if not 2 <= len(raw_tags) <= 4:
        raise ValueError("tags must contain 2-4 provider-selected values")

    mapped_tags = [AIHOT_TO_RADAR_TAG_MAP.get(tag, tag) for tag in raw_tags]
    unknown = [tag for tag in mapped_tags if tag not in _VOCABULARY_SET]
    if unknown:
        raise ValueError(f"tags outside controlled vocabulary: {unknown}")

    merged: list[str] = []
    for tag in [
        *mapped_tags,
        *deterministic_tags(
            source_id=item.source_id,
            url=item.url,
            title=item.title,
            content_text=item.content_text,
        ),
    ]:
        if tag not in merged:
            merged.append(tag)
    if len(merged) < 2:
        raise ValueError("tags must normalize to at least 2 unique controlled values")
    normalized["tags"] = merged[:4]
    return normalized
