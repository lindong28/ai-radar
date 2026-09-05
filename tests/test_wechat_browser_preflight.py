from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from airadar import cli
from airadar.admin.alerts import AlertRuleResult, run_alert_results_state_machine
from airadar.admin.metrics import _load_alert_summary
from airadar.fetcher.wechat import (
    WeChatBrowserNotVerified,
    WeChatBrowserUnavailable,
    inspect_wechat_browser_executable,
)


@pytest.fixture
def pipeline_success_evidence(tmp_path: Path) -> Iterator[dict[str, object]]:
    generation = "2ba69a1b-f8dd-4bd6-a27d-3494811fbc6c"
    lock_path = tmp_path / ".pipeline.flock"
    lock_handle = lock_path.open("a", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    lock_path.with_suffix(".activity").write_text(f"{generation}\n", encoding="utf-8")
    capability_path = tmp_path / ".pipeline.capability"
    capability_path.write_text(f"{generation}\n", encoding="utf-8")
    capability_handle = capability_path.open("r+", encoding="utf-8")
    capability_path.unlink()
    pipeline_log = tmp_path / "pipeline-20260904-080000.log"
    controls = [
        "=== pipeline RUN generation=" + generation + " ===",
        "=== egress preflight START ===",
        "=== egress preflight OK ===",
        "=== wechat_browser_preflight START ===",
        "=== wechat_browser_preflight OK ===",
        "=== fetch START ===",
        "stage output is allowed",
        "=== fetch OK ===",
        "=== prefilter START ===",
        "=== prefilter OK ===",
        "=== score START ===",
        "=== score OK ===",
        "=== enrich START ===",
        "=== enrich OK ===",
        "=== curate START ===",
        "=== curate OK ===",
        "=== interpret START ===",
        "=== interpret OK ===",
        "=== wechat_browser_preflight_resolve START ===",
    ]
    pipeline_log.write_text("\n".join(controls) + "\n", encoding="utf-8")
    try:
        yield {
            "pipeline_log": pipeline_log,
            "pipeline_lock_path": lock_path,
            "pipeline_lock_fd": lock_handle.fileno(),
            "pipeline_capability_fd": capability_handle.fileno(),
        }
    finally:
        capability_handle.close()
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def test_missing_browser_path_fails_with_install_action_and_alert(tmp_path: Path) -> None:
    empty_browser_root = tmp_path / "empty-playwright-browsers"
    empty_browser_root.mkdir()
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    notify_calls = tmp_path / "notify-calls.jsonl"
    fake_sender = fake_bin / "im-notify"
    fake_sender.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['FAKE_NOTIFY_CALLS'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')\n",
        encoding="utf-8",
    )
    fake_sender.chmod(0o755)
    env = {
        **os.environ,
        "AI_RADAR_DB": str(tmp_path / "radar.db"),
        "FAKE_NOTIFY_CALLS": str(notify_calls),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "PLAYWRIGHT_BROWSERS_PATH": str(empty_browser_root),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "airadar.cli",
            "wechat-browser-preflight",
            "--state-path",
            str(state_path),
            "--event-path",
            str(event_path),
        ],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        timeout=30,
    )

    output = completed.stdout
    assert completed.returncode == 1, output + completed.stderr
    assert "WeChat browser preflight: UNAVAILABLE" in output
    assert "Details: status=unavailable" in output
    assert "uv run playwright install chromium" in output
    assert "Impact: scheduled pipeline will stop before fetch" in output
    assert "Alert: accepted" in output
    alert_calls = [json.loads(line) for line in notify_calls.read_text(encoding="utf-8").splitlines()]
    assert len(alert_calls) == 1
    assert "--alert" in alert_calls[0]
    message = alert_calls[0][-1]
    assert "W1 微信全文浏览器依赖不可用" in message
    assert "故障类别：" not in message
    assert "status=unavailable" not in message
    assert str(empty_browser_root) not in message
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["W1"]["state"] == "firing"
    assert state["W1"]["announced"] is True
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["rule_id"] == "W1"
    assert event["type"] == "firing"
    assert str(empty_browser_root) in event["values"]["reason"]


