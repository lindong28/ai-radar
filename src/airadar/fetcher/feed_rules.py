from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from ..sources.loader import SourceConfig


def normalized_entry_url(source: SourceConfig, value: str) -> str:
    return urljoin(source.url, value)


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
