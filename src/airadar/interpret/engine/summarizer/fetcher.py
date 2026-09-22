from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from airadar.egress import selector_httpx_client
from airadar.interpret.engine.summarizer.schema import ArticleDocument


class FetchError(RuntimeError):
    """Raised when a URL cannot be fetched into article text."""


@dataclass(frozen=True)
class FetchOptions:
    timeout: float = 20.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )


async def fetch_url(
    url: str,
    *,
    options: FetchOptions | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> ArticleDocument:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "bytetech.info" in host:
        raise FetchError("bytetech.info requires authenticated access; provide the article text or a local file.")

    options = options or FetchOptions()
    response_text = await _http_get(url, options=options, client_factory=client_factory)
    if "mp.weixin.qq.com" in host:
        return _parse_weixin(response_text, url=url)
    return _parse_generic(response_text, url=url)


async def _http_get(url: str, *, options: FetchOptions, client_factory: Callable[..., Any] | None) -> str:
    if client_factory is None:
        return await asyncio.to_thread(_managed_http_get, url, options)
    async with client_factory(
        timeout=options.timeout,
        follow_redirects=True,
        headers={"User-Agent": options.user_agent},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _managed_http_get(url: str, options: FetchOptions) -> str:
    with selector_httpx_client(
        callsite_id="interpret.engine.fetch_url", request_url=url,
        timeout=options.timeout, follow_redirects=True,
        headers={"User-Agent": options.user_agent},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _parse_weixin(document: str, *, url: str) -> ArticleDocument:
    title = _extract_meta(document, "og:title") or _extract_var(document, "msg_title") or _extract_title(document)
    source = _extract_var(document, "nickname") or "mp.weixin.qq.com"
    publish_date = _extract_meta(document, "publish_time") or _extract_var(document, "publish_time")
    content_html = _extract_element_by_id(document, "js_content")
    content = _html_to_text(content_html or document)
    if not content.strip():
        raise FetchError("Fetched WeChat article but could not extract #js_content text.")
    return ArticleDocument(
        title=title or "微信文章", content=content, source=source, url=url, publish_date=publish_date
    )


def _parse_generic(document: str, *, url: str) -> ArticleDocument:
    title = _extract_meta(document, "og:title") or _extract_title(document) or url
    content = ""
    try:
        import trafilatura

        content = trafilatura.extract(document, include_comments=False, include_tables=True) or ""
    except Exception:
        content = ""
    if not content.strip():
        content = _html_to_text(document)
    if not content.strip():
        raise FetchError("Fetched URL but could not extract readable article text.")

    host = urlparse(url).netloc.lower()
    publish_date = _extract_meta(document, "article:published_time") or _extract_meta(document, "pubdate")
    return ArticleDocument(title=title, content=content, source=host, url=url, publish_date=publish_date)


def _extract_title(document: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", document, flags=re.DOTALL | re.IGNORECASE)
    return html.unescape(_strip_tags(match.group(1))).strip() if match else ""


def _extract_meta(document: str, name: str) -> str:
    pattern = rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, document, flags=re.IGNORECASE)
    if not match:
        pattern = rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']'
        match = re.search(pattern, document, flags=re.IGNORECASE)
    return html.unescape(match.group(1)).strip() if match else ""


def _extract_var(document: str, name: str) -> str:
    match = re.search(rf"var\s+{re.escape(name)}\s*=\s*(['\"])(.*?)\1", document, flags=re.DOTALL)
    return html.unescape(match.group(2)).strip() if match else ""


def _extract_element_by_id(document: str, element_id: str) -> str:
    match = re.search(
        rf"<(?P<tag>[a-z0-9]+)[^>]+id=[\"']{re.escape(element_id)}[\"'][^>]*>(?P<body>.*?)</(?P=tag)>",
        document,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return match.group("body") if match else ""


def _html_to_text(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragment, flags=re.DOTALL | re.IGNORECASE)
    fragment = re.sub(r"</(p|div|h[1-6]|li|br|tr)>", "\n", fragment, flags=re.IGNORECASE)
    text = _strip_tags(fragment)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _strip_tags(fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", fragment)