def test_present_browser_path_sends_no_alert_or_state_transition(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    executable = tmp_path / "chromium"
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(cli, "inspect_wechat_browser_executable", lambda: executable)
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"

    exit_code = cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        send=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("healthy preflight must not notify")
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "WeChat browser preflight: PRESENT" in output
    assert f"Details: status=present executable={executable}" in output
    assert (
        "Scope: expected executable only; browser launch, network, and WeChat full-text fetch "
        "were not checked"
    ) in output
    assert "Action: none" in output
    assert "Alert: not sent" in output
    assert not state_path.exists()
    assert not event_path.exists()


def test_resolve_without_pipeline_evidence_keeps_w1_open(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(WeChatBrowserUnavailable("missing")),
    )
    assert cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        send=lambda *_args, **_kwargs: {"skipped": False},
    ) == 1

    executable = tmp_path / "chromium"
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(cli, "inspect_wechat_browser_executable", lambda: executable)
    deliveries: list[str] = []

    exit_code = cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        resolve_after_pipeline=True,
        send=lambda text, **_kwargs: deliveries.append(text) or {"skipped": False},
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "NOT VERIFIED" in output
    assert "pipeline success evidence" in output
    assert not any("✅ W1" in text for text in deliveries)
    assert json.loads(state_path.read_text(encoding="utf-8"))["W1"]["state"] == "firing"


@pytest.mark.parametrize(
    "invalid_evidence",
    ["activity-mismatch", "duplicate-generation", "failed-stage", "out-of-order", "done"],
)
def test_resolve_rejects_invalid_pipeline_success_evidence_before_browser_check(
    monkeypatch,
    capsys,
    pipeline_success_evidence: dict[str, object],
    invalid_evidence: str,
) -> None:  # noqa: ANN001
    pipeline_log = Path(pipeline_success_evidence["pipeline_log"])
    lock_path = Path(pipeline_success_evidence["pipeline_lock_path"])
    if invalid_evidence == "activity-mismatch":
        lock_path.with_suffix(".activity").write_text(
            "51da3049-d3fe-45bd-a077-3001ca565f99\n", encoding="utf-8"
        )
    elif invalid_evidence == "duplicate-generation":
        generation_line = pipeline_log.read_text(encoding="utf-8").splitlines()[0]
        pipeline_log.write_text(
            pipeline_log.read_text(encoding="utf-8") + generation_line + "\n",
            encoding="utf-8",
        )
    elif invalid_evidence == "failed-stage":
        pipeline_log.write_text(
            pipeline_log.read_text(encoding="utf-8").replace(
                "=== fetch OK ===", "=== fetch FAIL (exit 1) ==="
            ),
            encoding="utf-8",
        )
    elif invalid_evidence == "out-of-order":
        text = pipeline_log.read_text(encoding="utf-8")
        pipeline_log.write_text(
            text.replace(
                "=== fetch START ===\nstage output is allowed\n=== fetch OK ===",
                "=== fetch OK ===\nstage output is allowed\n=== fetch START ===",
            ),
            encoding="utf-8",
        )
    else:
        pipeline_log.write_text(
            pipeline_log.read_text(encoding="utf-8")
            + "=== PIPELINE DONE (failed=0; alert_recovery=OK) ===\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(AssertionError("browser check must not run")),
    )

    exit_code = cli._wechat_browser_preflight(
        state_path=pipeline_log.with_name("state.json"),
        event_path=pipeline_log.with_name("events.jsonl"),
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("state transition must not run")
        ),
    )

    assert exit_code == 2
    assert "pipeline success evidence rejected" in capsys.readouterr().out


