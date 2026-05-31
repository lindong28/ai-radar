# Plan — timeline/curated 搜索覆盖来源名+作者+中文标题（混合 FTS+LIKE，改 003/004）

> ⚠️ **Long-task mode** — 进度 `./state.md`，决策日志 `./journal.md`，协议 `~/.claude/references/long-task-protocol.md`。
> 实施时（含 compact 后）先读 state.md + journal.md。声称完成前必须跑 verify 并贴输出。

---

## 输入

- **无 spec.md**：本 plan 自带 L1/L2/L3。
- **上游 issue**：`docs/issues/general.md` 的 `[open] /api/v1/timeline search 不匹配来源名 / 作者`。完成后标 resolved。
- **决策已与用户对齐**（多轮 AskUserQuestion），见 §「已锁定决策」。

---

## L1 — 最终产物 + 使用方式

**Before（已读源码 + 实测确认）**：
- `/api/v1/timeline?q=` + `/api/v1/curated?q=` 把 `q` 经 `fts_phrase_query()`（`common.py:248-254`）后用 `items_fts MATCH ?` 过滤。timeline 一处（`timeline.py:36`）；curated **两处**——`_load_precomputed`（`curated.py:64-102`，读 `curated_items.summary_json`、**不 join 任何表**、用 FTS 得 matching_ids 再过滤精选集）与 `_compute_items`（`curated.py:105-141`，join sources）。
- FTS5 虚表 `items_fts(item_id UNINDEXED, title, content_text, reasoning, tokenize='trigram')`（**003** 定义）。只索引 title/content_text/reasoning。`reasoning` 来自 scoring；`title_zh`/`summary_zh` 来自 enrich `output_json`（均不在 FTS）；`sources.name`/`items.author` 不在 FTS。
- **trigram 硬限制**：查询 <3 字符无法匹配（实测 "歸藏"(2)→空、"歸藏的"(3)→命中）。
- **003 与 004 都引用 `reasoning`**（已读源码）：003 的 CREATE/INSERT(:15)/items_ai_fts(:35)/evals_ai_fts(:51-56)；004 的 `UPDATE items_fts SET reasoning=''`(:69-71) + 重建 evals_ai_fts(:73-78)。
- **migrate 机制（已 trace `db.py:50-67`，非记忆）**：`_migration_already_applied` **只对 `004_enrich_stage.sql` 返回 True**（:51 硬编码 `if name != "004_enrich_stage.sql": return False`）；一旦 `airadar_migrations` 有 `004_enrich_stage` 行，`migrate()` **整个跳过 004 文件**(:64 `continue`)。故 **003（及 001/002）每次 migrate 都跑——是生产实际 schema 的唯一权威；004 冷启动跑一次、之后永久跳过**。**生产库 004 早在 2026-05-12 已应用 → 生产上 004 永不再执行**（reviewer 实测 live DB：今日 items_fts 仍旧 reasoning schema、triggers 无 enrich_ai_fts；我已核实源码）。004 文件内的 should_apply 是更早设计的二次防御，db.py 的整文件跳过使其在第 2 次起为 dead（无害）。
- **规模实测**（`data/radar.db`）：items 10,240+ 行；`content_text LIKE '%x%'` 全表扫 **~127ms**（reviewer 实测，非 19ms）；author 有值占 46%。

