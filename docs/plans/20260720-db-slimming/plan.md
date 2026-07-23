> **Archive status**: 已完成并上线（生产库 2.28GB→1.495GB，2026-07-22）。执行过程产物 `state.md` / `journal.md` 不入档；最终裁决、判据修订与实测证据见 [ADR-010](../../adr/010-db-slimming-clear-regenerable-cache.md) 与 [operations/db-slimming.md](../../operations/db-slimming.md)。
> 以下为原 plan 正文，未修改。

# Plan：radar.db DB 瘦身（缓存清列 + VACUUM + 常驻保留）

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

## 输入

- **本 plan 是实施/审查唯一入口**。上游背景（非契约、只读参考）：
  - `handoffs/db-slimming-create-plan-handoff-20260720.md` —— 任务由来、硬约束、初步分析（注意：其"summary 是 item 级、schema 去重省 600MB"前提已被本 plan research 证伪——summary 按 run 发散，见 §1）。
  - `plans/20260719-tencent-migration/plan.md` §"DB 瘦身机会" / §"待续 Open Issues" —— 迁移背景 + DB 同步管线 + FTS5 跨版本坑。
  - `~/.claude/references/concurrent-plan-isolation.md` —— 并发隔离协议（本 plan §0 据此声明）。
- **plan-time probe 证据**（持久 provenance artifact，与本 plan 同目录）：`plans/20260720-db-slimming/slim-probe.sh`（脚本）+ `slim-probe.out`（实测输出）—— 在生产库一致 `.backup` 副本上实测了：真基线体量、清列不变式、VACUUM 后 FTS5 完整性、瘦身后真实体量。结论写入 §1 与 §5，取代 handoff 的旧估算。**定位=一次性 provenance 记录，非长期可重跑 verifier**（`CP` 路径硬编码到当时 scratchpad、非 fail-fast）；实施期的可重复验证由 §4 L2 的单测/adapter 承载，若要重跑 probe 需先改 `CP` 指向新副本路径。
- **产物落点**：`src/airadar/` 的常驻保留逻辑 + `admin db slim`/`admin db retain` 子命令 + 测试；**不改 schema、不新增独立脚本**（CLI 决策见 §2）。

---

## §0 并发隔离声明（硬约束，先于一切实施）

**背景**：另一 session 正在 worktree `.claude/worktrees/feedback-loop`（分支 `worktree-feedback-loop`）开发 labeling + 反馈闭环，改动集中在 `web/static`、`web/templates`、`tests/`，并向 `data/radar.db` 的 `feedback` 表写数据；其 eval 模块**重度依赖 `curation_runs.input_eval_ids` / `output_curated_ids` 的完整历史**做回放/counterfactual/backtest。宿主上另有运行中的生产 serve(:8000)、cloudflared tunnel、pipeline(`pipeline.sh`)、cron(performance-probe 等)。

**三层结构隔离（默认全上）**：

| 层 | 手段 | 本任务铁律 |
|---|---|---|
| 代码 | 本任务专属 git worktree（**非** feedback-loop 那个），从 `main` 拉 | 绝不进入 `.claude/worktrees/feedback-loop` 改动 |
| 可变运行时状态（生产 DB） | 开发/测试指向 `data/radar.db` 的**副本**（`.backup`，`AI_RADAR_DB` 指向它） | 绝不在开发/测试中写 `data/radar.db`；只读探测用 `mode=ro`；Phase 2 生产 apply 是唯一蓄意写点，走 §7 停写窗口 |
| 服务/端口/进程 | read-path 验证起**独立端口**（非 8000）的 serve，指向瘦身副本 | 开发/验证阶段绝不碰线上 serve:8000 / aiplanet.live；**禁止未授权的直接 `kill`/`signal` 宿主进程**（本任务审查阶段曾观察到工具越界 kill，见 docs/issues/harness-issues.md H12）。**例外**：Phase 2 经用户批准后，允许通过既有 supervisor（launchd/cron 管理）正常 stop/start serve 与 writer（§7 Step 2/3/9）——这是受控运维动作，非直接 kill |

**Option A 的结构性优势**：本方案**只清 `curated_items.summary_json`**（feedback-loop 不读该列），**不动 `input_eval_ids`/`output_curated_ids`/不删任何行/不改 schema**。故与 feedback-loop 的两处潜在冲突面（共享 `input_eval_ids` 历史 + 删行破坏 backtest）**结构上都不发生**。这是选 A 而非 B/C 的关键理由之一。probe 已实测确认清列后 `curation_runs`（含 input_eval_ids/output_curated_ids）逐字节不变、`item_evaluations` 计数+长度和不变（全表逐字节由 Phase 1 gate 补齐，§5/RP3-06）。

**轻量登记**：开工前在 `.claude/active-work.md`（不存在则建）追加一条：worktree 名 + 占用资源（`data/radar.db` 只读 + 副本 + Phase 2 停写窗口）+ 文件面（`src/airadar/curator/`、`src/airadar/cli.py`、`tests/`）。开工前读它检测文件面重叠——与 feedback-loop 的 `cli.py`/`tests/` 面若重叠，merge 前协调谁先。完工删除自己的条目。

**副本不回 merge**：schema 无变更；最终变更 = 代码（保留逻辑 + admin 子命令）走常规 merge 回 main + 一次性瘦身对生产库 apply 一次（§7）。副本用完即弃。

---

## §1 当前状态（可观察事实）

### 体量构成（probe 实测，一致 `.backup` 副本 2026-07-20）

⚠️ **live 库的 `PRAGMA freelist_count` 不可信**：WAL 模式 + pipeline 持续写入下，freelist 在同一分钟内三次读得 86,701 / 1,012 / 29,551（WAL 文件 227MB）。**必须在 checkpoint 过的 `.backup` 副本上测**才是真值。以下为副本实测：

