from __future__ import annotations

import fcntl
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from test_admin_metrics import _seed_metrics_db
from test_pipeline_scheduler import _copy_pipeline_fixture
from test_wechat_browser_preflight import pipeline_success_evidence as pipeline_success_evidence

from airadar import cli, db
from airadar.admin.alerts import collect_alert_signals, evaluate_rules
from airadar.admin.metrics import collect_metrics
from airadar.pricing import PricingCatalog


def test_collector_uses_own_lock_and_preserves_main_activity(tmp_path):
    script, env = _copy_pipeline_fixture(tmp_path)
    env["AI_RADAR_DECOUPLED_INGESTION"] = "1"
    activity = tmp_path / ".pipeline.activity"
    activity.write_text("existing-main-generation")
    with (tmp_path / ".pipeline.flock").open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run([str(script), "--collect-only"], env=env, capture_output=True,
                                text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "run-calls.log").read_text().splitlines() == [
        "egress-preflight", "wechat-browser-preflight", "collect"]
    assert activity.read_text() == "existing-main-generation"
    assert len(list((tmp_path / "logs" / "collector").glob("pipeline-*.log"))) == 1


@pytest.mark.parametrize("failure", ["egress-preflight", "wechat-browser-preflight", "collect"])
def test_collector_failure_stops_before_ai(tmp_path, failure):
    script, env = _copy_pipeline_fixture(tmp_path, fail_stage=failure)
    env["AI_RADAR_DECOUPLED_INGESTION"] = "1"
    result = subprocess.run([str(script), "--collect-only"], env=env, capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 1
    calls = (tmp_path / "run-calls.log").read_text().splitlines()
    assert calls[-1] == failure and "score" not in calls and "fetch" not in calls


def test_processing_ingests_without_fetch_and_stops_on_bad_queue(tmp_path):
    script, env = _copy_pipeline_fixture(tmp_path, fail_stage="ingest")
    env["AI_RADAR_DECOUPLED_INGESTION"] = "1"
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert (tmp_path / "run-calls.log").read_text().splitlines() == [
        "egress-preflight", "wechat-browser-preflight", "ingest"]
    del env["FAIL_STAGE"]
    result = subprocess.run([str(script)], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    calls = (tmp_path / "run-calls.log").read_text().splitlines()
    assert "ingest" in calls and "fetch" not in calls and "collect" not in calls
    assert "score --since 24h" in calls


@pytest.mark.parametrize("receipt", ["current", "missing", "other-generation", "before-incident"])
def test_w1_requires_actual_consumed_success_for_this_generation(
        tmp_path, monkeypatch, pipeline_success_evidence, receipt):
    main = tmp_path / "main.db"
    monkeypatch.setenv("AI_RADAR_DB", str(main))
    db.migrate(main)
    log = Path(pipeline_success_evidence["pipeline_log"])
    log.write_text(log.read_text().replace("=== fetch ", "=== ingest "))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"W1": {"state": "firing", "since": "2026-09-16T00:00:00+00:00"}}))
    with db.get_conn(main) as conn:
        if receipt != "missing":
            generation = ("other" if receipt == "other-generation" else
                          "2ba69a1b-f8dd-4bd6-a27d-3494811fbc6c")
            completed = "2026-09-15T00:00:00+00:00" if receipt == "before-incident" else "2026-09-16T00:05:00+00:00"
            conn.execute("INSERT INTO ingestion_acks VALUES('queue',1,'hash',?,?)", (generation, completed))
            conn.commit()
    if receipt == "current":
        cli._verify_pipeline_success_evidence(**pipeline_success_evidence, alert_state_path=state)
    else:
        with pytest.raises(cli.PipelineSuccessNotVerified):
            cli._verify_pipeline_success_evidence(**pipeline_success_evidence, alert_state_path=state)


