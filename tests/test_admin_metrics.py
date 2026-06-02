from __future__ import annotations

import sqlite3
import textwrap
from datetime import datetime
from pathlib import Path

from airadar.admin.metrics import collect_metrics
from airadar.db import migrate


def _seed_metrics_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','Source','https://example.com/feed','T1',1,'{}','2026-06-01T00:00:00Z')"
    )
    for item_id, fetched_at in [
        ("item-today-1", "2026-06-01T16:30:00Z"),
        ("item-today-2", "2026-06-02T00:30:00Z"),
        ("item-yesterday", "2026-06-01T12:00:00Z"),
    ]:
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
                fetched_at,
                fetched_at,
                f"hash-{item_id}",
            ),
        )
    eval_rows = [
        ("item-today-1", "prefilter", 100, 0.0, None, "2026-06-01T16:35:00Z"),
        ("item-today-2", "prefilter", 300, 0.0, None, "2026-06-01T16:36:00Z"),
        ("item-yesterday", "prefilter", 700, 0.0, "provider timeout", "2026-06-01T16:37:00Z"),
        ("item-today-1", "scoring", 1000, 0.01, None, "2026-06-01T16:40:00Z"),
        ("item-today-2", "scoring", 2000, 0.02, None, "2026-06-01T16:41:00Z"),
        ("item-yesterday", "scoring", 3000, 0.03, "all DeepSeek provider endpoints failed", "2026-06-01T16:42:00Z"),
        ("item-today-1", "enrich", 5000, 0.04, None, "2026-06-01T16:50:00Z"),
    ]
    for item_id, stage, latency_ms, cost_usd, error, evaluated_at in eval_rows:
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, ?, 'test.r1', 'fake', '{}', '{}', '{}', ?, ?, ?, ?)
            """,
            (item_id, stage, latency_ms, cost_usd, evaluated_at, error),
        )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-today', 'test.r1', '{}', 6.5, '[]', '[]', '2026-06-01T17:20:00Z')
        """
    )
    conn.commit()
    return db_path


def test_collect_metrics_combines_db_and_pipeline_logs_with_score_mapping(tmp_path: Path) -> None:
    db_path = _seed_metrics_db(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "pipeline-20260602-074500.log").write_text(
        textwrap.dedent(
            """\
            [2026-06-02T07:45:00] === fetch START ===
            OK source_a fetched=10 inserted=3
            FAIL source_b TimeoutError: boom
            === attempted=2 inserted=3 failed=1
            [2026-06-02T07:46:00] === fetch OK ===
            [2026-06-02T07:46:00] === score START ===
            score processed=3 errors=1
            [2026-06-02T07:48:00] === score OK ===
            [2026-06-02T07:48:00] === curate START ===
            curate run_id=run-today selected=40 threshold=6.5
            [2026-06-02T07:49:30] === curate OK ===
            [2026-06-02T07:49:30] === PIPELINE DONE (failed=0) ===
            """
        ),
        encoding="utf-8",
    )

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=log_dir,
        access_log_paths=[],
        now=datetime.fromisoformat("2026-06-02T08:00:00+08:00"),
    )

    assert metrics["timezone"] == "Asia/Shanghai"
    assert metrics["ingestion"]["items_today"] == 2
    assert metrics["ingestion"]["curation_runs_today"] == 1
    assert metrics["ingestion"]["latest_fetch"]["attempted"] == 2
    assert metrics["ingestion"]["latest_fetch"]["inserted"] == 3
    assert metrics["ingestion"]["latest_fetch"]["failed"] == 1
    assert metrics["ingestion"]["latest_fetch"]["ok_sources"] == 1
    assert metrics["ingestion"]["latest_fetch"]["failed_sources"] == ["source_b"]

    stages = metrics["pipeline"]["stages"]
    assert metrics["pipeline"]["latest_run"]["status"] == "done"
    assert stages["prefilter"]["processed"] == 3
    assert stages["prefilter"]["errors"] == 1
    assert stages["prefilter"]["error_rate"] == 1 / 3
    assert stages["prefilter"]["p50_latency_ms"] == 300
    assert stages["prefilter"]["p95_latency_ms"] == 700
    assert stages["scoring"]["processed"] == 3
    assert stages["scoring"]["errors"] == 1
    assert stages["scoring"]["cost_usd"] == 0.06
    assert stages["scoring"]["latest_run_status"] == "ok"
    assert stages["scoring"]["latest_run_duration_ms"] == 120000
    assert stages["curate"]["latest_run_duration_ms"] == 90000
