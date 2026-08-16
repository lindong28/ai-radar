from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..runtime_env import read_value
from ..sources.loader import SourceConfig
from ..sources.x_state import X_RUNTIME_META_KEYS, X_USERNAME_RE, validate_x_runtime_meta
from .dedup import FetchedItem

X_API_BASE_URL = "https://api.x.com"
X_RECENT_LOOKBACK = timedelta(minutes=20)
X_MAX_RESULTS_PER_SOURCE = 5
X_TWEET_FIELDS = "author_id,created_at,lang,note_tweet,public_metrics,referenced_tweets"


@dataclass(frozen=True)
class XTimelinePage:
    items: list[FetchedItem]
    meta: dict[str, Any]


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _post_text(post: dict[str, Any]) -> str:
    note_tweet = post.get("note_tweet")
    if isinstance(note_tweet, dict) and note_tweet.get("text"):
        return str(note_tweet["text"]).strip()
    return str(post.get("text") or "").strip()


def _post_extra(post: dict[str, Any], username: str) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "x_post_id": str(post["id"]),
        "x_author_id": str(post.get("author_id") or ""),
        "x_username": username,
    }
    for key in ("lang", "public_metrics", "referenced_tweets"):
        value = post.get(key)
        if value not in (None, "", [], {}):
            extra[key] = value
    return extra


def _post_high_water(posts: list[dict[str, Any]]) -> str | None:
    post_ids = [str(post.get("id") or "") for post in posts]
    numeric_ids = [post_id for post_id in post_ids if post_id.isdigit()]
    return max(numeric_ids, key=int) if numeric_ids else None


def _request_cursor(source: SourceConfig, fetched_at: datetime) -> tuple[dict[str, object], str | None]:
    params: dict[str, object] = {
        "max_results": X_MAX_RESULTS_PER_SOURCE,
        "exclude": "retweets,replies",
        "tweet.fields": X_TWEET_FIELDS,
    }
    pagination_token = str(source.meta.get("x_pagination_token") or "") or None
    since_id = str(source.meta.get("x_since_id") or "") or None
    since_time = str(source.meta.get("x_since_time") or "") or None
    pending_start_time = str(source.meta.get("x_pending_start_time") or "") or None
    initial_start_time = str(source.meta.get("x_initial_start_time") or "") or None
    if since_id:
        params["since_id"] = since_id
    elif since_time:
        params["start_time"] = since_time
    else:
        params["start_time"] = (
            pending_start_time or initial_start_time or _utc_timestamp(fetched_at - X_RECENT_LOOKBACK)
        )
    if pagination_token:
        params["pagination_token"] = pagination_token
    return params, pagination_token


def _parse_aware_timestamp(value: str, *, context: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid X timeline response for {context}: created_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid X timeline response for {context}: created_at has no timezone")
    return parsed.astimezone(UTC)


def _next_meta(
    source: SourceConfig,
    *,
    posts: list[dict[str, Any]],
    response_meta: dict[str, Any],
    requested_pagination: str | None,
    requested_start_time: str | None,
    fetched_at: datetime,
) -> dict[str, Any]:
    meta = dict(source.meta)
    meta["x_reference_status"] = "verified"
    meta["x_reference_validated_at"] = _utc_timestamp(fetched_at)
    meta.pop("x_reference_attempted_at", None)
    meta.pop("x_reference_reason", None)
    meta.pop("x_reference_recovery", None)
    meta.pop("x_initial_start_time", None)
    high_water = _post_high_water(posts)
    pending_high_water = str(meta.get("x_pending_since_id") or "") or None
    if requested_pagination is None and high_water is not None:
        pending_high_water = high_water

    raw_next_token = response_meta.get("next_token")
    next_token = raw_next_token if isinstance(raw_next_token, str) and raw_next_token else None
    if next_token:
        if requested_pagination is not None and next_token == requested_pagination:
            raise ValueError(f"invalid X timeline response for {source.slug}: pagination token did not advance")
        meta["x_cursor_state"] = "draining"
        meta["x_pagination_token"] = next_token
        if pending_high_water:
            meta["x_pending_since_id"] = pending_high_water
        if requested_start_time and not meta.get("x_since_time"):
            meta["x_pending_start_time"] = requested_start_time
        validate_x_runtime_meta(meta, context=source.slug)
        return meta

    final_high_water = pending_high_water or high_water
    if final_high_water:
        meta["x_cursor_state"] = "checkpointed"
        meta["x_since_id"] = final_high_water
        meta.pop("x_since_time", None)
    elif requested_pagination is None and not meta.get("x_since_id"):
        meta["x_cursor_state"] = "checkpointed"
        next_time = _utc_timestamp(fetched_at)
        previous_time = str(meta.get("x_since_time") or "") or None
        if previous_time and _parse_aware_timestamp(
            next_time,
            context=source.slug,
        ) < _parse_aware_timestamp(previous_time, context=source.slug):
            raise ValueError(f"invalid X runtime transition for {source.slug}: time checkpoint moved backwards")
        meta["x_since_time"] = next_time
    for key in X_RUNTIME_META_KEYS - {
        "x_cursor_state",
        "x_reference_status",
        "x_reference_validated_at",
        "x_user_id",
        "x_since_id",
        "x_since_time",
    }:
        meta.pop(key, None)
    validate_x_runtime_meta(meta, context=source.slug)
    return meta


def _resolve_x_identity(
    source: SourceConfig,
    *,
    response: Any,
    username: str,
    fetched_at: datetime,
) -> XTimelinePage:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError(f"invalid X identity response for {source.slug}: error payload")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"invalid X identity response for {source.slug}: data is missing")
    user_id = str(data.get("id") or "")
    returned_username = str(data.get("username") or "")
    if not user_id.isdigit() or not 1 <= len(user_id) <= 19:
        raise ValueError(f"invalid X identity response for {source.slug}: user id is invalid")
    if returned_username.casefold() != username.casefold():
        raise ValueError(f"invalid X identity response for {source.slug}: username does not match")
    meta = dict(source.meta)
    meta["x_reference_status"] = "pending"
    meta.pop("x_reference_validated_at", None)
    meta.pop("x_reference_attempted_at", None)
    meta.pop("x_reference_reason", None)
    meta.pop("x_reference_recovery", None)
    meta["x_cursor_state"] = "uninitialized"
    meta["x_user_id"] = user_id
    meta["x_initial_start_time"] = _utc_timestamp(fetched_at - X_RECENT_LOOKBACK)
    validate_x_runtime_meta(meta, context=source.slug)
    return XTimelinePage(items=[], meta=meta)


