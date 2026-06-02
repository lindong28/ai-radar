from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.web.app import create_app


def _seed_admin_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
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
          '2026-06-01T16:30:00Z', '2026-06-01T16:30:00Z',
          'content', NULL, 'hash-1', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-1', 'scoring', 'test.r1', 'fake', '{}', '{}', '{}', 1000, 0.01, '2026-06-01T16:40:00Z', NULL)
        """
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
