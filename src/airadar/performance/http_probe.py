from __future__ import annotations

import json
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

from ..egress import open_external_url

TARGET_PATHS = {
    "homepage_http": "/",
    "wechat_list_http": "/api/v1/wechat?page=1&limit=50",
    "wechat_detail_http": "/wechat/{detail_slug}",
    "wechat_pagination_http": "/api/v1/wechat?page=2&limit=50",
}


@dataclass(frozen=True, slots=True)
class HttpMeasurement:
    request_url: str
    value_ms: float
    hard_failure: bool
    status: int
    identity_verified: bool


class _IdentityParser(HTMLParser):
    def __init__(self, css_class: str, attribute: str) -> None:
        super().__init__()
        self.css_class = css_class
        self.attribute = attribute
        self.identities: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if self.css_class in (values.get("class") or "").split() and values.get(self.attribute):
            self.identities.append(str(values[self.attribute]))


def _html_identities(body: bytes, css_class: str, attribute: str) -> list[str] | None:
    try:
        parser = _IdentityParser(css_class, attribute)
        parser.feed(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parser.identities


def _identity_matches(target: str, body: bytes, expected: dict[str, object] | None) -> bool:
    if target == "homepage_http":
        return expected is not None and _html_identities(body, "item-row", "data-item-id") == expected.get("item_ids")
    if target == "wechat_detail_http":
        return expected is not None and _html_identities(body, "wechat-detail", "data-item-id") == [
            str(expected.get("item_id"))
        ]
    if expected is None:
        return False
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        payload = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return False
    items = payload["items"]
    slugs = [item.get("slug") or item.get("id") for item in items if isinstance(item, dict)]
    return (
        payload.get("page") == expected.get("page")
        and payload.get("limit") == expected.get("limit")
        and payload.get("total") == expected.get("total")
        and len(slugs) == len(items)
        and slugs == expected.get("slugs")
    )


def measure_http_component(
    *,
    base_url: str,
    target: str,
    detail_slug: str,
    timeout_seconds: float,
    expected: dict[str, object] | None = None,
) -> HttpMeasurement:
    path = TARGET_PATHS[target].format(detail_slug=detail_slug)
    request_url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    started = time.perf_counter_ns()
    try:
        with open_external_url(
            request_url,
            callsite_id="performance.http_probe.measure",
            timeout=timeout_seconds,
        ) as response:
            value_ms = (time.perf_counter_ns() - started) / 1_000_000
            body = response.read()
            status = response.status
        identity_verified = _identity_matches(target, body, expected)
        return HttpMeasurement(request_url, value_ms, status >= 400 or not identity_verified, status, identity_verified)
    except Exception:
        return HttpMeasurement(request_url, (time.perf_counter_ns() - started) / 1_000_000, True, 0, False)
