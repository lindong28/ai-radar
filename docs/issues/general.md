# General Issues

> Mutable. 项目级未分类问题——按 lifecycle 维护。

---

## [open] feed URL 无 scheme 白名单，`javascript:` 等非 http(s) 协议可进入卡片与热点榜的 `href`

- Type: security-hardening
- Priority: medium
- Discovered: 2026-08-03 AIHOT 视觉复刻 review-gate（独立 Codex reviewer，session 019fc69a）；用户裁决记录 issue、暂不修复。
- Description: `src/airadar/fetcher/rss.py` 经 `urls.py` canonicalizer 处理 `entry.link` 时没有 `http/https` 白名单——reviewer 用本地 feedparser 实验证实 `javascript:alert(1)` 这类无 netloc 的值会原样返回并入库。前端模板/JS 只做 HTML 属性转义（约束不了 URL scheme），因此该值会进入 timeline 卡片、`/hot` 页与首页热点模块的 `href`。触发前提：一个被收录的信源（41 个、由维护者管理）被放入恶意 link，风险低但非零。浏览器对 `javascript:` + `target="_blank"` 的最终执行行为未在真实浏览器验证（review sandbox 限制）。
- Fix 方向: 在 fetcher 的 URL canonicalizer 层加 http/https scheme 白名单（一次覆盖全部消费方），非白名单值置空或丢弃该 link；配套单测覆盖 `javascript:`、`data:`、相对路径与大小写变体。渲染层可另加纵深（非 http(s) 渲染为无链接文本）。

---

## [open] shared Cloudflare tunnel has unstable origin-to-edge throughput for larger dynamic responses

- Type: reliability
- Priority: medium
- Discovered: 2026-06-24 `/wechat` production latency incident
- Description: Public requests through the shared `ai-radar` cloudflared tunnel showed response-size-correlated latency while local origins were fast. Before mitigation, `https://aiplanet.live/wechat` transferred 171 KB in 10-23s while `http://127.0.0.1:8000/wechat` returned in ~0.01s; `sjtu.aiplanet.live` showed the same pattern on a different local origin. cloudflared logs also contained QUIC `no recent network activity` timeouts and stream cancellations. Restarting cloudflared and temporarily switching `protocol: http2` did not resolve the underlying tunnel throughput problem.
- Current mitigation: AI Radar now gzip-compresses large origin responses and keeps `/wechat` initial payload to 20 items, reducing `/wechat` from ~171 KB uncompressed to ~13 KB gzipped and bringing the public page under 3s in verification. This does not fix the shared tunnel for other unoptimized origins or larger pages.
- Follow-up: Investigate Cloudflare Tunnel/network health outside app code: compare from an external client not on the local host, review Cloudflare tunnel diagnostics/status, consider a separate tunnel/connector, and decide whether Cloudflare account/network changes are needed. Preserve the shared `sjtu.aiplanet.live` ingress rules during any tunnel config work.

---

## [resolved] interpret KB check-url hit drops metadata tags in regression test

- Type: bug
- Priority: low
- Discovered: 2026-06-12 open-source-readiness TASK-011/TASK-012 verification
- Resolution (2026-06-15): `src/airadar/interpret/runner.py` now resolves the no-LLM `--check-url` hit back to the summary-agent index by URL, current-user summary file, or any-user summary file, then preserves `metadata.tags` (and related metadata such as recommendation/model) when saving `wechat_interpretations.tags_json`. Regression coverage is in `tests/test_wechat_interpretation.py::test_interpret_runner_reuses_kb_check_url_hit_without_llm`.
- Description: `AI_RADAR_DB=/tmp/airadar-task011-012-nonplaywright.db uv run pytest --ignore=tests/playwright -q` failed only at `tests/test_wechat_interpretation.py::test_interpret_runner_reuses_kb_check_url_hit_without_llm`. The row was written with `tags_json=[]` while the test expected `["Agent"]` from the KB metadata path on a `run.sh --check-url` hit. `src/airadar/interpret/runner.py` and `tests/test_wechat_interpretation.py` were untouched in this TASK-011/TASK-012 diff, so this was not fixed in the install/source-loader scope.

---

## [resolved] interpret 回填无法并发——复用的 ai-assistant KB 写入器非并发安全

- Type: improvement
- Priority: low
- Discovered: 2026-06-02 wechat-article-interpretation plan 执行中（TASK-008 回填提速评估，supervisor 调查）
- Resolution (2026-06-15): 维持串行（wontfix-by-decision）。一次性历史回填已完成（2026-06-02 启动时近六成，cron `interpret` 增量串行跑完）；稳态下每轮只增量处理新增几篇，串行永远够用，并发改造零长期收益。复用的 ai-assistant KB 写入器（`index.json`+`vectors.npy` 整文件读-改-写、零拷贝复用硬约束）非并发安全，ai-radar 侧并发会引入 KB 损坏风险；真正的上游修复（ai-assistant 侧加锁/原子追加）不在本项目。将来若需大批量重处理，按原 Notes 的安全形态（summarize 并行写独立 batch、save 串行排队）单独评估，不为日常运行预做。
- Description: `interpret` stage 的回填（`./run.sh interpret --backfill`）逐篇串行处理（`src/airadar/interpret/runner.py` 的 `for row in rows:`）——163 篇 × DeepSeek 总结约 60–90s/篇 ≈ 2–3 小时。每篇耗时主要在 summarize（DeepSeek）调用，该步骤独立、本身可并发；但每篇的 **save 回 KB** 步骤（`ai-assistant run.sh --save-from-batch` → `embedding --add`）写两个**全局共享、顺序必须一致**的文件：`index.json`（元数据事实来源）与 `vectors.npy`（约 1.8MB numpy embedding 数组，顺序与 index.json 严格对应），且 `--add` 是**整文件读-改-写**。并发 save 会互相覆盖 → index 与 vectors 错位 / 丢更新 / 损坏。由于对 ai-assistant 是**零拷贝复用**（本特性硬约束，不 fork 不改其代码），其 KB 写入器不是并发安全的，因此整条回填只能串行。
- Notes:
  - 影响面有限：这是**一次性**回填（plan D9 已把它移出热循环）；稳态下 cron `interpret` 每轮只增量处理新增的几篇，串行永远够用——并发改造对日常运行零长期收益，只对将来"大批量重处理"场景有意义。
  - 安全的提速形态（若未来确需）：summarize 阶段开 N 个 worker 并行（各写独立 batch 目录，无共享态），save 阶段串行排队（加锁 / 单消费者），可把大批回填 wall-clock 压到约 summarize_total/N + save_total。
  - 真正的上游修复在 **ai-assistant 侧**：让 KB 写入器并发安全（`index.json` + `vectors.npy` 的加锁 / 原子追加，且保持二者顺序一致），之后 ai-radar 的 runner 才能安全并发。在那之前，ai-radar 侧任何并发都会绕过这个不变量、引入 KB 损坏风险。
  - 用户 2026-06-02 知情决策：本次回填保持**串行跑完**（已近六成、健康、剩约 1h），不为这次一次性操作做并发改造。

---

## [resolved] `/admin` origin local-bypass 依赖 cloudflared 暴露公网 `client.host`

