from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from airadar.db import migrate
from airadar.eval.distribution import display_score, score_distribution


def test_display_score_matches_frontend_positive_rounding() -> None:
    assert display_score(7.04) == 70
    assert display_score(7.05) == 71


def test_score_distribution_asserts_v5_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-a', 'test.r1', '{}', 7.0, '[]', ?, '2026-05-12T00:00:00Z')
        """,
        (json.dumps([f"item-{idx}" for idx in range(12)]),),
    )
    scores = [9.8, 9.4, 9.1, 8.7, 8.3, 7.9, 7.6, 7.2, 6.9, 6.5, 6.0, 5.8]
    for rank, score in enumerate(scores, start=1):
        item_id = f"item-{rank}"
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, NULL, '2026-05-12T00:00:00Z', '2026-05-12T00:00:00Z', 'content', NULL, ?, '{}')
            """,
            (item_id, f"https://example.com/{rank}", item_id, f"h-{rank}"),
        )
        conn.execute(
            "INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json) VALUES ('run-a', ?, ?, ?, '{}')",
            (item_id, score, rank),
        )
    conn.commit()

    distribution = score_distribution(conn, "run-a")

    assert distribution.span >= 20
    assert distribution.stdev >= 8
    assert distribution.top10_unique_count == 10
    assert distribution.passes_v5 is True
