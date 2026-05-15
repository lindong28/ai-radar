from __future__ import annotations

import time

from playwright.sync_api import Page, expect


def test_feed_pages_show_loading_state_while_fetching(page: Page, base_url: str) -> None:
    for path, endpoint in [("/", "/api/v1/curated"), ("/all", "/api/v1/timeline")]:
        route_pattern = f"**{endpoint}*"

        def delay_response(route):
            time.sleep(0.35)
            route.continue_()

        page.route(route_pattern, delay_response)
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")

        expect(page.locator(".timeline-loading")).to_be_visible()
        expect(page.locator(".timeline-loading")).to_contain_text("正在加载")
        expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)

        page.unroute(route_pattern, delay_response)


def test_v15a_search_without_results_shows_empty_state(page: Page, base_url: str) -> None:
    for path, endpoint in [("/", "/api/v1/curated"), ("/all", "/api/v1/timeline")]:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)

        with page.expect_response(lambda response: endpoint in response.url and "q=" in response.url):
            page.locator('input[type="search"]').fill("xyzzyqwertynonexistent")

        expect(page.locator(".empty-state")).to_contain_text("没有匹配条目")
        assert page.locator(".timeline-card").count() == 0


def test_v18_unknown_tab_paths_return_404(page: Page, base_url: str) -> None:
    for path in ["/feedback", "/login", "/admin", "/publish"]:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert response is not None
        assert response.status == 404
        assert page.url == f"{base_url}{path}"
        expect(page.locator("body")).to_contain_text("404")


def test_v19_empty_daily_date_shows_placeholder(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/daily?date=2025-01-01", wait_until="domcontentloaded")

    expect(page.locator(".daily-empty")).to_contain_text("当日没有日报内容")
    assert page.locator(".timeline-card").count() == 0


def test_v20_feed_pages_have_url_backed_search_forms(page: Page, base_url: str) -> None:
    for path in ["/", "/all"]:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.locator("form.feed-filter")).to_be_visible()
        expect(page.locator("form.feed-filter input[name=q]")).to_be_visible()
        expect(page.locator("form.feed-filter button[type=submit]")).to_be_visible()

    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    assert page.locator("form.feed-filter").count() == 0

    page.goto(f"{base_url}/about", wait_until="domcontentloaded")
    assert page.locator("button[type=submit]").count() == 0