| 对象 | 体量 | 行数 | 说明 |
|---|---|---|---|
| 文件总计 | **2080 MB** | — | page_count=532,529、page_size=4096 |
| freelist（可 VACUUM 回收） | **340 MB** | 87,150 页 | 副本上稳定值（`slim-probe.out` §2）——即 handoff 原估 339MB 是对的，live 读数抖动误导了 reviewer 与初稿 |
| `curated_items.summary_json` | **451 MB** | 177,153 非空行 | digest 页可再生预计算缓存 |
| `curation_runs.input_eval_ids` | ~335 MB | 5,720 行 | 主分支零读者，但 feedback-loop 依赖 → **本方案不动** |
| `item_evaluations` | ~303 MB | 74,588 行 | 97% 唯一 + pipeline 增量幂等依据 → **不动** |
| `items` | ~237 MB | 31,554 行 | 正文，合理，不动 |

### 冗余结构（research 修正 handoff）

- `curated_items` 只覆盖 ~4,964 个 distinct item，228,813 行 / 4,964 ≈ **46× 冗余**（非 handoff 说的 7×）。每 curation run 为其 top-40 重插完整行（含 fresh summary）；5,720 run 集中在 68 天内（~84 run/天）。
- **`summary_json` 按 run 发散**：3,742/3,836 被收录 item 的 summary 跨 run 不同（`item_summary` 内嵌 per-run 的 rank/score/reason + 随时间漂移的 enrichment）。**不能简单去重归一到 items 表**——handoff schema 去重前提被证伪。
- 正确解：`summary_json` 是 **digest 页可再生 per-run 预计算缓存**（唯一写点 `src/airadar/curator/precompute.py:38`；读点 `src/airadar/web/routes/curated_digest.py:43/50/72/80/86`，按单 `run_id` 过滤，miss 时 `_compute_items`(`:181`) live 现算兜底）。清旧 run 的该列 → 该 run 的 digest 改走 live 现算。

### 读路径依赖（Explore 实测 file:line）

- **最新 run 恒被服务**：archive/timeline/curated 主页均 `ORDER BY created_at DESC LIMIT 1`（`curated_archive.py:112`、`timeline.py:72/294`）。最新 run 的 summary **永不清**（§2 保留规则）→ 这些主路径输出**字节一致**。
- **历史 run digest 只经 API 访问，非 HTML 用户页**（reviewer RP3-04 修正）：只有 `/api/v1/curated?run_id=X` 消费 `run_id`（`curated.py:30`）；HTML alias `/curated` **不消费** `run_id`（`app.py:390`），普通用户浏览器永远只看最新 run。故清历史缓存后：该 API（`run_id=X`）改 live 现算、内容反映当前 enrichment（`summary.py:62`）→ 非字节一致，但**只影响直接带 `run_id` 调 API 的消费者，不影响任何 HTML 用户页**。用户已拍板**接受此 TTL 语义**（见 §3 + §6）。
- **archive 按日报表**（`curated_archive.py:155-178 _compute_archive_for_date`）跨所有 run 读**历史 `curated_items` 行**重建"某日 curate 了什么"，但**不读 `summary_json`**（从 item + 最新 enrich 重算）→ 只清列保行，日报完整。
- `archive_cache_generations` 触发器：`curated_items` 上 trigger 为 `AFTER UPDATE OF run_id, item_id` —— **`UPDATE summary_json` 不在其列表，清列 trigger-free、不刷 archive 缓存**（已核 schema；§5 有单测守卫）。

### 无冲突边界

`feedback` 表键在 `item_id`、无 FK 指向 curation 表、当前 0 行；本方案不删 `items` 故 `feedback.item_id` 永不悬空。

---

## §2 要做什么（L1：产物 + 使用方式）

**使用者**：维护者（本人）。**下游用途**：把 radar.db 从 2080MB 压到 **~1151MB（实测，省 ~929MB / 45%）**，降低 DB 同步（scp 全传）与服务器磁盘成本，并**长期维持**（~84 run/天，summary ~8MB/天再生 ≈ 0.25GB/月，无常驻保留则数月打回原形）。

**两个交付物**（CLI 决策：**单一 admin db 入口**，不新增独立脚本）：

1. **常驻保留函数** `retain_curated_summaries(conn, keep_days)`（放 `src/airadar/curator/precompute.py` 旁或同模块）：NULL 掉**既非最新 run、又超保留窗口**的旧 run 的 `summary_json`。核心 SQL（probe 已验证）：
   ```sql
   UPDATE curated_items SET summary_json=NULL
    WHERE summary_json IS NOT NULL
      AND run_id <> (SELECT id FROM curation_runs ORDER BY created_at DESC LIMIT 1)
      AND run_id IN (
        SELECT id FROM curation_runs
         WHERE datetime(created_at) < datetime('now', printf('-%d days', :keep_days))
      );
   ```
   - **两侧都 `datetime()` 规范化再比较**（reviewer RP3-01 关键正确性）：`created_at` 存 `YYYY-MM-DDTHH:MM:SSZ`（T 分隔 + Z），SQLite `datetime()` 返回空格分隔无 Z——直接字符串比较在**同日边界**会误判（`'T'`=0x54 > `' '`=0x20，同日的 run 被错误判为"更晚"而误保留）。`datetime(x)` 把两侧归一到可比格式。
   - **语义 = UTC 滚动 24h×keep_days 窗口**（非自然日）。`keep_days=0` → cutoff=now → 除最新 run 外全清（合法值，=仅留最新 run）。
   - `run_id <> 最新 run` 是硬保护：防止停跑 >keep_days 天后 cutoff 误清仍在服务的最新 run（reviewer V1 sibling）。
   - **挂载**：curate 流程内、`precompute_curated_summaries` 之后调用（`src/airadar/cli.py:167` 处）。廉价 UPDATE，随 curate 自动跑。
   - **`keep_days` 默认值 = 7 贯穿三入口**（reviewer RP4-02）：两个 admin 子命令的 `--keep-days` 默认 7 + curate hook 调用默认传 7，单一常量 `DEFAULT_KEEP_DAYS=7`。**内部 verify**：断言三入口不传 `--keep-days` 时都用 7（防默认写错仍全绿）。
