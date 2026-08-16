from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Any

import feedparser

from ..sources.loader import SourceConfig
from .content import clean_content
from .dedup import FetchedItem
from .feed_rules import include_feed_entry, normalized_entry_url
from .urls import canonicalize_item_url


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _published_at(entry: Any) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if isinstance(parsed, struct_time):
        return datetime(*parsed[:6], tzinfo=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    value = entry.get("published") or entry.get("updated")
    if value:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError):
            pass
    return utc_now()


def _raw_content(entry: Any) -> str:
    contents = entry.get("content") or []
    if contents:
        first = contents[0]
        if isinstance(first, dict):
            return str(first.get("value") or "")
    return str(entry.get("summary") or entry.get("description") or "")


def parse_feed(source: SourceConfig, body: bytes) -> list[FetchedItem]:
    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"failed to parse feed for {source.slug}: {parsed.bozo_exception}")

    fetched_at = utc_now()
    items: list[FetchedItem] = []
    for entry in parsed.entries:
        title = clean_content(str(entry.get("title") or "Untitled"))
        url = normalized_entry_url(source, str(entry.get("link") or ""))
        if not url:
            continue
        if not include_feed_entry(source, entry, url):
            continue
        extra = {
            "guid": entry.get("id") or entry.get("guid"),
            "tags": [tag.get("term") for tag in entry.get("tags", []) if isinstance(tag, dict)],
        }
        url, extra = canonicalize_item_url(url, extra)
        raw = _raw_content(entry)
        content_text = clean_content(raw, fallback=title)
        items.append(
            FetchedItem(
                source_id=source.slug,
                url=url,
                title=title,
                author=entry.get("author") or entry.get("dc_creator"),
                published_at=_published_at(entry),
                fetched_at=fetched_at,
                content_text=content_text,
                content_html=raw,
                extra=extra,
            )
        )
    by_url: dict[str, FetchedItem] = {}
    for item in items:
        by_url[item.url.rstrip("/").casefold()] = item
    return list(by_url.values())
