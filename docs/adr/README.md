# Architecture Decision Records

> 每条决策独立文件，编号递增。Status: accepted / superseded / deprecated.

| # | Title | Status | Date |
|---|---|---|---|
| [001](./001-deterministic-source-brand-tags.md) | 标签生成优先使用确定性 source/brand 标签 | accepted | 2026-05-12 |
| [002](./002-deepseek-v4-flash-prefilter.md) | Prefilter 模型选用 deepseek-v4-flash 并禁用 thinking | accepted | 2026-05-15 |
| [003](./003-dual-dotenv-loader.md) | Runtime env loader 读取双层 .env 文件 | accepted | 2026-05-15 |
| [004](./004-n-plus-one-optimization-scope.md) | N+1 优化仅限 timeline 路由 | accepted | 2026-05-24 |
| [005](./005-timeline-exact-count-with-cached-cte.md) | Timeline 真实总数计数 + CTE 公式 + 进程内 LRU 缓存 | accepted | 2026-06-04 |
| [006](./006-curated-archive-mode.md) | 精选页改为跨 run 去重的累积归档（复用 ADR-005 真实计数 pattern） | accepted | 2026-06-04 |
| [007](./007-interpret-via-ai-assistant-summarizer.md) | 微信文章解读复用 ai-assistant summarizer，save_decision 作单一闸门 | accepted | 2026-06-06 |
| [008](./008-alert-severity-lifecycles.md) | 告警按 severity 维护独立 lifecycle | accepted | 2026-07-22 |
| [009](./009-alert-notification-ledger.md) | 用有界 JSONL 记录已送达告警通知 | accepted | 2026-07-22 |
| [010](./010-db-slimming-clear-regenerable-cache.md) | radar.db 瘦身选清可再生 summary 缓存（Option A）+ 常驻保留 + 历史 digest TTL | accepted | 2026-07-22 |
| [011](./011-perf-idle-only-probing.md) | PERF 改为 idle-only 探测并用 per-file launchd 调度 | accepted; supersedes ADR-008 PERF F1/F4 only | 2026-07-26 |
| [012](./012-single-dom-mobile-layer.md) | 移动层用单套 DOM + media query 重塑，不复制参考站双 DOM | accepted | 2026-08-03 |
| [013](./013-db-sync-cron-agent-socket-auth.md) | DB sync 自动化用 launchd ssh-agent socket 发现做 cron SSH 认证 | accepted | 2026-08-09 |
