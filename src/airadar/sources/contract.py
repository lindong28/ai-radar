from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CONTRACT_SCHEMA_VERSION = 2
KINDS = frozenset({"feed", "web", "x", "wechat"})
TIERS = frozenset({"T1", "T1.5", "T2"})
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]*[a-z0-9]$")
X_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
COMMON_FIELDS = frozenset(
    {
        "aihot_aliases",
        "derived_aihot_identity",
        "enabled",
        "fetch_url",
        "homepage_url",
        "icon_url",
        "kind",
        "ai_radar_main_timeline_member",
        "meta",
        "name",
        "slug",
        "tier",
    }
)
WECHAT_FIELDS = frozenset(
    {
        "optional",
        "public_url_override",
        "required_env",
        "wechat_only",
    }
)


def _http_url(value: object, *, field: str, slug: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid {field} for {slug}: expected string")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid {field} for {slug}: {value!r}")
    return value


def validate_source_contract(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "sources"}:
        raise ValueError("source contract must contain exact schema_version and sources fields")
    if payload.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ValueError(f"source contract schema_version must equal {CONTRACT_SCHEMA_VERSION}")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("source contract sources must be a non-empty list")

    from ..fetcher.web import WEB_SOURCE_REGISTRY

    identities: set[str] = set()
    slugs: set[str] = set()
    web_slugs: set[str] = set()
    public_label_owners: dict[str, str] = {}
    wechat_envs: set[str] = set()
    for index, value in enumerate(sources):
        if not isinstance(value, dict):
            raise ValueError(f"source contract row {index} must be an object")
        row = value
        slug = row.get("slug")
        kind = row.get("kind")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise ValueError(f"invalid source contract slug at row {index}: {slug!r}")
        if kind not in KINDS:
            raise ValueError(f"invalid source contract kind for {slug}: {kind!r}")
        allowed_fields = COMMON_FIELDS | (WECHAT_FIELDS if kind == "wechat" else frozenset())
        unknown_fields = set(row) - allowed_fields
        missing_fields = allowed_fields - set(row)
        if unknown_fields:
            if kind != "wechat" and unknown_fields & WECHAT_FIELDS:
                raise ValueError(f"source contract {slug} optional fields are WeChat-only")
            raise ValueError(f"source contract {slug} has unknown fields: {sorted(unknown_fields)}")
        if missing_fields:
            label = "optional fields" if kind == "wechat" else "required fields"
            raise ValueError(f"source contract {slug} is missing {label}: {sorted(missing_fields)}")

        name = row.get("name")
        aliases = row.get("aihot_aliases")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"source contract {slug} needs a public name")
        if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise ValueError(f"source contract {slug} aliases must be non-empty strings")
        normalized_aliases = [alias.casefold() for alias in aliases]
        if len(normalized_aliases) != len(set(normalized_aliases)):
            raise ValueError(f"source contract {slug} aliases must be unique")
        if name.casefold() in normalized_aliases:
            raise ValueError(f"source contract {slug} aliases repeat the public name")

        identity = row.get("derived_aihot_identity")
        if not isinstance(identity, str) or not identity:
            raise ValueError(f"source contract {slug} needs an identity")
        if identity in identities:
            raise ValueError(f"duplicate source contract identity: {identity}")
        if slug in slugs:
            raise ValueError(f"duplicate source contract slug: {slug}")
        identities.add(identity)
        slugs.add(slug)
        for label in [name, *aliases]:
            normalized = label.casefold()
            owner = public_label_owners.get(normalized)
            if owner is not None and owner != identity:
                raise ValueError(
                    "cross-identity public name/alias collision: "
                    f"{label!r} belongs to both {owner} and {identity}"
                )
            public_label_owners[normalized] = identity

        if row.get("tier") not in TIERS:
            raise ValueError(f"invalid source contract tier for {slug}")
        if not isinstance(row.get("enabled"), bool) or not isinstance(row.get("ai_radar_main_timeline_member"), bool):
            raise ValueError(f"source contract {slug} enabled/ai_radar_main_timeline_member must be booleans")
        _http_url(row.get("homepage_url"), field="homepage_url", slug=slug)
        _http_url(row.get("icon_url"), field="icon_url", slug=slug)
        fetch_url = row.get("fetch_url")
        if kind == "wechat":
            if row.get("ai_radar_main_timeline_member") is not False:
                raise ValueError(f"wechat source {slug} cannot be a main source")
            if row.get("optional") is not True or row.get("wechat_only") is not True:
                raise ValueError(f"wechat source {slug} optional fields must declare optional and wechat_only")
            required_env = row.get("required_env")
            if not isinstance(required_env, str) or fetch_url != f"${{{required_env}}}":
                raise ValueError(f"wechat source {slug} required_env and fetch_url must match")
            if required_env in wechat_envs:
                raise ValueError(f"wechat source {slug} reuses required_env {required_env}")
            wechat_envs.add(required_env)
            _http_url(row.get("public_url_override"), field="public_url_override", slug=slug)
            if identity != f"extra:{slug}":
                raise ValueError(f"invalid wechat identity for {slug}")
        else:
            if row.get("ai_radar_main_timeline_member") is not True:
                raise ValueError(f"main source {slug} must declare ai_radar_main_timeline_member=true")
            _http_url(fetch_url, field="fetch_url", slug=slug)
            expected_identity = f"{kind}:{slug}"
            if kind == "x":
                meta = row.get("meta")
                if not isinstance(meta, dict) or set(meta) != {"adapter", "username"}:
                    raise ValueError(f"invalid X meta for {slug}")
                username = meta.get("username")
                if meta.get("adapter") != "x_api" or not isinstance(username, str) or not X_USERNAME_RE.fullmatch(username):
                    raise ValueError(f"invalid X adapter or username for {slug}")
                expected_identity = f"x:{username.casefold()}"
                expected_fetch = f"https://api.x.com/2/users/by/username/{username}"
                expected_homepage = f"https://x.com/{username}"
                if str(fetch_url).rstrip("/").casefold() != expected_fetch.casefold():
                    raise ValueError(f"invalid X fetch URL for {slug}")
                if str(row["homepage_url"]).rstrip("/").casefold() != expected_homepage.casefold():
                    raise ValueError(f"invalid X homepage URL for {slug}")
            elif row.get("meta") != {}:
                raise ValueError(f"non-X source {slug} meta must be empty")
            if identity != expected_identity:
                raise ValueError(f"invalid identity for {slug}: expected {expected_identity}")
            if kind == "web":
                web_slugs.add(slug)

    if web_slugs != set(WEB_SOURCE_REGISTRY):
        raise ValueError("web source slugs must exactly match the code-owned registry")
    if not wechat_envs:
        raise ValueError("source contract must contain at least one optional WeChat source")
    return payload