def test_resolve_rejects_old_crash_log_without_active_pipeline_lock(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    generation = "2ba69a1b-f8dd-4bd6-a27d-3494811fbc6c"
    lock_path = tmp_path / ".pipeline.flock"
    with lock_path.open("a", encoding="utf-8") as lock_handle:
        lock_path.with_suffix(".activity").write_text(f"{generation}\n", encoding="utf-8")
        pipeline_log = tmp_path / "pipeline-20260904-080000.log"
        pipeline_log.write_text(
            "\n".join(
                [
                    f"=== pipeline RUN generation={generation} ===",
                    "=== egress preflight START ===",
                    "=== egress preflight OK ===",
                    "=== wechat_browser_preflight START ===",
                    "=== wechat_browser_preflight OK ===",
                    "=== fetch START ===",
                    "=== fetch OK ===",
                    "=== prefilter START ===",
                    "=== prefilter OK ===",
                    "=== score START ===",
                    "=== score OK ===",
                    "=== enrich START ===",
                    "=== enrich OK ===",
                    "=== curate START ===",
                    "=== curate OK ===",
                    "=== interpret START ===",
                    "=== interpret OK ===",
                    "=== wechat_browser_preflight_resolve START ===",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cli,
            "inspect_wechat_browser_executable",
            lambda: (_ for _ in ()).throw(AssertionError("browser check must not run")),
        )
        exit_code = cli._wechat_browser_preflight(
            state_path=tmp_path / "state.json",
            event_path=tmp_path / "events.jsonl",
            resolve_after_pipeline=True,
            pipeline_log=pipeline_log,
            pipeline_lock_path=lock_path,
            pipeline_lock_fd=lock_handle.fileno(),
            send=lambda *_args, **_kwargs: {"skipped": False},
        )

    assert exit_code == 2
    assert "active pipeline process tree" in capsys.readouterr().out


def test_resolve_rejects_same_inode_descriptor_that_does_not_hold_pipeline_lock(
    monkeypatch,
    capsys,
    tmp_path: Path,
    pipeline_success_evidence: dict[str, object],
) -> None:  # noqa: ANN001
    lock_path = Path(pipeline_success_evidence["pipeline_lock_path"])
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(AssertionError("browser check must not run")),
    )
    with lock_path.open("a", encoding="utf-8") as decoy:
        evidence = {**pipeline_success_evidence, "pipeline_lock_fd": decoy.fileno()}
        exit_code = cli._wechat_browser_preflight(
            state_path=tmp_path / "state.json",
            event_path=tmp_path / "events.jsonl",
            resolve_after_pipeline=True,
            **evidence,
            send=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("state transition must not run")
            ),
        )

    assert exit_code == 2
    assert "active pipeline process tree" in capsys.readouterr().out


def test_resolve_rejects_decoy_if_other_lock_holder_exits_during_verification(
    monkeypatch,
    capsys,
    tmp_path: Path,
    pipeline_success_evidence: dict[str, object],
) -> None:  # noqa: ANN001
    lock_path = Path(pipeline_success_evidence["pipeline_lock_path"])

    def release_holder_then_report_active(_path: Path) -> bool:
        fcntl.flock(int(pipeline_success_evidence["pipeline_lock_fd"]), fcntl.LOCK_UN)
        return True

    monkeypatch.setattr(cli, "pipeline_lock_is_held", release_holder_then_report_active)
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(AssertionError("browser check must not run")),
    )
    with lock_path.open("a", encoding="utf-8") as decoy_lock:
        exit_code = cli._wechat_browser_preflight(
            state_path=tmp_path / "state.json",
            event_path=tmp_path / "events.jsonl",
            resolve_after_pipeline=True,
            pipeline_log=pipeline_success_evidence["pipeline_log"],
            pipeline_lock_path=lock_path,
            pipeline_lock_fd=decoy_lock.fileno(),
            pipeline_capability_fd=decoy_lock.fileno(),
            send=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("state transition must not run")
            ),
        )

    assert exit_code == 2
    assert "inherited pipeline capability" in capsys.readouterr().out


def test_recovery_waits_for_full_pipeline_and_then_sends_resolved(
    monkeypatch, capsys, tmp_path: Path, pipeline_success_evidence: dict[str, object]
) -> None:  # noqa: ANN001
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    deliveries: list[str] = []
    severities: list[str] = []

    def sender(text: str, *, severity: str = "page") -> dict[str, object]:
        deliveries.append(text)
        severities.append(severity)
        return {"skipped": False}
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(WeChatBrowserNotVerified("driver failed")),
    )
    assert cli._wechat_browser_preflight(
        state_path=state_path, event_path=event_path, send=sender
    ) == 2

    executable = tmp_path / "chromium"
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(cli, "inspect_wechat_browser_executable", lambda: executable)
    assert cli._wechat_browser_preflight(
        state_path=state_path, event_path=event_path, send=sender
    ) == 0
    assert len(deliveries) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["W1"]["state"] == "firing"

    exit_code = cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=sender,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "WeChat browser recovery: RESOLVED" in output
    assert (
        "Scope: Chromium launch, network, and WeChat full-text fetch were not independently checked"
        in output
    )
    assert len(deliveries) == 2
    assert severities == ["page", "notice"]
    assert "✅ W1" in deliveries[-1]
    assert "处置方向：无需处置" in deliveries[-1]
    assert "仅关闭 executable-path incident" in deliveries[-1]
    assert "未验证 Chromium launch、网络或微信全文抓取" in deliveries[-1]
    assert "runbook：docs/operations/monitoring-alerting.md" in deliveries[-1]
    assert json.loads(state_path.read_text(encoding="utf-8"))["W1"]["state"] == "ok"
    assert [
        json.loads(line)["type"]
        for line in event_path.read_text(encoding="utf-8").splitlines()
    ] == ["firing", "resolved"]


