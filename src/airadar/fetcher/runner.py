from __future__ import annotations

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx

from .. import db
from ..runtime_env import read_value
from ..sources.loader import SourceConfig, load_sources
from ..sources.sync import (
    load_enabled_sources_from_db as load_enabled_sources_from_db,
)
from ..sources.sync import (
    load_fetchable_sources_from_db,
    sync_to_db,
)
from ..sources.x_state import validate_x_runtime_meta, without_x_runtime_meta, x_runtime_meta
from .dedup import FetchedItem, _normalized_url, upsert_item
from .http_client import FeedResponse, fetch_feed
from .rss import parse_feed
from .web import fetch_web_source
from .wechat import normalize_wechat_avatar_url, scrape_article
from .x_api import fetch_x_timeline

logger = logging.getLogger(__name__)
WECHAT_AVATAR_NEGATIVE_CACHE_TTL = timedelta(days=2)
WECHAT_AVATAR_BACKFILL_LIMIT = 1
SOURCE_FETCH_WORKERS = 12
WECHAT_BODY_FETCH_WORKERS = 4


@dataclass(frozen=True)
class SourceFetchSummary:
    source_id: str
    fetched: int = 0
    inserted: int = 0
    error: str | None = None
    http_status_class: str | None = None
    http_status_code: int | None = None


@dataclass(frozen=True)
class FetchSummary:
    sources: list[SourceFetchSummary] = field(default_factory=list)

    @property
    def inserted(self) -> int:
        return sum(source.inserted for source in self.sources)

    @property
    def attempted(self) -> int:
        return len(self.sources)

    @property
    def failed(self) -> int:
        return sum(1 for source in self.sources if source.error)


@dataclass(frozen=True)
class _WechatBodyPlan:
    item: FetchedItem
    stored_text: str
    stored_html: str | None
    use_stored_body: bool
    avatar_account: str | None
    needs_scrape: bool


@dataclass(frozen=True)
class _SourceMetaUpdate:
    source_id: str
    meta_json: str
    expected_runtime: dict[str, Any] | None = None
    source_identity: tuple[str, str] | None = None


@dataclass(frozen=True)
class _SourceFeedResult:
    source: SourceConfig
    response: FeedResponse | None = None
    items: list[FetchedItem] = field(default_factory=list)
    meta_update: _SourceMetaUpdate | None = None
    error: str | None = None
    http_status_class: str | None = None
    http_status_code: int | None = None
    x_failure_reason: str | None = None
    x_failure_recovery: str | None = None


class _SourceFeedMetaCapture:
    def __init__(self) -> None:
        self.meta_update: _SourceMetaUpdate | None = None

    def execute(self, _sql: str, parameters: tuple[object, object]) -> None:
        self.meta_update = _SourceMetaUpdate(source_id=str(parameters[1]), meta_json=str(parameters[0]))


def default_sources_path() -> Path:
    return db.PROJECT_ROOT / "data" / "sources.toml"


def reload_sources(conn: sqlite3.Connection, path: Path | None = None) -> list[SourceConfig]:
    sources = load_sources(path or default_sources_path())
    sync_to_db(sources, conn)
    return sources


def fetch_source(conn: sqlite3.Connection, source: SourceConfig) -> SourceFetchSummary:
    return _apply_source_feed_result(conn, _fetch_source_feed(source))


