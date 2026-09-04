#!/usr/bin/env bash
# External liveness check for the wechat2rss container.
#
# Deliberately external rather than using the service's own BOT_WEBHOOK_URL:
# a service that has crashed cannot send its own alert, and "the WeChat source
# stopped producing and nobody noticed for three days" is the exact failure this
# is here to prevent (see plans/20260816-mp2rss-replacement/state.md ISSUE-008).
#
# Covers five distinct terminal states, not just the happy path:
#   unreachable    — container down, port gone, or service wedged
#   apierr         — the service answered with an error payload
#   noaccount      — no WeChat account is logged in at all
#   login invalid  — WeRead session died; needs a QR re-scan
#   risk control   — WeChat throttling; usually self-clears, needs a phone tap
#                    if it persists
#
# Exit 0 = healthy, 1 = a problem was found and alerted.
#
# Delivery goes through `im-notify --alert --dedup-key wechat2rss-<kind>`, and
# that dedup is exact-text: once a key has fired, an identical recurrence is
# skipped until the key is cleared. So the healthy path must clear every key,
# or the first firing under a key (a deliberate exercise of the failure branch
# counts) suppresses every later real one. That is how the 2026-08-30 → 09-04
# outage went undelivered: 335 sends, all `skipped(unchanged)` against the
# signature left by the 2026-08-17 exercise (plans/20260816-mp2rss-replacement/
# state.md ISSUE-016).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

[[ -f .env ]] || { echo "no .env"; exit 1; }
TOKEN="$(grep '^RSS_TOKEN=' .env | cut -d= -f2-)"
[[ -n "$TOKEN" ]] || { echo "no RSS_TOKEN in .env"; exit 1; }

# One dedup key per problem identity (listed in docs/operations/services.md).
KEYS=(unreachable apierr noaccount login riskctl)
# The resolve has its own key, and its text carries the incident's first
# observation time: a resolve repeated for the *same* incident (state file not
# committable) is suppressed as unchanged, while the next incident's resolve
# differs by that timestamp and delivers without anyone having to clear the key.
RECOVERED_KEY=wechat2rss-recovered

mark_firing() {  # $1 = kind; keep the first observation time across repeats
  local cur; cur="$(cat "$STATE_FILE" 2>/dev/null || true)"
  [[ "$cur" == "firing $1 "* ]] && return 0
  printf 'firing %s %s\n' "$1" "$(date -u +%Y-%m-%dT%H:%MZ)" >"$STATE_FILE" 2>/dev/null
}
# Last outcome as seen by this probe, so the healthy path can tell "recovered"
# from "still fine". Sits beside the container's persistent data (gitignored).
# Unwritable state only costs the recovery notice; firing and clearing do not
# depend on it. Granularity is one probe: a recover-and-relapse that fits
# entirely between two cron runs is never observed, so it reads as one
# continuing incident (the operator was told it is down, and it is).
STATE_FILE="$PWD/data/healthcheck.state"
export STATE_FILE

alert() {  # $1 = dedup key suffix, $2 = message
  echo "$2"
  mark_firing "$1"
  im-notify --alert --dedup-key "wechat2rss-$1" "$2" >/dev/null 2>&1 \
    || echo "WARN: im-notify failed to deliver: $2"
  exit 1
}

# --noproxy: the feed host is loopback; an inherited proxy would black-hole it.
# Overridable so the unreachable path can actually be exercised; a health check
# whose failure branch has never been run is not known to work.
BASE_URL="${WECHAT2RSS_BASE_URL:-http://127.0.0.1:8080}"

body="$(curl -sS --noproxy '*' --max-time 25 "$BASE_URL/login/list?k=$TOKEN" 2>&1)" \
  || alert unreachable "wechat2rss 无响应：微信文章发现已停止。检查容器：cd deploy/wechat2rss && docker compose ps"

python3 - "$body" <<'PY' || exit 1
import json, os, sys, subprocess, time

def alert(key, msg):
    print(msg)
    try:  # same contract as mark_firing above: keep the first observation time
        state = os.environ['STATE_FILE']
        try:
            cur = open(state).read()
        except OSError:
            cur = ''
        if not cur.startswith(f'firing {key} '):
            with open(state, 'w') as f:
                f.write(f'firing {key} {time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())}\n')
    except (KeyError, OSError):
        pass
    try:
        r = subprocess.run(['im-notify','--alert','--dedup-key',f'wechat2rss-{key}',msg],
                           capture_output=True)
        failed = r.returncode != 0
    except OSError:
        failed = True
    if failed:
        print(f'WARN: im-notify failed to deliver: {msg}')
    sys.exit(1)

try:
    d = json.loads(sys.argv[1])
except Exception:
    alert('unreachable', 'wechat2rss 返回了无法解析的响应，微信文章发现可能已停止')

if d.get('err'):
    alert('apierr', f"wechat2rss API 报错：{d['err']}")

accounts = d.get('data') or []
if not accounts:
    alert('noaccount', 'wechat2rss 没有任何已登录微信账号，抓取已停止，需要重新扫码登录')

dead = [a for a in accounts if not a.get('available')]
if dead:
    names = ', '.join(str(a.get('name') or a.get('id')) for a in dead)
    alert('login', f"wechat2rss 微信账号登录失效：{names}。需要重新扫码（服务无法自行恢复）")

throttled = [a for a in accounts if a.get('needCheck')]
if throttled:
    names = ', '.join(f"{a.get('name') or a.get('id')}(重试 {a.get('waitTime')})" for a in throttled)
    alert('riskctl', f"wechat2rss 账号处于微信风控中：{names}。会自动退避重试；若持续，在微信读书里打开 书架→文章收藏→点公众号名称")

print(f"healthy: {len(accounts)} 个账号可用，均未风控")
PY

# Healthy. Clear every key so the next real recurrence delivers again
# (--dedup-clear is idempotent), then one explicit resolve on a firing→healthy
# transition so the reader knows the incident ended. The state is committed to
# "healthy" only after both succeeded: a failed clear or a failed resolve leaves
# it as it was, so the next healthy run retries instead of silently accepting
# a stale signature (a retried resolve is deduplicated under RECOVERED_KEY; a
# stale signature costs the next outage).
prev="$(cat "$STATE_FILE" 2>/dev/null || true)"
clear_failed=""
for k in "${KEYS[@]}"; do  # try every key; one failure must not shadow the rest
  im-notify --dedup-clear "wechat2rss-$k" >/dev/null 2>&1 || clear_failed="$clear_failed wechat2rss-$k"
done
if [[ -n "$clear_failed" ]]; then
  echo "WARN: im-notify --dedup-clear failed for:$clear_failed; state left as-is for retry"
  exit 0
fi
if [[ "$prev" == firing* ]]; then
  read -r _ kind since <<<"$prev"
  msg="wechat2rss 已恢复：容器可达、微信账号可用且未风控（此前告警：${kind}，探针自 ${since:-?} 起观察到）。无需动作。这只证明服务侧健康；AI Radar 是否重新入库，以下一轮 pipeline 日志的 wx_wechat2rss 行为准。"
  echo "$msg"
  im-notify --alert --dedup-key "$RECOVERED_KEY" "$msg" >/dev/null 2>&1 \
    || { echo "WARN: im-notify failed to deliver the recovery notice; state left as-is for retry"; exit 0; }
fi
printf 'healthy\n' >"$STATE_FILE" 2>/dev/null \
  || echo "WARN: cannot write $STATE_FILE; the resolve is deduplicated under $RECOVERED_KEY until it is writable"
exit 0
