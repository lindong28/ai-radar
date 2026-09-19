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
              "data/radar-8000.db", "data/anything.db",
              # H1 (2026-09-19 review): the roots THEMSELVES. `data` tracked
              # as a file/symlink makes checkout-index -f remove the whole
              # live directory; the prefix test alone said False here.
              "data", "logs", ".venv",
              # release identity written by the deployer, not by commits
              ".deployed-sha", ".git-deploy-index"):
        assert owned(p), f"{p} must be runtime-owned"
    for p in ("data/sources.toml", "data/aihot_retirements.json",
              "data/wechat-discovery.toml", ".env.example", ".python-version",
              "src/airadar/db.py", "deploy/sync/deploy_code.py",
              "database", "datafile.txt", "logstash/x", ".venvrc"):
        assert not owned(p), f"{p} must NOT be runtime-owned"


def test_symlink_target_escape_classification() -> None:
    escapes = dc.CodeDeploy._symlink_target_escapes
    assert escapes("data", "/etc")
    assert escapes("x", "../outside")
    assert escapes("a/b/link", "../../../etc/passwd")
    assert escapes("link", "")
    assert not escapes("a/b/link", "../../inside")
    assert not escapes("link", "src/airadar")
    assert not escapes("a/link", "../b")


def _seed_live_runtime(home: Path) -> dict[Path, bytes]:
    """Live state that a runtime-root takeover would destroy; returns the
    expected bytes so a test can prove it survived untouched."""
    files = {
        home / "data" / "radar-8000.db": b"slot 8000 database",
        home / "data" / "nginx" / "ai-radar-active-upstream.conf": b"upstream x {}",
        home / ".venv" / "marker": b"venv",
        home / "logs" / "serve.log": b"log",
    }
    for path, payload in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return files


def _src_repo(tmp_path: Path) -> tuple[Path, str]:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "keep.txt").write_text("v\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c1")
    return src, _git(src, "rev-parse", "HEAD")


def _bare_and_deployer(tmp_path: Path, src: Path) -> tuple[Path, dc.CodeDeploy]:
    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    return home, dc.CodeDeploy(_make_cfg(tmp_path, bare, home))


def _assert_untouched(files: dict[Path, bytes]) -> None:
    for path, payload in files.items():
        assert not path.is_symlink(), f"{path} was replaced by a symlink"
        assert path.read_bytes() == payload, f"{path} was clobbered"


@pytest.mark.parametrize(
    ("label", "make_offender"),
    [
        ("regular file at data", lambda src: (src / "data").write_text("not a dir\n")),
        ("symlink data -> /etc", lambda src: (src / "data").symlink_to("/etc")),
        ("symlink .venv -> /tmp", lambda src: (src / ".venv").symlink_to("/tmp")),
        ("symlink logs -> /private/tmp", lambda src: (src / "logs").symlink_to("/private/tmp")),
    ],
)
def test_materialize_refuses_runtime_root_takeover(tmp_path, label, make_offender) -> None:
    """H1 regression (the reviewer's bare-repo reproduction, 2026-09-19): a
    commit tracking `data` / `.venv` / `logs` themselves made materialize
    succeed and delete the live directories (two slot databases included).
    Must be REFUSED before any live write."""
    src, c1 = _src_repo(tmp_path)
    make_offender(src)
    _git(src, "add", "-f", "-A")
    _git(src, "commit", "-qm", label)
    c2 = _git(src, "rev-parse", "HEAD")
    home, d = _bare_and_deployer(tmp_path, src)
    live = _seed_live_runtime(home)
    idx = home / ".git-deploy-index"

    d.materialize(c1, home, idx, base=None)
    _assert_untouched(live)
    with pytest.raises(dc.DeployError, match="refusing to deploy"):
        d.materialize(c2, home, idx, base=c1)
    _assert_untouched(live)
    assert (home / "data").is_dir() and not (home / "data").is_symlink()
    assert (home / ".venv").is_dir() and not (home / ".venv").is_symlink()


