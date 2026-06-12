from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from ..site_config import site_user_agent
from ..sources.loader import SourceConfig


class _DynamicUserAgent:
    def __str__(self) -> str:
        return site_user_agent()

    def __repr__(self) -> str:
        return repr(str(self))

    def __eq__(self, other: object) -> bool:
        return str(self) == other


USER_AGENT = _DynamicUserAgent()


@dataclass(frozen=True)
class FeedResponse:
    status_code: int
    body: bytes
    not_modified: bool = False


def _is_loopback_url(url: str) -> bool:
    host = urlparse(url).hostname
    return host in {"localhost", "127.0.0.1", "::1"}


def fetch_feed(source: SourceConfig, conn: sqlite3.Connection, timeout: float = 30.0) -> FeedResponse:
    headers = {
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        "User-Agent": str(USER_AGENT),
    }
    etag = source.meta.get("etag")
    last_modified = source.meta.get("last_modified")
    if etag:
        headers["If-None-Match"] = str(etag)
    if last_modified:
        headers["If-Modified-Since"] = str(last_modified)

    response = httpx.get(
        source.url,
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
        trust_env=not _is_loopback_url(source.url),
    )
    if response.status_code == 304:
        return FeedResponse(status_code=304, body=b"", not_modified=True)
    response.raise_for_status()

    meta = dict(source.meta)
    if response.headers.get("etag"):
        meta["etag"] = response.headers["etag"]
    if response.headers.get("last-modified"):
        meta["last_modified"] = response.headers["last-modified"]
    if meta != source.meta:
        conn.execute(
            "UPDATE sources SET meta_json=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")), source.slug),
        )
    return FeedResponse(status_code=response.status_code, body=response.content)
