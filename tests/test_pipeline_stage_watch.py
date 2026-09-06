"""The stall detector, pinned on every way it has actually been wrong.

It exists so a batch job can see the externality it imposes on the scheduled pipeline. Two
drafts got it wrong in the direction that makes it useless -- reporting "nothing is stuck" while
the pipeline had been stuck for hours -- so the cases below are organised around those, not
around the happy path.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from pipeline_stage_watch import EXIT_OK, EXIT_STALLED, EXIT_UNKNOWN, inspect  # noqa: E402

_NOW = datetime(2026, 9, 6, 23, 20, 0)
WATCH = Path(__file__).resolve().parent.parent / "scripts" / "pipeline_stage_watch.py"


def _log(dir_: Path, name: str, body: str) -> None:
    (dir_ / name).write_text(body, encoding="utf-8")


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture
def held_lock(tmp_path: Path):
    """A pipeline holding the exclusive lock, as pipeline.sh does."""
    lock = tmp_path / ".pipeline.flock"
    handle = lock.open("a")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    yield lock
    handle.close()


@pytest.fixture
def free_lock(tmp_path: Path) -> Path:
    lock = tmp_path / ".pipeline.flock"
    lock.touch()
    return lock


def test_a_stall_hiding_behind_newer_skip_logs_is_still_found(logs: Path, held_lock: Path) -> None:
    # Every cron round that finds the lock held writes its own log and exits, so during a stall
    # the newest files hold no stages at all and the stalled run's log is several files back.
    # The stalled log is deliberately NOT the lexicographically smallest name here: an earlier
    # fixture made it both oldest and first, so reversing the scan order changed nothing and the
    # ordering the docstring claims went untested.
    # The older log carries an OPEN stage too -- a run that was killed mid-stage leaves exactly
    # that trace, forever. Scanning oldest-first would report ITS start time, i.e. a stall of
    # days. An earlier fixture only had a *finished* older log, so reversing the scan changed
    # nothing and the ordering this file's docstring claims went untested.
    _log(logs, "pipeline-20260906-120000.log", "[2026-09-05T12:00:01] === score START ===\n")
    _log(logs, "pipeline-20260906-204500.log", "[2026-09-06T21:08:58] === score START ===\n")
    for stamp in ("221500", "223000", "224500", "230000", "231500"):
        _log(logs, f"pipeline-20260906-{stamp}.log", "[2026-09-06T23:15:00] === pipeline SKIP: already running ===\n")

    got = inspect(logs, lock_path=held_lock, now=_NOW)
    assert got.state == "running" and got.stage == "score"
    assert 7800 < got.seconds < 8000


def test_a_stall_in_any_stage_is_reported_not_only_the_one_you_named(logs: Path, held_lock: Path) -> None:
    # The longest stall on record was in enrich while score finished in 73 seconds. A probe
    # watching only `score` returns "nothing is stuck" through all five hours of it.
    _log(
        logs,
        "pipeline-20260906-204500.log",
        "[2026-09-06T20:23:00] === score START ===\n"
        "[2026-09-06T20:24:13] === score OK ===\n"
        "[2026-09-06T20:23:59] === enrich START ===\n",
    )
    got = inspect(logs, lock_path=held_lock, now=_NOW)
    assert got.state == "running" and got.stage == "enrich"

    # …and naming a stage still restricts, which is what makes the default meaningful.
    assert inspect(logs, stage="score", lock_path=held_lock, now=_NOW).state == "unknown"


def test_a_fail_ends_a_stage_the_same_way_ok_does(logs: Path, held_lock: Path) -> None:
    # 36 real FAIL lines exist in the recorded history; treating them as non-terminal would keep
    # reporting a stage that ended hours ago.
    _log(
        logs,
        "pipeline-20260906-204500.log",
        "[2026-09-06T21:08:58] === score START ===\n[2026-09-06T21:09:25] === score FAIL (exit 143) ===\n",
    )
    assert inspect(logs, lock_path=held_lock, now=_NOW).state == "unknown"


def test_a_two_word_stage_name_parses(logs: Path, held_lock: Path) -> None:
    # `egress preflight` occurs 850 times in the recorded history and a \S+ capture never
    # matched any of them.
    _log(logs, "pipeline-20260906-204500.log", "[2026-09-06T21:08:58] === egress preflight START ===\n")
    got = inspect(logs, lock_path=held_lock, now=_NOW)
    assert got.state == "running" and got.stage == "egress preflight"


def test_a_stall_older_than_the_window_reads_as_unknown_not_idle(logs: Path, held_lock: Path) -> None:
    # The whole point of the third state: out of range must not look like healthy.
    _log(logs, "pipeline-20260901-120000.log", "[2026-09-01T12:00:00] === enrich START ===\n")
    assert inspect(logs, lock_path=held_lock, now=_NOW, window_hours=6).state == "unknown"


def test_an_observers_shared_lock_is_not_a_running_pipeline(logs: Path, tmp_path: Path) -> None:
    # ADR-052 observers probe with LOCK_SH. A LOCK_EX|LOCK_NB probe fails against a shared
    # holder, so an exclusive probe reports "pipeline running" whenever an observer happens to
    # be sampling -- and combined with a stale unterminated stage that makes a caller yield
    # forever. The probe has to take a shared lock, like every other reader.
    lock = tmp_path / ".pipeline.flock"
    handle = lock.open("a")
    fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
    try:
        _log(logs, "pipeline-20260906-204500.log", "[2026-09-06T21:08:58] === score START ===\n")
        assert inspect(logs, lock_path=lock, now=_NOW).state == "idle"
    finally:
        handle.close()


def test_a_dead_runs_unterminated_stage_is_not_reported_as_live(logs: Path, free_lock: Path) -> None:
    _log(logs, "pipeline-20260906-204500.log", "[2026-09-06T21:08:58] === score START ===\n")
    assert inspect(logs, lock_path=free_lock, now=_NOW).state == "idle"


def test_a_finished_stage_with_the_lock_held_reads_as_unknown(logs: Path, held_lock: Path) -> None:
    # Something holds the lock, so something is running; we just cannot see which stage.
    _log(
        logs,
        "pipeline-20260906-204500.log",
        "[2026-09-06T21:08:58] === score START ===\n[2026-09-06T21:09:25] === score OK ===\n",
    )
    assert inspect(logs, lock_path=held_lock, now=_NOW).state == "unknown"


def test_a_missing_lock_file_is_idle(logs: Path, tmp_path: Path) -> None:
    assert inspect(logs, lock_path=tmp_path / "absent.flock", now=_NOW).state == "idle"


def test_an_unreadable_logs_directory_is_unknown_not_idle(tmp_path: Path, held_lock: Path) -> None:
    assert inspect(tmp_path / "absent-logs", lock_path=held_lock, now=_NOW).state == "unknown"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WATCH), *args], capture_output=True, text=True, env={**os.environ}
    )


def test_the_exit_codes_are_the_contract_with_the_caller(logs: Path, held_lock: Path, tmp_path: Path) -> None:
    """Exercised through main(), because the exit code is the entire interface a shell driver has.

    Nothing tested it before: a mutation that disabled the stalled exit altogether -- making the
    starvation guard a no-op -- left the whole suite green.
    """
    _log(logs, "pipeline-20260906-204500.log", "[2026-09-06T21:08:58] === score START ===\n")
    base = ["--logs", str(logs), "--lock", str(held_lock), "--window-hours", "100000"]

    # `--max` is compared with a strict `>`; the `>=` variant is an accepted equivalent mutant,
    # since a one-second boundary carries no meaning for a threshold set in the hundreds.
    stalled = _run([*base, "--max", "1"])
    assert stalled.returncode == EXIT_STALLED, stalled.stderr
    assert "running for" in stalled.stdout

    relaxed = _run([*base, "--max", "999999999"])
    assert relaxed.returncode == EXIT_OK, relaxed.stderr

    unknown = _run(["--logs", str(tmp_path / "absent"), "--lock", str(held_lock), "--max", "1"])
    assert unknown.returncode == EXIT_UNKNOWN, unknown.stderr
    assert "unknown" in unknown.stdout
