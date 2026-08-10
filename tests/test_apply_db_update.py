"""State-machine matrix for the blue/green database apply.

Every scenario here is a finding from the adversarial review of the shell
version, kept as a regression: the shell rewrite went through four review
rounds and each round found new crash windows that were properties of bash
itself. The Python rewrite makes the machine unit-testable -- external effects
go through a Runner object this suite replaces.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "apply_db_update", REPO_ROOT / "deploy" / "sync" / "apply_db_update.py"
)
adu = importlib.util.module_from_spec(spec)
sys.modules["apply_db_update"] = adu
spec.loader.exec_module(adu)

manifest_spec = importlib.util.spec_from_file_location(
    "apply_db_update_manifest",
    REPO_ROOT / "deploy" / "sync" / "build_fts_manifest.py",
)
assert manifest_spec is not None and manifest_spec.loader is not None
manifest_module = importlib.util.module_from_spec(manifest_spec)
manifest_spec.loader.exec_module(manifest_module)


class FakeRunner:
    """Records commands; per-command results are injectable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.failures: dict[str, int] = {}  # substring -> rc
        self.slot_active: dict[str, bool] = {"8000": True, "8001": True}
        self.healthz: dict[str, bool] = {"8000": True, "8001": True}
        self.stopped_by_command: set[str] = set()

    def _rc(self, argv: tuple[str, ...]) -> int:
        joined = " ".join(argv)
        if "is-active" in joined:
            port = joined.split("@")[1].split(".")[0]
            return 0 if self.slot_active.get(port, False) else 3
        if argv[0] == "curl":
            port = joined.rsplit(":", 1)[1].split("/")[0]
            return 0 if self.healthz.get(port, False) else 7
        for needle, rc in self.failures.items():
            if needle in joined:
                return rc
        return 0

    def run(self, *argv: str, check: bool = True):
        self.calls.append(argv)
        rc = self._rc(argv)
        joined = " ".join(argv)
        if rc == 0 and "systemctl" in argv and "@" in joined:
            port = joined.split("@")[1].split(".")[0]
            if " stop " in f" {joined} ":
                self.slot_active[port] = False
                self.healthz[port] = False
                self.stopped_by_command.add(port)
            elif " restart " in f" {joined} " and port in self.stopped_by_command:
                self.slot_active[port] = True
                self.healthz[port] = True
                self.stopped_by_command.remove(port)

        class R:
            returncode = rc
            stdout = ""
            stderr = ""

        return R()

    def ok(self, *argv: str) -> bool:
        return self.run(*argv, check=False).returncode == 0


