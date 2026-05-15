from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..topics import is_in_vocabulary


class EnrichOutput(BaseModel):
    title_zh: str = Field(min_length=2, max_length=120)
    summary_zh: str = Field(min_length=20, max_length=400)
    why_recommend: str = Field(min_length=20, max_length=90)
    tags: list[str] = Field(min_length=2, max_length=4)

    @field_validator("tags")
    @classmethod
    def tags_must_be_in_vocabulary(cls, value: list[str]) -> list[str]:
        unknown = [tag for tag in value if not is_in_vocabulary(tag)]
        if unknown:
            raise ValueError(f"tags outside controlled vocabulary: {unknown}")
        return value
