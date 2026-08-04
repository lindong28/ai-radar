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

    mobile_spacing = module.evaluate(
        """module => {
          const head = getComputedStyle(module.querySelector('.hot-topics-head'));
          const row = module.querySelector('.hot-topics-row');
          const rowStyle = row ? getComputedStyle(row) : null;
          return {
            headPadding: head.padding,
            rowGap: rowStyle && rowStyle.gap,
            rowPadding: rowStyle && rowStyle.padding,
          };
        }"""
    )
    assert mobile_spacing["headPadding"] == "13px 16px 4px"
    if expected:
        assert mobile_spacing["rowGap"] == "12px"
        assert mobile_spacing["rowPadding"] == "10px 16px"


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
        expected_tags = [str(tag).lstrip("#") for tag in (tags or [])[:4]] or [
            "社交" if item.get("source_kind") == "x" else "AI"
        ]
        assert card.locator(".tags .tag").all_inner_texts() == expected_tags
        assert card.locator(".tags .tag").first.evaluate(
            "el => getComputedStyle(el, '::before').content"
        ) == '"#"'
        if item.get("source_icon_url"):
            expect(card.locator(".source-avatar")).to_have_attribute("src", str(item["source_icon_url"]))
        else:
            assert card.locator(".source-avatar").count() == 0
            expect(card.locator(".source-initial")).not_to_be_empty()


