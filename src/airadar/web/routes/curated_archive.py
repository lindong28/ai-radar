from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ...presentation.related import _batch_related_discussions
from ...presentation.summary import item_summary, json_loads, parse_enrichment
from .categories import category_filter_clause, deduped_item_clause
from .pagination import VersionedTotalCache, clamp_page
from .search import search_id_subquery, source_match_expression

_curated_total_cache = VersionedTotalCache(maxsize=64)


def _shanghai_date(published_at: str) -> str:
    base = published_at[:19].replace("T", " ")
    dt = datetime.fromisoformat(base)
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d")


def _curated_data_version(
    conn: sqlite3.Connection,
    *,
    include_enrichment: bool = False,
) -> tuple[Any, ...]:
    db_info = conn.execute("PRAGMA database_list").fetchone()
    row = conn.execute(
        """
        SELECT archive_generation, category_generation
        FROM archive_cache_generations
        WHERE id=1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("archive cache generation row is missing")
    version = (db_info["file"] if db_info is not None else "", int(row[0]))
    if not include_enrichment:
        return version
    return (*version, int(row[1]))


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
    include_enrichment: bool,
) -> int:
    version = (
        _curated_data_version(conn, include_enrichment=include_enrichment)
        if cacheable
        else ()
    )
    return _curated_total_cache.get_or_compute(
        signature=signature,
        version=version,
        compute=lambda: _count_archive_items(conn, where, params),
        cacheable=cacheable,
    )


def _archive_where(
    normalized_category: str | None,
    q: str | None,
    selected_date: str | None = None,
) -> tuple[str, list[object], str | None]:
    params: list[object] = []
    where_clauses = ["s.enabled=1", "COALESCE(s.kind, 'feed') != 'wechat'", deduped_item_clause("i")]
    search_subquery, search_params = search_id_subquery(q)
    if search_subquery:
        where_clauses.append(f"i.id IN ({search_subquery})")
        params.extend(search_params)
    if selected_date:
        where_clauses.append("date(datetime(i.published_at, '+08:00')) = ?")
        params.append(selected_date)
    category_clause, category_params = category_filter_clause(normalized_category, "i")
    if category_clause:
        where_clauses.append(category_clause)
        params.extend(category_params)
    return f"WHERE {' AND '.join(where_clauses)}", params, search_subquery


# Only three columns are ever read off this row (`id`, `ruleset_version`, and
# the date prefix of `created_at`). `SELECT *` also pulled input_eval_ids and
# output_curated_ids -- two wide TEXT columns that make curation_runs 688MB at
# 8235 rows -- costing 0.349s per call on the production origin for values no
# caller touches.
#
# `id DESC` is not decoration: created_at alone is not a total order, and this
# function and `_timeline_data_version` would otherwise be free to name
# different rows as "the latest run". It also matches idx_curation_runs_created_at.
_LATEST_RUN_SQL = """
SELECT id, ruleset_version, created_at
FROM curation_runs
ORDER BY created_at DESC, id DESC
LIMIT 1
"""


def _latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(_LATEST_RUN_SQL).fetchone()


def _compute_archive_page(
    conn: sqlite3.Connection,
    *,
    page: int,
    limit: int,
    normalized_category: str | None,
    q: str | None,
) -> tuple[list[dict[str, Any]], int, int]:
    started_read_transaction = not conn.in_transaction
    if started_read_transaction:
        conn.execute("BEGIN")
    try:
        where, params, search_subquery = _archive_where(normalized_category, q)
        total = _cached_archive_total(
            conn,
            where,
            tuple(params),
            (normalized_category or "", bool(search_subquery)),
            cacheable=not search_subquery,
            include_enrichment=normalized_category is not None,
        )
        response_page = clamp_page(page=page, total=total, limit=limit)
        offset = (response_page - 1) * limit
        items = _archive_items(
            conn,
            where,
            params,
            search_subquery,
            q=q,
            normalized_category=normalized_category,
            limit=limit,
            offset=offset,
        )
        return items, total, response_page
    finally:
        if started_read_transaction:
            conn.rollback()


def _compute_archive_for_date(
    conn: sqlite3.Connection,
    *,
    selected_date: str,
    normalized_category: str | None,
    q: str | None,
) -> list[dict[str, Any]]:
    """Curated items published on ``selected_date`` aggregated across all runs.

    Unlike the single-run paths, this dedupes by item to its latest curation, so a
    daily report covers every item ever curated for that day — not only items that
    happen to be in the most recent run.
    """
    where, params, search_subquery = _archive_where(normalized_category, q, selected_date)
    return _archive_items(
        conn,
        where,
        params,
        search_subquery,
        q=q,
        normalized_category=normalized_category,
        limit=-1,
        offset=0,
    )


def _compute_daily_archive(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Return every available daily issue from one SQLite read snapshot."""
    rows = conn.execute(
        f"""
        SELECT date(datetime(i.published_at, '+08:00')) AS issue_date,
               COUNT(*) AS story_count
        FROM items i
        JOIN sources s ON s.id=i.source_id
        {_latest_curated_join()}
        WHERE s.enabled=1
          AND COALESCE(s.kind, 'feed') != 'wechat'
          AND {deduped_item_clause("i")}
          AND date(datetime(i.published_at, '+08:00')) <= date(datetime('now', '+08:00'))
        GROUP BY issue_date
        ORDER BY issue_date DESC
        """
    ).fetchall()
    return [
        {"date": str(row["issue_date"]), "count": int(row["story_count"])}
        for row in rows
        if row["issue_date"]
    ]


def _archive_items(
    conn: sqlite3.Connection,
    where: str,
    params: list[object],
    search_subquery: str | None,
    *,
    q: str | None,
    normalized_category: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
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
        items.append(item)
    return items


def _archive_response_date(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> str | None:
    if items:
        return _shanghai_date(str(items[0].get("published_at") or ""))
    row = conn.execute(
        f"""
        SELECT i.published_at
        FROM items i
        {_latest_curated_join()}
        JOIN sources s ON s.id=i.source_id
        WHERE s.enabled=1
          AND COALESCE(s.kind, 'feed') != 'wechat'
          AND {deduped_item_clause('i')}
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
        _curated_total_cache.prewarm(
            signature=("", bool(search_subquery)),
            version=_curated_data_version(conn),
            compute=lambda: _count_archive_items(conn, where, tuple(params)),
        )
    finally:
        conn.close()
