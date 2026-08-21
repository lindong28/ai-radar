# ADR-062: 精选 ↔ 全部 AI 动态 的切换成本，在查询、边缘与导航三层同时切掉

- Status: accepted
- Date: 2026-08-20

## Context

用户报告：在 `news.aiplanet.live` 左侧栏交替点击「精选」`/` 与「全部 AI 动态」`/all` 有明显延迟，而对照站 AIHOT（`aihot.virxact.com`）同样操作顺滑。要求是把 AI Radar 的操作体验做到与 AIHOT 一致。

`docs/issues/ux-contract-issues.md` 里已有一条同题的验收口径（真实冷连接下首屏等待不得显著慢于 AIHOT 对照，median TTFB 与 FCP 不超过其 110%），本决策沿用它。

### 实测的三层成因

**① origin 每次现算。** 在部署机上对当时部署的代码（sha `5039988`；`curated_archive.py` / `timeline.py` / `related.py` 与本仓 md5 一致）跑 cProfile：

| 路由 | origin TTFB | 主要构成 |
| --- | --- | --- |
| `/` | 1.300 s | `_batch_related_discussions` **1.008 s** + `_latest_run` **0.349 s** |
| `/all` | 0.572 s | `_timeline_data_version` **0.433 s** + items 查询 0.15 s |
| `/daily` `/about` `/more` `/bookmarks` | < 30 ms | — |

- `_batch_related_discussions` 的**反向**查找（"谁提到了这条 item 的 URL"）为当页 40 条各拼一个 `content_text LIKE '%url%'`，OR 起来全表扫 `items_fts`（54,750 篇全文），只为渲染「关联讨论 N 条」这个角标。
- `curation_runs` 只有 8,235 行却占 **688 MB**（`input_eval_ids` / `output_curated_ids` 两个宽 TEXT 列），而 `created_at` 无索引 → `ORDER BY created_at DESC LIMIT 1` 是全表扫，读完 688 MB。它在 `_timeline_data_version` 里出现 3 次、`_latest_run` 1 次、timeline items 查询的标量子查询 1 次。
- 区分度读数（据以判定成本是固定开销、不随行数变化）：`/api/v1/timeline?limit=1` 与 `limit=40` 耗时相同（0.571 s vs 0.574 s）；带 cursor 强制绕过 total 缓存与不带 cursor 也相同（0.575 s vs 0.574 s）。

**② 边缘没在缓存这两个页面。** EdgeOne 规则只覆盖 `/wechat`、`/api/v1/wechat`、`/style.css`、`/app.js`、`/img`。`/` 每次 `eo-cache-status: MISS`，`/all` 连 `Cache-Control` 都不发。阳性对照——`/wechat` 是唯一挂着 follow-origin 规则的 HTML 路径，连续三次 MISS(0.216 s) → HIT(0.086 s) → HIT(0.075 s)，命中时 `server-timing` 冻结在缓存那一刻的值，origin 完全不被打扰。

**③ 切换本身是整页导航。** AIHOT 是 Next.js App Router，侧栏是 `<Link>`，切换为客户端路由 + prefetch。AI Radar 每页是独立的 Jinja SSR 文档，侧栏是裸 `<a href>`。

## Options Considered

### Option A: 只做 origin 查询优化

- Pros: 纯后端，无 UX 行为变化，回滚最容易。
- Cons: 公网仍要一次跨网往返（实测 origin→本机约 0.2 s），且切换仍是整页导航——白屏与滚动位置重置照旧。

### Option B: 只加 EdgeOne 缓存规则

- Pros: 零代码，单点杠杆最高，命中后 TTFB ~0.08 s。
- Cons: 缓存失效、SWR 回源、以及带 `q=` / `category=` 的筛选页**永远不可缓存**——那些请求仍是 1.3 s。止血不治本。

### Option C: 三层同时做（采用）

- Pros: 唯一能同时消掉"未命中时的 1.3 s"与"整页导航的白屏"的组合。
- Cons: 改动面最大，且引入一套此前不存在的客户端页面生命周期。

用户在三者中显式选 C。外部决策评审推荐"A+B 先行、客户端切换后置到有真实读数再定"，理由是其增量收益当时无读数；该推荐连同理由摆给用户后，用户仍选同批实施。**这是一次显式 waiver，记录在此**：客户端切换在**生产**上的增量收益至今没有真实浏览器读数（见下「未验证」）。

## Decision

### D1 · `curation_runs` 的最新 run 查询

新增 `idx_curation_runs_created_at(created_at DESC, id DESC)`（migration 019），`_latest_run` 由 `SELECT *` 收窄为实际读到的 `id, ruleset_version, created_at` 三列。

`id DESC` 不是装饰：`created_at` 不是全序，两个 run 时间戳相同时不同调用点会各自选中不同的行。全仓 **13 处** latest-run 排序统一（分布在 8 个文件：`web/routes/timeline.py` 4 处、`web/routes/curated_archive.py`、`cli.py`、`curator/precompute.py`、`eval/judge.py` 两处——含一处带日期过滤的、`eval/distribution.py`，以及测试端的 `tests/playwright/conftest.py` 与 `tests/test_db_slim_web_parity.py` 两处）。核验命令：

