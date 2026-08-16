from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Query, Request

from ...presentation.summary import (
    _visible_reason_from_payload,
    item_summary,
    json_loads,
    parse_enrichment,
)
from ..envelope import ok
from .categories import (
    CATEGORY_TAGS,
    category_filter_clause,
    deduped_item_clause,
    matches_category,
)
from .pagination import VersionedTotalCache, clamp_page
from .request_db import conn_from_request
from .search import search_id_subquery, source_match_expression

router = APIRouter()

_timeline_total_cache = VersionedTotalCache(maxsize=64)
_PREFILTER_SCORING_CLAUSE = """
EXISTS (
  SELECT 1 FROM item_evaluations pre
  WHERE pre.item_id=i.id
    AND pre.stage='prefilter'
    AND pre.error IS NULL
    AND pre.id = (
      SELECT MAX(latest.id)
      FROM item_evaluations latest
      WHERE latest.item_id=i.id
        AND latest.stage='prefilter'
    )
    AND json_extract(pre.numeric_json, '$.is_ai_related') = 1
)
AND (
  NOT EXISTS (
    SELECT 1 FROM item_evaluations scored_any
    WHERE scored_any.item_id=i.id
      AND scored_any.stage='scoring'
      AND scored_any.error IS NULL
  )
  OR EXISTS (
    SELECT 1 FROM item_evaluations scored
    WHERE scored.item_id=i.id
      AND scored.stage='scoring'
      AND scored.error IS NULL
      AND scored.id = (
        SELECT MAX(latest_score.id)
        FROM item_evaluations latest_score
        WHERE latest_score.item_id=i.id
          AND latest_score.stage='scoring'
          AND latest_score.error IS NULL
      )
      AND json_extract(scored.numeric_json, '$.relevance') >= 6.5
  )
)
"""


def _timeline_data_version(conn: sqlite3.Connection) -> tuple[Any, ...]:
    db_info = conn.execute("PRAGMA database_list").fetchone()
    row = conn.execute(
        """
        SELECT
          COALESCE((SELECT id FROM curation_runs ORDER BY created_at DESC LIMIT 1), '') AS latest_run_id,
          COALESCE((SELECT ruleset_version FROM curation_runs ORDER BY created_at DESC LIMIT 1), '') AS latest_ruleset,
          COALESCE((SELECT created_at FROM curation_runs ORDER BY created_at DESC LIMIT 1), '') AS latest_run_created_at,
          COALESCE((SELECT MAX(rowid) FROM items), 0) AS max_item_rowid,
          COALESCE((SELECT COUNT(*) FROM items), 0) AS item_count,
          COALESCE((SELECT MAX(id) FROM item_evaluations), 0) AS max_eval_id
        """
    ).fetchone()
    return (db_info["file"] if db_info is not None else "", *tuple(row))


