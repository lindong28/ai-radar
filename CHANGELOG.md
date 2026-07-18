# Changelog

## 2026-07-19

- 修复精选归档计数缓存的过度失效和微信 SSR 请求连接生命周期，使首页与微信页面在 pipeline 写入期间保持稳定响应，同时保留精确总数和分页语义。
- 新增 `performance-probe` 用户旅程监控：从同机 origin/public 测量首页、微信列表、详情和翻页，区分 pipeline idle/busy，以 `PERF:*` 规则告警并保留 14 天诊断证据。结果明确标为 same-host provisional，不作为区域 SLO。
- 新增 `performance-remediate` 候选修复 worker：confirmed 性能退化可触发单个、最长 60 分钟的隔离 Codex worktree，生成未进入主分支且未部署的本地候选 commit；越界配置会 fail closed。

## 2026-07-13

- A1-A4 告警的发送传输改为复用本机 `im-notify --alert`，不再由 AI Radar 直接调用飞书 webhook。原有 firing / resolved、debounce 与 30 分钟冷却状态机保持不变，且不叠加 `--dedup-key`；发送器失败会记录日志而不会终止周期告警检查。`alert` launchd 模板同时把 `~/.local/bin` 加入 `PATH`。

## 2026-06-24

- Split LLM usage accounting into `data/llm_usage.db` (`AI_RADAR_LLM_USAGE_DB`) so prefilter / score / enrich token writes no longer contend with the main `radar.db` writer. Existing `radar.db.llm_usage` history is copied into the dedicated DB on migration/first use, `/admin/usage` reads the dedicated DB while still showing item/source metadata from the main DB, and A2 prefilter P95 now uses a recent 2-hour sliding window so recovered latency incidents do not keep firing until midnight.
- Added an internal `/admin/usage` page and `/api/v1/admin/usage` endpoint for maintainers to inspect LLM usage attribution. DeepSeek/ARK `chat_json` calls now persist one `llm_usage` row per prefilter / score / enrich LLM call, including model, input/output tokens from `completion.usage`, item attribution, and input size. The page uses the same Cloudflare Access / dev-only local bypass guard as `/admin`, is not linked from public navigation, and rolls up the last 30 days by day, model, and pipeline stage.

## 2026-06-12

- Made fresh-clone setup degrade cleanly when optional private resources are missing. The Mp2RSS WeChat source now skips with a warning when `MP2RSS_FEED_URL` is unset or empty instead of aborting source loading, and `.env.example` no longer contains a fake Mp2RSS URL that would force a broken fetch. `./install.sh` now checks each service before installing: `serve` always installs, `pipeline` needs one LLM API key, `alert` needs `FEISHU_GENERAL_ALERT_WEBHOOK`, and `tunnel` needs `deploy/cloudflared/config.yml`. Missing promptable values can be entered interactively and are appended to `./.env`; non-interactive installs skip only the affected services and print a summary.

## 2026-06-09

- Stopped A4 (article ingestion) from alerting on transient fetch flaps. All X/Twitter sources fetch through the single public `nitter.net` instance, which intermittently times out for one ~15-minute fetch round and then self-heals; each flap fired and resolved A4 within ~15 minutes as pure noise, while the daily ingestion count was never actually affected (a round skipped by the flap is backfilled by the next). A4 now debounces: a fetch-failure condition must persist past a 30-minute window (≈2 fetch rounds) before it notifies, and a flap that recovers within the window is absorbed silently — no firing and no resolved. The debounce is per-rule and configured only for A4 (`a4.debounce_minutes`); A1/A3 still notify immediately, so this does not delay genuinely urgent alerts. Also corrected A4's disposition text, which pointed at the retired `wewe-rss/bridge` for WeChat — WeChat now ingests via Mp2RSS, and a batch X-source failure is the more common trigger. The `alert` service must be running the updated code for this to take effect.

## 2026-06-08

- Made `/wechat` search (and the shared timeline/curated search) whitespace-insensitive, so a query with extra internal spaces returns the same results as one without. Previously a stored title like `分享Claude Code` was found by `分享Claude Code` but not by `分享 Claude Code`, because the query and the matched columns were compared with their spaces intact. Both the query patterns and the searched columns (title, author, abstract, tags) now have all whitespace — including full-width spaces — stripped before matching, and the longer-query FTS path handles spaced queries too. Simplified/Traditional matching is unchanged. The web layer must be restarted for this to take effect.
- Fixed the missing avatar for the WeChat Official Account "赛博禅心", which showed a fallback initial instead of its real avatar. A single failed avatar scrape on 2026-06-02 had left its cache row empty and a 7-day negative cache prevented any retry. Added an `admin wechat-avatar refresh --account <name>` command that clears one account's cache row and re-scrapes immediately (used to repopulate 赛博禅心's avatar live), and shortened the failed-scrape negative-cache TTL from 7 days to 2 so a transient miss self-heals within days instead of a week.
- Fixed the 日报 page so navigating to a past day now shows that day's curated articles instead of an empty report. Previously `/daily/{date}` (前一日/后一日 and direct date URLs) only ever populated for today, because a dated request was answered from the single latest curation run, which curates only about one day of fresh items. A dated daily report now aggregates curated items published on that date across all curation runs (deduplicated to each item's latest curation), the same cumulative-archive logic the home page `/` uses, so any past date with curated content renders a populated report. The admin explicit-`run_id` path and the `/` and `/all` archive pagination are unchanged.

## 2026-06-07