def test_materialize_refuses_gitlink_entry(tmp_path) -> None:
    """A gitlink (mode 160000) at `data` -- checkout would turn the live
    directory into an empty submodule mount. Refused."""
    src, c1 = _src_repo(tmp_path)
    _git(src, "update-index", "--add", "--cacheinfo", f"160000,{c1},data")
    _git(src, "commit", "-qm", "gitlink at data")
    c2 = _git(src, "rev-parse", "HEAD")
    assert "160000 commit" in _git(src, "ls-tree", c2)
    _git(src, "update-index", "--force-remove", "data")
    _git(src, "update-index", "--add", "--cacheinfo", f"160000,{c1},vendor/lib")
    _git(src, "commit", "-qm", "gitlink elsewhere")
    c3 = _git(src, "rev-parse", "HEAD")
    home, d = _bare_and_deployer(tmp_path, src)
    live = _seed_live_runtime(home)
    idx = home / ".git-deploy-index"

    d.materialize(c1, home, idx, base=None)
    with pytest.raises(dc.DeployError, match="runtime-owned"):
        d.materialize(c2, home, idx, base=c1)
    # Not on a runtime root, still refused: checkout-index cannot materialize
    # a submodule, so the tree could never be a complete release.
    with pytest.raises(dc.DeployError, match="submodule"):
        d.materialize(c3, home, idx, base=c1)
    assert not (home / "vendor").exists()
    _assert_untouched(live)


def test_materialize_refuses_symlink_escaping_repo_root(tmp_path) -> None:
    """A tracked symlink anywhere whose target resolves outside the repo (or
    onto a runtime-owned path) is refused, even though its own path is not
    runtime-owned."""
    src, c1 = _src_repo(tmp_path)
    (src / "src").mkdir()
    (src / "src" / "evil").symlink_to("../../etc")
    _git(src, "add", "-f", "-A")
    _git(src, "commit", "-qm", "escaping symlink")
    c2 = _git(src, "rev-parse", "HEAD")
    (src / "src" / "evil").unlink()
    (src / "src" / "evil").symlink_to("../data/radar-8000.db")
    _git(src, "add", "-f", "-A")
    _git(src, "commit", "-qm", "symlink into runtime data")
    c3 = _git(src, "rev-parse", "HEAD")
    (src / "src" / "evil").unlink()
    (src / "src" / "evil").symlink_to("../keep.txt")
    _git(src, "add", "-f", "-A")
    _git(src, "commit", "-qm", "harmless in-tree symlink")
    c4 = _git(src, "rev-parse", "HEAD")
    home, d = _bare_and_deployer(tmp_path, src)
    live = _seed_live_runtime(home)
    idx = home / ".git-deploy-index"

    d.materialize(c1, home, idx, base=None)
    with pytest.raises(dc.DeployError, match="must stay inside the repo"):
        d.materialize(c2, home, idx, base=c1)
    with pytest.raises(dc.DeployError, match="must stay inside the repo"):
        d.materialize(c3, home, idx, base=c1)
    assert not (home / "src" / "evil").exists()
    _assert_untouched(live)
    d.materialize(c4, home, idx, base=c1)  # in-tree symlink is fine
    assert (home / "src" / "evil").is_symlink()
    assert (home / "src" / "evil").read_text() == "v\n"


