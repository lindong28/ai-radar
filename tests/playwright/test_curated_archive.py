from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, expect

PRELOAD_RE = re.compile(
    r'\s*<script id="__PRELOAD__" type="application/json">\s*.*?\s*</script>',
    re.S,
)


def _api_data(page: Page, base_url: str, path: str) -> dict[str, object]:
    response = page.request.get(f"{base_url}{path}")
    assert response.ok
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    return data


def _item(item_id: str, published_at: str) -> dict[str, object]:
    return {
        "id": item_id,
        "source_id": "openai_blog",
        "source_name": "OpenAI Blog",
        "source_kind": "feed",
        "source_homepage_url": "https://openai.com/",
        "source_icon_url": "",
        "tier": "T1",
        "url": f"https://example.com/{item_id}",
        "title": item_id,
        "title_zh": item_id,
        "author": "Ada",
        "published_at": published_at,
        "fetched_at": published_at,
        "summary_zh": "无限滚动归档契约测试摘要。",
        "topic_tags": ["模型发布"],
        "enriched_tags": ["模型发布"],
        "weighted_score": 7.2,
        "rank": 1,
        "reasoning": "测试推荐理由。",
    }


def _mock_curated(page: Page, items_by_category: dict[str, list[dict[str, object]]]) -> list[str]:
    requested_urls: list[str] = []

    def handler(route) -> None:  # noqa: ANN001
        parsed = urlparse(route.request.url)
        if parsed.path == "/api/v1/curated":
            requested_urls.append(route.request.url)
            params = parse_qs(parsed.query)
            category = params.get("category", ["all"])[0]
            page_number = int(params.get("page", ["1"])[0])
            items = items_by_category.get(category, items_by_category["all"])
            start = (page_number - 1) * 40
            payload = {
                "success": True,
                "data": {
                    "run_id": "archive-contract",
                    "ruleset_version": "test",
                    "date": "2026-08-02",
                    "items": items[start : start + 40],
                    "count": min(40, max(0, len(items) - start)),
                    "total": len(items),
                    "page": page_number,
                    "limit": 40,
                },
            }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
            return
        if route.request.resource_type == "document" and parsed.path == "/":
            response = route.fetch()
            route.fulfill(response=response, body=PRELOAD_RE.sub("", response.text()))
            return
        route.fallback()

    page.route("**/*", handler)
    return requested_urls


def _scroll_to_count(page: Page, expected: int) -> None:
    cards = page.locator(".timeline-card")
    expect(cards).to_have_count(min(40, expected), timeout=10_000)
    while cards.count() < expected:
        previous = cards.count()
        page.locator(".scroll-sentinel").scroll_into_view_if_needed()
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_function(
            "previous => document.querySelectorAll('.timeline-card').length > previous",
            arg=previous,
            timeout=10_000,
        )
    expect(cards).to_have_count(expected)


def _fixture_items(prefix: str, count: int) -> list[dict[str, object]]:
    return [
        _item(f"{prefix}-{index:02d}", f"2026-08-02T{23 - (index % 24):02d}:{59 - (index % 60):02d}:00Z")
        for index in range(count)
    ]


def test_hp8_curated_archive_infinite_scroll_reaches_terminal_total(page: Page, base_url: str) -> None:
    items = _fixture_items("archive", 45)
    requests = _mock_curated(page, {"all": items})
    page.goto(f"{base_url}/?q=archive-contract", wait_until="domcontentloaded")

    _scroll_to_count(page, len(items))
    assert page.locator("#pagination, .pagination-link").count() == 0
    expect(page.locator(".scroll-status")).to_have_text("已加载全部")
    ids = page.locator(".timeline-card").evaluate_all("els => els.map(el => el.dataset.itemId)")
    assert len(ids) == len(set(ids)) == len(items)
    assert any("page=2" in url for url in requests)


def test_hp8_curated_filter_resets_generation_and_terminal_total(page: Page, base_url: str) -> None:
    all_items = _fixture_items("all", 45)
    product_items = _fixture_items("product", 2)
    _mock_curated(page, {"all": all_items, "ai-models": _fixture_items("model", 3), "ai-products": product_items})
    page.goto(f"{base_url}/?q=filter-contract", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    assert page.locator(".timeline-card").count() >= 40

    page.evaluate(
        """() => {
          document.querySelector('[data-category="model"]').click();
          document.querySelector('[data-category="product"]').click();
        }"""
    )
    expect(page).to_have_url(f"{base_url}/?q=filter-contract&category=ai-products")
    expect(page.locator(".timeline-card")).to_have_count(2, timeout=10_000)
    assert page.locator(".timeline-card").evaluate_all("els => els.map(el => el.dataset.itemId)") == [
        item["id"] for item in product_items
    ]
    expect(page.locator('[data-category="product"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".scroll-status")).to_have_text("")


def test_hp8_curated_mobile_uses_tabbar_and_infinite_scroll(page: Page, base_url: str) -> None:
    items = _fixture_items("mobile", 45)
    _mock_curated(page, {"all": items})
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/?q=mobile-archive-contract", wait_until="domcontentloaded")

    expect(page.locator(".m-tabbar")).to_be_visible()
    assert page.locator("#pagination, .pagination-link").count() == 0
    expect(page.locator(".scroll-sentinel")).to_be_attached()
    _scroll_to_count(page, len(items))
    expect(page.locator(".scroll-status")).to_have_text("已加载全部")


def test_daily_latest_probe_and_date_navigation_survive_curated_archive(page: Page, base_url: str) -> None:
    archive = _api_data(page, base_url, "/api/v1/curated/daily-archive")
    days = archive["days"]
    assert isinstance(days, list) and days
    latest_date = str(days[0]["date"])

    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    expect(page.locator(".daily-article").first).to_be_visible(timeout=10_000)
    expect(page.locator("#daily-volume")).to_have_text(f"VOL.{latest_date.replace('-', '.')}")
    expect(page.locator(".daily-next")).to_be_hidden()
    expect(page.locator(".daily-story-count")).to_have_text(
        f"{page.locator('.daily-article').count()} STORIES"
    )

    page.locator(".daily-prev").click()
    expect(page).to_have_url(re.compile(rf"{re.escape(base_url)}/daily/\d{{4}}-\d{{2}}-\d{{2}}$"))
    expect(page.locator(".daily-readable-date")).to_contain_text("年")
    expect(page.locator(".daily-next")).to_be_visible()

    page.locator(".daily-next").click()
    expect(page).to_have_url(f"{base_url}/daily/{latest_date}")
    expect(page.locator(".daily-article").first).to_be_visible(timeout=10_000)
