from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from airadar import db
from airadar.fetcher import runner
from airadar.fetcher.dedup import upsert_item
from airadar.fetcher.x_api import (
    X_MAX_RESULTS_PER_SOURCE,
    X_RECENT_LOOKBACK,
    XTimelinePage,
    _media_index,
    _post_extra,
    _usable_timeline_payload,
)
from airadar.sources.loader import SourceConfig, load_sources
from airadar.sources.sync import sync_to_db
from airadar.sources.x_state import X_RUNTIME_META_KEYS, validate_x_runtime_meta


def test_x_runtime_schema_migrates_legacy_and_rejects_unknown_version() -> None:
    legacy = {"x_cursor_state": "identity_pending", "x_reference_status": "pending"}
    assert validate_x_runtime_meta(legacy, context="fixture")["x_state_schema_version"] == 1
    with pytest.raises(ValueError, match="schema version"):
        validate_x_runtime_meta({**legacy, "x_state_schema_version": 99}, context="fixture")


def test_blocked_x_reference_requires_structured_attempt_and_recovery() -> None:
    blocked = {
        "x_state_schema_version": 1, "x_cursor_state": "identity_pending", "x_reference_status": "blocked",
        "x_reference_attempted_at": "2026-08-13T00:00:00Z", "x_reference_reason": "authentication_rejected",
        "x_reference_recovery": "replace_or_confirm_token_then_rerun_single_source_probe",
    }
    assert validate_x_runtime_meta(blocked, context="fixture")["x_reference_status"] == "blocked"
    with pytest.raises(ValueError, match="attempt details"):
        validate_x_runtime_meta({key: value for key, value in blocked.items() if key != "x_reference_recovery"}, context="fixture")

    checkpointed = {
        **blocked,
        "x_cursor_state": "checkpointed",
        "x_user_id": "42",
        "x_since_id": "200",
    }
    assert validate_x_runtime_meta(checkpointed, context="fixture")["x_since_id"] == "200"


def _source(*, adapter: str = "x_api", persisted: bool = False) -> SourceConfig:
    meta = {"adapter": adapter, "username": "OpenAI"}
    if adapter == "x_api" and persisted:
        meta["x_cursor_state"] = "uninitialized"
        meta["x_reference_status"] = "pending"
        meta["x_user_id"] = "42"
        meta["x_initial_start_time"] = "2026-08-12T13:40:00Z"
    return SourceConfig(
        slug="x_openai",
        name="X: OpenAI (@OpenAI)",
        url="https://api.x.com/2/users/by/username/OpenAI",
        tier="T1",
        kind="x",
        homepage_url="https://x.com/OpenAI",
        meta=meta,
    )


def _with_runtime(source: SourceConfig, **runtime: str) -> SourceConfig:
    meta = {key: value for key, value in source.meta.items() if key not in X_RUNTIME_META_KEYS}
    state = runtime.get("x_cursor_state")
    if state in {"checkpointed", "draining"}:
        meta.update(
            {
                "x_reference_status": "verified",
                "x_reference_validated_at": "2026-08-12T14:00:00Z",
                "x_user_id": "42",
            }
        )
    elif state == "uninitialized":
        meta.update(
            {
                "x_reference_status": "pending",
                "x_user_id": "42",
                "x_initial_start_time": "2026-08-12T13:40:00Z",
            }
        )
    elif state == "identity_pending":
        meta["x_reference_status"] = "pending"
    meta.update(runtime)
    return SourceConfig(**{**source.__dict__, "meta": meta})


class _Response:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://api.x.com/test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)

    def json(self) -> dict[str, object]:
        return self._payload


class _Client:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_fetch_x_timeline_requires_bearer_token_before_http(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    client = _Client([])
    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "")

    with pytest.raises(RuntimeError, match="X_BEARER_TOKEN is not configured"):
        fetch_x_timeline(_source(persisted=True), client=client)

    assert client.calls == []