- Type: security_note
- Priority: high
- Discovered: 2026-06-02 monitoring-alerting supervisor review
- Resolution (2026-06-15): `/admin` 和 `/api/v1/admin/*` 的 loopback bypass 现仅在 `AI_RADAR_ADMIN_ALLOW_LOCAL` 为 `1/true/yes` 时启用，生产默认关闭；Cloudflare Access header 仍可放行运维入口。`tests/test_admin_routes.py` 覆盖无 env 的 loopback 403、显式 dev override 200、以及带 `Cf-Access-Jwt-Assertion` 的 200。剩余 JWT 验签 / origin token 属独立增强，记录在 `docs/operations/monitoring-alerting.md`。
- Description: `/admin` 与 `/api/v1/admin/*` 的 origin guard 允许 `127.0.0.1` / `::1` / `localhost` 本地 bypass。当前不是活跃漏洞：公网无凭证 `curl` 已验证为 403，TASK-001 探针也观察到 tunnel 请求在 FastAPI/access log 中呈现真实公网 IP（非 loopback）。但该安全性依赖 cloudflared 当前 forwarded/client.host 行为；如果未来 cloudflared 改为通过本地 socket 转发，并让 FastAPI 看到 `client.host=127.0.0.1`，公网请求会被当成本地请求放行。

---

## [resolved] 22 个 X 源全部走 nitter.net 单 instance，无 fallback

- Type: improvement
- Priority: medium
- Discovered: 2026-05-29 调查"各 source 拉取节奏"时，看 pipeline log 发现 `FAIL openai_devs_x SSL UNEXPECTED_EOF`（21:15 那次 cron）
- Resolution (2026-06-15): 前提已失效。`data/sources.toml` 当前仅 1 个 `kind="x"` 源（simon willison，走 Mastodon/fedi feed `fedi.simonwillison.net/@simon.rss`，非 nitter），nitter.net 在 sources.toml 中已无任何引用——原"22 个 X 源全部走 nitter.net 单 instance"的整批同点失败模式不复存在（X 源经 prune/迁移，存活者已转 fedi）。残留 nitter 引用仅 `src/airadar/fetcher/urls.py`（X_STATUS_HOSTS 历史归一化）+ alert 注释，非活跃摄取路径。余下唯一 X 源走 fedi，单源脆弱性不构成本 issue 的"整批静默失败"量级。
- Description: `data/sources.toml` 里 22 个 `kind="x"` 源的 URL 全部指向 `nitter.net/<handle>/rss`。nitter.net 主 instance 经常被 X rate limit、SSL 偶发失败、整 instance 也时不时挂。一旦它不可达，所有 22 个 X 源同时静默失败（pipeline 标 FAIL 后继续，无告警）。
- Notes:
  - 实际影响：X 源占 enabled 总数 65%（22/34），其中包含 T1 的 `openai_x`。挂一整段会让公开站点的内容池显著变窄。
  - Mitigation 方向：在 fetcher 里加 nitter mirror fallback list（nitter 社区维护多个 mirror）；或者迁移到 RSSHub 自建 instance。两者成本都不低。
  - 当前 silent failure 路径符合 pipeline.sh "记录 FAIL 后继续" 的设计——这是"链路降级"的预期行为，但缺主动监控通知。

---

## [resolved] WeChat 链路有 2 小时主动监控盲区

- Type: improvement
- Priority: low
- Discovered: 2026-05-29 完成 WeWe RSS launchd 守护后的端到端节奏分析
- Resolution (2026-06-15): 本 issue 的前提（WeWe 容器内 cron `7 */2 * * *`、扫码 token 失效链路）随 WeChat 摄取迁移到托管 Mp2RSS 合集 feed（`wx_mp2rss`，由常规 pipeline 15min cron 消费上游维护登录态）整体退役而失效——WeWe 容器与扫码登录已从服务层移除（见 `docs/operations/wechat-ingestion.md` §接入方案）。原 2h-WeWe-cron 盲点不复存在。泛化的"ingestion 滞后无主动监控"关切（若 Mp2RSS feed 停更如何发现）属另一机制，归入 [[缺少跨源覆盖率监控]] 同族，由 monitoring-alerting 体系覆盖，不再单独保留本条。
- Description: WeWe 容器内 cron `7 */2 * * *` 决定了"WeChat 公众号→本地"链路最大刷新间隔是 2 小时。这个频率由 WeWe 上游 default 决定，调短的风险是被微信读书风控。当前没有"WeWe 长时间没拉到新文章"的主动告警——只能事后看 SQLite `articles.created_at` 的最大值确认。
- Notes:
  - 不打算调 WeWe cron（动了风险大于收益）。
  - 潜在改进：加一个 daily 检查脚本——如果 `MAX(articles.created_at)` 距今超 24 小时，发个本地通知。
  - 与 nitter 脆点同族：缺乏对 ingestion 链路的主动健康监控。
  - **实证复发 (2026-06-01)**：WeRead token 于 ~2026-05-29 14:07 失效，wewe 每 2h cron 静默报 `Error: 暂无可用读书账号！`，**3 天无人察觉**（正是本盲区），歸藏+十字路口 sync_time 一起冻结、ai-radar 侧文章停在 05-28。**关键坑**：dash 里把账号「启用」(status 0→1) 看似可恢复，但 token 实际已过期——一旦触发同步（手动 `GET /feeds/<id>.rss?update=true` 或 2h cron），WeRead 返回 `401 Token 失效（WeReadError401, -2041）`，wewe 立即「账号登录失效，已禁用」把 status 打回 0。**真正恢复必须重扫二维码**（`http://localhost:4000/dash/accounts`，需用户微信扫码），仅 toggle 状态无效。→ 监控应同时覆盖"token 失效/账号被自动禁用"，不只是"长时间没新文章"。

---

## [resolved] /api/v1/timeline search 不匹配来源名 / 作者，用户搜源名找不到该来源的文章

- Type: improvement
- Priority: medium
- Discovered: 2026-05-29 用户尝试在公开站点搜索框输入"十字路口"想找十字路口Crossing 公众号的文章，结果搜出 X 上一篇碰巧 title 含"十字路口"的不相关推文（"18 年老粉与微软 GitHub 决裂..."），找不到真正想要的微信文章。
- Description: `/api/v1/timeline?q=<text>` 后端只对 items 表的 `title` 和 `content_text`（FTS5 索引）做匹配，不包含 sources 表的 `name`、`slug` 或文章 `author`。用户用源名 / 公众号名 / 作者名搜索时会撞到 title 里碰巧含相同字面的不相关文章，目标来源的真实条目反而不出现。等价的可观察案例：搜"十字路口"、"歸藏"、"OpenAI"（X 上提到 OpenAI 的推文 vs 来自 openai_blog 的文章）都有此问题。
- Notes:
  - Resolution: 2026-05-30 implemented free-text search over title/body/source name/author/Chinese title for both `/api/v1/timeline` and `/api/v1/curated`. Queries with 3+ characters use FTS; 1-2 character queries fall back to short-field LIKE over title/source name/author/Chinese title.
  - Verified on migrated production backup: `q=十字路口` returns `wx_crossing`; `q=歸藏` includes `wx_guizang`; `q=spankibalt` returns an author-only match.
  - Scope note: source slug is intentionally not searched. The user-facing search box remains free text over names/authors/titles/body, not a `?source=<slug>` filter.

---

## [resolved] prefilter 的 `--since 24h` 用 published_at 过滤，永久排除新接入源的历史导入文章

