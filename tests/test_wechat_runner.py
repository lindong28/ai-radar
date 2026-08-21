from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from airadar.db import migrate
from airadar.fetcher.dedup import FetchedItem, upsert_item
from airadar.fetcher.http_client import FeedResponse
from airadar.fetcher.runner import (
    _enrich_wechat_bodies,
    _wechat_avatar_cache_is_fresh,
    fetch_source,
    refresh_wechat_avatar,
)
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


def _cache_avatar(conn: sqlite3.Connection, account: str, avatar_url: str) -> None:
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES (?, ?, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')
        """,
        (account, avatar_url),
    )


def test_enrich_wechat_bodies_skips_already_stored_urls(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()
    stored = replace(item, content_text="Stored full body with guizang-social-card-skill")
    assert upsert_item(conn, stored) is True
    _cache_avatar(conn, "RSS Author", "https://mmbiz.qpic.cn/avatar.png")

    with patch("airadar.fetcher.runner.scrape_article", side_effect=AssertionError("should not scrape")):
        enriched = _enrich_wechat_bodies(conn, [item])

    assert enriched == [replace(item, content_text=stored.content_text, content_html=stored.content_html)]


def test_enrich_wechat_bodies_caches_avatar_for_stored_body_once_per_account(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    first = _item("https://mp.weixin.qq.com/s/first")
    second = _item("https://mp.weixin.qq.com/s/second")
    assert upsert_item(conn, replace(first, content_text="Stored first full body")) is True
    assert upsert_item(conn, replace(second, content_text="Stored second full body")) is True

    with patch(
        "airadar.fetcher.runner.scrape_article",
        return_value={
            "success": True,
            "author_avatar_url": "http://mmbiz.qpic.cn/rss-author.png",
            "content_html": "<div id='js_content'>Scraped body</div>",
            "content_text": "Scraped body",
        },
    ) as scrape:
        enriched = _enrich_wechat_bodies(conn, [first, second])

    scrape.assert_called_once_with(first.url)
    avatar = conn.execute(
        "SELECT avatar_url FROM wechat_account_avatars WHERE account='RSS Author'"
    ).fetchone()[0]
    assert avatar == "https://mmbiz.qpic.cn/rss-author.png"
    assert [item.content_text for item in enriched] == ["Stored first full body", "Stored second full body"]


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
            "author_avatar_url": "https://mmbiz.qpic.cn/scraped-author.png",
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
    avatar = conn.execute(
        "SELECT avatar_url FROM wechat_account_avatars WHERE account='RSS Author'"
    ).fetchone()[0]
    assert avatar == "https://mmbiz.qpic.cn/scraped-author.png"


def test_enrich_wechat_bodies_degrades_to_rss_item_on_scrape_failure(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()

    with patch("airadar.fetcher.runner.scrape_article", return_value={"success": False, "error": "blocked"}):
        enriched = _enrich_wechat_bodies(conn, [item])

    assert enriched == [item]


def test_enrich_wechat_bodies_scrapes_items_concurrently_and_keeps_failures(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    items = [
        replace(_item(f"https://mp.weixin.qq.com/s/{index}"), author=f"RSS Author {index}")
        for index in range(4)
    ]
    active = 0
    max_active = 0
    lock = threading.Lock()

    def blocked_scrape(url: str) -> dict[str, object]:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return {"success": False, "url": url, "error": "blocked"}
        finally:
            with lock:
                active -= 1

    with patch("airadar.fetcher.runner.scrape_article", side_effect=blocked_scrape) as scrape:
        enriched = _enrich_wechat_bodies(conn, items)

    assert scrape.call_count == len(items)
    assert max_active > 1
    assert enriched == items


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


def test_fetch_source_backfills_wechat_avatar_when_feed_not_modified(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    item = _item()
    assert upsert_item(conn, replace(item, content_text="Stored full body")) is True

    with (
        patch("airadar.fetcher.runner.fetch_feed", return_value=FeedResponse(status_code=304, body=b"", not_modified=True)),
        patch(
            "airadar.fetcher.runner.scrape_article",
            return_value={
                "success": True,
                "author_avatar_url": "https://mmbiz.qpic.cn/not-modified-avatar.png",
                "content_html": "<div id='js_content'>Full article body</div>",
                "content_text": "Full article body",
            },
        ) as scrape,
    ):
        summary = fetch_source(conn, _source())

    assert summary.error is None
    assert summary.fetched == 0
    assert summary.inserted == 0
    scrape.assert_called_once_with(item.url)
    avatar = conn.execute(
        "SELECT avatar_url FROM wechat_account_avatars WHERE account='RSS Author'"
    ).fetchone()[0]
    assert avatar == "https://mmbiz.qpic.cn/not-modified-avatar.png"


def test_failed_wechat_avatar_cache_expires_after_short_retry_window(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES ('RSS Author', NULL, ?, ?)
        """,
        (
            (now - timedelta(days=2, minutes=1)).isoformat().replace("+00:00", "Z"),
            (now - timedelta(days=2, minutes=1)).isoformat().replace("+00:00", "Z"),
        ),
    )

    assert _wechat_avatar_cache_is_fresh(conn, "RSS Author", now=now) is False


