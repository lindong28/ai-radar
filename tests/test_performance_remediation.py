from __future__ import annotations

import fcntl
import json
import subprocess
from pathlib import Path

import pytest

from airadar.performance import remediation
from airadar.performance.remediation import (
    ConfirmedIncident,
    RemediationConfig,
    build_worker_command,
    build_worker_environment,
    remediate_confirmed_incident,
    run_candidate_in_worktree,
)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "main"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    (root / "app.py").write_text("VALUE = 'slow'\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "initial")
    return root


def _state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "PERF:homepage.first_card:same_host_public:idle": {
                    "state": "firing",
                    "since": "2026-07-18T23:00:00+08:00",
                    "detail": "confirmed fixture regression",
                }
            }
        ),
        encoding="utf-8",
    )


def _config(tmp_path: Path, main: Path) -> RemediationConfig:
    state_path = tmp_path / "alert-state.json"
    _state(state_path)
    return RemediationConfig(
        main_checkout=main,
        alert_state_path=state_path,
        performance_evidence_dir=tmp_path / "performance-evidence",
        worker_root=tmp_path / "workers",
        remediation_state_path=tmp_path / "remediation-state.json",
        lock_path=tmp_path / "remediation.lock",
        remediation_evidence_dir=tmp_path / "remediation-evidence",
        production_db_path=main / "data" / "radar.db",
        timeout_seconds=30,
    )


def test_confirmed_incident_produces_candidate_commit_outside_main(
    tmp_path: Path,
) -> None:
    main = _repo(tmp_path)
    config = _config(tmp_path, main)
    main_head = _git(main, "rev-parse", "HEAD").stdout.strip()
    calls: list[tuple[list[str], Path, dict[str, str], float]] = []
    sent: list[str] = []

    def fake_agent(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        prompt: str,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd, env, timeout))
        assert "confirmed fixture regression" in prompt
        (cwd / "app.py").write_text("VALUE = 'fast'\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "candidate diagnosis", "")

    result = remediate_confirmed_incident(
        config,
        agent_runner=fake_agent,
        send=lambda text: sent.append(text) or {"skipped": False},
    )

    assert result["status"] == "candidate"
    assert len(calls) == 1
    command, worktree, environment, timeout = calls[0]
    assert command == build_worker_command(config.codex_binary, worktree)
    assert "--ignore-user-config" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("-C") + 1] == str(worktree)
    assert environment == build_worker_environment(config.production_db_path)
    assert timeout <= 30
    candidate = str(result["candidate_commit"])
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == candidate
    assert _git(main, "rev-parse", "HEAD").stdout.strip() == main_head
    assert _git(main, "merge-base", "--is-ancestor", candidate, main_head, check=False).returncode == 1
    assert _git(main, "show", "HEAD:app.py").stdout == "VALUE = 'slow'\n"
    assert Path(str(result["summary_path"])).is_file()
    assert len(sent) == 1
    assert candidate in sent[0]
    assert str(result["summary_path"]) in sent[0]
    assert not any(token in command for token in ("push", "deploy", "launchctl"))


def test_singleflight_skips_when_worker_lock_is_held(tmp_path: Path) -> None:
    main = _repo(tmp_path)
    config = _config(tmp_path, main)
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with config.lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = remediate_confirmed_incident(
            config,
            agent_runner=lambda *args, **kwargs: pytest.fail("worker must not launch"),
            send=lambda text: pytest.fail("busy singleflight is not a worker failure"),
        )
    assert result["status"] == "busy"
    assert not config.worker_root.exists()


