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
    assert "alert=skip:missing FEISHU_GENERAL_ALERT_WEBHOOK" in result.stdout
    assert "tunnel=skip:missing deploy/cloudflared/config.yml" in result.stdout


def test_install_dependency_decision_installs_when_project_env_has_deps(tmp_path: Path) -> None:
    repo_root = _minimal_repo_root(
        tmp_path,
        tunnel_config=True,
        env_text="\n".join(
            [
                "OPENAI_API_KEY=sk-project",
                "FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/project",
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
