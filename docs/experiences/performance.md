# Performance 经验

> Append-only. 性能优化相关的坑点和 pattern.

## 2026-05-24 SQLite 表达式索引消除 dedup 和排序瓶颈

- Problem: Timeline API TTFB 14s，瓶颈之一是 dedup 子查询（3.5s）和排序无索引（3s）。dedup 使用 `lower(rtrim(url, '/'))` 归一化 URL，普通列索引无法加速这种表达式。
- Solution: 创建两个索引——`idx_items_source_url_norm` 覆盖 `items(source_id, lower(rtrim(url, '/')))` 用于 dedup，`idx_items_published_fetched_id` 覆盖 `items(published_at DESC, fetched_at DESC, id DESC)` 用于排序。dedup 3.5s -> 0.02s，排序 3s -> 0.02s。
- Applies when: 修改 dedup 逻辑（`deduped_item_clause()`）或 timeline 排序时——确保改动后的查询仍能使用这些索引。如果改变了 URL 归一化规则，表达式索引需要同步更新。

## 2026-09-19 FTS5 UNINDEXED 列上的触发器 UPDATE 是全表扫描，随归档型 feed 变成不收敛

- Problem: 全站 30 小时无新文章入库。`airadar.cli ingest` 进程 CPU 70%、无网络连接，`sample` 栈 90% 落在 `fts5NextMethod` 逐页 `pread`。`EXPLAIN QUERY PLAN UPDATE items_fts ... WHERE item_id=?` 给出 `SCAN items_fts VIRTUAL TABLE`——`item_id UNINDEXED` 意味着按它定位就是全扫。`items_au_fts` 原写成 `AFTER UPDATE ON items`，于是 `upsert_item` 对已存在条目只刷 `fetched_at` 的 UPDATE 也每次全扫一遍 FTS（实测 0.1–0.2s）。`openai_blog` / `huggingface_blog` 的 RSS 每轮返回整个归档（1208 / 862 条），仅这两源每 15 分钟就要 4–7 分钟 FTS 扫描；ingest 超过 cron 周期后 outbox 只增不减（5.3 万批），每批越来越大，直到永远追不上。
- Solution: 触发器改为 `AFTER UPDATE OF title, content_text, author, source_id ON items`——FTS 只镜像这几列，其余列变更不需要碰它。修改 003 后 `_fts_schema_matches` 判为漂移，已有库下次 migrate 会做一次全量 FTS 重建（内容 68 MB，分钟级）；同步链发往服务器的快照本就不含 FTS 触发器、服务器按自己代码重建，故 DB 同步不受影响。回归测试用 `conn.total_changes` 区分：只刷 `fetched_at` 的 UPDATE 必须恰好改 1 行（旧触发器下是 10 行）。
- Applies when: 给 FTS5 虚表写任何 `WHERE <UNINDEXED 列>` 的触发器或语句时——它一定是全扫，只在低频路径上可接受；以及排查 pipeline「一直在跑但不出结果」时——先看 `ingestion_acks` 的推进速率与 outbox 积压，再用 `sample <pid>` 看栈，别先怀疑网络。诊断时别拿 `SELECT count(*) ... WHERE item_id=?` 的耗时代表 UPDATE 路径：前者只读首列、0.1s 内返回，与 UPDATE 逐行取全部列不是同一条路。
