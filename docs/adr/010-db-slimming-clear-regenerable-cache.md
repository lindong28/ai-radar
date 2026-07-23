# ADR-010: radar.db 瘦身选清可再生 summary 缓存（Option A）+ 常驻保留 + 历史 digest TTL

- Status: accepted
- Date: 2026-07-22

## Context

`radar.db` 长到 ~2.28GB，`scp` 全量同步与服务器磁盘成本上升。体量构成（在一致 `.backup` 副本上 probe 实测）：`curated_items.summary_json` ~537MB（digest 页可再生的 per-run 预计算缓存）、`curation_runs.input_eval_ids` ~335MB、`item_evaluations` ~313MB、`items` ~242MB、FTS5 ~357MB。`curated_items` 有 ~46× 行冗余（每 curation run 为其 top-40 重插整行）。约 84 run/天，`summary_json` 以 ~8MB/天增长——不治理数月即回涨。

约束：宿主上有并发 session 在开发 feedback-loop，其 eval 回放/counterfactual/backtest **重度依赖 `curation_runs.input_eval_ids` / `output_curated_ids` 的完整历史**；`curated_items` 历史行还被 archive 日报跨 run 读。

## Options Considered

### Option A: 只清可再生的 `summary_json` 缓存 + VACUUM + 常驻保留
- Pros: 安全、无跨 session 协调（不碰 feedback-loop 依赖的 `input_eval_ids`、不删行、不改 schema）；捕获主产品侧全部可省缓存；常驻保留使体量长期有界
- Cons: 达不到最激进压缩——保留 `input_eval_ids`（362MB，主分支零读者但 feedback-loop 需要）；历史被清 run 的 digest 首访变慢

### Option B: 额外清 `curation_runs.input_eval_ids`（~335MB）
- Pros: 体量再降 ~335MB，达到 ~1.3GB 目标
- Cons: `input_eval_ids` 是 feedback-loop backtest 的**必需历史数据**——清它 = 拿走另一条活跃工作线的必需数据（非冗余）；需跨 session 协调其保留窗口、加测试、重跑验证

### Option C: 删历史 `curated_items` 行 / 改 schema 去 46× 冗余
- Pros: 去掉真正的行冗余
- Cons: 破坏 archive 日报（跨 run 读历史行）与 feedback-loop backtest；不可逆、高风险；量还小

## Decision

选择 **Option A**（用户按"离必需底线多远"的比例判据拍板）。逐表分解证明：瘦身后主产品必需底线 ≈1.18GB，超出部分几乎全是 feedback-loop 需的 `input_eval_ids`（非浪费）；Option A 已榨干主产品侧所有可省缓存。驳回 B（拿走 feedback-loop 必需数据）与 C（破坏 archive/backtest）。

实现要点：

1. **常驻保留 `retain_curated_summaries(conn, keep_days=7)`**（`src/airadar/curator/precompute.py`）——把**既非最新 run、又超 keep_days 滚动窗口**的旧 run `summary_json` 置 NULL。两侧 `datetime()` 规范化比较（`created_at` 存 `...T..Z`，裸字符串比较在同日边界误判）；保护**所有** `MAX(datetime(created_at))` 并列的最新 run（同秒 tie 也不清）；只 `SET summary_json=NULL`（trigger 是 `AFTER UPDATE OF run_id,item_id`，清列 trigger-free）；幂等。随每次 curate 自动跑（`cli.py`），`DEFAULT_KEEP_DAYS=7` 贯穿三入口。`keep_days` 为**不可逆决策**（清掉的历史 per-run 快照不可恢复），用户锁定 7 天。
2. **`admin db retain` / `admin db slim` 子命令**——共用同一函数；`slim` = retain + VACUUM，两阶段返回 `retained`/`compacted`（VACUUM 不能在事务内，"retained 未 compacted" 是合法可重试中间态）；`--dry-run` 严格零写（主库字节不变、不删 sidecar），只报 `eligible_rows` + `logical_summary_bytes`。生产 apply 用停写维护窗口 + 停 serve + 备份双验 + fail-closed 回滚（见 `docs/operations/db-slimming.md`）。
3. **历史 digest TTL 语义**——超窗口被清的历史 run，其 `/api/v1/curated?run_id=X` digest 改由 `_compute_items` live 现算，内容反映**当前** enrichment 而非 curation 时快照。最新 run summary 永不清、字节一致；所有 HTML 用户页只服务最新 run，不受影响。仅直连旧 `run_id` 的边缘 API 消费者受影响（p95 ~3.8s），用户接受此 TTL 变慢；无 ux-contract delta。

## 被否的量化前提修正（Trajectory Gate）

初版目标 "省 ~929MB / 45% → ~1.15GB" 把 **340MB freelist** 当稳定收益——那是某次 `.backup` 上的**瞬时内部碎片**，两天后已被增长复用（副本 freelist 仅 ~5.6MB）。故 **freelist 回收是 VACUUM 的机会性副产品、非稳定节省来源**；瘦身的确定收益就是清 `summary_json`。成功判据因此改为**增长不变式**（`retained∧compacted∧after<before∧reclaimed≥0.9×清列 logical bytes`），弃用会随 DB 增长漂移的绝对/比例目标。生产实测：2.28GB → 1.495GB（省 ~785MB / 34%）。
