from __future__ import annotations

import re
from functools import lru_cache

from opencc import OpenCC

SEARCH_WHITESPACE_RE = re.compile(r"[\s\u3000]+")
SQL_SEARCH_WHITESPACE_REMOVALS = ("' '", "char(12288)", "char(9)", "char(10)", "char(13)")


def fts_phrase_query(value: str | None) -> str | None:
    if value is None:
        return None
    query = value.strip().replace('"', "").strip()
    if not query:
        return None
    return f'"{query}"'


def remove_search_whitespace(value: str | None) -> str:
    return SEARCH_WHITESPACE_RE.sub("", (value or "").strip())


def whitespace_insensitive_sql(expr: str) -> str:
    sql = f"COALESCE({expr}, '')"
    for removal in SQL_SEARCH_WHITESPACE_REMOVALS:
        sql = f"REPLACE({sql}, {removal}, '')"
    return sql


def expand_st_variants(q: str | None, *, remove_whitespace: bool = False) -> list[str]:
    query = (q or "").strip()
    if remove_whitespace:
        query = remove_search_whitespace(query)
    if not query:
        return []
    variants = [
        query,
        _opencc_converter("s2t").convert(query),
        _opencc_converter("t2s").convert(query),
    ]
    return list(dict.fromkeys(variant for variant in variants if variant))


@lru_cache(maxsize=2)
def _opencc_converter(config: str) -> OpenCC:
    return OpenCC(config)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_patterns_for_query(q: str | None) -> list[str]:
    return [f"%{escape_like(variant)}%" for variant in expand_st_variants(q, remove_whitespace=True)]


def source_match_expression(q: str | None, *, source_alias: str = "s", item_alias: str = "i") -> tuple[str, list[object]]:
    patterns = like_patterns_for_query(q)
    if not patterns:
        return "0", []
    clauses: list[str] = []
    params: list[object] = []
    for pattern in patterns:
        clauses.append(f"{whitespace_insensitive_sql(f'{source_alias}.name')} LIKE ? ESCAPE '\\'")
        params.append(pattern)
        clauses.append(f"{whitespace_insensitive_sql(f'{item_alias}.author')} LIKE ? ESCAPE '\\'")
        params.append(pattern)
    return f"CASE WHEN ({' OR '.join(clauses)}) THEN 1 ELSE 0 END", params


def _normalized_like_subquery_from_fts(patterns: list[str]) -> tuple[str | None, list[str]]:
    if not patterns:
        return None, []
    field_exprs = [
        whitespace_insensitive_sql("title"),
        whitespace_insensitive_sql("content_text"),
        whitespace_insensitive_sql("source_name"),
        whitespace_insensitive_sql("author"),
        whitespace_insensitive_sql("title_zh"),
    ]
    clauses: list[str] = []
    params: list[str] = []
    for pattern in patterns:
        for expr in field_exprs:
            clauses.append(f"{expr} LIKE ? ESCAPE '\\'")
            params.append(pattern)
    return f"SELECT item_id FROM items_fts WHERE {' OR '.join(clauses)}", params


def search_id_subquery(q: str | None) -> tuple[str | None, list[str]]:
    qs = (q or "").strip()
    if not qs:
        return None, []
    if len(qs) >= 3:
        fts_query = " OR ".join(phrase for variant in expand_st_variants(qs) if (phrase := fts_phrase_query(variant)))
        normalized_qs = remove_search_whitespace(qs)
        if normalized_qs != qs and normalized_qs:
            like_subquery, like_params = _normalized_like_subquery_from_fts(like_patterns_for_query(qs))
            if like_subquery:
                return f"SELECT item_id FROM items_fts WHERE items_fts MATCH ? UNION {like_subquery}", [
                    fts_query,
                    *like_params,
                ]
        return "SELECT item_id FROM items_fts WHERE items_fts MATCH ?", [fts_query]

    like_patterns = like_patterns_for_query(qs)
    field_exprs = [
        whitespace_insensitive_sql("i2.title"),
        whitespace_insensitive_sql("i2.author"),
        whitespace_insensitive_sql("s2.name"),
        whitespace_insensitive_sql("COALESCE(json_extract(e2.output_json, '$.title_zh'), '')"),
    ]
    subquery = (
        "SELECT i2.id FROM items i2 "
        "JOIN sources s2 ON s2.id = i2.source_id "
        "LEFT JOIN item_evaluations e2 ON e2.id = ("
        "  SELECT MAX(le.id) FROM item_evaluations le "
        "  WHERE le.item_id = i2.id AND le.stage = 'enrich' AND le.error IS NULL"
        ") "
        "WHERE s2.enabled=1 AND COALESCE(s2.kind, 'feed') != 'wechat' AND ("
        + " OR ".join(f"{expr} LIKE ? ESCAPE '\\'" for _pattern in like_patterns for expr in field_exprs)
        + ")"
    )
    params = [pattern for pattern in like_patterns for _ in range(4)]
    return subquery, params
