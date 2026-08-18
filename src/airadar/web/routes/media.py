from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...presentation.media import image_url_needs_proxy
from ...runtime_env import read_value

router = APIRouter()

# Browser UA — some CDNs 403 default httpx/library agents.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_CONNECT_TIMEOUT_SECONDS = 4.0
_FETCH_TIMEOUT_SECONDS = 10.0
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_REDIRECTS = 3
# Let Cloudflare's edge and the browser cache aggressively — image URLs are
# content-stable, so a long TTL keeps this off the origin after first fetch.
_CACHE_CONTROL = "public, max-age=604800, immutable"
# Hosts our serve host cannot reach directly; they are fetched through the
# egress proxy named by this variable. Shanghai has no route to twimg, so
# without the proxy the request would hang for the full timeout rather than
# fail fast — hence "no proxy configured" means 404, never a direct attempt.
_EGRESS_PROXY_ENV = "AI_RADAR_IMG_PROXY_URL"
_PROXY_REQUIRED_HOST_SUFFIXES = ("twimg.com",)


def _needs_egress_proxy(url: str) -> bool:
    # .hostname, not netloc.split(":") — see image_url_needs_proxy for why.
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in _PROXY_REQUIRED_HOST_SUFFIXES)


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.hostname) and parsed.scheme in {"http", "https"} and image_url_needs_proxy(url)


@router.get("/img", include_in_schema=False)
def proxy_image(url: str = Query(..., max_length=2048)) -> Response:
    """Same-origin proxy for image hosts the browser cannot load directly.

    Two classes of host go through here: the WeChat CDN (hotlink-blocked, but
    reachable from this host) and twimg (reachable only through the egress
    proxy). The host allowlist (image_host_needs_proxy) doubles as the SSRF
    guard, and it is re-applied to every redirect hop *before* that hop is
    requested — validating only the final URL would still have issued the
    request an open redirect pointed at.

    Every failure returns 404 so the frontend's onerror handler hides the
    image cleanly; a 5xx here would surface as a broken image instead.
    """
    if not _allowed(url):
        return Response(status_code=404)

    proxy = None
    if _needs_egress_proxy(url):
        proxy = (read_value(_EGRESS_PROXY_ENV) or "").strip() or None
        if not proxy:
            # Never fall back to a direct attempt: this host has no route to
            # twimg, so it would hang for the whole timeout on every image.
            return Response(status_code=404)

    timeout = httpx.Timeout(_FETCH_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
    try:
        # trust_env=False on both branches: the process environment must not be
        # able to silently reroute (or un-route) these fetches.
        with httpx.Client(proxy=proxy, trust_env=False, timeout=timeout, follow_redirects=False) as client:
            current = url
            for _ in range(_MAX_REDIRECTS + 1):
                # stream(), not get(): get() reads the whole body into memory
                # before any size check can run, so _MAX_IMAGE_BYTES would cap
                # what we *return* while a hostile or oversized response has
                # already consumed the memory, the worker and the proxy's
                # bandwidth. Here the cap aborts the transfer mid-flight.
                with client.stream("GET", current, headers={"User-Agent": _USER_AGENT}) as upstream:
                    if not upstream.is_redirect:
                        content_type = (
                            upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        )
                        if upstream.status_code != 200 or not content_type.startswith("image/"):
                            return Response(status_code=404)
                        body = bytearray()
                        for chunk in upstream.iter_bytes():
                            body.extend(chunk)
                            if len(body) > _MAX_IMAGE_BYTES:
                                return Response(status_code=404)
                        return Response(
                            content=bytes(body),
                            media_type=content_type,
                            headers={"Cache-Control": _CACHE_CONTROL},
                        )
                    location = upstream.headers.get("location", "")
                if not location:
                    return Response(status_code=404)
                current = urljoin(current, location)
                # Re-validate before the next hop is issued, not after.
                if not _allowed(current):
                    return Response(status_code=404)
            return Response(status_code=404)
    except httpx.HTTPError:
        # Proxy unreachable, connect/read timeout, reset, bad proxy auth — all
        # of it is "no image", not "server error".
        return Response(status_code=404)