def make_good_db(path: Path) -> None:
    """A snapshot that passes the full acceptance triple.

    Checkpointed to a single file at the end: the real transfer artifact is a
    single-file `.backup` snapshot, and leaving a -wal/-shm behind here makes
    renames strand them -- a failure mode of the fixture, not of the code.
    """
    from airadar import db as airadar_db

    airadar_db.migrate(path)
    # sqlite3's context manager commits but does NOT close; an open first
    # connection would hold the lock journal_mode=DELETE needs below.
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO sources (id, name, url, tier, enabled, kind, synced_at)"
            " VALUES ('s1', 'SourceOnlyCedar', 'https://e.example/f', 'T1', 1, 'feed', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO items (id, source_id, url, title, content_text, author, content_hash,"
            " published_at, fetched_at) VALUES ('i1', 's1', 'https://e.example/1',"
            " 'TitleOnlyBeacon', 'ContentOnlyHarbor', 'AuthorOnlyQuartz', 'h1',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO curation_runs (id, ruleset_version, weights_json, threshold,"
            " input_eval_ids, output_curated_ids, created_at) VALUES ('r1', 'v1', '{}',"
            " 0.5, '[]', '[]', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)"
            " VALUES ('r1', 'i1', 0.9, 1, '{}')"
        )
        conn.execute(
            "INSERT INTO item_evaluations (item_id, stage, ruleset_version, model_id,"
            " input_json, output_json, evaluated_at) VALUES ('i1', 'enrich', 'v1', 'm',"
            " '{}', '{\"title_zh\":\"中文独有灯塔词\"}', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        # TRUNCATE empties the WAL so the sidecar files can simply be removed;
        # switching journal_mode would need an exclusive lock we cannot get
        # (db.migrate's own connection is closed by GC, not deterministically).
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        side = Path(str(path) + suffix)
        if side.exists():
            side.unlink()


def stage_good_bundle(d) -> tuple[str, dict[str, object]]:
    primary = d.cfg.data_dir / "primary-with-fts.db"
    make_good_db(primary)
    shutil.copyfile(primary, d.cfg.incoming)
    conn = sqlite3.connect(d.cfg.incoming)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS items_ai_fts;
            DROP TRIGGER IF EXISTS items_au_fts;
            DROP TRIGGER IF EXISTS items_ad_fts;
            DROP TRIGGER IF EXISTS sources_au_fts;
            DROP TRIGGER IF EXISTS enrich_ai_fts;
            DROP TABLE IF EXISTS items_fts;
            """
        )
        conn.commit()
    finally:
        conn.close()
    snapshot_id = adu.snapshot_id_of(d.cfg.incoming)
    sidecar = d.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    manifest = manifest_module.build_manifest(
        snapshot=primary,
        artifact=d.cfg.incoming,
        output=sidecar,
    )
    return snapshot_id, manifest


def install_search_oracle(d, manifest: dict[str, object], *, fail_new: bool = False) -> None:
    probes = manifest["probes"]
    assert isinstance(probes, dict)
    expected = {
        probe["term"]: probe["timeline_http_matches"] for probe in probes.values()
    }
    old = {
        term: {"count": 1, "item_ids": [f"old-{index}"]}
        for index, term in enumerate(expected)
    }

    def fake_http(url: str, term: str) -> dict[str, object]:
        if url.startswith("http://127.0.0.1:8001/"):
            return expected[term]
        if d.active_port() == "8001":
            return {"count": 0, "item_ids": []} if fail_new else expected[term]
        return old[term]

    d._http_search_results = fake_http


def stage_prepared_release(d) -> tuple[str, dict[str, object]]:
    snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    d.cfg.incoming.rename(d.cfg.claimed)
    d._materialize_and_verify_candidate("8001", snapshot_id, manifest)
    d.journal_write(
        "prepared",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
    )
    return snapshot_id, manifest


@pytest.fixture
def deploy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    conf = tmp_path / "nginx" / "active.conf"
    conf.parent.mkdir()
    conf.write_text("upstream ai_radar_active { server 127.0.0.1:8000; }\n")
    cfg = adu.Config(
        home=REPO_ROOT,
        data_dir=data,
        incoming=data / "radar.db.incoming",
        claimed=data / "radar.db.claimed",
        basis_dir=data / "basis",
        receipt=data / "accepted-snapshot.json",
        journal=data / "switch-journal.json",
        active_conf=conf,
        lock=data / ".deploy.lock",
        quarantine_dir=data / "quarantine",
        public_search_url="https://public.invalid/api/v1/timeline",
        nginx_link=tmp_path / "etc-link.conf",
        health_wait_s=1,
    )
    cfg.nginx_link.symlink_to(cfg.active_conf)
    runner = FakeRunner()
    d = adu.Deploy(cfg, runner)
    monkeypatch.setattr(adu.Deploy, "free_mem_mb", lambda self: 4096)
    monkeypatch.setattr(adu.time, "sleep", lambda s: None)
    cfg.slot_db("8000").write_bytes(b"old-serving-database")
    return d, runner


def journal_state(d) -> str:
    return json.loads(d.cfg.journal.read_text())["state"]


def failure_record(d) -> tuple[dict[str, object], Path]:
    journal = json.loads(d.cfg.journal.read_text())
    failure_path = Path(journal["failure_path"])
    return json.loads(failure_path.read_text()), failure_path


def test_config_from_env_preserves_production_control_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AI_RADAR_PORTS",
        "AI_RADAR_UNIT_PREFIX",
        "AI_RADAR_SYSTEMCTL",
        "AI_RADAR_NGINX_BIN",
        "AI_RADAR_NGINX_PREFIX",
        "AI_RADAR_HTTP_PROBE_TIMEOUT_S",
        "AI_RADAR_HTTP_PROBE_INTERVAL_S",
        "AI_RADAR_NGINX_ROLLBACK_DRAIN_S",
        "AI_RADAR_ROUTE_PROOF_SEARCH_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg = adu.Config.from_env()

    assert cfg.ports == ("8000", "8001")
    assert cfg.serve_unit("8001") == "ai-radar-serve@8001.service"
    assert cfg.systemctl_args("is-active", "--quiet", cfg.serve_unit("8001")) == (
        "systemctl",
        "is-active",
        "--quiet",
        "ai-radar-serve@8001.service",
    )
    assert cfg.systemctl_args("stop", cfg.serve_unit("8001"), mutate=True) == (
        "sudo",
        "systemctl",
        "stop",
        "ai-radar-serve@8001.service",
    )
    assert cfg.nginx_args("-t") == ("sudo", "nginx", "-t")
    assert cfg.nginx_args("-s", "reload") == (
        "sudo",
        "nginx",
        "-s",
        "reload",
    )
    assert cfg.http_probe_timeout_s == 30
    assert cfg.http_probe_interval_s == 1.0
    assert cfg.nginx_rollback_drain_s == 90.0


def test_config_from_env_routes_every_control_effect_through_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "isolated-data"
    active_conf = tmp_path / "nginx" / "active.conf"
    nginx_link = tmp_path / "nginx" / "included.conf"
    active_conf.parent.mkdir(parents=True)
    active_conf.write_text(
        "upstream ai_radar_active { server 127.0.0.1:19000; }\n"
    )
    nginx_link.symlink_to(active_conf)
    overrides = {
        "AI_RADAR_HOME": str(REPO_ROOT),
        "AI_RADAR_DATA_DIR": str(data),
        "AI_RADAR_ACTIVE_UPSTREAM_CONF": str(active_conf),
        "AI_RADAR_NGINX_LINK": str(nginx_link),
        "AI_RADAR_PUBLIC_SEARCH_URL": "https://isolated.invalid/api/v1/timeline",
        "AI_RADAR_PORTS": "19000,19001",
        "AI_RADAR_UNIT_PREFIX": "ai-radar-isolated@",
        "AI_RADAR_SYSTEMCTL": "/opt/isolated/bin/systemctl --user",
        "AI_RADAR_NGINX_BIN": "/opt/isolated/bin/nginx",
        "AI_RADAR_NGINX_PREFIX": str(tmp_path / "nginx-root"),
        "AI_RADAR_HTTP_PROBE_TIMEOUT_S": "47",
        "AI_RADAR_HTTP_PROBE_INTERVAL_S": "1.5",
        "AI_RADAR_NGINX_ROLLBACK_DRAIN_S": "95.5",
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    cfg = adu.Config.from_env()

    assert cfg.ports == ("19000", "19001")
    assert cfg.data_dir == data
    assert cfg.incoming == data / "radar.db.incoming"
    assert cfg.claimed == data / "radar.db.claimed"
    assert cfg.basis == data / "basis" / "radar.db.upload"
    assert cfg.receipt == data / "accepted-snapshot.json"
    assert cfg.journal == data / "switch-journal.json"
    assert cfg.lock == data / ".deploy.lock"
    assert cfg.http_probe_timeout_s == 47
    assert cfg.http_probe_interval_s == 1.5
    assert cfg.nginx_rollback_drain_s == 95.5
    assert cfg.route_proof_search_url == (
        "https://127.0.0.1/api/v1/timeline"
    )
    assert cfg.serve_unit("19001") == "ai-radar-isolated@19001.service"
    expected_systemctl = (
        "/opt/isolated/bin/systemctl",
        "--user",
        "restart",
        "ai-radar-isolated@19001.service",
    )
    assert cfg.systemctl_args("restart", cfg.serve_unit("19001")) == (
        *expected_systemctl,
    )
    assert cfg.systemctl_args(
        "restart", cfg.serve_unit("19001"), mutate=True
    ) == (*expected_systemctl,)
    assert cfg.nginx_args("-t") == (
        "/opt/isolated/bin/nginx",
        "-p",
        f"{tmp_path / 'nginx-root'}/",
        "-t",
    )
    runner = FakeRunner()
    runner.slot_active["19001"] = True
    runner.healthz["19001"] = True
    deploy = adu.Deploy(cfg, runner)

    route_args = deploy._curl_search_args(
        cfg.route_proof_search_url, "term", 1
    )
    assert route_args[:4] == (
        "--noproxy",
        "*",
        "--connect-to",
        "isolated.invalid:443:127.0.0.1:443",
    )
    assert route_args[-1].startswith(
        "https://isolated.invalid/api/v1/timeline?"
    )

    assert deploy.slot_serving("19001")
    deploy._switch_include("19001")
    deploy._retire_candidate("19001")

    assert runner.calls == [
        (
            "/opt/isolated/bin/systemctl",
            "--user",
            "is-active",
            "--quiet",
            "ai-radar-isolated@19001.service",
        ),
        ("curl", "-sf", "-m", "5", "http://127.0.0.1:19001/api/v1/healthz"),
        (
            "/opt/isolated/bin/nginx",
            "-p",
            f"{tmp_path / 'nginx-root'}/",
            "-t",
        ),
        (
            "/opt/isolated/bin/nginx",
            "-p",
            f"{tmp_path / 'nginx-root'}/",
            "-s",
            "reload",
        ),
        (
            "/opt/isolated/bin/systemctl",
            "--user",
            "disable",
            "ai-radar-isolated@19001.service",
        ),
        (
            "/opt/isolated/bin/systemctl",
            "--user",
            "stop",
            "ai-radar-isolated@19001.service",
        ),
    ]


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_config_from_env_rejects_invalid_http_probe_timeout(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("AI_RADAR_HTTP_PROBE_TIMEOUT_S", value)

    with pytest.raises(ValueError):
        adu.Config.from_env()


@pytest.mark.parametrize("name", [
    "AI_RADAR_HTTP_PROBE_INTERVAL_S",
    "AI_RADAR_NGINX_ROLLBACK_DRAIN_S",
])
@pytest.mark.parametrize("value", ["-0.1", "nan", "not-a-number"])
def test_config_from_env_rejects_invalid_nonnegative_durations(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        adu.Config.from_env()


def test_no_incoming_is_a_clean_noop(deploy) -> None:
    d, _ = deploy
    assert d.apply() == 0


def test_full_happy_path_switches_and_commits(deploy) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    assert d.apply() == 0
    assert journal_state(d) == "committed"
    assert "8001" in d.cfg.active_conf.read_text()
    assert d.cfg.basis.exists()
    receipt = json.loads(d.cfg.receipt.read_text())
    assert receipt["serving_port"] == "8001"
    assert receipt["receipt_schema_version"] == 2
    assert receipt["snapshot_id"] == snapshot_id
    assert len(receipt["snapshot_id"]) == 64
    assert len(receipt["manifest_sha256"]) == 64
    assert not (d.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)).exists()
    joined = [" ".join(c) for c in r.calls]
    # enable-before-switch: candidate enabled before nginx reload happens
    assert any("enable ai-radar-serve@8001" in c for c in joined)
    reload_at = next(i for i, c in enumerate(joined) if "nginx -s reload" in c)
    enable_at = next(i for i, c in enumerate(joined) if "enable ai-radar-serve@8001" in c)
    assert enable_at < reload_at, "candidate must be reboot-safe before the durable switch"
    # old slot retired last
    stop_old = next(i for i, c in enumerate(joined) if "stop ai-radar-serve@8000" in c)
    assert stop_old > reload_at


def test_committed_receipt_drift_is_rejected_on_the_next_timer(deploy) -> None:
    d, _ = deploy
    _snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    assert d.apply() == 0
    receipt = json.loads(d.cfg.receipt.read_text())
    receipt["manifest_sha256"] = "0" * 64
    d.cfg.receipt.write_text(json.dumps(receipt))

    with pytest.raises(adu.ApplyError, match="receipt does not match"):
        d.apply()


@pytest.mark.parametrize("state", ["claiming", "prepared", "switching", "switched"])
def test_legacy_inflight_journal_has_an_explicit_rollout_blocker(
    deploy, state: str
) -> None:
    d, _ = deploy
    d.cfg.journal.write_text(
        json.dumps(
            {
                "state": state,
                "candidate_port": "8001",
                "snapshot_id": "" if state == "claiming" else "a" * 16,
                "at": "2026-08-09T00:00:00Z",
            }
        )
    )

    with pytest.raises(adu.ApplyError, match="settle it before rollout"):
        d.reconcile()


def test_legacy_committed_is_made_explicitly_unverified(deploy) -> None:
    d, _ = deploy
    d.cfg.basis_dir.mkdir()
    d.cfg.basis.write_bytes(b"legacy basis with no recoverable full identity")
    d.cfg.receipt.write_text(
        '{"snapshot_id":"0123456789abcdef","completed_at":"2026-08-01T00:00:00Z"}'
    )
    d.cfg.journal.write_text(
        json.dumps(
            {
                "state": "committed",
                "candidate_port": "8000",
                "snapshot_id": "0123456789abcdef",
                "at": "2026-08-01T00:00:00Z",
            }
        )
    )

    assert d.apply() == 0

    journal = json.loads(d.cfg.journal.read_text())
    assert journal["state"] == "legacy_committed_unverified"
    assert journal["identity_status"] == "unavailable-legacy"
    assert journal["recovery_action"] == "accept-new-full-identity-release"
    assert journal["serving_port"] == "8000"


def test_legacy_committed_with_unknown_claim_identity_is_made_explicit(deploy) -> None:
    d, _ = deploy
    d.cfg.basis_dir.mkdir()
    d.cfg.basis.write_bytes(b"legacy basis whose interrupted claim had no hash yet")
    d.cfg.receipt.write_text(
        '{"snapshot_id":"","completed_at":"2026-08-01T00:00:00Z"}'
    )
    d.cfg.journal.write_text(
        json.dumps(
            {
                "state": "committed",
                "candidate_port": "8000",
                "snapshot_id": "",
                "at": "2026-08-01T00:00:00Z",
            }
        )
    )

    assert d.apply() == 0

    journal = json.loads(d.cfg.journal.read_text())
    assert journal["state"] == "legacy_committed_unverified"
    assert journal["identity_status"] == "unavailable-legacy"
    assert journal["legacy_snapshot_id"] is None
    assert journal["legacy_snapshot_id_status"] == "unavailable-before-hash"
    assert journal["recovery_action"] == "accept-new-full-identity-release"


def test_corrupt_journal_stops_loudly(deploy) -> None:
    d, _ = deploy
    d.cfg.journal.write_text("not json")
    with pytest.raises(adu.ApplyError, match="refusing to guess"):
        d.apply()


def test_unknown_active_port_is_rejected_before_claim(deploy) -> None:
    d, _ = deploy
    d.cfg.incoming.write_bytes(b"must remain incoming")
    d.cfg.active_conf.write_text(
        "upstream ai_radar_active { server 127.0.0.1:9999; }\n"
    )

    with pytest.raises(adu.ApplyError, match="not one of"):
        d.apply()

    assert d.cfg.incoming.read_bytes() == b"must remain incoming"
    assert not d.cfg.claimed.exists()


def test_rejected_snapshot_is_quarantined_and_release_untouched(deploy) -> None:
    d, _ = deploy
    sqlite3.connect(d.cfg.incoming).execute("CREATE TABLE junk(x)")
    with pytest.raises(adu.ApplyError, match="manifest"):
        d.apply()
    failure, _failure_path = failure_record(d)
    evidence = failure["evidence"]
    assert isinstance(evidence, dict)
    assert isinstance(evidence.get("base"), str)
    assert Path(evidence["base"]).exists()
    assert journal_state(d) == "quarantined"
    assert "8000" in d.cfg.active_conf.read_text()


def test_prepared_recovery_is_by_hash_not_position(deploy) -> None:
    """The finding that forced the rewrite's recovery model.

    The inactive slot holds the PREVIOUS release's database; a recovery that
    grabs 'whichever file exists' recycles that old database into incoming and
    pushes stale data back into production. Only a hash match may be recovered.
    """
    d, _ = deploy
    snap, _manifest = stage_prepared_release(d)
    wrong_candidate = d.cfg.slot_db("8001")
    wrong_candidate.write_bytes(b"unrelated inactive-slot bytes")
    wrong_hash = adu.snapshot_id_of(wrong_candidate)

    d.reconcile()

    assert adu.snapshot_id_of(d.cfg.basis) == snap
    assert adu.snapshot_id_of(d.cfg.slot_db("8001")) != wrong_hash
    assert journal_state(d) == "committed"


def test_prepared_recovery_repairs_a_preflight_crashed_include(deploy) -> None:
    """journal=prepared + include naming the candidate = crashed preflight.

    `switching_pending_consumer` is fsynced before the include is first touched,
    so prepared proves the switch point was not crossed; the include must be
    repaired to the serving slot, not trusted as evidence of a switch.
    """
    d, r = deploy
    _snapshot_id, _manifest = stage_prepared_release(d)
    d.cfg.active_conf.write_text("upstream ai_radar_active { server 127.0.0.1:8001; }\n")
    switched_to: list[str] = []
    original_switch = d._switch_include

    def recording_switch(port: str) -> None:
        switched_to.append(port)
        original_switch(port)

    d._switch_include = recording_switch

    d.reconcile()

    assert switched_to[0] == "8000", "pre-switch recovery must first restore the old slot"
    assert switched_to[-1] == "8001"
    assert journal_state(d) == "committed"


def test_prepared_recovery_undoes_candidate_enablement(deploy) -> None:
    d, r = deploy
    stage_prepared_release(d)
    d.reconcile()
    joined = [" ".join(c) for c in r.calls]
    disable_at = next(i for i, c in enumerate(joined) if "disable ai-radar-serve@8001" in c)
    restart_at = next(i for i, c in enumerate(joined) if "restart ai-radar-serve@8001" in c)
    assert disable_at < restart_at
    assert any("stop ai-radar-serve@8001" in c for c in joined[:restart_at])


def test_switching_pending_consumer_rolls_back(deploy) -> None:
    d, _ = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    rollback = d._capture_rollback_oracle("8000", snapshot_id, manifest)
    d.write_active_include("8001")
    d.journal_write(
        "switching_pending_consumer",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
        rollback=rollback,
    )
    d.reconcile()
    assert journal_state(d) == "quarantined"
    assert "8000" in d.cfg.active_conf.read_text()
    assert not d.cfg.basis.exists()


@pytest.mark.parametrize("drift", ["term", "boolean-count"])
def test_rollback_oracle_drift_is_rejected_before_external_actions(
    deploy, drift: str
) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    rollback = d._capture_rollback_oracle("8000", snapshot_id, manifest)
    field = adu.SEARCH_FIELDS[0]
    results = rollback["previous_serving_public_results"]
    assert isinstance(results, dict)
    record = results[field]
    assert isinstance(record, dict)
    if drift == "term":
        record["term"] = "unrelated-query"
    else:
        result = record["result"]
        assert isinstance(result, dict)
        result["count"] = True
    before = d.cfg.journal.read_bytes()
    r.calls.clear()

    with pytest.raises(adu.ApplyError, match="manifest|count"):
        d.journal_write(
            "switching_pending_consumer",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=0,
            rollback=rollback,
        )

    assert d.cfg.journal.read_bytes() == before
    joined = [" ".join(call) for call in r.calls]
    assert not any("systemctl" in call or "nginx" in call for call in joined)


def test_raw_rollback_oracle_tamper_becomes_persistent_manual_block(deploy) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    rollback = d._capture_rollback_oracle("8000", snapshot_id, manifest)
    d.journal_write(
        "switching_pending_consumer",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
        rollback=rollback,
    )
    journal = json.loads(d.cfg.journal.read_text())
    field = adu.SEARCH_FIELDS[0]
    journal["rollback"]["previous_serving_public_results"][field]["term"] = (
        "raw-tamper"
    )
    d.cfg.journal.write_text(json.dumps(journal))
    r.calls.clear()

    with pytest.raises(adu.ApplyError, match="blocked by invalid oracle"):
        d.reconcile()

    blocked = json.loads(d.cfg.journal.read_text())
    assert blocked["state"] == "rollback_blocked_invalid_oracle"
    assert blocked["recovery_action"] == "manual-intervention"
    assert blocked["rollback_evidence"]["previous_serving_public_results"][field][
        "term"
    ] == "raw-tamper"
    joined = [" ".join(call) for call in r.calls]
    assert not any("systemctl" in call or "nginx" in call for call in joined)


def test_oracle_drift_after_pending_checkpoint_becomes_manual_block(deploy) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    rollback = d._capture_rollback_oracle("8000", snapshot_id, manifest)
    d.journal_write(
        "switching_pending_consumer",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
        rollback=rollback,
    )
    d.cfg.claimed.write_bytes(b"identity drift after durable pending checkpoint")
    r.calls.clear()

    with pytest.raises(adu.ApplyError, match="blocked by invalid oracle"):
        d.reconcile()

    journal = json.loads(d.cfg.journal.read_text())
    assert journal["state"] == "rollback_blocked_invalid_oracle"
    assert journal["recovery_action"] == "manual-intervention"
    assert journal["last_failure_category"] == "rollback-oracle-invalid"
    r.calls.clear()
    with pytest.raises(adu.ApplyError, match="rollback oracle invalid"):
        d.reconcile()
    assert not r.calls


def test_consumer_verified_authority_drift_becomes_persistent_manual_block(
    deploy,
) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    d.write_active_include("8001")
    d.journal_write(
        "consumer_verified",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
    )
    d.cfg.claimed.write_bytes(b"identity drift after consumer checkpoint")
    r.calls.clear()

    with pytest.raises(adu.ApplyError, match="finalize blocked by invalid authority"):
        d.reconcile()

    blocked = json.loads(d.cfg.journal.read_text())
    assert blocked["state"] == "finalize_blocked_invalid_authority"
    assert blocked["recovery_action"] == "manual-intervention"
    assert blocked["last_failure_category"] == "finalize-authority-invalid"
    assert blocked["authority_evidence"]["claimed"]["present"] is True
    assert not r.calls

    with pytest.raises(adu.ApplyError, match="finalize authority invalid"):
        d.reconcile()
    assert not r.calls


def test_finalize_block_persists_the_same_observation_that_failed(
    deploy, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, _ = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    d.write_active_include("8001")
    d.journal_write(
        "consumer_verified",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
    )
    original_base = d.cfg.claimed.read_bytes()
    drifted = b"authority drift visible to the failing observation"
    d.cfg.claimed.write_bytes(drifted)
    drifted_sha = adu.snapshot_id_of(d.cfg.claimed)
    original_observe = d._observe_finalize_authority

    def observe_then_repair(snapshot: str) -> dict[str, object]:
        evidence = original_observe(snapshot)
        d.cfg.claimed.write_bytes(original_base)
        return evidence

    monkeypatch.setattr(d, "_observe_finalize_authority", observe_then_repair)

    with pytest.raises(adu.ApplyError, match="finalize blocked by invalid authority"):
        d.reconcile()

    assert adu.snapshot_id_of(d.cfg.claimed) == snapshot_id
    blocked = json.loads(d.cfg.journal.read_text())
    assert blocked["authority_evidence"]["claimed"]["sha256"] == drifted_sha
    assert blocked["authority_evidence"]["claimed"]["sha256"] != snapshot_id


def test_terminal_evidence_rejects_empty_or_conflicting_shapes(deploy) -> None:
    d, _ = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    failure = {
        "last_failure_category": "finalize-authority-invalid",
        "last_failure_message": "injected malformed evidence",
        "last_failure_at": "2026-08-09T00:00:00Z",
    }

    with pytest.raises(adu.ApplyError, match="authority evidence"):
        d.journal_write(
            "finalize_blocked_invalid_authority",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=0,
            authority_evidence={},
            **failure,
        )

    evidence = d._observe_finalize_authority(snapshot_id)
    evidence["active_port_observed"] = None
    evidence["active_port_status"] = "known"
    with pytest.raises(adu.ApplyError, match="status/value conflict"):
        d.journal_write(
            "finalize_blocked_invalid_authority",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=0,
            authority_evidence=evidence,
            **failure,
        )

    missing = d._observe_finalize_authority(snapshot_id)
    missing["manifest"] = {"present": False, "sha256": None}
    missing["manifest_identity_status"] = "missing"
    missing["manifest_validation_error"] = "injected missing manifest"
    with pytest.raises(adu.ApplyError, match="missing manifest"):
        d.journal_write(
            "finalize_blocked_invalid_authority",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=0,
            authority_evidence=missing,
            **failure,
        )

    invalid = dict(missing)
    invalid["manifest_identity_status"] = "invalid"
    with pytest.raises(adu.ApplyError, match="invalid manifest"):
        d.journal_write(
            "finalize_blocked_invalid_authority",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=0,
            authority_evidence=invalid,
            **failure,
        )

    verified = d._observe_finalize_authority(snapshot_id)
    verified["observed_snapshot_id"] = "short"
    with pytest.raises(adu.ApplyError, match="verified manifest"):
        d.journal_write(
            "finalize_blocked_invalid_authority",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=0,
            authority_evidence=verified,
            **failure,
        )

    with pytest.raises(adu.ApplyError, match="legacy basis binding"):
        d.journal_write(
            "legacy_committed_unverified",
            "8000",
            None,
            legacy_snapshot_id=None,
            legacy_snapshot_id_status="unavailable-before-hash",
            identity_status="unavailable-legacy",
            legacy_basis={},
            legacy_receipt={"present": False, "sha256": None},
        )


def test_unknown_active_port_observation_is_persistently_blocked(deploy) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    d.write_active_include("8001")
    d.journal_write(
        "consumer_verified",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
    )
    d.write_active_include("9999")
    r.calls.clear()

    with pytest.raises(adu.ApplyError, match="finalize blocked by invalid authority"):
        d.reconcile()

    blocked = json.loads(d.cfg.journal.read_text())
    evidence = blocked["authority_evidence"]
    assert evidence["active_port_status"] == "unknown-port"
    assert evidence["active_port_observed"] == "9999"
    assert not r.calls


def test_invalid_manifest_evidence_uses_the_bytes_that_failed_validation(
    deploy, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, _ = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    d.write_active_include("8001")
    d.journal_write(
        "consumer_verified",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
    )
    manifest_path = d._manifest_path(snapshot_id)
    replacement = manifest_path.read_bytes()
    invalid = b"{invalid manifest observed once"
    manifest_path.write_bytes(invalid)
    original_loads = adu.json.loads

    def replace_after_observation(value):
        if value == invalid.decode("utf-8"):
            manifest_path.write_bytes(replacement)
        return original_loads(value)

    monkeypatch.setattr(adu.json, "loads", replace_after_observation)

    with pytest.raises(adu.ApplyError, match="finalize blocked by invalid authority"):
        d.reconcile()

    blocked = original_loads(d.cfg.journal.read_text())
    evidence = blocked["authority_evidence"]
    assert evidence["manifest_identity_status"] == "invalid"
    assert evidence["manifest"]["sha256"] == hashlib.sha256(invalid).hexdigest()
    assert adu.snapshot_id_of(manifest_path) != evidence["manifest"]["sha256"]


def test_unreadable_claimed_db_becomes_persistent_authority_evidence(
    deploy, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    d.write_active_include("8001")
    d.journal_write(
        "consumer_verified",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
    )
    original_open = Path.open

    def deny_claimed(path: Path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == d.cfg.claimed and mode == "rb":
            raise PermissionError("injected claimed read denial")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_claimed)
    r.calls.clear()

    with pytest.raises(adu.ApplyError, match="finalize blocked by invalid authority"):
        d.reconcile()

    blocked = json.loads(d.cfg.journal.read_text())
    evidence = blocked["authority_evidence"]
    assert evidence["claimed_status"] == "unreadable"
    assert evidence["claimed"] == {"present": None, "sha256": None}
    assert evidence["claimed_validation_error"] == "injected claimed read denial"
    assert not r.calls


def test_consumer_verified_refuses_active_port_mismatch_before_external_actions(
    deploy,
) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    before = d.cfg.journal.read_bytes()

    with pytest.raises(adu.ApplyError, match="mismatch"):
        d.journal_write(
            "consumer_verified",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=0,
        )

    assert d.cfg.journal.read_bytes() == before
    joined = [" ".join(call) for call in r.calls]
    assert not any("disable ai-radar-serve@8000" in call for call in joined)
    assert not d.cfg.basis.exists()
    assert not d.cfg.receipt.exists()


def test_consumer_verified_revalidates_manifest_binding_before_external_actions(
    deploy,
) -> None:
    d, r = deploy
    snapshot_id, _manifest = stage_prepared_release(d)
    d.write_active_include("8001")
    before = d.cfg.journal.read_bytes()

    with pytest.raises(adu.ApplyError, match="manifest identity"):
        d.journal_write(
            "consumer_verified",
            "8001",
            snapshot_id,
            manifest_sha256="0" * 64,
            retry_count=0,
        )

    assert d.cfg.journal.read_bytes() == before
    joined = [" ".join(call) for call in r.calls]
    assert not any("disable ai-radar-serve@8000" in call for call in joined)
    assert not d.cfg.basis.exists()
    assert not d.cfg.receipt.exists()


def test_boolean_retry_count_is_not_accepted_as_an_integer(deploy) -> None:
    d, _ = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    before = d.cfg.journal.read_bytes()

    with pytest.raises(adu.ApplyError, match="automatic retry usage"):
        d.journal_write(
            "prepared",
            "8001",
            snapshot_id,
            manifest_sha256=manifest["manifest_sha256"],
            retry_count=True,
        )

    assert d.cfg.journal.read_bytes() == before


def test_switched_with_dead_candidate_restores_old_slot(deploy) -> None:
    d, r = deploy
    snapshot_id, manifest = stage_prepared_release(d)
    rollback = d._capture_rollback_oracle("8000", snapshot_id, manifest)
    d.write_active_include("8001")
    d.journal_write(
        "switched_pending_consumer",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
        rollback=rollback,
    )
    r.slot_active["8001"] = False
    r.healthz["8001"] = False
    d.reconcile()
    assert journal_state(d) == "quarantined"
    assert "8000" in d.cfg.active_conf.read_text()


def test_finalize_failure_does_not_consume_next_incoming(deploy, monkeypatch) -> None:
    """Serialization: an uncommitted release blocks everything behind it."""
    d, r = deploy
    _snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    original_finalize = d.finalize
    monkeypatch.setattr(
        d,
        "finalize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("finalize crash")),
    )
    with pytest.raises(SystemExit, match="finalize crash"):
        d.apply()
    monkeypatch.setattr(d, "finalize", original_finalize)
    d.cfg.incoming.write_bytes(b"next incoming must remain untouched")
    r.failures["disable ai-radar-serve@8000"] = 1
    with pytest.raises(adu.ApplyError, match="disable"):
        d.apply()
    assert journal_state(d) == "consumer_verified"
    journal = json.loads(d.cfg.journal.read_text())
    assert journal["last_failure_category"] == "finalize-failed"
    assert "disable" in journal["last_failure_message"]
    assert d.cfg.incoming.exists(), "the next snapshot must not be consumed"


def test_enable_recovers_next_round(deploy) -> None:
    d, r = deploy
    _snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    r.failures["enable ai-radar-serve@8001"] = 1
    with pytest.raises(adu.ApplyError, match="enable"):
        d.apply()
    assert journal_state(d) == "prepared"
    assert json.loads(d.cfg.journal.read_text())["last_failure_category"] == (
        "candidate-enable-failed"
    )
    r.failures.clear()
    d.apply()
    assert journal_state(d) == "committed"


def test_low_memory_fails_closed(deploy, monkeypatch) -> None:
    d, _ = deploy
    make_good_db(d.cfg.incoming)
    monkeypatch.setattr(adu.Deploy, "free_mem_mb", lambda self: 512)
    with pytest.raises(adu.ApplyError, match="memory"):
        d.apply()
    assert d.cfg.incoming.exists(), "snapshot must remain for a retry after memory recovers"


def test_failed_candidate_start_rolls_back_enablement_and_snapshot(deploy) -> None:
    d, r = deploy
    snap_before, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    r.slot_active["8001"] = False
    r.healthz["8001"] = False
    with pytest.raises(adu.ApplyError, match="never became healthy"):
        d.apply()
    joined = [" ".join(c) for c in r.calls]
    assert any("disable ai-radar-serve@8001" in c for c in joined)
    assert d.cfg.claimed.exists()
    assert adu.snapshot_id_of(d.cfg.claimed) == snap_before
    assert journal_state(d) == "prepared"
    assert json.loads(d.cfg.journal.read_text())["last_failure_category"] == (
        "candidate-health-failed"
    )
    assert "8000" in d.cfg.active_conf.read_text()


def test_interrupted_claim_consumes_one_fresh_attempt(deploy) -> None:
    """A kill inside the claim window (rename / ~10s hash / verification).

    Without the `claiming` state this stranded a valid snapshot at claimed
    under a committed journal. Recovery now binds its full hash and sidecar,
    consumes the one automatic retry, and never returns it to incoming.
    """
    d, _ = deploy
    snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    d.cfg.incoming.rename(d.cfg.claimed)
    d.journal_write("claiming", "8001", None)

    d.reconcile()

    assert adu.snapshot_id_of(d.cfg.basis) == snapshot_id
    assert not d.cfg.claimed.exists()
    assert journal_state(d) == "committed"


def test_rejected_snapshot_does_not_loop_through_claiming_recovery(deploy) -> None:
    """Verification failure must exit the claiming state.

    If the journal stayed at `claiming`, the next reconcile would move the
    known-bad snapshot back to incoming for another doomed attempt, forever.
    """
    d, _ = deploy
    sqlite3.connect(d.cfg.incoming).execute("CREATE TABLE junk(x)")
    with pytest.raises(adu.ApplyError, match="manifest"):
        d.apply()
    assert journal_state(d) == "quarantined"
    _failure, failure_path = failure_record(d)
    assert failure_path.exists()
    # Second run: the quarantined identity is not reverified by the timer.
    assert d.apply() == 0


def test_failed_rollback_keeps_prepared_for_retry(deploy) -> None:
    """committed over a failed disable would revive the candidate on reboot."""
    d, r = deploy
    stage_prepared_release(d)
    r.failures["disable ai-radar-serve@8001"] = 1
    with pytest.raises(adu.ApplyError, match="disable"):
        d.reconcile()
    assert journal_state(d) == "prepared"
    r.failures.clear()
    d.reconcile()
    assert journal_state(d) == "committed"


def test_diverged_nginx_link_blocks_the_switch(deploy) -> None:
    """Installer and runtime resolving different include paths must not switch.

    Otherwise apply flips file A while nginx keeps reading file B: every
    switch "succeeds", traffic never moves, and finalize stops the slot that
    is actually serving.
    """
    d, _ = deploy
    other = d.cfg.active_conf.parent / "somewhere-else.conf"
    other.write_text("upstream ai_radar_active { server 127.0.0.1:8000; }\n")
    d.cfg.nginx_link.unlink()
    d.cfg.nginx_link.symlink_to(other)
    _snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    with pytest.raises(adu.ApplyError, match="disagree on the include path"):
        d.apply()
    assert "8000" in d.cfg.active_conf.read_text(), "no switch may have happened"
    assert journal_state(d) == "prepared"


def test_route_proof_url_must_be_loopback_before_switch(deploy) -> None:
    d, r = deploy
    _snapshot_id, manifest = stage_good_bundle(d)
    install_search_oracle(d, manifest)
    d.cfg.route_proof_search_url = "https://public.invalid/api/v1/timeline"

    with pytest.raises(adu.ApplyError, match="loopback"):
        d.apply()

    assert d.active_port() == "8000"
    joined = [" ".join(call) for call in r.calls]
    assert not any("nginx -s reload" in call for call in joined)


def test_quarantined_failure_record_is_hash_bound(deploy) -> None:
    d, _ = deploy
    sqlite3.connect(d.cfg.incoming).execute("CREATE TABLE junk(x)")
    with pytest.raises(adu.ApplyError, match="manifest"):
        d.apply()
    _failure, failure_path = failure_record(d)
    failure_path.write_text(failure_path.read_text() + " ")

    with pytest.raises(adu.ApplyError, match="no longer matches"):
        d.apply()


def test_quarantined_evidence_is_hash_bound(deploy) -> None:
    d, _ = deploy
    sqlite3.connect(d.cfg.incoming).execute("CREATE TABLE junk(x)")
    with pytest.raises(adu.ApplyError, match="manifest"):
        d.apply()
    failure, _failure_path = failure_record(d)
    evidence = failure["evidence"]
    assert isinstance(evidence, dict) and isinstance(evidence.get("base"), str)
    Path(evidence["base"]).write_bytes(b"tampered evidence")

    with pytest.raises(adu.ApplyError, match="base evidence binding mismatch"):
        d.apply()


def test_severely_corrupt_snapshot_still_exits_claiming(deploy) -> None:
    """Raw sqlite errors from verification must not strand `claiming`."""
    d, _ = deploy
    d.cfg.incoming.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    with pytest.raises(adu.ApplyError):
        d.apply()
    assert journal_state(d) == "quarantined"
    _failure, failure_path = failure_record(d)
    assert failure_path.exists()