def test_parity_theme_three_state_persists_and_system_tracks_media(page: Page, base_url: str) -> None:
    page.emulate_media(color_scheme="dark")
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    page.locator('[data-theme-pref="system"]').click()
    expect(page.locator(".theme-toggle-thumb")).to_have_attribute("data-pos", "auto")
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
    expect(page.locator(".theme-toggle-thumb")).to_have_attribute("data-pos", "dark")
    expect(page.locator("html")).to_have_attribute("data-theme-mode", "dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.locator('[data-theme-pref="light"]').click()
    expect(page.locator(".theme-toggle-thumb")).to_have_attribute("data-pos", "light")
    expect(page.locator("html")).to_have_attribute("data-theme-mode", "light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")

    page.wait_for_timeout(300)
    alignment = page.locator(".theme-toggle").evaluate(
        """toggle => {
          const thumb = toggle.querySelector('.theme-toggle-thumb').getBoundingClientRect();
          const active = toggle.querySelector('.theme-btn[aria-pressed="true"]').getBoundingClientRect();
          return Math.abs((thumb.left + thumb.width / 2) - (active.left + active.width / 2));
        }"""
    )
    assert alignment == pytest.approx(0, abs=0.6)


def test_parity_desktop_timeline_line_bookmark_accent_and_collapse(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    items = [
        _mock_item("rail-a", "Rail A", "2026-08-02T10:00:00Z"),
        _mock_item("rail-b", "Rail B", "2026-08-02T09:00:00Z"),
    ]
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload(items, 1, len(items)))
    page.goto(f"{base_url}/?q=rail-contract", wait_until="domcontentloaded")
    group = page.locator('.timeline-day[data-date="2026-08-02"]')
    day_items = group.locator(".timeline-day-items")
    expect(group.locator(".timeline-item")).to_have_count(2)

    geometry = day_items.evaluate(
        """items => {
          const box = items.getBoundingClientRect();
          const line = getComputedStyle(items, '::before');
          const dots = [...items.querySelectorAll('.timeline-dot')].map(dot => {
            const r = dot.getBoundingClientRect();
            return {x: r.left + r.width / 2, y: r.top + r.height / 2};
          });
          const chevron = items.parentElement.querySelector('.timeline-day-chevron').getBoundingClientRect();
          const lineTop = box.top + parseFloat(line.top);
          const lineBottom = box.bottom - parseFloat(line.bottom);
          const lineX = box.left + parseFloat(line.left);
          return {
            content: line.content,
            background: line.backgroundColor,
            lineTop,
            lineBottom,
            lineX,
            dotXs: dots.map(dot => dot.x),
            dotYs: dots.map(dot => dot.y),
            chevronBottom: chevron.bottom,
          };
        }"""
    )
    assert geometry["content"] == '""'
    assert geometry["background"] == "rgb(216, 219, 223)"
    assert all(x == pytest.approx(geometry["lineX"], abs=0.5) for x in geometry["dotXs"])
    assert geometry["lineTop"] <= min(geometry["dotYs"])
    assert geometry["lineBottom"] >= max(geometry["dotYs"])
    assert geometry["chevronBottom"] <= geometry["lineTop"]

    first_item = group.locator(".timeline-item").first
    first_item.locator(".bookmark-btn").click()
    expect(first_item).to_have_class(re.compile(r"timeline-item-starred"))
    expect(first_item.locator(".timeline-dot")).to_have_css("background-color", "rgb(184, 135, 58)")
    page.reload(wait_until="domcontentloaded")
    expect(first_item).to_have_class(re.compile(r"timeline-item-starred"))
    expect(first_item.locator(".timeline-dot")).to_have_css("background-color", "rgb(184, 135, 58)")
    first_item.locator(".bookmark-btn").click()
    expect(first_item).not_to_have_class(re.compile(r"timeline-item-starred"))
    expect(first_item.locator(".timeline-dot")).to_have_css("background-color", "rgb(19, 94, 107)")

    group.locator(".timeline-day-toggle").click()
    assert day_items.evaluate("items => getComputedStyle(items, '::before').content") == "none"


@pytest.mark.parametrize("viewport_width", [390, 720])
def test_parity_mobile_topbar_scrolls_away_and_day_head_sticks_at_viewport_top(
    page: Page,
    base_url: str,
    viewport_width: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 844})
    items = [
        _mock_item(f"mobile-{index}", f"Mobile {index}", f"2026-08-02T{10 - index:02d}:00:00Z")
        for index in range(10)
    ]
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload(items, 1, len(items)))
    page.goto(f"{base_url}/?q=mobile-topbar", wait_until="domcontentloaded")
    bar = page.locator(".app-mobile-bar")
    expect(bar).to_be_visible()
    initial = bar.bounding_box()
    assert initial is not None
    assert initial["height"] == pytest.approx(45, abs=0.5)
    expect(bar).to_have_css("position", "static")

    page.evaluate("window.scrollTo(0, 600)")
    page.wait_for_function("window.scrollY >= 590")
    scrolled = bar.bounding_box()
    assert scrolled is not None
    assert scrolled["y"] == pytest.approx(initial["y"] - page.evaluate("window.scrollY"), abs=1)
    day_head = page.locator(".timeline-day-head").first
    expect(day_head).to_have_css("position", "sticky")
    assert day_head.bounding_box()["y"] == pytest.approx(0, abs=1)
    expect(page.locator(".m-tabbar")).to_have_css("position", "fixed")
    assert page.locator(".m-tabbar").bounding_box()["height"] == pytest.approx(54, abs=0.5)


