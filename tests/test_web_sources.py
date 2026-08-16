from __future__ import annotations

import json
from pathlib import Path

import pytest

from airadar.audit.completeness_oracle import enumerate_web
from airadar.fetcher.http_client import FeedResponse
from airadar.fetcher.web import WEB_SOURCE_REGISTRY, parse_web_source
from airadar.sources.contract import load_source_contract
from airadar.sources.loader import SourceConfig

WEB_SLUGS = {
    "anthropic_news", "anthropic_research", "claude_platform_releases", "claude_blog", "cursor_blog",
    "every_latest", "google_research", "hf_daily_papers", "lmsys_blog", "langchain_blog", "microsoft_ai",
    "mistral_news", "runway_news", "sierra_blog", "suno_blog", "xai_news", "inclusionai_models",
    "deepseek_api_updates",
}
ROOT = Path(__file__).resolve().parents[1]


def _source(slug: str) -> SourceConfig:
    rows = load_source_contract(ROOT / "tests/fixtures/aihot_sources.json")["sources"]
    row = next(row for row in rows if row["slug"] == slug)
    return SourceConfig(slug=slug, name=slug, url=row["fetch_url"], tier="T1", kind="web")


def test_registry_exactly_covers_reviewed_web_sources() -> None:
    assert set(WEB_SOURCE_REGISTRY) == WEB_SLUGS


@pytest.mark.parametrize("slug", sorted(WEB_SLUGS - {"inclusionai_models", "hf_daily_papers", "claude_platform_releases", "lmsys_blog", "deepseek_api_updates"}))
def test_html_registry_accepts_scoped_items_and_excludes_navigation(slug: str) -> None:
    spec = WEB_SOURCE_REGISTRY[slug]
    path = spec.example_paths[0]
    body = f'<html><a data-ai-radar-item href="{path}"><h2>First</h2><time datetime="2026-08-13T10:00:00Z"></time></a><a data-ai-radar-item href="{path}-two"><h2>Second</h2><time datetime="2026-08-12T10:00:00Z"></time></a><a href="/">Navigation</a></html>'.encode()
    items = parse_web_source(_source(slug), FeedResponse(200, body, final_url=spec.final_url))
    assert [item.title for item in items] == ["First", "Second"]


def test_google_research_rejects_pagination_and_rss_navigation() -> None:
    body = b"""
        <a data-ai-radar-item href="/blog/real-research/"><h2>Real research</h2></a>
        <a href="javascript(0):void">Go to page 1, first page</a>
        <a href="/blog/rss">Follow us on rss</a>
    """

    items = parse_web_source(
        _source("google_research"),
        FeedResponse(200, body, final_url="https://research.google/blog/"),
    )

    assert [(item.url, item.title) for item in items] == [
        ("https://research.google/blog/real-research", "Real research"),
    ]


def test_huggingface_papers_emit_arxiv_urls() -> None:
    body = b'<a data-ai-radar-paper="2401.00001" href="/papers/2401.00001"><h2>Paper One</h2></a><a data-ai-radar-paper="2401.00002" href="/papers/2401.00002"><h2>Paper Two</h2></a>'
    items = parse_web_source(_source("hf_daily_papers"), FeedResponse(200, body, final_url="https://huggingface.co/papers"))
    assert [item.url for item in items] == ["https://arxiv.org/abs/2401.00001", "https://arxiv.org/abs/2401.00002"]


def test_huggingface_papers_ignore_navigation_and_choose_the_article_heading() -> None:
    body = b"""
        <a href="/papers/date/2026-08-12">Previous</a>
        <article>
          <a href="/papers/2608.00677"></a>
          <h3><a href="/papers/2608.00677">OpenART</a></h3>
          <a href="/papers/2608.00677">9 authors</a>
          <a href="/papers/2608.00677#community">3</a>
        </article>
    """
    response = FeedResponse(200, body, final_url="https://huggingface.co/papers")

    items = parse_web_source(_source("hf_daily_papers"), response)

    assert [(item.url, item.title) for item in items] == [("https://arxiv.org/abs/2608.00677", "OpenART")]
    assert enumerate_web("hf_daily_papers", body, response.final_url or "") == {
        ("https://arxiv.org/abs/2608.00677", "OpenART"),
    }


