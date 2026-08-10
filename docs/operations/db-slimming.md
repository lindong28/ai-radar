# radar.db 瘦身运维

> Mutable snapshot. `radar.db` 的体量主要由可再生的 per-run digest 预计算缓存（`curated_items.summary_json`）撑起。本文记录常驻保留、`admin db retain` / `admin db slim` 子命令用法，以及一次性物理瘦身（VACUUM）的独立维护窗口。DB 同步不以 VACUUM 为前置。

## 背景：summary 缓存为何持续膨胀

每次 `curate` 为其 top-40 精选 item **重插一整行** `curated_items`（含随 run 生成的 `summary_json`）。生产约 **84 run/天**，`summary_json` 因此以约 **8MB/天**（≈0.25GB/月）的速度增长——无常驻保留则数月即可把 `radar.db` 从瘦身后的 ~1.5GB 重新打回 ~2GB+。

该列是 **digest 页可再生的 per-run 预计算缓存**（唯一写点 `src/airadar/curator/precompute.py`，读点 `src/airadar/web/routes/curated_digest.py`，miss 时 `_compute_items` live 现算兜底）。清掉旧 run 的该列不丢任何行、不改 schema，只是让那些历史 run 的 digest 改走现算。

## 常驻保留（自动，随 curate 跑）

`retain_curated_summaries(conn, keep_days)`（`src/airadar/curator/precompute.py`）把**既非最新 run、又超出保留窗口**的旧 run 的 `summary_json` 置 NULL：

- **窗口 = UTC 滚动 24h × keep_days**（非自然日）。默认 `DEFAULT_KEEP_DAYS=7`，是贯穿三个入口（curate hook + 两个 admin 子命令）的单一常量。`keep_days` 已由维护者锁定为 **不可逆决策**——被清的历史 per-run 快照无法恢复。
- **最新 run 硬保护**：`run_id <> (最新 run)` 恒排除清理，即使停跑 >keep_days 天、cutoff 越过最新 run，它仍不被清（仍在被所有主路径服务）。
- **只 `SET summary_json=NULL`**，绝不写 `run_id`/`item_id`——`curated_items` 上的 archive-cache trigger 是 `AFTER UPDATE OF run_id, item_id`，清列 trigger-free，不刷 `archive_cache_generations`。
- **幂等**：连跑第二次 `changes()=0`。
- **挂载点**：curate 流程内 `precompute_curated_summaries` 之后自动调用（`src/airadar/cli.py`，默认 `keep_days=7`）。廉价 UPDATE，随每次 curate 跑，使 summary 长期有界。

常驻保留只清列，**不做 VACUUM**（VACUUM 重写整库、实测 ~30s，进 84×/天 的 curate 热路径是浪费）。清列腾出的页进入 freelist，物理体量要靠 VACUUM 才踢出文件。

## 子命令

比照现有 `admin db migrate` / `admin db checkpoint`，两个子命令共用同一 `retain_curated_summaries` 函数：

```bash
./run.sh admin db retain [--keep-days N] [--dry-run]   # 只清列
./run.sh admin db slim   [--keep-days N] [--dry-run]   # 清列 + VACUUM
```

- **`retain`** —— 只跑常驻保留（手动或 cron 补跑）。成功输出 `retained=true cleared_rows=<n>`。
- **`slim`** —— 一次性瘦身 = `retain` + `VACUUM`（回收 freelist，真正缩小文件）。**两阶段结果**：

  ```
  retained=<bool> compacted=<bool> cleared_rows=<n> reclaimed_file_bytes=<n>
  ```

  VACUUM 不能在事务内，故 `retained=true compacted=false` 是**合法的可重试中间态**：DB 逻辑已正确（summary 已清），只是文件未物理压缩——重跑 `slim`（或 `admin db checkpoint` + 再 `slim`）即可 compact，无需回滚。退出码：完整成功 = 0；`compacted=false` = 1；参数拒绝 / 不可写 = 2（零写）。磁盘 preflight 要求 ~2× 库大小临时空间，不足则拒绝 VACUUM（`retained` 仍可为 true）。

- **`--dry-run`** —— 严格轻量、**零写**：只打印 `eligible_rows` + `logical_summary_bytes`（待清 summary 的逻辑字节，从 live 库直接算、不建副本），主库字节不变、不删任何 sidecar。物理回收字节 `reclaimed_file_bytes` 只在实际 `slim` 后才报。

  ```
  eligible_rows=<n> logical_summary_bytes=<n>
  ```

- `--keep-days` 接受非负整数；`0` = 仅留最新 run（合法值，不拒绝），负数 / 非数字被拒绝。

## 何时 VACUUM

- **不进 curate 热路径**——常驻保留已让 summary 有界，物理压缩是低频动作。
- **不要为 DB 同步而跑**——当前 Mac producer 维护持久 base-only shipping replica，只把快照的非 FTS 逻辑差异就地应用到 replica，再与服务器上次接受的 base-only basis 做 rsync delta。VACUUM 会重写整库并重排 SQLite 页面，正是这条链路要避开的 churn；它不会减少逻辑差异，也不是同步 gate。
- **只在确有本机磁盘回收需求时**手动 `admin db slim` 一次性回收，例如已在一致副本上确认 freelist 显著积累且维护窗口、临时磁盘都充足。