2. **admin 子命令**（`src/airadar/cli.py` admin 组，比照现有 `admin db migrate/checkpoint`）：
   - `admin db retain [--keep-days N] [--dry-run]` —— 手动/cron 调 `retain_curated_summaries`；`--dry-run` 输出轻量字段（见 L2-E，不测物理体量）、不写。
   - `admin db slim [--keep-days N] [--dry-run]` —— 一次性瘦身 = `retain` + `VACUUM`（回收 freelist，把清列腾出的页真正踢出文件）。`--dry-run` 同上轻量字段、不写；物理回收字节只在实际 slim 后报。
   - 两者共用同一 `retain_curated_summaries` 函数（无重复实现面）。

**VACUUM 不进 curate 热路径**（虽 probe 实测仅 30s，但每 curate 跑仍浪费）。常驻只清列；VACUUM 由 `admin db slim` 一次性 + sync 前/定期手动执行。

**明确不做**：不动 `input_eval_ids`/`output_curated_ids`（留给 feedback-loop）、不删任何行、不改 schema、不动 `item_evaluations`、不新增 `scripts/` 独立脚本（避免双维护面，reviewer V5）。

---

## §3 取舍偏好 + rigor（横切）

**取舍偏好（用户已拍）**：
- **激进度 = A（缓存清列 + VACUUM）**：安全 + 无跨 session 协调 ≫ 最大压缩。捕获主收益（省 929MB/45%）于近零风险。放弃 B（+335MB，纠缠 feedback-loop）与 C（+更多，丢 archive 历史 + 破坏 backtest）。
- **形态 = 一次性 + 常驻保留**：长期维持 ≫ 只清一次。
- **历史 run digest = 接受 TTL 语义**（reviewer V1 决策）：最新 run 永久保留、字节一致；超窗口历史 run digest 改 live 现算、内容反映当前 enrichment（对新闻聚合器是"更新"而非损坏，仅影响直接访问旧 run_id 的边缘场景）。
- **保留窗口 keep_days = 7 天（用户已锁定，不可逆决策）**（reviewer D-KEEP-DAYS）：已清的历史 per-run 快照无法恢复，故这是**不可逆**决策、非 planner 可默认（reviewer 纠正了初稿把它标"低反转成本"之误）。用户锁定 7 天——对齐 probe 实测的 ~1.15GB 目标。
- **生产 apply = 停写维护窗口 + 短暂停 serve**（reviewer V4 / D-SERVE-WINDOW 决策）：见 §7。停 writer + 停 serve 使锁行为与 WAL 回滚边界确定，符合 A2/V2；停机窗口 ~1-2min，CDN 吸收。
- **slim 失败语义 = 显式两阶段结果**（reviewer D-SLIM-FAIL 决策）：`slim` 报 `retained`/`compacted`；VACUUM 失败为合法"已清未压缩"态、可重试；Phase 2 生产流程另有备份+回滚兜底。见 §5/§7。
- **CLI = 单一 admin db 入口**（reviewer V5 决策）：见 §2。

**rigor `(A,V)`（用户已确认 A2/V2）**：

- **默认向量 `(A0, V2)`**（取共同低基线）：绝大多数工作在副本上、可逆本地 → A0；但验证维全程 V2（线上站数据完整性零容忍）。
- **Phase 2 override `(A2, V2)`**：生产原地不可逆数据变更 → A2（.backup 备份是不可省硬门 + 用户 gate + 停写窗口）。
- 人读 label：Phase 1 = standard/max、Phase 2 = max。按"默认取低、override 只升不降"：`有效A = max(A0, phase override)`、`有效V = max(V2, review-gate 本地定档)`。
- **对称校验**（proportionality）：V2 的"全矩阵"在此 = milestone 尺度对副本跑全读路径 HTTP parity + 显式 DB 不变式 + FTS5 语义校验 + 体量断言 + error-path（§4）；纯机械 payload（清列 SQL 本体）做廉价 per-unit 单测。不对"清可再生缓存列"施加超额对抗审查。

---

## §4 用户视角 verify（L2，执行契约）

**原则**：每条给固定输入 + expected==actual 的 PASS/FAIL 断言，不用"合理/正常/差异可解释"这类模糊词。数值基线由 implementer 在真实副本上动态取，不在 plan 固化。

**验证职责分层（reviewer RP4-05：避免过重/重复的验证循环）**——三条正交、不重复：
- **全量覆盖 = L2-A 的 DB 键集/计数断言**（对全部行、便宜、确定），**不**靠对 5,720 个 run 逐个 HTTP capture。
- **fallback 机制 = L2-B 的 branch spy**（单测里证 `_load_precomputed`→None→`_compute_items`）。
- **HTTP 只取代表性边界样本 + 性能样本**（最新 run + 少量历史 run + 少量日期），不做全量遍历。
- **adapter 落在测试里**（给确定 `uv run pytest tests/test_db_slim_web_parity.py` 入口），**不新增运维脚本**；相关代码变化后全量重跑该测试即可。

### L2-A：DB 层不变式（"清列不动别的、且不制造半清 run"——probe 已验证 shasum 法可行）

对瘦身前/后副本，断言以下（reviewer RP3-06 校正 probe 宣称范围：probe 只**验证了此 shasum 法可行** + 头部数字（体量/VACUUM-FTS5 安全），其对 `item_evaluations` 仅比了计数+字段长度和、未覆盖全表逐字节/精确键集/schema hash——**故下表全矩阵是 Phase 1 待验证 gate，非 plan-time 已 PASS**）：

