# Changelog

## 2026-06-08

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
- Fixed the About page repository link to point at `lindong28/ai-radar`.

## 2026-05-24

- Improved `/api/v1/timeline` and `/all` load performance by adding SQLite indexes, preloading enrichment data in the timeline query, and replacing the exact count query with a pagination-safe estimate.
- Fixed timeline time display so visible times use the same Asia/Shanghai timezone as date grouping.
- Updated the About page repository link to `lindong28/ai-radar`.