@pytest.mark.parametrize("viewport_width", [390, 720])
@pytest.mark.parametrize(
    ("path", "content_selector", "has_topbar"),
    [
        ("/", ".page-header", True),
        ("/all", ".page-header", False),
        ("/hot", ".hot-hero", False),
        ("/daily", ".daily-shell", False),
        ("/changelog", ".cl-shell", False),
    ],
)
def test_parity_mobile_topbar_only_exists_on_home_and_tabbar_stays_available(
    page: Page,
    base_url: str,
    viewport_width: int,
    path: str,
    content_selector: str,
    has_topbar: bool,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 844})
    # `domcontentloaded` fires before the stylesheet has been applied, so the
    # mobile layout is not in effect yet and geometry assertions read the
    # pre-layout values. Wait for load: nothing is painted before then, so
    # this still asserts on what the user actually sees first.
    page.goto(f"{base_url}{path}", wait_until="load")

    topbar = page.locator(".app-mobile-bar")
    if has_topbar:
        expect(topbar).to_be_visible()
    else:
        expect(topbar).to_have_count(0)

    tabbar = page.locator(".m-tabbar")
    expect(tabbar).to_be_visible()
    expect(tabbar).to_have_css("position", "fixed")
    assert tabbar.locator(".m-tab").evaluate_all("els => els.map(el => el.getAttribute('href'))") == [
        "/",
        "/all",
        "/daily",
        "/more",
    ]
    content = page.locator(content_selector)
    expect(content).to_be_visible()
    content_rect = content.bounding_box()
    assert content_rect is not None
    # The page's own content starts at the top of the viewport when there is no
    # top bar, and just below it when there is one. It must not be pushed off
    # the first screen. An exact y is wrong here: on the home page the hot-topics
    # card sits above the header, matching the reference site's mobile order
    # (top bar 0-45, hot card at 45, feed heading below it).
    assert content_rect["y"] >= 0
    assert content_rect["y"] < 844, "page content must start within the first screen"
    if has_topbar:
        topbar_rect = topbar.bounding_box()
        assert topbar_rect is not None
        assert content_rect["y"] >= topbar_rect["y"] + topbar_rect["height"] - 1


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


def test_parity_desktop_feed_geometry_and_widest_date_alignment(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    item = _mock_item("widest-date", "Widest date", "2026-12-31T04:30:00Z")
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload([item], 1, 1))
    page.goto(f"{base_url}/?q=widest-date", wait_until="domcontentloaded")

    group = page.locator('.timeline-day[data-date="2026-12-31"]')
    toggle = group.locator(".timeline-day-toggle")
    expect(group.locator(".desktop-date-label")).to_have_text("12月31日")
    expect(group.locator(".timeline-day-meta")).to_contain_text("星期四")
    geometry = group.evaluate(
        """group => {
          const glyph = (el) => {
            const range = document.createRange();
            range.selectNodeContents(el);
            const rect = range.getBoundingClientRect();
            return {left: rect.left, right: rect.right, width: rect.width, height: rect.height};
          };
          const main = document.querySelector('main.app-main');
          const head = group.querySelector('.timeline-day-head');
          const toggle = group.querySelector('.timeline-day-toggle');
          const date = group.querySelector('.desktop-date-label');
          const stamp = group.querySelector('.timeline-time time');
          const mainRect = main.getBoundingClientRect();
          const headRect = head.getBoundingClientRect();
          const dateRect = glyph(date);
          const stampRect = glyph(stamp);
          return {
            mainLeft: mainRect.left,
            mainWidth: mainRect.width,
            columns: getComputedStyle(group.querySelector('.timeline-entry')).gridTemplateColumns,
            dateRightMinusTimeRight: dateRect.right - stampRect.right,
            dateInsideTimeTrack: dateRect.left >= headRect.left - 0.5 && dateRect.right <= headRect.left + 64.5,
            dateLineCount: date.getClientRects().length,
            toggleFits: toggle.scrollWidth <= toggle.clientWidth,
          };
        }"""
    )
    assert geometry["mainLeft"] == pytest.approx(180, abs=0.5)
    assert geometry["mainWidth"] == pytest.approx(1260, abs=0.5)
    assert geometry["columns"] == "64px 22px 1118px"
    assert geometry["dateRightMinusTimeRight"] == pytest.approx(0, abs=1)
    assert geometry["dateInsideTimeTrack"] is True
    assert geometry["dateLineCount"] == 1
    assert geometry["toggleFits"] is True

    toggle.focus()
    expect(toggle).to_be_focused()
    expect(toggle).to_have_attribute("aria-label", "折叠 12月31日")
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(group.locator(".timeline-entry")).to_have_class(re.compile(r"entry-hidden"))
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")


