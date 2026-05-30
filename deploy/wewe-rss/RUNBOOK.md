# WeWe RSS Runbook

> Deployment runbook for the local WeWe RSS bridge used by `kind="wechat"` sources.

## Purpose

WeWe RSS is the discovery layer for WeChat Official Account articles. It discovers account article URLs after a WeRead login. ai-radar then fetches each new `mp.weixin.qq.com/s/...` article itself with Playwright and stores full text only for internal pipeline use.

## Start / Stop

```bash
cd deploy/wewe-rss
cp .env.example .env
# edit WEWE_AUTH_CODE and proxy settings if needed
docker compose -f docker-compose.sqlite.yml up -d
docker compose -f docker-compose.sqlite.yml logs -f app
docker compose -f docker-compose.sqlite.yml down
```

The service is bound to `127.0.0.1:4000`.

## Keeping It Running

The plain `docker compose up -d` start above is fine for first-time bring-up, but it does NOT survive a Docker daemon restart, an OrbStack restart, or a macOS reboot in the same way that `live.aiplanet.ai-radar.serve` and `live.aiplanet.ai-radar.tunnel` do. There are two dependencies to satisfy:

1. **Docker daemon must auto-start at login**. OrbStack: open OrbStack → Settings → General → enable "Start at login". Docker Desktop: Preferences → General → "Start Docker Desktop when you log in". Without this, the launchd job below has nothing to talk to.
2. **WeWe RSS itself should be supervised by launchd**, matching the other ai-radar services so the same `launchctl` muscle memory works (`launchctl print gui/$UID/live.aiplanet.ai-radar.wewe`, `launchctl kickstart -k …`).

Install once:

```bash
cp deploy/launchd/ai-radar-wewe.plist.example deploy/launchd/ai-radar-wewe.plist
# Edit the two /path/to/ai-radar occurrences to your absolute repo path.
launchctl bootstrap gui/$UID deploy/launchd/ai-radar-wewe.plist
launchctl enable gui/$UID/live.aiplanet.ai-radar.wewe
launchctl kickstart -k gui/$UID/live.aiplanet.ai-radar.wewe
```

Verify:

```bash
launchctl print gui/$UID/live.aiplanet.ai-radar.wewe | rg 'state = |last exit'
docker ps --filter name=ai-radar-wewe-rss
curl -sf http://127.0.0.1:4000/ -o /dev/null && echo up
tail -n 20 /tmp/ai-radar-wewe.err
```

Notes:

- The plist runs `docker compose up` (foreground, no `-d`) so launchd can supervise the process directly. `KeepAlive=true` plus `ThrottleInterval=30` means launchd restarts the docker compose process on crash and waits 30 s between retries — enough for OrbStack to come up after login without spamming retries.
- `docker compose up` writes container logs to stdout, which launchd redirects to `/tmp/ai-radar-wewe.log` and `/tmp/ai-radar-wewe.err`. The container's own `restart: unless-stopped` policy still handles crashes *inside* the container; launchd handles the docker-daemon-not-running case.
- To uninstall: `launchctl bootout gui/$UID/live.aiplanet.ai-radar.wewe`.
- Health monitoring beyond "process alive" (e.g. periodic `curl /` check, alert if WeWe stops appearing in pipeline ingestion) is not in scope here — track that separately if it becomes a recurring failure mode.

## Required Environment

| Variable | Notes |
|---|---|
| `WEWE_AUTH_CODE` | Dashboard access code. Use a strong random value and do not commit `.env`. |
| `WEWE_CRON_EXPRESSION` | Current default is `7 */2 * * *`, so WeWe refreshes about every 2 hours. |
| `WEWE_SERVER_ORIGIN_URL` | Local origin, normally `http://localhost:4000`. |
| `WEWE_HTTP_PROXY` / `WEWE_HTTPS_PROXY` | Set to `http://host.docker.internal:59527` when WeRead egress needs the host proxy. |
| `WEWE_NO_PROXY` | Keep `localhost,127.0.0.1`. |

Do not set `FEED_MODE=fulltext` for this integration. ai-radar intentionally treats WeWe as discovery only and does its own full-text scrape.

## WeRead Login

1. Open `http://localhost:4000/dash/accounts`.
2. Enter `WEWE_AUTH_CODE` when prompted.
3. Click 「添加读书账号」 and scan the QR code with WeChat.
4. Confirm an enabled account row appears on the accounts page.

QR codes expire quickly. If login does not complete, generate a fresh QR and rescan.

## Add A WeChat Account

1. Open `http://localhost:4000/dash/sources`.
2. Add a representative article share link from the account, for example `https://mp.weixin.qq.com/s/...`.
3. Wait until the feed appears in the dashboard and `/feeds/all.rss`.
4. Record the per-feed URL: `http://localhost:4000/feeds/<feedId>.rss`.
5. Add that URL to `data/sources.toml` as `kind = "wechat"` and run `./run.sh admin sources reload`.

Space new account additions by about 10 minutes, or at least wait until the first account appears in `/feeds/all.rss`, to reduce upstream anti-abuse risk.

## Verify

```bash
curl -sf http://localhost:4000/ -o /dev/null && echo up
curl -s http://localhost:4000/feeds/all.rss | rg 'mp.weixin.qq.com/s/'
sqlite3 deploy/wewe-rss/data/wewe-rss.db "SELECT id, mp_name FROM feeds;"
```

The all-feed RSS may omit account names in RSS2 output; per-feed URLs and the WeWe SQLite `feeds` table are the source of truth for feedId/account mapping.

## Troubleshooting

- If Docker cannot pull or WeWe cannot reach WeRead, verify the host proxy at `127.0.0.1:59527` and set `WEWE_HTTP_PROXY` / `WEWE_HTTPS_PROXY` to `http://host.docker.internal:59527`.
- If adding a source initially shows no articles, retry after a short delay. The WeRead platform endpoint can return temporarily empty pages immediately after subscription.
- If ai-radar fetches `localhost:4000` through the shell HTTP proxy, it will fail with `RemoteProtocolError`. `fetcher/http_client.py` bypasses environment proxy settings for loopback URLs; keep WeWe URLs on `localhost` or `127.0.0.1`.
- Freshness is bounded by WeWe cron plus ai-radar pipeline cadence: about 2 hours + 15 minutes in the current deployment. Do not reduce this aggressively without reassessing anti-ban risk.
