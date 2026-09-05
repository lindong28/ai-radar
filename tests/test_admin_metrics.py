from __future__ import annotations

import shutil
import sqlite3
import textwrap
from datetime import datetime
from pathlib import Path

from airadar.admin.alerts import AlertSignals, collect_alert_signals, evaluate_rules
from airadar.admin.metrics import collect_metrics
from airadar.db import migrate

PIPELINE_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pipeline_logs"


def _copy_pipeline_fixture(source_name: str, log_dir: Path, target_name: str | None = None) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / (target_name or source_name)
    shutil.copyfile(PIPELINE_FIXTURE_DIR / source_name, target)
    return target


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
    assert stages["prefilter"]["p95_latency_ms"] is None
    assert stages["scoring"]["processed"] == 3
    assert stages["scoring"]["errors"] == 1
    assert "cost_usd" not in stages["scoring"]
    assert stages["scoring"]["latest_run_status"] == "ok"
    assert stages["scoring"]["latest_run_duration_ms"] == 120000
    assert stages["curate"]["latest_run_duration_ms"] == 90000


def test_latest_fetch_replays_last_complete_round_and_buckets_http_statuses(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    log_dir = tmp_path / "logs"
    _copy_pipeline_fixture("pipeline-20260904-053000.txt", log_dir, "pipeline-20260904-053000.log")
    _copy_pipeline_fixture(
        "pipeline-20260904-054500-before-summary.txt",
        log_dir,
        "pipeline-20260904-054500.log",
    )
    _copy_pipeline_fixture("pipeline-20260904-190000.txt", log_dir, "pipeline-20260904-190000.log")

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=log_dir,
        access_log_paths=[],
        now=datetime.fromisoformat("2026-09-04T06:00:00+08:00"),
    )

    latest = metrics["ingestion"]["latest_fetch"]
    assert latest is not None
    assert latest["summary_seen"] is True
    assert latest["attempted"] == 163
    assert latest["failed"] == 111
    assert latest["failed_by_status"][402] == 109
    assert len(latest["failed_sources_by_status"][402]) == 109
    assert latest["completed_at"] == datetime.fromisoformat("2026-09-04T05:40:51+08:00")
    assert latest["stale_minutes"] == 19
    assert latest["stale"] is False
    assert latest["stale_reason"] is None
    assert latest["stale_limit_minutes"] == 90

    skewed = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=log_dir,
        access_log_paths=[],
        now=datetime.fromisoformat("2026-09-04T05:30:00+08:00"),
    )
    skewed_ingestion = skewed["ingestion"]
    assert isinstance(skewed_ingestion, dict)
    skewed_latest = skewed_ingestion["latest_fetch"]
    assert isinstance(skewed_latest, dict)
    # completed_at (05:40:51) is 10+ minutes after "now": a future round is not fresh.
    assert skewed_latest["stale_minutes"] == 0
    assert skewed_latest["stale"] is True
    assert skewed_latest["stale_reason"] == "future_timestamp"
    assert metrics["ingestion"]["recent_complete_fetches"] == [
        {
            "completed_at": datetime.fromisoformat("2026-09-04T05:40:51+08:00"),
            "attempted": 163,
            "failed_by_status": {402: 109},
        }
    ]


def test_fetch_summary_without_later_terminal_line_is_not_complete(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    log_dir = tmp_path / "logs"
    log = _copy_pipeline_fixture(
        "pipeline-20260904-054500-before-summary.txt",
        log_dir,
        "pipeline-20260904-054500.log",
    )
    with log.open("a", encoding="utf-8") as handle:
        handle.write("=== attempted=163 inserted=0 failed=111\n")

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=log_dir,
        access_log_paths=[],
        now=datetime.fromisoformat("2026-09-04T06:00:00+08:00"),
    )

    latest_run = metrics["pipeline"]["latest_run"]
    assert latest_run["fetch"]["summary_seen"] is True
    assert latest_run["fetch"]["completed_at"] is None
    assert metrics["ingestion"]["latest_fetch"] is None
    assert metrics["ingestion"]["recent_complete_fetches"] == []


