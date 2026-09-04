# Wechat2RSS Runbook

Self-hosted active ingestion layer for `kind="wechat"` sources. The pending-release AI Radar configuration fetches `wx_wechat2rss`; `wx_mp2rss` remains `enabled=true, paused=true` only as a historical-visibility and cross-source-deduplication identity, so it is inert for fetch and A7 even when its environment variable is present. The program target is a named Lima instance whose official generated system LaunchDaemon starts the VM at boot; no GUI login or global Docker context is part of that target lifecycle contract.

> **Current T1 checkout boundary:** this checkout contains `docker-compose.yml`, the legacy no-argument `healthcheck.sh`, and `logs.sh`, but it does not contain `compose.sh` or `boot-witness.sh`. Its legacy healthcheck does not support `--observe-only` or `--receipt`; passing those arguments would not suppress notifier, dedup, or state-file effects. Do not run the helper or flag-bearing commands below until program assembly has merged the T3 assets and revalidated this runbook. Until then, current-checkout status uses `docker compose ps` and `./healthcheck.sh` with no arguments. The remaining sections describe the program-assembly target, not a completed production cutover; current activation status is recorded in [`docs/operations/services.md`](../../docs/operations/services.md).

## Program-assembly target: host prerequisites and instance creation

Lima `>=2.2` and the Docker CLI are required. Confirm the installed CLI contract before creating the instance:

```bash
HOMEBREW_NO_AUTO_UPDATE=1 brew install lima
limactl --version
limactl autostart --help
limactl list --help
```

Choose the absolute directory that contains this `docker-compose.yml` and `data/`. Mount that exact directory at the same absolute path in the guest; do not create a second writable copy of `data/` inside the VM. Only one daemon may own the production bind at a time.

```bash
LIVE_DEPLOY=/absolute/path/to/ai-radar/deploy/wechat2rss
limactl start --name wechat2rss --vm-type=vz --mount-type=virtiofs --mount "$LIVE_DEPLOY:w" template:docker
limactl autostart enable --condition=boot --user "$(id -un)" wechat2rss
sudo launchctl print system/io.lima-vm.daemon.wechat2rss
```

The generated plist is the supervisor authority. Inspect it with `plutil` and `launchctl`; do not copy or hand-edit it into this repository. `compose.sh` dynamically resolves `unix://{{.Dir}}/sock/docker.sock` from `limactl list`, exports `DOCKER_HOST` only to its child command, and leaves the global Docker context unchanged.

## Program-assembly target: first login and normal operation

After program assembly has supplied the named-socket helpers, copy `.env.example` to `.env`, fill `LIC_EMAIL`, `LIC_CODE`, and `RSS_TOKEN`, then create the container through the socket-aware helper:

```bash
cd deploy/wechat2rss
cp .env.example .env
limactl start wechat2rss
./compose.sh up -d
./compose.sh ps
./healthcheck.sh --observe-only
./logs.sh --since 10m
```

The service listens only on `127.0.0.1:8080`. Before scanning the login QR code, the WeChat account must have authorized WeRead's article feature once: open an Official Account article in WeChat, share it to “在微信读书中阅读”, and complete the prompt. After login, `./healthcheck.sh --observe-only` must exit 0 and print `HEALTHY: wechat2rss 有 N 个账号可用且均未风控`.

Set the following only in the AI Radar root `.env` on the same host; the token-bearing URL must not enter git or shared logs:

```bash
WECHAT2RSS_FEED_URL=http://127.0.0.1:8080/feed/all.xml?k=<RSS_TOKEN>
```

