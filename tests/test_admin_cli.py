from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from airadar import cli
from airadar.admin.alerts import AlertSignals
from airadar.admin.cost_audit import CostAuditReport
from airadar.db import migrate


def test_admin_alert_check_command_prints_ruleset_and_results(monkeypatch, capsys, tmp_path: Path) -> None:  # noqa: ANN001
    signals = AlertSignals(
        upstream_sample_size=1,
        upstream_error_rate=0.0,
        upstream_schema_error_rate=0.0,
        stage_error_rate={},
        stage_p95_latency_ms={},
        minutes_since_successful_pipeline=1,
        consecutive_skip_logs=0,
        server_error_rate=0.0,
        fetch_failed_ratio=0.0,
        items_today=300,
    )
    state_path = tmp_path / "alert-state.json"

    monkeypatch.setattr(cli, "collect_alert_signals", lambda **kwargs: signals)

    def fake_state_machine(collected: AlertSignals, **kwargs: object) -> dict[str, object]:
        assert collected is signals
        assert kwargs["state_path"] == str(state_path_arg)
        assert kwargs["serve_plist_path"] == cli.DEFAULT_SERVE_LAUNCH_AGENT_PATH
        return {
            "ruleset": ["A1", "A2", "A3", "A4", "A5", "A6"],
            "sent_count": 0,
            "sent": [
                {
                    "rule_id": "A1",
                    "type": "firing",
                    "send_result": {"skipped": False, "status_code": 200},
                },
                {
                    "rule_id": "A2",
                    "type": "firing",
                    "send_result": {"skipped": True, "reason": "FEISHU_GENERAL_ALERT_WEBHOOK is not set"},
                },
            ],
            "results": [
                {"rule_id": "A1", "firing": False, "title": "上游模型不可用", "detail": "ok"},
                {"rule_id": "A2", "firing": False, "title": "阶段错误率/耗时异常", "detail": "ok"},
                {"rule_id": "A3", "firing": False, "title": "网站用户侧异常", "detail": "ok"},
                {"rule_id": "A4", "firing": False, "title": "文章摄取骤降", "detail": "ok"},
                {
                    "rule_id": "A6",
                    "firing": False,
                    "evaluation_state": "in_progress",
                    "title": "LLM 近 24 小时成本突变",
                    "detail": "pipeline 运行中，暂缓评估",
                },
            ],
        }

    state_path_arg = state_path
    monkeypatch.setattr(cli, "run_alert_state_machine", fake_state_machine)
    monkeypatch.setattr(cli, "build_cost_report", lambda **kwargs: {})
    monkeypatch.setattr(
        cli,
        "run_pricing_notifications",
        lambda *args, **kwargs: {"sent": [], "cleared": []},
    )

    args = cli.build_parser().parse_args(["admin", "alert-check", "--state-path", str(state_path)])

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert "alert-check ruleset={A1,A2,A3,A4,A5,A6}" in output
    assert "sent=0" in output
    assert "send A1 firing sent status_code=200" in output
    assert "send A2 firing skipped reason=FEISHU_GENERAL_ALERT_WEBHOOK is not set" in output
    assert "A1 ok 上游模型不可用" in output
    assert "A4 ok 文章摄取骤降" in output
    assert "A6 in-progress LLM 近 24 小时成本突变" in output


def test_admin_db_checkpoint_command_prints_passive_result(monkeypatch, capsys, tmp_path: Path) -> None:  # noqa: ANN001
    db_path = tmp_path / "radar.db"
    seen: dict[str, str] = {}

    def fake_checkpoint(path: str):
        seen["path"] = path
        return cli.db.CheckpointResult(busy=0, log=42, checkpointed=41)

    monkeypatch.setattr(cli.db, "checkpoint_db", fake_checkpoint)

    args = cli.build_parser().parse_args(["admin", "db", "checkpoint", "--db-path", str(db_path)])

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert seen == {"path": str(db_path)}
    assert "checkpoint busy=0 log=42 checkpointed=41" in output


def test_admin_cost_audit_prints_reconciliation_and_returns_report_status(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        cli,
        "run_cost_audit",
        lambda **kwargs: CostAuditReport(
            passed=True,
            human_lines=("LLM cost reconciliation: CONSISTENT",),
            kv_lines=(
                "group stage=interpret provider=deepseek model=deepseek-v4-pro expected_usd=1.0 derived_usd=1.0 admin_usage_usd=1.0 PASS",
                "cost-audit PASS groups=1",
            ),
            json_payload={"consistent": True},
            resolution_unverified=(),
        ),
    )
    args = cli.build_parser().parse_args(["admin", "cost-audit"])

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert output == "LLM cost reconciliation: CONSISTENT\n"


def test_cost_report_cli_rejects_zero_window_and_mutually_exclusive_modes() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["admin", "cost-report", "--window-days", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["admin", "cost-report", "--send", "--dry-run"])


