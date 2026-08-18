from __future__ import annotations

import contextlib

import httpx

from airadar.presentation.media import _media_assets_from_html, proxy_image_url
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
