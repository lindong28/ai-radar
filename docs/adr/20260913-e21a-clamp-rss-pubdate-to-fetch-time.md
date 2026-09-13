# ADR-20260913-e21a：RSS 入库把未来 pubDate 钳到当前时刻，只在 RSS 路径上

- Status: accepted（2026-09-13，决策评审 gate 出口为 waive 后放行，见 Consequences）
- Date: 2026-09-13

## Context

用户 2026-09-13 报告首页顶部出现一条 `9月14 8:00` 的条目，而当时是 9-13 10:19 CST。

实测读数（2026-09-13 10:20–10:30 CST）：

1. **上游自己给了未来时间戳**。`https://openai.com/news/rss.xml` 中
   `perplexity-improving-accuracy-with-astra` 一条的 `<pubDate>` 是
   `Mon, 14 Sep 2026 00:00:00 GMT`。同一 feed 的 pubDate **非单调**（第 2 条 09-11 10:00、
   第 3 条 09-11 16:00），即上游的排序与日期都不可靠。该文章当时已可访问、已在我方热点榜
   （92 热度），故更像元数据错误而非 embargo——但这是推断，不是读数。
2. **我方解析无上界**。`fetcher/rss.py:_published_at()` 只做时区归一到 UTC。
3. **频次 = 1**。全库 117302 条中 `published_at` 晚于取数挂钟的仅 1 条。
   量具对照：同一查询放宽到 `-24 hours` 命中 99876，故"1"是真读数。
   （首次测量用坏量具得出 71858 的假读数——把 `published_at` 的 `'T'` 分隔符与
   `datetime()` 输出的空格混比，因 `'T' > ' '` 几乎全表命中。已弃用。）
4. **时区侧无缺陷**。`web/routes/curated_archive.py:19` 用 `(dt + timedelta(hours=8))`
   分组，即存 UTC、按 UTC+8 渲染，换算正确。用户看到的 `08:00` 是 UTC 零点的正确本地化。

危害来自与 [ADR-006](./006-curated-archive-mode.md) 的交界面：归档面「按发布时间倒序」，
故一条未来时间戳把自己**永久钉在首位**（直到真实时间越过它，本例约 22 小时），
并生成一个只含 1 条的 `data-date="2026-09-14"` 日期分组。

## Decision

`fetcher/rss.py:_published_at()` 给解析结果加上界：解析值晚于当前时刻时取当前时刻。
同时把已入库的 `items.id = 8bc5d4adf864b177` 的 `published_at` 由 `2026-09-14T00:00:00Z`
改为其 `fetched_at`。

**语义变更**：`published_at` 由"上游声明的发布时间"变为"上游声明的发布时间，但不晚于
**我方解析到它的那一刻**"。ADR-006 排序契约里"发布时间"对撒谎信源的取值因此改变。

⚠️ **本文件名里的 `fetch-time` 是误称**（review-gate F5）。`rss.py:62` 的 `fetched_at = utc_now()`
在条目循环**之前**取一次，而 `_not_after_now` 的 `now` 对每条 entry 各重取一次，故钳到的是 parse
time、不是该行的 `fetched_at`；跨秒时钳过的行会出现 `published_at > fetched_at`。ADR 文件名
append-only 不改，在此标明。**别把 `fetched_at` 当 first-seen 的代理**——见下文 Consequences 的 F1。

## Scope

- **只覆盖 RSS 入库路径**（`fetcher/rss.py`）。`fetcher/web.py` 与 `fetcher/x_api.py` 不动。
- ⚠️ **"RSS 路径"包含微信源**，不要按源类型读这句。`runner.py:150` 的 `parse_feed` 是默认分支，
  只有 `kind == "x"` + `x_api` adapter（`:123`）与 `kind == "web"`（`:139`）分流出去，
  故 `kind == "wechat"` 的 feed 源也经 `_published_at`。caller 初稿把本节写成"微信各路径不动"，
  是错的；下文 Consequences 记了由此带来的一处潜在交界面。
- 作用域依据：`rigor-tiers.md`「已观察到的失败」这条来源的射程列写明「只补该失效域、不外扩」，
  而实测那 1 条越界条目落在 rss.py。用户 2026-09-13 在三档作用域里选定本档。

## 被否决的备选

