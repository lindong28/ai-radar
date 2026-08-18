# AI Radar · UX Issues

> 配对 [`ux-contract.md`](../contracts/ux-contract.md) 的 issue ledger。装当前 product 已确认的 user-observable 问题。
>
> 协议：`~/.claude/references/ux-test-protocol.md` §3。
> 状态语义：`pending` 已发现未处理 / `in_progress` 正在修 / `done <date>` 已修复并验证 / `cancelled` 决定不修。

---

## Issues

- [done 2026-06-08] `/wechat` 搜索对空格敏感：`分享Claude Code`（库内无空格）能搜到，`分享 Claude Code`（带空格）搜不到
  - 背景：用户 2026-06-08 反馈带空格搜不到内容，体验不好。
  - 根因：当时搜索 helper（现位于 `src/airadar/web/routes/search.py` 的 `expand_st_variants` / `like_patterns_for_query`）只 `strip()` 首尾、不归一化内部空格，被匹配列（title/author/abstract/tags）也不归一化；LIKE pattern `%分享 Claude Code%` 命中不了存库的 `分享Claude Code`。
  - 影响面：同一搜索通道也服务 timeline/curated，修一处全局受益。
  - 修复：搜索做空格不敏感——查询 pattern 与被匹配列两侧都剥除空白（含全角空格）后比对，并补 FTS 路径（timeline/curated 长查询走 trigram FTS）。加回归测试复现该用例；89 测试通过、ruff clean。**需重启 serve 生效**（纯 Python web 层改动）。

- [done 2026-06-08] `/wechat` 公众号「赛博禅心」头像缺失（回退显示首字"赛"，被读作头像不对）
  - 背景：用户 2026-06-08 反馈该公众号头像不对。
  - 根因：生产库 `data/radar.db` 中它是 15 个账号里唯一 `avatar_url` 为空者——2026-06-02 Playwright 抓 `round_head_img` 失败，落 7 天负缓存（`fetcher/runner.py` `WECHAT_AVATAR_NEGATIVE_CACHE_TTL=7d`），到期前不重试，页面回退显示首字。
  - 修复：新增 `admin wechat-avatar refresh --account <名>` CLI（清该账号缓存 + 实抓），对 赛博禅心 实跑 Playwright 重抓成功，`avatar_url` 填入有效 `mmbiz.qpic.cn` URL（非空头像 14→15，已生效无需重启）；抓取失败负缓存 TTL 由 7 天缩到 2 天让偶发失败更快自愈。手动覆盖未用到。加 CLI + TTL 回归测试。

- [cancelled] 微信文章解读：解读内容质量验收（用户 2026-06-02 决定不验）
  - 背景：create-ux-contract 时 de-scoped（功能流程 + 视觉交互优先）。execute-ux-contract round 1 的机制/功能/视觉验收（WX-1~9）全过后，用户决定**不再单独验内容质量**——渲染正确即足够；解读忠实/有用、"值得读"判定合理性、标签语义交由 summarizer（ai-assistant summarize-article）上游保证。
  - 原计划（不再执行）：抽样 `/wechat` 详情对照原文微信文章判断忠实/有用 + 值得读判定是否合理 + 标签语义。
  - 机制验收记录：`plans/20260602-2034-ux-contract-ux-test/`（TS-001~008 PASS，TS-009 不可自然到达）。

- [pending] 部分信源的展示名把抓取实现细节暴露给读者（如 `Hacker News popular via buzzing.cc`）
  - 背景：2026-08-17 与 aihot.virxact.com 做同条件成对 UI 对比时发现。注意最初观察到的 `IT Home：RSS` 形态在当日某次部署后已消失——复测线上 `/all` 40 个 `.source-name`，含 "RSS" 的为 0；剩下的是 `via buzzing.cc` 这类。
  - **不能用 `aihot_aliases` 当展示名**（已证伪的方案）：`tests/fixtures/aihot_sources.json` 里 52 条 alias 中 31 条含 "RSS"、47 条含括号（`Ars Technica：AI（RSS）`、`Claude：YouTube（RSS）`），拿它当展示名会原样重新引入本 issue 要消除的形态。且 `docs/plans/20260812-aihot-original-source-alignment/plan.md` 明文规定 `name` 才是 public display name、`aihot_aliases` 是 alternate/historical aliases，fixture 不得成为第二个 runtime registry。
  - 也不能直接改数据层 `sources.name`：它同时写进 `items_fts` 索引（`db.py:124`）、被 `eval/judge.py` 的评测对齐用作相似度输入（`judge.py:676-682`）、并由公开 sources API 返回。
  - 真要做需新建一个独立的展示名权威字段（覆盖 162 条主信源），服务端投影给全部消费者（`/all`、`/hot`、日报、条目详情、关联讨论、`/about` 的 `/api/v2/sources`），并处理旧 localStorage 书签快照只存了 `source_name` 的兼容问题（`app.js:2005`）。属独立项目，不随 UI 借鉴那一轮做。

