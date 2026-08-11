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
| [014](./014-ship-base-only-db-and-rebuild-fts.md) | 传输 base-only DB 并在服务器候选槽重建 FTS | accepted | 2026-08-10 |
| [015](./015-interval-aware-supplement-pricing.md) | Supplement 定价按调用时间选择有效区间 | accepted | 2026-08-11 |
| [016](./016-rollout-compatible-deprecated-cost-column.md) | 废弃成本列在滚动发布期接受但不消费旧数值 | accepted | 2026-08-11 |
| [017](./017-preserve-paid-results-on-metering-failure.md) | 计量失败不得伪装成模型失败 | accepted | 2026-08-11 |
| [018](./018-normalize-a6-to-current-tariff.md) | A6 只比较现行可报价 cohort 的量结构成本 | accepted | 2026-08-11 |
| [019](./019-reference-interpret-unit-cost-to-comparable-window.md) | 单篇解读成本只与自身可比前窗对照 | accepted | 2026-08-11 |
| [020](./020-normalize-cost-comparisons-to-cache-all-miss.md) | 成本比较统一归一化为 cache 全未命中 | accepted; supersedes ADR-018/019 cache gates | 2026-08-11 |
| [021](./021-audit-alert-delivery-and-suppression-decisions.md) | 告警事件 ledger 同时审计投递与合并抑制决策 | accepted; supersedes ADR-009 scope | 2026-08-11 |
| [022](./022-evaluate-a6-in-progress-cost-as-lower-bound.md) | A6 在途成本作为下界继续正向评估 | accepted; supersedes ADR-020 in-progress handling | 2026-08-11 |