## 关键坑：freelist_count 只在 checkpoint 过的 `.backup` 副本上可信

**绝不**在 live WAL 库上读 `PRAGMA freelist_count` 判断可回收空间——WAL 模式 + pipeline 持续写入下它在同一分钟内可读得 86,701 / 1,012 / 29,551 的抖动值。实测真值必须在 `sqlite3 "$DB" ".backup '$COPY'"` 出的、已 checkpoint 的一致副本上取。且 freelist 回收是 VACUUM 的**机会性**副产品、不是稳定节省来源——瘦身的**确定收益来自清 `summary_json`**。生产实测 **2.28GB → 1.495GB（省 ~785MB / 34%）**；plan-time probe 曾估 ~45%（2080→1151MB），但其中 ~340MB 是当时的**瞬时 freelist**，两天后已被增长复用掉，故实际回收就是 summary 清列这一份。

## Mac 主库一次性物理瘦身 + 回滚（停写维护窗口）

本段只处理 Mac primary 的 `data/radar.db` 原地 VACUUM；腾讯服务器上的只读 serving replica 由独立的 strip/rebuild 同步状态机管理，不在这里原地瘦身。整个流程 **fail-closed 严格有序**，任一 gate 不满足即停在该步：

1. **停写放行门**（正向取证，非"发个停止命令"）：`crontab -l` / launchd 清点并 disable 本项目**全部**定时项（pipeline、performance-probe、DB sync 等）；`pipeline.sh` 终态以 `.pipeline.lock` 目录不存在为准（不用子进程 pgrep，父进程 stage 间隙无子进程仍会启下一 writer）；`PRAGMA wal_checkpoint(TRUNCATE)` 返回 `busy=0` 证无活跃写事务。
2. **停本机 reader**：任何仍指向该 primary 的本机 serve 都经既有 supervisor 正常 stop，非直接 kill。至此宿主对 `radar.db` 无打开连接。
3. **磁盘 preflight**：可用空间 ≥ 2× 库大小，否则停、交回。
4. **备份并验证**（A2 硬门，验证过才算可信回滚锚点）：`sqlite3 "$PROD_DB" ".backup '$BACKUP'"`（用 `.backup`，**不用** `VACUUM INTO`——后者坏 FTS5）；在 `$BACKUP`（已 checkpoint 一致）上测准 freelist；验证三层 `PRAGMA integrity_check=ok` + `INSERT INTO items_fts(items_fts) VALUES('integrity-check')` 无错 + FTS 覆盖完整（`items.id` 全键集 == `items_fts.item_id` 全键集）。任一失败 → 备份不可信、停。
5. **apply 原地**：`wal_checkpoint(TRUNCATE)` → 清列 → `VACUUM`。**生产语义比 CLI 严格**：`compacted=false` 直接进回滚（生产要么完整成功、要么回滚到已验证备份，不停在中间态）。
6. **本地验证**（放行 gate）：用独立端口的短生命周期临时 serve / TestClient 指向 `$PROD_DB`——FTS5 integrity-check + 无半清 run + 被清键集精确 + schema hash 不变 + 最新 run `/api/v1/curated` 与 apply 前 sha256 一致。任一失败 → 回滚。
7. **回滚**（fail-closed）：确认 serve/writer 仍停 → **先删 `"$PROD_DB-wal"` / `"$PROD_DB-shm"`**（与恢复文件不匹配的 WAL/SHM 会静默损坏库）→ 原子 `mv "$BACKUP" "$PROD_DB"` → 复验 integrity + FTS5 + FTS keyset → 通过前不恢复 writer/serve。
8. **恢复 reader + writer + scheduler**：仅当验证通过。恢复此前 disable 的本机 serve、launchd 作业、pipeline cron 与 DB sync cron。
9. **让正常同步链接管**：下一轮 Mac `deploy/sync/sync-db-to-server.sh` 会重新取 `query_only` 一致快照，对持久 shipping replica 应用非 FTS 逻辑差异并逐表对账，再传输 base-only artifact；无需、也不要再为同步追加 VACUUM。需要立即刷新时手动运行 `deploy/sync/sync-db-cron.sh`，并以 producer 报出本轮 `terminal state committed`、服务器 receipt/journal identity 一致和公网 health/search 验证为完成证据。远端拒绝本轮时保留旧 serving release，本地已验证的物理瘦身不因此回滚。

> `.backup` 副本用完即弃、不回 merge——schema 无变更，代码（保留函数 + admin 子命令）走常规 merge 回 main，瘦身对生产库 apply 一次即可。

## 相关参考

- [docs/architecture.md §API 端点](../architecture.md#api-端点) — `/api/v1/curated?run_id=X` 历史 run digest 的 TTL 语义
- [plans/20260720-db-slimming/plan.md](../../plans/20260720-db-slimming/plan.md) — 完整设计、probe 证据、验证矩阵与生产 apply 流程
- [docs/operations/services.md](services.md#db-sync-职责验证与故障证据) — Mac producer、服务器 apply、5 小时 cron、验证入口与故障证据
