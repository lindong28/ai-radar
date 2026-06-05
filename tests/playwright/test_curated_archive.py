from __future__ import annotations

import math
import re

from playwright.sync_api import Page, expect


def _api_data(page: Page, base_url: str, path: str) -> dict[str, object]:
    response = page.request.get(f"{base_url}{path}")
    assert response.ok
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    return data


def _total_pages(data: dict[str, object]) -> int:
    return max(1, math.ceil(int(data["total"]) / int(data["limit"])))


def _visible_page_numbers(page: Page) -> list[int]:
    return page.locator("#pagination [data-page]").evaluate_all(
        "nodes => nodes.map((node) => Number(node.dataset.page)).filter(Boolean)"
    )


def _item_ids(page: Page) -> list[str]:
    return page.locator(".timeline-card").evaluate_all(
        "nodes => nodes.map((node) => node.querySelector('.item-title')?.textContent || '')"
    )


def test_hp8_curated_archive_numeric_pagination(page: Page, base_url: str) -> None:
    api_first = _api_data(page, base_url, "/api/v1/curated?page=1&limit=40")
    total_pages = _total_pages(api_first)
    assert total_pages >= 10

    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    expect(page.locator("#pagination")).to_be_visible()
    expect(page.locator("#pagination [aria-current='page']")).to_have_text("1")
    assert max(_visible_page_numbers(page)) == total_pages
    assert page.locator("#pagination [rel='prev']").count() == 0
    first_page_items = _item_ids(page)

    target_page = 20 if total_pages >= 22 else max(2, total_pages - 1)
    page.goto(f"{base_url}/?page={target_page}", wait_until="domcontentloaded")
    expect(page.locator("#pagination [aria-current='page']")).to_have_text(str(target_page))
    numbers = _visible_page_numbers(page)
    assert 1 in numbers
    assert total_pages in numbers
    assert target_page - 2 in numbers
    assert target_page + 2 in numbers

    jump_page = target_page + 2
    page.locator(f"#pagination [data-page='{jump_page}']").click()
    expect(page).to_have_url(f"{base_url}/?page={jump_page}")
    expect(page.locator("#pagination [aria-current='page']")).to_have_text(str(jump_page))
    assert _item_ids(page) != first_page_items

    page.goto(f"{base_url}/?page=9999", wait_until="domcontentloaded")
    expect(page.locator("#pagination [aria-current='page']")).to_have_text(str(total_pages))
    expect(page).to_have_url(f"{base_url}/?page={total_pages}")
    assert page.locator("#pagination [rel='next']").count() == 0


def test_hp8_curated_filter_resets_page_and_updates_total(page: Page, base_url: str) -> None:
    all_data = _api_data(page, base_url, "/api/v1/curated?page=1&limit=40")
    categories = [
        ("model", "ai-models"),
        ("product", "ai-products"),
        ("industry", "industry"),
        ("paper", "paper"),
        ("practice", "tip"),
    ]
    chosen = None
    for ui_key, api_value in categories:
        filtered = _api_data(page, base_url, f"/api/v1/curated?category={api_value}&page=1&limit=40")
        if int(filtered["total"]) > 0 and int(filtered["total"]) != int(all_data["total"]):
            chosen = (ui_key, api_value, filtered)
            break
    assert chosen is not None
    ui_key, api_value, filtered_data = chosen
    filtered_pages = _total_pages(filtered_data)

    page.goto(f"{base_url}/?page=2", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    page.locator(f"[data-category='{ui_key}']").click()
    expect(page).to_have_url(re.compile(rf"{re.escape(base_url)}/\?category={api_value}$"))

    if filtered_pages > 1:
        expect(page.locator("#pagination")).to_be_visible()
        assert max(_visible_page_numbers(page)) == filtered_pages
    else:
        expect(page.locator("#pagination")).to_be_hidden()


def test_hp8_curated_mobile_pagination_is_visible(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")

    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    expect(page.locator("#pagination")).to_be_visible()
    box = page.locator("#pagination").bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= 390


def test_daily_latest_probe_and_date_navigation_survive_curated_archive(page: Page, base_url: str) -> None:
    latest_data = _api_data(page, base_url, "/api/v1/curated")
    latest_date = str(latest_data["date"])
    assert latest_date

    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    expect(page.locator(".daily-article").first).to_be_visible(timeout=10_000)
    expect(page.locator("#daily-latest-date")).to_have_text(latest_date)
    expect(page.locator(".daily-next")).to_be_hidden()
    assert page.locator(".daily-article").first.get_attribute("data-published-date") == latest_date

    page.locator(".daily-prev").click()
    expect(page).to_have_url(re.compile(rf"{re.escape(base_url)}/daily/\d{{4}}-\d{{2}}-\d{{2}}$"))
    expect(page.locator(".daily-readable-date")).to_contain_text("年")
    expect(page.locator(".daily-next")).to_be_visible()

    page.locator(".daily-next").click()
    expect(page).to_have_url(f"{base_url}/daily/{latest_date}")
    expect(page.locator(".daily-article").first).to_be_visible()