def test_performance_probe_cli_wires_runtime_paths_and_prints_samples(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    def fake_monitor(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "scope": "same-host provisional; not a regional SLO",
            "samples": [
                {
                    "journey": "homepage.first_card",
                    "vantage": "same_host_origin",
                    "value_ms": 123.0,
                    "load_class": "idle",
                    "provisional": True,
                }
            ],
            "alerts": {"sent_count": 0},
        }

    monkeypatch.setattr(cli, "run_journey_monitor", fake_monitor)
    sample_path = tmp_path / "samples.jsonl"
    args = cli.build_parser().parse_args(
        [
            "performance-probe",
            "--origin-url",
            "http://origin.invalid",
            "--public-url",
            "https://public.invalid",
            "--samples-path",
            str(sample_path),
        ]
    )

    assert cli._performance_probe(args) == 0
    assert captured["origin_url"] == "http://origin.invalid"
    assert captured["public_url"] == "https://public.invalid"
    assert captured["sample_path"] == sample_path
    output = capsys.readouterr().out
    assert "homepage.first_card" in output
    assert "load_class=idle" in output
    assert "provisional=true" in output
    assert "not a regional SLO" in output


def test_performance_remediate_cli_reads_perf_state_and_wires_worker_paths(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    def fake_remediate(config):  # noqa: ANN001
        captured["config"] = config
        return {
            "status": "candidate",
            "candidate_commit": "abc123",
            "worktree": str(tmp_path / "worker"),
            "summary_path": str(tmp_path / "summary.md"),
        }

    monkeypatch.setattr(cli, "remediate_confirmed_incident", fake_remediate)
    args = cli.build_parser().parse_args(
        [
            "performance-remediate",
            "--alert-state-path",
            str(tmp_path / "alert-state.json"),
            "--worker-root",
            str(tmp_path / "workers"),
            "--timeout-seconds",
            "120",
        ]
    )

    assert cli._performance_remediate(args) == 0
    config = captured["config"]
    assert config.alert_state_path == tmp_path / "alert-state.json"
    assert config.worker_root == tmp_path / "workers"
    assert config.timeout_seconds == 120
    output = capsys.readouterr().out
    assert "status=candidate" in output
    assert "candidate_commit=abc123" in output
    assert "summary_path=" in output


def test_serve_pre_migrated_db_skips_redundant_migration(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    db_path = tmp_path / "pre-migrated.db"
    migrate(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    monkeypatch.setenv("AI_RADAR_PRE_MIGRATED_DB", "1")
    sys.modules.pop("airadar.web.app", None)
    web_app = importlib.import_module("airadar.web.app")

    def forbidden_migration(path: object = None) -> None:
        raise AssertionError(f"pre-migrated serve attempted migration for {path}")

    monkeypatch.setattr(web_app.db, "migrate", forbidden_migration)
    args = cli.build_parser().parse_args(["serve", "--pre-migrated-db", "--port", "43210"])
    observed: dict[str, object] = {}

    def fake_uvicorn(app, **kwargs: object) -> None:  # noqa: ANN001
        observed["db_path"] = app.state.db_path
        observed.update(kwargs)

    monkeypatch.setattr(web_app.uvicorn, "run", fake_uvicorn)

    assert cli._serve(args) == 0
    assert observed["db_path"] == str(db_path)
    assert observed["port"] == 43210


def test_importing_web_app_does_not_migrate_default_database(monkeypatch) -> None:  # noqa: ANN001
    from airadar import db

    monkeypatch.delenv("AI_RADAR_DB", raising=False)
    monkeypatch.delenv("AI_RADAR_PRE_MIGRATED_DB", raising=False)

    def forbidden_migration(path: object = None) -> None:
        raise AssertionError(f"import attempted migration for {path}")

    monkeypatch.setattr(db, "migrate", forbidden_migration)
    sys.modules.pop("airadar.web.app", None)

    web_app = importlib.import_module("airadar.web.app")

    assert callable(web_app.create_app)
    assert not hasattr(web_app, "app")


def test_admin_wechat_avatar_refresh_command_updates_one_account(monkeypatch, capsys, tmp_path: Path) -> None:  # noqa: ANN001
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    seen: dict[str, str] = {}

    def fake_refresh(conn, account: str) -> str:
        seen["account"] = account
        seen["db_path"] = conn.execute("PRAGMA database_list").fetchone()[2]
        return "https://mmbiz.qpic.cn/avatar.png"

    monkeypatch.setattr(cli, "refresh_wechat_avatar", fake_refresh)

    args = cli.build_parser().parse_args(
        ["admin", "wechat-avatar", "refresh", "--account", "赛博禅心", "--db-path", str(db_path)]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert seen == {"account": "赛博禅心", "db_path": str(db_path)}
    assert "wechat-avatar account=赛博禅心 avatar_url=https://mmbiz.qpic.cn/avatar.png" in output
