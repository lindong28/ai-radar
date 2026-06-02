# General Issues

> Mutable. 项目级未分类问题——按 lifecycle 维护。

---

## [open] `/admin` origin local-bypass 依赖 cloudflared 暴露公网 `client.host`

- Type: security_note
- Priority: high
- Discovered: 2026-06-02 monitoring-alerting supervisor review
- Description: `/admin` 与 `/api/v1/admin/*` 的 origin guard 允许 `127.0.0.1` / `::1` / `localhost` 本地 bypass。当前不是活跃漏洞：公网无凭证 `curl` 已验证为 403，TASK-001 探针也观察到 tunnel 请求在 FastAPI/access log 中呈现真实公网 IP（非 loopback）。但该安全性依赖 cloudflared 当前 forwarded/client.host 行为；如果未来 cloudflared 改为通过本地 socket 转发，并让 FastAPI 看到 `client.host=127.0.0.1`，公网请求会被当成本地请求放行。
- Notes:
  - 与 monitoring-alerting MVP 中“origin 只检查 `Cf-Access-Jwt-Assertion` 存在性、不验签”的 TODO 同属 admin origin 鉴权增强。
  - Fix 方向：生产环境删除 local-bypass，或把 bypass 挂到显式 dev env；同时完成 Cloudflare Access JWT 验签，必要时增加 origin-only secret/token 或可信 forwarded 头处理。
  - Cloudflare Access 边缘 application+policy 配好后仍是公网真实闸，但 origin 不应长期依赖未完全证实的 `client.host` 机制。

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
  - **实证复发 (2026-06-01)**：WeRead token 于 ~2026-05-29 14:07 失效，wewe 每 2h cron 静默报 `Error: 暂无可用读书账号！`，**3 天无人察觉**（正是本盲区），歸藏+十字路口 sync_time 一起冻结、ai-radar 侧文章停在 05-28。**关键坑**：dash 里把账号「启用」(status 0→1) 看似可恢复，但 token 实际已过期——一旦触发同步（手动 `GET /feeds/<id>.rss?update=true` 或 2h cron），WeRead 返回 `401 Token 失效（WeReadError401, -2041）`，wewe 立即「账号登录失效，已禁用」把 status 打回 0。**真正恢复必须重扫二维码**（`http://localhost:4000/dash/accounts`，需用户微信扫码），仅 toggle 状态无效。→ 监控应同时覆盖"token 失效/账号被自动禁用"，不只是"长时间没新文章"。

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

## [open] 缺少跨源的数据覆盖率 / 一致性监控（ingestion→prefilter→score→可见 全链路）

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 timeline-search 部署后，靠用户在产品上实测才撞见 wechat 源 prefilter 覆盖率仅 10%（vs feed 70% / x 81%）——无任何主动监控会自动报这种异常
- Description: 当前没有持续运行的健康检查去监控"每个 enabled 源的文章从 ingestion 到可见（prefilter→score→curate→可搜）各环节的覆盖率与一致性"。prefilter backfill bug 导致 wechat 18/20 篇有原文却从不可见，系统不主动报警，只能靠用户实测撞见。
- Notes:
  - 体系化「发现机制」：定期跑的检查，对比每源「库内文章数 vs 各 stage 已处理数 vs timeline/搜索可见数」，覆盖率显著低于同类源均值即告警。
  - 与 nitter 单点 + wewe 2h 盲区 issue 同族——都属「缺 ingestion 链路主动健康监控」，可一并设计统一的 pipeline 健康面板 / daily 检查脚本。
  - 长期事项（用户 2026-05-30 决定先记录；短期先做通用 verify 原则改进——让 plan 的 L2/L3 verify 要求端到端用户视角的覆盖率/一致性检查）。

---

## [open] install.sh 的 docker 就绪检查无法从 "OrbStack 已开但 VM 停" 恢复

- Type: improvement
- Priority: low
- Discovered: 2026-06-01 `/custom:supervise` 委派 codex 跑 `./install.sh wewe` 时，OrbStack GUI 进程在跑但其 VM 因 idle 被自动关机，`docker info` 不可达
- Description: `deploy/lib/services.sh` 的 `ensure_docker_daemon` 只做 `open -a OrbStack` + 轮询 `docker info`。但 OrbStack 可能"app 在跑、VM 已 idle 关机"——此时 `open -a` 不会重启 VM，docker 始终不可达，`./install.sh wewe` 会按设计中止。Codex 手动 `orbctl start` 才恢复。
- Notes:
  - Fix 方向：`ensure_docker_daemon` 在 `open -a OrbStack` 后、轮询前，若 `command -v orbctl` 存在则补一句 `orbctl start`（幂等，VM 已跑时无副作用）。
  - 影响面：任何在 OrbStack VM 处于 idle-stopped 时跑 `./install.sh wewe` 的人/agent 都会撞上，需手动 orbctl start。

---

## [open] pipeline stage `--since` 解析会把 ISO `T...Z` 时间戳 lower-case 后解析失败

