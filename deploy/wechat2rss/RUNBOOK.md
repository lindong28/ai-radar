# Wechat2RSS Runbook

Discovery layer for `kind="wechat"` sources, replacing the hosted Mp2RSS feed.

Upstream is **WeRead**, not the mp backend. That distinction is the whole reason
this route exists: cross-account article listing via `searchbiz + appmsg` was
restricted platform-wide around 2026-07-30 and no longer works for third-party
accounts (evidence in `plans/20260816-mp2rss-replacement/state.md`, ISSUE-001).

## Bring-up

```bash
cd deploy/wechat2rss
cp .env.example .env      # fill LIC_EMAIL, LIC_CODE, RSS_TOKEN
docker compose up -d
docker compose logs -f
```

Service listens on `127.0.0.1:8080` (loopback only — the admin UI accepts
subscription changes and must not be world-reachable).

## Host prerequisites (macOS / OrbStack)

1. **OrbStack must auto-start at login**, or the container is down after every
   reboot. OrbStack → Settings → General → "Start at login".
2. **`network_proxy` must not point at a dead port.** It was found set to
   `http://host.orbstack.internal:59527` while the agent-proxy tunnel was on
   59520; OrbStack transparently redirects all container TCP through that value,
   so every container had zero egress — including DNS-over-TCP. Set to `none`
   on 2026-08-17:

   ```bash
   orbctl config show | grep network_proxy   # expect: none
   orbctl config set network_proxy none
   ```

   Pinning it to the *current* port does not fix this: the agent-proxy tunnel
   binds `127.0.0.1` only, so the VM cannot reach it on any port, and the port
   itself is re-chosen on every reconnect. Containers reach WeRead, WeChat and
   the vendor registry directly, so no proxy is needed.

## First login (needs a phone, once)

The service crawls through a logged-in WeChat account's WeRead session.

**Before scanning**, the WeChat account must have authorized WeRead's article
feature at least once: in WeChat, open any Official Account article → Share →
"在微信读书中阅读" → complete the prompt. Skipping this produces a login that
succeeds but crawls nothing, and the symptom looks like risk-control.

Then in the admin UI: 微信账号 → 添加账号 → scan. Prefer a **frequently used**
WeChat account; the vendor notes those trip risk-control less often.

## Risk control

Expected, not a failure. The service backs off 15m → 30m → 60m → … capped at 6h,
and resets on recovery. Manual clear: in WeRead, 书架 → 文章收藏 → tap an account
**name** (not an article title) → follow the prompts.

Alerting: `BOT_WEBHOOK_URL` posts `{"msgtype":"text","text":{"content":"..."}}`.
That is the **企业微信** bot schema, not Feishu's
(`{"msg_type":"text","content":{"text":...}}`) — pointing it straight at the
Feishu webhook will be rejected. A translating endpoint is needed in between.

## Known coverage limits

- Only **群发** (broadcast) articles are collected. An account may publish
  without broadcasting; those appear on its profile page but never in the feed.
- Only the newest 20 articles are ever crawled. There is **no historical
  backfill** — `RSS_KEEP_OLD_COUNT=-1` preserves everything from the moment the
  subscription is added forward, but nothing before it.
- Vendor QA states each account is checked 1–2×/day with 0–24h delay; the
  "average 6h" figure on the pricing page is a different measurement (public
  service under 400+ accounts). Treat neither as our number until the shadow
  comparison reports one.

## Migration to another host

Copy the whole `deploy/wechat2rss` directory (including `data/`), then
`docker compose up -d` there. No re-login required.
