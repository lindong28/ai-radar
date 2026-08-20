> **Archive status**: 已归档并上线。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> 解读流水线、KB 回写与展示闸门的当前运行口径见 [operations/wechat-ingestion.md](../../operations/wechat-ingestion.md)「微信文章解读与知识库回写」节与 [architecture.md](../../architecture.md)「Database」「关键设计」的「微信解读闸门」条，复用 ai-assistant summarizer 的裁决见 [ADR-007](../../adr/007-interpret-via-ai-assistant-summarizer.md)，跨仓契约见 [references/ai-assistant-contract.md](../../references/ai-assistant-contract.md)。以下为原 plan 正文，未修改。

# Plan — 微信文章解读（your-domain.example 新 tab + summarize-article 复用 + KB 回写）

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

---

## 输入

- 来源：用户口头任务（`/custom:create-plan`），无 spec.md。
- 跨两个 repo：
  - **ai-radar**（本 repo，`~/research/ai-radar`）= your-domain.example 网站 + 数据管线。
  - **ai-assistant**（`$AI_ASSISTANT_ROOT`）= 个人知识库，含 `summarize-article` / `search-knowledgebase` skill 与 summarizer 代码。
- 本 plan 含完整 L1/L2/L3：在 your-domain.example 新增"微信文章解读" tab，复用 ai-assistant 的 summarize-article 逻辑对每篇微信公众号文章做"总结 + 打标签 + 是否值得读判断"，值得读的展示在 tab，并把处理结果回写 ai-assistant 知识库供 `search-knowledgebase` 检索；ai-radar 自留一份独立网站数据（重复可接受）。

---

## L1 — 最终产物 + 使用方式

**产物**：your-domain.example 左栏新增独立 tab「微信文章解读」。
- 列表页 `/wechat`：展示所有"值得阅读"的微信公众号文章卡片，每卡片含**题目（原文标题）+ 摘要 + 标签**，复用现有暗色卡片风格。
- 详情页 `/wechat/<slug>`：点击卡片进入，渲染该文章的**完整结构化总结**（summarize-article 的 6 模块 markdown），独立可分享 URL，走现有 SSR 架构。
- 数据来源：ai-radar 已有的微信文章正文（`items.content_text`，kind=`wechat`，当前 159 篇）经新增 `interpret` 管线阶段处理。

**使用方式**：用户在 your-domain.example 浏览精选的微信文章解读（读摘要决定是否点开 → 读完整总结）；同时在 ai-assistant 用 `search-knowledgebase` skill 检索/分析这些微信文章的知识。两个使用面都必须 work。

**范围 / 约束 / 假设**：
- 只处理**启用的** wechat 源 item（`kind='wechat' AND enabled=1` = `wx_mp2rss`，plan 写定时约 159–161 篇，**随 15min cron 持续增长——所有篇数为示意，执行时一律实测**）。已停用旧源 `wx_guizang`(42)/`wx_crossing`(102) 及其 144 篇历史存量按用户知情决策**移除**（TASK-000，destructive，先备份 DB；其中约 116 篇当前仍在线展示，删除即从站点撤下，用户已确认）；移除后 `kind='wechat'` 即等于启用源实测计数（与 interpret 取数同口径）。interpret 取数恒用 `enabled=1` 谓词，即便清理延后也正确。
- 复用 ai-assistant 代码**零拷贝**：subprocess 调其 `summarize.sh` / `run.sh`（cwd=ai-assistant），不 fork、不 vendor。
- 网站请求时**只读 radar.db**，serve 路径不依赖 ai-assistant 文件系统（独立网站数据）。
- 假设：ai-assistant repo 在位、其 `.env` 含 `OPENAI_API_KEY`（embedding）+ `DEEPSEEK_API_KEY`（summarize）、`data/` 子模块可写、persona/参考文件就位（已验证：`data/summary_agent/<owner-user>/{persona.md,index.json(215条),article_summaries/{building_effective_agents_output.md,软件工程师头衔要没了ClaudeCode之父YC访谈_output.md}}` 均存在）。