| 备选 | 否决理由 | 谁否决 |
|---|---|---|
| 只 UPDATE 那一行，不动代码 | 下次上游再撒谎会重演 | 用户 |
| 只在展示层（归档面 / timeline / hot）钳制，保留上游原值 | 数据保真度最高，但要改多个面、每面各自验证 | 用户 |
| 不动，等 2026-09-14 08:00 CST 后自愈 | 在那之前首页顶部一直挂一条假日期分组 | 用户 |
| 入库时丢弃未来条目（不收录） | 会让一篇真实存在、已在热点榜上的文章整篇消失 | caller |
| RSS + web 两条路径都加钳制 | `web.py:_date()`（`:77-88`）实测同样无上界、18 个 web 源走它，**但全库 0 条 web 源越界条目**——属预防性加码，无 `rigor-tiers` 认可的来源 | 用户（2026-09-13 选定只 RSS） |
| 钳制时把原始 pubDate 存进 `extra_json` | 可恢复性是用户未提出的非功能属性，不自行加码 | 用户（2026-09-13 选定不保留） |

## 已知未验证项

1. **未测 `x_api.py` 是否有同类无上界解析**。其 `created_at` 由 X 服务端生成，风险低但未实测。
2. **未测是否存在合法预告未来发布的信源**（embargo / 定时发布）。若存在，钳制会把它的发布时间
   压成抓取时间，该条会早于应有时间出现在归档面上。未做的检索：遍历 43 个 feed 源的历史 pubDate
   看有无"首次出现时即在未来、随后自然到期"的模式。
3. **`web.py:_date()` 的同形缺口仍在**（本轮实测确认），按本决策作用域有意不修。

## Consequences

**恢复性**：原始 pubDate 不保留，且恢复**不确定**。`fetcher/dedup.py` 有三条写入路径：
同 `content_hash` 命中（`:145-157`）只刷 `fetched_at` / `extra_json`；
**同归一化 URL 而内容变化（`:158-188`）的 UPDATE 会连 `published_at` 一起写**；
其余为 INSERT。故回滚本决策后，只有该文章内容再变一次时原值才会被带回来，
内容不再变则永久保持钳制值。

（caller 在送审 packet 里曾把这条写成"`published_at` 写一次后再抓不更新、钳制不可逆"——
评审者查证后指出该描述不完整，此处为更正后的事实。）

**与 ADR-059 微信跨源身份的潜在交界面**（本轮 review-gate 定档时发现，尚未激活）：
`dedup.wechat_duplicate_id()` 用 `author` + 标题 + `published_at BETWEEN published ± 5 分钟`
（`WECHAT_IDENTITY_WINDOW`，`dedup.py:21`）跨源认同一篇文章。钳制把未来 pubDate 压成"我方见到它的
时刻"，而两个微信 feed 的首次入库时刻可能相差超过 5 分钟 ⇒ 同一篇文章的两份副本会落在窗口之外、
被存成两条。

该风险需要三件事同时成立：mp2rss（或第二个微信 feed）重新启用、出现一篇 pubDate 在未来的公众号
文章、两个 feed 的入库时刻相差 >5 分钟。当前三条都不成立——`MP2RSS_FEED_URL` 未配置、
该源自 2026-09-04 起 0 次抓取，全库微信越界条目 0 条。故记录为潜在风险，本决策不为它加处置。

## review-gate findings（2026-09-13 一轮，general-purpose-readonly）

**F1（HIGH，已修）：数据修复配方用错了锚。** 初次修复把该行 `published_at` 写成它的 `fetched_at`，
而 `dedup.py:145-157` 在**每一轮**抓取都 `UPDATE items SET fetched_at=?` ⇒ `fetched_at` 是
**last-seen 不是 first-seen**，越晚执行偏得越多（实测它在本次 session 内从 `03:03:32Z` 漂到
`04:47:18Z`）。初次写入的 `2026-09-13T03:03:32Z` 比真相晚 ≥26 小时。

已改为 `2026-09-12T00:45:14Z`，推导：`logs/pipeline-20260912-083000.log` 是唯一提到该 item id 的
pipeline 日志，其 fetch 阶段为 `08:30:04`–`08:45:14` CST（= `00:30:04Z`–`00:45:14Z`，该轮
`OK openai_blog fetched=1192 inserted=2`），随后 08:46:46 enrich、`00:49:21Z` 进入首个含它的
curation run。取该窗口上界，误差 ≤15 分钟。

