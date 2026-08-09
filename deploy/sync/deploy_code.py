#!/usr/bin/env python3
"""Deploy a pushed commit to the live tree, with rollback and a durable journal.

Invoked by the bare repo's post-receive shell stub (which only parses the ref
line and execs this). Standard library only, so it runs on the system python3
before any venv exists.

The shell version of this logic went through two adversarial review rounds and
both times the blocking findings were bash properties: `fn || handler` turns
off errexit inside fn, and there is no sane way to hold transactional state.
Same lesson as the DB apply state machine, same remedy.

Tree materialization uses `git read-tree`, measured before trusting:
  * `read-tree -m -u OLD NEW` applies the diff to the work tree -- updates,
    ADDS, and DELETES tracked files -- while leaving untracked files (data/,
    logs/, .env, .venv) and, unlike `checkout`, the bare repo's HEAD alone.
  * `checkout -f SHA -- .` (the first attempt) runs in overlay mode and leaves
    deleted files behind for import to find; plain `checkout -f SHA` deletes
    correctly but moves the bare HEAD; and a `GIT_INDEX_FILE` pointing at an
    existing empty file (mktemp!) is fatal: "index file smaller than expected".

Journal: {state: promoting, old_sha, new_sha}, fsynced before the live tree is
first touched. Recovery (run on every deploy, before anything else): a stale
`promoting` entry means a previous deploy died mid-flight -- roll back to its
old_sha so the host converges on a complete release instead of serving a
half-updated tree. The health checker pages on both the failure marker and a
stale journal, because a failed post-receive CANNOT fail the `git push` that
triggered it (githooks(5)): alerting is the reliable feedback channel here,
not the push exit code.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


def log(msg: str) -> None:
    print(f"[deploy] {msg}", flush=True)


class DeployError(RuntimeError):
    pass


@dataclass
class Config:
    home: Path
    bare: Path
    candidate_dir: Path
    lock: Path
    active_conf: Path
    uv: Path
    journal: Path
    failed_marker: Path
    slot_env_dir: Path
    health_wait_s: int = 120

    @classmethod
    def from_env(cls) -> Config:
        home = Path(os.environ.get("AI_RADAR_HOME", "") or Path.home() / "ai-radar")
        return cls(
            home=home,
            bare=Path(os.environ.get("AI_RADAR_BARE", "") or Path.home() / "ai-radar.git"),
            candidate_dir=Path(
                os.environ.get("AI_RADAR_CODE_CANDIDATE", "")
                or Path.home() / "ai-radar-candidate"
            ),
            lock=Path(os.environ.get("AI_RADAR_DEPLOY_LOCK", "") or home / "data" / ".deploy.lock"),
            active_conf=Path(
                os.environ.get("AI_RADAR_ACTIVE_UPSTREAM_CONF", "")
                or home / "data" / "nginx" / "ai-radar-active-upstream.conf"
            ),
            uv=Path(os.environ.get("AI_RADAR_UV", "") or Path.home() / ".local" / "bin" / "uv"),
            journal=Path(
                os.environ.get("AI_RADAR_CODE_JOURNAL", "")
                or home / "data" / "code-deploy-journal.json"
            ),
            failed_marker=Path(
                os.environ.get("AI_RADAR_DEPLOY_FAILED_MARKER", "")
                or home / "data" / ".deploy-failed"
            ),
            slot_env_dir=Path(
                os.environ.get("AI_RADAR_SLOT_DIR", "") or Path("/etc/ai-radar/slots")
            ),
            health_wait_s=int(os.environ.get("AI_RADAR_HEALTH_WAIT_S", "120")),
        )


def fsync_path(path: Path) -> None:
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


class Runner:
    def run(
        self, *argv: str, env: dict | None = None, cwd: Path | None = None
    ) -> subprocess.CompletedProcess:
        merged = {**os.environ, **(env or {})}
        return subprocess.run(
            argv, check=False, capture_output=True, text=True, env=merged, cwd=cwd
        )

    def ok(self, *argv: str, env: dict | None = None, cwd: Path | None = None) -> bool:
        return self.run(*argv, env=env, cwd=cwd).returncode == 0


class CodeDeploy:
    def __init__(self, cfg: Config, runner: Runner | None = None) -> None:
        self.cfg = cfg
        self.r = runner or Runner()

    # ---------------- journal ----------------

    def journal_write(self, payload: dict) -> None:
        tmp = self.cfg.journal.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        fsync_path(tmp)
        os.replace(tmp, self.cfg.journal)
        fsync_path(self.cfg.journal)

    def journal_read(self) -> dict | None:
        if not self.cfg.journal.exists():
            return None
        try:
            return json.loads(self.cfg.journal.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise DeployError(f"code-deploy journal unreadable; refusing to guess: {exc}") from exc

    # ---------------- facts ----------------

    def active_port(self) -> str | None:
        """The serving slot's port, or None ONLY when genuinely un-bootstrapped.

        The distinction is load-bearing: a truly fresh host (no include, no
        prior deploy) legitimately has no active slot and skips the schema
        gate / restart. But an include that merely became unreadable or
        malformed on a host that HAS deployed before must not be read as
        bootstrap -- that path skips the gate and restart, then stamps a new
        .deployed-sha while the old serve keeps running old code. So this
        raises for a present-but-unusable include and reserves None for real
        absence; callers treat a raise as fail-closed.
        """
        # read_text distinguishes the two cases directly. Path.exists() (an
        # earlier version) routes PermissionError and every other OSError to
        # False, so a present-but-unreadable include would look like a fresh
        # host -- skipping the gate/restart and stamping a new SHA over a serve
        # still running old code. Only FileNotFoundError is bootstrap.
        try:
            text = self.cfg.active_conf.read_text()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DeployError(f"active upstream include exists but is unreadable: {exc}") from exc
        for token in text.replace(";", " ").split():
            if token.startswith("127.0.0.1:"):
                return token.split(":", 1)[1]
        raise DeployError(
            f"active upstream include {self.cfg.active_conf} names no 127.0.0.1:<port>; "
            "refusing to treat a malformed include as a fresh install"
        )

    def active_db_path(self, port: str) -> Path | None:
        """The DB the active slot actually serves, from its slot env file.

        Not a guessed conventional path: slots bind their DB through
        /etc/ai-radar/slots/<port>.env, and a gate that checks a file the slot
        does not use silently checks nothing.
        """
        env_file = self.cfg.slot_env_dir / f"{port}.env"
        try:
            for line in env_file.read_text().splitlines():
                match = re.match(r"^AI_RADAR_DB=(.+)$", line.strip())
                if match:
                    return Path(match.group(1))
        except OSError:
            return None
        return None

    def deployed_sha(self) -> str | None:
        try:
            return (self.cfg.home / ".deployed-sha").read_text().strip() or None
        except OSError:
            return None

    # ---------------- primitives ----------------

    def materialize(self, sha: str, tree: Path, index: Path, base: str | None) -> None:
        """Bring `tree`'s tracked files to exactly `sha`, propagate deletions,
        preserve untracked files, and leave the bare repo's HEAD alone.

        Force-based, not merge-based. `git checkout-index -f -a` writes every
        tracked file from `sha` unconditionally, so materialize does not depend
        on the worktree already matching `base` and cannot hit the "entry not
        uptodate" that a stat-less `read-tree -m -u` raises the instant a
        deploy must OVERWRITE (not merely add) a tracked file. A freshly
        `read-tree`'d base index records no stat, so `-m` treats every entry as
        potentially dirty and refuses any path it must change; that only stayed
        hidden because earlier deploys happened to add files (.python-version)
        rather than change them, and it broke rollback the same way -- rollback
        passes base=the failed `new`, but a promote that died before touching
        the worktree leaves it at `old`, so `-m -u new old` also failed
        "not uptodate". Both are measured against the real bare repo.

        Order matters and is the opposite of the obvious one: DELETIONS FIRST,
        then checkout. A path that changed file<->directory between base and sha
        (a `pkg` file becoming `pkg/mod.py`, or the reverse) would otherwise
        have the stale file/dir block checkout-index; removing the base-only
        paths and pruning emptied ancestors first clears the way. The deletion
        set uses `--no-renames` so a rename shows as delete+add (default rename
        detection reports `R`, which `--diff-filter=D` drops, orphaning the old
        path), and `-z`/NUL parsing so paths with spaces or newlines are exact.

        Force overwrite has one edge the merge form guarded for free: it will
        clobber an untracked runtime file if a commit ever tracks that path.
        `_guard_runtime_paths` fails the deploy closed before any live write,
        so a mistaken `git add` of .env / .venv / a slot DB / logs cannot
        irreversibly destroy live state that rollback (git-only) can't restore.
        """
        env = {
            "GIT_DIR": str(self.cfg.bare),
            "GIT_WORK_TREE": str(tree),
            "GIT_INDEX_FILE": str(index),
        }
        # Refuse, before touching the tree, any commit that tracks a
        # runtime-owned path -- checkout-index -f would overwrite live state.
        self._guard_runtime_paths(sha, env)
        # A pre-existing (possibly empty) index file is fatal to git -- "index
        # file smaller than expected" -- so start each materialize clean.
        if index.exists():
            index.unlink()
        # Deletions before checkout so a file<->directory swap does not block it.
        if base:
            self._apply_deletions(base, sha, tree, env)
        # -c core.bare=false: the source is a bare repo; GIT_WORK_TREE lets
        # these commands operate on a worktree, and being explicit avoids any
        # "must be run in a work tree" refusal on hosts whose git is stricter.
        if not self.r.ok("git", "-c", "core.bare=false", "read-tree", sha, env=env):
            raise DeployError(f"could not read tree {sha} into the deploy index")
        if not self.r.ok("git", "-c", "core.bare=false", "checkout-index", "-f", "-a", env=env):
            raise DeployError(f"could not check out {sha} into {tree}")

    # data/ is runtime-owned except for these intentionally-tracked config
    # files; everything under .env (the exact secrets file), .venv/, and logs/
    # is live state git must never write over. .env.example is not .env, so it
    # is not caught.
    _RUNTIME_ALLOW = frozenset({"data/sources.toml"})

    @classmethod
    def _is_runtime_owned(cls, path: str) -> bool:
        if path in cls._RUNTIME_ALLOW:
            return False
        return (
            path == ".env"
            or path.startswith(".venv/")
            or path.startswith("logs/")
            or path.startswith("data/")
        )

    def _guard_runtime_paths(self, sha: str, env: dict) -> None:
        res = self.r.run("git", "-c", "core.bare=false", "ls-tree", "-r",
                         "--name-only", "-z", sha, env=env)
        if res.returncode != 0:
            raise DeployError(
                f"could not list tree {sha}: {(res.stderr or res.stdout)[-200:]}"
            )
        offenders = sorted(
            p for p in res.stdout.split("\0") if p and self._is_runtime_owned(p)
        )
        if offenders:
            raise DeployError(
                "refusing to deploy: commit tracks runtime-owned paths that "
                "checkout would overwrite (live state git cannot restore): "
                + ", ".join(offenders[:10])
            )

    def _apply_deletions(self, base: str, sha: str, tree: Path, env: dict) -> None:
        diff = self.r.run("git", "-c", "core.bare=false", "diff", "--no-renames",
                          "--name-only", "--diff-filter=D", "-z", base, sha, env=env)
        if diff.returncode != 0:
            raise DeployError(
                f"could not compute deletions {base}->{sha}: "
                f"{(diff.stderr or diff.stdout)[-200:]}"
            )
        for rel in diff.stdout.split("\0"):
            if not rel:  # NUL-separated; trailing empty field, never strip()
                continue
            target = tree / rel
            try:
                target.unlink()
            except FileNotFoundError:
                pass  # already gone; deletion is idempotent
            except OSError as exc:
                raise DeployError(f"could not delete {rel} removed in {sha}: {exc}")
            # Prune ancestors emptied by the deletion, so a directory that
            # became a file in sha is not blocked by the leftover empty dir.
            parent = target.parent
            while parent != tree:
                try:
                    parent.rmdir()
                except OSError:
                    break  # non-empty or already gone; stop climbing
                parent = parent.parent

    def uv_sync_locked(self, tree: Path) -> None:
        # --locked, not --frozen: frozen trusts a stale lock silently; locked
        # fails when pyproject and uv.lock disagree -- exactly the commit that
        # should not reach production.
        result = self.r.run(str(self.cfg.uv), "sync", "--locked", cwd=tree)
        if result.returncode != 0:
            raise DeployError(
                f"uv sync --locked failed in {tree}: {(result.stderr or result.stdout)[-300:]}"
            )

    def wait_healthy(self, port: str) -> bool:
        deadline = time.monotonic() + self.cfg.health_wait_s
        while time.monotonic() < deadline:
            if self.r.ok("curl", "-sf", "-m", "5", f"http://127.0.0.1:{port}/api/v1/healthz"):
                return True
            time.sleep(2)
        return False

    def restart_serving_slot(self, port: str) -> None:
        if not self.r.ok("sudo", "systemctl", "restart", f"ai-radar-serve@{port}.service"):
            raise DeployError(f"restart of serve@{port} failed")
        if not self.wait_healthy(port):
            raise DeployError(f"serve@{port} did not come back healthy")

    # ---------------- gates ----------------

    def verify_candidate(self, sha: str) -> None:
        cfg = self.cfg
        if cfg.candidate_dir.exists():
            shutil.rmtree(cfg.candidate_dir)
        cfg.candidate_dir.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as tmp:
            # The index path must NOT exist yet: git treats an existing empty
            # file as a corrupt index and dies (measured -- a bare mktemp here
            # made every deploy fail before this run).
            self.materialize(sha, cfg.candidate_dir, Path(tmp) / "index", base=None)

        log("candidate: resolving dependencies (uv sync --locked)")
        result = self.r.run(str(cfg.uv), "sync", "--locked", cwd=cfg.candidate_dir)
        if result.returncode != 0:
            raise DeployError(
                "candidate uv sync --locked failed (stale lockfile?); live tree untouched: "
                + (result.stderr or result.stdout)[-300:]
            )
        py = cfg.candidate_dir / ".venv" / "bin" / "python3"
        if not self.r.ok(str(py), "-c", "import airadar.cli",
                         env={"PYTHONPATH": str(cfg.candidate_dir / "src")}):
            raise DeployError("candidate cannot import airadar; live tree untouched")

        # Byte-compile the NEXT version of the deploy control plane. This run
        # is executed from the CURRENT live tree, so a commit that breaks the
        # deployer would otherwise deploy fine now and only reveal the broken
        # authority on the following push -- possibly before it can even write
        # a failure marker. Catching it here keeps the control plane self-hosting.
        for tool in ("deploy/sync/deploy_code.py", "deploy/sync/schema_gate.py"):
            if not self.r.ok(str(py), "-m", "py_compile", str(cfg.candidate_dir / tool)):
                raise DeployError(f"candidate's {tool} does not compile; live tree untouched")

        port = self.active_port()
        if not port:
            log("no active slot; skipping the schema gate (bootstrap install)")
            return
        db = self.active_db_path(port)
        if db is None or not db.exists():
            raise DeployError(
                f"cannot resolve the active slot's database (slot env for {port}); "
                "refusing to skip the schema gate on a live host"
            )
        log("candidate: schema compatibility against the active database")
        # Run the candidate's schema_gate.py with the candidate's interpreter
        # and source, so "what this code expects" is the code about to go live.
        gate_script = cfg.candidate_dir / "deploy" / "sync" / "schema_gate.py"
        result = self.r.run(str(py), str(gate_script), str(db),
                            env={"PYTHONPATH": str(cfg.candidate_dir / "src")})
        if result.returncode != 0:
            raise DeployError(
                "candidate code does not match the active database "
                f"({(result.stderr or result.stdout).strip()[-200:]}); sync the DB first"
            )

    # ---------------- transaction ----------------

    def promote(self, sha: str, base: str | None) -> None:
        self.materialize(sha, self.cfg.home, self.cfg.home / ".git-deploy-index", base)
        self.uv_sync_locked(self.cfg.home)

    def write_deployed_sha(self, sha: str) -> None:
        sha_file = self.cfg.home / ".deployed-sha"
        tmp = sha_file.with_suffix(".tmp")
        tmp.write_text(sha + "\n")
        fsync_path(tmp)
        os.replace(tmp, sha_file)
        fsync_path(sha_file)

    def rollback(self, old_sha: str, from_sha: str | None, port: str | None) -> None:
        log(f"rolling back to {old_sha}")
        # Two-tree read from the tree we were promoting: this DELETES files the
        # new release added that old_sha lacks. A single-tree reset (an earlier
        # version) could not know those files were tracked and left them behind
        # for import to find -- the very orphan problem the forward path fixed.
        self.materialize(old_sha, self.cfg.home, self.cfg.home / ".git-deploy-index", base=from_sha)
        self.uv_sync_locked(self.cfg.home)
        # .deployed-sha is part of the release identity: restore it as part of
        # rolling back, before the journal goes idle, or the next deploy would
        # compute its rollback base from a stale/half-written value.
        self.write_deployed_sha(old_sha)
        if port:
            self.restart_serving_slot(port)
        log(f"rollback to {old_sha} complete")

    def commit_release(self, new_sha: str) -> None:
        """Record a release whose new code is already live and healthy.

        Reachable from the normal path and from `serving`/`activating`
        recovery, so it is idempotent: rewriting the same SHA and journal idle
        converges. Once the journal is idle the deploy has SUCCEEDED -- marker
        and candidate cleanup is best-effort and must never raise, or a
        post-success housekeeping failure would make main() page for a deploy
        that actually worked.
        """
        self.write_deployed_sha(new_sha)
        self.journal_write({"state": "idle", "deployed": new_sha})
        try:
            if self.cfg.failed_marker.exists():
                self.cfg.failed_marker.unlink()
            if self.cfg.candidate_dir.exists():
                shutil.rmtree(self.cfg.candidate_dir)
        except OSError as exc:
            log(f"post-success cleanup failed (deploy still succeeded): {exc}")

    def reconcile(self) -> None:
        entry = self.journal_read()
        if not entry:
            return
        state = entry.get("state")
        old = entry.get("old_sha") or ""
        new = entry.get("new_sha") or None
        port = self.active_port()
        if state == "promoting":
            # The live tree was being materialized to new; serve was never
            # restarted onto it. Roll BACK -- what is serving is still old.
            log(f"reconcile: a previous deploy of {new} died before activation")
            if not old:
                raise DeployError(
                    "interrupted first-ever deploy: no previous SHA to roll back to; "
                    "re-push to retry or finish manually"
                )
            self.rollback(old, new, port)
            self.journal_write({"state": "idle", "recovered_to": old})
        elif state == "activating":
            # A restart onto new code was in flight when we died -- serve may
            # or may not have picked it up. Re-run restart+health (idempotent)
            # and let the OUTCOME decide direction: healthy -> forward, not
            # healthy -> the new code cannot serve, roll back. This is the
            # window the plain `serving`-after-restart ordering could not cover.
            if not new:
                raise DeployError("journal says activating but names no SHA")
            log(f"reconcile: re-driving an interrupted activation of {new}")
            try:
                if port:
                    self.restart_serving_slot(port)
            except (DeployError, OSError, subprocess.SubprocessError):
                if not old:
                    raise
                self.rollback(old, new, port)
                self.journal_write({"state": "idle", "recovered_to": old})
                return
            self.journal_write({"state": "serving", "new_sha": new})
            self.commit_release(new)
        elif state == "serving":
            # New code is already live and healthy; only the record
            # (deployed-sha + journal idle) was interrupted. Roll FORWARD --
            # rolling back a serving version would be the outage.
            if not new:
                raise DeployError("journal says serving but names no SHA")
            log(f"reconcile: completing the record of an already-serving deploy {new}")
            self.commit_release(new)

    def deploy(self, new_sha: str) -> None:
        cfg = self.cfg
        cfg.home.mkdir(parents=True, exist_ok=True)
        (cfg.home / "data").mkdir(exist_ok=True)

        # Same lock file as the DB apply (flock(2) either way): a code deploy
        # and a slot switch computing state concurrently would each act on the
        # other's stale view. Blocking wait -- a push should queue behind a
        # running sync, not bounce.
        lock_fd = os.open(cfg.lock, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            log("waiting for the deploy lock (a DB sync may be in progress)...")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._deploy_locked(new_sha)
        finally:
            os.close(lock_fd)

    def _deploy_locked(self, new_sha: str) -> None:
        self.reconcile()

        old_sha = self.deployed_sha()
        port = self.active_port()
        log(f"deploying {new_sha} (previous: {old_sha or 'none'}, active slot: {port or 'none'})")

        self.verify_candidate(new_sha)

        # From here the live tree is touched; the journal makes it recoverable.
        self.journal_write({"state": "promoting", "old_sha": old_sha, "new_sha": new_sha,
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        try:
            log("promote: updating the live tree")
            self.promote(new_sha, base=old_sha)
            if port:
                # `activating` is journalled (fsynced) BEFORE the restart, not
                # after: a kill between a successful restart and the journal
                # write would otherwise leave state=promoting over a serving
                # version, and reconcile would roll it back. From `activating`,
                # recovery re-runs restart+health and lets the outcome decide
                # direction. (Mirrors the DB apply machine's `switching`.)
                self.journal_write({"state": "activating", "old_sha": old_sha,
                                    "new_sha": new_sha,
                                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                log(f"restarting serve@{port}")
                self.restart_serving_slot(port)
            else:
                log("no active slot configured; code deployed without a restart")
        except (DeployError, OSError, subprocess.SubprocessError) as exc:
            # Catch OSError/subprocess too, not just DeployError: a disk or
            # fsync failure here must still trigger this run's rollback, not
            # fall through to main() with the live tree half-updated. Safe from
            # either promoting or activating: if the restart never made new
            # code healthy, rolling back to old is correct.
            if old_sha:
                try:
                    self.rollback(old_sha, new_sha, port)
                    self.journal_write({"state": "idle", "recovered_to": old_sha})
                except (DeployError, OSError, subprocess.SubprocessError) as rb:
                    # journal stays promoting/activating; reconcile retries on
                    # next push and the health checker pages meanwhile.
                    raise DeployError(f"ROLLBACK ALSO FAILED: {rb}") from rb
                raise DeployError(str(exc)) from exc
            raise

        # Point of no return: new code is materialized and (if there is a slot)
        # proven healthy. `serving` means a failure in the record below rolls
        # FORWARD next run, never back.
        self.journal_write({"state": "serving", "new_sha": new_sha,
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        self.commit_release(new_sha)
        log(f"deployed {new_sha}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: deploy_code.py <sha>", file=sys.stderr)
        return 2
    cfg = Config.from_env()
    deployer = CodeDeploy(cfg)
    try:
        deployer.deploy(sys.argv[1])
        return 0
    except (DeployError, OSError, subprocess.SubprocessError) as exc:
        # Catch OSError/subprocess too, not just DeployError: an fsync or
        # filesystem failure that escaped uncaught would skip the marker, and
        # since post-receive cannot fail the git push, the operator would get
        # no signal at all. The marker is the reliable feedback channel.
        print(f"[deploy] ✗✗✗ DEPLOY FAILED ✗✗✗ {exc}", file=sys.stderr, flush=True)
        print(
            "[deploy] NOTE: git push will still report success; "
            "post-receive cannot fail it. The health checker pages on this.",
            file=sys.stderr, flush=True,
        )
        try:
            cfg.failed_marker.write_text(
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {exc}\n"
            )
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
