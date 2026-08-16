from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _current_boot_id() -> str:
    linux_boot_id = Path("/proc/sys/kernel/random/boot_id")
    if linux_boot_id.exists():
        return linux_boot_id.read_text(encoding="utf-8").strip()
    return subprocess.run(
        ["/usr/sbin/sysctl", "-n", "kern.boottime"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _process_start_identity(pid: int) -> str:
    return " ".join(
        subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "lstart="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
    )


def _write_live_pipeline_owner(lock_dir: Path, pid: int) -> None:
    (lock_dir / "owner").write_text(
        (
            f"token=test-owner\npid={pid}\nboot_id={_current_boot_id()}\n"
            f"process_start={_process_start_identity(pid)}\n"
        ),
        encoding="utf-8",
    )
    (lock_dir / "pid").write_text(f"{pid}\n", encoding="utf-8")


def _copy_pipeline_fixture(tmp_path: Path, *, fail_stage: str | None = None) -> tuple[Path, dict[str, str]]:
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
            exit 0
            """
        ),
        encoding="utf-8",
    )
    run_sh.chmod(0o755)

    env = os.environ.copy()
    if fail_stage:
        env["FAIL_STAGE"] = fail_stage
    else:
        env.pop("FAIL_STAGE", None)
    return script, env


def test_pipeline_script_runs_stages_in_order_and_logs_success(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines() == [
        "fetch",
        "prefilter --since 24h",
        "score --since 24h",
        "enrich --since 24h",
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


def test_pipeline_script_continues_after_stage_failure(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path, fail_stage="prefilter")

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 1
    assert (tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines() == [
        "fetch",
        "prefilter --since 24h",
        "score --since 24h",
        "enrich --since 24h",
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
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    _write_live_pipeline_owner(lock_dir, os.getpid())

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "run-calls.log").exists()
    log_text = next((tmp_path / "logs").glob("pipeline-*.log")).read_text(encoding="utf-8")
    assert f"=== pipeline SKIP: already running pid={os.getpid()} ===" in log_text


def test_pipeline_reclaims_reused_pid_with_mismatched_owner_identity(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    (lock_dir / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (lock_dir / "owner").write_text(
        (
            f"token=old-owner\npid={os.getpid()}\n"
            "boot_id=different-boot\nprocess_start=different-process-start\n"
        ),
        encoding="utf-8",
    )
    stale = time.time() - 60
    os.utime(lock_dir, (stale, stale))

    result = subprocess.run(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6
    assert not lock_dir.exists()


def test_pipeline_prepares_complete_owner_before_publishing_canonical_lock(
    tmp_path: Path,
) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    home = tmp_path / "home"
    fake_bin = home / ".local" / "bin"
    fake_bin.mkdir(parents=True)
    real_mktemp = shutil.which("mktemp")
    real_mv = shutil.which("mv")
    assert real_mktemp is not None
    assert real_mv is not None
    publish_ready = tmp_path / "publish-ready"
    publish_release = tmp_path / "publish-release"

    fake_mktemp = fake_bin / "mktemp"
    fake_mktemp.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            if [[ "$*" == *".pipeline-owner."* ]]; then
              touch "$PUBLISH_READY"
              while [[ ! -e "$PUBLISH_RELEASE" ]]; do sleep 0.01; done
            fi
            exec {real_mktemp!r} "$@"
            """
        ),
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            if [[ "$*" == *".pipeline.lock.acquire."* ]]; then
              touch "$PUBLISH_READY"
              while [[ ! -e "$PUBLISH_RELEASE" ]]; do sleep 0.01; done
            fi
            exec {real_mv!r} "$@"
            """
        ),
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)
    env.update(
        {
            "HOME": str(home),
            "PUBLISH_READY": str(publish_ready),
            "PUBLISH_RELEASE": str(publish_release),
        }
    )

    process = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    lock_dir = tmp_path / ".pipeline.lock"
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not publish_ready.exists():
            time.sleep(0.01)
        assert publish_ready.exists(), "initializer did not pause before atomic publication"
        assert not lock_dir.exists(), "canonical lock was visible before its owner was complete"
        candidates = list(tmp_path.glob(".pipeline.lock.acquire.*/.pipeline.lock"))
        assert len(candidates) == 1
        owner = (candidates[0] / "owner").read_text(encoding="utf-8")
        assert "token=" in owner
        assert "generation=" in owner
        assert "pid=" in owner
        assert "boot_id=" in owner
        assert "process_start=" in owner
        assert (candidates[0] / "pid").read_text(encoding="utf-8").strip()
        publish_release.touch()
        stdout, stderr = process.communicate(timeout=5)
    finally:
        publish_release.touch()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)

    assert process.returncode == 0, stdout + stderr
    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6


def test_pipeline_reclaims_ownerless_lock_with_future_mtime(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    future = time.time() + 24 * 60 * 60
    os.utime(lock_dir, (future, future))

    result = subprocess.run(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6
    assert not lock_dir.exists()


def test_concurrent_stale_reclaimers_do_not_steal_successor_owner(
    tmp_path: Path,
) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    future = time.time() + 24 * 60 * 60
    os.utime(lock_dir, (future, future))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_mv = shutil.which("mv")
    assert real_mv is not None
    first_mv_claim = tmp_path / "first-mv-claim"
    second_mv_attempt = tmp_path / "second-mv-attempt"
    owner_ready = tmp_path / "owner-ready"
    release_owner = tmp_path / "release-owner"
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            relevant=0
            for argument in "$@"; do
              if [[ "$argument" == "$PIPELINE_LOCK_DIR" ]]; then
                relevant=1
              fi
            done
            if [[ "$relevant" == "1" ]]; then
              if mkdir "$FIRST_MV_CLAIM" 2>/dev/null; then
                exec {real_mv!r} "$@"
              fi
              touch "$SECOND_MV_ATTEMPT"
              while [[ ! -e "$OWNER_READY" ]]; do sleep 0.01; done
            fi
            exec {real_mv!r} "$@"
            """
        ),
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)
    run_sh = tmp_path / "run.sh"
    first_stage_claim = tmp_path / "first-stage-claim"
    successor_stage = tmp_path / "successor-stage"
    run_sh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -eu
            if [[ "${1:-}" == "fetch" ]]; then
              if mkdir "$FIRST_STAGE_CLAIM" 2>/dev/null; then
                touch "$OWNER_READY"
              else
                touch "$SUCCESSOR_STAGE"
              fi
              while [[ ! -e "$RELEASE_OWNER" ]]; do sleep 0.01; done
            fi
            printf '%s\n' "$*" >> run-calls.log
            """
        ),
        encoding="utf-8",
    )
    run_sh.chmod(0o755)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "PIPELINE_LOCK_DIR": str(lock_dir),
            "FIRST_MV_CLAIM": str(first_mv_claim),
            "SECOND_MV_ATTEMPT": str(second_mv_attempt),
            "OWNER_READY": str(owner_ready),
            "RELEASE_OWNER": str(release_owner),
            "FIRST_STAGE_CLAIM": str(first_stage_claim),
            "SUCCESSOR_STAGE": str(successor_stage),
        }
    )
    first = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not (
            second_mv_attempt.exists() and owner_ready.exists()
        ):
            time.sleep(0.01)
        assert second_mv_attempt.exists(), "second reclaimer did not reach the rename fence"
        assert owner_ready.exists(), "winning owner did not start"
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not successor_stage.exists():
            time.sleep(0.01)
        release_owner.touch()
        first_stdout, first_stderr = first.communicate(timeout=5)
        second_stdout, second_stderr = second.communicate(timeout=5)
    finally:
        release_owner.touch()
        for process in (first, second):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)

    assert first.returncode == second.returncode == 0, (
        first_stdout + first_stderr + second_stdout + second_stderr
    )
    assert not successor_stage.exists()
    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6


def test_stale_judgment_cannot_reclaim_a_new_successor_generation(
    tmp_path: Path,
) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    future = time.time() + 24 * 60 * 60
    os.utime(lock_dir, (future, future))

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_stat = shutil.which("stat")
    assert real_stat is not None
    generation_claim = tmp_path / "generation-claim"
    stale_judgment_paused = tmp_path / "stale-judgment-paused"
    release_stale_judgment = tmp_path / "release-stale-judgment"
    owner_ready = tmp_path / "owner-ready"
    release_owner = tmp_path / "release-owner"
    fake_stat = fake_bin / "stat"
    fake_stat.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            if [[ "$*" == *"%d-%i-%c"* || "$*" == *"%d-%i-%Z"* ]]; then
              if mkdir "$GENERATION_CLAIM" 2>/dev/null; then
                touch "$STALE_JUDGMENT_PAUSED"
                while [[ ! -e "$RELEASE_STALE_JUDGMENT" ]]; do sleep 0.01; done
              fi
            fi
            exec {real_stat!r} "$@"
            """
        ),
        encoding="utf-8",
    )
    fake_stat.chmod(0o755)

    run_sh = tmp_path / "run.sh"
    first_stage_claim = tmp_path / "first-stage-claim"
    successor_stage = tmp_path / "successor-stage"
    run_sh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -eu
            if [[ "${1:-}" == "fetch" ]]; then
              if mkdir "$FIRST_STAGE_CLAIM" 2>/dev/null; then
                touch "$OWNER_READY"
              else
                touch "$SUCCESSOR_STAGE"
              fi
              while [[ ! -e "$RELEASE_OWNER" ]]; do sleep 0.01; done
            fi
            printf '%s\n' "$*" >> run-calls.log
            """
        ),
        encoding="utf-8",
    )
    run_sh.chmod(0o755)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "GENERATION_CLAIM": str(generation_claim),
            "STALE_JUDGMENT_PAUSED": str(stale_judgment_paused),
            "RELEASE_STALE_JUDGMENT": str(release_stale_judgment),
            "OWNER_READY": str(owner_ready),
            "RELEASE_OWNER": str(release_owner),
            "FIRST_STAGE_CLAIM": str(first_stage_claim),
            "SUCCESSOR_STAGE": str(successor_stage),
        }
    )

    stale_observer = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    successor: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not stale_judgment_paused.exists():
            time.sleep(0.01)
        assert stale_judgment_paused.exists(), "stale observer did not pause before generation read"

        successor = subprocess.Popen(
            [str(script)],
            cwd="/",
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not owner_ready.exists():
            time.sleep(0.01)
        assert owner_ready.exists(), "successor did not replace the stale generation"

        successor_owner = (lock_dir / "owner").read_text(encoding="utf-8")
        release_stale_judgment.touch()
        deadline = time.monotonic() + 3
        while (
            time.monotonic() < deadline
            and stale_observer.poll() is None
            and not successor_stage.exists()
        ):
            time.sleep(0.01)
        assert not successor_stage.exists(), "stale observer stole the live successor lock"
        assert stale_observer.poll() == 0
        assert lock_dir.exists()
        assert (lock_dir / "owner").read_text(encoding="utf-8") == successor_owner

        release_owner.touch()
        stale_stdout, stale_stderr = stale_observer.communicate(timeout=5)
        assert stale_observer.returncode == 0, stale_stdout + stale_stderr
        successor_stdout, successor_stderr = successor.communicate(timeout=5)
        assert successor.returncode == 0, successor_stdout + successor_stderr
    finally:
        release_stale_judgment.touch()
        release_owner.touch()
        for process in (stale_observer, successor):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=2)

    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6


def test_pipeline_exit_trap_does_not_delete_successor_owned_lock(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    run_sh = tmp_path / "run.sh"
    run_sh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -eu
            printf '%s\\n' "$*" >> run-calls.log
            if [[ ! -f successor-installed ]]; then
              touch successor-installed
              rm -rf .pipeline.lock
              mkdir .pipeline.lock
              printf '%s\\n' successor-owner > .pipeline.lock/owner
              printf '%s\\n' "$$" > .pipeline.lock/pid
            fi
            """
        ),
        encoding="utf-8",
    )
    run_sh.chmod(0o755)

    result = subprocess.run(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".pipeline.lock" / "owner").read_text().strip() == "successor-owner"


