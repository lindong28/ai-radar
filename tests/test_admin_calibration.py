from __future__ import annotations

import sqlite3
import textwrap
from datetime import datetime
from pathlib import Path

from airadar.admin.calibration import calibrate_thresholds
from airadar.db import migrate


def _seed_calibration_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','Source','https://example.com/feed','T1',1,'{}','2026-06-01T00:00:00Z')"
    )
    for index in range(1, 5):
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, NULL, '2026-06-01T16:00:00Z', '2026-06-01T16:00:00Z', 'content', NULL, ?, '{}')
            """,
            (f"item-{index}", f"https://example.com/{index}", f"Item {index}", f"hash-{index}"),
        )
    eval_rows = [
        ("prefilter", 100, None),
        ("prefilter", 200, "schema validation failed"),
        ("scoring", 1000, None),
        ("scoring", 3000, "all DeepSeek provider endpoints failed: 404 InvalidEndpoint"),
        ("enrich", 5000, None),
    ]
    for index, (stage, latency_ms, error) in enumerate(eval_rows, start=1):
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, ?, 'test.r1', 'fake', '{}', '{}', '{}', ?, 0.01, '2026-06-01T16:30:00Z', ?)
            """,
            (f"item-{min(index, 4)}", stage, latency_ms, error),
        )
    conn.commit()
    return db_path


def test_calibrate_thresholds_derives_baselines_for_all_alerts(tmp_path: Path) -> None:
    db_path = _seed_calibration_db(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "pipeline-20260602-074500.log").write_text(
        textwrap.dedent(
            """\
            [2026-06-02T07:45:00] === fetch START ===
            OK source_a fetched=10 inserted=6
            FAIL source_b TimeoutError: boom
            === attempted=2 inserted=6 failed=1
            [2026-06-02T07:46:00] === fetch OK ===
            [2026-06-02T07:46:00] === curate START ===
            curate run_id=run selected=40 threshold=6.5
            [2026-06-02T07:47:00] === curate OK ===
            [2026-06-02T07:47:00] === PIPELINE DONE (failed=0) ===
            """
        ),
        encoding="utf-8",
    )
    access_log = tmp_path / "serve-access.log"
    access_log.write_text(
        "\n".join(
            [
                'INFO:     82.152.91.79:0 - "GET / HTTP/1.1" 200 OK "Mozilla/5.0"',
                'INFO:     82.152.91.80:0 - "GET /all HTTP/1.1" 500 Internal Server Error "Mozilla/5.0"',
                'INFO:     66.249.66.1:0 - "GET /robots.txt HTTP/1.1" 404 Not Found "Googlebot/2.1"',
            ]
        ),
        encoding="utf-8",
    )

    calibration = calibrate_thresholds(
        db_path=db_path,
        pipeline_log_dir=log_dir,
        access_log_paths=[access_log],
        now=datetime.fromisoformat("2026-06-02T08:00:00+08:00"),
        days=7,
    )

    assert calibration["baselines"]["a1"]["upstream_error_rate"] == 0.2
    assert calibration["baselines"]["a2"]["stages"]["scoring"]["p95_latency_ms"] == 3000
    assert calibration["baselines"]["a3"]["server_error_rate"] == 0.5
    assert calibration["baselines"]["a4"]["fetch_failed_ratio_avg"] == 0.5
    assert calibration["baselines"]["a4"]["successful_runs"] == 1
    assert calibration["thresholds"]["a1"]["upstream_error_rate"] == 0.5
    assert calibration["thresholds"]["a2"]["stage_p95_latency_ms"]["scoring"] == 9000
    assert calibration["thresholds"]["a3"]["server_error_rate"] == 0.5
    assert calibration["thresholds"]["a4"]["daily_inserted_floor"] == 1