- Type: bug
- Priority: high
- Discovered: 2026-05-30 部署 timeline-search 到生产后，用户实测搜"十字路口"只返回 1 篇 wx_crossing（该源实有 10 篇），深挖发现 20 篇 wechat 文章里只 2 篇被 prefilter 处理
- Description: `pipeline.sh:62` 调 `prefilter --since 24h`，而 `prefilter/runner.py:87` 候选 query 是 `i.fetched_at >= cutoff AND i.published_at >= cutoff`（cutoff=now-24h）。新接入源（wechat）一次性导入的是历史存量——published_at 跨度大（实测 wx_crossing/wx_guizang 20 篇 published 跨 04-16~05-29），多数早于"24h 前"。这些文章一进库 published_at 就已超窗，prefilter 永不选中 → 永不进 prefilter→score→enrich→timeline/搜索。实测覆盖率 wechat 10%(2/20) vs feed 70%(5512/7832) vs x 81%(2103/2602)：feed/x 持续 fetch 新发布内容（published 新）故正常，wechat backfill 历史文章被系统性跳过。
- Notes:
  - 端到端后果：接入公众号后绝大多数文章（有完整 content_text 原文）从不出现在 timeline/搜索。直接违反 ux-contract HP-4「搜索来源名会返回该来源的内容」。
  - 影响面不限 wechat——任何"导入历史存量"的新源（backfill 场景）都被排除。
  - 立即缓解：对现有未 prefilter 的 item 跑 `prefilter --force` 或 `--item-id-file`（绕过 since），触发后续 score/enrich/curate。
  - 系统修方向：backfill 场景按 fetched_at 窗口而非 published_at（新 fetch 的历史文章应被处理一次），或新源首次导入开一次性全量 prefilter。
  - 核实补充 (2026-05-31)：同一 `published_at >= cutoff` 过滤也存在于 `scorer/runner.py:91-92`，故仅修 prefilter 不够——backfill item 即便过了 prefilter 仍会卡在 score 阶段。`enrich/runner.py:96` 只用 `fetched_at`（无此 bug，是正确范式，可作修复参照）。当前生产数据：wx_crossing 10 篇仅 1 prefilter、wx_guizang 10 篇仅 1，wechat 整体 2/20（10%）vs feed 72% / x 81%；18 篇未处理文章均有完整正文（2.7k–16k 字），是真实可见性损失。
  - Resolution (2026-06-01): `prefilter/runner.py` 与 `scorer/runner.py` 候选窗口改为 `fetched_at`-only，并对 18 篇 WeChat 存量 id 执行 prefilter→score→enrich→curate backfill。L2 V1 结果：`wx_crossing|10|10|10|9|1|0`、`wx_guizang|10|10|10|8|2|0`（total|prefiltered|ai_related|visible|score_below_6_5|unexplained_unprefiltered），零 unexplained 缺席；不可见项均由最新 score < 6.5 解释。

---

## [resolved] 搜索不做简繁归一化，搜简体匹配不到繁体源

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 部署 timeline-search 后验证，搜简体"归藏"返回 0 结果，而源名是繁体"歸藏的AI工具箱"
- Description: FTS5 trigram 与 <3 字 LIKE 兜底都是字面（codepoint）匹配，简体"归"与繁体"歸"是不同字符。实测 items_fts 含简体"归藏" 1 行 vs 繁体"歸藏" 194 行——用户用简体搜中文源（自然输入习惯）匹配不到繁体 source_name/title/title_zh。
- Notes:
  - Fix 方向：搜索时把 query 做简繁双向扩展（如 `MATCH '"归藏" OR "歸藏"'`），或索引+query 统一归一化（需引入 opencc 类简繁转换）。query 层扩展不动索引、相对有界，但要引依赖。
  - 用户 2026-05-30 明确要求先记录、之后再处理。
  - Resolution (2026-06-01): 引入 pure-Python `opencc-python-reimplemented`，query 层生成原文+s2t+t2s 去重变体；FTS5 使用 phrase OR，短 query LIKE 与 source-match ranking 共用同一变体集合。L2 V4：`q=归藏&limit=50` 返回 `wx_guizang` 8 条，等于动态可见 expected=8，位置 `1,3,5,7,9,11,13,15`。

---

## [resolved] 搜来源名结果被同名/同词的高产源按时间淹没，无来源匹配优先排序

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 搜繁体"歸藏"返回 57 条但公众号 wx_guizang 仅 1 条（其余被同名 X 账号 op7418_x 淹没）
- Description: 歸藏本人有两个源——公众号 wx_guizang(歸藏的AI工具箱) + X 账号 op7418_x(source_name="歸藏")。搜"歸藏"两源都因 source_name 命中，但结果按 published_at 时间排序，op7418_x 推文多且新，把 wx_guizang 挤到尾部/外（实测 page1+page2 共 57 条，wx_guizang 仅 1 条）。timeline-search 是 free-text FTS，命中即按时间混排，无"来源名精确匹配优先"逻辑（plan R3 已预见）。
- Notes:
  - 叠加上面 prefilter backfill bug 后果更重：wx_guizang 9/10 篇本就因未 prefilter 缺席，仅存 1 篇又被淹没。
  - ux-contract HP-4 写了"搜源名返回该源内容"的承诺，但未定义结果排序（时间 vs 相关性 vs 来源优先）——建议在 ux-contract-issues 记一条 contract 定义缺失。
  - Fix 方向：source_name 精确/前缀匹配条目加 rank 提权；或搜源名时按"来源匹配 > 内容匹配"分层排序。
  - Resolution (2026-06-01): `/api/v1/timeline` 与 `/api/v1/curated` 搜索态增加 `is_source_match`（source name/author LIKE，复用简繁变体）与 `ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY published_at DESC, fetched_at DESC, id DESC)` 来源轮转；无 q 时保留原时间/日期排序。L2 V3：`q=歸藏&limit=50` page1 同时包含 `op7418_x` 与 `wx_guizang`，`wx_guizang` 首条位置 1，之后位置 `3,5,7,9,11,13,15`。

---

## [resolved] 缺少跨源的数据覆盖率 / 一致性监控（ingestion→prefilter→score→可见 全链路）

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 timeline-search 部署后，靠用户在产品上实测才撞见 wechat 源 prefilter 覆盖率仅 10%（vs feed 70% / x 81%）——无任何主动监控会自动报这种异常
- Resolution (2026-06-15): 归并（fold-into-plan）。跨源覆盖率/一致性监控属"ingestion 链路主动健康监控"族（同 [[WeChat 2h 盲区]] 已随 Mp2RSS 迁移失效、[[A3 healthz 维度]] 已加主动探测），其体系化载体是已定稿的 monitoring-alerting plan（`plans/20260601-monitoring-alerting/`）。该 plan 的后续 build-out 应纳入"每源 库内文章数 vs 各 stage 已处理数 vs timeline/搜索可见数"的覆盖率检查与异常告警；不在 general.md 重复跟踪，避免与 plan 双轨。短期通用 verify 原则改进（L2/L3 端到端覆盖率/一致性检查）已落地。
- Description: 当前没有持续运行的健康检查去监控"每个 enabled 源的文章从 ingestion 到可见（prefilter→score→curate→可搜）各环节的覆盖率与一致性"。prefilter backfill bug 导致 wechat 18/20 篇有原文却从不可见，系统不主动报警，只能靠用户实测撞见。
- Notes:
  - 体系化「发现机制」：定期跑的检查，对比每源「库内文章数 vs 各 stage 已处理数 vs timeline/搜索可见数」，覆盖率显著低于同类源均值即告警。
  - 与 nitter 单点 + wewe 2h 盲区 issue 同族——都属「缺 ingestion 链路主动健康监控」，可一并设计统一的 pipeline 健康面板 / daily 检查脚本。
  - 长期事项（用户 2026-05-30 决定先记录；短期先做通用 verify 原则改进——让 plan 的 L2/L3 verify 要求端到端用户视角的覆盖率/一致性检查）。

