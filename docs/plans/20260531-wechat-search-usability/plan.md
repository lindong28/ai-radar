# Plan：中文/微信公众号源搜索可用性修复（#4 + #6 + #5）

> **Long-task mode** — 本 plan 启用状态外部化协议。implementer 必读 `~/.claude/references/long-task-protocol.md`：
> 每步开工/收尾同步同目录 `state.md`（任务条目 [pending]→[in_progress]→[done]）与 `journal.md`（决策/踩坑/verify 证据）。
> 交付前过协议的"交付前验证"。state.md 是真值，plan.md 是契约。

---

## 输入与范围

- **来源**：`/custom:resolve-issues` 本轮 triage，goal = 解决 `docs/issues/general.md` 中影响用户体验（user consumer）的 issue。已逐条核实 + 用户批准。
- **本 plan 收口三条 issue**（同一"中文/公众号源搜不到、搜不全、简繁不通"场景的三半）：
  - **#4**（HIGH，前置）：prefilter/score 候选用 `published_at >= cutoff` 永久排除 backfill 历史文章。
  - **#6**（依赖 #4）：搜来源名结果被同名高产源按时间淹没，无来源匹配优先 + 多样性。
  - **#5**（独立）：搜索不做简繁归一化，搜简体匹配不到繁体源。
- **不在本 plan**：general #1（nitter 单点）/#2（wewe 2h 盲区）/#7（覆盖率监控）— ops/监控类，本轮 OUT；ux-contract-issues 7 条 — 契约文本，本轮 OUT。
- **无 schema/migration 改动** — 三个修复都在 runner 候选 SQL / web 查询层 / 搜索 query 层 + 一个新依赖（opencc），不碰表结构。故项目记忆"migration 004 不重跑"约束**不适用**本 plan。

---

## 背景：用户真实场景

用户在 aiplanet.live 搜索框输入公众号名/作者想找该来源文章：
- 搜 **"十字路口"** → 想要 `wx_crossing`（十字路口Crossing 公众号，10 篇）的文章。
- 搜 **"歸藏"**（繁）→ 想要 `wx_guizang`（歸藏的AI工具箱 公众号，10 篇）的文章。
- 搜 **"归藏"**（简）→ 同上，但用户自然输入简体。

三个场景当前都坏：文章根本不可见（#4）/ 被同名 X 账号 op7418_x 淹没（#6）/ 简繁不通（#5）。搜索框 → `/api/v1/timeline?q=`（`web/static/app.js:613`）。

---

## L1：最终产物 + 使用方式

- **产物**：ai-radar 代码修复 bundle（runner 候选选取 + web 搜索排序 + 搜索 query 简繁扩展）+ 一次存量数据 backfill。
- **使用者 A（端用户）**：aiplanet.live 访客，用搜索框按来源名/作者找文章。拿到结果用来**判断该来源近期发了什么、决定打开哪篇深读**。
- **使用者 B（implementer / reviewer）**：读本 plan.md 落地与审查。
- **成功画面**：用户搜公众号名/作者（简或繁），该来源的文章**出现在首屏、不被同名高产源完全淹没、简繁互通**；wechat 源覆盖率从 2/20 回归到接近 feed/x 水平。

---

## 取舍偏好 + 三层影响

| 维度 | 用户偏好 | 三层影响 |
|---|---|---|
| 一致性 vs 新代码路径（#4） | 偏**一致性**：复用 enrich 已验证的 `fetched_at`-only 范式 | L3：candidate SQL 最小改动，不新增 backfill 模式 |
| 完整解决 vs 简单（#6） | 偏**完整**：要公众号真正露出，接受额外复杂度 | L2：verify 必须断言 wx_guizang 在首屏；L3：window 函数做来源多样性 |
| 准确 vs 零依赖（#5） | 偏**准确**：接受引入 opencc 依赖换标准简繁映射 | L3：query 层 opencc 扩展；不动 FTS 索引 |

---

## 已锁定的设计决策（用户访谈 2026-05-31）

