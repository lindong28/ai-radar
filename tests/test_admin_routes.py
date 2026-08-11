from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.llm_usage import migrate_usage_db
from airadar.pricing import get_pricing
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
    db_path = tmp_path / "llm_usage.db"
    migrate(db_path)
    observed_at = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
        (observed_at,),
    )
    conn.execute(
        """
        INSERT INTO llm_usage (
          stage, provider, model, item_id, input_tokens, output_tokens,
          total_tokens, input_item_count, input_char_count, cost_usd,
          attribution_json, created_at
        ) VALUES (
          'enrich', 'unknown', 'missing-model', 'item-1',
          50, 10, 60, 1, 500, 99, '{}', ?
        )
        """,
        (observed_at,),
    )
    conn.execute(
        """
        INSERT INTO llm_usage (
          stage, provider, model, item_id, input_tokens, output_tokens,
          total_tokens, input_item_count, input_char_count, cost_usd,
          attribution_json, created_at
        )
        VALUES (
          'interpret', 'ark', 'deepseek-v4-pro-260425', 'item-1',
          1000, 200, 1200, 1, 12345, 0, '{"source":"summary-agent"}', ?
        )
        """,
        (observed_at,),
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


def test_public_response_exposes_machine_readable_server_timing(tmp_path: Path) -> None:
    app = create_app(_seed_admin_db(tmp_path))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["Server-Timing"].startswith("app;dur=")


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
    assert "公开页面性能" in response.text


def test_admin_performance_route_is_read_only_shared_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"rows": [{"journey": "homepage.first_card", "is_green": True}], "completeness": True}
    monkeypatch.setattr("airadar.web.routes.admin.collect_performance_status", lambda: expected)
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app)

    assert client.get("/api/v1/admin/performance").status_code == 403
    response = client.get("/api/v1/admin/performance", headers={"Cf-Access-Jwt-Assertion": "test"})

    assert response.status_code == 200
    assert response.json()["data"] == expected


def test_admin_head_probe_uses_same_cloudflare_access_guard(tmp_path: Path) -> None:
    app = create_app(_seed_admin_db(tmp_path))
    app.state.pipeline_log_dir = str(tmp_path / "logs")
    app.state.access_log_paths = []
    client = TestClient(app)

    assert client.head("/admin").status_code == 403
    assert client.head("/admin", headers={"Cf-Access-Jwt-Assertion": "test"}).status_code == 204


def test_admin_usage_route_requires_admin_access_and_renders_usage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_db_path = _seed_admin_db(tmp_path)
    usage_db_path = _seed_usage_db(tmp_path)
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db_path))
    pricing = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-flash": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
        persist=False,
    )
    monkeypatch.setattr("airadar.admin.usage.get_pricing", lambda: pricing)
    app = create_app(main_db_path)
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
    assert "deepseek-v4-pro-260425" in page.text
    assert "人民币" in page.text
    assert "未定价" in page.text
    assert "定价来源" in page.text
    assert "来源 / 时间边界" in page.text
    assert "仅供参考，不构成路由对比结论" in page.text
    assert "缓存拆分覆盖分母是 calls" in page.text
    assert "Cache 命中率分母是已采集子集的 input tokens" in page.text
    assert "catalog 来源状态" in page.text
    assert "litellm-live" in page.text
    assert "deepseek/deepseek-v4-flash" in page.text
    assert "exact" in page.text
    assert "成本分组与前窗对比" not in page.text
    assert "按天 / 模型" not in page.text
    assert "归因解释" not in page.text
    assert api.status_code == 200
    data = api.json()["data"]
    assert data["totals"]["known_cost_usd"] > 0
    assert data["totals"]["known_cost_cny"] > 0
    assert data["exchange_rate_usd_cny"] == 7.2
    assert data["unpriced"] == [{"provider": "unknown", "model": "missing-model", "calls": 1}]
    assert {"stage_costs", "provider_costs", "cost_groups", "comparison", "daily"}.isdisjoint(
        data
    )


def test_admin_usage_page_flags_unreviewed_fuzzy_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    main_db_path = _seed_admin_db(tmp_path)
    usage_db_path = tmp_path / "llm_usage.db"
    migrate_usage_db(usage_db_path=usage_db_path, main_db_path=tmp_path / "missing.db")
    observed_at = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(usage_db_path) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, item_id, input_tokens, output_tokens,
              total_tokens, input_item_count, input_char_count, cost_usd,
              attribution_json, created_at
            )
            VALUES (
              'interpret', 'deepseek', 'deepseek-v4-fla', 'item-1',
              100, 20, 120, 1, 4321, NULL, '{}', ?
            )
            """,
            (observed_at,),
        )
        conn.commit()
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db_path))
    pricing = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-flash": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
        persist=False,
    )
    monkeypatch.setattr("airadar.admin.usage.get_pricing", lambda: pricing)
    client = TestClient(create_app(main_db_path))

    page = client.get("/admin/usage", headers={"Cf-Access-Jwt-Assertion": "test"})

    assert page.status_code == 200
    assert "deepseek/deepseek-v4-flash" in page.text
    assert "fuzzy（未审计）" in page.text


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
