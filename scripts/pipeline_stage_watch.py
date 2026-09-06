#!/usr/bin/env python3
"""Report whether the scheduled pipeline is stuck in a stage, and for how long.

Exists because a batch job can starve the pipeline while every signal the batch job watches says
it is healthy. On 2026-09-06 a backfill at eight workers took the scoring lock in a tight loop;
the pipeline's own score stage went from tens of seconds to over two hours, eleven consecutive
cron rounds logged "already running", and nothing entered the archive. The backfill's four exit
conditions all watched the backfill -- its own failures, its own deadline, its own error rate --
so all four read green throughout. A person reading consumer-side counters found it instead.

    scripts/pipeline_stage_watch.py                  # prints state, exit 0
    scripts/pipeline_stage_watch.py --max 600        # exit 1 if stuck longer, 2 if unknown
    scripts/pipeline_stage_watch.py --stage score    # restrict to one stage

Three states, not two. "Cannot tell" is its own answer: a caller that treats an unreadable probe
as a stall pauses forever, and one that treats it as healthy keeps starving the pipeline. The
first draft of this file had only two, and its exit code could not distinguish a real stall from
its own crash.

WHICH STAGE. By default, any of them. The contended resource is the SQLite write lock, which
enrich, curate and interpret take as much as score does -- and the longest stall in the recorded
history (2026-09-02, 5h15m) was in enrich while score finished in 73 seconds. Watching one named
stage makes the probe narrower than the incident it exists to catch.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from airadar.pipeline_lock import DEFAULT_PIPELINE_LOCK_PATH, pipeline_lock_is_held  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Stage names are captured non-greedily so two-word stages parse too: `egress preflight` is a
# real stage that a `\S+` capture silently never matched.
LINE = re.compile(r"\[([0-9T:\-]+)\] === (.+?) (START|OK|FAIL[^=]*) ===")
LOG_NAME = re.compile(r"pipeline-(\d{8}-\d{6})\.log$")
DEFAULT_WINDOW_HOURS = 24.0

EXIT_OK, EXIT_STALLED, EXIT_UNKNOWN = 0, 1, 2


@dataclass(frozen=True)
class StageState:
    state: str  # "idle" | "running" | "unknown"
    stage: str | None = None
    seconds: float | None = None
    detail: str = ""

    def __str__(self) -> str:
        if self.state == "running":
            return f"{self.stage}: running for {self.seconds:.0f}s"
        if self.state == "unknown":
            return f"unknown: {self.detail}"
        return f"idle: {self.detail}" if self.detail else "idle"


def _logs_within(logs_dir: Path, now: datetime, window_hours: float) -> list[Path]:
    """Newest first, bounded by TIME rather than by file count.

    A count bound is not a time bound: every cron round writes one log whether it runs or skips,
    so "the last 20 files" silently means "the last 5 hours" only while cron stays at 15 minutes.
    Worse, the recorded history already contains a 21-round skip streak -- longer than that
    window -- so the count bound went blind at the exact point the stall was worst.
    """
    cutoff = now - timedelta(hours=window_hours)
    dated: list[tuple[datetime, Path]] = []
    for path in logs_dir.glob("pipeline-*.log"):
        match = LOG_NAME.search(path.name)
        if not match:
            continue
        stamp = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
        if stamp >= cutoff:
            dated.append((stamp, path))
    return [path for _, path in sorted(dated, key=lambda pair: pair[0], reverse=True)]


def _open_stage(log: Path, stage: str | None) -> tuple[str, datetime] | None:
    """The stage this log entered and never left, if any."""
    started: dict[str, datetime] = {}
    for line in log.read_text(errors="replace").splitlines():
        match = LINE.match(line)
        if not match:
            continue
        name = match.group(2)
        if stage is not None and name != stage:
            continue
        if match.group(3) == "START":
            started[name] = datetime.fromisoformat(match.group(1))
        else:
            started.pop(name, None)
    if not started:
        return None
    return max(started.items(), key=lambda pair: pair[1])


def inspect(
    logs_dir: Path,
    *,
    stage: str | None = None,
    lock_path: Path = DEFAULT_PIPELINE_LOCK_PATH,
    now: datetime | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
) -> StageState:
    now = now or datetime.now()
    held = pipeline_lock_is_held(lock_path)
    if held is None:
        return StageState("unknown", detail=f"lock probe failed on {lock_path}")
    if not held:
        return StageState("idle", detail="no pipeline holds the lock")
    if not logs_dir.is_dir():
        return StageState("unknown", detail=f"lock is held but {logs_dir} is not readable")

    for log in _logs_within(logs_dir, now, window_hours):
        found = _open_stage(log, stage)
        if found is not None:
            name, started = found
            return StageState("running", stage=name, seconds=(now - started).total_seconds())
    # The lock is held, so something IS running; not finding its stage means the probe cannot
    # see it -- a stall older than the window, or a stage name it fails to parse. Reporting
    # "idle" here is the failure this whole file exists to avoid.
    return StageState(
        "unknown",
        detail=f"lock is held but no open stage found in the last {window_hours:g}h of logs",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", help="Restrict to one stage; default is whichever is open.")
    parser.add_argument("--logs", type=Path, default=PROJECT_ROOT / "logs")
    parser.add_argument("--lock", type=Path, default=DEFAULT_PIPELINE_LOCK_PATH)
    parser.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS)
    parser.add_argument("--max", type=float, help="Exit 1 when a stage has been open longer.")
    args = parser.parse_args()

    result = inspect(
        args.logs, stage=args.stage, lock_path=args.lock, window_hours=args.window_hours
    )
    print(result)
    if result.state == "unknown":
        return EXIT_UNKNOWN
    if args.max is not None and result.seconds is not None and result.seconds > args.max:
        return EXIT_STALLED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
