from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from playwright.sync_api import Page, expect

PRELOAD_RE = re.compile(
    r'\s*<script id="__PRELOAD__" type="application/json">\s*.*?\s*</script>',
    re.S,
)


def _goto(page: Page, base_url: str, path: str, cards: bool = False) -> None:
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    if cards:
        expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
        # SSR prepaint 先渲染首屏子集（约 12 条），CSR hydration 随后用完整首批（约 40 条）替换。
        # 只等「首张卡可见」会在替换前放行，导致后续基线读到 prepaint 的条数而非首批条数。
        _settle_card_count(page)


def _settle_card_count(page: Page, quiet_ms: int = 350, timeout_ms: int = 10_000) -> int:
    """等卡片数量停止变化后返回它，避开 SSR→CSR 替换与无限滚动追加的竞态。"""
    cards = page.locator(".timeline-card")
    deadline = time.monotonic() + timeout_ms / 1000
    previous = -1
    while time.monotonic() < deadline:
        current = cards.count()
        if current == previous and current > 0:
            return current
        previous = current
        page.wait_for_timeout(quiet_ms)
    return cards.count()


def _strip_wechat_preload(page: Page) -> None:
    def strip_document_preload(route):
        parsed = urlparse(route.request.url)
        if route.request.resource_type == "document" and parsed.path == "/wechat":
            response = route.fetch()
            route.fulfill(response=response, body=PRELOAD_RE.sub("", response.text()))
            return
        route.fallback()

    page.route("**/*", strip_document_preload)


def _visible_card_count(page: Page) -> int:
    return page.locator(".timeline-card").count()


def _api_total(page: Page, base_url: str, path: str) -> int:
    response = page.request.get(f"{base_url}{path}")
    assert response.ok
    payload = response.json()
    assert payload["success"] is True
    return int(payload["data"]["total"])


def _require_wechat_cards_on_page(page: Page, base_url: str, page_number: int) -> None:
    path = f"/api/v1/wechat?page={page_number}&limit=50"
    response = page.request.get(f"{base_url}{path}")
    assert response.ok
    payload = response.json()
    assert payload["success"] is True
    data = payload["data"]
    items = data["items"]
    current_page = int(data["page"])
    if current_page != page_number or not items:
        pytest.skip(
            f"requires local WeChat data visible on /wechat?page={page_number}; "
            f"{path} returned page={current_page} with {len(items)} items (total={data['total']})"
        )


def _grouped_times(page: Page) -> list[list[str]]:
    return page.evaluate(
        """() => {
          return [...document.querySelectorAll("#list .timeline-day")].map(group =>
            [...group.querySelectorAll(".timeline-entry .timeline-time time")]
              .map(el => el.textContent?.trim())
              .filter(Boolean)
          );
        }"""
    )


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _ensure_multiple_date_groups(page: Page) -> list[list[str]]:
    groups = _grouped_times(page)
    for _ in range(12):
        if len(groups) >= 2:
            return groups
        sentinel = page.locator(".scroll-sentinel")
        if not sentinel.count():
            break
        previous = page.locator(".timeline-card").count()
        sentinel.scroll_into_view_if_needed()
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_function(
            "previous => document.querySelectorAll('.timeline-card').length > previous",
            arg=previous,
            timeout=10_000,
        )
        groups = _grouped_times(page)
    return groups


