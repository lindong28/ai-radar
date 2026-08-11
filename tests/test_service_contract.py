from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _allow_fixture_home(services_path: Path, home: Path) -> None:
    canonical_home = str(home.resolve())
    with services_path.open("a", encoding="utf-8") as handle:
        handle.write(
            textwrap.dedent(
                f"""

                # Test fixture only: production validate_user_home remains allowlist-only.
                eval "$(declare -f validate_user_home | sed \
                  '1s/^validate_user_home/production_validate_user_home/')"
                validate_user_home() {{
                  local canonical_home
                  if [[ -z "${{HOME:-}}" || "$HOME" != /* ]]; then
                    production_validate_user_home
                    return
                  fi
                  canonical_home="$(canonicalize_user_path "$HOME")" || return 1
                  if [[ "$canonical_home" == {shlex.quote(canonical_home)} ]]; then
                    return 0
                  fi
                  production_validate_user_home
                }}
                """
            )
        )


def test_alert_service_is_registered_in_service_helpers() -> None:
    script = """
    source deploy/lib/services.sh
    printf 'services=%s\\n' "${ALL_SERVICES[*]}"
    printf 'label=%s\\n' "$(service_label alert)"
    printf 'plist=%s\\n' "$(service_plist_name alert)"
    printf 'desc=%s\\n' "$(service_desc alert)"
    """

    result = subprocess.run(["bash", "-lc", script], cwd=REPO_ROOT, text=True, capture_output=True, check=True)

    assert "services=serve tunnel pipeline alert performance-probe" in result.stdout
    assert "label=live.aiplanet.ai-radar.alert" in result.stdout
    assert "plist=ai-radar-alert.plist" in result.stdout
    assert "desc=Monitoring alert check" in result.stdout


def test_alert_launchd_template_runs_alert_check_every_five_minutes() -> None:
    plist = (REPO_ROOT / "deploy/launchd/ai-radar-alert.plist.example").read_text(encoding="utf-8")
    services = (REPO_ROOT / "deploy/lib/services.sh").read_text(encoding="utf-8")

    assert "<string>live.aiplanet.ai-radar.alert</string>" in plist
    assert 'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" ./run.sh admin alert-check' in plist
    assert "__AI_RADAR_ALERT_ENVIRONMENT__" in plist
    assert "FEISHU_GENERAL_ALERT_WEBHOOK" in services
    assert "FEISHU_GENERAL_NOTIFICATION_WEBHOOK" in services
    assert "AI_RADAR_DB" in services
    assert "<key>EnvironmentVariables</key>" in services
    assert "<key>StartInterval</key><integer>300</integer>" in plist
    assert "<key>KeepAlive</key>" not in plist


@pytest.mark.parametrize(
    ("slug", "label"),
    [
        ("serve", "live.aiplanet.ai-radar.serve"),
        ("tunnel", "live.aiplanet.ai-radar.tunnel"),
        ("alert", "live.aiplanet.ai-radar.alert"),
        ("performance-probe", "live.aiplanet.ai-radar.performance-probe"),
    ],
)
def test_managed_launchd_templates_have_exact_owner_marker_and_label(
    slug: str,
    label: str,
) -> None:
    template = (REPO_ROOT / f"deploy/launchd/ai-radar-{slug}.plist.example").read_text(
        encoding="utf-8"
    )

    assert "<!-- ai-radar:managed-launch-agent:v1 -->" in template
    assert f"<key>Label</key><string>{label}</string>" in template


