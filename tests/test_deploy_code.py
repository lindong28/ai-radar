"""State matrix for the code-deploy transaction (post-receive's Python core).

Scenarios mirror the adversarial review findings against the two shell
versions; external commands go through a fake Runner, git effects included, so
the machine is exercised without a bare repo or systemd.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "deploy_code", REPO_ROOT / "deploy" / "sync" / "deploy_code.py"
)
dc = importlib.util.module_from_spec(spec)
sys.modules["deploy_code"] = dc
spec.loader.exec_module(dc)


class FakeRunner:
    """Simulates git/uv/systemctl/curl; failures injectable by substring."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.calls: list[str] = []
        self.failures: dict[str, int] = {}
        self.healthz_ok = True
        # Per-tree staged sha (set by `read-tree <sha>`, written out by
        # `checkout-index`), the deletion set a `diff --diff-filter=D` reports,
        # and the tree listing the runtime-path guard's `ls-tree` returns
        # (NUL-separated; empty means no offenders). Tests inject as needed.
        self._staged: dict[str, str] = {}
        self.deletions = ""
        self.tree_paths = ""

    class R:
        def __init__(self, rc: int) -> None:
            self.returncode = rc
            self.stdout = ""
            self.stderr = "injected failure" if rc else ""

    def run(self, *argv, env=None, cwd=None):
        joined = " ".join(str(a) for a in argv)
        # git's work-tree travels in env, not argv; append it so tests can
        # target the candidate call and the live call separately.
        if env and "GIT_WORK_TREE" in env:
            joined += f" [tree={env['GIT_WORK_TREE']}]"
        self.calls.append(joined)
        # py_compile is a candidate sanity check, not the thing most tests
        # target; let it pass unless a test explicitly injects "py_compile".
        if "py_compile" in joined and "py_compile" not in self.failures:
            return self.R(0)
        for needle, rc in self.failures.items():
            if needle in joined:
                return self.R(rc)
        if argv[0] == "curl":
            return self.R(0 if self.healthz_ok else 7)
        # The runtime-path guard lists the target tree; empty = no offenders.
        if "ls-tree" in joined:
            res = self.R(0)
            res.stdout = self.tree_paths
            return res
        # Model force-based materialization: `read-tree <sha>` stages the sha
        # for the tree in env; `checkout-index -f -a` writes it out as the
        # tree's marker. `diff --diff-filter=D base sha` reports deletions.
        if "read-tree" in joined:
            tree = Path(env["GIT_WORK_TREE"])
            tree.mkdir(parents=True, exist_ok=True)
            self._staged[str(tree)] = argv[-1]  # the sha to materialize
        if "checkout-index" in joined:
            tree = Path(env["GIT_WORK_TREE"])
            tree.mkdir(parents=True, exist_ok=True)
            (tree / ".materialized").write_text(self._staged.get(str(tree), ""))
            (tree / "src").mkdir(exist_ok=True)
        if "diff" in joined and "--diff-filter=D" in joined:
            res = self.R(0)
            res.stdout = self.deletions
            return res
        if "uv sync" in joined or (str(self.cfg.uv) in joined and "sync" in joined):
            base = cwd or self.cfg.home
            (Path(base) / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
            (Path(base) / ".venv" / "bin" / "python3").write_text("")
        return self.R(0)

    def ok(self, *argv, env=None, cwd=None) -> bool:
        return self.run(*argv, env=env, cwd=cwd).returncode == 0


@pytest.fixture
def deployer(tmp_path, monkeypatch):
    home = tmp_path / "live"
    (home / "data").mkdir(parents=True)
    conf = home / "data" / "nginx" / "active.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("upstream ai_radar_active { server 127.0.0.1:8000; }\n")
    slots = tmp_path / "slots"
    slots.mkdir()
    active_db = tmp_path / "radar-8000.db"
    active_db.write_bytes(b"db")
    (slots / "8000.env").write_text(f"AI_RADAR_DB={active_db}\n")

    cfg = dc.Config(
        home=home,
        bare=tmp_path / "bare.git",
        candidate_dir=tmp_path / "candidate",
        lock=home / "data" / ".deploy.lock",
        active_conf=conf,
        uv=tmp_path / "uv",
        journal=home / "data" / "code-deploy-journal.json",
        failed_marker=home / "data" / ".deploy-failed",
        slot_env_dir=slots,
        health_wait_s=1,
    )
    runner = FakeRunner(cfg)
    d = dc.CodeDeploy(cfg, runner)
    monkeypatch.setattr(dc.time, "sleep", lambda s: None)
    return d, runner


def journal(d) -> dict:
    return json.loads(d.cfg.journal.read_text())


def seed_deployed(d, sha: str) -> None:
    (d.cfg.home / ".deployed-sha").write_text(sha + "\n")


def test_happy_path_deploys_and_records(deployer) -> None:
    d, r = deployer
    seed_deployed(d, "oldsha")
    d.deploy("newsha")
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "newsha"
    assert journal(d)["state"] == "idle"
    assert not d.cfg.failed_marker.exists()


def test_second_deploy_propagates_deletions_from_prev_release(deployer) -> None:
    """Each update computes the deletion set against the PREVIOUS deployed sha,
    so files the new commit removed are deleted from the live tree."""
    d, r = deployer
    seed_deployed(d, "oldsha")
    d.deploy("midsha")
    d.deploy("newsha")
    joined = " ".join(r.calls)
    assert (
        f"diff --no-renames --name-only --diff-filter=D -z midsha newsha [tree={d.cfg.home}]" in joined
    ), "live update must compute deletions against the previous release"
    assert (d.cfg.home / ".materialized").read_text() == "newsha"


def test_candidate_failure_leaves_live_untouched(deployer) -> None:
    d, r = deployer
    seed_deployed(d, "oldsha")
    r.failures["sync"] = 1  # candidate uv sync fails
    with pytest.raises(dc.DeployError, match="uv sync"):
        d.deploy("newsha")
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "oldsha"
    assert not (d.cfg.home / ".materialized").exists(), "live tree must not be touched"


def test_schema_gate_blocks_incompatible_code(deployer) -> None:
    d, r = deployer
    seed_deployed(d, "oldsha")
    r.failures["schema_gate.py"] = 1  # the gate script rejects
    with pytest.raises(dc.DeployError, match="does not match the active database"):
        d.deploy("newsha")
    assert not (d.cfg.home / ".materialized").exists()


def test_missing_slot_db_refuses_to_skip_the_gate(deployer) -> None:
    d, _ = deployer
    seed_deployed(d, "oldsha")
    (d.cfg.slot_env_dir / "8000.env").unlink()
    with pytest.raises(dc.DeployError, match="refusing to skip the schema gate"):
        d.deploy("newsha")


def test_promote_failure_rolls_back_and_restores_service(deployer) -> None:
    d, r = deployer
    seed_deployed(d, "oldsha")
    # Fail only the LIVE materialization onto newsha; candidate (a different
    # work-tree) must succeed or the run never reaches promote.
    r.failures[f"read-tree newsha [tree={d.cfg.home}]"] = 1
    with pytest.raises(dc.DeployError):
        d.deploy("newsha")
    joined = " ".join(r.calls)
    assert f"read-tree oldsha [tree={d.cfg.home}]" in joined, "rollback must materialize old"
    assert (d.cfg.home / ".materialized").read_text() == "oldsha"
    assert journal(d)["state"] == "idle"
    assert journal(d)["recovered_to"] == "oldsha"
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "oldsha"


def test_health_failure_rolls_back(deployer) -> None:
    d, r = deployer
    seed_deployed(d, "oldsha")
    calls = {"n": 0}

    real_run = r.run

    def run_with_flaky_health(*argv, env=None, cwd=None):
        if argv[0] == "curl":
            calls["n"] += 1
            # New release never healthy; rollback's health check succeeds.
            joined_all = " ".join(r.calls)
            healthy = f"read-tree oldsha [tree={d.cfg.home}]" in joined_all
            return FakeRunner.R(0 if healthy else 7)
        return real_run(*argv, env=env, cwd=cwd)

    r.run = run_with_flaky_health
    r.ok = lambda *a, **k: r.run(*a, **k).returncode == 0
    with pytest.raises(dc.DeployError):
        d.deploy("newsha")
    assert journal(d)["recovered_to"] == "oldsha"


def test_interrupted_promote_is_recovered_on_next_deploy(deployer) -> None:
    """SIGKILL/power loss between journal(promoting) and completion."""
    d, r = deployer
    seed_deployed(d, "oldsha")
    d.journal_write({"state": "promoting", "old_sha": "oldsha", "new_sha": "deadsha"})
    d.deploy("newsha")
    joined = " ".join(r.calls)
    assert (
        f"diff --no-renames --name-only --diff-filter=D -z deadsha oldsha [tree={d.cfg.home}]" in joined
    ), "reconcile must roll back toward old, computing deletions from the dead sha"
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "newsha"


def test_corrupt_journal_stops_loudly(deployer) -> None:
    d, _ = deployer
    d.cfg.journal.write_text("not json")
    with pytest.raises(dc.DeployError, match="refusing to guess"):
        d.deploy("newsha")


def test_failed_rollback_keeps_promoting_for_retry(deployer) -> None:
    d, r = deployer
    seed_deployed(d, "oldsha")
    r.failures[f"read-tree newsha [tree={d.cfg.home}]"] = 1
    r.failures[f"read-tree oldsha [tree={d.cfg.home}]"] = 1  # rollback fails too
    with pytest.raises(dc.DeployError, match="ROLLBACK ALSO FAILED"):
        d.deploy("newsha")
    assert journal(d)["state"] == "promoting", "journal must keep the debt visible"


def test_bootstrap_without_active_slot_deploys_without_restart(deployer) -> None:
    d, r = deployer
    d.cfg.active_conf.unlink()
    d.deploy("newsha")
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "newsha"
    assert not any("systemctl restart" in c for c in r.calls)


def test_broken_next_deployer_is_caught_in_candidate(deployer) -> None:
    """A commit that breaks the deploy control plane must fail in candidate,
    not silently deploy now and break the next push."""
    d, r = deployer
    seed_deployed(d, "oldsha")
    r.failures["py_compile"] = 1
    with pytest.raises(dc.DeployError, match="does not compile"):
        d.deploy("newsha")
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "oldsha"


def test_unreadable_active_include_fails_closed(deployer) -> None:
    """Present-but-unreadable include must NOT look like a fresh install.

    Path.exists()/os.path.exists() would swallow the PermissionError and
    return False, routing a live host into the bootstrap path -- skipping the
    gate and restart while stamping a new SHA over old serving code.
    """
    d, _ = deployer
    seed_deployed(d, "oldsha")

    real_read_text = Path.read_text

    def denied(self, *a, **k):
        if self == d.cfg.active_conf:
            raise PermissionError("denied")
        return real_read_text(self, *a, **k)

    import unittest.mock
    with unittest.mock.patch.object(Path, "read_text", denied):
        with pytest.raises(dc.DeployError, match="unreadable"):
            d.deploy("newsha")


def test_oserror_mid_promote_triggers_rollback(deployer) -> None:
    """A non-DeployError raised during promote must still roll back this run."""
    d, r = deployer
    seed_deployed(d, "oldsha")
    real_run = r.run
    state = {"tripped": False}

    def run_raising_once(*argv, env=None, cwd=None):
        joined = " ".join(str(a) for a in argv)
        # Fail the live materialization onto newsha with an OSError, once.
        if (not state["tripped"] and "read-tree" in joined and argv[-1] == "newsha"
                and env and str(d.cfg.home) == env.get("GIT_WORK_TREE")):
            state["tripped"] = True
            raise OSError("disk gone")
        return real_run(*argv, env=env, cwd=cwd)

    r.run = run_raising_once
    r.ok = lambda *a, **k: (lambda rc: rc == 0)(
        (lambda res: res.returncode)(r.run(*a, **k))
    )
    with pytest.raises(dc.DeployError):
        d.deploy("newsha")
    joined = " ".join(r.calls)
    assert f"read-tree oldsha [tree={d.cfg.home}]" in joined, "OSError must still roll back"
    assert journal(d)["recovered_to"] == "oldsha"


def test_interrupted_serving_rolls_forward_not_back(deployer) -> None:
    """Killed after the new code is serving but before the record is written.

    The new code is live and healthy; recovery must FINISH the record (roll
    forward), never roll back a serving version. This is the code-deploy
    analogue of the DB apply machine's switched->committed rule.
    """
    d, r = deployer
    seed_deployed(d, "oldsha")
    d.journal_write({"state": "serving", "new_sha": "newsha"})
    d.reconcile()
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "newsha"
    assert journal(d)["state"] == "idle"
    joined = " ".join(r.calls)
    assert f"read-tree oldsha [tree={d.cfg.home}]" not in joined, "must NOT roll back a serving release"


def test_interrupted_activation_healthy_rolls_forward(deployer) -> None:
    """Killed mid-restart, new code turns out healthy -> forward to serving."""
    d, r = deployer
    seed_deployed(d, "oldsha")
    d.journal_write({"state": "activating", "old_sha": "oldsha", "new_sha": "newsha"})
    d.reconcile()  # FakeRunner healthz_ok defaults True -> new code is healthy
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "newsha"
    assert journal(d)["state"] == "idle"
    joined = " ".join(r.calls)
    assert f"read-tree oldsha [tree={d.cfg.home}]" not in joined, "healthy activation must not roll back"


def test_interrupted_activation_unhealthy_rolls_back(deployer) -> None:
    """Killed mid-restart, new code will not become healthy -> roll back."""
    d, r = deployer
    seed_deployed(d, "oldsha")
    d.journal_write({"state": "activating", "old_sha": "oldsha", "new_sha": "newsha"})
    r.healthz_ok = False  # new slot never healthy; rollback's own restart also uses this...
    # ...so make health depend on which sha is materialized, like the earlier test.
    real_run = r.run

    def run_health_by_tree(*argv, env=None, cwd=None):
        if argv[0] == "curl":
            healthy = f"read-tree oldsha [tree={d.cfg.home}]" in " ".join(r.calls)
            return FakeRunner.R(0 if healthy else 7)
        return real_run(*argv, env=env, cwd=cwd)

    r.run = run_health_by_tree
    r.ok = lambda *a, **k: r.run(*a, **k).returncode == 0
    d.reconcile()
    assert journal(d)["recovered_to"] == "oldsha"
    assert (d.cfg.home / ".deployed-sha").read_text().strip() == "oldsha"


# --- Real git, no fake Runner. The FakeRunner cannot model git's actual
# "entry not uptodate" refusal, which is exactly the failure that shipped: a
# stat-less base index made read-tree -m -u reject the first deploy that had to
# OVERWRITE (not just add) a tracked file. These exercise materialize() against
# a real bare repo so that regression is caught here, not on the server. ---


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_materialize_real_git_modify_delete_and_rollback(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "keep.txt").write_text("v1\n")
    (src / "gone.txt").write_text("bye\n")
    (src / "pkg").mkdir()
    (src / "pkg" / "mod.py").write_text("A\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c1")
    c1 = _git(src, "rev-parse", "HEAD")
    (src / "pkg" / "mod.py").write_text("B\n")  # MODIFY -- the shipped-bug case
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c2")
    c2 = _git(src, "rev-parse", "HEAD")
    (src / "gone.txt").unlink()  # DELETE
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c3")
    c3 = _git(src, "rev-parse", "HEAD")

    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))

    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    cfg = dc.Config(
        home=home,
        bare=bare,
        candidate_dir=tmp_path / "cand",
        lock=home / "data" / ".lock",
        active_conf=home / "data" / "a.conf",
        uv=tmp_path / "uv",
        journal=home / "data" / "j.json",
        failed_marker=home / "data" / ".f",
        slot_env_dir=tmp_path / "slots",
    )
    d = dc.CodeDeploy(cfg)  # REAL Runner -- real git
    idx = home / ".git-deploy-index"

    # Bootstrap (base=None): lay down c1.
    d.materialize(c1, home, idx, base=None)
    assert (home / "keep.txt").read_text() == "v1\n"
    assert (home / "gone.txt").exists()
    assert (home / "pkg" / "mod.py").read_text() == "A\n"

    # Untracked file must survive every subsequent materialization.
    (home / "data" / "radar.db").write_bytes(b"DB")

    # MODIFY c1->c2: read-tree -m -u refused this ("entry not uptodate"); the
    # force-based materialize must apply it.
    d.materialize(c2, home, idx, base=c1)
    assert (home / "pkg" / "mod.py").read_text() == "B\n", "overwrite must apply"
    assert (home / "data" / "radar.db").read_bytes() == b"DB", "untracked preserved"

    # DELETE c2->c3: gone.txt removed in c3 must disappear from the live tree.
    d.materialize(c3, home, idx, base=c2)
    assert not (home / "gone.txt").exists(), "removed file must be deleted"
    assert (home / "pkg" / "mod.py").read_text() == "B\n"
    assert (home / "data" / "radar.db").read_bytes() == b"DB"

    # ROLLBACK with the worktree NOT at base: worktree is at c3, roll back to
    # c1 using base=c3 (the from-sha rollback passes). This is the exact shape
    # that produced "ROLLBACK ALSO FAILED" -- it must now succeed, restoring the
    # file c3 had dropped and reverting the modification.
    d.materialize(c1, home, idx, base=c3)
    assert (home / "pkg" / "mod.py").read_text() == "A\n", "rollback reverts modification"
    assert (home / "gone.txt").read_text() == "bye\n", "rollback restores dropped file"
    assert (home / "data" / "radar.db").read_bytes() == b"DB"

    # The bare repo's HEAD must never move.
    assert _git(bare, "symbolic-ref", "HEAD")  # still a valid symref


