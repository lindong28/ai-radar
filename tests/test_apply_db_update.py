"""State-machine matrix for the blue/green database apply.

Every scenario here is a finding from the adversarial review of the shell
version, kept as a regression: the shell rewrite went through four review
rounds and each round found new crash windows that were properties of bash
itself. The Python rewrite makes the machine unit-testable -- external effects
go through a Runner object this suite replaces.
"""

from __future__ import annotations

import importlib.util
import json
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


class FakeRunner:
    """Records commands; per-command results are injectable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.failures: dict[str, int] = {}  # substring -> rc
        self.slot_active: dict[str, bool] = {"8000": True, "8001": True}
        self.healthz: dict[str, bool] = {"8000": True, "8001": True}

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

        class R:
            returncode = rc

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
            " VALUES ('s1', 'Feed', 'https://e.example/f', 'T1', 1, 'feed', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO items (id, source_id, url, title, content_text, content_hash,"
            " published_at, fetched_at) VALUES ('i1', 's1', 'https://e.example/1',"
            " 'OpenAI Anthropic GPU coverage', 'OpenAI Anthropic GPU 内容', 'h1',"
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
            " '{}', '{}', '2026-01-01T00:00:00Z')"
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
        nginx_link=tmp_path / "etc-link.conf",
        health_wait_s=1,
    )
    cfg.nginx_link.symlink_to(cfg.active_conf)
    runner = FakeRunner()
    d = adu.Deploy(cfg, runner)
    monkeypatch.setattr(adu.Deploy, "free_mem_mb", lambda self: 4096)
    monkeypatch.setattr(adu.time, "sleep", lambda s: None)
    return d, runner


def journal_state(d) -> str:
    return json.loads(d.cfg.journal.read_text())["state"]


def test_no_incoming_is_a_clean_noop(deploy) -> None:
    d, _ = deploy
    assert d.apply() == 0


def test_full_happy_path_switches_and_commits(deploy) -> None:
    d, r = deploy
    make_good_db(d.cfg.incoming)
    assert d.apply() == 0
    assert journal_state(d) == "committed"
    assert "8001" in d.cfg.active_conf.read_text()
    assert d.cfg.basis.exists()
    assert json.loads(d.cfg.receipt.read_text())["port"] == "8001"
    joined = [" ".join(c) for c in r.calls]
    # enable-before-switch: candidate enabled before nginx reload happens
    assert any("enable ai-radar-serve@8001" in c for c in joined)
    reload_at = next(i for i, c in enumerate(joined) if "nginx -s reload" in c)
    enable_at = next(i for i, c in enumerate(joined) if "enable ai-radar-serve@8001" in c)
    assert enable_at < reload_at, "candidate must be reboot-safe before the durable switch"
    # old slot retired last
    stop_old = next(i for i, c in enumerate(joined) if "stop ai-radar-serve@8000" in c)
    assert stop_old > reload_at


def test_corrupt_journal_stops_loudly(deploy) -> None:
    d, _ = deploy
    d.cfg.journal.write_text("not json")
    with pytest.raises(adu.ApplyError, match="refusing to guess"):
        d.apply()


def test_rejected_snapshot_stays_claimed_and_release_untouched(deploy) -> None:
    d, _ = deploy
    sqlite3.connect(d.cfg.incoming).execute("CREATE TABLE junk(x)")
    with pytest.raises(adu.ApplyError, match="failed verification"):
        d.apply()
    assert d.cfg.claimed.exists(), "rejected snapshot must stay for inspection"
    assert "8000" in d.cfg.active_conf.read_text()


def test_prepared_recovery_is_by_hash_not_position(deploy) -> None:
    """The finding that forced the rewrite's recovery model.

    The inactive slot holds the PREVIOUS release's database; a recovery that
    grabs 'whichever file exists' recycles that old database into incoming and
    pushes stale data back into production. Only a hash match may be recovered.
    """
    d, _ = deploy
    # Old release DB sits at the candidate slot path.
    old_db = d.cfg.slot_db("8001")
    make_good_db(old_db)
    # The journalled snapshot (different content, hence different hash) sits
    # at claimed -- killed between journal_write(prepared) and the move.
    make_good_db(d.cfg.claimed)
    # The marker must reach the MAIN file: two make_good_db runs are
    # byte-identical (deterministic writes), and an un-checkpointed UPDATE
    # lives only in the WAL -- leaving both files with the same hash, which
    # is precisely the ambiguity this test exists to resolve.
    conn = sqlite3.connect(d.cfg.claimed)
    conn.execute("UPDATE items SET title = 'newer snapshot marker'")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for suffix in ("-wal", "-shm"):
        side = Path(str(d.cfg.claimed) + suffix)
        if side.exists():
            side.unlink()
    snap = adu.snapshot_id_of(d.cfg.claimed)
    d.journal_write("prepared", "8001", snap)

    d.reconcile()

    assert d.cfg.incoming.exists()
    assert adu.snapshot_id_of(d.cfg.incoming) == snap, "recovered the wrong file"
    assert old_db.exists(), "the old release DB must be left alone"
    assert journal_state(d) == "committed"


def test_prepared_recovery_repairs_a_preflight_crashed_include(deploy) -> None:
    """journal=prepared + include naming the candidate = crashed preflight.

    `switching` is fsynced before the include is first touched, so prepared
    proves the switch point was not crossed; the include must be repaired to
    the serving slot, not trusted as evidence of a switch.
    """
    d, r = deploy
    d.cfg.active_conf.write_text("upstream ai_radar_active { server 127.0.0.1:8001; }\n")
    make_good_db(d.cfg.claimed)
    snap = adu.snapshot_id_of(d.cfg.claimed)
    d.journal_write("prepared", "8001", snap)

    d.reconcile()

    assert "8000" in d.cfg.active_conf.read_text(), "include must be repaired to the serving slot"
    assert journal_state(d) == "committed"
    assert d.cfg.incoming.exists()


def test_prepared_recovery_undoes_candidate_enablement(deploy) -> None:
    d, r = deploy
    make_good_db(d.cfg.claimed)
    d.journal_write("prepared", "8001", adu.snapshot_id_of(d.cfg.claimed))
    d.reconcile()
    joined = [" ".join(c) for c in r.calls]
    assert any("disable ai-radar-serve@8001" in c for c in joined)
    assert any("stop ai-radar-serve@8001" in c for c in joined)


def test_switching_rolls_forward(deploy) -> None:
    d, _ = deploy
    db = d.cfg.slot_db("8001")
    make_good_db(db)
    d.journal_write("switching", "8001", adu.snapshot_id_of(db))
    d.reconcile()
    assert journal_state(d) == "committed"
    assert "8001" in d.cfg.active_conf.read_text()
    assert d.cfg.basis.exists()


def test_switched_with_dead_candidate_stays_switched(deploy) -> None:
    d, r = deploy
    db = d.cfg.slot_db("8001")
    make_good_db(db)
    d.journal_write("switched", "8001", adu.snapshot_id_of(db))
    r.slot_active["8001"] = False
    r.healthz["8001"] = False
    with pytest.raises(adu.ApplyError, match="not serving"):
        d.reconcile()
    assert journal_state(d) == "switched"


def test_finalize_failure_does_not_consume_next_incoming(deploy) -> None:
    """Serialization: an uncommitted release blocks everything behind it."""
    d, r = deploy
    db = d.cfg.slot_db("8001")
    make_good_db(db)
    d.journal_write("switched", "8001", adu.snapshot_id_of(db))
    make_good_db(d.cfg.incoming)
    r.failures["disable ai-radar-serve@8000"] = 1
    with pytest.raises(adu.ApplyError, match="disable"):
        d.apply()
    assert journal_state(d) == "switched"
    assert d.cfg.incoming.exists(), "the next snapshot must not be consumed"


def test_enable_recovers_next_round(deploy) -> None:
    d, r = deploy
    db = d.cfg.slot_db("8001")
    make_good_db(db)
    d.journal_write("switched", "8001", adu.snapshot_id_of(db))
    r.failures["enable ai-radar-serve@8001"] = 1
    with pytest.raises(adu.ApplyError, match="enable"):
        d.reconcile()
    assert journal_state(d) == "switched"
    r.failures.clear()
    d.reconcile()
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
    make_good_db(d.cfg.incoming)
    snap_before = adu.snapshot_id_of(d.cfg.incoming)
    r.slot_active["8001"] = False
    r.healthz["8001"] = False
    with pytest.raises(adu.ApplyError, match="never became healthy"):
        d.apply()
    joined = [" ".join(c) for c in r.calls]
    assert any("disable ai-radar-serve@8001" in c for c in joined)
    assert d.cfg.incoming.exists()
    assert adu.snapshot_id_of(d.cfg.incoming) == snap_before
    assert "8000" in d.cfg.active_conf.read_text()


def test_interrupted_claim_is_returned_for_a_fresh_attempt(deploy) -> None:
    """A kill inside the claim window (rename / ~10s hash / verification).

    Without the `claiming` state this stranded a VALID snapshot at claimed
    under a committed journal -- invisible to every recovery path, then
    silently overwritten by the next upload's rename.
    """
    d, _ = deploy
    make_good_db(d.cfg.claimed)
    d.journal_write("claiming", "8001", "")

    d.reconcile()

    assert d.cfg.incoming.exists(), "the claimed snapshot must return to incoming"
    assert not d.cfg.claimed.exists()
    assert journal_state(d) == "committed"


def test_rejected_snapshot_does_not_loop_through_claiming_recovery(deploy) -> None:
    """Verification failure must exit the claiming state.

    If the journal stayed at `claiming`, the next reconcile would move the
    known-bad snapshot back to incoming for another doomed attempt, forever.
    """
    d, _ = deploy
    sqlite3.connect(d.cfg.incoming).execute("CREATE TABLE junk(x)")
    with pytest.raises(adu.ApplyError, match="failed verification"):
        d.apply()
    assert journal_state(d) == "committed"
    assert d.cfg.claimed.exists()
    # Second run: nothing to do, and the bad snapshot stays put.
    assert d.apply() == 0
    assert d.cfg.claimed.exists()


def test_failed_rollback_keeps_prepared_for_retry(deploy) -> None:
    """committed over a failed disable would revive the candidate on reboot."""
    d, r = deploy
    make_good_db(d.cfg.claimed)
    d.journal_write("prepared", "8001", adu.snapshot_id_of(d.cfg.claimed))
    r.failures["disable ai-radar-serve@8001"] = 1
    with pytest.raises(adu.ApplyError, match="could not retire"):
        d.reconcile()
    assert journal_state(d) == "prepared"
    r.failures.clear()
    d.reconcile()
    assert journal_state(d) == "committed"
    assert d.cfg.incoming.exists()


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
    make_good_db(d.cfg.incoming)
    with pytest.raises(adu.ApplyError, match="disagree on the include path"):
        d.apply()
    assert "8000" in d.cfg.active_conf.read_text(), "no switch may have happened"


def test_severely_corrupt_snapshot_still_exits_claiming(deploy) -> None:
    """Raw sqlite errors from verification must not strand `claiming`."""
    d, _ = deploy
    d.cfg.incoming.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    with pytest.raises(adu.ApplyError):
        d.apply()
    assert journal_state(d) == "committed"
