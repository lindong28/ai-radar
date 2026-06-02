# Changelog

## 2026-06-02

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