**通用教训**：任何"把 published_at 退回到我方见到它的时刻"的配方都不能用 `fetched_at` 当锚。

**F2（HIGH，缺陷独立 / 危害模型与残留记录为本 ADR 的增量）：真正的爆炸半径是精选 freshness 池塌缩，
不是假日期分组。** `curator/select.py:640-651` 先取 `latest_fresh_date = max(fresh_pool 的上海日期)`，
再只保留等于该日期的条目。未来戳落在 48 小时 `fresh_pool` 窗内 ⇒ 独占 `latest_fresh_date`
⇒ `fresh` 退化成 1 条 ⇒ 36 个 freshness 槽只用掉 1 个，其余 39 槽从**全历史**按分取。

生产实况读数：`2026-09-13T00:30Z`–`03:29Z` 的连续多个 run `min_pub` 全是 `2024-12-18T16:47:58Z`，
即**首页主面退化成 all-time greatest hits 约 27 小时**，而它不报错、不告警，页面只是"看起来有点旧"。

**已确认解除**：F1 修复后的两个 run（`04:25:50Z`、`04:44:29Z`）`min_pub` 由 `2024-12-18` 跳到
`2026-02-12`，与 reviewer 的「已修复」模拟读数一致。这同时关掉了它那条承重未核实项
（"修复之后是否已有一次 curation run 跑过"）。

**残留（本 ADR 记录，不为它加处置）**：退化窗内 `published_at` 晚于 `2026-09-11T23:36:54Z` 的
2203 条已入库条目中只有 1 条被精选过（就是那条未来戳自己）。`fresh` 只取 `max` 那一天，
故 09-12 那批即使仍在 48h 池内也已被 09-13 顶掉，此后只能靠全历史分数竞争 ⇒ 归档面在
09-12 08:49 CST → 09-13 11:00 CST 这段存在**永久版面缺口**。是否回填交用户裁决。

**F8（LOW → 已修措辞；其正向后果为新记录）**：`dedup.py:158-188`（同归一化 URL、内容变化）的
UPDATE 会连 `published_at` 一起重写 ⇒ 上游只要**持续**给未来戳，每次内容变动都会把它重钳到当时的
now、重新弹回归档面前列；而弹回之后"明天的日期"这个 tell 已经没了，**比钳制前更难发现**。
本 ADR 此前只从"回滚后原值能不能带回来"的方向写过这条 UPDATE 路径。

**仍未处置、已交用户裁决的 findings**：F3（全部写入路径都无 `published_at > now` 不变量探测，
且全仓无任何此类检查；`hot_cache` 与 `_compute_daily_archive` 的两处回退只保护 `/hot` 与日期列表，
归档面主序与 freshness 池都没有）、F4（同批多条未来戳塌到同一秒，相对顺序退化成 `id DESC` 即 sha1
顺序）、F7（新增测试未覆盖 F2 那一层，且 `_not_after_now` 的 `now` 不可注入，把参照换成 `fetched_at`
三个测试照过）、以及上文 F2 的残留回填。F6（挂钟向后跳会静默钳错该时段全部正常条目，且按用户裁定
不保留原值故不可恢复）记录为已知风险。

**决策评审 gate**：2026-09-13 一轮，Codex read-only，session `01a098c7-43d4-7d72-81f3-b729641a9be7`。
判据 1（是否与替代项比较）= **不成立 · 应修 · 决策增量**，裁决出口「交用户」，
两条理由：① 全局 RSS 钳制证据不足（越界读数只有 1 条）；② 原始时间不可确定恢复这一点未向用户告知。

**waive 理由**：两条均已消解而非带病落地——① 作用域经用户 2026-09-13 在三档里显式选定
「只 RSS 路径」，该项因此取得 `rigor-tiers` 第三类来源（用户要求锚）；② 恢复条件已按上文
更正后的事实向用户完整告知，用户选定「不保留」。方案未更换，故按 `decision-review` 处置表
waive 行放行，不重走完整 gate。

评审者 round-1 的完整返回**未能保全**：wrapper 日志写在系统临时目录
（`/var/folders/.../codeagent-wrapper-2622.log`），读取时已被系统清理，只余进度行里被截断的判据表。
本节的裁决与两条理由取自那些进度行。