@pytest.mark.parametrize(
    ("viewport_width", "expected_width", "expected_left"),
    [(960, 640, 160), (720, 640, 40), (641, 605, 18), (640, 604, 18)],
)
def test_parity_feed_column_keeps_reference_net_width(
    page: Page,
    base_url: str,
    viewport_width: int,
    expected_width: int,
    expected_left: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 900})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator(".timeline")).to_be_visible(timeout=10_000)
    rect = page.locator(".timeline").bounding_box()
    assert rect is not None
    assert rect["width"] == pytest.approx(expected_width, abs=0.5)
    assert rect["x"] == pytest.approx(expected_left, abs=0.5)
    assert viewport_width - rect["x"] - rect["width"] == pytest.approx(expected_left, abs=0.5)


@pytest.mark.parametrize("viewport_width", [960, 720, 641, 640, 390])
def test_parity_mobile_timestamp_uses_visible_aihot_row_metrics(
    page: Page,
    base_url: str,
    viewport_width: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 900})
    page.goto(f"{base_url}/all", wait_until="domcontentloaded")
    timestamp = page.locator(".timeline-time").first
    expect(timestamp).to_be_visible(timeout=10_000)
    expect(timestamp).to_have_css("width", "40px")
    expect(timestamp).to_have_css("font-size", "12px")

    metrics = timestamp.evaluate(
        """el => {
          const text = el.querySelector('time');
          const textRange = document.createRange();
          textRange.selectNodeContents(text);
          const glyph = textRange.getBoundingClientRect();
          const box = el.getBoundingClientRect();
          const rowElement = el.closest('.timeline-entry');
          const row = rowElement.getBoundingClientRect();
          const card = rowElement.querySelector('.timeline-card').getBoundingClientRect();
          const rowStyle = getComputedStyle(rowElement);
          const style = getComputedStyle(el);
          return {
            width: box.width,
            topInset: box.top - row.top,
            bodyInset: card.left - box.right,
            columnGap: rowStyle.columnGap,
            glyphWidth: glyph.width,
            padding: style.padding,
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
            letterSpacing: style.letterSpacing,
            color: style.color,
            textAlign: style.textAlign,
          };
        }"""
    )
    assert metrics["width"] == pytest.approx(40, abs=0.1)
    assert metrics["topInset"] == pytest.approx(14, abs=0.1)
    assert metrics["bodyInset"] == pytest.approx(12, abs=0.1)
    assert metrics["columnGap"] == "12px"
    assert metrics["glyphWidth"] < metrics["width"]
    assert metrics["padding"] == "0px"
    assert "ui-monospace" in metrics["fontFamily"]
    assert metrics["fontSize"] == "12px"
    assert metrics["fontWeight"] == "400"
    assert metrics["lineHeight"] == "18px"
    assert metrics["letterSpacing"] == "normal"
    assert metrics["color"] == "rgb(101, 112, 126)"
    assert metrics["textAlign"] == "left"


@pytest.mark.parametrize(
    ("viewport_width", "shell_left", "shell_width", "shell_right"),
    [(1440, 370, 880, 190), (960, 40, 880, 40), (720, 18, 684, 18), (390, 18, 354, 18)],
)
def test_parity_changelog_shell_stays_centered_across_regimes(
    page: Page,
    base_url: str,
    viewport_width: int,
    shell_left: int,
    shell_width: int,
    shell_right: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 900})
    page.goto(f"{base_url}/changelog", wait_until="domcontentloaded")
    shell = page.locator(".cl-shell")
    expect(shell).to_be_visible()
    rect = shell.bounding_box()
    assert rect is not None
    assert rect["x"] == pytest.approx(shell_left, abs=0.5)
    assert rect["width"] == pytest.approx(shell_width, abs=0.5)
    assert viewport_width - rect["x"] - rect["width"] == pytest.approx(shell_right, abs=0.5)
    if viewport_width <= 960:
        expect(page.locator(".app-mobile-bar")).to_have_count(0)


