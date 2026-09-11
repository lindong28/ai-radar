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

# The egress exit port is configurable, and a stale value in the environment routes this job at a
# port nobody serves. Cron hands us a near-empty environment so this is a no-op there -- it is the
# manual re-run that is exposed, and a manual re-run is the documented recovery for a missed
# window, which cannot be re-taken later. Measured 2026-09-09: a recovery run inherited
# AI_RADAR_EGRESS_PROXY_PORT=7897 from the shell that launched it (clash had since moved to 59527,
# nothing was listening on 7897) and died with `no request got through the egress proxy`. That text
# reads like an outage, so the operator goes looking at the proxy rather than at their own shell.
# Unset rather than pin: the code default is the single source for the port.
unset AI_RADAR_EGRESS_PROXY_PORT

# Do not ASK for a pair that overlaps what is already published. The tool gives us no choice of
# window -- aihot_dataset.py:3799 rejects any request that is not verbatim the canonical two
# complete UTC days the server currently designates -- and that pair slides by one day, while
# publishing is all-or-nothing: aihot_dataset.py:3879 builds publish_roots from the capture dir
# plus BOTH windows and raises target_exists if ANY of them exists, rolling the whole staging
# tree back. So on every day after a successful one, the pair's first day is yesterday's
# published window and the request drops the NEW day with it.
#
# Measured 2026-09-10: both runs fetched the whole surface (33 min each) and published nothing;
# windows/2026-09-09--09-10 exists nowhere, and the newest capture dir is still 09-09's.
#
# Skipping today loses nothing: tomorrow's pair starts exactly where our history ends, so that
# day is captured as the pair's FIRST half. Accrual stays one window per day. Deriving the
# decision from the last published window rather than an every-other-day cron also recovers the
# PHASE after a missed run, and across 31-day month boundaries where an alternating schedule
# desyncs. Phase only: a missed run still loses its window for good, because the pair the server
# offers has moved on and there is no way to ask for an older one.
# A directory NAME is not a publication, and this gate fails in the worst direction: anything it
# mistakes for published history suppresses the request, silently, forever. `ls` + a name regex
# cannot tell a validated window from an empty directory, a plain file, a half-removed tree, or a
# date-SHAPED name that is not a date -- `junk--9999-99-99T000000Z` parses to a future instant and
# would pin the gate shut for all time. So require a real directory, a real calendar date, and the
# manifest the publish step writes; anything else is not history and does not hold back a request.
last_end="$(python3 - <<'PY'
import datetime, pathlib, re
root = pathlib.Path("benchmarks/aihot/windows")
# Canonical windows are whole UTC days, so the time is literally 000000 -- pin it rather than
# accept \d{6}. `99:99:99` passes a digit-count check, sorts above every real instant, and one
# such directory stops this job requesting anything ever again while exiting 0 each day. That is
# the third face of the same hole (after the calendar date and the symlink); the shape to
# remember is that EVERY unvalidated field here fails toward permanent silence.
pat = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T000000Z--(\d{4}-\d{2}-\d{2})T000000Z$"
)
best = ""
for d in sorted(root.iterdir()) if root.is_dir() else []:
    m = pat.match(d.name)
    # `is_dir()`/`is_file()` FOLLOW symlinks, so those alone would let a link to any directory
    # holding a manifest pass as published history. Both files are required: publish writes the
    # manifest AND the items, and a tree with only one of them is not a window.
    if not (m and d.is_dir() and not d.is_symlink()):
        continue
    if not all((d / f).is_file() and not (d / f).is_symlink() for f in ("manifest.json", "items.jsonl")):
        continue
    try:
        start = datetime.date.fromisoformat(m.group(1))
        end = datetime.date.fromisoformat(m.group(2))
    except ValueError:
        continue
    # Fifth face: both dates can be real and still not name a canonical window.
    # `2026-01-01T000000Z--2099-01-01T000000Z` is two valid days, sorts above everything, and
    # would skip every request until 2099. A canonical window is exactly one day wide.
    if end - start != datetime.timedelta(days=1):
        continue
    best = max(best, f"{m.group(2)}T00:00:00Z")
print(best)
PY
)"
requested=""
if [ -n "$last_end" ] && [ "$last_end" \> "$START" ]; then
  echo "no request: windows already cover through $last_end, so the canonical pair"
  echo "            $START .. $END overlaps it and would drop its new day"
  rc=0