def test_release_notes_emit_fragment_urls() -> None:
    body = b'<h2 data-ai-radar-release id="aug-13-2026"><time datetime="2026-08-13T00:00:00Z"></time>August 13, 2026</h2><h2 data-ai-radar-release id="aug-12-2026"><time datetime="2026-08-12T00:00:00Z"></time>August 12, 2026</h2>'
    items = parse_web_source(_source("claude_platform_releases"), FeedResponse(200, body, final_url=WEB_SOURCE_REGISTRY["claude_platform_releases"].final_url))
    assert items[0].url.endswith("#aug-13-2026")


def test_lmsys_structured_payload_is_parsed() -> None:
    body = b'\\"posts\\":[{\\"slug\\":\\"2026-one\\",\\"title\\":\\"First\\",\\"date\\":\\"Aug 13, 2026\\"},{\\"slug\\":\\"2026-two\\",\\"title\\":\\"Second\\",\\"date\\":\\"August 12, 2026\\"}]'
    items = parse_web_source(_source("lmsys_blog"), FeedResponse(200, body, final_url="https://www.lmsys.org/blog/"))
    assert [item.title for item in items] == ["First", "Second"]


def test_lmsys_title_whitespace_is_canonical_in_production_and_oracle() -> None:
    body = 'X\\"slug\\":\\"2025-12-19-diffusion-llm\\",\\"title\\":\\"Power Up Diffusion LLMs: Day-0 Support for LLaDA\u202f2.0\\",\\"date\\":\\"Dec 19, 2025\\"'.encode()
    response = FeedResponse(200, body, final_url="https://www.lmsys.org/blog/")

    items = parse_web_source(_source("lmsys_blog"), response)

    expected = {("https://www.lmsys.org/blog/2025-12-19-diffusion-llm", "Power Up Diffusion LLMs: Day-0 Support for LLaDA 2.0")}
    assert {(item.url, item.title) for item in items} == expected
    assert enumerate_web("lmsys_blog", body, response.final_url or "") == expected


def test_empty_absolute_langchain_card_link_uses_sibling_heading_in_production_and_oracle() -> None:
    body = b'<div class="blog-item"><a class="blog-link-absolute" href="/blog/managed-agents"></a><h2>Managed agents</h2></div>'
    response = FeedResponse(200, body, final_url="https://www.langchain.com/blog")

    items = parse_web_source(_source("langchain_blog"), response)

    expected = {("https://www.langchain.com/blog/managed-agents", "Managed agents")}
    assert {(item.url, item.title) for item in items} == expected
    assert enumerate_web("langchain_blog", body, response.final_url or "") == expected


def test_microsoft_anchor_title_wins_over_unrelated_section_heading() -> None:
    body = b'<li><h2>Featured stories</h2><div><h2>Another story</h2><a href="/news/mai-code">MAI-Code 1.1 Flash</a></div></li>'
    response = FeedResponse(200, body, final_url="https://microsoft.ai/blog/")

    items = parse_web_source(_source("microsoft_ai"), response)

    assert [(item.url, item.title) for item in items] == [
        ("https://microsoft.ai/news/mai-code", "MAI-Code 1.1 Flash"),
    ]


def test_real_card_heading_wins_when_navigation_links_the_same_article_first() -> None:
    body = b'<nav><a href="/news/mai-code">News</a></nav><article><h2>MAI-Code 1.1 Flash</h2><a href="/news/mai-code"></a></article>'
    response = FeedResponse(200, body, final_url="https://microsoft.ai/blog/")

    items = parse_web_source(_source("microsoft_ai"), response)

    expected = {("https://microsoft.ai/news/mai-code", "MAI-Code 1.1 Flash")}
    assert {(item.url, item.title) for item in items} == expected
    assert enumerate_web("microsoft_ai", body, response.final_url or "") == expected


def test_huggingface_ignores_legal_paper_link_outside_a_paper_card() -> None:
    body = b"""
        <div><a href="/papers/2608.00677">Featured papers</a></div>
        <article><h3>OpenART</h3><a href="/papers/2608.00677"></a></article>
    """
    response = FeedResponse(200, body, final_url="https://huggingface.co/papers")

    items = parse_web_source(_source("hf_daily_papers"), response)

    expected = {("https://arxiv.org/abs/2608.00677", "OpenART")}
    assert {(item.url, item.title) for item in items} == expected
    assert enumerate_web("hf_daily_papers", body, response.final_url or "") == expected


