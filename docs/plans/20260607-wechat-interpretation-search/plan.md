> **Archive status**: 已归档并上线。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> `/wechat` 搜索的当前字段范围、LIKE/简繁/空格不敏感口径与上下文保留见 [operations/wechat-ingestion.md](../../operations/wechat-ingestion.md)「微信文章解读与知识库回写」节，用户可见验收见 [contracts/ux-contract.md](../../contracts/ux-contract.md) WX-4 与「微信文章解读页（`/wechat`）」节。以下为原 plan 正文，未修改。

# Plan：微信文章解读页（/wechat）支持搜索

> **Long-task mode** — 本 plan 启用状态外部化协议。implementer 必读 `~/.claude/references/long-task-protocol.md`：
> 每步开工/收尾同步同目录 `state.md`（任务条目 [pending]→[in_progress]→[done]）与 `journal.md`（决策/踩坑/verify 证据）。
> 交付前过协议的"交付前验证"。state.md 是真值，plan.md 是契约。

---

## 输入与范围

- **来源**：用户诉求「我希望 https://your-domain.example/wechat 能支持搜索，有类似于精选和"全部 AI 动态"的搜索体验」。
- **无 spec.md** — 本 plan 自带全部对齐（L1/取舍/L2/L3）。
- **关键对齐（已用 AskUserQuestion 拍板）**：
  - **搜索范围 = 解读卡片字段**：匹配 中文标题 + 公众号(作者) + 摘要(abstract) + 标签(tags)。不搜原文正文，不搜 `summary_md` 结构化总结全文。
  - **筛选器 = 只加搜索框**：本版不加公众号/分类 chips（留作后续独立增量）。
- **不在本 plan**：FTS 索引 / migration 改动（202 行用 LIKE 即可，且避开"migration 004 永不重跑"约束）；公众号筛选 chips；分类 chips；搜 `summary_md` 全文；搜索结果高亮 / 摘要替换为命中片段。

### 与既有 plan 的区分（避免误判重复）

- `plans/20260529-timeline-search-source-author/` 与 `plans/20260531-wechat-search-usability/` 修的是 **timeline（/all 全部 AI 动态）** 搜索（`/api/v1/timeline?q=`），已落地（commit `5e9df0c`、`36000ee`）。
- 本 plan 是 **/wechat 解读页** 自己的搜索（`/api/v1/wechat?q=`），数据源是 `wechat_interpretations` 表，与 timeline 搜索是不同端点、不同字段集。两者无代码重叠。
- 项目记忆 `timeline-search-plan`（称该 plan "待实施"）已过期——实际已实施。

---

## L1：最终产物 + 使用方式

- **产物**：ai-radar web 层的一处功能增量——`/wechat` 页面新增搜索框，后端 `/api/v1/wechat` 新增 `q` 过滤参数。
- **使用者（端用户）**：your-domain.example/wechat 访客，浏览"微信文章解读"精选库（当前 202 篇、10 个公众号）。
- **使用方式 / 下游动作**：用户记得"某篇讲 X 的解读"，在搜索框输入关键词（公众号名 / 标题词 / 主题词 / 标签），**快速定位到那几篇解读，决定打开哪篇读结构化总结**。这是一个"精选库内检索"场景——精准、可预期比召回最大化更重要（已在取舍中确认）。
- **成功画面**：用户在 /wechat 输入关键词后，列表即时收敛为命中的解读；公众号名命中时该号文章排在前面；繁简互通；清空恢复全量；URL 带 `?q=` 可分享/刷新保持；点进解读详情再返回，仍停在搜索结果。

---

## 取舍偏好 + 三层影响

| 维度 | 取舍 | 三层影响 |
|---|---|---|
| 精准 vs 召回 | **精准**（用户已选"解读卡片字段"而非"全文"） | L1 定位为"库内精确检索"；L3 匹配字段限定为 title/author/abstract/tags，不含 summary_md 全文 |
| 当下交付 vs 长期演进 | **当下交付**（只加搜索框） | L1 不含筛选 chips；公众号/分类筛选作为已知的后续增量，不阻塞本版 |

> 注：本任务只有上述两个真取舍。"一致体验 vs 最简实现"在此**不冲突**——最简路径 = 复用 peer 代码（SQL LIKE / 既有搜索 helper / app.js），复用本身即一致，故无需作为取舍列出。