- [pending] 没有 `window.matchMedia` 的浏览器上，首屏定主题成功但 hydration 会抛 `TypeError`
  - 背景：2026-08-17 做 ADR-055（默认主题跟随系统）时由对抗审查发现，属**该改动之前就存在**的独立问题。
  - 现象：内联脚本与 `initThemeToggle()` 现在都对 `matchMedia` 缺失做了常量兜底（落深色），但 `web/static/app.js` 里另有若干处直接 `window.matchMedia(...)` 调用（列表卡片的 `mobileFeed` 判定、响应式绑定等）没有保护。访问 `/`、`/all`、`/wechat` 或带收藏数据的 `/bookmarks` 时，hydration 在那些调用点抛 `TypeError`，后续渲染与交互中止。
  - 复现：隔离调用 `itemCard(..., {mobileFeed: true})` 且 `window.matchMedia` 不存在 → `TypeError: window.matchMedia is not a function`。
  - 现实包络：主流浏览器均支持 `matchMedia`，因此审查按 stakes 封顶 MEDIUM、不阻塞发布。ADR-055 已显式把容错声明收窄到主题路径，不声称整页容错。
  - 若要修：统一一个 `prefersDark()` / `isNarrow()` helper 收口全部 `matchMedia` 调用点，而不是逐处加判断。

- [pending] 评分 tooltip 写「满分 10」，但 T1 信源的 weighted_score 会超过 10
  - 背景：2026-08-17 给评分加语义标签（`AI 评分 89`）时，对抗审查指出写死分母会渲染出 `108/100`；据此查生产实况证实。
  - 读数：`GET /api/v1/timeline?limit=100` 的 100 条里，**5 条 `weighted_score > 10`，最大 10.75**；tier 分布 T1=94 / T1.5=3 / T2=3。根因是 `src/airadar/curator/score.py:8` 的 `TIER_MULTIPLIERS = {"T1": 1.25, ...}` 直接乘在加权分上（`score.py:29`）。
  - 现状：卡片上不再写分母（只显示 `AI 评分 108`），所以页面本身不再有假值；但 `web/static/app.js` 与 `web/templates/_prepaint_list.html` 两处评分元素的 `title` 仍写着「LLM 5 维评分加权后得分（满分 10，阈值 6.5 进精选）」，对 T1 条目是假的。
  - 未修的原因：改这句文案要先决定真正的口径（上界按 12.5？分 tier 说明？还是把 tier 乘数移出展示分），那是一个独立决策，不随本轮 UI 改动做。

- [pending] 日报索引只显示「N 篇报道」，无法据以选择看哪一期
  - 背景：2026-08-17 与 aihot.virxact.com 对比时发现——参照站的期次索引显示当期头条标题，本站只有篇数。
  - 前端**已就绪**：`web/static/app.js:1707` 写的是 `day.title || "${day.count} 篇报道"`，缺的是后端 `src/airadar/web/routes/curated_archive.py:199` 只返回 `{"date", "count"}`。
  - **不能直接取「最新一篇」**：`_compute_daily_archive()` 按 `date(published_at)` 分组、条目按 `published_at DESC` 排；而日报正文按固定分类分节重排（模型发布/更新 → 产品发布/更新 → 行业动态…）。两边的"第一篇"不是同一条，rank 最小或发布时间最新都不保证等于用户点进去看到的首篇。
  - 做之前要先定契约：「索引标题 == 正文首篇」并加关系测试。否则错标题会是一个完全合法、看不出病的新闻标题——无症状错误。
  - 另需评估：同一日期可能含来自不同 run 的条目（跨 run 去重归档），「当期」的归属未定义；以及加子查询对该端点延迟的影响未量。

- [pending] X 推文媒体的作者图片描述（`alt_text`）已入库，但展示层一律 `alt=""`，读屏用户拿不到
  - 背景：2026-08-18 做 ADR-058（缩略图收缩包裹 + lightbox）时由生成后 review gate 的对抗审查发现，属**该改动之前就存在**的独立问题——本轮新增的 lightbox 只是又多了一个消费残缺契约的界面。
  - 读数：抓取层明确请求并保存 `alt_text`（`src/airadar/fetcher/x_api.py:19` 的 `X_MEDIA_FIELDS`、`x_api.py:107` 的可选字段透传），但展示投影 `src/airadar/presentation/media.py:100-109` 只产出 `{"type", "url"}` 两个键。CSR（`app.js` 的 `xMedia()`）、SSR（`web/templates/_prepaint_list.html`）与 lightbox 因此全部写死 `alt=""`，即把每张图声明为装饰内容。
  - 后果：作者提供了可访问描述时，读屏用户仍然什么都听不到。ADR-058 给 lightbox 加的 `aria-live` 计数器能播报"第 2 张，共 4 张"，但播报不了图是什么——那要靠描述穿过投影契约。
  - 未随 ADR-058 修的原因：`media_assets` 是**会被本进程之外的消费者读到、且字段名与值会被人读到**的数据契约，加字段要走 `/custom:review-schema`（见 user-CLAUDE.md「数据契约 / Schema 设计」），并处理三处消费端（CSR / SSR / lightbox）与旧收藏快照的兼容。属独立改动，不随 UI 借鉴那一轮做。
  - 若要修：投影补 `alt`（缺失时省略该键而不是写空串——空串与"作者没写描述"在下游分不开），三处消费端在有值时写进 `alt`、无值时保持 `alt=""`（无描述的图确实该按装饰处理）。