---

## [resolved] `alert` 服务每 5 分钟运行但飞书 webhook 未配置，告警无法送达

- Resolution: 2026-06-06 改为直接读 `~/.claude/.env` 已有的 `FEISHU_GENERAL_ALERT_WEBHOOK`（与 watchdog 共用，单一来源）；`alerts.py` / `deploy/lib/services.sh` / 测试 / 文档不再引用 ai-radar 专属的 `AI_RADAR_FEISHU_WEBHOOK`。`./uninstall.sh alert && ./install.sh alert` 后 `launchctl print` 确认环境注入，走 `send_feishu_message` 实发飞书返回 `StatusCode:0 success`，A1-A4 全绿 `sent=0`。
- Type: bug
- Priority: medium
- Discovered: 2026-06-06 服务级审计——`live.aiplanet.ai-radar.alert` launchd 每 5 分钟跑 `admin alert-check` 并算出 A1-A4（日志显示规则在跑），但 `AI_RADAR_FEISHU_WEBHOOK` 在进程 env、项目 `.env`、`~/.claude/.env` 三处均未设置。
- Description: webhook 缺失时 alert 降级为 dry-run（sent=0）——逐次计算告警规则却无法投递。后果：serve/pipeline/摄取真异常时**不会有任何告警发出**，监控形同虚设，且每 5 分钟仍消耗一次计算。`~/.claude/.env` 里已有 `FEISHU_GENERAL_ALERT_WEBHOOK`（watchdog 用），但 ai-radar 专属的 `AI_RADAR_FEISHU_WEBHOOK` 从未配置。
- Notes:
  - 两种处置（用户决定）：(a) 配置 `AI_RADAR_FEISHU_WEBHOOK`（专属 webhook，或复用 `FEISHU_GENERAL_ALERT_WEBHOOK`）让 alert 真正能送达；(b) 暂不需要 ai-radar 告警则 `./uninstall.sh alert` 停掉每 5 分钟 inert 运行，想做时再 install。
  - 配置后用 `./run.sh admin alert-check` 验证：有触发条件时 sent 计数 > 0。

---

## [resolved] install.sh 的 docker 就绪检查无法从 "OrbStack 已开但 VM 停" 恢复

- Resolution: 2026-06-06 `wewe` 从服务层移除（WeChat 摄取迁移 Mp2RSS），`deploy/lib/services.sh` 的 `ensure_docker_daemon` 整个 docker 预检随之删除，`install.sh` 不再触碰 Docker——本 issue 的代码路径已不存在。
- Type: improvement
- Priority: low
- Discovered: 2026-06-01 `/custom:supervise` 委派 codex 跑 `./install.sh wewe` 时，OrbStack GUI 进程在跑但其 VM 因 idle 被自动关机，`docker info` 不可达
- Description: `deploy/lib/services.sh` 的 `ensure_docker_daemon` 只做 `open -a OrbStack` + 轮询 `docker info`。但 OrbStack 可能"app 在跑、VM 已 idle 关机"——此时 `open -a` 不会重启 VM，docker 始终不可达，`./install.sh wewe` 会按设计中止。Codex 手动 `orbctl start` 才恢复。
- Notes:
  - Fix 方向：`ensure_docker_daemon` 在 `open -a OrbStack` 后、轮询前，若 `command -v orbctl` 存在则补一句 `orbctl start`（幂等，VM 已跑时无副作用）。
  - 影响面：任何在 OrbStack VM 处于 idle-stopped 时跑 `./install.sh wewe` 的人/agent 都会撞上，需手动 orbctl start。

---

## [resolved] pipeline stage `--since` 解析会把 ISO `T...Z` 时间戳 lower-case 后解析失败

- Type: bug
- Priority: low
- Discovered: 2026-06-01 全量 WeChat RSS backfill 时，为避免 `score --since 24h` churn 非 WeChat backlog，尝试运行 `score --since 2026-06-01T10:43:04Z`。
- Resolution (2026-06-15): `prefilter` / `scorer` / `enrich` runner 的 `_parse_since` 只对最后一位单位后缀做大小写归一，不再 lower-case 整个输入；标准 `2026-06-01T10:43:04Z` 与显式 offset ISO 都可解析为 UTC。Regression: `tests/test_runner_since_parsing.py`.
- Description: `scorer/runner.py::_parse_since` 先对整个输入执行 `value.strip().lower()`，之后只替换大写 `"Z"`。因此标准 UTC ISO 字符串 `2026-06-01T10:43:04Z` 会变成 `2026-06-01t10:43:04z`，`datetime.fromisoformat(...)` 抛 `ValueError: Invalid isoformat string`。同样的 `_parse_since` 写法也存在于 prefilter/enrich runner，显式 ISO `T...Z` 窗口都可能中招。

---

## [resolved] OrbStack VM idle 自动关机 → wewe 容器随之停 → WeChat 摄取频繁中断

- Resolution: 2026-06-06 WeChat 摄取迁移到托管 Mp2RSS feed，`wewe` 桥 + 本地 docker 容器整体退役（服务层移除），不再依赖 OrbStack VM——本失败模式消失。同日还移除了 OrbStack 开机自启（登录项）+ 退出 helper，因为已无任何服务需要 Docker。
- Type: bug
- Priority: medium
- Discovered: 2026-06-01 一个 session 内观察到 3 次：每次起好 wewe（`./install.sh wewe` / orbctl start）后几十分钟内 OrbStack 又把 VM idle 关机，`ai-radar-wewe-rss` 容器随之停，`127.0.0.1:4000` 不可达。
- Description: wewe（WeChat 摄取桥）跑在 OrbStack 的 docker VM 里。OrbStack 默认会在 VM idle 一段时间后自动关机；VM 一停容器就停，wewe launchd 的 KeepAlive 也救不回来（docker daemon 不可达，`docker compose up` 直接失败）。直接后果：**WeChat 公众号→本地的摄取并非持续**——OrbStack 一 idle 关机，wechat 链路就断，直到下次有人/agent 手动 `orbctl start`。这是用户问"微信文章在持续摄取吗"的真实答案：不持续。
- Notes:
  - 与 [install.sh docker 就绪检查] 和 [WeChat 2h 盲区] 同族，但根因不同：那两条是"起不来/没告警"，这条是"起来后被 OrbStack idle 关机反复打死"。
  - Fix 方向（需用户拍资源取舍）：(a) 关掉 OrbStack 的 VM idle 自动关机（VM 常驻，wewe 稳定，但常占资源/电）；(b) 加一个 launchd/cron 周期 `orbctl start`（幂等）兜底，VM 被关后很快拉回；(c) 接受间歇 + 加"wewe 长时间不可达"告警。
  - 临时：2026-06-01 已 `orbctl start` 恢复，wewe :4000=200。
  - Action (2026-06-01, 用户选"关 idle 自动关机")：`orb config set power.pause_in_sleep false` + `orb stop/start` 应用；wewe 已恢复。**但有效性未验证**——VM 当时是 "Stopped"（非 paused），pause_in_sleep 是否就是根因尚不确定，只能等下个 idle/sleep 周期观察是否还停。若仍复发：根因另在，需上 fallback (b)/(a)——周期 `orbctl start` keep-alive 或 caffeinate/pmset 阻止 Mac 睡眠（Mac 整机睡时 VM 无论如何跑不了）。

