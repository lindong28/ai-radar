from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..envelope import ok
from .common import (
    CATEGORY_TAGS,
    _visible_reason_from_payload,
    category_filter_clause,
    conn_from_request,
    deduped_item_clause,
    fts_phrase_query,
    item_summary,
    json_loads,
    matches_category,
)

router = APIRouter()


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
    where_clauses: list[str] = []
    search_query = fts_phrase_query(q)
    if search_query:
        where_clauses.append("i.id IN (SELECT item_id FROM items_fts WHERE items_fts MATCH ?)")
        params.append(search_query)
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
    preview_query = q if search_query else None
    with conn_from_request(request) as conn:
        has_prefilter = bool(conn.execute("SELECT 1 FROM item_evaluations WHERE stage='prefilter' LIMIT 1").fetchone())
        if has_prefilter:
            where_clauses.append(
                """
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
            )
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        offset = 0 if cursor else (page - 1) * limit
        query_params = [*params, limit + 1, offset]
        rows = conn.execute(
            f"""
            SELECT i.*, s.name AS source_name, s.tier,
                   s.kind AS source_kind,
                   s.homepage_url AS source_homepage_url,
                   s.icon_url AS source_icon_url,
                   e.numeric_json,
                   c.weighted_score AS curated_weighted_score,
                   c.rank,
                   c.reason_json
            FROM items i
            JOIN sources s ON s.id=i.source_id
            LEFT JOIN item_evaluations e ON e.id = (
              SELECT MAX(latest.id)
              FROM item_evaluations latest
              WHERE latest.item_id=i.id
                AND latest.stage='scoring'
                AND latest.error IS NULL
            )
            LEFT JOIN curated_items c ON c.item_id = i.id
              AND c.run_id = (SELECT id FROM curation_runs ORDER BY created_at DESC LIMIT 1)
            {where}
            ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
            LIMIT ? OFFSET ?
            """,
            query_params,
        ).fetchall()
        items = []
        for row in rows:
            item = item_summary(row, preview_query, conn, include_related=False)
            if row["rank"] is not None:
                item["rank"] = row["rank"]
                item["weighted_score"] = row["curated_weighted_score"]
                item["reason"] = json_loads(row["reason_json"], {})
                visible_reason = _visible_reason_from_payload(item["reason"], row)
                if visible_reason:
                    item["reasoning"] = visible_reason
                    item["why_recommend"] = visible_reason
            if matches_category(item, normalized_category):
                items.append(item)
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM items i
            JOIN sources s ON s.id=i.source_id
            {where}
            """,
            params,
        ).fetchone()[0]
        page_rows = rows[:limit]
        next_cursor = (
            f"{page_rows[-1]['published_at']}|{page_rows[-1]['fetched_at']}|{page_rows[-1]['id']}"
            if len(rows) > limit and page_rows
            else None
        )
        items = items[:limit]
    return ok({"items": items, "next_cursor": next_cursor, "total": total, "page": 1 if cursor else page, "limit": limit})