def _make_cfg(tmp_path, bare, home):
    return dc.Config(
        home=home, bare=bare, candidate_dir=tmp_path / "cand",
        lock=home / "data" / ".lock", active_conf=home / "data" / "a.conf",
        uv=tmp_path / "uv", journal=home / "data" / "j.json",
        failed_marker=home / "data" / ".f", slot_env_dir=tmp_path / "slots",
    )


def test_materialize_real_git_rename_and_file_dir_typechange(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "old.py").write_text("x\n")
    (src / "thing").write_text("file\n")  # a tracked FILE named 'thing'
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c1")
    c1 = _git(src, "rev-parse", "HEAD")
    # c2: rename old.py -> new.py, AND turn 'thing' from a file into a directory.
    (src / "old.py").rename(src / "new.py")
    (src / "thing").unlink()
    (src / "thing").mkdir()
    (src / "thing" / "inner").write_text("dir\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c2")
    c2 = _git(src, "rev-parse", "HEAD")

    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    d = dc.CodeDeploy(_make_cfg(tmp_path, bare, home))
    idx = home / ".git-deploy-index"

    d.materialize(c1, home, idx, base=None)
    assert (home / "old.py").exists()
    assert (home / "thing").is_file()

    # Rename must not orphan old.py; file->directory conversion must succeed.
    d.materialize(c2, home, idx, base=c1)
    assert not (home / "old.py").exists(), "renamed-away path must not be orphaned"
    assert (home / "new.py").read_text() == "x\n"
    assert (home / "thing").is_dir(), "file must convert to directory"
    assert (home / "thing" / "inner").read_text() == "dir\n"

    # Reverse (directory -> file), as a rollback whose worktree sits at c2.
    d.materialize(c1, home, idx, base=c2)
    assert (home / "thing").is_file() and (home / "thing").read_text() == "file\n"
    assert (home / "old.py").exists()
    assert not (home / "new.py").exists()