---

## 已确认决策（AskUserQuestion 对齐结果）

| # | 决策 | 选择 | 影响 |
|---|---|---|---|
| A | "值得阅读"判定 | **统一用 `save_decision`** | tab 和 KB 同口径：`save_decision=true` 才上 tab，也才写 KB |
| B | 总结展示形态 | **独立详情页 `/wechat/<slug>`**（SSR、可分享 URL） | 新增 detail 视图 + markdown 渲染能力 |
| C | 复用与同步 | **全自动·pipeline 内一体 `interpret` stage** | subprocess 调 ai-assistant，一次同时产出总结→存 radar.db + 写 KB + embedding |
| D | 存量 | **仅回填启用源 wx_mp2rss 159 篇** | 一次性 `interpret --backfill`；之后管线增量处理 |
| E | 已停用旧源 | **移除 `wx_guizang`/`wx_crossing` 及其 144 篇存量**（destructive，先备份） | TASK-000 前置清理；移除后口径统一 |

---

## 取舍偏好 + 三层影响

- **生产稳定性 ≫ 实时性**：ai-radar 的 15 分钟 cron 是 your-domain.example 命脉。→ L3：`interpret` 是管线**最后一个 stage** 且 **fail-safe**（per-item try/except、永不 abort、preflight 缺依赖则跳过而非失败）。→ L2：verify 含"interpret 失败时 fetch→curate 与 web 不受影响"。
- **复用真实代码 ≫ 解耦**：用户要"继续复用" skill。→ L3：subprocess 调真 skill，接受跨 repo 运行时耦合（用 fail-safe + preflight 缓解）。
- **网站数据独立 ≫ 去重**：用户明确"维护独立网站数据，重复可接受"。→ L3：完整 summary_md/abstract/tags 落 radar.db，serve 不碰 ai-assistant。
- **召回（KB 全量可搜）vs 精选**：决策 A 选了统一 `save_decision`（偏召回，含"可跳过但有次级可实现项"）。→ L2：tab 文章数 == `save_decision=1` 计数。

---

## 架构与数据流

```
ai-radar pipeline (cron 每 15 分钟，pipeline.sh):
  fetch → prefilter → score → enrich → curate → interpret(NEW, 末位, fail-safe)

interpret stage（src/airadar/interpret/runner.py）逐条处理未处理的 wechat item：
  0. preflight：AI_ASSISTANT_ROOT 存在且 summarize.sh 可执行？否 → log+skip 整个 stage（不 fail pipeline）
  1. 写临时正文文件 tmp/interpret/<item_id>.md  内容 = "# <title>\n\n<content_text>"
  2. --check-url <item.url>（subprocess, cwd=ai-assistant）
       命中 → 该文已在 KB（KB 只存 save_decision=true）⇒ 值得读：
              从 check-url 返回的 `summary_file_path` 读 KB 总结文件 → 填 radar.db
              （summary_md=文件内容，abstract=文章概况首段，tags 取 index 条目，
               recommendation 用 schema 同款正则从总结解析，save_decision=1，kb_synced=1，不调 LLM）
              **trigger response**：summary_file_path 缺失/文件不可读 → fallback 走 step 3 正常 summarize
       未命中 → 走 3
  3. summarize.sh --input <tmpfile> --user <owner-user>  → 解析 stdout JSON（batch_dir + result）
       注意：stdout 的 result 不含正文（schema.to_meta_dict 第 79 行 pop summary_md）；
       完整总结只在 <batch_dir>/<slug>_summary.md（core.py:232），summary_md/abstract 从该文件读
  4. 按 result.save_decision 分流：
       true  → patch <slug>_meta.json 的 url/source/publish_date 为 item 真实值
             → run.sh --save-from-batch <slug> --batch-dir .. --meta-json <patched>
               （原子：写 KB 文件 + index + embedding(--add, 需 OPENAI_API_KEY) + projects）
             → 存 radar.db wechat_interpretations（save_decision=1, kb_synced=1, 展示）
       false → 存 radar.db（save_decision=0, kb_synced=0, 不展示、不写 KB），避免重复处理
  5. 任意一步异常 → 记 wechat_interpretations.error，继续下一条

web（serve 只读 radar.db）：
  GET /api/v1/wechat        → save_decision=1 的解读列表（envelope {success,data,error}）
  GET /wechat               → SSR 列表页（复用卡片组件 + SSR preload）
  GET /wechat/<slug>        → SSR 详情页（summary_md → 净化后 HTML 渲染）

KB 回写文件落在 ai-assistant/data/ 子模块工作区 → search-knowledgebase 本地即可检索。
（git commit/push 该子模块不在 cron 内做，见 Defaulted D6）
```