def test_materialize_validates_tree_before_deleting_live_files(tmp_path) -> None:
    """L4: a tree git itself rejects (here: an entry named `.git`) must fail
    at read-tree BEFORE _apply_deletions removes anything from the live tree.
    Before the reorder, src/a.py was already gone when read-tree refused."""
    src, _ = _src_repo(tmp_path)
    (src / "src").mkdir()
    (src / "src" / "a.py").write_text("print(1)\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "c2")
    c2 = _git(src, "rev-parse", "HEAD")
    home, d = _bare_and_deployer(tmp_path, src)
    bare = d.cfg.bare
    idx = home / ".git-deploy-index"
    d.materialize(c2, home, idx, base=None)
    assert (home / "src" / "a.py").exists()

    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=bare, input="x\n",
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    bad_tree = subprocess.run(
        ["git", "mktree"], cwd=bare, input=f"100644 blob {blob}\t.git\n",
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    with pytest.raises(dc.DeployError, match="could not read tree"):
        d.materialize(bad_tree, home, idx, base=c2)
    assert (home / "src" / "a.py").read_text() == "print(1)\n", (
        "live file deleted before the target tree was validated"
    )
    assert (home / "keep.txt").exists()


def test_read_tree_precedes_deletions_in_live_update(deployer) -> None:
    d, r = deployer
    seed_deployed(d, "oldsha")
    d.deploy("newsha")
    read_tree = next(
        i for i, c in enumerate(r.calls)
        if c.startswith("git -c core.bare=false read-tree newsha") and f"[tree={d.cfg.home}]" in c
    )
    deletions = next(
        i for i, c in enumerate(r.calls)
        if "--diff-filter=D -z oldsha newsha" in c and f"[tree={d.cfg.home}]" in c
    )
    assert read_tree < deletions, "target tree must be validated before live deletions"


# --- schema gate: table names from the database are interpolated into
# `PRAGMA table_info(...)`; anything outside the identifier alphabet fails the
# gate rather than being inspected (L3). ---

_sg_spec = importlib.util.spec_from_file_location(
    "schema_gate", REPO_ROOT / "deploy" / "sync" / "schema_gate.py"
)
sg = importlib.util.module_from_spec(_sg_spec)
_sg_spec.loader.exec_module(sg)


def test_schema_gate_refuses_non_identifier_table_names() -> None:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE items(id INTEGER, title TEXT)")
    assert sg._tables_and_columns(conn) == {"items": {"id", "title"}}
    conn.execute('CREATE TABLE "weird name; --"(x)')
    with pytest.raises(sg.SchemaGateError, match="unexpected name"):
        sg._tables_and_columns(conn)


# --- pre-receive: real git, real ssh signatures. The bare repo's hook must
# refuse an unsigned tip, accept a tip signed by an allowed key, and refuse a
# non-fast-forward / non-main ref. ---

PRE_RECEIVE = REPO_ROOT / "deploy" / "server" / "pre-receive"


def _isolated_git_env(tmp_path: Path) -> dict[str, str]:
    """No user/system git config: the developer's own signing setup must not
    leak into what the hook is being tested against."""
    import os
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("")
    return {
        **os.environ,
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_SSH_COMMAND": "false",  # nothing here may reach the network
    }


def _run(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def test_pre_receive_requires_allowed_signature_and_fast_forward(tmp_path) -> None:
    import shutil
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen not available")
    env = _isolated_git_env(tmp_path)

    key = tmp_path / "signing-key"
    if _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], tmp_path, env).returncode:
        pytest.skip("ssh-keygen cannot generate an ed25519 key here")
    keytype, keydata = (key.with_suffix(".pub")).read_text().split()[:2]
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f'deployer namespaces="git" {keytype} {keydata}\n')

    bare = tmp_path / "bare.git"
    assert _run(["git", "init", "-q", "--bare", str(bare)], tmp_path, env).returncode == 0
    hook = bare / "hooks" / "pre-receive"
    shutil.copyfile(PRE_RECEIVE, hook)
    hook.chmod(0o755)
    for k, v in (
        ("ai-radar.allowedSignersFile", str(allowed)),
        ("receive.denyNonFastForwards", "true"),
        ("receive.denyDeletes", "true"),
        ("receive.fsckObjects", "true"),
    ):
        assert _run(["git", "config", k, v], bare, env).returncode == 0

    work = tmp_path / "work"
    assert _run(["git", "init", "-q", "-b", "main", str(work)], tmp_path, env).returncode == 0
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        _run(["git", "config", k, v], work, env)
    (work / "a.txt").write_text("1\n")
    _run(["git", "add", "a.txt"], work, env)
    assert _run(["git", "commit", "-qm", "unsigned"], work, env).returncode == 0

    # 1. unsigned tip -> refused
    push = _run(["git", "push", str(bare), "main"], work, env)
    assert push.returncode != 0, push.stderr
    assert "not signed by a key" in push.stderr, push.stderr
    assert _run(["git", "rev-parse", "--verify", "-q", "refs/heads/main"], bare, env).returncode != 0

    # 2. signed by the allowed key -> accepted
    sign = ["-c", "gpg.format=ssh", "-c", f"user.signingkey={key}"]
    signed = _run(["git", *sign, "commit", "-q", "-S", "--amend", "--no-edit"], work, env)
    if signed.returncode != 0 and "ssh-keygen" in signed.stderr:
        pytest.skip(f"ssh signing unsupported here: {signed.stderr.strip()[-200:]}")
    assert signed.returncode == 0, signed.stderr
    push = _run(["git", "push", str(bare), "main"], work, env)
    assert push.returncode == 0, push.stderr
    tip = _run(["git", "rev-parse", "main"], work, env).stdout.strip()
    assert _run(["git", "rev-parse", "refs/heads/main"], bare, env).stdout.strip() == tip

    # 3. signed but not a fast-forward -> refused by the hook (it runs before
    #    receive.denyNonFastForwards, so the message is the hook's)
    _run(["git", "reset", "-q", "--hard", "HEAD~0"], work, env)
    _run(["git", "checkout", "-q", "--orphan", "rewrite"], work, env)
    (work / "a.txt").write_text("rewritten\n")
    _run(["git", "add", "a.txt"], work, env)
    assert _run(["git", *sign, "commit", "-q", "-S", "-m", "rewrite"], work, env).returncode == 0
    push = _run(["git", "push", "--force", str(bare), "rewrite:main"], work, env)
    assert push.returncode != 0
    assert "not a fast-forward" in push.stderr, push.stderr
    assert _run(["git", "rev-parse", "refs/heads/main"], bare, env).stdout.strip() == tip

    # 4. a signed tip on any other ref -> refused; main only
    push = _run(["git", "push", str(bare), "rewrite:refs/heads/feature"], work, env)
    assert push.returncode != 0
    assert "refs/heads/main only" in push.stderr, push.stderr

    # 5. deleting main -> refused
    push = _run(["git", "push", str(bare), ":refs/heads/main"], work, env)
    assert push.returncode != 0
    assert "deleting refs/heads/main" in push.stderr, push.stderr
    assert _run(["git", "rev-parse", "refs/heads/main"], bare, env).stdout.strip() == tip

    # 6. allowed_signers missing -> fail closed even for a signed fast-forward
    _run(["git", "checkout", "-q", "main"], work, env)
    (work / "b.txt").write_text("2\n")
    _run(["git", "add", "b.txt"], work, env)
    assert _run(["git", *sign, "commit", "-q", "-S", "-m", "second"], work, env).returncode == 0
    allowed.rename(allowed.with_suffix(".moved"))
    push = _run(["git", "push", str(bare), "main"], work, env)
    assert push.returncode != 0
    assert "allowed signers file" in push.stderr, push.stderr
    allowed.with_suffix(".moved").rename(allowed)
    assert _run(["git", "push", str(bare), "main"], work, env).returncode == 0


