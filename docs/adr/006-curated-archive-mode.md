# ADR-006: 精选页改为跨 run 去重的累积归档（复用 ADR-005 真实计数 pattern）

- Status: accepted
- Date: 2026-06-04

## Context

精选首页 `/` 历史上只展示"最新一轮 curation 的 top-40"单页——一次 curation run 选出的条目固定数量，无分页。但用户想把精选当成一个可回溯的累积档案：所有曾被选入精选的不同条目，按发布时间倒序，像 `/all` 一样数字页码分页翻阅。

`/api/v1/curated` 此前只服务"单轮/单日 digest"语义（`/daily` 也复用同一路径）。要支持累积归档，需要在同一端点上区分两种语义，且避免破坏 `/daily` 已依赖的单轮行为。

累积归档引入两个新问题：

1. **跨 run 去重**——同一 item 可能被多轮 curation 反复选中，归档里只应出现一次。
2. **真实总数 + 性能**——数字分页要落到真实末页，需要真实 COUNT；归档跨全部 curated 历史，朴素现算每页和每次 COUNT 都可能退化。这与 ADR-005（timeline 真实计数）是同一族问题。

## Options Considered

### Option A: 同端点区分"归档模式"vs"单轮模式"，复用 ADR-005 计数 pattern
- Pros: `/daily` 等带 `run_id`/`date` 的调用零改动；归档去重 + 真实计数 + 数据版本缓存直接复用 ADR-005 已验证的 pattern；前端归档与 `/all` 共用同一分页组件
- Cons: 单端点承载两套语义，分支逻辑需清晰标注；归档每页要现算 summary（预计算只覆盖约 30%）

### Option B: 新开独立归档端点
- Pros: 两种语义物理隔离，各自演化互不影响
- Cons: 与 `/curated` 大量重复（信封、分类过滤、搜索、item_summary 组装）；前端要维护两套数据形状；收益不抵重复成本

## Decision

选择 Option A。`/api/v1/curated` 在**无 `run_id`/`date` 参数**时进入归档模式：返回跨 run 去重的累积归档并分页；**带 `run_id`/`date`** 时保留原单轮/单日 digest 行为（`/daily` 复用此路径，未受影响）。

实现要点：

1. **跨 run 去重**——每个 item 只保留其**最近一次**被精选的元数据：`_latest_curated_join()` 用 `c.run_id = (SELECT MAX(run_id) FROM curated_items WHERE item_id=i.id)` 相关子查询。run_id 按时间字典序，免 join `curation_runs`。
2. **真实计数 + 数据版本缓存（复用 ADR-005 pattern）**——`_count_archive_items()` 算真实 total；`_cached_archive_total()` 维护独立的进程内 LRU（上限 64，带锁），key = 过滤签名（category + 是否搜索）+ 数据版本 `_curated_data_version()`（latest run_id、curated_items 计数与 max rowid、items 计数与 max rowid、max eval id）。pipeline/curation 落新数据时数据版本变化，key 自然失效。搜索路径高基数，`cacheable=not search_subquery` 直接现算不污染 LRU。
3. **归档每页现算 item_summary**——`_compute_archive_page()` 不依赖 `summary_json`（预计算只覆盖约 30% 的 curated 条目），现算保证归档全集都能渲染。enrichment 走 `LEFT JOIN item_evaluations`（最新 enrich 评估）一次取出，避免 per-row 回查。
4. **关联讨论按页批量查询**——`_batch_related_discussions()` 对当页 rows 一次性正查（item 正文里的链接）+ 反查（`items_fts` 里引用了当页 item URL 的条目），而非逐条查询。
5. **越界 clamp**——`response_page = min(max(page,1), total_pages)`，与 ADR-005 一致。
6. **新增 migration `011_curated_archive_indexes.sql`**——`idx_curated_items_item_run(item_id, run_id)`，支撑去重相关子查询取每条 item 的 MAX(run_id)。
7. **启动 prewarm**——FastAPI lifespan 启动时 `prewarm_curated_archive_total_cache()` 预热默认归档（无过滤）计数。

这是 ADR-005（timeline 真实计数 + CTE + 缓存）的同族延续——精选归档复用了同样的"真实计数 + 数据版本缓存"骨架（独立 count 函数、过滤签名 + 数据版本作 key、search 路径不缓存、lifespan prewarm）。差异在去重用相关子查询而非 timeline 的 CTE，且归档现算 summary。

## Consequences

- `/` 从单页 top-40 变为累积归档（当前约 1,793 条、约 45 页），与 `/all` 分页语义统一；第 1 页仍是最新精选，保留"5 分钟扫一遍"用法。
- `/daily` 及任何带 `run_id`/`date` 的 `/curated` 调用语义不变。
- 计数缓存正确性依赖 `_curated_data_version()` 覆盖所有影响归档可见集合的数据维度；未来新增会改变 curated 可见集合的数据源时，需把对应版本信号纳入该函数（与 ADR-005 的 `_timeline_data_version()` 同理）。
- 归档现算 summary：改 `item_summary()` / enrichment 解析逻辑会直接影响归档每页渲染，无预计算兜底。
- 实测 warm TTFB：page1 ~85ms、page20 ~115ms。
- 索引 `011` 只跑一次（migration 编号 ≥ 010 的常规增量规则见项目 migration 约定）。
