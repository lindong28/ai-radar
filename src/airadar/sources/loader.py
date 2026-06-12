from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VALID_TIERS = {"T1", "T1.5", "T2"}
VALID_KINDS = {"feed", "x", "wechat"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*[a-z0-9]$")
LOGGER = logging.getLogger(__name__)
MP2RSS_FEED_PLACEHOLDER = "${MP2RSS_FEED_URL}"
MP2RSS_FEED_ENV = "MP2RSS_FEED_URL"


@dataclass(frozen=True)
class SourceConfig:
    slug: str
    name: str
    url: str
    tier: str
    enabled: bool = True
    kind: str = "feed"
    homepage_url: str | None = None
    icon_url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def _validate_http_url(slug: str, label: str, url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid {label} for {slug}: {url}")
    return url


def _validate_source(raw: dict[str, Any]) -> SourceConfig:
    try:
        slug = str(raw["slug"])
        name = str(raw["name"])
        url = str(raw["url"])
        tier = str(raw["tier"])
    except KeyError as exc:
        raise ValueError(f"missing source field: {exc.args[0]}") from exc

    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid source slug: {slug}")
    url = os.path.expandvars(url)
    if "${" in url:
        raise ValueError(
            f"source url for {slug} references an unset env var: {raw['url']!r}"
        )
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid source url for {slug}: {url}")
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier for {slug}: {tier}")
    enabled = bool(raw.get("enabled", True))
    kind = str(raw.get("kind", "feed"))
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind for {slug}: {kind} (expected one of {sorted(VALID_KINDS)})")
    homepage_raw = raw.get("homepage_url")
    homepage_url = _validate_http_url(slug, "homepage_url", str(homepage_raw) if homepage_raw is not None else None)
    icon_raw = raw.get("icon_url")
    icon_url = _validate_http_url(slug, "icon_url", str(icon_raw) if icon_raw is not None else None)
    meta = raw.get("meta", {})
    if not isinstance(meta, dict):
        raise ValueError(f"invalid meta for {slug}: must be table/object")
    return SourceConfig(
        slug=slug,
        name=name,
        url=url,
        tier=tier,
        enabled=enabled,
        kind=kind,
        homepage_url=homepage_url,
        icon_url=icon_url,
        meta=meta,
    )


def _should_skip_unconfigured_mp2rss(raw: dict[str, Any]) -> bool:
    raw_url = raw.get("url")
    if raw_url is None or MP2RSS_FEED_PLACEHOLDER not in str(raw_url):
        return False
    return not os.environ.get(MP2RSS_FEED_ENV, "").strip()


def load_sources(path: Path) -> list[SourceConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_sources = data.get("source", [])
    if not isinstance(raw_sources, list):
        raise ValueError("sources.toml must contain [[source]] entries")

    sources: list[SourceConfig] = []
    for raw in raw_sources:
        if _should_skip_unconfigured_mp2rss(raw):
            LOGGER.warning(
                "source %s skipped: %s is not set; set it to enable the Mp2RSS WeChat feed",
                raw.get("slug", "<unknown>"),
                MP2RSS_FEED_ENV,
            )
            continue
        sources.append(_validate_source(raw))
    seen: set[str] = set()
    for source in sources:
        if source.slug in seen:
            raise ValueError(f"duplicate source slug: {source.slug}")
        seen.add(source.slug)
    return sources
