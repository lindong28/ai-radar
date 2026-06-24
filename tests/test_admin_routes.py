from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.web.app import create_app


def _seed_admin_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','Source','https://example.com/feed','T1',1,'{}','2026-06-01T00:00:00Z')"
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES ('item-1', 's', 'https://example.com/1', 'Item 1', NULL,
          ?, ?,
          'content', NULL, 'hash-1', '{}')
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-1', 'scoring', 'test.r1', 'fake', '{}', '{}', '{}', 1000, 0.01, ?, NULL)
        """,
        (now,),
    )
    conn.commit()
    return db_path


def _seed_usage_db(tmp_path: Path) -> Path:
    db_path = _seed_admin_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO llm_usage (
          stage, provider, model, item_id, input_tokens, output_tokens,
          total_tokens, input_item_count, input_char_count, cost_usd,
          attribution_json, created_at
        )
        VALUES (
          'prefilter', 'deepseek', 'deepseek-v4-flash', 'item-1',
          100, 20, 120, 1, 4321, 0, '{"source":"test"}', ?
        )
        """,
        (datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),),
    )
    conn.commit()
    return db_path


def test_admin_metrics_requires_cloudflare_access_header(tmp_path: Path) -> None:
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app)

    assert client.get("/api/v1/admin/metrics").status_code == 403

    response = client.get("/api/v1/admin/metrics", headers={"Cf-Access-Jwt-Assertion": "test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["timezone"] == "Asia/Shanghai"
    assert payload["data"]["pipeline"]["stages"]["scoring"]["processed"] == 1


def test_admin_metrics_blocks_loopback_without_dev_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_RADAR_ADMIN_ALLOW_LOCAL", raising=False)
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app, client=("127.0.0.1", 50000))

    assert client.get("/api/v1/admin/metrics").status_code == 403


def test_admin_metrics_allows_loopback_with_dev_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_RADAR_ADMIN_ALLOW_LOCAL", "yes")
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get("/api/v1/admin/metrics")

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_admin_metrics_allows_cloudflare_header_from_loopback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_RADAR_ADMIN_ALLOW_LOCAL", raising=False)
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get("/api/v1/admin/metrics", headers={"Cf-Access-Jwt-Assertion": "test"})

    assert response.status_code == 200
    assert response.json()["success"] is True


def test_admin_page_renders_four_dashboard_sections(tmp_path: Path) -> None:
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app)

    response = client.get("/admin", headers={"Cf-Access-Jwt-Assertion": "test"})

    assert response.status_code == 200
    assert "用户量" in response.text
    assert "文章摄取" in response.text
    assert "Pipeline 阶段健康" in response.text
    assert "当前告警" in response.text


def test_admin_head_probe_uses_same_cloudflare_access_guard(tmp_path: Path) -> None:
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app)

    assert client.head("/admin").status_code == 403
    assert client.head("/admin", headers={"Cf-Access-Jwt-Assertion": "test"}).status_code == 204


def test_admin_usage_route_requires_admin_access_and_renders_usage(tmp_path: Path) -> None:
    app = create_app(_seed_usage_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app)

    assert client.get("/admin/usage").status_code == 403
    assert client.get("/api/v1/admin/usage").status_code == 403

    page = client.get("/admin/usage", headers={"Cf-Access-Jwt-Assertion": "test"})
    api = client.get("/api/v1/admin/usage", headers={"Cf-Access-Jwt-Assertion": "test"})

    assert page.status_code == 200
    assert "LLM 用量" in page.text
    assert "deepseek-v4-flash" in page.text
    assert "prefilter" in page.text
    assert "Item 1" in page.text
    assert "4,321" in page.text
    assert api.status_code == 200
    data = api.json()["data"]
    model_row = next(
        row
        for day in data["daily"]
        for row in day["models"]
        if row["model"] == "deepseek-v4-flash"
    )
    assert model_row["calls"] == 1
    assert model_row["input_tokens"] == 100
    assert model_row["output_tokens"] == 20


def test_admin_usage_is_not_linked_from_public_navigation() -> None:
    public_pages = [
        Path("web/templates/index.html"),
        Path("web/templates/all.html"),
        Path("web/templates/wechat.html"),
        Path("web/templates/wechat_detail.html"),
        Path("web/templates/about.html"),
        Path("web/static/index.html"),
        Path("web/static/all.html"),
        Path("web/static/daily.html"),
        Path("web/static/item.html"),
    ]

    for page in public_pages:
        assert "/admin/usage" not in page.read_text(encoding="utf-8")
