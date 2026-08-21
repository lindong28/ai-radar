from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

X_STATUS_HOSTS = {"x.com", "twitter.com", "www.twitter.com", "nitter.net"}
# Rebuilding the query re-encodes reserved characters that were literal in the
# original — a WeChat article link carries base64 `__biz=…==`, which comes back
# as `%3D%3D`. Whether WeChat's parser accepts that is not something we have
# evidence for either way, and the link is what a reader clicks, so leave these
# URLs exactly as published unless a tracking parameter actually has to go.
VERBATIM_QUERY_HOSTS = {"mp.weixin.qq.com"}


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

    parsed_query = parse_qsl(parts.query, keep_blank_values=True)
    query = [(key, val) for key, val in parsed_query if not key.startswith("utm_")]
    if netloc in VERBATIM_QUERY_HOSTS and len(query) == len(parsed_query):
        encoded_query = parts.query
    else:
        encoded_query = urlencode(query)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, encoded_query, "")), selected_extra
