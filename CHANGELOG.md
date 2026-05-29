# Changelog

## 2026-05-28

- Improved `/` and `/all` first-screen loading by serving SSR-preloaded feed HTML; production verification now shows no visible loading spinner and sub-1.5s median first content on the main feed URLs.
- Fixed `/all` timeline entries without scoring data so every visible card renders a numeric score pill.
- Fixed the About page repository link to point at `lindong28/ai-radar`.

## 2026-05-24

- Improved `/api/v1/timeline` and `/all` load performance by adding SQLite indexes, preloading enrichment data in the timeline query, and replacing the exact count query with a pagination-safe estimate.
- Fixed timeline time display so visible times use the same Asia/Shanghai timezone as date grouping.
- Updated the About page repository link to `lindong28/ai-radar`.