- Reduced false alerts in the monitoring rules. A2 (pipeline health) no longer treats a long in-progress run as a fault: a `SKIP` log means "pipeline already running" (liveness), so it is no longer a standalone trigger, and the "no successful pipeline" heartbeat threshold was raised from 45 to 120 minutes — this eliminates the recurring fire/resolve flapping that produced the bulk of past alert noise. A3 (website) dropped its `healthz` dimension, which was a dead signal (hardcoded to never fire) that misleadingly displayed "healthz 连续失败 0 次"; A3 now reports only the real user-side 5xx rate. Each line in `logs/alert-check.log` is now timestamped (Asia/Shanghai) for incident forensics. A true active healthz probe and a time-aware A4 ingestion floor are tracked as follow-ups in `docs/issues/general.md`.
- Prefixed every Feishu monitoring alert (both firing and resolved) with a `【AI Radar】` project label. Because the alert webhook (`FEISHU_GENERAL_ALERT_WEBHOOK`) is shared across projects, the prefix lets a recipient tell at a glance which project an alert came from.
- Added an explicit "访问原文" (visit original) link to WeChat interpretations so readers can jump to the source Official Account article: a bordered button below the title on each `/wechat/<slug>` detail page, plus a compact "原文 ↗" link on every list card. Both open the original in a new tab and reuse the existing source URL. The shared frontend asset version was bumped so visitors receive the new behavior.
- Added search to `/wechat`, scoped to interpretation card fields: Chinese title, Official Account author, abstract, and tags. Search URLs are shareable with `?q=`, pagination and detail-page return links preserve the query, Simplified/Traditional Chinese variants match, and the shared frontend asset version was bumped so visitors receive the new behavior.

## 2026-06-06

- Removed the retired WeWe RSS bridge from the service layer. `./install.sh`, `./uninstall.sh`, and `./status.sh` now manage four services (`serve`, `tunnel`, `pipeline`, `alert`) instead of five, and a bare `./install.sh` no longer requires Docker or aborts when it is unavailable. WeChat ingestion continues through Mp2RSS; rollback material (`deploy/wewe-rss/` + RUNBOOK) is retained, with the launchd wiring recoverable from git history.
## 2026-06-04

- Changed the curated home page `/` from a single page of the latest curation round's top 40 into a cumulative archive of every item ever curated. It now aggregates all distinct items selected across past curation runs (deduplicated, currently about 1,793 items), ordered newest first, paginated about 40 per page (currently about 45 pages) using the same numbered page controls as `/all`. Page 1 still shows the latest curated picks, preserving the "skim in five minutes" use.
- Upgraded pagination on the `/all` timeline and the `/wechat` interpretation list to numbered page controls: first and last pages are always visible, the current page shows two neighbors on each side, gaps collapse to an ellipsis, any page number is directly clickable, and previous/next arrows remain. Both pages share one pagination component.
- Changed `/all` to show the true first and last pages. `/api/v1/timeline` now returns an exact total count instead of the previous forward-looking estimate, so the last page reflects real data (matching `/wechat`), and out-of-range requests such as `?page=9999` clamp to the real last page.

## 2026-06-02

- Added the `/wechat` tab for WeChat Official Account article interpretations, with structured summaries, tags, worth-reading filtering, shareable detail pages, and ai-assistant knowledge-base writeback for saved articles.
- Removed the disabled legacy WeWe source definitions `wx_guizang` and `wx_crossing` and deleted their historical item rows from the production DB after creating a verified backup.
- Migrated WeChat Official Account ingestion from the self-hosted WeWe RSS bridge to the hosted Mp2RSS feed, removing the local Docker container and WeRead QR-login maintenance that frequently broke ingestion in production. The feed URL with its embedded key is read from the `MP2RSS_FEED_URL` environment variable and never committed.
- Added real Official Account names and avatars to WeChat article cards. Cards now show each article's source account name and its avatar instead of the shared collection name, falling back to the WeChat icon when no avatar is cached.
- Added the `/admin` operations dashboard for user traffic, ingestion, pipeline health, and active alert status.
- Added the `alert` background service, which runs `admin alert-check` every five minutes and sends A1-A4 monitoring alerts through a Feishu custom-bot webhook.
- Documented the monitoring runbook, including Cloudflare Access setup for `/admin*` and `/api/v1/admin*`, Feishu webhook setup, and daily service verification commands.

## 2026-06-01

- Fixed Chinese-source search so recently fetched backfill articles are evaluated and visible even when their original publish date is older than the pipeline window.
- Improved source-name searches by ranking source/author matches ahead of content-only matches and rotating same-name sources so a prolific source no longer hides lower-volume WeChat sources on the first page.
- Added Simplified/Traditional Chinese query expansion, so searches such as `归藏` and `歸藏` find the same Chinese-source articles.

## 2026-05-30

- Expanded `/api/v1/timeline` and `/api/v1/curated` search to match source names, authors, and Chinese titles in addition to article title/body. Searches of 3+ characters use FTS; 1-2 character queries fall back to short-field LIKE matching.
- Changed search semantics intentionally: searching a source name can return all matching articles from that source, and scoring `reasoning` is no longer part of the search index.

## 2026-05-29

- Added WeChat Official Account ingestion through a local WeWe RSS bridge, with Playwright-based full-text scraping for internal LLM processing and public cards limited to generated summaries plus original article links.

## 2026-05-28

- Improved `/` and `/all` first-screen loading by serving SSR-preloaded feed HTML; production verification now shows no visible loading spinner and sub-1.5s median first content on the main feed URLs.
- Fixed `/all` timeline entries without scoring data so every visible card renders a numeric score pill.
- Fixed the About page repository link to point at `your-org/ai-radar`.

## 2026-05-24

- Improved `/api/v1/timeline` and `/all` load performance by adding SQLite indexes, preloading enrichment data in the timeline query, and replacing the exact count query with a pagination-safe estimate.
- Fixed timeline time display so visible times use the same Asia/Shanghai timezone as date grouping.
- Updated the About page repository link to `your-org/ai-radar`.