def test_reclaimer_cannot_steal_atomically_published_live_initializer(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    home = tmp_path / "home"
    fake_bin = home / ".local" / "bin"
    fake_bin.mkdir(parents=True)
    real_mv = shutil.which("mv")
    assert real_mv is not None
    publish_claim = tmp_path / "publish-claim"
    publish_ready = tmp_path / "publish-ready"
    publish_release = tmp_path / "publish-release"
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            candidate=""
            for argument in "$@"; do
              if [[ "$argument" == *".pipeline.lock.acquire."*"/.pipeline.lock" ]]; then
                candidate="$argument"
              fi
            done
            if [[ -n "$candidate" ]]; then
              {real_mv!r} "$@"
              status=$?
              if [[ "$status" == "0" && ! -e "$candidate" ]] && mkdir "$PUBLISH_CLAIM" 2>/dev/null; then
                touch "$PUBLISH_READY"
                while [[ ! -e "$PUBLISH_RELEASE" ]]; do sleep 0.01; done
              fi
              exit "$status"
            fi
            exec {real_mv!r} "$@"
            """
        ),
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)
    env.update(
        {
            "HOME": str(home),
            "PUBLISH_CLAIM": str(publish_claim),
            "PUBLISH_READY": str(publish_ready),
            "PUBLISH_RELEASE": str(publish_release),
        }
    )

    initializer = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    contender: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not publish_ready.exists():
            time.sleep(0.01)
        assert publish_ready.exists(), "initializer did not pause after atomic publication"
        lock_dir = tmp_path / ".pipeline.lock"
        assert lock_dir.exists()
        published_owner = (lock_dir / "owner").read_text(encoding="utf-8")
        assert "generation=" in published_owner
        assert (lock_dir / "pid").read_text(encoding="utf-8").strip()

        contender = subprocess.Popen(
            [str(script)],
            cwd="/",
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        contender_stdout, contender_stderr = contender.communicate(timeout=5)
        assert contender.returncode == 0, contender_stdout + contender_stderr
        assert "already running" in contender_stdout
        assert (lock_dir / "owner").read_text(encoding="utf-8") == published_owner

        publish_release.touch()
        initializer_stdout, initializer_stderr = initializer.communicate(timeout=5)
    finally:
        publish_release.touch()
        for process in (initializer, contender):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=2)

    assert initializer.returncode == 0, initializer_stdout + initializer_stderr
    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6


def test_concurrent_initializers_publish_only_one_canonical_lock(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    owner_ready = tmp_path / "owner-ready"
    release_owner = tmp_path / "release-owner"
    first_stage_claim = tmp_path / "first-stage-claim"
    successor_stage = tmp_path / "successor-stage"
    run_sh = tmp_path / "run.sh"
    run_sh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -eu
            if [[ "${1:-}" == "fetch" ]]; then
              if mkdir "$FIRST_STAGE_CLAIM" 2>/dev/null; then
                touch "$OWNER_READY"
              else
                touch "$SUCCESSOR_STAGE"
              fi
              while [[ ! -e "$RELEASE_OWNER" ]]; do sleep 0.01; done
            fi
            printf '%s\n' "$*" >> run-calls.log
            """
        ),
        encoding="utf-8",
    )
    run_sh.chmod(0o755)
    env.update(
        {
            "OWNER_READY": str(owner_ready),
            "RELEASE_OWNER": str(release_owner),
            "FIRST_STAGE_CLAIM": str(first_stage_claim),
            "SUCCESSOR_STAGE": str(successor_stage),
        }
    )
    first = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not owner_ready.exists():
            time.sleep(0.01)
        assert owner_ready.exists(), "neither initializer published the canonical lock"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and first.poll() is None and second.poll() is None:
            time.sleep(0.01)
        assert not successor_stage.exists()
        release_owner.touch()
        first_stdout, first_stderr = first.communicate(timeout=5)
        second_stdout, second_stderr = second.communicate(timeout=5)
    finally:
        release_owner.touch()
        for process in (first, second):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)

    assert first.returncode == second.returncode == 0, (
        first_stdout + first_stderr + second_stdout + second_stderr
    )
    assert not successor_stage.exists()
    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6


