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
# The capture refuses a dirty TOOL checkout -- it records the checkout's exact HEAD, an
# eval-identity guarantee. Its data happens to live inside that checkout, so without this line
# the job's own output counts as code dirt and the first success blocks every run after it.
# That is how 2026-09-08 09:37 failed: rc=2 on the 09-07 capture's untracked files.
#
# Scoping the dirt check out of the submodule fixes the whole class at once -- untracked new
# captures, the tracked deletions retention makes, and a `.staging` tree left by a capture
# killed mid-flight (16 minutes a day of exposure) all stop mattering. Verified on this
# worktree: with 771 tracked files deleted, the tool's own predicate still reads dirty=False.
# It hides genuine gitlink changes too, which costs nothing here -- nothing else moves it.
git config --local submodule.benchmarks/aihot.ignore dirty

echo "requesting $START .. $END"

# Seam so the git-handling below can be exercised without spending a real AIHOT window.
# tests/test_capture_aihot_daily.sh substitutes a stub; nothing else sets it.
AIHOT_CAPTURE_CMD="${AIHOT_CAPTURE_CMD:-PYTHONPATH=src uv run python scripts/capture_aihot_dataset.py capture}"
eval "$AIHOT_CAPTURE_CMD --start \"$START\" --end \"$END\""
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
# Runs whether or not today's capture succeeded. It was briefly guarded on rc=0 after the
# 2026-09-08 incident, on a misreading: the capture it deleted that day (aihot-20260821T142635Z)
# was 18 days old against a 14-day window, so a SUCCESSFUL run would have deleted exactly the
# same directory. rc=2 was concurrent, not causal. And gating disk policy on network success is
# backwards -- an outage is when pruning matters most, and a full disk is itself a reason
# capture fails, which the guard would have made self-perpetuating.
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

# Persist, AFTER retention so the commit records the pruned state rather than a tree retention
# is about to change. This is durability, not deadlock relief -- `ignore=dirty` above owns that,
# and this block existing does not make tomorrow's run depend on it.
#
# Named paths, and note what that does NOT buy: `captures` and `windows` are directories, so a
# stray file under either still gets committed. It only excludes strays elsewhere in the tree.
#
# KNOWN EXPOSURE, larger than a re-clone: these commits live in this linked worktree's private
# gitdir (.git/worktrees/<name>/modules/...). `git worktree remove` deletes that gitdir, and the
# objects are then gone machine-wide while the superproject keeps a pin pointing at nothing.
# This is a dated program worktree -- exactly the kind that gets cleaned up. Pushing the
# submodule is what makes the data durable; until then there is one copy, in a disposable place.
if [ $rc -eq 0 ]; then
  if git -C benchmarks/aihot add captures windows 2>&1 &&
     ! git -C benchmarks/aihot diff --cached --quiet; then
    if git -C benchmarks/aihot commit -q -m "data(aihot): $(basename "$(ls -dt benchmarks/aihot/captures/aihot-* 2>/dev/null | head -1)")"; then
      echo "  submodule commit: $(git -C benchmarks/aihot rev-parse --short HEAD)"
      git commit -q -m "chore(aihot): pin $(git -C benchmarks/aihot rev-parse --short HEAD)" -- benchmarks/aihot \
        && echo "  pointer commit: $(git rev-parse --short HEAD)" \
        || echo "  WARNING: submodule committed but the parent pin did NOT -- run git submodule update and the new capture becomes an orphan"
    else
      echo "  WARNING: submodule commit failed; today's capture is uncommitted"
    fi
  else
    echo "  submodule: nothing staged (either nothing new, or git add failed above)"
  fi
fi

# Last word, after everything that can dirty the tree. The 2026-09-08 failure was invisible for
# a day because the only record was a log nobody reads, so this exits non-zero.
#
# Two checks, because `ignore=dirty` above means the parent's status no longer SEES the
# submodule -- which is the point, and also the reason a single parent-side check would have
# been blind to the likeliest place for junk to accumulate.
if [ -n "$(git status --porcelain)" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === WARNING: worktree dirty at exit, tomorrow will fail ==="
  git status --short | sed 's/^/    /'
  [ $rc -eq 0 ] && rc=1
fi
# Submodule leftovers no longer block tomorrow, so this reports without failing the run -- but
# it does report, because unbounded junk under a data directory is worth someone seeing.
sub_dirt="$(git -C benchmarks/aihot status --porcelain)"
if [ -n "$sub_dirt" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === NOTE: uncommitted content in the dataset submodule ==="
  printf '%s\n' "$sub_dirt" | sed 's/^/    /'
fi
exit $rc