Service health is not consumer verification. Complete the AI Radar-side fetch and database checks in [the WeChat ingestion runbook](../../docs/operations/wechat-ingestion.md#验证) before concluding that the source is connected. The repository-external `shadow-observe` cron may still read Mp2RSS independently of this source pause; retiring that cron (or proving it absent with a no-op readback) remains a future, separately authorized production-closure action whose current status is recorded in [the service inventory](../../docs/operations/services.md#服务).

## Program-assembly target: lifecycle semantics

This section applies only after program assembly has supplied and reviewed `compose.sh` plus the flag-aware healthcheck. At that point, run all Compose operations through `compose.sh`; use `logs.sh` for logs so both credential-redaction channels remain active.

## Subscription management

The runtime subscription set has 22 accounts as of 2026-09-04. The numeric ID is the WeChat Official Account ID returned by `atob(biz)` on an article page and is stored as `rsses.biz_id`:

| Account | `biz_id` |
|---|---:|
| AI前线 | `3554086560` |
| AI寒武纪 | `3871912638` |
| AI科技评论 | `3098132220` |
| Draco正在VibeCoding | `3267381402` |
| Founder Park | `3698401914` |
| InfoQ | `2390142780` |
| 数字生命卡兹克 | `3223096120` |
| 暗涌Waves | `3940324519` |
| 歸藏的AI工具箱 | `3540975510` |
| 海外独角兽 | `3869640945` |
| 硅星人Pro | `3926568365` |
| 虎嗅APP | `1432156401` |
| 赛博禅心 | `3934419561` |
| 量子位 | `3236757533` |
| 机器之心 | `3073282833` |
| AGI Hunt | `3087832081` |
| 十字路口Crossing | `3010319264` |
| 记忆承载 | `3542604563` |
| 记忆承载3 | `3574797745` |
| 西风的罗盘 | `3903453739` |
| 人间罗盘 | `3881863572` |
| DeepSeek | `3949607775` |

Add subscriptions through the loopback management API, one at a time. Start in `deploy/wechat2rss/`, replace `<BIZ_ID>` with a numeric ID from the table, and keep the real token only in the local `.env` (`RSS_TOKEN=<RSS_TOKEN>` is the placeholder form; never paste the value into the command or print it):

```bash
sqlite3 -readonly data/res.db \
  'SELECT biz_id,name,check_time FROM rsses WHERE biz_id=<BIZ_ID>;'
# Any row means the account is already subscribed: stop here and do not call /add.

started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
set -a
source .env
set +a
curl --noproxy '*' -fsS --get \
  --data-urlencode "k=$RSS_TOKEN" \
  'http://127.0.0.1:8080/add/<BIZ_ID>'
```

A successful request returns JSON with an empty `err` and enqueues an initial update. If the response is lost or times out, query the database first and never retry `/add` blindly: calling `/add` for an existing ID also enqueues an update. Before adding the next account, repeat the read-only query above until its `biz_id` and `name` exactly match the selected table row and `check_time` is non-zero; the preflight absence check binds that new row to this add. Then run `./healthcheck.sh` and inspect only this operation's redacted log window with `./logs.sh --since "$started_at"`.

Stop submitting new adds if that window reports WeChat risk-control or login-timeout codes such as `-2041` or `-2012`. Resume only after the same window shows recovery followed by a successful `checked <expected name> <BIZ_ID>` event and `./healthcheck.sh` is healthy; otherwise let the service follow its configured backoff and retry schedule.

Use these lifecycle and verification entries from `deploy/wechat2rss/`:

```bash
./compose.sh ps
./healthcheck.sh
./logs.sh --since 10m
./compose.sh pull
./compose.sh up -d
./healthcheck.sh --observe-only
```

`./compose.sh ps` is read-only with respect to VM lifecycle and never starts Lima. Start or stop the VM explicitly with `limactl start wechat2rss` or `limactl stop wechat2rss`.

`./compose.sh stop` is a manual disable and `./compose.sh down` is a teardown. Either action exits the boot-recoverable desired state and invalidates earlier boot receipts; `down` also removes the container. The single recovery sequence is:

```bash
./compose.sh up -d
./healthcheck.sh --observe-only
```

The normal no-argument health check retains alert delivery, dedup clearing, recovery-state updates, and exit codes for cron. `--observe-only` exercises the same liveness/account/risk-control branches and exit codes without notifier, dedup, or production-state I/O. Its stdout starts with one of four explicit states:

| Status | Meaning | `healthy_account_count` |
|---|---|---|
| `HEALTHY` | The service responded and every observed account is usable and outside risk control. | Non-negative integer |
| `DEGRADED` | At least one account remains usable, but another account has a terminal login or risk-control problem. | Positive integer |
| `UNHEALTHY` | The service probe failed, its response was invalid, or no usable account remains. | `null` before account observation; otherwise a non-negative integer |
| `UNMEASURED` | `.env` or `RSS_TOKEN` was absent before a service probe could run. | `null` |

A caller may add `--receipt /absolute/isolated/path.json`. Schema v1 has the exact fields `schema_version`, `observed_at`, `status`, `healthy_account_count`, `failure_category`, and `probe`; `probe` is `loopback_login_list`. The complete `failure_category` enum is `null` for `HEALTHY`, `missing_env`, `missing_rss_token`, `unreachable`, `invalid_response`, `api_error`, `no_account`, `login_invalid`, and `risk_control`. `healthy_account_count` is `null` for configuration, transport, invalid-response, and API-error branches because no account list was successfully observed; an observed empty list uses `0`. Receipts never contain account identity, URL, token, raw response, or raw error. Schema v1 freezes after the first accepted live receipt; any later incompatible field or semantic change requires a new schema version rather than silently reinterpreting v1. The notifier's existing dedup suffixes remain `unreachable`, `apierr`, `noaccount`, `login`, and `riskctl`; they are deliberately distinct from the receipt vocabulary.

## Data, egress, and migration boundary

Before moving an existing deployment, stop the old writer, take a cold snapshot, and verify that Lima mounts the same absolute host `data/` path. Preserve image ID/digest, account identity set, subscription/history identity, feed identity, and an AI Radar consumer read across the cutover; file existence or a readable cached feed is insufficient.

Lima does not inherit an old runtime's proxy settings. Before production use, verify DNS and HTTPS separately from the guest and a diagnostic container for the registry, WeRead, and WeChat. Print only proxy-variable names/presence, never values. A configured but unreachable proxy is a failed preflight.

After cutover the recovery policy is fix-forward on Lima. The previous OrbStack container, image, Compose files, and cold snapshot may remain as passive migration residue, but they are not a recovery target and must not be started against the shared writable data. OrbStack “Start at login” was a migration pre-state, not a supported dependency of this runtime.

## Program-assembly target: reboot verification

After program assembly has supplied `boot-witness.sh`, boot autostart is accepted only after one real reboot proves loopback readiness before any GUI or SSH login. Install a temporary, one-shot system LaunchDaemon that runs that helper as the owning non-root user with an absolute receipt path, a measured deadline, and the fixed `http://127.0.0.1:8080/` probe. During the observation window, do not log in and do not run any manual start or repair command.

A valid receipt must bind to the current `kern.boottime`, report `PASS`, and show zero console and `who` sessions. After the readiness request succeeds, the witness resamples the host clock and both session probes; only that post-success timestamp may become `ready_epoch`, and it must remain strictly earlier than `boot_epoch + deadline_seconds`. A session that appears or a deadline reached during the request therefore produces terminal `FAIL`, not a stale `PASS`. Each session count remains JSON `null` until that specific probe has succeeded in the current witness run; a later terminal failure may retain the latest count that probe successfully observed. The receipt derives its deadline only from `boot_epoch` and `deadline_seconds`; it does not duplicate a `deadline_epoch` field. Missing, stale, or failed receipts cannot be repaired by later logged-in checks. After copying the accepted receipt to durable evidence, remove only the temporary witness job; keep Lima's generated LaunchDaemon enabled.

Current repository evidence for the receipt writer is limited to deterministic isolated fixtures. A same-day live `HEALTHY` loopback probe recorded before the writer was finalized is historical observation only: it does not establish the current writer, Lima, cron, boot, consumer, schema, or production-cutover acceptance. Schema v1 therefore freezes only when a later live check explicitly accepts a receipt and binds it to the reviewed implementation.

## Risk control and known limits

Risk control is expected. The service backs off 15m → 30m → 60m → … capped at 6h and resets on recovery. Manual clear: in WeRead, open 书架 → 文章收藏, tap an account name, and follow the prompts.

- Only broadcast articles are collected.
- Only the newest 20 articles are crawled; `RSS_KEEP_OLD_COUNT=-1` preserves future history but does not backfill the past.
- Vendor cadence claims are not this deployment's measured refresh interval. Use the accepted refresh observer and AI Radar consumer evidence for operational conclusions.