| 不变式 | 判据 | PASS 条件 |
|---|---|---|
| curated_items 行数 | `SELECT COUNT(*)` | 前后相等 |
| curated_items 主键集 | `run_id\|\|item_id` 排序后 shasum | 前后相等 |
| curated_items 非-summary 列 | `run_id\|item_id\|weighted_score\|rank\|reason_json` shasum | 前后相等 |
| **被清的精确键集** | 前后 summary 由非空变 NULL 的 `(run_id,item_id)` 集 | **完全等于** {属于"既非最新 run 又 <cutoff"的 run 的全部行}；无一行属于其它 run |
| **无半清 run（RP-03 关键正确性）** | `SELECT run_id FROM curated_items GROUP BY run_id HAVING COUNT(summary_json) NOT IN (0, COUNT(*))` | 返回**空集**——每个 run 要么全有 summary、要么全 NULL；防 `_load_precomputed`(`curated_digest.py:33` 命中"任一行非空"却过滤掉 NULL 行) 静默少显条目 |
| curation_runs 全表（含 input_eval_ids/output_curated_ids/weights_json） | 全行 shasum | 前后**相等**（证明未越界）|
| item_evaluations 全表 | **全行内容 shasum**（非仅行数+长度和，防等长损坏）| 前后相等 |
| items / feedback 全表 | 行数 + 内容 shasum | 前后相等 |
| **schema 未改** | `SELECT sql FROM sqlite_master WHERE type IN('table','index','trigger') ORDER BY name` 的 shasum | 前后相等（证明"不改 schema"）|
| archive 缓存代际 | 清列前后 `archive_cache_generations` 计数 | **不变**（证明 trigger 未误触发）|

### L2-B：HTTP 读路径 parity（服务输出，含数据层）

用 `scripts/web_contract_golden.py` 的 **`capture()`**（对每个 URL 抓 HTTP 响应、canonicalize、sha256），**不用 `verify()`**——后者的 `logical_db_invariant` 校验整库摘要、清 summary 必然使其失败（reviewer V2，已核 `web_contract_golden.py:307`）。数据经 SSR `__PRELOAD__` 内嵌页面，故 capture 的 sha256 已覆盖数据层。

- **verification surface = `/api/v1/*` JSON 数据端点，不是 HTML 页**（reviewer RP2-01 修正）：HTML 路由（`/curated.html` 是 308 redirect、`capture()` 拒绝 redirect（`web_contract_golden.py:65`）；`/daily/{date}`、`/curated` 是静态壳，数据由前端 `app.js` 客户端 fetch）。真实数据面是 `/api/v1/*`（app.js:1254/706/873 等实测调用）。故 L2-B 对 **API 端点**做 parity。
- **adapter（implementer 落地）**：`capture()` 无独立 CLI/默认 specs（`web_contract_golden.py:129`，caller 须给 `base_url/output/concurrency/specs`）。implementer 写 adapter 构造 `HttpSpec` 列表（参考 `tests/test_web_contract_golden.py:91` 的 `HttpSpec(artifact, path, kind)`），对指向副本的独立端口 serve，瘦身前后各 `capture` 一次再逐 artifact sha256 比对；先各 URL 确认 HTTP 200 非 redirect：

| 数据面 | 真实端点（app.js 实测）| 输入选择（代表性样本，非全量）| PASS 条件 |
|---|---|---|---|
| 最新 run curated | `/api/v1/curated`（默认最新 run）| 默认 | sha256 **前后完全相等** |
| 每日报表 | `/api/v1/curated?date=YYYY-MM-DD`（app.js:1254）| **代表性 2-3 个历史日期**（含最早/最近/一个中间）| 前后 sha256 相等（不读 summary，应完全不变；全量日期覆盖由 L2-A 键集承载）|
| timeline | `/api/v1/timeline`（app.js:873）| 默认 | 前后 sha256 相等 |
| wechat | `/api/v1/wechat`（app.js:706）| 默认 + 1 个 slug | 前后 sha256 相等 |
| search | `/api/v1/curated?q=<固定词>` | 2 个固定查询词 | 前后 sha256 相等 |
| **历史 run digest（被清旧 run，边界样本）** | `/api/v1/curated?run_id=X`（HTML alias 不吃 run_id，须走 API）| **代表性 2-3 个被清 run**（非全部 5,720）| HTTP 200 + item ID 集与 count 前后不变（条目不得短缺）+ **fallback 由 branch spy 证明**（单测 spy `_load_precomputed`→None→`_compute_items` 被调用；RP3-04：cached/live 响应形态相同，"无专有字段"不成立）+ 满足 `curated_api` p95 性能预算 |

### L2-C：FTS5 完整性（probe 已证伪主风险）

清列 + VACUUM 后三项（本地）+ 远端 rebuild 后同样三项：
- `INSERT INTO items_fts(items_fts) VALUES('integrity-check')` 无错（probe 实测 OK）；
- **FTS 覆盖完整（reviewer RP4-04 关键）**：`items.id` 全键集 == `items_fts.item_id` 全键集。integrity-check 只验内部一致、**不验完整**（reviewer /tmp 实验：源 2 行、FTS 1 行时 integrity-check 仍过）——keyset 相等才证没漏 item；
- 跑一个 **baseline 命中 >0 的查询词**（implementer 在副本上确定；probe 的"人工智能"恰好 0→0（`slim-probe.out:29`）**只证 integrity-check 未报错、不证搜索无漂移**），断言命中数瘦身前后相等。

### L2-D：体量 + 两阶段 slim + 常驻保留有界

- `admin db slim` 后副本文件体量：断言 `after_size < 0.62 × before_size` **且** `after_size ≤ 1,300,000,000 bytes`（~1240 MiB；probe 实测 1,206,968,320 bytes = 1151 MiB 作参考，阈值留数据自然增长容差）；`reclaimed_file_bytes = before - after` 与 dry-run 的 `eligible_rows × 平均行字节 + freelist` 量级一致。
- **两阶段结果**（RP-01/D-SLIM-FAIL）：`slim` 返回 `retained`/`compacted` 两个布尔 + 清行数 + `reclaimed_file_bytes`。断言：正常路径 `retained=true,compacted=true`；**mock 注入 VACUUM 失败**（monkeypatch，非真占盘）→ `retained=true,compacted=false` 且 DB 逻辑正确（summary 已清、可安全重跑 VACUUM），不留半改的错误态。
- 常驻有界：副本上人工插入 N 条模拟"超窗口新 run + curated 行" → 跑 `retain` → 断言仅窗口内 + 最新 run 保有 summary、更旧已全 NULL（满足 L2-A 无半清 run），且 `item_evaluations`/`input_eval_ids` 未触碰。
- **curate→retain 集成**：副本 + 独立端口跑一次真实 curate → 断言其后自动触发 retain（新 run 全保留、超窗口旧 run 全清、无半清）。