---

## L3：设计决策 + 内部 verify

### 后端：`src/airadar/web/routes/wechat.py`

**改 `list_wechat_items`（当前 L83–119）**：新增关键字 `q: str | None = None`。

- q 为空/全空白 → **完全保持现状**（无 WHERE、无排序变化、total = 全量 saved 数）。这是回归不变式。
- q 非空 → 构造过滤与排序：
  - **匹配字段（4 个）**：`i.title`、`i.author`、`wi.abstract`、`wi.tags_json`，全部 `LIKE ? ESCAPE '\'`。
    - 复用 `common.py` 的 `like_patterns_for_query(q)`（当前 L280，内部走 `expand_st_variants` 做繁简扩展 + `escape_like`）。每个变体对 4 字段各一个 LIKE，OR 连接。
    - **刻意不匹配 `s.name`**：wechat 的 `s.name` 是聚合 feed 名「微信公众号（Mp2RSS 合集）」，匹配它会让所有条目命中。用户语义里的"来源"= 公众号 = `i.author`。此处刻意与精选/全部页（匹配 source_name）不同。见 Defaulted Decision #3。
    - **tags 匹配**：对 `wi.tags_json`（JSON 数组字符串，如 `["Agent","工程化"]`）直接 LIKE。词级查询足够；可接受偶发匹配到 JSON 标点的理论边界。见 Defaulted Decision #4。
  - **来源优先排序**：`is_source_match = CASE WHEN (<i.author LIKE 各变体>) THEN 1 ELSE 0 END`，`ORDER BY is_source_match DESC, i.published_at DESC, i.fetched_at DESC, i.id DESC`。复刻精选/全部的"来源命中优先"，但"来源"= author。
    - **⚠️ 不可复用 `common.py:source_match_expression`**：该 helper（L284–295）同时匹配 `s.name` **和** `i.author`——直接复用会把 s.name（聚合 feed 名，含「合集」等，覆盖全部 202 行）一并算作来源命中，使所有条目变 source-match、优先级失效，且违背 Defaulted Decision #3。本步须**自建 author-only** 的 CASE/WHERE（可复用 `like_patterns_for_query(q)` 产出的 patterns，只对 `i.author` 套 LIKE）。负向守护见 V14。
  - **total**：过滤后的 `COUNT(*)`（用于分页）。页码 clamp 逻辑同现状（`min(max(page,1), total_pages)`）。
- **匹配 raw `i.title`**：展示用 `normalize_wechat_title` 仅折叠空白/换行、不增删词（已核验 `src/airadar/wechat_text.py`），故 SQL 对 raw title 的 LIKE 与"用户看到的标题"等价。见 Defaulted Decision #2。

**改 `_detail_url`（当前 L63–65）**：签名加 `q: str | None`，把 q 一并拼进 detail URL（`/wechat/<slug>?q=...&page=...`），使详情页返回能回到搜索结果。`_item_from_row`（L68–80）相应透传 q。

**改 `@router.get("/wechat")`（当前 L148–155）**：加 `q: str | None = Query(default=None)`，传入 `list_wechat_items`。返回 envelope 里 `items/total/page/limit` 语义不变（新增 q 不改 envelope 结构）。

**内部 verify**：新增/扩展 `tests/test_wechat_interpretation.py`（复用 `_seed_wechat_db`，当前 L48；参考 `test_wechat_api_returns_only_worth_reading_items_with_pagination` L215、`test_wechat_api_clamps_out_of_range_page_and_carries_page_in_detail_urls` L239）。断言：① 按 author 命中、② 按 title 词命中、③ 按 abstract 词命中、④ 按 tag 命中、⑤ 繁简互通、⑥ 2 字专名命中、⑦ author 命中排序优先、⑧ q 为空时 total == 全量（回归）、⑨ 过滤分页 + 越界 clamp、⑩ detail_url/back 链接带 q。

### 页面路由：`src/airadar/web/app.py`

