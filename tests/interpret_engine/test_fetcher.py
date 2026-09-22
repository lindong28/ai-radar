import asyncio

import pytest

from airadar.interpret.engine.summarizer.fetcher import FetchError, fetch_url


class _Response:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, html: str) -> None:
        self.html = html

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str) -> _Response:
        return _Response(self.html)


def test_fetch_weixin_extracts_js_content() -> None:
    html = """
    <html>
      <head><meta property="og:title" content="微信测试文章"></head>
      <body><div id="js_content"><p>第一段</p><p>第二段</p></div></body>
    </html>
    """

    article = asyncio.run(fetch_url("https://mp.weixin.qq.com/s/example", client_factory=lambda **_: _Client(html)))

    assert article.title == "微信测试文章"
    assert article.source == "mp.weixin.qq.com"
    assert "第一段" in article.content
    assert "第二段" in article.content


def test_fetch_bytetech_returns_actionable_error() -> None:
    with pytest.raises(FetchError, match="provide the article text"):
        asyncio.run(fetch_url("https://bytetech.info/articles/abc#token"))


def test_default_url_fetch_uses_managed_selector(monkeypatch):
    from airadar.interpret.engine.summarizer import fetcher

    observed = {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, url):
            observed["url"] = url
            return _Response('<meta property="og:title" content="Managed"><div id="js_content">Body</div>')

    def factory(**kwargs):
        observed.update(kwargs)
        return Client()

    monkeypatch.setattr(fetcher, "selector_httpx_client", factory)
    article = asyncio.run(fetch_url("https://mp.weixin.qq.com/s/managed"))
    assert article.title == "Managed"
    assert observed["callsite_id"] == "interpret.engine.fetch_url"
    assert observed["request_url"] == observed["url"]