def test_v03_v04_v06_navigation_and_search_inputs(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/")
    assert page.locator(".side-link").all_inner_texts() == [
        "精选",
        "全部 AI 动态",
        "热点榜",
        "微信文章解读",
        "AI 日报",
        "收藏",
        "关于",
        "更新日志",
    ]
    body = " ".join(page.locator(".side-nav").all_inner_texts())
    for forbidden in ["公众号", "Agent", "反馈", "登录"]:
        assert forbidden not in body

    for path in ["/", "/all", "/about"]:
        _goto(page, base_url, path)
        expect(page.locator('input[type="search"]')).to_be_visible()

    _goto(page, base_url, "/daily")
    assert page.locator('input[type="search"]').count() == 0


def test_v07_v08_v09_v11_cards_links_and_score_gating(
    page: Page, base_url: str, source_homepages: dict[str, str]
) -> None:
    for path in ["/", "/all"]:
        _goto(page, base_url, path, cards=True)
        entries = page.locator(".timeline-entry")
        cards = page.locator(".timeline-card")
        assert entries.count() >= 10

        assert page.locator(".timeline-card .timeline-score").count() == cards.count()
        badges = page.locator(".timeline-card .timeline-selected-badge", has_text="精选")
        if path == "/":
            assert badges.count() == cards.count()
        assert page.locator(".source-line > .timeline-selected-badge").count() == badges.count()
        assert page.locator(".card-topline-end .timeline-selected-badge").count() == 0

        for index in range(10):
            entry = entries.nth(index)
            card = entry.locator(".timeline-card")
            expect(card.locator(".source-line")).to_be_visible()
            expect(card.locator(".source-icon")).to_be_visible()
            expect(entry.locator(".timeline-time")).to_be_visible()
            expect(card.locator(".summary")).to_be_visible()
            if path == "/":
                assert card.locator(".tags").count() == 0
                expect(card.locator(".reason")).to_be_visible()
            else:
                expect(card.locator(".tags")).to_be_visible()

            source_id = card.get_attribute("data-source-id")
            assert source_id
            assert card.locator(".source-line a").first.get_attribute("href") == source_homepages[source_id]

            icon_bg = card.locator(".source-icon").first.evaluate("el => getComputedStyle(el).backgroundImage")
            assert (icon_bg and icon_bg != "none") or card.locator(".source-icon img").count() == 1

            if "x-card" not in (card.get_attribute("class") or ""):
                title = card.locator(".item-title")
                expect(title).to_be_visible()
                href = title.get_attribute("href") or ""
                assert href.startswith("http")
                assert "/item.html" not in href
                assert "#" not in href


def test_curated_page_does_not_duplicate_selected_badge_in_tags(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/", cards=True)

    assert page.locator(".timeline-card .timeline-selected-badge", has_text="精选").count() >= 1
    assert page.locator(".timeline-card .source-line .timeline-selected-badge", has_text="精选").count() >= 1
    assert page.locator(".card-topline-end .timeline-selected-badge").count() == 0
    assert page.locator(".timeline-card .tags").count() == 0

    _goto(page, base_url, "/all", cards=True)
    assert page.locator(".timeline-card .tags .tag", has_text="精选").count() == 0
    tag_locator = page.locator(".timeline-card .tags .tag")
    assert tag_locator.count() >= 1
    assert all(not label.startswith("#") for label in tag_locator.all_inner_texts())
    assert tag_locator.evaluate_all(
        "nodes => nodes.every(node => getComputedStyle(node, '::before').content === '\"#\"')"
    )


def test_v10_x_cards_keep_original_title_link_but_render_body_first(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all?channel=x", cards=True)

    x_cards = page.locator(".timeline-card.x-card")
    assert x_cards.count() >= 3
    assert page.locator(".timeline-card").count() == x_cards.count()
    for index in range(3):
        card = x_cards.nth(index)
        title = card.locator(".item-title")
        expect(title).to_be_attached()
        expect(title).to_be_hidden()
        title_href = title.get_attribute("href") or ""
        assert title_href.startswith("https://x.com/")
        assert "/status/" in title_href
        assert "#" not in title_href
        assert len(card.locator(".summary").inner_text()) > 40
        assert card.locator(".origin-link").count() == 0
        assert card.locator(".x-media-affordance").count() == 0


def test_v10b_article_media_does_not_dominate_viewport(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/", cards=True)

    media = page.locator(".article-media-img")
    assert media.count() >= 1
    max_ratio = media.evaluate_all(
        """imgs => Math.max(...imgs.map(img => img.getBoundingClientRect().height / window.innerHeight))"""
    )
    assert max_ratio <= 0.4


def test_v10d_all_page_preserves_aihot_media_score_and_selected_reason(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    assert page.locator(".timeline-card").count() >= 30
    assert page.locator(".timeline-card .article-media-img").count() >= 1
    assert page.locator(".timeline-card .timeline-score").count() == page.locator(".timeline-card").count()
    assert page.locator(".timeline-card .timeline-selected-badge", has_text="精选").count() == 0

    _goto(page, base_url, "/", cards=True)
    assert page.locator(".timeline-card .timeline-selected-badge", has_text="精选").count() >= 1
    selected_reason_count = page.locator(".timeline-card").evaluate_all(
        """cards => cards.filter(card =>
          card.querySelector('.timeline-selected-badge')?.textContent?.trim() === '精选'
          && card.querySelector('.reason')?.textContent?.trim()
        ).length"""
    )
    assert selected_reason_count >= 1
    assert page.locator(".timeline-card .tags .tag", has_text="精选").count() == 0
    assert page.locator(".timeline-card .timeline-dup-count").count() == 0

    metrics = page.locator(".timeline-card").evaluate_all(
        """cards => {
          const firstTen = cards.slice(0, 10).map(card => card.getBoundingClientRect());
          return {
            firstViewportCards: firstTen.filter(rect => rect.top < window.innerHeight && rect.bottom > 0).length,
            avgHeight: firstTen.reduce((sum, rect) => sum + rect.height, 0) / firstTen.length,
          };
        }"""
    )
    assert metrics["firstViewportCards"] >= 2
    assert metrics["avgHeight"] <= 520


def test_x_cards_use_body_text_as_the_desktop_reading_surface(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all?channel=x", cards=True)

    card = page.locator(".timeline-card.x-card").first
    expect(card.locator(".item-title")).to_be_hidden()
    summary = card.locator(".summary")
    expect(summary).to_be_visible()
    metrics = summary.evaluate(
        """el => {
          const style = getComputedStyle(el);
          return {
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
          };
        }"""
    )

    assert metrics["fontSize"] == "14px"
    assert metrics["fontWeight"] == "400"
    assert metrics["lineHeight"] == "23.1px"


def test_v10e_all_page_channel_filters_are_url_backed_targets(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    channel = page.locator("#channel-param")
    assert channel.locator("option").all_inner_texts() == ["全部", "一手信源", "资讯", "推文"]
    with page.expect_response(
        lambda response: "/api/v1/timeline" in response.url and "channel=x" in response.url and response.status == 200
    ):
        channel.select_option("x")
    expect(page).to_have_url(f"{base_url}/all?channel=x")
    x_cards = page.locator(".timeline-card.x-card")
    assert x_cards.count() >= 3
    assert page.locator(".timeline-card").count() == x_cards.count()

    with page.expect_response(
        lambda response: "/api/v1/timeline" in response.url
        and "channel=firstParty" in response.url
        and response.status == 200
    ):
        channel.select_option("firstParty")
    expect(page).to_have_url(f"{base_url}/all?channel=firstParty")
    assert page.locator(".timeline-card.x-card").count() == 0
    expect(channel).to_have_value("firstParty")


def test_v10f_all_page_infinite_scroll_appends_the_next_cursor_batch(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    assert page.locator("#pagination, .pagination-link").count() == 0
    cards = page.locator(".timeline-card")
    before = cards.count()
    ids_before = cards.evaluate_all("els => els.map(el => el.dataset.itemId)")
    with page.expect_response(
        lambda response: "/api/v1/timeline" in response.url
        and "cursor=" in response.url
        and response.status == 200
    ) as response_info:
        page.locator(".scroll-sentinel").scroll_into_view_if_needed()
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    response_data = response_info.value.json()["data"]
    expected_appended = [
        str(item["id"])
        for item in sorted(
            response_data["items"],
            key=lambda item: (
                str(item.get("published_at") or item.get("fetched_at") or ""),
                str(item.get("fetched_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
    ]

    page.wait_for_function(
        "before => document.querySelectorAll('.timeline-card').length > before",
        arg=before,
        timeout=10_000,
    )
    ids_after = cards.evaluate_all("els => els.map(el => el.dataset.itemId)")
    assert ids_after[:before] == ids_before
    assert ids_after[before:] == expected_appended
    assert len(ids_after) == len(set(ids_after))
    expect(page).to_have_url(f"{base_url}/all")


def test_wechat_card_body_click_opens_detail_and_back_preserves_page(page: Page, base_url: str) -> None:
    _require_wechat_cards_on_page(page, base_url, 2)
    _goto(page, base_url, "/wechat?page=2", cards=True)

    first_card = page.locator(".wechat-card").first
    detail_url = first_card.get_attribute("data-detail-url") or ""
    assert detail_url.startswith("/wechat/")
    assert "page=2" in detail_url

    first_card.locator(".summary").click()
    expect(page.locator(".detail-back")).to_be_visible()
    assert unquote(page.url) == f"{base_url}{detail_url}"
    expect(page.locator(".detail-back")).to_have_attribute("href", "/wechat?page=2")

    page.locator(".detail-back").click()
    expect(page).to_have_url(f"{base_url}/wechat?page=2")
    expect(page.locator(".wechat-card").first).to_be_visible()


def test_wechat_pagination_renders_numeric_direct_links_and_arrow_boundaries(page: Page, base_url: str) -> None:
    _strip_wechat_preload(page)
    requested_pages: list[int] = []

    def wechat_response(route):
        parsed = urlparse(route.request.url)
        params = parse_qs(parsed.query)
        requested_page = int(params.get("page", ["1"])[0])
        page_number = min(max(requested_page, 1), 4)
        requested_pages.append(page_number)
        item = {
            "slug": f"wechat-page-{page_number}",
            "title": f"WeChat page {page_number}",
            "abstract": "Deterministic Playwright pagination item.",
            "tags": ["Agent"],
            "author": "AI Planet",
            "avatar_url": "",
            "published_at": f"2026-06-0{page_number}T10:00:00Z",
            "url": f"https://example.com/wechat-page-{page_number}",
            "detail_url": f"/wechat/wechat-page-{page_number}?page={page_number}",
            "recommendation": "值得一看",
        }
        payload = {
            "success": True,
            "data": {"items": [item], "total": 200, "page": page_number, "limit": 50},
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/v1/wechat*", wechat_response)
    with page.expect_response(lambda response: "/api/v1/wechat" in response.url and "page=2" in response.url):
        page.goto(f"{base_url}/wechat", wait_until="domcontentloaded")

    expect(page.locator(".wechat-card").first).to_be_visible(timeout=10_000)
    numeric_texts = page.locator("#pagination .pagination-link").evaluate_all(
        "els => els.map(el => el.textContent.trim()).filter(text => /^\\d+$/.test(text))"
    )
    assert numeric_texts == ["1", "2", "3", "4"]
    assert page.locator('#pagination .pagination-link[rel="prev"]').count() == 0
    assert requested_pages.count(1) == 1
    assert requested_pages.count(2) == 1

    with page.expect_response(lambda response: "/api/v1/wechat" in response.url and "page=3" in response.url):
        page.locator('#pagination .pagination-link[rel="next"]').click()

    expect(page).to_have_url(f"{base_url}/wechat?page=2")
    expect(page.locator(".wechat-card .item-title")).to_have_text("WeChat page 2")
    expect(page.locator('.pagination-link[aria-current="page"]')).to_have_text("2")
    assert requested_pages.count(2) == 1
    assert requested_pages.count(3) == 1

    with page.expect_response(lambda response: "/api/v1/wechat" in response.url and "page=4" in response.url):
        page.locator('#pagination .pagination-link[data-page="3"]').filter(has_text=re.compile(r"^3$")).click()

    expect(page).to_have_url(f"{base_url}/wechat?page=3")
    expect(page.locator(".wechat-card .item-title")).to_have_text("WeChat page 3")
    expect(page.locator('.pagination-link[aria-current="page"]')).to_have_text("3")
    assert requested_pages.count(3) == 1
    assert requested_pages.count(4) == 1

    page.locator('#pagination .pagination-link[data-page="4"]').filter(has_text=re.compile(r"^4$")).click()

    expect(page).to_have_url(f"{base_url}/wechat?page=4")
    expect(page.locator(".wechat-card .item-title")).to_have_text("WeChat page 4")
    assert requested_pages.count(4) == 1
    assert page.locator('#pagination .pagination-link[rel="next"]').count() == 0


def test_v10g_mobile_all_page_keeps_scan_reading_density(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _goto(page, base_url, "/all", cards=True)

    metrics = page.locator(".timeline-card").evaluate_all(
        """cards => {
          const firstTen = cards.slice(0, 10).map(card => card.getBoundingClientRect());
          return {
            firstViewportCards: firstTen.filter(rect => rect.top < window.innerHeight && rect.bottom > 0).length,
            avgHeight: firstTen.reduce((sum, rect) => sum + rect.height, 0) / firstTen.length,
            minWidth: Math.min(...firstTen.map(rect => rect.width)),
          };
        }"""
    )
    assert metrics["firstViewportCards"] >= 2
    assert metrics["avgHeight"] <= 420
    assert metrics["minWidth"] >= 280


def test_v10h_typography_tone_matches_aihot_reference(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    styles = page.evaluate(
        """() => {
          const pick = (selector) => {
            const el = document.querySelector(selector);
            const cs = getComputedStyle(el);
            return {
              color: cs.color,
              fontFamily: cs.fontFamily,
              fontSize: cs.fontSize,
              fontWeight: cs.fontWeight,
              lineHeight: cs.lineHeight,
            };
          };
            return {
            pageTitle: pick("h1"),
            time: pick(".timeline-time"),
            cardTitle: pick(".timeline-card:not(.x-card) .item-title"),
            source: pick(".source-line"),
            summary: pick(".summary"),
            tag: pick(".tag"),
            filter: pick(".seg-item"),
          };
        }"""
    )
    _goto(page, base_url, "/all?channel=x", cards=True)
    x_body = page.locator(".timeline-card.x-card .summary").first.evaluate(
        """el => {
          const style = getComputedStyle(el);
          return {
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
          };
        }"""
    )

    assert "system-ui" in styles["pageTitle"]["fontFamily"]
    assert styles["pageTitle"]["fontWeight"] == "700"
    assert styles["pageTitle"]["fontSize"] == "24px"

    assert "ui-monospace" in styles["time"]["fontFamily"]
    # measured-tokens B.1 的 base `.timeline-time`：font-size 12.5px / line-height 1.1 → 13.75px。
    # 旧断言写的 12px 来自 base 误用了 ≤640px 档的 12px/1（CSS 忠实度审计 MISMATCH 6/7 已修）。
    assert styles["time"]["fontSize"] == "12.5px"
    assert styles["time"]["lineHeight"] == "13.75px"

    assert "system-ui" in styles["cardTitle"]["fontFamily"]
    assert styles["cardTitle"]["fontSize"] == "15.5px"
    assert styles["cardTitle"]["fontWeight"] == "700"
    assert styles["cardTitle"]["lineHeight"] == "22.475px"

    assert x_body["fontSize"] == "14px"
    assert x_body["fontWeight"] == "400"
    assert x_body["lineHeight"] == "23.1px"

    assert "system-ui" in styles["source"]["fontFamily"]
    assert styles["source"]["fontSize"] == "13px"
    assert styles["source"]["color"] == "rgb(92, 102, 114)"

    assert "system-ui" in styles["summary"]["fontFamily"]
    assert styles["summary"]["fontSize"] == "13.5px"
    assert styles["summary"]["lineHeight"] == "22.275px"
    assert styles["summary"]["color"] == "rgb(92, 102, 114)"

    assert "system-ui" in styles["tag"]["fontFamily"]
    assert "system-ui" in styles["filter"]["fontFamily"]
    assert styles["filter"]["lineHeight"] == "13px"


def test_v10c_mobile_category_filter_keeps_all_options_visible(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _goto(page, base_url, "/", cards=True)

    items = page.locator(".seg-item").evaluate_all(
        """els => els.map(el => {
          const rect = el.getBoundingClientRect();
          return {
            text: el.textContent.trim(),
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
            visible: rect.left >= 0 && rect.right <= window.innerWidth
          };
        })"""
    )
    assert [item["text"] for item in items] == ["全部", "模型", "产品", "行业", "论文", "技巧"]
    assert all(item["visible"] for item in items)


def test_v12_date_groups_are_descending(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)
    groups = _ensure_multiple_date_groups(page)
    assert len(groups) >= 2
    for group in groups:
        values = [_minutes(value) for value in group]
        assert values == sorted(values, reverse=True)

    _goto(page, base_url, "/", cards=True)
    date_values = page.locator(".timeline-date time").evaluate_all("els => els.map(el => el.getAttribute('datetime'))")
    assert date_values == sorted(date_values, reverse=True)
    for group in _grouped_times(page):
        values = [_minutes(value) for value in group]
        assert values == sorted(values, reverse=True)


def test_v14_v15_search_filters_and_clears(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/", cards=True)
    baseline = _visible_card_count(page)
    baseline_total = _api_total(page, base_url, "/api/v1/curated")

    with page.expect_response(lambda response: "/api/v1/curated" in response.url and "q=OpenAI" in response.url):
        page.locator('input[type="search"]').fill("OpenAI")
    expect(page.locator(".timeline-card").first).to_be_visible()
    assert _visible_card_count(page) <= baseline
    assert _api_total(page, base_url, "/api/v1/curated?q=OpenAI") < baseline_total
    for index in range(_visible_card_count(page)):
        assert "openai" in page.locator(".timeline-card").nth(index).inner_text().lower()

    with page.expect_response(lambda response: "/api/v1/curated" in response.url and "q=" not in response.url):
        page.locator('input[type="search"]').fill("")
    expect(page.locator(".timeline-card").first).to_be_visible()
    assert _visible_card_count(page) == baseline


def test_v15b_switching_tabs_clears_search_state(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)
    all_baseline = _visible_card_count(page)

    _goto(page, base_url, "/", cards=True)
    with page.expect_response(lambda response: "/api/v1/curated" in response.url and "q=OpenAI" in response.url):
        page.locator('input[type="search"]').fill("OpenAI")

    page.locator('.side-link[href="/all"]').click()
    expect(page).to_have_url(f"{base_url}/all")
    assert page.locator('input[type="search"]').input_value() == ""
    assert _visible_card_count(page) == all_baseline
