from __future__ import annotations

import fcntl
import os
import re
import shutil
import signal
import stat
import subprocess
import textwrap
import time
from pathlib import Path
from typing import IO

REPO_ROOT = Path(__file__).resolve().parents[1]

# Minimal PATH mimicking a cron non-interactive shell: nothing from the
# interactive rc is present. Every tool pipeline.sh needs (bash, date, mktemp,
# mv, tee, sleep, find, python3) must resolve from these system dirs alone.
CRON_LIKE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _copy_pipeline_fixture(
    tmp_path: Path,
    *,
    fail_stage: str | None = None,
    stage_sleep: float | None = None,
) -> tuple[Path, dict[str, str]]:
    source = REPO_ROOT / "pipeline.sh"
    assert source.exists(), "pipeline.sh should exist at the repository root"

    script = tmp_path / "pipeline.sh"
    shutil.copy2(source, script)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    run_sh = tmp_path / "run.sh"
    run_sh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -u
            printf '%s\\n' "$*" >> run-calls.log
            if [[ "${1:-}" == "${FAIL_STAGE:-}" ]]; then
              exit 42
            fi
            if [[ -n "${STAGE_SLEEP:-}" ]]; then
              # exec into a python interpreter so the fd-9 inheritance
              # contract is exercised across an exec into python, not just
              # /bin/sleep. This still bypasses uv itself; the real
              # run.sh -> uv -> python chain was verified manually
              # (uv-run python fstat(9) succeeded) but is not automated.
              exec /usr/bin/python3 -c "import time; time.sleep($STAGE_SLEEP)"
            fi
            exit 0
            """
        ),
        encoding="utf-8",
    )
    run_sh.chmod(0o755)

    env = {
        "PATH": CRON_LIKE_PATH,
        "HOME": os.environ.get("HOME", str(tmp_path)),
    }
    if fail_stage:
        env["FAIL_STAGE"] = fail_stage
    if stage_sleep is not None:
        env["STAGE_SLEEP"] = str(stage_sleep)
    return script, env


def _flock_probe(lock_path: Path, operation: int) -> IO[str] | None:
    """Try a non-blocking flock; return the holding handle on success, None on busy."""
    handle = open(lock_path, "a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _assert_exclusive_available(lock_path: Path) -> None:
    # SIGKILL delivery is asynchronous; poll briefly for the kernel release.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        handle = _flock_probe(lock_path, fcntl.LOCK_EX)
        if handle is not None:
            handle.close()
            return
        time.sleep(0.05)
    raise AssertionError("exclusive lock should be acquirable")


def _child_pids(parent_pid: int) -> list[int]:
    completed = subprocess.run(
        ["/usr/bin/pgrep", "-P", str(parent_pid)],
        capture_output=True,
        text=True,
    )
    return [int(line) for line in completed.stdout.split()]


def _kill_tree(root_pid: int) -> None:
    pending = [root_pid]
    while pending:
        pid = pending.pop()
        pending.extend(_child_pids(pid))
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_pipeline_script_runs_stages_in_order_and_logs_success(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines() == [
        "egress-preflight",
        "fetch",
        "prefilter --since 24h",
        "score --since 24h",
        "enrich --since 24h --limit 40",
        "curate",
        "interpret --limit 30",
    ]
    logs = sorted((tmp_path / "logs").glob("pipeline-*.log"))
    assert len(logs) == 1
    assert re.match(r"pipeline-\d{8}-\d{6}\.log", logs[0].name)
    log_text = logs[0].read_text(encoding="utf-8")
    assert "=== fetch START ===" in log_text
    assert "=== enrich OK ===" in log_text
    assert "=== PIPELINE DONE (failed=0) ===" in log_text


def test_pipeline_fails_before_any_stage_when_egress_preflight_fails(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    env["FAIL_STAGE"] = "egress-preflight"

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 1
    assert (tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines() == ["egress-preflight"]
    log_text = next((tmp_path / "logs").glob("pipeline-*.log")).read_text(encoding="utf-8")
    assert "=== egress preflight FAIL (exit 42) ===" in log_text
    assert "=== fetch START ===" not in log_text
    assert "AI_RADAR_PROXY_FILE" not in (tmp_path / "pipeline.sh").read_text(encoding="utf-8")


def test_pipeline_script_continues_after_stage_failure(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path, fail_stage="prefilter")

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 1
    assert (tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines() == [
        "egress-preflight",
        "fetch",
        "prefilter --since 24h",
        "score --since 24h",
        "enrich --since 24h --limit 40",
        "curate",
        "interpret --limit 30",
    ]
    log_text = next((tmp_path / "logs").glob("pipeline-*.log")).read_text(encoding="utf-8")
    assert "=== prefilter FAIL (exit 42) ===" in log_text
    assert "=== score START ===" in log_text
    assert "=== curate OK ===" in log_text
    assert "=== interpret OK ===" in log_text
    assert "=== PIPELINE DONE (failed=1) ===" in log_text


def test_pipeline_script_skips_when_another_pipeline_is_running(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    holder = _flock_probe(tmp_path / ".pipeline.flock", fcntl.LOCK_EX)
    assert holder is not None

    try:
        result = subprocess.run(
            [str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30
        )
    finally:
        holder.close()

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "run-calls.log").exists()
    log_text = next((tmp_path / "logs").glob("pipeline-*.log")).read_text(encoding="utf-8")
    assert "=== pipeline SKIP: already running ===" in log_text


def test_pipeline_lock_released_when_whole_process_tree_dies(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path, stage_sleep=30)
    process = subprocess.Popen(
        [str(script)], cwd="/", env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    lock_path = tmp_path / ".pipeline.flock"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (tmp_path / "run-calls.log").exists():
            time.sleep(0.05)
        assert (tmp_path / "run-calls.log").exists(), "first stage never started"
        # Positive control: while the tree is alive the lock must be held.
        assert _flock_probe(lock_path, fcntl.LOCK_EX) is None
    finally:
        _kill_tree(process.pid)
        process.wait(timeout=5)

    _assert_exclusive_available(lock_path)


def test_pipeline_lock_survives_orchestrator_kill_while_stage_alive(tmp_path: Path) -> None:
    # The mutex protects the process tree that writes the DB (ADR-052): after
    # SIGKILLing only the orchestrator, the surviving stage must still hold the
    # lock so a new cron round cannot start writing concurrently.
    script, env = _copy_pipeline_fixture(tmp_path, stage_sleep=30)
    process = subprocess.Popen(
        [str(script)], cwd="/", env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    lock_path = tmp_path / ".pipeline.flock"
    stage_roots: list[int] = []
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (tmp_path / "run-calls.log").exists():
            time.sleep(0.05)
        assert (tmp_path / "run-calls.log").exists(), "first stage never started"
        stage_roots = _child_pids(process.pid)
        assert stage_roots, "expected a live stage child of the orchestrator"

        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        time.sleep(0.2)

        assert _flock_probe(lock_path, fcntl.LOCK_EX) is None, (
            "lock must remain held by the surviving stage tree after the "
            "orchestrator is SIGKILLed"
        )
    finally:
        for pid in stage_roots:
            _kill_tree(pid)
        if process.poll() is None:
            _kill_tree(process.pid)
            process.wait(timeout=5)

    time.sleep(0.2)
    _assert_exclusive_available(lock_path)


def test_pipeline_lock_released_after_natural_exit(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    _assert_exclusive_available(tmp_path / ".pipeline.flock")


def test_observer_shared_probe_does_not_starve_pipeline_acquisition(tmp_path: Path) -> None:
    # Observers (journey monitor, A6) probe the lock with LOCK_SH for a moment.
    # The pipeline's short retry loop must cross that window instead of
    # reporting a spurious "already running" SKIP. The shared lock is held
    # until the pipeline has *logged a failed attempt*, so this test proves a
    # retry actually happened — without the retry loop the first failed
    # attempt would be an immediate SKIP.
    script, env = _copy_pipeline_fixture(tmp_path)
    lock_path = tmp_path / ".pipeline.flock"
    holder = _flock_probe(lock_path, fcntl.LOCK_SH)
    assert holder is not None

    process = subprocess.Popen(
        [str(script)], cwd="/", env=env, text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        saw_busy_attempt = False
        while time.monotonic() < deadline:
            logs = list((tmp_path / "logs").glob("pipeline-*.log"))
            if logs and "pipeline lock busy" in logs[0].read_text(encoding="utf-8"):
                saw_busy_attempt = True
                break
            time.sleep(0.02)
        assert saw_busy_attempt, "pipeline never logged a failed lock attempt while the observer held LOCK_SH"
        holder.close()
        assert process.wait(timeout=30) == 0
    finally:
        holder.close()
        if process.poll() is None:
            _kill_tree(process.pid)
            process.wait(timeout=5)

    assert (tmp_path / "run-calls.log").exists(), (
        "pipeline should have run its stages once the observer released its probe"
    )
    log_text = next((tmp_path / "logs").glob("pipeline-*.log")).read_text(encoding="utf-8")
    assert "=== pipeline SKIP" not in log_text


def test_pipeline_activity_generation_does_not_aba_after_marker_deletion(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)

    first = subprocess.run(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    first_generation = (tmp_path / ".pipeline.activity").read_text().strip()
    (tmp_path / ".pipeline.activity").unlink()
    second = subprocess.run(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    second_generation = (tmp_path / ".pipeline.activity").read_text().strip()

    assert first.returncode == second.returncode == 0
    assert second_generation != first_generation


def test_pipeline_deploy_cron_entry_uses_repo_placeholder_fifteen_minute_schedule() -> None:
    cron_file = REPO_ROOT / "deploy" / "cron" / "ai-radar-pipeline"
    assert cron_file.exists(), "deploy/cron/ai-radar-pipeline should exist"

    lines = [
        line.strip()
        for line in cron_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" in lines
    assert "*/15 * * * * /path/to/ai-radar/pipeline.sh >/dev/null 2>&1" in lines


def test_pipeline_crontab_lifecycle_matches_phase_one_contract() -> None:
    cron = (REPO_ROOT / "deploy/cron/ai-radar-pipeline").read_text(encoding="utf-8")
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    uninstall = (REPO_ROOT / "uninstall.sh").read_text(encoding="utf-8")
    services = (REPO_ROOT / "deploy/lib/services.sh").read_text(encoding="utf-8")

    assert cron == (
        "# Run the AI Radar incremental pipeline every 15 minutes.\n"
        "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin\n"
        "*/15 * * * * /path/to/ai-radar/pipeline.sh >/dev/null 2>&1\n"
    )
    assert 'grep -q "ai-radar/pipeline.sh"' in install
    assert 'grep -v "ai-radar/pipeline.sh"' in uninstall
    assert "validate_pipeline_cron_table" not in services
    assert "pipeline_legacy_line_matches" not in services
    assert "ai-radar:pipeline:begin" not in cron


def test_install_pipeline_expands_cron_template_to_repo_root(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "installed-crontab"
    crontab = fake_bin / "crontab"
    crontab.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "${1:-}" == "-l" ]]; then
              exit 1
            fi
            cat > "$CRONTAB_CAPTURE"
            """
        ),
        encoding="utf-8",
    )
    crontab.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["CRONTAB_CAPTURE"] = str(capture)

    result = subprocess.run(
        ["./install.sh", "pipeline"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    installed = capture.read_text(encoding="utf-8")
    assert f"*/15 * * * * {REPO_ROOT / 'pipeline.sh'} >/dev/null 2>&1" in installed
    assert "/path/to/ai-radar" not in installed


def test_pipeline_launchd_fallback_runs_every_fifteen_minutes() -> None:
    plist = (REPO_ROOT / "deploy" / "launchd" / "ai-radar-pipeline.plist.example").read_text(encoding="utf-8")

    assert "<string>live.aiplanet.ai-radar.pipeline</string>" in plist
    assert "<string>/path/to/ai-radar/pipeline.sh</string>" in plist
    assert "<key>StartInterval</key><integer>900</integer>" in plist
    assert "<key>WorkingDirectory</key><string>/path/to/ai-radar</string>" in plist


def test_readme_documents_automatic_scheduler_setup() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "## 自动化调度" in readme
    assert "./pipeline.sh" in readme
    assert "./install.sh pipeline" in readme
    assert "不要把 `deploy/cron/ai-radar-pipeline` 或其展开结果直接送入 `crontab -`" in readme
    assert "sed \"s|/path/to/ai-radar|$PWD|g\" deploy/cron/ai-radar-pipeline | crontab -" not in readme
    assert "*/15 * * * *" in readme
    assert "launchd 备选模板" in readme
    assert ".env" in readme