**可复用的现成代码/事实（implementer 直接用，免重查）**：

| 用途 | 位置 |
|---|---|
| summarizer 文件输入入口 | `ai-assistant/agents/summary-agent/summarize.sh --input <file> --user <owner-user>`（stdout JSON：`{ok,skipped,dedup:{slug,summary_file_path,saved_at},batch_dir,result:{slug,save_decision,save_reason,recommendation,tags,validation,projects}}`；**result 不含 summary 正文**——`to_meta_dict` 第 79 行 pop summary_md） |
| 完整总结正文落点 | `<batch_dir>/<slug>_summary.md`（`core.py:232` 写）。summary_md（详情页）+ abstract（文章概况首段）都从此文件读，不从 stdout result 取 |
| KB 保存（含 embedding） | `ai-assistant/agents/summary-agent/run.sh --save-from-batch <slug> --batch-dir <dir> --meta-json '<json>'`（最后一行 stdout 含 `summary_file_path`） |
| KB 去重检查 | `ai-assistant/agents/summary-agent/run.sh --check-url <url> --user <owner-user>`（`check_url_in_index` 返回 `{slug, summary_file_path, ...}`，embedding.py:539-548） |
| save_decision 规则（确定性） | `ai-assistant/.../summarizer/schema.py:compute_save_decision`（有 primary 可实现项 OR rec∈{必读,值得一看} OR 可跳过+次级可实现项） |
| 总结 6 模块结构 / 概况首段 | `_output.md` 结构见 `ai-assistant/agents/summary-agent/docs/data_stores.md §2`；`### 📋 文章概况` 首行非空（abstract 取此首段） |
| ai-radar 卡片组件 | `web/static/app.js:352-425`（`itemCard`/`renderTimeline`）、`web/templates/_prepaint_list.html`（首屏直出，已支持 `source_kind=='wechat'` 头像） |
| ai-radar SSR 路由 + preload 模式 | `src/airadar/web/app.py:178-275`（`_preload_context`/页面路由）；新页面契约见 `docs/architecture.md:231-246` |
| 频道/分类过滤 SQL 复用 | `src/airadar/web/routes/timeline.py`、`routes/common.py` |
| 微信头像 JOIN | `LEFT JOIN wechat_account_avatars`（见 `docs/operations/wechat-ingestion.md`） |
| 迁移系统 | `src/airadar/db.py:migrate`（glob `*.sql` 排序执行，`already exists` 幂等 no-op，仅 004 特殊跳过；新增 009 纯增量安全） |
| 侧栏导航（硬编码，每模板一份） | `web/templates/{index,all}.html` `.side-nav` + `web/static/about.html`/`daily.html`；`.side-icon-*` 在 `web/static/style.css` |
| CSS token / 卡片样式 | `web/static/style.css`（`--bg/--panel/--cyan/--ink`、`.card/.seg-list/.timeline`） |

---

## L3 — 设计决策 + 内部 verify