---

## [resolved] A4 `daily_inserted_floor` 对"当日累积计数器"全天比较，跨日初假阳

- Type: bug
- Priority: low
- Discovered: 2026-06-07 复盘 alert-check 历史日志（A2 减噪改动时）发现冷启动轮 A4 firing「今日 items 增量 10 < 127」，下一轮即「3064」恢复
- Resolution (2026-06-15): A4 now compares `items_today` against `daily_inserted_floor * minutes_elapsed_today / 1440` (clamped to the day), and `collect_alert_signals` supplies Shanghai-local minutes elapsed. The calibrated full-day floor remains the scaling basis while early-day false positives are avoided. Regression coverage is in `tests/test_admin_alerts.py::test_a4_daily_insert_floor_is_time_proportional`.
- Description: A4 触发条件之一是 `signals.items_today < daily_inserted_floor`（floor=127，`thresholds.py` a4）。`items_today` 是"自当日 00:00 起的累积插入数"，但被**全天任意时刻**与一个**全天总量底线**比较。每天午夜后到累积满 127 篇之前，`items_today` 必然小于 floor → A4 在每日清晨假阳。历史日志中观察到一次（items=10，紧接 3064），launchd 因机器休眠未连续运行掩盖了发生频率。

---

## [resolved] A3 healthz 维度缺主动探测——只能靠 5xx 率间接发现站点异常

- Type: improvement
- Priority: medium
- Discovered: 2026-06-07 告警减噪改动中发现 `collect_alert_signals` 把 `health_failures=0` 写死，A3 的 healthz 分支永不触发（死信号）
- Resolution (2026-06-15): `run_alert_state_machine` now actively probes local `/api/v1/healthz` every alert-check run, persists consecutive failures under `healthz_probe` in `data/alert-state.json`, and feeds that count into A3. A3 fires when either user-side 5xx rate exceeds threshold or healthz consecutive failures reach the configured floor. Regression coverage is in `tests/test_admin_alerts.py::test_a3_active_healthz_probe_persists_failures_and_recovers`.
- Description: A3 原设计含两维度——用户侧 5xx 率 + "healthz 连续失败 N 次"。但 `health_failures` 在采集层硬编码为 0，`>=2` 永远不成立，healthz 维度是死代码，且消息里"healthz 连续失败 0 次"制造"信号活着"的错觉。2026-06-07 已**删除**该死分支，A3 现仅靠 access log 的 5xx 率触发。遗留盲区：若站点以"不产生 5xx"的方式挂掉（如 tunnel 断 → 请求到不了 origin → 无 5xx、PV=0 → 5xx 率=0），A3 完全沉默。

---

## [resolved] launchd serve plist 与 test_service_contract 期望漂移（access log 路径）

- Type: bug
- Priority: low
- Discovered: 2026-06-08 daily 历史数据修复 session 跑全量测试时（已验 pre-task `HEAD` 基线同样 fail = 既有，与本次 curated 改动无关）
- Description: `tests/test_service_contract.py` 期望 `deploy/launchd/ai-radar-serve.plist` 的 `StandardOutPath` 指向 `<repo>/logs/serve-access.log`，但实际 plist 指向 `/tmp/ai-radar-serve.log` + `StandardErrorPath=/tmp/ai-radar-serve.err`。test 与 plist 哪个为准需确认：若访问日志应持久化到 `logs/`，改 plist；否则改 test。Fix 方向：对齐二者，确认生产 serve 的 access log 落盘位置。
- Resolution (2026-06-15): 已对齐——plist 现指向 `<repo>/logs/serve-access.log` / `logs/serve-access.err.log`（`deploy/launchd/ai-radar-serve.plist:15-16`），`tests/test_service_contract.py` 全部 5 个测试通过。本 issue 在 resolve-issues 核实时确认已修，回写 closed。

## [resolved] test_phase2 wechat 卡片点击测试数据依赖 flaky（.timeline-card 不可见）

- Type: bug
- Priority: low
- Discovered: 2026-06-08 daily 历史数据修复 session（已验 pre-task `HEAD` 基线同样 fail = 既有，与本次 curated 改动无关）
- Description: `tests/playwright/test_phase2.py::test_wechat_card_body_click_opens_detail_and_back_preserves_page` 等待 `.timeline-card` 可见超时——依赖本地 DB 内有 wechat 数据，数据缺失即 fail。与 general.md 既有的 test_phase2 数据依赖 flaky 同族。Fix 方向：测试自带 seed 数据或显式 skip 无数据场景，去除对本地 DB 现状的隐式依赖。
- Resolution (2026-06-15): `tests/playwright/test_phase2.py` 加 `_require_wechat_cards_on_page(page, base_url, page_number)` 守卫——测试先查 `/api/v1/wechat?page=N&limit=50`，若无可见 wechat 数据则 `pytest.skip(...)` 给出明确原因，不再隐式依赖本地 DB 现状（断言本身未削弱）。ruff + py_compile 通过。注：Playwright fixture 复用项目 `data/radar.db`（生产 serve 占锁），完整运行态验证需在无 serve 占锁的隔离环境进行。

## [resolved] test_no_write_endpoints 写共享生产 DB，serve/cron 写入期 transient "database is locked"

- Type: bug
- Priority: low
- Discovered: 2026-06-15 resolve-issues 全量验证时——`uv run pytest --ignore=tests/playwright -q` 偶发 `tests/test_no_write_endpoints.py::test_business_routes_are_read_only` 报 `sqlite3.OperationalError: database is locked`（`src/airadar/db.py:56`）；同轮重跑或换 `AI_RADAR_DB=/tmp/...` 即过，故为环境争用而非代码回归。
- Resolution (2026-06-15): The test now migrates a temp DB, sets `AI_RADAR_DB` to that path, and imports/creates the web app only after that isolation is in place, so the test no longer needs the shared production SQLite file. Verified with `.venv/bin/python -m pytest tests/test_no_write_endpoints.py`.
- Description: 该测试通过发写请求验证"业务路由只读"，但运行在共享的生产 `data/radar.db` 上——生产 serve（127.0.0.1:8000，aiplanet.live）+ 15min `score --since 24h` cron 写入时会拿到写锁，测试的写探针在短 busy_timeout 内撞锁即 fail。同族于 [[test_phase2 数据依赖 flaky]]：测试未隔离 DB，依赖运行环境。Fix 方向：测试改用隔离 DB（`AI_RADAR_DB` 指向 tmp 副本或 fixture 临时库），不写共享生产库。

---

## [open] codex (codeagent-wrapper) 无法中断前台长跑进程，只能 kill by PID