### L2-E：error-path（A2/V2 必覆盖，两阶段分别定义）

`admin db slim/retain` 各给确定行为 + 可断言退出码/消息：非法 `keep_days`（负/非数）→ 拒绝、零写（注：`keep_days=0`=仅留最新 run，是合法值不拒绝）；DB 不可写/路径错 → 明确报错、零写；`retain` 阶段本身非破坏（幂等 NULL）；`VACUUM` 前磁盘 preflight（需 ~2× 库大小临时空间）不足 → 拒绝执行 VACUUM 并提示（`retained` 仍可为 true）；VACUUM 遇独占锁冲突 → 超时后返回 `compacted=false`、不半改、可重试。
- **VACUUM 失败注入用 mock/fault-injection，不用真占满磁盘**（reviewer RP2-02：同宿主有生产任务，真填盘会波及生产）——如 monkeypatch VACUUM 抛错、或 mock preflight 返回不足。
- **退出码语义锁定（RP4-02）**：成功 = 0；参数拒绝/不可写 = 非零且零写；`retained=true,compacted=false`（VACUUM 失败）= 非零且 stdout 明确 `retained/compacted` 状态供重试。
- **`--dry-run` = 严格轻量（RP2-04/D-DRYRUN-FREELIST 选项1）**：只打印 `eligible_rows` + `logical_summary_bytes`（待清 summary 逻辑**字节**）——两者都能在**不建副本、不写源库**下从 live 库直接测（`COUNT` / `SUM(LENGTH(CAST(summary_json AS BLOB)))`——**必须 CAST AS BLOB**，reviewer RP4-02：summary 用 `ensure_ascii=False`，`LENGTH()` 对 TEXT 返回**字符数**、会少算中文字节；**内部 verify** 用含多字节 UTF-8 的行断言 BLOB 字节 > 字符数）。**不**在 dry-run 报 `existing_freelist_bytes` 或物理回收（准确 freelist 由 Step 5 一致 `.backup` preflight 提供）。`reclaimed_file_bytes = before_size - after_size` 只在实际 slim 后报。**内部 verify**：dry-run 零写（前后 mtime+shasum 不变）+ `eligible_rows` == 实际 slim 清行数。

### L2-F：隔离不变式

开发/验证阶段 `$PROD_DB` mtime/sha 未变（Phase 2 蓄意备份+apply 除外）；无写 feedback-loop worktree；验证 serve 端口 ≠ 8000；**无未授权直接 kill/signal**（Phase 2 批准后经 supervisor 正常 stop/start serve/writer 是受控运维动作，不违反本条——与 §0 一致）。

### L2-G：生产 apply 后（Phase 2）

- 本地：apply 后 L2-C（FTS5 integrity-check）+ L2-A 关键不变式子集（无半清 run + 被清键集精确 + schema hash 不变）+ 最新 run `/curated` 与 apply 前 sha256 一致。
- 线上（sync 后）：**对"实际传输快照的 manifest"验证，非对移动中的 live source**（reviewer RP2-03/RP3-03）。见 §7 Step 10 生成 manifest、Step 11 验证：先 mutation anchor（远端"已替换并重启"终态 + summary 非空计数 == manifest + 文件体量 == manifest ~1.15GB），再 preservation（远端各普通表逻辑摘要 == manifest，排除 FTS shadow）。远端失败不回滚本地已验证瘦身。冒烟 200。**agent 自动兜底 + 用户最终确认**。

---

## §5 设计决策 + 内部 verify（L3）

- **清列 SQL 严格只 `SET summary_json=NULL`**，绝不写 `run_id`/`item_id`（否则触发 archive 缓存 trigger）。**内部 verify**：单测断言执行后 `archive_cache_generations` 计数不变（probe 已宏观验证）。
- **保留窗口按 `datetime(created_at)` 规范化比较 + 硬保护最新 run**（RP3-01）。**内部 verify（边界测试必覆盖）**：
  - 造 run（今天/3天前/40天前）+ keep_days=7 → 仅 40 天前的 summary 被 NULL；
  - **同日多 run 边界**：造两个 `created_at` 为 `...Thh:mm:ssZ` 格式、恰在 cutoff 同日两侧的 run → 断言 T/Z 格式不导致误判（规范化后正确分界）；
  - **cutoff 前后 1 秒**：造 created_at 恰在 cutoff ±1s 的 run → 断言精确分界；
  - **keep_days=0**：断言除最新 run 外全清；
  - **停跑场景**：造"最新 run 也已 <cutoff"（停跑 >keep_days 天）→ 断言最新 run 仍**不被清**（硬保护生效）。
- **幂等**：**内部 verify**：连跑两次 `retain`，第二次 `changes()`=0。
- **不越界**：**内部 verify**：L2-A 的 curation_runs/item_evaluations/items/feedback 全表逐字节不变断言（Phase 1 gate；probe 验证了 curation_runs 一致但 item_evaluations 仅比了计数+长度和，全表逐字节由 Phase 1 补齐）。
- **VACUUM 与 FTS5**：**probe 已证伪主风险**——普通 VACUUM 后 FTS5 integrity-check OK、VACUUM 仅 30s（probe 只证 integrity-check 未报错，未证"搜索无漂移"——见 L2-C）。故 apply 流程用普通 VACUUM，**保留 integrity-check + FTS keyset 完整（L2-C）作放行守卫**。不需 rebuild fallback（若守卫某日失败才启用 `INSERT INTO items_fts(items_fts) VALUES('rebuild')`）。
- **两阶段 slim 语义（RP-01/D-SLIM-FAIL）**：普通 SQLite `VACUUM` 不能在事务内，故 `slim` = 阶段① `retain`（`UPDATE ... SET summary_json=NULL`，单事务提交、幂等；**注意语义精确性 RP3-06：对"保留的 run"其 summary 缓存可再生，但对"被清的历史 run"其 per-run 快照是字节不可逆清除**——非"无损"）+ 阶段② `VACUUM`（独立、可重试）。**合法中间态**："retained 但未 compacted"（阶段①成功、阶段②因锁/磁盘失败）——DB 逻辑正确、只是文件未缩小，重跑 `slim`/`admin db checkpoint`+`VACUUM` 即可 compact，无需回滚。`slim` 显式返回 `retained`/`compacted` 两布尔（**内部 verify**：注入 VACUUM 失败断言 `retained=true,compacted=false` 且逻辑正确、可重跑）。Phase 2 生产流程另有 §7 的备份+验证+回滚兜底任一步失败，与本命令级语义分层。
- **dry-run**：**内部 verify**：`admin db slim/retain --dry-run` 前后文件 mtime+shasum 不变。