def test_later_incomplete_and_preflight_fail_rounds_do_not_replace_stale_complete_fetch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    log_dir = tmp_path / "logs"
    _copy_pipeline_fixture("pipeline-20260904-053000.txt", log_dir, "pipeline-20260904-053000.log")
    _copy_pipeline_fixture(
        "pipeline-20260904-054500-before-summary.txt",
        log_dir,
        "pipeline-20260904-054500.log",
    )
    _copy_pipeline_fixture("pipeline-20260904-190000.txt", log_dir, "pipeline-20260904-190000.log")

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=log_dir,
        access_log_paths=[],
        now=datetime.fromisoformat("2026-09-04T19:05:00+08:00"),
    )

    latest = metrics["ingestion"]["latest_fetch"]
    assert latest is not None
    assert latest["completed_at"] == datetime.fromisoformat("2026-09-04T05:40:51+08:00")
    assert latest["stale_minutes"] == 804
    assert latest["stale"] is True


def _a2_result_from_metrics(metrics: dict[str, object]):
    pipeline = metrics["pipeline"]
    assert isinstance(pipeline, dict)
    stages = pipeline["stages"]
    assert isinstance(stages, dict)
    stage_error_rate: dict[str, float] = {}
    stage_p95_latency_ms: dict[str, int] = {}
    for stage in ("prefilter", "scoring", "enrich"):
        row = stages[stage]
        assert isinstance(row, dict)
        stage_error_rate[stage] = float(row["error_rate"] or 0.0)
        stage_p95_latency_ms[stage] = int(row["p95_latency_ms"] or 0)
    signals = AlertSignals(
        upstream_sample_size=0,
        upstream_error_rate=0.0,
        upstream_schema_error_rate=0.0,
        stage_error_rate=stage_error_rate,
        stage_p95_latency_ms=stage_p95_latency_ms,
        minutes_since_successful_pipeline=10,
        consecutive_skip_logs=0,
        server_error_rate=0.0,
        fetch_failed_ratio=0.0,
        items_today=300,
        stage_sample_count={
            stage: int(stages[stage]["processed"] or 0)
            for stage in ("prefilter", "scoring", "enrich")
        },
        server_pv=0,
    )
    return evaluate_rules(signals)[1]


