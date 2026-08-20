from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.web.app import create_app
from airadar.web.routes import curated as curated_routes

FIXED_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:
        if tz is None:
            return FIXED_NOW.replace(tzinfo=None)
        return FIXED_NOW


def _related(source_id: str, source_name: str, author: str) -> dict[str, str]:
    return {
        "source_id": source_id,
        "source_name": source_name,
        "source_kind": "x",
        "author": author,
        "url": f"https://example.com/{source_id}",
    }


@pytest.fixture
def hot_items() -> list[dict[str, Any]]:
    return [
        {
            "id": "c-normal",
            "title": "Normal timestamp",
            "title_zh": "正常发布时间",
            "url": "https://example.com/normal",
            "source_name": "Normal Wire",
            "source_kind": "feed",
            "author": "Chen",
            "published_at": "2026-08-02T10:00:00Z",
            "fetched_at": "2026-08-02T10:05:00Z",
            "weighted_score": 10.0,
            "related_discussions": [],
        },
        {
            "id": "b-invalid",
            "title": "Invalid source timestamp",
            "title_zh": "非法发布时间回退",
            "url": "https://example.com/invalid",
            "source_name": "Invalid Wire",
            "source_kind": "wechat",
            "author": "测试作者",
            "published_at": "not-a-date",
            "fetched_at": "2026-08-02T09:00:00Z",
            "weighted_score": 9.0,
            "related_discussions": [
                _related("related-two", "Related Two", "R2"),
                _related("related-two", "Related Two", "R2"),
                _related("related-three", "Related Three", "R3"),
            ],
        },
        {
            "id": "a-future",
            "title": "Future source timestamp",
            "title_zh": "未来发布时间回退",
            "url": "https://example.com/future",
            "source_name": "Future Wire",
            "source_kind": "feed",
            "author": "Ada",
            "published_at": "2026-08-04T00:00:00Z",
            "fetched_at": "2026-08-02T11:00:00Z",
            "weighted_score": 10.0,
            "related_discussions": [_related("related-one", "Related One", "R1")],
        },
    ]


@pytest.fixture
def hot_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hot_items: list[dict[str, Any]],
) -> TestClient:
    db_path = tmp_path / "hot.db"
    migrate(db_path)
    monkeypatch.setattr(curated_routes, "datetime", _FrozenDateTime)
    # 候选集由后台线程供给（ADR-060），所以测试从缓存这一层注入，而不是从
    # `_compute_archive_page`——请求路径已经不再走它了。
    monkeypatch.setattr(
        curated_routes.hot_cache.HOT_CANDIDATE_CACHE,
        "peek",
        lambda: hot_items,
    )
    return TestClient(create_app(db_path))


@pytest.fixture
def unready_hot_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """候选缓存尚未就绪的客户端——peek 返回 None，且**绝不**触发计算。"""
    db_path = tmp_path / "hot-unready.db"
    migrate(db_path)
    monkeypatch.setattr(curated_routes, "datetime", _FrozenDateTime)
    monkeypatch.setattr(curated_routes.hot_cache.HOT_CANDIDATE_CACHE, "peek", lambda: None)
    return TestClient(create_app(db_path))


def test_hot_api_exposes_snapshot_fields_formula_and_event_time_fallback(
    hot_client: TestClient,
) -> None:
    response = hot_client.get("/api/v1/hot", params={"limit": 10})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["generated_at"] == "2026-08-02T12:00:00Z"
    assert data["hours"] == 48
    assert [item["id"] for item in data["items"]] == ["a-future", "b-invalid", "c-normal"]
    assert [item["heat"] for item in data["items"]] == [105, 105, 100]
    assert data["items"][0]["event_time"] == data["items"][0]["fetched_at"]
    assert data["items"][1]["event_time"] == data["items"][1]["fetched_at"]
    assert data["items"][2]["event_time"] == data["items"][2]["published_at"]
    expected_fields = {
        "id",
        "title",
        "url",
        "source_name",
        "published_at",
        "fetched_at",
        "event_time",
        "source_kind",
        "author",
        "related_discussions",
        "heat",
    }
    assert all(set(item) == expected_fields for item in data["items"])
    assert len(data["items"][0]["related_discussions"]) == 1
    assert len(data["items"][1]["related_discussions"]) == 3
    assert data["items"][2]["related_discussions"] == []


