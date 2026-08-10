> **Archive status**: 已完成并上线（稳态传输 16.39MB < 20MB 阈值，对比基线 1.9GB；零停机切换后 3500/3500 请求全 200；五字段 + `title_zh` 公网等价校验全过，2026-08-10）。执行过程产物 `state.md` / `journal.md` 不入档；最终裁决与实测证据见 [ADR-014](../../adr/014-ship-base-only-db-and-rebuild-fts.md)，链路现状见 [architecture.md](../../architecture.md) 的「Mac primary → Tencent serving replica」节与 [operations/services.md](../../operations/services.md)。
> 以下为原 plan 正文，未修改。

# Plan: FTS-rebuild DB sync (strip FTS from transfer, rebuild on server)

> **Long-task mode** — see `~/.claude/references/long-task-protocol.md`. state.md / journal.md live beside this file. Implementer: read the plan banner + global CLAUDE.md long-task protocol, work from state.md, append to journal.md.

## 输入 / 上游

- 本 plan 是 `plans/20260808-news-aiplanet-launch` 的 **P3 完成路径的替代实现**（P3「DB 同步上线」原设计假设增量 ~5MiB/轮，已被证伪）。根因实测数据（页级归因原始表、determinism 测试、本地 rsync `--whole-file` 陷阱）见该目录 `journal.md`「2026-08-09 深夜 — G2 根因确证」条目。
- 站点现状：news.aiplanet.live 已公网 HTTPS 上线、跑当天数据、蓝绿 + 零停机已验证；Tencent 旧单例已退役（8000 空闲，可作蓝绿备用槽）；**自动同步尚未启用**——本改造落地是启用它的前置。

## L1 — 最终产物 + 使用方式

**产物**：改造后的 DB 跨洋同步链路（Mac primary → Tencent replica），使每轮**稳态**同步的传输量降到「一个规定的正常增量窗口内 **< 20MB（预期个位数 MB）**」，而非当前的 ~570MB（FTS 索引 churn）。**验收契约 = 固定 cap `<20MB`（用户决策 R3-F03=A），不宣称严格数学比例 `∝`**——目标是消除 FTS churn 这一与新数据无关的大头。

**使用者 / 使用方式**：运维（本人）。落地并验证后，才谈"持续新鲜"。

> **⚠️ producer 是 Mac 侧的 `sync-db-to-server.sh`,不是 server timer（F-05 修正）**：`ai-radar-db-apply.service/timer` **只 apply Mac 已推送的 snapshot,不 pull**（见 `deploy/systemd/ai-radar-db-apply.service`）。真正的数据 producer 是 Mac 上的 `deploy/sync/sync-db-to-server.sh`,它 `.backup`+strip+rsync+触发 server apply。tracked repo **目前没有定时调用该 producer 的 scheduler**。故"持续新鲜"的入口是 **给 Mac producer 排期**（launchd/cron，频率=后续 G2 决策）；server 的 db-apply timer 若保留,语义只能是 reconcile/retry（补跑未完成的 apply），**不得当作数据生产器**。本 plan 交付后的 handoff 须明确这一点。

**范围**：只改同步 + apply 链路与 FTS 在副本上的产生方式。**不改**前端、pipeline 的 fetch/score/curate（含挂在 fetch pipeline 上的 `maintain_fts`,见 F-06）、news 服务栈。

**硬约束（均可失败断言）**：
- 副本 FTS 与 primary **语义等价**（搜索结果逐字段一致，含中文标题 title_zh），非仅命中数相等。
- 蓝绿零停机保持（切换窗口内公网采样全 200）。
- **绝不写 live 库、不改 pipeline**：.backup + strip 仅在快照副本上做；有可失败断言（源库 path/inode 与 artifact 不同 + strip 全流程后源 fixture 的 schema/data/hash 不变,仅 snapshot artifact 变——见 L2#5b）。
- Mac 侧资源（.backup + strip + 重建触发的墙钟/峰值临时磁盘/pipeline 是否受影响）**只测量、交 G2**（F-09=A）,本轮不设硬阈值,也不作为 pivot 触发。

## 根因（当前状态，可观察事实）