def test_a4_reads_real_collection_not_newer_ingest(tmp_path):
    main = _seed_metrics_db(tmp_path)
    logs = tmp_path / "logs"
    collector = logs / "collector"
    collector.mkdir(parents=True)
    (collector / "pipeline-20260602-080000.log").write_text(
        "[2026-06-02T08:00:00] === fetch START ===\n"
        "OK source1 fetched=3 inserted=2\nFAIL source2 timeout\n"
        "=== attempted=2 inserted=2 failed=1\n"
        "[2026-06-02T08:01:00] === fetch FAIL (exit 1) ===\n"
        "[2026-06-02T08:01:00] === PIPELINE DONE (failed=1; alert_recovery=NOT_RUN) ===\n")
    (logs / "pipeline-20260602-083000.log").write_text(
        "[2026-06-02T08:30:00] === ingest START ===\n"
        "ingest applied_batches=0 already_applied_batches=0 pending_batches=0\n"
        "[2026-06-02T08:30:01] === ingest OK ===\n"
        "[2026-06-02T08:30:02] === PIPELINE DONE (failed=0; alert_recovery=OK) ===\n")
    metrics = collect_metrics(db_path=main, pipeline_log_dir=logs, access_log_paths=[],
                              now=datetime.fromisoformat("2026-06-02T08:31:00+08:00"))
    latest = metrics["ingestion"]["latest_fetch"]
    assert latest["attempted"] == 2 and latest["failed"] == 1
    assert latest["completed_at"].isoformat() == "2026-06-02T08:01:00+08:00"
    assert metrics["pipeline"]["latest_run"]["name"] == "pipeline-20260602-083000.log"
    assert metrics["pipeline"]["stages"]["fetch"]["errors"] == 1


def test_a4_real_alert_entry_reads_collector_failure_and_recovery(tmp_path, monkeypatch):
    main = _seed_metrics_db(tmp_path)
    logs = tmp_path / "logs"
    collector = logs / "collector"
    collector.mkdir(parents=True)
    catalog = PricingCatalog({}, {}, "fresh", "fixture", None)
    monkeypatch.setattr("airadar.pricing._fetch_litellm_pricing",
                        lambda *a, **kw: pytest.fail("unexpected pricing refresh"))
    results = []
    for minute, failed in [(0, True), (15, False), (30, False)]:
        # Keep recovery rounds inside the existing 90-minute freshness window.
        stamp = f"2026-06-02T08:{minute:02d}:00"
        rows = "".join(
            f"FAIL x_source{i} HTTPStatusError: Client error '402 Payment Required'\n"
            if failed else f"OK x_source{i} fetched=0 inserted=0\n"
            for i in range(10)
        )
        (collector / f"pipeline-20260602-08{minute:02d}00.log").write_text(
            f"[{stamp}] === fetch START ===\n{rows}"
            f"=== attempted=10 inserted=0 failed={10 if failed else 0}\n"
            f"[{stamp}] === fetch {'FAIL (exit 1)' if failed else 'OK'} ===\n"
            f"[{stamp}] === PIPELINE DONE (failed={int(failed)}; alert_recovery=NOT_RUN) ===\n"
        )
        signals = collect_alert_signals(
            db_path=main, pipeline_log_dir=logs, access_log_paths=[],
            usage_db_path=tmp_path / "no-usage.db", pricing_catalog=catalog,
            pipeline_lock_path=tmp_path / "pipeline.lock",
            now=datetime.fromisoformat(f"2026-06-02T08:{minute:02d}:01+08:00"),
        )
        signals.items_today = 300
        assert signals.fetch_attempted == 10 and signals.fetch_evaluated
        assert signals.failed_by_status == ({402: 10} if failed else {})
        results.append(next(r for r in evaluate_rules(signals) if r.rule_id == "A4"))
    assert results[0].firing and results[0].severity == "page"
    assert not results[1].firing and results[1].evaluation_state == "in_progress"
    assert not results[2].firing and results[2].evaluation_state == "healthy"
