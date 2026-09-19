from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from ..sources.loader import SourceConfig

# Feed entries are untrusted input. Anything that is not a fetchable http(s)
# URL is dropped here rather than stored: a `javascript:` or `data:` link would
# otherwise land verbatim in items.url and be rendered as a clickable href,
# and the WeChat scraper would hand it to a headless browser.
ALLOWED_ENTRY_SCHEMES = {"http", "https"}


def normalized_entry_url(source: SourceConfig, value: str) -> str:
    # urljoin itself raises on malformed input (e.g. an unclosed IPv6
    # bracket); keep it inside the guard so one bad link drops that entry,
    # not the whole feed.
    try:
        resolved = urljoin(source.url, (value or "").strip())
        parts = urlparse(resolved)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ALLOWED_ENTRY_SCHEMES or not parts.netloc:
        return ""
    return resolved


def include_feed_entry(source: SourceConfig, entry: Any, url: str) -> bool:
    if source.slug != "google_cloud_databases":
        return True
    tags = {
        str(tag.get("term") or "").strip().casefold()
        for tag in entry.get("tags", [])
        if isinstance(tag, dict)
    }
    path = urlparse(url).path.casefold()
    return "databases" in tags or path.startswith("/blog/products/databases/")
