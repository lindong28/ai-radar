from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

type PrimaryCategory = Literal["model", "product", "industry", "paper", "tutorial"]
type ProjectionStatus = Literal["exact", "ambiguous", "unclassified"]
type ProjectionAuthority = Literal[
    "candidate_v2",
    "legacy_v1",
    "none",
    "malformed_candidate_v2",
]

PRIMARY_CATEGORIES: tuple[PrimaryCategory, ...] = (
    "model",
    "product",
    "industry",
    "paper",
    "tutorial",
)

PRIMARY_CATEGORY_SLUGS: dict[PrimaryCategory, str] = {
    "model": "ai-models",
    "product": "ai-products",
    "industry": "industry",
    "paper": "paper",
    "tutorial": "tip",
}
SLUG_PRIMARY_CATEGORIES = {slug: category for category, slug in PRIMARY_CATEGORY_SLUGS.items()}


@dataclass(frozen=True, slots=True)
class TagCondition:
    all_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (self.all_of or self.none_of or self.any_of):
            raise ValueError("TagCondition requires at least one tag predicate")

    def matches(self, tags: set[str]) -> bool:
        return (
            all(tag in tags for tag in self.all_of)
            and all(tag not in tags for tag in self.none_of)
            and (not self.any_of or any(tag in tags for tag in self.any_of))
        )


@dataclass(frozen=True, slots=True)
class CategoryRule:
    include_any: tuple[str, ...]
    exclude_when: tuple[TagCondition, ...] = ()

    def matches(self, tags: set[str]) -> bool:
        if any(exclusion.matches(tags) for exclusion in self.exclude_when):
            return False
        return any(tag in tags for tag in self.include_any)


# Historical v1 records encoded category-like signals only in tags. This map is
# deliberately confined to the explicit legacy projection path; candidate v2
# normalization never calls it.
LEGACY_CATEGORY_CONTRACT: dict[str, CategoryRule] = {
    "ai-models": CategoryRule(
        include_any=("模型发布",),
        exclude_when=(TagCondition(all_of=("教程/实践",)),),
    ),
    "ai-products": CategoryRule(
        include_any=("MCP/工具", "产品更新"),
        exclude_when=(TagCondition(all_of=("模型发布",), none_of=("产品更新",)),),
    ),
    "industry": CategoryRule(include_any=("安全/对齐", "现象/趋势", "行业动态")),
    "paper": CategoryRule(include_any=("论文/研究",)),
    "tip": CategoryRule(
        include_any=("教程/实践", "部署/工程"),
        exclude_when=(
            TagCondition(
                none_of=("教程/实践",),
                any_of=("安全/对齐", "现象/趋势", "行业动态"),
            ),
        ),
    ),
}


class ClassificationProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_category: PrimaryCategory | None
    is_opinion: bool | None
    projection_status: ProjectionStatus
    authority: ProjectionAuthority
    evidence: list[str]


def project_legacy_tags(tags: list[str] | tuple[str, ...]) -> ClassificationProjection:
    tag_set = {tag for tag in tags if isinstance(tag, str)}
    matches = [
        SLUG_PRIMARY_CATEGORIES[slug]
        for slug, rule in LEGACY_CATEGORY_CONTRACT.items()
        if rule.matches(tag_set)
    ]
    evidence = [f"legacy_tag_projection_v1:{category}" for category in matches]
    if len(matches) == 1:
        primary_category: PrimaryCategory | None = matches[0]
        status: ProjectionStatus = "exact"
    elif matches:
        primary_category = None
        status = "ambiguous"
    else:
        primary_category = None
        status = "unclassified"
    return ClassificationProjection(
        primary_category=primary_category,
        is_opinion="大佬观点" in tag_set,
        projection_status=status,
        authority="legacy_v1",
        evidence=evidence,
    )


def classification_projection(value: object) -> ClassificationProjection:
    from .schema import EnrichOutput
    from .schema_v2 import EnrichOutputV2

    if isinstance(value, EnrichOutputV2):
        return ClassificationProjection(
            primary_category=value.primary_category,
            is_opinion=value.is_opinion,
            projection_status="exact",
            authority="candidate_v2",
            evidence=["candidate_authority_v2"],
        )
    if isinstance(value, EnrichOutput):
        return project_legacy_tags(value.tags)
    if isinstance(value, Mapping):
        if "primary_category" in value or "is_opinion" in value:
            try:
                return classification_projection(EnrichOutputV2.model_validate(value))
            except ValueError:
                return ClassificationProjection(
                    primary_category=None,
                    is_opinion=None,
                    projection_status="unclassified",
                    authority="malformed_candidate_v2",
                    evidence=["candidate_authority_v2_invalid"],
                )
        try:
            return classification_projection(EnrichOutput.model_validate(value))
        except ValueError:
            pass
    return ClassificationProjection(
        primary_category=None,
        is_opinion=None,
        projection_status="unclassified",
        authority="none",
        evidence=[],
    )
