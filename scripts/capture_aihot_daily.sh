#!/bin/bash
# Capture AIHOT's canonical two-day window, once a day.
#
# Why a dedicated worktree: the capture records the tool checkout's exact HEAD and refuses to run
# from a dirty tree (an eval-identity guarantee). The main checkout is routinely dirty -- other
# agent sessions leave untracked files in it -- so a job pinned to it would fail every day for a
# reason that has nothing to do with AIHOT.
#
# Why this runs daily at all: AIHOT's server only serves a 7-day rolling window
# (`ensure_window_covered` derives coverage_start from the last response date minus 7 days), and the
# capture tool only accepts the canonical window the server currently designates. A day not captured
# on the day is gone -- there is no backfill.
set -uo pipefail

WORKTREE="${AIHOT_CAPTURE_WORKTREE:-/Users/lindong/research/ai-radar-worktrees/t3-aihot-recapture-20260907}"
LOG_DIR="${AIHOT_CAPTURE_LOG_DIR:-/Users/lindong/research/ai-radar/logs}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/aihot-capture-$STAMP.log"
mkdir -p "$LOG_DIR"

exec >>"$LOG" 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === aihot capture START (worktree=$WORKTREE) ==="

cd "$WORKTREE" || { echo "FATAL: worktree missing"; exit 3; }

# The canonical window is whatever the server designates; ask for the two most recent complete UTC
# days and let the tool reject it if that is not the canonical pair.
END="$(date -u +%Y-%m-%dT00:00:00Z)"
START="$(date -u -v-2d +%Y-%m-%dT00:00:00Z 2>/dev/null || date -u -d '2 days ago' +%Y-%m-%dT00:00:00Z)"
echo "requesting $START .. $END"

PYTHONPATH=src uv run python scripts/capture_aihot_dataset.py capture --start "$START" --end "$END"
rc=$?

# An already-captured window is the expected steady state on a re-run, not a failure.
if [ $rc -ne 0 ] && grep -q "existing capture\|already" "$LOG"; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === aihot capture SKIP: window already captured ==="
  exit 0
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === aihot capture EXIT rc=$rc ==="
exit $rc