**Change（产物）**：
- **改 `003_add_fts5_search.sql`**：重建为最终 schema——`items_fts(item_id, title, content_text, source_name, author, title_zh)`（**移除 reasoning，新增 3 列**），DROP+CREATE、回填（含 title_zh 快照）、重建**全部 6 个 triggers**（items 增删改 + `sources.name→source_name` + **`enrich→title_zh` 的 `enrich_ai_fts`**，**移除** scoring→reasoning 的 `evals_ai_fts`）。**`enrich_ai_fts` 在 003 是关键**：003 每次 migrate 都跑、是生产 schema 权威；只放 004 则生产永不创建（见 §B）。
- **改 `004_enrich_stage.sql`**：① 删 `UPDATE items_fts SET reasoning=''`(:69-71)（冷启动 run#1 会崩——003 已先建无 reasoning 的 items_fts）；② 把 RENAME 后的 `evals_ai_fts`(reasoning) 重建(:73-78)改成 **belt-and-suspenders `enrich_ai_fts`(title_zh)**——仅为补冷启动 run#1 窗口（同次 migrate 内 004 的 DROP TABLE 销毁 003 刚建的 enrich_ai_fts；权威定义在 003）。保留 item_evaluations 重建 + should_apply ledger。
- **不新建 007**（items_fts 由 003 单一定义）。
- `common.py` 新增搜索子查询 helper（按 q 长度选 FTS / 短字段 LIKE）；timeline + curated 三处 search 复用。
- 单测 + 端到端 + 文档。

**使用者 + 使用方式**：网站访问者在 aiplanet.live 精选页/时间线页搜索框输入来源名/作者/中文标题词 → 命中该来源/作者的文章；输入 2 字专名（"歸藏"）也命中（LIKE 兜底）；**精选页与时间线页行为一致**（决策④）。

**范围 / 边界**：
- ✅ 做：FTS 改字段集（去 reasoning + 加 source_name/author/title_zh）；<3 字短字段 LIKE 兜底（timeline + curated 双端点三处）；改 003/004；单测；文档。
- ❌ 不做：前端来源筛选器/`?source=`；来源匹配优先排序（Defaulted+R3）；2 字搜正文（兜底不含 content_text，Defaulted+R4）；summary_zh/reasoning 入索引（用户排除）；新建 007。

---

## 已锁定决策（用户对齐）

| # | 决策 | 选择 |
|---|---|---|
| ① | 功能形态 | 扩展搜索框 free-text，覆盖来源名+作者+中文标题（非前端筛选器） |
| ② | 实现方式 | 混合：≥3 字 FTS、<3 字短字段 LIKE 兜底；保留 FTS |
| ③ | 索引字段集 | 搜 title/content_text/source_name/author/title_zh；**不搜** reasoning（移除）、summary_zh |
| ④ | curated 一致性 | **timeline + curated 双端点**都支持 <3 字 LIKE 兜底 |
| ⑤ | CRITICAL 修复 | **改 003 成最终 schema（含 enrich_ai_fts——003 每次跑=生产权威）+ 改 004（删 reasoning UPDATE、RENAME 后放 belt-and-suspenders enrich_ai_fts 补冷启动窗口），不新建 007** |

---

## L3 — 设计

### A) 改 003 为最终 schema（migration 主体替换）

> 关键点：① 现有库 items_fts 是旧 schema，`CREATE IF NOT EXISTS` 不会更新它 → 必须 `DROP TABLE IF EXISTS` 再 CREATE；② triggers 同理 `DROP TRIGGER IF EXISTS` 再建（`IF NOT EXISTS` 不覆盖旧同名 trigger）；③ 幂等靠 `DROP ... IF EXISTS` 自愈（每次 migrate 重跑）；④ 更新 003 顶部注释（不再是 "title/body/reasoning"，改为 "title/body/source_name/author/title_zh"）。