else
  echo "requesting $START .. $END (published through ${last_end:-nothing})"
  requested=1
  # Seam so the git-handling below can be exercised without spending a real AIHOT window.
  # tests/test_capture_aihot_daily.sh substitutes a stub; nothing else sets it.
  AIHOT_CAPTURE_CMD="${AIHOT_CAPTURE_CMD:-PYTHONPATH=src uv run python scripts/capture_aihot_dataset.py capture}"
  eval "$AIHOT_CAPTURE_CMD --start \"$START\" --end \"$END\""
  rc=$?
fi

# `target_exists` is NOT the benign steady state the old branch treated it as, and that branch is
# gone. Its premise was that the error means "AIHOT has not advanced its canonical window yet",
# but aihot_dataset.py:3799 refuses a request that does not equal the server's current canonical
# pair outright (`window_invalid`) -- so reaching the publish step at all proves the server DID
# advance. The old branch turned that alarm into rc=0 plus a log line asserting the opposite. Its
# 22 tests passed and both reverse mutations went red: they encoded the same wrong premise as the
# code, so they could not see it.
#
# NOT a universal claim, and the earlier wording here ("always", "can only mean") was wrong.
# There is one benign shape: two runs that both pass the gate before either publishes -- a manual
# recovery overlapping the cron slot is the realistic case -- and the loser gets `target_exists`
# ~33 minutes later on data its twin already stored. No lock guards that window. It is deliberately
# not guarded: concurrency has not actually bitten (2026-09-11's manual recovery finished 00:57,
# the cron slot is 01:37), and this repo's standing rule is to add machinery for observed failures,
# not predicted ones. Cost if it does happen: one wasted fetch and one spurious non-zero.
#
# The overlap it was papering over is now prevented upstream, at the request. Reaching here now
# means either a genuine defect -- a miscomputed date, a window published out of band -- or the
# concurrent-publish race described above. It is not self-evident which; read the log.
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
#
# Runs on no-request days too. Retention above deletes TRACKED capture directories, so a skip day
# can produce real staged deletions; guarding this block on "we fetched something" left them as
# submodule dirt that `ignore=dirty` then hides from the parent. Nothing new to store is already
# handled below by the `diff --cached --quiet` arm.
#
# Every failure arm sets rc. Persisting is not bookkeeping: until these objects are in a second
# place they exist only in this disposable gitdir, and a run that fetched an irrecoverable day and
# then failed to commit it must not report success. (Measured 2026-09-11: the recovered windows
# sat on a DETACHED submodule HEAD in this worktree's private gitdir -- no branch, one object
# store. `git worktree remove` would have destroyed them.)
if [ $rc -eq 0 ]; then
  if ! git -C benchmarks/aihot add captures windows 2>&1; then
    echo "  WARNING: git add failed; nothing was committed"
    rc=1
  elif git -C benchmarks/aihot diff --cached --quiet; then
    echo "  submodule: nothing staged"
  else
    if [ -n "$requested" ]; then
      msg="data(aihot): $(basename "$(ls -dt benchmarks/aihot/captures/aihot-* 2>/dev/null | head -1)")"
    else
      msg="chore(aihot): retention on a no-request day"
    fi
    if git -C benchmarks/aihot commit -q -m "$msg"; then
      echo "  submodule commit: $(git -C benchmarks/aihot rev-parse --short HEAD)"
      if git commit -q -m "chore(aihot): pin $(git -C benchmarks/aihot rev-parse --short HEAD)" -- benchmarks/aihot; then
        echo "  pointer commit: $(git rev-parse --short HEAD)"
      else
        echo "  WARNING: submodule committed but the parent pin did NOT -- run git submodule update and the new capture becomes an orphan"
        rc=1
      fi
    else
      echo "  WARNING: submodule commit failed; this run's output is uncommitted"
      rc=1
    fi
  fi
fi

# Last word, after everything that can dirty the tree. The 2026-09-08 failure was invisible for
# a day because the only record was a log nobody reads, so this exits non-zero.
#
# Two checks, because `ignore=dirty` above means the parent's status no longer SEES the
# submodule -- which is the point, and also the reason a single parent-side check would have
# been blind to the likeliest place for junk to accumulate.
worktree_dirty=0
if [ -n "$(git status --porcelain)" ]; then
  worktree_dirty=1
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
