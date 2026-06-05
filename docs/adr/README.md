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
