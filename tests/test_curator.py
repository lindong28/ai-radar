from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from airadar.curator.dedup import deduplicate_candidates
from airadar.curator.score import ScoredCandidate, weighted_score
from airadar.curator.select import curate
from airadar.curator.weights import DEFAULT_WEIGHTS, Weights, load_weights
from airadar.db import migrate


def test_weighted_score_applies_weights_and_tier_multiplier() -> None:
    numeric = {
        "relevance": 10.0,
        "density": 8.0,
        "recency": 6.0,
        "authority": 4.0,
        "engineering": 2.0,
    }

    base = weighted_score(numeric, DEFAULT_WEIGHTS, "T1.5")
    with_tier = weighted_score(numeric, DEFAULT_WEIGHTS, "T1")

    assert base == pytest.approx(6.6)
    assert with_tier == pytest.approx(8.25)


def test_load_weights_rejects_zero_or_negative_totals(tmp_path: Path) -> None:
    zero = tmp_path / "weights_zero.json"
    zero.write_text(json.dumps({"relevance": 0, "density": 0, "recency": 0, "authority": 0, "engineering": 0}))
    negative = tmp_path / "weights_negative.json"
    negative.write_text(json.dumps({"relevance": -1, "density": 1, "recency": 0, "authority": 0, "engineering": 0}))

    with pytest.raises(ValueError):
        load_weights(zero)
    with pytest.raises(ValueError):
        load_weights(negative)


def test_deduplicate_candidates_keeps_highest_score_by_hash_and_url() -> None:
    low = ScoredCandidate(1, "item-low", "h1", "https://example.com/a", "2026-05-08T00:00:00Z", 7.0, {})
    high_same_hash = ScoredCandidate(2, "item-high", "h1", "https://example.com/b", "2026-05-08T00:00:00Z", 8.0, {})
    low_same_url = ScoredCandidate(3, "item-url-low", "h2", "https://example.com/b", "2026-05-08T00:00:00Z", 7.5, {})
    unique = ScoredCandidate(4, "item-unique", "h3", "https://example.com/c", "2026-05-08T00:00:00Z", 6.5, {})

    deduped = deduplicate_candidates([low, high_same_hash, low_same_url, unique])

    assert [candidate.item_id for candidate in deduped] == ["item-high", "item-unique"]


def _setup_curator_db(tmp_path: Path, count: int = 35) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','S','https://example.com','T1.5',1,'{}','2026-05-08T00:00:00Z')"
    )
    for idx in range(count):
        item_id = f"item-{idx:02d}"
        score = 9.0 if idx < 28 else 6.49
        if idx == 29:
            score = 6.6
        if idx == 30:
            score = 6.5
        content_hash = f"h-{idx:02d}"
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, NULL, '2026-05-08T00:00:00Z', '2026-05-08T00:00:00Z', 'content', NULL, ?, '{}')
            """,
            (item_id, f"https://example.com/{content_hash}", item_id, content_hash),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T00:00:00Z', NULL)
            """,
            (
                item_id,
                json.dumps(
                    {
                        "relevance": score,
                        "density": score,
                        "recency": score,
                        "authority": score,
                        "engineering": score,
                        "reasoning": "ok",
                    }
                ),
            ),
        )
    conn.commit()
    return conn


def test_curate_applies_threshold_limit_sort_and_writes_run(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path)

    run = curate(conn, ruleset_version="test.r1", weights=Weights.default(), threshold=6.5, limit=30)

    assert run.threshold == 6.5
    assert len(run.output_curated_ids) == 30
    assert "item-34" not in run.output_curated_ids
    assert "item-30" in run.output_curated_ids
    rows = conn.execute(
        "SELECT rank, weighted_score FROM curated_items WHERE run_id=? ORDER BY rank",
        (run.id,),
    ).fetchall()
    assert len(rows) == 30
    assert [row[0] for row in rows] == list(range(1, 31))
    assert rows[0][1] >= rows[-1][1]


def test_curate_default_threshold_matches_prd_contract(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path, count=3)

    run = curate(conn, ruleset_version="test.r1")

    assert run.threshold == 6.5


def test_curate_default_limit_matches_reference_feed_depth(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path, count=45)
    for idx in range(45):
        item_id = f"item-{idx:02d}"
        conn.execute(
            """
            UPDATE item_evaluations
            SET numeric_json=?
            WHERE item_id=? AND stage='scoring'
            """,
            (
                json.dumps(
                    {
                        "relevance": 8.0,
                        "density": 8.0,
                        "recency": 8.0,
                        "authority": 8.0,
                        "engineering": 8.0,
                        "reasoning": "ok",
                    }
                ),
                item_id,
            ),
        )
    conn.commit()

    run = curate(conn, ruleset_version="test.r1")

    assert len(run.output_curated_ids) == 40


def test_curate_prioritizes_latest_visible_date_for_reference_parity(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','S','https://example.com','T1.5',1,'{}','2026-05-08T00:00:00Z')"
    )
    fresh_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    old_at = fresh_at - timedelta(days=2)

    def insert_item(item_id: str, published_at: datetime, score: float) -> None:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, NULL, ?, ?, 'content', NULL, ?, '{}')
            """,
            (
                item_id,
                f"https://example.com/{item_id}",
                item_id,
                published_at.isoformat().replace("+00:00", "Z"),
                published_at.isoformat().replace("+00:00", "Z"),
                f"h-{item_id}",
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
            (
                item_id,
                json.dumps(
                    {
                        "relevance": score,
                        "density": score,
                        "recency": score,
                        "authority": score,
                        "engineering": score,
                        "reasoning": "ok",
                    }
                ),
                published_at.isoformat().replace("+00:00", "Z"),
            ),
        )

    for idx in range(45):
        insert_item(f"old-{idx:02d}", old_at - timedelta(minutes=idx), 9.0)
    for idx in range(36):
        insert_item(f"fresh-{idx:02d}", fresh_at - timedelta(minutes=idx), 4.2)
    conn.commit()

    run = curate(conn, ruleset_version="test.r1")

    assert len(run.output_curated_ids) == 40
    assert sum(item_id.startswith("fresh-") for item_id in run.output_curated_ids) == 36
    assert sum(item_id.startswith("old-") for item_id in run.output_curated_ids) == 4


def test_curate_with_high_threshold_returns_empty_run(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path, count=3)

    run = curate(conn, ruleset_version="test.r1", threshold=9.99)

    assert run.output_curated_ids == []
    assert conn.execute("SELECT COUNT(*) FROM curated_items WHERE run_id=?", (run.id,)).fetchone()[0] == 0
