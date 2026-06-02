from __future__ import annotations

import json
import sqlite3
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..envelope import ok
from .common import (
    CATEGORY_TAGS,
    category_filter_clause,
    conn_from_request,
    deduped_item_clause,
    item_summary,
    json_loads,
    matches_category,
    search_id_subquery,
    source_match_expression,
)

router = APIRouter()


def _normalized_date(value: str | None) -> str | None:
    if value is None:
        return None
    today = date_cls.today()
    try:
        parsed = date_cls.fromisoformat(value)
    except ValueError:
        return today.isoformat()
    if parsed > today:
        return today.isoformat()
    return parsed.isoformat()


def _shanghai_date(published_at: str) -> str:
    base = published_at[:19].replace("T", " ")
    dt = datetime.fromisoformat(base)
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d")


def _search_preview(text: str, q: str) -> str:
    needle = q.strip().lower()
    idx = text.lower().find(needle)
    if idx < 0:
        return text[:320]
    start = max(0, idx - 120)
    end = min(len(text), idx + len(needle) + 220)
    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _load_precomputed(
    conn: sqlite3.Connection,
    run_id: str,
    selected_date: str | None,
    category: str | None,
    q: str | None,
) -> list[dict[str, Any]] | None:
    has_precomputed = conn.execute(
        "SELECT 1 FROM curated_items WHERE run_id=? AND summary_json IS NOT NULL LIMIT 1",
        (run_id,),
    ).fetchone()
    if not has_precomputed:
        return None

    search_subquery, search_params = search_id_subquery(q)
    if search_subquery:
        source_match_sql, source_match_params = source_match_expression(q, source_alias="s", item_alias="i")
        where = f"WHERE c.run_id=? AND c.summary_json IS NOT NULL AND i.id IN ({search_subquery})"
        params: list[object] = [*source_match_params, run_id, *search_params]
        if selected_date:
            where += " AND date(datetime(i.published_at, '+08:00')) = ?"
            params.append(selected_date)
        rows = conn.execute(
            f"""
            SELECT c.item_id, c.summary_json, i.content_text,
                   wa.avatar_url AS author_avatar_url,
                   {source_match_sql} AS is_source_match,
                   ROW_NUMBER() OVER (
                     PARTITION BY i.source_id
                     ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                   ) AS intra_source_rank
            FROM curated_items c
            JOIN items i ON i.id=c.item_id
            JOIN sources s ON s.id=i.source_id
            LEFT JOIN wechat_account_avatars wa
              ON COALESCE(s.kind, 'feed')='wechat'
             AND wa.account=i.author
             AND wa.avatar_url IS NOT NULL
            {where}
            ORDER BY is_source_match DESC, intra_source_rank ASC,
                     i.published_at DESC, i.fetched_at DESC, i.id DESC
            """,
            params,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.item_id, c.summary_json, i.content_text, wa.avatar_url AS author_avatar_url "
            "FROM curated_items c "
            "JOIN items i ON i.id=c.item_id "
            "JOIN sources s ON s.id=i.source_id "
            "LEFT JOIN wechat_account_avatars wa "
            "  ON COALESCE(s.kind, 'feed')='wechat' "
            " AND wa.account=i.author "
            " AND wa.avatar_url IS NOT NULL "
            "WHERE c.run_id=? AND c.summary_json IS NOT NULL ORDER BY c.rank",
            (run_id,),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = json.loads(row["summary_json"])
        if row["author_avatar_url"]:
            item["author_avatar_url"] = row["author_avatar_url"]
        if selected_date and _shanghai_date(item.get("published_at", "")) != selected_date:
            continue
        if not matches_category(item, category):
            continue
        if search_subquery and q:
            if row["content_text"]:
                item["content_preview"] = _search_preview(row["content_text"], q)
        items.append(item)

    if not search_subquery:
        items.sort(
            key=lambda x: (x.get("published_at") or "", x.get("fetched_at") or "", x.get("id") or ""),
            reverse=True,
        )
    return items


def _compute_items(
    conn: sqlite3.Connection,
    run: sqlite3.Row,
    selected_date: str | None,
    normalized_category: str | None,
    q: str | None,
) -> list[dict[str, Any]]:
    search_subquery, search_params = search_id_subquery(q)
    where = "WHERE c.run_id=?"
    params: list[object] = [run["id"]]
    where += f" AND {deduped_item_clause('i')}"
    if selected_date:
        where += " AND date(datetime(i.published_at, '+08:00')) = ?"
        params.append(selected_date)
    if search_subquery:
        where += f" AND i.id IN ({search_subquery})"
        params.extend(search_params)
    category_clause, category_params = category_filter_clause(normalized_category, "i")
    if category_clause:
        where += f" AND {category_clause}"
        params.extend(category_params)
    search_select = ""
    order_by = (
        "ORDER BY date(datetime(i.published_at, '+08:00')) DESC, "
        "i.published_at DESC, i.fetched_at DESC, i.id DESC"
    )
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
    rows = conn.execute(
        f"""
        SELECT i.*, s.name AS source_name, s.tier,
               s.kind AS source_kind,
               s.homepage_url AS source_homepage_url,
               s.icon_url AS source_icon_url,
               wa.avatar_url AS author_avatar_url,
               {search_select}
               c.weighted_score, c.rank, c.reason_json
        FROM curated_items c
        JOIN items i ON i.id=c.item_id
        JOIN sources s ON s.id=i.source_id
        LEFT JOIN wechat_account_avatars wa
          ON COALESCE(s.kind, 'feed')='wechat'
         AND wa.account=i.author
         AND wa.avatar_url IS NOT NULL
        {where}
        {order_by}
        """,
        [*search_sort_params, *params],
    ).fetchall()
    preview_query = q if search_subquery else None
    items: list[dict[str, Any]] = []
    for row in rows:
        item = item_summary(row, preview_query, conn)
        item["weighted_score"] = row["weighted_score"]
        item["rank"] = row["rank"]
        item["reason"] = json_loads(row["reason_json"], {})
        scores = item["reason"].get("scores", {})
        item["scores"] = scores
        if matches_category(item, normalized_category):
            items.append(item)
    return items


@router.get("/curated")
def curated(
    request: Request,
    run_id: str | None = None,
    date: str | None = None,
    category: str | None = None,
    q: str | None = None,
) -> dict[str, object]:
    selected_date = _normalized_date(date)
    normalized_category = category if category in CATEGORY_TAGS else None
    with conn_from_request(request) as conn:
        if run_id:
            run = conn.execute("SELECT * FROM curation_runs WHERE id=?", (run_id,)).fetchone()
        else:
            run = conn.execute("SELECT * FROM curation_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if run is None:
            if run_id:
                raise HTTPException(status_code=404, detail="curation run not found")
            return ok({"run_id": None, "ruleset_version": None, "items": [], "date": selected_date, "count": 0})
        items = _load_precomputed(conn, run["id"], selected_date, normalized_category, q)
        if items is None:
            items = _compute_items(conn, run, selected_date, normalized_category, q)
        response_date = selected_date or str(run["created_at"])[:10]
    return ok(
        {
            "run_id": run["id"],
            "ruleset_version": run["ruleset_version"],
            "items": items,
            "date": response_date,
            "count": len(items),
        }
    )
