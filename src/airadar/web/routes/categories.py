from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


CATEGORY_CONTRACT = {
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

CATEGORY_TAGS = {category: set(rule.include_any) for category, rule in CATEGORY_CONTRACT.items()}


def _latest_enrich_tag_exists_clause(item_alias: str, eval_alias: str, tag_alias: str, condition: str) -> str:
    return f"""
    EXISTS (
      SELECT 1
      FROM item_evaluations {eval_alias}
      JOIN json_each(json_extract({eval_alias}.output_json, '$.tags')) {tag_alias}
      WHERE {eval_alias}.item_id={item_alias}.id
        AND {eval_alias}.stage='enrich'
        AND {eval_alias}.error IS NULL
        AND {eval_alias}.id = (
          SELECT MAX(latest_enrich.id)
          FROM item_evaluations latest_enrich
          WHERE latest_enrich.item_id={item_alias}.id
            AND latest_enrich.stage='enrich'
            AND latest_enrich.error IS NULL
        )
        AND {condition}
    )
    """


def _tag_condition_clause(
    condition: TagCondition,
    item_alias: str,
    alias_prefix: str,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    for index, tag in enumerate(condition.all_of):
        clauses.append(
            _latest_enrich_tag_exists_clause(
                item_alias,
                f"{alias_prefix}_all_{index}_enrich",
                f"{alias_prefix}_all_{index}_tag",
                f"{alias_prefix}_all_{index}_tag.value = ?",
            )
        )
        params.append(tag)
    if condition.none_of:
        placeholders = ", ".join("?" for _ in condition.none_of)
        clauses.append(
            f"NOT {_latest_enrich_tag_exists_clause(item_alias, f'{alias_prefix}_none_enrich', f'{alias_prefix}_none_tag', f'{alias_prefix}_none_tag.value IN ({placeholders})')}"
        )
        params.extend(condition.none_of)
    if condition.any_of:
        placeholders = ", ".join("?" for _ in condition.any_of)
        clauses.append(
            _latest_enrich_tag_exists_clause(
                item_alias,
                f"{alias_prefix}_any_enrich",
                f"{alias_prefix}_any_tag",
                f"{alias_prefix}_any_tag.value IN ({placeholders})",
            )
        )
        params.extend(condition.any_of)
    return " AND ".join(clauses), params


def category_filter_clause(category: str | None, item_alias: str = "i") -> tuple[str, list[object]]:
    if not category:
        return "", []
    rule = CATEGORY_CONTRACT.get(category)
    if rule is None:
        return "", []
    placeholders = ", ".join("?" for _ in rule.include_any)
    clauses = [
        _latest_enrich_tag_exists_clause(
            item_alias,
            "category_enrich",
            "category_tag",
            f"category_tag.value IN ({placeholders})",
        )
    ]
    params: list[object] = list(rule.include_any)
    for index, exclusion in enumerate(rule.exclude_when):
        exclusion_clause, exclusion_params = _tag_condition_clause(
            exclusion,
            item_alias,
            f"category_exclude_{index}",
        )
        clauses.append(f"NOT ({exclusion_clause})")
        params.extend(exclusion_params)
    return f"({' AND '.join(clauses)})", params


def deduped_item_clause(item_alias: str = "i") -> str:
    return f"""
    NOT EXISTS (
      SELECT 1
      FROM items duplicate_item
      WHERE duplicate_item.source_id = {item_alias}.source_id
        AND lower(rtrim(duplicate_item.url, '/')) = lower(rtrim({item_alias}.url, '/'))
        AND (
          duplicate_item.published_at > {item_alias}.published_at
          OR (
            duplicate_item.published_at = {item_alias}.published_at
            AND duplicate_item.fetched_at > {item_alias}.fetched_at
          )
          OR (
            duplicate_item.published_at = {item_alias}.published_at
            AND duplicate_item.fetched_at = {item_alias}.fetched_at
            AND duplicate_item.id > {item_alias}.id
          )
        )
    )
    """


def matches_category(item: dict[str, Any], category: str | None) -> bool:
    if not category:
        return True
    rule = CATEGORY_CONTRACT.get(category)
    if rule is None:
        return True
    tags = item.get("topic_tags")
    if not isinstance(tags, list):
        return False
    tag_set = {tag for tag in tags if isinstance(tag, str)}
    if any(exclusion.matches(tag_set) for exclusion in rule.exclude_when):
        return False
    return any(tag in tag_set for tag in rule.include_any)
