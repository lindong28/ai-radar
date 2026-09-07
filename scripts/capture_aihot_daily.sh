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

# Retention. One capture is ~30 MB of raw pages; the windows the evalset actually reads are
# ~550 KB per day and live outside it. Left alone this job adds ~11 GB a year to the dataset
# repository, so the size has to be someone's decision rather than a side effect.
#
# 14 days, chosen by the repository owner on 2026-09-07, holds a steady ~420 MB. It is twice
# AIHOT's own 7-day rolling coverage: once a window is older than that the source cannot be
# re-captured at all, so 14 leaves one full re-take window of slack after a problem surfaces.
# AIHOT_CAPTURE_RETAIN_DAYS overrides it; 0 keeps everything.
#
# Windows are never pruned -- they are the data. Captures are the evidence a window validates
# against, and a window's manifest names its capture by path and sha256, so pruning a capture
# leaves that window usable but no longer verifiable.
RETAIN="${AIHOT_CAPTURE_RETAIN_DAYS:-14}"
if [ "$RETAIN" -gt 0 ] 2>/dev/null; then
  # Age comes from the directory name (aihot-YYYYMMDDTHHMMSSZ), not from mtime. A fresh clone
  # stamps every capture with the checkout time, so an mtime predicate prunes nothing for the
  # first RETAIN days on a new machine and then prunes the whole history at once -- and it fails
  # that way silently, which is exactly the shape this repo keeps getting bitten by.
  before=$(du -sm benchmarks/aihot/captures 2>/dev/null | cut -f1)
  cutoff=$(python3 -c "import datetime,sys; print((datetime.datetime.now(datetime.UTC)-datetime.timedelta(days=int(sys.argv[1]))).strftime('%Y%m%dT%H%M%SZ'))" "$RETAIN")
  for d in benchmarks/aihot/captures/aihot-*; do
    [ -d "$d" ] || continue
    stamp="${d##*/aihot-}"
    case "$stamp" in
      [0-9]*Z) [ "$stamp" \< "$cutoff" ] && { echo "pruning $d"; rm -rf "$d"; } ;;
      *) echo "skipping unrecognised capture dir name: $d" ;;
    esac
  done
  after=$(du -sm benchmarks/aihot/captures 2>/dev/null | cut -f1)
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === retention: keep ${RETAIN}d, captures ${before:-?}MB -> ${after:-?}MB ==="
fi
exit $rc
