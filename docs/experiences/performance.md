# Performance 经验

> Append-only. 性能优化相关的坑点和 pattern.

## 2026-05-24 SQLite 表达式索引消除 dedup 和排序瓶颈

- Problem: Timeline API TTFB 14s，瓶颈之一是 dedup 子查询（3.5s）和排序无索引（3s）。dedup 使用 `lower(rtrim(url, '/'))` 归一化 URL，普通列索引无法加速这种表达式。
- Solution: 创建两个索引——`idx_items_source_url_norm` 覆盖 `items(source_id, lower(rtrim(url, '/')))` 用于 dedup，`idx_items_published_fetched_id` 覆盖 `items(published_at DESC, fetched_at DESC, id DESC)` 用于排序。dedup 3.5s -> 0.02s，排序 3s -> 0.02s。
- Applies when: 修改 dedup 逻辑（`deduped_item_clause()`）或 timeline 排序时——确保改动后的查询仍能使用这些索引。如果改变了 URL 归一化规则，表达式索引需要同步更新。
