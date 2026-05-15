from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from .base import PrefilterResult, ProviderItem, ScoringResult

AI_TERMS = (
    "ai",
    "agent",
    "anthropic",
    "benchmark",
    "chatgpt",
    "claude",
    "deepseek",
    "embedding",
    "eval",
    "gpt",
    "inference",
    "llm",
    "model",
    "openai",
    "prompt",
    "rag",
    "transformer",
)

ENGINEERING_TERMS = ("api", "code", "benchmark", "eval", "tool", "sdk", "cli", "github", "repo", "deploy")
RESEARCH_TERMS = ("paper", "arxiv", "research", "benchmark", "frontiermath", "evaluation")
PRODUCT_TERMS = ("launch", "release", "update", "announces", "introduces", "beta", "preview")


def _term_count(text: str, terms: tuple[str, ...]) -> int:
    return sum(text.count(term) for term in terms)


def _recency_score(published_at: str) -> float:
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return 6.5
    age_days = max(0.0, (datetime.now(UTC) - published).total_seconds() / 86400)
    if age_days <= 1:
        return 9.2
    if age_days <= 3:
        return 8.4
    if age_days <= 7:
        return 7.4
    if age_days <= 30:
        return 6.2
    return 4.8


def _stable_jitter(item: ProviderItem) -> float:
    digest = hashlib.sha1(f"{item.id}:{item.title}".encode()).hexdigest()
    return (int(digest[:2], 16) / 255) * 0.45


def _reason(item: ProviderItem, tags: list[str], engineering_hits: int, research_hits: int, product_hits: int) -> str:
    title = item.title.strip()
    if len(title) > 34:
        title = f"{title[:34]}..."
    topic = tags[0] if tags else "AI"
    if engineering_hits:
        signal = "包含可落地的工具、API 或评测信号"
    elif research_hits:
        signal = "提供研究或基准层面的新信息"
    elif product_hits:
        signal = "体现产品/模型更新动向"
    else:
        signal = "与当前 AI 生态变化相关"
    return f"{title} 属于{topic}，来自 {item.source_id}；{signal}，适合快速判断是否继续精读。"


def heuristic_prefilter(item: ProviderItem) -> PrefilterResult:
    text = f"{item.title}\n{item.content_text}".lower()
    hits = sum(1 for term in AI_TERMS if term in text)
    is_related = hits > 0
    confidence = min(0.98, 0.35 + hits * 0.12) if is_related else 0.18
    return PrefilterResult(is_ai_related=is_related, confidence=round(confidence, 3), raw={"term_hits": hits})


def heuristic_score(item: ProviderItem) -> ScoringResult:
    text = f"{item.title}\n{item.content_text}".lower()
    hits = sum(1 for term in AI_TERMS if term in text)
    hit_count = _term_count(text, AI_TERMS)
    engineering_hits = _term_count(text, ENGINEERING_TERMS)
    research_hits = _term_count(text, RESEARCH_TERMS)
    product_hits = _term_count(text, PRODUCT_TERMS)
    length = len(item.content_text)
    authority_by_tier = {"T1": 8.8, "T1.5": 7.2, "T2": 6.0}.get(item.tier, 5.0)
    jitter = _stable_jitter(item)
    tags: list[str] = []
    relevance = min(10.0, 4.0 + hits * 0.62 + min(hit_count, 14) * 0.12 + len(tags) * 0.18 + jitter)
    density = min(10.0, 4.4 + min(length, 5000) / 1150 + min(hits, 8) * 0.08 + jitter / 2)
    recency = min(10.0, _recency_score(item.published_at) + jitter / 3)
    engineering = min(10.0, 4.0 + engineering_hits * 0.62 + research_hits * 0.25 + jitter)
    return ScoringResult(
        relevance=round(relevance, 2),
        density=round(density, 2),
        recency=round(recency, 2),
        authority=authority_by_tier,
        engineering=round(engineering, 2),
        reasoning=_reason(item, tags, engineering_hits, research_hits, product_hits),
        topics=tuple(tags),
        raw={
            "provider": "heuristic",
            "term_hits": hits,
            "term_count": hit_count,
            "engineering_hits": engineering_hits,
            "research_hits": research_hits,
            "product_hits": product_hits,
            "content_length": length,
            "topics": tags,
        },
    )