`003_add_fts5_search.sql` 改为：
```sql
-- Phase 2: server-side search over title, content, source_name, author, title_zh.
-- Rebuilt idempotently on every migrate() via DROP ... IF EXISTS.
DROP TRIGGER IF EXISTS items_ai_fts;
DROP TRIGGER IF EXISTS items_au_fts;
DROP TRIGGER IF EXISTS items_ad_fts;
DROP TRIGGER IF EXISTS evals_ai_fts;        -- scoring→reasoning trigger 不再需要（也清生产现存的旧 reasoning trigger）
DROP TRIGGER IF EXISTS enrich_ai_fts;       -- 003 每次重跑、裸 CREATE：先 DROP 再建，定义始终最新
DROP TRIGGER IF EXISTS sources_au_fts;      -- 同上，否则第 2 次撞 "already exists"
DROP TABLE IF EXISTS items_fts;

CREATE VIRTUAL TABLE items_fts USING fts5(
  item_id UNINDEXED, title, content_text, source_name, author, title_zh,
  tokenize='trigram'
);

INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
SELECT i.id, i.title, i.content_text,
  COALESCE(s.name,''), COALESCE(i.author,''),
  COALESCE(json_extract(
    (SELECT output_json FROM item_evaluations
       WHERE item_id=i.id AND stage='enrich' AND error IS NULL
       ORDER BY evaluated_at DESC, id DESC LIMIT 1), '$.title_zh'), '')
FROM items i LEFT JOIN sources s ON s.id=i.source_id;

CREATE TRIGGER items_ai_fts AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
  VALUES (new.id, new.title, new.content_text,
          COALESCE((SELECT name FROM sources WHERE id=new.source_id),''),
          COALESCE(new.author,''), '');
END;
CREATE TRIGGER items_au_fts AFTER UPDATE ON items BEGIN
  UPDATE items_fts SET title=new.title, content_text=new.content_text,
    author=COALESCE(new.author,''),
    source_name=COALESCE((SELECT name FROM sources WHERE id=new.source_id),'')
  WHERE item_id=old.id;
END;
CREATE TRIGGER items_ad_fts AFTER DELETE ON items BEGIN
  DELETE FROM items_fts WHERE item_id=old.id;
END;
-- enrich_ai_fts 在 003（不是只在 004）★：003 每次 migrate 都跑、是生产实际 schema 权威。
-- 生产已应用 004→004 永久跳过（db.py:51,64），若 trigger 只在 004 则生产永不创建（fix-a 的致命错，reviewer 实证）。
-- 生产路径：004 跳过、不碰 item_evaluations，003 建的 enrich_ai_fts 存活，一次 migrate 即生效。
-- 冷启动路径：003 建→同次 004 的 DROP TABLE 销毁→§B 的 belt 补建（run#1 即有），run#2 起 003 重建。
CREATE TRIGGER enrich_ai_fts AFTER INSERT ON item_evaluations
WHEN new.stage='enrich' AND new.error IS NULL AND new.output_json IS NOT NULL BEGIN
  -- error IS NULL 必须有：失败 enrich 写非 NULL error（enrich/runner.py:202 _insert_evaluation），
  -- error IS NULL 过滤掉它们，避免失败 retry 用（可能缺 title_zh 的）output 覆盖好值（与回填过滤一致）
  UPDATE items_fts SET title_zh=COALESCE(json_extract(new.output_json,'$.title_zh'),'')
  WHERE item_id=new.item_id;
END;
CREATE TRIGGER sources_au_fts AFTER UPDATE OF name ON sources BEGIN
  UPDATE items_fts SET source_name=COALESCE(new.name,'')
  WHERE item_id IN (SELECT id FROM items WHERE source_id=new.id);
END;
```

### B) 改 004：删 reasoning UPDATE + 把 RENAME 后的 trigger 重建改成 belt-and-suspenders enrich_ai_fts

**关键（reviewer 实证 + 我核实 `db.py:50-67`）**：`_migration_already_applied` 只对 004 返回 True，一旦 ledger 有 `004_enrich_stage` 行就**整个跳过 004 文件**。生产库 004 早已应用（2026-05-12）→ **生产上 004 永不再跑**。所以 enrich_ai_fts 的**权威定义必须在 003**（每次跑，见 §A）；004 这份只是 **belt**——补冷启动 run#1 窗口（同次 migrate 内 004 的 `DROP TABLE item_evaluations`(:62) 会销毁 003 刚建的 enrich_ai_fts，RENAME 后补建让冷启动单次 migrate 也有 trigger；run#2 起 004 跳过、靠 003 重建）。

