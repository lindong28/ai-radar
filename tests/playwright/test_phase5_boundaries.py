from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, expect

PRELOAD_RE = re.compile(
    r'\s*<script id="__PRELOAD__" type="application/json">\s*.*?\s*</script>',
    re.S,
)


def _strip_ssr_preload(page: Page) -> None:
    def strip_document_preload(route):
        parsed = urlparse(route.request.url)
        if route.request.resource_type == "document" and parsed.path in {"/", "/all"}:
            response = route.fetch()
            route.fulfill(response=response, body=PRELOAD_RE.sub("", response.text()))
            return
        route.fallback()

    page.route("**/*", strip_document_preload)


def _timeline_payload_item(item_id: str, title: str, published_at: str) -> dict[str, object]:
    return {
        "id": item_id,
        "source_id": "openai_blog",
        "source_name": "OpenAI Blog",
        "source_kind": "feed",
        "source_homepage_url": "https://openai.com/",
        "source_icon_url": "",
        "tier": "T1",
        "url": f"https://example.com/{item_id}",
        "title": title,
        "title_zh": title,
        "author": "Ada",
        "published_at": published_at,
        "fetched_at": published_at,
        "summary_zh": "这是一段用于测试列表状态的中文摘要。",
        "topic_tags": ["模型发布"],
        "enriched_tags": ["模型发布"],
        "weighted_score": 7.2,
        "rank": 1,
        "reasoning": "测试推荐理由。",
    }


def _curated_payload(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "run_id": "test-run",
            "ruleset_version": "test.r1",
            "date": "2026-05-15",
            "count": len(items),
            "items": items,
        },
    }


def test_feed_pages_show_loading_state_while_fetching(page: Page, base_url: str) -> None:
    _strip_ssr_preload(page)
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


def test_feed_pages_show_error_state_when_fetch_fails(page: Page, base_url: str) -> None:
    _strip_ssr_preload(page)
    for path, endpoint in [("/", "/api/v1/curated"), ("/all", "/api/v1/timeline")]:
        route_pattern = f"**{endpoint}*"

        def fail_response(route):
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"success": False, "error": "simulated failure"}),
            )

        page.route(route_pattern, fail_response)
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")

        expect(page.locator(".feed-error")).to_contain_text("加载失败")
        expect(page.locator(".feed-error")).to_contain_text("simulated failure")
        expect(page.locator("[data-retry-feed]")).to_be_visible()
        assert page.locator(".timeline-loading").count() == 0

        page.unroute(route_pattern, fail_response)


def test_v15a_search_without_results_shows_empty_state(page: Page, base_url: str) -> None:
    for path, endpoint in [("/", "/api/v1/curated"), ("/all", "/api/v1/timeline")]:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)

        with page.expect_response(lambda response: endpoint in response.url and "q=" in response.url):
            page.locator('input[type="search"]').fill("xyzzyqwertynonexistent")

        expect(page.locator(".empty-state")).to_contain_text("没有匹配条目")
        assert page.locator(".timeline-card").count() == 0


def test_v18_unknown_tab_paths_return_404(page: Page, base_url: str) -> None:
    for path in ["/feedback", "/login", "/publish"]:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert response is not None
        assert response.status == 404
        assert page.url == f"{base_url}{path}"
        expect(page.locator("body")).to_contain_text("404")

    admin_response = page.goto(f"{base_url}/admin", wait_until="domcontentloaded")
    assert admin_response is not None
    assert admin_response.status == 403

    page.set_extra_http_headers({"Cf-Access-Jwt-Assertion": "test"})
    admin_response = page.goto(f"{base_url}/admin", wait_until="domcontentloaded")
    assert admin_response is not None
    assert admin_response.status == 200
    expect(page.locator("h1")).to_contain_text("运维监控")


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


def test_all_page_out_of_range_page_clamps_to_last_result_page(page: Page, base_url: str) -> None:
    _strip_ssr_preload(page)

    def timeline_response(route):
        if "page=9999" in route.request.url:
            payload = {"success": True, "data": {"items": [], "page": 9999, "limit": 40, "total": 41}}
        else:
            payload = {
                "success": True,
                "data": {
                    "items": [_timeline_payload_item("last-page-item", "最后一页的模型发布", "2026-05-14T10:00:00Z")],
                    "page": 2,
                    "limit": 40,
                    "total": 41,
                },
            }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/v1/timeline*", timeline_response)
    page.goto(f"{base_url}/all?page=9999", wait_until="domcontentloaded")

    expect(page.locator(".timeline-card .item-title")).to_have_text("最后一页的模型发布")
    expect(page.locator('.pagination-link[aria-current="page"]')).to_have_text("2")
    expect(page).to_have_url(f"{base_url}/all?page=2")
    assert page.locator(".empty-state").count() == 0


