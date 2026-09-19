"""Regression tests for the 2026-09-19 security review fixes.

Each test pins one finding from `.local/security-review-20260919` (the review
itself is not tracked); the finding numbers are quoted so the mapping survives
the review file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.fetcher.feed_rules import normalized_entry_url
from airadar.fetcher.rss import _clean_author, parse_feed
from airadar.fetcher.wechat import is_scrapable_wechat_url, scrape_article
from airadar.sources.loader import SourceConfig
from airadar.web.app import create_app
from airadar.web.routes.search import SEARCH_QUERY_MAX_LENGTH, search_id_subquery

SOURCE = SourceConfig(slug="feed", name="Feed", url="https://example.test/feed.xml", tier="T1")


def _rss(*entries: tuple[str, str, str]) -> bytes:
    items = "".join(
        f"<item><title>{title}</title><link>{link}</link><author>{author}</author></item>"
        for title, link, author in entries
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{items}</channel></rss>'.encode()


# --- S3: feed <link> must be a fetchable http(s) URL -------------------------


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(document.domain)",
        "data:text/html,%3Cscript%3Ealert(1)%3C/script%3E",
        "about:blank",
        "ftp://example.test/file",
        "mailto:someone@example.test",
        "http:///no-host",
        "http://[::1",  # malformed: urljoin raises; must drop the entry, not the feed
    ],
)
def test_non_http_entry_links_are_dropped(link: str) -> None:
    assert normalized_entry_url(SOURCE, link) == ""


def test_relative_and_absolute_http_links_still_resolve() -> None:
    assert normalized_entry_url(SOURCE, "/post/1") == "https://example.test/post/1"
    # urljoin lower-cases the scheme; the point is that a mixed-case scheme
    # is still recognised as http(s) rather than dropped.
    assert normalized_entry_url(SOURCE, "HTTPS://Other.test/x") == "https://Other.test/x"


def test_parse_feed_skips_entries_whose_link_is_not_http() -> None:
    items = parse_feed(
        SOURCE,
        _rss(
            ("evil", "javascript:alert(1)", "a"),
            ("fine", "https://example.test/post/2", "b"),
        ),
    )
    assert [item.url for item in items] == ["https://example.test/post/2"]


# --- S13: author is bounded plain text --------------------------------------


def test_clean_author_strips_markup_and_bounds_length() -> None:
    assert _clean_author("<b onmouseover=alert(2)>Auth</b>") == "Auth"
    assert _clean_author("&lt;img src=x onerror=alert(1)&gt;Name") == "Name"
    assert _clean_author("  spaced   out \n name ") == "spaced out name"
    assert _clean_author("") is None
    assert _clean_author(None) is None
    assert len(_clean_author("x" * 1000) or "") == 200


def test_parse_feed_stores_cleaned_author() -> None:
    items = parse_feed(SOURCE, _rss(("t", "https://example.test/1", "&lt;b onmouseover=alert(2)&gt;Auth&lt;/b&gt;")))
    assert items[0].author == "Auth"


# --- S4: the headless browser only opens mp.weixin.qq.com -------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mp.weixin.qq.com/s/abc", True),
        ("http://mp.weixin.qq.com/s?__biz=x", True),
        ("https://MP.WEIXIN.QQ.COM/s/abc", True),
        ("https://mp.weixin.qq.com.evil.test/s/abc", False),
        ("https://evil.test/mp.weixin.qq.com/s", False),
        ("https://evil.test@mp.weixin.qq.com.evil.test/", False),
        ("javascript:alert(1)", False),
        ("file:///etc/passwd", False),
        ("about:blank", False),
        ("", False),
    ],
)
def test_is_scrapable_wechat_url(url: str, expected: bool) -> None:
    assert is_scrapable_wechat_url(url) is expected


def test_scrape_article_refuses_foreign_hosts_before_launching_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    def _must_not_launch(*args: object, **kwargs: object) -> None:
        raise AssertionError("WeChatScraper must not be constructed for a refused URL")

    monkeypatch.setattr("airadar.fetcher.wechat.WeChatScraper", _must_not_launch)

    result = scrape_article("https://evil.test/#js_content")

    assert result["success"] is False
    assert "refused" in result["error"]


# --- S7 / S8 / S9: request bounds on the public surface ---------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    return TestClient(create_app(db_path))


@pytest.mark.parametrize("path", ["/", "/all", "/wechat", "/curated"])
@pytest.mark.parametrize("limit", ["0", "-1", "-2", "abc"])
def test_html_page_routes_reject_out_of_range_limit(client: TestClient, path: str, limit: str) -> None:
    # limit=0 used to divide by zero in clamp_page (500); a negative limit
    # reached SQLite as `LIMIT -1`, which means "no limit".
    assert client.get(f"{path}?limit={limit}").status_code == 422


def test_html_page_routes_cap_limit_like_the_api(client: TestClient) -> None:
    assert client.get("/all?limit=101").status_code == 422
    assert client.get("/wechat?limit=501").status_code == 422
    assert client.get("/all?limit=100").status_code == 200


@pytest.mark.parametrize("q", ['"""', '" "', '"  "', '""""'])
def test_quote_only_search_is_treated_as_no_query(q: str) -> None:
    # `items_fts MATCH ''` is an FTS5 syntax error, which surfaced as a 500.
    # (Inputs shorter than 3 chars never reach the FTS branch; they take the
    # LIKE path, whose `%"%` pattern is a legal, harmless search.)
    assert search_id_subquery(q) == (None, [])


def test_quote_only_search_returns_200_on_every_search_route(client: TestClient) -> None:
    for path in ("/api/v1/timeline", "/api/v1/curated", "/all", "/"):
        assert client.get(path, params={"q": '"""'}).status_code == 200, path


def test_search_query_length_is_bounded(client: TestClient) -> None:
    too_long = "x" * (SEARCH_QUERY_MAX_LENGTH + 1)
    for path in ("/api/v1/timeline", "/api/v1/curated", "/api/v1/wechat", "/all", "/", "/wechat", "/curated"):
        assert client.get(path, params={"q": too_long}).status_code == 422, path
    assert client.get("/api/v1/timeline", params={"q": "x" * SEARCH_QUERY_MAX_LENGTH}).status_code == 200


def test_unknown_timeline_channel_is_ignored_not_cached(client: TestClient) -> None:
    from airadar.web.routes import timeline as timeline_module

    cache = timeline_module._timeline_total_cache
    before = len(cache._values)
    for i in range(5):
        assert client.get("/api/v1/timeline", params={"channel": f"bogus-{i}"}).status_code == 200
    # Every bogus channel collapses onto the same (no-channel) cache key.
    assert len(cache._values) <= before + 1
