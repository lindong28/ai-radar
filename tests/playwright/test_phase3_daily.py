from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def _wait_for_daily_report(page: Page) -> None:
    expect(page.locator(".daily-masthead-title")).to_be_visible(timeout=10_000)


def test_v13_daily_defaults_to_aihot_style_report(page: Page, base_url: str, historical_date: str) -> None:
    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    _wait_for_daily_report(page)

    expect(page).to_have_url(f"{base_url}/daily")
    expect(page.locator(".daily-masthead-title")).to_contain_text("AIRADAR")
    expect(page.locator(".daily-readable-date")).to_contain_text("年")
    expect(page.locator(".daily-story-count")).to_contain_text("STORIES")
    expect(page.locator(".daily-section").first).to_be_visible()
    expect(page.locator(".daily-article").first).to_be_visible()
    expect(page.locator(".daily-article-title a").first).to_have_attribute("href", re.compile(r"^https?://"))

    assert page.locator(".feed-toolbar-row").count() == 0
    assert page.locator(".timeline-card").count() == 0
    assert page.locator("input[type='search']").count() == 0
    assert page.locator("input[type='date']").count() == 0
    assert historical_date in page.locator(".daily-archive-panel").inner_text()
    expect(page.locator(".daily-next")).to_be_hidden()
    title_styles = page.locator(".daily-article-title").first.evaluate(
        """el => {
            const style = getComputedStyle(el);
            return { color: style.color, fontSize: style.fontSize, fontWeight: style.fontWeight };
        }"""
    )
    assert title_styles == {
        "color": "rgb(231, 238, 246)",
        "fontSize": "19px",
        "fontWeight": "600",
    }


def test_v13a_daily_accepts_query_and_path_date(page: Page, base_url: str, historical_date: str) -> None:
    page.goto(f"{base_url}/daily?date={historical_date}", wait_until="domcontentloaded")
    _wait_for_daily_report(page)

    expect(page.locator(".daily-readable-date")).to_contain_text("年")
    expect(page).to_have_url(f"{base_url}/daily/{historical_date}")
    assert page.locator(".daily-article").first.get_attribute("data-published-date") == historical_date

    page.goto(f"{base_url}/daily/{historical_date}", wait_until="domcontentloaded")
    _wait_for_daily_report(page)
    assert page.locator(".daily-article").first.get_attribute("data-published-date") == historical_date


def test_v13b_daily_invalid_and_future_dates_fallback_to_recent_content(
    page: Page, base_url: str, historical_date: str
) -> None:
    for requested in ["invalid", "2099-01-01"]:
        page.goto(f"{base_url}/daily?date={requested}", wait_until="domcontentloaded")
        _wait_for_daily_report(page)
        expect(page.locator("#daily-fallback")).to_contain_text(f"已切到最近一期 {historical_date}")
        expect(page).to_have_url(f"{base_url}/daily/{historical_date}")


def test_v13c_v13d_daily_empty_date_has_report_placeholder(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/daily?date=2025-01-01", wait_until="domcontentloaded")

    expect(page.locator(".daily-readable-date")).to_contain_text("二〇二五")
    expect(page.locator(".daily-empty")).to_contain_text("当日没有日报内容")
    assert page.locator(".daily-article").count() == 0


def test_v13e_mobile_daily_keeps_aihot_reading_flow(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    _wait_for_daily_report(page)

    assert page.locator(".daily-archive-panel").bounding_box() is None
    assert page.locator(".feed-toolbar-row").count() == 0
    first_section = page.locator(".daily-section").first.bounding_box()
    first_article = page.locator(".daily-article").first.bounding_box()
    assert first_section is not None
    assert first_article is not None
    assert first_section["y"] < 320
    assert first_article["y"] < 430


def test_v13f_zoomed_desktop_daily_uses_aihot_narrow_reading_flow(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 960, "height": 900})
    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    _wait_for_daily_report(page)

    expect(page.locator(".app-hamburger")).to_be_visible()
    archive = page.locator(".daily-archive-panel").bounding_box()
    title = page.locator(".daily-masthead-title").bounding_box()
    report = page.locator(".daily-report").bounding_box()
    first_section = page.locator(".daily-section").first.bounding_box()
    assert archive is not None
    assert title is not None
    assert report is not None
    assert first_section is not None
    assert archive["width"] >= 860
    assert report["width"] >= 740
    assert archive["y"] < title["y"]
    assert title["height"] < 120
    assert first_section["width"] >= 740