> ⚠️ **上一轮 fix-a 的错（已反转）**：曾把 enrich_ai_fts **只**放 004，假设"004 每次跑会销毁 003 的 trigger"。实测 004 跳过——只放 004 等于放进生产永不执行的代码，trigger 永不创建、title_zh 同步在生产静默死掉。正确是 §A 的 003 版为权威 + 此处 belt。

改法（`grep -n reasoning 004_enrich_stage.sql` 定位行号）：
- `UPDATE items_fts SET reasoning=''`（约 :70）→ **删除**：reasoning 列经 003 改后不存在；SQLite 在 prepare 时解析列名（早于 `should_apply` 过滤），冷启动 run#1（003 已建无 reasoning items_fts）此句即 `no such column: reasoning` abort。
- `CREATE TRIGGER IF NOT EXISTS evals_ai_fts ...reasoning...`（约 :73-78，**在 RENAME 之后**）→ **改为 belt enrich_ai_fts**：
  ```sql
  CREATE TRIGGER IF NOT EXISTS enrich_ai_fts AFTER INSERT ON item_evaluations
  WHEN new.stage='enrich' AND new.error IS NULL AND new.output_json IS NOT NULL BEGIN
    UPDATE items_fts SET title_zh=COALESCE(json_extract(new.output_json,'$.title_zh'),'')
    WHERE item_id=new.item_id;
  END;
  ```
  **定义必须与 §A 的 003 版逐字一致**（这是同一 trigger 的副本，仅冷启动 run#1 用到）；`IF NOT EXISTS` 防与 003 版冲突。
- `:34 DROP TRIGGER IF EXISTS evals_ai_fts` 保留（清旧名，无害）；item_evaluations 重建 + should_apply ledger 不动。
**load-bearing**：① 删 reasoning UPDATE 否则冷启动 run#1 `no such column: reasoning` abort；② 真正保证生产有 trigger 的是 §A 的 003 版，此 belt 仅冷启动有效。两者由 I6 + I9 gate 守，**且 I9 必须在「已应用 004 的库」上验**（见 L2）——cold 库会让 004 跑、给 false green。

### C) 搜索引擎切换 helper（common.py）

```python
def search_id_subquery(q: str | None) -> tuple[str | None, list[str]]:
    """返回用于 `i.id IN (<sub>)` 的子查询 SQL + params；None=不过滤。"""
    qs = (q or "").strip()
    if not qs:
        return None, []
    if len(qs) >= 3:
        return "SELECT item_id FROM items_fts WHERE items_fts MATCH ?", [fts_phrase_query(qs)]
    like = "%" + qs.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sub = ("SELECT i2.id FROM items i2 JOIN sources s2 ON s2.id=i2.source_id "
           "LEFT JOIN item_evaluations e2 ON e2.id=("
           "  SELECT MAX(le.id) FROM item_evaluations le "
           "  WHERE le.item_id=i2.id AND le.stage='enrich' AND le.error IS NULL) "
           "WHERE i2.title LIKE ? ESCAPE '\\' OR i2.author LIKE ? ESCAPE '\\' "
           "OR s2.name LIKE ? ESCAPE '\\' "
           "OR COALESCE(json_extract(e2.output_json,'$.title_zh'),'') LIKE ? ESCAPE '\\'")
    return sub, [like, like, like, like]
```
- **timeline.py + curated `_compute_items`**（有 SQL where）：`sub, p = search_id_subquery(q); if sub: where += f" AND i.id IN ({sub})"; params += p`。
- **curated `_load_precomputed`**（无 where，过滤精选集）：`sub, p = search_id_subquery(q); if sub: matching_ids = {r[0] for r in conn.execute(sub, p)}` 再过滤精选（替换现有 `items_fts MATCH` 那步——≥3 字仍是 FTS 子查询，<3 字是 LIKE 子查询，对 `_from_summary` 透明）。
- 这样 <3 字短字段 LIKE 覆盖 title/author/source_name/title_zh 在**三处一致**（决策④），且 helper 单一、无 caller-specific 分支。

