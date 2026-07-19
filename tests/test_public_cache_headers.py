from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from airadar.web.app import create_app

PUBLIC_CACHE_CONTROL = "public, max-age=90, stale-while-revalidate=30"
PRIVATE_CACHE_CONTROL = "private, no-store"


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:  # noqa: ANN001
    with TestClient(create_app(tmp_path / "radar.db")) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    [
        "/wechat",
        "/wechat?page=2",
        "/api/v1/wechat?page=2&limit=50",
        "/",
        "/?page=2",
        "/api/v1/curated?page=2&limit=40",
    ],
)
def test_public_pagination_responses_emit_short_cache_control(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL


@pytest.mark.parametrize(
    "path",
    [
        "/wechat?q=",
        "/api/v1/wechat?q=&page=1&limit=50",
        "/?q=OpenAI",
        "/api/v1/curated?q=OpenAI&page=1&limit=40",
        "/?category=ai-models",
        "/api/v1/curated?date=2026-07-18",
        "/wechat?theme=dark",
        "/wechat?limit=500",
        "/?limit=100",
    ],
)
def test_search_and_other_response_variants_are_not_shared_cached(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.headers["cache-control"] == PRIVATE_CACHE_CONTROL


def test_request_cookie_does_not_vary_public_pagination_response(client: TestClient) -> None:
    without_cookie = client.get("/wechat?page=1")
    with_cookie = client.get("/wechat?page=1", headers={"Cookie": "session=fixture"})

    assert with_cookie.status_code == 200
    assert with_cookie.text == without_cookie.text
    assert with_cookie.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert "set-cookie" not in with_cookie.headers


def test_non_success_and_unrelated_routes_are_not_shared_cached(client: TestClient) -> None:
    invalid_page = client.get("/api/v1/wechat?page=0")
    health = client.get("/api/v1/healthz")

    assert invalid_page.status_code == 422
    assert invalid_page.headers["cache-control"] == PRIVATE_CACHE_CONTROL
    assert "cache-control" not in health.headers
