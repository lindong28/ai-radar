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


def test_curated_date_filters_items_by_published_day(tmp_path: Path) -> None:
    db_path, _today, history = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    data = client.get("/api/v1/curated", params={"date": history.isoformat()}).json()["data"]

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