### 语义变化（必须文档化）

source_name 入 FTS 后，搜出现在源名里的词（≥3 字，如 "OpenAI" 命中 "OpenAI Blog"）会命中该源全部文章。现有 `test_timeline_search_uses_fts_and_filters_results`（`test_web_routes.py:358`，断言 `?q=OpenAI` 仅 item-openai/total=1）**会变**为 item-openai+item-claude（同源）。这是**有意语义变化**——TASK-004 更新该测试期望、写进 CHANGELOG，**不得声称"不回归"**。

---

## 实施步骤

### TASK-001 改 003 为最终 schema（含 enrich_ai_fts）
- Goal: 按 §A 替换 `003_add_fts5_search.sql`（DROP+CREATE 新字段集、回填、**6 triggers 含 enrich_ai_fts**、更新注释）。
- 内部 verify（cold 临时库；**004 须同步按 TASK-002 改后一起跑**——二者是同一 migration 修复，单改 003 会撞旧 004 的 reasoning UPDATE）: **连跑两次 migrate，两次都 exit 0**（★第二次才会撞 §A DROP 块漏 drop trigger 的 "already exists"）；`SELECT sql FROM sqlite_master WHERE name='items_fts'` 含 source_name/author/title_zh、无 reasoning；full migrate 后 triggers 含 items_ai/au/ad_fts + sources_au_fts + **enrich_ai_fts**、无 evals_ai_fts。**注：cold run#1 full migrate 后 enrich_ai_fts 存在 = §B 的 belt 在 004 `DROP TABLE item_evaluations` 销毁 003 副本后成功补建——这一步即 belt 的 gate**。

### TASK-002 改 004（删 reasoning UPDATE + RENAME 后 belt enrich_ai_fts）
- Goal: 按 §B——删 004 的 `UPDATE items_fts SET reasoning`；把 RENAME 后的 `evals_ai_fts`(reasoning) 重建改成 **belt `enrich_ai_fts`(title_zh)**（定义与 §A 003 版逐字一致，`IF NOT EXISTS`）。
- 内部 verify（**CRITICAL gate I6+I9，必须在「已应用 004 的库」上**）: 取**生产 `.backup`**（`airadar_migrations` 已有 `004_enrich_stage`、items_fts 仍旧 reasoning schema）——**不是新建 cold temp 库**（cold 库 004 会跑、给 false green，正是它让 fix-a 蒙混）。对该库 `migrate()`：exit 0（无 `no such column: reasoning`、无 `trigger already exists`）；**`enrich_ai_fts` trigger 存在**（`SELECT ... sqlite_master`，证明 004 跳过时由 003 建成）；**插一条 enrich(error IS NULL) → `items_fts.title_zh` 同步**（I9 end-to-end）；再 migrate 一次仍 exit 0。`rg reasoning src/airadar/migrations/` 无对 items_fts 的 reasoning 列引用。

### TASK-003 搜索 helper + 三处 call site
- Goal: `common.py` 加 `search_id_subquery`（§C）；timeline.py + curated.py 两处改用它（`_compute_items` append where；`_load_precomputed` 用子查询得 matching_ids）。
- 内部 verify: helper 单测（≥3→FTS 子查询、<3→LIKE 子查询且含 4 短字段+ESCAPE、空→None）；mypy + ruff。

