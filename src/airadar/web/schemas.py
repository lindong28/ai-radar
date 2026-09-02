from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedItem(_ResponseModel):
    id: str
    source_id: str
    source_name: str
    source_kind: str
    source_homepage_url: str | None
    source_icon_url: str | None
    author_avatar_url: str | None = None
    tier: str
    url: str
    title: str
    title_zh: str | None
    author: str | None
    published_at: str
    fetched_at: str
    content_preview: str | None
    summary_zh: str | None
    why_recommend: str | None = Field(json_schema_extra={"preload": False})
    enriched_tags: list[str]
    topic_tags: list[str]
    primary_category: Literal["model", "product", "industry", "paper", "tutorial"] | None = None
    is_opinion: bool | None = None
    classification_projection_status: Literal["exact", "ambiguous", "unclassified"] = "unclassified"
    classification_projection_authority: Literal[
        "candidate_v2", "legacy_v1", "none", "malformed_candidate_v2"
    ] = "none"
    classification_projection_evidence: list[str] = Field(default_factory=list)
    reasoning: str | None
    related_discussions: list[dict[str, Any]]
    media_assets: list[dict[str, Any]]
    content_text: str | None = Field(default=None, json_schema_extra={"preload": False})
    weighted_score: float
    scores: dict[str, Any] | None = Field(default=None, json_schema_extra={"preload": False})
    rank: int | None = None
    reason: dict[str, Any] | None = Field(default=None, json_schema_extra={"preload": False})


class TimelineResponse(_ResponseModel):
    items: list[FeedItem]
    next_cursor: str | None
    total: int
    page: int
    limit: int


class CuratedArchiveResponse(_ResponseModel):
    run_id: str | None
    ruleset_version: str | None
    items: list[FeedItem]
    date: str | None
    count: int
    total: int
    page: int
    limit: int


class DailyMetrics(_ResponseModel):
    events: int
    first_party: int
    new_models: int
    sources: int


class CuratedDigestResponse(_ResponseModel):
    run_id: str | None
    ruleset_version: str | None
    items: list[FeedItem]
    date: str | None
    count: int
    daily_metrics: DailyMetrics | None = None


class WechatItem(_ResponseModel):
    slug: str
    title: str
    abstract: str
    tags: list[str]
    author: str
    avatar_url: str | None
    published_at: str
    url: str
    detail_url: str
    recommendation: str | None


class WechatListResponse(_ResponseModel):
    items: list[WechatItem]
    total: int
    page: int
    limit: int
