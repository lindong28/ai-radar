from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from ..fetcher.dedup import content_hash
from ..interpret.runner import _abstract_from_summary, _recommendation_from_summary, _safe_tags
from ..wechat_archive import (
    ARCHIVE_SOURCE_ID,
    ensure_archive_source,
    public_source_sql,
    wechat_visibility_sql,
)

CATALOG_SCHEMA_VERSION = 1
CATALOG_ORIGIN = "ai_assistant_kb_archive"
CATALOG_MODEL = "ai-assistant-kb-archive"
CATALOG_VECTOR_DIM = 1536


@dataclass(frozen=True)
class CatalogSnapshot:
    header: dict[str, Any]
    articles: tuple[dict[str, Any], ...]


@dataclass
class ImportReceipt:
    run_id: str
    dry_run: bool
    catalog_articles: int = 0
    eligible: int = 0
    imported: int = 0
    already_present: int = 0
    existing_without_interpretation: int = 0
    skipped: int = 0
    remaining: int = 0
    changed: bool = False
    postcheck: str = "not_run"
    skipped_reasons: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["skipped_reasons"] = dict(sorted(self.skipped_reasons.items()))
        return payload


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port is not None and not (scheme == "https" and port == 443) and not (scheme == "http" and port == 80):
        netloc = f"{host}:{port}"
    identity_keys = {"__biz", "mid", "idx", "sn"}
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if host == "mp.weixin.qq.com" and parts.path.startswith("/s/"):
        pairs = []
    elif host == "mp.weixin.qq.com" and parts.path == "/s":
        pairs = [(key, value) for key, value in pairs if key in identity_keys]
    else:
        tracking = {"fbclid", "gclid", "scene", "mpshare", "from", "isappinstalled"}
        pairs = [(key, value) for key, value in pairs if not key.startswith("utm_") and key not in tracking]
    return urlunsplit((scheme, netloc, parts.path.rstrip("/") or "/", urlencode(sorted(pairs)), ""))


def _is_wechat_article_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (
        parts.scheme in {"http", "https"}
        and (parts.hostname or "").lower() == "mp.weixin.qq.com"
        and (parts.path == "/s" or parts.path.startswith("/s/"))
    )


def load_catalog(assistant_root: Path, user: str) -> CatalogSnapshot:
    run_script = assistant_root / "agents" / "summary-agent" / "run.sh"
    if not run_script.is_file() or not os.access(run_script, os.X_OK):
        raise FileNotFoundError(f"summary-agent run.sh is missing or not executable: {run_script}")
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        [str(run_script), "--list-article-records", "--user", user],
        cwd=assistant_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    records = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    if not records or records[0].get("record_type") != "catalog":
        raise ValueError("summary-agent catalog did not start with a catalog record")
    header = records[0]
    if header.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError(f"unsupported summary-agent catalog schema: {header.get('schema_version')!r}")
    articles = tuple(records[1:])
    if any(record.get("record_type") != "article" for record in articles):
        raise ValueError("summary-agent catalog contains an unsupported record type")
    if any(record.get("schema_version") != CATALOG_SCHEMA_VERSION for record in articles):
        raise ValueError("summary-agent catalog contains an unsupported article schema")
    index_rows = int(header.get("index_rows", -1))
    header_shape = (
        header.get("user") == user,
        index_rows == len(articles),
        int(header.get("manifest_rows", -1)) == index_rows,
        int(header.get("vector_rows", -1)) == index_rows,
        int(header.get("vector_ndim", -1)) == 2,
        int(header.get("vector_dim", -1)) == CATALOG_VECTOR_DIM,
        int(header.get("expected_vector_dim", -1)) == CATALOG_VECTOR_DIM,
        header.get("alignment_status") == "exact",
    )
    if not all(header_shape):
        raise ValueError("summary-agent catalog header is not an exact aligned snapshot")
    return CatalogSnapshot(header=header, articles=articles)


def _safe_catalog_path(assistant_root: Path, value: object) -> Path:
    path = Path(str(value or ""))
    if not path.is_absolute():
        raise ValueError("summary-agent catalog paths must be absolute")
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved


def _article_metadata(article_md: str, record: dict[str, Any]) -> tuple[str, str, str, str]:
    title_match = re.search(r"^#\s+(.+?)\s*$", article_md, re.MULTILINE)
    author_match = re.search(r"^\*\*作者\*\*\s*[:：]\s*(.+?)\s*$", article_md, re.MULTILINE)
    published_match = re.search(r"^\*\*发布时间\*\*\s*[:：]\s*(.+?)\s*$", article_md, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else str(record.get("title") or "").strip()
    author = author_match.group(1).strip() if author_match else str(record.get("source") or "").strip()
    published = published_match.group(1).strip() if published_match else str(record.get("saved_at") or "").strip()
    published_at_basis = "article_header" if published_match else "kb_saved_at_fallback"
    if not title or not author or not published:
        raise ValueError("article metadata requires title, author/source, and published_at/saved_at")
    parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    published_at = parsed.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return title, author, published_at, published_at_basis


def _existing_by_canonical_url(conn: sqlite3.Connection) -> dict[str, tuple[str, bool]]:
    rows = conn.execute(
        """
        SELECT i.id, i.url, wi.item_id IS NOT NULL AS has_interpretation
        FROM items i
        LEFT JOIN wechat_interpretations wi ON wi.item_id=i.id
        """
    ).fetchall()
    result: dict[str, tuple[str, bool]] = {}
    for row in rows:
        try:
            result[_canonical_url(str(row["url"]))] = (str(row["id"]), bool(row["has_interpretation"]))
        except ValueError:
            continue
    return result


def _unique_slug(conn: sqlite3.Connection, base: str) -> str:
    slug = base
    suffix = 2
    while conn.execute("SELECT 1 FROM wechat_interpretations WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _skip_reason(record: dict[str, Any]) -> str | None:
    if record.get("schema_version") != CATALOG_SCHEMA_VERSION:
        return "schema_version"
    if record.get("entry_status") != "ok":
        return "entry_status"
    if record.get("file_status") != "ok":
        return f"file_{record.get('file_status') or 'invalid'}"
    if record.get("vector_status") != "ok":
        return f"vector_{record.get('vector_status') or 'invalid'}"
    url = str(record.get("url") or "")
    canonical_url = str(record.get("canonical_url") or "")
    if not _is_wechat_article_url(url) or not _is_wechat_article_url(canonical_url):
        return "not_wechat_article"
    if _canonical_url(url) != _canonical_url(canonical_url):
        return "canonical_url_mismatch"
    if not str(record.get("kb_slug") or "").strip():
        return "missing_slug"
    return None


def _validate_record_files(assistant_root: Path, record: dict[str, Any]) -> None:
    article_path = _safe_catalog_path(assistant_root, record.get("article_file_path"))
    summary_path = _safe_catalog_path(assistant_root, record.get("summary_file_path"))
    article_md = article_path.read_text(encoding="utf-8")
    summary_path.read_text(encoding="utf-8")
    _article_metadata(article_md, record)


def _validation_skip_reason(exc: OSError | UnicodeError | ValueError) -> str:
    if isinstance(exc, FileNotFoundError):
        return "article_or_summary_file_missing"
    if isinstance(exc, UnicodeError):
        return "article_or_summary_encoding_invalid"
    if isinstance(exc, ValueError):
        return "article_metadata_or_date_invalid"
    return "article_or_summary_file_unreadable"


def _insert_article(
    conn: sqlite3.Connection,
    *,
    assistant_root: Path,
    user: str,
    run_id: str,
    record: dict[str, Any],
    now: str,
) -> None:
    article_path = _safe_catalog_path(assistant_root, record.get("article_file_path"))
    summary_path = _safe_catalog_path(assistant_root, record.get("summary_file_path"))
    article_md = article_path.read_text(encoding="utf-8")
    summary_md = summary_path.read_text(encoding="utf-8")
    title, author, published_at, published_at_basis = _article_metadata(article_md, record)
    canonical_url = _canonical_url(str(record["canonical_url"]))
    kb_slug = str(record["kb_slug"]).strip()
    item_id = "kb-" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    slug = _unique_slug(conn, kb_slug)
    extra = {
        "origin": CATALOG_ORIGIN,
        "import_run_id": run_id,
        "kb_slug": kb_slug,
        "upstream_canonical_url": canonical_url,
        "published_at_basis": published_at_basis,
    }
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            item_id,
            ARCHIVE_SOURCE_ID,
            canonical_url,
            title,
            author,
            published_at,
            now,
            article_md,
            content_hash(f"{article_md}\n{canonical_url}"),
            json.dumps(extra, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ),
    )
    recommendation = _recommendation_from_summary(summary_md) or "值得一看"
    abstract = _abstract_from_summary(summary_md, fallback=article_md)
    tags = _safe_tags(record.get("tags"))
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error,
          error_retry_count, criteria_reason_source, interpret_user
        )
        VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, 1, ?, NULL, 0, ?, ?)
        """,
        (
            item_id,
            slug,
            recommendation,
            "已存在于 ai-assistant 知识库",
            abstract,
            json.dumps(tags, ensure_ascii=False, separators=(",", ":")),
            summary_md,
            CATALOG_MODEL,
            now,
            "markdown_value_judgment_line" if _recommendation_from_summary(summary_md) else None,
            user,
        ),
    )


def _postcheck(conn: sqlite3.Connection, *, run_id: str, expected: int) -> None:
    params = (CATALOG_ORIGIN, run_id)
    provenance = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM items
            WHERE json_extract(extra_json, '$.origin')=?
              AND json_extract(extra_json, '$.import_run_id')=?
            """,
            params,
        ).fetchone()[0]
    )
    item_count = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM items
            WHERE source_id=? AND json_extract(extra_json, '$.import_run_id')=?
            """,
            (ARCHIVE_SOURCE_ID, run_id),
        ).fetchone()[0]
    )
    interpretation_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM items i JOIN wechat_interpretations wi ON wi.item_id=i.id
            WHERE i.source_id=? AND json_extract(i.extra_json, '$.import_run_id')=?
              AND wi.save_decision=1 AND wi.kb_synced=1 AND wi.error IS NULL
            """,
            (ARCHIVE_SOURCE_ID, run_id),
        ).fetchone()[0]
    )
    visible_count = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM items i JOIN sources s ON s.id=i.source_id
            JOIN wechat_interpretations wi ON wi.item_id=i.id
            WHERE json_extract(i.extra_json, '$.import_run_id')=?
              AND wi.save_decision=1 AND {wechat_visibility_sql()}
            """,
            (run_id,),
        ).fetchone()[0]
    )
    fts_count = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM items_fts
            WHERE item_id IN (
              SELECT id FROM items WHERE source_id=?
                AND json_extract(extra_json, '$.import_run_id')=?
            )
            """,
            (ARCHIVE_SOURCE_ID, run_id),
        ).fetchone()[0]
    )
    public_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM sources s WHERE s.id=? AND {public_source_sql()}",
            (ARCHIVE_SOURCE_ID,),
        ).fetchone()[0]
    )
    observed = (provenance, item_count, interpretation_count, visible_count, fts_count)
    if observed != (expected,) * 5 or public_count != 0:
        raise RuntimeError(
            f"wechat KB import postcheck failed: expected={expected} observed={observed} public_count={public_count}"
        )