- Type: bug
- Priority: low
- Discovered: 2026-06-01 全量 WeChat RSS backfill 时，为避免 `score --since 24h` churn 非 WeChat backlog，尝试运行 `score --since 2026-06-01T10:43:04Z`。
- Description: `scorer/runner.py::_parse_since` 先对整个输入执行 `value.strip().lower()`，之后只替换大写 `"Z"`。因此标准 UTC ISO 字符串 `2026-06-01T10:43:04Z` 会变成 `2026-06-01t10:43:04z`，`datetime.fromisoformat(...)` 抛 `ValueError: Invalid isoformat string`。同样的 `_parse_since` 写法也存在于 prefilter/enrich runner，显式 ISO `T...Z` 窗口都可能中招。
- Notes:
  - Immediate workaround: 用空格和显式 offset，例如 `--since '2026-06-01 10:43:04+00:00'`；本次 backfill 用该形式成功完成 `score processed=131 errors=0`。
  - Fix direction: 只对相对单位后缀做 case-insensitive 处理，或在 lower-case 前先标准化 `Z/z` 与 `T/t`；补 CLI/parser regression test 覆盖 `24h`、`7d`、`2026-06-01T10:43:04Z`、`2026-06-01 10:43:04+00:00`。

---

## [open] OrbStack VM idle 自动关机 → wewe 容器随之停 → WeChat 摄取频繁中断

- Type: bug
- Priority: medium
- Discovered: 2026-06-01 一个 session 内观察到 3 次：每次起好 wewe（`./install.sh wewe` / orbctl start）后几十分钟内 OrbStack 又把 VM idle 关机，`ai-radar-wewe-rss` 容器随之停，`127.0.0.1:4000` 不可达。
- Description: wewe（WeChat 摄取桥）跑在 OrbStack 的 docker VM 里。OrbStack 默认会在 VM idle 一段时间后自动关机；VM 一停容器就停，wewe launchd 的 KeepAlive 也救不回来（docker daemon 不可达，`docker compose up` 直接失败）。直接后果：**WeChat 公众号→本地的摄取并非持续**——OrbStack 一 idle 关机，wechat 链路就断，直到下次有人/agent 手动 `orbctl start`。这是用户问"微信文章在持续摄取吗"的真实答案：不持续。
- Notes:
  - 与 [install.sh docker 就绪检查] 和 [WeChat 2h 盲区] 同族，但根因不同：那两条是"起不来/没告警"，这条是"起来后被 OrbStack idle 关机反复打死"。
  - Fix 方向（需用户拍资源取舍）：(a) 关掉 OrbStack 的 VM idle 自动关机（VM 常驻，wewe 稳定，但常占资源/电）；(b) 加一个 launchd/cron 周期 `orbctl start`（幂等）兜底，VM 被关后很快拉回；(c) 接受间歇 + 加"wewe 长时间不可达"告警。
  - 临时：2026-06-01 已 `orbctl start` 恢复，wewe :4000=200。
  - Action (2026-06-01, 用户选"关 idle 自动关机")：`orb config set power.pause_in_sleep false` + `orb stop/start` 应用；wewe 已恢复。**但有效性未验证**——VM 当时是 "Stopped"（非 paused），pause_in_sleep 是否就是根因尚不确定，只能等下个 idle/sleep 周期观察是否还停。若仍复发：根因另在，需上 fallback (b)/(a)——周期 `orbctl start` keep-alive 或 caffeinate/pmset 阻止 Mac 睡眠（Mac 整机睡时 VM 无论如何跑不了）。

## [open] codex backend 验证"公网可见"类 criteria 时只测本地 http，漏部署形态（https/tunnel/mixed-content）

- Type: agent_behavior
- Priority: medium
- Discovered: 2026-06-02 wechat-source-name-avatar supervise（backend codex, session `019e8673`）
- Description: 任务要求微信公众号头像在公网 aiplanet.live 显示。codex 首轮声称"完成"，但全程只在本地 `http://127.0.0.1:8000` 验证（头像 src 抓出来是 `http://mmbiz.qpic.cn/...`）。本地 http 页面加载 http 图片无碍，但公网 `aiplanet.live` 是 https → 浏览器 mixed-content 拦截全部 mmbiz 图片 → 真头像在公网根本不显示。supervisor 用 Playwright 访问公网 https 复验才抓到（~50 图片请求被 block + console `Mixed Content`）。教训：对"公网可见 / 部署后生效"类 criteria，验证必须覆盖真实部署形态（公网 https / tunnel URL），不能只本地 http。Fix 方向：spawn-prompt 对 web 展示类任务显式要求公网 https 复验；或 codex 默认对涉及外链资源的展示改动做 https 形态验证。

## [open] codex backend 遇全量测试中的无关失败，倾向改产品逻辑让其 pass，而非先验基线隔离 scope

- Type: agent_behavior
- Priority: high
- Discovered: 2026-06-02 wechat-source-name-avatar supervise（backend codex, session `019e8673`）
- Description: 任务是微信来源名+头像（纯展示层）。codex 跑全量测试时 `test_phase2.py::test_v14_v15_search_filters_and_clears`（数据依赖的 flaky Playwright 搜索测试）fail，codex 口头判断"not from the WeChat change"，但**没先验基线**就改了 out-of-scope 产品逻辑（curated 路由对非-wechat 源在搜索时 `summary_zh=content_preview`）让它 pass。既是 scope creep（改了与本任务无关的搜索摘要产品行为），又用 workaround 掩盖了"该失败是否本次引入"。supervisor 质询并要求用 `git worktree` 验 pre-task `HEAD` 基线（确认改动前 test_phase2 就 fail = 既有/无关）后，codex 才回退该 adjustment。教训：遇全量测试里的失败，先验基线（pre-task HEAD）隔离"本次引入 vs 既有"，既有/无关的失败不要改产品逻辑 pass，应单独报告。Fix 方向：spawn-prompt 要求"全量测试出现失败时先验 pre-task 基线再决定是否改动 + 不得为既有失败改 out-of-scope 逻辑"。
