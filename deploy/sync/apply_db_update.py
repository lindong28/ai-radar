#!/usr/bin/env python3
"""Apply a database snapshot the Mac has pushed, without a serving gap.

Production state is one atomic active release; each slot (8000/8001) serves its
own database file. The candidate slot is prepared and verified while the active
slot keeps serving; only then does nginx swing over.

Why Python and not shell: this is a crash-consistent state machine, and four
adversarial review rounds against the shell version kept finding new windows
that were properties of bash itself -- `fn || fail` suppresses errexit inside
the function, EXIT traps fire on paths they were not written for, and every
hash/JSON operation shells out to another process that can fail half-way.
None of those classes exist here, and the whole machine is unit-testable with
a mocked command runner instead of a stubbed server.

State machine (journal, fsynced before the action it precedes):

    committed -> claiming -> prepared -> switching -> switched -> committed

Recovery rules, learned finding by finding:
  * claiming   -> the snapshot may sit at claimed, mid-hash or mid-verify;
                  return it to incoming so the next run redoes the (cheap,
                  idempotent) claim+verify. Without this state, a kill inside
                  the ~10s hash or the verification left a valid snapshot
                  stranded at claimed under a committed journal -- invisible
                  to every recovery path and silently overwritten later.
  * prepared   -> roll BACK. Traffic never moved (`switching` is durable
                  before the include is touched, so prepared alone proves it).
                  The snapshot is recovered by CONTENT HASH, never by which
                  path happens to exist -- the inactive slot usually holds the
                  *previous* release's database, and reclaiming that by
                  position would push old data back into production.
  * switching  -> roll FORWARD (rewrite include, -t, reload, proceed). The
                  switch is idempotent; guessing whether reload happened isn't.
  * switched   -> roll FORWARD (finalize). Never move the candidate DB: nginx
                  is routing to it.
  * committed  -> nothing to do.
  * unreadable -> stop loudly. Guessing here is how mixtures survive.

Finalize order (candidate proven live -> enable new -> disable old -> stop old
-> basis/receipt -> committed): the old slot is the only fallback until the
candidate is proven, so it is retired last, and `committed` asserts
reboot-correct enablement, so any failure keeps the journal at `switched` and
ends the run without consuming further snapshots.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, "") or default)


@dataclass
class Config:
    home: Path
    data_dir: Path
    incoming: Path
    claimed: Path
    basis_dir: Path
    receipt: Path
    journal: Path
    active_conf: Path
    lock: Path
    # The root-installed symlink nginx actually includes. Checked, not trusted:
    # installer and runtime resolve the real file from the same env overrides,
    # but nothing guarantees they ran with the same environment.
    nginx_link: Path = Path("/etc/nginx/conf.d/ai-radar-active-upstream.conf")
    ports: tuple[str, str] = ("8000", "8001")
    min_free_mem_mb: int = 1536
    probe_terms: tuple[str, ...] = ("OpenAI", "Anthropic", "GPU")
    health_wait_s: int = 120

    @classmethod
    def from_env(cls) -> Config:
        home = _env_path("AI_RADAR_HOME", REPO_ROOT)
        data = _env_path("AI_RADAR_DATA_DIR", home / "data")
        return cls(
            home=home,
            data_dir=data,
            incoming=_env_path("AI_RADAR_INCOMING", data / "radar.db.incoming"),
            claimed=_env_path("AI_RADAR_CLAIMED", data / "radar.db.claimed"),
            basis_dir=_env_path("AI_RADAR_BASIS_DIR", data / "basis"),
            receipt=_env_path("AI_RADAR_SNAPSHOT_RECEIPT", data / "accepted-snapshot.json"),
            journal=_env_path("AI_RADAR_SWITCH_JOURNAL", data / "switch-journal.json"),
            # The REAL file lives where the app user can atomically write it;
            # /etc/nginx/conf.d holds a root-installed symlink pointing here
            # (nginx follows symlinks on include). The rewrite's first draft
            # wrote /etc directly and, running as the app user, would have
            # hit PermissionError on every switch -- after journalling
            # `switching`, wedging the machine at the same point forever.
            active_conf=_env_path(
                "AI_RADAR_ACTIVE_UPSTREAM_CONF",
                data / "nginx" / "ai-radar-active-upstream.conf",
            ),
            lock=_env_path("AI_RADAR_DEPLOY_LOCK", data / ".deploy.lock"),
            nginx_link=_env_path(
                "AI_RADAR_NGINX_LINK",
                Path("/etc/nginx/conf.d/ai-radar-active-upstream.conf"),
            ),
            min_free_mem_mb=int(os.environ.get("AI_RADAR_MIN_FREE_MEM_MB", "1536")),
            probe_terms=tuple(
                os.environ.get("AI_RADAR_PROBE_TERMS", "OpenAI Anthropic GPU").split()
            ),
            health_wait_s=int(os.environ.get("AI_RADAR_HEALTH_WAIT_S", "120")),
        )

    def slot_db(self, port: str) -> Path:
        return self.data_dir / f"radar-{port}.db"

    def other_port(self, port: str) -> str:
        return self.ports[1] if port == self.ports[0] else self.ports[0]

    @property
    def basis(self) -> Path:
        # Directory + fixed basename: rsync --copy-dest matches by basename
        # inside a directory, so the basis keeps the upload's name.
        return self.basis_dir / "radar.db.upload"


class ApplyError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[apply] {msg}", flush=True)


class Runner:
    """All external effects go through here so tests can intercept them."""

    def run(self, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(argv, check=check, capture_output=True, text=True)

    def ok(self, *argv: str) -> bool:
        return self.run(*argv, check=False).returncode == 0


def fsync_path(path: Path) -> None:
    """fsync a file and its parent directory.

    rename is atomic against process crashes, but power loss reorders
    unfsynced writes: without this, disk could hold a `switched` journal whose
    include rename never made it. Recovery tolerates a LAGGING journal
    (switching re-runs the switch); it cannot tolerate a LEADING one.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def snapshot_id_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