---

## §6 UX 契约影响

产品有 `docs/contracts/ux-contract.md`。**对 HTML 用户界面：无用户可感知变化**（reviewer RP3-04 澄清后收敛）：

- **所有 HTML 用户页（首页/`/daily`/`/all`/`/curated`/`/wechat`）只显示最新 run**，最新 run summary 永不清 → 字节一致、无变化。
- **唯一受影响面是 API 消费者**：`/api/v1/curated?run_id=X`（带历史 run_id）在其缓存被清后改 live 现算、内容反映当前 enrichment。HTML alias 不消费 `run_id`，故**没有任何 HTML 用户页会因此变化**。
- **给 execute-plan 的指令**：ux-contract 描述的是 HTML 用户界面行为，本方案不改任何 HTML 用户页 → **无需投影任何 ux-contract section**（记一句"仅 API `run_id=X` 消费者受 TTL 语义影响，非 HTML UI，ux-contract 无 delta"）。该 API 语义变化的验证由 L2-B 历史 run digest 行承载。

---

## §7 生产 apply（Phase 2，停写维护窗口 + 短暂停 serve，用户 gate）

用户已选**停写维护窗口 + 短暂停 serve**（D-SERVE-WINDOW）+ **两阶段结果语义**（D-SLIM-FAIL）。前置：Phase 1 已在副本证明操作安全（FTS5 存活、L2-A 不变式全 PASS、体量达标）。整个流程 **fail-closed 严格有序**：任一 gate 不满足即停在该步，不前进。

**Step 1 — 用户批准 packet（RP-06，A2 终裁材料）**。向用户提交固定内容、每项具体值：
- **锁定绝对 `PROD_DB` 路径**（reviewer RP3-02 关键）：Phase 0 在独立 worktree 开发，但 worktree 里的相对 `data/radar.db` 会解析到 worktree 自己的库、非生产库。生产库是 **main checkout** 的 `data/radar.db`（serve:8000 从主 checkout 跑）。故 packet 锁定一个绝对路径 `PROD_DB=/Users/lindong/research/ai-radar/data/radar.db`（实施时以 `git rev-parse --show-toplevel` 主工作树为准确认）；Phase 2 **所有** DB/WAL/SHM/备份/sync 操作一律引用 `$PROD_DB`，且执行前先 `realpath` 断言解析到该绝对路径（防 worktree 误伤）。代码（retain 函数 + admin 命令）先 merge 回 main，Phase 2 从 main checkout 跑。
- 备份路径 `$PROD_DB.bak-<ts>`（含 `-wal`/`-shm` 处置）；
- 预计清理行数、预计回收字节（~929MB）、apply 后目标体量（~1151MB）、当前磁盘余量 vs 需求（≥2× 库大小）；
- 维护窗口时长与预期中断（停 writer 全程 + 停 serve ≈ VACUUM+验证 ~1-2min，CDN 吸收）；
- 已在副本跑过的 zero-write preflight 与 L2-A/B/C 结果摘要；
- 明确批准选项 + pass/fail 后续（批准→执行 2-11；任一验证 fail→回滚+交回）。

**Step 2 — 停写放行门（RP-04/RP2-02，正向证据，非"发个停止命令"、非子进程 pgrep）**。依次达成并**逐条取证**，全满足才进 Step 3：
- 禁用所有定时触发（curate/fetch/performance-probe cron + `pipeline.sh` 调度）——先 `crontab -l`/launchd 清点本项目**全部**定时项并 disable，取证 = 无本项目定时项将在窗口内触发；
- **pipeline 终态以 `.pipeline.lock` 为准，不用子进程 pgrep**（reviewer RP2-02：`pipeline.sh` 父进程顺序启动 6 个 stage（`pipeline.sh:61`），stage 间隙可能无匹配 Python 子进程但父进程仍会启下一 writer；子进程 pgrep 会误判已停）。判据：`pipeline.sh` 用 `LOCK_DIR=.pipeline.lock` + `trap 'rm -rf' EXIT`（`pipeline.sh:15/24`）→ **`.pipeline.lock` 目录不存在**（父进程已退）即 pipeline 全停；辅以 `pgrep -f pipeline.sh` 为空；
- 无进程持写事务——`PRAGMA wal_checkpoint(TRUNCATE)` 返回 `busy=0` 成功截断（有活跃写事务会 busy≠0）即证无残留 writer。
- 未全满足 → 不得进入 backup/checkpoint/apply（回本步等待/排查）。

**Step 3 — 停 serve**（D-SERVE-WINDOW，经既有 supervisor 正常 stop，非直接 kill）：经 launchd/管理方 stop 生产 serve(:8000)（读也短暂中断，CDN 缓存吸收）。至此宿主对 `data/radar.db` 无任何打开连接。

**Step 4 — 磁盘 preflight**：确认可用空间 ≥ 2× 库大小（VACUUM 临时重写全库）。不足 → 停、交回用户。