- 同步机制：`deploy/sync/sync-db-to-server.sh` 在 Mac `.backup` 全量 radar.db → 快照 → GNU rsync（`--copy-dest=basis` delta）→ 服务器 `radar.db.incoming` → `ai-radar-db-apply.service`（`deploy/sync/apply_db_update.py`）蓝绿 apply。**传输整库,含 FTS**。
- 实测：真实同步 `Total bytes sent: 1.90G`；页级归因（~40min 间隔,共 642.8MB 变化）**items_fts_data 570MB(88.7%)**、items_fts_content 50MB、**items(真新文章)仅 4.1MB(0.6%)**、其余基础表合计 <0.5MB。items_fts_data 共 108,369 页却 145,910 页变化 → **索引被 maintain_fts 的 merge 周期性整体重写+重定位**,与新文章无关。
- `.backup` 本身确定（静态库两次 .backup byte-identical,已实测）。
- **本地 rsync 默认 `--whole-file`（不算 delta）**;远程默认算 delta。测 delta 必须用真实远程同步,或本地加 `--no-whole-file`（已踩坑）。
- **FTS schema**：`items_fts` own-content（无 `content=`），`tokenize='trigram'`，字段 `item_id UNINDEXED, title, content_text, source_name, author, title_zh`。shadow：`items_fts_data`(423MB)/`items_fts_content`(81MB)/`items_fts_idx`/`items_fts_docsize`/`items_fts_config`。
- **UX 契约**：`docs/contracts/ux-contract.md`(约 §247)承诺搜索覆盖 title/content/source/author/title_zh **五字段**——重建后必须保持,是 L2 的锚点。

### FTS 的精确派生（load-bearing，已从触发器实测——实现必须完整复刻）

items_fts 由 **5 个触发器**从 items + sources + item_evaluations 累积维护。**从 items 单表重建会丢 title_zh**（中文标题搜索静默失效）。完整派生：

| items_fts 字段 | 来源 |
|---|---|
| item_id | `items.id` |
| title | `items.title` |
| content_text | `items.content_text` |
| source_name | `COALESCE((SELECT name FROM sources WHERE id=items.source_id),'')` |
| author | `COALESCE(items.author,'')` |
| title_zh | `item_evaluations` 中该 item 最新一条 `stage='enrich' AND error IS NULL AND output_json IS NOT NULL` 的 `json_extract(output_json,'$.title_zh')`,无则 `''`。**"最新"的排序须与 migration 003 回填一致 = `evaluated_at DESC, id DESC`（不是 `rowid DESC`——R2-F01 catch）** |

参考重建 SQL（**T2：实现须以运行时 `SELECT sql FROM sqlite_master WHERE type='trigger' AND sql LIKE '%items_fts%'` 与 `src/airadar/migrations/003_add_fts5_search.sql:22` 的回填排序为权威复核**）：

```sql
DELETE FROM items_fts;
INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
SELECT i.id, i.title, i.content_text,
       COALESCE(s.name,''), COALESCE(i.author,''),
       COALESCE((SELECT json_extract(e.output_json,'$.title_zh')
                 FROM item_evaluations e
                 WHERE e.item_id=i.id AND e.stage='enrich'
                   AND e.error IS NULL AND e.output_json IS NOT NULL
                 ORDER BY e.evaluated_at DESC, e.id DESC LIMIT 1),'')
FROM items i LEFT JOIN sources s ON s.id=i.source_id;
```

> **⚠️ 参考 SQL 不是等价的最终裁判（R2-F01）**：rebuild 与 trigger-replay 若共享同一错误排序会一起"通过"却仍与 primary 真实 FTS 不等价。**等价性的最终 oracle 是 primary snapshot 的真实 `items_fts`**——见 L2#2 的 baseline manifest。参考 SQL 仅为实现起点。

## 取舍偏好 + 三层影响

- **带宽/磁盘 ≫ 实现简单度**：核心目标是把传输量降到 ∝ 新数据,为此可接受实现更复杂（strip + 服务器重建 + 双 artifact 身份）。
- **副本正确性 = 零容忍（G=max）**：搜索结果错/陈旧比"没自动刷新"更糟。verify 侧重 FTS 逐字段语义等价（含 title_zh）与零停机,不可为省事降级。
- **用户已定方向 = Option A**（不同步 FTS,从 items 表在服务器重建）。