1. **#4**：prefilter + score 候选选取改为 **`fetched_at`-only**（去掉 `published_at >= cutoff`），对齐 `enrich/runner.py` 的正确范式。已有 `NOT EXISTS prefilter eval` 去重防重复处理。
2. **#6**：**来源匹配优先 + 同名来源多样性**——来源名/作者命中的条目排在内容命中之前；命中层内按"来源轮转"（window 函数 `ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY published_at DESC)`）保证每个命中来源都在首屏露出。
3. **#5**：**query 层简繁双向扩展 + opencc**——搜索时把 query 展开成 简体+繁体 两形态 OR 匹配，不动 FTS 索引。opencc 优先 pure-python 实现避免编译（见 Phase 3）。

---

## Phase 1 — #4 backfill 可见性修复（前置）

### 当前状态（核实事实）
- `src/airadar/prefilter/runner.py:87`：`item_filter = "i.fetched_at >= ? AND i.published_at >= ?"`，`params.extend([cutoff, cutoff])`（行 88）。
- `src/airadar/scorer/runner.py:74`：`params: list[Any] = [cutoff, cutoff]`；行 91-92：`WHERE i.fetched_at >= ? AND i.published_at >= ?`。
- `src/airadar/enrich/runner.py:96`：`item_filter = "i.fetched_at >= ?"`（**正确范式，不改，作参照**）。
- 数据（本地 `data/radar.db`，已生产同步，max fetched_at 2026-06-01Z）：wechat 整体 prefilter 2/20（10%）vs feed 72% / x 81%；wx_crossing 10→1、wx_guizang 10→1。18 篇未处理项均有完整正文（2.7k–16k 字），fetched_at 均为 2026-06-01T04:31:28Z（近期），published_at 跨 2026-04-16~05-28（老）。

### 改什么
1. **`src/airadar/prefilter/runner.py`**：
   - 行 87：`item_filter = "i.fetched_at >= ?"`（去掉 ` AND i.published_at >= ?`）。
   - 行 88：`params.extend([cutoff])`（删掉第二个 cutoff）。
   - `item_ids is not None` 分支不变。
2. **`src/airadar/scorer/runner.py`**：
   - 行 74：`params: list[Any] = [cutoff]`。
   - 行 91-92：`WHERE i.fetched_at >= ?`（去掉 ` AND i.published_at >= ?`）。
   - 若 score runner 也有 `item_ids`/`force` 分支，保持与 prefilter 一致的结构。
3. **测试反转（关键）**：
   - `tests/test_prefilter.py:107` `test_run_prefilter_since_requires_recent_published_at_not_only_recent_fetch` **编码了 bug 为预期行为**（断言 recent-fetch + old-publish 的 item `processed == 0`）。**改写**为断言该 item **被处理**（`processed == 1`、`item_evaluations` 计数 1），**重命名**为 `test_run_prefilter_includes_recently_fetched_backfill_regardless_of_old_published`。
   - `tests/test_scorer.py`：检查是否有类比的 published_at-gate 断言（约行 129-152 区域有 `_recent_iso(30)` 种子），若有，按 `fetched_at`-only 新行为一致更新；若该种子 fetched 也老（非 backfill 场景）则无需改——以实际断言为准。

### 内部 verify（L3，agent 独立跑）
- `uv run pytest tests/test_prefilter.py tests/test_scorer.py -q` 全绿（含新改写的 backfill 测试）。
- `uv run ruff check src/airadar/prefilter/runner.py src/airadar/scorer/runner.py` + `uv run mypy` 无新增错误。
- 新增针对性单测（建议 `tests/test_prefilter.py`）：seed 一个 `fetched_at=recent, published_at=老` 的 item，`run_prefilter(since="24h")` → `processed == 1`；同时 seed 一个 `fetched_at=老` 的 item → 不被选中（确认窗口仍按 fetched_at 生效，没退化成全表）。

