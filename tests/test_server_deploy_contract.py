"""Contract for the Linux server deployment layer.

The macOS side (``install.sh`` + ``deploy/lib/services.sh``) stays untouched:
the Mac runs the pipeline, the server only serves. Two small layers with
different jobs beat one abstraction stretched across launchd and systemd,
especially while the Mac side is running the only pipeline there is.

These assertions exist because the unit that ran on the server before this
layer was written had every failure mode below: absolute paths baked into
``ExecStart``, no ``EnvironmentFile``, and no ``AI_RADAR_PRE_MIGRATED_DB`` --
so every restart silently rewrote the read-only replica's search index.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVER_DIR = REPO_ROOT / "deploy" / "server"
NGINX_DIR = REPO_ROOT / "deploy" / "nginx"

SERVE_UNIT = SYSTEMD_DIR / "ai-radar-serve@.service"
SCRIPTS = ("install-server.sh", "status-server.sh", "uninstall-server.sh")


def _directives(path: Path) -> list[tuple[str, str, str]]:
    """Parse a unit into (section, key, value) triples, line by line.

    Not configparser: it applies ``%``-interpolation, which mangles systemd
    specifiers like ``%i``, and it collapses repeated keys into one -- but
    systemd reads units line-oriented and treats a repeated ``EnvironmentFile=``
    as an additional file, which is exactly how the per-slot config is loaded.
    A dict-shaped model would hide the thing being asserted.
    """
    out: list[tuple[str, str, str]] = []
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            out.append((section, key.strip(), value.strip()))
    return out


def _values(path: Path, section: str, key: str) -> list[str]:
    return [v for s, k, v in _directives(path) if s == section and k == key]


def _all_units() -> list[Path]:
    return sorted(SYSTEMD_DIR.glob("*.service")) + sorted(SYSTEMD_DIR.glob("*.timer"))


def test_layer_exists() -> None:
    assert SERVE_UNIT.is_file(), "templated serve unit is missing"
    for name in SCRIPTS:
        script = SERVER_DIR / name
        assert script.is_file(), f"{name} is missing"
        assert script.stat().st_mode & stat.S_IXUSR, f"{name} is not executable"


def test_serve_unit_is_templated_by_port() -> None:
    """One unit, two slots: blue/green switching needs both alive at once."""
    text = SERVE_UNIT.read_text(encoding="utf-8")
    assert "%i" in text, "serve unit must be templated so two ports can coexist"


def test_units_carry_no_absolute_home_paths() -> None:
    """A unit with /home/ubuntu baked in cannot be replayed on a rebuilt host."""
    offenders = {}
    for unit in _all_units():
        hits = re.findall(r"/home/[A-Za-z0-9_.-]+", unit.read_text(encoding="utf-8"))
        if hits:
            offenders[unit.name] = sorted(set(hits))
    assert not offenders, f"hardcoded home paths in units: {offenders}"


def test_units_read_configuration_from_environment_file() -> None:
    for unit in sorted(SYSTEMD_DIR.glob("*.service")):
        assert _values(unit, "Service", "EnvironmentFile"), (
            f"{unit.name} does not load an EnvironmentFile"
        )


def test_serve_never_migrates_the_replica() -> None:
    """The whole read-only-replica premise rests on this one flag.

    Without it every serve start runs migrations against the replica. That is
    how the pre-existing server unit behaved, and under blue/green it is worse
    than wasteful: two instances would rebuild the same index concurrently
    while one of them is serving reads from it.
    """
    text = SERVE_UNIT.read_text(encoding="utf-8")
    assert (
        "AI_RADAR_PRE_MIGRATED_DB=1" in text or "--pre-migrated-db" in text
    ), "serve unit must start in pre-migrated mode"


def test_each_slot_binds_its_own_database() -> None:
    """Standby must read its candidate DB, not whatever active points at.

    If both slots resolved the same path, the candidate could never be verified
    before the switch -- it would already be the live database.
    """
    slot_scoped = [v for v in _values(SERVE_UNIT, "Service", "EnvironmentFile") if "%i" in v]
    assert slot_scoped, "serve unit must load a per-slot EnvironmentFile keyed by %i"


def test_units_are_system_level_not_user_level() -> None:
    """The server has Linger=no: user units die at logout."""
    for unit in sorted(SYSTEMD_DIR.glob("*.service")):
        wanted = " ".join(_values(unit, "Install", "WantedBy"))
        assert wanted, f"{unit.name} has no [Install] WantedBy"
        assert "multi-user.target" in wanted, (
            f"{unit.name} targets {wanted!r}; a user-session target would not "
            "survive logout on a host with Linger=no"
        )


def test_install_and_status_agree_on_the_service_list() -> None:
    """Two lists that drift make status blind to something install created."""
    install = (SERVER_DIR / "install-server.sh").read_text(encoding="utf-8")
    status = (SERVER_DIR / "status-server.sh").read_text(encoding="utf-8")

    def declared(text: str) -> set[str]:
        match = re.search(r"^SERVICES=\(([^)]*)\)", text, re.MULTILINE)
        assert match, "script must declare a SERVICES=(...) array"
        return set(match.group(1).split())

    assert declared(install) == declared(status)


def test_uninstall_covers_everything_install_creates() -> None:
    install = (SERVER_DIR / "install-server.sh").read_text(encoding="utf-8")
    uninstall = (SERVER_DIR / "uninstall-server.sh").read_text(encoding="utf-8")
    match = re.search(r"^SERVICES=\(([^)]*)\)", install, re.MULTILINE)
    assert match
    for service in match.group(1).split():
        assert service in uninstall, f"{service} is installed but never removed"


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_are_syntactically_valid(name: str) -> None:
    import subprocess

    result = subprocess.run(
        ["bash", "-n", str(SERVER_DIR / name)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_fail_fast(name: str) -> None:
    """Half-applied deployment state is worse than a refused one."""
    text = (SERVER_DIR / name).read_text(encoding="utf-8")
    assert re.search(r"^set -euo pipefail", text, re.MULTILINE), f"{name} lacks set -euo pipefail"


def test_nginx_bootstrap_half_is_free_of_tls_dependencies() -> None:
    """The HTTP half must be enableable before any certificate exists.

    That is the whole point of the split: the first `nginx -t` on a fresh host
    runs with no certificate and no upstream include, and a single file that
    referenced either could never bootstrap certbot (observed: the previous
    single-file layout failed -t before certbot could run).
    """
    conf = (NGINX_DIR / "news.aiplanet.live-http.conf").read_text(encoding="utf-8")
    assert ".well-known/acme-challenge" in conf, "no ACME challenge location"
    assert "ssl_certificate" not in conf, "bootstrap half must not reference certificates"
    assert "ai_radar_active" not in conf, "bootstrap half must not need the upstream include"
    # ACME before the redirect: Let's Encrypt fetches over plain HTTP and does
    # not follow a 301 to a certificate that may not exist yet.
    assert conf.index("acme-challenge") < conf.index("return 301")


def test_nginx_tls_half_avoids_the_1_25_only_http2_directive() -> None:
    """`http2 on;` first exists in nginx 1.25.1; the pinned host runs 1.24."""
    conf = (NGINX_DIR / "news.aiplanet.live.conf").read_text(encoding="utf-8")
    assert not re.search(r"^\s*http2\s+on\s*;", conf, re.MULTILINE), (
        "http2 on; is unknown to nginx 1.24 -- use `listen ... http2`"
    )
    assert "listen 443 ssl http2" in conf


def test_nginx_keeps_a_catch_all_rejection_on_both_ports() -> None:
    """Bare-IP requests must be dropped on 80 and 443 alike.

    A 443-only rejection still leaves plain HTTP answering on the address,
    which is all a scanner needs to find the origin.
    """
    http_conf = (NGINX_DIR / "news.aiplanet.live-http.conf").read_text(encoding="utf-8")
    tls_conf = (NGINX_DIR / "news.aiplanet.live.conf").read_text(encoding="utf-8")
    assert "return 444" in http_conf
    assert "return 444" in tls_conf


def test_nginx_site_is_not_a_second_default_server() -> None:
    """Two default_server declarations on the same port refuse to start nginx.

    The host already ships sites-available/default with one, so the site file
    must not add another.
    """
    conf = (NGINX_DIR / "news.aiplanet.live.conf").read_text(encoding="utf-8")
    declarations = [
        line.strip()
        for line in conf.splitlines()
        if "default_server" in line and not line.strip().startswith("#")
    ]
    # Only the dedicated reject-server may claim it (one IPv4 + one IPv6 listen).
    assert len(declarations) <= 2, f"too many default_server declarations: {declarations}"
    reject_block = conf[conf.index("return 444") - 800 : conf.index("return 444")]
    for line in declarations:
        assert line in reject_block, (
            f"default_server declared outside the reject block: {line!r} -- the named "
            "vhost must not claim it, or nginx refuses to start alongside "
            "sites-available/default"
        )


def test_every_execstart_target_exists_in_the_repo() -> None:
    """A unit pointing at a missing script installs fine and fails at runtime.

    systemd reports this as a generic 203/EXEC on first activation, with
    nothing naming the absent file, so it is worth catching here instead.
    """
    missing = {}
    for unit in sorted(SYSTEMD_DIR.glob("*.service")):
        for value in _values(unit, "Service", "ExecStart"):
            for token in value.split():
                # Both the executable and script-style arguments count: an
                # interpreter line like `python3 script.py` fails at runtime
                # just as hard when the SCRIPT is missing.
                if "@AI_RADAR_HOME@" not in token:
                    continue
                relative = token.replace("@AI_RADAR_HOME@/", "")
                if relative.startswith(".venv"):
                    continue  # created on the host by uv sync, not tracked
                path = REPO_ROOT / relative
                if not path.is_file():
                    missing[unit.name] = relative
    assert not missing, f"units reference scripts that do not exist: {missing}"