## rigor (A,V) — 分档（F-08 / R2-F06 修正）

- **共同默认档 `(A0,V0)`**：只读调查、文档、机械单测——可逆、低影响,不背高档流程。
- **`(A1,V1)`**：D1 的行为改动（strip 机制,可逆本地、判据明确）。
- **`(A2,V2)`（用户已确认核心向量,override 只升不降）**：D2（服务器重建 + pre-switch 语义等价 gate）、D4（artifact 身份 + 崩溃恢复语义）、以及**真实 milestone**——真实同步 delta 实测、真实库 FTS 重建 + acceptance、真实切换零停机采样、以及 R2-F05 的两段真实接口 preflight（隔离 driver sweep + 候选槽 HTTP 五字段探针）。这些改动生产复制 authority / 只能真实接口整路 sweep（真库重建 trigram + 完整 triple + 真实 delta + 真实 HTTP,不可 mock）。
- **stakes**：R=高（不可逆公开数据/搜索错误、跨洋复制 authority）；**G=max / 回归容忍度为零**（live 公网站点）。

## L2 — 用户视角 verify（implementer-executable；authority/milestone 项按 A2/V2 真实端到端）

> **L2 是运维消费者可观测结果（R4-F03）**：核心验收 = ①稳态传输量 <20MB ②公网五字段搜索等价 ③零停机 ④live DB/pipeline/scope 保持。下列条目里出现的**内部机制**（隔离 namespace/effect sink、baseline manifest、inode 断言、tracked-delta allowlist、candidate slot 等）是**支撑这些消费者结果的内部 verify,其权威定义在 D1/D2/D4/D5**;实现设计若换,只要消费者结果与等价证明仍成立即可,机制可替换。此处并列写出是为让 implementer 一处看全,不代表机制本身是用户验收面。

0. **真实接口 preflight（R2-F05 / R3-F01 / R4-F05,首次生产同步前）**：现 sync dry-run 在 remote publish/apply trigger **前**退出,故完整 Mac producer→publish→apply handoff 从未被 exercise。**从真实 driver(`apply_db_update.py`)的全部 effect sink 反向定义隔离接口（abstraction reset,不再逐个补 namespace 名词——R4-F05）**:real switch 会写 `active_conf`、校验 `nginx_link`、执行**全局 `nginx -t / -s reload`**、持 lock、起 candidate units/DB。故隔离须覆盖:
   - **完整 effect-sink 隔离**:独立 data/journal/basis/receipt/lock/ports、独立 `active_conf` 与 `nginx_link`、candidate 独立 units/DB、**独立 nginx instance/reload（绝不碰全局 nginx）**;真实 `ai-radar-db-apply.service` 固定读生产 `/etc/ai-radar/server.env`——隔离须显式覆盖独立 env/路径,**不复用生产 unit**。（T7 只留具体搭建细节,不再承担接口定义。）
   - **同分支**：producer 走**与生产相同的** publish/trigger/终态解析分支,**不在 live-only 前退出**,不只测替身。
   - **PASS 双条件（覆盖每个 effect sink）**：① 隔离目标达预期终态;② production 的 **active 槽 / data / journal / basis / nginx include / link / routing / units 全部前后不变**（逐项可失败断言）。全绿才允许首次真实生产同步。
1. **增量降到 ~5MB 量级（核心,真实端到端,两阶段——R2-F02）**：dry-run 测不出 delta,必须真实同步读 rsync `Total bytes sent`。基线 ~1.9G。
   - **阶段① bootstrap 轮**：首轮 basis 仍是含 FTS 的 serving candidate（现实现如此）,故 full-with-FTS→base-only 的过渡轮**不受 `<20MB` gate**;只记录传输量 + 确认 committed basis 已是对应 snapshot 的 **base-only artifact**。
   - **阶段② 稳态轮（固定 workload contract——R4-F01）**：bootstrap 后以**一次成功 pipeline completion 后**的 snapshot 为 A;**等到恰好下一次成功增量 pipeline completion 且基础表存在非零逻辑变化**,取 snapshot B;D1 spike / 本地预筛 / 真实远程稳态验收**均比较 A→B** 并记录基础表变化量。**只有这轮 base-only→base-only(A→B)验收 `< 20MB`**（预期个位数 MB;紧邻两轮无新数据不构成有效验收窗口,零基础表变化不算过）。