def test_performance_probe_launchd_template_runs_watchdog_entry_every_five_minutes(
    tmp_path: Path,
) -> None:
    template_path = REPO_ROOT / "deploy/launchd/ai-radar-performance-probe.plist.example"
    template = template_path.read_text(encoding="utf-8")

    assert "<string>live.aiplanet.ai-radar.performance-probe</string>" in template
    assert "<key>StartInterval</key><integer>300</integer>" in template
    assert "./run.sh performance-probe" in template
    assert "mkdir -p logs" in template
    assert "ai-radar:managed-launch-agent:v1" in template

    repo = tmp_path / "repo"
    repo.mkdir()
    started = tmp_path / "probe-started"
    run_sh = repo / "run.sh"
    run_sh.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > \"$PROBE_STARTED\"\n",
        encoding="utf-8",
    )
    run_sh.chmod(0o755)
    rendered = template.replace("/path/to/ai-radar", str(repo))
    plist = plistlib.loads(rendered.encode())
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["PROBE_STARTED"] = str(started)

    result = subprocess.run(
        plist["ProgramArguments"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert started.read_text(encoding="utf-8").strip() == "performance-probe"
    assert (repo / "logs").is_dir()


def test_launch_agent_lifecycle_has_no_custom_mutex_symlink_cas_or_probe_cron() -> None:
    lifecycle = "\n".join(
        (REPO_ROOT / path).read_text(encoding="utf-8")
        for path in ("deploy/lib/services.sh", "install.sh", "uninstall.sh")
    )
    journey_monitor = (
        REPO_ROOT / "src/airadar/performance/journey_monitor.py"
    ).read_text(encoding="utf-8")

    assert ".ai-radar-lock" not in lifecycle
    assert ".ai-radar-owner" not in lifecycle
    assert "flock" not in lifecycle
    assert "ensure_launch_agent_symlink" not in lifecycle
    assert "restore_launch_agent_symlink" not in lifecycle
    assert "CRONTAB_SAMPLE" not in journey_monitor
    assert not (REPO_ROOT / "deploy/cron/ai-radar-performance-probe").exists()


def test_alert_launchd_environment_xml_includes_webhook_and_optional_db() -> None:
    script = """
    source deploy/lib/services.sh
    FEISHU_GENERAL_ALERT_WEBHOOK='http://127.0.0.1:8765/hook?a=1&b=2' \
    FEISHU_GENERAL_NOTIFICATION_WEBHOOK='http://127.0.0.1:8765/notice?a=3&b=4' \
    AI_RADAR_DB='/tmp/ai-radar-alert-prod-path.db' \
    alert_environment_xml
    """

    result = subprocess.run(["bash", "-lc", script], cwd=REPO_ROOT, text=True, capture_output=True, check=True)

    assert "<key>FEISHU_GENERAL_ALERT_WEBHOOK</key>" in result.stdout
    assert "http://127.0.0.1:8765/hook?a=1&amp;b=2" in result.stdout
    assert "<key>FEISHU_GENERAL_NOTIFICATION_WEBHOOK</key>" in result.stdout
    assert "http://127.0.0.1:8765/notice?a=3&amp;b=4" in result.stdout
    assert "<key>AI_RADAR_DB</key>" in result.stdout
    assert "/tmp/ai-radar-alert-prod-path.db" in result.stdout


def test_alert_launchd_environment_refuses_partial_webhook_configuration(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env.pop("FEISHU_GENERAL_ALERT_WEBHOOK", None)
    env.pop("FEISHU_GENERAL_NOTIFICATION_WEBHOOK", None)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    script = f"""
    source deploy/lib/services.sh
    REPO_ROOT='{repo_root}'
    FEISHU_GENERAL_ALERT_WEBHOOK='http://127.0.0.1:8765/alert' alert_environment_xml
    """

    result = subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "missing FEISHU_GENERAL_NOTIFICATION_WEBHOOK" in result.stderr


def test_alert_plist_generation_does_not_write_partial_service(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env.pop("FEISHU_GENERAL_ALERT_WEBHOOK", None)
    env.pop("FEISHU_GENERAL_NOTIFICATION_WEBHOOK", None)
    repo_root = tmp_path / "repo"
    launchd_dir = repo_root / "deploy" / "launchd"
    launchd_dir.mkdir(parents=True)
    (launchd_dir / "ai-radar-alert.plist.example").write_text(
        "<plist>\n  <!-- __AI_RADAR_ALERT_ENVIRONMENT__ -->\n</plist>\n",
        encoding="utf-8",
    )
    script = f"""
    source deploy/lib/services.sh
    REPO_ROOT='{repo_root}'
    FEISHU_GENERAL_ALERT_WEBHOOK='http://127.0.0.1:8765/alert' ensure_plist ai-radar-alert.plist
    """

    result = subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert not (launchd_dir / "ai-radar-alert.plist").exists()
    assert "missing FEISHU_GENERAL_NOTIFICATION_WEBHOOK" in result.stderr


def test_loaded_alert_service_is_rebootstrapped_during_install(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "deploy" / "lib").mkdir(parents=True)
    (repo / "deploy" / "launchd").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "install.sh", repo / "install.sh")
    shutil.copy(REPO_ROOT / "deploy/lib/services.sh", repo / "deploy/lib/services.sh")
    shutil.copy(
        REPO_ROOT / "deploy/launchd/ai-radar-alert.plist.example",
        repo / "deploy/launchd/ai-radar-alert.plist.example",
    )
    (repo / ".env").write_text(
        "FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/alert\n"
        "FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/hook/notice\n",
        encoding="utf-8",
    )

    calls_path = tmp_path / "launchctl-calls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$LAUNCHCTL_CALLS\"\n"
        "if [[ \"${1:-}\" == \"print\" ]]; then printf 'path = %s\\n' \"$LAUNCHD_PATH\"; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    env = os.environ.copy()
    home = tmp_path / "home"
    _allow_fixture_home(repo / "deploy/lib/services.sh", home)
    installed_path = (
        home
        / "Library"
        / "LaunchAgents"
        / "live.aiplanet.ai-radar.alert.plist"
    )
    _write_owned_alert_launch_agent(installed_path)
    with installed_path.open("a", encoding="utf-8") as handle:
        handle.write("<!-- previous-version -->\n")
    env["HOME"] = str(home)
    env["LAUNCHCTL_CALLS"] = str(calls_path)
    env["LAUNCHD_PATH"] = str(installed_path)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "./install.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    label = "live.aiplanet.ai-radar.alert"
    calls = calls_path.read_text(encoding="utf-8").splitlines()
    assert calls[:3] == [
        f"print gui/{os.getuid()}/{label}",
        f"bootout gui/{os.getuid()}/{label}",
        f"bootstrap gui/{os.getuid()} {installed_path}",
    ]
    assert "reloading loaded job" in result.stdout
    assert "bootstrapped + started" in result.stdout


def test_alert_install_uninstall_round_trip_manages_launch_agent_file_and_job(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "deploy" / "lib").mkdir(parents=True)
    (repo / "deploy" / "launchd").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "install.sh", repo / "install.sh")
    shutil.copy(REPO_ROOT / "uninstall.sh", repo / "uninstall.sh")
    shutil.copy(REPO_ROOT / "deploy/lib/services.sh", repo / "deploy/lib/services.sh")
    shutil.copy(
        REPO_ROOT / "deploy/launchd/ai-radar-alert.plist.example",
        repo / "deploy/launchd/ai-radar-alert.plist.example",
    )
    (repo / ".env").write_text(
        "FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/alert\n"
        "FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/hook/notice\n",
        encoding="utf-8",
    )

    home = tmp_path / "home"
    _allow_fixture_home(repo / "deploy/lib/services.sh", home)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl_state = tmp_path / "launchctl-state"
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            command="${1:-}"
            target="${2:-}"
            case "$command" in
              print)
                loaded_path="$(cat "$LAUNCHCTL_STATE" 2>/dev/null || true)"
                if [[ -z "$loaded_path" ]]; then
                  printf 'Could not find service %s\\n' "$target" >&2
                  exit 3
                fi
                printf 'path = %s\\n' "$loaded_path"
                ;;
              bootstrap)
                realpath "${3:?missing plist path}" > "$LAUNCHCTL_STATE"
                ;;
              bootout)
                : > "$LAUNCHCTL_STATE"
                ;;
              enable|kickstart)
                ;;
              *)
                exit 2
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LAUNCHCTL_STATE"] = str(launchctl_state)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    installed = subprocess.run(
        ["bash", "./install.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stdout + installed.stderr
    label = "live.aiplanet.ai-radar.alert"
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    assert installed_path.is_file()
    assert not installed_path.is_symlink()
    assert installed_path.stat().st_mode & 0o777 == 0o600
    installed_content = installed_path.read_text(encoding="utf-8")
    assert "ai-radar:managed-launch-agent:v1" in installed_content
    assert f"<key>Label</key><string>{label}</string>" in installed_content
    loaded = subprocess.run(
        [str(fake_launchctl), "print", f"gui/{os.getuid()}/{label}"],
        env=env,
        check=False,
    )
    assert loaded.returncode == 0

    uninstalled = subprocess.run(
        ["bash", "./uninstall.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert uninstalled.returncode == 0, uninstalled.stdout + uninstalled.stderr
    assert not installed_path.exists()
    unloaded = subprocess.run(
        [str(fake_launchctl), "print", f"gui/{os.getuid()}/{label}"],
        env=env,
        check=False,
    )
    assert unloaded.returncode != 0


def _copy_performance_probe_lifecycle_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    (repo / "deploy" / "lib").mkdir(parents=True)
    (repo / "deploy" / "launchd").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "install.sh", repo / "install.sh")
    shutil.copy(REPO_ROOT / "uninstall.sh", repo / "uninstall.sh")
    shutil.copy(REPO_ROOT / "status.sh", repo / "status.sh")
    shutil.copy(REPO_ROOT / "deploy/lib/services.sh", repo / "deploy/lib/services.sh")
    shutil.copy(
        REPO_ROOT / "deploy/launchd/ai-radar-performance-probe.plist.example",
        repo / "deploy/launchd/ai-radar-performance-probe.plist.example",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl_state = tmp_path / "launchctl-state"
    launchctl_calls = tmp_path / "launchctl-calls"
    bootstrap_count = tmp_path / "launchctl-bootstrap-count"
    bootout_count = tmp_path / "launchctl-bootout-count"
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$*" >> "$LAUNCHCTL_CALLS"
            command="${1:-}"
            target="${2:-}"
            case "$command" in
              print)
                loaded_path="$(cat "$LAUNCHCTL_STATE" 2>/dev/null || true)"
                if [[ -z "$loaded_path" ]]; then
                  printf 'Could not find service %s\\n' "$target" >&2
                  exit 3
                fi
                printf 'path = %s\\n' "$loaded_path"
                ;;
              bootstrap)
                count="$(cat "$LAUNCHCTL_BOOTSTRAP_COUNT" 2>/dev/null || printf '0')"
                count=$((count + 1))
                printf '%s\\n' "$count" > "$LAUNCHCTL_BOOTSTRAP_COUNT"
                if [[ "${LAUNCHCTL_MODE:-}" == "fail-first-bootstrap" && "$count" -eq 1 ]]; then
                  exit 42
                fi
                if [[ "${LAUNCHCTL_MODE:-}" == "partial-bootstrap-rollback-bootout-fail" && "$count" -eq 1 ]]; then
                  realpath "${3:?missing plist path}" > "$LAUNCHCTL_STATE"
                  exit 42
                fi
                realpath "${3:?missing plist path}" > "$LAUNCHCTL_STATE"
                ;;
              bootout)
                count="$(cat "$LAUNCHCTL_BOOTOUT_COUNT" 2>/dev/null || printf '0')"
                count=$((count + 1))
                printf '%s\\n' "$count" > "$LAUNCHCTL_BOOTOUT_COUNT"
                if [[ "${LAUNCHCTL_MODE:-}" == "bootout-fail" ]]; then
                  exit 46
                fi
                if [[ "${LAUNCHCTL_MODE:-}" == "partial-bootstrap-rollback-bootout-fail" && "$count" -ge 2 ]]; then
                  exit 45
                fi
                : > "$LAUNCHCTL_STATE"
                ;;
              enable)
                [[ "${LAUNCHCTL_MODE:-}" != "enable-fail" ]] || exit 43
                ;;
              kickstart)
                [[ "${LAUNCHCTL_MODE:-}" != "kickstart-fail" ]] || exit 44
                ;;
              *)
                exit 2
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    home = tmp_path / "home"
    _allow_fixture_home(repo / "deploy/lib/services.sh", home)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LAUNCHCTL_STATE"] = str(launchctl_state)
    env["LAUNCHCTL_CALLS"] = str(launchctl_calls)
    env["LAUNCHCTL_BOOTSTRAP_COUNT"] = str(bootstrap_count)
    env["LAUNCHCTL_BOOTOUT_COUNT"] = str(bootout_count)
    env["LAUNCHD_LABEL"] = "live.aiplanet.ai-radar.performance-probe"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    return repo, home, launchctl_calls, env