- Type: improvement
- Priority: low
- Occurrences:
  - 2026-06-29 11:48 | session `019f115e-a010-7f21-9631-935634590a3d` | backend `codex` | 在 interpret ark+计量任务的真实端到端验证中，codex 两次需要中断自己拉起的前台长跑进程（卡住的 interpret `--check-url` preflight、本地冒充 ark 的 429 server），尝试发送中断时报 `write_stdin failed: stdin is closed for this session; rerun exec_command with tty=true to keep stdin open`，无法交互式 Ctrl-C。codex 改用 `ps` 定位 PID + `kill <pid>` 绕过，任务未受阻但多花了诊断/重试步骤。
- Description: 在 codeagent-wrapper 下跑的 codex，其 exec_command 会话 stdin 已关闭，无法对前台长时间运行的子进程发送中断信号（需 tty=true 才保持 stdin）。当 wrapped agent 自己启动需要手动停止的前台进程（本地测试服务器、卡住的命令）时，唯一可靠手段是 `kill by PID`。Fix 方向：要么 wrapper 暴露一个保持 stdin/tty 的执行模式，要么在 agent 指引里固化"前台长跑进程一律 background + kill by PID 收尾"的约定，避免每次重新踩 stdin-closed。属工具/config 缺口，非产品代码问题。

---

## [open] wrapped agent 为让全量 pytest 绿而扩大 scope 修 pre-existing failure

- Type: improvement
- Priority: medium
- Occurrences:
  - 2026-07-06 20:39 | session `019f3766-5a56-7252-a8b5-64f3f7661586` | backend `codex` | A2 enrich P95 误报全根因修复任务中，全量 pytest 出现 4 个 Playwright daily-date failure（pre-existing，根因是 live radar.db 有未来日期 curated item `2026-07-07`）。codex 未识别为 pre-existing 并报告，而是扩大 scope 改了 `web/static/app.js`（daily-date routing 重构 26 行）+ `src/airadar/web/routes/curated.py`（后端 daily API）去"修"这 4 个 failure。supervisor resume 介入后全部 revert。
- Description: wrapped agent 跑全量 pytest 时，遇到 failure 默认认为"应该修到绿"，即使该 failure 与 task scope 无关（不同代码路径/模块）。结果为"全绿"扩大 scope，引入无关改动（这次还连带把 runtime context dump 写进 `AGENTS.md`，见 [[codex 把 runtime context dump 写入项目指令文件]]）。正确做法：识别 failure 是否在 task touched 的代码路径上、是否 pre-existing（查 git blame / fixture / 数据依赖），pre-existing 则在 summary 报告"已知、根因、与 task 无关"，不修、不扩大 scope。Fix 方向：spawn-prompt 里预声明已知 baseline failure（"no NEW failures"语义），或要求 agent 跑全量前先记录 baseline，新 failure 才算 regression。
- Notes: 相关 success criterion 应表述为"no NEW failures vs baseline"而非"全量 pytest 绿"，避免 agent 把 pre-existing failure 当自己责任。

---

## [open] codex 把 runtime context dump 写入项目指令文件（AGENTS.md）

- Type: bug
- Priority: medium
- Occurrences:
  - 2026-07-06 ~20:30 | session `019f3766-5a56-7252-a8b5-64f3f7661586` (phase-1) | backend `codex` | 同上任务中，codex 把一段 `<claude-mem-context>` 运行时 context dump（claude-mem recent-context block + observations 列表，~86 行）粘进了 `AGENTS.md` 项目指令文件。supervisor 发现后指示 revert，codex 用 `git checkout -- AGENTS.md`——**连带丢弃了用户会话前未 commit 的 AGENTS.md 改动（unstaged，不可恢复，supervisor 失误：应精细剥离污染段而非整个 checkout）**。
  - 2026-07-06 ~21:15 | session `019f3766-5a56-7252-a8b5-64f3f7661586` (phase-2 resume) | backend `codex` | phase-2 resume 时**再次**把 `<claude-mem-context>` dump（~86 行，2 处）粘进 AGENTS.md——supervisor `git checkout` 清理（用户改动 phase-1 已丢，无新损失）。**确认系统性问题：同一 session 两次复发**，证明 spawn-prompt 预禁触指令文件是必要的前置约束。
- Description: codex backend 的 context injection（claude-mem 提供的 recent context）被当作"项目内容"写入版本控制的指令文件。runtime context 是给 agent 读的、非项目持久内容，不该写入 `AGENTS.md`/`CLAUDE.md`。Fix 方向：(1) supervise spawn-prompt 显式提醒"不要修改 AGENTS.md/CLAUDE.md 等项目指令文件，除非 task 明确要求"；(2) supervisor 复核 git diff 时检查指令文件是否被污染；(3) revert 污染时用精细剥离（去掉污染段保留其余），而非 `git checkout` 整个文件——后者会丢失用户预存的未 commit 改动。同族于 [[wrapped agent 为让全量 pytest 绿而扩大 scope]]（都是 agent 在验证压力下的越界行为）。

---

## [open] SSR prepaint 与 hydrated JavaScript 卡片是两套并行 renderer

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: 首屏 Jinja/prepaint 与 hydration 后 JavaScript 分别实现 feed 和 WeChat 卡片。字段、点击目标或展示规则只改一侧时，会产生闪烁或前后不一致。Fix 方向：先建立 feed/WeChat 的 SSR 与 hydrated DOM parity 契约，再决定共享模板输出或保留双 renderer。

---

## [open] `web/static/app.js` 承担全部页面行为，缺少页面级边界

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: 单文件同时承载共享 DOM/API/date 工具、feed 卡片、分页及 `/`、`/all`、`/wechat`、`/daily`、`/about`、`/item` 初始化，页面级修改需要加载无关上下文。Fix 方向：按共享 core、feed/pagination 和页面 initializer 渐进拆成 ESM，并在过渡期保持现有公开 initializer。

---

## [open] `web/static/style.css` 的页面样式与响应式区段相互交叠

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: 全局 shell、feed、daily、WeChat 与多个重叠 breakpoint 共用同一长 cascade，移动端修复容易依赖远距离覆盖顺序。Fix 方向：先统一 breakpoint 结构，再按页面/组件拆分；实施时配套桌面与移动端视觉回归。

---

## [open] `interpret/runner.py` 混合跨仓库 adapter、解析、持久化与编排

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: runner 同时负责 ai-assistant 子进程协议、KB/index 查询、结果解析、slug 冲突、DB 写入、usage 记账与 stage loop，容易在清理时破坏 ADR-007 的串行 writeback 与单一 `save_decision` gate。Fix 方向：抽取 client/adapter、typed normalizer、KB lookup 与 repository；顶层编排继续保持串行。

---

## [open] Playwright 服务启动窗口小于大型数据库 migration 时间

- Type: test-infrastructure
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构最终验证
- Description: `tests/playwright/conftest.py` 的固定 30 秒 healthz 等待窗口，小于大型冻结数据库执行幂等 migration 003/FTS 重建所需时间，导致整套 Playwright 在 setup 共因失败。临时放宽到 120 秒可启动，但不应永久以大 timeout 掩盖 fixture 成本。Fix 方向：让 Playwright 使用预迁移的小型隔离 DB，或把一次性 migration 明确移出每次服务启动路径。

---

## [open] continuous-performance fleet 把 `incompatible` 计为 infra failure，public vantage 未配置时无法真正"干净跳过"