def test_hot_page_dom_matches_api_and_omits_unsupported_aihot_concepts(
    hot_client: TestClient,
) -> None:
    api_items = hot_client.get("/api/v1/hot", params={"limit": 10}).json()["data"]["items"]
    response = hot_client.get("/hot")

    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.select(".hot-rank-row")
    assert len(rows) == len(api_items)
    for rank, (row, item) in enumerate(zip(rows, api_items, strict=True), start=1):
        assert row.get("data-item-id") == item["id"]
        assert row.select_one(".hot-rank-number").get_text(strip=True) == f"{rank:02d}"
        link = row.select_one(".hot-rank-link")
        assert link.get_text(strip=True) == item["title"]
        assert link.get("href") == item["url"]
        assert row.select_one(".hot-rank-sources-count").get_text(strip=True) == str(item["heat"])
        assert row.select_one(".hot-rank-source").get_text(strip=True) == item["source_name"]
        assert row.select_one("time").get("datetime") == item["event_time"]
        assert bool(row.select_one("details.hot-rank-sources")) is bool(item["related_discussions"])

    assert [row.select_one("time").get_text(strip=True) for row in rows] == [
        "1小时前",
        "3小时前",
        "2小时前",
    ]

    note = soup.select_one(".hot-method-note").get_text(" ", strip=True)
    assert "过去 48 小时" in note
    assert "加权分×10 + 关联讨论×5" in note
    assert not soup.select(".hot-status, [class*='hot-rank-spark']")
    assert "氛围票" not in response.text
    assert not soup.select("a[href^='/story/']")


def test_hot_page_related_source_list_is_real_complete_and_deduplicated(
    hot_client: TestClient,
) -> None:
    response = hot_client.get("/hot")
    soup = BeautifulSoup(response.text, "html.parser")

    first_sources = [node.get_text(" ", strip=True) for node in soup.select("[data-item-id='a-future'] .dup-tooltip-item")]
    second_sources = [node.get_text(" ", strip=True) for node in soup.select("[data-item-id='b-invalid'] .dup-tooltip-item")]
    assert first_sources == ["Future Wire (Ada)", "Related One (R1)"]
    assert second_sources == ["Invalid Wire (测试作者)", "Related Two (R2)", "Related Three (R3)"]
    assert len(first_sources) == len(set(first_sources))
    assert len(second_sources) == len(set(second_sources))
    assert not soup.select("[data-item-id='c-normal'] details")


