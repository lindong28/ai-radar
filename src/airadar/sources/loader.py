from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .x_state import X_RUNTIME_META_KEYS, X_USERNAME_RE

VALID_TIERS = {"T1", "T1.5", "T2"}
VALID_KINDS = {"feed", "web", "x", "wechat"}
WEB_RUNTIME_META_KEYS = {"parser", "selector", "minimum_items", "allowed_host", "allowed_path"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*[a-z0-9]$")
LOGGER = logging.getLogger(__name__)
# A WeChat feed URL always carries a subscription token, so it is configured as
# a single env-var placeholder rather than a literal. Which env var is per
# source: running two WeChat feeds side by side is how a replacement is proven
# against the incumbent before either is switched off.
ENV_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


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
    optional: bool = False
    required_env: str | None = None
    wechat_only: bool = False
    public_url_override: str | None = None


def _validate_http_url(slug: str, label: str, url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid {label} for {slug}: {url}")
    return url


def _validate_source(
    raw: dict[str, Any],
    *,
    schema_version: int = 1,
) -> SourceConfig:
    try:
        slug = str(raw["slug"])
        name = str(raw["name"])
        url_field = "fetch_url" if schema_version >= 2 else "url"
        url = str(raw[url_field])
        tier = str(raw["tier"])
    except KeyError as exc:
        raise ValueError(f"missing source field: {exc.args[0]}") from exc

    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid source slug: {slug}")
    url = os.path.expandvars(url)
    if "${" in url:
        raise ValueError(
            f"source {url_field} for {slug} references an unset env var: {raw[url_field]!r}"
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
    optional = bool(raw.get("optional", False)) if schema_version >= 2 else False
    required_env = str(raw["required_env"]) if schema_version >= 2 and raw.get("required_env") is not None else None
    wechat_only = bool(raw.get("wechat_only", False)) if schema_version >= 2 else False
    override_raw = raw.get("public_url_override") if schema_version >= 2 else None
    public_url_override = _validate_http_url(slug, "public_url_override", str(override_raw) if override_raw is not None else None)
    if kind == "wechat" and schema_version >= 2:
        placeholder = ENV_PLACEHOLDER_RE.match(str(raw[url_field]))
        if (
            not optional
            or not wechat_only
            or public_url_override is None
            or placeholder is None
            or placeholder.group(1) != required_env
        ):
            raise ValueError(f"invalid optional WeChat configuration for {slug}")
    if schema_version >= 2 and kind == "x":
        adapter = meta.get("adapter")
        if adapter is None:
            canonical_api_path = re.fullmatch(r"/2/users/by/username/[^/]+(?:/tweets)?/?", parsed.path)
            canonical_api_requires_adapter = (
                parsed.netloc.casefold() == "api.x.com"
                and canonical_api_path
                and schema_version >= 2
            )
            if canonical_api_requires_adapter:
                raise ValueError(f"invalid adapter for X API source {slug}: adapter='x_api' is required")
            meta = {**meta, "adapter": "rss"}
            adapter = "rss"
        if adapter == "x_api":
            runtime_keys = X_RUNTIME_META_KEYS & meta.keys()
            if runtime_keys:
                raise ValueError(
                    f"invalid x_api configuration for {slug}: runtime keys are managed internally"
                )
            username = meta.get("username")
            if not isinstance(username, str) or not X_USERNAME_RE.fullmatch(username):
                raise ValueError(f"invalid x_api username for {slug}: {username!r}")
            canonical_homepage = f"https://x.com/{username}"
            canonical_fetch_url = f"https://api.x.com/2/users/by/username/{username}"
            if url.rstrip("/").casefold() != canonical_fetch_url.casefold() or (
                homepage_url is not None
                and homepage_url.rstrip("/").casefold() != canonical_homepage.casefold()
            ):
                raise ValueError(f"invalid x_api identity for {slug}: fetch URL, homepage, and username must match")
    if kind == "web":
        if schema_version < 2:
            raise ValueError(f"kind='web' requires sources.toml schema_version=2: {slug}")
        web_runtime_keys = WEB_RUNTIME_META_KEYS & meta.keys()
        if web_runtime_keys:
            raise ValueError(f"invalid web configuration for {slug}: runtime parser keys are code-owned")
        from ..fetcher.web import WEB_SOURCE_REGISTRY

        spec = WEB_SOURCE_REGISTRY.get(slug)
        if spec is None:
            raise ValueError(f"unregistered web source: {slug}")
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
        optional=optional,
        required_env=required_env,
        wechat_only=wechat_only,
        public_url_override=public_url_override,
    )


def _unconfigured_placeholder_env(raw: dict[str, Any], *, schema_version: int) -> str | None:
    """The env var this source needs, when it is a placeholder URL and unset.

    An *optional* source whose feed URL is entirely one env-var placeholder is
    skipped rather than failing the whole load, so a checkout without that
    subscription still runs every other source. A source that is not declared
    optional keeps failing loudly, because there nothing else would notice.
    """
    if schema_version < 2 or raw.get("optional") is not True:
        return None
    raw_url = raw.get("fetch_url")
    if raw_url is None:
        return None
    match = ENV_PLACEHOLDER_RE.match(str(raw_url))
    if match is None:
        return None
    env_name = match.group(1)
    return None if os.environ.get(env_name, "").strip() else env_name


def load_sources(path: Path) -> list[SourceConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    schema_version = data.get("schema_version", 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version not in {1, 2}:
        raise ValueError("sources.toml schema_version must be 1 or 2")
    raw_sources = data.get("source", [])
    if not isinstance(raw_sources, list):
        raise ValueError("sources.toml must contain [[source]] entries")

    sources: list[SourceConfig] = []
    for raw in raw_sources:
        unset_env = _unconfigured_placeholder_env(raw, schema_version=schema_version)
        if unset_env is not None:
            LOGGER.warning(
                "source %s skipped: %s is not set; set it to enable that feed",
                raw.get("slug", "<unknown>"),
                unset_env,
            )
            continue
        sources.append(
            _validate_source(
                raw,
                schema_version=schema_version,
            )
        )
    seen: set[str] = set()
    for source in sources:
        if source.slug in seen:
            raise ValueError(f"duplicate source slug: {source.slug}")
        seen.add(source.slug)
    return sources