- **`wechat_page`（L284–292）**：加 `q: str | None = None`，传给 `list_wechat_items`——让 SSR preload 反映过滤结果（与 `index_page`/`all_page` 把 q 传给 curated/timeline 一致，app.py L252/L269）。**搜索框初值由前端 `app.js` 的 `search.value = searchFromUrl()` 填充**（与精选/全部页一致：index.html/all.html 的 `#search` 无 `value` 属性，JS 注入），**不引入 SSR `value="{{ query }}"` 属性、不新增 `query` 模板 context 变量**。
- **`wechat_detail_page`（L294–302）** + **`_wechat_back_href`（L189–190）** + **404 handler（L213–224）**：把 q 线程化进 back_href（`/wechat?q=...&page=...`）。**这是扩展该页已有的 `page` 线程化模式**——`_detail_url`/`_wechat_back_href` 当前已携带 `page`（wechat.py L63–64、app.py L189–190），本步只是让 q 与 page 并行携带。三处都要带 q，否则搜索后点进详情/404，其**服务端渲染的页内返回链接**会丢失搜索态。（注：curated/timeline 无此需求，因其条目直接外链原文、无站内详情页；故它们不线程化 q 不构成 peer 反例。浏览器 back 按钮另由 history 兜底，但页内返回链接是服务端渲染、必须带 q。）
- **内部 verify**：类型注解齐全（`mypy` 干净）；`test_wechat_pages_render_preload_detail...`（L285）扩展断言：① 带 `?q=` 请求时 SSR preload 的 items 已过滤；② 详情页 back_href 带 q。

### 模板：`web/templates/wechat.html`

- 在 `.page-header`（当前 L33–38）内加搜索表单，**复用 all.html（L55–60）的既有 class**：`<form class="feed-filter" action="/wechat" method="get"><input id="search" type="search" name="q" placeholder="搜索标题/公众号/摘要…"><button class="filter-submit" type="submit">搜索</button></form>`。`#search` 无 `value` 属性（初值由 app.js 注入，同精选/全部页）。
- **⚠️ 只搬 `name=q` 这一个 input**：all.html 的同一表单还含 `<input type="hidden" name="category">`、`<input type="hidden" name="channel">`（all.html L56–57）和两组 `.seg-item` segments（L40–53）。本版"只加搜索框"——**这些 hidden input 与 segments 一律不要搬**，否则静默违背用户锁定决策（见 V13 负向断言）。
- **复用既有 CSS**：`.feed-filter` / `#search` / `.filter-submit` 已在 `style.css` 定义。若布局在 wechat header 下不协调，**最小补一条 CSS**（TODO，由 implementer 视觉验证后决定，不预设）。
- **bump cache-busting 版本号**（见下"全局必做"）。

### 前端：`web/static/app.js`

**改 `initWechat`（L693–738）**：接入搜索（参考 `initCurated` L879 的最简形态——它也只有搜索框+分类，本版去掉分类只留搜索）：
- 取 `#search`，初值 `search.value = searchFromUrl()`（L140）；进场 `normalizeFeedUrl("/wechat", {q, page})`（复用 `feedUrl` L153 / `normalizeFeedUrl` L170——category/channel 缺省 "all" 会被忽略，对 /wechat 即只产出 q+page）。
- `load()` 把 `q = search.value.trim()` 传给 `queryPath("/api/v1/wechat", {page, q, limit:50})`（L512）；`updateFeedUrl("/wechat", {q, page})` 同步 URL。
- `debounceInput(search, runSearch)`（L664，200ms）+ form submit 阻止默认 → runSearch（load page 1）。
- `popstate` 重读 q + page 重载。
- **`renderWechatPagination`（L654–656）**：`urlForPage` 改为带 q——`feedUrl("/wechat", {q, page:value})`，使翻页保持搜索态。
- **`renderWechatTimeline`（L450–471）空状态参数化**：区分"有 q 无命中"（如「没有匹配条目 / 清空搜索后可回到默认列表」）与"无 q 无数据"（保留现有「暂无微信文章解读」），对齐 `initTimeline`/`initCurated` 的空态文案模式。
- detail 卡片导航（`bindWechatCardNavigation` L539 起 + `data-detail-url`）已由后端把 q 写进 detail_url，返回链路自然带 q，无需前端额外处理。

**内部 verify**：本地 serve 后用 agent-browser 跑 UI 流（见 L2）；`renderWechatTimeline` 的纯函数若已有单测则补空态分支断言。

### 全局必做：cache-busting 版本号