### TASK-004 单元测试（语义变化 + 2 字兜底 + 注入 + reasoning 清理）
- Goal:
  - FTS（≥3）：`?q=OpenAI Blog`→openai_blog 两条；**`?q=Ada`→item-openai（author-only——"Ada" 不在其 source_name/title，证明命中来自 author 列；勿用 `Simon`，与 source_name "Simon Willison (Mastodon)" 碰撞会 vacuous）**；中文 title_zh 词命中（seed 需 enrich title_zh）。
  - **2 字 LIKE 兜底**：seed 2 字可命中的源名/作者，`?q=<2字>` 命中——**timeline 与 curated 各一条**（决策④双端点）。
  - **更新** `test_timeline_search_uses_fts_and_filters_results`(:358) 的 `?q=OpenAI` 期望为同源涌入（item-openai+item-claude）。**已审计全部 5 处依赖 `?q=OpenAI` 的断言**(:325/:358/:560/:571/:619)：仅 :358（timeline 全文搜索）结果变；其余 4 处 **survive、不要改**——item-claude 同源但不在 curated 集（:325/:619 curated 仍 `["item-openai"]`），item-openai 按 published_at 排第一（:560/:571 `items[0]` 不变）。
  - **移除** `test_web_routes.py` 的 `WHERE reasoning != ''` 查询（reasoning_count 块，约 :352/:355；列已删是硬错误非改值，grep 定位）；确认 `test_phase1_bootstrap.py:40` items_fts 断言不破。
  - **LIKE 通配符**：`?q=%`、`?q=_` 只命中含字面 %/_ 的行，不 match 全表。
- 内部 verify: `AI_RADAR_DB=/tmp/t.db pytest tests/test_web_routes.py tests/test_phase1_bootstrap.py -q` 全绿。

### TASK-005 端到端验证（真实数据 = 已应用 004 的生产 schema）
- Goal: `sqlite3 data/radar.db ".backup '/tmp/radar-fts.db'"`（此 backup 即 prod-state：004 已应用）→ `AI_RADAR_DB=/tmp/radar-fts.db ... admin db migrate`（跑两次确认幂等）→ 临时 serve。
- verify: §L2 C1–C6 + I1/I2/I6/**I9**/I7。**I9 在此 `.backup` 库上跑是 CRITICAL**——这是唯一能暴露"trigger 只放 004 会在生产坏掉"的库状态（004 已应用→跳过，全靠 003 建 enrich_ai_fts）。

### TASK-006 文档 + 关闭 issue
- Goal: ux-contract HP-4/TL-3 补"搜索匹配 标题/正文/来源名/作者/中文标题；≥3 字全文、<3 字短字段兜底（双端点）"；CHANGELOG 记语义变化（搜源名→该源文章 + reasoning 不再参与搜索）；general.md issue 标 [resolved]（注明 slug 有意排除）；architecture.md FTS 小节更新字段集 + 注明 003 现为最终 schema。
- 内部 verify: rg 确认；issue 非 [open]。

---

## L2 — 用户视角 verify（🤖=agent 独立，无人工 gate）

### 消费者 gate

| # | 验证 | 步骤 | 判据 |
|---|---|---|---|
| C1 | timeline 搜源名（≥3 FTS） | `curl '.../timeline?q=十字路口'` | 含 `source_id='wx_crossing'` |
| C2 | timeline 2 字源名（LIKE 兜底） | `curl '.../timeline?q=歸藏'` | 含 `source_id='wx_guizang'` |
| C3 | 中文标题词命中（title_zh） | 取某 enrich item 的 title_zh 词搜 | 该 item 出现 |
| C4 | title 词搜不破 | `curl '.../timeline?q=Marvis'` | 仍返回该文章 |
| **C5** | **curated 搜源名命中其精选（≥3 FTS，双端点 work）** | 先 `SELECT DISTINCT source_id...` 查 curated 里存在的源，对其源名 `curl '.../curated?q=<该源名>'` | 返回该源的精选条目（证明 curated 搜索覆盖 source_name；不依赖"恰好歸藏在精选"）。curated <3 字 LIKE 的确定性验证见 I8 |
| **C6** | **timeline 搜作者（≥3 FTS，决策③ author first-class）** | 取一个**作者名不出现在其 source_name/title 的** item（seed 用 `Ada`(item-openai)；**勿用 `Simon`**——与 source_name "Simon Willison (Mastodon)" 碰撞致 vacuous；真实库取 author-only 的）`curl '.../timeline?q=<该作者>'` | 含该作者文章，**且该作者名不出现在其 source_name/title**（证明命中来自 author 列、非 source_name 兜底——否则 C6 形同虚设，测不出 author first-class） |

