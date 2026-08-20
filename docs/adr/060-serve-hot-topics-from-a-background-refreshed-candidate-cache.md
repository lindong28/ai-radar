# ADR-060: 热点榜由后台刷新的候选缓存供给，请求路径永不同步计算，未就绪返回 503

- Status: Accepted
- Date: 2026-08-20
- Context: 用户报障"首页当前热点打开后空白好几秒"
- Revisits: ADR-004（timeline N+1 优化范围——`hot()` 复用 curated archive 路径已破坏其「curated 固定上限 30 条」的前提）

## Context

用户报"`https://news.aiplanet.live/` 的当前热点区打开后空白好几秒"。取证把成因钉在端点自身，不在网络或边缘：

| 观察面 | 读数 |
|---|---|
| origin 本机 `127.0.0.1:8001/api/v1/hot?limit=2` | 14.29 / 14.35 / 14.37 s |
| origin 本机 `/api/v1/curated?limit=40`（同一轮） | 1.44 s |
| origin 本机 `/`（同一轮） | 1.447 / 1.453 / 1.463 s |
| 公网 `/hot` 整页 TTFB | 14.9 s |
| origin 主机 | 2 vCPU，load average 0.08（空闲），单 uvicorn |

从 origin 本机测就排除了 EdgeOne 与跨洋链路；同机 `/curated` 只要 1.44s 排除了"库慢"这个笼统解释。

根因在 `src/airadar/web/routes/curated.py` 的 `hot()`：为了返回前 2 条，它先调 `_compute_archive_page(..., limit=600)` **全量水合 600 条**，再在 Python 里按 48 小时窗口过滤、算热度、排序、切片。本地 cProfile（`data/radar.db`，3.8GB）显示这 600 条的 1.26s 里 **`_batch_related_discussions` 占 0.97s（77%）**——它对每条候选正文做反向 URL LIKE 扫描，成本随候选数超线性增长。`item_summary` 与媒体解析 0.13s，COUNT 0.10s。

这直接踩在 **ADR-004** 的前提上：那条 ADR 把 N+1 优化限定在 timeline，理由是"curated 固定上限 30 条，绝对耗时可忽略"，Cons 明写"未来数据量增长可能需要回头优化"。`hot()` 后来复用同一条 curated archive 路径却传 `limit=600`，把该前提废掉了。

`/api/v1/hot` 的响应头已是 `public, max-age=90, stale-while-revalidate=30`，但 EdgeOne 连续 4 次 GET 全部 `eo-cache-status: MISS`——边缘缓存事实上没在承担这条路径，且边缘规则住在腾讯云控制台（仓外权威，见 ADR-039），不可依赖。

## Decision

**四件事，热度公式 `heat = weighted_score*10 + related_count*5` 与排序完全不变。**

### 1. 48 小时窗口下推到 SQL

候选查询追加：

```sql
AND (i.published_at >= ?cutoff_minus_2s
     OR (i.published_at NOT GLOB '<YYYY-MM-DDTHH:MM:SS>Z'
         AND i.published_at NOT GLOB '<YYYY-MM-DDTHH:MM:SS>.[0-9][0-9][0-9]Z'))
```

不变式是 **SQL 候选集必须是 Python 保留集的超集**，Python 侧的过滤/热度/排序逻辑一行不改。论证按 Python 走的分支分两种：

- **published 分支**（`published_ts ≤ now` 且 `event_ts ≥ cutoff`）：UTC-Z 串的字典序与 UTC 时序同序 → 第一析取项命中。
- **fetched 分支**（`published_ts > now`，即发布时间在未来；或解析失败）：未来时间的 UTC-Z 串必 `> now_str ≥ cutoff_str` → 第一析取项命中；解析失败且非 UTC-Z 形 → 第二析取项命中。

三个细节各自换来一次证伪：