def test_next_confirmed_incident_runs_after_an_earlier_rule_was_handled(tmp_path: Path) -> None:
    main = _repo(tmp_path)
    config = _config(tmp_path, main)
    first = ConfirmedIncident(
        rule_id="PERF:homepage.first_card:same_host_public:idle",
        since="2026-07-18T23:00:00+08:00",
        detail="first",
    )
    config.remediation_state_path.write_text(
        json.dumps({"incident_fingerprint": first.fingerprint, "status": "candidate"}),
        encoding="utf-8",
    )
    alert_state = json.loads(config.alert_state_path.read_text(encoding="utf-8"))
    alert_state["PERF:wechat.detail.readable:same_host_public:idle"] = {
        "state": "firing",
        "since": "2026-07-18T23:01:00+08:00",
        "detail": "second confirmed regression",
    }
    config.alert_state_path.write_text(json.dumps(alert_state), encoding="utf-8")
    prompts: list[str] = []

    def fake_agent(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        prompt: str,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        prompts.append(prompt)
        (cwd / "app.py").write_text("VALUE = 'second-fix'\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "second candidate", "")

    result = remediate_confirmed_incident(
        config,
        agent_runner=fake_agent,
        send=lambda text: {"skipped": False},
    )

    assert result["status"] == "candidate"
    assert len(prompts) == 1
    assert "PERF:wechat.detail.readable:same_host_public:idle" in prompts[0]


def test_timeout_alerts_records_evidence_and_removes_failed_worktree(tmp_path: Path) -> None:
    main = _repo(tmp_path)
    config = _config(tmp_path, main)
    sent: list[str] = []

    def timeout_agent(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="codex", timeout=1, output="partial")

    result = remediate_confirmed_incident(
        config,
        agent_runner=timeout_agent,
        send=lambda text: sent.append(text) or {"skipped": False},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "worker_timeout"
    assert Path(str(result["evidence_path"])).is_file()
    assert len(sent) == 1
    assert "worker_timeout" in sent[0]
    assert not Path(str(result["worktree"])).exists()
    assert _git(main, "worktree", "list", "--porcelain").stdout.count("worktree ") == 1


def test_worktree_path_sanitizes_rule_id_and_stays_within_worker_root(tmp_path: Path) -> None:
    incident = ConfirmedIncident(
        rule_id="PERF:/../../Outside_Incident!",
        since="2026-07-18T23:00:00+08:00",
        detail="malicious path fixture",
    )
    worker_root = tmp_path / "workers"

    worktree = remediation._worktree_path(worker_root, incident)

    assert worktree.parent == worker_root.resolve()
    assert worktree.name == f"perf-outside-incident-{incident.fingerprint}"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("main_worktree", "worktree_is_main_checkout"),
        ("ai_radar_db", "ai_radar_db_is_worker_writable"),
        ("production_db", "production_db_is_worker_writable"),
        ("deploy_env", "forbidden_environment_key"),
        ("sandbox", "sandbox_not_workspace_write"),
        ("approval_override", "codex_config_not_pinned"),
        ("extra_config", "codex_config_not_pinned"),
    ],
)
def test_preflight_violation_refuses_launch_and_alerts(
    case: str,
    expected: str,
    tmp_path: Path,
) -> None:
    main = _repo(tmp_path)
    config = _config(tmp_path, main)
    worktree = tmp_path / "worktree"
    _git(main, "worktree", "add", "--detach", str(worktree), "HEAD")
    command = build_worker_command(config.codex_binary, worktree)
    environment = build_worker_environment(config.production_db_path)
    selected_worktree = worktree
    production_db = config.production_db_path
    if case == "main_worktree":
        selected_worktree = main
        command = build_worker_command(config.codex_binary, main)
    elif case == "ai_radar_db":
        environment["AI_RADAR_DB"] = str(worktree / "radar.db")
    elif case == "production_db":
        production_db = worktree / "production.db"
    elif case == "deploy_env":
        environment["GITHUB_TOKEN"] = "must-not-pass"
    elif case == "sandbox":
        command[command.index("workspace-write")] = "danger-full-access"
    elif case == "approval_override":
        command[command.index("-c") + 1] = 'approval_policy="on-request"'
    elif case == "extra_config":
        command[-1:-1] = ["-c", "sandbox_workspace_write.network_access=true"]
    sent: list[str] = []

    result = run_candidate_in_worktree(
        rule_id="PERF:homepage.first_card:same_host_public:idle",
        since="2026-07-18T23:00:00+08:00",
        detail="confirmed fixture regression",
        main_checkout=main,
        worktree=selected_worktree,
        production_db_path=production_db,
        command=command,
        environment=environment,
        evidence_dir=config.remediation_evidence_dir,
        timeout_seconds=30,
        agent_runner=lambda *args, **kwargs: pytest.fail("preflight must refuse launch"),
        send=lambda text: sent.append(text) or {"skipped": False},
    )

    assert result["status"] == "failed"
    assert result["reason"] == "preflight_failed"
    assert expected in result["violations"]
    assert Path(str(result["evidence_path"])).is_file()
    assert len(sent) == 1
    assert expected in sent[0]
