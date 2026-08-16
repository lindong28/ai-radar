from __future__ import annotations

import sqlite3
from pathlib import Path

from playwright.sync_api import Page, expect

from airadar.sources.loader import load_sources
from airadar.sources.sync import sync_to_db


def test_v05_about_page_renders_sources_and_static_sections(
    page: Page,
    base_url: str,
    playwright_db_path: Path | None,
) -> None:
    if playwright_db_path is not None:
        sources = load_sources(Path(__file__).resolve().parents[2] / "data" / "sources.toml")
        wechat_source = next((source for source in sources if source.kind == "wechat"), None)
        if wechat_source is None:
            from airadar.sources.loader import SourceConfig

            wechat_source = SourceConfig(
                slug="wx_test",
                name="微信测试来源",
                url="https://example.com/wechat.xml",
                tier="T2",
                kind="wechat",
                homepage_url="https://mp.weixin.qq.com/",
            )
            sources.append(wechat_source)
        with sqlite3.connect(playwright_db_path) as conn:
            sync_to_db(sources, conn)
    page.goto(f"{base_url}/about", wait_until="domcontentloaded")

    expect(page.locator("h1")).to_have_text("关于")
    expect(page.locator(".page-subtitle")).to_have_text("产品定位、来源（信源池）、设计原则、联系方式")
    expect(page.get_by_role("heading", name="产品定位")).to_be_visible()
    expect(page.get_by_role("heading", name="设计原则")).to_be_visible()
    expect(page.get_by_role("heading", name="联系方式")).to_be_visible()
    expect(page.get_by_role("heading", name="来源（信源池）")).to_be_visible()
    expect(page.locator("#sources-table tr").first).to_be_visible(timeout=10_000)
    assert page.locator("#sources-table tr").count() >= 10
    expect(page.locator("#sources-table")).to_contain_text("openai_blog")
    if playwright_db_path is not None:
        expect(page.locator("#sources-table")).to_contain_text("x_openai")
        expect(page.locator("#sources-table")).not_to_contain_text("simonw_mastodon")
        expect(page.locator("#sources-table")).to_contain_text(wechat_source.slug)
        x_openai_row = page.locator("#sources-table tr").filter(
            has=page.get_by_text("x_openai", exact=True)
        )
        expect(x_openai_row.locator("a")).to_have_attribute("href", "https://x.com/OpenAI")
        expect(x_openai_row.locator("a")).to_have_text("X：OpenAI")
        expect(x_openai_row.locator("a")).not_to_contain_text("@OpenAI")
        expect(x_openai_row).to_contain_text("待首次验证")
    expect(page.locator("#sources-table")).to_contain_text("T1")
