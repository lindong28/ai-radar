from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.web.app import create_app


def _seed_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','Source','https://example.com/feed','T1',1,'{}','2026-05-08T00:00:00Z')"
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES ('item-1','s','https://example.com/item','AI API Release','Ada','2026-05-08T00:00:00Z','2026-05-08T01:00:00Z','content',NULL,'h1','{}')
        """
    )
    numeric = {
        "relevance": 9,
        "density": 8,
        "recency": 7,
        "authority": 10,
        "engineering": 8,
        "reasoning": "Useful API release.",
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-1','scoring','test.r1','fake','{}','{}',?,1,0,'2026-05-08T01:01:00Z',NULL)
        """,
        (json.dumps(numeric),),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-1','test.r1','{}',6.5,'[1]','["item-1"]','2026-05-08T01:02:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-1','item-1',8.2,1,?)
        """,
        (json.dumps({"scores": numeric}),),
    )
    conn.commit()
    return db_path


def test_web_read_endpoints_return_enveloped_data(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    health = client.get("/api/v1/healthz").json()
    assert health["success"] is True
    assert health["data"]["ok"] is True

    timeline = client.get("/api/v1/timeline?limit=5").json()
    assert timeline["success"] is True
    assert timeline["data"]["items"][0]["id"] == "item-1"
    assert timeline["data"]["items"][0]["content_preview"] == "content"
    assert timeline["data"]["items"][0]["weighted_score"] == pytest.approx(8.2)
    assert timeline["data"]["items"][0]["rank"] == 1
    assert "Useful API release" not in timeline["data"]["items"][0]["reasoning"]
    assert any("\u4e00" <= char <= "\u9fff" for char in timeline["data"]["items"][0]["reasoning"])
    assert timeline["data"]["items"][0]["summary_zh"] is None

    curated = client.get("/api/v1/curated").json()
    assert curated["data"]["run_id"] == "run-1"
    assert curated["data"]["items"][0]["weighted_score"] == 8.2
    assert curated["data"]["items"][0]["why_recommend"] == curated["data"]["items"][0]["reasoning"]
    assert curated["data"]["items"][0]["rank"] == 1
    assert any("\u4e00" <= char <= "\u9fff" for char in curated["data"]["items"][0]["reasoning"])

    item = client.get("/api/v1/items/item-1").json()
    assert item["data"]["item"]["title"] == "AI API Release"
    assert item["data"]["evaluations"][0]["stage"] == "scoring"

    sources = client.get("/api/v1/sources").json()
    assert sources["data"]["sources"][0]["id"] == "s"


def test_web_errors_and_cors_are_read_only(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    assert client.post("/api/v1/timeline").status_code == 405
    assert client.get("/api/v1/items/notexist").status_code == 404
    for path in ["/api/admin", "/api/v1/admin", "/api/v1/sources/add"]:
        assert client.get(path).status_code == 404

    response = client.options(
        "/api/v1/timeline",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_item_detail_suppresses_wechat_full_text(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (id,name,url,tier,kind,enabled,meta_json,synced_at)
        VALUES ('wx','WeChat Source','http://localhost:4000/feeds/MP.rss','T2','wechat',1,'{}','2026-05-08T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'wechat-item','wx','https://mp.weixin.qq.com/s/seed','Seed WeChat Article',NULL,
          '2026-05-08T00:00:00Z','2026-05-08T01:00:00Z',
          'full copied article body must stay internal',NULL,'h-wechat','{}'
        )
        """
    )
    conn.commit()

    client = TestClient(create_app(db_path))

    payload = client.get("/api/v1/items/wechat-item").json()["data"]["item"]

    assert payload["source_kind"] == "wechat"
    assert payload["content_preview"] is None
    assert "content_text" not in payload


def test_static_pages_are_served(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    assert "AI Radar" in client.get("/index.html").text
    assert "AI Radar" in client.get("/curated.html").text
    assert "AI Radar" in client.get("/item.html").text
