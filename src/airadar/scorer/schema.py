from __future__ import annotations

from pydantic import BaseModel, Field


class ScoringNumeric(BaseModel):
    relevance: float = Field(ge=0.0, le=10.0)
    density: float = Field(ge=0.0, le=10.0)
    recency: float = Field(ge=0.0, le=10.0)
    authority: float = Field(ge=0.0, le=10.0)
    engineering: float = Field(ge=0.0, le=10.0)
    # Optional because this model validates stored rows as well as fresh provider output, and
    # every row written before this field existed lacks it. Requiring it would make the whole
    # history unreadable rather than merely unscored on this dimension.
    significance: float | None = Field(default=None, ge=0.0, le=10.0)
    reasoning: str = Field(max_length=200)
    topics: list[str] = Field(default_factory=list, max_length=4)
