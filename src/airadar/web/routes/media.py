from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...presentation.media import image_url_needs_proxy
from ...runtime_env import read_value

router = APIRouter()

_log = logging.getLogger(__name__)

# Failures must never be cached — see _fail for what happens when they are.
_NO_STORE = "no-store"


# Anything a log reader could mistake for a line break. \r and \n are the
# obvious ones and urlparse already strips those from a hostname, but several
# others survive parsing and still start a new line in most viewers — enough to
# forge a convincing second entry from a public query parameter. Rather than
# enumerate by intuition (U+2028 and U+0085 were each missed once, one round
# apart), the class is the union of C0, DEL, the whole C1 block and the Unicode
# separators — a superset of every character `str.splitlines()` breaks on.
_LOG_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
# `//user:pass@host` — the egress proxy URL's shape. It is never passed to
# _fail deliberately, but `content_type` is copied from an upstream response
# header, and a proxy answering 407 chooses that header's value.
_CREDENTIALS = re.compile(r"//[^/@\s]*:[^/@\s]*@")
_LOG_VALUE_MAX = 64


def _scrub(value: object) -> str:
    """Make one field safe to put in a log line.

    Two independent hazards, both from strings this module does not author:
    forged line breaks, and credentials arriving inside an upstream header.
    Truncation bounds the third — a single WARNING should not dwarf the
    access-log line it explains.
    """
    text = _LOG_UNSAFE.sub("", str(value))
    text = _CREDENTIALS.sub("//<redacted>@", text)
    if len(text) > _LOG_VALUE_MAX:
        text = text[:_LOG_VALUE_MAX] + "..."
    return text


def _fail(reason: str, host: str = "", **detail: object) -> Response:
    """Return the 404 every failure path returns — but say which one it was.

    Every failure mode here collapses to 404 by design (ADR-057: fail fast, and
    let the frontend's onerror hide the element). The cost is that the status
    code alone cannot separate "the egress tunnel timed out" from "twimg
    returned 404 for a deleted image" — 2026-08-18 a user-reported missing
    image could not be attributed because of exactly this, and the failure
    paths wrote nothing at all (586 lines of err log, zero img/timeout/httpx
    hits). The reason token is what makes the next occurrence diagnosable.

    Volume is bounded by what the access log already emits: uvicorn logs one
    line per request regardless, so this at most doubles the lines for failing
    requests rather than adding a new order of magnitude. Never log `proxy` —
    it carries credentials.
    """
    extra = " ".join(f"{k}={_scrub(v)}" for k, v in detail.items() if v != "")
    _log.warning("img fetch failed reason=%s host=%s %s", reason, _scrub(host) or "?", extra)
    # no-store, and it has to be explicit: the EdgeOne rule for /img follows the
    # origin's Cache-Control and applies its *default* strategy when the
    # header is absent — which negatively caches the 404. Measured right after that rule
    # went live: one 404 then three HITs on the same URL. Without this header a
    # single transient timeout freezes into "this image does not exist" for
    # every later visitor until the TTL lapses, which is strictly worse than
    # the intermittent failure the rule was added to reduce.
    return Response(status_code=404, headers={"Cache-Control": _NO_STORE})

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
    try:
        allowed = _allowed(url)
        request_host = (urlparse(url).hostname or "").lower()
    except ValueError as error:
        # urlsplit raises on inputs like "http://[" — outside the fetch try
        # block, so this used to escape as a 500 from a public query parameter.
        return _fail("unparsable_url", "", error=type(error).__name__)
    if not allowed:
        return _fail("host_not_allowed", request_host)

    proxy = None
    if _needs_egress_proxy(url):
        proxy = (read_value(_EGRESS_PROXY_ENV) or "").strip() or None
        if not proxy:
            # Never fall back to a direct attempt: this host has no route to
            # twimg, so it would hang for the whole timeout on every image.
            return _fail("no_egress_proxy_configured", request_host)

    timeout = httpx.Timeout(_FETCH_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)
    # Tracked outside the try so a transport failure names the hop that
    # actually failed: after a redirect, logging the *original* host points
    # the reader at the wrong CDN.
    current = url
    try:
        # trust_env=False on both branches: the process environment must not be
        # able to silently reroute (or un-route) these fetches.
        with httpx.Client(proxy=proxy, trust_env=False, timeout=timeout, follow_redirects=False) as client:
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
                            return _fail(
                                "upstream_rejected",
                                (urlparse(current).hostname or "").lower(),
                                status=upstream.status_code,
                                content_type=content_type or "-",
                            )
                        body = bytearray()
                        for chunk in upstream.iter_bytes():
                            body.extend(chunk)
                            if len(body) > _MAX_IMAGE_BYTES:
                                return _fail(
                                    "oversized",
                                    (urlparse(current).hostname or "").lower(),
                                    limit=_MAX_IMAGE_BYTES,
                                )
                        return Response(
                            content=bytes(body),
                            media_type=content_type,
                            headers={"Cache-Control": _CACHE_CONTROL},
                        )
                    location = upstream.headers.get("location", "")
                if not location:
                    return _fail("redirect_without_location", (urlparse(current).hostname or "").lower())
                current = urljoin(current, location)
                # Re-validate before the next hop is issued, not after.
                if not _allowed(current):
                    return _fail("redirect_host_not_allowed", (urlparse(current).hostname or "").lower())
            return _fail("redirect_limit", (urlparse(current).hostname or "").lower(), limit=_MAX_REDIRECTS)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        # Proxy unreachable, connect/read timeout, reset, bad proxy auth — all
        # of it is "no image", not "server error". The exception class is what
        # separates "the tunnel timed out" from the upstream saying no, so it
        # is the one detail worth carrying into the log.
        #
        # InvalidURL and ValueError are caught alongside HTTPError because
        # neither derives from it (InvalidURL inherits straight from Exception),
        # so they escaped as a 500 — breaking ADR-057's "every failure is a 404
        # the frontend can hide" contract from a public query parameter. A URL
        # with a CR/LF in its path reaches it: the host passes the allowlist and
        # httpx rejects the URL only when building the request.
        return _fail(
            "transport_error",
            (urlparse(current).hostname or "").lower(),
            error=type(error).__name__,
        )
