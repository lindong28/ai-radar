from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

X_STATUS_HOSTS = {"x.com", "twitter.com", "www.twitter.com", "nitter.net"}


def canonicalize_item_url(url: str, extra: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    selected_extra = dict(extra or {})
    value = url.strip().replace("：//", "://")
    try:
        parts = urlsplit(value)
    except ValueError:
        return url, selected_extra
    if not parts.scheme or not parts.netloc:
        return url, selected_extra

    netloc = parts.netloc.lower()
    path_parts = [part for part in parts.path.split("/") if part]
    if netloc in X_STATUS_HOSTS and len(path_parts) >= 3 and path_parts[1] == "status" and path_parts[2].isdigit():
        canonical = urlunsplit(("https", "x.com", f"/{path_parts[0].lower()}/status/{path_parts[2]}", "", ""))
        if netloc == "nitter.net" and canonical != value and "original_url" not in selected_extra:
            selected_extra["original_url"] = url
        return canonical, selected_extra

    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if not key.startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), "")), selected_extra
