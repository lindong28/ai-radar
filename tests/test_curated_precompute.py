from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from airadar.curator.precompute import precompute_curated_summaries
from airadar.db import migrate
from airadar.web.app import create_app


def _seed_db(tmp_path: Path, item_count: int = 5) -> Path:
    today = date.today()
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          's', 'Source', 'https://example.com/feed.xml', 'T1', 1, 'feed',
          'https://example.com/', 'https://example.com/favicon.ico', '{}', ?
        )
        """,
        (f"{today.isoformat()}T00:00:00Z",),
    )
    numeric = {
        "relevance": 9,
        "density": 8,
        "recency": 7,
        "authority": 10,
        "engineering": 8,
        "reasoning": "中文推荐理由：值得阅读。",
    }
    item_ids = []
    for idx in range(item_count):
        item_id = f"item-{idx}"
        item_ids.append(item_id)
        published_at = f"{(today - timedelta(days=idx)).isoformat()}T09:00:00Z"
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, 'Ada', ?, ?, ?, NULL, ?, '{}')
            """,
            (
                item_id,
                f"https://example.com/item-{idx}",
                f"Item {idx}",
                published_at,
                published_at,
                f"content text {idx} with searchable_keyword",
                f"hash-{idx}",
            ),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, ?, NULL)
            """,
            (item_id, json.dumps(numeric), published_at),
        )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-1', 'test.r1', '{}', 6.5, '[]', ?, ?)
        """,
        (json.dumps(item_ids), f"{today.isoformat()}T10:00:00Z"),
    )
    for rank, item_id in enumerate(item_ids, start=1):
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-1', ?, 8.2, ?, ?)
            """,
            (item_id, rank, json.dumps({"scores": numeric})),
        )
    conn.commit()
    conn.close()
    return db_path


def test_precompute_fills_summary_json(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path, item_count=3)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    count = precompute_curated_summaries(conn, "run-1")
    assert count == 3

    rows = conn.execute(
        "SELECT item_id, summary_json FROM curated_items WHERE run_id='run-1' ORDER BY rank"
    ).fetchall()
    for row in rows:
        assert row["summary_json"] is not None
        summary = json.loads(row["summary_json"])
        assert summary["id"] == row["item_id"]
        assert summary["weighted_score"] == 8.2
        assert summary["rank"] is not None
        assert "scores" in summary
        assert summary["scores"]["relevance"] == 9
    conn.close()


def test_curated_api_serves_precomputed_data(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path, item_count=3)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    precompute_curated_summaries(conn, "run-1")
    conn.close()

    client = TestClient(create_app(db_path))
    data = client.get("/api/v1/curated").json()["data"]

    assert data["count"] == 3
    assert [item["id"] for item in data["items"]] == ["item-0", "item-1", "item-2"]
    for item in data["items"]:
        assert item["weighted_score"] == 8.2
        assert item["scores"]["relevance"] == 9


def test_curated_api_falls_back_when_no_precompute(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path, item_count=2)
    client = TestClient(create_app(db_path))
    data = client.get("/api/v1/curated").json()["data"]

    assert data["count"] == 2
    assert {item["id"] for item in data["items"]} == {"item-0", "item-1"}


def test_curated_api_date_filter_on_precomputed(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path, item_count=3)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    precompute_curated_summaries(conn, "run-1")
    conn.close()

    today = date.today()
    client = TestClient(create_app(db_path))
    data = client.get("/api/v1/curated", params={"date": today.isoformat()}).json()["data"]

    assert data["count"] == 1
    assert data["items"][0]["id"] == "item-0"


def test_curated_api_search_on_precomputed(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path, item_count=3)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    precompute_curated_summaries(conn, "run-1")
    conn.close()

    client = TestClient(create_app(db_path))
    data = client.get("/api/v1/curated", params={"q": "searchable_keyword"}).json()["data"]

    assert data["count"] == 3
    for item in data["items"]:
        assert "searchable_keyword" in (item.get("content_preview") or "")


def test_curated_api_precomputed_latency(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path, item_count=40)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    precompute_curated_summaries(conn, "run-1")
    conn.close()

    client = TestClient(create_app(db_path))
    client.get("/api/v1/curated")

    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        response = client.get("/api/v1/curated")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        samples.append(elapsed_ms)
        assert response.status_code == 200

    samples.sort()
    median = samples[len(samples) // 2]
    assert median < 100, f"precomputed API median latency {median:.1f}ms exceeds 100ms budget"
