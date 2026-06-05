from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from datetime import date as date_cls
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..envelope import ok
from .common import (
    CATEGORY_TAGS,
    _clean_url,
    _urls_in_text,
    category_filter_clause,
    conn_from_request,
    deduped_item_clause,
    item_summary,
    json_loads,
    matches_category,
    parse_enrichment,
    search_id_subquery,
    source_match_expression,
)

router = APIRouter()

_CURATED_TOTAL_CACHE_MAX = 64
_CURATED_TOTAL_CACHE: OrderedDict[tuple[Any, ...], int] = OrderedDict()
_CURATED_TOTAL_CACHE_LOCK = Lock()


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


def _curated_data_version(conn: sqlite3.Connection) -> tuple[Any, ...]:
    db_info = conn.execute("PRAGMA database_list").fetchone()
    row = conn.execute(
        """
        SELECT
          COALESCE((SELECT MAX(run_id) FROM curated_items), '') AS latest_run_id,
          COALESCE((SELECT COUNT(*) FROM curated_items), 0) AS curated_count,
          COALESCE((SELECT MAX(rowid) FROM curated_items), 0) AS max_curated_rowid,
          COALESCE((SELECT MAX(rowid) FROM items), 0) AS max_item_rowid,
          COALESCE((SELECT COUNT(*) FROM items), 0) AS item_count,
          COALESCE((SELECT MAX(id) FROM item_evaluations), 0) AS max_eval_id
        """
    ).fetchone()
    return (db_info["file"] if db_info is not None else "", *tuple(row))


def _latest_curated_join() -> str:
    return """
    JOIN curated_items c
      ON c.item_id=i.id
     AND c.run_id = (
       SELECT MAX(latest_curated.run_id)
       FROM curated_items latest_curated
       WHERE latest_curated.item_id=i.id
    )
    """