### 0. 移除已停用旧源（前置清理，destructive，决策 E）

**已验证事实（不是待 implementer 验证的假设）**：web 展示层**不按 `sources.enabled` 过滤**（`src/airadar/web/` 无 enabled 谓词）。144 篇禁用源 item 中 **约 116 篇当前正在 `/all` 等 tab 在线展示**——展示过滤口径是 prefilter `numeric_json.is_ai_related=1` 且（无 scoring 或最新 scoring `numeric_json.relevance≥6.5`）（注：`is_ai_related`/`relevance` 在 prefilter/scoring 的 `item_evaluations.numeric_json` 里，**不是 items 表的列**；116 仅为解释性数字，不进任何 verify gate，实测以真实查询为准）。**删除 = 把这批历史文章从线上撤下——用户已在知情下确认要删（决策 E 二次确认）。**

- `data/sources.toml`：删除 `wx_guizang` / `wx_crossing` 两个源定义（并复核第 ~374 行"保留作回滚锚点"注释一并清理）。
- **删除前 gate（备份有效性，决策 E "先备份" 子承诺）**：`cp data/radar.db data/radar.db.bak-<ts>` 后，断言备份文件存在、size>0、`sqlite3 bak "PRAGMA integrity_check"`=ok 且 `SELECT COUNT(*) FROM items` 与源库相等；任一不满足则 **abort，不进入 DELETE**。
- prod DB 级联删除（实测量，删前再核一次）：按 item_id 删 `item_evaluations`(433) + `curated_items`(7) + `feedback`(0)，**再删** `items`(144)。删 items 时 `items_ad_fts` trigger **自动**清 `items_fts`（144 行），**无需手动删 fts**。
  - **`curation_runs` 不动**：FK 是 `curated_items.run_id → curation_runs.id`（child→parent），那 7 条 curated_items 散落在与**启用 item 共享**的历史 run 里；删 run 父行会孤立同 run 的其它启用 item 记录。`curation_runs.output_curated_ids`/`input_eval_ids` 是去规范化 text blob，删 item 后会残留 stale id —— **接受为历史审计残留**（非 FK、不影响 serve），不清理。
  - **`wechat_account_avatars` 不动**（歸藏头像仍被 wx_mp2rss 文章复用）。
- 不阻塞主特性：interpret 取数恒带 `enabled=1`，故即便本任务延后也不影响 159 口径。
- 内部 verify：① 删除前后 `SELECT COUNT(*)` 核对——items −144、item_evaluations −433、curated_items −7；② `items_fts` 对应 144 行**自动消失**（trigger 生效）；③ **`wechat_account_avatars` 行数删前==删后**（11，preservation 断言，误伤即 fail）；④ `curation_runs` 行数删前==删后（preservation）；⑤ 站点 smoke `/ /all /curated` 仍 200（接受 /all 在线条数减少约 116）。

### 1. DB schema（migration `009_wechat_interpretations.sql`）

```sql
CREATE TABLE IF NOT EXISTS wechat_interpretations (
  item_id       TEXT PRIMARY KEY REFERENCES items(id),
  slug          TEXT NOT NULL,
  recommendation TEXT,                       -- 必读 / 值得一看 / 可跳过
  save_decision INTEGER NOT NULL DEFAULT 0,  -- 0/1，tab 与 KB 的唯一闸门
  save_reason   TEXT,
  abstract      TEXT,                        -- 卡片摘要（文章概况首段，见 D1）
  tags_json     TEXT NOT NULL DEFAULT '[]',  -- summarizer 受控标签
  summary_md    TEXT NOT NULL DEFAULT '',    -- 完整结构化总结（详情页）
  model         TEXT,
  kb_synced     INTEGER NOT NULL DEFAULT 0,
  processed_at  TEXT NOT NULL,
  error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_wechat_interp_decision
  ON wechat_interpretations(save_decision, processed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wechat_interp_slug ON wechat_interpretations(slug);
```
- 内部 verify：在 **prod DB 的拷贝**上跑 `migrate()`，断言 `airadar_migrations` 里 004 记录未被重跑、表/索引存在、原有数据不变。

