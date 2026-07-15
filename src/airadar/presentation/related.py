from __future__ import annotations

import re
import sqlite3
from typing import Any

URL_RE = re.compile(r"https?://[^\s<>)\"']+")
TERM_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{3,}")
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
