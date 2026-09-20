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
# legacy mode accepts only the canonical pair. Opt-in fill-missing mode can recover missing
# days while they remain inside the API's observed rolling coverage.
set -uo pipefail

WORKTREE="${AIHOT_CAPTURE_WORKTREE:-/Users/lindong/research/ai-radar-worktrees/t3-aihot-recapture-20260907}"
LOG_DIR="${AIHOT_CAPTURE_LOG_DIR:-/Users/lindong/research/ai-radar/logs}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/aihot-capture-$STAMP.log"
mkdir -p "$LOG_DIR"

exec >>"$LOG" 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === aihot capture START (worktree=$WORKTREE) ==="

cd "$WORKTREE" || { echo "FATAL: worktree missing"; exit 3; }
AIHOT_REFERENCE_PATH="$(git config -f .gitmodules --get submodule.benchmarks/aihot.path)" || {
  echo "FATAL: AIHOT reference submodule path missing from runtime .gitmodules"; exit 3;
}

# Wall-clock bound for the network calls below. There is no `timeout(1)` on this host (measured:
# `command not found`), and ssh's own `ConnectTimeout` covers the TCP connect ONLY -- a half-open
# connection after the handshake, a stalled resolver, or a slow large push all hang indefinitely,
# holding the daily slot with an empty log because `exec >>"$LOG"` has already swallowed stdout.
# That silent-hang shape is the one this job keeps getting bitten by, so the bound is outside the
# tool rather than a flag on it. This is what ADR-20260913-c7d4 Consequences §1 calls the outer
# timeout; `BatchMode=yes` handles the interactive-prompt half, and is deliberately not trusted to
# handle this half -- whether it suppresses a macOS Keychain GUI dialog is unverified here.
# bash 3.2 compatible: background the command, arm a killer, take whichever lands first.
bounded() {
  bounded_secs="$1"; shift
  "$@" &
  bounded_pid=$!
  # The killer MUST NOT inherit this function's stdout. Measured: without `>/dev/null`, a caller
  # that pipes us (`bounded ... git ls-remote | awk ...`) hangs for the full bound -- awk waits for
  # EOF, and EOF needs every writer to close, including a `sleep` that is doing nothing with the
  # pipe. The command finishes in milliseconds and the whole job still stalls; nothing reports it.
  # stdin too, so it can never contend for the terminal.
  # Children first, then the process itself. `git push` forks `ssh`, and a TERM that reaches only
  # git leaves ssh running -- on this host that can mean an orphaned process still sitting on a
  # Keychain dialog nobody will close. Killing the parent first would reparent the child and lose
  # the handle on it. NOT `kill -- -$pid`: this shell runs without job control, so the background
  # command shares the script's own process group and a group kill would take the script down too.
  ( sleep "$bounded_secs"
    for bounded_child in $(pgrep -P "$bounded_pid" 2>/dev/null); do
      kill -TERM "$bounded_child" 2>/dev/null
    done
    kill -TERM "$bounded_pid" 2>/dev/null
  ) </dev/null >/dev/null 2>&1 &
  bounded_killer=$!
  wait "$bounded_pid"; bounded_code=$?
  kill "$bounded_killer" 2>/dev/null
  wait "$bounded_killer" 2>/dev/null
  return "$bounded_code"
}

# The canonical window is whatever the server designates; ask for the two most recent complete UTC
# days and let the tool reject it if that is not the canonical pair.
END="${AIHOT_CAPTURE_END:-$(date -u +%Y-%m-%dT00:00:00Z)}"
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
last_end="$(python3 - "$AIHOT_REFERENCE_PATH" <<'PY'
import datetime, pathlib, re, sys
root = pathlib.Path(sys.argv[1]) / "windows"
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
if [ "${AIHOT_CAPTURE_SKIP_FETCH:-0}" = 1 ]; then
  echo "no source request: supervised windows already validated; retrying pending publication"
  rc=0
