from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from airadar.db import migrate
from airadar.web.app import create_app
from fastapi.testclient import TestClient


def _seed_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'openai_blog', 'OpenAI Blog', 'https://openai.com/blog/rss.xml', 'T1', 1,
          'feed', 'https://openai.com/', 'https://example.com/openai.ico', '{}', '2026-05-08T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'simonw_mastodon', 'Simon Willison (Mastodon)', 'https://fedi.simonwillison.net/@simon.rss', 'T1.5', 1,
          'x', 'https://fedi.simonwillison.net/@simon', 'https://example.com/simon.ico', '{}', '2026-05-08T00:00:00Z'
        )
        """
    )
    items = [
        (
            "item-openai",
            "openai_blog",
            "https://example.com/api-release",
            "OpenAI API Release",
            "Ada",
            "2026-05-08T10:00:00Z",
            "2026-05-08T10:02:00Z",
            "OpenAI released a practical API update for developers.",
            '<p>OpenAI released a practical API update.</p><img src="https://nitter.net/pic/media%2Fopenai.jpg"><img src="javascript:alert(1)">',
            "h-openai",
        ),
        (
            "item-claude",
            "openai_blog",
            "https://example.com/claude",
            "Claude Notes",
            "Ben",
            "2026-05-08T09:00:00Z",
            "2026-05-08T09:02:00Z",
            "Anthropic published engineering notes for model users.",
            None,
            "h-claude",
        ),
        (
            "item-x",
            "simonw_mastodon",
            "https://fedi.simonwillison.net/@simon/1",
            "Mastodon post",
            "Simon",
            "2026-05-08T08:00:00Z",
            "2026-05-08T08:02:00Z",
            "Long social post about SQLite FTS5 trigram search and developer tooling. "
            "Related discussion for https://example.com/api-release",
            None,
            "h-x",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
        """,
        items,
    )
    numeric = {
        "relevance": 9,
        "density": 8,
        "recency": 7,
        "authority": 10,
        "engineering": 8,
        "reasoning": "OpenAI release is useful for API builders.",
    }
    curated_reason = {
        "scores": {
            **numeric,
            "reasoning": "精选推荐理由：OpenAI API 更新对开发者有直接价值，适合优先阅读。",
        }
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-openai', 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T10:03:00Z', NULL)
        """,
        (json.dumps(numeric),),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-1', 'test.r1', '{}', 6.5, '[1]', '["item-openai"]', '2026-05-08T10:04:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-1', 'item-openai', 8.2, 1, ?)
        """,
        (json.dumps(curated_reason),),
    )
    conn.commit()
    conn.close()
    return db_path


