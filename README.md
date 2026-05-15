# AI Radar

AI Radar is a public, read-only AI information stream. It ingests owner-managed RSS sources, scores items, curates high-value entries, and serves an AIHOT-style timeline at `https://aiplanet.live/`.

## Web Entrypoints

| Tab | URL | Purpose |
| --- | --- | --- |
| 精选 | `/` | High-value curated items grouped by date |
| 全部 AI 动态 | `/all` | Full ingested timeline, newest first |
| AI 日报 | `/daily` | Daily curated archive, with `?date=YYYY-MM-DD` support |
| 关于 | `/about` | Product positioning, source pool, principles, contact note |

## Setup

```bash
uv sync
```

## Commands

```bash
./run.sh fetch
./run.sh prefilter
./run.sh score
./run.sh curate
./run.sh serve --host 127.0.0.1 --port 8000
```

Admin helpers:

```bash
./run.sh admin db migrate
./run.sh admin sources reload
./run.sh admin sources list
```

## Verification

```bash
./test.sh
./tests/run_user_verify.sh
```

`tests/run_user_verify.sh` starts the local FastAPI server on a free port and runs the Playwright checks for the local user-facing contract. Public-domain checks for `https://aiplanet.live/` are deployment-gated.

## Data And Config

- SQLite DB: `data/radar.db`
- Source pool: `data/sources.toml`
- Static frontend: `web/static/`
- Launchd templates: `deploy/launchd/`
- Cloudflared config: `deploy/cloudflared/config.yml`

## Deploy

```bash
# Install launchd services (serve + cloudflared tunnel)
cp deploy/launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/live.aiplanet.ai-radar.serve.plist
launchctl load ~/Library/LaunchAgents/live.aiplanet.ai-radar.tunnel.plist
```
