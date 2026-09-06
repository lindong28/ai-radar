from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderItem:
    id: str
    title: str
    url: str
    source_id: str
    tier: str
    author: str | None
    published_at: str
    content_text: str


@dataclass(frozen=True)
class PrefilterResult:
    is_ai_related: bool
    confidence: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoringResult:
    relevance: float
    density: float
    recency: float
    authority: float
    engineering: float
    # Optional, not defaulted to 0.0: the heuristic provider has no notion of it, and a provider
    # that never produced the signal must stay distinguishable from one that scored it zero --
    # a weight fitted over a column silently filled with zeros is fitted on a fiction.
    significance: float | None = None
    reasoning: str = ""
    topics: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnrichResult:
    title_zh: str
    summary_zh: str
    why_recommend: str
    tags: tuple[str, ...]
    raw: dict[str, Any] = field(default_factory=dict)


class PrefilterProvider(Protocol):
    model_id: str

    def is_ai_related(self, item: ProviderItem) -> PrefilterResult: ...

    def smoke_test(self) -> str: ...


class ScoringProvider(Protocol):
    model_id: str

    def score_5d(self, item: ProviderItem) -> ScoringResult: ...

    def smoke_test(self) -> str: ...


class EnrichProvider(Protocol):
    model_id: str

    def enrich(self, item: ProviderItem) -> EnrichResult: ...

    def smoke_test(self) -> str: ...