def test_invalid_category_deeplinks_are_normalized(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/?category=not-real&q=Qwen", wait_until="domcontentloaded")
    expect(page).to_have_url(f"{base_url}/?q=Qwen")
    assert "seg-item-active" in (page.locator('[data-category="all"]').first.get_attribute("class") or "")

    page.goto(f"{base_url}/all?category=not-real&channel=x&page=1", wait_until="domcontentloaded")
    expect(page).to_have_url(f"{base_url}/all?channel=x")
    assert "seg-item-active" in (page.locator('[data-category="all"]').first.get_attribute("class") or "")
    expect(page.locator('[data-channel="x"]').first).to_have_class(re.compile("seg-item-active"))


def test_home_product_and_tip_categories_request_server_filtered_results(page: Page, base_url: str) -> None:
    _strip_ssr_preload(page)
    items = [
        {
            **_timeline_payload_item("model-only", "纯模型多模态条目", "2026-05-15T10:00:00Z"),
            "topic_tags": ["模型发布", "多模态", "MCP/工具"],
            "enriched_tags": ["模型发布", "多模态", "MCP/工具"],
        },
        {
            **_timeline_payload_item("product", "真实产品更新", "2026-05-15T09:00:00Z"),
            "topic_tags": ["产品更新", "模型发布"],
            "enriched_tags": ["产品更新", "模型发布"],
        },
        {
            **_timeline_payload_item("repo-edge", "泛开源端侧条目", "2026-05-15T08:00:00Z"),
            "topic_tags": ["开源/仓库", "端侧"],
            "enriched_tags": ["开源/仓库", "端侧"],
        },
        {
            **_timeline_payload_item("tutorial", "Transformer 实践课程", "2026-05-15T07:00:00Z"),
            "topic_tags": ["模型发布", "教程/实践"],
            "enriched_tags": ["模型发布", "教程/实践"],
        },
        {
            **_timeline_payload_item("deploy", "部署工程实践", "2026-05-15T06:00:00Z"),
            "topic_tags": ["部署/工程"],
            "enriched_tags": ["部署/工程"],
        },
        {
            **_timeline_payload_item("opinion", "行业观点新闻", "2026-05-15T05:00:00Z"),
            "topic_tags": ["大佬观点", "行业动态"],
            "enriched_tags": ["大佬观点", "行业动态"],
        },
        {
            **_timeline_payload_item("deploy-news", "部署行业新闻", "2026-05-15T04:00:00Z"),
            "topic_tags": ["部署/工程", "行业动态"],
            "enriched_tags": ["部署/工程", "行业动态"],
        },
    ]
    items_by_category = {
        "ai-products": [items[1]],
        "tip": [items[3], items[4]],
    }
    seen_categories: list[str] = []

    def curated_response(route):
        category = parse_qs(urlparse(route.request.url).query).get("category", ["all"])[0]
        seen_categories.append(category)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_curated_payload(items_by_category.get(category, items))),
        )

    page.route("**/api/v1/curated*", curated_response)

    page.goto(f"{base_url}/?category=ai-products", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card .item-title")).to_have_text("真实产品更新")
    assert seen_categories[-1] == "ai-products"

    page.goto(f"{base_url}/?category=tip", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible()
    titles = page.locator(".timeline-card .item-title").all_inner_texts()
    assert titles == ["Transformer 实践课程", "部署工程实践"]
    assert seen_categories[-1] == "tip"


def test_home_search_request_keeps_active_category(page: Page, base_url: str) -> None:
    _strip_ssr_preload(page)
    seen_urls: list[str] = []
    items = [
        {
            **_timeline_payload_item("qwen", "Qwen 模型发布", "2026-05-15T10:00:00Z"),
            "topic_tags": ["模型发布"],
            "enriched_tags": ["模型发布"],
        }
    ]

    def curated_response(route):
        seen_urls.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(_curated_payload(items)))

    page.route("**/api/v1/curated*", curated_response)
    page.goto(f"{base_url}/?category=ai-models", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card .item-title")).to_have_text("Qwen 模型发布")

    page.locator('input[type="search"]').fill("Qwen")
    page.locator("form.feed-filter").evaluate("form => form.requestSubmit()")
    page.wait_for_timeout(500)

    assert any("q=Qwen" in url and "category=ai-models" in url for url in seen_urls)


def test_home_category_requests_server_filter_and_sorts_by_visible_time(page: Page, base_url: str) -> None:
    _strip_ssr_preload(page)
    items = [
                {
                    "id": "older-high-score",
                    "source_id": "openai_blog",
                    "source_name": "OpenAI Blog",
                    "source_kind": "feed",
                    "source_homepage_url": "https://openai.com/",
                    "source_icon_url": "",
                    "tier": "T1",
                    "url": "https://example.com/older",
                    "title": "Older high-score model release",
                    "title_zh": "较早的高分模型发布",
                    "author": "Ada",
                    "published_at": "2026-05-15T01:00:00Z",
                    "fetched_at": "2026-05-15T01:01:00Z",
                    "summary_zh": "较早发布但分数更高的模型发布。",
                    "topic_tags": ["模型发布"],
                    "enriched_tags": ["模型发布"],
                    "weighted_score": 9.9,
                    "rank": 1,
                    "reasoning": "测试推荐理由。",
                },
                {
                    "id": "newer-low-score",
                    "source_id": "openai_blog",
                    "source_name": "OpenAI Blog",
                    "source_kind": "feed",
                    "source_homepage_url": "https://openai.com/",
                    "source_icon_url": "",
                    "tier": "T1",
                    "url": "https://example.com/newer",
                    "title": "Newer low-score model release",
                    "title_zh": "较新的低分模型发布",
                    "author": "Ada",
                    "published_at": "2026-05-15T10:00:00Z",
                    "fetched_at": "2026-05-15T10:01:00Z",
                    "summary_zh": "较晚发布但分数更低的模型发布。",
                    "topic_tags": ["模型发布"],
                    "enriched_tags": ["模型发布"],
                    "weighted_score": 6.8,
                    "rank": 2,
                    "reasoning": "测试推荐理由。",
                },
                {
                    "id": "inference-course",
                    "source_id": "openai_blog",
                    "source_name": "OpenAI Blog",
                    "source_kind": "feed",
                    "source_homepage_url": "https://openai.com/",
                    "source_icon_url": "",
                    "tier": "T1",
                    "url": "https://example.com/course",
                    "title": "Inference course",
                    "title_zh": "推理优化课程",
                    "author": "Ada",
                    "published_at": "2026-05-15T11:00:00Z",
                    "fetched_at": "2026-05-15T11:01:00Z",
                    "summary_zh": "这是一条推理和教程内容，不应进入模型分类。",
                    "topic_tags": ["推理", "教程/实践"],
                    "enriched_tags": ["推理", "教程/实践"],
                    "weighted_score": 9.8,
                    "rank": 3,
                    "reasoning": "测试推荐理由。",
                },
                {
                    "id": "misclassified-course",
                    "source_id": "openai_blog",
                    "source_name": "OpenAI Blog",
                    "source_kind": "feed",
                    "source_homepage_url": "https://openai.com/",
                    "source_icon_url": "",
                    "tier": "T1",
                    "url": "https://example.com/model-course",
                    "title": "Transformer practice course",
                    "title_zh": "Transformer 实践课程",
                    "author": "Ada",
                    "published_at": "2026-05-15T12:00:00Z",
                    "fetched_at": "2026-05-15T12:01:00Z",
                    "summary_zh": "这是一条教程课程内容，即使命中模型发布标签也不应进入模型分类。",
                    "topic_tags": ["模型发布", "教程/实践"],
                    "enriched_tags": ["模型发布", "教程/实践"],
                    "weighted_score": 9.7,
                    "rank": 4,
                    "reasoning": "测试推荐理由。",
                },
    ]
    seen_categories: list[str] = []

    def curated_response(route):
        category = parse_qs(urlparse(route.request.url).query).get("category", ["all"])[0]
        seen_categories.append(category)
        response_items = items[:2] if category == "ai-models" else items
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(_curated_payload(response_items)),
        )

    page.route("**/api/v1/curated*", curated_response)

    page.goto(f"{base_url}/?category=ai-models", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card")).to_have_count(2)
    assert seen_categories[-1] == "ai-models"

    titles = page.locator(".timeline-card .item-title").all_inner_texts()
    assert titles == ["较新的低分模型发布", "较早的高分模型发布"]


def test_mobile_closed_sidebar_is_not_in_tab_order(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/?category=ai-models", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)

    page.keyboard.press("Tab")
    focused = page.evaluate(
        """() => {
          const el = document.activeElement;
          const rect = el.getBoundingClientRect();
          return {
            text: (el.innerText || el.getAttribute("aria-label") || "").trim(),
            className: el.className,
            left: Math.round(rect.left),
            right: Math.round(rect.right),
          };
        }"""
    )

    assert "app-hamburger" in focused["className"]
    assert focused["left"] >= 0