elif [ "${AIHOT_CAPTURE_FILL_MISSING:-0}" = 1 ]; then
  # Opt-in only: six complete days fit inside the API's rolling seven-day coverage.
  # The collector verifies every requested day's actual response-Date bounds.
  START="${AIHOT_CAPTURE_START:-$(date -u -v-6d +%Y-%m-%dT00:00:00Z 2>/dev/null || date -u -d '6 days ago' +%Y-%m-%dT00:00:00Z)}"
  requested=1
  AIHOT_CAPTURE_CMD="${AIHOT_CAPTURE_CMD:-PYTHONPATH=src uv run python scripts/capture_aihot_dataset.py capture}"
  resilient_flag=""
  [ "${AIHOT_CAPTURE_RESILIENT:-0}" = 1 ] && resilient_flag="--resilient"
  eval "$AIHOT_CAPTURE_CMD --output-root \"$AIHOT_REFERENCE_PATH\" --fill-missing $resilient_flag --start \"$START\" --end \"$END\""
  rc=$?
elif [ -n "$last_end" ] && [ "$last_end" \> "$START" ]; then
  echo "no request: windows already cover through $last_end, so the canonical pair"
  echo "            $START .. $END overlaps it and would drop its new day"
  rc=0
else
  echo "requesting $START .. $END (published through ${last_end:-nothing})"
  requested=1
  # Seam so the git-handling below can be exercised without spending a real AIHOT window.
  # tests/test_capture_aihot_daily.sh substitutes a stub; nothing else sets it.
  AIHOT_CAPTURE_CMD="${AIHOT_CAPTURE_CMD:-PYTHONPATH=src uv run python scripts/capture_aihot_dataset.py capture}"
  eval "$AIHOT_CAPTURE_CMD --output-root \"$AIHOT_REFERENCE_PATH\" --start \"$START\" --end \"$END\""
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
# 30 days, chosen by the repository owner on 2026-09-14, keeps roughly one month of raw
# evidence. Actual storage varies with AIHOT's content and the capture cadence, so it is not
# derived from a fixed per-capture size estimate.
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
RETAIN="${AIHOT_CAPTURE_RETAIN_DAYS:-30}"
if [ "$RETAIN" -gt 0 ] 2>/dev/null; then
  # Age comes from the directory name (aihot-YYYYMMDDTHHMMSSZ), not from mtime. A fresh clone
  # stamps every capture with the checkout time, so an mtime predicate prunes nothing for the
  # first RETAIN days on a new machine and then prunes the whole history at once -- and it fails
  # that way silently, which is exactly the shape this repo keeps getting bitten by.
  before=$(du -sm "$AIHOT_REFERENCE_PATH/captures" 2>/dev/null | cut -f1)
  cutoff=$(python3 -c "import datetime,sys; print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=int(sys.argv[1]))).strftime('%Y%m%dT%H%M%SZ'))" "$RETAIN")
  for d in "$AIHOT_REFERENCE_PATH"/captures/aihot-*; do
    [ -d "$d" ] || continue
    stamp="${d##*/aihot-}"
    case "$stamp" in
      [0-9]*Z) [ "$stamp" \< "$cutoff" ] && { echo "pruning $d"; rm -rf "$d"; } ;;
      *) echo "skipping unrecognised capture dir name: $d" ;;
    esac
  done
  after=$(du -sm "$AIHOT_REFERENCE_PATH/captures" 2>/dev/null | cut -f1)
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
# `git worktree remove` is not the only way that copy dies: retention above prunes capture
# directories older than RETAIN days on its own clock, which does not consult whether they ever
# reached a remote. A capture that stays unpushed long enough is deleted by this job itself --
# and correctly reporting rc=1 every one of those days does not save it. The reachability check
# further down is what makes those days loud; acting on them is still a human's job.
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
  if ! git -C "$AIHOT_REFERENCE_PATH" add captures windows 2>&1; then
    echo "  WARNING: git add failed; nothing was committed"
    rc=1
  elif git -C "$AIHOT_REFERENCE_PATH" diff --cached --quiet; then
    echo "  submodule: nothing staged"
  else
    if [ -n "$requested" ]; then
      msg="data(aihot): $(basename "$(ls -dt "$AIHOT_REFERENCE_PATH"/captures/aihot-* 2>/dev/null | head -1)")"
    else
      msg="chore(aihot): retention on a no-request day"
    fi
    if ! git -C "$AIHOT_REFERENCE_PATH" commit -q -m "$msg"; then
      echo "  WARNING: submodule commit failed; this run's output is uncommitted"
      rc=1
    fi
  fi
  # Retry publication even when the previous attempt already committed the data.
  if [ $rc -eq 0 ]; then
      sub_sha="$(git -C "$AIHOT_REFERENCE_PATH" rev-parse HEAD)"
      echo "  submodule commit: $(git -C "$AIHOT_REFERENCE_PATH" rev-parse --short HEAD)"
      # Order is load-bearing and comes from
      # [ADR-060](../docs/adr/060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md):
      # 「data local commit → 经显式授权 push并验证远端 exact ref → 主仓记录 gitlink」.
      # A failed push must therefore NOT be followed by the pointer commit, or the superproject
      # pins a SHA that exists on no remote -- the exact orphan the pin is supposed to prevent.
      # ADR-20260913-c7d4 supersedes only the AUTHORISATION half of that sentence (this one ref
      # is authorised standing, by user decision on 2026-09-13); the ordering half stands.
      #
      # `BatchMode=yes` keeps ssh from raising an interactive passphrase prompt this job could
      # never answer; the wall-clock bound below covers the rest. It is NOT what makes auth work.
      #
      # What makes auth work is the ssh-agent, and cron does not have it. Measured 2026-09-14,
      # same environment both arms, the ONLY difference being SSH_AUTH_SOCK:
      #   without it -> `git@github.com: Permission denied (publickey)`
      #   with it    -> the ref reads back fine
      # `~/.ssh/id_rsa` carries a passphrase, and [ADR-013](../docs/adr/013-db-sync-cron-agent-socket-auth.md)
      # already settled this exact problem for the sibling DB-sync cron: discover the logged-in
      # user's agent via launchd. That ADR explicitly REJECTED leaning on `UseKeychain yes`
      # ("passphrase 是否已入 keychain 未确认"), so an earlier revision of this comment, which
      # assumed the Keychain authenticates here, was wrong.
      #
      # Deliberately simpler than the sibling: it fingerprint-matches a specific sync key, this
      # takes the first agent holding any key. One wrong pick degrades to the push failure this
      # block already handles and alerts on -- whereas duplicating the fingerprint derivation
      # would add a second copy of logic that has to track `ssh -G`.
      #
      # Inherited limitation, same as ADR-013's Option A: no logged-in session means no agent
      # means this fails. It fails loudly (rc=1 -> Feishu), which is the documented trade.
      #
      # The glob is overridable for ONE reason: to be testable. Its real value lives under
      # /var/run, where a test cannot create a socket without root -- so with the path fixed, the
      # single piece of this block that decides whether auth works had no automated coverage at
      # all, which is how it stayed missing until cron had already been unable to push.
      agent_glob="${AIHOT_CAPTURE_AGENT_SOCK_GLOB:-/var/run/com.apple.launchd.*/Listeners}"
      if [ -z "${SSH_AUTH_SOCK:-}" ] || ! ssh-add -l >/dev/null 2>&1; then
        for sock in $agent_glob; do  # unquoted on purpose: this is a glob, not a path
          [ -S "$sock" ] || continue
          if SSH_AUTH_SOCK="$sock" ssh-add -l >/dev/null 2>&1; then
            export SSH_AUTH_SOCK="$sock"
            echo "  ssh-agent: discovered $sock"
            break
          fi
        done
      fi
      [ -n "${SSH_AUTH_SOCK:-}" ] || echo "  WARNING: no ssh-agent with a key found; the push below will fail"
      push_remote="${AIHOT_CAPTURE_PUSH_REMOTE-origin}"
      push_ref="${AIHOT_CAPTURE_PUSH_REF:-refs/heads/captures/daily}"
      # Set UNCONDITIONALLY, not `${GIT_SSH_COMMAND:-...}`: an inherited value -- from a manual
      # re-run's shell, or any wrapper -- would silently replace these flags wholesale rather than
      # add to them, and the loss is invisible (no log, no failure, just a job that can hang).
      # Same reasoning as the `unset AI_RADAR_EGRESS_PROXY_PORT` above, and the same measured
      # shape: a manual recovery run is the exposed case, cron's environment is near-empty.
      export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15"
      pushed=""
      if [ -z "$push_remote" ]; then
        echo "  submodule push: skipped -- AIHOT_CAPTURE_PUSH_REMOTE is set empty, so this run"
        echo "           deliberately did not push. NOT a network failure; nothing to retry."
        echo "           The gitlink is NOT recorded either: ADR-060's order pins only what a"
        echo "           remote has verifiably got, and 'skipped' is not 'got'."
        pushed=skipped
      elif ! bounded "${AIHOT_CAPTURE_NET_TIMEOUT:-300}" git -C "$AIHOT_REFERENCE_PATH" push -q "$push_remote" "HEAD:$push_ref"; then
        echo "  WARNING: submodule push to $push_remote/$push_ref FAILED or timed out."
        echo "           Not recording the gitlink: pinning a SHA that reached no remote is the"
        echo "           orphan ADR-060's ordering exists to prevent. The capture is committed"
        echo "           locally and tomorrow's run retries it -- IF the cause was transient."
        echo "           A non-fast-forward does NOT self-heal: it fails identically every day"
        echo "           until someone reconciles \`$push_ref\` by hand. git's own error is above."
        rc=1
      # ADR-060 asks for the remote EXACT ref, not just a zero exit. A push can report success
      # while a hook or a racing writer leaves the ref elsewhere; that reads as durable and is not.
      else
        remote_sha="$(bounded "${AIHOT_CAPTURE_NET_TIMEOUT:-300}" git -C "$AIHOT_REFERENCE_PATH" ls-remote "$push_remote" "$push_ref" 2>/dev/null | awk 'NR==1{print $1}')"
        if [ -z "$remote_sha" ]; then
          # Empty means the read-back itself did not happen (unreachable, timed out, no such ref).
          # Kept separate from a value mismatch: this one is usually transient and retries on its
          # own, the other one means something else is writing the ref. Same log line for both
          # would send the 2am reader down the wrong path.
          echo "  WARNING: pushed, but could NOT read $push_remote/$push_ref back (empty result:"
          echo "           unreachable, timed out, or the ref is absent). Durability unconfirmed,"
          echo "           so the gitlink is not recorded. Usually transient -- tomorrow retries."
          rc=1
        elif [ "$remote_sha" != "$sub_sha" ]; then
          echo "  WARNING: pushed, but $push_remote/$push_ref reads back as $remote_sha,"
          echo "           not $sub_sha. Something else is writing that ref (a server hook, or a"
          echo "           concurrent publisher). This does NOT clear on its own -- find the other"
          echo "           writer. Gitlink not recorded."
          rc=1
        else
          echo "  submodule push: $(git -C "$AIHOT_REFERENCE_PATH" rev-parse --short HEAD) -> $push_remote/$push_ref (verified)"
          pushed=1
        fi
      fi
      # `= 1`, NOT `-n`: `pushed=skipped` is also non-empty, and an earlier revision of this block
      # used `-n` and therefore recorded the gitlink on skip days -- pinning a SHA that had reached
      # no remote, which is the exact orphan ADR-060's ordering exists to prevent. Caught by an
      # independent reviewer, not by the tests; case 19a now asserts it.
      if [ "$pushed" = 1 ]; then
        if git diff --quiet HEAD -- "$AIHOT_REFERENCE_PATH"; then
          echo "  pointer already records the verified data commit"
        elif git commit -q -m "chore(aihot): pin $(git -C "$AIHOT_REFERENCE_PATH" rev-parse --short HEAD)" -- "$AIHOT_REFERENCE_PATH"; then
          echo "  pointer commit: $(git rev-parse --short HEAD)"
        else
          echo "  WARNING: submodule committed but the parent pin did NOT -- run git submodule update and the new capture becomes an orphan"
          rc=1
        fi
      fi
  fi
    # Deliberately OUTSIDE the "did we stage anything today" branch. The real 2026-09-13
    # failure was not one loud day followed by quiet ones -- it was that every day AFTER the
    # unpushed capture staged nothing, said "submodule: nothing staged", and exited 0. A check
    # that only runs on capture days is silent for exactly the days the data is at risk. The
    # test suite caught this: case 19's second arm ran, staged nothing, and printed neither
    # verdict.
    #
    # The switch exists because the 43 cases above assert a different invariant (a run leaves
    # a clean tree, and persistence failures exit non-zero) on a fixture whose submodule is
    # never pushed. Without it every one of them would fail for a reason none of them is
    # about. It defaults ON, so a real run cannot lose the check by forgetting a variable;
    # the two cases at the end of the test file cover both of its arms.
    sub_head="$(git -C "$AIHOT_REFERENCE_PATH" rev-parse --short HEAD)"
    if [ "${AIHOT_CAPTURE_DURABILITY_CHECK:-1}" != 1 ]; then
      echo "  submodule durability check: off"
    elif [ -n "$(git -C "$AIHOT_REFERENCE_PATH" branch -r --contains HEAD 2>/dev/null)" ]; then
      echo "  submodule durable: $sub_head is on a remote-tracking ref"
    else
      # Local remote-tracking refs are a CACHE, not the remote. Pushing from a different checkout
      # of the same submodule leaves this one's copy behind, and the check then alarms every day
      # about data that is in fact durable. Measured 2026-09-14: this worktree's
      # `origin/captures/daily` still read 137a468 while the remote had 623728b, pushed the day
      # before from the main checkout -- a guaranteed daily false alarm with no way for the
      # reader to tell it from the real thing. So before alarming, ask the remote itself.
      dur_remote="${AIHOT_CAPTURE_PUSH_REMOTE-origin}"
      dur_ref="${AIHOT_CAPTURE_PUSH_REF:-refs/heads/captures/daily}"
      dur_sha=""
      if [ -n "$dur_remote" ]; then
        dur_sha="$(bounded "${AIHOT_CAPTURE_NET_TIMEOUT:-300}" git -C "$AIHOT_REFERENCE_PATH" ls-remote "$dur_remote" "$dur_ref" 2>/dev/null | awk 'NR==1{print $1}')"
      fi
      if [ -n "$dur_sha" ] && git -C "$AIHOT_REFERENCE_PATH" merge-base --is-ancestor HEAD "$dur_sha" 2>/dev/null; then
        echo "  submodule durable: $sub_head is reachable from $dur_remote/$dur_ref on the remote"
        echo "           (local remote-tracking ref was stale; not an alarm)"
      else
        echo "  WARNING: submodule commit $sub_head is on no remote-tracking ref, and the remote"
        echo "           does not have it either (read back: ${dur_sha:-<unreadable>})."
        echo "           It exists only in this worktree's private gitdir; \`git worktree remove\`"
        echo "           destroys it, no other checkout can fetch it, and every consumer of"
        echo "           $AIHOT_REFERENCE_PATH keeps reading the last pushed capture with no signal."
        echo "           This run's own push either was skipped or failed -- see above. To recover:"
        # `${dur_remote:-origin}`, not `$dur_remote`: with pushing deliberately off the variable is
        # empty, and printing it bare hands the reader `git push  HEAD:...` -- a command that fails
        # with an unhelpful error. The recovery line has to be runnable in every arm that prints it.
        echo "             git -C \"$AIHOT_REFERENCE_PATH\" push ${dur_remote:-origin} HEAD:$dur_ref"
        echo "           If that says 'Permission denied (publickey)', it is the missing ssh-agent,"
        echo "           not a key problem: log in so launchd holds an agent (see ADR-013)."
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
sub_dirt="$(git -C "$AIHOT_REFERENCE_PATH" status --porcelain)"
if [ -n "$sub_dirt" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === NOTE: uncommitted content in the dataset submodule ==="
  printf '%s\n' "$sub_dirt" | sed 's/^/    /'
fi
exit $rc
