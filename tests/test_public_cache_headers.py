from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Response
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
        "/all",
        "/all?page=2",
        # The four sidebar destinations. Measured 2026-08-21 against production:
        # all four returned `eo-cache-status: MISS` on every request and sent no
        # Cache-Control at all, so the edge had nothing to follow.
        "/daily",
        "/bookmarks",
        "/more",
        "/about",
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
        # /all carries four filters that /'s parameter set does not. Each has to
        # fall out of the shared cache on its own: the edge keys on the URL, so
        # a filtered view leaking into the public entry would be served to the
        # next visitor asking for the unfiltered one.
        "/all?q=OpenAI",
        "/all?category=ai-models",
        "/all?channel=x",
        "/all?cursor=2026-08-20T00%3A00%3A00Z%7C2026-08-20T00%3A00%3A00Z%7Cabc",
        "/all?limit=100",
        # The four sidebar destinations read no query parameter at all, so their
        # key set is empty: anything appended has to fall out of the shared
        # cache, not be ignored into it.
        "/daily?page=2",
        "/bookmarks?q=OpenAI",
        "/more?theme=dark",
        "/about?anything=1",
    ],
)
def test_search_and_other_response_variants_are_not_shared_cached(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.headers["cache-control"] == PRIVATE_CACHE_CONTROL


@pytest.mark.parametrize(
    "path",
    [
        "/wechat?page=1",
        "/",
        "/all",
        # `/bookmarks` is the one this test exists for now. Its bookmark set
        # lives in localStorage, so the document is visitor-independent -- but
        # that is a property of the handler (an empty template context), not of
        # the URL.
        #
        # Do NOT read this case as a guard against a future server-side sync:
        # the cookie here is invented, and a real auth state would not
        # necessarily be activated by it, so this test would not necessarily go
        # red. What actually guards that direction is
        # `test_handler_declared_private_is_not_overwritten_with_public` below
        # -- a handler that declares itself private keeps it. This case only
        # asserts today's property: the response does not vary by cookie.
        "/bookmarks",
        "/daily",
        "/more",
        "/about",
    ],
)
def test_request_cookie_does_not_vary_public_pagination_response(
    client: TestClient, path: str
) -> None:
    """Precondition for letting the edge hold these: one visitor's response is
    every visitor's response. If any of them ever varied by cookie, a shared
    cache would hand one reader another reader's page."""
    without_cookie = client.get(path)
    with_cookie = client.get(path, headers={"Cookie": "session=fixture"})

    assert with_cookie.status_code == 200
    assert with_cookie.text == without_cookie.text
    assert with_cookie.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert "set-cookie" not in with_cookie.headers


@pytest.mark.parametrize("query", ["&", "&&", "&&&"])
def test_query_string_that_parses_to_no_parameters_is_still_not_shared_cached(
    client: TestClient, query: str
) -> None:
    """`parse_qsl` drops bare separators, so `?&&` arrives with an empty
    parameter set. The edge keys on the URL, so publishing it under a public
    entry would put a URL nobody modelled into a shared cache."""
    response = client.get(f"/daily?{query}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == PRIVATE_CACHE_CONTROL


def test_handler_declared_private_is_not_overwritten_with_public(tmp_path) -> None:  # noqa: ANN001
    """The middleware runs after the handler. Without an explicit precedence
    rule it would overwrite a handler that correctly declared itself private --
    turning a correct handler into a shared-cache leak, silently and in one
    direction only."""
    from airadar.web import app as app_module

    application = app_module.create_app(tmp_path / "radar.db")
    probe_path = "/__private_probe"

    def _probe() -> Response:
        return Response("per-visitor", headers={"Cache-Control": "private, no-store"})

    # Ahead of the StaticFiles mount, which otherwise answers unknown paths.
    application.add_api_route(probe_path, _probe, methods=["GET"], include_in_schema=False)
    application.router.routes.insert(0, application.router.routes.pop())
    app_module._PUBLIC_PAGINATION_QUERY_KEYS[probe_path] = frozenset()
    try:
        with TestClient(application) as probe_client:
            response = probe_client.get(probe_path)
    finally:
        app_module._PUBLIC_PAGINATION_QUERY_KEYS.pop(probe_path, None)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "declared",
    [
        [("cache-control", "private, no-store")],
        # Two separate fields. `headers.get()` returns only the first, so a
        # substring test on it would miss the `no-store` and publish this.
        [("cache-control", "max-age=0"), ("cache-control", "no-store")],
        [("cache-control", "No-Store")],
    ],
    ids=["single-field", "split-across-two-fields", "mixed-case"],
)
def test_private_detection_reads_every_cache_control_field(tmp_path, declared) -> None:  # noqa: ANN001
    from airadar.web import app as app_module

    application = app_module.create_app(tmp_path / "radar.db")
    probe_path = "/__private_probe_multi"

    def _probe() -> Response:
        resp = Response("per-visitor")
        del resp.headers["Cache-Control"]
        for key, value in declared:
            resp.raw_headers.append((key.encode(), value.encode()))
        return resp

    application.add_api_route(probe_path, _probe, methods=["GET"], include_in_schema=False)
    application.router.routes.insert(0, application.router.routes.pop())
    app_module._PUBLIC_PAGINATION_QUERY_KEYS[probe_path] = frozenset()
    try:
        with TestClient(application) as probe_client:
            response = probe_client.get(probe_path)
    finally:
        app_module._PUBLIC_PAGINATION_QUERY_KEYS.pop(probe_path, None)

    assert response.headers["cache-control"] != PUBLIC_CACHE_CONTROL


def test_non_success_and_unrelated_routes_are_not_shared_cached(client: TestClient) -> None:
    invalid_page = client.get("/api/v1/wechat?page=0")
    health = client.get("/api/v1/healthz")

    assert invalid_page.status_code == 422
    assert invalid_page.headers["cache-control"] == PRIVATE_CACHE_CONTROL
    assert "cache-control" not in health.headers