- **GLOB 必须校验 `Z` 收尾**，不能只校验前缀。带偏移量的串（`2026-08-18T07:30:00-05:00`）匹配前缀 GLOB 却会被字符串比较错误排除——它的字典序是**本地墙钟**不是 UTC。
- **cutoff 后退 2 秒**并渲染成无小数的规范串。否则同一时刻的不同小数精度会打架：`.000Z < Z`（`.`=0x2E < `Z`=0x5A），而 Python 解析后二者相等。后退 2 秒后，任何 `t ≥ cutoff` 的串其整秒部分严格大于 SQL cutoff，字典序在第 19 字符即判定为真，**小数部分不参与比较**。
- **最终改成允许清单而不是屏蔽清单**。屏蔽清单每补一次就漏下一个形状：只校验前缀 → 漏 `...T07:30:00-05:00`；补上 `Z` 收尾 → 漏 `...T00:00:00ZZ`（`*` 吞掉第一个 `Z`，而 `hot_datetime` 把**每个** `Z` 换成 `+00:00` 后解析失败）；再补"存在非末位 `Z`" → 漏 `...T00:00:00+bogusZ`。枚举畸形形状不收敛，枚举**两种良构形状**收敛：只有 `<秒>Z` 与 `<秒>.dddZ` 被信任按字符串比较，其余一律进候选集交 Python 判。

SQL 侧只负责给出一个不漏的候选集；真正判定始终在 Python 那一侧，所以这些条件都只是把"可能该留"的行放进来，不改变任何保留判据。

SQLite 的 `datetime()` 方案实测与 Python 解析在 7 种时间形态上逐条一致，但 hours=48 从 0.54s 涨到 1.77s（函数调用打掉走索引的可能），故弃用。

### 2. 缓存"候选集"而非"整份 payload"，且只缓存 hours=168 那一份

缓存对象是候选水合结果（含 `related_discussions`），**不是** `hot()` 的返回值。每次请求用**当次的 `now`** 重新做解析、年龄过滤、热度、排序、切片、`generated_at`。所以 `generated_at`、`/hot` 的相对时间、以及"条目滑出窗口"三件事逐请求现算。

`hours` 参数上限 168，而 `cutoff_h ≥ cutoff_168`（h ≤ 168），故 **168h 候选集是每一个更小窗口的超集**——只缓存这一份，任何 `hours` 取值都从它在内存里派生。于是缓存只有一个键：没有 per-hours 槽，就没有 LRU 驱逐、跨键并发、默认键饿死。

缓存键 = `(db 文件路径, archive_generation, category_generation, COUNT(*) 与 MAX(id) of curation_runs, sources 指纹)`。后两项各补一个触发器接不住的写入面：

- **curation_runs 的 COUNT 与 MAX**：migration 014 把 `archive_cache_curated_ai` 收窄为"仅当该 item 此前未被 curate 过才 bump"，所以同一条目在新一轮 run 里拿到不同 `weighted_score` 时 `archive_generation` 不动。COUNT 与 MAX 都要——run id 是 TEXT，一次 id 比现有最大值小的插入不会改变 MAX。
- **sources 指纹**（对全部 `(id, enabled, kind, name)` 按 id 排序取 blake2b 摘要）：`enabled` 与 `kind` 决定候选成员资格（`_archive_where` 用它们过滤），`name` 直接进 payload，而 `archive_cache_sources_au_id` 只认 `UPDATE OF id`——`admin sources reload` 停用一个来源不会推进 generation。**用内容摘要而不是几个聚合量**：聚合会碰撞，且碰撞方向是漏失效——等长改名（`Wire` → `News`）或"一开一关"都能让 COUNT/SUM 那组值原封不动。这段只在 keeper 线程上跑、不在请求路径上，读全表几百行的成本无关紧要。

**版本的职责是告诉 keeper 何时重新水合，不是让 `peek` 拒供旧值。** 这一条在实现期被推翻过一次，值得记下推翻它的读数：早先定的是"版本不匹配 = 未命中"，听起来更严格，实际后果是**每轮 curation（生产 `*/15`）之后出现一个 4–16 秒的空白窗口**——`peek` 立刻拒供，而 keeper 还要一次轮询（≤10s）加一次水合（3.5–6.1s）才补得上。那正是本 ADR 要消灭的症状。用户裁定：重算窗口内继续供上一代数据。代价是新一轮 curation 的结果最多晚约 16 秒可见，作用在一个 48 小时窗口、上游每 15 分钟才动一次的榜单上。