def _count_archive_items(conn: sqlite3.Connection, where: str, params: tuple[object, ...]) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM items i
        JOIN sources s ON s.id=i.source_id
        {_latest_curated_join()}
        {where}
        """,
        params,
    ).fetchone()
    return int(row[0] if row is not None else 0)


def _cached_archive_total(
    conn: sqlite3.Connection,
    where: str,
    params: tuple[object, ...],
    signature: tuple[object, ...],
    *,
    cacheable: bool,
) -> int:
    if not cacheable:
        return _count_archive_items(conn, where, params)

    key = (*signature, _curated_data_version(conn))
    with _CURATED_TOTAL_CACHE_LOCK:
        cached = _CURATED_TOTAL_CACHE.get(key)
        if cached is not None:
            _CURATED_TOTAL_CACHE.move_to_end(key)
            return cached

    total = _count_archive_items(conn, where, params)
    with _CURATED_TOTAL_CACHE_LOCK:
        _CURATED_TOTAL_CACHE[key] = total
        _CURATED_TOTAL_CACHE.move_to_end(key)
        while len(_CURATED_TOTAL_CACHE) > _CURATED_TOTAL_CACHE_MAX:
            _CURATED_TOTAL_CACHE.popitem(last=False)
    return total


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


def _archive_where(normalized_category: str | None, q: str | None) -> tuple[str, list[object], str | None]:
    params: list[object] = []
    where_clauses = [deduped_item_clause("i")]
    search_subquery, search_params = search_id_subquery(q)
    if search_subquery:
        where_clauses.append(f"i.id IN ({search_subquery})")
        params.extend(search_params)
    category_clause, category_params = category_filter_clause(normalized_category, "i")
    if category_clause:
        where_clauses.append(category_clause)
        params.extend(category_params)
    return f"WHERE {' AND '.join(where_clauses)}", params, search_subquery


def _latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM curation_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def _related_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "source_name": row["source_name"],
        "source_kind": row["source_kind"],
        "author": row["author"],
        "url": row["url"],
    }


def _batch_related_discussions(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> dict[str, list[dict[str, Any]]]:
    linked_urls_by_id: dict[str, list[str]] = {}
    current_url_by_id: dict[str, str] = {}
    all_linked_urls: set[str] = set()
    for row in rows:
        item_id = row["id"]
        linked_urls = _urls_in_text(row["content_text"] if "content_text" in row.keys() else None)
        linked_urls_by_id[item_id] = linked_urls
        all_linked_urls.update(linked_urls)
        current_url = _clean_url(row["url"])
        if current_url:
            current_url_by_id[item_id] = current_url

    candidates: list[sqlite3.Row] = []
    if all_linked_urls:
        placeholders = ", ".join("?" for _ in all_linked_urls)
        candidates.extend(
            conn.execute(
                f"""
                SELECT i.id, i.url, i.author, i.content_text, i.published_at, i.fetched_at,
                       s.id AS source_id, s.name AS source_name, s.kind AS source_kind
                FROM items i
                JOIN sources s ON s.id=i.source_id
                WHERE lower(rtrim(i.url, '/')) IN ({placeholders})
                ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                """,
                sorted(all_linked_urls),
            ).fetchall()
        )
    current_urls = list(current_url_by_id.values())
    if current_urls:
        reverse_where = " OR ".join("f.content_text LIKE ?" for _ in current_urls)
        candidates.extend(
            conn.execute(
                f"""
                SELECT i.id, i.url, i.author, i.content_text, i.published_at, i.fetched_at,
                       s.id AS source_id, s.name AS source_name, s.kind AS source_kind
                FROM items_fts f
                JOIN items i ON i.id=f.item_id
                JOIN sources s ON s.id=i.source_id
                WHERE {reverse_where}
                ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                """,
                [f"%{url}%" for url in current_urls],
            ).fetchall()
        )
    if not candidates:
        return {}
    candidates.sort(key=lambda row: (row["published_at"], row["fetched_at"], row["id"]), reverse=True)

    by_url: dict[str, list[sqlite3.Row]] = {}
    for candidate in candidates:
        by_url.setdefault(_clean_url(candidate["url"]), []).append(candidate)

    related_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item_id = row["id"]
        current_url = current_url_by_id.get(item_id, "")
        related: list[sqlite3.Row] = []
        for linked_url in linked_urls_by_id.get(item_id, []):
            related.extend(by_url.get(linked_url, []))
        if current_url:
            related.extend(
                candidate
                for candidate in candidates
                if current_url in str(candidate["content_text"] or "").lower()
            )

        seen: set[str] = set()
        payloads: list[dict[str, Any]] = []
        for candidate in related:
            candidate_id = candidate["id"]
            if candidate_id == item_id or candidate_id in seen:
                continue
            seen.add(candidate_id)
            payloads.append(_related_payload(candidate))
            if len(payloads) >= 3:
                break
        related_by_id[item_id] = payloads
    return related_by_id


def _compute_archive_page(
    conn: sqlite3.Connection,
    *,
    page: int,
    limit: int,
    normalized_category: str | None,
    q: str | None,
) -> tuple[list[dict[str, Any]], int, int]:
    where, params, search_subquery = _archive_where(normalized_category, q)
    total = _cached_archive_total(
        conn,
        where,
        tuple(params),
        (normalized_category or "", bool(search_subquery)),
        cacheable=not search_subquery,
    )
    total_pages = max(1, (total + limit - 1) // limit)
    response_page = min(max(page, 1), total_pages)
    offset = (response_page - 1) * limit
    preview_query = q if search_subquery else None
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
    rows = conn.execute(
        f"""
        SELECT i.*, s.name AS source_name, s.tier,
               s.kind AS source_kind,
               s.homepage_url AS source_homepage_url,
               s.icon_url AS source_icon_url,
               wa.avatar_url AS author_avatar_url,
               {search_select}
               c.weighted_score, c.rank, c.reason_json,
               enrich_eval.output_json AS enrich_output_json
        FROM items i
        JOIN sources s ON s.id=i.source_id
        {_latest_curated_join()}
        LEFT JOIN wechat_account_avatars wa
          ON COALESCE(s.kind, 'feed')='wechat'
         AND wa.account=i.author
         AND wa.avatar_url IS NOT NULL
        LEFT JOIN item_evaluations enrich_eval ON enrich_eval.id = (
          SELECT MAX(latest_enrich.id)
          FROM item_evaluations latest_enrich
          WHERE latest_enrich.item_id=i.id
            AND latest_enrich.stage='enrich'
            AND latest_enrich.error IS NULL
        )
        {where}
        {order_by}
        LIMIT ? OFFSET ?
        """,
        [*search_sort_params, *params, limit, offset],
    ).fetchall()
    related_by_id = _batch_related_discussions(conn, rows)
    items: list[dict[str, Any]] = []
    for row in rows:
        item = item_summary(
            row,
            preview_query,
            conn,
            include_related=False,
            enrichment=parse_enrichment(row["enrich_output_json"]),
            enrichment_loaded=True,
        )
        item["related_discussions"] = related_by_id.get(row["id"], [])
        item["weighted_score"] = row["weighted_score"]
        item["rank"] = row["rank"]
        item["reason"] = json_loads(row["reason_json"], {})
        scores = item["reason"].get("scores", {})
        item["scores"] = scores
        if matches_category(item, normalized_category):
            items.append(item)
    return items, total, response_page


def _archive_response_date(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> str | None:
    if items:
        return _shanghai_date(str(items[0].get("published_at") or ""))
    row = conn.execute(
        f"""
        SELECT i.published_at
        FROM items i
        {_latest_curated_join()}
        WHERE {deduped_item_clause('i')}
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
        LIMIT 1
        """
    ).fetchone()
    return _shanghai_date(row["published_at"]) if row is not None else None


def prewarm_curated_archive_total_cache(db_path: object) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        where, params, search_subquery = _archive_where(None, None)
        _cached_archive_total(
            conn,
            where,
            tuple(params),
            ("", bool(search_subquery)),
            cacheable=True,
        )
    finally:
        conn.close()


@router.get("/curated")
def curated(
    request: Request,
    run_id: str | None = None,
    date: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = Query(default=40, ge=1, le=100),
    page: int = Query(default=1, ge=1),
) -> dict[str, object]:
    selected_date = _normalized_date(date)
    normalized_category = category if category in CATEGORY_TAGS else None
    with conn_from_request(request) as conn:
        if run_id or selected_date:
            if run_id:
                run = conn.execute("SELECT * FROM curation_runs WHERE id=?", (run_id,)).fetchone()
            else:
                run = _latest_run(conn)
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

        run = _latest_run(conn)
        if run is None:
            return ok(
                {
                    "run_id": None,
                    "ruleset_version": None,
                    "items": [],
                    "date": None,
                    "count": 0,
                    "total": 0,
                    "page": 1,
                    "limit": limit,
                }
            )
        items, total, response_page = _compute_archive_page(
            conn,
            page=page,
            limit=limit,
            normalized_category=normalized_category,
            q=q,
        )
        response_date = _archive_response_date(conn, items) or str(run["created_at"])[:10]
    return ok(
        {
            "run_id": run["id"],
            "ruleset_version": run["ruleset_version"],
            "items": items,
            "date": response_date,
            "count": len(items),
            "total": total,
            "page": response_page,
            "limit": limit,
        }
    )