**Step 5 — 备份并验证（RP-02，A2 硬门，验证过才算可信回滚锚点）**（**全程用绝对 `$PROD_DB`/`$BACKUP`，reviewer RP4-01**，令 `BACKUP=$PROD_DB.bak-<ts>`）：
- `sqlite3 "$PROD_DB" ".backup '$BACKUP'"`（`.backup` 非 `VACUUM INTO`——后者坏 FTS5）；
- **测准 freelist**（D-DRYRUN 承诺的一致副本 preflight，RP4-02）：对 `$BACKUP`（已 checkpoint 一致）测 `page_size × freelist_count` = 可回收字节，纳入批准 packet；
- **验证备份两层**（比照 `sync-db-to-tencent.sh:19`）：`PRAGMA integrity_check` = ok **且** `INSERT INTO items_fts(items_fts) VALUES('integrity-check')` 无错 **且** FTS 覆盖完整（RP4-04：`items.id` 全键集 == `items_fts.item_id` 全键集）。任一失败 → 备份不可信、停、交回用户。

**Step 6 — apply 原地（两阶段）**：`PRAGMA wal_checkpoint(TRUNCATE)`（折 WAL）→ 阶段① `retain_curated_summaries`(清列，提交) → 阶段② `VACUUM`（实测 ~30s）。**A2 生产语义（reviewer RP2-02，区别于通用 CLI 的容忍）**：这是已锁定的 A2 wrapper，`compacted=false`（VACUUM 失败/锁冲突）→ **进入 Step 8 回滚**（生产要么完整成功、要么回滚到已验证备份，不停在"已清未压缩"中间态）。通用 `admin db slim` 命令仍可返回 `retained=true,compacted=false` 容忍重试（§5）——两层语义分开。

**Step 7 — 本地验证（放行 gate）**：生产 serve 已在 Step 3 停，故 HTTP 验证用**短生命周期、独立端口的临时 serve（或 TestClient）指向 `$PROD_DB`**（reviewer RP3-02），验证完即关闭、且在 Step 8 回滚/Step 9 恢复前确保其连接已断（否则占锁）。验证内容：FTS5 integrity-check + L2-A 无半清 run/被清键集精确/schema hash 不变 + 最新 run `/api/v1/curated` 与 apply 前 sha256 一致。任一失败 → Step 8 回滚。

**Step 8 — 回滚（fail-closed，健壮，全程绝对路径 RP4-01）**：由 **Step 6 `compacted=false`** 或 **Step 7 验证失败** 触发。顺序：确认 serve/writer 仍停（无打开连接）→ **删除 `"$PROD_DB-wal"` / `"$PROD_DB-shm"`**（与恢复文件不匹配的 WAL/SHM 会损坏库）→ 原子替换 `mv "$BACKUP" "$PROD_DB"` → 恢复后复验 `PRAGMA integrity_check` + FTS5 integrity-check + FTS 覆盖完整（items keyset==fts keyset）→ **验证通过前不恢复 writer/serve** → 通过后再 Step 9、交回用户说明。

**Step 9 — 恢复 serve + writer**：仅当 Step 7 通过（或 Step 8 回滚验证通过）。重启 serve(:8000)、恢复 Step 2 禁用的 cron/pipeline 调度。

**Step 10 — 同步服务器（绑定"实际传输的那份快照"，reviewer RP3-03/RP4-03）**：Step 9 恢复 writer 后 live source 继续变化，故**不能拿移动中的 live source 与远端比**。

> **边界声明**：sync driver `plans/20260719-tencent-migration/sync-db-to-tencent.sh` 归**迁移 plan** 所有——它自建固定 `/tmp/radar-sync-snapshot.db`、传后即删（`sync-db-to-tencent.sh:17/29`），且超时/失败仍打印"完成"成功退出（`:33-41`）。本瘦身 plan **依赖对该 driver 的一处增强**（作为对迁移 plan 的依赖项在此登记，由 execute-plan 落地时协调）：让 driver 对**它实际创建并传输的那份快照**生成并**持久化** manifest（不即删），并解析远端 apply 真实终态。

manifest 内容：`snapshot_hash` + 文件体量 + `summary_json IS NOT NULL` 计数 + **动态枚举的普通表**逐表逻辑摘要——`SELECT name FROM sqlite_master WHERE type='table'` 全表，**排除** FTS shadow（`items_fts*`，远端 rebuild）与环境特有的 migration/cache-generation 表（`airadar_migrations`、`archive_cache_generations` 等）；覆盖 `items`/`item_evaluations`/`curation_runs`/`curated_items`/`feedback`/`sources`/`wechat_*`/`llm_usage` 等**当前存在的**普通表，不写死 5 张。sync 前本地已 VACUUM（Step 6），快照即 compact。

**Step 11 — 线上验证（对 manifest，非对 live source）**：
- **远端 apply 契约在执行期 preflight 确认**（reviewer RP4-03 evidence blocker：远端 `apply-db-update.sh` 不在本地 provenance、只读 plan 期无法核实）→ execute-plan 时对目标服务器确认其版本/权限/终态语义，不在 plan 期臆断。
- **先过 mutation anchor**（证新库上线，堵假绿）：远端取得**明确"已替换并重启"终态**（超时/失败**不**当成功）+ 远端 `summary_json IS NOT NULL` 计数 == manifest + 远端文件体量落入 manifest **区间**（远端 rebuild FTS5 后物理布局会变，故用体量**范围**如 [manifest_size×0.9, ×1.15]，不要求精确等值——RP4-03）。
- **再过 preservation**：远端各普通表逻辑摘要 == **manifest**（rebuild 前比 incoming hash，rebuild 后比普通表摘要——FTS 由 keyset 完整守卫，L2-C）。
- **远端失败不回滚已验证的本地瘦身**：远端终态不明确 → 保留远端旧库、停止并重试 sync，本地 `$PROD_DB` 已验证瘦身结果不动。
- aiplanet.live 冒烟 200。用户最终确认。

---

## §8 文档同步