- `app.js` 被各页以 `?v=20260604-curated-archive1` 引用。**改了 app.js 必须 bump 此版本串**，否则回访用户拿到旧 JS。
- 步骤：**以 `grep -rn "v=20260604-curated-archive1" web/` 实际命中为准全量替换**为新版本串（如 `v=20260607-wechat-search1`）。当前共 **14 处、跨 10 个文件**（`web/templates/` 5 个：wechat / wechat_detail / wechat_404 / all / index；`web/static/` 5 个：index / all / daily / about / item）。不要凭"只改 wechat.html"截断范围——搜索→详情→返回链路会经过 wechat_detail / wechat_404，漏 bump 会版本漂移。判据见 TASK-003（grep 无残留）。

---

## L2：用户视角 verify（implementer-executable）

> 维度对齐精选/全部页的搜索体验。每条标注【agent 可独立】或【人工 gate】。覆盖率类断言**不止存在性**——见末条。

| # | 维度 | 可执行步骤 | 预期 | 人机 |
|---|---|---|---|---|
| V1 | 按公众号名搜 + 来源优先排序 | `curl -s "localhost:<port>/api/v1/wechat?q=<某公众号名>"` | 返回该号文章；且**结果中 `author == <该公众号>` 的条目全部排在 `author != <该公众号>` 的条目之前**（可机械判定 is_source_match 优先，非自然语言） | 【agent 可独立】 |
| V2 | 按中文标题词搜 | 取一篇已知 saved 文章标题里的词 q → 请求 | 该文章在结果中 | 【agent 可独立】 |
| V3 | 按摘要词搜 | 取一篇 abstract 里的词（不在标题里）→ 请求 | 该文章命中（证明 abstract 进了匹配面） | 【agent 可独立】 |
| V4 | 按标签搜 | 取一篇 tags 里的标签 → 请求 | 该文章命中 | 【agent 可独立】 |
| V5 | 繁简互通 | 用真实数据「歸藏」(繁) 与「归藏」(简) 分别请求 | 两者都命中「歸藏的AI工具箱」的文章 | 【agent 可独立】 |
| V6 | 2 字专名 | q=「歸藏」(2 字) | 有结果（无 ≥3 字门槛） | 【agent 可独立】 |
| V7 | 分页保持 q | `?q=<高频词>&page=2` | 返回过滤后的第 2 页；total = 过滤计数；越界页 clamp 到末页 | 【agent 可独立】 |
| V8 | 空结果不崩 | q=「zzz不存在zzz」 | items 空、total 0、page 1、HTTP 200 | 【agent 可独立】 |
| V9 | 无 q 回归 | ① `/api/v1/wechat`（无 q）；② 改动前先 `git stash` 取基线首屏 | ① total == 改动前全量 saved 数（基线见下）；② **无 q 时首屏 50 条的 `(slug, published_at)` 序列与基线逐条相等**（证明 q 空走原 SQL/ORDER BY、零行为改变） | 【agent 可独立】 |
| V10 | 详情/404 返回保持搜索态 | ① 请求 `/wechat/<slug>?q=foo&page=2`；② 请求 `/wechat/<不存在的 slug>?q=foo&page=2`（HTTP 404，wechat_404 页 HTML） | 两者的返回链接 back_href 均 == `/wechat?q=foo&page=2`（#8 把 404 也列为 q 线程化点；注意 `_wechat_back_href` 当前仅收 page，须改签名收 q） | 【agent 可独立】 |
| V11a | UI 行为流（自动） | 本地 serve → agent-browser：搜索框输入词 → 列表收敛、URL `?q=` 同步、debounce 不每键请求；清空 → 恢复全量；点结果 → 详情 → 返回 → 仍在搜索结果 | 行为如述；无 JS 报错（agent 全程机械断言） | 【agent 可独立】 |
| V11b | UI 视觉确认 | 在 V11a 通过的前提下，截图搜索框在 wechat header 下的布局 | 布局协调、不破版（仅此项需人眼，是 V11a 之后的薄残留） | 【人工 gate，V11a 已兜底功能风险】 |
| V12 | **覆盖率/一致性（跨字段）** | **对每个匹配字段各取一个"只该字段命中"的词**（仅标题命中 / 仅摘要命中 / 仅标签命中）+ 一对繁简词，**各自直接对 DB 用同口径 SQL（对应字段 + 繁简变体）算 expected 命中数**，对比 API `total`。implementer 基于真实数据动态取基线，不在 plan 写死。 | 每个词 expected == actual，gap 须可解释（覆盖全 4 字段 + 繁简双向，防单字段/单方向静默漏召） | 【agent 可独立】 |
| V13 | 只加搜索框（负向断言） | 渲染 `/wechat` HTML，检查 `.feed-filter` 表单内容 | 表单内**仅有 `name=q` 一个 input**；**无 `name=category`/`name=channel` 的 input、无 `.seg-item` segments**（违反即 fail，守住用户"只加搜索框"锁定决策） | 【agent 可独立】 |
| V14 | 不匹配 s.name（负向断言） | `curl -s ".../api/v1/wechat?q=合集"`（「合集」仅存在于聚合 feed 名 `s.name`，已核验 author/title/abstract/tags 中均无） | `total == 0`（违反即 fail——若误用 `source_match_expression` 把 s.name 纳入匹配，会返回全部 202 行，此断言立刻暴露，守住 Defaulted Decision #3） | 【agent 可独立】 |
| V15 | 不搜 summary_md 全文（负向断言） | implementer 从真实 DB 取一个**只出现在 `summary_md`、不在 title/author/abstract/tags** 的词（同 V12 的动态取词法），`q=<该词>` 请求 | `total == 0`（守用户"精准/卡片字段"锁定决策的负向边界，与 V14 对称；违反即 fail。注：list SELECT 本就不含 summary_md，结构上已兜底，本断言是显式守护） | 【agent 可独立】 |