### 内部机制检查

| # | 验证 | 判据 |
|---|---|---|
| I1 | FTS schema | 列集**恰好** = item_id/title/content_text/source_name/author/title_zh（6 列）——含 source_name/author/title_zh，无 reasoning、无 summary_zh |
| I2 | 回填 | wx_crossing item 的 fts 行 source_name="十字路口Crossing"、author 正确、enrich item 的 title_zh 非空 |
| I3 | trigger 同步 | 单测：item INSERT→fts 有 source_name/author；**item UPDATE→fts 刷新且不 orphan、且 title_zh 不被重置**（enrich 维护）；enrich INSERT(error IS NULL)→title_zh 更新（验 003 的 enrich_ai_fts）；**失败 enrich(error 非空)→不覆盖已有 title_zh**；sources name UPDATE→source_name 更新。（"已应用 004 的库上 trigger 存活"的端到端另见 I9） |
| I4 | 引擎切换 | helper 单测（≥3 FTS / <3 LIKE 短字段 / 空 None） |
| I5 | 套件全绿（含语义变化） | `pytest tests -q -m "not integration"` + ruff + mypy 全绿（含已更新语义变化测试，非"行为不回归"） |
| **I6** | **migrate 幂等（CRITICAL）** | 已过 004 的库连跑两次 migrate，第二次 exit 0，无 `no such column: reasoning`、无 `trigger already exists`（★仅 exit 0 不够——还需 I9，被销毁的 trigger 静默 no-op 不报错） |
| **I7** | **LIKE 注入** | `?q=%` 不 match 全表（单测断言） |
| **I8** | **curated <3 字 LIKE 确定性 gate（决策④）** | 单测：seed 一个 source_name 含已知 2 字 token 的 curated 行（**必须 populate `summary_json`**——否则 `_load_precomputed` 命中 0 行退化到 `_compute_items`，静默把同一路径测两次而非覆盖生产路径 `_load_precomputed`；现有 `_seed_db` 不 populate summary_json），断言两个函数对该 2 字 query 都命中它（不依赖真实库精选数据） |
| **I9** | **enrich_ai_fts 存活 + title_zh 同步（CRITICAL #3，必须在已应用 004 的库验）** | 在**已有 `004_enrich_stage` ledger 行的库**（生产 `.backup`，**非** cold temp——cold 库 004 会跑、给 false green）上 `migrate()` 后：`SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='enrich_ai_fts'` 返回 1（证明 004 跳过时由 003 建成）；且插一条 enrich(error IS NULL) → `items_fts.title_zh` 同步更新（end-to-end）。★这是唯一能暴露"trigger 只放 004 会在生产坏"的库状态——exit-0 gate 与 cold-DB I9 都盲 |

**交付 gate**：C1–C6 贴 curl 证据 + I5/I6/I7/I9 全绿。

---

## Defaulted Decisions

| 决策 | 选择 | 理由 | 反转成本 |
|---|---|---|---|
| LIKE 兜底字段 | 短字段（source_name/author/title/title_zh），不含 content_text | 2 字搜正文噪音大、非诉求；正文搜由 ≥3 字 FTS 承担 | 低 |
| 引擎切换阈值 | `len>=3` 用 FTS | 与 trigram 下限对齐 | 低 |
| 排序 | 不改（published_at DESC），混排 | 痛点已解（源文章出现）；优先排序是增强 | 低（R3 触发条件） |
| ?q=OpenAI 测试 | 更新期望为同源涌入 | source_name 入索引必然语义变化 | 低 |

---

## Risks