```bash
grep -rc "created_at DESC, id DESC" --include="*.py" src/ tests/   # 13
grep -rn "curation_runs" --include="*.py" src/ tests/ | grep -i "order by" | grep -v "id DESC"   # 空
```

### D2 · `item_links`：把反向链接从每次现扫改成一条索引边

`item_links(item_id, linked_url)` + `(linked_url, item_id)` 覆盖索引 + `item_links_backfill` 账本 + 两个触发器（`AFTER DELETE ON items` 删边、`AFTER UPDATE OF id ON items` 改键）。

三处不显然但承重：

- **前缀匹配而非等值**。引用方文本常在 URL 后附加内容。生产快照实测：30,394 条边里 12,913 条与某 item 的规范化 URL 完全相等，另有 **326 条是严格扩展**——等值匹配会静默丢掉这 326 条。上界用真正的 prefix successor（处理 U+10FFFF 进位与代理区跳过）；`prefix + '￿'` 看着对但会漏掉延伸进补充平面的 URL。
- **整页一条语句**。`/` 一页交进来 40 个 URL，逐 URL 发一次范围查询答案相同却是 N+1。用 `WITH targets(lo,hi) AS (VALUES ...)` 驱动索引。
- **回填未完成时回落到旧的全表扫描**，由账本判定。半填的表返回**更少**的行而不报错——读者看到角标消失，无法与"这篇确实没有关联讨论"区分。回落慢但正确。
- **回填的每一批在一个 `BEGIN IMMEDIATE` 里读写**。生产上 pipeline 与站点同时跑；读在事务外时，两步之间被重新抓取的条目会先由 `upsert_item` 写下新边、再被回填用旧正文覆盖，而账本照样标记完成。

增量维护挂在 `fetcher/dedup.py::upsert_item` 里两个真正改写 `content_text` 的分支，与写入同事务。

### D3 · `_timeline_data_version()` 的覆盖面**不动**

只让它变便宜（受益于 D1 的索引），维度一个不删。理由是**不在一次性能修复里顺带改变"哪些写入使缓存失效"**——两者混在一起，日后总数真的开始发陈旧时就分不清是哪一半造成的。

同时明确记录：该 tuple **本来就不完整**（`sources.enabled` / `kind` 变更、既有 item 与 evaluation 的原地更新都不会推进它），这与 ADR-005 自己写下的契约不符。该缺口独立成条记在 `docs/issues/general.md`，不在本次修。

### D4 · 让 `/` 与 `/all` 的 HTML 走边缘

应用侧：`/all` 加入 `_PUBLIC_PAGINATION_QUERY_KEYS`（允许键仅 `page`），使其像 `/` 一样发出 `public, max-age=90, stale-while-revalidate=30`；带 `q` / `category` / `channel` / `cursor` / `limit` 的变体按既有 helper 落 `private, no-store`。

生产侧：**覆盖式修改现有规则 `rule-3tqd7hwdvi49`**，把 `/`、`/all` 加进它的精确路径集合，不新增规则、不改优先级、不动 FollowOrigin 动作。回滚对象是 `web/edgeone-cache-rules.json` 里改动前的完整规则对象。

一个曾经悬空的前提已实测关闭：**EdgeOne 的缓存 key 含 query string**。在同一条规则上取证——`/wechat` 与 `/wechat?page=3` 各自独立 MISS → HIT，内容哈希不同（`82915a0d…` vs `bdb1eb31…`），两个查询不共用条目。

### D5 · 只在 `/` ↔ `/all` 之间做客户端切换

范围刻意窄：只有这两条路径，且只在 URL **既无 query 也无 fragment** 时拦截。其余一切（`/hot`、`/daily`、`/wechat`、文章页、这两页的筛选与分页视图、以及移动端指向 `/all#search` 的搜索按钮）保留浏览器原生导航。

配套引入**页面生命周期**：`beginPageLifetime()` / `pageSignal()` / `onPageTeardown()`。此前每个页面是独立文档，浏览器替我们回收绑定；换 `<main>` 只回收 `<main>` 内部的东西，凡挂在 window / document / 常驻 chrome 上的都会存活并被下一页的 initializer 再绑一次。其失败形态不是报错，而是第 N 次后退被处理 N 次——几次切换之后才显形，且离成因很远。

不采纳 Speculation Rules：它是竞争方案而非默认叠加项，且 Safari / iOS WebKit 不支持，而本站有专门的移动层（ADR-012）。

## Consequences

**配对性能测量**（同机、同一份 3.8 GB 生产快照副本、同一份代码，唯一变量是有没有跑 migration 019 + 回填）：

| | before（best） | after（best） |
| --- | --- | --- |
| `curated(page=1, limit=40)` → `/` | 6774 ms | **77 ms** |
| `timeline(page=1, limit=40)` → `/all` | 430 ms | **8 ms** |

回填 30,390 条边耗时 2.7 s，`item_links` 表加索引共约 5 MB。

**客户端切换的增量收益：本地建立不起来，是一个公开的证据缺口。**