### 2. interpret runner（`src/airadar/interpret/{__init__,runner.py}` + CLI 接线 `cli.py`）
- **取数口径**：恒用 `items JOIN sources ON ... WHERE sources.kind='wechat' AND sources.enabled=1`（159；不依赖 TASK-000 是否已执行）。
- `./run.sh interpret`（管线增量：上述口径中尚无 `wechat_interpretations` 行的）。
- `./run.sh interpret --backfill`（一次性：上述口径全集，已处理则跳过=幂等）。
- provenance：内容用 `content_text` 写临时文件喂 summarizer（绕开微信反爬）；summary_md/abstract 从 `<batch_dir>/<slug>_summary.md` 读（非 stdout result）；save 前 patch `<slug>_meta.json` 的 `url/source/publish_date` 为 item 真实值；`--check-url` 用真实 URL。
- check-url 命中分支：从返回的 `summary_file_path` 读 KB 总结填库；文件缺失/不可读 → fallback 走正常 summarize（见架构 step 2）。
- **determinism 立场**：复用 summarizer 上游默认（temp=0.3，非确定）——honor reuse，不改其采样。每篇**只处理一次、结果即锁定**（增量只处理无行 item / backfill 跳过已处理），故 save_decision 不会跨次重跑漂移；要重判须显式重处理（删行）。
- slug 唯一：若 slug 已存在于表，追加 `-2/-3`，最终 slug 落库。
- 内部 verify：unit test mock subprocess 返回 canned JSON，断言 (a) save_decision=true 路径写表+触发 save、(b) false 路径只写表不 save、(c) check-url 命中走读 `summary_file_path` 不调 LLM、(c2) 命中但文件缺失 → fallback summarize、(d) provenance patch 正确、(e) summary_md 从 batch-dir 文件读而非 stdout、(f) 异常被捕获写 error 且不抛出。eval 锚点：实现时选 2-3 个代表性 item_id（一篇长高信号、一篇短文 ~786 字、一篇 borderline）供 evaluator 复跑对照。

### 3. 管线接线 + fail-safe（`pipeline.sh` + preflight）
- `pipeline.sh` 末尾加 `run_stage interpret`（在 `curate` 之后）。`pipeline.sh` 已是 continue-on-fail。
- runner preflight：`AI_ASSISTANT_ROOT`（默认取 owner 本机 ai-assistant checkout 路径）存在 + `summarize.sh` 可执行；否则 log warning 并 0 退出（skip），不污染 pipeline。
- 内部 verify：临时 unset `AI_ASSISTANT_ROOT` 跑 `./run.sh interpret`，断言 exit 0 + 日志 "skip"。

### 4. API `/api/v1/wechat`（`src/airadar/web/routes/wechat.py`）
- 返回 `save_decision=1` 的解读，JOIN items 取 题目/author(公众号)/published_at + `wechat_account_avatars` 取头像；字段 `{slug,title,abstract,tags,author,avatar_url,published_at,url}`；按 `published_at DESC` 分页。envelope `{success,data,error}`。
- 内部 verify：contract test —— 只回 save_decision=1；字段齐全；分页正确。

### 5. SSR 列表页 `/wechat`（`web/templates/wechat.html` + `app.js:initWechat`）
- 由 `all.html` 派生：去掉频道/分类过滤（WeChat 专属、summarizer 标签≠ai-radar 分类）；v1 = 列表 + SSR preload + 首屏直出，按时间倒序分页；卡片点击 → `/wechat/<slug>`。tag 过滤/搜索列 TODO。
- 内部 verify：`initWechat` 读 `#__PRELOAD__`；preload 存在则直接 render（无 spinner）。