def test_failed_recovery_notification_stays_pending_for_next_success(
    monkeypatch, capsys, tmp_path: Path, pipeline_success_evidence: dict[str, object]
) -> None:  # noqa: ANN001
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(WeChatBrowserNotVerified("driver failed")),
    )
    assert cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        send=lambda *_args, **_kwargs: {"skipped": False},
    ) == 2

    executable = tmp_path / "chromium"
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(cli, "inspect_wechat_browser_executable", lambda: executable)

    assert cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=lambda *_args, **_kwargs: {"skipped": True, "reason": "transport down"},
    ) == 2
    output = capsys.readouterr().out
    assert "WeChat browser recovery: DEGRADED" in output
    assert "Impact: recovery notification was not accepted; W1 remains open" in output
    assert (
        "Scope: executable path is present; Chromium launch, network, and WeChat full-text fetch "
        "were not checked"
    ) in output
    assert "no data repair required" not in output
    assert "will retry automatically" in output
    state = json.loads(state_path.read_text(encoding="utf-8"))["W1"]
    assert state["state"] == "firing"
    assert state["lifecycles"]["page"]["pending_notification"]["event_type"] == "resolved"

    deliveries: list[str] = []
    assert cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=lambda text, **_kwargs: deliveries.append(text) or {"skipped": False},
    ) == 0
    assert len(deliveries) == 1
    assert "✅ W1" in deliveries[0]


def test_new_w1_failure_discards_stale_pending_recovery_before_cooldown(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    started = datetime.fromisoformat("2026-09-04T08:00:00+08:00")
    firing = AlertRuleResult(
        "W1",
        "微信全文浏览器依赖不可用",
        True,
        "Playwright 预期 Chromium 可执行文件缺失或不可执行",
        "安装 Chromium",
        values={"status": "unavailable"},
    )
    resolved = AlertRuleResult(
        "W1",
        "微信全文浏览器依赖不可用",
        False,
        "expected executable present",
        "无需处置",
        values={"status": "present"},
    )
    run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=lambda *_args, **_kwargs: {"skipped": False},
    )
    run_alert_results_state_machine(
        [resolved],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=10),
        send=lambda *_args, **_kwargs: {"skipped": True, "reason": "transport down"},
    )

    outcome = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=15),
        send=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cooldown recurrence must not send another page")
        ),
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))["W1"]
    assert outcome["sent_count"] == 0
    assert state["state"] == "firing"
    assert state["status"] == "unavailable"
    assert state["lifecycles"]["page"]["pending_notification"] is None
    summary = _load_alert_summary(state_path)
    assert summary["degraded"] == []
    assert len(summary["firing"]) == 1
    assert "scheduled pipeline is blocked before fetch" in summary["firing"][0]
    assert "Run now" in summary["firing"][0]


