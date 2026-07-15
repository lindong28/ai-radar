from __future__ import annotations

import sqlite3
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

CURATED_MEDIA_FULL_RANK_LIMIT = 12
CURATED_MEDIA_PREVIEW_RANK_LIMIT = 13
PROXY_IMAGE_HOST_SUFFIXES = ("qpic.cn",)
_LAZY_SRC_ATTRS = ("data-src", "data-original", "data-lazy-src")


class _ImageSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attr_map = {name.lower(): value for name, value in attrs if value}
        src = attr_map.get("src")
        chosen = src if src and not src.strip().lower().startswith("data:") else None
        if chosen is None:
            chosen = next((attr_map[a] for a in _LAZY_SRC_ATTRS if attr_map.get(a)), src)
        if chosen:
            self.urls.append(unescape(chosen.strip()))


def _safe_media_url(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def image_host_needs_proxy(netloc: str) -> bool:
    host = netloc.lower().split(":", 1)[0]
    return any(host == suffix or host.endswith("." + suffix) for suffix in PROXY_IMAGE_HOST_SUFFIXES)


def proxy_image_url(url: str | None) -> str | None:
    """Route hotlink-blocked image hosts through the same-origin /img proxy."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and image_host_needs_proxy(parsed.netloc):
        return "/img?url=" + quote(url, safe="")
    return url


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
        proxied = proxy_image_url(url)
        if proxied:
            assets.append({"type": "image", "url": proxied})
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
