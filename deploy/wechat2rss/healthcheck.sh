#!/usr/bin/env bash
# External liveness check for the wechat2rss container.
#
# Deliberately external rather than using the service's own BOT_WEBHOOK_URL:
# a service that has crashed cannot send its own alert, and "the WeChat source
# stopped producing and nobody noticed for three days" is the exact failure this
# is here to prevent (see plans/20260816-mp2rss-replacement/state.md ISSUE-008).
#
# Covers three distinct terminal states, not just the happy path:
#   unreachable    — container down, port gone, or service wedged
#   login invalid  — WeRead session died; needs a QR re-scan
#   risk control   — WeChat throttling; usually self-clears, needs a phone tap
#                    if it persists
#
# Exit 0 = healthy, 1 = a problem was found and alerted.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

[[ -f .env ]] || { echo "no .env"; exit 1; }
TOKEN="$(grep '^RSS_TOKEN=' .env | cut -d= -f2-)"
[[ -n "$TOKEN" ]] || { echo "no RSS_TOKEN in .env"; exit 1; }

alert() {  # $1 = dedup key suffix, $2 = message
  echo "$2"
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
import json, sys, subprocess

def alert(key, msg):
    print(msg)
    subprocess.run(['im-notify','--alert','--dedup-key',f'wechat2rss-{key}',msg],
                   capture_output=True)
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
