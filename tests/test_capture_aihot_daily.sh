#!/bin/bash
# The invariant that matters: a run ends with a clean worktree. Everything the daily capture job
# can leave behind -- a fresh untracked capture, the tracked deletions retention makes, a
# `.staging` tree from a capture killed mid-flight, a half-published capture, a stray file --
# must not block tomorrow's run, because the capture refuses a dirty tool checkout and a missed
# day is unrecoverable after AIHOT's 7-day window closes.
#
# This exists because the manual check that "verified" the first fix exercised exactly one state
# (clean tree, one fresh capture, nothing old enough to prune) and read 0 dirty lines. That
# reading was true and had no predictive value: the very next state -- retention deleting a
# tracked capture -- put the deadlock straight back, and a single-state check on a state machine
# cannot tell those two apart.
set -uo pipefail
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/scripts/capture_aihot_daily.sh"
pass=0; fail=0
check(){ if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "  ok   $1"; else fail=$((fail+1)); echo "  FAIL $1: expected '$3', got '$2'"; fi; }

setup(){  # $1 = stub behaviour; echoes the worktree path
  root=$(mktemp -d); export GIT_CONFIG_GLOBAL=/dev/null
  git init -q "$root/data"; ( cd "$root/data"
    mkdir -p captures/aihot-20260101T000000Z windows/w1
    echo old > captures/aihot-20260101T000000Z/page.json    # older than any retention window
    echo w   > windows/w1/items.jsonl
    git add -A && git -c user.email=t@t -c user.name=t commit -qm base )
  git init -q "$root/tool"; ( cd "$root/tool"
    mkdir -p benchmarks src scripts logs
    git -c protocol.file.allow=always submodule -q add "$root/data" benchmarks/aihot 2>/dev/null
    cp "$SCRIPT" scripts/capture_aihot_daily.sh
    git add -A && git -c user.email=t@t -c user.name=t commit -qm base )
  echo "$root"
}
run(){ ( cd "$1/tool" && AIHOT_CAPTURE_WORKTREE="$1/tool" AIHOT_CAPTURE_LOG_DIR="$1/tool/logs" \
         AIHOT_CAPTURE_CMD="$2" GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t \
         bash scripts/capture_aihot_daily.sh >/dev/null 2>&1; echo $? ); }
dirt(){ ( cd "$1/tool" && git status --porcelain | wc -l | tr -d ' ' ); }

echo "1. fresh capture, plus a tracked capture old enough to prune"
r=$(setup); rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z benchmarks/aihot/windows/w2; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json; echo y > benchmarks/aihot/windows/w2/items.jsonl" --')
check "clean at exit"        "$(dirt "$r")" "0"
check "the stale capture was pruned" "$( [ -d "$r/tool/benchmarks/aihot/captures/aihot-20260101T000000Z" ] && echo present || echo pruned )" "pruned"
rm -rf "$r"

echo "2. capture killed mid-flight leaves a .staging tree"
r=$(setup); rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/.staging/x; echo p > benchmarks/aihot/.staging/x/partial; exit 2" --')
check "does not block tomorrow" "$(dirt "$r")" "0"
check "reports failure"         "$rc" "2"
rm -rf "$r"

echo "3. capture fails outright"
r=$(setup); rc=$(run "$r" 'bash -c "exit 2" --')
check "clean at exit"    "$(dirt "$r")" "0"
check "propagates rc"    "$rc" "2"
rm -rf "$r"

echo "4. half-published capture: a capture dir with no matching window"
r=$(setup); rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json; exit 2" --')
check "does not block tomorrow" "$(dirt "$r")" "0"
rm -rf "$r"

echo "5. a stray file under captures/ -- documented as NOT excluded; pinned so it stays known"
r=$(setup); rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json; echo junk > benchmarks/aihot/captures/stray.tmp" --')
check "clean at exit"                 "$(dirt "$r")" "0"
check "stray was committed, as documented" \
  "$( cd "$r/tool/benchmarks/aihot" && git show --name-only --format= HEAD | grep -c 'stray.tmp' )" "1"
