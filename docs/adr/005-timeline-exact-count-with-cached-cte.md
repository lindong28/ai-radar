# ADR-005: Timeline 真实总数计数 + CTE 公式 + 进程内 LRU 缓存

- Status: accepted
- Date: 2026-06-04

## Context

`/all` 时间线页要显示数字页码分页（首末页固定可见、当前页相邻页、… 省略），这要求知道真实的末页号，即真实的过滤结果总数。

但 `/api/v1/timeline` 历史上为性能不算真实总数——`total` 是前向估算 `page*limit+1`（背景见 ADR-004 N+1 优化：timeline 是唯一有性能问题的路由，14s TTFB 中精确 COUNT 是瓶颈之一）。前向估算下 `/all` 永远显示"还有下一页"，无法落到真实末页，也无法和 `/wechat`（已有真实总数）统一。

`/all` 的 timeline 查询带 prefilter/scoring 过滤——rows 查询用 EXISTS-per-row 子句判定每条 item 的最新 prefilter/scoring 评估。直接把同样的 EXISTS-per-row 子句套进 `SELECT COUNT(*)` 会让计数随数据量线性退化，这正是当初放弃精确 COUNT 的原因。

## Options Considered

### Option A: 加真实计数显真末页
- Pros: `/all` 显示真实首末页，与 `/wechat` 统一；越界页 clamp 到真实末页；分页语义对用户透明
- Cons: 引入 COUNT 成本，必须做性能缓解（CTE 公式 + 缓存）才不回退 TTFB

### Option B: 不显假末页
- Pros: 零计数成本，不动性能
- Cons: `/all` 数字分页无法落到真实末页（要么不显末页、要么显假末页），与 `/wechat` 不一致，用户体验割裂

## Decision

选择 Option A（用户在两候选中显式选 A，接受加缓存兜性能）。返回真实总数 COUNT，使 `/all` 显示真实首末页，并把越界页 clamp 到真实末页（`response_page = min(max(page,1), 末页)`）。

性能缓解（本决策的核心，缺了就会回退到 ADR-004 当初规避的瓶颈）：

1. **独立 CTE 公式**——计数不复用 rows 查询的 EXISTS-per-row 子句，而是单独的 `_count_timeline_items_with_prefilter()`：`latest_prefilter` / `latest_scoring` CTE 先各取每条 item 的最新评估，再 JOIN 进 `COUNT(*)`，把 per-row 子查询降为一次集合运算。
2. **进程内 LRU 缓存**——`_cached_timeline_total()` 维护 `OrderedDict` LRU（上限 64，带锁）。缓存 key = 过滤签名（channel / category / search / cursor / has_prefilter）+ 数据版本 `_timeline_data_version()`（最新 curation_run id 与 ruleset、items 行数与 max rowid、max eval id）。pipeline / curation 落新数据时数据版本变化，key 自然失效，无需手动清缓存。
3. **search / cursor 路径不缓存**——`cacheable = not search_subquery and not cursor`，这类高基数 key 直接现算，避免污染 LRU。
4. **启动 prewarm**——FastAPI lifespan 启动时 `prewarm_timeline_total_cache()` 预热默认视图（无过滤）计数，首个请求即 warm。
5. **新增 migration `010_timeline_count_indexes.sql`**——`item_evaluations(stage, error, item_id, id DESC)` 覆盖索引，支撑 CTE 取每条 item 最新评估。

## Consequences

- `/all` 与 `/wechat` 分页语义统一：真实首末页 + 越界 clamp。
- TTFB warm 实测 25-30ms，无回退；默认 / channel / category 三种过滤下 total 与真实分页行数精确一致。
- 计数与 rows 查询是两套 SQL（CTE 公式 vs EXISTS-per-row），改 timeline 过滤逻辑时两处需同步保持语义一致，否则 total 与实际页数会偏差。
- 缓存正确性依赖 `_timeline_data_version()` 覆盖所有影响计数的数据维度；新增会改变可见 item 集合的数据源时，需把对应版本信号纳入该函数。
- 索引 `010` 只跑一次（migration 编号 ≥ 010 的常规增量规则见项目 migration 约定）。
