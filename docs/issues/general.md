# General Issues

> Mutable. 项目级未分类问题——按 lifecycle 维护。

---

## [open] 22 个 X 源全部走 nitter.net 单 instance，无 fallback

- Type: improvement
- Priority: medium
- Discovered: 2026-05-29 调查"各 source 拉取节奏"时，看 pipeline log 发现 `FAIL openai_devs_x SSL UNEXPECTED_EOF`（21:15 那次 cron）
- Description: `data/sources.toml` 里 22 个 `kind="x"` 源的 URL 全部指向 `nitter.net/<handle>/rss`。nitter.net 主 instance 经常被 X rate limit、SSL 偶发失败、整 instance 也时不时挂。一旦它不可达，所有 22 个 X 源同时静默失败（pipeline 标 FAIL 后继续，无告警）。
- Notes:
  - 实际影响：X 源占 enabled 总数 65%（22/34），其中包含 T1 的 `openai_x`。挂一整段会让 aiplanet.live 的内容池显著变窄。
  - Mitigation 方向：在 fetcher 里加 nitter mirror fallback list（nitter 社区维护多个 mirror）；或者迁移到 RSSHub 自建 instance。两者成本都不低。
  - 当前 silent failure 路径符合 pipeline.sh "记录 FAIL 后继续" 的设计——这是"链路降级"的预期行为，但缺主动监控通知。

---

## [open] WeChat 链路有 2 小时主动监控盲区

- Type: improvement
- Priority: low
- Discovered: 2026-05-29 完成 WeWe RSS launchd 守护后的端到端节奏分析
- Description: WeWe 容器内 cron `7 */2 * * *` 决定了"WeChat 公众号→本地"链路最大刷新间隔是 2 小时。这个频率由 WeWe 上游 default 决定，调短的风险是被微信读书风控。当前没有"WeWe 长时间没拉到新文章"的主动告警——只能事后看 SQLite `articles.created_at` 的最大值确认。
- Notes:
  - 不打算调 WeWe cron（动了风险大于收益）。
  - 潜在改进：加一个 daily 检查脚本——如果 `MAX(articles.created_at)` 距今超 24 小时，发个本地通知。
  - 与 nitter 脆点同族：缺乏对 ingestion 链路的主动健康监控。

---

## [resolved] /api/v1/timeline search 不匹配来源名 / 作者，用户搜源名找不到该来源的文章

- Type: improvement
- Priority: medium
- Discovered: 2026-05-29 用户尝试在 aiplanet.live 搜索框输入"十字路口"想找十字路口Crossing 公众号的文章，结果搜出 X 上一篇碰巧 title 含"十字路口"的不相关推文（"18 年老粉与微软 GitHub 决裂..."），找不到真正想要的微信文章。
- Description: `/api/v1/timeline?q=<text>` 后端只对 items 表的 `title` 和 `content_text`（FTS5 索引）做匹配，不包含 sources 表的 `name`、`slug` 或文章 `author`。用户用源名 / 公众号名 / 作者名搜索时会撞到 title 里碰巧含相同字面的不相关文章，目标来源的真实条目反而不出现。等价的可观察案例：搜"十字路口"、"歸藏"、"OpenAI"（X 上提到 OpenAI 的推文 vs 来自 openai_blog 的文章）都有此问题。
- Notes:
  - Resolution: 2026-05-30 implemented free-text search over title/body/source name/author/Chinese title for both `/api/v1/timeline` and `/api/v1/curated`. Queries with 3+ characters use FTS; 1-2 character queries fall back to short-field LIKE over title/source name/author/Chinese title.
  - Verified on migrated production backup: `q=十字路口` returns `wx_crossing`; `q=歸藏` includes `wx_guizang`; `q=spankibalt` returns an author-only match.
  - Scope note: source slug is intentionally not searched. The user-facing search box remains free text over names/authors/titles/body, not a `?source=<slug>` filter.

---

## [open] prefilter 的 `--since 24h` 用 published_at 过滤，永久排除新接入源的历史导入文章

