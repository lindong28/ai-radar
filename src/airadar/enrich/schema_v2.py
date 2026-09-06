from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .classification import PrimaryCategory
from .normalizers.production_enrich_provider_output_v2 import is_in_v2_vocabulary

_SENTENCE_RE = re.compile(r"[^。！？!?]+[。！？!?]+")
_GENERIC_REASON_PREFIXES = ("如果你是", "适合", "必读", "必看", "推荐给")
_GENERIC_REASON_EXACT_TEMPLATES = {
    "这是行业发展的关键一步，值得关注，对相关从业者具有直接参考价值和重要启发意义。",
}


class EnrichOutputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_zh: str = Field(min_length=2, max_length=120)
    summary_zh: str = Field(min_length=20, max_length=400)
    why_recommend: str = Field(min_length=35, max_length=90)
    # One, not two. The floor was our own convention, and it was costing whole enrichments:
    # 81 of 2741 items on the 2026-09-06 full run were rejected outright -- summary, reason and
    # category discarded -- because only a single controlled tag survived, and 52 of those had
    # been given exactly one tag by the model in the first place. AIHOT, the thing this output is
    # being fitted to, leaves 2157 of the same 2741 items with no tags at all, so requiring two
    # was never fidelity to it. Widening also keeps every stored row readable.
    tags: list[str] = Field(min_length=1, max_length=4)
    primary_category: PrimaryCategory
    is_opinion: bool = Field(strict=True)

    @field_validator("summary_zh")
    @classmethod
    def summary_must_be_one_to_five_sentences(cls, value: str) -> str:
        # AIHOT writes 1-2 sentences for 74% of items, median 131 characters; the old floor of
        # three forced padding on exactly the thin sources where there is nothing to add, and
        # the padding was usually an explanation of why the source is thin. Widening the window
        # is read-safe: every stored 3-5 sentence summary still validates.
        terminal_sentences = _SENTENCE_RE.findall(value)
        trailing = _SENTENCE_RE.sub("", value).strip()
        sentence_count = len(terminal_sentences)
        if trailing or not 1 <= sentence_count <= 5:
            raise ValueError("summary_zh must contain 1 to 5 sentences")
        return value

    @field_validator("why_recommend")
    @classmethod
    def reason_must_not_be_generic_template(cls, value: str) -> str:
        compact = "".join(value.split())
        if compact.startswith(_GENERIC_REASON_PREFIXES) or compact in _GENERIC_REASON_EXACT_TEMPLATES:
            raise ValueError("why_recommend uses a generic recommendation template")
        return value

    @field_validator("tags")
    @classmethod
    def tags_must_be_known_and_unique(cls, value: list[str]) -> list[str]:
        unknown = [tag for tag in value if not is_in_v2_vocabulary(tag)]
        if unknown:
            raise ValueError(f"tags outside v2 controlled vocabulary: {unknown}")
        if len(set(value)) != len(value):
            raise ValueError("tags must be unique")
        return value
