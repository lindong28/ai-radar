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

    def fake_archive_page(*args: object, **kwargs: object) -> tuple[list[dict[str, Any]], int, int]:
        del args, kwargs
        return hot_items, len(hot_items), 1

    monkeypatch.setattr(curated_routes.curated_archive, "_compute_archive_page", fake_archive_page)
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
    monkeypatch.setattr(
        curated_routes.curated_archive,
        "_compute_archive_page",
        lambda *args, **kwargs: ([], 0, 1),
    )

    response = hot_client.get("/hot")
    soup = BeautifulSoup(response.text, "html.parser")

    assert response.status_code == 200
    assert not soup.select(".hot-rank-row")
    assert soup.select_one(".hot-rank-empty").get_text(strip=True) == "过去 48 小时暂无热点"


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