- Type: bug
- Priority: high
- Discovered: 2026-05-30 部署 timeline-search 到生产后，用户实测搜"十字路口"只返回 1 篇 wx_crossing（该源实有 10 篇），深挖发现 20 篇 wechat 文章里只 2 篇被 prefilter 处理
- Description: `pipeline.sh:62` 调 `prefilter --since 24h`，而 `prefilter/runner.py:87` 候选 query 是 `i.fetched_at >= cutoff AND i.published_at >= cutoff`（cutoff=now-24h）。新接入源（wechat）一次性导入的是历史存量——published_at 跨度大（实测 wx_crossing/wx_guizang 20 篇 published 跨 04-16~05-29），多数早于"24h 前"。这些文章一进库 published_at 就已超窗，prefilter 永不选中 → 永不进 prefilter→score→enrich→timeline/搜索。实测覆盖率 wechat 10%(2/20) vs feed 70%(5512/7832) vs x 81%(2103/2602)：feed/x 持续 fetch 新发布内容（published 新）故正常，wechat backfill 历史文章被系统性跳过。
- Notes:
  - 端到端后果：接入公众号后绝大多数文章（有完整 content_text 原文）从不出现在 timeline/搜索。直接违反 ux-contract HP-4「搜索来源名会返回该来源的内容」。
  - 影响面不限 wechat——任何"导入历史存量"的新源（backfill 场景）都被排除。
  - 立即缓解：对现有未 prefilter 的 item 跑 `prefilter --force` 或 `--item-id-file`（绕过 since），触发后续 score/enrich/curate。
  - 系统修方向：backfill 场景按 fetched_at 窗口而非 published_at（新 fetch 的历史文章应被处理一次），或新源首次导入开一次性全量 prefilter。

---

## [open] 搜索不做简繁归一化，搜简体匹配不到繁体源

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 部署 timeline-search 后验证，搜简体"归藏"返回 0 结果，而源名是繁体"歸藏的AI工具箱"
- Description: FTS5 trigram 与 <3 字 LIKE 兜底都是字面（codepoint）匹配，简体"归"与繁体"歸"是不同字符。实测 items_fts 含简体"归藏" 1 行 vs 繁体"歸藏" 194 行——用户用简体搜中文源（自然输入习惯）匹配不到繁体 source_name/title/title_zh。
- Notes:
  - Fix 方向：搜索时把 query 做简繁双向扩展（如 `MATCH '"归藏" OR "歸藏"'`），或索引+query 统一归一化（需引入 opencc 类简繁转换）。query 层扩展不动索引、相对有界，但要引依赖。
  - 用户 2026-05-30 明确要求先记录、之后再处理。

---

## [open] 搜来源名结果被同名/同词的高产源按时间淹没，无来源匹配优先排序

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 搜繁体"歸藏"返回 57 条但公众号 wx_guizang 仅 1 条（其余被同名 X 账号 op7418_x 淹没）
- Description: 歸藏本人有两个源——公众号 wx_guizang(歸藏的AI工具箱) + X 账号 op7418_x(source_name="歸藏")。搜"歸藏"两源都因 source_name 命中，但结果按 published_at 时间排序，op7418_x 推文多且新，把 wx_guizang 挤到尾部/外（实测 page1+page2 共 57 条，wx_guizang 仅 1 条）。timeline-search 是 free-text FTS，命中即按时间混排，无"来源名精确匹配优先"逻辑（plan R3 已预见）。
- Notes:
  - 叠加上面 prefilter backfill bug 后果更重：wx_guizang 9/10 篇本就因未 prefilter 缺席，仅存 1 篇又被淹没。
  - ux-contract HP-4 写了"搜源名返回该源内容"的承诺，但未定义结果排序（时间 vs 相关性 vs 来源优先）——建议在 ux-contract-issues 记一条 contract 定义缺失。
  - Fix 方向：source_name 精确/前缀匹配条目加 rank 提权；或搜源名时按"来源匹配 > 内容匹配"分层排序。

---

## [open] 缺少跨源的数据覆盖率 / 一致性监控（ingestion→prefilter→score→可见 全链路）

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 timeline-search 部署后，靠用户在产品上实测才撞见 wechat 源 prefilter 覆盖率仅 10%（vs feed 70% / x 81%）——无任何主动监控会自动报这种异常
- Description: 当前没有持续运行的健康检查去监控"每个 enabled 源的文章从 ingestion 到可见（prefilter→score→curate→可搜）各环节的覆盖率与一致性"。prefilter backfill bug 导致 wechat 18/20 篇有原文却从不可见，系统不主动报警，只能靠用户实测撞见。
- Notes:
  - 体系化「发现机制」：定期跑的检查，对比每源「库内文章数 vs 各 stage 已处理数 vs timeline/搜索可见数」，覆盖率显著低于同类源均值即告警。
  - 与 nitter 单点 + wewe 2h 盲区 issue 同族——都属「缺 ingestion 链路主动健康监控」，可一并设计统一的 pipeline 健康面板 / daily 检查脚本。
  - 长期事项（用户 2026-05-30 决定先记录；短期先做通用 verify 原则改进——让 plan 的 L2/L3 verify 要求端到端用户视角的覆盖率/一致性检查）。