**基线动态推算**：V9 的"全量 saved 数"与 V12 的"某词预期命中数"由 implementer 在真实 DB（`data/radar.db`）上现算，不在 plan 固定数字。判据是 expected-vs-actual 相等，不是"≥1 命中"。
**⚠️ 基线务必带 `save_decision=1` 过滤**：`wechat_interpretations` 表当前共 **230 行**，其中 `save_decision=1` 的 **202 行**才是 /wechat 展示集。无 q 回归基线 = `SELECT COUNT(*) … WHERE save_decision=1` = 202；若漏过滤会得 230 致 V9 误判。

---

## 部署与运维（交付必读）

- **改了 Python 路由 → 必须重启 serve** 才生效。**重启踩坑（记忆 `wechat-interpretation-plan` 已记）**：生产 serve 启动会 `db.migrate()`；若 cron 跑 pipeline 持锁时重启 → `database is locked` → 站点 down。安全重启：先暂停 cron（注释 crontab `*/15 … pipeline.sh`）或等锁释放，再 `launchctl kickstart -k gui/$UID/com.example.ai-radar.serve`。
- **前端版本号 bump 后**需确认浏览器拉到新 `app.js`（硬刷或确认 `?v=` 已变）。
- 本功能**不碰 DB schema、不碰 pipeline、不碰 migration**，部署面仅限 web 层重启 + 静态资源版本。

---

## 文档同步（落 commit 前）

本改动是**用户可感知变化**（新功能上线）。按 `~/.claude/references/docs-organization-protocol.md`，commit 前先同步 [User] 档：
- `CHANGELOG.md`：加一条 feat（/wechat 支持搜索）。
- `README.md`：若列了各页能力，补一句 /wechat 可搜索。
- `docs/operations/wechat-ingestion.md` 或相关运维档：如涉及"如何重启 serve 使前端/路由生效"，确认与上"部署"段一致。
- 开发者档（architecture/adr/experiences）留给手动 `/custom:update-docs`，本 plan 不强制。

---

## 实施顺序（建议）

