from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.enrich.category import RUBRIC
from airadar.enrich.classification import classification_projection
from airadar.enrich.prompts_v2 import SYSTEM_PROMPT, render_enrich_prompt
from airadar.provider.base import ProviderItem
from airadar.web.app import create_app
from airadar.web.routes import categories, categories_v2

SLUGS = {
    "model": "ai-models", "product": "ai-products", "industry": "industry",
    "paper": "paper", "tutorial": "tip", "opinion": "opinion",
}


def _enrichment(category: str | None, tags: list[str], opinion: bool = False) -> dict:
    payload = {
        "title_zh": "测试新闻标题",
        "summary_zh": "这条测试新闻保留原文信息，用于检查分类消费者如何选择和展示新闻内容。",
        "why_recommend": "原文交代了这次发布的信息与适用范围，读者可以据此核对自己的使用条件并判断是否需要继续了解。",
        "tags": tags,
    }
    if category is not None:
        payload.update(primary_category=category, is_opinion=opinion)
    return payload


@pytest.fixture
def category_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "six-category.db"
    migrate(db_path)
    payloads = {category: _enrichment(category, ["模型发布"]) for category in SLUGS}
    payloads["opinion-true"] = _enrichment("opinion", ["模型发布"], True)
    payloads["legacy-opinion"] = _enrichment(None, ["教程/实践", "大佬观点"])
    payloads["legacy-multi"] = _enrichment(None, ["模型发布", "论文/研究"])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO sources (id,name,url,tier,enabled,kind,meta_json,synced_at)
               VALUES ('s','Source','https://example.com/feed','T1',1,'feed','{}','2026-09-20T00:00:00Z')"""
        )
        conn.execute(
            """INSERT INTO curation_runs
               (id,ruleset_version,weights_json,threshold,input_eval_ids,output_curated_ids,created_at)
               VALUES ('six','test','{}',6,'[]',?,'2026-09-20T00:03:00Z')""",
            (json.dumps(list(payloads)),),
        )
        for rank, (item_id, payload) in enumerate(payloads.items(), 1):
            conn.execute(
                """INSERT INTO items
                   (id,source_id,url,title,published_at,fetched_at,content_text,content_hash,extra_json)
                   VALUES (?,'s',?,?,'2026-09-20T00:00:00Z','2026-09-20T00:01:00Z',?,?,'{}')""",
                (item_id, f"https://example.com/{item_id}", item_id, "Article " + item_id, item_id),
            )
            conn.execute(
                """INSERT INTO item_evaluations
                   (item_id,stage,ruleset_version,model_id,input_json,output_json,evaluated_at)
                   VALUES (?,'enrich','test','fixture','{}',?,'2026-09-20T00:02:00Z')""",
                (item_id, json.dumps(payload, ensure_ascii=False)),
            )
            conn.execute(
                "INSERT INTO curated_items (run_id,item_id,weighted_score,rank,reason_json) VALUES ('six',?,9,?,'{}')",
                (item_id, rank),
            )
    return db_path


@pytest.mark.parametrize("endpoint", ["/api/v1/timeline", "/api/v1/curated", "/api/v1/curated?run_id=six"])
def test_real_feed_routes_respect_six_explicit_categories_and_legacy_tags(category_db: Path, endpoint: str) -> None:
    with TestClient(create_app(category_db)) as client:
        for category, slug in SLUGS.items():
            response = client.get(endpoint, params={"category": slug, **({"run_id": "six"} if "?" in endpoint else {})})
            assert response.status_code == 200
            data = response.json()["data"]
            expected = {category}
            if category == "opinion":
                expected |= {"opinion-true", "legacy-opinion"}
            elif category in {"model", "paper"}:
                expected.add("legacy-multi")
            elif category == "tutorial":
                expected.add("legacy-opinion")
            assert {item["id"] for item in data["items"]} == expected, (endpoint, category)
            if "total" in data:
                assert data["total"] == len(expected)


@pytest.mark.parametrize("endpoint", ["/", "/all"])
def test_ssr_opinion_category_has_filtered_items_and_six_navigation_links(category_db: Path, endpoint: str) -> None:
    with TestClient(create_app(category_db)) as client:
        response = client.get(endpoint, params={"category": "opinion"})
    assert response.status_code == 200
    assert 'data-category="opinion"' in response.text
    assert '>教程</a>' in response.text
    assert 'https://example.com/opinion' in response.text
    assert 'https://example.com/product' not in response.text


def test_v2_opinion_helpers_include_explicit_opinion_even_when_old_boolean_is_false(category_db: Path) -> None:
    with sqlite3.connect(category_db) as conn:
        for build_clause in (
            lambda: categories_v2.category_filter_clause("opinion"),
            lambda: categories_v2.opinion_filter_clause(True),
        ):
            clause, params = build_clause()
            ids = {row[0] for row in conn.execute(f"SELECT i.id FROM items i WHERE {clause}", params)}
            assert ids == {"opinion", "opinion-true", "legacy-opinion"}
    for category, opinion, expected in [("opinion", False, True), ("model", True, True), ("model", False, False)]:
        projection = classification_projection(_enrichment(category, ["模型发布"], opinion))
        item = {
            "primary_category": projection.primary_category,
            "is_opinion": projection.is_opinion,
            "classification_projection_authority": projection.authority,
            "classification_projection_status": projection.projection_status,
        }
        assert categories_v2.matches_opinion(item) is expected
        assert categories_v2.matches_category(item, "opinion") is (category == "opinion")


def test_newest_successful_enrichment_controls_opinion_filter(category_db: Path) -> None:
    with sqlite3.connect(category_db) as conn:
        conn.execute(
            """INSERT INTO item_evaluations
               (item_id,stage,ruleset_version,model_id,input_json,output_json,evaluated_at)
               VALUES ('opinion','enrich','test','fixture','{}',?,'2026-09-20T00:04:00Z')""",
            (json.dumps(_enrichment("tutorial", ["大佬观点"])),),
        )
        clause, params = categories.category_filter_clause("opinion")
        ids = {row[0] for row in conn.execute(f"SELECT i.id FROM items i WHERE {clause}", params)}
        assert ids == {"opinion-true", "legacy-opinion"}


def test_full_enrich_prompt_uses_shared_six_way_rubric_without_expanding_output_fields() -> None:
    item = ProviderItem("i", "Title", "https://example.com", "s", "T1", None, "2026-09-20", "Body")
    prompt = render_enrich_prompt(item)
    assert RUBRIC in SYSTEM_PROMPT
    assert "五个主类" not in prompt["user"]
    assert "观点不是第六个主类" not in SYSTEM_PROMPT
    shape = json.loads(prompt["user"].split("输出 JSON，字段必须完全如下：\n", 1)[1])
    assert set(shape) == {"title_zh", "summary_zh", "why_recommend", "tags", "primary_category", "is_opinion"}
