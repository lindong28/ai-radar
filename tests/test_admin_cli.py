from __future__ import annotations

from pathlib import Path

from airadar import cli
from airadar.admin.alerts import AlertSignals


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
        health_failures=0,
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
