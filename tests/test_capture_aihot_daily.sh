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
# Overridable so a reverse mutation can be pointed at a modified copy. Without this, an
# attempted mutation runs the real script and reports green -- indistinguishable from a
# mutation the tests genuinely caught. Measured: both directions of the gate "passed".
SCRIPT="${SCRIPT:-$(cd "$(dirname "$0")/.." && pwd)/scripts/capture_aihot_daily.sh}"
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
# The durability check is OFF for these cases by default: the fixture's submodule is never
# pushed, so it would fire in every one of them for a reason none of them is about. Case 19
# below turns it back on and covers both of its arms.
run(){ ( cd "$1/tool" && AIHOT_CAPTURE_WORKTREE="$1/tool" AIHOT_CAPTURE_LOG_DIR="$1/tool/logs" \
         AIHOT_CAPTURE_DURABILITY_CHECK="${AIHOT_CAPTURE_DURABILITY_CHECK:-0}" \
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

echo "8. target_exists is a real failure whenever this job runs alone -- including, and this is"
echo "   the case the old branch passed, when the window it names IS already on disk."
echo "   A branch here used to pass exactly that case as a benign steady state, on the premise"
echo "   that it meant \"AIHOT has not advanced its canonical window yet\". The premise is false:"
echo "   aihot_dataset.py:3799 rejects a request that is not verbatim the server's current"
echo "   canonical pair, so reaching the publish step proves the server DID advance -- the error"
echo "   means we collided with our own history and dropped the pair's new day. (The one benign"
echo "   shape is two runs racing each other to publish; no lock guards it, deliberately.)"
echo "   That branch had 22 green tests and two red reverse mutations; they encoded the same"
echo "   wrong premise as the code, so this case is the regression guard, not those."
r=$(setup); rc=$(run "$r" 'bash -c "echo \"ERROR target_exists: refusing to overwrite windows/w1\"; echo \"Choose a new output path and retry; existing files are never overwritten.\"; exit 2" --')
check "a window already on disk still fails" "$rc" "2"
check "nothing is logged as a skip"          "$( grep -c 'SKIP' "$r"/tool/logs/*.log )" "0"
check "retention still ran"                  "$( [ -d "$r/tool/benchmarks/aihot/captures/aihot-20260101T000000Z" ] && echo present || echo pruned )" "pruned"
check "clean at exit"                        "$(dirt "$r")" "0"
rm -rf "$r"

# The overlap those refusals came from is now prevented at the request instead. Both directions
# of that gate are pinned: 9 asserts it blocks, 10 asserts it does not block. One without the
# other reads the same whether the predicate works or is simply always true.
d(){ date -u -v"$1"d +%Y-%m-%dT000000Z 2>/dev/null || date -u -d "${1#-} days ago" +%Y-%m-%dT000000Z; }

echo "9. the canonical pair overlaps what is published -> no request is made at all."
echo "   Publishing is all-or-nothing (aihot_dataset.py:3879 raises if ANY publish root exists),"
echo "   so asking anyway would fetch the whole surface for 33 minutes and store nothing."
echo "   Nothing is lost: tomorrow's pair starts where our history ends. The corpus therefore"
echo "   trails by at most one day, which is structural -- AIHOT only offers day T-1 paired with"
echo "   T-2 -- not something this gate introduced."
r=$(setup); w="$r/tool/benchmarks/aihot/windows/$(d -1)--$(d -0)"; mkdir -p "$w"
echo x > "$w/items.jsonl"; echo '{}' > "$w/manifest.json"
rc=$(run "$r" 'bash -c "exit 0" --')
check "exits 0"                       "$rc" "0"
check "says it made no request"       "$( grep -c 'no request: windows already cover through' "$r"/tool/logs/*.log )" "1"
check "the capture tool was NOT run"  "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "0"
check "retention still ran"           "$( [ -d "$r/tool/benchmarks/aihot/captures/aihot-20260101T000000Z" ] && echo present || echo pruned )" "pruned"
# The message is not echoed to the log, so read it off the commit itself -- grepping the log
# here silently passed as 0 and had to be traced by hand.
check "retention deletions committed" "$( git -C "$r/tool/benchmarks/aihot" log -1 --format=%s )" "chore(aihot): retention on a no-request day"
check "clean at exit"                 "$(dirt "$r")" "0"
rm -rf "$r"

echo "10. history ending exactly AT the pair's start does not overlap -- the request is made."
echo "    This is the day the gate must not eat; without it, 9 would pass on a predicate that"
echo "    simply always refuses and the job would never capture anything again."
r=$(setup); w="$r/tool/benchmarks/aihot/windows/$(d -3)--$(d -2)"; mkdir -p "$w"
echo x > "$w/items.jsonl"; echo '{}' > "$w/manifest.json"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "the request was made"   "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                "$rc" "0"
check "clean at exit"          "$(dirt "$r")" "0"
rm -rf "$r"

# 11 and 12 are the same defect in two shapes: the gate fails in the worst direction, because
# anything it mistakes for published history suppresses the request silently and forever. A name
# is not a publication. Both were found by an independent reviewer, not by these tests.
echo "11. a window directory with no manifest.json is NOT published history."
echo "    The publish step writes the manifest; an empty or half-removed directory carries the"
echo "    same name as a validated window, and counting it would eat that day for good."
r=$(setup); mkdir -p "$r/tool/benchmarks/aihot/windows/$(d -1)--$(d -0)"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "does not suppress the request" "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                       "$rc" "0"
rm -rf "$r"

echo "12. a date-SHAPED name that is not a date must not pin the gate shut."
echo "    9999-99-99 sorts above every real window, so a single such directory would stop this"
echo "    job requesting anything ever again -- and it would exit 0 every day while doing it."
r=$(setup); bad="$r/tool/benchmarks/aihot/windows/2026-01-01T000000Z--9999-99-99T000000Z"
mkdir -p "$bad"; echo x > "$bad/items.jsonl"; echo '{}' > "$bad/manifest.json"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "does not suppress the request" "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                       "$rc" "0"
rm -rf "$r"

echo "14. a SYMLINK to a directory that holds a manifest is not published history."
echo "    Path.is_dir() follows symlinks, so the manifest check alone would pass a link to any"
echo "    directory. Found by the reviewer, not by cases 11-12."
# BOTH files in the decoy, or this case is rejected for the missing items.jsonl instead and
# proves nothing about the symlink predicate. Reviewer caught that the first version did not
# isolate it -- so the mutation reading it produced was not evidence.
r=$(setup); real="$r/decoy"; mkdir -p "$real"
echo x > "$real/items.jsonl"; echo '{}' > "$real/manifest.json"
ln -s "$real" "$r/tool/benchmarks/aihot/windows/$(d -1)--$(d -0)"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "does not suppress the request" "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                       "$rc" "0"
rm -rf "$r"

echo "15. a bogus START date invalidates the name even when the END date is a real day."
echo "    The gate only reads the end, so validating only that leaves a name no publish step"
echo "    ever wrote counting as history."
# Both files present, so this case isolates the START-date check: without items.jsonl it would
# pass for the wrong reason and stay green if that check were deleted.
r=$(setup); bad="$r/tool/benchmarks/aihot/windows/2026-99-99T000000Z--$(d -0)"
mkdir -p "$bad"; echo x > "$bad/items.jsonl"; echo '{}' > "$bad/manifest.json"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "does not suppress the request" "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                       "$rc" "0"
rm -rf "$r"

echo "16. a 99:99:99 TIME component must not pin the gate shut either."
echo "    It passes a digit-count check and sorts above every real instant. Same hole as 12 and"
echo "    14, third face: every unvalidated field in this name fails toward permanent silence."
r=$(setup); bad="$r/tool/benchmarks/aihot/windows/2026-09-01T000000Z--2026-09-11T999999Z"
mkdir -p "$bad"; echo x > "$bad/items.jsonl"; echo '{}' > "$bad/manifest.json"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "does not suppress the request" "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                       "$rc" "0"
rm -rf "$r"

echo "17. a half-published window (manifest but no items) is not history."
echo "    Publish writes both; a tree with one of them is a partial or half-removed write."
r=$(setup); half="$r/tool/benchmarks/aihot/windows/$(d -1)--$(d -0)"
mkdir -p "$half"; echo '{}' > "$half/manifest.json"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "does not suppress the request" "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                       "$rc" "0"
rm -rf "$r"

echo "18. two real dates that are not ONE DAY apart do not name a canonical window."
echo "    2026-01-01--2099-01-01 passes every other check and would skip every request until"
echo "    2099. Fifth face of the same hole; found by the reviewer after cases 12/14/15/16/17."
r=$(setup); wide="$r/tool/benchmarks/aihot/windows/2026-01-01T000000Z--2099-01-01T000000Z"
mkdir -p "$wide"; echo x > "$wide/items.jsonl"; echo '{}' > "$wide/manifest.json"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "does not suppress the request" "$( grep -c 'requesting ' "$r"/tool/logs/*.log )" "1"
check "exits 0"                       "$rc" "0"
rm -rf "$r"

echo "13. failing to persist must not report success. Until these objects reach a second place"
echo "    they exist only in this worktree's private gitdir; a run that fetched an irrecoverable"
echo "    day and then could not commit it is a failure, not a success."
# benchmarks/aihot/.git is a gitlink FILE, so chmod on it locks nothing -- the objects live in
# $r/tool/.git/modules/. An index.lock in the real gitdir is what actually makes git refuse.
r=$(setup); : > "$r/tool/.git/modules/benchmarks/aihot/index.lock"
rc=$(run "$r" 'bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --')
check "persist failure is non-zero"  "$( [ "$rc" != 0 ] && echo nonzero || echo zero )" "nonzero"
check "and it says so"               "$( grep -cE 'WARNING: (git add failed|submodule commit failed)' "$r"/tool/logs/*.log )" "1"
rm -rf "$r"

echo "19. the capture must reach a second place, and the gitlink must not outrun it."
echo "    2026-09-13: the commit sat on a detached submodule HEAD in this worktree's private"
echo "    gitdir, rc was 0, and every consumer kept reading the previous capture for two days."
echo "    ADR-20260913-c7d4 authorises the daily push; ADR-060's ORDER still binds, so a failed"
echo "    push must leave the superproject pin where it was rather than pinning an orphan."
stub='bash -c "mkdir -p benchmarks/aihot/captures/aihot-20260908T000000Z; echo x > benchmarks/aihot/captures/aihot-20260908T000000Z/p.json" --'

# (a) push turned off -- the pre-c7d4 world. The durability check is the only thing standing
#     between that state and a silent two days, so it must fire and name the way out.
r=$(setup); before=$( cd "$r/tool" && git rev-parse HEAD )
rc=$(AIHOT_CAPTURE_DURABILITY_CHECK=1 AIHOT_CAPTURE_PUSH_REMOTE= run "$r" "$stub")
after=$( cd "$r/tool" && git rev-parse HEAD )
check "push off: non-zero"         "$( [ "$rc" != 0 ] && echo nonzero || echo zero )" "nonzero"
check "push off: names recovery"   "$( cat "$r"/tool/logs/*.log | grep -c 'push origin HEAD:refs/heads/captures/daily' )" "1"
# The recovery line must be RUNNABLE in this arm too. With pushing off the remote variable is
# empty, and an earlier revision printed `git push  HEAD:...` -- a command the 2am reader would
# copy and get an unhelpful error from. Assert no double space where the remote belongs.
check "push off: recovery has a remote" "$( cat "$r"/tool/logs/*.log | grep -c 'push  HEAD:' )" "0"
# The assertion this case was MISSING, and an independent reviewer found the bug it hid: the
# guard read `[ -n "$pushed" ]`, which `pushed=skipped` satisfies, so a run with pushing turned
# off still pinned the gitlink at a SHA no remote had. rc was non-zero anyway -- from the
# unrelated durability check -- so both assertions above passed while the orphan was created.
check "push off: no gitlink"       "$( [ "$before" = "$after" ] && echo unchanged || echo pinned )" "unchanged"
rm -rf "$r"

# (b) the shipped default: push, verify the remote exact ref, then pin.
r=$(setup)
rc=$(AIHOT_CAPTURE_DURABILITY_CHECK=1 run "$r" "$stub")
check "push on: exits 0"           "$rc" "0"
check "push on: verified"          "$( cat "$r"/tool/logs/*.log | grep -c 'submodule push: .* (verified)' )" "1"
check "push on: landed on remote"  "$( cd "$r/data" && git rev-parse --verify -q refs/heads/captures/daily >/dev/null && echo yes || echo no )" "yes"
rm -rf "$r"

# (c) THE order invariant. A push that fails must not be followed by the pointer commit: a pin
#     to a SHA that reached no remote is the orphan ADR-060's ordering exists to prevent, and it
#     is worse than no pin because `git submodule update` then fails for everyone.
r=$(setup); before=$( cd "$r/tool" && git rev-parse HEAD )
rc=$(AIHOT_CAPTURE_PUSH_REMOTE="$r/no-such-remote.git" run "$r" "$stub")
after=$( cd "$r/tool" && git rev-parse HEAD )
check "push fails: non-zero"       "$( [ "$rc" != 0 ] && echo nonzero || echo zero )" "nonzero"
check "push fails: no gitlink"     "$( [ "$before" = "$after" ] && echo unchanged || echo pinned )" "unchanged"
check "push fails: says why"       "$( cat "$r"/tool/logs/*.log | grep -c 'Not recording the gitlink' )" "1"
rm -rf "$r"

# (d) push reports success but the remote ref is NOT this commit. ADR-060 asks for the remote
#     EXACT ref, not a zero exit, and this is why: a hook or a racing writer can leave the branch
#     somewhere else, which reads as durable and is not. Simulated with a post-receive hook that
#     moves the ref back. This is a fair unit test of the ls-remote branch (receive-pack
#     answers the client only after post-receive runs, so the push itself still succeeds),
#     but it is NOT a real concurrent publisher: a real race usually shows up one branch
#     earlier, as a non-fast-forward rejection. It proves the check reads the remote, not
#     that concurrency was reproduced.
r=$(setup)
other=$( cd "$r/data" && git rev-parse HEAD )
mkdir -p "$r/data/.git/hooks"
printf '#!/bin/sh\ngit update-ref refs/heads/captures/daily %s\n' "$other" > "$r/data/.git/hooks/post-receive"
chmod +x "$r/data/.git/hooks/post-receive"
before=$( cd "$r/tool" && git rev-parse HEAD )
rc=$(run "$r" "$stub")
after=$( cd "$r/tool" && git rev-parse HEAD )
check "ref moved: non-zero"        "$( [ "$rc" != 0 ] && echo nonzero || echo zero )" "nonzero"
check "ref moved: no gitlink"      "$( [ "$before" = "$after" ] && echo unchanged || echo pinned )" "unchanged"
check "ref moved: says who wrote" "$( cat "$r"/tool/logs/*.log | grep -c 'Something else is writing that ref' )" "1"
rm -rf "$r"

# (e) push reports success but the ref cannot be read back at all -- the branch the wording split
#     created. Simulated with a post-receive hook that DELETES the ref; observationally the same
#     empty `ls-remote` result as an unreachable remote or a timed-out read. It must be told apart
#     from (d) in the log: (e) is usually transient and retries itself, (d) needs a human to find
#     the other writer, and one shared sentence would send the 2am reader down the wrong path.
r=$(setup)
mkdir -p "$r/data/.git/hooks"
printf '#!/bin/sh\ngit update-ref -d refs/heads/captures/daily\n' > "$r/data/.git/hooks/post-receive"
chmod +x "$r/data/.git/hooks/post-receive"
before=$( cd "$r/tool" && git rev-parse HEAD )
rc=$(run "$r" "$stub")
after=$( cd "$r/tool" && git rev-parse HEAD )
check "unreadable: non-zero"       "$( [ "$rc" != 0 ] && echo nonzero || echo zero )" "nonzero"
check "unreadable: no gitlink"     "$( [ "$before" = "$after" ] && echo unchanged || echo pinned )" "unchanged"
check "unreadable: says transient" "$( cat "$r"/tool/logs/*.log | grep -c 'Usually transient' )" "1"
check "unreadable: not confused with (d)" "$( cat "$r"/tool/logs/*.log | grep -c 'Something else is writing' )" "0"
rm -rf "$r"

echo "20. bounded must terminate the command's CHILDREN too, not just the command. Found by an"
echo "    independent reviewer after the first version shipped: \`git push\` forks \`ssh\`, and a"
echo "    TERM that reaches only git leaves ssh running -- on this host possibly still sitting on"
echo "    a Keychain dialog. The job stops waiting either way, so nothing in the suite noticed;"
echo "    the earlier hand-check used \`sleep 30\`, a leaf with no children, and could not see it."
r=$(mktemp -d); pidfile="$r/child.pid"
(
  eval "$(sed -n '/^bounded() {/,/^}/p' "$SCRIPT")"
  bounded 2 sh -c "sleep 30 & echo \$! > $pidfile; wait"
) >/dev/null 2>&1
child=$(cat "$pidfile" 2>/dev/null)
sleep 1
check "bounded: spawns a child"     "$( [ -n "$child" ] && echo yes || echo no )" "yes"
check "bounded: child terminated"   "$( kill -0 "$child" 2>/dev/null && echo alive || echo gone )" "gone"
rm -rf "$r"

echo "21. a STALE local remote-tracking ref must not be read as 'not durable'. Measured in"
echo "    production 2026-09-14: the capture worktree's origin/captures/daily still read"
echo "    137a468 while the remote had 623728b, pushed the day before from another checkout"
echo "    of the same submodule. Local remote-tracking refs are a cache, not the remote, so"
echo "    the check alarmed every day about data that was in fact durable -- and the reader"
echo "    had no way to tell that from the real thing."
r=$(setup)
rc1=$(AIHOT_CAPTURE_DURABILITY_CHECK=1 run "$r" "$stub")           # pushes; ref now current
base=$( cd "$r/data" && git rev-parse HEAD )
( cd "$r/tool/benchmarks/aihot" && git update-ref refs/remotes/origin/captures/daily "$base" )  # go stale
rc2=$(AIHOT_CAPTURE_DURABILITY_CHECK=1 run "$r" 'true --')         # nothing staged, stale ref
check "stale ref: still exits 0"   "$rc2" "0"
check "stale ref: says stale"      "$( cat "$r"/tool/logs/*.log | grep -c 'local remote-tracking ref was stale' )" "1"
check "stale ref: no false alarm"  "$( cat "$r"/tool/logs/*.log | grep -c 'is on no remote-tracking ref, and the remote' )" "0"
rm -rf "$r"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
