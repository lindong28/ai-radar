"""Build the aihot-fit evalset: AIHOT reference outputs joined to our ``items`` rows."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from ... import db
from .common import (
    BUILDER_VERSION,
    CATEGORY_SLUG_TO_PRIMARY,
    read_json,
    read_jsonl,
    readonly_db_uri,
    sha256_file,
    sha256_text,
    utc_now,
    write_json,
    write_jsonl,
)

# The AIHOT benchmark repo is this repo's ``benchmarks/aihot`` submodule; point
# AI_RADAR_AIHOT_DATASET_ROOT at another checkout of it when the submodule is not
# initialised here. Every default stays repo-relative so no maintainer path is baked in.
_AIHOT_DATASET_ROOT = Path(os.environ.get("AI_RADAR_AIHOT_DATASET_ROOT") or db.PROJECT_ROOT / "benchmarks" / "aihot")
_T5_RAW_DEFAULT = db.PROJECT_ROOT / ".label-serve" / "round45-human" / "t5" / "r2_raw" / "aihot_items_raw_r2.json"
DEFAULT_SOURCES: tuple[tuple[str, Path], ...] = (
    (
        "t2-window-2026-08-19",
        _AIHOT_DATASET_ROOT / "windows" / "2026-08-19T000000Z--2026-08-20T000000Z" / "items.jsonl",
    ),
    (
        "t2-window-2026-08-20",
        _AIHOT_DATASET_ROOT / "windows" / "2026-08-20T000000Z--2026-08-21T000000Z" / "items.jsonl",
    ),
    ("t5-raw-r2-20260905", Path(os.environ.get("AI_RADAR_AIHOT_T5_RAW") or _T5_RAW_DEFAULT)),
)

_X_STATUS_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/.*?/status(?:es)?/(\d+)",
    re.IGNORECASE,
)


def normalize_url(url: str) -> tuple[str, str]:
    """Return ``(match_key, match_method)`` for an original URL.

    X / Twitter status links collapse onto the status id (our items store them as
    ``https://x.com/i/web/status/<id>``); everything else drops ``www.``, the fragment
    and the trailing slash, lower-cases scheme and host, and keeps the query sorted
    with ``utm_*`` trackers removed.

    The query has to stay: WeChat (``/s?__biz=..&mid=..&idx=..&sn=..``), Hacker News
    (``/item?id=``) and YouTube (``/watch?v=``) carry the article id there, so dropping
    it collapses every article on such a host onto one key and pairs an AIHOT reference
    with an unrelated item — silently, with ``match_method`` still reading ``url``.
    """
    candidate = url.strip()
    status = _X_STATUS_RE.match(candidate)
    if status:
        return f"x_status:{status.group(1)}", "x_status_id"
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and not (
        (parts.scheme == "https" and parts.port == 443) or (parts.scheme == "http" and parts.port == 80)
    ):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.startswith("utm_")
        )
    )
    suffix = f"?{query}" if query else ""
    return f"{parts.scheme.lower()}://{host}{path}{suffix}", "url"


def question_id_for(match_key: str) -> str:
    return hashlib.sha256(match_key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ReferenceRecord:
    aihot_id: str
    aihot_url: str | None
    original_url: str
    title: str | None
    category_slug: str | None
    tags: list[str] | None
    score_0_100: float | None
    selected: bool
    summary: str | None
    reason: str | None
    published_at: str | None

    def as_reference(self) -> dict[str, Any]:
        return {
            "provider": "aihot",
            "aihot_id": self.aihot_id,
            "aihot_url": self.aihot_url,
            "title": self.title,
            "category_slug": self.category_slug,
            "primary_category": CATEGORY_SLUG_TO_PRIMARY.get(self.category_slug or ""),
            "tags": self.tags,
            "score_0_100": self.score_0_100,
            "selected": self.selected,
            "summary": self.summary,
            "reason": self.reason,
            "published_at": self.published_at,
        }


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _from_window_record(record: dict[str, Any]) -> ReferenceRecord:
    tags = record.get("tags")
    return ReferenceRecord(
        aihot_id=str(record["id"]),
        aihot_url=record.get("aihot_url"),
        original_url=str(record["original_url"]),
        title=record.get("aihot_title"),
        category_slug=record.get("aihot_category_slug"),
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else None,
        score_0_100=_float_or_none(record.get("aihot_score_0_to_100")),
        selected=bool(record.get("aihot_selected")),
        summary=record.get("aihot_summary"),
        reason=record.get("aihot_recommendation_reason"),
        published_at=record.get("published_at"),
    )


def _from_raw_record(record: dict[str, Any]) -> ReferenceRecord:
    return ReferenceRecord(
        aihot_id=str(record["id"]),
        aihot_url=record.get("aihot_url"),
        original_url=str(record["original_url"]),
        title=record.get("title"),
        category_slug=record.get("category"),
        tags=None,
        score_0_100=_float_or_none(record.get("score")),
        selected=bool(record.get("selected")),
        summary=record.get("summary"),
        reason=record.get("reason"),
        published_at=record.get("published_at"),
    )


def load_reference_batch(path: Path) -> list[ReferenceRecord]:
    """``.jsonl`` files are t2 window captures (aihot-item-v1); ``.json`` files are t5 raw lists."""
    if path.suffix == ".jsonl":
        return [_from_window_record(record) for record in read_jsonl(path) if record.get("original_url")]
    payload = read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"{path}: raw batch must be a JSON list")
    return [_from_raw_record(record) for record in payload if record.get("original_url")]


@dataclass(frozen=True)
class DbItem:
    item_id: str
    source_id: str
    tier: str
    url: str
    title: str
    author: str | None
    published_at: str
    content_text: str

    def as_input(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "source_id": self.source_id,
            "tier": self.tier,
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "published_at": self.published_at,
            "content_text": self.content_text,
            "content_sha256": sha256_text(self.content_text),
        }


def _index_item_urls(conn: sqlite3.Connection) -> tuple[dict[str, str], int]:
    """Map normalized url key -> item id (first fetched wins); return the duplicate count."""
    index: dict[str, str] = {}
    duplicates = 0
    for item_id, url in conn.execute("SELECT id, url FROM items ORDER BY fetched_at ASC, id ASC"):
        key, _ = normalize_url(str(url))
        if key in index:
            duplicates += 1
            continue
        index[key] = str(item_id)
    return index, duplicates


def _fetch_item(conn: sqlite3.Connection, item_id: str) -> DbItem:
    row = conn.execute(
        """
        SELECT i.id, i.source_id, COALESCE(s.tier, 'unknown'), i.url, i.title, i.author, i.published_at, i.content_text
        FROM items i LEFT JOIN sources s ON s.id = i.source_id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise LookupError(item_id)
    return DbItem(
        item_id=str(row[0]),
        source_id=str(row[1]),
        tier=str(row[2]),
        url=str(row[3]),
        title=str(row[4]),
        author=row[5],
        published_at=str(row[6]),
        content_text=str(row[7] or ""),
    )


def build_evalset(
    *,
    db_path: Path,
    out_dir: Path,
    sources: tuple[tuple[str, Path], ...] = DEFAULT_SOURCES,
) -> dict[str, Any]:
    """Write ``questions.jsonl`` + ``manifest.json`` into ``out_dir``; return the manifest."""
    built_at = utc_now()
    conn = sqlite3.connect(readonly_db_uri(db_path), uri=True)
    try:
        url_index, db_url_duplicates = _index_item_urls(conn)
        max_fetched_at = conn.execute("SELECT max(fetched_at) FROM items").fetchone()[0]
        questions: dict[str, dict[str, Any]] = {}
        batches: dict[str, dict[str, Any]] = {}
        duplicates: list[dict[str, str]] = []
        unknown_category_slugs: Counter[str] = Counter()
        for batch_name, source_path in sources:
            records = load_reference_batch(source_path)
            source_sha256 = sha256_file(source_path)
            matched = 0
            unmatched = 0
            deduped = 0
            for record in records:
                key, method = normalize_url(record.original_url)
                item_id = url_index.get(key)
                if item_id is None:
                    unmatched += 1
                    continue
                matched += 1
                if record.category_slug not in CATEGORY_SLUG_TO_PRIMARY:
                    unknown_category_slugs[str(record.category_slug)] += 1
                question_id = question_id_for(key)
                existing = questions.get(question_id)
                if existing is not None:
                    keep_new = bool(record.tags) and not existing["reference"]["tags"]
                    duplicates.append(
                        {
                            "question_id": question_id,
                            "kept_batch": batch_name if keep_new else existing["provenance"]["batch"],
                            "dropped_batch": existing["provenance"]["batch"] if keep_new else batch_name,
                        }
                    )
                    deduped += 1
                    if not keep_new:
                        continue
                    batches[existing["provenance"]["batch"]]["kept"] -= 1
                item = _fetch_item(conn, item_id)
                questions[question_id] = {
                    "question_id": question_id,
                    "input": item.as_input(),
                    "reference": record.as_reference(),
                    "provenance": {
                        "batch": batch_name,
                        "source_file": str(source_path),
                        "source_sha256": source_sha256,
                        "match_method": method,
                        "built_at": built_at,
                        "builder_version": BUILDER_VERSION,
                    },
                }
                batches.setdefault(batch_name, {"kept": 0})
                batches[batch_name]["kept"] += 1
            batches.setdefault(batch_name, {"kept": 0})
            batches[batch_name].update(
                {
                    "source_file": str(source_path),
                    "source_sha256": source_sha256,
                    "read": len(records),
                    "matched": matched,
                    "unmatched": unmatched,
                    "deduped": deduped,
                }
            )
    finally:
        conn.close()

    ordered = [questions[question_id] for question_id in sorted(questions)]
    out_dir.mkdir(parents=True, exist_ok=True)
    questions_path = out_dir / "questions.jsonl"
    write_jsonl(questions_path, ordered)
    by_category: Counter[str] = Counter(str(question["reference"]["primary_category"]) for question in ordered)
    by_selected: Counter[str] = Counter(str(bool(question["reference"]["selected"])) for question in ordered)
    manifest = {
        "evalset": "aihot-fit-v1",
        "builder_version": BUILDER_VERSION,
        "built_at": built_at,
        "db_path": str(db_path),
        "db_items_max_fetched_at": max_fetched_at,
        "db_url_index_duplicates": db_url_duplicates,
        "batches": batches,
        "duplicates": duplicates,
        "duplicate_count": len(duplicates),
        "unknown_category_slugs": dict(unknown_category_slugs),
        "question_count": len(ordered),
        "questions_sha256": sha256_file(questions_path),
        "by_primary_category": dict(sorted(by_category.items())),
        "by_selected": dict(sorted(by_selected.items())),
        "with_tags": sum(1 for question in ordered if question["reference"]["tags"]),
        "with_summary": sum(1 for question in ordered if question["reference"]["summary"]),
        "with_reason": sum(1 for question in ordered if question["reference"]["reason"]),
    }
    write_json(out_dir / "manifest.json", manifest)
    return manifest