于是 `peek` **不再访问数据库**：唯一的服务判据是年龄。

**年龄两档，与刷新周期解耦**：`refresh_after = 120s` 触发重新水合但旧值仍可服务；`max_stale = 180s` 是**陈旧硬上界**，超过即判为不可用。两个阈值必须分开——只有一个阈值时 SWR 会让它退化成"刷新周期"而不是上界，刷新持续失败即可无限陈旧。

180s 这个上界不是可选项，它承担着**证明触发器完备性的替代品**。上面两条补进版本键的写入面是已核实的，但穷举是接不住的：`_batch_related_discussions` 从**全部 items**（不限精选）里找关联，而 `archive_cache_items_ai` 仅在该 item 已被精选时才 bump；`items.content_text` 的更新不在任何触发器覆盖内。每加一个上游，"覆盖完备"就要重新证明一次；给一条时间上界不用。

### 3. 水合归一个常驻 keeper 线程所有；请求路径是纯读

一个 daemon 线程（`_keep_warm`）每 `KEEPER_POLL_SECONDS = 10s` 跑一次**廉价的版本查询**（亚毫秒），仅当版本变了、或条目年龄超过 `refresh_after` 时才做那次 3.5–6 秒的水合。请求路径只做一次**非阻塞 peek**——纯内存读，唯一判据是年龄，连数据库都不碰——拿不到就立刻按下面的三态返回。

**轮询而不是被请求驱动**，因为没有人会通知这个进程：curation 跑在独立进程、按自己的节奏（生产是 `*/15`），本进程唯一能知道"那一轮跑完了"的办法就是自己去看。让请求来发现，等于把每轮 curation 之后的全部代价压给第一个到达的访客——那正好让"首页热点不再空白"这个目标在每 15 分钟后重新失效一次。

**请求不发起水合**，这不只是分工问题：只要请求能发起水合，客户端最后一次重试就可能正是**发起了那次成功水合**的请求——它拿到 503 就放弃，而数据一秒后才落地，于是那位访客永久看不到已经备好的热点。水合的所有权收在一个线程上，这个形态不可能出现；single-flight 也随之从"要维护的标志"变成结构性成立，不再有"标志已释放、结果尚未发布"的窗口。

**不设等待预算**——同步路由跑在 Starlette 共享线程池里，任何"等一下"都会把计算并发转成 worker 排队，持续喷请求时可以拖住其它同步页面。

keeper 用自己的 `sqlite3.connect()`，`try/finally` 保证 `close()`。这不是理论洁癖：本仓有过一次实测故障就是 web 读路径不关连接导致 WAL 膨胀、`healthz` 500 / `CANTOPEN`（commit `9801434`）。keeper 线程在锁内创建**并启动**——存进字段后到锁外再 `start()` 会留一个窗口，让第二个调用者看到 `is_alive() == False` 而再起一个 keeper，重复水合就回来了。

### 4. "未就绪" 与 "确实没有热点" 必须在响应层可区分

这是本决策最容易做错的一处，做错的后果是三重的。三态：

| 状态 | `/api/v1/hot` | `/hot` 整页 | `/` 默认视图 |
|---|---|---|---|
| 就绪 + 有条目 | `200` + items | 榜单 | SSR 直出热点块 |
| 就绪 + 无条目 | `200` + `items: []` | "过去 N 小时暂无热点"（真话） | 不渲染热点块 |
| **未就绪** | **`503` + `Retry-After: 2`** | "榜单正在生成，稍后自动刷新" + `no-store` | 不渲染热点块，CSR 兜底 |

未就绪返回 `503` 而非 `200`-空的三个理由，每一个都被实测或代码证实：