def test_materialize_refuses_commit_that_tracks_a_runtime_path(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "keep.txt").write_text("v\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c1")
    c1 = _git(src, "rev-parse", "HEAD")
    # A commit that force-adds a runtime-owned path (the real secrets file).
    (src / ".env").write_text("SECRET=1\n")
    _git(src, "add", "-f", ".env")
    _git(src, "commit", "-qm", "mistakenly track .env")
    c2 = _git(src, "rev-parse", "HEAD")

    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / ".env").write_text("LIVE_SECRET=keepme\n")  # live untracked secrets
    d = dc.CodeDeploy(_make_cfg(tmp_path, bare, home))
    idx = home / ".git-deploy-index"

    d.materialize(c1, home, idx, base=None)  # no runtime path -> fine
    with pytest.raises(dc.DeployError, match="runtime-owned"):
        d.materialize(c2, home, idx, base=c1)
    assert (home / ".env").read_text() == "LIVE_SECRET=keepme\n", "live secret untouched"


def test_runtime_owned_classification() -> None:
    owned = dc.CodeDeploy._is_runtime_owned
    for p in (".env", ".venv/bin/python3", "logs/serve.log",
              "data/radar-8000.db", "data/anything.db"):
        assert owned(p), f"{p} must be runtime-owned"
    for p in ("data/sources.toml", ".env.example", ".python-version",
              "src/airadar/db.py", "deploy/sync/deploy_code.py"):
        assert not owned(p), f"{p} must NOT be runtime-owned"


def test_materialize_real_git_deletes_path_with_space(tmp_path) -> None:
    """A removed path containing a space must be deleted exactly (-z/NUL),
    not mangled by whitespace handling."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "a b.txt").write_text("space\n")
    (src / "keep.txt").write_text("k\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c1")
    c1 = _git(src, "rev-parse", "HEAD")
    (src / "a b.txt").unlink()
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c2")
    c2 = _git(src, "rev-parse", "HEAD")

    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    d = dc.CodeDeploy(_make_cfg(tmp_path, bare, home))
    idx = home / ".git-deploy-index"

    d.materialize(c1, home, idx, base=None)
    assert (home / "a b.txt").exists()
    d.materialize(c2, home, idx, base=c1)
    assert not (home / "a b.txt").exists(), "spaced path must be deleted exactly"
    assert (home / "keep.txt").exists()
