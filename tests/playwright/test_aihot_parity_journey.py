from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import Page, expect

PRELOAD_RE = re.compile(
    r'\s*<script id="__PRELOAD__" type="application/json">\s*.*?\s*</script>',
    re.S,
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _api_data(page: Page, base_url: str, path: str) -> dict[str, object]:
    response = page.request.get(f"{base_url}{path}")
    assert response.ok
    payload = response.json()
    assert payload["success"] is True
    assert isinstance(payload["data"], dict)
    return payload["data"]


def _title(item: dict[str, object]) -> str:
    return str(item.get("title_zh") or item.get("title") or "").strip()


def _bounded_query(page: Page, base_url: str, endpoint: str) -> tuple[str, dict[str, object]]:
    initial = _api_data(page, base_url, f"{endpoint}?page=1&limit=40")
    candidates: list[str] = []
    for item in initial["items"]:
        title = _title(item)
        for length in (4, 6, 8, 12):
            step = max(1, length // 2)
            for start in range(0, max(1, len(title) - length + 1), step):
                candidate = title[start : start + length].strip(" ：，。！？、")
                if len(candidate) >= 4 and candidate not in candidates:
                    candidates.append(candidate)
    for candidate in candidates[:160]:
        query = urlencode({"q": candidate, "page": 1, "limit": 40})
        data = _api_data(page, base_url, f"{endpoint}?{query}")
        if 40 < int(data["total"]) <= 120:
            return candidate, data
    pytest.fail(f"preflight: {endpoint} has no non-empty query bounded to 2-3 batches")


def _expected_item_id_stages(
    page: Page,
    base_url: str,
    endpoint: str,
    query: str,
    total: int,
) -> list[list[str]]:
    group_order: list[str] = []
    grouped_ids: dict[str, list[str]] = {}
    stages: list[list[str]] = []
    for page_number in range(1, (total + 39) // 40 + 1):
        params = urlencode({"q": query, "page": page_number, "limit": 40})
        data = _api_data(page, base_url, f"{endpoint}?{params}")
        rendered_items = sorted(
            data["items"],
            key=lambda item: (
                datetime.fromisoformat(
                    str(item.get("published_at") or item.get("fetched_at")).replace("Z", "+00:00")
                ).timestamp(),
                str(item.get("fetched_at") or ""),
                str(item.get("id") or ""),
            ),
            reverse=True,
        )
        for item in rendered_items:
            published = datetime.fromisoformat(
                str(item.get("published_at") or item.get("fetched_at")).replace("Z", "+00:00")
            )
            bucket = published.astimezone(SHANGHAI).date().isoformat()
            if bucket not in grouped_ids:
                group_order.append(bucket)
                grouped_ids[bucket] = []
            grouped_ids[bucket].append(str(item["id"]))
        stages.append([item_id for bucket in group_order for item_id in grouped_ids[bucket]])
    assert len(stages[-1]) == total
    assert len(stages[-1]) == len(set(stages[-1]))
    return stages


def _load_to_terminal(page: Page, expected_stages: list[list[str]]) -> list[int]:
    cards = page.locator(".timeline-card")
    expect(cards.first).to_be_visible(timeout=10_000)
    expected_by_count = {len(stage): stage for stage in expected_stages}
    total = len(expected_stages[-1])
    counts = [cards.count()]
    assert cards.evaluate_all("els => els.map(el => el.dataset.itemId)") == expected_by_count[counts[-1]]
    for _ in range(2):
        if counts[-1] == total:
            break
        previous = counts[-1]
        page.locator(".scroll-sentinel").scroll_into_view_if_needed()
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_function(
            "previous => document.querySelectorAll('.timeline-card').length > previous",
            arg=previous,
            timeout=10_000,
        )
        counts.append(cards.count())
        assert cards.evaluate_all("els => els.map(el => el.dataset.itemId)") == expected_by_count[counts[-1]]
    assert counts[-1] == total
    assert all(after > before for before, after in zip(counts, counts[1:], strict=False))
    item_ids = cards.evaluate_all("els => els.map(el => el.dataset.itemId)")
    assert len(item_ids) == len(set(item_ids)) == total
    return counts


def _mock_item(item_id: str, title: str, published_at: str) -> dict[str, object]:
    return {
        "id": item_id,
        "source_id": "fixture-source",
        "source_name": "Fixture Source",
        "source_kind": "feed",
        "source_homepage_url": "https://fixture.invalid/",
        "source_icon_url": "https://fixture.invalid/icon.png",
        "author_avatar_url": None,
        "url": f"https://fixture.invalid/{item_id}",
        "title": title,
        "title_zh": title,
        "author": "Fixture Author",
        "published_at": published_at,
        "fetched_at": published_at,
        "content_preview": f"{title} summary",
        "summary_zh": f"{title} 摘要",
        "why_recommend": "fixture reason",
        "enriched_tags": ["契约", "回归"],
        "topic_tags": [],
        "reasoning": "fixture reason",
        "related_discussions": [],
        "media_assets": [],
        "weighted_score": 8.0,
        "rank": 1,
        "reason": "fixture reason",
        "scores": {},
    }


def _curated_payload(items: list[dict[str, object]], page_number: int, total: int) -> dict[str, object]:
    return {
        "success": True,
        "data": {
            "run_id": "fixture-run",
            "ruleset_version": "fixture",
            "items": items,
            "date": "2026-08-02",
            "count": len(items),
            "total": total,
            "page": page_number,
            "limit": 40,
        },
        "error": None,
    }


def _strip_preload_and_mock_curated(page: Page, payload_for_url) -> None:  # noqa: ANN001
    def handler(route) -> None:  # noqa: ANN001
        parsed = urlparse(route.request.url)
        if parsed.path == "/api/v1/curated":
            route.fulfill(status=200, content_type="application/json", body=json.dumps(payload_for_url(parsed)))
            return
        if route.request.resource_type == "document" and parsed.path == "/":
            response = route.fetch()
            route.fulfill(response=response, body=PRELOAD_RE.sub("", response.text()))
            return
        route.fallback()

    page.route("**/*", handler)


def test_parity_home_scroll_reaches_api_total_without_duplicates(page: Page, base_url: str) -> None:
    query, data = _bounded_query(page, base_url, "/api/v1/curated")
    expected_stages = _expected_item_id_stages(page, base_url, "/api/v1/curated", query, int(data["total"]))
    page.goto(f"{base_url}/?{urlencode({'q': query})}", wait_until="domcontentloaded")
    counts = _load_to_terminal(page, expected_stages)
    assert len(counts) >= 2


def test_parity_home_rapid_category_switch_keeps_last_generation(page: Page, base_url: str) -> None:
    expected = _api_data(page, base_url, "/api/v1/curated?category=ai-products&page=1&limit=40")
    expected_ids = [str(item["id"]) for item in expected["items"]]
    assert expected_ids
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    page.evaluate(
        """() => {
          document.querySelector('[data-category="model"]').click();
          document.querySelector('[data-category="product"]').click();
        }"""
    )
    expect(page).to_have_url(f"{base_url}/?category=ai-products")
    cards = page.locator(".timeline-card")
    expect(cards).to_have_count(len(expected_ids), timeout=10_000)
    assert cards.evaluate_all("els => els.map(el => el.dataset.itemId)") == expected_ids
    expect(page.locator('[data-category="product"]')).to_have_attribute("aria-pressed", "true")


def test_parity_home_date_prefix_tracks_query_refresh_and_empty_state(page: Page, base_url: str) -> None:
    initial = _api_data(page, base_url, "/api/v1/curated?page=1&limit=40")
    item = initial["items"][0]
    query = _title(item)
    filtered = _api_data(page, base_url, f"/api/v1/curated?{urlencode({'q': query, 'page': 1, 'limit': 40})}")
    latest = max(
        datetime.fromisoformat(str(entry.get("published_at") or entry.get("fetched_at")).replace("Z", "+00:00"))
        for entry in filtered["items"]
    ).astimezone(SHANGHAI)
    weekday = "一二三四五六日"[latest.weekday()]
    expected = f"{latest.year}年{latest.month}月{latest.day}日星期{weekday}"

    page.goto(f"{base_url}/?{urlencode({'q': query})}", wait_until="domcontentloaded")
    expect(page.locator("#run-meta")).to_contain_text(expected, timeout=10_000)
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#run-meta")).to_contain_text(expected, timeout=10_000)

    page.locator("#search").fill("__ai_radar_parity_no_results__")
    page.locator("form.feed-filter").evaluate("form => form.requestSubmit()")
    expect(page.locator(".empty-state")).to_be_visible(timeout=10_000)
    expect(page.locator("#run-meta")).to_have_text("AI 自动挑选的高价值内容")


def _hot_items(page: Page, base_url: str, limit: int) -> list[dict[str, object]]:
    data = _api_data(page, base_url, f"/api/v1/hot?limit={limit}")
    assert isinstance(data.get("generated_at"), str) and data["generated_at"]
    items = data["items"]
    assert isinstance(items, list)
    return items


def test_parity_home_hot_module_matches_two_item_api_prefix(page: Page, base_url: str) -> None:
    """首页热点严格等于 /api/v1/hot?limit=2 的响应（GAP-15/16/17/50）。"""
    expected = _hot_items(page, base_url, 2)
    page.goto(f"{base_url}/", wait_until="networkidle")
    module = page.locator("#hot-topics")
    expect(module).to_be_visible()
    rows = module.locator(".hot-topics-row")
    expect(rows).to_have_count(len(expected))

    for index, item in enumerate(expected):
        row = rows.nth(index)
        link = row.locator(".hot-topics-link")
        expect(link).to_have_attribute("href", str(item["url"]))
        assert _title(item)[:12] in link.inner_text()
        # 名次按 1-based 顺序，且热度值与 API 一致
        assert row.locator(".hot-topics-rank").inner_text().strip().lstrip("0") == str(index + 1)
        assert str(item["heat"]) in row.inner_text()

    more = module.locator(".hot-topics-more")
    expect(more).to_have_attribute("href", "/hot")
    assert "当前热点" in module.locator(".hot-topics-title").first.inner_text()

    # ≤960px 文案切换（GAP-50）
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(300)
    visible_titles = page.evaluate(
        """() => [...document.querySelectorAll('#hot-topics .hot-topics-title')]
                  .filter(el => getComputedStyle(el).display !== 'none' && el.offsetParent !== null)
                  .map(el => el.innerText.trim())"""
    )
    assert visible_titles, "390px 下热点标题不可见"
    assert all("今日热点" in text for text in visible_titles), visible_titles


def test_parity_hot_ranking_matches_complete_api_payload(page: Page, base_url: str) -> None:
    """/hot 逐项等于 /api/v1/hot?limit=10 的完整响应（GAP-21/56）。"""
    expected = _hot_items(page, base_url, 10)
    page.goto(f"{base_url}/hot", wait_until="networkidle")
    rows = page.locator(".hot-rank-row")
    expect(rows).to_have_count(len(expected))

    for index, item in enumerate(expected):
        row = rows.nth(index)
        rank = row.locator(".hot-rank-number")
        assert rank.inner_text().strip().lstrip("0") == str(index + 1)
        if index < 3:
            assert f"hot-rank-number-{index + 1}" in (rank.get_attribute("class") or "")
        link = row.locator(".hot-rank-link")
        expect(link).to_have_attribute("href", str(item["url"]))
        assert _title(item)[:12] in link.inner_text()
        assert row.locator(".hot-rank-sources-count").inner_text().strip() == str(item["heat"])
        assert str(item.get("source_name") or "") in row.locator(".hot-rank-meta").inner_text()

    note = page.locator(".hot-method-note").inner_text()
    assert "48" in note, note
    assert "加权分" in note and "关联讨论" in note, note


def test_parity_hot_times_and_related_sources_are_data_conditional(page: Page, base_url: str) -> None:
    """相对时间只来自 event_time；信源展开只在真有 related 时出现（GAP-56/57）。"""
    data = _api_data(page, base_url, "/api/v1/hot?limit=10")
    generated_at = datetime.fromisoformat(str(data["generated_at"]).replace("Z", "+00:00"))
    expected = data["items"]
    assert isinstance(expected, list)
    page.goto(f"{base_url}/hot", wait_until="networkidle")
    rows = page.locator(".hot-rank-row")

    for index, item in enumerate(expected):
        stamp = rows.nth(index).locator("time").first.get_attribute("datetime")
        assert stamp, f"第 {index + 1} 行缺 <time datetime>"
        rendered = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        event = datetime.fromisoformat(str(item["event_time"]).replace("Z", "+00:00"))
        assert rendered == event, f"第 {index + 1} 行 time={rendered} 应等于 event_time={event}"
        seconds = max(0, int((generated_at - event).total_seconds()))
        if seconds < 60:
            relative = "刚刚"
        elif seconds < 3600:
            relative = f"{seconds // 60}分钟前"
        elif seconds < 86400:
            relative = f"{seconds // 3600}小时前"
        else:
            relative = f"{seconds // 86400}天前"
        expect(rows.nth(index).locator("time").first).to_have_text(relative)

    with_related = sum(1 for item in expected if item.get("related_discussions"))
    expect(page.locator(".hot-rank-row details")).to_have_count(with_related)
    for index, item in enumerate(expected):
        related = item.get("related_discussions")
        if not related:
            continue
        candidates = [(item.get("source_name"), item.get("author"))]
        candidates.extend(
            (entry.get("source_name") or entry.get("source_id"), entry.get("author")) for entry in related
        )
        expected_labels: list[str] = []
        for source_name, author in candidates:
            name = str(source_name or "").strip()
            byline = str(author or "").strip()
            label = name if not byline or byline in name else f"{name} ({byline})"
            if label and label not in expected_labels:
                expected_labels.append(label)
        assert rows.nth(index).locator(".dup-tooltip-item").all_inner_texts() == expected_labels

    # GAP-57 accepted-divergence：这些元素不得出现
    for selector in (".hot-status", ".hot-rank-spark", "a[href^='/story/']"):
        expect(page.locator(selector)).to_have_count(0)
    assert "氛围票" not in page.content()


def test_parity_hot_navigation_and_mobile_entry_are_reachable(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/hot", wait_until="domcontentloaded")
    active = page.locator('.side-link-active[href="/hot"]')
    expect(active).to_have_count(1)
    expect(active).to_have_text("热点榜")

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator('.hot-topics-more[href="/hot"]')).to_have_text("完整榜单 →", timeout=10_000)
    tabs = page.locator(".m-tabbar .m-tab")
    expect(tabs).to_have_count(4)
    assert tabs.evaluate_all("els => els.map(el => el.getAttribute('href'))") == ["/", "/all", "/daily", "/more"]

    page.goto(f"{base_url}/more", wait_until="domcontentloaded")
    assert page.locator(".more-row").evaluate_all("els => els.map(el => el.getAttribute('href'))") == [
        "/wechat",
        "/bookmarks",
        "/about",
        "/changelog",
    ]


def test_parity_all_search_filters_and_terminal_total(page: Page, base_url: str) -> None:
    query, data = _bounded_query(page, base_url, "/api/v1/timeline")
    expected_stages = _expected_item_id_stages(page, base_url, "/api/v1/timeline", query, int(data["total"]))
    search_requests: list[str] = []
    page.on(
        "request",
        lambda request: search_requests.append(request.url)
        if "/api/v1/timeline" in request.url and "q=" in request.url
        else None,
    )
    page.goto(f"{base_url}/all?{urlencode({'q': query})}", wait_until="domcontentloaded")
    expect(page.locator("#channel-param")).to_be_visible()
    assert page.locator("[data-category-filter] [data-category]").all_inner_texts() == [
        "全部",
        "模型",
        "产品",
        "行业",
        "论文",
        "技巧",
    ]
    counts = _load_to_terminal(page, expected_stages)
    assert len(counts) >= 2
    assert any("page=2" in url for url in search_requests)
    assert all("cursor=" not in url for url in search_requests)


def test_parity_bookmark_export_can_be_imported(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    card = page.locator(".timeline-card").first
    expect(card).to_be_visible(timeout=10_000)
    item_id = card.get_attribute("data-item-id")
    assert item_id
    card.locator(".bookmark-btn").click()
    expect(card.locator(".bookmark-btn")).to_have_attribute("aria-pressed", "true")

    page.goto(f"{base_url}/bookmarks", wait_until="domcontentloaded")
    expect(page.locator(f'[data-item-id="{item_id}"]')).to_be_visible(timeout=10_000)
    with page.expect_download() as download_info:
        page.locator("#bookmark-export").click()
    download = download_info.value
    exported_path = download.path()
    assert exported_path is not None
    exported = json.loads(exported_path.read_text(encoding="utf-8"))
    assert [str(item["id"]) for item in exported["items"]] == [item_id]

    page.evaluate("localStorage.removeItem('ai-radar:bookmarks:v1')")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".timeline-card")).to_have_count(0)
    page.locator("#bookmark-import-file").set_input_files(exported_path)
    expect(page.locator("#run-meta")).to_have_text("共 1 条收藏 · 保存在本设备浏览器")
    expect(page.locator(f'[data-item-id="{item_id}"]')).to_be_visible()


def test_parity_wechat_api_identity_detail_and_back_state(page: Page, base_url: str) -> None:
    data = _api_data(page, base_url, "/api/v1/wechat?page=1&limit=50")
    expected = [str(item["detail_url"]) for item in data["items"]]
    assert expected
    page.goto(f"{base_url}/wechat", wait_until="domcontentloaded")
    cards = page.locator(".wechat-card")
    expect(cards.first).to_be_visible(timeout=10_000)
    assert cards.evaluate_all("els => els.map(el => el.dataset.detailUrl)") == expected

    first_title = str(data["items"][0]["title"])
    cards.first.locator(".item-title").click()
    assert unquote(page.url) == f"{base_url}{expected[0]}"
    expect(page.locator(".wechat-detail h1")).to_have_text(first_title)
    expect(page.locator(".summary-body")).not_to_be_empty()
    page.locator(".detail-back").click()
    expect(page).to_have_url(f"{base_url}/wechat")
    expect(page.locator(".wechat-card")).to_have_count(len(expected))
    assert page.locator(".wechat-card").evaluate_all("els => els.map(el => el.dataset.detailUrl)") == expected


def test_parity_wechat_search_empty_and_pagination_match_api(page: Page, base_url: str) -> None:
    first_page = _api_data(page, base_url, "/api/v1/wechat?page=1&limit=50")
    query = str(first_page["items"][0]["title"])
    filtered = _api_data(page, base_url, f"/api/v1/wechat?{urlencode({'q': query, 'page': 1, 'limit': 50})}")
    page.goto(f"{base_url}/wechat?{urlencode({'q': query})}", wait_until="domcontentloaded")
    expect(page.locator(".wechat-card")).to_have_count(len(filtered["items"]))
    assert page.locator(".wechat-card").evaluate_all("els => els.map(el => el.dataset.detailUrl)") == [
        item["detail_url"] for item in filtered["items"]
    ]

    page.locator("#search").fill("__ai_radar_wechat_no_results__")
    page.locator("form.feed-filter").evaluate("form => form.requestSubmit()")
    expect(page.locator(".empty-state")).to_contain_text("没有匹配条目")
    assert page.locator(".wechat-card").count() == 0

    second_page = _api_data(page, base_url, "/api/v1/wechat?page=2&limit=50")
    page.goto(f"{base_url}/wechat?page=2", wait_until="domcontentloaded")
    expect(page.locator(".wechat-card")).to_have_count(len(second_page["items"]))
    assert page.locator(".wechat-card").evaluate_all("els => els.map(el => el.dataset.detailUrl)") == [
        item["detail_url"] for item in second_page["items"]
    ]


def test_parity_about_sources_match_api_and_filter_states(page: Page, base_url: str) -> None:
    sources = _api_data(page, base_url, "/api/v1/sources")["sources"]
    expected_ids = [str(source["id"]) for source in sources]
    page.goto(f"{base_url}/about", wait_until="domcontentloaded")
    rows = page.locator("#sources-table tr")
    expect(rows).to_have_count(len(sources), timeout=10_000)
    assert rows.locator("td:first-child").all_inner_texts() == expected_ids

    query = expected_ids[0]
    page.locator("#search").fill(query)
    expected_filtered = [
        str(source["id"])
        for source in sources
        if query.lower()
        in f"{source['id']} {source['name']} {source['tier']} {source['kind']}".lower()
    ]
    expect(rows).to_have_count(len(expected_filtered))
    assert rows.locator("td:first-child").all_inner_texts() == expected_filtered

    page.locator("#search").fill("__ai_radar_source_no_results__")
    expect(page.locator("#sources-table td[colspan='5']")).to_have_text("没有匹配信源")
    assert page.locator("#sources-table tr:not(:has(td[colspan='5']))").count() == 0
    page.locator("#search").fill("")
    expect(rows).to_have_count(len(sources))


def test_parity_unique_navigation_tags_and_favicons_are_preserved(page: Page, base_url: str) -> None:
    for path in ["/", "/all", "/wechat", "/bookmarks", "/about"]:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        links = page.locator('.side-link[href="/wechat"]')
        assert links.count() == 1
        expect(links).to_have_text("微信文章解读")

    data = _api_data(page, base_url, "/api/v1/timeline?page=1&limit=40")
    page.goto(f"{base_url}/all", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    for item in data["items"][:10]:
        card = page.locator(f'[data-item-id="{item["id"]}"]')
        tags = item.get("enriched_tags") if isinstance(item.get("enriched_tags"), list) else item.get("topic_tags")
        expected_tags = [f"#{str(tag).lstrip('#')}" for tag in (tags or [])[:4]] or [
            "#社交" if item.get("source_kind") == "x" else "#AI"
        ]
        assert card.locator(".tags .tag").all_inner_texts() == expected_tags
        if item.get("source_icon_url"):
            expect(card.locator(".source-avatar")).to_have_attribute("src", str(item["source_icon_url"]))
        else:
            assert card.locator(".source-avatar").count() == 0
            expect(card.locator(".source-initial")).not_to_be_empty()


def test_parity_theme_three_state_persists_and_system_tracks_media(page: Page, base_url: str) -> None:
    page.emulate_media(color_scheme="dark")
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.locator('[data-theme-pref="system"]').click()
    expect(page.locator("html")).to_have_attribute("data-theme-mode", "system")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#10151c")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("html")).to_have_attribute("data-theme-mode", "system")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    page.emulate_media(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#f4f5f6")
    page.locator('[data-theme-pref="dark"]').click()
    expect(page.locator("html")).to_have_attribute("data-theme-mode", "dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.locator('[data-theme-pref="light"]').click()
    expect(page.locator("html")).to_have_attribute("data-theme-mode", "light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")


def test_parity_l1_prepaint_sets_theme_before_app_init(page: Page, base_url: str) -> None:
    page.add_init_script("localStorage.setItem('ai-radar:theme', 'dark')")
    page.route("**/app.js*", lambda route: route.abort())
    wechat = _api_data(page, base_url, "/api/v1/wechat?page=1&limit=50")
    detail_paths = [str(item["detail_url"]) for item in wechat["items"][:1]]
    assert detail_paths, "L1 prepaint 契约需要至少一个微信详情路由"
    paths = [
        "/",
        "/all",
        "/hot",
        "/daily",
        "/wechat",
        *detail_paths,
        "/bookmarks",
        "/about",
        "/more",
        "/changelog",
    ]
    for path in paths:
        page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        expect(page.locator("html")).to_have_attribute("data-theme-mode", "dark")
        expect(page.locator('meta[name="theme-color"]')).to_have_attribute("content", "#10151c")


def test_parity_collapsed_date_group_stays_collapsed_after_append(page: Page, base_url: str) -> None:
    items = [
        _mock_item(f"collapse-{index:02d}", f"Collapse item {index}", f"2026-08-02T10:{index:02d}:00Z")
        for index in range(41)
    ]

    def payload_for_url(parsed) -> dict[str, object]:  # noqa: ANN001
        page_number = int(parse_qs(parsed.query).get("page", ["1"])[0])
        page_items = items[:40] if page_number == 1 else items[40:]
        return _curated_payload(page_items, page_number, len(items))

    _strip_preload_and_mock_curated(page, payload_for_url)
    page.goto(f"{base_url}/?q=collapse-contract", wait_until="domcontentloaded")
    group = page.locator('.timeline-day[data-date="2026-08-02"]')
    expect(group).to_be_visible(timeout=10_000)
    group.locator(".date-collapse").click()
    expect(group.locator(".date-collapse")).to_have_attribute("aria-expanded", "false")
    page.locator(".scroll-sentinel").scroll_into_view_if_needed()
    expect(page.locator(".timeline-card")).to_have_count(41, timeout=10_000)
    expect(group).to_have_class(re.compile(r"date-group-collapsed"))
    appended = page.locator('[data-item-id="collapse-40"]').locator("xpath=ancestor::*[contains(@class, 'timeline-entry')]")
    expect(appended).to_have_class(re.compile(r"entry-hidden"))


def test_parity_desktop_date_group_uses_shanghai_day_and_weekday(page: Page, base_url: str) -> None:
    item = _mock_item("shanghai-boundary", "Shanghai boundary", "2026-01-01T16:30:00Z")
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload([item], 1, 1))
    page.goto(f"{base_url}/?q=shanghai-boundary", wait_until="domcontentloaded")
    group = page.locator('.timeline-day[data-date="2026-01-02"]')
    expect(group.locator(".desktop-date-label")).to_have_text("1月2日")
    expect(group.locator(".timeline-day-meta")).to_contain_text("星期五")


def test_parity_mobile_date_labels_use_today_yesterday_and_absolute_fallback(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    today = datetime.fromisoformat(
        page.evaluate(
            """() => {
              const parts = Object.fromEntries(
                new Intl.DateTimeFormat('en', {
                  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'
                }).formatToParts(new Date()).filter(part => part.type !== 'literal')
                  .map(part => [part.type, part.value])
              );
              return `${parts.year}-${parts.month}-${parts.day}`;
            }"""
        )
    ).date()
    dates = [today, today - timedelta(days=1), today - timedelta(days=3)]
    items = [
        _mock_item(
            f"mobile-date-{index}",
            f"Mobile date {index}",
            datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI).replace(hour=12).isoformat(),
        )
        for index, day in enumerate(dates)
    ]
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload(items, 1, len(items)))
    page.goto(f"{base_url}/?q=mobile-date-contract", wait_until="domcontentloaded")

    labels = page.locator(".timeline-day .mobile-date-label")
    expect(labels).to_have_count(3)
    assert labels.nth(0).inner_text().startswith("今天 ")
    assert labels.nth(1).inner_text().startswith("昨天 ")
    fallback = labels.nth(2).inner_text()
    assert not fallback.startswith(("今天 ", "昨天 "))
    assert f"{dates[2].month}月{dates[2].day}日" in fallback
    assert all(label.is_visible() for label in [labels.nth(0), labels.nth(1), labels.nth(2)])
    assert all(not label.is_visible() for label in page.locator(".desktop-date-label").all())


def test_parity_back_to_top_resets_scroll_position(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/all", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    expect(page.locator(".back-to-top")).to_be_visible()
    page.locator(".back-to-top").click()
    page.wait_for_function("window.scrollY === 0")


def test_parity_mobile_home_more_journey_reaches_four_approved_targets(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    tabs = page.locator(".m-tabbar .m-tab")
    expect(tabs).to_have_count(4)
    assert tabs.evaluate_all("els => els.map(el => el.getAttribute('href'))") == ["/", "/all", "/daily", "/more"]
    tabs.nth(3).click()
    expect(page).to_have_url(f"{base_url}/more")

    approved = [
        ("/wechat", "微信文章解读"),
        ("/bookmarks", "收藏"),
        ("/about", "关于"),
        ("/changelog", "Changelog"),
    ]
    rows = page.locator(".more-list .more-row")
    expect(rows).to_have_count(4)
    assert rows.evaluate_all("els => els.map(el => el.getAttribute('href'))") == [path for path, _ in approved]
    assert [text.replace("\n›", "") for text in rows.all_inner_texts()] == ["微信文章解读", "收藏", "关于", "更新日志"]

    for path, heading in approved:
        page.locator(f'.more-row[href="{path}"]').click()
        expect(page).to_have_url(f"{base_url}{path}")
        expect(page.locator("main h1")).to_contain_text(heading, timeout=10_000)
        page.goto(f"{base_url}/more", wait_until="domcontentloaded")


def test_parity_mobile_all_keeps_search_source_select_and_category_tabs(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/all", wait_until="domcontentloaded")
    expect(page.locator("form.feed-filter #search")).to_be_visible()
    expect(page.locator("form.feed-filter button[type=submit]")).to_be_visible()
    channel = page.locator("#channel-param")
    expect(channel).to_be_visible()
    assert channel.locator("option").all_inner_texts() == ["全部", "一手信源", "资讯", "推文"]
    categories = page.locator("[data-category-filter] .seg-item")
    assert categories.all_inner_texts() == ["全部", "模型", "产品", "行业", "论文", "技巧"]
    assert page.locator(".mobile-search-link").count() == 0
    category_rows = categories.evaluate_all("els => new Set(els.map(el => Math.round(el.getBoundingClientRect().top))).size")
    assert category_rows == 1
    assert page.locator(".seg-list").evaluate("el => getComputedStyle(el).flexWrap === 'nowrap'")


def test_parity_excluded_navigation_and_routes_remain_absent(page: Page, base_url: str) -> None:
    forbidden_paths = ["/topics", "/agent", "/feedback"]
    for path in ["/", "/all", "/hot", "/more", "/changelog"]:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        for forbidden in forbidden_paths:
            assert page.locator(f'a[href="{forbidden}"]').count() == 0
        navigation_text = " ".join(
            page.locator(".side-nav, .m-tabbar, .more-list").all_inner_texts()
        )
        for label in ["主题", "Agent 接入", "反馈"]:
            assert label not in navigation_text

    for path in forbidden_paths:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert response is not None and response.status == 404


def test_parity_daily_and_changelog_journeys_match_dynamic_content(page: Page, base_url: str) -> None:
    archive_days = _api_data(page, base_url, "/api/v1/curated/daily-archive")["days"]
    assert archive_days
    latest_date = str(archive_days[0]["date"])

    response = page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator(".daily-article").first).to_be_visible(timeout=10_000)
    articles = page.locator(".daily-article")
    article_count = articles.count()
    expect(page.locator(".daily-masthead-title")).to_contain_text("AI RADAR")
    expect(page.locator(".daily-masthead-title")).to_contain_text("日报")
    expect(page.locator("#daily-volume")).to_have_text(f"VOL.{latest_date.replace('-', '.')}")
    expect(page.locator(".daily-story-count")).to_have_text(f"{article_count} STORIES")

    month_details = page.locator("#daily-archive details.daily-side-month")
    assert month_details.count() >= 1
    assert sum(detail.locator(".daily-side-day").count() for detail in month_details.all()) == len(archive_days)
    first_month = month_details.first
    expect(first_month).to_have_attribute("open", "")
    first_month.locator("summary").click()
    assert first_month.get_attribute("open") is None
    first_month.locator("summary").click()
    expect(first_month).to_have_attribute("open", "")

    section_counts = [int(value) for value in page.locator(".daily-section-count strong").all_inner_texts()]
    assert sum(section_counts) == article_count
    assert all(
        re.match(r"^\d{2}\n.+\n[A-Z /]+\n\d+ 篇$", header)
        for header in page.locator(".daily-section-header").all_inner_texts()
    )
    visible_summary_text = "".join(page.locator(".daily-article-summary").all_inner_texts())
    cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", visible_summary_text))
    expected_minutes = max(1, (cjk_count + 299) // 300)
    highlights = page.locator("#daily-highlights")
    expect(highlights).to_contain_text(f"{article_count} 篇报道 · 约 {expected_minutes} 分钟")

    page.goto(f"{base_url}/daily?date=invalid", wait_until="domcontentloaded")
    status = page.locator('#daily-fallback[role="status"]')
    expect(status).to_be_visible(timeout=10_000)
    expect(status).to_contain_text(latest_date)
    expect(page).to_have_url(f"{base_url}/daily/{latest_date}")

    page.goto(f"{base_url}/daily?date=2025-01-01", wait_until="domcontentloaded")
    expect(page.locator(".daily-empty")).to_be_visible(timeout=10_000)
    expect(page).to_have_url(f"{base_url}/daily?date=2025-01-01")
    assert page.locator(".daily-article").count() == 0

    response = page.goto(f"{base_url}/changelog", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator(".cl-shell")).to_be_visible()
    expect(page.locator("#changelog-content h1")).to_have_text("Changelog")
    expect(page.locator('.side-link-active[href="/changelog"]')).to_have_text("更新日志")
