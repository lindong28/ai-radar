"""Retention for data/eval-fit/runs, 14 days, chosen by the repository owner 2026-09-09."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from airadar.eval.aihot_fit.common import prune_old_runs


def _mk(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "payload.json").write_text("{}", encoding="utf-8")
    return d


def _stamp(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y%m%dT%H%M%SZ")


def test_prunes_only_runs_older_than_the_window(tmp_path: Path) -> None:
    old = _mk(tmp_path, f"{_stamp(30)}-deadbeef")
    fresh = _mk(tmp_path, f"{_stamp(3)}-cafef00d")
    pruned = prune_old_runs(tmp_path, retain_days=14)
    assert pruned == [old.name]
    assert not old.exists()
    assert fresh.exists()


def test_never_prunes_a_hand_named_run(tmp_path: Path) -> None:
    """Undatable by construction, so no window can justify deleting it."""
    named = _mk(tmp_path, "CAT-AB-A-baseline")
    _mk(tmp_path, f"{_stamp(99)}-deadbeef")
    prune_old_runs(tmp_path, retain_days=14)
    assert named.exists(), "hand-named comparison runs must survive retention"


def test_zero_disables_pruning(tmp_path: Path) -> None:
    ancient = _mk(tmp_path, f"{_stamp(999)}-deadbeef")
    assert prune_old_runs(tmp_path, retain_days=0) == []
    assert ancient.exists()


def test_missing_runs_dir_is_not_an_error(tmp_path: Path) -> None:
    assert prune_old_runs(tmp_path / "absent", retain_days=14) == []