2. **FTS 逐字段语义等价（F-02 / R2-F01,以 primary 真实 FTS 为最终 oracle）**：
   - **baseline manifest（切换/strip 前从确切 primary snapshot 生成,绑定 base-only artifact 的 `snapshot_id`,与 DB artifact 原子传递;apply 拒绝 identity 不匹配）**:manifest 至少含 primary 真实 `items_fts` 六字段的规范化全量 digest/计数 + 五字段各真实搜索探针的 result IDs/count + 字段专属性证据。**这是等价性的最终裁判**——trigger-replay 与 B-01 仅作独立内部检查,不能自证（rebuild 与 replay 可共享同一错误排序一起通过）。
   - **pre-switch 内部 gate（候选槽 DB 上,切换前）**：重建后 items_fts 与 baseline manifest 的 primary oracle 做**逐行/逐键全字段（item_id/title/content_text/source_name/author/title_zh）双向等价**（缺行/多行/错行/字段漂移全捕获）。**并（R2-F05）经候选槽真实 HTTP/API 搜索路径跑五字段探针**,与 snapshot-bound baseline 比 result IDs+count——在切换前就验证候选 app/API 搜索路径,而非切给用户后才由 post-switch 发现。任一不过 → quarantine（D4）,不切换。
   - **post-switch live L2（切换后,公网 `https://news.aiplanet.live`）**：五字段各专属词查询,比 result IDs+count vs baseline。**title_zh 专属词须证明只来自 title_zh**（选只在中文译名出现、不在任何其它字段的词）。
