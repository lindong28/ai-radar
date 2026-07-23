from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from .. import db
from ..admin.alerts import send_alert_message

DEFAULT_REMEDIATION_ROOT = Path.home() / ".local" / "share" / "ai-radar" / "performance-remediation"
DEFAULT_REMEDIATION_STATE_PATH = db.PROJECT_ROOT / "logs" / "performance" / "remediation-state.json"
DEFAULT_REMEDIATION_LOCK_PATH = db.PROJECT_ROOT / "logs" / "performance" / "remediation.lock"
DEFAULT_REMEDIATION_EVIDENCE_DIR = db.PROJECT_ROOT / "logs" / "performance" / "remediation-evidence"
DEFAULT_TIMEOUT_SECONDS = 60 * 60
REMEDIATION_CRONTAB_SAMPLE = (
    "25 * * * * cd /path/to/ai-radar && "
    "./run.sh performance-remediate >> logs/performance-remediate-cron.log 2>&1"
)


class AgentRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        prompt: str,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class RemediationConfig:
    main_checkout: Path = db.PROJECT_ROOT
    alert_state_path: Path = db.PROJECT_ROOT / "logs" / "performance" / "alert-state.json"
    performance_evidence_dir: Path = db.PROJECT_ROOT / "logs" / "performance" / "evidence"
    worker_root: Path = DEFAULT_REMEDIATION_ROOT / "worktrees"
    remediation_state_path: Path = DEFAULT_REMEDIATION_STATE_PATH
    lock_path: Path = DEFAULT_REMEDIATION_LOCK_PATH
    remediation_evidence_dir: Path = DEFAULT_REMEDIATION_EVIDENCE_DIR
    production_db_path: Path = db.DEFAULT_DB_PATH
    codex_binary: str = "codex"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ConfirmedIncident:
    rule_id: str
    since: str
    detail: str

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.rule_id}\0{self.since}".encode()).hexdigest()[:16]


def build_worker_command(codex_binary: str, worktree: Path) -> list[str]:
    return [
        codex_binary,
        "exec",
        "--ignore-user-config",
        "--sandbox",
        "workspace-write",
        "-c",
        'approval_policy="never"',
        "--ephemeral",
        "--color",
        "never",
        "-C",
        str(worktree.resolve()),
        "-",
    ]


