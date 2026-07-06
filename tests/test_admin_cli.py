from __future__ import annotations

from pathlib import Path

from airadar import cli
from airadar.admin.alerts import AlertSignals
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

    monkeypatch.setattr(cli, "collect_alert_signals", lambda: signals)

    def fake_state_machine(collected: AlertSignals, *, state_path: str) -> dict[str, object]:
        assert collected is signals
        assert state_path == str(state_path_arg)
        return {
            "ruleset": ["A1", "A2", "A3", "A4"],
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
            ],
        }

    state_path_arg = state_path
    monkeypatch.setattr(cli, "run_alert_state_machine", fake_state_machine)

    args = cli.build_parser().parse_args(["admin", "alert-check", "--state-path", str(state_path)])

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert "alert-check ruleset={A1,A2,A3,A4}" in output
    assert "sent=0" in output
    assert "send A1 firing sent status_code=200" in output
    assert "send A2 firing skipped reason=FEISHU_GENERAL_ALERT_WEBHOOK is not set" in output
    assert "A1 ok 上游模型不可用" in output
    assert "A4 ok 文章摄取骤降" in output


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
