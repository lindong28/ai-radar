# Wechat2RSS Runbook

Self-hosted discovery layer for `kind="wechat"` sources. AI Radar currently runs it alongside Mp2RSS and takes the cross-source union rather than treating either feed as a complete replacement.

Upstream is **WeRead**, not the mp backend. That distinction is the whole reason this route exists: cross-account article listing via `searchbiz + appmsg` was restricted platform-wide around 2026-07-30 and no longer works for third-party accounts; the retired discovery path and evidence boundary are summarized in [`061-wechat-discovery`](../../docs/adr/061-deprecate-wechat-admin-discovery-line.md).

## Bring-up

```bash
cd deploy/wechat2rss
cp .env.example .env      # fill LIC_EMAIL, LIC_CODE, RSS_TOKEN
docker compose up -d
docker compose ps
curl -sf http://127.0.0.1:8080/ >/dev/null && echo admin_ui_ok
./logs.sh --since 10m
```

`admin_ui_ok` is the pre-login bring-up signal. It proves the loopback UI answered; account usability is checked separately with `./healthcheck.sh` after the first login.

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

Open `http://127.0.0.1:8080`, then in the admin UI choose 微信账号 → 添加账号 → scan. Prefer a **frequently used** WeChat account; the vendor notes those trip risk-control less often. After login, run `./healthcheck.sh`; success is a terminal line shaped as `healthy: N 个账号可用，均未风控` with exit code 0.

## Connect and operate AI Radar

Set the following in the AI Radar repository root `.env` on the same host; replace `<RSS_TOKEN>` with the value from this directory's `.env`:

```bash
WECHAT2RSS_FEED_URL=http://127.0.0.1:8080/feed/all.xml?k=<RSS_TOKEN>
```

The feed URL is host-local and must not go in a cross-machine shared env file. Its response semantics, cross-source union, and deduplication contract are maintained in [the WeChat ingestion runbook](../../docs/operations/wechat-ingestion.md#双跑wechat2rss_feed_url-与跨源去重). Service health is not consumer verification: after setting the root `.env`, complete that runbook's [AI Radar-side fetch and database checks](../../docs/operations/wechat-ingestion.md#验证) before concluding that the feed is connected.

Use these lifecycle and verification entries from `deploy/wechat2rss/`:

```bash
docker compose ps                 # read-only container status
./healthcheck.sh                  # exit 0 plus healthy: ... means the logged-in accounts are usable
./logs.sh --since 10m             # redacted logs; never use raw docker compose logs
docker compose down               # stop and remove the container; persistent data remains in ./data
docker compose pull
docker compose up -d              # make image or .env/compose changes live
./healthcheck.sh                  # post-change terminal check
```

## Risk control

Expected, not a failure. The service backs off 15m → 30m → 60m → … capped at 6h,
and resets on recovery. Manual clear: in WeRead, 书架 → 文章收藏 → tap an account
**name** (not an article title) → follow the prompts.

Repository-owned alerting deliberately does not use the service's `BOT_WEBHOOK_URL`: the external `healthcheck.sh` can still notify through `im-notify --alert` when the container itself is down. If you independently enable the vendor webhook, it posts the **企业微信** schema `{"msgtype":"text","text":{"content":"..."}}`, not Feishu's `{"msg_type":"text","content":{"text":...}}`; a translating endpoint is required between it and a Feishu webhook.

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

Copy the whole `deploy/wechat2rss` directory (including `data/`), run `docker compose up -d` there, and finish with `./healthcheck.sh`. Exit 0 plus the `healthy: ...` terminal line is the migration terminal check; no re-login is required when the copied data is valid.