### 6. SSR 详情页 `/wechat/<slug>`（`web/templates/wechat_detail.html` + route）
- 从 radar.db 取 `summary_md` → markdown 渲染为**净化后** HTML（LLM 内容=不可信，必须 sanitize）。
- 依赖：选 server-side markdown 渲染库 + sanitizer（如 `markdown-it-py`+`nh3`，或 `markdown`+`bleach`）。**加依赖前必须 `mcp__context7__query-docs` 确认 API + 在 `pyproject.toml` pin 版本**（CLAUDE.md 硬要求）。
- 含"‹ 返回列表"、题目、公众号+日期、标签、6 模块正文。slug 不存在 → 404。
- 内部 verify：render 单测 —— 给含 6 模块 + 恶意 `<script>`/`<img onerror>` 的 summary_md，输出 HTML 含渲染后的 `<h3>` 且**不含**原始 script/onerror。

### 7. 侧栏导航 + 图标
- 在 `index.html`/`all.html`/`wechat.html`/`wechat_detail.html` 模板 + `about.html`/`daily.html` 静态页 `.side-nav` 加 `<a href="/wechat">微信文章解读</a>`；当前页加 `.side-link-active`。
- `style.css` 加 `.side-icon-wechat`（复用现有 `.side-icon-*` CSS 绘制范式）。
- 内部 verify：每个页面 DOM 含该链接（见 L2）。

---

## L2 — 用户视角 verify（implementer-executable；除非标注，agent 可独立完成）

> 数值基线（启用源 159、save_decision=1 计数）由 implementer 基于回填后真实数据动态推算；判据是 **expected==actual 且 gap 可解释**，不是存在性。所有"全部 wechat"口径恒指 `kind='wechat' AND enabled=1`（与 interpret 取数一致）。

1. **侧栏 tab 全站存在**：对 `/ /all /daily /about /wechat` 各 `curl -s localhost:8000<path> | grep -c '微信文章解读'` ≥1。
2. **列表数量一致**：`curl -s localhost:8000/api/v1/wechat | jq '.data.items|length'` == `sqlite3 data/radar.db "SELECT COUNT(*) FROM wechat_interpretations WHERE save_decision=1;"`。
3. **卡片三要素齐全**：jq 断言每个 item 的 `title/abstract/tags` 非空（tags 为非空数组）。
4. **详情页渲染**：取一个 save_decision=1 的 slug，`curl -s localhost:8000/wechat/<slug>` 含 6 模块标题（文章概况/独特亮点/可动手实践/可复用认知/关键词/价值判断对应渲染）+ 返回链接；HTML 为渲染后结构（含 `<h3>`）。
5. **回填覆盖一致性**（核心，expected-vs-actual）：
   - 全部启用 wechat item：`SELECT COUNT(*) FROM items i JOIN sources s ON s.id=i.source_id WHERE s.kind='wechat' AND s.enabled=1;`（回填时实测，当前 159）。
   - 已处理：`SELECT COUNT(*) FROM wechat_interpretations;` 应 == 上一行；**任何 gap 必须可解释**（如 content_text 为空的条目，需列出 item_id + 原因）。
   - 值得读：`... WHERE save_decision=1` == 步骤 2 的 API 计数 == 页面渲染卡片数。
   - 已同步 KB：每条 save_decision=1 均 `kb_synced=1`。
6. **search-knowledgebase 可检索**（cwd=ai-assistant）：
   - `./agents/summary-agent/run.sh --check-url '<一个回填的微信URL>' --user <owner-user>` 返回 exists。
   - 跑 search-knowledgebase skill 查一个回填文章覆盖的主题 → 该文章出现在结果。
   - **D7 去重断言（防重复入库/重复 LLM）**：回填前快照 `index.json` 的 slug 集合 `S0`；回填后，对"check-url 命中（回填前已在 KB）"的那批 item，断言它们在 `index.json` **零新增条目**（`S1 - S0` 与命中集合不相交），且这些 item 的 `wechat_interpretations` 行 `kb_synced=1`、无新 summarize 产生的 batch 目录。`index.json` 总条目数 == `|S0|` + 真正新 save（未命中且 save_decision=1）数。
