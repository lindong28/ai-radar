from __future__ import annotations

import sqlite3
from typing import Any
from urllib.parse import urlencode

import nh3
from fastapi import APIRouter, HTTPException, Query, Request
from markdown_it import MarkdownIt

from ...presentation.media import proxy_image_url
from ...presentation.summary import json_loads
from ...wechat_text import normalize_wechat_title
from ..envelope import ok
from .pagination import clamp_page
from .request_db import conn_from_request
from .search import like_patterns_for_query, whitespace_insensitive_sql

router = APIRouter()

_MARKDOWN = MarkdownIt("commonmark", {"breaks": True, "html": True}).enable("table")
_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "code": {"class"}}


def render_summary_html(summary_md: str) -> str:
    rendered = _MARKDOWN.render(summary_md or "")
    return nh3.clean(
        rendered,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        clean_content_tags={"script", "style"},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )


def _tags(value: str | None) -> list[str]:
    raw = json_loads(value, [])
    if not isinstance(raw, list):
        return []
    return [tag.strip() for tag in raw if isinstance(tag, str) and tag.strip()]


def _detail_url(slug: str, page: int | None = None, q: str | None = None) -> str:
    params: list[tuple[str, object]] = []
    query = (q or "").strip()
    if query:
        params.append(("q", query))
    if page and page > 1:
        params.append(("page", page))
    suffix = f"?{urlencode(params)}" if params else ""
    return f"/wechat/{slug}{suffix}"


def _item_from_row(row: sqlite3.Row, *, page: int | None = None, q: str | None = None) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "title": normalize_wechat_title(row["title"]),
        "abstract": row["abstract"] or "",
        "tags": _tags(row["tags_json"]),
        "author": row["author"] or row["source_name"],
        "avatar_url": proxy_image_url(row["avatar_url"]),
        "published_at": row["published_at"],
        "url": row["url"],
        "detail_url": _detail_url(str(row["slug"]), page, q),
        "recommendation": row["recommendation"],
    }


def _search_sql(q: str | None) -> tuple[str, list[object], str, list[object], str | None]:
    patterns = like_patterns_for_query(q)
    if not patterns:
        return "", [], "", [], None

    search_clauses: list[str] = []
    search_params: list[object] = []
    author_clauses: list[str] = []
    author_params: list[object] = []
    search_fields = [
        whitespace_insensitive_sql("i.title"),
        whitespace_insensitive_sql("i.author"),
        whitespace_insensitive_sql("wi.abstract"),
        whitespace_insensitive_sql("wi.tags_json"),
    ]
    author_field = whitespace_insensitive_sql("i.author")
    for pattern in patterns:
        search_clauses.extend(f"{field} LIKE ? ESCAPE '\\'" for field in search_fields)
        search_params.extend([pattern, pattern, pattern, pattern])
        author_clauses.append(f"{author_field} LIKE ? ESCAPE '\\'")
        author_params.append(pattern)

    where_sql = f" AND ({' OR '.join(search_clauses)})"
    author_match_sql = f"CASE WHEN ({' OR '.join(author_clauses)}) THEN 1 ELSE 0 END"
    query = (q or "").strip()
    return where_sql, search_params, author_match_sql, author_params, query


def list_wechat_items(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    where_sql, search_params, author_match_sql, author_params, query = _search_sql(q)
    total = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM wechat_interpretations wi
        JOIN items i ON i.id=wi.item_id
        WHERE wi.save_decision=1{where_sql}
        """,
        search_params,
    ).fetchone()[0]
    current_page = clamp_page(page=int(page), total=int(total), limit=limit)
    offset = (current_page - 1) * limit
    order_sql = (
        f"{author_match_sql} DESC, i.published_at DESC, i.fetched_at DESC, i.id DESC"
        if query
        else "i.published_at DESC, i.fetched_at DESC, i.id DESC"
    )
    rows = conn.execute(
        f"""
        SELECT wi.slug, wi.recommendation, wi.abstract, wi.tags_json,
               i.title, i.author, i.published_at, i.url,
               s.name AS source_name,
               wa.avatar_url
        FROM wechat_interpretations wi
        JOIN items i ON i.id=wi.item_id
        JOIN sources s ON s.id=i.source_id
        LEFT JOIN wechat_account_avatars wa
          ON COALESCE(s.kind, 'feed')='wechat'
         AND wa.account=i.author
         AND wa.avatar_url IS NOT NULL
        WHERE wi.save_decision=1{where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        (*search_params, *author_params, limit, offset),
    ).fetchall()
    return {
        "items": [_item_from_row(row, page=current_page, q=query) for row in rows],
        "total": total,
        "page": current_page,
        "limit": limit,
    }


def get_wechat_detail(conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT i.id, wi.slug, wi.recommendation, wi.abstract, wi.tags_json, wi.summary_md,
               i.title, i.author, i.published_at, i.url,
               s.name AS source_name,
               wa.avatar_url
        FROM wechat_interpretations wi
        JOIN items i ON i.id=wi.item_id
        JOIN sources s ON s.id=i.source_id
        LEFT JOIN wechat_account_avatars wa
          ON COALESCE(s.kind, 'feed')='wechat'
         AND wa.account=i.author
         AND wa.avatar_url IS NOT NULL
        WHERE wi.slug=? AND wi.save_decision=1
        """,
        (slug,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="wechat interpretation not found")
    item = _item_from_row(row)
    item["id"] = row["id"]
    item["summary_md"] = row["summary_md"]
    item["summary_html"] = render_summary_html(row["summary_md"])
    return item


@router.get("/wechat")
def wechat(
    request: Request,
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, object]:
    with conn_from_request(request) as conn:
        return ok(list_wechat_items(conn, q=q, page=page, limit=limit))
