from __future__ import annotations

from typing import Any

from ...enrich.classification import (
    LEGACY_CATEGORY_CONTRACT,
    SLUG_PRIMARY_CATEGORIES,
    CategoryRule,
    TagCondition,
)

CATEGORY_CONTRACT = LEGACY_CATEGORY_CONTRACT

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
    target_legacy_clause, target_legacy_params = _legacy_rule_clause(rule, item_alias, "category_target")
    exact_legacy_clauses: list[str] = []
    exact_legacy_params: list[object] = []
    for index, legacy_rule in enumerate(CATEGORY_CONTRACT.values()):
        legacy_clause, legacy_params = _legacy_rule_clause(legacy_rule, item_alias, f"category_exact_{index}")
        exact_legacy_clauses.append(f"CASE WHEN ({legacy_clause}) THEN 1 ELSE 0 END")
        exact_legacy_params.extend(legacy_params)
    output = _latest_enrich_output_clause(item_alias)
    primary_category = SLUG_PRIMARY_CATEGORIES[category]
    clause = f"""
    (
      (json_type({output}, '$.primary_category') = 'text'
       AND json_extract({output}, '$.primary_category') = ?)
      OR
      (json_type({output}, '$.primary_category') IS NULL
       AND json_type({output}, '$.is_opinion') IS NULL
       AND {_legacy_output_clause(output)}
       AND ({target_legacy_clause})
       AND ({' + '.join(exact_legacy_clauses)}) = 1)
    )
    """
    return clause, [primary_category, *target_legacy_params, *exact_legacy_params]


def _latest_enrich_output_clause(item_alias: str) -> str:
    return f"""
    (
      SELECT latest_enrich_output.output_json
      FROM item_evaluations latest_enrich_output
      WHERE latest_enrich_output.item_id={item_alias}.id
        AND latest_enrich_output.stage='enrich'
        AND latest_enrich_output.error IS NULL
      ORDER BY latest_enrich_output.id DESC
      LIMIT 1
    )
    """


def _legacy_output_clause(output: str) -> str:
    return f"""
    json_type({output}, '$.title_zh') = 'text'
    AND json_type({output}, '$.summary_zh') = 'text'
    AND json_type({output}, '$.why_recommend') = 'text'
    AND json_type({output}, '$.tags') = 'array'
    """


def _legacy_rule_clause(
    rule: CategoryRule,
    item_alias: str,
    alias_prefix: str,
) -> tuple[str, list[object]]:
    placeholders = ", ".join("?" for _ in rule.include_any)
    clauses = [
        _latest_enrich_tag_exists_clause(
            item_alias,
            f"{alias_prefix}_enrich",
            f"{alias_prefix}_tag",
            f"{alias_prefix}_tag.value IN ({placeholders})",
        )
    ]
    params: list[object] = list(rule.include_any)
    for index, exclusion in enumerate(rule.exclude_when):
        exclusion_clause, exclusion_params = _tag_condition_clause(
            exclusion,
            item_alias,
            f"{alias_prefix}_exclude_{index}",
        )
        clauses.append(f"NOT ({exclusion_clause})")
        params.extend(exclusion_params)
    return f"({' AND '.join(clauses)})", params


def opinion_filter_clause(enabled: bool, item_alias: str = "i") -> tuple[str, list[object]]:
    if not enabled:
        return "", []
    output = _latest_enrich_output_clause(item_alias)
    legacy_opinion = _latest_enrich_tag_exists_clause(
        item_alias,
        "opinion_enrich",
        "opinion_tag",
        "opinion_tag.value = ?",
    )
    return (
        f"""
        (
          (json_type({output}, '$.is_opinion') IN ('true', 'false')
           AND json_extract({output}, '$.is_opinion') = 1)
          OR
          (json_type({output}, '$.is_opinion') IS NULL
           AND json_type({output}, '$.primary_category') IS NULL
           AND {_legacy_output_clause(output)}
           AND {legacy_opinion})
        )
        """,
        ["大佬观点"],
    )


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
    primary_category = SLUG_PRIMARY_CATEGORIES.get(category)
    if primary_category is None:
        return True
    value = item.get("primary_category")
    status = item.get("classification_projection_status")
    authority = item.get("classification_projection_authority")
    if authority in {"candidate_v2", "legacy_v1", "none", "malformed_candidate_v2"}:
        return status == "exact" and value == primary_category
    return False


def matches_opinion(item: dict[str, Any], enabled: bool = True) -> bool:
    if not enabled:
        return True
    value = item.get("is_opinion")
    authority = item.get("classification_projection_authority")
    return authority in {"candidate_v2", "legacy_v1"} and value is True