7. **不值得读被排除**：取一个 save_decision=0 的 item，断言其 slug 不在 `/api/v1/wechat`，且 `/wechat/<slug>` 404。
8. **风格一致**（人工 gate，前置自动兜底）：agent-browser 截 `/wechat` 列表 + 1-2 个 `/wechat/<slug>` 详情页；**按用户全局 BINDING 多模态审核规则**，把截图汇总成一个 HTML 页、经本地 web server 暴露 **http 链接**交用户在浏览器看全（禁止只贴单图/给文件路径）。前置 agent-autonomous 自检：HTML 页可访问(200)、每张截图非空且加载成功、暗色 token/字体/卡片类与现有 tab 一致——通过后再请用户终审"美观、易用、符合直觉"。
9. **XSS 安全**：见 L3.6 内部 verify + 在真实详情页输出中 grep 确认无原始 `<script`/`onerror=`。
10. **fail-safe + serve 独立性**（人工触发即可）：unset `AI_ASSISTANT_ROOT` 跑一次 `./pipeline.sh`，断言 (a) fetch→curate 完成、interpret 记 skip；(b) **serve 独立性**——此状态下 `/wechat` 与 `/wechat/<slug>` 仍返回 200 且完整渲染（证明 serve 只读 radar.db、不碰 ai-assistant，对应取舍偏好#3/D3）。

---

## Defaulted Decisions（planner 自拍，供 reviewer 审）

| # | 决策 | 默认 | 理由 |
|---|---|---|---|
| D1 | 卡片摘要来源 | 从 `<batch_dir>/<slug>_summary.md`（或命中分支的 KB `summary_file_path`）解析 `### 📋 文章概况` 首段（fallback ai-radar enrich `summary_zh`）。**注：不从 summarize.sh stdout 取——result 已 pop summary_md** | tab 内容统一源自 summarize-article；确定性可提取 |
| D2 | 卡片题目 | 原文微信标题（题目） | 用户原话"文章的题目" |
| D3 | 网站数据存储 | 完整 summary_md/abstract/tags 落 radar.db，serve 只读 radar.db | 用户"独立网站数据+重复可接受"；serve 与 ai-assistant 解耦更稳 |
| D4 | 正文来源/provenance | 喂 `content_text` 临时文件 + patch meta 真实 url/source/date | 全文已在库（可靠、避反爬）；ai-assistant 代码零改动 |
| D5 | stage 位置/容错 | interpret 末位 + per-item try/except + preflight skip | 保护 your-domain.example 命脉 cron |
| D6 | KB 子模块 commit | cron 内只写文件不 git commit/push；commit 留手动/低频（文档化） | 避免 cron git/LFS 噪声与失败；search 读本地文件即生效 |
| D7 | 已在 KB 的文章 | --check-url 命中 ⇒ 视为值得读，从返回的 `summary_file_path` 读 KB 总结填库，不再调 LLM；**文件缺失/不可读则 fallback 走正常 summarize** | KB 只存 save_decision=true；省重复成本与重复条目；fallback 防残缺数据 |
| D12 | LLM determinism | honor summarizer 上游默认 temp=0.3；每篇只处理一次、结果锁定（不重跑），save_decision 不跨次漂移 | reuse skill as-is；幂等 + 锁定首次结果，重判须显式删行重处理 |
| D8 | ai-assistant 定位/用户 | `AI_ASSISTANT_ROOT` env（默认取 owner 本机 ai-assistant checkout 路径），user=<owner-user> | 匹配 skill 默认 |
| D9 | 回填执行 | 一次性 `interpret --backfill`；管线 `interpret` 仅增量 | 把昂贵一次性操作移出热循环 |
| D10 | detail slug 唯一 | 冲突追加 `-2/-3`，最终 slug 落库 | 稳定可分享 URL |
| D11 | v1 列表过滤 | 无频道/分类/搜索，仅时间倒序分页 | surgical；tag 过滤/搜索列 TODO |

