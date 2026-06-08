from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.web.app import create_app


def _seed_db(tmp_path: Path) -> tuple[Path, date, date]:
    today = date.today()
    history = today - timedelta(days=2)
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
        "reasoning": "Useful item.",
    }
    items = [
        (
            "item-today",
            "https://example.com/today",
            "Today Item",
            f"{today.isoformat()}T09:00:00Z",
            "today",
        ),
        (
            "item-history",
            "https://example.com/history",
            "History Item",
            f"{history.isoformat()}T08:00:00Z",
            "history",
        ),
    ]
    for item_id, url, title, published_at, content_hash in items:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, 'Ada', ?, ?, 'content', NULL, ?, '{}')
            """,
            (item_id, url, title, published_at, published_at, content_hash),
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
        VALUES ('run-1', 'test.r1', '{}', 6.5, '[1,2]', '["item-today","item-history"]', ?)
        """,
        (f"{today.isoformat()}T10:00:00Z",),
    )
    for rank, item_id in enumerate(["item-history", "item-today"], start=1):
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-1', ?, 8.2, ?, ?)
            """,
            (item_id, rank, json.dumps({"scores": numeric})),
        )
    conn.commit()
    conn.close()
    return db_path, today, history


def test_curated_without_date_keeps_latest_run_behavior(tmp_path: Path) -> None:
    db_path, _today, _history = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    data = client.get("/api/v1/curated").json()["data"]

    assert data["count"] == 2
    assert [item["id"] for item in data["items"]] == ["item-today", "item-history"]


def test_curated_without_date_returns_deduped_archive_page_and_latest_metadata(tmp_path: Path) -> None:
    db_path, today, history = _seed_db(tmp_path)
    older = history - timedelta(days=5)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES ('item-older', 's', 'https://example.com/older', 'Older Item', 'Ada', ?, ?, 'older', NULL, 'older', '{}')
        """,
        (f"{older.isoformat()}T07:00:00Z", f"{older.isoformat()}T07:00:00Z"),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-2', 'test.r1', '{}', 6.5, '[]', '["item-history","item-older"]', ?)
        """,
        (f"{today.isoformat()}T11:00:00Z",),
    )
    latest_reason = {
        "scores": {
            "relevance": 10,
            "density": 9,
            "recency": 8,
            "authority": 9,
            "engineering": 9,
            "reasoning": "最新精选理由：历史条目在第二轮再次入选，应使用第二轮元数据。",
        }
    }
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-2', 'item-history', 9.1, 7, ?)
        """,
        (json.dumps(latest_reason),),
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-2', 'item-older', 7.7, 8, ?)
        """,
        (json.dumps(latest_reason),),
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    first_page = client.get("/api/v1/curated", params={"limit": 2}).json()["data"]
    overflow = client.get("/api/v1/curated", params={"page": 99, "limit": 2}).json()["data"]
    daily_date = client.get("/api/v1/curated", params={"date": history.isoformat()}).json()["data"]

    assert first_page["page"] == 1
    assert first_page["limit"] == 2
    assert first_page["total"] == 3
    assert first_page["count"] == 2
    assert first_page["date"] == today.isoformat()
    assert [item["id"] for item in first_page["items"]] == ["item-today", "item-history"]
    assert first_page["items"][1]["weighted_score"] == 9.1
    assert first_page["items"][1]["rank"] == 7
    assert "第二轮元数据" in first_page["items"][1]["reasoning"]
    assert overflow["page"] == 2
    assert [item["id"] for item in overflow["items"]] == ["item-older"]
    assert daily_date["count"] == 1
    assert [item["id"] for item in daily_date["items"]] == ["item-history"]
    assert "total" not in daily_date


def test_curated_date_filters_items_by_published_day(tmp_path: Path) -> None:
    db_path, _today, history = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    data = client.get("/api/v1/curated", params={"date": history.isoformat()}).json()["data"]

    assert data["date"] == history.isoformat()
    assert data["count"] == 1
    assert [item["id"] for item in data["items"]] == ["item-history"]


def test_curated_date_includes_items_curated_only_in_older_runs(tmp_path: Path) -> None:
    db_path, today, history = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    # A newer run that re-curates only today's item — mirrors production, where each
    # run curates ~one day of fresh items and the history item lives only in run-1.
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-2', 'test.r1', '{}', 6.5, '[]', '["item-today"]', ?)
        """,
        (f"{today.isoformat()}T12:00:00Z",),
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-2', 'item-today', 8.5, 1, ?)
        """,
        (json.dumps({"scores": {}}),),
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    data = client.get("/api/v1/curated", params={"date": history.isoformat()}).json()["data"]

    # The history item is curated only in the older run-1, but the latest run is run-2.
    # The daily report must aggregate curated items across runs by published day.
    assert data["date"] == history.isoformat()
    assert data["count"] == 1
    assert [item["id"] for item in data["items"]] == ["item-history"]


def test_curated_invalid_and_future_dates_fallback_to_today(tmp_path: Path) -> None:
    db_path, today, _history = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    invalid = client.get("/api/v1/curated", params={"date": "invalid"}).json()["data"]
    future = client.get("/api/v1/curated", params={"date": "2099-01-01"}).json()["data"]

    assert invalid["date"] == today.isoformat()
    assert future["date"] == today.isoformat()
    assert [item["id"] for item in invalid["items"]] == ["item-today"]
    assert [item["id"] for item in future["items"]] == ["item-today"]


def test_curated_empty_history_date_returns_count_zero(tmp_path: Path) -> None:
    db_path, _today, _history = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    data = client.get("/api/v1/curated", params={"date": "2025-01-01"}).json()["data"]

    assert data["date"] == "2025-01-01"
    assert data["count"] == 0
    assert data["items"] == []
