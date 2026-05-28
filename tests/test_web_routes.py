from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.enrich.schema import EnrichOutput
from airadar.web.app import create_app
from airadar.web.routes import common as route_common


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


def test_perf_migration_adds_timeline_indexes(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)

    index_names = {row[1] for row in conn.execute("PRAGMA index_list('items')").fetchall()}

    assert "idx_items_source_url_norm" in index_names
    assert "idx_items_published_fetched_id" in index_names


def test_item_summary_uses_preloaded_enrichment_without_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT i.*, s.name AS source_name, s.tier,
               s.kind AS source_kind,
               s.homepage_url AS source_homepage_url,
               s.icon_url AS source_icon_url
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE i.id='item-openai'
        """
    ).fetchone()
    enrichment = EnrichOutput(
        title_zh="OpenAI API 产品更新",
        summary_zh="这是一段用于验证预加载 enrichment 路径的中文摘要，长度足够满足结构化校验要求。",
        why_recommend="这是一段用于验证预加载 enrichment 路径的中文推荐理由。",
        tags=["产品更新", "MCP/工具"],
    )

    def fail_latest_lookup(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preloaded enrichment should skip latest_enrichment lookup")

    monkeypatch.setattr(route_common, "latest_enrichment", fail_latest_lookup)

    item = route_common.item_summary(row, conn=conn, include_related=False, enrichment=enrichment)

    assert item["title_zh"] == "OpenAI API 产品更新"
    assert item["summary_zh"] == enrichment.summary_zh
    assert item["why_recommend"] == enrichment.why_recommend
    assert "产品更新" in item["topic_tags"]
    assert item["related_discussions"] == []


def test_timeline_total_uses_limit_plus_one_estimate(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    page_one = client.get("/api/v1/timeline", params={"limit": 1, "page": 1}).json()["data"]
    last_page = client.get("/api/v1/timeline", params={"limit": 1, "page": 3}).json()["data"]
    single_page = client.get("/api/v1/timeline", params={"limit": 50, "page": 1}).json()["data"]
    empty_page = client.get("/api/v1/timeline", params={"q": "xyzzyqwertynonexistent", "limit": 50}).json()["data"]

    assert page_one["total"] == 2
    assert last_page["total"] == 3
    assert single_page["total"] == 3
    assert empty_page["total"] == 0


def test_timeline_exposes_default_score_for_unscored_items(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    items = client.get("/api/v1/timeline", params={"limit": 3}).json()["data"]["items"]

    unscored = next(item for item in items if item["id"] == "item-claude")
    assert unscored["weighted_score"] == 0


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
    assert page_one["total"] == 2
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
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-opinion', 'openai_blog', 'https://example.com/opinion', 'Opinion news', 'Ada',
          '2026-05-08T07:00:00Z', '2026-05-08T07:02:00Z',
          'This is industry opinion without a concrete tutorial or deployment practice.',
          NULL, 'h-opinion', '{}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-deploy-news', 'openai_blog', 'https://example.com/deploy-news', 'Deploy industry news', 'Ada',
          '2026-05-08T06:00:00Z', '2026-05-08T06:02:00Z',
          'This is broad infrastructure industry news, not a concrete deployment practice.',
          NULL, 'h-deploy-news', '{}'
        )
        """
    )
    _insert_enrichment(conn, "item-openai", "泛开源端侧条目", ["开源/仓库", "端侧"])
    _insert_enrichment(conn, "item-claude", "Transformer 实践课程", ["模型发布", "教程/实践"])
    _insert_enrichment(conn, "item-x", "部署工程实践", ["部署/工程", "大佬观点"])
    _insert_enrichment(conn, "item-opinion", "纯观点行业新闻", ["大佬观点", "行业动态"])
    _insert_enrichment(conn, "item-deploy-news", "部署行业新闻", ["部署/工程", "行业动态"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 9.9, 2, '{}'),
          ('run-1', 'item-x', 9.5, 3, '{}'),
          ('run-1', 'item-opinion', 9.1, 4, '{}'),
          ('run-1', 'item-deploy-news', 9.0, 5, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "tip"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "tip"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-claude", "item-x"]
    assert [item["id"] for item in curated["items"]] == ["item-claude", "item-x"]


def test_timeline_and_curated_deduplicate_same_source_url(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-claude-dup', 'openai_blog', 'https://example.com/claude', 'Claude Notes Updated', 'Ben',
          '2026-05-08T09:30:00Z', '2026-05-08T09:32:00Z',
          'Anthropic published updated engineering notes for model users.',
          NULL, 'h-claude-dup', '{}'
        )
        """
    )
    _insert_enrichment(conn, "item-claude", "Claude 研究笔记", ["论文/研究", "Anthropic"])
    _insert_enrichment(conn, "item-claude-dup", "Claude 研究笔记更新", ["论文/研究", "Anthropic"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 8.0, 2, '{}'),
          ('run-1', 'item-claude-dup', 8.1, 3, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "paper"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "paper"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-claude-dup"]
    assert [item["id"] for item in curated["items"]] == ["item-claude-dup"]


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