def import_catalog(
    conn: sqlite3.Connection,
    *,
    assistant_root: Path,
    user: str,
    dry_run: bool,
    limit: int | None,
    catalog_loader: Callable[[Path, str], CatalogSnapshot] = load_catalog,
) -> ImportReceipt:
    snapshot = catalog_loader(assistant_root, user)
    run_id = f"wechat-kb-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    receipt = ImportReceipt(run_id=run_id, dry_run=dry_run, catalog_articles=len(snapshot.articles))
    existing = _existing_by_canonical_url(conn)
    candidates: list[dict[str, Any]] = []
    for record in snapshot.articles:
        reason = _skip_reason(record)
        if reason:
            receipt.skipped += 1
            receipt.skipped_reasons[reason] += 1
            continue
        canonical_url = _canonical_url(str(record["canonical_url"]))
        current = existing.get(canonical_url)
        if current is not None:
            if current[1]:
                receipt.already_present += 1
            else:
                receipt.existing_without_interpretation += 1
            continue
        candidates.append(record)

    validated: list[dict[str, Any]] = []
    if candidates:
        with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as executor:
            validations = [executor.submit(_validate_record_files, assistant_root, record) for record in candidates]
            for record, validation in zip(candidates, validations, strict=True):
                try:
                    validation.result()
                except (OSError, UnicodeError, ValueError) as exc:
                    receipt.skipped += 1
                    receipt.skipped_reasons[_validation_skip_reason(exc)] += 1
                else:
                    validated.append(record)
    candidates = validated
    receipt.eligible = len(candidates)
    selected = candidates[:limit] if limit is not None else candidates
    receipt.remaining = len(candidates) - len(selected)
    if dry_run:
        return receipt
    if not selected:
        receipt.postcheck = "not_needed"
        return receipt

    now = _utc_now()
    try:
        ensure_archive_source(conn, synced_at=now)
        for record in selected:
            try:
                _insert_article(
                    conn,
                    assistant_root=assistant_root,
                    user=user,
                    run_id=run_id,
                    record=record,
                    now=now,
                )
                receipt.imported += 1
            except (OSError, UnicodeError, ValueError) as exc:
                receipt.skipped += 1
                receipt.skipped_reasons[_validation_skip_reason(exc)] += 1
        _postcheck(conn, run_id=run_id, expected=receipt.imported)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    receipt.changed = receipt.imported > 0
    receipt.postcheck = "passed"
    return receipt