- `_public_pagination_cache_control`（`src/airadar/web/app.py`）对 `status_code != 200` 一律返回 `private, no-store`，所以 503 **自动**不进任何缓存。若返回 200-空，它会拿到 `public, max-age=90, stale-while-revalidate=30`（`/api/v1/hot` 明确在 `_PUBLIC_PAGINATION_QUERY_KEYS` 里），把一次约 14 秒的冷态**放大成约 120 秒的缓存空结果**。
- `renderHotTopics()`（`web/static/app.js`）收到空数组只隐藏容器、不重试。200-空之下"几秒后自动有"对当前访客不成立。
- `web/templates/hot.html` 的 `.hot-rank-empty` 文案是"过去 N 小时暂无热点"（`N` 由 `hours` 渲染）。未就绪时渲染它**是说假话**，不是降级展示。

配套：CSR 收到 503 走有界退避重试（2s / 4s / 8s / 16s，累计 30 秒），期间保持既有骨架占位（桌面 `.hot-topics:empty` 留 132.5px、移动断点 173px，无布局跳动）；200-空维持现状直接隐藏（那是真的没有，重试无意义）。`/hot` 未就绪时有界自动重载（3s / 5s / 8s / 14s，同样累计 30 秒，`sessionStorage` 计数防循环，就绪后清计数）。

30 秒是照最坏冷态配的，不是拍的：keeper 最多 10 秒才看一眼，一次水合实测 3.5–6.1 秒，合计约 16 秒；余量是为了不让最后一次重试恰好落在数据到达前一刻。

## Options Considered

| 备选 | 否决理由 |
|---|---|
| 不改 | 见 Context 读数；用户主动报障 |
| 只加缓存、不下推窗口 | 未命中那一次仍 14.3s |
| 只下推窗口、不加缓存 | 本地 1.19s → 0.54s；按同机 curated 的 prod/local ≈ 13.7x 外推，prod 仍约 7.4s |
| 排序阶段跳过关联讨论 | 最省（砍掉 77% 开销），但 `related_count` 上限 3、贡献 0–15 点热度，跳过会改变部分条目名次。这是用户可感知的产品行为变化 → surface 给用户，**用户选择不动公式** |
| 候选窗口用 `(published_at >= cutoff OR fetched_at >= cutoff)` 超集 | 实测 419 条 / 1.17s，几乎无收益（大量条目 published 老但 fetched 新） |
| 靠 EdgeOne 边缘缓存 | origin 已在发 `public, max-age=90`，而边缘连续 4 次 MISS；规则住在控制台，仓外权威 |
| 首页不 SSR、只留客户端 fetch | surface 给用户，**用户选择要 SSR** |
| 首页 SSR 直接调 `hot()` | 会把冷启动的 5–14s 搬进首页 TTFB，比原问题更糟。改为只 peek 已热缓存 |
| 按 `hours` 分槽 + `Semaphore(1)` | 同步路由在共享线程池里阻塞等 semaphore，喷非 48 取值可拖住其它同步页面 |
| 用 SQLite `datetime()` 做窗口比较 | 与 Python 解析逐条一致，但 hours=48 从 0.54s 涨到 1.77s |

## Consequences

改后实测（本地，同一 DB 快照）：

| hours | 候选数 | 水合耗时 |
|---|---|---|
| 6 | 1 | 0.010 s |
| 48 | 189 | 0.538 s |
| 168（缓存的那一份） | 391 | 1.159 s |
| 原实现（固定 600 条） | 600 | 1.19 s |

即 168h 冷算与原实现**耗时同量级**，但它只发生在后台线程上，不在任何请求路径上。

**行为变更（明示）**：取消 600 条上限修正了一处既有的静默截断。等价成立的**充要条件**是"所有窗口内合格条目都落在旧查询按 `published_at DESC` 取的前 600 位之内"——不是"合格条目数 ≤ 600"：前 600 位混入大量不合格条目时，第 601 位之后的合格高热条目照样被旧实现丢弃。违反该条件时新实现是旧实现候选池的**严格超集**；注意最终切片后的 payload **不是**集合意义上的超集，新纳入的高热条目会替换旧 top-N。

