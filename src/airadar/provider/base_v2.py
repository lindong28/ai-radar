from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .base import ProviderItem


@dataclass(frozen=True)
class EnrichResultV2:
    title_zh: str
    summary_zh: str
    why_recommend: str
    tags: tuple[str, ...]
    primary_category: str
    is_opinion: bool
    raw: dict[str, Any] = field(default_factory=dict)


class EnrichProviderV2(Protocol):
    model_id: str

    def enrich(self, item: ProviderItem) -> EnrichResultV2: ...

    def smoke_test(self) -> str: ...