def test_parity_daily_desktop_uses_reference_inner_grid_width(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    shell = page.locator(".daily-shell")
    expect(shell).to_be_visible()
    rect = shell.bounding_box()
    assert rect is not None
    assert rect["x"] == pytest.approx(180, abs=0.5)
    assert rect["width"] == pytest.approx(1204, abs=0.5)
    assert 1440 - rect["x"] - rect["width"] == pytest.approx(56, abs=0.5)


@pytest.mark.parametrize("viewport_width", [390, 720])
def test_parity_mobile_date_labels_use_today_yesterday_and_absolute_fallback(
    page: Page,
    base_url: str,
    viewport_width: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 844})
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
    weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    assert labels.locator(".m-daybar-main").all_inner_texts() == [
        "今天",
        "昨天",
        f"{dates[2].month}月{dates[2].day}日",
    ]
    assert labels.locator(".m-daybar-sub").all_inner_texts() == [
        f"{dates[0].month}月{dates[0].day}日 {weekdays[dates[0].weekday()]}",
        f"{dates[1].month}月{dates[1].day}日 {weekdays[dates[1].weekday()]}",
        weekdays[dates[2].weekday()],
    ]
    for part in labels.locator(".m-daybar-main").all():
        expect(part).to_have_css("font-size", "13.5px")
        expect(part).to_have_css("font-weight", "900")
    for part in labels.locator(".m-daybar-sub").all():
        expect(part).to_have_css("font-size", "11.5px")
        expect(part).to_have_css("font-weight", "700")
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
    expect(page.locator(".feed-channel-select-icon")).to_be_visible()
    expect(channel).to_have_attribute("data-channel-filter-bound", "true")
    assert channel.locator("option").all_inner_texts() == ["全部", "一手信源", "资讯", "推文"]
    channel.select_option("x")
    expect(channel).to_have_class(re.compile(r"feed-channel-select-active"))
    expect(page.locator(".feed-channel-select-icon")).to_have_css("color", "rgb(19, 94, 107)")
    channel.select_option("")
    expect(channel).not_to_have_class(re.compile(r"feed-channel-select-active"))
    categories = page.locator("[data-category-filter] .seg-item")
    assert categories.all_inner_texts() == ["全部", "模型", "产品", "行业", "论文", "技巧"]
    assert page.locator(".mobile-search-link").count() == 0
    category_rows = categories.evaluate_all("els => new Set(els.map(el => Math.round(el.getBoundingClientRect().top))).size")
    assert category_rows == 1
    assert page.locator(".seg-list").evaluate("el => getComputedStyle(el).flexWrap === 'nowrap'")


def test_parity_global_scrollbar_controls_touch_and_tooltip_bridge(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/hot", wait_until="domcontentloaded")

    global_styles = page.locator("html").evaluate(
        """html => {
          const scrollbar = getComputedStyle(html, '::-webkit-scrollbar');
          const link = document.querySelector('a');
          const button = document.querySelector('button');
          const label = document.createElement('label');
          document.body.append(label);
          const result = {
            scrollbarWidth: scrollbar.width,
            scrollbarHeight: scrollbar.height,
            touchActions: [link, button, label].map(el => getComputedStyle(el).touchAction),
            tapColors: [link, button, label].map(el => getComputedStyle(el).webkitTapHighlightColor),
          };
          label.remove();
          return result;
        }"""
    )
    assert global_styles["scrollbarWidth"] == "6px"
    assert global_styles["scrollbarHeight"] == "6px"
    assert global_styles["touchActions"] == ["manipulation"] * 3
    assert global_styles["tapColors"] == ["rgba(79, 163, 179, 0.22)"] * 3

    page.evaluate(
        """() => {
              const details = document.createElement('details');
              details.id = 'gap67-fixture';
              details.className = 'hot-rank-sources timeline-dup-hover';
              details.style.cssText = 'position:fixed;top:100px;right:20px;z-index:1000';
          details.innerHTML = '<summary><span class="hot-rank-sources-count">88</span></summary><span class="dup-tooltip hot-topics-tooltip"><span class="dup-tooltip-item">来源</span></span>';
          document.body.append(details);
        }"""
    )
    page.evaluate("import('/app.js?v=gap67-test').then(module => module.initNavigationOnly())")
    host = page.locator("#gap67-fixture")
    summary = host.locator("summary")
    tooltip = host.locator(".hot-topics-tooltip")
    summary.hover()
    expect(tooltip).to_be_visible()
    box = host.bounding_box()
    assert box is not None
    bridge = host.evaluate(
        "el => ({content:getComputedStyle(el, '::after').content,height:getComputedStyle(el, '::after').height})"
    )
    assert bridge == {"content": '""', "height": "8px"}
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] + 4, steps=8)
    expect(tooltip).to_be_visible()
    tooltip_box = tooltip.bounding_box()
    assert tooltip_box is not None
    summary.hover()
    page.mouse.move(tooltip_box["x"] + 20, tooltip_box["y"] + 10, steps=8)
    expect(tooltip).to_be_visible()

    page.goto(f"{base_url}/all", wait_until="domcontentloaded")
    submit = page.locator(".filter-submit")
    box = submit.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    expect(submit).to_have_css("transform", re.compile(r"matrix\(0\.98"))
    page.mouse.up()
    submit.evaluate("el => { el.disabled = true; }")
    expect(submit).to_have_css("opacity", "0.4")
    expect(submit).to_have_css("cursor", "not-allowed")
    search = page.locator("#search")
    search.evaluate("el => { el.disabled = true; }")
    expect(search).to_have_css("opacity", "0.5")
    expect(search).to_have_css("cursor", "not-allowed")


