from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_alert_service_is_registered_in_service_helpers() -> None:
    script = """
    source deploy/lib/services.sh
    printf 'services=%s\\n' "${ALL_SERVICES[*]}"
    printf 'label=%s\\n' "$(service_label alert)"
    printf 'plist=%s\\n' "$(service_plist_name alert)"
    printf 'desc=%s\\n' "$(service_desc alert)"
    """

    result = subprocess.run(["bash", "-lc", script], cwd=REPO_ROOT, text=True, capture_output=True, check=True)

    assert "services=serve tunnel pipeline alert" in result.stdout
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
        "exit 0\n",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["LAUNCHCTL_CALLS"] = str(calls_path)
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
        f"bootstrap gui/{os.getuid()} {repo}/deploy/launchd/ai-radar-alert.plist",
    ]
    assert "reloading loaded job" in result.stdout
    assert "bootstrapped + started" in result.stdout


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
    assert "serve | tunnel | pipeline | alert" in install_text
    assert "serve | tunnel | pipeline | alert" in uninstall_text
    assert "logs/alert-check.log" in install_text
