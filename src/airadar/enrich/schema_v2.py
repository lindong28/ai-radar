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
    tags: list[str] = Field(min_length=2, max_length=4)
    primary_category: PrimaryCategory
    is_opinion: bool = Field(strict=True)

    @field_validator("summary_zh")
    @classmethod
    def summary_must_have_three_to_five_sentences(cls, value: str) -> str:
        terminal_sentences = _SENTENCE_RE.findall(value)
        trailing = _SENTENCE_RE.sub("", value).strip()
        sentence_count = len(terminal_sentences)
        if trailing or not 3 <= sentence_count <= 5:
            raise ValueError("summary_zh must contain 3 to 5 sentences")
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
