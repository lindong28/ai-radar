from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from airadar.db import migrate
from airadar.fetcher.dedup import FetchedItem, content_hash, upsert_item
from airadar.fetcher.runner import default_sources_path
from airadar.fetcher.rss import parse_feed
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
