from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from ...provider.base import ProviderItem

NORMALIZER_ID = "production_enrich_provider_output_v1"
AUTHORITY_PATH = "src/airadar/enrich/normalizers/production_enrich_provider_output_v1.py"

CONTROLLED_VOCABULARY: tuple[str, ...] = (
    "智能体",
    "具身智能",
    "产品更新",
    "OpenAI",
    "图像生成",
    "推理",
    "教程/实践",
    "行业动态",
    "Anthropic",
    "MCP/工具",
    "开源生态",
    "部署/工程",
    "大佬观点",
    "开源/仓库",
    "GitHub",
    "安全/对齐",
    "模型发布",
    "评测/基准",
    "编码",
    "视频",
    "现象/趋势",
    "端侧",
    "arXiv",
    "多模态",
    "搜索",
    "数据/训练",
    "论文/研究",
)
_VOCABULARY_SET = frozenset(CONTROLLED_VOCABULARY)
_WORD_RE = re.compile(r"[a-z0-9_.-]+")


def is_in_vocabulary(tag: str) -> bool:
    return tag in _VOCABULARY_SET


def _text_parts(*values: str | None) -> set[str]:
    text = " ".join(value or "" for value in values).lower()
    return set(_WORD_RE.findall(text))


def deterministic_tags(
    *,
    source_id: str | None = None,
    source_name: str | None = None,
    url: str | None = None,
    title: str | None = None,
    content_text: str | None = None,
) -> list[str]:
    tags: list[str] = []
    try:
        parsed = urlsplit(url or "")
    except ValueError:
        parsed = urlsplit("")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    parts = _text_parts(source_id, source_name, host, path, title, content_text[:800] if content_text else None)
    joined = " ".join(sorted(parts))

    def add(tag: str) -> None:
        if tag in _VOCABULARY_SET and tag not in tags:
            tags.append(tag)

    if "openai" in joined or "chatgpt" in joined:
        add("OpenAI")
    if "anthropic" in joined or "claude" in joined:
        add("Anthropic")
    if host == "github.com" or "github" in parts:
        add("GitHub")
    if host.endswith("arxiv.org") or "arxiv" in parts:
        add("arXiv")
    return tags


def topic_tags(
    enriched_tags: list[str],
    *,
    source_id: str | None = None,
    source_name: str | None = None,
    url: str | None = None,
    title: str | None = None,
    content_text: str | None = None,
    limit: int = 4,
) -> list[str]:
    deterministic = deterministic_tags(
        source_id=source_id,
        source_name=source_name,
        url=url,
        title=title,
        content_text=content_text,
    )
    merged: list[str] = []
    for tag in [*deterministic, *enriched_tags]:
        if tag in _VOCABULARY_SET and tag not in merged:
            merged.append(tag)
    return merged[:limit]


def adapt_raw(value: Mapping[str, object]) -> dict[str, Any]:
    """Mirror the production provider JSON-to-result coercion exactly."""
    raw_tags = value.get("tags", [])
    return {
        "title_zh": str(value.get("title_zh", "")),
        "summary_zh": str(value.get("summary_zh", "")),
        "why_recommend": str(value.get("why_recommend", "")),
        "tags": [str(tag) for tag in raw_tags],  # type: ignore[attr-defined]
    }


def normalize(value: Mapping[str, object], *, item: ProviderItem) -> dict[str, Any]:
    """Apply the immutable v1 production provider and tag normalization."""
    normalized = adapt_raw(value)
    provider_tags = normalized["tags"]
    tags = topic_tags(
        provider_tags,
        source_id=item.source_id,
        url=item.url,
        title=item.title,
        content_text=item.content_text,
    )
    for fallback in ("行业动态", "模型发布", "教程/实践"):
        if len(tags) >= 2:
            break
        if fallback not in tags:
            tags.append(fallback)
    normalized["tags"] = tags[:4]
    return normalized
