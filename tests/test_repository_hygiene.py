from __future__ import annotations

import os
import subprocess
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