def _count_timeline_items(conn: sqlite3.Connection, where: str, params: tuple[object, ...]) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM items i
        JOIN sources s ON s.id=i.source_id
        {where}
        """,
        params,
    ).fetchone()
    return int(row[0] if row is not None else 0)


def _append_where_condition(where: str, condition: str) -> str:
    return f"{where} AND ({condition})" if where else f"WHERE ({condition})"


def _count_timeline_items_with_prefilter(
    conn: sqlite3.Connection,
    where: str,
    params: tuple[object, ...],
) -> int:
    filtered_where = _append_where_condition(where, "scored.id IS NULL OR json_extract(scored.numeric_json, '$.relevance') >= 6.5")
    row = conn.execute(
        f"""
        WITH latest_prefilter AS (
          SELECT item_id, MAX(id) AS id
          FROM item_evaluations
          WHERE stage='prefilter'
          GROUP BY item_id
        ), latest_scoring AS (
          SELECT item_id, MAX(id) AS id
          FROM item_evaluations
          WHERE stage='scoring' AND error IS NULL
          GROUP BY item_id
        )
        SELECT COUNT(*)
        FROM items i
        JOIN sources s ON s.id=i.source_id
        JOIN latest_prefilter lp ON lp.item_id=i.id
        JOIN item_evaluations pre
          ON pre.id=lp.id
         AND pre.error IS NULL
         AND json_extract(pre.numeric_json, '$.is_ai_related') = 1
        LEFT JOIN latest_scoring ls ON ls.item_id=i.id
        LEFT JOIN item_evaluations scored ON scored.id=ls.id
        {filtered_where}
        """,
        params,
    ).fetchone()
    return int(row[0] if row is not None else 0)


def _cached_timeline_total(
    conn: sqlite3.Connection,
    where: str,
    params: tuple[object, ...],
    signature: tuple[object, ...],
    *,
    cacheable: bool,
    use_prefilter_count: bool,
) -> int:
    count_fn = _count_timeline_items_with_prefilter if use_prefilter_count else _count_timeline_items
    version = _timeline_data_version(conn) if cacheable else ()
    return _timeline_total_cache.get_or_compute(
        signature=signature,
        version=version,
        compute=lambda: count_fn(conn, where, params),
        cacheable=cacheable,
    )


def prewarm_timeline_total_cache(db_path: object) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        has_prefilter = bool(conn.execute("SELECT 1 FROM item_evaluations WHERE stage='prefilter' LIMIT 1").fetchone())
        where = f"WHERE {deduped_item_clause('i')}"
        count_fn = _count_timeline_items_with_prefilter if has_prefilter else _count_timeline_items
        _timeline_total_cache.prewarm(
            signature=("", "", False, False, has_prefilter),
            version=_timeline_data_version(conn),
            compute=lambda: count_fn(conn, where, ()),
        )
    finally:
        conn.close()


@router.get("/timeline")
def timeline(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    page: int = Query(default=1, ge=1),
    channel: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> dict[str, object]:
    params: list[object] = []
    where_clauses: list[str] = ["s.enabled=1", "COALESCE(s.kind, 'feed') != 'wechat'"]
    search_subquery, search_params = search_id_subquery(q)
    if search_subquery:
        where_clauses.append(f"i.id IN ({search_subquery})")
        params.extend(search_params)
    elif cursor:
        cursor_parts = cursor.split("|", 2)
        if len(cursor_parts) == 3:
            published_at, fetched_at, item_id = cursor_parts
            where_clauses.append(
                """
            (
              i.published_at < ?
              OR (i.published_at = ? AND i.fetched_at < ?)
              OR (i.published_at = ? AND i.fetched_at = ? AND i.id < ?)
            )
            """
            )
            params.extend([published_at, published_at, fetched_at, published_at, fetched_at, item_id])
        else:
            where_clauses.append("i.published_at < ?")
            params.append(cursor)
    if channel == "x":
        where_clauses.append("s.kind = 'x'")
    elif channel == "news":
        where_clauses.append("COALESCE(s.kind, 'feed') != 'x'")
    elif channel == "firstParty":
        where_clauses.append("COALESCE(s.kind, 'feed') != 'x' AND s.tier = 'T1'")
    normalized_category = category if category in CATEGORY_TAGS else None
    where_clauses.append(deduped_item_clause("i"))
    category_clause, category_params = category_filter_clause(normalized_category, "i")
    if category_clause:
        where_clauses.append(category_clause)
        params.extend(category_params)
    preview_query = q if search_subquery else None
    with conn_from_request(request) as conn:
        has_prefilter = bool(conn.execute("SELECT 1 FROM item_evaluations WHERE stage='prefilter' LIMIT 1").fetchone())
        count_where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        if has_prefilter:
            where_clauses.append(_PREFILTER_SCORING_CLAUSE)
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        offset = 0 if cursor else (page - 1) * limit
        search_select = ""
        order_by = "ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC"
        search_sort_params: list[object] = []
        if search_subquery:
            source_match_sql, search_sort_params = source_match_expression(q, source_alias="s", item_alias="i")
            search_select = f"""
                   {source_match_sql} AS is_source_match,
                   ROW_NUMBER() OVER (
                     PARTITION BY i.source_id
                     ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                   ) AS intra_source_rank,
            """
            order_by = (
                "ORDER BY is_source_match DESC, intra_source_rank ASC, "
                "i.published_at DESC, i.fetched_at DESC, i.id DESC"
            )
        total = _cached_timeline_total(
            conn,
            count_where,
            tuple(params),
            (
                channel or "",
                normalized_category or "",
                bool(search_subquery),
                bool(cursor),
                has_prefilter,
            ),
            cacheable=not search_subquery and not cursor,
            use_prefilter_count=has_prefilter,
        )
        response_page = 1 if cursor else clamp_page(page=page, total=total, limit=limit)
        offset = 0 if cursor else (response_page - 1) * limit
        query_params = [*search_sort_params, *params, limit + 1, offset]
        rows = conn.execute(
            f"""
            SELECT i.*, s.name AS source_name, s.tier,
                   s.kind AS source_kind,
                   s.homepage_url AS source_homepage_url,
                   s.icon_url AS source_icon_url,
                   wa.avatar_url AS author_avatar_url,
                   {search_select}
                   e.numeric_json,
                   enrich_eval.output_json AS enrich_output_json,
                   c.weighted_score AS curated_weighted_score,
                   c.rank,
                   c.reason_json
            FROM items i
            JOIN sources s ON s.id=i.source_id
            LEFT JOIN wechat_account_avatars wa
              ON COALESCE(s.kind, 'feed')='wechat'
             AND wa.account=i.author
             AND wa.avatar_url IS NOT NULL
            LEFT JOIN item_evaluations e ON e.id = (
              SELECT MAX(latest.id)
              FROM item_evaluations latest
              WHERE latest.item_id=i.id
                AND latest.stage='scoring'
                AND latest.error IS NULL
            )
            LEFT JOIN item_evaluations enrich_eval ON enrich_eval.id = (
              SELECT MAX(latest_enrich.id)
              FROM item_evaluations latest_enrich
              WHERE latest_enrich.item_id=i.id
                AND latest_enrich.stage='enrich'
                AND latest_enrich.error IS NULL
            )
            LEFT JOIN curated_items c ON c.item_id = i.id
              AND c.run_id = (SELECT id FROM curation_runs ORDER BY created_at DESC LIMIT 1)
            {where}
            {order_by}
            LIMIT ? OFFSET ?
            """,
            query_params,
        ).fetchall()
        items = []
        for row in rows:
            enrichment = parse_enrichment(row["enrich_output_json"])
            item = item_summary(
                row,
                preview_query,
                conn,
                include_related=False,
                enrichment=enrichment,
                enrichment_loaded=True,
            )
            if row["rank"] is not None:
                item["rank"] = row["rank"]
                item["weighted_score"] = row["curated_weighted_score"]
                item["reason"] = json_loads(row["reason_json"], {})
                visible_reason = _visible_reason_from_payload(item["reason"], row)
                if visible_reason:
                    item["reasoning"] = visible_reason
                    item["why_recommend"] = visible_reason
            item.setdefault("weighted_score", 0.0)
            if matches_category(item, normalized_category):
                items.append(item)
        page_rows = rows[:limit]
        next_cursor = (
            f"{page_rows[-1]['published_at']}|{page_rows[-1]['fetched_at']}|{page_rows[-1]['id']}"
            if len(rows) > limit and page_rows
            else None
        )
        items = items[:limit]
    return ok({"items": items, "next_cursor": next_cursor, "total": total, "page": response_page, "limit": limit})