def load_source_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return validate_source_contract(payload)


def validate_source_union_receipt(
    payload: object,
    *,
    contract_path: Path,
) -> dict[str, Any]:
    fields = {
        "schema_version", "artifact_type", "status", "contract_sha256",
        "source_counts", "identities", "limitations",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError("invalid source union fields")
    if payload["schema_version"] != 1 or payload["artifact_type"] != "aihot_source_union":
        raise ValueError("invalid source union schema")
    if payload["status"] != "generated_current_contract_projection":
        raise ValueError("invalid source union status")
    contract = load_source_contract(contract_path)
    if payload["contract_sha256"] != hashlib.sha256(contract_path.read_bytes()).hexdigest():
        raise ValueError("source union contract hash mismatch")
    rows = [row for row in contract["sources"] if row["ai_radar_main_timeline_member"]]
    expected_counts = {
        "total": len(rows),
        "feed": sum(row["kind"] == "feed" for row in rows),
        "web": sum(row["kind"] == "web" for row in rows),
        "x": sum(row["kind"] == "x" for row in rows),
    }
    if payload["source_counts"] != expected_counts:
        raise ValueError("source union count summary drift")
    expected_identities = [
        {"derived_aihot_identity": row["derived_aihot_identity"], "slug": row["slug"]}
        for row in rows
    ]
    if payload["identities"] != expected_identities:
        raise ValueError("source union identity projection drift")
    if not isinstance(payload["limitations"], str) or not payload["limitations"]:
        raise ValueError("source union limitations must be non-empty")
    return payload