| # | 风险 | acceptance | trigger response |
|---|---|---|---|
| R1 | migrate 重建需写锁，:8000 launchd serve 持锁 | 已知 | TASK-005 用 `.backup` 库；生产由 launchd serve 重启时 migrate |
| R2 | 003 重建非原子（executescript 隐式 commit、无显式事务），并发读可能见空 FTS 窗口 | migrate 在 backup/重启窗口跑、无并发读 | 重启窗口短；需在线迁移再包 BEGIN/COMMIT |
| R3 | 搜源名词→该源全部文章混排（噪音） | 决策①已接受；核心诉求达成 | observable：若 C1 示例查询中源匹配 item 被大量内容碰巧命中挤到很后，加来源匹配优先 rank |
| R4 | content_text 不在 <3 字兜底；若日后要 2 字搜正文加 LIKE | 当前非诉求；`content_text LIKE` 全表扫 **~127ms**（非 19ms） | 届时评估加 FTS prefix index 而非裸 LIKE，而非简单"性能允许" |
| R5 | title_zh 仅 enrich 后有；未 enrich item 其 title_zh 为空 | enrich 是常规阶段 | 未 enrich item 仍可由 title/content/source_name/author 命中；C3 用 enrich item 验证 |
| R6 | 改 003/004 历史 migration | 003 每次 migrate 重跑=幂等 schema 声明（生产权威）；004 冷启动一次（生产已应用、跳过）。改的是已失效语句 + schema | I6（已应用 004 的库 migrate 两次 exit 0）守住；TASK-001/002 verify 覆盖 |
| R7 | enrich_ai_fts 权威定义必须在 **003**（非只在 004）——`db.py:51` 只跳过 004；生产 004 已应用→永久跳过，trigger 只放 004 则生产永不创建（fix-a 的错，已反转） | reviewer 实证 5 次冷跑 + 生产升级 + live prod DB（004 应用于 2026-05-12、今无 enrich_ai_fts）；我已核实 `db.py:50-67` | **I9 在已应用 004 的库（生产 `.backup`）上守住**。⚠️ 任何把 enrich_ai_fts 改成"只在 004"的改动都会让生产静默失效——exit-0 与 cold-DB I9 都盲，必须在 prod-state 库验 I9 |

---

## 引用索引

| 引用 | 路径 | 用途 |
|---|---|---|
| FTS 定义（改，含 enrich_ai_fts） | `migrations/003_add_fts5_search.sql`（全文，见 §A）；003 每次跑=生产 schema 权威 | 改为最终 schema + 6 triggers |
| enrich migration（改） | `migrations/004_enrich_stage.sql`：:62 DROP TABLE + :64 RENAME（rebuild，仅冷启动跑）；:69-71 删 reasoning UPDATE；:73-78 改成 belt enrich_ai_fts | 删 reasoning UPDATE；belt 补冷启动窗口 |
| migrate 机制（已 trace） | `db.py:50-67`：`_migration_already_applied` 只对 `004_enrich_stage.sql` 返回 True(:51)→ledger 有则整个跳过 004(:64)；003 等其余每次跑。`_execute_migration_idempotent`(:31-47) 对含 `CREATE TRIGGER` 的文件走 executescript | I6/I9/R7 依据 |
| timeline search | `web/routes/timeline.py:22-37` | 用 helper |
| curated search（两处） | `web/routes/curated.py:57+`（`_load_precomputed`，读 summary_json、无 join）、`:105-141`（`_compute_items`，join sources） | 用 helper；_from_summary 用 matching_ids |
| fts_phrase_query + 新 helper | `web/routes/common.py:248-254` | FTS phrase + search_id_subquery |
| items/sources/enrich | `migrations/001_init.sql:5-28`；enrich output_json `$.title_zh` | 字段来源 |
| 搜索/FTS 测试 | `tests/test_web_routes.py:23-139`(_seed_db),`:351-352`(reasoning 断言→删),`:358`(语义变化),`:616`(curated)；`tests/test_phase1_bootstrap.py:40` | 改/加测试 |
| 真实验证数据 | DB `wx_crossing`(十字路口Crossing)/`wx_guizang`(歸藏的AI工具箱) | C1/C2/C5 |