**新鲜度档位下调（明示）**：候选缓存硬上界 180s，叠加 `/` 与 `/api/v1/hot` 共用的 `max-age=90 + stale-while-revalidate=30`（120s），消费者可见陈旧上界约 **300 秒**。判为可接受的依据：热点取 48 小时窗口，curation pipeline 产出节奏是数十分钟量级。该上界同时作用于首页 SSR、CSR 与直接 API 消费者。

**冷态**可由部署、进程重启（`deploy/systemd/ai-radar-serve@.service` 是 `Restart=on-failure`）、主机重启、以及后台刷新持续失败进入，**频率不受本设计控制**。正因如此，"降级是否诚实"从边角情况升格为承重条件——即上面第 4 条。配套可观测：以未就绪状态服务时打 WARN（含原因与已持续时长，需节流防洪泛），后台刷新失败打 ERROR、成功打 INFO（含耗时与候选条数）。

## Scope

- **语义正确性**覆盖 `/api/v1/hot`（hours 6–168、limit 1–10）、`/hot` 整页、以及 `/` 的**默认视图**（无搜索词、无分类筛选，与既有 `show_hot_topics` 条件一致）。
- **性能读数**只在 hours=48 与 168 上取得，不外推到其它取值。
- **生产耗时未实测**——所有 prod 数字都是拿本地读数乘以同机硬件比外推的。验收条件见下。
- 候选集取消 600 上限后不再有硬上界，其规模是**随数据密度变化的量**，不是常数保证。当前快照下 hours=168 的 391 条仍少于原实现固定水合的 600 条。
- `/api/v1/curated`、`/api/v1/timeline`、`/wechat` 不在改动面内。

## 已知未验证项与验收

部署后从 origin 本机（`127.0.0.1:8001`，绕开 EdgeOne）逐项取数：

| 入口 | 状态 | 通过条件 |
|---|---|---|
| `/api/v1/hot?limit=2` | 重启后立刻（冷） | < 2.2s，返回 503 且带 `Retry-After` |
| 同上 | 预热完成后 | < 200ms 且 items 非空 |
| 同上 | 连打 3 次 | < 100ms，极差 < 50ms |
| `/` 默认视图 | 冷态 | TTFB < 1.75s（= 改前基线 1.463s × 1.2），**且与热点状态无关** |
| `/` 默认视图 | 预热完成后 | TTFB < 1.75s 且 HTML 含热点块 |
| `/hot` 整页 | 冷态 | < 2.2s，渲染"正在生成"态 |
| `/hot` 整页 | 预热完成后 | < 1.75s |

`/` 的 `< 1.75s` 只验收"无可感知回退"，不能单独证明因果上的"不依赖热点计算"；后者由实现层门禁用**阻塞计算桩**验证 peek 路径不等待。

尚未验证：缓存失效的完备性只做到"给上界"而非"证明覆盖"；`items.content_text` 更新不在任何触发器覆盖内；SSR 后 `/` 的边缘缓存行为未在真实公网验证。

## 决策评审记录

本决策过 `decision-review` gate，Codex read-only 评审者对抗式复核 6 轮。首轮 4 blocker / 3 应修，逐轮收敛至无 blocker。评审推翻的实质设计错误四处，均已并入上文：

1. "缓存整份 payload"会冻结 `generated_at` 与窗口淘汰 → 改为缓存候选集、逐请求重算。
2. 首页 SSR 直调 `hot()` 会把冷算搬进首页 TTFB → 改为只 peek。
3. GLOB 逃生口只校验前缀，被带偏移量时间串证伪；后续又被不同小数精度证伪 → 改为校验 `Z` 收尾 + cutoff 后退 2 秒。
4. "未就绪"被编码成 200-空，会被公共缓存放大、前端不重试、`/hot` 说假话 → 改为 503 三态分离。