def test_parity_theme_thumb_disables_motion_when_reduced(page: Page, base_url: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator(".theme-toggle-thumb")).to_have_css("transition-duration", "0s")


@pytest.mark.parametrize(
    ("viewport_width", "expected_left", "expected_width", "expected_right"),
    [
        (1440, 250, 1120, 70),
        (1201, 232, 917, 52),
        (960, 100, 760, 100),
        (640, 32, 576, 32),
        (390, 32, 326, 32),
    ],
)
def test_parity_hot_page_uses_aihot_container_box(
    page: Page,
    base_url: str,
    viewport_width: int,
    expected_left: int,
    expected_width: int,
    expected_right: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 900})
    page.goto(f"{base_url}/hot", wait_until="domcontentloaded")
    container = page.locator("main.app-main > .hot-page")
    expect(container).to_be_visible(timeout=10_000)
    rect = container.bounding_box()
    assert rect is not None
    assert rect["x"] == pytest.approx(expected_left, abs=0.5)
    assert rect["width"] == pytest.approx(expected_width, abs=0.5)
    assert viewport_width - rect["x"] - rect["width"] == pytest.approx(expected_right, abs=0.5)
    expect(page.locator("main.app-main")).to_have_css("display", "grid")
    expect(page.locator("main.app-main")).to_have_css("max-width", "none")
    expect(page.locator(".hot-hero")).to_have_css("max-width", "760px")
    expect(page.locator(".hot-hero p")).to_have_css("max-width", "680px")


def test_parity_navigation_uses_inline_lucide_svg_paths(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    side_icons = page.locator(".side-link .side-icon")
    expect(side_icons).to_have_count(8)
    assert side_icons.evaluate_all(
        "els => els.every(el => el.querySelector('svg path') && el.querySelector('svg').getAttribute('stroke') === 'currentColor')"
    )
    assert side_icons.evaluate_all(
        "els => new Set(els.map(el => el.closest('a').getAttribute('href'))).size"
    ) == 8

    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="domcontentloaded")
    mobile_icons = page.locator(".m-tab .m-tab-icon")
    expect(mobile_icons).to_have_count(4)
    assert mobile_icons.evaluate_all("els => els.every(el => Boolean(el.querySelector('svg path')))" )