- **API TTL 语义写入权威文档（reviewer RP4-07）**：唯一行为变化是 `/api/v1/curated?run_id=X`——在 API 端点文档 `docs/architecture.md`（现 API 入口说明约 :273）注明"历史 run 超 keep_days 天后其 digest 走 live 现算（内容为当前 enrichment，非 curation 时快照）"。UX contract 无需变更（结论见 §6）。
- **运维文档**：新建 `docs/operations/db-slimming.md`（子命令用法、常驻保留、§7 apply+回滚、VACUUM/sync 时机、freelist 须在副本测的坑），并在 `docs/CLAUDE.md`/docs 索引登记该新文件。
- README「Useful Commands」补 `admin db slim/retain`。
- CHANGELOG 记一条。
- 写 `docs/` 前先读 `docs/CLAUDE.md` 遵循其路由。

---

## §9 实施阶段（供 state.md 展开）

- **Phase 0**：建专属 worktree（非 feedback-loop）；`.backup` 出开发副本；`.claude/active-work.md` 登记 + 读它检测文件面重叠。
- **Phase 1a**：写 `retain_curated_summaries` + 单测（§5 内部 verify：窗口/最新 run 保护/幂等/不越界/trigger 不触发）。先建失败证据（RED）再实现。
- **Phase 1b**：加 `admin db retain` + `admin db slim` 子命令（两阶段 `retained`/`compacted` 结果，§5）+ curate 后挂载点 + 单测（含 dry-run 零写、两阶段失败态、curate→retain 集成、error-path L2-E）。
- **Phase 1c**：跑 L2-A/B/C/D/F 全套（副本 + 独立端口 serve + web_contract_golden capture 前后对比）。
- **Phase 1d**：review-gate（其高档路由=独立 Codex reviewer，只读边界见 §0）。
- **Phase 1e — 代码 promotion（reviewer RP4-06，必须在 Phase 2 前）**：create-commit 提交代码（retain 函数 + admin 子命令 + 测试；不加 Co-Authored-By）→ merge 回 main。Phase 2 从 **main checkout** 跑已合入的代码对 `$PROD_DB` 操作（§7 Step 1 的绝对路径 + realpath 断言防 worktree 误伤）。
- **用户 gate**：报告副本验证结果（§7 Step 1 packet），请求 apply 批准。**回复**：批准 / 要求调整。
- **Phase 2**：§7 停写窗口 apply + sync + 线上验证（用户最终确认）。
- **Phase 3**：§8 文档同步（含 architecture.md API 端点 + docs/operations 新节 + docs/CLAUDE.md 索引）；后续 create-commit 提交文档（`slim-probe.sh`/`.out` 作为持久证据随 plan 目录保留、不删）；清理 worktree/开发副本/`.claude/active-work.md` 登记条目。

---

## Defaulted Decisions（planner 自拍，供 reviewer 审）

| 决策 | 值 | 理由 | 反转成本 |
|---|---|---|---|
| 常驻保留挂载点 | curate 后调 `retain`（`cli.py:167`）+ `admin db retain` 供 cron | curate 后是天然点、廉价、随 curate 自动；admin 子命令供手动/cron。二者共用一函数。 | 低 |
| VACUUM 时机 | `admin db slim` 一次性 + sync 前/定期手动，不进 curate 热路径 | VACUUM 重写整库（实测 30s），84×/天浪费；scp 传整文件故 VACUUM 前置于 sync。 | 低 |
| 保留最新 run 硬保护 | `run_id <> 最新 run` 恒排除清理 | 防停跑 >7 天后 cutoff 误清仍在服务的最新 run（reviewer V1 sibling）。probe 已含此逻辑。 | 低 |
| 不动 input_eval_ids/output_curated_ids/item_evaluations/删行 | 一律不动 | Option A 边界；避免 feedback-loop 冲突 + 保 pipeline 增量幂等 + 保 archive 历史。 | 中（=切 B/C，需用户改激进度）|

---

## Risks（load-bearing 假设已 probe 到事实的不再列为 risk）

- **[已证伪，非 risk] 普通 VACUUM 坏 FTS5**：probe 实测普通 VACUUM 后 FTS5 integrity-check OK（未证搜索无漂移，那由 L2-C 的 keyset 完整 + 非零查询承载）。保留 integrity-check + FTS keyset 完整作放行守卫，未来 sqlite 版本变化时若守卫失败才加 rebuild。
- **[已解决] freelist 测不准**：live 库 WAL 抖动导致，须在 `.backup` 副本 + checkpoint 后测（§1/§7 step 5 已纳入）。
- **feedback-loop 合流顺序**：Option A **不动** `input_eval_ids`，与 feedback-loop 无 DB 数据冲突。文件面：本任务碰 `cli.py`/`tests/`，feedback-loop 也可能碰 → merge 前按 §0 登记协调，谁先 merge 谁后 rebase。若**将来**想做 B（清 input_eval_ids），须等 feedback-loop 合流并自定义其保留窗口后作为独立决策再议，本 plan 不预埋。
- **Phase 2 停写窗口协调**：§7 Step 2 把"所有 writer 已停"做成正向证据放行门（禁 cron + pipeline 结束 + `wal_checkpoint(TRUNCATE)` 成功证无写事务），Step 3 停 serve 断最后连接，Step 6 锁冲突兜底。performance-probe / 其它 cron 须在 Step 2 完整枚举并 disable——实施时先 `crontab -l`/launchd 清点本项目全部定时项。
- **回滚的 WAL/SHM 陷阱**：§7 Step 8 回滚必须先删 `data/radar.db-wal`/`-shm` 再原子替换——残留的旧 WAL/SHM 与恢复的主文件不匹配会静默损坏库。恢复后强制复验 integrity+FTS5，通过前不恢复 serve/writer。
- **备份可信性**：§7 Step 5 备份后立即 integrity_check + FTS5 校验（比照现有 sync 脚本），验证过才作回滚锚点；不带未验证备份进入破坏性操作。
- **历史 run digest live 现算成本**：被清 run 的 digest 首次访问变慢（现算 + 读最新 enrich）。仅影响直接访问旧 run_id 的边缘场景；主路径（最新 run）不受影响。已随 TTL 语义被用户接受。