3. **零停机覆盖切换窗口（F-03）**：**固定 orchestration**——真实 sync **前**即从外部对 canonical HTTPS `https://news.aiplanet.live` 持续采样,直到"匹配本轮 snapshot 的 apply 到达 `committed` 且新数据公网可见"后才停。断言:`sample_count>0`、**按 elapsed/interval 校验采样覆盖整个切换窗口**（切换前后均有样本）、全 200、**连接错误也算失败**。server-local 探针仅诊断,不替代公网 gate。
4. **完整 apply 分阶段成本测量（F-09=A / R2-F04 / R4-F04,只测量不设阈值）**：按 **host × phase 固定字段/单位/观察窗口/baseline** 测每轮 recurring apply 完整 verifier 成本——`[Mac] strip 墙钟(s)/峰值临时磁盘(MB)/pipeline 是否受影响`；`[Tencent] rebuild 墙钟(s)、pre-switch 全字段 equality+digest 墙钟(s)、候选槽 HTTP 探针墙钟(s)、切换墙钟(s)、峰值内存/磁盘(MB)`；`[失败重试的重复成本]`。每项标 host/单位/baseline/观察窗口,**映射到上游 G2 字段**(传输量/端到端耗时/内容陈旧度/内存磁盘余量/失败成本)。本轮**只记录**交 G2。本轮硬门 = 正确性(#2)+稳态`<20MB`(#1 阶段②)+零停机(#3)+ live-DB(#5b)+scope 保全(#6)。
5. **保真（F-04 / R2-F03,两条独立断言）**：
   - **(a) 副本非 FTS 全量保真**：**动态枚举**副本全部非 FTS schema 与逻辑数据,与**同一 snapshot identity 的 immutable pre-strip snapshot**（**非**持续变化的 live primary——避免 pipeline 并发写假失败,R3-T01）做**双向 schema/data digest 比对**（非白名单）,唯一允许缺失 = `items_fts`+shadow+对应 5 触发器。其它任何差异 fail。
   - **(b) live 库逻辑内容绝不被改（独立可失败断言；2026-08-10 用户批准修订）**：strip 全流程运行后,**源 live 库 fixture 的逻辑 schema+数据 digest 不变**（物理字节可因 WAL checkpoint 变化——SQLite WAL 读者需 shm 写权限,U5 生产实测 `-readonly` 在 sidecar 缺失时必然 error 14,故 source 连接为 `PRAGMA query_only=ON` 的标准 WAL 读者形态;无并发写者的 fixture 场景仍断言物理 hash 不变）;运行时保留 source/artifact **path 与 inode 不同**的拒绝条件。
6. **scope 保全 gate（R3-F04,可失败断言）**：本 plan 承诺不改前端/pipeline/news 栈/`maintain_fts`/producer scheduler,须有 gate 兜住:
   - 基于实施起点建 **精确路径 tracked-delta allowlist（R4-F06,不用 `deploy/sync/*` 通配——该目录还含 `deploy_code.py`/`pagediff.py`/`schema_gate.py` 等不在交付范围的文件）**:仅 `deploy/sync/sync-db-to-server.sh`、`deploy/sync/apply_db_update.py`、`src/airadar/db.py` 中新增的 FTS 重建 helper、明确列出的测试与 docs 文件;**新增路径必须先进入「交付物清单」才可改**。allowlist 外的 tracked 改动即 fail。
   - **运行态 scheduler/service 保全另做 baseline 比较**（不只靠 tracked-delta）:断言无 scheduler/service wiring 被新增或启用,运行态排期 unchanged 直到 G2。
   - **`maintain_fts` 定义 + fetch pipeline 调用点（`runner.py`）定向 unchanged 断言**（顺手改了会 fail）。
   - 断言**无 scheduler/service wiring 被新增或启用**,运行态排期保持 unchanged 直到 G2。
   - **前端 / news 文件不在允许变更集**。

人机边界：#0–#6 **全 agent 可独立完成**（隔离 driver sweep + 真实同步 + SQL/digest + 候选槽与公网 HTTP 探针 + inode/allowlist 断言）,无需人工。启用 producer 排期(G2 频率)是本 plan 之后的单独用户决策,不在本 plan 验收内。

## L3 — 设计决策 + 内部 verify

### D1. 剥离机制（Mac sync 侧）— 持久 base-only shipping replica（**2026-08-10 修订，用户已批准**）

> 原两候选 (a) DROP+VACUUM / (b) base-only fresh copy 已被 S1 spike 实测证伪（972MB / 1.28GB）：**每轮全量重物化必然全库页重编号**——SQLite 页内嵌页号指针，少量插入使后续页整体位移、全库指针连锁变化，rsync 滚动校验失配。证据链见 journal S1/S1b/S1c 条目。

**选定机制（S1b+S1c 实测 1.85MB/轮，fallback 8.45MB）**：
- **bootstrap（一次）**：动态枚举非 FTS 表（非白名单），把快照物化成 base-only shipping replica——免 cap 的过渡轮（沿 L2#1 阶段①）。
- **每轮稳态**：`.backup` 快照 → 对 replica 做 **PK-based 逻辑差分**（本机无 sqldiff；全部业务表有稳定 PK，`sqlite_sequence` 特判；spike 参考实现在 worktree `spike-data/logical_delta.py`）→ **就地 apply** 到 replica（页号跨轮稳定）→ **全表 row count + 全行 digest 与快照 base 表对账（每轮保真 gate；失配 → 丢弃 replica 整重建自愈，该轮按 bootstrap 语义传输、免 cap 但记录告警态）**。
- **传输参数**：rsync `--no-whole-file --block-size=4096 --compress-choice=zstd --compress-level=3`（生产候选，S1c 实测 1,849,196B）；**前置 capability gate**：两端 GNU rsync 且远端支持 zstd，否则 fallback 无压缩 `--block-size=4096`（实测 8,453,886B 仍达标）。现有 `--copy-dest=basis` / `--inplace` 语义保留。**须保持 `sync-db-cron.sh` 对 `sync-db-to-server.sh` 的调用契约（退出码分类）不变。**
- **每轮 artifact identity**：apply+对账通过后的 replica 字节态即该轮 **immutable base-only transfer artifact**（hash = snapshot identity，进 D4 语义；下一轮 apply 产生新 identity）。

**内部 verify**：单测——差分/apply 正确性（含 UPDATE/INSERT/DELETE/sqlite_sequence）、digest 对账失配→自愈路径、live-DB path/inode 拒绝（L2#5b）；集成——连续两轮真实增量的本地 rsync **`--no-whole-file --block-size=4096`** delta < 20MB。

### D2. 服务器重建（apply 侧）— rebuild FTS on the candidate, gated before switch

在 `apply_db_update.py` apply 流程中,候选槽 DB（base-only）落盘后、健康检查/切换**之前**：① 确保候选槽有 items_fts 表+触发器 schema（T5：来源 = server 既有 migration 或 apply 显式建）；② 执行「精确派生」重建 SQL；③ 跑 **L2#2 pre-switch 逐字段等价 gate + 完整 acceptance triple**；④ 仅全过才继续切换。重建在候选槽做 → 旧槽持续服务 → 零停机吸收耗时。

**base-only 传输必须含 `item_evaluations`(title_zh 依赖) 与 `sources`(source_name 依赖)**（T3）——它们变化极小,成本可忽略,但语义必需。

**内部 verify**：单测——重建 SQL 在 fixture 上产正确 items_fts 行(含 title_zh);**B-01：title_zh 多事件语义 fixture**——多条成功 enrich、后续失败、null/缺失 title_zh、`evaluated_at` 与插入/id 顺序不一致,断言最终逐 item 结果 == 按真实触发器顺序 replay 的结果。契约——gate 不过则 quarantine,不切换。

### D4. artifact 身份 + 崩溃一致性（F-01，A2/V2）

重建改变字节与 hash,破坏现有"按原 hash 恢复 snapshot""从已服务 candidate 复制 basis"的语义。**明确定义两个 artifact**：
- **immutable base-only transfer artifact**：持有 snapshot identity（hash）、rollback anchor、**下一轮 basis 的唯一来源**；从传输到 commit 全程**不被重建改动**,保留到 commit。
- **mutable serving candidate**：由前者复制/物化后**在其上重建 FTS**;它是被服务的、字节会变的那份。

据此改 `apply_db_update.py` 的 journal/恢复/finalize：恢复按 base-only artifact 的原 hash（而非重建后的 candidate）；**basis 只能从 base-only artifact 推进**（保证下一轮 stripped source 与 server basis 同形,rsync delta 才小）；committed basis **不含 FTS**。

**内部 verify（A2/V2 fault-injection）**：在 rebuild **前/中/后** 注入崩溃,断言——active 槽始终不变、按原 hash 可恢复、committed basis 无 FTS。**重试契约（R4-F09=A）**：authority identity(artifact/manifest/verifier)未变时每 snapshot **最多一次自动 fresh retry**(可复用已验证 checkpoint);**第二次 crash 即 quarantine**,不无限重跑全量 verifier(现有 30min reconcile timer 不得反复支付全部成本)。**确定性 rebuild/triple 失败 → quarantine(隔离 snapshot,不自动回 incoming)**。**告警归 P3b（R4-F08=A）**：本 plan 只承诺 quarantine + 写**持久、可诊断的失败状态**,由后续 P3b 消费投递;本 plan 不做即时告警投递。

### D5. 切换后 consumer-gate 的回滚不变量（R3-F05，A2/V2）

pre-switch 失败已有 quarantine,但**公网五字段验收(L2#2 post-switch)与零停机采样(L2#3)发生在切换后**——若 canonical HTTPS 搜索错/连接失败/路由异常,此时流量已指向新槽、basis/receipt 可能已推进,现设计无固定 rollback。

**固定 invariant（外部终态,实现方式可选）**:**在 post-switch consumer gate 通过之前,旧槽与旧 basis 始终可恢复**。gate 失败时:自动切回旧槽 → 复验公网旧状态恢复 → quarantine 新 candidate → **不推进最终 basis/receipt**。用"延迟 final commit(切流后先不推进 basis/receipt,gate 过再 commit)"还是"commit 后可逆回切"由 implementer 定,但上述外部终态是硬约束。

**内部 verify（A2/V2 fault-injection）**：注入 post-switch consumer gate 失败,断言——流量回到旧槽、公网旧状态可复验、新 candidate 被隔离、最终 basis/receipt 未推进。

### （删除 F-06）不动 live primary FTS 维护

本 plan **不停用、不改动** primary 的 `maintain_fts`（它挂在 fetch pipeline `runner.py`、防 FTS 段+freelist 膨胀,属 pipeline scope,本 plan 明文不改）。strip 只发生在**同步快照副本**,live 库照常 maintain。原 D3/T4 已删除。

## UX 契约影响

搜索是用户可感知的,且 `docs/contracts/ux-contract.md` 承诺五字段搜索。本改造**目标是保持既有搜索语义不变**（重建后逐字段等价,含 title_zh），**不改 ux-contract section**——L2#2 即按该 section 的搜索 lens 校（保持,不新增/移除承诺）。

## 并发隔离

~~单 session 实现即可,execute-plan 可默认单 session 独占~~ **（2026-08-10 修订：该前提已被证伪——另 session 已上线 5h producer cron `ea1b2c6`,且存在两个存活写入者 session。已按「执行中提升」在独立 worktree `.claude/worktrees/fts-rebuild-sync` 实施;scope 保全 gate 的 baseline 以含该 scheduler 的现状为准,本 plan 不新增/不修改 scheduler wiring;U4/U5 真实验收窗口经用户批准临时暂停该 cron、验收后恢复。）****pipeline cron 每15min 写 live radar.db 并发运行**——strip 在 .backup 快照副本上做、绝不碰 live 库,天然隔离。跑真实同步验证前确认 Mac ≥7G 空闲（磁盘曾紧,见 journal）。

## 交付物清单

- 代码：`deploy/sync/sync-db-to-server.sh`（replica 维护 + 差分 apply + 调优 rsync 参数 + capability gate）、**`deploy/sync/logical_delta.py`（新增,PK 差分/apply/对账工具,2026-08-10 修订加入 allowlist）**、**`deploy/sync/build_fts_manifest.py`（新增,baseline manifest 生成,同上加入 allowlist）**、`deploy/sync/apply_db_update.py`（重建 + gate + 双 artifact 身份/恢复）、`src/airadar/db.py`（如需 FTS schema/重建 helper；**不动 maintain_fts**）、相应单测。
- **文档（F-07 / R4-F07,列入本 plan,不推给上游 P6；均带可失败语义检查）**：
  - `README.md` 的 DB sync/slim 指引（改为反映 strip+rebuild,去掉 scp/VACUUM 前置的旧说法）；
  - `docs/operations/db-slimming.md`（当前说同步用 `scp` + 同步前 `admin db slim/VACUUM`——会重排页面、正是要消灭的,须改）；
  - `docs/operations/services.md`（Mac producer / server apply 职责边界 + 验证入口 + producer 排期是持续新鲜的入口）；
  - `docs/architecture.md`（同步数据流、base-only transfer artifact vs serving candidate、basis/恢复/回滚不变量）；
  - **ADR**（Option A = 不同步 FTS/服务器重建、双 artifact 身份、D5 恢复/回滚不变量、R4-F09 重试契约）；
  - `CHANGELOG.md`；ADR 与 docs 索引同步。
  - **可失败断言（R4-F07）**：① 三份 operations 文档的 producer/apply 职责表述经语义检查（不再出现 scp/同步前 VACUUM、明确 producer=Mac 脚本）；② 断言上游 `plans/20260808-news-aiplanet-launch` 的 **P3 仍保持 open**（未被本前置单元误标完成）。

## Defaulted Decisions（reviewer 审）

- **D-a**：strip 机制留实现实测选定(T1),判据 L2#1 明确。
- **D-b**：不动 live 库 maintain_fts（F-06 已固定为范围外）。
- **D-c（用户决策 R3-F03=A,非 default）**：验收契约 = 固定 cap 稳态 delta `<20MB`（预期个位数 MB）,不宣称严格数学比例。
- **D-d（用户已确认 A,非 default——R4-F02）**：重建耗时/Mac 负担本轮**只测量记录、不设硬阈值**（阈值留 G2）。

## 用户决策（F-09，已定）

**用户选定 A**（2026-08-09）：本 plan 对 FTS 重建耗时/Mac 负担**只测量记录、不设硬阈值**,阈值留后续 G2 频率决策。本轮硬门 = FTS 逐字段正确性(L2#2) + 增量<20MB(L2#1) + 零停机(L2#3) + 绝不写 live DB。已反映于 D-d 与 L2#4。

## Bounded TODO

| # | 细化 plan 哪一处 | 内容 |
|---|---|---|
| T1 | D1/R1 | ~~先 spike 实测选定 strip 机制~~ **已完成（S1/S1b/S1c）**：(a)(b) 均 FAIL(972MB/1.28GB)；选定持久 shipping replica + 逻辑增量 + `--block-size=4096`+zstd(1.85MB)，用户已批准 D1 修订 |
| T2 | D2 | 从运行时 `sqlite_master` 取权威触发器,复核重建 SQL 完整复刻(title_zh 多事件语义 + source_name COALESCE) |
| T3 | D2 | 确认 base-only 传输含 item_evaluations + sources |
| T5 | D2 | 确认候选槽 items_fts schema+触发器来源(migration vs apply 显式建),base-only 落槽后存在 |
| T6 | L2#2/D4 | 定义+实现 **baseline manifest**:从 primary snapshot 生成 items_fts 六字段规范化 digest/count + 五探针 result IDs/count + 字段专属性证据,绑定 base-only artifact 的 snapshot_id,原子传递,apply 校 identity 不匹配即拒 |
| T7 | L2#0/D2 | 搭 zero-production-write 隔离目标,跑首次真实 driver sweep(rsync+remote publish+apply handoff+终态解析,不在 live-only 分支前退出) |
| B-01 | D2 | title_zh 多事件语义 fixture（多成功 enrich/后续失败/null;注:replay 仅内部检查,最终 oracle 是 primary manifest per R2-F01） |
| R3-T01 | L2#5(a) | 比较对象固定为「同一 snapshot identity 的 immutable pre-strip snapshot」,不得写成持续变化的 live primary(pipeline 并发写会造假失败) |
| R3-T02 | D2/T2/B-01 | **分开记录两套语义**:migration 003 回填按 `evaluated_at DESC, id DESC` 重选;**运行时 enrich trigger 是每次成功 INSERT 立即覆盖 title_zh、并不按 evaluated_at 重选**。primary manifest(非 replay)是 gate,故本项 T2 实施期收敛;但**不得再把两者混称同一个"真实 trigger replay"** |

## Risk

- **R1（最大）**：~~strip 后页面布局跨轮不稳定~~ **已消解（S1b/S1c 实测）**。残余:①PK 差分器正确性（每轮 digest 对账 gate 兜底,失配自愈）;②`items.fetched_at` 类宽泛 churn 跨窗口波动（单窗口实测 2,686 行/6.9MB 页变化,S9 真实窗口复验）;③远端 zstd capability 未验（capability gate + 无压缩 fallback 兜底）。
- **R2**：重建耗时过长使单轮 apply 太慢。缓解:本轮**只测量**完整 apply 分阶段成本(L2#4),交 G2。**本 plan 不定义"过慢"、不做增量重建**（那会要求 implementer 凭空定阈值并扩张架构 scope——R2-F04）;若 G2 据实测判定确需增量重建,另立 plan。
- **R3**：title_zh/source_name 派生遗漏 → 中文/来源搜索静默错。缓解:L2#2 探针含 title_zh 专属词 + T2/B-01 逐触发器复核 + pre-switch 逐字段等价 gate。

## 交付后（R3-F02：本 plan 是 P3 的前置实现,不等于 P3 完成）

本 plan 的定位 = **P3 的 strip/rebuild 前置实现单元**,不是 P3 本身。落地并 L2 全过后:
- 只记录"该前置单元完成"; **上游 `plans/20260808-news-aiplanet-launch` 的 P3 保持 open**——因为 tracked repo 仍无 Mac producer scheduler,仅靠本 plan 的两次手工同步不构成"持续新鲜"(否则 Mac 继续写、Tencent 不再收 snapshot、公网数据冻结,而 P3 被误标完成)。
- **P3 只有在 G2 安装 Mac producer scheduler(launchd/cron)、readback 排期、并连续自动成功三轮后才可标完成**(与上游 P3 原验收「enable 调度 + 连续观察三轮」一致)。
- 之后续:定 G2 频率、P3b 告警、P5 域名下线、P6 剩余文档。