def fetch_x_timeline(
    source: SourceConfig,
    *,
    client: Any = httpx,
    now: datetime | None = None,
) -> XTimelinePage:
    token = read_value("X_BEARER_TOKEN").strip()
    if not token:
        raise RuntimeError("X_BEARER_TOKEN is not configured")

    username = str(source.meta.get("username") or "").strip().lstrip("@")
    if not X_USERNAME_RE.fullmatch(username):
        raise ValueError(f"invalid X username for {source.slug}: {username!r}")

    fetched_at = now or datetime.now(UTC)
    validate_x_runtime_meta(source.meta, context=source.slug)
    if source.meta.get("x_cursor_state") == "identity_pending":
        response = client.get(
            source.url,
            headers={"Authorization": f"Bearer {token}"},
            params={},
            timeout=30.0,
        )
        return _resolve_x_identity(
            source,
            response=response,
            username=username,
            fetched_at=fetched_at,
        )

    user_id = str(source.meta.get("x_user_id") or "")
    params, requested_pagination = _request_cursor(source, fetched_at)
    response = client.get(
        f"{X_API_BASE_URL}/2/users/{user_id}/tweets",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("errors"):
        raise ValueError(f"invalid X timeline response for {source.slug}: error payload")
    response_meta = payload.get("meta")
    if not isinstance(response_meta, dict):
        raise ValueError(f"invalid X timeline response for {source.slug}: meta is missing")
    posts = payload.get("data")
    if posts is None and response_meta.get("result_count") == 0:
        posts = []
    if not isinstance(posts, list):
        raise ValueError(f"invalid X timeline response for {source.slug}: data is not a list")
    result_count = response_meta.get("result_count")
    if isinstance(result_count, bool) or not isinstance(result_count, int) or result_count != len(posts):
        raise ValueError(f"invalid X timeline response for {source.slug}: result_count does not match data")
    next_token = response_meta.get("next_token")
    if next_token is not None and (not isinstance(next_token, str) or not next_token):
        raise ValueError(f"invalid X timeline response for {source.slug}: next_token is not a string")

    items: list[FetchedItem] = []
    requested_since_id = str(params.get("since_id") or "") or None
    requested_start_time = str(params.get("start_time") or "") or None
    start_boundary = (
        _parse_aware_timestamp(requested_start_time, context=source.slug)
        if requested_start_time
        else None
    )
    for raw_post in posts:
        if not isinstance(raw_post, dict):
            raise ValueError(f"invalid X timeline response for {source.slug}: post is not an object")
        post_id = str(raw_post.get("id") or "")
        author_id = str(raw_post.get("author_id") or "")
        published_at = str(raw_post.get("created_at") or "")
        text = _post_text(raw_post)
        if not post_id.isdigit() or not published_at or not text:
            raise ValueError(f"invalid X timeline response for {source.slug}: post is missing required fields")
        if author_id != user_id:
            raise ValueError(
                f"invalid X timeline response for {source.slug}: author_id does not match resolved user"
            )
        if requested_since_id is not None and int(post_id) <= int(requested_since_id):
            raise ValueError(f"invalid X timeline response for {source.slug}: post does not advance since_id")
        if start_boundary is not None and _parse_aware_timestamp(
            published_at,
            context=source.slug,
        ) < start_boundary:
            raise ValueError(f"invalid X timeline response for {source.slug}: post predates start_time")
        items.append(
            FetchedItem(
                source_id=source.slug,
                url=f"https://x.com/i/web/status/{post_id}",
                title=text,
                author=f"@{username}",
                published_at=published_at,
                fetched_at=_utc_timestamp(fetched_at),
                content_text=text,
                content_html=None,
                extra=_post_extra(raw_post, username),
            )
        )
    return XTimelinePage(
        items=items,
        meta=_next_meta(
            source,
            posts=posts,
            response_meta=response_meta,
            requested_pagination=requested_pagination,
            requested_start_time=requested_start_time,
            fetched_at=fetched_at,
        ),
    )
