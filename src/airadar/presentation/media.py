from __future__ import annotations

import json
import sqlite3
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

CURATED_MEDIA_FULL_RANK_LIMIT = 12
CURATED_MEDIA_PREVIEW_RANK_LIMIT = 13
# qpic.cn: hotlink-blocked but reachable from the serve host.
# pbs.twimg.com: reachable only through the egress proxy (see routes/media.py).
PROXY_IMAGE_HOST_SUFFIXES = ("qpic.cn", "pbs.twimg.com")
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


def image_url_needs_proxy(url: str) -> bool:
    """Whether this URL's host is one we proxy — and the SSRF allowlist.

    Takes the whole URL and parses it here on purpose. The obvious-looking
    `netloc.split(":", 1)[0]` is wrong: netloc is `[userinfo@]host[:port]`, so
    for `http://mmbiz.qpic.cn:80@169.254.169.254/` it yields `mmbiz.qpic.cn`
    while the request actually goes to `169.254.169.254`. Checking a different
    string than the one connected to is exactly how an allowlist becomes a
    blind SSRF, so callers no longer get to choose which part they pass.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in PROXY_IMAGE_HOST_SUFFIXES)


def proxy_image_url(url: str | None) -> str | None:
    """Route hotlink-blocked image hosts through the same-origin /img proxy."""
    if not url:
        return url
    if urlparse(url).scheme in {"http", "https"} and image_url_needs_proxy(url):
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


def _x_media_assets(row: sqlite3.Row) -> list[dict[str, str]]:
    """Media a tweet carries, in the upstream media_keys order.

    Stored by the fetcher under ``extra_json.x_media``; ``content_html`` stays
    None for X, so the RSS HTML parser below never sees these.
    """
    keys = row.keys()
    if "extra_json" not in keys or not row["extra_json"]:
        return []
    try:
        extra = json.loads(row["extra_json"])
    except (TypeError, ValueError):
        return []
    media = extra.get("x_media") if isinstance(extra, dict) else None
    if not isinstance(media, list):
        return []
    assets: list[dict[str, str]] = []
    for entry in media:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("url")
        url = _safe_media_url(raw) if isinstance(raw, str) else ""
        proxied = proxy_image_url(url) if url else None
        if proxied:
            assets.append({"type": "image", "url": proxied})
    return assets


def _visible_media_assets(row: sqlite3.Row) -> list[dict[str, str]]:
    keys = row.keys()
    if "source_kind" in keys and row["source_kind"] == "x":
        # A tweet's images are the tweet's content, so they are not subject to
        # the rank-based trimming below — that policy exists for RSS body images
        # scraped from articles (ADR-054), which X posts never have.
        return _x_media_assets(row)
    assets = _media_assets_from_html(row["content_html"] if "content_html" in keys else None)
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