### 数据 backfill（落地后执行）
> 这是 implementer 执行步，非用户决策。在生产同步的 `data/radar.db` 上跑。
- 因 18 篇 fetched_at 为近 2h，理论上 `--since 24h` 即可命中；为稳妥用更宽窗口或精确 id。推荐：
  ```bash
  uv run airadar prefilter --since 7d
  uv run airadar score --since 7d
  uv run airadar enrich --since 7d
  uv run airadar curate
  ```
  （`prefilter` 也支持 `--item-id-file` 精确指定；`enrich` runner 有 `item_ids` 参数。窗口法更简单，去重 clause 防止重复处理已评估项。）
- backfill 后即时核查（写入 journal）：
  ```bash
  sqlite3 data/radar.db "
  SELECT s.id, COUNT(i.id) total,
    SUM(CASE WHEN EXISTS(SELECT 1 FROM item_evaluations e WHERE e.item_id=i.id AND e.stage='prefilter') THEN 1 ELSE 0 END) prefiltered
  FROM sources s LEFT JOIN items i ON i.source_id=s.id WHERE s.kind='wechat' GROUP BY s.id;"
  ```

### state.md 任务条目
- [pending] P1.1 prefilter+score runner 候选改 fetched_at-only
- [pending] P1.2 反转/新增 prefilter+score 测试
- [pending] P1.3 跑 backfill（prefilter→score→enrich→curate）并核查覆盖率

---

## Phase 2 — #6 来源匹配优先 + 同名来源多样性（依赖 P1）

### 当前状态（核实事实）
- `src/airadar/web/routes/timeline.py:143`：`ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC` — 纯时间排序，搜索命中后无来源优先。
- `src/airadar/web/routes/common.py:257-277` `search_id_subquery`：≥3 字走 FTS MATCH（title/content/source_name/author/title_zh 全字段）；<3 字走 LIKE（title/author/source_name/title_zh）。返回的是 **item_id 集合**，不带"命中字段类型"。
- `src/airadar/web/routes/curated.py:134-135`：curated 搜索同样 `ORDER BY date(...) DESC, i.published_at DESC, ...`。
- 数据验证：搜"歸藏"（2 字 LIKE 路径，匹配 source_name）→ op7418_x（source_name="歸藏"，96 篇 ai-prefiltered，近期）+ wx_guizang（source_name="歸藏的AI工具箱"，P1 修后 ~10 篇，published 较老）。**两源都命中 source_name**，纯时间排序下 op7418_x 近期推文把 wx_guizang 全部压到首屏外。已用 window 函数 PoC 验证：`ROW_NUMBER() PARTITION BY source_id` 后两源交替（wx_guizang 落到位置 2/4/6），首屏即露出。

### 改什么（仅在 `q` 命中时改变排序；无 `q` 时排序不变）
1. **`timeline.py` 排序**（核心）。仅当 `search_subquery` 存在时启用新排序：
   - 在外层 SELECT 增列：
     - `is_source_match`：`CASE WHEN (s.name LIKE ? ESCAPE '\' OR i.author LIKE ? ESCAPE '\') THEN 1 ELSE 0 END`（参数复用 q 的 LIKE 形态；与 search 同源的 q）。
     - `intra_source_rank`：`ROW_NUMBER() OVER (PARTITION BY i.source_id ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC)`。
   - 搜索态 `ORDER BY is_source_match DESC, intra_source_rank ASC, i.published_at DESC, i.fetched_at DESC, i.id DESC`。
     - 效果：来源名/作者命中的条目整体优先；命中层内按"每源第 1 篇、每源第 2 篇…"轮转 → 每个命中来源都在首屏露出；非命中（纯内容命中）按原时间序殿后。
   - **分页一致性**：搜索态用 offset 分页（`timeline.py:111` `offset = (page-1)*limit`，cursor 仅非搜索态）。window 排序在 offset 分页下稳定（同一 q 下确定序），无需改 cursor 协议。确认 `next_cursor` 在搜索态本就为 None / offset 递进（见 timeline.py:170-178）——保持现状，仅排序键变化。
