from __future__ import annotations

from playwright.sync_api import Page, expect


def test_v05_about_page_renders_sources_and_static_sections(page: Page, base_url: str) -> None:
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
    expect(page.locator("#sources-table")).to_contain_text("T1")
