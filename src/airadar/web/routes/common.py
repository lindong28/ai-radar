from __future__ import annotations

import json
import re
import sqlite3
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from fastapi import Request

from ...curator.score import weighted_score
from ...curator.weights import DEFAULT_WEIGHTS
from ...enrich.schema import EnrichOutput
from ...topics import topic_tags

URL_RE = re.compile(r"https?://[^\s<>)\"']+")
TERM_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{3,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
RELATED_STOPWORDS = {
    "https",
    "http",
    "status",
    "about",
    "with",
    "from",
    "this",
    "that",
    "will",
    "have",
    "your",
    "openai",
    "github",
}
CURATED_MEDIA_FULL_RANK_LIMIT = 12
CURATED_MEDIA_PREVIEW_RANK_LIMIT = 13
CATEGORY_TAGS = {
    "ai-models": {"模型发布"},
    "ai-products": {"产品更新", "MCP/工具"},
    "industry": {"行业动态", "安全/对齐", "现象/趋势"},
    "paper": {"论文/研究"},
    "tip": {"教程/实践", "部署/工程"},
}


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


def category_filter_clause(category: str | None, item_alias: str = "i") -> tuple[str, list[object]]:
    if not category:
        return "", []
    wanted = sorted(CATEGORY_TAGS.get(category) or [])
    if not wanted:
        return "", []
    placeholders = ", ".join("?" for _ in wanted)
    clauses = [
        _latest_enrich_tag_exists_clause(
            item_alias,
            "category_enrich",
            "category_tag",
            f"category_tag.value IN ({placeholders})",
        )
    ]
    params: list[object] = list(wanted)
    if category == "ai-models":
        clauses.append(
            f"NOT {_latest_enrich_tag_exists_clause(item_alias, 'model_exclude_enrich', 'model_exclude_tag', 'model_exclude_tag.value = ?')}"
        )
        params.append("教程/实践")
    elif category == "ai-products":
        has_model = _latest_enrich_tag_exists_clause(
            item_alias, "product_model_enrich", "product_model_tag", "product_model_tag.value = ?"
        )
        has_product = _latest_enrich_tag_exists_clause(
            item_alias, "product_update_enrich", "product_update_tag", "product_update_tag.value = ?"
        )
        clauses.append(f"NOT ({has_model} AND NOT {has_product})")
        params.extend(["模型发布", "产品更新"])
    elif category == "tip":
        has_tutorial = _latest_enrich_tag_exists_clause(
            item_alias, "tip_tutorial_enrich", "tip_tutorial_tag", "tip_tutorial_tag.value = ?"
        )
        broad_news = ["安全/对齐", "现象/趋势", "行业动态"]
        broad_placeholders = ", ".join("?" for _ in broad_news)
        has_broad_news = _latest_enrich_tag_exists_clause(
            item_alias,
            "tip_broad_enrich",
            "tip_broad_tag",
            f"tip_broad_tag.value IN ({broad_placeholders})",
        )
        clauses.append(f"NOT (NOT {has_tutorial} AND {has_broad_news})")
        params.extend(["教程/实践", *broad_news])
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
    wanted = CATEGORY_TAGS.get(category)
    if not wanted:
        return True
    tags = item.get("topic_tags")
    if not isinstance(tags, list):
        return False
    if category == "ai-models" and "教程/实践" in tags:
        return False
    if category == "ai-products" and "模型发布" in tags and "产品更新" not in tags:
        return False
    if (
        category == "tip"
        and "教程/实践" not in tags
        and any(tag in {"安全/对齐", "现象/趋势", "行业动态"} for tag in tags)
    ):
        return False
    return any(isinstance(tag, str) and tag in wanted for tag in tags)


class _ImageSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attr_map = {name.lower(): value for name, value in attrs if value}
        src = attr_map.get("src")
        if src:
            self.urls.append(unescape(src.strip()))


def _has_cjk(value: str | None) -> bool:
    return bool(CJK_RE.search(value or ""))


def _fallback_visible_reason(row: sqlite3.Row) -> str:
    source = row["source_name"] if "source_name" in row.keys() and row["source_name"] else "该来源"
    return f"{source} 发布的新动态包含 AI 产品、模型或工程信号；摘要可帮助先判断是否需要打开原文深读。"


def _visible_reason_from_payload(payload: dict[str, Any] | None, row: sqlite3.Row) -> str | None:
    if not payload:
        return None
    scores = payload.get("scores") if isinstance(payload.get("scores"), dict) else payload
    raw_reason = scores.get("reasoning") if isinstance(scores, dict) else None
    if isinstance(raw_reason, str) and _has_cjk(raw_reason):
        return raw_reason
    return _fallback_visible_reason(row)


def _safe_media_url(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _media_assets_from_html(value: str | None) -> list[dict[str, str]]:
    if not value:
        return []
    parser = _ImageSrcParser()
    parser.feed(value)
    assets: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_url in parser.urls:
        url = _safe_media_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        assets.append({"type": "image", "url": url})
    return assets


def _visible_media_assets(row: sqlite3.Row) -> list[dict[str, str]]:
    assets = _media_assets_from_html(row["content_html"] if "content_html" in row.keys() else None)
    if not assets:
        return []
    if "rank" not in row.keys() or row["rank"] is None:
        return assets[:1]
    rank = int(row["rank"])
    if rank <= CURATED_MEDIA_FULL_RANK_LIMIT:
        return assets
    if rank <= CURATED_MEDIA_PREVIEW_RANK_LIMIT:
        return assets[:1]
    return []


def conn_from_request(request: Request) -> sqlite3.Connection:
    conn = sqlite3.connect(request.app.state.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def fts_phrase_query(value: str | None) -> str | None:
    if value is None:
        return None
    query = value.strip().replace('"', "").strip()
    if not query:
        return None
    return f'"{query}"'


def content_preview(row: sqlite3.Row, preview_query: str | None = None) -> str | None:
    if "content_text" not in row.keys() or not row["content_text"]:
        return None
    text = row["content_text"]
    query = (preview_query or "").strip().lower()
    if query:
        index = text.lower().find(query)
        if index >= 0:
            start = max(0, index - 120)
            end = min(len(text), index + len(query) + 220)
            prefix = "..." if start else ""
            suffix = "..." if end < len(text) else ""
            return f"{prefix}{text[start:end]}{suffix}"
    return text[:320]


def latest_enrichment(conn: sqlite3.Connection | None, item_id: str) -> EnrichOutput | None:
    if conn is None:
        return None
    row = conn.execute(
        """
        SELECT output_json
        FROM item_evaluations
        WHERE item_id=?
          AND stage='enrich'
          AND error IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return parse_enrichment(row["output_json"])


def parse_enrichment(value: str | None) -> EnrichOutput | None:
    parsed = json_loads(value, None)
    if parsed is None:
        return None
    try:
        return EnrichOutput.model_validate(parsed)
    except ValueError:
        return None


def _clean_url(value: str) -> str:
    return value.strip().rstrip(".,;:!?)»”'\"").rstrip("/").lower()


def _urls_in_text(value: str | None) -> list[str]:
    if not value:
        return []
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_RE.findall(value):
        cleaned = _clean_url(match)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def _important_terms(*values: str | None) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        for raw in TERM_RE.findall(value or ""):
            term = raw.lower().strip("._-")
            if len(term) < 4 or term in RELATED_STOPWORDS or term in seen:
                continue
            seen.add(term)
            terms.append(term)
    return terms[:8]


def _related_rows_from_terms(conn: sqlite3.Connection, row: sqlite3.Row) -> list[sqlite3.Row]:
    terms = _important_terms(row["title"], row["content_text"][:500] if "content_text" in row.keys() else None)
    if len(terms) < 2:
        return []
    clauses = " OR ".join("lower(i.title || ' ' || i.content_text) LIKE ?" for _ in terms)
    params: list[object] = [row["id"], row["source_id"], *(f"%{term}%" for term in terms)]
    candidates = conn.execute(
        f"""
        SELECT i.id, i.url, i.author, i.title, i.content_text,
               s.id AS source_id, s.name AS source_name, s.kind AS source_kind
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE i.id != ?
          AND i.source_id != ?
          AND ({clauses})
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
        LIMIT 20
        """,
        params,
    ).fetchall()
    scored: list[tuple[int, sqlite3.Row]] = []
    for candidate in candidates:
        haystack = f"{candidate['title']} {candidate['content_text']}".lower()
        score = sum(1 for term in terms if term in haystack)
        if score >= 2:
            scored.append((score, candidate))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["source_id"], pair[1]["id"]))
    return [candidate for _, candidate in scored[:3]]


def related_discussions(conn: sqlite3.Connection | None, row: sqlite3.Row) -> list[dict[str, Any]]:
    if conn is None:
        return []
    current_url = _clean_url(row["url"])
    linked_urls = _urls_in_text(row["content_text"] if "content_text" in row.keys() else None)
    clauses = ["i.id != ?"]
    params: list[object] = [row["id"]]
    related_conditions: list[str] = []
    if linked_urls:
        placeholders = ", ".join("?" for _ in linked_urls)
        related_conditions.append(f"lower(rtrim(i.url, '/')) IN ({placeholders})")
        params.extend(linked_urls)
    if current_url:
        related_conditions.append("lower(i.content_text) LIKE ?")
        params.append(f"%{current_url}%")
    if not related_conditions:
        return []
    clauses.append(f"({' OR '.join(related_conditions)})")
    rows = conn.execute(
        f"""
        SELECT i.id, i.url, i.author, s.id AS source_id, s.name AS source_name, s.kind AS source_kind
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE {" AND ".join(clauses)}
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
        LIMIT 3
        """,
        params,
    ).fetchall()
    if not rows:
        rows = _related_rows_from_terms(conn, row)
    return [
        {
            "source_id": related["source_id"],
            "source_name": related["source_name"],
            "source_kind": related["source_kind"],
            "author": related["author"],
            "url": related["url"],
        }
        for related in rows
    ]


def item_summary(
    row: sqlite3.Row,
    preview_query: str | None = None,
    conn: sqlite3.Connection | None = None,
    include_related: bool = True,
    enrichment: EnrichOutput | None = None,
    enrichment_loaded: bool = False,
) -> dict[str, Any]:
    preview = content_preview(row, preview_query)
    row_keys = row.keys()
    enrichment = enrichment if enrichment is not None or enrichment_loaded else latest_enrichment(conn, row["id"])
    enriched_tags = (
        topic_tags(
            enrichment.tags,
            source_id=row["source_id"],
            source_name=row["source_name"],
            url=row["url"],
            title=row["title"],
            content_text=row["content_text"] if "content_text" in row_keys else None,
        )
        if enrichment
        else []
    )
    item = {
        "id": row["id"],
        "source_id": row["source_id"],
        "source_name": row["source_name"],
        "source_kind": row["source_kind"] if "source_kind" in row_keys else "feed",
        "source_homepage_url": row["source_homepage_url"] if "source_homepage_url" in row_keys else None,
        "source_icon_url": row["source_icon_url"] if "source_icon_url" in row_keys else None,
        "tier": row["tier"],
        "url": row["url"],
        "title": row["title"],
        "title_zh": enrichment.title_zh if enrichment else row["title"],
        "author": row["author"],
        "published_at": row["published_at"],
        "fetched_at": row["fetched_at"],
        "content_preview": preview,
        "summary_zh": enrichment.summary_zh if enrichment else None,
        "why_recommend": enrichment.why_recommend if enrichment else None,
        "enriched_tags": enriched_tags,
        "topic_tags": enriched_tags,
        "reasoning": enrichment.why_recommend if enrichment else None,
        "related_discussions": related_discussions(conn, row) if include_related else [],
        "media_assets": _visible_media_assets(row),
    }
    fallback_reason = None
    if "reason_json" in row_keys and row["reason_json"]:
        fallback_reason = _visible_reason_from_payload(json_loads(row["reason_json"], {}), row)
    if item["source_kind"] == "x" and "content_text" in row_keys:
        item["content_text"] = row["content_text"]
    if "numeric_json" in row.keys() and row["numeric_json"]:
        numeric = json_loads(row["numeric_json"], None)
        if numeric:
            item["weighted_score"] = weighted_score(numeric, DEFAULT_WEIGHTS, row["tier"])
            item["scores"] = numeric
            if not item["reasoning"]:
                item["reasoning"] = _visible_reason_from_payload(numeric, row)
    if not item["reasoning"] and fallback_reason:
        item["reasoning"] = fallback_reason
    if item["reasoning"] and not item["why_recommend"]:
        item["why_recommend"] = item["reasoning"]
    return item