@pytest.mark.parametrize(
    ("theme", "background", "foreground"),
    [
        ("light", "rgb(19, 94, 107)", "rgb(252, 252, 253)"),
        ("dark", "rgb(79, 163, 179)", "rgb(16, 21, 28)"),
    ],
)
def test_parity_search_submit_is_aihot_primary_button(
    page: Page,
    base_url: str,
    theme: str,
    background: str,
    foreground: str,
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.add_init_script(f"localStorage.setItem('ai-radar:theme', '{theme}')")
    page.goto(f"{base_url}/all", wait_until="domcontentloaded")
    submit = page.locator(".filter-submit")
    expect(submit).to_have_css("background-color", background)
    expect(submit).to_have_css("border-color", background)
    expect(submit).to_have_css("color", foreground)
    assert submit.bounding_box()["width"] == pytest.approx(74, abs=0.5)


@pytest.mark.parametrize("viewport_width", [720, 390])
def test_parity_mobile_reason_is_fully_visible_and_uses_aihot_line_height(
    page: Page,
    base_url: str,
    viewport_width: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 900})
    item = _mock_item("long-reason", "Long reason", "2026-08-02T10:00:00Z")
    item["reasoning"] = "这是一个足够长的推荐理由，用于确认移动端不会只保留两行，而是会把全部文本完整展示给读者。" * 3
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload([item], 1, 1))
    page.goto(f"{base_url}/?q=reason-contract", wait_until="domcontentloaded")
    reason = page.locator(".reason")
    expect(reason).to_be_visible()
    metrics = reason.evaluate(
        "el => ({clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,lineHeight:getComputedStyle(el).lineHeight,clamp:getComputedStyle(el).webkitLineClamp,overflow:getComputedStyle(el).overflow})"
    )
    assert metrics["clientHeight"] == metrics["scrollHeight"]
    assert metrics["lineHeight"] == "21px"
    assert metrics["clamp"] == "none"
    assert metrics["overflow"] == "visible"


def test_parity_home_hides_tags_but_all_keeps_them(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)
    assert page.locator(".timeline-card .tags").count() == 0
    page.goto(f"{base_url}/all", wait_until="domcontentloaded")
    expect(page.locator(".timeline-card .tag").first).to_be_visible(timeout=10_000)


def test_parity_mobile_feed_header_keeps_only_source_and_score(page: Page, base_url: str) -> None:
    item = _mock_item("mobile-density", "Mobile density", "2026-08-02T10:00:00Z")
    item["source_id"] = "openai_blog"
    item["source_name"] = "OpenAI Blog"
    page.set_viewport_size({"width": 390, "height": 844})
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload([item], 1, 1))
    page.goto(f"{base_url}/?q=mobile-density", wait_until="domcontentloaded")
    card = page.locator(".timeline-card")
    expect(card).to_be_visible()
    assert card.locator(".timeline-selected-badge").count() == 0
    assert card.locator(".bookmark-btn").count() == 0
    assert card.locator(".tags").count() == 0
    expect(card.locator(".timeline-score")).to_be_visible()
    source_name = card.locator(".source-name")
    assert source_name.evaluate("el => el.scrollWidth <= el.clientWidth")

    page.set_viewport_size({"width": 1440, "height": 900})
    page.reload(wait_until="domcontentloaded")
    card = page.locator(".timeline-card")
    expect(card.locator(".timeline-selected-badge")).to_be_visible()
    expect(card.locator(".bookmark-btn")).to_be_visible()


def test_parity_mobile_day_heading_is_not_a_collapse_control(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    expect(page.locator(".timeline-day-head").first).to_be_visible(timeout=10_000)
    expect(page.locator(".timeline-day-chevron")).to_have_count(0)
    expect(page.locator("[aria-expanded]")).to_have_count(0)
    expect(page.locator(".date-collapse")).to_have_count(0)
    assert page.locator(".timeline-day-toggle").first.evaluate("el => el.tagName") == "DIV"
    assert page.locator(".timeline-day-toggle").first.get_attribute("tabindex") is None

    page.set_viewport_size({"width": 1440, "height": 900})
    page.reload(wait_until="domcontentloaded")
    toggle = page.locator(".timeline-day-toggle").first
    assert toggle.evaluate("el => el.tagName") == "BUTTON"
    expect(toggle).to_have_attribute("aria-expanded", "true")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "false")