尝试过四种本地仪器，每一种都因不同原因不成立，且每一次的读数都推翻了上一次的解释：

| 仪器 | 为什么不成立 |
| --- | --- |
| headless 浏览器直接量公网 | 经系统代理有约 1 秒固定开销。阴性对照：origin 侧 6 ms 的 `/about` 读出 1012 ms |
| 两臂口径不一致 | 一臂取新文档自己的 `domContentLoadedEventEnd`，另一臂取含 Playwright 点击开销的墙钟，不可比 |
| `page.route` + `time.sleep()` 注入延迟 | 阻塞 Playwright sync API 的 dispatcher 线程。实测把整个 playwright 套件从 `131 passed in 44s` 变成 `18 failed / 114 passed / 7 errors in 582s`，**且损坏落在别的文件上**（`Page.goto` 与 `BrowserContext.close: Route.fetch` 超时），形态与本仓已记录的全量顺序依赖 flaky 完全一样，差点被当成既有问题写掉 |
| CDP `Network.emulateNetworkConditions` | 节流本身对 loopback 有效（8.2 ms → 306.6 ms 实测）。但本次改动给 `/` 与 `/all` 加了 `max-age=90`，**浏览器于是缓存了这两个文档**，反复 `page.goto` 不再走网络，被注入的延迟对"整页导航"那一臂根本不生效 |

最后一条顺带暴露了一个真实的、此前没被算进来的因素：**在浏览器缓存命中的情况下，原生导航也不付文档往返**。所以 D5 的收益不能按"省下一次往返"来估——它省下的只是文档拆建（重新解析 HTML、重新执行 `app.js`、白屏、滚动位置重置），而那部分在 fixture 规模的页面上小于这套机制自身的开销。

据此，**本 ADR 不对 D5 的增量收益作任何量化声明**。playwright 建立的是结构事实：切换不重载文档、不丢滚动位置、侧栏状态跟随、失败回退真导航、监听不随轮次累积。量化收益只能在生产上按 `docs/issues/ux-contract-issues.md` 的口径测——那也是用户在知情"当时无读数"的前提下仍选择同批实施时接受的。

**发布顺序被 `deploy/sync/schema_gate.py` 钉死**：它要求代码的 `migrate()` 声明的每张表与索引在活动库中**已经存在**。所以必须是「本地跑 migration + 回填 → DB 快照同步到 `committed` → 才能部署代码」，反过来会被 gate 拒绝。

**ADR-004 的范围结论被本决策推翻。** 它当时决定只优化 timeline 路由，理由是「curated 数据量小（<=30），N+1 的绝对耗时可忽略」。该前提已不成立：curated 是 40 条，且瓶颈不是 N+1 本身，而是 batch 化之后那一次全表 LIKE 扫描。

**`related.py` 的反向方向不再经过 `items_fts`**，因此该方向的正确性不再依赖 FTS 索引的完整性，转而依赖 `item_links` 的回填与增量维护。

## 未验证（waiver 与证据缺口，不得被后续读者当作已验证）

1. **生产 after 读数缺失**。配对测量那张表的 77 ms / 8 ms 取自本地 3.8 GB 库；生产是 2.7 GB 库 + 2 vCPU，before 为 1380 ms / 608 ms。部署后须按 `docs/issues/ux-contract-issues.md` 的口径重测。
2. **客户端切换在生产上的增量收益无真实读数**——本地四种仪器全部不成立，逐条见上。用户已知情并选择同批实施（见 Options）。
3. **AIHOT 的客户端切换未被直接观测**。它对 agent 返回 `{"error":"blocked","code":567}`，headless 进不去；架构判断来自其 HTML 里的 Next.js App Router chunk 路径与侧栏形态。
4. **EdgeOne 规则尚未修改，CAM 密钥的 `ModifyL7AccRule` 权限未验证**——仓内最小权限契约目前只记录 Describe 与 purge。
5. **Safari / iOS WebKit 上未实测** `AbortSignal` 的 listener 选项、`history.scrollRestoration`、`DOMParser` 这套组合。
6. **预取流量的成本正当性证据不足**（外部评审记为 MEDIUM）：每次跨路由 `mouseover` / `touchstart` 都会取一份完整 SSR HTML，即使之后没有点击；只有"先 hover、随后 30 秒内点击"的读者获益。30 秒缓存去重限制了重复取，但未点击者没有任何行为收益。
7. **`_reverse_link_candidates` 的 hydration 查询数没有被测试守住**。现有断言只数了 `item_links` 那一次索引查询是单条，没有断言随后按 id 取候选行的那次也是一次有界查询。当前实现确实是一次（`WHERE i.id IN (...)`，无分块），但没有任何断言拦住把它改回逐条取——而那正是这次改动要消灭的形态。
8. **`30394 / 12913 / 326` 三个数字的 oracle 是转录而非真消费者**：来源 SQL 已写进 `citing_item_ids` 的 docstring，但那两条 SQL 是对 `clean_url` 与 `prefix_successor` 的 SQL 转写，不是调用真实的 Python 实现。数量级可信，逐条相等未经真消费者复核。