def test_hot_page_renders_measured_empty_state(
    hot_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """就绪但窗口内确实没有条目——"暂无热点"在这里是真话。"""
    monkeypatch.setattr(curated_routes.hot_cache.HOT_CANDIDATE_CACHE, "peek", lambda: [])

    response = hot_client.get("/hot")
    soup = BeautifulSoup(response.text, "html.parser")

    assert response.status_code == 200
    assert not soup.select(".hot-rank-row")
    assert soup.select_one(".hot-rank-empty").get_text(strip=True) == "过去 48 小时暂无热点"
    assert not soup.select("[data-hot-preparing]")


def test_ready_but_empty_hot_api_is_a_cacheable_200(hot_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curated_routes.hot_cache.HOT_CANDIDATE_CACHE, "peek", lambda: [])

    response = hot_client.get("/api/v1/hot", params={"limit": 2})

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []
    assert response.headers["cache-control"] == "public, max-age=90, stale-while-revalidate=30"


def test_unready_hot_api_is_503_and_never_publicly_cached(unready_hot_client: TestClient) -> None:
    """未就绪必须与"确实没有热点"在响应层可区分（ADR-060）。

    200 + 空 items 会拿到 `public, max-age=90`，把一次冷态放大成约 120 秒的
    缓存空结果；503 走 `_public_pagination_cache_control` 的非 200 分支，自动
    `private, no-store`。
    """
    response = unready_hot_client.get("/api/v1/hot", params={"limit": 2})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.headers["cache-control"] == "private, no-store"


def test_unready_hot_page_does_not_claim_there_are_no_hot_topics(
    unready_hot_client: TestClient,
) -> None:
    response = unready_hot_client.get("/hot")
    soup = BeautifulSoup(response.text, "html.parser")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert not soup.select(".hot-rank-row")
    preparing = soup.select_one("[data-hot-preparing]")
    assert preparing is not None
    assert preparing.get_text(strip=True) == "热点榜单正在生成，稍后自动刷新"
    assert "暂无热点" not in response.text


def test_hot_topics_ssr_partial_and_csr_renderer_stay_structurally_paired() -> None:
    """ADR-012 的硬契约：同一 surface 的 SSR 与 CSR 必须成对同步。

    只改一边会留下另一半，表现为 CSR 接管首屏时结构跳变。这里比对两边用到的
    class 名集合——它是"同形"最便宜的可机械检查的代理。
    """
    partial = Path("web/templates/_hot_topics.html").read_text()
    app_js = Path("web/static/app.js").read_text()
    csr_block = app_js.split("async function renderHotTopics")[1].split("/* ---------- infinite scroll")[0]

    paired_classes = [
        "hot-topics-head",
        "hot-topics-title",
        "hot-topics-title-desktop",
        "hot-topics-title-mobile",
        "hot-topics-more",
        "hot-topics-list",
        "hot-topics-row",
        "hot-topics-rank",
        "hot-topics-link",
        "hot-topics-meta",
        "hot-topics-heat",
    ]
    for class_name in paired_classes:
        assert class_name in partial, f"SSR partial 缺 {class_name}"
        assert class_name in csr_block, f"CSR renderHotTopics 缺 {class_name}"

    assert 'target="_blank"' in partial and 'target="_blank"' in csr_block
    assert 'rel="noopener noreferrer"' in partial and 'rel="noopener noreferrer"' in csr_block
    # SSR 命中后必须让 CSR 让位，否则首屏内容会被客户端 fetch 覆盖重画。
    assert 'data-loaded="true"' in Path("web/templates/index.html").read_text()


def test_ssr_and_csr_agree_on_rank_variants_order_and_escaping(hot_client: TestClient) -> None:
    """比 class 名集合更进一步：名次分档、顺序、转义、`heat` 边界值。

    上一个测试只看 token 出没出现——它在"分档写错"和"分档写对"两种情况下读数
    相同。这个测试渲染真实 DOM 并逐条比对，删掉或写错某个 rank variant 会红。
    """
    api_items = hot_client.get("/api/v1/hot", params={"limit": 10}).json()["data"]["items"]
    soup = BeautifulSoup(hot_client.get("/").text, "html.parser")
    rows = soup.select("#hot-topics .hot-topics-row")

    assert rows, "首页 SSR 未渲染热点行"
    # 首页只取前 2 条；名次分档按 index 走 1/2/3/rest。
    for rank, (row, item) in enumerate(zip(rows, api_items, strict=False), start=1):
        rank_span = row.select_one(".hot-topics-rank")
        expected = f"hot-topics-rank-{rank}" if rank <= 3 else "hot-topics-rank-rest"
        assert expected in rank_span.get("class"), f"第 {rank} 名的分档 class 不是 {expected}"
        assert rank_span.get_text(strip=True) == str(rank)
        assert rank_span.get("aria-hidden") == "true"
        link = row.select_one(".hot-topics-link")
        assert link.get_text(strip=True) == item["title"]
        assert link.get("href") == item["url"]
        assert row.select_one(".hot-topics-heat").get_text(strip=True) == f"{item['heat']} 热度"


def test_ssr_escapes_titles_rather_than_emitting_raw_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """标题来自抓取的第三方内容，SSR 必须转义——CSR 那侧走的是 esc()。"""
    from airadar.db import migrate as _migrate

    db_path = tmp_path / "escape.db"
    _migrate(db_path)
    monkeypatch.setattr(
        curated_routes.hot_cache.HOT_CANDIDATE_CACHE,
        "peek",
        lambda: [
            {
                "id": "x",
                "title_zh": '<img src=x onerror="alert(1)">',
                "url": 'https://example.invalid/"><script>alert(1)</script>',
                "published_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "weighted_score": 1.0,
                "related_discussions": [],
            }
        ],
    )
    body = TestClient(create_app(db_path)).get("/").text

    assert "<img src=x onerror" not in body
    assert "<script>alert(1)</script>" not in body
    assert "&lt;img src=x" in body


def test_home_hot_contract_and_every_desktop_sidebar_consumer_links_hot() -> None:
    app_js = Path("web/static/app.js").read_text()
    assert 'api("/api/v1/hot?limit=2")' in app_js
    assert 'api("/api/v1/hot?limit=5")' not in app_js
    assert 'href="/hot"' in app_js
    assert "当前热点" in app_js
    assert "今日热点" in app_js

    sidebar_consumers = [
        Path("web/templates/index.html"),
        Path("web/templates/all.html"),
        Path("web/templates/about.html"),
        Path("web/templates/bookmarks.html"),
        Path("web/templates/wechat.html"),
        Path("web/templates/wechat_404.html"),
        Path("web/templates/wechat_detail.html"),
        Path("web/static/index.html"),
        Path("web/static/all.html"),
        Path("web/static/daily.html"),
        Path("web/static/item.html"),
    ]
    for path in sidebar_consumers:
        soup = BeautifulSoup(path.read_text(), "html.parser")
        links = soup.select(".side-nav a[href='/hot']")
        assert len(links) == 1, path
        assert links[0].get_text(strip=True) == "热点榜"
        assert not soup.select("a[href='/topics'], a[href='/agent'], a[href='/feedback']")

    hot_template = BeautifulSoup(Path("web/templates/hot.html").read_text(), "html.parser")
    active = hot_template.select(".side-link-active")
    assert len(active) == 1
    assert active[0].get("href") == "/hot"