def test_parity_feed_structure_rebuilds_when_crossing_mobile_breakpoint(page: Page, base_url: str) -> None:
    item = _mock_item("responsive-structure", "Responsive structure", "2026-08-02T10:00:00Z")
    item["source_id"] = "openai_blog"
    item["source_name"] = "OpenAI Blog"
    _strip_preload_and_mock_curated(page, lambda parsed: _curated_payload([item], 1, 1))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}/?q=responsive-structure", wait_until="domcontentloaded")
    expect(page.locator(".timeline-day-toggle")).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".bookmark-btn")).to_be_visible()

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".timeline-day-toggle")).not_to_have_attribute("aria-expanded", re.compile(".+"))
    assert page.locator(".timeline-day-toggle").evaluate("el => el.tagName") == "DIV"
    assert page.locator(".bookmark-btn").count() == 0
    assert page.locator(".source-name").evaluate("el => el.scrollWidth <= el.clientWidth")

    page.set_viewport_size({"width": 1440, "height": 900})
    expect(page.locator(".timeline-day-toggle")).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".bookmark-btn")).to_be_visible()


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
    issue_data = _api_data(page, base_url, f"/api/v1/curated?date={latest_date}")
    issue_items = issue_data["items"]
    independently_recomputed = [
        len(issue_items),
        sum(item.get("source_kind") != "x" and item.get("tier") == "T1" for item in issue_items),
        sum(
            "模型发布" in (item.get("topic_tags") or [])
            and "教程/实践" not in (item.get("topic_tags") or [])
            for item in issue_items
        ),
        len({item.get("source_id") for item in issue_items}),
    ]
    metrics = page.locator(".daily-metrics .daily-metric")
    expect(metrics).to_have_count(4)
    assert metrics.locator(".daily-metric-label").all_inner_texts() == ["今日事件", "一手报道", "新模型", "信源"]
    assert [int(value) for value in metrics.locator(".daily-metric-value").all_inner_texts()] == independently_recomputed

    page.goto(f"{base_url}/daily?date=invalid", wait_until="domcontentloaded")
    status = page.locator('#daily-fallback[role="status"]')
    expect(status).to_be_visible(timeout=10_000)
    expect(status).to_contain_text(latest_date)
    expect(page).to_have_url(f"{base_url}/daily/{latest_date}")

    page.goto(f"{base_url}/daily?date=2025-01-01", wait_until="domcontentloaded")
    expect(page.locator(".daily-empty")).to_be_visible(timeout=10_000)
    expect(page).to_have_url(f"{base_url}/daily?date=2025-01-01")
    assert page.locator(".daily-article").count() == 0
    expect(page.locator(".daily-metrics")).to_be_hidden()
    assert page.locator(".daily-metrics").inner_html() == ""

    response = page.goto(f"{base_url}/changelog", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    expect(page.locator(".cl-shell")).to_be_visible()
    expect(page.locator("#changelog-content h1")).to_have_text("Changelog")
    expect(page.locator('.side-link-active[href="/changelog"]')).to_have_text("更新日志")


@pytest.mark.parametrize("viewport_width", [960, 720, 640, 390])
def test_parity_daily_metrics_use_two_by_two_mobile_grid(
    page: Page,
    base_url: str,
    viewport_width: int,
) -> None:
    page.set_viewport_size({"width": viewport_width, "height": 900})
    page.goto(f"{base_url}/daily", wait_until="domcontentloaded")
    metrics = page.locator(".daily-metric")
    expect(metrics).to_have_count(4)
    boxes = [metric.bounding_box() for metric in metrics.all()]
    assert all(box is not None for box in boxes)
    assert boxes[0]["y"] == pytest.approx(boxes[1]["y"], abs=0.5)
    assert boxes[2]["y"] == pytest.approx(boxes[3]["y"], abs=0.5)
    assert boxes[2]["y"] > boxes[0]["y"]
    assert boxes[0]["x"] == pytest.approx(boxes[2]["x"], abs=0.5)
    assert boxes[1]["x"] == pytest.approx(boxes[3]["x"], abs=0.5)