def test_card_heading_wins_over_cta_aria_label_in_production_and_oracle() -> None:
    body = b'<article><h2>Canonical heading</h2><a href="/news/item" aria-label="Open item">Read more</a></article>'
    response = FeedResponse(200, body, final_url="https://www.anthropic.com/news")

    items = parse_web_source(_source("anthropic_news"), response)

    expected = {("https://www.anthropic.com/news/item", "Canonical heading")}
    assert {(item.url, item.title) for item in items} == expected
    assert enumerate_web("anthropic_news", body, response.final_url or "") == expected


def test_mistral_news_excludes_rss_navigation() -> None:
    body = b'<a href="/news/real-release"><h2>Real release</h2></a><a href="/news/rss">RSS feed</a>'
    response = FeedResponse(200, body, final_url="https://mistral.ai/news/")

    items = parse_web_source(_source("mistral_news"), response)

    expected = {("https://mistral.ai/news/real-release", "Real release")}
    assert {(item.url, item.title) for item in items} == expected
    assert enumerate_web("mistral_news", body, response.final_url or "") == expected


def test_inclusionai_json_list_is_validated() -> None:
    body = json.dumps([{"id": "inclusionAI/model-one", "lastModified": "2026-08-13T00:00:00Z"}, {"id": "inclusionAI/model-two", "lastModified": "2026-08-12T00:00:00Z"}]).encode()
    items = parse_web_source(_source("inclusionai_models"), FeedResponse(200, body, final_url=WEB_SOURCE_REGISTRY["inclusionai_models"].final_url))
    assert [item.url for item in items] == ["https://huggingface.co/inclusionAI/model-one", "https://huggingface.co/inclusionAI/model-two"]


def test_deepseek_updates_emit_dated_release_anchors_and_match_independent_oracle() -> None:
    body = (ROOT / "tests/fixtures/web_sources/deepseek_api_updates.html").read_bytes()
    body = body.replace(b"</article>", b"\0</article>")
    final_url = "https://api-docs.deepseek.com/zh-cn/updates"

    items = parse_web_source(
        _source("deepseek_api_updates"),
        FeedResponse(200, body, final_url=final_url),
    )

    expected = [
        (
            "https://api-docs.deepseek.com/zh-cn/updates#%E6%97%B6%E9%97%B4-2026-08-13",
            "DeepSeek-V4-Pro 更新",
            "2026-08-13T00:00:00Z",
        ),
        (
            "https://api-docs.deepseek.com/zh-cn/updates#%E6%97%B6%E9%97%B42026-07-31",
            "DeepSeek-V4-Flash 更新",
            "2026-07-31T00:00:00Z",
        ),
    ]
    assert [(item.url, item.title, item.published_at) for item in items] == expected
    assert enumerate_web("deepseek_api_updates", body, final_url) == {
        (url, title) for url, title, _published_at in expected
    }


def test_semantic_zero_wrong_final_host_invalid_date_and_duplicates_fail() -> None:
    source = _source("anthropic_news")
    with pytest.raises(ValueError, match="zero accepted"):
        parse_web_source(source, FeedResponse(200, b"<html></html>", final_url="https://www.anthropic.com/news"))
    with pytest.raises(ValueError, match="final response host"):
        parse_web_source(source, FeedResponse(200, b"x", final_url="https://mirror.example/news"))
    bad_date = b'<a data-ai-radar-item href="/news/a"><h2>A</h2><time datetime="not-a-date"></time></a>'
    with pytest.raises(ValueError, match="date"):
        parse_web_source(source, FeedResponse(200, bad_date, final_url="https://www.anthropic.com/news"))
    duplicate = b'<a data-ai-radar-item href="/news/a"><h2>A</h2><time datetime="2026-08-13T00:00:00Z"></time></a><a data-ai-radar-item href="/news/a"><h2>B</h2><time datetime="2026-08-13T00:00:00Z"></time></a>'
    with pytest.raises(ValueError, match="duplicate"):
        parse_web_source(source, FeedResponse(200, duplicate, final_url="https://www.anthropic.com/news"))