def _insert_enrichment(conn: sqlite3.Connection, item_id: str, title: str, tags: list[str]) -> None:
    payload = {
        "title_zh": title,
        "summary_zh": "这是一段用于测试分类筛选的中文摘要，长度足够满足结构化校验要求。",
        "why_recommend": "这是一段用于测试分类筛选的中文推荐理由。",
        "tags": tags,
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, 'enrich', 'test.r1', 'fake', '{}', ?, '{}', 1, 0, '2026-05-08T10:07:00Z', NULL)
        """,
        (item_id, json.dumps(payload)),
    )


def test_static_clean_routes_and_curated_redirect(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    for path in ["/", "/all", "/daily", "/about"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "side-nav" in response.text

    redirect = client.get("/curated.html", follow_redirects=False)
    assert redirect.status_code == 308
    assert redirect.headers["location"] == "/"


def test_fts_backfill_keeps_one_row_per_item(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    migrate(db_path)
    migrate(db_path)
    conn = sqlite3.connect(db_path)

    items_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    fts_count = conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
    reasoning_count = conn.execute("SELECT COUNT(*) FROM items_fts WHERE reasoning != ''").fetchone()[0]

    assert fts_count == items_count
    assert reasoning_count == 1


def test_timeline_search_uses_fts_and_filters_results(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/api/v1/timeline?q=OpenAI")
    assert response.status_code == 200
    data = response.json()["data"]

    assert [item["id"] for item in data["items"]] == ["item-openai"]
    assert data["total"] == 1


def test_timeline_supports_channel_filters_and_url_page_state(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    page_one = client.get("/api/v1/timeline", params={"limit": 1, "page": 1}).json()["data"]
    page_two = client.get("/api/v1/timeline", params={"limit": 1, "page": 2}).json()["data"]

    assert page_one["page"] == 1
    assert page_one["limit"] == 1
    assert page_one["total"] == 3
    assert [item["id"] for item in page_one["items"]] == ["item-openai"]
    assert page_two["page"] == 2
    assert [item["id"] for item in page_two["items"]] == ["item-claude"]

    x_only = client.get("/api/v1/timeline", params={"channel": "x"}).json()["data"]
    assert [item["id"] for item in x_only["items"]] == ["item-x"]
    assert x_only["total"] == 1

    first_party = client.get("/api/v1/timeline", params={"channel": "firstParty"}).json()["data"]
    assert [item["id"] for item in first_party["items"]] == ["item-openai", "item-claude"]
    assert {item["source_kind"] for item in first_party["items"]} == {"feed"}


def test_timeline_category_filter_applies_before_pagination(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "OpenAI API 产品更新", ["产品更新", "OpenAI"])
    _insert_enrichment(conn, "item-claude", "Claude 研究笔记", ["论文/研究", "Anthropic"])
    _insert_enrichment(conn, "item-x", "SQLite 研究讨论", ["论文/研究", "教程/实践"])
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    page_one = client.get("/api/v1/timeline", params={"category": "paper", "limit": 1, "page": 1}).json()["data"]
    page_two = client.get("/api/v1/timeline", params={"category": "paper", "limit": 1, "page": 2}).json()["data"]

    assert page_one["total"] == 2
    assert [item["id"] for item in page_one["items"]] == ["item-claude"]
    assert [item["id"] for item in page_two["items"]] == ["item-x"]
    assert all("论文/研究" in item["topic_tags"] for item in [*page_one["items"], *page_two["items"]])


def test_ai_models_category_excludes_course_and_benchmark_only_items(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "OpenAI 模型发布", ["模型发布", "OpenAI"])
    _insert_enrichment(conn, "item-claude", "Transformer 实践课程", ["模型发布", "教程/实践"])
    _insert_enrichment(conn, "item-x", "模型评测讨论", ["评测/基准", "开源/仓库"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 9.9, 2, '{}'),
          ('run-1', 'item-x', 9.5, 3, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "ai-models"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "ai-models"}).json()["data"]
    all_curated = client.get("/api/v1/curated").json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-openai"]
    assert [item["id"] for item in curated["items"]] == ["item-openai"]
    assert [item["id"] for item in all_curated["items"]] == ["item-openai", "item-claude", "item-x"]


def test_product_category_uses_product_semantics_not_model_or_multimodal_only(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "纯模型多模态条目", ["模型发布", "多模态", "MCP/工具"])
    _insert_enrichment(conn, "item-claude", "真实产品更新", ["产品更新", "模型发布"])
    _insert_enrichment(conn, "item-x", "泛开源仓库", ["开源/仓库", "端侧"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 9.9, 2, '{}'),
          ('run-1', 'item-x', 9.5, 3, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "ai-products"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "ai-products"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-claude"]
    assert [item["id"] for item in curated["items"]] == ["item-claude"]


def test_tip_category_excludes_repo_and_edge_only_items(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "泛开源端侧条目", ["开源/仓库", "端侧"])
    _insert_enrichment(conn, "item-claude", "Transformer 实践课程", ["模型发布", "教程/实践"])
    _insert_enrichment(conn, "item-x", "部署工程实践", ["部署/工程", "大佬观点"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 9.9, 2, '{}'),
          ('run-1', 'item-x', 9.5, 3, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "tip"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "tip"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-claude", "item-x"]
    assert [item["id"] for item in curated["items"]] == ["item-claude", "item-x"]


def test_timeline_exposes_latest_curated_metadata_for_all_feed(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/timeline?q=OpenAI").json()["data"]["items"][0]

    assert item["rank"] == 1
    assert item["weighted_score"] == pytest.approx(8.2)
    assert item["reasoning"] == "精选推荐理由：OpenAI API 更新对开发者有直接价值，适合优先阅读。"
    assert item["why_recommend"] == item["reasoning"]


def test_timeline_uses_chinese_fallback_instead_of_english_scoring_reason(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/timeline?q=OpenAI").json()["data"]["items"][0]

    assert item["reasoning"] != "OpenAI release is useful for API builders."
    assert item["why_recommend"] == item["reasoning"]
    assert any("\u4e00" <= char <= "\u9fff" for char in item["reasoning"])


def test_timeline_filters_to_latest_prefilter_pass_when_prefilter_exists(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES
          ('item-openai', 'prefilter', 'test.r1', 'fake', '{}', '{}', '{"is_ai_related":true,"confidence":0.99}', 1, 0, '2026-05-08T10:05:00Z', NULL),
          ('item-claude', 'prefilter', 'test.r1', 'fake', '{}', '{}', '{"is_ai_related":true,"confidence":0.90}', 1, 0, '2026-05-08T10:05:00Z', NULL),
          ('item-x', 'prefilter', 'test.r1', 'fake', '{}', '{}', '{"is_ai_related":false,"confidence":0.90}', 1, 0, '2026-05-08T10:05:00Z', NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (
          'item-claude', 'scoring', 'test.r1', 'fake', '{}', '{}',
          '{"relevance":2,"density":2,"recency":2,"authority":2,"engineering":2,"reasoning":"low relevance"}',
          1, 0, '2026-05-08T10:06:00Z', NULL
        )
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    data = client.get("/api/v1/timeline").json()["data"]

    assert [item["id"] for item in data["items"]] == ["item-openai"]
    assert data["total"] == 1


def test_curated_search_uses_fts_and_filters_results(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    match = client.get("/api/v1/curated?q=OpenAI").json()["data"]
    no_match = client.get("/api/v1/curated?q=xyzzyqwertynonexistent").json()["data"]

    assert [item["id"] for item in match["items"]] == ["item-openai"]
    assert no_match["items"] == []


def test_curated_api_exposes_related_discussions_for_cross_source_links(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/curated").json()["data"]["items"][0]

    assert item["related_discussions"] == [
        {
            "source_id": "simonw_mastodon",
            "source_name": "Simon Willison (Mastodon)",
            "source_kind": "x",
            "author": "Simon",
            "url": "https://fedi.simonwillison.net/@simon/1",
        }
    ]


def test_curated_api_exposes_safe_article_media_assets(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/curated").json()["data"]["items"][0]

    assert item["media_assets"] == [{"type": "image", "url": "https://nitter.net/pic/media%2Fopenai.jpg"}]


def test_search_query_treats_fts_syntax_as_literal_text(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    for query in ["title:foo", "OpenAI OR Claude", '"DROP TABLE items"']:
        response = client.get("/api/v1/timeline", params={"q": query})
        assert response.status_code == 200

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