def test_recovery_state_failure_keeps_scope_and_does_not_claim_data_integrity(
    monkeypatch, capsys, tmp_path: Path, pipeline_success_evidence: dict[str, object]
) -> None:  # noqa: ANN001
    executable = tmp_path / "chromium"
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(cli, "inspect_wechat_browser_executable", lambda: executable)
    monkeypatch.setattr(
        cli,
        "_run_wechat_browser_alert_transition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state unavailable")),
    )

    exit_code = cli._wechat_browser_preflight(
        state_path=tmp_path / "state.json",
        event_path=tmp_path / "events.jsonl",
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=lambda *_args, **_kwargs: {"skipped": False},
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "WeChat browser recovery: DEGRADED" in output
    assert (
        "Scope: executable path is present; Chromium launch, network, and WeChat full-text fetch "
        "were not checked"
    ) in output
    assert "no data repair required" not in output


def test_resolve_rechecks_browser_and_keeps_w1_open_when_it_disappears(
    monkeypatch, capsys, tmp_path: Path, pipeline_success_evidence: dict[str, object]
) -> None:  # noqa: ANN001
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(WeChatBrowserNotVerified("driver failed")),
    )
    assert cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        send=lambda *_args, **_kwargs: {"skipped": False},
    ) == 2
    deliveries: list[str] = []

    exit_code = cli._wechat_browser_preflight(
        state_path=state_path,
        event_path=event_path,
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=lambda text, **_kwargs: deliveries.append(text) or {"skipped": False},
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "WeChat browser recovery: NOT VERIFIED" in output
    assert "W1 remains open" in output
    assert not any("✅ W1" in text for text in deliveries)
    assert json.loads(state_path.read_text(encoding="utf-8"))["W1"]["state"] == "firing"


def test_first_w1_firing_at_pipeline_end_reports_completed_data_run(
    monkeypatch, tmp_path: Path, pipeline_success_evidence: dict[str, object]
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(WeChatBrowserUnavailable("chromium disappeared")),
    )
    deliveries: list[str] = []

    exit_code = cli._wechat_browser_preflight(
        state_path=tmp_path / "state.json",
        event_path=tmp_path / "events.jsonl",
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=lambda text, **_kwargs: deliveries.append(text) or {"skipped": False},
    )

    assert exit_code == 1
    assert len(deliveries) == 1
    assert "影响：本轮数据 pipeline 已完成；下一轮在 Chromium 恢复前将在 fetch 前停止" in deliveries[0]
    assert "本轮 RSS/X 抓取与后续处理不会启动" not in deliveries[0]


def test_browser_introspection_failure_is_not_verified_and_alerts(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        cli,
        "inspect_wechat_browser_executable",
        lambda: (_ for _ in ()).throw(WeChatBrowserNotVerified("driver failed")),
    )
    alert_calls: list[str] = []

    exit_code = cli._wechat_browser_preflight(
        state_path=tmp_path / "state.json",
        event_path=tmp_path / "events.jsonl",
        send=lambda text, **_kwargs: alert_calls.append(text) or {"skipped": False},
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "WeChat browser preflight: NOT VERIFIED" in output
    assert "Details: status=not_verified reason=driver failed" in output
    assert "inspect and repair the Playwright driver/runtime error" in output
    assert "uv run playwright install chromium" not in output
    assert len(alert_calls) == 1
    assert "logs/pipeline-*.log" in alert_calls[0]


def test_first_healthy_pipeline_resolve_sends_no_notification(
    monkeypatch, capsys, tmp_path: Path, pipeline_success_evidence: dict[str, object]
) -> None:  # noqa: ANN001
    executable = tmp_path / "chromium"
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(cli, "inspect_wechat_browser_executable", lambda: executable)

    exit_code = cli._wechat_browser_preflight(
        state_path=tmp_path / "state.json",
        event_path=tmp_path / "events.jsonl",
        resolve_after_pipeline=True,
        **pipeline_success_evidence,
        send=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("first healthy pipeline must not notify")
        ),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "WeChat browser recovery: NOT NEEDED" in output
    assert (
        "Scope: Chromium launch, network, and WeChat full-text fetch were not independently checked"
        in output
    )
    assert "Alert: not sent" in output


def test_inspector_accepts_a_regular_executable(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    executable = tmp_path / "chromium"
    executable.write_text("browser", encoding="utf-8")
    executable.chmod(0o755)

    class FakeChromium:
        executable_path = str(executable)

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeManager:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("airadar.fetcher.wechat.sync_playwright", FakeManager)

    assert inspect_wechat_browser_executable() == executable


def test_cli_parser_exposes_wechat_browser_preflight() -> None:
    args = cli.build_parser().parse_args(
        [
            "wechat-browser-preflight",
            "--state-path",
            "/tmp/state.json",
            "--event-path",
            "/tmp/events.jsonl",
            "--resolve-after-pipeline",
            "--pipeline-log",
            "/tmp/pipeline-20260904-080000.log",
        ]
    )
    assert args.command == "wechat-browser-preflight"
    assert args.state_path == "/tmp/state.json"
    assert args.event_path == "/tmp/events.jsonl"
    assert args.resolve_after_pipeline is True
    assert args.pipeline_log == "/tmp/pipeline-20260904-080000.log"
