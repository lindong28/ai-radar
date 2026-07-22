from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_services_helper(script: str, tmp_path: Path, repo_root: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TEST_REPO_ROOT"] = str(repo_root)
    env["HOME"] = str(home)
    for key in (
        "DEEPSEEK_API_KEY",
        "ARK_API_KEY",
        "OPENAI_API_KEY",
        "GLM_API_KEY",
        "FEISHU_GENERAL_ALERT_WEBHOOK",
        "FEISHU_GENERAL_NOTIFICATION_WEBHOOK",
    ):
        env.pop(key, None)
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _minimal_repo_root(tmp_path: Path, *, tunnel_config: bool = False, env_text: str = "") -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "deploy" / "cloudflared").mkdir(parents=True)
    (repo_root / "deploy" / "cloudflared" / "config.yml.example").write_text(
        "tunnel: your-tunnel-id\n",
        encoding="utf-8",
    )
    if tunnel_config:
        (repo_root / "deploy" / "cloudflared" / "config.yml").write_text(
            "tunnel: test-tunnel\n",
            encoding="utf-8",
        )
    if env_text:
        (repo_root / ".env").write_text(env_text, encoding="utf-8")
    return repo_root


def test_install_dependency_decision_skips_missing_fresh_clone_noninteractive(tmp_path: Path) -> None:
    repo_root = _minimal_repo_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    script = textwrap.dedent(
        """\
        source deploy/lib/services.sh
        REPO_ROOT="$TEST_REPO_ROOT"
        for slug in serve pipeline alert tunnel; do
          if ensure_install_dependency "$slug" >/dev/null; then
            printf '%s=install\\n' "$slug"
          else
            printf '%s=skip:%s\\n' "$slug" "$SERVICE_DEPENDENCY_SKIP_REASON"
          fi
        done
        """
    )

    result = _run_services_helper(script, tmp_path, repo_root, home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "serve=install" in result.stdout
    assert "pipeline=skip:missing one of DEEPSEEK_API_KEY, ARK_API_KEY, OPENAI_API_KEY, GLM_API_KEY" in result.stdout
    assert (
        "alert=skip:missing FEISHU_GENERAL_ALERT_WEBHOOK, FEISHU_GENERAL_NOTIFICATION_WEBHOOK"
        in result.stdout
    )
    assert "tunnel=skip:missing deploy/cloudflared/config.yml" in result.stdout


def test_install_dependency_decision_installs_when_project_env_has_deps(tmp_path: Path) -> None:
    repo_root = _minimal_repo_root(
        tmp_path,
        tunnel_config=True,
        env_text="\n".join(
            [
                "OPENAI_API_KEY=sk-project",
                "FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/project",
                "FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/hook/project-notice",
            ]
        ),
    )
    home = tmp_path / "home"
    home.mkdir()
    script = textwrap.dedent(
        """\
        source deploy/lib/services.sh
        REPO_ROOT="$TEST_REPO_ROOT"
        for slug in serve pipeline alert tunnel; do
          if ensure_install_dependency "$slug" >/dev/null; then
            printf '%s=install\\n' "$slug"
          else
            printf '%s=skip:%s\\n' "$slug" "$SERVICE_DEPENDENCY_SKIP_REASON"
          fi
        done
        """
    )

    result = _run_services_helper(script, tmp_path, repo_root, home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "serve=install",
        "pipeline=install",
        "alert=install",
        "tunnel=install",
    ]


def test_install_dependency_decision_uses_claude_env_without_prompt(tmp_path: Path) -> None:
    repo_root = _minimal_repo_root(tmp_path, tunnel_config=True)
    home = tmp_path / "home"
    claude_env = home / ".claude" / ".env"
    claude_env.parent.mkdir(parents=True)
    claude_env.write_text(
        "\n".join(
            [
                "ARK_API_KEY=ark-owner",
                "FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/owner",
                "FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/hook/owner-notice",
            ]
        ),
        encoding="utf-8",
    )
    script = textwrap.dedent(
        """\
        source deploy/lib/services.sh
        REPO_ROOT="$TEST_REPO_ROOT"
        for slug in serve pipeline alert tunnel; do
          if ensure_install_dependency "$slug" >/dev/null; then
            printf '%s=install\\n' "$slug"
          else
            printf '%s=skip:%s\\n' "$slug" "$SERVICE_DEPENDENCY_SKIP_REASON"
          fi
        done
        """
    )

    result = _run_services_helper(script, tmp_path, repo_root, home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "serve=install",
        "pipeline=install",
        "alert=install",
        "tunnel=install",
    ]
    assert result.stderr == ""


def test_alert_install_dependency_rejects_each_missing_webhook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    script = textwrap.dedent(
        """\
        source deploy/lib/services.sh
        REPO_ROOT="$TEST_REPO_ROOT"
        if ensure_install_dependency alert >/dev/null; then
          printf 'install\n'
        else
          printf 'skip:%s\n' "$SERVICE_DEPENDENCY_SKIP_REASON"
        fi
        """
    )

    missing_notice = _minimal_repo_root(
        tmp_path / "missing-notice",
        env_text="FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/alert\n",
    )
    notice_result = _run_services_helper(script, tmp_path, missing_notice, home)

    missing_alert = _minimal_repo_root(
        tmp_path / "missing-alert",
        env_text="FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/hook/notice\n",
    )
    alert_result = _run_services_helper(script, tmp_path, missing_alert, home)

    assert notice_result.stdout == "skip:missing FEISHU_GENERAL_NOTIFICATION_WEBHOOK; stdin is not a TTY\n"
    assert alert_result.stdout == "skip:missing FEISHU_GENERAL_ALERT_WEBHOOK; stdin is not a TTY\n"


def test_alert_install_dependency_interactively_collects_both_webhooks(tmp_path: Path) -> None:
    repo_root = _minimal_repo_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["TEST_REPO_ROOT"] = str(repo_root)
    env["HOME"] = str(home)
    env.pop("FEISHU_GENERAL_ALERT_WEBHOOK", None)
    env.pop("FEISHU_GENERAL_NOTIFICATION_WEBHOOK", None)
    script = textwrap.dedent(
        """\
        source deploy/lib/services.sh
        REPO_ROOT="$TEST_REPO_ROOT"
        ensure_install_dependency alert
        """
    )
    master_fd, slave_fd = os.openpty()
    try:
        process = subprocess.Popen(
            ["bash", "-lc", script],
            cwd=REPO_ROOT,
            env=env,
            stdin=slave_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        os.write(
            master_fd,
            b"https://open.feishu.cn/hook/alert-entered\nhttps://open.feishu.cn/hook/notice-entered\n",
        )
        stdout, stderr = process.communicate(timeout=10)
    finally:
        os.close(master_fd)
        if slave_fd >= 0:
            os.close(slave_fd)

    assert process.returncode == 0, stdout + stderr
    env_text = (repo_root / ".env").read_text(encoding="utf-8")
    assert "FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/alert-entered" in env_text
    assert "FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/hook/notice-entered" in env_text


def test_append_runtime_env_value_writes_project_env(tmp_path: Path) -> None:
    repo_root = _minimal_repo_root(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    script = textwrap.dedent(
        """\
        source deploy/lib/services.sh
        REPO_ROOT="$TEST_REPO_ROOT"
        append_runtime_env_value DEEPSEEK_API_KEY sk-entered
        runtime_env_value DEEPSEEK_API_KEY
        """
    )

    result = _run_services_helper(script, tmp_path, repo_root, home)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "sk-entered"
    assert (repo_root / ".env").read_text(encoding="utf-8") == "\nDEEPSEEK_API_KEY=sk-entered\n"