def test_performance_probe_per_file_install_is_idempotent_then_uninstalls(
    tmp_path: Path,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(tmp_path)
    label = "live.aiplanet.ai-radar.performance-probe"
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"

    first = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    assert installed_path.is_file()
    assert not installed_path.is_symlink()
    assert installed_path.stat().st_mode & 0o777 == 0o600
    first_content = installed_path.read_bytes()
    assert b"ai-radar:managed-launch-agent:v1" in first_content
    assert (repo / "logs").is_dir()

    second = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert installed_path.read_bytes() == first_content
    assert list(installed_path.parent.glob(f"{label}.plist")) == [installed_path]
    calls = (
        launchctl_calls.read_text(encoding="utf-8").splitlines()
        if launchctl_calls.exists()
        else []
    )
    assert sum(call.startswith("bootstrap ") for call in calls) == 2
    assert (
        f"bootstrap gui/{os.getuid()} "
        f"{installed_path}"
    ) in calls

    removed = subprocess.run(
        ["bash", "./uninstall.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not installed_path.exists()
    unloaded = subprocess.run(
        [str(Path(env["PATH"].split(":", maxsplit=1)[0]) / "launchctl"), "print", f"gui/{os.getuid()}/{label}"],
        env=env,
        check=False,
    )
    assert unloaded.returncode != 0


def test_owned_legacy_launch_agent_symlink_is_migrated_to_regular_file(
    tmp_path: Path,
) -> None:
    repo, home, _launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(tmp_path)
    label = "live.aiplanet.ai-radar.performance-probe"
    generated = repo / "deploy/launchd/ai-radar-performance-probe.plist"
    template = (
        repo / "deploy/launchd/ai-radar-performance-probe.plist.example"
    ).read_text(encoding="utf-8")
    generated.write_text(template.replace("/path/to/ai-radar", str(repo)), encoding="utf-8")
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    installed_path.parent.mkdir(parents=True)
    installed_path.symlink_to(generated)

    result = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert installed_path.is_file()
    assert not installed_path.is_symlink()
    assert "ai-radar:managed-launch-agent:v1" in installed_path.read_text(encoding="utf-8")


def _prepare_loaded_legacy_probe(
    repo: Path,
    home: Path,
    env: dict[str, str],
) -> tuple[Path, Path, str]:
    label = "live.aiplanet.ai-radar.performance-probe"
    generated = repo / "deploy" / "launchd" / "ai-radar-performance-probe.plist"
    previous = (
        f"<key>Label</key><string>{label}</string>\n"
        "<!-- pre-u16-live-configuration -->\n"
    )
    generated.write_text(previous, encoding="utf-8")
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    installed_path.parent.mkdir(parents=True)
    installed_path.symlink_to(generated)
    Path(env["LAUNCHCTL_STATE"]).write_text(
        f"{generated.resolve()}\n",
        encoding="utf-8",
    )
    return generated, installed_path, previous


def _prepare_interrupted_legacy_migration(
    repo: Path,
    home: Path,
    env: dict[str, str],
) -> tuple[Path, Path]:
    label = "live.aiplanet.ai-radar.performance-probe"
    generated = repo / "deploy" / "launchd" / "ai-radar-performance-probe.plist"
    current = (
        repo / "deploy" / "launchd" / "ai-radar-performance-probe.plist.example"
    ).read_text(encoding="utf-8").replace("/path/to/ai-radar", str(repo))
    generated.write_text(current, encoding="utf-8")
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    installed_path.parent.mkdir(parents=True)
    installed_path.write_text(current, encoding="utf-8")
    Path(env["LAUNCHCTL_STATE"]).write_text(
        f"{generated.resolve()}\n",
        encoding="utf-8",
    )
    return generated, installed_path


def _prepare_loaded_generated_without_destination(
    repo: Path,
    env: dict[str, str],
) -> Path:
    generated = repo / "deploy" / "launchd" / "ai-radar-performance-probe.plist"
    current = (
        repo / "deploy" / "launchd" / "ai-radar-performance-probe.plist.example"
    ).read_text(encoding="utf-8").replace("/path/to/ai-radar", str(repo))
    generated.write_text(current, encoding="utf-8")
    Path(env["LAUNCHCTL_STATE"]).write_text(
        f"{generated.resolve()}\n",
        encoding="utf-8",
    )
    return generated


@pytest.mark.parametrize("action", ["install", "uninstall", "status"])
def test_loaded_legacy_symlink_job_is_owned_by_resolved_generated_path(
    tmp_path: Path,
    action: str,
) -> None:
    repo, home, _launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path / action
    )
    generated, installed_path, previous = _prepare_loaded_legacy_probe(
        repo, home, env
    )

    result = subprocess.run(
        ["bash", f"./{action}.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "foreign" not in (result.stdout + result.stderr)
    if action == "install":
        assert installed_path.is_file()
        assert not installed_path.is_symlink()
        assert "ai-radar:managed-launch-agent:v1" in installed_path.read_text(
            encoding="utf-8"
        )
        assert Path(env["LAUNCHCTL_STATE"]).read_text(
            encoding="utf-8"
        ).strip() == str(installed_path.resolve())
    elif action == "uninstall":
        assert not installed_path.exists()
        assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8") == ""
        assert generated.read_text(encoding="utf-8") == previous
    else:
        assert "loaded ✓" in result.stdout
        assert installed_path.is_symlink()
        assert generated.read_text(encoding="utf-8") == previous


@pytest.mark.parametrize("action", ["install", "uninstall", "status"])
def test_interrupted_legacy_migration_loaded_from_generated_is_self_healing(
    tmp_path: Path,
    action: str,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path / action
    )
    generated, installed_path = _prepare_interrupted_legacy_migration(
        repo, home, env
    )

    result = subprocess.run(
        ["bash", f"./{action}.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "foreign" not in (result.stdout + result.stderr)
    calls = launchctl_calls.read_text(encoding="utf-8").splitlines()
    if action == "install":
        assert Path(env["LAUNCHCTL_STATE"]).read_text(
            encoding="utf-8"
        ).strip() == str(installed_path.resolve())
        assert sum(call.startswith("bootout ") for call in calls) == 1
        assert sum(call.startswith("bootstrap ") for call in calls) == 1
        assert "already loaded" not in result.stdout
    elif action == "uninstall":
        assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8") == ""
        assert not installed_path.exists()
        assert generated.exists()
        assert sum(call.startswith("bootout ") for call in calls) == 1
        assert not any(call.startswith("bootstrap ") for call in calls)
    else:
        assert "migration pending" in result.stdout
        assert installed_path.is_file()
        assert Path(env["LAUNCHCTL_STATE"]).read_text(
            encoding="utf-8"
        ).strip() == str(generated.resolve())
        assert not any(
            call.startswith(("bootout ", "bootstrap ")) for call in calls
        )


def test_regular_update_interrupted_before_bootout_reloads_on_rerun(
    tmp_path: Path,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path
    )
    _generated, installed_path = _prepare_interrupted_legacy_migration(
        repo, home, env
    )
    Path(env["LAUNCHCTL_STATE"]).write_text(
        f"{installed_path.resolve()}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = launchctl_calls.read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("bootout ") for call in calls) == 1
    assert sum(call.startswith("bootstrap ") for call in calls) == 1
    assert "already loaded" not in result.stdout
    assert "bootstrapped + started" in result.stdout


@pytest.mark.parametrize("action", ["install", "uninstall", "status"])
def test_loaded_generated_path_without_owned_destination_is_not_claimed(
    tmp_path: Path,
    action: str,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path / action
    )
    generated = _prepare_loaded_generated_without_destination(repo, env)
    label = "live.aiplanet.ai-radar.performance-probe"
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"

    result = subprocess.run(
        ["bash", f"./{action}.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    calls = (
        launchctl_calls.read_text(encoding="utf-8").splitlines()
        if launchctl_calls.exists()
        else []
    )
    assert not any(call.startswith(("bootout ", "bootstrap ")) for call in calls)
    assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8").strip() == str(
        generated.resolve()
    )
    assert generated.is_file()
    assert not installed_path.exists()
    if action == "install":
        assert result.returncode != 0
        assert "label is occupied without an owned LaunchAgent destination" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "not installed" in result.stdout
        assert "loaded ✓" not in result.stdout
        assert "migration pending" not in result.stdout


@pytest.mark.parametrize("mode", ["fail-first-bootstrap", "bootout-fail"])
def test_loaded_legacy_install_failure_restores_old_source_symlink_and_job(
    tmp_path: Path,
    mode: str,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path
    )
    generated, installed_path, previous = _prepare_loaded_legacy_probe(
        repo, home, env
    )
    env["LAUNCHCTL_MODE"] = mode

    result = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert installed_path.is_symlink()
    assert installed_path.resolve() == generated.resolve()
    assert generated.read_text(encoding="utf-8") == previous
    assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8").strip() == str(
        generated.resolve()
    )
    assert "bootstrapped + started" not in result.stdout
    calls = launchctl_calls.read_text(encoding="utf-8").splitlines()
    if mode == "fail-first-bootstrap":
        assert sum(call.startswith("bootstrap ") for call in calls) == 2
        assert "restored previous launchd job" in result.stderr
    else:
        assert not any(call.startswith("bootstrap ") for call in calls)
        assert "bootout failed" in result.stderr


@pytest.mark.parametrize("action", ["install", "uninstall", "status"])
def test_foreign_loaded_job_with_same_label_is_never_touched_or_reported_owned(
    tmp_path: Path,
    action: str,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path / action
    )
    label = "live.aiplanet.ai-radar.performance-probe"
    foreign_path = tmp_path / action / "foreign" / f"{label}.plist"
    foreign_path.parent.mkdir(parents=True)
    foreign_path.write_text("<plist><dict/></plist>\n", encoding="utf-8")
    Path(env["LAUNCHCTL_STATE"]).write_text(f"{foreign_path}\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", f"./{action}.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    calls = (
        launchctl_calls.read_text(encoding="utf-8").splitlines()
        if launchctl_calls.exists()
        else []
    )
    assert not any(call.startswith(("bootout ", "bootstrap ")) for call in calls)
    assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8").strip() == str(
        foreign_path
    )
    assert not installed_path.exists()
    if action == "install":
        assert result.returncode != 0
        assert "label is occupied without an owned LaunchAgent destination" in result.stderr
    else:
        assert result.returncode == 0
        assert "not installed" in result.stdout
        assert "loaded ✓" not in result.stdout


@pytest.mark.parametrize("action", ["install", "uninstall", "status"])
@pytest.mark.parametrize("alias_kind", ["leaf", "ancestor"])
def test_foreign_loaded_symlink_alias_to_generated_is_never_claimed(
    tmp_path: Path,
    action: str,
    alias_kind: str,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path / action
    )
    generated, installed_path = _prepare_interrupted_legacy_migration(
        repo, home, env
    )
    foreign_root = tmp_path / action / "foreign"
    foreign_root.mkdir(parents=True)
    if alias_kind == "leaf":
        foreign_alias = foreign_root / "alias.plist"
        foreign_alias.symlink_to(generated)
    else:
        alias_dir = foreign_root / "alias-dir"
        alias_dir.symlink_to(generated.parent, target_is_directory=True)
        foreign_alias = alias_dir / generated.name
    Path(env["LAUNCHCTL_STATE"]).write_text(
        f"{foreign_alias}\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", f"./{action}.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    calls = launchctl_calls.read_text(encoding="utf-8").splitlines()
    assert not any(call.startswith(("bootout ", "bootstrap ")) for call in calls)
    assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8").strip() == str(
        foreign_alias
    )
    assert installed_path.is_file()
    assert foreign_alias.resolve() == generated.resolve()
    if action == "status":
        assert result.returncode == 0
        assert "foreign job" in result.stdout
        assert "loaded ✓" not in result.stdout
    else:
        assert result.returncode != 0
        assert "foreign launchd job" in result.stderr


def test_loaded_update_bootstrap_failure_restores_previous_file_and_job(
    tmp_path: Path,
) -> None:
    repo, home, launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(tmp_path)
    label = "live.aiplanet.ai-radar.performance-probe"
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    installed_path.parent.mkdir(parents=True)
    previous = (
        "<!-- ai-radar:managed-launch-agent:v1 -->\n"
        f"<key>Label</key><string>{label}</string>\n"
        "<!-- previous-version -->\n"
    )
    installed_path.write_text(previous, encoding="utf-8")
    Path(env["LAUNCHCTL_STATE"]).write_text(f"{installed_path}\n", encoding="utf-8")
    env["LAUNCHCTL_MODE"] = "fail-first-bootstrap"

    result = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    calls = launchctl_calls.read_text(encoding="utf-8").splitlines()
    assert result.returncode != 0
    assert installed_path.read_text(encoding="utf-8") == previous
    assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8").strip() == str(
        installed_path
    )
    assert sum(call.startswith("bootstrap ") for call in calls) == 2
    assert "restored previous launchd job" in result.stderr
    assert "bootstrapped + started" not in result.stdout


def test_loaded_update_does_not_restore_old_file_when_rollback_bootout_fails(
    tmp_path: Path,
) -> None:
    repo, home, _launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path
    )
    label = "live.aiplanet.ai-radar.performance-probe"
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    installed_path.parent.mkdir(parents=True)
    previous = (
        "<!-- ai-radar:managed-launch-agent:v1 -->\n"
        f"<key>Label</key><string>{label}</string>\n"
        "<!-- previous-version -->\n"
    )
    installed_path.write_text(previous, encoding="utf-8")
    Path(env["LAUNCHCTL_STATE"]).write_text(f"{installed_path}\n", encoding="utf-8")
    env["LAUNCHCTL_MODE"] = "partial-bootstrap-rollback-bootout-fail"

    result = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "rollback bootout failed" in result.stderr
    assert Path(env["LAUNCHCTL_STATE"]).read_text(encoding="utf-8").strip() == str(
        installed_path
    )
    assert installed_path.read_text(encoding="utf-8") != previous
    assert "ai-radar:managed-launch-agent:v1" in installed_path.read_text(
        encoding="utf-8"
    )
    assert "restored previous launchd job" not in result.stderr


def test_generated_plist_symlink_is_replaced_without_truncating_target(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    launchd_dir = repo / "deploy" / "launchd"
    launchd_dir.mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / "deploy/launchd/ai-radar-performance-probe.plist.example",
        launchd_dir / "ai-radar-performance-probe.plist.example",
    )
    victim = tmp_path / "victim"
    victim.write_text("do-not-touch\n", encoding="utf-8")
    generated = launchd_dir / "ai-radar-performance-probe.plist"
    generated.symlink_to(victim)
    script = f"""
    source deploy/lib/services.sh
    REPO_ROOT={str(repo)!r}
    ensure_plist ai-radar-performance-probe.plist
    """

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert victim.read_text(encoding="utf-8") == "do-not-touch\n"
    assert generated.is_file()
    assert not generated.is_symlink()
    assert str(repo) in generated.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["enable-fail", "kickstart-fail"])
def test_launchd_activation_failure_is_reported_without_started_claim(
    tmp_path: Path,
    mode: str,
) -> None:
    repo, _home, _launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path
    )
    env["LAUNCHCTL_MODE"] = mode

    result = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert mode.split("-", maxsplit=1)[0] in result.stderr
    assert "bootstrapped + started" not in result.stdout


@pytest.mark.parametrize("system_home", ["/var/root", "/etc", "/usr/local"])
def test_service_launch_agent_path_rejects_system_home(system_home: str) -> None:
    script = f"""
    source deploy/lib/services.sh
    HOME={system_home!r}
    output="$(service_launch_agent_path alert)"
    status=$?
    printf 'status=%s output=%s\\n' "$status" "$output"
    """

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "status=1 output=" in result.stdout
    assert "LaunchAgents" not in result.stdout
    assert "not an allowlisted user home" in result.stderr


def test_service_launch_agent_path_rejects_other_writable_non_user_home(
    tmp_path: Path,
) -> None:
    writable_home = tmp_path / "writable-home"
    writable_home.mkdir()
    script = f"""
    source deploy/lib/services.sh
    HOME={str(writable_home)!r}
    output="$(service_launch_agent_path alert)"
    status=$?
    printf 'status=%s output=%s\\n' "$status" "$output"
    """

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "status=1 output=" in result.stdout
    assert "LaunchAgents" not in result.stdout
    assert "not an allowlisted user home" in result.stderr


def test_uninstall_accepts_pre_marker_project_symlink_but_not_foreign_symlink(
    tmp_path: Path,
) -> None:
    repo, home, _launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(
        tmp_path
    )
    label = "live.aiplanet.ai-radar.performance-probe"
    generated = repo / "deploy" / "launchd" / "ai-radar-performance-probe.plist"
    generated.write_text(
        f"<key>Label</key><string>{label}</string>\n<!-- pre-u16 -->\n",
        encoding="utf-8",
    )
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    installed_path.parent.mkdir(parents=True)
    installed_path.symlink_to(generated)

    removed = subprocess.run(
        ["bash", "./uninstall.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not installed_path.exists()
    foreign = tmp_path / "foreign.plist"
    foreign.write_text(
        f"<key>Label</key><string>{label}</string>\n",
        encoding="utf-8",
    )
    installed_path.symlink_to(foreign)

    refused = subprocess.run(
        ["bash", "./uninstall.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode != 0
    assert installed_path.is_symlink()
    assert foreign.exists()
    assert "not owned by ai-radar" in refused.stderr


def test_foreign_same_name_launch_agent_is_never_clobbered_or_removed(
    tmp_path: Path,
) -> None:
    repo, home, _launchctl_calls, env = _copy_performance_probe_lifecycle_fixture(tmp_path)
    label = "live.aiplanet.ai-radar.performance-probe"
    installed_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    installed_path.parent.mkdir(parents=True)
    foreign = (
        "<?xml version=\"1.0\"?><plist><dict>"
        f"<key>Label</key><string>{label}</string>"
        "<key>ProgramArguments</key><array><string>/usr/bin/true</string></array>"
        "</dict></plist>\n"
    )
    installed_path.write_text(foreign, encoding="utf-8")

    installed = subprocess.run(
        ["bash", "./install.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert installed.returncode != 0
    assert installed_path.read_text(encoding="utf-8") == foreign
    assert "not owned by ai-radar" in installed.stderr

    removed = subprocess.run(
        ["bash", "./uninstall.sh", "performance-probe"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert removed.returncode != 0
    assert installed_path.read_text(encoding="utf-8") == foreign
    assert "not owned by ai-radar" in removed.stderr


def _copy_alert_lifecycle_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "deploy" / "lib").mkdir(parents=True)
    (repo / "deploy" / "launchd").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "install.sh", repo / "install.sh")
    shutil.copy(REPO_ROOT / "uninstall.sh", repo / "uninstall.sh")
    shutil.copy(REPO_ROOT / "deploy/lib/services.sh", repo / "deploy/lib/services.sh")
    _allow_fixture_home(repo / "deploy/lib/services.sh", tmp_path / "home")
    shutil.copy(
        REPO_ROOT / "deploy/launchd/ai-radar-alert.plist.example",
        repo / "deploy/launchd/ai-radar-alert.plist.example",
    )
    (repo / ".env").write_text(
        "FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/hook/alert\n"
        "FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/hook/notice\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            case "${LAUNCHCTL_MODE:?}:${1:-}" in
              bootstrap-fail:print)
                printf 'Could not find service\\n' >&2
                exit 3
                ;;
              bootstrap-fail:bootstrap)
                exit 42
                ;;
              query-fail:print)
                printf 'launchctl transport unavailable\\n' >&2
                exit 74
                ;;
              bootout-fail:print)
                printf 'path = %s\\n' "${LAUNCHD_PATH:?}"
                ;;
              bootout-fail:bootout)
                exit 75
                ;;
              *:enable|*:kickstart)
                exit 0
                ;;
              *)
                exit 2
                ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    return repo, fake_bin


def _write_owned_alert_launch_agent(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "<!-- ai-radar:managed-launch-agent:v1 -->\n"
        "<key>Label</key><string>live.aiplanet.ai-radar.alert</string>\n",
        encoding="utf-8",
    )


def test_alert_install_does_not_place_file_when_bootstrap_fails(tmp_path: Path) -> None:
    repo, fake_bin = _copy_alert_lifecycle_fixture(tmp_path)
    home = tmp_path / "home"
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LAUNCHCTL_MODE"] = "bootstrap-fail"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "./install.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    installed_path = home / "Library" / "LaunchAgents" / "live.aiplanet.ai-radar.alert.plist"
    assert result.returncode != 0
    assert not installed_path.exists()
    assert "bootstrap" in result.stderr


def test_alert_uninstall_keeps_owned_file_when_launchctl_query_fails(tmp_path: Path) -> None:
    repo, fake_bin = _copy_alert_lifecycle_fixture(tmp_path)
    home = tmp_path / "home"
    installed_path = home / "Library" / "LaunchAgents" / "live.aiplanet.ai-radar.alert.plist"
    _write_owned_alert_launch_agent(installed_path)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LAUNCHCTL_MODE"] = "query-fail"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "./uninstall.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert installed_path.is_file()
    assert "query failed" in result.stderr
    assert "unloaded from launchd" not in result.stdout
    assert "nothing to remove" not in result.stdout


def test_alert_uninstall_keeps_owned_file_when_bootout_fails(tmp_path: Path) -> None:
    repo, fake_bin = _copy_alert_lifecycle_fixture(tmp_path)
    home = tmp_path / "home"
    installed_path = home / "Library" / "LaunchAgents" / "live.aiplanet.ai-radar.alert.plist"
    _write_owned_alert_launch_agent(installed_path)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["LAUNCHCTL_MODE"] = "bootout-fail"
    env["LAUNCHD_PATH"] = str(installed_path)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "./uninstall.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert installed_path.is_file()
    assert "bootout failed" in result.stderr
    assert "unloaded from launchd" not in result.stdout


def test_alert_install_rejects_empty_home_before_launch_agent_write(tmp_path: Path) -> None:
    repo, fake_bin = _copy_alert_lifecycle_fixture(tmp_path)
    env = os.environ.copy()
    env["HOME"] = ""
    env["LAUNCHCTL_MODE"] = "bootstrap-fail"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "./install.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HOME must be a non-root absolute path" in result.stderr


@pytest.mark.parametrize("equivalent_root", ["//", "/.", "/tmp/.."])
def test_alert_install_rejects_home_values_equivalent_to_root(
    tmp_path: Path,
    equivalent_root: str,
) -> None:
    repo, fake_bin = _copy_alert_lifecycle_fixture(tmp_path)
    env = os.environ.copy()
    env["HOME"] = equivalent_root
    env["LAUNCHCTL_MODE"] = "bootstrap-fail"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "./install.sh", "alert"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HOME is not an allowlisted user home" in result.stderr


def test_service_launch_agent_path_returns_no_path_after_home_validation_failure() -> None:
    script = """
    source deploy/lib/services.sh
    HOME='//'
    output="$(service_launch_agent_path alert)"
    status=$?
    printf 'status=%s output=%s\\n' "$status" "$output"
    """

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "status=1 output=" in result.stdout
    assert "/Library/LaunchAgents" not in result.stdout


def test_service_launch_agent_path_rejects_missing_home_under_root_symlink(
    tmp_path: Path,
) -> None:
    root_alias = tmp_path / "root-alias"
    root_alias.symlink_to("/")
    unsafe_home = root_alias / "missing" / "deeper" / ".." / ".."
    script = f"""
    source deploy/lib/services.sh
    HOME={str(unsafe_home)!r}
    output="$(service_launch_agent_path alert)"
    status=$?
    printf 'status=%s output=%s\\n' "$status" "$output"
    """

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "status=1 output=" in result.stdout
    assert "/Library/LaunchAgents" not in result.stdout


def test_service_launch_agent_path_rejects_library_symlink_escape(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "Library").symlink_to("/")
    services_path = tmp_path / "services.sh"
    shutil.copy(REPO_ROOT / "deploy/lib/services.sh", services_path)
    _allow_fixture_home(services_path, home)
    script = f"""
    source {shlex.quote(str(services_path))}
    HOME={str(home)!r}
    output="$(service_launch_agent_path alert)"
    status=$?
    printf 'status=%s output=%s\\n' "$status" "$output"
    """

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "status=1 output=" in result.stdout
    assert "escapes canonical HOME" in result.stderr


def test_serve_launchd_template_writes_access_log_to_repo_logs_dir() -> None:
    plist = (REPO_ROOT / "deploy/launchd/ai-radar-serve.plist.example").read_text(encoding="utf-8")

    assert "<key>StandardOutPath</key><string>/path/to/ai-radar/logs/serve-access.log</string>" in plist
    assert "<key>StandardErrorPath</key><string>/path/to/ai-radar/logs/serve-access.err.log</string>" in plist
    assert ("/" + "Users/") not in plist


def test_service_scripts_accept_alert_slug_and_document_usage() -> None:
    status = subprocess.run(["./status.sh", "alert"], cwd=REPO_ROOT, text=True, capture_output=True, check=True)
    install_text = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    uninstall_text = (REPO_ROOT / "uninstall.sh").read_text(encoding="utf-8")

    assert status.stdout.startswith("alert     | ")
    assert "unknown service" not in status.stderr
    assert "serve | tunnel | pipeline | alert | performance-probe" in install_text
    assert "serve | tunnel | pipeline | alert | performance-probe" in uninstall_text
    assert "logs/alert-check.log" in install_text


def test_cost_report_cron_install_reinstall_and_uninstall_preserve_unrelated_entries(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "deploy/lib").mkdir(parents=True)
    (repo / "deploy/cron").mkdir(parents=True)
    for name in ("install.sh", "uninstall.sh", "status.sh"):
        shutil.copy(REPO_ROOT / name, repo / name)
    shutil.copy(REPO_ROOT / "deploy/lib/services.sh", repo / "deploy/lib/services.sh")
    shutil.copy(
        REPO_ROOT / "deploy/cron/ai-radar-cost-report",
        repo / "deploy/cron/ai-radar-cost-report",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_crontab = fake_bin / "crontab"
    fake_crontab.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-l" ]]; then
  [[ -f "$FAKE_CRONTAB_STATE" ]] || exit 1
  cat "$FAKE_CRONTAB_STATE"
else
  cat > "$FAKE_CRONTAB_STATE"
fi
""",
        encoding="utf-8",
    )
    fake_crontab.chmod(0o755)
    state = tmp_path / "crontab"
    state.write_text("25 * * * * performance-probe-owner\n41 1 * * * sync-db-cron.sh\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_CRONTAB_STATE": str(state),
            "FEISHU_GENERAL_NOTIFICATION_WEBHOOK": "https://example.invalid/notification",
        }
    )

    for _ in range(2):
        result = subprocess.run(
            ["bash", "./install.sh", "cost-report"], cwd=repo, env=env,
            text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    installed = state.read_text(encoding="utf-8")
    assert installed.count("# ai-radar-cost-report") == 1
    assert "17 9 * * 1" in installed
    assert "run-or-alert --key ai-radar-cost-report --" in installed
    assert "/path/to/" not in installed
    assert "performance-probe-owner" in installed
    assert "sync-db-cron.sh" in installed
    status = subprocess.run(
        ["bash", "./status.sh", "cost-report"], cwd=repo, env=env,
        text=True, capture_output=True, check=False,
    )
    assert "in crontab" in status.stdout
    removed = subprocess.run(
        ["bash", "./uninstall.sh", "cost-report"], cwd=repo, env=env,
        text=True, capture_output=True, check=False,
    )
    assert removed.returncode == 0
    remaining = state.read_text(encoding="utf-8")
    assert "ai-radar-cost-report" not in remaining
    assert "performance-probe-owner" in remaining
    assert "sync-db-cron.sh" in remaining