def test_prefilter_p95_uses_recent_sliding_window_and_auto_clears(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    for item_id, latency_ms, evaluated_at in [
        # True-hang sample above the 25s prefilter breakage floor — within the 2h
        # window it drives A2 firing; once it ages out, A2 must auto-clear.
        ("slow-lock-sample", 26_000, "2026-06-01T16:30:00Z"),  # 2026-06-02 00:30 Shanghai
        ("fast-recovered-1", 1_000, "2026-06-01T18:10:00Z"),
        ("fast-recovered-2", 1_100, "2026-06-01T18:20:00Z"),
        ("fast-recovered-3", 1_200, "2026-06-01T18:30:00Z"),
    ]:
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'prefilter', 'test.r1', 'fake', '{}', '{}', '{}', ?, 0, ?, NULL)
            """,
            (item_id, latency_ms, evaluated_at),
        )
    conn.commit()

    incident = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[],
        now=datetime.fromisoformat("2026-06-02T01:00:00+08:00"),
    )
    recovered = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[],
        now=datetime.fromisoformat("2026-06-02T03:00:00+08:00"),
    )

    incident_prefilter = incident["pipeline"]["stages"]["prefilter"]
    recovered_prefilter = recovered["pipeline"]["stages"]["prefilter"]
    assert incident_prefilter["p95_latency_ms"] == 26_000
    assert recovered_prefilter["p95_latency_ms"] == 1_200
    assert _a2_result_from_metrics(incident).firing is True
    assert _a2_result_from_metrics(recovered).firing is False


def test_scoring_and_enrich_p95_use_recent_sliding_window_and_auto_clear(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    for stage, slow_latency_ms, recovered_latency_ms in [
        ("scoring", 20_000, 2_000),
        ("enrich", 24_122, 11_465),
    ]:
        for item_id, latency_ms, evaluated_at in [
            (f"{stage}-old-stall", slow_latency_ms, "2026-06-01T16:30:00Z"),
            (f"{stage}-fast-1", recovered_latency_ms - 100, "2026-06-01T18:10:00Z"),
            (f"{stage}-fast-2", recovered_latency_ms, "2026-06-01T18:20:00Z"),
        ]:
            conn.execute(
                """
                INSERT INTO item_evaluations (
                  item_id, stage, ruleset_version, model_id, input_json, output_json,
                  numeric_json, latency_ms, cost_usd, evaluated_at, error
                )
                VALUES (?, ?, 'test.r1', 'fake', '{}', '{}', '{}', ?, 0, ?, NULL)
                """,
                (item_id, stage, latency_ms, evaluated_at),
            )
    conn.commit()

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[],
        now=datetime.fromisoformat("2026-06-02T03:00:00+08:00"),
    )

    stages = metrics["pipeline"]["stages"]
    assert stages["scoring"]["processed"] == 3
    assert stages["scoring"]["p95_latency_ms"] == 2_000
    assert stages["enrich"]["processed"] == 3
    assert stages["enrich"]["p95_latency_ms"] == 11_465
    assert _a2_result_from_metrics(metrics).firing is False


def test_alert_windows_exclude_outside_and_unprovable_samples_without_changing_dashboard_metrics(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    for item_id, latency_ms, error, evaluated_at in [
        ("outside", 26_000, "provider timeout", "2026-06-02T07:44:00+08:00"),
        ("inside-error", 1_000, "provider timeout", "2026-06-02T07:46:00+08:00"),
        ("inside-ok-1", 1_100, None, "2026-06-02T07:50:00+08:00"),
        ("inside-ok-2", 1_200, None, "2026-06-02T07:55:00+08:00"),
    ]:
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'prefilter', 'test.r1', 'fake', '{}', '{}', '{}', ?, 0.01, ?, ?)
            """,
            (item_id, latency_ms, evaluated_at, error),
        )
    conn.commit()
    conn.close()

    dashboard_access_log = tmp_path / "dashboard-access.log"
    dashboard_access_log.write_text(
        "\n".join(
            [
                '2026-06-02T07:44:00+08:00 INFO: 10.0.0.1:1 - "GET / HTTP/1.1" 500 Error "Mozilla/5.0"',
                '2026-06-02T07:46:00+08:00 INFO: 10.0.0.2:2 - "GET / HTTP/1.1" 500 Error "Mozilla/5.0"',
                '2026-06-02T07:50:00+08:00 INFO: 10.0.0.3:3 - "GET /all HTTP/1.1" 200 OK "Mozilla/5.0"',
                'INFO: 10.0.0.4:4 - "GET /about HTTP/1.1" 200 OK "Mozilla/5.0"',
            ]
        ),
        encoding="utf-8",
    )
    alert_access_log = tmp_path / "alert-access.log"
    alert_access_log.write_text(
        dashboard_access_log.read_text(encoding="utf-8")
        + '\n2026-99-99T07:52:00+08:00 INFO: 10.0.0.5:5 - "GET / HTTP/1.1" 500 Error "Mozilla/5.0"',
        encoding="utf-8",
    )
    now = datetime.fromisoformat("2026-06-02T08:00:00+08:00")
    window_start = datetime.fromisoformat("2026-06-02T07:45:00+08:00")

    dashboard = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[dashboard_access_log],
        now=now,
    )
    alert_window = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[alert_access_log],
        now=now,
        stage_since=window_start,
        access_since=window_start,
    )
    signals = collect_alert_signals(
        db_path=db_path,
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[alert_access_log],
        now=now,
    )

    dashboard_stage = dashboard["pipeline"]["stages"]["prefilter"]
    alert_stage = alert_window["pipeline"]["stages"]["prefilter"]
    assert dashboard_stage["processed"] == 4
    assert dashboard_stage["errors"] == 2
    assert dashboard_stage["error_rate"] == 2 / 4
    assert dashboard_stage["p50_latency_ms"] == 1_150
    assert dashboard_stage["p95_latency_ms"] == 26_000
    assert alert_stage["processed"] == 3
    assert alert_stage["errors"] == 1
    assert alert_stage["error_rate"] == 1 / 3
    assert alert_stage["p50_latency_ms"] == dashboard_stage["p50_latency_ms"]
    assert alert_stage["p95_latency_ms"] == dashboard_stage["p95_latency_ms"]
    assert "cost_usd" not in dashboard_stage
    assert "cost_usd" not in alert_stage

    assert dashboard["users"]["pv"] == 4
    assert dashboard["users"]["status_counts"] == {200: 2, 500: 2}
    assert alert_window["users"]["pv"] == 2
    assert alert_window["users"]["status_counts"] == {200: 1, 500: 1}
    assert signals.stage_sample_count["prefilter"] == 3
    assert signals.stage_error_rate["prefilter"] == 1 / 3
    assert signals.server_pv == 2
    assert signals.server_error_rate == 1 / 2