def test_successful_wechat_avatar_cache_remains_fresh_without_retry(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    _cache_avatar(conn, "RSS Author", "https://mmbiz.qpic.cn/avatar.png")

    assert _wechat_avatar_cache_is_fresh(conn, "RSS Author", now=now) is True


def test_refresh_wechat_avatar_scrapes_latest_account_article_and_updates_cache(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    old_item = _item("https://mp.weixin.qq.com/s/old")
    new_item = replace(
        _item("https://mp.weixin.qq.com/s/new"),
        published_at="2026-05-29T01:02:03Z",
        content_text="New RSS Title",
    )
    assert upsert_item(conn, old_item) is True
    assert upsert_item(conn, new_item) is True
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES ('RSS Author', NULL, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')
        """
    )

    with patch(
        "airadar.fetcher.runner.scrape_article",
        return_value={
            "success": True,
            "author_avatar_url": "http://mmbiz.qpic.cn/refreshed-avatar.png",
        },
    ) as scrape:
        avatar = refresh_wechat_avatar(conn, "RSS Author")

    scrape.assert_called_once_with(new_item.url)
    assert avatar == "https://mmbiz.qpic.cn/refreshed-avatar.png"
    cached = conn.execute(
        "SELECT avatar_url FROM wechat_account_avatars WHERE account='RSS Author'"
    ).fetchone()[0]
    assert cached == "https://mmbiz.qpic.cn/refreshed-avatar.png"


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


LONG_FORM_URL = (
    "https://mp.weixin.qq.com/s?__biz=MzIzNjc1NzUzMw==&mid=2247913187&idx=1&sn=5389abc"
)


def _second_wechat_source() -> SourceConfig:
    return SourceConfig(
        slug="wx_selfhosted",
        name="Self-hosted WeChat feed",
        url="http://127.0.0.1:8080/feed/all.xml",
        tier="T2",
        kind="wechat",
        homepage_url="https://mp.weixin.qq.com/",
    )


def _dual_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db([_source(), _second_wechat_source()], conn)
    return conn


def _stored_urls(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT url FROM items ORDER BY url")]


def test_second_wechat_feed_does_not_duplicate_an_article_the_first_already_carried(
    tmp_path: Path,
) -> None:
    conn = _dual_conn(tmp_path)
    incumbent = replace(_item(), author="量子位", title="世界模型进入“有声时代”：24FPS画面")
    assert upsert_item(conn, incumbent, wechat=True) is True

    # Same article from the other feed: unrelated URL, its own body, a title
    # differing only in the whitespace and full/half-width punctuation two
    # renderers disagree on, and a publish time seconds apart.
    candidate = replace(
        incumbent,
        source_id="wx_selfhosted",
        url=LONG_FORM_URL,
        title=" 世界模型进入“有声时代”:24FPS画面 ",
        published_at="2026-05-28T01:02:58Z",
        content_text="Full body served by the self-hosted feed",
    )
    assert upsert_item(conn, candidate, wechat=True) is False
    assert _stored_urls(conn) == [incumbent.url]


def test_second_wechat_feed_still_inserts_an_article_the_first_missed(tmp_path: Path) -> None:
    conn = _dual_conn(tmp_path)
    assert upsert_item(conn, replace(_item(), author="量子位", title="第一篇"), wechat=True) is True

    missed = replace(
        _item(),
        source_id="wx_selfhosted",
        url=LONG_FORM_URL,
        author="量子位",
        title="只有自建源发现的第二篇",
    )
    assert upsert_item(conn, missed, wechat=True) is True
    assert len(_stored_urls(conn)) == 2


def test_wechat_dedup_keeps_a_genuine_repost_of_the_same_title(tmp_path: Path) -> None:
    conn = _dual_conn(tmp_path)
    original = replace(_item(), author="量子位", title="量子位编辑作者招聘")
    assert upsert_item(conn, original, wechat=True) is True

    # The closest genuine repost measured in production is 3.3 hours later; the
    # dedup window must not reach it.
    repost = replace(
        original,
        source_id="wx_selfhosted",
        url=LONG_FORM_URL,
        published_at="2026-05-28T04:22:03Z",
    )
    assert upsert_item(conn, repost, wechat=True) is True
    assert len(_stored_urls(conn)) == 2


def test_a_plain_feed_source_goes_through_the_real_runner_without_wechat_dedup(
    tmp_path: Path,
) -> None:
    """Drive `fetch_source`, not `upsert_item` directly.

    Calling upsert_item without `wechat=True` only restates the caller's own
    behaviour; it stays green even if the runner starts passing wechat=True for
    every source. The branch that decides is `source.kind`, so exercise that.
    """
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    feed_source = SourceConfig(
        slug="plain_feed",
        name="Plain feed",
        url="https://example.test/feed.xml",
        tier="T2",
        kind="feed",
        homepage_url="https://example.test/",
    )
    sync_to_db([_source(), feed_source], conn)
    wechat_row = replace(_item(), author="量子位", title="同题")
    assert upsert_item(conn, wechat_row, wechat=True) is True

    same_title_from_a_feed = replace(
        wechat_row, source_id="plain_feed", url="https://example.test/a"
    )
    with (
        patch("airadar.fetcher.runner.fetch_feed", return_value=FeedResponse(status_code=200, body=b"<rss/>")),
        patch("airadar.fetcher.runner.parse_feed", return_value=[same_title_from_a_feed]),
    ):
        summary = fetch_source(conn, feed_source)

    assert summary.error is None
    assert summary.inserted == 1, "a non-wechat source must not be deduped against WeChat rows"


def test_long_form_wechat_urls_are_not_sent_to_the_scraper(tmp_path: Path) -> None:
    conn = _dual_conn(tmp_path)
    item = replace(_item(), source_id="wx_selfhosted", url=LONG_FORM_URL, author="量子位")

    # Assert on the call itself: the scraper runs inside a thread pool that
    # turns any exception into a failed-article dict, so a raising sentinel
    # would be swallowed and the test would pass either way.
    with patch(
        "airadar.fetcher.runner.scrape_article",
        return_value={"success": False, "error": "captcha"},
    ) as scrape:
        enriched = _enrich_wechat_bodies(conn, [item])

    scrape.assert_not_called()
    assert enriched == [item]


def test_a_disabled_sources_rows_do_not_block_the_remaining_feed(tmp_path: Path) -> None:
    """Switching a feed off must not take its articles down with it.

    `/wechat` only shows rows whose source is enabled, so a disabled source's
    rows are already invisible there. If dedup still matched them, turning one
    feed off would hide everything it happened to find first AND stop the other
    feed from ever bringing those articles back.
    """
    conn = _dual_conn(tmp_path)
    found_first_by_the_candidate = replace(
        _item(), source_id="wx_selfhosted", url=LONG_FORM_URL, author="量子位", title="只有候选源先发现的一篇"
    )
    assert upsert_item(conn, found_first_by_the_candidate, wechat=True) is True

    conn.execute("UPDATE sources SET enabled=0 WHERE id='wx_selfhosted'")
    incumbent_brings_it_later = replace(
        found_first_by_the_candidate,
        source_id="wx_guizang",
        url="https://mp.weixin.qq.com/s/incumbent-token",
    )
    assert upsert_item(conn, incumbent_brings_it_later, wechat=True) is True


def test_titles_differing_only_in_punctuation_are_not_merged(tmp_path: Path) -> None:
    """The two renderers were measured to agree on punctuation, so folding it
    away buys nothing and merges genuinely different articles for good."""
    conn = _dual_conn(tmp_path)
    first = replace(_item(), author="量子位", title="报告：1.0！")
    assert upsert_item(conn, first, wechat=True) is True

    different_article = replace(
        first, source_id="wx_selfhosted", url=LONG_FORM_URL, title="报告10", published_at="2026-05-28T01:04:03Z"
    )
    assert upsert_item(conn, different_article, wechat=True) is True
    assert len(_stored_urls(conn)) == 2


def test_a_repost_from_the_same_source_is_never_merged_by_title(tmp_path: Path) -> None:
    conn = _dual_conn(tmp_path)
    first = replace(_item(), author="量子位", title="量子位编辑作者招聘")
    assert upsert_item(conn, first, wechat=True) is True
    minutes_later = replace(
        first, url="https://mp.weixin.qq.com/s/second-posting", published_at="2026-05-28T01:03:03Z"
    )
    assert upsert_item(conn, minutes_later, wechat=True) is True
    assert len(_stored_urls(conn)) == 2
