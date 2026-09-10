from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_execution_plan_workspace_is_ignored_and_untracked() -> None:
    root = Path(__file__).resolve().parents[1]
    ignore_rules = (root / ".gitignore").read_text().splitlines()
    plan_rule_index = max(
        (index for index, rule in enumerate(ignore_rules) if rule.strip() == "/plans/"),
        default=-1,
    )
    assert plan_rule_index >= 0
    later_rules = (
        rule.strip()
        for rule in ignore_rules[plan_rule_index + 1 :]
        if rule.strip() and not rule.lstrip().startswith("#")
    )
    assert list(later_rules) == []

    if not (root / ".git").exists():
        pytest.skip("repository tracking contract requires a Git checkout")

    git_env = os.environ.copy()
    for variable in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        git_env.pop(variable, None)

    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", "plans/.lifecycle-probe"],
        cwd=root,
        env=git_env,
        check=False,
    )
    assert ignored.returncode == 0

    tracked = subprocess.run(
        ["git", "ls-files", "--", "plans"],
        cwd=root,
        env=git_env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []


def test_no_tracked_file_is_runtime_owned_on_the_deploy_server() -> None:
    """The production deploy refuses any commit that tracks a runtime-owned path, and nothing
    in this repo checked that against the REAL tree until it fired.

    **Two of this test's own branches are not held by any in-suite mutation**, and saying so
    matters more than the pass count: reverting the export gate to "no .git" or dropping `.venv`
    from the walk both leave the suite green, because neither manifests in a tree that has
    `.git`. Their verification is a four-shape matrix, run by hand 2026-09-10:
    pristine `git archive` export -> runs, passes (this is the gap being closed);
    production-shaped tree (`.env` + `.deployed-sha`) -> skips (no false red);
    copied dirty worktree (`.env` only) -> skips;
    working tree (`.git`) -> runs, passes;
    pristine export with a planted `data/` file -> red (positive control).

    Measured 2026-09-10: commit `09dea35` added `data/eval-fit/labels/page-categories.jsonl`
    behind a `!data/eval-fit/labels/` exception in `.gitignore`. Everything local was green --
    ruff, mypy, 2790 tests, the export-tree execution check, `git check-ignore` in both
    directions. The push succeeded and the deploy then refused the commit, leaving production on
    the previous code with the health checker paging. `checkout-index -f` would have clobbered
    live state git cannot restore, so refusing is correct; the gap was that the refusal only
    existed on the server.

    `test_runtime_owned_classification` covers the predicate and
    `test_materialize_refuses_runtime_owned_paths` covers a synthetic commit. Neither reads this
    repository's own tracked file list, which is the thing that was wrong.
    """
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "deploy" / "sync"))
    import deploy_code

    is_runtime_owned = deploy_code.CodeDeploy._is_runtime_owned
    # Positive control: the predicate must actually reject something, or an empty offender list
    # below would mean "the predicate is broken" rather than "the tree is clean".
    assert is_runtime_owned("data/radar.db")
    assert not is_runtime_owned("data/sources.toml")

    # Three sources, because "no .git" alone does NOT mean "pristine export". The most
    # important .git-less tree in this project is the PRODUCTION home: code arrives there via
    # `checkout-index -f -a` from a bare repo (deploy/sync/deploy_code.py), and it has .env,
    # data/ and logs/ by design -- exactly the three things this predicate rejects. A fallback
    # keyed on ".git is absent" therefore produced, on that tree, a red in which no clause was
    # true (those paths are not tracked, no commit was being refused, production was not stuck),
    # and it fired at the very moment the docstring above describes. Measured.
    #
    # A pristine `git archive` export is distinguishable by what the production home has and it
    # does not: `.deployed-sha` (deploy_code.py:452 writes the release identity there) and `.env`
    # (the live secrets file, which `_is_runtime_owned` names explicitly). Requiring both absent
    # also excludes a copied dirty worktree, which carries `.env`.
    #
    # NOT gated on `.venv`, though a pristine export has none: `uv run` materialises it before
    # pytest starts, so by the time this line executes it exists in the export too. Measured --
    # the first version used it and the export silently SKIPPED, i.e. the fix read exactly like
    # the bug it was fixing. If neither source applies, skip rather than guess: a wrong red here
    # points at the wrong cause during an outage, which is worse than no check.
    if (root / ".git").exists():
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.split()
        source = "git ls-files"
    elif not (root / ".deployed-sha").exists() and not (root / ".env").exists():
        tracked = [
            str(path.relative_to(root))
            for path in root.rglob("*")
            # `.venv` is excluded from the WALK even though it is not the gate: `uv run`
            # creates it inside the export, and `_is_runtime_owned` rejects the `.venv/` prefix,
            # so leaving it in makes every export run red on the test runner's own artefacts.
            if path.is_file() and not {"__pycache__", ".venv"} & set(path.parts)
        ]
        source = "filesystem walk of a pristine export tree"
    else:
        pytest.skip("neither a git checkout nor a pristine export; cannot enumerate tracked files")
    assert tracked, f"{source} returned nothing; the check would pass vacuously"
    offenders = sorted(path for path in tracked if is_runtime_owned(path))
    assert offenders == [], (
        f"[{source}] these tracked paths are runtime-owned; the deploy will refuse the "
        f"commit and production will stay on the previous code: {offenders}"
    )