def test_fetch_x_timeline_default_transport_uses_selector_factory(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import X_API_BASE_URL, fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    client = _Client([_Response({"data": [], "meta": {"result_count": 0}})])
    calls: list[dict[str, object]] = []

    class ContextClient:
        def __enter__(self) -> _Client:
            return client

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_factory(**kwargs: object) -> ContextClient:
        calls.append(kwargs)
        return ContextClient()

    monkeypatch.setattr("airadar.fetcher.x_api.selector_httpx_client", fake_factory)

    fetch_x_timeline(_source(persisted=True))

    assert calls == [{
        "callsite_id": "fetcher.x_api.fetch_x_timeline",
        "request_url": X_API_BASE_URL,
        "timeout": 30.0,
    }]


def test_fetch_x_timeline_resolves_identity_in_one_request_before_timeline(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source()
    source = _with_runtime(source, x_cursor_state="identity_pending")
    client = _Client([_Response({"data": {"id": "42", "username": "OpenAI"}})])

    result = fetch_x_timeline(
        source,
        client=client,
        now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
    )

    assert len(client.calls) == 1
    assert client.calls[0][0] == "https://api.x.com/2/users/by/username/OpenAI"
    assert client.calls[0][1]["params"] == {}
    assert result.items == []
    assert result.meta["x_cursor_state"] == "uninitialized"
    assert result.meta["x_reference_status"] == "pending"
    assert result.meta["x_user_id"] == "42"
    assert result.meta["x_initial_start_time"] == "2026-08-12T13:40:00Z"


def test_fetch_x_timeline_uses_one_bounded_cold_start_page_and_persists_cursor(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    client = _Client(
        [
            _Response(
                {
                    "data": [
                        {
                            "id": "1234567890123456789",
                            "author_id": "42",
                            "created_at": "2026-08-12T13:59:00.000Z",
                            "lang": "en",
                            "text": "Short fallback text",
                            "note_tweet": {"text": "Full long-form post text"},
                            "public_metrics": {"like_count": 7},
                            "referenced_tweets": [{"type": "quoted", "id": "9"}],
                        }
                    ],
                    "meta": {"result_count": 1, "next_token": "must-not-be-followed"},
                }
            )
        ]
    )

    result = fetch_x_timeline(
        _source(persisted=True),
        client=client,
        now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
    )

    assert len(client.calls) == 1
    url, kwargs = client.calls[0]
    assert url == "https://api.x.com/2/users/42/tweets"
    assert kwargs["headers"] == {"Authorization": "Bearer secret"}
    assert kwargs["params"] == {
        "start_time": "2026-08-12T13:40:00Z",
        "max_results": 5,
        "exclude": "retweets,replies",
        "tweet.fields": "attachments,author_id,created_at,lang,note_tweet,public_metrics,referenced_tweets",
        "expansions": "attachments.media_keys",
        "media.fields": "media_key,type,url,preview_image_url,width,height,alt_text",
    }
    assert result.items[0].source_id == "x_openai"
    assert result.items[0].url == "https://x.com/i/web/status/1234567890123456789"
    assert result.items[0].title == "Full long-form post text"
    assert result.items[0].content_text == "Full long-form post text"
    assert result.items[0].author == "@OpenAI"
    assert result.items[0].published_at == "2026-08-12T13:59:00.000Z"
    assert result.items[0].fetched_at == "2026-08-12T14:00:00Z"
    assert result.items[0].extra == {
        "x_post_id": "1234567890123456789",
        "x_author_id": "42",
        "x_username": "OpenAI",
        "x_media": [],
        "lang": "en",
        "public_metrics": {"like_count": 7},
        "referenced_tweets": [{"type": "quoted", "id": "9"}],
    }
    assert result.meta["x_pagination_token"] == "must-not-be-followed"
    assert result.meta["x_pending_since_id"] == "1234567890123456789"
    assert result.meta["x_pending_start_time"] == "2026-08-12T13:40:00Z"
    assert "x_since_id" not in result.meta
    assert "x_initial_start_time" not in result.meta


def test_fetch_x_timeline_drains_one_saved_page_per_round_then_advances_since_id(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source(persisted=True)
    source = _with_runtime(
        source,
        x_cursor_state="draining",
        x_pagination_token="page-2",
        x_pending_since_id="200",
        x_pending_start_time="2026-08-12T13:40:00Z",
    )
    client = _Client(
        [
            _Response(
                {
                    "data": [
                        {
                            "id": "150",
                            "author_id": "42",
                            "created_at": "2026-08-12T13:50:00.000Z",
                            "text": "Older page item",
                        }
                    ],
                    "meta": {"result_count": 1},
                }
            )
        ]
    )

    result = fetch_x_timeline(source, client=client)

    assert len(client.calls) == 1
    assert client.calls[0][1]["params"] == {
        "start_time": "2026-08-12T13:40:00Z",
        "pagination_token": "page-2",
        "max_results": 5,
        "exclude": "retweets,replies",
        "tweet.fields": "attachments,author_id,created_at,lang,note_tweet,public_metrics,referenced_tweets",
        "expansions": "attachments.media_keys",
        "media.fields": "media_key,type,url,preview_image_url,width,height,alt_text",
    }
    assert result.meta["x_since_id"] == "200"
    assert "x_pagination_token" not in result.meta
    assert "x_pending_since_id" not in result.meta
    assert "x_pending_start_time" not in result.meta


def test_fetch_x_timeline_uses_since_id_after_backlog_is_drained(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source(persisted=True)
    source = _with_runtime(source, x_cursor_state="checkpointed", x_since_id="200")
    client = _Client([_Response({"meta": {"result_count": 0}})])

    result = fetch_x_timeline(source, client=client)

    assert client.calls[0][1]["params"]["since_id"] == "200"
    assert "start_time" not in client.calls[0][1]["params"]
    assert result.items == []
    assert result.meta["x_since_id"] == "200"
    assert "x_since_time" not in result.meta


def test_fetch_x_timeline_success_clears_checkpoint_failure_state(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _with_runtime(_source(persisted=True), x_cursor_state="checkpointed", x_since_id="200")
    source = SourceConfig(
        **{
            **source.__dict__,
            "meta": {
                **source.meta,
                "x_reference_status": "blocked",
                "x_reference_attempted_at": "2026-08-12T14:05:00Z",
                "x_reference_reason": "authentication_rejected",
                "x_reference_recovery": "replace_or_confirm_token_then_rerun_fetch",
            },
        }
    )
    source.meta.pop("x_reference_validated_at")

    result = fetch_x_timeline(
        source,
        client=_Client([_Response({"meta": {"result_count": 0}})]),
        now=datetime(2026, 8, 12, 14, 10, tzinfo=UTC),
    )

    assert result.meta["x_reference_status"] == "verified"
    assert result.meta["x_reference_validated_at"] == "2026-08-12T14:10:00Z"
    assert "x_reference_attempted_at" not in result.meta
    assert "x_reference_reason" not in result.meta
    assert "x_reference_recovery" not in result.meta


def test_fetch_x_identity_success_clears_identity_failure_state(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = SourceConfig(
        **{
            **_source().__dict__,
            "meta": {
                "adapter": "x_api",
                "username": "OpenAI",
                "x_cursor_state": "identity_pending",
                "x_reference_status": "blocked",
                "x_reference_attempted_at": "2026-08-12T14:05:00Z",
                "x_reference_reason": "authentication_rejected",
                "x_reference_recovery": "replace_or_confirm_token_then_rerun_fetch",
            },
        }
    )

    result = fetch_x_timeline(
        source,
        client=_Client([_Response({"data": {"id": "42", "username": "OpenAI"}})]),
        now=datetime(2026, 8, 12, 14, 10, tzinfo=UTC),
    )

    assert result.meta["x_cursor_state"] == "uninitialized"
    assert result.meta["x_reference_status"] == "pending"
    assert "x_reference_attempted_at" not in result.meta
    assert "x_reference_reason" not in result.meta
    assert "x_reference_recovery" not in result.meta


def test_fetch_x_timeline_empty_cold_start_commits_window_boundary(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    client = _Client([_Response({"meta": {"result_count": 0}})])

    result = fetch_x_timeline(
        _source(persisted=True),
        client=client,
        now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
    )

    assert result.items == []
    assert result.meta["x_since_time"] == "2026-08-12T14:00:00Z"


def test_runner_persists_legal_x_state_without_exposing_it(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from airadar.fetcher.x_api import fetch_x_timeline
    from airadar.web.app import create_app

    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    sync_to_db([_source()], conn)
    pending = runner.load_enabled_sources_from_db(conn)[0]
    resolved = _with_runtime(
        pending,
        x_cursor_state="uninitialized",
        x_user_id="42",
        x_initial_start_time="2026-08-12T13:40:00Z",
    )
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=?",
        (json.dumps(resolved.meta, sort_keys=True), pending.slug),
    )
    conn.commit()
    source = runner.load_enabled_sources_from_db(conn)[0]
    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    client = _Client([_Response({"meta": {"result_count": 0}})])
    monkeypatch.setattr(
        runner,
        "fetch_x_timeline",
        lambda fetched_source: fetch_x_timeline(
            fetched_source,
            client=client,
            now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
        ),
    )

    summary = runner.fetch_source(conn, source)
    stored = json.loads(conn.execute("SELECT meta_json FROM sources WHERE id=?", (source.slug,)).fetchone()[0])
    conn.close()
    public = TestClient(create_app(db_path)).get("/api/v2/sources").json()["data"]["sources"][0]

    assert summary.error is None
    assert stored["x_cursor_state"] == "checkpointed"
    assert stored["x_since_time"] == "2026-08-12T14:00:00Z"
    assert public["retrieval_validation"] == {
        "status": "verified",
        "label": "已验证",
        "scope": "x_timeline_retrieval",
        "trigger": "next_successful_x_timeline_fetch",
        "validated_at": "2026-08-12T14:00:00Z",
        "attempted_at": None,
        "reason": None,
        "recovery": None,
    }


def test_fetch_x_timeline_uses_committed_time_after_empty_cold_start(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source(persisted=True)
    source = _with_runtime(
        source,
        x_cursor_state="checkpointed",
        x_since_time="2026-08-12T14:00:00Z",
    )
    client = _Client([_Response({"meta": {"result_count": 0}})])

    result = fetch_x_timeline(source, client=client)

    assert client.calls[0][1]["params"]["start_time"] == "2026-08-12T14:00:00Z"
    assert result.meta["x_since_time"] != "2026-08-12T14:00:00Z"


def test_time_checkpoint_pagination_keeps_one_start_boundary(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source(persisted=True)
    source = _with_runtime(
        source,
        x_cursor_state="checkpointed",
        x_since_time="2026-08-12T14:00:00Z",
    )
    client = _Client(
        [
            _Response(
                {
                    "data": [
                        {
                            "id": "250",
                            "author_id": "42",
                            "created_at": "2026-08-12T14:10:00Z",
                            "text": "new",
                        }
                    ],
                    "meta": {"result_count": 1, "next_token": "page-2"},
                }
            )
        ]
    )

    result = fetch_x_timeline(source, client=client)

    assert result.meta["x_since_time"] == "2026-08-12T14:00:00Z"
    assert "x_pending_start_time" not in result.meta
    assert result.meta["x_cursor_state"] == "draining"


def test_fetch_x_timeline_rejects_non_advancing_pagination_token(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source(persisted=True)
    source = _with_runtime(
        source,
        x_cursor_state="draining",
        x_pagination_token="page-2",
        x_pending_since_id="250",
        x_pending_start_time="2026-08-12T13:40:00Z",
    )
    client = _Client(
        [
            _Response(
                {
                    "data": [
                        {
                            "id": "200",
                            "author_id": "42",
                            "created_at": "2026-08-12T13:50:00Z",
                            "text": "old",
                        }
                    ],
                    "meta": {"result_count": 1, "next_token": "page-2"},
                }
            )
        ]
    )

    with pytest.raises(ValueError, match="pagination token did not advance"):
        fetch_x_timeline(source, client=client)


def test_fetch_x_timeline_rejects_time_checkpoint_rollback(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source(persisted=True)
    source = _with_runtime(
        source,
        x_cursor_state="checkpointed",
        x_since_time="2026-08-12T15:00:00Z",
    )

    with pytest.raises(ValueError, match="time checkpoint moved backwards"):
        fetch_x_timeline(
            source,
            client=_Client([_Response({"meta": {"result_count": 0}})]),
            now=datetime(2026, 8, 12, 14, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize("status_code", [400, 404, 410])
def test_ambiguous_pagination_4xx_preserves_cursor(monkeypatch, status_code: int) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _source(persisted=True)
    source = _with_runtime(
        source,
        x_cursor_state="draining",
        x_pagination_token="expired-page",
        x_pending_since_id="250",
        x_pending_start_time="2026-08-12T13:40:00Z",
    )
    client = _Client([_Response({}, status_code=status_code)])

    with pytest.raises(httpx.HTTPStatusError):
        fetch_x_timeline(source, client=client)

    assert len(client.calls) == 1


def test_runner_preserves_cursor_and_reports_ambiguous_pagination_4xx(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    sync_to_db([_source()], conn)
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id='x_openai'",
        (
            '{"adapter":"x_api","username":"OpenAI","x_reference_status":"verified",'
            '"x_reference_validated_at":"2026-08-12T14:00:00Z","x_cursor_state":"draining",'
            '"x_user_id":"42","x_since_id":"200","x_pending_since_id":"250",'
            '"x_pagination_token":"expired-page"}',
        ),
    )
    conn.commit()
    source = runner.load_enabled_sources_from_db(conn)[0]
    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    client = _Client([_Response({}, status_code=400)])
    monkeypatch.setattr(
        runner,
        "fetch_x_timeline",
        lambda fetched_source: fetch_x_timeline(fetched_source, client=client),
    )

    summary = runner.fetch_source(conn, source)
    stored = json.loads(conn.execute("SELECT meta_json FROM sources WHERE id='x_openai'").fetchone()[0])

    assert summary.error is not None
    assert summary.error.startswith("HTTPStatusError:")
    assert stored["x_cursor_state"] == "draining"
    assert stored["x_since_id"] == "200"
    assert stored["x_pagination_token"] == "expired-page"
    assert stored["x_pending_since_id"] == "250"


def test_runner_persists_x_fetch_failure_without_discarding_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from airadar.web.app import create_app

    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    sync_to_db([_source()], conn)
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id='x_openai'",
        (
            '{"adapter":"x_api","username":"OpenAI","x_reference_status":"verified",'
            '"x_reference_validated_at":"2026-08-12T14:00:00Z","x_cursor_state":"checkpointed",'
            '"x_user_id":"42","x_since_id":"200"}',
        ),
    )
    conn.commit()
    source = runner.load_enabled_sources_from_db(conn)[0]

    def rejected(_source: SourceConfig) -> XTimelinePage:
        request = httpx.Request("GET", "https://api.x.com/test")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("HTTP 401", request=request, response=response)

    monkeypatch.setattr(runner, "fetch_x_timeline", rejected)

    summary = runner.fetch_source(conn, source)
    stored = json.loads(conn.execute("SELECT meta_json FROM sources WHERE id='x_openai'").fetchone()[0])
    conn.close()
    validation = TestClient(create_app(db_path)).get("/api/v2/sources").json()["data"]["sources"][0][
        "retrieval_validation"
    ]

    assert summary.http_status_code == 401
    assert stored["x_reference_status"] == "blocked"
    assert stored["x_reference_reason"] == "authentication_rejected"
    assert stored["x_reference_recovery"] == "replace_or_confirm_token_then_rerun_fetch"
    assert stored["x_cursor_state"] == "checkpointed"
    assert stored["x_since_id"] == "200"
    assert "x_reference_validated_at" not in stored
    assert validation["status"] == "blocked"
    assert validation["reason"] == "authentication_rejected"


@pytest.mark.parametrize("payload", [{}, {"errors": [{"detail": "bad token"}]}])
def test_fetch_x_timeline_rejects_ambiguous_200_payloads(monkeypatch, payload) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")

    with pytest.raises(ValueError, match="invalid X timeline response"):
        fetch_x_timeline(_source(persisted=True), client=_Client([_Response(payload)]))


@pytest.mark.parametrize(
    "payload",
    [
        {
            "data": [{"id": "123", "created_at": "2026-08-12T14:00:00Z", "text": "ok"}],
            "meta": {"result_count": 0},
        },
        {
            "data": [{"id": "not-a-snowflake", "created_at": "2026-08-12T14:00:00Z", "text": "ok"}],
            "meta": {"result_count": 1},
        },
        {"meta": {"result_count": 0, "next_token": ["not", "a", "string"]}},
    ],
)
def test_fetch_x_timeline_rejects_inconsistent_200_payloads(monkeypatch, payload) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")

    with pytest.raises(ValueError, match="invalid X timeline response"):
        fetch_x_timeline(_source(persisted=True), client=_Client([_Response(payload)]))


def test_fetch_x_timeline_rejects_post_from_another_user(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    payload = {
        "data": [
            {
                "id": "123",
                "author_id": "not-42",
                "created_at": "2026-08-12T13:59:00Z",
                "text": "wrong author",
            }
        ],
        "meta": {"result_count": 1},
    }

    with pytest.raises(ValueError, match="author_id does not match resolved user"):
        fetch_x_timeline(_source(persisted=True), client=_Client([_Response(payload)]))


@pytest.mark.parametrize(
    ("meta", "post"),
    [
        (
            {"x_cursor_state": "checkpointed", "x_since_id": "200"},
            {"id": "150", "created_at": "2026-08-12T14:10:00Z", "text": "old"},
        ),
        (
            {"x_cursor_state": "checkpointed", "x_since_time": "2026-08-12T14:00:00Z"},
            {"id": "250", "created_at": "2026-08-12T13:59:00Z", "text": "old"},
        ),
    ],
)
def test_fetch_x_timeline_rejects_posts_outside_requested_checkpoint(
    monkeypatch,
    meta: dict[str, str],
    post: dict[str, str],
) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    source = _with_runtime(_source(), **meta)
    post["author_id"] = "42"
    client = _Client([_Response({"data": [post], "meta": {"result_count": 1}})])

    with pytest.raises(ValueError, match="invalid X timeline response"):
        fetch_x_timeline(source, client=client)


def test_fetch_x_timeline_rejects_invalid_username_before_http(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    client = _Client([])
    source = _source(persisted=True)
    source = SourceConfig(
        **{
            **source.__dict__,
            "meta": {"adapter": "x_api", "username": "bad/name", "x_cursor_state": "uninitialized"},
        }
    )

    with pytest.raises(ValueError, match="invalid X username"):
        fetch_x_timeline(source, client=client)

    assert client.calls == []


def test_fetch_x_timeline_rejects_invalid_persisted_state_before_http(monkeypatch) -> None:  # noqa: ANN001
    from airadar.fetcher.x_api import fetch_x_timeline

    monkeypatch.setattr("airadar.fetcher.x_api.read_value", lambda key: "secret")
    client = _Client([])
    source = _source(persisted=True)
    source = SourceConfig(
        **{
            **source.__dict__,
            "meta": {
                **source.meta,
                "x_since_id": "200",
                "x_since_time": "2026-08-12T14:00:00Z",
            },
        }
    )

    with pytest.raises(ValueError, match="committed checkpoints are mutually exclusive"):
        fetch_x_timeline(source, client=client)

    assert client.calls == []


def test_x_posts_with_same_text_but_different_ids_remain_distinct(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    sync_to_db([_source()], conn)
    first = runner.FetchedItem(
        source_id="x_openai",
        url="https://x.com/openai/status/100",
        title="Same announcement",
        author="@OpenAI",
        published_at="2026-08-12T14:00:00Z",
        fetched_at="2026-08-12T14:01:00Z",
        content_text="Same announcement",
        extra={"x_post_id": "100"},
    )
    second = runner.FetchedItem(
        source_id="x_openai",
        url="https://x.com/openai/status/101",
        title="Same announcement",
        author="@OpenAI",
        published_at="2026-08-12T14:02:00Z",
        fetched_at="2026-08-12T14:03:00Z",
        content_text="Same announcement",
        extra={"x_post_id": "101"},
    )

    assert XTimelinePage(items=[first, second], meta={}).items == [first, second]
    assert upsert_item(conn, first) is True
    assert upsert_item(conn, second) is True
    assert conn.execute("SELECT COUNT(*) FROM items WHERE source_id='x_openai'").fetchone()[0] == 2


def test_runner_dispatches_only_explicit_x_api_sources(monkeypatch) -> None:  # noqa: ANN001
    api_source = _source()
    rss_source = _source(adapter="rss")
    expected = object()

    monkeypatch.setattr(
        runner,
        "fetch_x_timeline",
        lambda source: XTimelinePage(items=[expected], meta={**source.meta, "x_since_id": "1"}),
    )
    monkeypatch.setattr(
        runner,
        "fetch_feed",
        lambda source, conn: pytest.fail("explicit X API source used the RSS path"),
    )

    api_result = runner._fetch_source_feed(api_source)

    assert api_result.error is None
    assert api_result.items == [expected]
    assert api_result.meta_update is not None

    called: list[str] = []

    def fake_feed(source: SourceConfig, conn: object) -> runner.FeedResponse:
        called.append(source.slug)
        return runner.FeedResponse(status_code=304, body=b"", not_modified=True)

    monkeypatch.setattr(runner, "fetch_feed", fake_feed)
    rss_result = runner._fetch_source_feed(rss_source)

    assert rss_result.error is None
    assert called == [rss_source.slug]


def test_runner_rejects_stale_x_cursor_update(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    source = _source()
    sync_to_db([source], conn)
    resolved = _with_runtime(source, x_cursor_state="uninitialized")
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=?",
        (json.dumps(resolved.meta, sort_keys=True, separators=(",", ":")), source.slug),
    )
    conn.commit()
    source = runner.load_enabled_sources_from_db(conn)[0]
    monkeypatch.setattr(
        runner,
        "fetch_x_timeline",
        lambda source: XTimelinePage(
            items=[],
            meta=_with_runtime(source, x_cursor_state="checkpointed", x_since_id="200").meta,
        ),
    )

    result = runner._fetch_source_feed(source)
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=?",
        (
            '{"adapter":"x_api","username":"OpenAI","x_reference_status":"verified",'
            '"x_reference_validated_at":"2026-08-12T14:00:00Z","x_cursor_state":"checkpointed",'
            '"x_user_id":"42","x_since_id":"150"}',
            source.slug,
        ),
    )
    conn.commit()

    summary = runner._apply_source_feed_result(conn, result)

    assert summary.error == "ValueError: X source state changed while fetching: x_openai"
    stored = json.loads(conn.execute("SELECT meta_json FROM sources WHERE id=?", (source.slug,)).fetchone()[0])
    assert stored["x_since_id"] == "150"


def test_runner_preserves_current_static_meta_when_applying_x_cursor(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    source = _source()
    sync_to_db([source], conn)
    resolved = _with_runtime(source, x_cursor_state="uninitialized")
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=?",
        (json.dumps(resolved.meta, sort_keys=True, separators=(",", ":")), source.slug),
    )
    conn.commit()
    source = runner.load_enabled_sources_from_db(conn)[0]
    monkeypatch.setattr(
        runner,
        "fetch_x_timeline",
        lambda source: XTimelinePage(
            items=[],
            meta=_with_runtime(source, x_cursor_state="checkpointed", x_since_id="200").meta,
        ),
    )

    result = runner._fetch_source_feed(source)
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=?",
        (
            '{"adapter":"x_api","username":"OpenAI","label":"current",'
            '"x_reference_status":"pending",'
            '"x_cursor_state":"uninitialized","x_user_id":"42",'
            '"x_initial_start_time":"2026-08-12T13:40:00Z"}',
            source.slug,
        ),
    )
    conn.commit()

    summary = runner._apply_source_feed_result(conn, result)

    assert summary.error is None
    stored = json.loads(conn.execute("SELECT meta_json FROM sources WHERE id=?", (source.slug,)).fetchone()[0])
    assert stored == {
        "adapter": "x_api",
        "username": "OpenAI",
        "label": "current",
        "x_reference_status": "verified",
        "x_reference_validated_at": "2026-08-12T14:00:00Z",
        "x_cursor_state": "checkpointed",
        "x_user_id": "42",
            "x_since_id": "200",
            "x_state_schema_version": 1,
        }


def test_source_pool_contains_exact_aihot_active_x_allowlist(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("MP2RSS_FEED_URL", raising=False)
    sources_path = Path(__file__).resolve().parents[1] / "data" / "sources.toml"
    x_api_sources = [
        source
        for source in load_sources(sources_path)
        if source.kind == "x" and source.meta.get("adapter") == "x_api"
    ]

    assert {str(source.meta.get("username")) for source in x_api_sources} == {
        "AISafetyMemes",
        "AIatMeta",
        "_akhaliq",
        "aidangomez",
        "alexandr_wang",
        "ammaar",
        "milichab",
        "AndrewYNg",
        "AnthropicAI",
        "AravSrinivas",
        "ArtificialAnlys",
        "barret_china",
        "bcherny",
        "charlieholtz",
        "claudeai",
        "ClaudeDevs",
        "ClementDelangue",
        "cohere",
        "deedydas",
        "demishassabis",
        "dexhorthy",
        "elonmusk",
        "omarsar0",
        "EMostaque",
        "EpochAIResearch",
        "ericmitchellai",
        "ericzakariasson",
        "emollick",
        "drfeifei",
        "fchollet",
        "lifesinger",
        "gabriel1",
        "GeminiApp",
        "GoogleAI",
        "googleaidevs",
        "GoogleDeepMind",
        "gdb",
        "jxnlco",
        "JeffDean",
        "JensenHuang",
        "joshwoodward",
        "karinanguyen",
        "kimmonismus",
        "Kimi_Moonshot",
        "krea_ai",
        "leerob",
        "OfficialLoganK",
        "LumaLabsAI",
        "finkd",
        "mntruell",
        "MSFTResearch",
        "MiniMax_AI",
        "MistralAI",
        "mustafasuleyman",
        "natolambert",
        "noahzweben",
        "polynoamial",
        "odysseyml",
        "OpenAI",
        "OpenAIDevs",
        "OpenRouter",
        "perplexity_ai",
        "steipete",
        "PixVerse_",
        "Replit",
        "rohanpaul_ai",
        "runwayml",
        "sama",
        "SemiAnalysis_",
        "sundarpichai",
        "suno",
        "testingcatalog",
        "trq212",
        "Thom_Wolf",
        "tianyi",
        "thsottiaux",
        "ViggleAI",
        "thexpin",
        "Yuchenj_UW",
        "ZHO_ZHO_ZHO",
        "cb_doge",
        "fofrAI",
        "karminski3",
        "opencode",
        "swyx",
        "HuaweiCloud1",
        "Kling_ai",
        "jietang",
        "frxiaobei",
        "Zai_org",
        "lijigang",
        "hongming731",
        "Baidu_Inc",
        "TencentHunyuan",
        "AntLingAGI",
        "chunxiangai",
        "Alibaba_Qwen",
        "foxshuo",
        "AYi_AInotes",
        "alibaba_cloud",
        "OpenBMB",
            "dongxi_nlp",
            "openclaw",
            "SpaceXAI",
            "WorkBuddy_AI",
            "PeterMcCrory",
            "deepseek_ai",
            "zhang_benita",
            "SiliconFlowAI",
    }
    assert len(x_api_sources) == 109
    assert len({source.slug for source in x_api_sources}) == 109
    assert all(
        source.url == f"https://api.x.com/2/users/by/username/{source.meta['username']}"
        for source in x_api_sources
    )
    assert all(source.homepage_url == f"https://x.com/{source.meta['username']}" for source in x_api_sources)


def test_current_x_limits_are_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    architecture = (root / "docs" / "architecture.md").read_text(encoding="utf-8")

    assert X_RECENT_LOOKBACK.total_seconds() == 20 * 60
    assert X_MAX_RESULTS_PER_SOURCE == 5
    assert readme.count("首次只看最近 20 分钟") == 1
    assert readme.count("max_results=5") == 1
    assert "20 分钟" not in architecture
    assert "max_results=5" not in architecture


def test_aihot_x_sources_reach_public_inventory_without_internal_state(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from airadar.web.app import create_app

    monkeypatch.delenv("MP2RSS_FEED_URL", raising=False)
    sources_path = Path(__file__).resolve().parents[1] / "data" / "sources.toml"
    sources = load_sources(sources_path)
    db_path = tmp_path / "radar.db"
    db.migrate(db_path)
    conn = db.get_conn(db_path)
    sync_to_db(sources, conn)
    conn.close()

    payload = TestClient(create_app(db_path)).get("/api/v2/sources").json()["data"]["sources"]
    x_sources = [source for source in payload if source["kind"] == "x"]

    assert len(x_sources) == 109
    assert all(
        source["retrieval_validation"]
        == {
            "status": "pending",
            "label": "待首次验证",
            "scope": "x_timeline_retrieval",
            "trigger": "next_successful_x_identity_lookup",
            "validated_at": None,
            "attempted_at": None,
            "reason": None,
            "recovery": None,
        }
        for source in x_sources
    )
    assert all(source["retrieval_entrypoint_url"].startswith("https://api.x.com/2/users/by/username/") for source in x_sources)
    assert all(not source["retrieval_entrypoint_url"].endswith("/tweets") for source in x_sources)
    assert all(source["public_landing_url"].startswith("https://x.com/") for source in x_sources)


def _media_source() -> SourceConfig:
    return SourceConfig(
        slug="x_mediafixture",
        name="X：Media Fixture",
        url="https://api.x.com/2/users/by/username/mediafixture",
        tier="T1.5",
        enabled=True,
        kind="x",
        homepage_url="https://x.com/mediafixture",
        icon_url="",
        meta={"adapter": "x_api", "username": "mediafixture", "x_user_id": "42"},
    )


def _media_payload() -> dict:
    """One page carrying every media shape X can return, plus a post with none."""
    return {
        "data": [
            {  # photo + video, order deliberately video-first to pin ordering
                "id": "900000000000000001",
                "text": "chart plus clip",
                "author_id": "42",
                "created_at": "2026-08-18T01:00:00.000Z",
                "attachments": {"media_keys": ["3_photo", "13_vid"]},
            },
            {  # animated_gif only
                "id": "900000000000000002",
                "text": "gif",
                "author_id": "42",
                "created_at": "2026-08-18T01:01:00.000Z",
                "attachments": {"media_keys": ["16_gif"]},
            },
            {  # no attachments at all
                "id": "900000000000000003",
                "text": "text only",
                "author_id": "42",
                "created_at": "2026-08-18T01:02:00.000Z",
            },
        ],
        "includes": {
            "media": [
                {"media_key": "3_photo", "type": "photo", "width": 1125, "height": 750,
                 "url": "https://pbs.twimg.com/media/photo.jpg"},
                {"media_key": "13_vid", "type": "video", "width": 1920, "height": 1080,
                 "preview_image_url": "https://pbs.twimg.com/media/vidthumb.jpg"},
                {"media_key": "16_gif", "type": "animated_gif",
                 "preview_image_url": "https://pbs.twimg.com/media/gifthumb.jpg"},
            ]
        },
        "meta": {"result_count": 3},
    }


def test_x_request_asks_for_media_expansions() -> None:
    """Without the expansion the API never returns media at all."""
    from airadar.fetcher.x_api import _request_cursor

    params, _ = _request_cursor(_media_source(), datetime(2026, 8, 18, tzinfo=UTC))
    assert params["expansions"] == "attachments.media_keys"
    assert "media_key" in str(params["media.fields"])
    assert "url" in str(params["media.fields"])
    assert "preview_image_url" in str(params["media.fields"])
    assert "attachments" in str(params["tweet.fields"])


def test_x_media_is_typed_ordered_and_optional() -> None:
    from airadar.fetcher.x_api import _media_index, _post_extra

    payload = _media_payload()
    index = _media_index(payload)
    posts = payload["data"]

    mixed = _post_extra(posts[0], "mediafixture", index)["x_media"]
    # order follows attachments.media_keys, not includes.media
    # sorted() would give ["13_vid", "3_photo"] — asserting the reverse pins real ordering
    assert [m["media_key"] for m in mixed] == ["3_photo", "13_vid"]
    # video has no still `url`; only preview_image_url is usable
    assert mixed[0]["type"] == "photo"
    assert mixed[0]["url"] == "https://pbs.twimg.com/media/photo.jpg"
    assert mixed[0]["width"] == 1125
    # video has no still `url`; only preview_image_url is usable
    assert mixed[1]["type"] == "video"
    assert mixed[1]["url"] == "https://pbs.twimg.com/media/vidthumb.jpg"

    gif = _post_extra(posts[1], "mediafixture", index)["x_media"]
    assert gif[0]["url"] == "https://pbs.twimg.com/media/gifthumb.jpg"

    # a post without attachments carries no media key at all
    assert _post_extra(posts[2], "mediafixture", index)["x_media"] == []  # present-and-empty marks "looked, none there" (F4)


def test_x_media_index_is_per_page_and_survives_missing_includes() -> None:
    """A page's includes must not leak into another page's posts."""
    from airadar.fetcher.x_api import _media_index, _post_extra

    page_one = _media_payload()
    page_two = {"data": [dict(page_one["data"][0])], "meta": {"result_count": 1}}  # same post, no includes

    assert _media_index(page_two) == {}
    # The post declares media_keys but page two's index cannot resolve them.
    # A key missing from includes is X's signal that the media is gone
    # (deleted/protected) — terminal, not retryable. So this resolves to a
    # final empty list, marking the post processed rather than re-querying it
    # forever. The real index still resolves the same post to actual media, so
    # this pair discriminates "resolved to nothing" from "resolved to media".
    assert _post_extra(page_two["data"][0], "mediafixture", _media_index(page_two))["x_media"] == []
    assert _post_extra(page_one["data"][0], "mediafixture", _media_index(page_one))["x_media"]


def test_x_media_rejects_non_https_and_unknown_keys() -> None:
    from airadar.fetcher.x_api import _post_extra

    # Media resolution is terminal: whatever does not resolve to an https still
    # is dropped and the post is marked done, never retried. A non-https photo
    # and a video with no preview both drop; a key the index does not carry is
    # X's signal that the media is gone (deleted/protected), which also drops.
    index = {
        "a": {"media_key": "a", "type": "photo", "url": "http://pbs.twimg.com/insecure.jpg"},
        "b": {"media_key": "b", "type": "video"},  # no preview_image_url
        "ok": {"media_key": "ok", "type": "photo", "url": "https://pbs.twimg.com/media/ok.jpg"},
    }
    # all keys unusable → marked done with an empty list, not left to retry
    all_bad = {"id": "1", "author_id": "42", "attachments": {"media_keys": ["a", "b", "gone"]}}
    assert _post_extra(all_bad, "mediafixture", index)["x_media"] == []
    # the crucial one: a post with one good and one gone image still shows the
    # good image and is done — a permanent miss must not withhold the rest.
    partial = {"id": "2", "author_id": "42", "attachments": {"media_keys": ["ok", "gone"]}}
    urls = [m["url"] for m in _post_extra(partial, "mediafixture", index)["x_media"]]
    assert urls == ["https://pbs.twimg.com/media/ok.jpg"]


def test_partial_success_with_errors_still_ingests_the_valid_posts() -> None:
    """X documents 200 + `data` + `errors` as partial success, not failure.

    Rejecting the whole page became a live hazard with the media expansion: one
    deleted image among the newest five posts would fail that source every
    round forever, because the checkpoint never advances past it.
    """
    payload = {
        "data": [{"id": "1", "text": "still valid", "created_at": "2026-08-18T00:00:00.000Z"}],
        "meta": {"result_count": 1},
        "errors": [{"title": "Not Found Error", "resource_type": "media",
                    "detail": "Could not find media with keys: [3_gone]"}],
    }
    assert _usable_timeline_payload(payload) is True


def test_errors_without_usable_data_is_still_fatal() -> None:
    """Negative control: a genuine error page must not be read as success.

    Empty `data` with errors is the dangerous one: `data` is a list, so a
    laxer "is it a list?" check would accept it and advance the time checkpoint
    past a window that returned no posts, permanently skipping whatever it held.
    """
    assert _usable_timeline_payload({"errors": [{"title": "Unauthorized"}]}) is False
    assert _usable_timeline_payload({"errors": [{"title": "x"}], "data": "not-a-list"}) is False
    assert _usable_timeline_payload({"errors": [{"title": "x"}], "data": []}) is False
    # and a truly empty window (no errors) is still fine — must not over-reject
    assert _usable_timeline_payload({"data": [], "meta": {"result_count": 0}}) is True


def test_media_aware_fetch_marks_text_only_posts_as_processed() -> None:
    """Absent `x_media` means "fetched before media support" — nothing else.

    If a media-aware fetch left text-only posts unmarked, they would stay
    candidates forever and every backfill run would pay X to look them up again.
    """
    index = _media_index({"includes": {"media": [
        {"media_key": "3_a", "type": "photo", "url": "https://pbs.twimg.com/media/a.jpg"}]}})
    text_only = _post_extra({"id": "1", "author_id": "9"}, "acct", media_index=index)
    with_media = _post_extra({"id": "2", "author_id": "9",
                              "attachments": {"media_keys": ["3_a"]}}, "acct", media_index=index)
    assert text_only["x_media"] == []          # present and empty: "looked, none there"
    assert len(with_media["x_media"]) == 1
    # And the pre-media path (no expansion requested) still leaves no marker,
    # so genuinely un-backfilled rows stay candidates.
    assert "x_media" not in _post_extra({"id": "3", "author_id": "9"}, "acct")