2. **`curated.py` 排序**：搜索态（`search_subquery` 存在）应用同样的 `is_source_match` + 来源轮转，与 timeline 保持一致体验。curated 结果集是 curated_items 子集（rank 受限），改动同形：在 `_compute_items` 的 ORDER BY 前置 `is_source_match DESC, intra_source_rank ASC`，非搜索态保持 `date(...) DESC` 日期分组序不变。
   - 若 curated 的日期分组语义与来源轮转冲突（curated 按天分节），以"搜索态优先来源轮转、非搜索态保持日期分组"二选一实现；**搜索态不需要日期分组**（用户在搜全局，不在看某天），故搜索态可整体走来源轮转序。
3. **LIKE 转义**：构造 `is_source_match` 的 LIKE 形态时复用 `common.py` 已有的 escape（`%`/`_`/`\` 转义），与 `search_id_subquery` 的 LIKE 分支一致，避免特殊字符注入/误匹配。
4. **抽取共用（单一变体源）**：`is_source_match` 的 LIKE 形态构造提到 `common.py` 一个 helper（如 `source_match_like(q)`）供 timeline + curated 复用。**该 helper 必须直接消费 Phase 3 的 `expand_st_variants(q)` 输出构造 LIKE 变体集合——只有一处变体来源、一处 escape，不另起平行构造**（避免 #5/#6 两套重叠简繁逻辑漂移）。#5 deferred 时 helper 退化为单一原 query（无变体）。

### 内部 verify（L3）
- 新增 web 路由测试（`tests/test_web_routes.py` 或新文件）：seed 两个 source（一个高产 X 同名、一个低产 wechat 同名前缀）+ 各若干 item，`GET /api/v1/timeline?q=<源名>` → 断言**首屏（page1）items 同时包含两个 source 的条目**（wechat 源至少 1 条在 page1），且来源命中的排在纯内容命中之前。
- 断言无 `q` 时排序与改动前一致（纯时间序回归测试）。
- `ruff` + `mypy` clean。

### state.md 任务条目
- [pending] P2.1 common.py 增 source_match helper（LIKE 转义复用）
- [pending] P2.2 timeline.py 搜索态排序：is_source_match + 来源轮转 window
- [pending] P2.3 curated.py 搜索态同款排序
- [pending] P2.4 web 路由测试：首屏含同名两源 + 来源优先 + 无 q 回归

---

## Phase 3 — #5 简繁归一化（query 层 + opencc）

### 当前状态（核实事实）
- `items_fts` 用 `tokenize='trigram'`（`common.py` search：≥3 字 FTS MATCH，<3 字 LIKE 兜底）；FTS 与 LIKE 都是字面 codepoint 匹配。
- 数据：items_fts 含简体"归藏" 1 行 vs 繁体"歸藏" 196 行——搜简体匹配不到繁体源。
- opencc **未安装**（`pyproject.toml` deps 无）。

### 改什么
1. **新增依赖**：在 `pyproject.toml` deps 加 opencc。
   - 安装顺序：pure-python `opencc-python-reimplemented` → C++ `opencc`（OpenCC pip 包）→ **都装不上则跳过 #5**（用户已拍 fallback：仅发 #4+#6，#5 落 `docs/issues/general.md` 新 follow-up issue，**不静默丢**）。journal 记最终选择。
   - **必须先 `mcp__context7__query-docs`** 查 opencc API（`OpenCC('s2t')` / `convert()` 用法、配置名），再写代码——遵循"改 public 软件配置前查官方文档"。
2. **query 层双向扩展**：在 `common.py` 新增 `expand_st_variants(q: str) -> list[str]`：
   - 返回 `q` 的去重变体集合：原文 + s2t（简→繁）+ t2s（繁→简）。对纯英文/数字/已统一的输入，变体集合可能只含原文（无副作用）。
   - 用 opencc 做转换：`OpenCC('s2t')`、`OpenCC('t2s')`（converter 实例**模块级缓存**，避免每请求重建——opencc 初始化有开销）。
3. **接入 `search_id_subquery`**（`common.py:257`）：
   - **≥3 字 FTS 分支**：把单一 `MATCH '"q"'` 改为多变体 OR：`items_fts MATCH '"v1" OR "v2"'`（FTS5 支持 OR）。用 `expand_st_variants(q)` 的变体构造 phrase。
   - **<3 字 LIKE 分支**：把 4 个 `LIKE ?` 扩成对每个变体 OR（title/author/source_name/title_zh × 变体）。参数随之展开。
   - 注意：trigram FTS 仍要求单变体 ≥3 字；2 字简繁仍走 LIKE 分支（变体也 2 字），逻辑一致。
4. **#6 的 `is_source_match` 也用变体**：Phase 2 的 `source_match_like` helper 应基于 `expand_st_variants` 的变体集合 OR 匹配，保证"搜简体归藏 → 命中繁体源名 → 来源优先排序"端到端连通（否则 #5 让简体命中了 item，但 #6 的 is_source_match 用原简体 LIKE 繁体源名仍为 0，公众号又被降权）。**Phase 2 与 Phase 3 在此交汇，implementer 实现时让二者共用变体集合。**

### 内部 verify（L3）
- 单测 `expand_st_variants`：`"归藏"` → 含 `"歸藏"`；`"歸藏"` → 含 `"归藏"`；纯英文 `"openai"` → 仅 `["openai"]`（无 CJK 变体噪音）。
- 单测 `search_id_subquery`：mock/真实 db，`q="归藏"`（简）能命中 source_name 为繁体"歸藏…"的 item id。
- web 路由测试：`GET /api/v1/timeline?q=归藏`（简）→ 返回含 wx_guizang / op7418_x（繁体源）。
- `ruff` + `mypy` + opencc import clean；`uv run pytest` 全绿。

### state.md 任务条目
- [pending] P3.1 context7 查 opencc → 选 pure-python opencc 加入 deps
- [pending] P3.2 common.py expand_st_variants + 模块级 converter 缓存
- [pending] P3.3 search_id_subquery 接入变体（FTS OR + LIKE OR）
- [pending] P3.4 #6 source_match 复用变体集合（交汇点）
- [pending] P3.5 单测 + 路由测试（简体命中繁体源）

---

## L2：端到端用户视角验证（implementer-executable，全部 agent 可独立跑）

> 在生产同步 `data/radar.db` 上，启动 web 服务后用 `curl`/sqlite 验证。覆盖率类判据用 **expected-vs-actual 对比，gap 必须可解释**，不停在存在性（≥1 命中）。
> 启服务：`uv run airadar serve`（或项目既有 `run.sh`）；API base `/api/v1`。
>
> **"首屏" 的定义（贯穿 #6 验证）**：`首屏 = page1 = 前 limit 条`；timeline 默认 `limit=50`（`timeline.py:26` `Query(default=50)`）。所有"首屏 / 不被淹没"判据按"目标来源首条的排序位置 < limit"**机械判定**，不用"看起来在前面"。
>
> **覆盖率 baseline 是动态且 upstream 含随机性**：prefilter/score 是 LLM 判定，逐次运行可能判不同子集。故 V1/V2/V4 的 expected **不是"原始 10 篇"**，而是"**本次 backfill 运行后匹配 timeline 完整可见性判据的条目数**"（从 `data/radar.db` 实时查），actual 对其比对。可见性判据须与 timeline 实际查询一致（`timeline.py:62,69,86-108`），**含三部分**：(a) `deduped_item_clause`（同源同 URL 仅留最新，`common.py:115`）；(b) `prefilter is_ai_related=1`；(c) `无 scoring 行 OR 最新 scoring relevance≥6.5`；且 `has_prefilter` 成立。**关键**：#4 re-fetch 场景会产生同源同 URL 重复行，dedup 会合法抑制旧行——baseline **必须含 dedup**，否则会高估 expected、把 dedup 抑制误报成 unexplained gap。prefilter 判 AI 但尚未 score 的项也算可见（全量 backfill 后正常应都已 score）。任何 unexplained 缺口（有正文 + 匹配完整判据却搜不到）= 失败。

| # | 场景 | 命令（示意） | 期望（expected-vs-actual，gap 须可解释） | 人机边界 |
|---|---|---|---|---|
| V1 | #4 覆盖率回归 | 上方 backfill 核查 SQL | 每个 wechat 源：`prefiltered_evaluated + 可解释排除 == total`，**零 unexplained 缺席**；未可见项逐篇归因（prefilter=非AI / score<6.5）。expected = 本次运行 prefilter 判 AI 数（动态查），非原始 10 | agent 独立 |
| V2 | 搜"十字路口" 覆盖 | `curl -s '/api/v1/timeline?q=十字路口&limit=50' \| jq '[.data.items[]\|select(.source_id=="wx_crossing")]\|length'`，对比 sqlite 查 wx_crossing 可见（prefilter 判AI + score≥6.5）总数 | 跨页 reachable 的 `wx_crossing` 条数 ≈ 其可见总数（**expected-vs-actual，非 ≥1**），且首屏（位置<50）即出现多条；缺口须可解释 | agent 独立 |
| V3 | 搜"歸藏"（繁）首屏多样性 | `curl -s '/api/v1/timeline?q=歸藏&limit=50' \| jq '[.data.items[].source_id]'` | **page1（前 50）同时含 `wx_guizang` 与 `op7418_x`**，且 `wx_guizang` 首条位置 < 50（公众号不被淹没）；来源命中整体排在纯内容命中前 | agent 独立 |
| V4 | 搜"归藏"（简）简繁互通+覆盖 | `curl -s '/api/v1/timeline?q=归藏&limit=50' \| jq '[.data.items[]\|select(.source_id=="wx_guizang")]\|length'`，对比 sqlite 查 wx_guizang 可见总数 | 简体 query 命中繁体源：reachable `wx_guizang` 条数 ≈ 其可见总数（**非 ≥1 存在性**），证明 2 字 LIKE 简繁扩展端到端生效 | agent 独立 |
| V5 | 无 q 排序回归（保留承诺） | `curl -s '/api/v1/timeline?limit=50'` 改动前后输出 diff（**权威 gate**）+ P2.4 单测断言 no-q 路径排序键不变 | 无 q 时结果顺序与改动前一致（纯时间序），#6 完全不生效。**输出 diff 为权威判据**；若实现用单一参数化 ORDER BY（无独立 no-q 分支），单测断言"无 q 时退化为原时间序键"而非字面字符串匹配 | agent 独立 |
| V6 | curated 两态 | 搜索态 `curl '/api/v1/curated?q=十字路口'`；非搜索态 `curl '/api/v1/curated'` 改动前后输出 diff（**权威 gate**）+ P2.3 单测 | 搜索态来源优先（若 wx 进 curated）；非搜索态结果顺序与改动前一致（日期分组），**输出 diff 为权威判据**；单测断言"无 q 时退化为原日期分组键"而非字面字符串匹配 | agent 独立 |
| V7 | 全测试 + 静态检查 | `uv run pytest -q && uv run ruff check . && uv run mypy src` | 全绿 | agent 独立 |

> 视觉/主观兜底（可选人工 gate）：在 aiplanet.live 或本地前端实际搜"十字路口/歸藏/归藏"，肉眼确认公众号文章出现在首屏。V2–V4 的 API 断言已先兜底主要风险，人工仅做最终体感确认。
> #5 deferred 时（opencc 两变体都装不上）：V4 标 N/A 并在交付说明注明 #5 转 follow-up；V1/V2/V3/V5/V6/V7 仍须全过。

---

## Defaulted Decisions（planner 自拍，reviewer 审）

| 决策 | default | 理由 |
|---|---|---|
| #6 仅在 `q` 命中时改排序，无 q 时不变 | 是 | 来源优先只对"搜来源名"场景有意义；无 q 的 feed 浏览保持纯时间序，避免影响主 feed 体验（V5 回归守住） |
| #6 来源轮转用 `ROW_NUMBER PARTITION BY source_id` 而非"每源 top-N 截断" | 是 | 轮转保留全部命中条目只改顺序，分页仍可翻到尾部；截断会丢条目、违反 HP-4「返回该来源内容」 |
| #5 query 层扩展而非索引归一化 | 是（用户已选） | 不动 FTS 索引、无需 reindex、reversal 成本低；正文简繁互通非本轮诉求 |
| opencc 选 pure-python reimplemented；两变体都装不上 → 仅发 #4+#6、#5 转 follow-up issue | 是（用户 2026-06-01 拍板 fallback）| 避免 C++ 构建链风险；两 opencc 变体都装不上概率极低，不该阻塞高价值 #4+#6。implementer 按"pure-python → C++ → 都失败则跳 #5 落 follow-up"顺序，不中途问 |
| backfill 用宽 `--since` 窗口而非 `--item-id-file` | 是 | 18 篇 fetched 为近 2h，宽窗口足够命中；去重 clause 防重复。更简单 |
| curated 搜索态放弃日期分组改走来源轮转 | 是 | 搜索是全局找来源，不是看某天；日期分组在搜索态无意义 |

---

## Risks

| Risk | 缓解 |
|---|---|
| score runner 结构与 prefilter 不完全对称，盲改 params 索引错位 | 改前 Read `scorer/runner.py` 完整 `_candidate_rows`，按实际行号改；pytest 守 |
| 改 candidate 为 fetched_at-only 后，某源每轮 re-fetch 大量历史档案会一次性涌入 prefilter | 接受：`NOT EXISTS prefilter eval` 去重（prefilter+score 都有，已核实）仅让首次见到的新 item 处理一次。**Trigger response（agent 自主）**：backfill 前先 `--limit` 试跑 + 查待处理量；若某源单次 processed 超 ~200，改用 `prefilter --item-id-file`（18 条已知 id）精确处理，不放开全量窗口。journal 记实际处理量 |
| opencc 两变体（pure-python + C++）在实施/CI 环境都装不上，#5 无法落地 | 接受：尾风险（两变体都失败概率极低）。**Trigger response（用户 2026-06-01 已预排，不中途问）**：pure-python `opencc-python-reimplemented` → C++ `opencc` → 都失败则**仅发 #4+#6**，#5 转 `docs/issues/general.md` 一条新 follow-up issue，交付说明注明 #5 deferred + 原因。V4 随之标 N/A |
| opencc 变体扩展使 FTS/LIKE 参数数量变化，SQL 占位符与 params 数目不匹配 | 变体集合统一构造、占位符按变体数动态生成；单测覆盖参数计数 |
| window 函数排序在 curated 日期分组下语义冲突 | 搜索态走来源轮转、非搜索态保日期分组，二态分支清晰；V6 守 |
| #5/#6 交汇漏接（简体命中 item 但 source_match 仍按简体 LIKE 繁体源名=0） | P3.4 明确二者共用变体集合；V4 端到端守 |

---

## 文档同步（交付前）

按 docs-organization-protocol：
- **CHANGELOG.md**：用户可感知变化（搜索可按公众号名/作者找到文章、简繁互通、来源不被淹没）。
- **docs/issues/general.md**：#4/#5/#6 标 `[resolved]` + Resolution 行（修复方式 + commit + verify 证据）。
- **ux-contract**（若 HP-4 需补"搜来源名结果排序：来源匹配优先 + 多样性"的承诺）：按协议 §4.6 走 ux-contract-issues 记一条 contract 演化候选，不在本 plan 直接改契约。
- **architecture.md / experiences**：若 backfill 候选语义变化值得留给后续 agent（"新源 backfill 按 fetched_at 窗口"），落 experiences。
- **依赖**：opencc 经 `pyproject.toml` + `uv sync` 安装，无需额外安装文档；若最终选 C++ `opencc` 且有构建前置，在 README 安装段或 `docs/operations` 补一行。