def build_worker_environment(production_db_path: Path) -> dict[str, str]:
    allowed = ("PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["AI_RADAR_DB"] = str(production_db_path.resolve())
    return environment


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _worktree_path(worker_root: Path, incident: ConfirmedIncident) -> Path:
    root = worker_root.resolve()
    slug = re.sub(r"[^a-z0-9]+", "-", incident.rule_id.lower()).strip("-") or "perf"
    worktree = (root / f"{slug}-{incident.fingerprint}").resolve()
    if worktree.parent != root or not _is_within(worktree, root):
        raise ValueError("resolved remediation worktree must remain within worker_root")
    return worktree


def _git_root(path: Path) -> Path | None:
    try:
        raw = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(raw).resolve()


def validate_worker_preflight(
    *,
    main_checkout: Path,
    worktree: Path,
    production_db_path: Path,
    command: Sequence[str],
    environment: Mapping[str, str],
) -> list[str]:
    violations: list[str] = []
    main = main_checkout.resolve()
    worker = worktree.resolve()
    if worker == main:
        violations.append("worktree_is_main_checkout")
    if _git_root(main) != main:
        violations.append("main_checkout_not_git_root")
    if _git_root(worker) != worker:
        violations.append("worktree_not_isolated_git_root")

    command_list = list(command)
    if "--ignore-user-config" not in command_list:
        violations.append("ambient_user_config_not_disabled")
    try:
        sandbox = command_list[command_list.index("--sandbox") + 1]
    except (ValueError, IndexError):
        sandbox = None
    if sandbox != "workspace-write":
        violations.append("sandbox_not_workspace_write")
    config_values: list[str] = []
    malformed_config = False
    for index, token in enumerate(command_list):
        if token in {"-c", "--config"}:
            if index + 1 >= len(command_list):
                malformed_config = True
            else:
                config_values.append(command_list[index + 1])
        elif token.startswith("--config=") or (token.startswith("-c") and token != "-c"):
            config_values.append(token)
    if malformed_config or config_values != ['approval_policy="never"']:
        violations.append("codex_config_not_pinned")
    try:
        command_worktree = Path(command_list[command_list.index("-C") + 1]).resolve()
    except (ValueError, IndexError):
        command_worktree = None
    if command_worktree != worker:
        violations.append("command_workdir_not_worktree")
    if "--add-dir" in command_list:
        violations.append("additional_writable_root_requested")
    if any(
        flag in command_list
        for flag in ("--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust")
    ):
        violations.append("dangerous_codex_override_requested")

    configured_db = environment.get("AI_RADAR_DB")
    if not configured_db:
        violations.append("ai_radar_db_not_pinned")
    elif _is_within(Path(configured_db), worker):
        violations.append("ai_radar_db_is_worker_writable")
    if _is_within(production_db_path, worker):
        violations.append("production_db_is_worker_writable")

    allowed_keys = {"PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "AI_RADAR_DB"}
    forbidden = sorted(set(environment) - allowed_keys)
    if forbidden:
        violations.append("forbidden_environment_key")
    return violations


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _failure_evidence(
    *,
    evidence_dir: Path,
    rule_id: str,
    since: str,
    reason: str,
    violations: Sequence[str] = (),
    detail: str = "",
) -> Path:
    now = datetime.now(UTC)
    digest = hashlib.sha256(f"{rule_id}\0{since}".encode()).hexdigest()[:12]
    path = evidence_dir / f"{now:%Y%m%dT%H%M%SZ}-{digest}-failure.json"
    payload = {
        "schema_version": 1,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "rule_id": rule_id,
        "since": since,
        "reason": reason,
        "violations": list(violations),
        "detail": detail[:20_000],
    }
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return path


def _report_failure(
    *,
    evidence_dir: Path,
    rule_id: str,
    since: str,
    reason: str,
    send: Callable[[str], object],
    violations: Sequence[str] = (),
    detail: str = "",
) -> dict[str, object]:
    evidence_path = _failure_evidence(
        evidence_dir=evidence_dir,
        rule_id=rule_id,
        since=since,
        reason=reason,
        violations=violations,
        detail=detail,
    )
    violation_text = f" violations={','.join(violations)}" if violations else ""
    send(f"【AI Radar】性能候选修复 worker 失败：{rule_id} reason={reason}{violation_text} evidence={evidence_path}")
    return {
        "status": "failed",
        "reason": reason,
        "violations": list(violations),
        "evidence_path": str(evidence_path),
    }


def _agent_prompt(rule_id: str, since: str, detail: str) -> str:
    return f"""You are producing a local candidate fix for a confirmed AI Radar performance incident.

Incident: {rule_id}
Firing since: {since}
Detail: {detail}

Diagnose from the repository and available read-only evidence, implement the smallest justified fix in this worktree, and run focused tests. Do not commit: the trusted orchestrator will create the candidate commit after you exit. Never push, deploy, invoke launchctl, modify production data, or write outside this worktree. If no safe justified fix exists, leave the worktree unchanged and explain why in your final response.
"""


def run_agent_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    prompt: str,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _git(
    cwd: Path,
    *args: str,
    timeout: float = 30,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        timeout=max(0.1, timeout),
    )


def run_candidate_in_worktree(
    *,
    rule_id: str,
    since: str,
    detail: str,
    main_checkout: Path,
    worktree: Path,
    production_db_path: Path,
    command: list[str],
    environment: dict[str, str],
    evidence_dir: Path,
    timeout_seconds: float,
    agent_runner: AgentRunner = run_agent_process,
    send: Callable[[str], object] = send_alert_message,
) -> dict[str, object]:
    started = monotonic()

    def remaining() -> float:
        value = timeout_seconds - (monotonic() - started)
        if value <= 0:
            raise subprocess.TimeoutExpired("performance-remediation", timeout_seconds)
        return value

    violations = validate_worker_preflight(
        main_checkout=main_checkout,
        worktree=worktree,
        production_db_path=production_db_path,
        command=command,
        environment=environment,
    )
    if violations:
        return _report_failure(
            evidence_dir=evidence_dir,
            rule_id=rule_id,
            since=since,
            reason="preflight_failed",
            violations=violations,
            send=send,
        )

    main_head = _git(main_checkout, "rev-parse", "HEAD", timeout=remaining()).stdout.strip()
    try:
        completed = agent_runner(
            command,
            cwd=worktree,
            env=environment,
            prompt=_agent_prompt(rule_id, since, detail),
            timeout=remaining(),
        )
    except subprocess.TimeoutExpired as exc:
        partial = str(exc.output or "") + "\n" + str(exc.stderr or "")
        return _report_failure(
            evidence_dir=evidence_dir,
            rule_id=rule_id,
            since=since,
            reason="worker_timeout",
            detail=partial,
            send=send,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _report_failure(
            evidence_dir=evidence_dir,
            rule_id=rule_id,
            since=since,
            reason="worker_launch_failed",
            detail=f"{type(exc).__name__}: {exc}",
            send=send,
        )
    if completed.returncode != 0:
        return _report_failure(
            evidence_dir=evidence_dir,
            rule_id=rule_id,
            since=since,
            reason="worker_failed",
            detail=f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}",
            send=send,
        )
    if _git(main_checkout, "rev-parse", "HEAD", timeout=remaining()).stdout.strip() != main_head:
        return _report_failure(
            evidence_dir=evidence_dir,
            rule_id=rule_id,
            since=since,
            reason="main_checkout_changed",
            detail="Main HEAD changed while the candidate worker was running.",
            send=send,
        )
    if not _git(worktree, "status", "--porcelain", timeout=remaining()).stdout.strip():
        return _report_failure(
            evidence_dir=evidence_dir,
            rule_id=rule_id,
            since=since,
            reason="no_candidate_changes",
            detail=completed.stdout,
            send=send,
        )

    _git(worktree, "add", "-A", timeout=remaining())
    _git(
        worktree,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        f"fix(performance): candidate for {rule_id}",
        timeout=remaining(),
    )
    candidate_commit = _git(worktree, "rev-parse", "HEAD", timeout=remaining()).stdout.strip()
    if _git(main_checkout, "rev-parse", "HEAD", timeout=remaining()).stdout.strip() != main_head:
        return _report_failure(
            evidence_dir=evidence_dir,
            rule_id=rule_id,
            since=since,
            reason="candidate_landed_on_main",
            detail=f"candidate={candidate_commit}",
            send=send,
        )

    summary_path = evidence_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{candidate_commit[:12]}-summary.md"
    summary = (
        f"# Performance candidate {candidate_commit[:12]}\n\n"
        f"- Incident: `{rule_id}`\n"
        f"- Firing since: `{since}`\n"
        f"- Worktree: `{worktree}`\n"
        f"- Candidate commit: `{candidate_commit}`\n"
        f"- Main commit remained: `{main_head}`\n\n"
        "## Agent diagnostic\n\n"
        f"{completed.stdout.strip() or '(no diagnostic text)'}\n"
    )
    _atomic_write(summary_path, summary)
    send(
        f"【AI Radar】性能候选修复待审：{rule_id} candidate={candidate_commit} "
        f"worktree={worktree} summary={summary_path}"
    )
    return {
        "status": "candidate",
        "candidate_commit": candidate_commit,
        "worktree": str(worktree),
        "summary_path": str(summary_path),
        "main_commit": main_head,
    }


def _load_confirmed_incidents(path: Path) -> list[ConfirmedIncident]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    incidents: list[ConfirmedIncident] = []
    for rule_id in sorted(payload):
        entry = payload[rule_id]
        if not str(rule_id).startswith("PERF:") or not isinstance(entry, dict):
            continue
        lifecycles = entry.get("lifecycles")
        if isinstance(lifecycles, dict) and lifecycles:
            page_lifecycle = lifecycles.get("page")
            if (
                not isinstance(page_lifecycle, dict)
                or page_lifecycle.get("state") != "firing"
                or not page_lifecycle.get("since")
            ):
                continue
            incident_entry = page_lifecycle
        else:
            if entry.get("state") != "firing" or not entry.get("since"):
                continue
            if entry.get("severity", "page") != "page":
                continue
            incident_entry = entry
        incidents.append(
            ConfirmedIncident(
                rule_id=str(rule_id),
                since=str(incident_entry["since"]),
                detail=str(
                    incident_entry.get("detail")
                    or entry.get("detail")
                    or "confirmed performance regression"
                ),
            )
        )
    return incidents


def _load_remediation_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _processed_fingerprints(state: Mapping[str, object]) -> set[str]:
    processed: set[str] = set()
    legacy = state.get("incident_fingerprint")
    if legacy:
        processed.add(str(legacy))
    incidents = state.get("incidents")
    if isinstance(incidents, dict):
        processed.update(str(fingerprint) for fingerprint in incidents)
    return processed


def _latest_incident_evidence(evidence_dir: Path, rule_id: str) -> Path | None:
    for path in sorted(evidence_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("rule_id") == rule_id:
            return path
    return None


def _remove_failed_worktree(main_checkout: Path, worktree: Path) -> bool:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree)],
        cwd=main_checkout,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=main_checkout,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=main_checkout,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    marker = f"worktree {worktree.resolve()}"
    return not worktree.exists() and marker not in listed.stdout


def remediate_confirmed_incident(
    config: RemediationConfig,
    *,
    agent_runner: AgentRunner = run_agent_process,
    send: Callable[[str], object] = send_alert_message,
) -> dict[str, object]:
    if not 0 < config.timeout_seconds <= DEFAULT_TIMEOUT_SECONDS:
        raise ValueError("remediation timeout must be within 1..3600 seconds")
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with config.lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}

        incidents = _load_confirmed_incidents(config.alert_state_path)
        if not incidents:
            return {"status": "no_confirmed_incident"}
        previous_state = _load_remediation_state(config.remediation_state_path)
        processed = _processed_fingerprints(previous_state)
        incident = next((item for item in incidents if item.fingerprint not in processed), None)
        if incident is None:
            return {
                "status": "already_handled",
                "incident_fingerprints": [item.fingerprint for item in incidents],
            }

        started = monotonic()
        config.worker_root.mkdir(parents=True, exist_ok=True)
        worktree = _worktree_path(config.worker_root, incident)
        worktree_preexisted = worktree.exists()
        try:
            _git(
                config.main_checkout,
                "worktree",
                "add",
                "--detach",
                str(worktree),
                "HEAD",
                timeout=config.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = _report_failure(
                evidence_dir=config.remediation_evidence_dir,
                rule_id=incident.rule_id,
                since=incident.since,
                reason="worktree_setup_failed",
                detail=f"{type(exc).__name__}: {exc}",
                send=send,
            )
            result["worktree"] = str(worktree)
            if not worktree_preexisted:
                _remove_failed_worktree(config.main_checkout, worktree)
        else:
            incident_evidence = _latest_incident_evidence(
                config.performance_evidence_dir,
                incident.rule_id,
            )
            detail = incident.detail
            if incident_evidence is not None:
                detail = f"{detail}; source_evidence={incident_evidence}"
            remaining_budget = config.timeout_seconds - (monotonic() - started)
            if remaining_budget <= 0:
                result = _report_failure(
                    evidence_dir=config.remediation_evidence_dir,
                    rule_id=incident.rule_id,
                    since=incident.since,
                    reason="worker_timeout",
                    detail="Timeout budget exhausted during worktree setup and preflight.",
                    send=send,
                )
            try:
                if remaining_budget > 0:
                    result = run_candidate_in_worktree(
                        rule_id=incident.rule_id,
                        since=incident.since,
                        detail=detail,
                        main_checkout=config.main_checkout,
                        worktree=worktree,
                        production_db_path=config.production_db_path,
                        command=build_worker_command(config.codex_binary, worktree),
                        environment=build_worker_environment(config.production_db_path),
                        evidence_dir=config.remediation_evidence_dir,
                        timeout_seconds=remaining_budget,
                        agent_runner=agent_runner,
                        send=send,
                    )
            except subprocess.TimeoutExpired as exc:
                result = _report_failure(
                    evidence_dir=config.remediation_evidence_dir,
                    rule_id=incident.rule_id,
                    since=incident.since,
                    reason="worker_timeout",
                    detail=f"{type(exc).__name__}: {exc}",
                    send=send,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                result = _report_failure(
                    evidence_dir=config.remediation_evidence_dir,
                    rule_id=incident.rule_id,
                    since=incident.since,
                    reason="orchestration_failed",
                    detail=f"{type(exc).__name__}: {exc}",
                    send=send,
                )
            result["worktree"] = str(worktree)
            if result["status"] != "candidate":
                if not _remove_failed_worktree(config.main_checkout, worktree):
                    result = _report_failure(
                        evidence_dir=config.remediation_evidence_dir,
                        rule_id=incident.rule_id,
                        since=incident.since,
                        reason="worktree_cleanup_failed",
                        detail=f"original_failure={result}",
                        send=send,
                    )
                    result["worktree"] = str(worktree)

        incident_record = {
            "rule_id": incident.rule_id,
            "since": incident.since,
            "status": result["status"],
            "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "result": result,
        }
        history = previous_state.get("incidents")
        incident_history = dict(history) if isinstance(history, dict) else {}
        legacy_fingerprint = previous_state.get("incident_fingerprint")
        if legacy_fingerprint and str(legacy_fingerprint) not in incident_history:
            incident_history[str(legacy_fingerprint)] = {
                "status": previous_state.get("status", "handled"),
            }
        # Single-shot per firing episode: every terminal attempt, including a
        # failure, marks this fingerprint handled to prevent Codex spawn storms.
        incident_history[incident.fingerprint] = incident_record
        state = {
            "schema_version": 1,
            "incident_fingerprint": incident.fingerprint,
            **incident_record,
            "incidents": incident_history,
        }
        _atomic_write(config.remediation_state_path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
        return result