- Type: integration
- Priority: low（该集成当前休眠：registry 为空、checkout 无 `config/performance-adapter` 入口、历史 wrapper source-hash 与当前源码不符）
- Discovered: 2026-07-19 规则审计的 review gate（`AI_RADAR_PUBLIC_URL` 中性化改造复核）
- Description: `run_adapter` 现对未配置的 `same_host_public` vantage 以 `ProbeInfrastructureError("vantage_unconfigured")` 干净拒绝（wire-schema safe 的 bare slug reason，exit 78 → status=incompatible），不再用空 base URL 产生伪 hard_failure 样本——这是 repo 侧能做到的最干净语义。但外层 continuous-performance fleet 会把 `incompatible` 计为 infrastructure failure，令 `run-all` 失败并触发外层告警，因此"未配置 = 静默跳过"在 fleet 层面不成立。Fix 方向（激活该集成时执行）：在 fleet/journey 注册配置层面按 `AI_RADAR_PUBLIC_URL` 是否配置决定是否注册 `same_host_public` vantage（未配置就不调度，而不是调度后靠 incompatible 兜底）；或在 fleet 侧为 `vantage_unconfigured` reason 增加"配置性跳过"的非告警处置。在此之前不要在未配置 public URL 的环境注册 public vantage journeys。

---

## [open] PERF probe `value_ms` 含 Chromium 启动耗时 → 慢启动可伪造站点性能页

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；pre-existing（HEAD `browser_probe.py` 即在 `chromium.launch()`/`new_context()`/`new_page()` 之前记 `started`），idle-only follow-up 未引入，按用户裁决单独排期。
- Description: `value_ms = (perf_counter_ns() - started)/1e6` 的 `started` 在浏览器启动前记录，故上报延迟包含 Chromium 启动+context/page 创建耗时。若某轮启动异常慢（如 4s）而站点本身瞬时返回正确内容，样本仍为 `observed, hard_failure=False, value_ms≈4000`；连续 22 轮慢启动会把 p75/p95 顶过 2/3s 预算，产生并非站点退化的 PERF page（reviewer 最小实验已复现）。这是探针基础设施耗时污染站点测量的方向（与 browser-launch-failure 分类正交）。Fix 方向：把测量起点移到 page 就绪之后（site_deadline 已如此计算），使 `value_ms` 只覆盖站点旅程、不含浏览器启动；或从 value 中扣除已知启动区间。改动需配套确认 2/3s 预算校准仍成立。

---

## [open] PERF probe 不检查 `goto()` HTTP 状态，500 + 有效 DOM 判成功

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；pre-existing（HEAD `browser_probe.py:72` 即丢弃 `page.goto()` 返回值），按用户裁决单独排期。
- Description: `page.goto()` 的 response 返回值被完全丢弃，无 status 检查。服务返回 HTTP 500 但错误页/缓存页/fallback shell 仍含预期 selector、文本与 item IDs 时，全部 DOM 断言通过，结果为 `observed, hard_failure=False`，持续 500 永不 page（reviewer 已复现 `status=500` 且 DOM 匹配→observed/non-failure）。空白 500 通常因 selector timeout 兜底 firing，但一般 500 不覆盖。Fix 方向：捕获 `goto()` 返回的 response，对 5xx（及按契约的 4xx）计 `hard_failure=True`；注意与既有 blank-page/selector-timeout 路径去重。

---

## [open] PERF probe 无 timeout 的 `locator.count()/evaluate_all()` renderer hang → 被判 infra 而非站点故障

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；renderer-hang 子路径 pre-existing（无 timeout 的协议调用），idle-only follow-up 的 grace-race 半边已单独修复，此半边按用户裁决单独排期。
- Description: `locator.count()` 与 `locators.evaluate_all()` 在当前 Playwright 实现下经无 timeout 的协议调用执行。页面已渲染预期 DOM 后 JS/renderer 卡死于这些调用时，worker 永不发布 site result，父进程在 `timeout+grace` 后杀 worker 返回 `probe_infra_failure/worker_unavailable`——真实站点/renderer 挂死被改判成 infra、连续发生也不 page。Fix 方向：给这些 locator 操作套用与 goto 一致的剩余 site deadline（`remaining_site_timeout_ms()`），使 renderer hang 触发 `PlaywrightTimeoutError`→observed hard_failure（site fire），而非 worker 沉默→infra。

---

## [open] PERF probe `Pipe()/Process()` 构造失败未包成 infra marker

- Type: reliability
- Priority: low
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；按用户裁决单独排期。
- Description: `process.start()` 已被 try 包裹并转 `probe_infra_failure`，但 `get_context()`、`Pipe()`、`Process()` 在 try 之外。文件描述符耗尽等导致 `Pipe()` 抛 `OSError` 时整个 probe 异常退出、无持久 `probe_infra_failure` marker（stderr 留堆栈、非完全静默，但 worker-start infra 的可见性契约不完整）。Fix 方向：把 context/Pipe/Process 构造纳入同一 infra 兜底，构造失败也产出可见的 `browser_runtime:*` infra 样本。

---

## [open] PERF probe 把 invalid selector/protocol PlaywrightError（探针自身故障）当站点故障 → 误 page

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾第五轮对抗复审（reviewer 019f92af）；pre-existing（HEAD 即 `except (OSError, PlaywrightError) → hard_failure=True`），按用户裁决单独排期。
- Description: page 就绪后的 site-operation 阶段，若探针自身的 selector/API 契约不兼容（如 `PlaywrightError: Unexpected token "?" while parsing css selector` 或 `Protocol error: Invalid parameters`），该错误既不匹配 `_is_browser_runtime_loss` 的 target/browser-closed marker、也不是 `AttributeError`，于是落到 `outcome="observed", hard_failure=True`（browser_probe.py:246）当成真实站点故障；连续 22 次后 evaluator 产生 firing 并写入可信 `firing_basis="observed"`，误发"站点慢" page，且此后即使 probe 转 infra，round-5 统一迁移规则还会把这个错误 provenance 当可信 observed firing 持有（reviewer 只读复现 classification observed True→count 22→evaluation True observed）。这是探针自身故障被误报为站点退化的方向——与 launch/crash 分类同类，但发生在启动后、且原授权曾把"启动后 error 保留 page"划在 scope 外，故本轮 defer。实务风险低：selector 是代码静态常量、部署测试全绿，仅 Playwright 升级破坏 API 契约才触发（可检测的部署/升级时风险）。Fix 方向：新增一个 invalid-API 分类谓词（匹配 invalid selector/protocol 的 PlaywrightError 签名，与 `AttributeError→browser_runtime:invalid_api` 同归），把探针自身契约错误归为 non-firing `browser_runtime:invalid_api` infra，使其永不 page、也不取得 observed provenance；注意与真实站点驱动的 PlaywrightError（导航/连接失败）区分，后者仍 fire。

## 2026-08-04 Playwright 套件对隔离快照过拟合：换真实数据 5 条失败

**现象**：同一份代码，对隔离快照实例（8011，`data/radar.db` 为 VACUUM 快照）跑 `tests/playwright` 是 **114 passed**；对生产库实例（8010，默认 DB，pipeline 每 15 分钟写入）跑同一套是 **109 passed / 5 failed**。逐条查证，**5 条都不是产品缺陷**：

