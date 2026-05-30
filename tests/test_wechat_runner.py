from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from airadar.db import migrate
from airadar.fetcher.dedup import FetchedItem, upsert_item
from airadar.fetcher.http_client import FeedResponse
from airadar.fetcher.runner import _enrich_wechat_bodies, fetch_source
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import sync_to_db


def _source(kind: str = "wechat") -> SourceConfig:
    return SourceConfig(
        slug="wx_guizang",
        name="歸藏的 AI 工具箱",
        url="http://localhost:4000/feeds/guizang.rss",
        tier="T2",
        kind=kind,
        homepage_url="https://mp.weixin.qq.com/",
    )


def _item(url: str = "https://mp.weixin.qq.com/s/KWtnToEa7K-13k002K-nRw") -> FetchedItem:
    return FetchedItem(
        source_id="wx_guizang",
        url=url,
        title="RSS Title",
        author="RSS Author",
        published_at="2026-05-28T01:02:03Z",
        fetched_at="2026-05-28T01:03:00Z",
        content_text="RSS Title",
        content_html="",
        extra={"guid": "seed-guid"},
    )


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db([_source()], conn)
    return conn


def test_enrich_wechat_bodies_skips_already_stored_urls(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()
    stored = replace(item, content_text="Stored full body with guizang-social-card-skill")
    assert upsert_item(conn, stored) is True

    with patch("airadar.fetcher.runner.scrape_article", side_effect=AssertionError("should not scrape")):
        enriched = _enrich_wechat_bodies(conn, [item])

    assert enriched == [replace(item, content_text=stored.content_text, content_html=stored.content_html)]


def test_enrich_wechat_bodies_recovers_existing_degraded_item(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()
    assert upsert_item(conn, item) is True

    with patch(
        "airadar.fetcher.runner.scrape_article",
        return_value={
            "success": True,
            "content_html": "<div id='js_content'>Recovered full article body</div>",
            "content_text": "Recovered full article body with guizang-social-card-skill",
        },
    ) as scrape:
        enriched = _enrich_wechat_bodies(conn, [item])

    scrape.assert_called_once_with(item.url)
    assert enriched[0].content_text == "Recovered full article body with guizang-social-card-skill"


def test_enrich_wechat_bodies_replaces_body_but_preserves_rss_metadata(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()

    with patch(
        "airadar.fetcher.runner.scrape_article",
        return_value={
            "success": True,
            "title": "Scraped Title",
            "author": "Scraped Author",
            "publish_time": "2026年05月28日",
            "content_html": "<div id='js_content'>Full article body</div>",
            "content_text": "Full article body with 28 个版式骨架",
        },
    ) as scrape:
        enriched = _enrich_wechat_bodies(conn, [item])

    scrape.assert_called_once_with(item.url)
    assert enriched[0].title == "RSS Title"
    assert enriched[0].author == "RSS Author"
    assert enriched[0].url == item.url
    assert enriched[0].published_at == item.published_at
    assert enriched[0].content_text == "Full article body with 28 个版式骨架"
    assert enriched[0].content_html == "<div id='js_content'>Full article body</div>"


def test_enrich_wechat_bodies_degrades_to_rss_item_on_scrape_failure(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()

    with patch("airadar.fetcher.runner.scrape_article", return_value={"success": False, "error": "blocked"}):
        enriched = _enrich_wechat_bodies(conn, [item])

    assert enriched == [item]


def test_fetch_source_enriches_wechat_items_before_upsert(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()

    with (
        patch("airadar.fetcher.runner.fetch_feed", return_value=FeedResponse(status_code=200, body=b"<rss/>")),
        patch("airadar.fetcher.runner.parse_feed", return_value=[item]),
        patch(
            "airadar.fetcher.runner.scrape_article",
            return_value={
                "success": True,
                "content_html": "<div id='js_content'>Full article body</div>",
                "content_text": "Full article body with guizang-social-card-skill",
            },
        ),
    ):
        summary = fetch_source(conn, _source())

    assert summary.error is None
    assert summary.fetched == 1
    assert summary.inserted == 1
    row = conn.execute("SELECT title, url, published_at, content_text, content_html FROM items").fetchone()
    assert row == (
        "RSS Title",
        item.url,
        "2026-05-28T01:02:03Z",
        "Full article body with guizang-social-card-skill",
        "<div id='js_content'>Full article body</div>",
    )


def test_fetch_source_preserves_existing_full_text_when_repeat_fetch_degrades(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()
    repeat_item = replace(item, fetched_at="2026-05-28T01:18:00Z")

    with (
        patch("airadar.fetcher.runner.fetch_feed", return_value=FeedResponse(status_code=200, body=b"<rss/>")),
        patch("airadar.fetcher.runner.parse_feed", return_value=[item]),
        patch(
            "airadar.fetcher.runner.scrape_article",
            return_value={
                "success": True,
                "content_html": "<div id='js_content'>Full article body</div>",
                "content_text": "Full article body with guizang-social-card-skill",
            },
        ),
    ):
        first = fetch_source(conn, _source())

    assert first.error is None
    assert first.inserted == 1

    with (
        patch("airadar.fetcher.runner.fetch_feed", return_value=FeedResponse(status_code=200, body=b"<rss/>")),
        patch("airadar.fetcher.runner.parse_feed", return_value=[repeat_item]),
        patch("airadar.fetcher.runner.scrape_article", return_value={"success": False, "error": "blocked"}),
    ):
        second = fetch_source(conn, _source())

    assert second.error is None
    row = conn.execute("SELECT content_text, content_html, fetched_at FROM items WHERE url=?", (item.url,)).fetchone()
    assert row == (
        "Full article body with guizang-social-card-skill",
        "<div id='js_content'>Full article body</div>",
        repeat_item.fetched_at,
    )