def test_pipeline_crash_mid_acquire_leaves_only_ignorable_candidate(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    home = tmp_path / "home"
    fake_bin = home / ".local" / "bin"
    fake_bin.mkdir(parents=True)
    real_mv = shutil.which("mv")
    assert real_mv is not None
    publish_ready = tmp_path / "publish-ready"
    publish_release = tmp_path / "publish-release"
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -eu
            if [[ "${{BLOCK_BEFORE_PUBLISH:-}}" == "1" && "$*" == *".pipeline.lock.acquire."* ]]; then
              touch "$PUBLISH_READY"
              while [[ ! -e "$PUBLISH_RELEASE" ]]; do sleep 0.01; done
            fi
            exec {real_mv!r} "$@"
            """
        ),
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)
    env.update(
        {
            "HOME": str(home),
            "BLOCK_BEFORE_PUBLISH": "1",
            "PUBLISH_READY": str(publish_ready),
            "PUBLISH_RELEASE": str(publish_release),
        }
    )

    crashed = subprocess.Popen(
        [str(script)],
        cwd="/",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not publish_ready.exists():
            time.sleep(0.01)
        assert publish_ready.exists(), "initializer did not pause before atomic publication"
        assert not (tmp_path / ".pipeline.lock").exists()
        os.killpg(crashed.pid, signal.SIGKILL)
        crashed.wait(timeout=3)
    finally:
        publish_release.touch()
        if crashed.poll() is None:
            os.killpg(crashed.pid, signal.SIGKILL)
            crashed.wait(timeout=2)

    candidates = list(tmp_path.glob(".pipeline.lock.acquire.*/.pipeline.lock"))
    assert len(candidates) == 1
    assert (candidates[0] / "owner").exists()
    assert not (tmp_path / ".pipeline.lock").exists()

    recovery_env = {**env, "BLOCK_BEFORE_PUBLISH": "0"}
    recovery = subprocess.run(
        [str(script)],
        cwd="/",
        env=recovery_env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert recovery.returncode == 0, recovery.stdout + recovery.stderr
    assert len((tmp_path / "run-calls.log").read_text(encoding="utf-8").splitlines()) == 6


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