def test_materialize_accepts_versioned_data_configs(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "keep.txt").write_text("v\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "base")
    base = _git(src, "rev-parse", "HEAD")
    (src / "data").mkdir()
    (src / "data" / "aihot_retirements.json").write_text("{}\n")
    (src / "data" / "wechat-discovery.toml").write_text("version = 3\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "add versioned configs")
    target = _git(src, "rev-parse", "HEAD")

    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    d = dc.CodeDeploy(_make_cfg(tmp_path, bare, home))
    idx = home / ".git-deploy-index"

    d.materialize(base, home, idx, base=None)
    d.materialize(target, home, idx, base=base)
    assert (home / "data" / "aihot_retirements.json").read_text() == "{}\n"
    assert (home / "data" / "wechat-discovery.toml").read_text() == "version = 3\n"


def test_materialize_refuses_allowlisted_config_symlink(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "data").mkdir()
    (src / "data" / "wechat-discovery.toml").symlink_to("../runtime-secret")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "symlink config")
    target = _git(src, "rev-parse", "HEAD")

    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    d = dc.CodeDeploy(_make_cfg(tmp_path, bare, home))

    with pytest.raises(dc.DeployError, match="regular tracked files"):
        d.materialize(target, home, home / ".git-deploy-index", base=None)


def test_first_materialize_refuses_preexisting_allowlisted_config(tmp_path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@example.com")
    _git(src, "config", "user.name", "t")
    (src / "data").mkdir()
    (src / "data" / "aihot_retirements.json").write_text("{}\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "tracked config")
    target = _git(src, "rev-parse", "HEAD")

    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(src), str(bare))
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    live_config = home / "data" / "aihot_retirements.json"
    live_config.write_text('{"runtime": true}\n')
    d = dc.CodeDeploy(_make_cfg(tmp_path, bare, home))

    with pytest.raises(dc.DeployError, match="pre-existing untracked config"):
        d.materialize(target, home, home / ".git-deploy-index", base=None)
    assert live_config.read_text() == '{"runtime": true}\n'


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
