from __future__ import annotations

import contextlib
import pathlib
import re

import httpx

from airadar.presentation.media import _media_assets_from_html, proxy_image_url
from airadar.web.routes import media as media_module
from airadar.web.routes.media import proxy_image

WX = "https://mmbiz.qpic.cn/sz_mmbiz_png/abc123/640?wx_fmt=png"
TW = "https://pbs.twimg.com/media/abc.jpg"


class _FakeClient:
    """Stand-in for httpx.Client that records how it was constructed."""

    last_kwargs: dict = {}
    requested: list = []

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        type(self).last_kwargs = kwargs
        type(self).requested = []
        self._responses = list(type(self).script)

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc) -> bool:  # noqa: ANN002
        return False

    @contextlib.contextmanager
    def stream(self, method, url, **kwargs):  # noqa: ANN001, ANN003, ANN201
        type(self).requested.append(url)
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        yield item


def _install_client(monkeypatch, *script) -> type[_FakeClient]:  # noqa: ANN001
    cls = type("_C", (_FakeClient,), {"script": list(script)})
    monkeypatch.setattr("airadar.web.routes.media.httpx.Client", cls)
    return cls


def _redirect(location: str):  # noqa: ANN201
    resp = httpx.Response(302, headers={"location": location}, request=httpx.Request("GET", "https://x/"))
    return resp



def test_extracts_data_src_when_src_is_placeholder() -> None:
    html = f'<p><img src="data:image/svg+xml,%3Csvg%3E" data-src="{WX}" class="js_img_placeholder"></p>'
    assets = _media_assets_from_html(html)
    assert len(assets) == 1
    # Real WeChat URL recovered from data-src and routed through the proxy.
    assert assets[0]["url"].startswith("/img?url=")
    assert "mmbiz.qpic.cn" in assets[0]["url"]


def test_direct_real_src_is_kept() -> None:
    html = '<img src="https://img.ithome.com/a.jpg">'
    assets = _media_assets_from_html(html)
    assert assets == [{"type": "image", "url": "https://img.ithome.com/a.jpg"}]


def test_data_uri_placeholder_without_lazy_is_dropped() -> None:
    assert _media_assets_from_html('<img src="data:image/svg+xml,%3Csvg%3E">') == []


def test_proxy_image_url_only_wraps_wechat_cdn() -> None:
    assert proxy_image_url(WX).startswith("/img?url=")
    # Non-WeChat hosts already load fine — left untouched, no proxy load.
    assert proxy_image_url("https://www.google.com/s2/favicons?domain=x") == (
        "https://www.google.com/s2/favicons?domain=x"
    )
    assert proxy_image_url("https://img.ithome.com/a.jpg") == "https://img.ithome.com/a.jpg"
    assert proxy_image_url(None) is None


def test_proxy_image_url_is_idempotent() -> None:
    once = proxy_image_url(WX)
    assert proxy_image_url(once) == once


def test_proxy_image_url_rejects_lookalike_host() -> None:
    # SSRF/spoof guard: only genuine qpic.cn subdomains are proxied.
    assert proxy_image_url("https://qpic.cn.evil.com/x.png") == "https://qpic.cn.evil.com/x.png"
    assert proxy_image_url("https://evilqpic.cn/x.png") == "https://evilqpic.cn/x.png"


def _fake_response(status: int, ctype: str, body: bytes) -> httpx.Response:
    return httpx.Response(status_code=status, headers={"content-type": ctype}, content=body)


def test_proxy_route_serves_allowed_image(monkeypatch) -> None:  # noqa: ANN001
    _install_client(monkeypatch, _fake_response(200, "image/png", b"\x89PNG\r\n"))
    resp = proxy_image(url=WX)
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.body == b"\x89PNG\r\n"
    assert "max-age" in resp.headers.get("cache-control", "")


def test_proxy_route_rejects_disallowed_host(monkeypatch) -> None:  # noqa: ANN001
    called = False

    def _boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        nonlocal called
        called = True
        raise AssertionError("must not fetch disallowed host")

    monkeypatch.setattr("airadar.web.routes.media.httpx.Client", _boom)
    assert proxy_image(url="http://169.254.169.254/latest/meta-data").status_code == 404
    assert proxy_image(url="https://img.ithome.com/a.jpg").status_code == 404
    assert called is False


