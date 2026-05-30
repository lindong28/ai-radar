from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.web.app import create_app


def _seed_cross_midnight_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES ('s', 'Source', 'https://example.com/feed.xml', 'T1', 1, 'feed', 'https://example.com/', NULL, '{}', '2026-05-11T00:00:00Z')
        """
    )
    numeric = {
        "relevance": 9,
        "density": 8,
        "recency": 7,
        "authority": 10,
        "engineering": 8,
        "reasoning": "Useful cross-midnight item.",
    }
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-cross-midnight', 's', 'https://example.com/cross', 'Cross midnight',
          'Ada', '2026-05-11T18:00:00Z', '2026-05-11T18:01:00Z',
          'AI systems item', NULL, 'hash-cross', '{}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-cross-midnight', 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-11T18:02:00Z', NULL)
        """,
        (json.dumps(numeric),),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-1', 'test.r1', '{}', 6.5, '[1]', '["item-cross-midnight"]', '2026-05-11T18:03:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-1', 'item-cross-midnight', 8.2, 1, ?)
        """,
        (json.dumps({"scores": numeric}),),
    )
    conn.commit()
    conn.close()
    return db_path


def test_curated_date_filter_uses_asia_shanghai_published_day(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_cross_midnight_db(tmp_path)))

    shanghai_day = client.get("/api/v1/curated", params={"date": "2026-05-12"}).json()["data"]
    utc_day = client.get("/api/v1/curated", params={"date": "2026-05-11"}).json()["data"]

    assert [item["id"] for item in shanghai_day["items"]] == ["item-cross-midnight"]
    assert shanghai_day["date"] == "2026-05-12"
    assert utc_day["items"] == []