---

## Risks / TODO

- **R1 跨 repo 运行时耦合**：cron 依赖 ai-assistant repo+venv+keys+可写子模块。缓解：preflight skip + fail-safe + 末位 stage；后续可接入 `/admin` 健康检查与告警（TODO）。
- **R2 回填 LLM 成本/余额**：≤启用源实测篇数（写定时约 159–161，随 cron 增长）次 DeepSeek 调用（--check-url 命中的不调）。注意已知坑——**DeepSeek 余额不足会伪装成 404**（`docs/issues`/memory），回填若整批 404 先查余额。回填幂等可续跑。
- **R6 旧源移除是 destructive 且用户可感知**（TASK-000）：删 144 item + 级联（item_evaluations 433 / curated_items 7 / feedback 0；items_fts 由 trigger 自动清；curation_runs 与 avatars 不动）。**已验证 116 篇当前在线**，删除会从 /all 等 tab 撤下这 116 篇——用户已知情确认（决策 E）。缓解：备份有效性 gate（见 L3.0）+ 删前后计数/preservation 核对 + 站点 smoke。备份文件 `data/radar.db.bak-<ts>` 是唯一回滚路径，保留至验收后。
- **R3 content_text 质量**：部分微信 item 很短（~786 字）→ 总结质量略低，可接受。
- **R4 markdown XSS**：详情页渲染 LLM 内容必须 sanitize（L3.6/L2.9 已覆盖）。
- **R5 data 子模块是 LFS**（`vectors.npy`）：`--add` 每次重写，cron 不 commit 故工作区 churn 无害。
- **TODO**：考虑给 summarizer 上游加 provenance-override flag（比 patch meta 干净）；`/wechat` 加 tag 过滤/搜索；interpret 接入 /admin 监控。
- **文档同步（交付前必做）**：README §信源/页面表加 `/wechat`；`docs/architecture.md` 数据流加 interpret stage；interpret 运维 + KB 回写 + 子模块 commit 约定记入 `docs/operations/wechat-ingestion.md` **或**新建 `docs/operations/wechat-interpretation.md`——**若新建，必须同步在 `docs/CLAUDE.md` 索引登记该文档**（docs 索引协议）；若并入既有文件则无需改索引。
- **TASK-000 后文档同步**：**两类同字面"回滚锚点"语义不同，区别处理**——
  - **A 类（两个源 → 必须改，只这两个活目标）**：`data/sources.toml`（删 `slug="wx_guizang"`/`"wx_crossing"` 源定义 + 行 ~374-375 的"暂保留作回滚锚点"注释）；`docs/operations/wechat-ingestion.md §已停用的旧源`（行 ~31/35-36，改为"已移除 + 日期 + 备份文件名"）。
  - **B 类（WeWe RSS **服务/基础设施**回滚锚点 → 本 plan 不动）**：`docs/operations/services.md`(~12)、`wechat-ingestion.md`(~15) 的 wewe docker 桥接"仅作回滚锚点保留"；`references/wechat-sources.md`（旧 WeWe 添加流程 runbook，不含两源名）。**全部保持原样**。
  - **历史记录（不动）**：`docs/plans/*`、`docs/issues/general.md` 里对两源的引用是 append-only 过往工作记录，**不修改**。
  - verify（只锁活目标，避开 B 类与历史记录）：`grep -n 'slug = "wx_guizang"\|slug = "wx_crossing"' data/sources.toml` 应为空（源定义已删）；`grep -n "暂留作回滚锚点\|暂保留.*回滚锚点" docs/operations/wechat-ingestion.md` 应为空或已改写为"已移除"。
