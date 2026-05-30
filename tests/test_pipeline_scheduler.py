from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    ]
    log_text = next((tmp_path / "logs").glob("pipeline-*.log")).read_text(encoding="utf-8")
    assert "=== prefilter FAIL (exit 42) ===" in log_text
    assert "=== score START ===" in log_text
    assert "=== curate OK ===" in log_text
    assert "=== PIPELINE DONE (failed=1) ===" in log_text


def test_pipeline_script_skips_when_another_pipeline_is_running(tmp_path: Path) -> None:
    script, env = _copy_pipeline_fixture(tmp_path)
    lock_dir = tmp_path / ".pipeline.lock"
    lock_dir.mkdir()
    (lock_dir / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    result = subprocess.run([str(script)], cwd="/", env=env, text=True, capture_output=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "run-calls.log").exists()
    log_text = next((tmp_path / "logs").glob("pipeline-*.log")).read_text(encoding="utf-8")
    assert f"=== pipeline SKIP: already running pid={os.getpid()} ===" in log_text


def test_pipeline_deploy_cron_entry_uses_absolute_fifteen_minute_schedule() -> None:
    cron_file = REPO_ROOT / "deploy" / "cron" / "ai-radar-pipeline"
    assert cron_file.exists(), "deploy/cron/ai-radar-pipeline should exist"

    lines = [
        line.strip()
        for line in cron_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" in lines
    assert f"*/15 * * * * {REPO_ROOT / 'pipeline.sh'} >/dev/null 2>&1" in lines


def test_pipeline_launchd_fallback_runs_every_fifteen_minutes() -> None:
    plist = (REPO_ROOT / "deploy" / "launchd" / "ai-radar-pipeline.plist.example").read_text(encoding="utf-8")

    assert "<string>live.aiplanet.ai-radar.pipeline</string>" in plist
    assert f"<string>{REPO_ROOT / 'pipeline.sh'}</string>" in plist
    assert "<key>StartInterval</key><integer>900</integer>" in plist
    assert f"<key>WorkingDirectory</key><string>{REPO_ROOT}</string>" in plist


def test_readme_documents_automatic_scheduler_setup() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "## 自动化调度" in readme
    assert "./pipeline.sh" in readme
    assert "crontab deploy/cron/ai-radar-pipeline" in readme
    assert "*/15 * * * *" in readme
    assert "launchctl bootstrap" in readme
    assert "~/.claude/.env" in readme