rm -rf "$r"

echo "6. capture FAILS while a stale capture is on disk -- pruning is disk policy, not a"
echo "   reward for a successful fetch; an outage is when it matters most, and a full disk is"
echo "   itself a reason capture fails."
r=$(setup); rc=$(run "$r" 'bash -c "exit 2" --')
check "stale capture still pruned" "$( [ -d "$r/tool/benchmarks/aihot/captures/aihot-20260101T000000Z" ] && echo present || echo pruned )" "pruned"
check "clean at exit"              "$(dirt "$r")" "0"
rm -rf "$r"

echo "7. dirt the named paths do NOT cover must be reported, not swallowed"
r=$(setup); rc=$(run "$r" 'bash -c "echo junk > benchmarks/aihot/loose.tmp" --')
check "reported in the log"  "$( grep -c 'uncommitted content in the dataset submodule' "$r"/tool/logs/*.log )" "1"
check "names the actual file" "$( grep -c 'loose.tmp' "$r"/tool/logs/*.log )" "1"
check "but does not fail the run -- it no longer blocks tomorrow" "$rc" "0"
rm -rf "$r"

echo "8. AIHOT has not advanced its canonical window yet -- the tool refuses to overwrite a"
echo "   window that IS already on disk. That is the steady state on most days, not a failure."
echo "   Measured 2026-09-10: a manual re-run ~9h after the cron slot fetched the whole surface"
echo "   (33 min) and refused the same window, so the timing hypothesis is dead."
echo "   The two assertions after the exit code are the ones every other terminal-state case"
echo "   here already had, and their absence is exactly why the first version of this branch"
echo "   could skip retention and the exit-time dirty check unnoticed."
r=$(setup); rc=$(run "$r" 'bash -c "echo \"ERROR target_exists: refusing to overwrite windows/w1\"; echo \"Choose a new output path and retry; existing files are never overwritten.\"; exit 2" --')
check "benign steady state exits 0"      "$rc" "0"
check "names the window it skipped"      "$( grep -c 'SKIP: windows/w1' "$r"/tool/logs/*.log )" "1"
check "retention still ran"              "$( [ -d "$r/tool/benchmarks/aihot/captures/aihot-20260101T000000Z" ] && echo present || echo pruned )" "pruned"
check "clean at exit"                    "$(dirt "$r")" "0"
rm -rf "$r"

echo "8b. a refusal naming a window that is NOT on disk is a real defect (a date-computation"
echo "    bug colliding with something else) and must NOT be silently passed. The message is"
echo "    the verbatim line from logs/aihot-capture-20260910-103623.log, so the extraction is"
echo "    pinned against a real sample rather than a hand transcription."
r=$(setup); rc=$(run "$r" 'bash -c "echo \"ERROR target_exists: refusing to overwrite windows/2026-09-08T000000Z--2026-09-09T000000Z\"; exit 2" --')
check "unknown window still fails"   "$rc" "2"
check "and is not logged as a skip"  "$( grep -c 'SKIP:' "$r"/tool/logs/*.log )" "0"
rm -rf "$r"

echo "8c. a skip day that ALSO leaves the tree dirty must still fail: a dirty tool checkout"
echo "    makes tomorrow's capture refuse to run, and tomorrow is not recoverable. \"No new"
echo "    window today\" and \"nothing is wrong\" are two claims, not one."
r=$(setup); rc=$(run "$r" 'bash -c "echo oops > oops.tmp; echo \"ERROR target_exists: refusing to overwrite windows/w1\"; exit 2" --')
check "dirty tree overrides the skip"  "$rc" "2"
check "and the dirt is reported"       "$( grep -c 'worktree dirty at exit' "$r"/tool/logs/*.log )" "1"
rm -rf "$r"



echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