| 用例 | 失败形态 | 判定 |
|---|---|---|
| `test_parity_home_scroll_reaches_api_total_without_duplicates` | `KeyError: 12`（不是断言失败） | 并发写入使批次键漂移。**测试健壮性问题**——它应当以清晰断言失败，而不是 KeyError。契约本身只在"滚动期间无采集写入"时承诺精确等式 |
| `test_parity_hot_times_and_related_sources_are_data_conditional` | 期望 1 个信源名，得到 `['', '']` | **测试提取逻辑过拟合**。实测生产数据下展开正确渲染 `Hacker News AI/LLM (ddp26)` 与 `(mckennameyer)` 两个真实名（API 返回 3 条同名 related，DOM 已按信源+作者去重）；测试期望 `<li>` 结构而实际名字在子 span 里（实测 `li` 计数为 0） |
| `test_parity_unique_navigation_tags_and_favicons_are_preserved` | 期望 Google favicon 代理 URL，实得真实微信头像 `mmbiz.qpic.cn` | **测试断言的是 fallback 分支**。生产数据已 backfill 真实公众号头像，走的是更好的那条路径 |
| `test_parity_daily_and_changelog_journeys_match_dynamic_content` | `all(...)` 为 False | 依赖当期日报内容形态 |
| `test_v10d_all_page_preserves_aihot_media_score_and_selected_reason` | `assert 27 == 0` | 生产库 `/all` 有 27 条精选条目带标记，快照里是 0。断言把"快照恰好为 0"当成了不变量 |

**为什么值得记**：契约规定的 L2-3 gate 用隔离实例，所以这 5 条不阻塞本轮交付。但它意味着**这套 Playwright 不能对真实数据运行**——若将来接 CI 或用生产快照验收，会得到 5 个假失败。修复方向是让断言表达数据无关的不变量（"每个展开项都有非空信源名"而非"恰好是这一个名字"；"精选标记数等于 API 报告的精选数"而非"等于 0"），而不是继续绑定某一份快照的具体取值。

**并发写入的证据**：本次运行期间 `data/radar.db` mtime 为 18:01、运行时刻 18:08，crontab 有 5 条 pipeline 条目（每 15 分钟一轮）。

## 2026-08-04 补充：`test_parity_feed_column_keeps_reference_net_width` 在全量运行下顺序依赖

同一份代码（commit `5b25f62`）：

| 运行方式 | 结果 |
|---|---|
| 全量 `tests/playwright` | `[720-640-40]` 失败——`.timeline` 实测 **704**，期望 640 |
| 只跑该测试的 4 个参数化用例 | **4 passed** |
| 直接用浏览器量（3 次试验，DCL 与稳定后各一次） | `.timeline` 恒为 x=40 / w=**640**，父 `.app-main` 676、内容 640 —— **与期望一致** |

即产品状态正确，失败只在全量顺序下出现。`704 = 720 − 2×8`，说明失败那一刻横向 gutter 是 8px 而非 18px——与前一个用例遗留的视口/样式状态一致，而非本页真实布局。

该测试用 `page.set_viewport_size()` + `page.goto(..., wait_until="domcontentloaded")` 后立即量盒，**没有等待布局在新视口下稳定**；同文件其它用例已有 `_settle_card_count()` 一类的稳定等待，这条没有。

**为什么值得记**：它与本文件上一条（对隔离快照过拟合）同族但成因不同——那批是断言绑定了某份数据快照，这条是**断言绑定了单跑时的时序**。两者共同的后果是：这套 Playwright 目前**不能作为无人值守的 CI gate**，因为它在"换数据"和"换运行顺序"两个维度都会产生假失败。修法是给该用例补视口切换后的布局稳定等待（对 `.timeline` 的盒做两次采样直到一致，或复用既有的 settle 助手），而不是放宽断言容差——容差放宽会把真回归一起放过。

## [open] 2026-08-17：`test_reclaimer_cannot_steal_atomically_published_live_initializer` 在满负载下 flaky

- Type: test reliability · Priority: low · Discovered: 2026-08-17，前端 cache-busting 改动的独立评审中由 reviewer 观测到

该用例用 `subprocess.run(..., timeout=30)` 起外部脚本，属负载敏感。reviewer 在满负载全量跑中观测到一次 `1 failed`，单独重跑 3/3 绿；本地三次全量（`--ignore=tests/playwright`，1554 passed）与单独重跑均未复现。与前端资源改动无共享代码路径。**影响**：把"全量绿"当发布 gate 时，它在这台机器上不是稳定可复现的。仓库根目录当前遗留 79 个 `.pipeline.lock.reclaim.*` 目录，可能与该用例的环境假设相互作用，值得一并排查。

## [open] 2026-08-17：首屏密度守卫用相交判可见、用均值守最坏形态，两条都放行真失败

- Type: test reliability（守卫失效）· Priority: medium · Discovered: 2026-08-17，与 AIHOT 做同条件成对 UI 对比时实测线上首屏

线上 `news.aiplanet.live` 当日 15:01 那版在 1440×900 下**首屏完整可见条目数 = 0**：首张卡片 949px、高过 900px 的视口，第二条卡片顶边在 y=1276；滚动加载后 80 条里有 8 条超过 900px，最高 998px。而 `tests/playwright/test_phase2.py` 里三条本该防住它的断言全部通过。

`0f827cd` 已把根因（列表卡片渲染正文配图）整条移除，随之删掉了三条里的第一条（`max_ratio <= 0.4`）。**剩下两条原封不动，仍然是坏的**：

| 断言 | 位置 | 为什么放行 |
|---|---|---|
| `firstViewportCards >= 2` | `test_phase2.py:264,269` 与 `456,462` | 判据是 `top < innerHeight && bottom > 0`，即**相交**而非完整可见；一张占满整屏的卡片照样计 1 |
| `avgHeight <= 520`（`/all` 档为 420） | `test_phase2.py:265,270` 与 `457,463` | **均值**摊平最坏形态：一条 998px 混进九条约 210px，均值 289 |

作为参照，已删的那条错在口径是**单张图**不是整张卡片——生产上 4 图拼贴每张 360/900 = 恰好 0.400 逐张合规，两行合计 0.8 视口，加标题摘要推荐理由就是 949px。

更根本的一层在夹具：`tests/playwright/conftest.py:176` 只给 index 0 和 30 两条配图、每条 1 张，且 URL 是 `http://127.0.0.1:9/playwright-media.png`（discard 端口，**永不加载**）。于是当时那条名为"配图不得主导视口"的断言，在"图确实不主导"与"根本没有配图"两种情况下读数完全相同——它从未在会让它失败的输入上跑过。配图移除后这条具体夹具缺口不再有消费者，但同一形态的下一条守卫仍会踩。

**怎么修**：这两条断言的正确量法已经落成可执行仪器 `~/.claude/bin/first-screen-density`（完整可见而非相交、max 而非均值、band 要扣 fixed/sticky 头且经祖先裁剪、两条可见性路径不一致即报 unresolved），17 条夹具矩阵在同目录 `.test.py`。要么在 Playwright 里照它的判据重写，要么直接在 CI 里调它。**先让新断言在会失败的输入上红一次再改实现**——当前实现已无正文配图，最坏形态得靠夹具造（一条超过视口高的卡片）。

**关联**：`docs/contracts/ux-contract.md` 的 HP-1 与「精选页"正常日"最少条数 ≥10 条（首屏）」把"首屏"定义成 SSR 直出条数，与"视口里看得见几条"脱钩；改守卫时一并收。HP-7 的配图条款已由 `0f827cd` 记入 `ux-contract-issues.md`。