class Deploy:
    def __init__(self, cfg: Config, runner: Runner | None = None) -> None:
        self.cfg = cfg
        self.r = runner or Runner()

    # ---------------- journal ----------------

    def journal_write(self, state: str, candidate_port: str, snapshot_id: str) -> None:
        tmp = self.cfg.journal.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "state": state,
                    "candidate_port": candidate_port,
                    "snapshot_id": snapshot_id,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
        )
        fsync_path(tmp)
        os.replace(tmp, self.cfg.journal)
        fsync_path(self.cfg.journal)

    def journal_read(self) -> dict | None:
        if not self.cfg.journal.exists():
            return None
        try:
            data = json.loads(self.cfg.journal.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise ApplyError(
                f"journal {self.cfg.journal} exists but cannot be parsed; "
                f"refusing to guess the release state ({exc})"
            ) from exc
        if "state" not in data:
            raise ApplyError(f"journal {self.cfg.journal} has no state field")
        return data

    # ---------------- environment facts ----------------

    def active_port(self) -> str | None:
        try:
            text = self.cfg.active_conf.read_text()
        except OSError:
            return None
        for token in text.replace(";", " ").split():
            if token.startswith("127.0.0.1:"):
                return token.split(":", 1)[1]
        return None

    def free_mem_mb(self) -> int:
        # MemAvailable, not disk space: an earlier revision ran df here and
        # approved a two-slot start on an OOM-bound host.
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) // 1024
        raise ApplyError("MemAvailable not found in /proc/meminfo")

    def slot_serving(self, port: str) -> bool:
        return self.r.ok(
            "systemctl", "is-active", "--quiet", f"ai-radar-serve@{port}.service"
        ) and self.r.ok("curl", "-sf", "-m", "5", f"http://127.0.0.1:{port}/api/v1/healthz")

    # ---------------- verification ----------------

    def verify_snapshot(self, db: Path) -> None:
        """The full acceptance triple plus schema compatibility.

        Any single check alone gives false greens: a malformed index still
        returns correct hit counts, and an empty index passes both integrity
        checks (both observed on the real database).
        """
        try:
            conn = sqlite3.connect(db)
        except sqlite3.Error as exc:
            raise ApplyError(f"cannot open snapshot: {exc}") from exc
        # close() can itself raise on I/O failure; it lives inside the same
        # wrapper so even that exit path leaves the claiming state cleanly.
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ApplyError("integrity_check failed")
            try:
                conn.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
            except sqlite3.DatabaseError as exc:
                raise ApplyError(f"fts5 integrity-check failed: {exc}") from exc
            for table in ("items", "curated_items", "curation_runs", "item_evaluations"):
                if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] <= 0:
                    raise ApplyError(f"snapshot has no rows in {table}")
            for term in self.cfg.probe_terms:
                hits = conn.execute(
                    "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (term,)
                ).fetchone()[0]
                if hits <= 0:
                    raise ApplyError(f"probe {term!r} returned no hits; index unusable")
            # Schema compatibility with the code THIS host serves it with. The
            # snapshot arrives pre-migrated and serve runs with migrations
            # disabled, so drift would otherwise surface as runtime 500s.
            sys.path.insert(0, str(self.cfg.home / "src"))
            try:
                from airadar import db as airadar_db
            except ImportError as exc:
                raise ApplyError(f"cannot import airadar for schema check: {exc}") from exc
            if not airadar_db._fts_schema_matches(conn):
                raise ApplyError("snapshot FTS schema does not match what this code expects")
            if not conn.execute(
                "SELECT 1 FROM airadar_migrations WHERE id='004_enrich_stage'"
            ).fetchone():
                raise ApplyError("snapshot predates migration 004; this code cannot serve it")
        except (sqlite3.Error, OSError) as exc:
            # Severe corruption surfaces as raw sqlite/OS errors from any of
            # the probes above. They are expected verification outcomes, not
            # programming errors, and must take the same "leave claiming,
            # keep the file for inspection" path as a clean rejection --
            # otherwise the journal stays `claiming` and the next reconcile
            # loops the known-bad snapshot back to incoming forever.
            raise ApplyError(f"snapshot verification errored: {exc}") from exc
        finally:
            try:
                conn.close()
            except (sqlite3.Error, OSError) as exc:  # pragma: no cover - disk I/O edge
                raise ApplyError(f"snapshot connection close failed: {exc}") from exc

    # ---------------- switch primitives ----------------

    def write_active_include(self, port: str) -> None:
        content = f"upstream ai_radar_active {{ server 127.0.0.1:{port}; }}\n"
        self.cfg.active_conf.parent.mkdir(parents=True, exist_ok=True)
        # Same-directory temp + rename: rename is only atomic within one
        # filesystem, and /tmp is usually a different one.
        tmp = self.cfg.active_conf.with_suffix(".tmp")
        tmp.write_text(content)
        fsync_path(tmp)
        os.replace(tmp, self.cfg.active_conf)
        # Fail-closed durability, same reasoning as the journal: if this sync
        # silently failed, a later power loss could persist `committed` while
        # the include rename never reached disk -- and committed reconcile
        # (correctly) touches nothing, freezing the mixture.
        fsync_path(self.cfg.active_conf)

    def assert_nginx_link_matches(self) -> None:
        """The file we write must be the file nginx reads.

        Config comes from env overrides (AI_RADAR_DATA_DIR, _HOME, explicit
        _ACTIVE_UPSTREAM_CONF), and the installer resolved the same values at
        install time -- possibly with a different environment. If they
        diverged, apply would flip file A while nginx keeps including file B:
        every switch "succeeds", traffic never moves, and finalize then stops
        the slot that is actually serving. Checked before any switch rather
        than trusted; a missing link means install-server.sh never ran.
        """
        link = self.cfg.nginx_link
        if not link.is_symlink():
            raise ApplyError(
                f"{link} is not a symlink to the active include; run install-server.sh"
            )
        target = Path(os.readlink(link))
        if target != self.cfg.active_conf:
            raise ApplyError(
                f"nginx includes {target} but this run writes {self.cfg.active_conf}; "
                "installer and runtime disagree on the include path -- fix the "
                "environment or re-run install-server.sh before any switch"
            )

    def roll_switch_forward(self, candidate: str, snapshot_id: str) -> None:
        """Idempotent forward completion: include -> -t -> reload -> finalize.

        -t failure here stops loudly for a human. The include content is
        machine-generated and fixed-form, so a rejection means the wider nginx
        config is broken; auto-reverting would hide that while the journal says
        a switch is still owed.
        """
        self.assert_nginx_link_matches()
        self.write_active_include(candidate)
        if not self.r.ok("sudo", "nginx", "-t"):
            raise ApplyError("nginx rejected the config while rolling the switch forward")
        if not self.r.ok("sudo", "nginx", "-s", "reload"):
            raise ApplyError("nginx reload failed while rolling the switch forward")
        self.journal_write("switched", candidate, snapshot_id)
        self.finalize(candidate, snapshot_id)

    def finalize(self, candidate: str, snapshot_id: str) -> None:
        """Prove candidate live -> enable new -> disable old -> stop old ->
        basis/receipt -> committed. Any failure raises: the journal stays
        `switched` and THIS RUN ENDS -- an earlier revision converted this to
        success and carried on to consume the next incoming, starting a
        reverse switch on top of an uncommitted release.
        """
        cfg = self.cfg
        old = cfg.other_port(candidate)
        new_unit = f"ai-radar-serve@{candidate}.service"
        old_unit = f"ai-radar-serve@{old}.service"

        if not self.slot_serving(candidate):
            # Reconcile paths reach here without the normal path's health wait
            # (e.g. after power loss, candidate never restarted).
            self.r.ok("sudo", "systemctl", "restart", new_unit)
            deadline = time.monotonic() + self.cfg.health_wait_s
            while time.monotonic() < deadline:
                if self.slot_serving(candidate):
                    break
                time.sleep(2)
            else:
                raise ApplyError(
                    f"candidate slot {candidate} is not serving; staying in switched"
                )

        if not self.r.ok("sudo", "systemctl", "enable", new_unit):
            raise ApplyError("enable candidate failed; staying in switched")
        if not self.r.ok("sudo", "systemctl", "disable", old_unit):
            raise ApplyError(f"disable {old_unit} failed; staying in switched")
        # Only now is the old slot expendable; a stop failure is tolerable
        # (a lingering process wastes memory but nginx no longer routes to it).
        self.r.ok("sudo", "systemctl", "stop", old_unit)

        # basis and receipt get the same durability treatment as the journal:
        # committed asserts they exist and are whole. An in-place copyfile also
        # opened a window where the Mac's next rsync could read a half-written
        # copy-dest and silently fall back to a full transfer.
        cfg.basis_dir.mkdir(parents=True, exist_ok=True)
        basis_tmp = cfg.basis.with_suffix(".tmp")
        shutil.copyfile(cfg.slot_db(candidate), basis_tmp)
        fsync_path(basis_tmp)
        os.replace(basis_tmp, cfg.basis)
        fsync_path(cfg.basis)

        tmp = cfg.receipt.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "port": candidate,
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
        )
        fsync_path(tmp)
        os.replace(tmp, cfg.receipt)
        fsync_path(cfg.receipt)
        self.journal_write("committed", candidate, snapshot_id)
        log(f"committed: snapshot {snapshot_id} serving on {candidate}")

    # ---------------- recovery ----------------

    def _recover_snapshot_by_hash(self, port: str, snapshot_id: str) -> None:
        """Return the prepared snapshot to incoming, identified by CONTENT.

        The inactive slot usually holds the previous release's database, so
        "whichever file exists" is not evidence of which file is the snapshot
        -- position-based recovery here once meant recycling an old release
        into incoming and pushing old data back into production. Files that
        do not match the journal's hash are left where they are.
        """
        if self.cfg.incoming.exists():
            return  # a newer upload wins; leftovers stay put for inspection
        for station in (self.cfg.slot_db(port), self.cfg.claimed):
            if station.exists() and snapshot_id_of(station) == snapshot_id:
                station.rename(self.cfg.incoming)
                log(f"reconcile: recovered snapshot {snapshot_id} from {station.name}")
                return
        log(
            "reconcile: no file matching the journalled snapshot survived; "
            "nothing recovered (leftovers, if any, kept for inspection)"
        )

    def reconcile(self) -> None:
        entry = self.journal_read()
        if entry is None or entry["state"] == "committed":
            return
        state = entry["state"]
        port = entry.get("candidate_port", "")
        snap = entry.get("snapshot_id", "")
        if not port:
            raise ApplyError(f"journal state {state!r} names no candidate port")

        if state == "claiming":
            log("reconcile: interrupted claim; returning the snapshot for a fresh attempt")
            if self.cfg.claimed.exists() and not self.cfg.incoming.exists():
                self.cfg.claimed.rename(self.cfg.incoming)
            self.journal_write("committed", self.active_port() or "", "")
        elif state == "prepared":
            # Traffic never moved: `switching` is journalled (fsynced) before
            # the real include is first touched, so prepared alone proves the
            # switch point was not crossed. Roll back by content hash. An
            # earlier revision trusted the include here ("if it names the
            # candidate, roll forward") -- but a crashed nginx preflight can
            # leave the include naming a candidate whose DB was already
            # recovered, and rolling forward then routes nginx at nothing.
            log(f"reconcile: unfinished prepare on slot {port}")
            disabled = self.r.ok("sudo", "systemctl", "disable", f"ai-radar-serve@{port}.service")
            stopped = self.r.ok("sudo", "systemctl", "stop", f"ai-radar-serve@{port}.service")
            if not (disabled and stopped):
                raise ApplyError(
                    "prepared rollback could not retire the candidate unit; "
                    "journal stays prepared for a retry"
                )
            active = self.active_port()
            if active == port:
                # Repair a preflight-crash include: point it back at the slot
                # that is actually serving.
                other = self.cfg.other_port(port)
                log(f"reconcile: include named the unswitched candidate; restoring {other}")
                self.write_active_include(other)
                self.r.ok("sudo", "nginx", "-s", "reload")
            self._recover_snapshot_by_hash(port, snap)
            self.journal_write("committed", self.active_port() or "", snap)
        elif state == "switching":
            log(f"reconcile: completing an in-flight switch to slot {port}")
            self.roll_switch_forward(port, snap)
        elif state == "switched":
            log(f"reconcile: completing an interrupted switch to slot {port}")
            self.roll_switch_forward(port, snap)
        else:
            raise ApplyError(f"unrecognised journal state {state!r}")

    # ---------------- main ----------------

    def apply(self) -> int:
        cfg = self.cfg
        cfg.data_dir.mkdir(parents=True, exist_ok=True)

        lock_fd = os.open(cfg.lock, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ApplyError(f"another deploy or sync holds the lock ({cfg.lock})")
            return self._apply_locked()
        finally:
            # Process exit would release the lock anyway, but callers that
            # reuse this object in-process (tests, a future daemon) must not
            # inherit a lock held by a finished run.
            os.close(lock_fd)

    def _apply_locked(self) -> int:
        cfg = self.cfg
        self.reconcile()

        if not cfg.incoming.exists():
            log("no incoming snapshot; nothing to do")
            return 0

        active = self.active_port()
        if not active:
            raise ApplyError(
                f"no active upstream in {cfg.active_conf}; run install-server.sh first"
            )
        candidate = cfg.other_port(active)
        log(f"active={active} candidate={candidate}")

        free = self.free_mem_mb()
        if free < cfg.min_free_mem_mb:
            # Fail closed: a stop-start fallback would quietly retract the
            # zero-downtime guarantee this mechanism exists to provide.
            raise ApplyError(
                f"only {free}MB memory available, need {cfg.min_free_mem_mb}MB "
                "to run two slots; keeping the current release"
            )

        # Claim first, verify second: incoming can be atomically replaced by
        # the next upload at any moment, and without the claim the race is
        # "verify one inode, ship another". `claiming` is journalled first so
        # a kill anywhere between here and `prepared` (the rename, the ~10s
        # hash, the verification) leaves a state recovery recognises.
        self.journal_write("claiming", candidate, "")
        cfg.incoming.rename(cfg.claimed)
        snapshot_id = snapshot_id_of(cfg.claimed)

        log(f"verifying claimed snapshot {snapshot_id}")
        try:
            self.verify_snapshot(cfg.claimed)
        except ApplyError as exc:
            # Left at claimed for inspection; a newer upload landing at
            # incoming is processed normally on the next run. The journal must
            # leave `claiming` here, or the next reconcile would loop this
            # known-bad snapshot back to incoming for another doomed attempt.
            self.journal_write("committed", active, snapshot_id)
            raise ApplyError(
                f"claimed snapshot failed verification ({exc}); "
                f"kept at {cfg.claimed} for inspection, active release untouched"
            ) from exc

        candidate_db = cfg.slot_db(candidate)
        candidate_unit = f"ai-radar-serve@{candidate}.service"
        try:
            # Journal BEFORE the move: a kill between the two leaves the
            # snapshot at claimed under a prepared journal, and prepared
            # recovery checks claimed (by hash).
            self.journal_write("prepared", candidate, snapshot_id)
            cfg.claimed.rename(candidate_db)
            for suffix in ("-wal", "-shm"):
                side = Path(str(candidate_db) + suffix)
                if side.exists():
                    side.unlink()

            log("starting candidate slot")
            if not self.r.ok("sudo", "systemctl", "restart", candidate_unit):
                raise ApplyError("could not restart the candidate slot")
            deadline = time.monotonic() + cfg.health_wait_s
            while time.monotonic() < deadline:
                if self.slot_serving(candidate):
                    break
                time.sleep(2)
            else:
                raise ApplyError("candidate never became healthy; active release untouched")

            # Enable BEFORE the durable switch: both slots enabled is a safe
            # intermediate (a reboot starts both, nginx still points at the
            # old one); an include durably naming a slot systemd will not
            # start is the outage.
            if not self.r.ok("sudo", "systemctl", "enable", candidate_unit):
                raise ApplyError("could not enable the candidate slot")
        except ApplyError:
            # Rollback of the prepared phase, in one place rather than a trap:
            # undo enablement, stop the candidate, recover the snapshot by
            # hash. Rollback commands must SUCCEED before the journal leaves
            # prepared -- writing committed over a failed disable would let a
            # reboot start a candidate everyone believes is retired. On
            # failure the journal stays prepared and the next run's reconcile
            # retries this same rollback.
            disabled = self.r.ok("sudo", "systemctl", "disable", candidate_unit)
            stopped = self.r.ok("sudo", "systemctl", "stop", candidate_unit)
            if disabled and stopped:
                self._recover_snapshot_by_hash(candidate, snapshot_id)
                self.journal_write("committed", active, snapshot_id)
            else:
                log("rollback commands failed; journal stays prepared for a retried rollback")
            raise

        # -------- the switch: from here, recovery only rolls FORWARD --------
        # Link divergence is checked BEFORE journalling `switching`: from that
        # state recovery can only roll forward, so a config problem discovered
        # after it forces a fix-and-forward instead of a clean prepared
        # rollback. (roll_switch_forward re-checks for its recovery callers.)
        self.assert_nginx_link_matches()
        # `switching` is journalled (fsynced) before the include is touched,
        # which is what makes `prepared` unambiguous evidence of "not crossed".
        self.journal_write("switching", candidate, snapshot_id)
        self.roll_switch_forward(candidate, snapshot_id)
        log(f"release complete: snapshot {snapshot_id} serving on {candidate}")
        return 0


def main() -> int:
    try:
        return Deploy(Config.from_env()).apply()
    except ApplyError as exc:
        print(f"[apply] ✗ {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
