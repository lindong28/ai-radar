from __future__ import annotations

import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from airadar.db import migrate
from airadar.fetcher import http_client, runner
from airadar.fetcher.dedup import FetchedItem, content_hash, upsert_item
from airadar.fetcher.http_client import FeedResponse, fetch_feed
from airadar.fetcher.rss import parse_feed
from airadar.fetcher.runner import default_sources_path, fetch_all
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import sync_to_db

RSS_BYTES = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example</title>
    <item>
      <title>New LLM benchmark</title>
      <link>https://example.com/llm-benchmark</link>
      <author>Ada</author>
      <pubDate>Fri, 08 May 2026 01:02:03 GMT</pubDate>
      <description><![CDATA[This article discusses an LLM benchmark and API details.]]></description>
    </item>
  </channel>
</rss>
"""


def test_default_sources_path_points_to_repo_data_file() -> None:
    assert default_sources_path() == Path(__file__).resolve().parents[1] / "data" / "sources.toml"


def test_fetch_all_runs_passive_checkpoint_after_fetch_round(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    db_path = tmp_path / "radar.db"
    sources_path = tmp_path / "sources.toml"
    sources_path.write_text(
        """
        [[source]]
        slug = "example"
        name = "Example"
        url = "https://example.com/feed.xml"
        tier = "T2"
        enabled = true
        """,
        encoding="utf-8",
    )
    checkpointed_paths: list[Path | None] = []

    def fake_fetch_source_feed(source: SourceConfig) -> object:
        return runner._SourceFeedResult(source=source, response=FeedResponse(status_code=200, body=b"<rss/>"))

    monkeypatch.setattr(runner, "_fetch_source_feed", fake_fetch_source_feed)
    monkeypatch.setattr(runner, "parse_feed", lambda source, body: [])  # noqa: ARG005
    monkeypatch.setattr("airadar.fetcher.runner.db.checkpoint_db", checkpointed_paths.append)

    summary = fetch_all(sources_path, db_path)

    assert summary.attempted == 1
    assert summary.inserted == 0
    assert checkpointed_paths == [db_path]


def test_fetch_all_fetches_source_feeds_in_parallel_and_writes_on_main_thread(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    db_path = tmp_path / "radar.db"
    sources_path = tmp_path / "sources.toml"
    source_slugs = [f"source_{index}" for index in range(4)]
    sources_path.write_text(
        "\n".join(
            [
                f"""
        [[source]]
        slug = "{slug}"
        name = "{slug}"
        url = "https://example.com/{slug}.xml"
        tier = "T2"
        enabled = true
        """
                for slug in source_slugs
            ]
        ),
        encoding="utf-8",
    )
    active = 0
    max_active = 0
    lock = threading.Lock()
    main_thread = threading.get_ident()
    upsert_threads: list[int] = []

    def item_for(source: SourceConfig) -> FetchedItem:
        return FetchedItem(
            source_id=source.slug,
            url=f"https://example.com/{source.slug}/1",
            title=f"{source.slug} title",
            author=None,
            published_at="2026-05-08T01:02:03Z",
            fetched_at="2026-05-08T01:03:00Z",
            content_text=f"{source.slug} text",
            content_html=None,
            extra={},
        )

    def fake_fetch_source_feed(source: SourceConfig) -> object:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return runner._SourceFeedResult(
                source=source,
                response=FeedResponse(status_code=200, body=b"<rss/>"),
                items=[item_for(source)],
            )
        finally:
            with lock:
                active -= 1

    def fake_upsert_item(conn: sqlite3.Connection, item: FetchedItem) -> bool:
        assert threading.get_ident() == main_thread
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == len(source_slugs)
        upsert_threads.append(threading.get_ident())
        return True

    monkeypatch.setattr(runner, "_fetch_source_feed", fake_fetch_source_feed)
    monkeypatch.setattr(runner, "upsert_item", fake_upsert_item)
    monkeypatch.setattr(runner.db, "checkpoint_db", lambda path: None)

    summary = fetch_all(sources_path, db_path)

    assert [source.source_id for source in summary.sources] == source_slugs
    assert summary.inserted == len(source_slugs)
    assert max_active > 1
    assert upsert_threads == [main_thread] * len(source_slugs)


def test_fetch_all_continues_after_parallel_source_fetch_error(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    db_path = tmp_path / "radar.db"
    sources_path = tmp_path / "sources.toml"
    sources_path.write_text(
        """
        [[source]]
        slug = "a_good"
        name = "Good"
        url = "https://example.com/good.xml"
        tier = "T2"
        enabled = true

        [[source]]
        slug = "z_broken"
        name = "Broken"
        url = "https://example.com/broken.xml"
        tier = "T2"
        enabled = true
        """,
        encoding="utf-8",
    )

    def fake_fetch_source_feed(source: SourceConfig) -> object:
        if source.slug == "z_broken":
            return runner._SourceFeedResult(source=source, error="TimeoutException: blocked")
        return runner._SourceFeedResult(
            source=source,
            response=FeedResponse(status_code=200, body=b"<rss/>"),
            items=[
                FetchedItem(
                    source_id=source.slug,
                    url=f"https://example.com/{source.slug}/1",
                    title=f"{source.slug} title",
                    author=None,
                    published_at="2026-05-08T01:02:03Z",
                    fetched_at="2026-05-08T01:03:00Z",
                    content_text=f"{source.slug} text",
                    content_html=None,
                    extra={},
                )
            ],
        )

    monkeypatch.setattr(runner, "_fetch_source_feed", fake_fetch_source_feed)
    monkeypatch.setattr(runner, "upsert_item", lambda conn, item: True)  # noqa: ARG005
    monkeypatch.setattr(runner.db, "checkpoint_db", lambda path: None)

    summary = fetch_all(sources_path, db_path)

    assert [source.source_id for source in summary.sources] == ["a_good", "z_broken"]
    assert summary.inserted == 1
    assert summary.failed == 1
    assert summary.sources[1].error == "TimeoutException: blocked"


def test_parse_feed_extracts_entry_fields() -> None:
    source = SourceConfig(
        slug="example", name="Example", url="https://example.com/feed.xml", tier="T2", enabled=True, meta={}
    )

    entries = parse_feed(source, RSS_BYTES)

    assert len(entries) == 1
    assert entries[0].title == "New LLM benchmark"
    assert entries[0].url == "https://example.com/llm-benchmark"
    assert entries[0].author == "Ada"
    assert entries[0].published_at == "2026-05-08T01:02:03Z"
    assert "LLM benchmark" in entries[0].content_text


def test_fetch_feed_bypasses_proxy_for_loopback_urls(monkeypatch) -> None:  # noqa: ANN001
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        content = RSS_BYTES
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("airadar.fetcher.http_client.httpx.get", fake_get)
    source = SourceConfig(
        slug="local_wewe",
        name="Local WeWe",
        url="http://localhost:4000/feeds/MP_WXS_3540975510.rss",
        tier="T2",
        enabled=True,
        meta={},
        kind="wechat",
    )

    response = fetch_feed(source, sqlite3.connect(":memory:"))

    assert response.status_code == 200
    assert calls[0]["trust_env"] is False


def test_fetch_feed_keeps_environment_proxy_for_external_urls(monkeypatch) -> None:  # noqa: ANN001
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        content = RSS_BYTES
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs: object) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("airadar.fetcher.http_client.httpx.get", fake_get)
    source = SourceConfig(
        slug="external",
        name="External",
        url="https://example.com/feed.xml",
        tier="T2",
        enabled=True,
        meta={},
    )

    response = fetch_feed(source, sqlite3.connect(":memory:"))

    assert response.status_code == 200
    assert calls[0]["trust_env"] is True


def test_fetch_user_agent_defaults_to_neutral_value(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("AI_RADAR_SITE_DOMAIN", raising=False)

    assert http_client.USER_AGENT == "ai-radar/0.1"


def test_fetch_user_agent_uses_configured_site_domain(monkeypatch) -> None:  # noqa: ANN001
    owner_domain = "ai" + "planet.live"
    monkeypatch.setenv("AI_RADAR_SITE_DOMAIN", owner_domain)

    assert http_client.USER_AGENT == f"ai-radar/0.1 (+https://{owner_domain})"


def test_content_hash_normalizes_case_and_whitespace() -> None:
    assert content_hash(" Hello   LLM\nBenchmark ") == content_hash("hello llm benchmark")


def test_upsert_item_deduplicates_by_source_and_content_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db(
        [
            SourceConfig(
                slug="example", name="Example", url="https://example.com/feed.xml", tier="T2", enabled=True, meta={}
            )
        ],
        conn,
    )
    item = FetchedItem(
        source_id="example",
        url="https://example.com/1",
        title="One",
        author=None,
        published_at="2026-05-08T01:02:03Z",
        fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        content_text="An LLM benchmark with API examples.",
        content_html=None,
        extra={"guid": "1"},
    )

    assert upsert_item(conn, item) is True
    assert upsert_item(conn, item) is False
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_upsert_item_deduplicates_by_source_and_url_when_title_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db(
        [
            SourceConfig(
                slug="hn_ai", name="Hacker News AI/LLM", url="https://hnrss.org/newest?q=AI", tier="T2", enabled=True, meta={}
            )
        ],
        conn,
    )
    first = FetchedItem(
        source_id="hn_ai",
        url="https://x.com/example/status/1",
        title="AI psychosis discussion",
        author=None,
        published_at="2026-05-08T01:02:03Z",
        fetched_at="2026-05-08T01:03:00Z",
        content_text="Original Hacker News title and metadata.",
        content_html=None,
        extra={"guid": "1"},
    )
    updated = FetchedItem(
        source_id="hn_ai",
        url="https://x.com/example/status/1",
        title="Updated AI psychosis title",
        author=None,
        published_at="2026-05-08T01:02:03Z",
        fetched_at="2026-05-08T01:04:00Z",
        content_text="Updated Hacker News title and metadata.",
        content_html=None,
        extra={"guid": "2"},
    )

    assert upsert_item(conn, first) is True
    assert upsert_item(conn, updated) is False

    rows = conn.execute("SELECT url, title, content_text FROM items").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "https://x.com/example/status/1"
    assert rows[0][1] == "Updated AI psychosis title"
    assert rows[0][2] == "Updated Hacker News title and metadata."
