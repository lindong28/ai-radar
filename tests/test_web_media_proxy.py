from __future__ import annotations

import httpx

from airadar.presentation.media import _media_assets_from_html, proxy_image_url
from airadar.web.routes.media import proxy_image

WX = "https://mmbiz.qpic.cn/sz_mmbiz_png/abc123/640?wx_fmt=png"


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
    monkeypatch.setattr(
        "airadar.web.routes.media.httpx.get",
        lambda *a, **k: _fake_response(200, "image/png", b"\x89PNG\r\n"),
    )
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

    monkeypatch.setattr("airadar.web.routes.media.httpx.get", _boom)
    assert proxy_image(url="http://169.254.169.254/latest/meta-data").status_code == 404
    assert proxy_image(url="https://img.ithome.com/a.jpg").status_code == 404
    assert called is False


def test_proxy_route_rejects_non_image(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "airadar.web.routes.media.httpx.get",
        lambda *a, **k: _fake_response(200, "text/html", b"<html>nope</html>"),
    )
    assert proxy_image(url=WX).status_code == 404


def test_proxy_route_handles_upstream_error(monkeypatch) -> None:  # noqa: ANN001
    def _raise(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("airadar.web.routes.media.httpx.get", _raise)
    assert proxy_image(url=WX).status_code == 502
