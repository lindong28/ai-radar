from __future__ import annotations

from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...presentation.media import image_host_needs_proxy

router = APIRouter()

# Browser UA — some CDNs 403 default httpx/library agents.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_FETCH_TIMEOUT_SECONDS = 10.0
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Let Cloudflare's edge and the browser cache aggressively — image URLs are
# content-stable, so a long TTL keeps this off the origin after first fetch.
_CACHE_CONTROL = "public, max-age=604800, immutable"


@router.get("/img", include_in_schema=False)
def proxy_image(url: str = Query(..., max_length=2048)) -> Response:
    """Same-origin proxy for hotlink-blocked image hosts (WeChat CDN).

    The host allowlist (image_host_needs_proxy) is also the SSRF guard: only
    genuine WeChat CDN subdomains are ever fetched, so internal/link-local
    targets can never be reached. Any failure returns 404 so the frontend's
    onerror handler hides the image cleanly.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return Response(status_code=404)
    if not image_host_needs_proxy(parsed.netloc):
        return Response(status_code=404)

    try:
        upstream = httpx.get(
            url,
            timeout=_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except httpx.HTTPError:
        return Response(status_code=502)

    content_type = upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if upstream.status_code != 200 or not content_type.startswith("image/"):
        return Response(status_code=404)
    if len(upstream.content) > _MAX_IMAGE_BYTES:
        return Response(status_code=404)

    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Cache-Control": _CACHE_CONTROL},
    )
