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

echo "requesting $START .. $END"

# Seam so the git-handling below can be exercised without spending a real AIHOT window.
# tests/test_capture_aihot_daily.sh substitutes a stub; nothing else sets it.
AIHOT_CAPTURE_CMD="${AIHOT_CAPTURE_CMD:-PYTHONPATH=src uv run python scripts/capture_aihot_dataset.py capture}"
eval "$AIHOT_CAPTURE_CMD --start \"$START\" --end \"$END\""
rc=$?

# An already-captured window is the expected steady state on a re-run, not a failure.
#
# `target_exists` 是这个稳态**最常见**的形态，而它此前不在匹配里：工具实际打印的是
# `ERROR target_exists: refusing to overwrite windows/...` 加 `existing files are never overwritten`,
# 而这里匹配的是 `existing capture`（词组不同）与 `already`（根本没出现）。于是每一天 AIHOT
# 还没推进 canonical 窗口时，这个良性稳态都以 rc=2 报成失败——2026-09-08 与 09-10 两次都是。
#
# 2026-09-10 的决定性读数：在 cron 时段之后近 9 小时手工重跑，它照样抓完整个 surface（33 分钟）
# 才在**写入**那一步拒绝同一个窗口 ⇒ **不是时段问题**（那条假设已被这次实验证伪），是 AIHOT
# 自己还没把 09-09→09-10 定为 canonical。**所以不要据此改 cron 时间。**
#
# 匹配 `target_exists` 这个机器 token 而不是它后面那句人话：token 由本仓自己的工具产出、是它的
# 契约的一部分；散文文案随时会改。
# 只放行**确实已经有那个窗口**的情形。光匹配 `target_exists` 会造出一个新沉默：日期算错而撞上
# 某个已存在的窗口，也会静默 exit 0——而那是真缺陷。错误里带着窗口路径，核它在不在盘上即可分开
# 「今天没有新窗口」与「它要写的窗口算错了」，代价是两行。
# **不 `exit 0`，只置一个标记落穿**。第一版在这里直接 `exit 0`，于是 retention 与收尾的脏树检查
# 一起被跳过——而这条分支是**最常见的那一天**（每天 AIHOT 没推进窗口时都走它）。两个后果各自
# 独立成立，都由 reviewer 实测：① 本文件第 99 行起那整段论证明写 retention「Runs whether or not
# today's capture succeeded…gating disk policy on network success is backwards」，2026-09-08 之后
# 装过一次这样的 guard 又因误读撤掉，而我把它按回来了、还按在默认路径上；② 收尾脏检查被跳过时，
# 树留脏而脚本报成功——**今天报成功、同时静默保证明天失败**，那正是本脚本与其测试存在的那个
# 不变量（脏的工具 checkout 会让明天的 capture 拒绝运行，且那一天不可回补）。
skipped=""
# **全部**命中都必须在盘上，不是 `tail -1` 挑一条。今天生产者每次运行最多 raise 一次、且 `$LOG`
# 是 per-run，故两者等价；但若它将来改成一次收集所有冲突，挑一条存在的会把另一条真缺陷吞掉。
skip_windows="$(sed -n 's/.*target_exists: refusing to overwrite \(windows\/[^ ]*\).*/\1/p' "$LOG")"
if [ $rc -ne 0 ] && [ -n "$skip_windows" ]; then
  all_present=1
  while IFS= read -r w; do
    [ -n "$w" ] || continue
    [ -e "benchmarks/aihot/$w" ] || all_present=0
  done <<<"$skip_windows"
  if [ "$all_present" = 1 ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] === aihot capture SKIP: $(echo "$skip_windows" | tr '\n' ' ')已在盘上，AIHOT 尚未推进 canonical 窗口 ==="
    skipped=1
  fi
fi
# 原先这里还有一条 `grep -q "existing capture\|already"`。**已删除，不是扩写**：
# `existing capture` 在生产者里根本不存在（grep 0 命中），而 `already` 出现在两条 `target_exists`
# 的 detail 里（`staged artifact already exists` / `capture target or staging target already exists`），
# 那两条正是"要写的目标算错/撞上"这一类——它们过那条无任何盘上校验的分支时会静默 exit 0。
# 也就是说：我为新分支写下的「光匹配 token 会造出新沉默」这句话，**逐字适用于它，而它更宽**
# （对一份 33 分钟全 surface 日志做子串匹配）。真正的良性文本已由上面那条带校验的分支接管，
# 留着它只提供误放行、不提供任何放行能力。9 份真实日志对它的命中数是 0/9。
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
# 良性稳态归零，**但只在收尾检查没发现脏树时**：脏树会挡住明天的 capture、而那一天不可回补，
# 所以它必须继续非零报出去，即便今天没有新窗口本身是正常的。两件事都要成立才算"今天没问题"。
if [ -n "$skipped" ] && [ "$worktree_dirty" = 0 ]; then
  rc=0
fi
exit $rc