1. 后端 `list_wechat_items` + `_detail_url` + 路由 `q` 参数 → 写单测（含 V1–V10、V12 的后端可断言部分）→ `pytest tests/test_wechat_interpretation.py` 绿。
2. 页面路由 `wechat_page`/`wechat_detail_page`/`_wechat_back_href`/404 线程化 q → 扩展页面测试（含 V13 表单负向断言、带 q 的 SSR preload 过滤）。
3. 模板搜索框（只搬 `name=q`，不搬 hidden inputs/segments）+ cache-busting bump（grep 全 14 处）。
4. 前端 `initWechat` 搜索接入 + 分页带 q + 空态参数化 + 搜索框初值 `searchFromUrl()`。
5. 全量 `pytest` + `ruff` + `mypy` 绿。
6. 本地 serve → agent-browser 跑 V11a（agent 行为流）+ V11b（人工视觉）+ 复核 V1–V15（含 V14 s.name / V15 summary_md 两条负向断言、V10 详情+404 双 back_href）。
7. 文档同步 → 走 `create-commit`（记忆 `commit-via-create-commit-no-coauthor`：commit 走 create-commit skill，不加 Co-Authored-By）。
8. 部署：按"部署与运维"安全重启 serve，线上复核 V11a 关键路径。

---

## Defaulted Decisions（访谈未问、planner 自拍，供 reviewer 审）

| 决策 | 选择 | 理由 |
|---|---|---|
| #1 匹配机制 | SQL `WHERE … LIKE` + SQL 分页 | 202 行，LIKE 毫秒级；与 curated/timeline 的 SQL 分页模式一致；零 FTS、零 migration（避开 004 永不重跑约束） |
| #2 title 匹配 raw 还是 normalized | 匹配 raw `i.title` | `normalize_wechat_title` 仅折叠空白/换行、不增删词，故 SQL LIKE raw 与展示标题等价 |
| #3 是否匹配 `s.name` | **否**，只匹配 `i.author` | wechat 的 s.name 是聚合 feed 名，匹配它会全表命中；用户语义"来源"= 公众号 = author。刻意与其他页不同 |
| #4 tags 匹配方式 | 对 `tags_json` 原始字符串 LIKE | 简单、词级查询足够；不必解析 JSON |
| #5 搜索时卡片正文 | 仍展示 `abstract`，不替换为命中片段、不高亮 | 保持该页身份一致；snippet/高亮非本版诉求 |
| #6 短词是否走 FTS 分支 | 不分支，一律 LIKE | 202 行无需 FTS；一律 LIKE 反而天然支持 2 字专名 |
| #7 来源优先排序的"来源" | = `i.author`（非 source_name） | 同 #3 |
| #8 q 透传范围 | detail_url + back_href + 404 都带 q（服务端线程化） | 搜索→详情→返回不丢搜索态。**这是扩展该页已有的 `page` 线程化**（非新机制）；/wechat 有站内详情页（curated/timeline 外链原文、无此页），其页内返回链接服务端渲染、必须带 q |
| #9 版本号 bump 范围 | 以 grep 命中为准全替换（现 14 处/10 文件） | 避免共享 app.js 的版本串漂移 |

## TODO（implementer 实施时定，plan 不指定 how）

- 搜索框在 wechat header 下的具体布局：优先纯复用 `.feed-filter` class；视觉不协调时最小补 CSS。
- 空态文案最终措辞。
- 新版本串具体取值（如 `v=20260607-wechat-search1`）。

## Risks

每条风险含 *acceptance*（为何不预先消除）+ *trigger response*（触发时跑什么）。

- **风险**：前端漏 bump 部分模板 → 版本串不一致。**acceptance**：同一 app.js 文件，理论不影响功能，仅混淆。**trigger response**：TASK-003 的 `grep -rn ... web/` 残留即触发 → 按命中补全（agent 自主，无用户影响）。
- **风险**：tags 对 `tags_json` LIKE 命中 JSON 结构字符（如查 `a` 命中 `["..."]`）。**acceptance**：本版已确认词级查询场景，短到匹配标点的查询极少。**trigger response**：信号 = V8/V12 中观察到明显的标点/结构噪声命中 → 回退为 `json_each` 精确匹配该字段（用户体验无影响、可延后，agent 自主）。
- **风险**：生产重启撞 cron 锁致站点 down（`database is locked`）。**acceptance**：仅部署窗口存在、且有既定安全流程。**trigger response（含默认顺序）**：**默认等锁释放**（serve 不动、零停机）；若锁持有 > 5 分钟则暂停 cron（注释 crontab `*/15 … pipeline.sh`）再 `launchctl kickstart -k`；若 kickstart 后仍 `database is locked` → 中止本次重启（serve 维持旧版本、无停机），确认锁释放后重试。implementer 按此顺序执行、不在现场重新决策。
