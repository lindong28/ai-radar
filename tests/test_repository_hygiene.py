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

    # `git ls-files` only, and therefore SKIPPED where there is no .git -- including
    # `create-commit`'s export-tree check, which is the gap. A filesystem-walk fallback was built
    # for that and withdrawn: the most important .git-less tree in this project is the PRODUCTION
    # home (code arrives via `checkout-index -f -a` from a bare repo, deploy/sync/deploy_code.py),
    # and it has .env, data/ and logs/ by design -- exactly the three things this predicate
    # rejects. There the walk produced a red in which no clause was true: those paths are not
    # tracked, no commit was being refused, production was not stuck. Worse, it fires at the very
    # moment the docstring above describes (a refused deploy) and points at the wrong cause.
    # A pristine `git archive` export has no .venv and no `.deployed-sha`; whoever closes this
    # should gate the fallback on that rather than on ".git is absent".
    if not (root / ".git").exists():
        pytest.skip("no .git: cannot tell a pristine export from the production tree")
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    source = "git ls-files"
    assert tracked, f"{source} returned nothing; the check would pass vacuously"
    offenders = sorted(path for path in tracked if is_runtime_owned(path))
    assert offenders == [], (
        f"[{source}] these tracked paths are runtime-owned; the deploy will refuse the "
        f"commit and production will stay on the previous code: {offenders}"
    )