def test_proxy_route_rejects_non_image(monkeypatch) -> None:  # noqa: ANN001
    _install_client(monkeypatch, _fake_response(200, "text/html", b"<html>nope</html>"))
    assert proxy_image(url=WX).status_code == 404


def test_proxy_route_handles_upstream_error(monkeypatch) -> None:  # noqa: ANN001
    def _raise(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise httpx.ConnectError("boom")

    _install_client(monkeypatch, httpx.ConnectError("boom"))
    # Contract: every failure is 404 so the frontend onerror hides the image.
    # A 5xx would render as a broken image instead.
    assert proxy_image(url=WX).status_code == 404


def test_twimg_requires_egress_proxy_and_never_falls_back_to_direct(monkeypatch) -> None:  # noqa: ANN001
    """Shanghai has no route to twimg: a direct attempt would hang, not fail fast."""
    monkeypatch.setattr("airadar.web.routes.media.read_value", lambda _k: "")
    cls = _install_client(monkeypatch, _fake_response(200, "image/png", b"\x89PNG"))
    assert proxy_image(url=TW).status_code == 404
    # The decisive part: no request was issued at all.
    assert cls.requested == []


def test_twimg_goes_through_proxy_and_wechat_does_not(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("airadar.web.routes.media.read_value", lambda _k: "http://user:pw@sg:39147")

    cls = _install_client(monkeypatch, _fake_response(200, "image/png", b"\x89PNG"))
    assert proxy_image(url=TW).status_code == 200
    assert cls.last_kwargs["proxy"] == "http://user:pw@sg:39147"

    cls = _install_client(monkeypatch, _fake_response(200, "image/png", b"\x89PNG"))
    assert proxy_image(url=WX).status_code == 200
    # WeChat CDN is reachable directly; routing it through Singapore would be a
    # pointless detour, so this branch must carry no proxy.
    assert cls.last_kwargs["proxy"] is None


def test_both_branches_ignore_process_proxy_environment(monkeypatch) -> None:  # noqa: ANN001
    """trust_env=True would let a stray HTTPS_PROXY silently reroute these."""
    monkeypatch.setattr("airadar.web.routes.media.read_value", lambda _k: "http://sg:39147")
    for target in (TW, WX):
        cls = _install_client(monkeypatch, _fake_response(200, "image/png", b"\x89PNG"))
        proxy_image(url=target)
        assert cls.last_kwargs["trust_env"] is False


def test_redirect_host_is_validated_before_the_next_hop_is_issued(monkeypatch) -> None:  # noqa: ANN001
    """Validating only the final URL still lets the open-redirect request out."""
    monkeypatch.setattr("airadar.web.routes.media.read_value", lambda _k: "http://sg:39147")
    cls = _install_client(
        monkeypatch,
        _redirect("http://169.254.169.254/latest/meta-data"),
        _fake_response(200, "image/png", b"\x89PNG"),  # must never be reached
    )
    assert proxy_image(url=TW).status_code == 404
    # Exactly one request: the original. The redirect target was never fetched.
    assert cls.requested == [TW]
    # And the property that makes per-hop validation possible at all: httpx must
    # not follow redirects itself, or it would issue the next hop before we look.
    # (The fake client ignores this flag, so assert the construction directly.)
    assert cls.last_kwargs["follow_redirects"] is False


def test_redirect_within_allowlist_is_followed(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("airadar.web.routes.media.read_value", lambda _k: "http://sg:39147")
    other = "https://pbs.twimg.com/media/moved.jpg"
    cls = _install_client(monkeypatch, _redirect(other), _fake_response(200, "image/png", b"\x89PNG"))
    assert proxy_image(url=TW).status_code == 200
    assert cls.requested == [TW, other]


def test_redirect_loop_is_bounded(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("airadar.web.routes.media.read_value", lambda _k: "http://sg:39147")
    hops = [_redirect(TW) for _ in range(10)]
    cls = _install_client(monkeypatch, *hops)
    assert proxy_image(url=TW).status_code == 404
    assert len(cls.requested) <= 4  # _MAX_REDIRECTS + 1


def test_proxy_unreachable_and_bad_proxy_auth_are_404_not_5xx(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("airadar.web.routes.media.read_value", lambda _k: "http://sg:39147")
    for failure in (
        httpx.ProxyError("proxy refused"),
        httpx.ConnectTimeout("blackhole"),
        httpx.ReadTimeout("slow"),
    ):
        _install_client(monkeypatch, failure)
        assert proxy_image(url=TW).status_code == 404
    # A proxy that answers 407 is an upstream non-200, not an exception.
    _install_client(monkeypatch, _fake_response(407, "text/plain", b"auth required"))
    assert proxy_image(url=TW).status_code == 404


def test_userinfo_cannot_smuggle_a_disallowed_host_past_the_allowlist(monkeypatch) -> None:  # noqa: ANN001
    """netloc is `[userinfo@]host[:port]`; splitting on ":" yields the userinfo.

    `http://mmbiz.qpic.cn:80@169.254.169.254/` used to read as host
    `mmbiz.qpic.cn` and pass, while the request went to the cloud metadata
    service. Checking a different string than the one connected to is how an
    allowlist becomes a blind SSRF — so assert on the two that matter: the
    metadata endpoint and this process's own loopback.
    """
    cls = _install_client(monkeypatch)  # no scripted response: a request would blow up
    for smuggled in (
        "http://mmbiz.qpic.cn:80@169.254.169.254/latest/meta-data",
        "https://pbs.twimg.com:443@127.0.0.1:8000/admin",
        "https://pbs.twimg.com@[::1]/",
    ):
        assert proxy_image(url=smuggled).status_code == 404, smuggled
    assert cls.requested == []  # and crucially, nothing was ever requested


def test_oversize_body_is_cut_off_mid_transfer_not_after_buffering(monkeypatch) -> None:  # noqa: ANN001
    """A cap applied after the body is in memory caps the reply, not the cost.

    Deliberately NOT an httpx.Response subclass: its __init__ calls read(),
    which is b"".join(self.iter_bytes()), so an overridden iter_bytes gets
    drained at construction time and the meter reads ~1000 chunks no matter
    what the route does. A stand-in keeps the count attributable to the route.
    """
    pulled = []

    class _Streaming:
        is_redirect = False
        status_code = 200
        headers = {"content-type": "image/png"}

        def iter_bytes(self, chunk_size=None):  # noqa: ANN001, ANN201
            for i in range(1000):
                pulled.append(i)
                yield b"x" * (1024 * 1024)  # 1 MiB each; cap is 10 MiB

    _install_client(monkeypatch, _Streaming())
    assert proxy_image(url=WX).status_code == 404
    # Stopped as soon as the cap was crossed, instead of buffering 1000 MiB.
    assert len(pulled) <= 12, f"pulled {len(pulled)} MiB before giving up"


def test_body_under_the_cap_is_served_whole(monkeypatch) -> None:  # noqa: ANN001
    """Negative control: the cap must not truncate legitimate images."""

    class _Small:
        is_redirect = False
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def iter_bytes(self, chunk_size=None):  # noqa: ANN001, ANN201
            yield b"\xff\xd8"
            yield b"body"

    _install_client(monkeypatch, _Small())
    resp = proxy_image(url=WX)
    assert resp.status_code == 200 and resp.body == b"\xff\xd8body"


def _reasons(caplog) -> list[str]:  # noqa: ANN001
    return [
        record.getMessage().split("reason=", 1)[1].split(" ", 1)[0]
        for record in caplog.records
        if "img fetch failed" in record.getMessage()
    ]


def test_each_failure_mode_logs_a_distinct_reason(monkeypatch, caplog) -> None:  # noqa: ANN001
    """Every failure returns 404, so the reason token is the only thing that
    separates them.

    2026-08-18 a user-reported missing image could not be attributed: the
    access log showed `404 Not Found` and the failure paths wrote nothing, so
    "the egress tunnel timed out" and "twimg returned 404 for that image" were
    indistinguishable after the fact. Assert the tokens differ per mode —
    asserting only "something was logged" would pass even if every path logged
    the same word, which is exactly the state that made the incident unsolvable.
    """
    monkeypatch.setenv("AI_RADAR_IMG_PROXY_URL", "http://user:pw@127.0.0.1:1/")
    caplog.set_level("WARNING", logger="airadar.web.routes.media")

    cases = {}

    caplog.clear()
    assert proxy_image(url="https://img.ithome.com/a.jpg").status_code == 404
    cases["host_not_allowed"] = _reasons(caplog)

    caplog.clear()
    monkeypatch.delenv("AI_RADAR_IMG_PROXY_URL", raising=False)
    assert proxy_image(url=TW).status_code == 404
    cases["no_egress_proxy_configured"] = _reasons(caplog)
    monkeypatch.setenv("AI_RADAR_IMG_PROXY_URL", "http://user:pw@127.0.0.1:1/")

    caplog.clear()
    _install_client(monkeypatch, _fake_response(404, "text/html", b"nope"))
    assert proxy_image(url=WX).status_code == 404
    cases["upstream_rejected"] = _reasons(caplog)

    caplog.clear()
    _install_client(monkeypatch, httpx.ConnectTimeout("slow"))
    assert proxy_image(url=WX).status_code == 404
    cases["transport_error"] = _reasons(caplog)

    caplog.clear()
    _install_client(monkeypatch, _redirect("https://169.254.169.254/x.png"))
    assert proxy_image(url=WX).status_code == 404
    cases["redirect_host_not_allowed"] = _reasons(caplog)

    caplog.clear()
    _install_client(monkeypatch, _fake_response(200, "image/png", b"x" * (10 * 1024 * 1024 + 1)))
    assert proxy_image(url=WX).status_code == 404
    cases["oversized"] = _reasons(caplog)

    caplog.clear()
    _install_client(monkeypatch, _redirect(""))
    assert proxy_image(url=WX).status_code == 404
    cases["redirect_without_location"] = _reasons(caplog)

    caplog.clear()
    _install_client(monkeypatch, *[_redirect(WX)] * 4)
    assert proxy_image(url=WX).status_code == 404
    cases["redirect_limit"] = _reasons(caplog)

    caplog.clear()
    assert proxy_image(url="http://[").status_code == 404
    cases["unparsable_url"] = _reasons(caplog)

    for expected, observed in cases.items():
        assert observed == [expected], f"{expected!r} logged {observed!r}"
    # The whole point: every failure mode gets its own token. Counting distinct
    # tokens is what makes collapsing two of them fail this test — asserting
    # only "each logged something" would pass on the very state that made the
    # 2026-08-18 incident unattributable.
    assert len({v[0] for v in cases.values()}) == len(cases)
    # Every _fail reason token in the module must be exercised above; a new
    # failure mode added without a case here would otherwise ship untested.
    source = pathlib.Path(media_module.__file__).read_text(encoding="utf-8")
    declared = set(re.findall(r'_fail\(\s*"([a-z_]+)"', source))
    assert declared == set(cases), f"untested reasons: {declared - set(cases)}"


def test_malformed_url_is_404_not_500(monkeypatch, caplog) -> None:  # noqa: ANN001
    """A CR/LF in the path passes the host allowlist and blows up inside httpx.

    httpx.InvalidURL inherits from Exception, not HTTPError, so it escaped the
    handler as a 500 — from a public query parameter, and against ADR-057's
    contract that every failure is a 404 the frontend can hide.
    """
    caplog.set_level("WARNING", logger="airadar.web.routes.media")
    for evil in (
        "https://mmbiz.qpic.cn/x.png\r\n2026-01-01 WARNING injected",
        "https://mmbiz.qpic.cn/\x00.png",
    ):
        assert proxy_image(url=evil).status_code == 404, evil
    logged = "\n".join(r.getMessage() for r in caplog.records)
    # and the log stays one line per failure: hostname parsing strips the CRLF
    assert "injected" not in logged
    assert len([line for line in logged.splitlines() if line.strip()]) == len(caplog.records)


def test_failure_log_never_carries_proxy_credentials(monkeypatch, caplog) -> None:  # noqa: ANN001
    """The egress proxy URL holds a password; a failure log is exactly where a
    careless f-string would leak it, and the twimg path fails often."""
    monkeypatch.setenv("AI_RADAR_IMG_PROXY_URL", "http://tunneluser:s3cr3t@127.0.0.1:39148/")
    caplog.set_level("WARNING", logger="airadar.web.routes.media")
    _install_client(monkeypatch, httpx.ConnectTimeout("slow"))
    assert proxy_image(url=TW).status_code == 404
    # Sweep every failure mode, not just this one: the leak would come from a
    # careless detail= at any one of them, and checking a single path leaves
    # the other seven free to leak.
    _install_client(monkeypatch, _fake_response(403, "text/html", b"no"))
    assert proxy_image(url=TW).status_code == 404
    _install_client(monkeypatch, _redirect("https://169.254.169.254/x.png"))
    assert proxy_image(url=TW).status_code == 404
    assert proxy_image(url="https://img.ithome.com/a.jpg").status_code == 404

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "s3cr3t" not in logged
    assert "tunneluser" not in logged
    assert "39148" not in logged
    # and it still said something useful
    assert "transport_error" in logged
    assert "ConnectTimeout" in logged


def test_unparsable_url_is_404_not_500() -> None:
    """`urlsplit` raises on inputs like "http://[", and that parse happens
    before the fetch try block — so it escaped as a 500 from a public query
    parameter, same class of bug as the CR/LF one but a different code path."""
    assert proxy_image(url="http://[").status_code == 404


def test_log_fields_cannot_forge_a_second_line(monkeypatch, caplog) -> None:  # noqa: ANN001
    """CR/LF is stripped by hostname parsing, but U+2028 is not.

    It survives urlparse and still renders as a line break in most log viewers,
    so a public query parameter could forge a convincing second entry. Assert
    on the rendered text, not on the record count: the record count is 1 either
    way, which is precisely why this bug reads as absent when you count records.
    """
    caplog.set_level("WARNING", logger="airadar.web.routes.media")
    # Every character str.splitlines() breaks on, not just the two that were
    # noticed by hand: \u2028 and \x85 were each missed once, a round apart.
    for breaker in ("\r\n", "\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
        caplog.clear()
        url = f"https://evil.example{breaker}WARNING forged=true/path"
        assert proxy_image(url=url).status_code == 404, breaker
        rendered = "\n".join(r.getMessage() for r in caplog.records)
        assert len(rendered.strip().splitlines()) == 1, f"{breaker!r} forged a line: {rendered!r}"
        assert breaker not in rendered, breaker


def test_upstream_content_type_cannot_leak_proxy_credentials(monkeypatch, caplog) -> None:  # noqa: ANN001
    """`content_type` is copied from an upstream response header, and a proxy
    answering 407 picks that value — so the one logged field this module does
    not author is exactly the one that can carry `user:pass@host`.

    The credential must sit in the media-type token itself, not in a parameter:
    the handler keeps only `split(";", 1)[0]`, so a `; via=...` parameter never
    reaches the log at all. Asserting the parameter form would pass without the
    scrubber doing anything — a test green for the wrong reason.
    """
    caplog.set_level("WARNING", logger="airadar.web.routes.media")
    _install_client(
        monkeypatch,
        _fake_response(407, "//tunneluser:s3cr3t@127.0.0.1:39148/nope", b"no"),
    )
    assert proxy_image(url=WX).status_code == 404
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "s3cr3t" not in logged
    assert "tunneluser" not in logged
    assert "<redacted>" in logged


def test_application_logger_is_wired_to_a_timestamped_handler() -> None:
    """Formatting the "default" formatter changes nothing unless our loggers
    actually reach it. They did not: "airadar.*" had no handler and neither did
    root, so records fell through to logging's lastResort — bare message, no
    timestamp. A reason token that cannot be lined up with the access-log entry
    it explains does not do its job."""
    from airadar.web.app import _uvicorn_log_config

    config = _uvicorn_log_config()
    assert config["loggers"]["airadar"]["handlers"] == ["default"]
    assert config["loggers"]["airadar"]["propagate"] is False
    assert "%(asctime)s" in config["formatters"]["default"]["fmt"]
    assert config["formatters"]["default"]["datefmt"] == "%Y-%m-%dT%H:%M:%S%z"
