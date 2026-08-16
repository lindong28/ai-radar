from __future__ import annotations

from airadar.fetcher.rss import parse_feed
from airadar.sources.loader import SourceConfig


def _source(slug: str) -> SourceConfig:
    return SourceConfig(slug=slug, name=slug, url="https://example.test/feed.xml", tier="T1")


def _rss(items: str) -> bytes:
    return f"<rss><channel><title>Test</title>{items}</channel></rss>".encode()


def test_google_cloud_keeps_database_category_or_path() -> None:
    body = _rss(
        """
        <item><title>Tagged</title><link>https://cloud.google.com/blog/other/tagged</link><category>Databases</category></item>
        <item><title>Path</title><link>https://cloud.google.com/blog/products/databases/path</link></item>
        <item><title>Other</title><link>https://cloud.google.com/blog/products/compute/no</link></item>
        """
    )
    assert [item.title for item in parse_feed(_source("google_cloud_databases"), body)] == ["Tagged", "Path"]


def test_relative_entry_urls_are_resolved_against_source_origin() -> None:
    source = SourceConfig(slug="sakana_blog", name="Sakana", url="https://sakana.ai/feed.xml", tier="T1")
    [item] = parse_feed(source, _rss("<item><title>Relative</title><link>/blog/relative</link></item>"))
    assert item.url == "https://sakana.ai/blog/relative"


def test_openrouter_official_feed_is_not_path_filtered() -> None:
    paths = ["announcements/release", "insights/governing-team-ai-spend", "tutorials/tool-calling"]
    body = _rss("".join(f"<item><title>{path}</title><link>https://openrouter.ai/blog/{path}</link></item>" for path in paths))
    assert [item.url for item in parse_feed(_source("openrouter_announcements"), body)] == [f"https://openrouter.ai/blog/{path}" for path in paths]


def test_default_feed_behavior_is_unchanged() -> None:
    body = _rss("<item><title>Any</title><link>https://example.test/any</link></item>")
    assert [item.url for item in parse_feed(_source("ordinary"), body)] == ["https://example.test/any"]


def test_duplicate_canonical_feed_url_uses_last_entry_state() -> None:
    body = _rss("<item><title>Old</title><link>https://example.test/one</link></item><item><title>New</title><link>https://example.test/one/</link></item>")
    items = parse_feed(_source("ordinary"), body)
    assert [(item.url, item.title) for item in items] == [("https://example.test/one", "New")]