def _fetch_source_feed(source: SourceConfig) -> _SourceFeedResult:
    capture = _SourceFeedMetaCapture()
    try:
        if source.kind == "x" and source.meta.get("adapter") == "x_api":
            page = fetch_x_timeline(source)
            return _SourceFeedResult(
                source=source,
                response=FeedResponse(status_code=200, body=b""),
                items=page.items,
                meta_update=_SourceMetaUpdate(
                    source_id=source.slug,
                    meta_json=json.dumps(page.meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    expected_runtime=x_runtime_meta(source.meta),
                    source_identity=(
                        str(source.meta.get("adapter") or ""),
                        str(source.meta.get("username") or "").casefold(),
                    ),
                ),
            )
        if source.kind == "web":
            response, items = fetch_web_source(source, cast(sqlite3.Connection, capture))
            return _SourceFeedResult(
                source=source,
                response=response,
                items=items,
                meta_update=capture.meta_update,
            )
        response = fetch_feed(source, cast(sqlite3.Connection, capture))
        if response.not_modified:
            return _SourceFeedResult(source=source, response=response, meta_update=capture.meta_update)
        items = parse_feed(source, response.body)
        return _SourceFeedResult(source=source, response=response, items=items, meta_update=capture.meta_update)
    except Exception as exc:
        status_class = None
        status_code = None
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            status_class = f"{status_code // 100}xx"
        x_failure_reason = None
        x_failure_recovery = None
        if source.kind == "x" and source.meta.get("adapter") == "x_api":
            if status_code == 401:
                x_failure_reason = "authentication_rejected"
                x_failure_recovery = "replace_or_confirm_token_then_rerun_fetch"
            elif isinstance(exc, RuntimeError) and str(exc) == "X_BEARER_TOKEN is not configured":
                x_failure_reason = "x_bearer_token_not_configured"
                x_failure_recovery = "configure_X_BEARER_TOKEN_then_rerun_fetch"
            else:
                x_failure_reason = "http_or_runtime_failure"
                x_failure_recovery = "inspect_X_fetch_error_then_rerun_fetch"
        return _SourceFeedResult(
            source=source,
            error=f"{type(exc).__name__}: {exc}",
            http_status_class=status_class,
            http_status_code=status_code,
            x_failure_reason=x_failure_reason,
            x_failure_recovery=x_failure_recovery,
        )


def _persist_x_failure(conn: sqlite3.Connection, result: _SourceFeedResult) -> None:
    if result.x_failure_reason is None or result.x_failure_recovery is None:
        return
    source = result.source
    row = conn.execute("SELECT meta_json FROM sources WHERE id=?", (source.slug,)).fetchone()
    if row is None:
        raise ValueError(f"source disappeared while fetching: {source.slug}")
    current_meta_json = row[0] or "{}"
    current_meta = json.loads(current_meta_json)
    if not isinstance(current_meta, dict):
        raise ValueError(f"invalid source metadata while fetching: {source.slug}")
    current_runtime = validate_x_runtime_meta(current_meta, context=source.slug)
    current_identity = (
        str(current_meta.get("adapter") or ""),
        str(current_meta.get("username") or "").casefold(),
    )
    source_identity = (
        str(source.meta.get("adapter") or ""),
        str(source.meta.get("username") or "").casefold(),
    )
    if current_runtime != x_runtime_meta(source.meta) or current_identity != source_identity:
        raise ValueError(f"X source state changed while fetching: {source.slug}")
    next_runtime = dict(current_runtime)
    next_runtime["x_reference_status"] = "blocked"
    next_runtime["x_reference_attempted_at"] = _utc_now()
    next_runtime["x_reference_reason"] = result.x_failure_reason
    next_runtime["x_reference_recovery"] = result.x_failure_recovery
    next_runtime.pop("x_reference_validated_at", None)
    validate_x_runtime_meta(next_runtime, context=source.slug)
    next_meta = {**without_x_runtime_meta(current_meta), **next_runtime}
    next_meta_json = json.dumps(next_meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cursor = conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=? AND meta_json=?",
        (next_meta_json, source.slug, current_meta_json),
    )
    if cursor.rowcount != 1:
        raise ValueError(f"X source state changed while fetching: {source.slug}")
    conn.commit()


def _apply_source_feed_result(conn: sqlite3.Connection, result: _SourceFeedResult) -> SourceFetchSummary:
    source = result.source
    if result.error is not None:
        try:
            _persist_x_failure(conn, result)
        except Exception as exc:
            conn.rollback()
            return SourceFetchSummary(
                source_id=source.slug,
                error=f"{result.error}; X state persistence failed: {type(exc).__name__}: {exc}",
                http_status_class=result.http_status_class,
                http_status_code=result.http_status_code,
            )
        return SourceFetchSummary(source_id=source.slug, error=result.error, http_status_class=result.http_status_class, http_status_code=result.http_status_code)
    response = result.response
    if response is None:
        return SourceFetchSummary(source_id=source.slug, error="RuntimeError: missing feed response")

    try:
        if result.meta_update is not None:
            update = result.meta_update
            meta_json = update.meta_json
            if update.expected_runtime is not None:
                row = conn.execute(
                    "SELECT meta_json FROM sources WHERE id=?",
                    (update.source_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"source disappeared while fetching: {update.source_id}")
                current_meta_json = row[0] or "{}"
                current_meta = json.loads(current_meta_json)
                if not isinstance(current_meta, dict):
                    raise ValueError(f"invalid source metadata while fetching: {update.source_id}")
                current_runtime = validate_x_runtime_meta(current_meta, context=update.source_id)
                current_identity = (
                    str(current_meta.get("adapter") or ""),
                    str(current_meta.get("username") or "").casefold(),
                )
                if current_runtime != update.expected_runtime or current_identity != update.source_identity:
                    raise ValueError(f"X source state changed while fetching: {update.source_id}")
                fetched_meta = json.loads(update.meta_json)
                next_runtime = validate_x_runtime_meta(fetched_meta, context=update.source_id)
                merged_meta = {**without_x_runtime_meta(current_meta), **next_runtime}
                meta_json = json.dumps(
                    merged_meta,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            if update.expected_runtime is None:
                conn.execute(
                    "UPDATE sources SET meta_json=? WHERE id=?",
                    (meta_json, update.source_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE sources SET meta_json=? WHERE id=? AND meta_json=?",
                    (meta_json, update.source_id, current_meta_json),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"X source state changed while fetching: {update.source_id}")
        if response.not_modified:
            if source.kind == "wechat":
                _backfill_wechat_avatar_cache(conn, source.slug)
                conn.commit()
            elif result.meta_update is not None:
                conn.commit()
            return SourceFetchSummary(
                source_id=source.slug,
                http_status_class=f"{response.status_code // 100}xx",
                http_status_code=response.status_code,
            )
        items = result.items
        is_wechat = source.kind == "wechat"
        if is_wechat:
            items = _enrich_wechat_bodies(conn, items)
        inserted = 0
        for item in items:
            inserted += 1 if upsert_item(conn, item, wechat=is_wechat) else 0
        conn.commit()
        return SourceFetchSummary(
            source_id=source.slug,
            fetched=len(items),
            inserted=inserted,
            http_status_class=f"{response.status_code // 100}xx",
            http_status_code=response.status_code,
        )
    except Exception as exc:
        conn.rollback()
        return SourceFetchSummary(source_id=source.slug, error=f"{type(exc).__name__}: {exc}")


def _fetch_and_apply_sources(conn: sqlite3.Connection, sources: list[SourceConfig]) -> list[SourceFetchSummary]:
    if not sources:
        return []
    summaries: list[SourceFetchSummary | None] = [None] * len(sources)
    workers = min(SOURCE_FETCH_WORKERS, len(sources))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_source_feed, source): (index, source)
            for index, source in enumerate(sources)
        }
        for future in as_completed(futures):
            index, source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = _SourceFeedResult(source=source, error=f"{type(exc).__name__}: {exc}")
            summaries[index] = _apply_source_feed_result(conn, result)
    return [summary for summary in summaries if summary is not None]


def _stored_wechat_body(conn: sqlite3.Connection, item: FetchedItem) -> tuple[str, str | None] | None:
    row = conn.execute(
        """
        SELECT content_text, content_html
        FROM items
        WHERE source_id=? AND lower(rtrim(url, '/'))=?
        ORDER BY length(content_text) DESC
        LIMIT 1
        """,
        (item.source_id, _normalized_url(item.url)),
    ).fetchone()
    if row is None:
        return None
    return str(row[0] or ""), row[1]


def _is_unscrapable_wechat_url(url: str) -> bool:
    """True for the long ``?__biz=`` article form, which the scraper cannot read.

    Opening one in the headless browser lands on ``wappoc_appmsgcaptcha`` rather
    than the article, so each attempt costs three bounded retries and returns
    nothing. Feeds that serve this form carry the article body themselves, so
    skipping the scrape loses no text — it only stops paying for the captcha.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.netloc.lower() != "mp.weixin.qq.com":
        return False
    return "__biz" in parse_qs(parts.query)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _wechat_account(value: str | None) -> str | None:
    account = str(value or "").strip()
    return account or None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _wechat_avatar_cache_is_fresh(
    conn: sqlite3.Connection,
    account: str,
    *,
    now: datetime | None = None,
) -> bool:
    row = conn.execute(
        "SELECT avatar_url, checked_at FROM wechat_account_avatars WHERE account=?",
        (account,),
    ).fetchone()
    if row is None:
        return False
    if row[0]:
        return True
    checked_at = _parse_utc(row[1])
    if checked_at is None:
        return False
    return ((now or datetime.now(UTC)) - checked_at) < WECHAT_AVATAR_NEGATIVE_CACHE_TTL


def _cache_wechat_avatar_result(conn: sqlite3.Connection, account: str, avatar_url: object) -> None:
    normalized = normalize_wechat_avatar_url(avatar_url)
    checked_at = _utc_now()
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(account) DO UPDATE SET
          avatar_url=COALESCE(excluded.avatar_url, wechat_account_avatars.avatar_url),
          checked_at=excluded.checked_at,
          updated_at=CASE
            WHEN excluded.avatar_url IS NOT NULL THEN excluded.updated_at
            ELSE wechat_account_avatars.updated_at
          END
        """,
        (account, normalized, checked_at, checked_at),
    )


def _wechat_avatar_account_to_fetch(
    conn: sqlite3.Connection,
    item: FetchedItem,
    attempted_accounts: set[str],
) -> str | None:
    account = _wechat_account(item.author)
    if account is None or account in attempted_accounts:
        return None
    if _wechat_avatar_cache_is_fresh(conn, account):
        return None
    return account


def _cache_wechat_avatar_from_article(
    conn: sqlite3.Connection,
    account: str | None,
    article: dict[str, Any],
) -> None:
    if account is None:
        return
    _cache_wechat_avatar_result(conn, account, article.get("author_avatar_url"))


def refresh_wechat_avatar(conn: sqlite3.Connection, account: str) -> str | None:
    normalized_account = _wechat_account(account)
    if normalized_account is None:
        raise ValueError("account must not be empty")

    row = conn.execute(
        """
        SELECT i.url
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE COALESCE(s.kind, 'feed')='wechat'
          AND i.author=?
          AND i.url IS NOT NULL
          AND trim(i.url) != ''
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
        LIMIT 1
        """,
        (normalized_account,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no WeChat article found for account: {normalized_account}")

    article = scrape_article(str(row[0]))
    _cache_wechat_avatar_from_article(conn, normalized_account, article)
    cached = conn.execute(
        "SELECT avatar_url FROM wechat_account_avatars WHERE account=?",
        (normalized_account,),
    ).fetchone()
    if cached is None or not cached[0]:
        return None
    return str(cached[0])


def _backfill_wechat_avatar_cache(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    limit: int = WECHAT_AVATAR_BACKFILL_LIMIT,
) -> int:
    rows = conn.execute(
        """
        SELECT author, url
        FROM (
          SELECT i.author, i.url,
                 ROW_NUMBER() OVER (
                   PARTITION BY i.author
                   ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
                 ) AS rn
          FROM items i
          LEFT JOIN wechat_account_avatars wa ON wa.account=i.author
          WHERE i.source_id=?
            AND i.author IS NOT NULL
            AND trim(i.author) != ''
            AND (wa.account IS NULL OR wa.avatar_url IS NULL)
        )
        WHERE rn=1
        LIMIT ?
        """,
        (source_id, limit),
    ).fetchall()
    checked = 0
    for row in rows:
        account = _wechat_account(row[0])
        if account is None or _wechat_avatar_cache_is_fresh(conn, account):
            continue
        article = scrape_article(str(row[1]))
        _cache_wechat_avatar_from_article(conn, account, article)
        checked += 1
    return checked


def _plan_wechat_body_enrichment(conn: sqlite3.Connection, items: list[FetchedItem]) -> list[_WechatBodyPlan]:
    plans: list[_WechatBodyPlan] = []
    attempted_avatar_accounts: set[str] = set()
    for item in items:
        stored_body = _stored_wechat_body(conn, item)
        stored_text = stored_body[0] if stored_body is not None else ""
        stored_html = stored_body[1] if stored_body is not None else None
        use_stored_body = stored_body is not None and len(stored_text) > len(item.content_text)
        avatar_account = _wechat_avatar_account_to_fetch(conn, item, attempted_avatar_accounts)
        if avatar_account is not None:
            attempted_avatar_accounts.add(avatar_account)
        plans.append(
            _WechatBodyPlan(
                item=item,
                stored_text=stored_text,
                stored_html=stored_html,
                use_stored_body=use_stored_body,
                avatar_account=avatar_account,
                needs_scrape=(
                    not (use_stored_body and avatar_account is None)
                    and not _is_unscrapable_wechat_url(item.url)
                ),
            )
        )
    return plans


def _scrape_wechat_body_articles(plans: list[_WechatBodyPlan]) -> dict[int, dict[str, Any]]:
    indexed_plans = [(index, plan) for index, plan in enumerate(plans) if plan.needs_scrape]
    if not indexed_plans:
        return {}
    workers = min(WECHAT_BODY_FETCH_WORKERS, len(indexed_plans))
    articles: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(scrape_article, plan.item.url): (index, plan.item.url)
            for index, plan in indexed_plans
        }
        for future in as_completed(futures):
            index, url = futures[future]
            try:
                articles[index] = future.result()
            except Exception as exc:
                articles[index] = {"success": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
    return articles


def _apply_wechat_body_article(
    conn: sqlite3.Connection,
    plan: _WechatBodyPlan,
    article: dict[str, Any] | None,
) -> FetchedItem:
    if article is not None and plan.avatar_account is not None:
        _cache_wechat_avatar_from_article(conn, plan.avatar_account, article)
    if article is None:
        if plan.use_stored_body:
            return replace(plan.item, content_text=plan.stored_text, content_html=plan.stored_html)
        return plan.item

    content_text = str(article.get("content_text") or "")
    if article.get("success") is True and content_text and not plan.use_stored_body:
        return replace(
            plan.item,
            content_text=content_text,
            content_html=str(article.get("content_html") or ""),
        )
    if plan.use_stored_body:
        return replace(plan.item, content_text=plan.stored_text, content_html=plan.stored_html)

    logger.warning(
        "Failed to scrape WeChat article; using RSS item only source_id=%s url=%s error=%s",
        plan.item.source_id,
        plan.item.url,
        article.get("error"),
    )
    return plan.item


def _enrich_wechat_bodies(conn: sqlite3.Connection, items: list[FetchedItem]) -> list[FetchedItem]:
    plans = _plan_wechat_body_enrichment(conn, items)
    articles = _scrape_wechat_body_articles(plans)
    enriched: list[FetchedItem] = []
    for index, plan in enumerate(plans):
        enriched.append(_apply_wechat_body_article(conn, plan, articles.get(index)))
    return enriched


def _checkpoint_after_fetch(db_path: Path | None) -> None:
    try:
        db.checkpoint_db(db_path)
    except Exception as exc:
        logger.warning("Failed to checkpoint database after fetch round: %s", exc)
    # Bounded FTS merge rides the same end-of-round hook: since migration 003
    # stopped rebuilding the index every round, this is what keeps incremental
    # segments from accumulating (see db.maintain_fts). Failure is non-fatal
    # for the same reason checkpointing is -- the fetch itself succeeded.
    try:
        db.maintain_fts(db_path)
    except Exception as exc:
        logger.warning("Failed FTS maintenance after fetch round: %s", exc)


def fetch_all(path: Path | None = None, db_path: Path | None = None) -> FetchSummary:
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    try:
        reload_sources(conn, path)
        sources = load_fetchable_sources_from_db(conn)
        unavailable: list[SourceFetchSummary] = []
        if not read_value("X_BEARER_TOKEN").strip():
            unavailable = [
                _apply_source_feed_result(
                    conn,
                    _SourceFeedResult(
                        source=source,
                    error="RuntimeError: X_BEARER_TOKEN is not configured",
                        x_failure_reason="x_bearer_token_not_configured",
                        x_failure_recovery="configure_X_BEARER_TOKEN_then_rerun_fetch",
                    ),
                )
                for source in sources
                if source.kind == "x" and source.meta.get("adapter") == "x_api"
            ]
            if unavailable:
                logger.warning(
                    "%d enabled X API sources cannot run because X_BEARER_TOKEN is not configured",
                    len(unavailable),
                )
            sources = [
                source
                for source in sources
                if not (source.kind == "x" and source.meta.get("adapter") == "x_api")
            ]
        summaries = unavailable + _fetch_and_apply_sources(conn, sources)
        return FetchSummary(summaries)
    finally:
        conn.close()
        _checkpoint_after_fetch(db_path)
