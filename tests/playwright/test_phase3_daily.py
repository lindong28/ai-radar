from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def _api_data(page: Page, base_url: str, path: str) -> dict[str, object]:
    response = page.request.get(f"{base_url}{path}")
    assert response.ok
    payload = response.json()
    assert payload["success"] is True
    assert isinstance(payload["data"], dict)
    return payload["data"]


def _wait_for_daily_report(page: Page) -> None:
    expect(page.locator(".daily-masthead-title")).to_be_visible(timeout=10_000)
    page.wait_for_function(
        "() => document.querySelectorAll('.daily-article, .daily-empty').length > 0",
        timeout=10_000,
    )


def test_v13_daily_defaults_to_aihot_style_report(page: Page, base_url: str, historical_date: str) -> None:
    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    _wait_for_daily_report(page)

    expect(page).to_have_url(f"{base_url}/daily")
    expect(page.locator(".daily-masthead-title")).to_contain_text("AI RADAR")
    expect(page.locator(".daily-masthead-title")).to_contain_text("日报")
    expect(page.locator(".daily-readable-date")).to_contain_text("年")
    expect(page.locator(".daily-story-count")).to_contain_text("STORIES")
    expect(page.locator(".daily-section").first).to_be_visible()
    expect(page.locator(".daily-article").first).to_be_visible()
    expect(page.locator(".daily-article-title a").first).to_have_attribute("href", re.compile(r"^https?://"))

    assert page.locator(".feed-toolbar-row").count() == 0
    assert page.locator(".timeline-card").count() == 0
    assert page.locator("input[type='search']").count() == 0
    assert page.locator("input[type='date']").count() == 0
    assert historical_date[:4] in page.locator(".daily-archive-panel").inner_text()
    assert page.locator(".daily-side-month").count() >= 1
    expect(page.locator(".daily-side-month").first).to_have_attribute("open", "")
    expect(page.locator(".daily-next")).to_be_hidden()
    title_styles = page.locator(".daily-article-title").first.evaluate(
        """el => {
            const style = getComputedStyle(el);
            return { color: style.color, fontSize: style.fontSize, fontWeight: style.fontWeight };
        }"""
    )
    assert title_styles == {
        "color": "rgb(28, 39, 51)",
        "fontSize": "15px",
        "fontWeight": "700",
    }


def test_v13a_daily_accepts_query_and_path_date(page: Page, base_url: str) -> None:
    archive = _api_data(page, base_url, "/api/v1/curated/daily-archive")
    latest = str(archive["days"][0]["date"])
    selected_date = ""
    expected_hrefs: list[str] = []
    for day in archive["days"][1:]:
        candidate = str(day["date"])
        data = _api_data(page, base_url, f"/api/v1/curated?date={candidate}")
        hrefs = sorted(str(item["url"]).split("#", 1)[0] for item in data["items"])
        if candidate != latest and hrefs:
            selected_date = candidate
            expected_hrefs = hrefs
            break
    assert selected_date, "归档中需要至少一个非最新且有内容的历史日期"

    page.goto(f"{base_url}/daily?date={selected_date}", wait_until="domcontentloaded")
    _wait_for_daily_report(page)

    expect(page.locator(".daily-readable-date")).to_contain_text("年")
    expect(page).to_have_url(f"{base_url}/daily?date={selected_date}")
    expect(page.locator("#daily-volume")).to_have_text(f"VOL.{selected_date.replace('-', '.')}")
    assert sorted(page.locator(".daily-article-title a").evaluate_all("els => els.map(el => el.href.split('#')[0])")) == expected_hrefs

    page.goto(f"{base_url}/daily/{selected_date}", wait_until="domcontentloaded")
    _wait_for_daily_report(page)
    expect(page.locator("#daily-volume")).to_have_text(f"VOL.{selected_date.replace('-', '.')}")
    assert sorted(page.locator(".daily-article-title a").evaluate_all("els => els.map(el => el.href.split('#')[0])")) == expected_hrefs


def test_v13b_daily_invalid_and_future_dates_fallback_to_recent_content(
    page: Page, base_url: str, historical_date: str
) -> None:
    for requested in ["invalid", "2099-01-01"]:
        page.goto(f"{base_url}/daily?date={requested}", wait_until="domcontentloaded")
        _wait_for_daily_report(page)
        status = page.locator('#daily-fallback[role="status"]')
        expect(status).to_be_visible()
        expect(status).to_contain_text(f"已显示最近一期 {historical_date}")
        expect(page).to_have_url(f"{base_url}/daily/{historical_date}")
        styles = status.evaluate(
            """el => {
              const style = getComputedStyle(el);
              return {
                color: style.color,
                backgroundColor: style.backgroundColor,
                fontWeight: style.fontWeight,
                borderStyle: style.borderStyle,
              };
            }"""
        )
        assert styles == {
            "color": "rgb(107, 118, 132)",
            "backgroundColor": "rgba(0, 0, 0, 0)",
            "fontWeight": "400",
            "borderStyle": "none",
        }


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
    expect(page.locator(".app-mobile-bar")).to_have_count(0)
    expect(page.locator(".m-tabbar")).to_be_visible()
    assert first_section["width"] <= 354
    assert first_section["y"] < 550
    assert first_article["x"] >= first_section["x"]
    assert first_article["x"] + first_article["width"] <= first_section["x"] + first_section["width"]


def test_v13f_zoomed_desktop_daily_uses_aihot_narrow_reading_flow(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 960, "height": 900})
    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    _wait_for_daily_report(page)

    expect(page.locator(".app-mobile-bar")).to_have_count(0)
    expect(page.locator(".m-tabbar")).to_be_visible()
    assert page.locator(".app-hamburger").count() == 0
    archive = page.locator(".daily-archive-panel").bounding_box()
    title = page.locator(".daily-masthead-title").bounding_box()
    report = page.locator(".daily-report").bounding_box()
    first_section = page.locator(".daily-section").first.bounding_box()
    assert archive is None
    assert title is not None
    assert report is not None
    assert first_section is not None
    assert 560 <= report["width"] <= 640
    assert abs((report["x"] + report["width"] / 2) - 480) <= 1
    assert title["height"] < 120
    assert first_section["width"] < report["width"]