放行时仍有 3 条应修，已并入实现要求而非 waive：CSR 需结构化拿到 HTTP status（现有 `api()` 不保留 status，不得靠解析 `"HTTP 503"` 文本判断）；`/hot` 自动重载窗口需覆盖预期冷态且就绪后清 `sessionStorage` 计数；未就绪 WARN 需节流，且需明确"持续失败"的主动发现路径（仅写日志给不出发现时限——**这一条仍未闭合**，见下）。

## 生成后 review gate 记录

实现完成后过 `review-gate`（高档，Codex read-only 独立 context）。首轮 3 HIGH / 5 MEDIUM，全部依附本轮 diff、无独立 finding。三条 HIGH 有同一个结构根因，已一并修掉：

1. **刷新其实由用户请求触发**——`_spawn_refresh` 只从 `peek()` 调用，于是每轮 curation（生产 `*/15`）之后第一个访客承担 3.5–6 秒冷态，目标 1 每 15 分钟重新失效一次。
2. **最后一次重试可能正是发起了那次成功水合的请求**，它拿到 503 就放弃，数据随后落地而当前访客永久看不到。
3. **single-flight 标志在发布结果之前释放**：`finally` 先 `_refreshing=False`，再另取一次锁发布结果；落在两次加锁之间的并发请求会看到旧缓存 + 标志已释放，于是重复发起整轮水合。

修法是把水合的所有权收给一个常驻 keeper 线程（上文 Decision §3），三条同时消失，另外顺带消解了"线程启动失败后 `_refreshing` 永久卡死"那条 MEDIUM。其余 MEDIUM 也已修：非末位 `Z` 的逃生口（§1 第三条）、`sources.enabled/kind/name` 进版本键（§2）、SSR/CSR 同形测试的 rank 断言此前因 `rsplit` 写法实际打不中目标（已改为渲染真实 DOM 逐条比对，并新增转义测试）。

**仍未闭合、需要单独跟进**：未就绪与刷新失败目前只有日志，没有主动发现路径——"keeper 持续失败"会表现为"热点块偶尔不见了"，而不是一个有发现时限的具名事件。这一条按告警设计原则应有一条 fire 条件，本轮未做。

**残留缺口，明示保留**：形如 UTC-Z 规范形状、末位只有一个 `Z`、但语义非法（`1999-13-45T00:00:00Z` 这类）**且**字典序落在 cutoff 之前的时间串，Python 会因解析失败走 fetched 分支从而可能保留它，SQL 会排除它。判为可接受：这需要一条同时满足"形状合规 + 语义非法 + 日期陈旧"的数据，本身已是数据完整性缺陷，且只影响少一条候选、不产生错误内容。`tests/test_hot_candidate_cache.py::test_declared_residual_gap_has_exactly_the_shape_the_adr_claims` 把这个形状连同三个"缺一条即不漏"的对照钉住。

**本地快照读数（2026-08-20，`data/radar.db`，54920 行）**：`published_at` 不匹配 UTC-Z GLOB 的行数 **0**，含非末位 `Z` 的 **0**，毫秒形态 **986**，真正落在未来的发布时间 **0**（`MAX(published_at)` 早于当前 UTC）。这些随 pipeline 持续变化，是快照而非常量。

> 更正：本 ADR 早先版本写"757 行 `published_at > now`（未来发布时间）"，那个数字来自一次**错误的比较**——`datetime('now')` 返回空格分隔的 `2026-08-20 10:01:13`，而 `published_at` 用 `T` 分隔，`'T'`(0x54) > `' '`(0x20)，于是该查询实际数的是"今天及以后发布"（现为 1140 行），不是未来。用同格式的 `strftime('%Y-%m-%dT%H:%M:%SZ','now')` 重测为 **0**。fetched 分支仍需正确处理（`hot_datetime` 解析失败同样走它，且上游时间戳偏斜随时可能出现），超集论证不依赖这个计数——但那句断言本身是错的，故在此更正而非删除。
