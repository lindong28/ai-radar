from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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
          's', 'Source', 'https://example.com/feed.xml', 'T1.5', 1, 'feed',
          'https://example.com/', 'https://example.com/favicon.ico', '{}', '2026-05-12T00:00:00Z'
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
          'item-1', 's', 'https://example.com/item', 'Original title', 'Ada',
          '2026-05-12T00:00:00Z', '2026-05-12T00:01:00Z',
          'Original content preview', NULL, 'h1', '{}'
        )
        """
    )
    enrich = {
        "title_zh": "中文标题",
        "summary_zh": "这是一段中文摘要，覆盖核心事实、原因和对读者的实际意义，便于快速判断是否继续阅读。",
        "why_recommend": "做 AI 工程落地的你应该读这篇，因为它提供了明确的实践信号。",
        "tags": ["模型发布", "教程/实践"],
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-1', 'enrich', 'test.r1', 'fake', '{}', ?, NULL, 1, 0, '2026-05-12T00:02:00Z', NULL)
        """,
        (json.dumps(enrich),),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-1', 'test.r1', '{}', 6.0, '[]', '["item-1"]', '2026-05-12T00:03:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-1', 'item-1', 9.2, 1, '{}')
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_curated_api_returns_enriched_fields(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/curated").json()["data"]["items"][0]

    assert item["title_zh"] == "中文标题"
    assert item["summary_zh"].startswith("这是一段中文摘要")
    assert item["why_recommend"] == "做 AI 工程落地的你应该读这篇，因为它提供了明确的实践信号。"
    assert item["reasoning"] == item["why_recommend"]
    assert item["enriched_tags"] == ["模型发布", "教程/实践"]
    assert item["topic_tags"] == ["模型发布", "教程/实践"]
