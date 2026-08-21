# Closed Issues

> Append-only archive for resolved or wontfix issues. Entries moved from a domain file retain their historical evidence; issues discovered and resolved within one plan are recorded here directly with terminal evidence.

## [resolved] ISSUE-019 · P3 ballot repeat-set 的 N=4 分布带窄于实测运行内噪声

**状态**：resolved · **优先级**：high

P3 为把慢变 prompt 前缀移到文章前而执行 before/after 成对评测。第一次 reordered after 在第 4 篇被 schema validator 以 `summary JSON missing non-empty criteria_reason` 拒绝；没有补跑。唯一一次 D5 有界重设计在文章尾部完整重申 schema 的七个字段（`recommendation`、`criteria_reason`、`save_decision`、`save_reason`、`tags`、`keywords`、`projects`）后，新的 10 primary + 2 repeat 全部通过 schema、provider/revision、sampling、system/keywords hash 与逐块 hash；primary N=10 三档分布也全部在冻结带内。

原冻结判据还把 production-derived interval 用于 ballot repeat set N=4，得到 `必读=2`、允许 `[0,1]`。User adjudication 指出：两篇各两次的 repeat set 中，全部 before/after 差异来自 `cec6aabadcc4ed2a`；before primary=`必读`、before repeat=`值得一看`，而 after primary/repeat 均=`必读`。before 侧在模板完全不变时已经跨相邻档翻转，说明运行内噪声底宽于该 N=4 band；repeat 本应量化这种 variance，冻结判据却没有使用它。

**Resolution（2026-08-12，user adjudication）**：N=4 band 作为 criterion defect set aside，primary N=10 成为 operative distribution gate。这个修改发生在看到 reading 之后，确实削弱预注册纪律；因此由用户裁决，implementer/supervisor 不自行豁免。原始 `automatic-assertions.json` 保留 frozen failure，不覆盖历史。D5 template 为 after-redesign SHA-256 `c29f794c66836ffcd45cbca780a665a963a70e746d426c5dfc2c475ded578dd3`，12 份保存的 rendered prompt 已逐一 hash 相等；两组成对 human ballot 的三问均已通过，故 redesign 保留。后续 fresh official L2 在第 1 调用遇到历史 vocabulary gap 后，implementer 一度误用 V38 回滚；独立 provenance 证明该值由 2026-06-07 的旧模板批次 commit `4a74a58353d8091af81d74c09bb6fc946226699d` 预先引入，用户裁决它不是 redesign regression。该回滚确实发生过，但已因错误归因而逆转，不能继续记为本 issue 或 P3 的失败终态。

**P3 terminal evidence（2026-08-12）**：后续按冻结 before 输出中的 novel-keyword 差集预选 `1b0e38e487e98573`，真实保存使隔离 KB keyword count/hash `11,528 / 07c11a... → 11,533 / 5d714f...`，再对不同 item/hash/text 的 `398c50cf6c6ffab7` 完成 raw official `deepseek-v4-pro` 调用。第二次 raw/landed usage 为 input/output/cached=`76,599/2,014/74,880`，hit=`97.755845%`，官方 tariff 派生 `¥0.019953972/篇`；append-only 零-provider finalization 已把 `cached_input_tokens=74,880` 与 source 落到隔离 usage DB，原 failed checkpoint 未改写。由此 V40/V42/V44 全过，成本降低目标已达成；此前 rollback 与失败终态记录只保留为已逆转的审计历史。

## [resolved] ISSUE-016 · A3 healthz 探针端口与已安装 serve 端口不一致

**状态**：resolved · **优先级**：high

**Resolution（2026-08-11）**：生产 `admin alert-check` 改为从已安装 `live.aiplanet.ai-radar.serve.plist` 的 `ProgramArguments` 解析 serve 端口，静态 threshold/calibration 不再夹带 8000 override。真实 LaunchAgent 于 13:46:16 把 A3 从 `firing / 292 failures / :8000` 迁移到 `ok / 0 / :8010`，`sent=1` 且输出 `send A3 resolved sent`；13:51:27 下一轮仍为 `sent=0 / A3 ok / failures=0`。

## [resolved] 2026-08-12 · performance-probe 服务表状态漂移

**状态**：resolved · **优先级**：medium

`docs/operations/services.md` 的服务表曾把 `performance-probe` 写成已部署的 per-file LaunchAgent，与实机状态不符。

**Resolution（2026-08-12）**：文档已与 `./status.sh performance-probe` 和 PAUSED 旧 cron 的现场证据对齐；当前状态由 services.md 服务表单点维护，明确为未安装且旧 hourly cron 保持暂停。

## [resolved] 2026-08-17 [drift] HP-7「媒体资产」与 ADR-054 相反，需随契约修订撤下

**状态**：resolved

- Discovered: 与 aihot.virxact.com 做同条件成对 UI 对比后决定列表卡片不再渲染正文抓取的图片（ADR-054）。实测依据：1440×900 下首屏完整可见条目 0（参照站 3），80 张卡片里 8 张高度超过整屏（max 998px > 视口 900px），且图片被 `object-fit: cover` 大幅裁切（1072×360 的格子里塞 534×610 竖图）。
- 契约现状：`ux-contract.md` HP-7 仍要求「有配图的卡片展示文章图片」「点击图片在新标签页打开大图」——与当前实现直接相反。若不撤下，后续按 hard contract 跑的 UX 测试会把正确实现判成回归，甚至驱动恢复已被明确删除的渲染器。
- 需要的修订：HP-7 整条撤下（或改写为「列表卡片不渲染正文图片；媒体数据仍在 API 中」）。`/wechat` 详情页的图片不在此列，不受影响。
- 同轮另一处**不**冲突、无需修订：L1「时间线页」的「所有条目显示分数标签」与 L2 HP-1 的「分数标签」仍然成立——评分展示只是从裸数字 `89` 改为 `AI 评分 89`（≤960px 视觉上只留 `AI 89`，「评分」二字保留在可访问性树里），标签依然逐条存在。不写分母见 ADR-056。

**Resolution（2026-08-18）**：已被 [ADR-057](../../adr/057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md) 部分推翻，本条的建议方向已不适用。「列表不渲染 RSS 正文图」这一半仍然成立，且已写进 HP-7 现文；但「HP-7 整条撤下」是错的——ADR-057 恢复了 X 推文自带媒体的渲染，参照站的规则本就是「只显示推文媒体、不显示 RSS 正文图」，而非「不显示图片」。点击手势的演化改由 `ux-contract-issues.md` 的 2026-08-18 条目承载（[ADR-058](../../adr/058-shrink-wrap-x-media-thumbnails-and-add-a-lightbox.md)）。本条留档只为保留当时的读数与推理，不再作为待办。

## [resolved] interpret KB check-url hit drops metadata tags in regression test

- Type: bug
- Priority: low
- Discovered: 2026-06-12 open-source-readiness TASK-011/TASK-012 verification
- Resolution (2026-06-15): `src/airadar/interpret/runner.py` now resolves the no-LLM `--check-url` hit back to the summary-agent index by URL, current-user summary file, or any-user summary file, then preserves `metadata.tags` (and related metadata such as recommendation/model) when saving `wechat_interpretations.tags_json`. Regression coverage is in `tests/test_wechat_interpretation.py::test_interpret_runner_reuses_kb_check_url_hit_without_llm`.
- Description: `AI_RADAR_DB=/tmp/airadar-task011-012-nonplaywright.db uv run pytest --ignore=tests/playwright -q` failed only at `tests/test_wechat_interpretation.py::test_interpret_runner_reuses_kb_check_url_hit_without_llm`. The row was written with `tags_json=[]` while the test expected `["Agent"]` from the KB metadata path on a `run.sh --check-url` hit. `src/airadar/interpret/runner.py` and `tests/test_wechat_interpretation.py` were untouched in this TASK-011/TASK-012 diff, so this was not fixed in the install/source-loader scope.

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

## [resolved] `/admin` origin local-bypass 依赖 cloudflared 暴露公网 `client.host`

- Type: security_note
- Priority: high
- Discovered: 2026-06-02 monitoring-alerting supervisor review
- Resolution (2026-06-15): `/admin` 和 `/api/v1/admin/*` 的 loopback bypass 现仅在 `AI_RADAR_ADMIN_ALLOW_LOCAL` 为 `1/true/yes` 时启用，生产默认关闭；Cloudflare Access header 仍可放行运维入口。`tests/test_admin_routes.py` 覆盖无 env 的 loopback 403、显式 dev override 200、以及带 `Cf-Access-Jwt-Assertion` 的 200。剩余 JWT 验签 / origin token 属独立增强，记录在 `docs/operations/monitoring-alerting.md`。
- Description: `/admin` 与 `/api/v1/admin/*` 的 origin guard 允许 `127.0.0.1` / `::1` / `localhost` 本地 bypass。当前不是活跃漏洞：公网无凭证 `curl` 已验证为 403，TASK-001 探针也观察到 tunnel 请求在 FastAPI/access log 中呈现真实公网 IP（非 loopback）。但该安全性依赖 cloudflared 当前 forwarded/client.host 行为；如果未来 cloudflared 改为通过本地 socket 转发，并让 FastAPI 看到 `client.host=127.0.0.1`，公网请求会被当成本地请求放行。

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

## [resolved] WeChat 链路有 2 小时主动监控盲区

- Type: improvement
- Priority: low
- Discovered: 2026-05-29 完成 WeWe RSS launchd 守护后的端到端节奏分析
- Resolution (2026-06-15): 本 issue 的前提（WeWe 容器内 cron `7 */2 * * *`、扫码 token 失效链路）随 WeChat 摄取迁移到托管 Mp2RSS 合集 feed（`wx_mp2rss`，由常规 pipeline 15min cron 消费上游维护登录态）整体退役而失效——WeWe 容器与扫码登录已从服务层移除（见 `docs/operations/wechat-ingestion.md` §接入方案）。原 2h-WeWe-cron 盲点不复存在。泛化的"ingestion 滞后无主动监控"关切（若 Mp2RSS feed 停更如何发现）属另一机制，归入本文件『缺少跨源的数据覆盖率 / 一致性监控（ingestion→prefilter→score→可见 全链路）』同族，由 monitoring-alerting 体系覆盖，不再单独保留本条。
- Description: WeWe 容器内 cron `7 */2 * * *` 决定了"WeChat 公众号→本地"链路最大刷新间隔是 2 小时。这个频率由 WeWe 上游 default 决定，调短的风险是被微信读书风控。当前没有"WeWe 长时间没拉到新文章"的主动告警——只能事后看 SQLite `articles.created_at` 的最大值确认。
- Notes:
  - 不打算调 WeWe cron（动了风险大于收益）。
  - 潜在改进：加一个 daily 检查脚本——如果 `MAX(articles.created_at)` 距今超 24 小时，发个本地通知。
  - 与 nitter 脆点同族：缺乏对 ingestion 链路的主动健康监控。
  - **实证复发 (2026-06-01)**：WeRead token 于 ~2026-05-29 14:07 失效，wewe 每 2h cron 静默报 `Error: 暂无可用读书账号！`，**3 天无人察觉**（正是本盲区），歸藏+十字路口 sync_time 一起冻结、ai-radar 侧文章停在 05-28。**关键坑**：dash 里把账号「启用」(status 0→1) 看似可恢复，但 token 实际已过期——一旦触发同步（手动 `GET /feeds/<id>.rss?update=true` 或 2h cron），WeRead 返回 `401 Token 失效（WeReadError401, -2041）`，wewe 立即「账号登录失效，已禁用」把 status 打回 0。**真正恢复必须重扫二维码**（`http://localhost:4000/dash/accounts`，需用户微信扫码），仅 toggle 状态无效。→ 监控应同时覆盖"token 失效/账号被自动禁用"，不只是"长时间没新文章"。

## [resolved] /api/v1/timeline search 不匹配来源名 / 作者，用户搜源名找不到该来源的文章

- Type: improvement
- Priority: medium
- Discovered: 2026-05-29 用户尝试在公开站点搜索框输入"十字路口"想找十字路口Crossing 公众号的文章，结果搜出 X 上一篇碰巧 title 含"十字路口"的不相关推文（"18 年老粉与微软 GitHub 决裂..."），找不到真正想要的微信文章。
- Description: `/api/v1/timeline?q=<text>` 后端只对 items 表的 `title` 和 `content_text`（FTS5 索引）做匹配，不包含 sources 表的 `name`、`slug` 或文章 `author`。用户用源名 / 公众号名 / 作者名搜索时会撞到 title 里碰巧含相同字面的不相关文章，目标来源的真实条目反而不出现。等价的可观察案例：搜"十字路口"、"歸藏"、"OpenAI"（X 上提到 OpenAI 的推文 vs 来自 openai_blog 的文章）都有此问题。
- Notes:
  - Resolution: 2026-05-30 implemented free-text search over title/body/source name/author/Chinese title for both `/api/v1/timeline` and `/api/v1/curated`. Queries with 3+ characters use FTS; 1-2 character queries fall back to short-field LIKE over title/source name/author/Chinese title.
  - Verified on migrated production backup: `q=十字路口` returns `wx_crossing`; `q=歸藏` includes `wx_guizang`; `q=spankibalt` returns an author-only match.
  - Scope note: source slug is intentionally not searched. The user-facing search box remains free text over names/authors/titles/body, not a `?source=<slug>` filter.

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

## [resolved] 搜索不做简繁归一化，搜简体匹配不到繁体源

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 部署 timeline-search 后验证，搜简体"归藏"返回 0 结果，而源名是繁体"歸藏的AI工具箱"
- Description: FTS5 trigram 与 <3 字 LIKE 兜底都是字面（codepoint）匹配，简体"归"与繁体"歸"是不同字符。实测 items_fts 含简体"归藏" 1 行 vs 繁体"歸藏" 194 行——用户用简体搜中文源（自然输入习惯）匹配不到繁体 source_name/title/title_zh。
- Notes:
  - Fix 方向：搜索时把 query 做简繁双向扩展（如 `MATCH '"归藏" OR "歸藏"'`），或索引+query 统一归一化（需引入 opencc 类简繁转换）。query 层扩展不动索引、相对有界，但要引依赖。
  - 用户 2026-05-30 明确要求先记录、之后再处理。
  - Resolution (2026-06-01): 引入 pure-Python `opencc-python-reimplemented`，query 层生成原文+s2t+t2s 去重变体；FTS5 使用 phrase OR，短 query LIKE 与 source-match ranking 共用同一变体集合。L2 V4：`q=归藏&limit=50` 返回 `wx_guizang` 8 条，等于动态可见 expected=8，位置 `1,3,5,7,9,11,13,15`。

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

## [resolved] 缺少跨源的数据覆盖率 / 一致性监控（ingestion→prefilter→score→可见 全链路）

- Type: improvement
- Priority: medium
- Discovered: 2026-05-30 timeline-search 部署后，靠用户在产品上实测才撞见 wechat 源 prefilter 覆盖率仅 10%（vs feed 70% / x 81%）——无任何主动监控会自动报这种异常
- Resolution (2026-06-15): 归并（fold-into-plan）。跨源覆盖率/一致性监控属"ingestion 链路主动健康监控"族（同本文件『WeChat 链路有 2 小时主动监控盲区』已随 Mp2RSS 迁移失效、本文件『A3 healthz 维度缺主动探测——只能靠 5xx 率间接发现站点异常』已加主动探测），其体系化载体是已定稿的 monitoring-alerting plan（`plans/20260601-monitoring-alerting/`）。该 plan 的后续 build-out 应纳入"每源 库内文章数 vs 各 stage 已处理数 vs timeline/搜索可见数"的覆盖率检查与异常告警；不在 general.md 重复跟踪，避免与 plan 双轨。短期通用 verify 原则改进（L2/L3 端到端覆盖率/一致性检查）已落地。
- Description: 当前没有持续运行的健康检查去监控"每个 enabled 源的文章从 ingestion 到可见（prefilter→score→curate→可搜）各环节的覆盖率与一致性"。prefilter backfill bug 导致 wechat 18/20 篇有原文却从不可见，系统不主动报警，只能靠用户实测撞见。
- Notes:
  - 体系化「发现机制」：定期跑的检查，对比每源「库内文章数 vs 各 stage 已处理数 vs timeline/搜索可见数」，覆盖率显著低于同类源均值即告警。
  - 与 nitter 单点 + wewe 2h 盲区 issue 同族——都属「缺 ingestion 链路主动健康监控」，可一并设计统一的 pipeline 健康面板 / daily 检查脚本。
  - 长期事项（用户 2026-05-30 决定先记录；短期先做通用 verify 原则改进——让 plan 的 L2/L3 verify 要求端到端用户视角的覆盖率/一致性检查）。

## [resolved] `alert` 服务每 5 分钟运行但飞书 webhook 未配置，告警无法送达

- Resolution: 2026-06-06 改为直接读 `~/.claude/.env` 已有的 `FEISHU_GENERAL_ALERT_WEBHOOK`（与 watchdog 共用，单一来源）；`alerts.py` / `deploy/lib/services.sh` / 测试 / 文档不再引用 ai-radar 专属的 `AI_RADAR_FEISHU_WEBHOOK`。`./uninstall.sh alert && ./install.sh alert` 后 `launchctl print` 确认环境注入，走 `send_feishu_message` 实发飞书返回 `StatusCode:0 success`，A1-A4 全绿 `sent=0`。
- Type: bug
- Priority: medium
- Discovered: 2026-06-06 服务级审计——`live.aiplanet.ai-radar.alert` launchd 每 5 分钟跑 `admin alert-check` 并算出 A1-A4（日志显示规则在跑），但 `AI_RADAR_FEISHU_WEBHOOK` 在进程 env、项目 `.env`、`~/.claude/.env` 三处均未设置。
- Description: webhook 缺失时 alert 降级为 dry-run（sent=0）——逐次计算告警规则却无法投递。后果：serve/pipeline/摄取真异常时**不会有任何告警发出**，监控形同虚设，且每 5 分钟仍消耗一次计算。`~/.claude/.env` 里已有 `FEISHU_GENERAL_ALERT_WEBHOOK`（watchdog 用），但 ai-radar 专属的 `AI_RADAR_FEISHU_WEBHOOK` 从未配置。
- Notes:
  - 两种处置（用户决定）：(a) 配置 `AI_RADAR_FEISHU_WEBHOOK`（专属 webhook，或复用 `FEISHU_GENERAL_ALERT_WEBHOOK`）让 alert 真正能送达；(b) 暂不需要 ai-radar 告警则 `./uninstall.sh alert` 停掉每 5 分钟 inert 运行，想做时再 install。
  - 配置后用 `./run.sh admin alert-check` 验证：有触发条件时 sent 计数 > 0。

## [resolved] install.sh 的 docker 就绪检查无法从 "OrbStack 已开但 VM 停" 恢复

- Resolution: 2026-06-06 `wewe` 从服务层移除（WeChat 摄取迁移 Mp2RSS），`deploy/lib/services.sh` 的 `ensure_docker_daemon` 整个 docker 预检随之删除，`install.sh` 不再触碰 Docker——本 issue 的代码路径已不存在。
- Type: improvement
- Priority: low
- Discovered: 2026-06-01 `/custom:supervise` 委派 codex 跑 `./install.sh wewe` 时，OrbStack GUI 进程在跑但其 VM 因 idle 被自动关机，`docker info` 不可达
- Description: `deploy/lib/services.sh` 的 `ensure_docker_daemon` 只做 `open -a OrbStack` + 轮询 `docker info`。但 OrbStack 可能"app 在跑、VM 已 idle 关机"——此时 `open -a` 不会重启 VM，docker 始终不可达，`./install.sh wewe` 会按设计中止。Codex 手动 `orbctl start` 才恢复。
- Notes:
  - Fix 方向：`ensure_docker_daemon` 在 `open -a OrbStack` 后、轮询前，若 `command -v orbctl` 存在则补一句 `orbctl start`（幂等，VM 已跑时无副作用）。
  - 影响面：任何在 OrbStack VM 处于 idle-stopped 时跑 `./install.sh wewe` 的人/agent 都会撞上，需手动 orbctl start。

## [resolved] pipeline stage `--since` 解析会把 ISO `T...Z` 时间戳 lower-case 后解析失败

- Type: bug
- Priority: low
- Discovered: 2026-06-01 全量 WeChat RSS backfill 时，为避免 `score --since 24h` churn 非 WeChat backlog，尝试运行 `score --since 2026-06-01T10:43:04Z`。
- Resolution (2026-06-15): `prefilter` / `scorer` / `enrich` runner 的 `_parse_since` 只对最后一位单位后缀做大小写归一，不再 lower-case 整个输入；标准 `2026-06-01T10:43:04Z` 与显式 offset ISO 都可解析为 UTC。Regression: `tests/test_runner_since_parsing.py`.
- Description: `scorer/runner.py::_parse_since` 先对整个输入执行 `value.strip().lower()`，之后只替换大写 `"Z"`。因此标准 UTC ISO 字符串 `2026-06-01T10:43:04Z` 会变成 `2026-06-01t10:43:04z`，`datetime.fromisoformat(...)` 抛 `ValueError: Invalid isoformat string`。同样的 `_parse_since` 写法也存在于 prefilter/enrich runner，显式 ISO `T...Z` 窗口都可能中招。

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

## [resolved] A4 `daily_inserted_floor` 对"当日累积计数器"全天比较，跨日初假阳

- Type: bug
- Priority: low
- Discovered: 2026-06-07 复盘 alert-check 历史日志（A2 减噪改动时）发现冷启动轮 A4 firing「今日 items 增量 10 < 127」，下一轮即「3064」恢复
- Resolution (2026-06-15): A4 now compares `items_today` against `daily_inserted_floor * minutes_elapsed_today / 1440` (clamped to the day), and `collect_alert_signals` supplies Shanghai-local minutes elapsed. The calibrated full-day floor remains the scaling basis while early-day false positives are avoided. Regression coverage is in `tests/test_admin_alerts.py::test_a4_daily_insert_floor_is_time_proportional`.
- Description: A4 触发条件之一是 `signals.items_today < daily_inserted_floor`（floor=127，`thresholds.py` a4）。`items_today` 是"自当日 00:00 起的累积插入数"，但被**全天任意时刻**与一个**全天总量底线**比较。每天午夜后到累积满 127 篇之前，`items_today` 必然小于 floor → A4 在每日清晨假阳。历史日志中观察到一次（items=10，紧接 3064），launchd 因机器休眠未连续运行掩盖了发生频率。

## [resolved] A3 healthz 维度缺主动探测——只能靠 5xx 率间接发现站点异常

- Type: improvement
- Priority: medium
- Discovered: 2026-06-07 告警减噪改动中发现 `collect_alert_signals` 把 `health_failures=0` 写死，A3 的 healthz 分支永不触发（死信号）
- Resolution (2026-06-15): `run_alert_state_machine` now actively probes local `/api/v1/healthz` every alert-check run, persists consecutive failures under `healthz_probe` in `data/alert-state.json`, and feeds that count into A3. A3 fires when either user-side 5xx rate exceeds threshold or healthz consecutive failures reach the configured floor. Regression coverage is in `tests/test_admin_alerts.py::test_a3_active_healthz_probe_persists_failures_and_recovers`.
- Description: A3 原设计含两维度——用户侧 5xx 率 + "healthz 连续失败 N 次"。但 `health_failures` 在采集层硬编码为 0，`>=2` 永远不成立，healthz 维度是死代码，且消息里"healthz 连续失败 0 次"制造"信号活着"的错觉。2026-06-07 已**删除**该死分支，A3 现仅靠 access log 的 5xx 率触发。遗留盲区：若站点以"不产生 5xx"的方式挂掉（如 tunnel 断 → 请求到不了 origin → 无 5xx、PV=0 → 5xx 率=0），A3 完全沉默。

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
- Description: 该测试通过发写请求验证"业务路由只读"，但运行在共享的生产 `data/radar.db` 上——生产 serve（127.0.0.1:8000，aiplanet.live）+ 15min `score --since 24h` cron 写入时会拿到写锁，测试的写探针在短 busy_timeout 内撞锁即 fail。同族于本文件『test_phase2 wechat 卡片点击测试数据依赖 flaky（.timeline-card 不可见）』：测试未隔离 DB，依赖运行环境。Fix 方向：测试改用隔离 DB（`AI_RADAR_DB` 指向 tmp 副本或 fixture 临时库），不写共享生产库。

## [resolved] X 推文媒体在列表里缺失：拓扑决定了不能用请求时代理

- Discovered: 2026-08-17 用户问「为什么 aiplanet 上文章都没有图，aihot 上有」时逐层查出。
- **aihot 的规则**（curl SSR HTML 实测）：首页 8 个 `<img>` = 4 个 `uc-avatar` + 4 个 `x-tweet-media-img`；`/all` 26 个 = 16 + 10。**RSS 正文抓取图 0 张**，显示的全是 X 推文自带媒体（`pbs.twimg.com`，2048×1152 一类）。所以 ADR-054「列表不渲染 RSS 正文图」与参照站一致；不一致的只有 X 媒体这一条。
- **我们在数据层就是空的**：`src/airadar/fetcher/x_api.py` 的 `tweet.fields` 只有 `author_id,created_at,lang,note_tweet,public_metrics,referenced_tweets`，全文件 0 处 `expansions` / `media.fields` / `media_keys`，且 `content_html=None`；而 `media_assets` 由 `presentation/media.py` 从 `content_html` 解析。**X 推文媒体从未被取回过**。
- **关键约束（实测读数，别再重跑）**：

  | 主机 | 角色 | qpic.cn（国内） | pbs.twimg.com（海外） |
  |---|---|---|---|
  | macmini | 抓取 / LLM / DB 同步源 | 可达 | **经 `AI_RADAR_PROXY_FILE` 代理 HTTP 200** |
  | 腾讯上海 | 只 serve 公网 | 可达（favicon 返回 400，即链路通） | **000 / 超时**；serve 进程环境里代理变量 **0 个**；`.env` 无 `AI_RADAR_PROXY_FILE`；交互与非交互 shell 读数相同（排除"非交互丢环境"） |

- **推论**：现有 `/img?url=` 那套「请求时同源代理」跑在腾讯，对 X 媒体**结构性不可行**——把 `pbs.twimg.com` 加进 `PROXY_IMAGE_HOST_SUFFIXES` 也只会得到 502。微信图能显示恰恰因为 qpic.cn 是国内 CDN。
- **可行形态**：抓取时在 Mac 经代理下载媒体并自存，再经一条**新的静态资产同步通道**送到腾讯由 serve 提供。图片字节不得进 DB——DB 被专门瘦身过（ADR-010：2.28GB→1.495GB；FTS 同步稳态 16.39MB）。
- 待定项（归 plan）：资产存哪、过期策略、同步失败时的降级、存储与带宽预算、EdgeOne 缓存规则、是否改用 EdgeOne 海外节点回源 twimg（可省掉自存，但需控制台确认，agent 无法代做）。

**Resolution（2026-08-20）**：本条的「请求时代理结构性不可行」推论已被 [ADR-057](../../adr/057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md) 推翻——落地形态不是「抓取时自存 + 新静态同步通道」，而是给腾讯 serve 侧的媒体代理接一个新加坡出口代理。取证（2026-08-20 读源码）：`src/airadar/fetcher/x_api.py` 现有 `X_EXPANSIONS = "attachments.media_keys"` 与 `X_MEDIA_FIELDS = "media_key,type,url,preview_image_url,width,height,alt_text"`，媒体落 `extra_json.x_media`；`src/airadar/presentation/media.py` 的 `PROXY_IMAGE_HOST_SUFFIXES` 已含 `pbs.twimg.com`，`_x_media_assets()` 按 `source_kind` 渲染（RSS 正文图仍不展示，与 ADR-054 一致）；`web/static/app.js` 渲染 X 媒体缩略图并带 lightbox（[ADR-058](../../adr/058-shrink-wrap-x-media-thumbnails-and-add-a-lightbox.md)）。落地 commit `42db1fb`。原「待定项（归 plan）」中的自存/过期/带宽预算随之作废——图片字节仍不进 DB。

---

## 迁入批次 · 2026-08-20 harness-issues.md lifecycle 清理

以下条目自 `docs/issues/harness-issues.md` 整条移入——它们各自带终态证据（`Fix APPLIED` / 已闭合 / 已降级），按 domain 文件只存 open 条目的惯例归档。状态标记为本次补写，正文与原有取证一字未改。

## [resolved] H2 — claude-mem appends `<claude-mem-context>` into tracked `AGENTS.md` each session

- Type: plugin / open-source-cleanliness
- Discovered: 2026-06-12, post-TASK-010; working tree showed `M AGENTS.md` re-introducing `aiplanet.live` and observation summaries via an injected `<claude-mem-context>` block.
- Impact: `AGENTS.md` is a tracked file intended for the public open-source repo. The plugin re-pollutes it every session with memory context that can contain project/personal observations — a recurring leak risk that undermines sanitization. (The TASK-010 public clone was cut from the committed clean `AGENTS.md`, so it is unaffected; the pollution was uncommitted.)
- Reproduced: 2026-07-15, a read-only nested Codex review launched through `codeagent-wrapper` appended the same block to an isolated candidate's tracked `AGENTS.md`; the reviewer prompt explicitly prohibited writes. The supervisor removed only the injected block and verified the file was byte-identical to `HEAD` before continuing.
- Reproduced again: the clean Phase 0 canonical verifier passed when invoked with the candidate's `.venv/bin/python`, but each controller subcommand launched through `uv run ... verifier.py` observed a transient injected `AGENTS.md` and correctly failed repository identity with `candidate is dirty`; the block disappeared when the child process exited. The project verifier was changed to invoke its controller with the already validated Python runtime, while keeping canonical project checks under `uv run`.
- Workaround used: `git restore AGENTS.md` before committing.
- Update 2026-07-19: `AGENTS.md` is now a symlink to `CLAUDE.md`（规则加载断链修复，owner 已裁决维持此方向）。claude-mem 的追加会穿透链接写入 `CLAUDE.md` 真身——git 表现为 `M CLAUDE.md`，commit/publish 前的清洁校验对象同步改为 `CLAUDE.md`。**清理必须精细剥离 `<claude-mem-context>...</claude-mem-context>` 块**（如 sed 定界删除），而非整文件 `git restore`——后者会连带丢弃同文件的合法未暂存修改，`docs/issues/general.md` 已有该做法造成不可恢复数据丢失的记录；仅当确认无其它本地修改时才可整文件 restore。
- Update 2026-07-20（复核不复现，降级为已缓解）: 当前 claude-mem **v12.7.5 不再写盘**——核查证据三点:(1) 主 checkout 与 `feedback-loop` worktree 的 `CLAUDE.md` 现均干净;(2) `git log -S "claude-mem-context" -- CLAUDE.md AGENTS.md` 零命中(该块从未进过 git 历史);(3) 插件脚本对 `CLAUDE.md`/`AGENTS.md`/`CLAUDE.local.md` 只作**上下文源读取**、无指向它们的 `writeFileSync`——SessionStart hook 只把记忆**注入 session**(如本类 session 开头的 `<claude-mem-context>` 注入块只在会话上下文、不落盘)。H2 记录的写盘污染在当前版本已消失(很可能插件升级后转为纯 session 注入)。据此**降级为「已缓解待观察」**:commit/publish 前的 `CLAUDE.md` 清洁校验作为廉价保险保留,但不再需要主动追修;若未来某版本回归写盘,再按下条 Suggested fix 处置。
- Suggested fix (owner harness config): exclude `AGENTS.md` from claude-mem's injection target, or strip the `<claude-mem-context>` block pre-commit, or keep memory context in an untracked file. Until fixed, verify `CLAUDE.md` is clean before any commit/publish.

## [resolved] H5 — agent-browser from a non-GUI (Background) session forces headless + webdriver=true, breaking human-in-the-loop login and tripping bot-protection

- Type: agent-behavior / tooling
- Discovered: 2026-06-24, Cloudflare Access `/admin*` setup (backend codex, session `019ef777`, then supervisor direct).
- Symptom: 任务要 codex 用 agent-browser 配 Cloudflare Zero Trust。codex/Claude 的 shell 处于 `launchctl managername=Background`（SSH/后台/子 agent 无 Aqua GUI 访问）→ agent-browser 起的浏览器被强制 `--headless=new`，用户远程桌面**看不到任何窗口**，无法完成人工登录；且无头浏览器 `navigator.webdriver=true` + `HeadlessChrome` UA 被 Cloudflare Turnstile 反复拦截（"请验证您是真人"死循环）。即便 `--headed` 也无效（Background 会话画不出窗口）。
- Impact: 任何需要人工介入（登录/2FA/CAPTCHA）或受 bot 防护的站点，用 agent-browser 自带浏览器全程不可行；浪费大量 wall-clock 在"找不到窗口 + 过不了验证"。
- Workaround used: `open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir=/tmp/...`（经 `open` 路由到 GUI/Aqua 会话 → 真实可见浏览器、`webdriver=false`）+ `agent-browser --cdp 9222` 连接驱动；用户在可见窗口人工过验证。最终对 Cloudflare 这类自家控制台，改用其 **REST API**（CLOUDFLARE_API_TOKEN）一步到位，彻底绕开浏览器。
- Suggested fix: APPLIED — `~/.claude/skills/agent-browser/SKILL.md`（hard-link ×3）已把 Default Path 改为默认 `--headed`，并加了"Background 会话画不出窗口、改用 open+CDP 连真实浏览器、launched 浏览器仍带 webdriver 标志"的说明。教训：(1) 需人工介入/受 bot 防护的站点，不要让 agent-browser 自起浏览器，应 `open` 真实浏览器 + `--cdp` 连接；(2) 自动化厂商自家控制台（Cloudflare 等）天然对抗 bot 检测，优先用其 API 而非浏览器自动化。

## [resolved] H7 — agent-browser daemon ignores configured timeout and repeatedly stalls on healthy local pages

- Type: tooling / browser automation
- Discovered: 2026-07-14, web refactor Batch 3 rollback-page category smoke test.
- Symptom: the first named session opened `/index.html`, captured a snapshot with 40 cards, and clicked the model category; the server logged the expected filtered API request with HTTP 200. A stale daemon then sent later eval/get commands to `about:blank`. After `agent-browser close --all`, three consecutive `open`/`batch` attempts still timed out at about 25.5 seconds even with `AGENT_BROWSER_DEFAULT_TIMEOUT=60000` and `120000`. Server logs showed the HTML, API, and static assets all returning 200 throughout.
- Impact: a healthy local UI cannot complete an agent-browser smoke test, and increasing the documented timeout does not affect the daemon's effective deadline. Repeated retries can waste unbounded wall-clock and leave product acceptance incomplete.
- Workaround used: stop after three repeated failures; retain the successful first snapshot/click plus server request evidence, then use fresh golden HTTP comparison, focused Playwright, and static import/export contracts for the remaining product checks. Do not report the browser smoke as passed.
- Suggested fix: make the daemon honor the configured default timeout for `open`, `batch`, and post-click evaluation; expose the effective timeout and active page/session in diagnostics; make stale-session recovery reset `about:blank` state deterministically.
- Fix APPLIED 2026-07-20 (root cause was different than the suggested fix assumed): agent-browser is a **third-party compiled Homebrew binary** (v0.31.2; the `.js` is a thin launcher) — the daemon cannot be patched, so the durable fix is the user-owned SKILL.md, not the tool. And the real defect was a **doc error**, verified live: `AGENT_BROWSER_DEFAULT_TIMEOUT` (default `25000`ms = the ~25.5s) is read by the daemon at spawn (a client-env change against a running daemon is ignored), and `close` / `close --all` do **not** kill the daemon (it survives `close --all`) — so the doc's "close to reset a stale daemon" claim was wrong in four places and sent agents into unbounded retries. Corrected in ai-agent-config `af9f344` (agent-browser SKILL.md): one canonical "Troubleshooting: Stale Daemon" section, reset = namespace-scoped `pkill` of the process (not `close`), and timeout-is-daemon-spawn-level. Passed `/custom:review-skill` (2 rounds — caught a cross-file contradiction and an over-broad `pkill` that would nuke sibling agents' daemons).

## [resolved] H10 — Self-built verification machinery dominated wall-clock in the continuous-performance plan execution

- Type: agent-behavior / plan-execution economics
- Discovered: 2026-07-17, reviewing the codex session executing `plans/20260715-continuous-performance-loop/` (journal.md is the primary evidence).
- Symptom: the product fix (archive count-cache invalidation + `/wechat` connection close) was complete with RED/GREEN evidence on day 1 (7/15 evening). The following ~2 days went almost entirely to the plan's self-built verification machinery: (1) the 7-node canonical chain was fully rebuilt at least 6 times, of which at least 2 were triggered by mechanical frozen-SHA identity syncs (2-line commits) and 2 failed on sandbox-environment issues (Seatbelt `.venv` ignore semantics, bash 3.2 heredoc temp files) unrelated to product or logic; (2) the production-action ledger anchor was hash-bound to the task-manifest file, so the first legitimate manifest evolution deadlocked the entire approval chain and required a new "manifest evolution contract" plus 3 review rounds; (3) an approval receipt had a ~16-minute expiry window against a human responder who answered 3h24m later — guaranteed expire-and-reprepare; (4) adversarial reviews of internal tooling ran 2-4 rounds per fix, repeatedly escalating findings premised on same-UID-attacker capabilities that the plan's locked trust model had explicitly excluded.
- Impact: ~1.5 of 2 execution days spent on machinery self-verification rather than deliverable progress; the shipped performance fix sat undeployed the whole time.
- Workaround used: user sent a mid-execution steering instruction — batch remaining production actions into one sequence, allow reuse of unaffected canonical green lights for non-authority-path fixes (tests/fixtures/docs), one final full-chain rebuild before delivery; plus a standing policy pre-approving non-push local actions (eliminates receipt-expiry rounds).
- Suggested fix: APPLIED at the durable carriers — `~/research/ai-agent-config/claude/references/plan-review-principles.md` new conditional principle 17 (Verification Machinery Operating Contract: incremental re-verification semantics, receipt windows matched to responder, review depth bounded by declared threat model, identity/anchor evolution contract) and `claude/skills/review-gate/SKILL.md` (feed declared threat model to reviewers; findings premised on excluded capabilities cap at MEDIUM). Both passed their specialty review gates (review-principles 3 rounds / review-skill 2 rounds + verification lens) and are committed in ai-agent-config as abd1f74.
- Recurrence 2026-07-19 (execution-side, `plans/20260718-feedback-loop/` P0 implementation review): the applied fix lives in principle/skill text but was not honored at review-spawn time. P0 (`TASK-001`) is baseline + safety infra that the plan's own rigor vector (D8) labels default `(A1,V1)` — only the replica guard (~4 lines + a symlink-bypass test) is genuinely high-blast-radius; the rest is read-only metrics (`eval/baseline.py`) and test scaffolding. Yet the whole ~1000-line bundle went into a HIGH-tier full-bundle adversarial Codex review *on top of* 2 dev-time fix-verification rounds and the mandatory review-gate, and sat frozen ~15 min while a long-lived codex process (80 min elapsed) ran it. Same shape as H10/H11: review depth not bounded to the declared per-unit rigor, escalation defaults to "up". Owner decision (2026-07-19): narrow P0 adversarial verification to the replica guard + production-DB immutability, let review-gate + the green suite carry the low-risk remainder, then commit; for P1–P6, scope each per-task adversarial pass to the units D8 tags `(A2,·)` rather than the full diff. This strengthens the case for H11 follow-up (b): the orchestrator must scope/bound the implementation-phase review from the plan's declared rigor label, not leave depth to reviewer discretion.
- Fix APPLIED 2026-07-19 at the durable carrier — `~/research/ai-agent-config/claude/skills/review-gate/SKILL.md` 「分档执行」§ gained an "对抗启动面（施加对抗前必做）" forcing step: before 中/高档 adversarial on a multi-hunk diff, the author partitions hunks into authority-defining (deep adversarial) vs frozen-authority mechanical/read-only payload (excluded) per rigor-tiers' "对抗审查只施于定义或修改 authority 的 unit" rule, records the partition to the gate opening, and feeds the excluded set to the reviewer via 「喂什么」; the reviewer MUST cheap-validate each excluded hunk and return a per-hunk disposition in the 返回契约 (confirm frozen / re-judge as authority → pull into adversarial / unverifiable → 未能核实项), so a silent skip becomes an incomplete return contract caught by 「审不了 ≠ 审过」. The over-rigor direction (deep-reviewing a mostly-mechanical bundle unpartitioned) is an author/main-session gate-opening self-check. Passed its /custom:review-skill gate (3 adversarial rounds: the naive scoping first introduced a symmetric under-coverage hole — author mislabels authority as mechanical to escape adversarial — which the closed-loop disposition mechanism above resolves). Committed in ai-agent-config `88b8633`, which also bundled unrelated concurrent-session edits (`execute-plan.md`, `supervise.md`, the tracked `env` template) under a generated message.

**[Correction 2026-07-22 — the "auto-commit daemon" here was a misdiagnosis.]** An earlier version of this note attributed the `88b8633` bundling to an "auto-commit daemon … firing mid-work without author sign-off." A thorough investigation (ruled out every launchd / cron / git-hook / Claude-hook candidate + hermes / openclaw / cogfs / `claude/daemon`; no `core.hooksPath`, no git wrapper) found **there is no auto-commit daemon**. ai-agent-config commits are made by whichever **interactive agent session** finishes a unit of work in the **shared worktree**, calling git via the repo's own create-commit discipline: selective staging (`git add -A` is forbidden by `create-commit/SKILL.md`), `.gitignore` already separating tracked instruction-artifacts from ignored runtime churn, and `suggest-commit-message` generating the conventional message via a `checkpoint` placeholder → `git commit --amend -m`. The `88b8633` bundling was a **one-off staging lapse by a concurrent agent in the shared worktree**, not a systemic committer. **Root cause reframed**: multiple concurrent agent sessions share this config worktree and the commit discipline is declarative-only (no machine gate), so a concurrent session can commit another session's work (observed 2026-07-22: `f94baa1` cleanly committed a completed review-agent-rules edit before its author session got to it) or bundle when staging isn't scoped. **Decision 2026-07-22**: existing discipline (selective staging + `.gitignore` + openclaw committer blacklist) covers the common case and observed harm is low; NOT adding a pre-commit machine gate or worktree isolation for config edits (disproportionate to the low-frequency, low-harm residual). Nothing to remove — no daemon existed.

## [resolved] H13 — feedback-loop 长后台任务 ~20min "Execution cancelled" 的归因被证伪：不是 codex/websocket，是外部 SIGTERM（≥2 个已知 vector）

- Type: agent-behavior / 归因纠正 × 根因
- Discovered: 2026-07-20，复查 `plans/20260718-feedback-loop/` journal（2026-07-19 21:10 lesson）对长后台 codex 实现任务 ~20min 被 "externally Execution cancelled"（观测两次）的归因。
- 被证伪的归因: journal 记为 "upstream codex websocket 503/flap（idle model socket 掉线）"。四个复现实验 + 退出码签名逐一排除：(1) 裸 `codex exec` 单条 25min 静默命令 exit 0 存活（codex 自带状态心跳、免疫代理侧 idle 回收）；(2) 真 `codeagent-wrapper`+codex 同样 exit 0；(3) 纯 `run_in_background` bash 28.5min 无任何信号存活 → 无 harness 定时 reaper；(4) wrapper `--help` 退出码表：`130`=Interrupted（外部信号）、`124`=inactivity/timeout。故 ~20min "Execution cancelled"(`exit 130`) = **外部 SIGTERM/SIGINT**，不是 codex socket 死（那会走 codex 退出码 passthrough）、不是 wrapper 自身的 inactivity/timeout（那是 124）、不是 harness 定时器。传输侧确有激进 idle 回收（纵云梯代理实测空闲 CONNECT 隧道 ~15s 被掐），但 codex 靠自发心跳不受其害——所以它不是杀因。
- 两个已知 external-kill vector（缺原始 codex/wrapper 日志，无法定论哪个杀了 7/19 的两次；两者同为 exit-130、同在共享宿主）：
  - **(a) 监控层误杀**：把"安静但在耗 CPU/IO 的长本地命令"（2.15GB DB 快照）当挂起后 kill。修复见 F1/F3（下）。
  - **(b) 并发 sandbox-bypass reviewer 的 `kill`**：见 **H12**——review-gate 高档 Codex reviewer 在共享宿主上对匹配 `pipeline|feedback|...` 的进程发 `kill`，是独立且实证的 vector。
- Impact: "websocket" 误归因会把后续 session 引向无效应对（重试 / 换实例 / 自建 nitter 式），而真因在监控判据与并发权限面。这条留档，防止那条错误假设被未来 session 重新采信。
- Fixes APPLIED 2026-07-20（均过各自 review gate）:
  - **F1 监控计算活性守卫 + F3 路由规则** → ai-agent-config `62d4555`（`references/background-agent-monitoring.md`）：判挂起前先跑 `task_computing`（进程子树 R/D/U 态或推进 CPU = 在干活），把 stdout 静默 ≠ 挂起坐实为确定性判据；GB 级长静默命令改由 supervisor 自己 `run_in_background` bash 直跑、不进被监控 codex 任务。修复 vector (a)。
  - **F2 wrapper 可 resume 化（原 H8）** → ccg-workflow `ff52c18`，已装 `~/.claude/bin`：被杀前落盘 `<state-dir>/results/*.result.json`（session_id/exit/reason）、失败/被杀保留日志、cancel 路径带 session_id。让任一 vector 的误杀从"结果全丢"变"可干净 resume"。
  - **point1 review 过深熔断（H11 follow-up b）** → ai-agent-config `946db50`（`commands/custom/create-plan.md`）：orchestrator 无条件计数、达预算强制"定稿 vs 继续" AskUserQuestion，不依赖 reviewer 自报收敛。
- 关联: **H8**（F2 修）、**H10/H11**（point1 = H11-b 执行侧配套）、**H12**（vector b 及其权限面修复）。

## [resolved] 2026-08-02 补充观察（AIHOT 改版 session）

### agent-browser 截图管线复发性卡死（os error 35），repo 自带 Playwright 是更稳的兜底
- 现象：`screenshot` 在 open/scroll 等活动后频繁 "Resource temporarily unavailable (os error 35) (after 5 retries)"，杀 daemon 重开、换 session、`network route --abort` 屏蔽悬挂图片请求均无效；同一页面用项目 venv 里的 Playwright（chromium.launch + page.screenshot）一次通过且可顺带做断言。
- 建议：agent-browser SKILL.md 增补一条 fallback 指引——目标 repo 已含 Playwright 依赖时，验证/截图类任务直接用 repo Playwright 脚本，不与 agent-browser daemon 缠斗超过两次重置。
- Fix APPLIED 2026-08-02 (ai-agent-config `e9af26a`): `agent-browser` SKILL.md 的 Stale Daemon 节新增「Fallback: the project's own Playwright for verification」。审查过程纠正了本条建议的两个措辞：(a) 启用判据不是「repo 含 Playwright 依赖」而是「项目本身就在驱动 Playwright（浏览器二进制已装）」——官方契约明确包可 import ≠ 可 launch；(b) 脚本必须走 runner stdin 或 `mktemp`、不落进目标仓库，否则制造需清理的 orphan。同批修好既有文本两处：中段「仍 stall 就报 `Blocked`」改为指向本节终局规则（否则最常见的「无 live pid」路径永远到不了该 fallback），终局补回 `Blocked`/`uncovered` 二元。

### 主 harness 文件工具链退化时，未考虑把批量编辑委派给 Codex（本轮教训）
- 本轮 Read 工具被坏 hook 全程拦死（连带 Edit/对已有文件的 Write），作者改用 Bash+python 补丁脚本硬扛完成了 ~20 文件的改版。可行但易错（补丁脚本自身出过一次结构错误），且每次编辑都失去 harness 的 file-state 跟踪。
- 事后评估：Codex 是独立 harness，不受 Claude Code hook 影响，文件编辑工具链完好。该场景下"把 spec 明确的批量编辑单元委派给 codeagent-wrapper --backend codex"很可能更快更稳。
- 建议：在 delegation-policy（或 durable-solution-carriers 指认的更合适载体）增加一条路由信号：主 harness 编辑工具链因 hook/权限故障退化、且故障本 session 不可修复时，优先评估跨 harness 委派而非 shell 层 workaround。
- Fix APPLIED 2026-08-02 (ai-agent-config `e9af26a`): 落在两层——`delegation-policy.md` 的 Eligibility 新增该规则（条件收窄为「主 harness **自身工具层**故障」，因为括号里的论证只对 harness 层成立，文件系统级故障会让 Codex 同样瘫痪），Codex 专属理由移入「Harness transport」节末段。关键补充：user CLAUDE.md 的「Delegation Boundary」新增三行 when-to-delegate 场景表——审查指出仅改 reference 这条规则**在自己的触发场景下永远读不到**（该文件只在「委派前」被读，而规则要纠正的恰是没打算委派的 agent，且 always-loaded 层已有「Resolve Blockers, Don't Bypass」这条竞争默认）。表格内容经用户裁决保留（其要求 always-loaded 层给出常见适合委派场景清单）。

## [resolved] 2026-06-07 [expansion] ux-contract §微信文章解读页 未覆盖新增的搜索功能（closed 2026-06-15）

- Resolution (2026-06-15): 已在 ux-contract.md `/wechat` 页面描述与 WX-4 写入 WeChat 专属搜索字段、LIKE/繁简/2 字行为、URL/分页/详情/404 上下文和空态，修正"v1 无搜索"旧描述。
- Discovered: execute-plan 实施 `20260607-wechat-interpretation-search`（/wechat 新增搜索框）后的 supervisor 收尾核查 + test-ux 验收。已上线公开站点 `/wechat`。
- Description: 契约 §微信文章解读页 当前只描述"列表卡片 + 站内详情"、无搜索；但 `/wechat` 已新增搜索框，且语义**刻意不同于**精选/全部页（后者匹配 标题/正文/来源名/作者/中文标题、≥3 字走 FTS）：
  - 匹配字段：原文标题 / 公众号名(作者) / 摘要(abstract) / 标签(tags)——**不搜正文、不搜结构化解读全文 summary_md、不匹配聚合 feed 来源名 s.name「微信公众号（Mp2RSS 合集）」**（匹配 s.name 会让全部条目命中）。
  - 一律 LIKE（无 ≥3 字 FTS 分支），繁简互通，2 字专名可搜。
  - 排序：公众号名(作者)命中优先于其他字段命中，其余按发布时间倒序。
  - 行为：debounce 即时收敛；翻页保持 `q`；URL `?q=` 同步、刷新/分享保持；清空恢复全量；详情页与 404 页的站内返回链接保持搜索态（`/wechat?q=...&page=...`）；无匹配显示空状态；placeholder = `搜索标题/公众号/摘要/标签…`。
- Recommendation: 在 §微信文章解读页 增「搜索」契约段，写明上述匹配维度与语义，**特别标注与精选/全部页搜索的差异**（不搜正文/解读全文、不匹配聚合来源名、按公众号作者优先），并补 URL `?q=` 同步、清空恢复、详情/404 返回保持搜索态、空状态文案 这些可验证行为，供下游 test-ux 据以验收。

## [resolved] 2026-06-01 [expansion] ux-contract 未明确搜来源名时的排序承诺（closed 2026-06-15）

- Resolution (2026-06-15): 已在 HP-4/TL-3 写入 `q` 生效时来源名/作者命中优先、同层按 `source_id` 轮转进入分页结果、无 `q` 保持时间倒序。
- Discovered: 中文/微信公众号源搜索可用性修复（#6）落地后，产品实现已在搜索态将 source name / author 命中的条目排在内容命中之前，并在同名来源之间用 source_id 轮转，避免高产同名源淹没低产公众号源。
- Description: `ux-contract.md` HP-4 已承诺"搜源名返回该源内容"，但未定义首屏排序语义。没有排序契约时，未来重构可能回退到纯时间序，导致 `歸藏` 这类同名 X + 微信公众号场景再次让公众号在首屏外。
- Recommendation: 在搜索契约中补充：有 `q` 时，source name / author 命中优先于 title/content-only 命中；同一命中层内按来源轮转保证每个命中来源首条在 page1 可见；无 `q` 时保留原时间/日期排序。

## [resolved] 2026-05-28 19:20 [expansion] ux-contract 未明确 `/` 和 `/all` 首屏应 SSR 预载且不显示 loading spinner（closed 2026-06-15）

- Resolution (2026-06-15): 已在 HP-1/TL-1/RS-3 写入 `/` 与 `/all` 首屏 SSR preload、HTML 到达即有 `.item-row`、不依赖初始 `/api/v1/*` fetch、无可感知 spinner。
- Discovered: SSR preload plan production verification for the public site after comparing the existing CSR loading behavior with AIHOT-style inline/preloaded content.
- Description: 当前实现已让 `/`、`/all` 和三个常见 deep link 在生产环境首屏直出 `.item-row`，Playwright gate 结果为 spinner 0、initial API 0，FCP median 均低于 1.5s。但 ux-contract 还没有把"主 feed 首屏应在 HTML/preload 阶段可见，不依赖初始 API fetch，也不出现可感知 loading spinner"作为行为契约写死。
- Recommendation: 在对应 Feed Reading / Initial Load contract 中补充：`/` 与 `/all` 的首屏内容必须通过 SSR preload 或等价机制在 HTML 到达后即可渲染；生产验证以 spinner 出现次数、首个 `.item-row` 时间、initial `/api/v1/*` 请求数为指标。

## [resolved] 2026-05-29 [expansion] ux-contract 未约定图片加载行为（图床可达性 / 不阻塞首屏 / 懒加载），与 AIHOT 实现存在 parity gap（closed 2026-06-15）

- Resolution (2026-06-15): 已在 HP-7 写入当前 shipped 图片 lazy loading 与失败隔离契约；未改变产品行为，未引入图片代理或额外属性。
- Discovered: 对比 `https://aihot.virxact.com/all` 加载机制的讨论收尾。AIHOT 首屏初次加载发起 26 个 `/api/img-proxy?u=<encoded-image-url>` 请求代理外部图床（主要是 X `pbs.twimg.com` 头像），并行下载且不阻塞 HTML 首屏渲染。AI Planet 现状是 `app.js` 渲染卡片时直接引用原始外部图床 URL（X `pbs.twimg.com`、各家 OG image 等），无服务端代理、无懒加载属性。
- Description: 现行 `ux-contract.md` Feed Reading 段只约束文本/标签/分数的首屏可见性，对图片只字未提。实际后果至少三条：(a) X 图床在国内网络不稳定，图片偶发失败/超时但 contract 未声明"图片失败不应影响阅读"或"图片必须可达"；(b) 大量并行图片请求与文本首屏共享 HTTP 连接预算，理论上可能拖累 `.item-row` 渲染（已通过 SSR prepaint 缓解但未量化）；(c) Off-screen 图片随 HTML 一并加载，浪费首屏带宽。AIHOT 通过 `/api/img-proxy` 同源代理把图床可达性收敛到自家 CF/服务器，并隐式启用浏览器 connection coalescing。
- Recommendation: 三选一或组合：
  - (a) **快胜**：现有 `<img>` 加 `loading="lazy" decoding="async"`，约束 contract："首屏外可视区域的图片不应在初次 HTML 加载阶段下载完成；图片失败不应影响 `.item-row` 文本可读性。" 工作量极低，立刻可做。
  - (b) **中期**：实现 `/api/img-proxy?u=<url>` 同源代理 + 服务端缓存（参照 AIHOT 命名约定保持 parity），契约约束图片源可达性 SLO（如 p95 < 500ms）。涉及缓存层与带宽成本，需要单独 plan 评估。
  - (c) **观测先行**：在做 (a)/(b) 之前，加一次 Playwright 性能 probe 测量当前生产 X 图床失败率与首屏阻塞情况，用数据决定优先级。
  推荐顺序：(c) probe → (a) 快胜立刻做 → (b) 视 probe 结果决定是否独立 plan。

## [resolved] 2026-05-18 22:30 [drift] aihot-parity-contract §SourceParity-AboutSurfaceReflection 假设 AIHOT 通过 /about 暴露 source pool，实际 AIHOT /about 是个人介绍页 + 公众号 QR（closed 2026-06-15）

- Resolution (2026-06-15): Obsolete/resolved：`aihot-parity-contract.md` 已在开源清理中移除，目标契约不存在，不再需要修订该 parity 条目。
- Discovered: 2026-05-18-r1 / s3-parity-auditor / Layer 1 跑测时对照 AIHOT `/about`
- Description: `aihot-parity-contract.md §SourceParity-AboutSurfaceReflection` 暗含"两端 /about 都暴露 source table"的假设；实测 AIHOT `/about` (`evidence/s3/aihot-about.png`) 是"嗨,我是数字生命卡兹克 / 这个站是我做的,免费给大家用" + 公众号 QR，不暴露任何 source pool。AIHOT 的源池只能从 `/all` / `/curated` 卡片头像 + handle 推断。AI Planet `/about` 暴露 41 行 source table 是设计差异，不算 issue（VISION §6 透明原则），但当前契约措辞会让下游 test-ux 误以为可以两端 `/about` 直接对照。
- Recommendation: 修改 §0 参照锚点表中 `信源池真值` 一栏，对 AIHOT 改为 "公开站点暴露源（卡片头像 + handle，不通过 /about）"；并把 §SourceParity-AboutSurfaceReflection 改为 AI Planet 内部一致性测试（`sources.toml` ↔ `/about table`），不再要求与 AIHOT 对照。

## [resolved] 2026-05-18 22:30 [drift] ux-contract §Feature-DailyNav 与 §Feature-DailySections 在"合法日期 + 无内容"上承诺重叠/冲突（closed 2026-06-15）

- Resolution (2026-06-15): 已在 DY-2 拆分边界：非法/不可解析日期切最近一期并显示 fallback banner；合法但无数据日期保留该日期并显示明确空态。
- Discovered: 2026-05-18-r1 / s4-responsive-and-edges / Issue 6（也被 s1-first-time-visitor Issue 2 在 `/daily/1999-01-01` 上独立交叉验证）
- Description: §Feature-DailyNav 边界承诺：「访问 `/daily/<无效或无内容日期>` 时静默切到最近一期，并显示 fallback banner」；§Feature-DailySections 边界承诺：「某日全节皆空时整个 sections 区显示明确空态文案而非白屏」。两条边界在"合法日期格式但无数据"上重叠：当前实现是 `/daily/9999-99-99`（非法格式）走 §Feature-DailyNav fallback banner，`/daily/2000-01-01` 或 `/daily/1999-01-01`（合法格式 + 无数据）走 §Feature-DailySections 空态文案。契约没区分"非法格式 vs 合法 + 无内容"两种情形，导致同样是无内容用户拿到两种不同体验。
- Recommendation: 拆分边界承诺。建议措辞：
  - §Feature-DailyNav 边界："访问 `/daily/<非法日期格式>` 时静默切到最近一期 + fallback banner。"
  - §Feature-DailySections 边界（保留）："某日全节皆空时显式空态文案，不白屏。"
  - 或者反之：合法 + 无内容也走 fallback。两选一并写死。

## [resolved] 2026-05-18 22:30 [drift] ux-contract §Feature-Pagination 措辞"超范围 page 返回空列表"，实现是 clamp 到 max page（closed 2026-06-15）

- Resolution (2026-06-15): Resolved：ux-contract.md HP-8/TL-4/WX-4 现均明确越界页码 clamp 到最后一页，契约已与实现对齐。
- Discovered: 2026-05-18-r1 / s4-responsive-and-edges Issue 5 + Issue 8（s2-returning-power-user Issue 5 也在 `?page=999` 上看到了长 loading 后才发生 clamp）
- Description: §Feature-Pagination 边界："超范围 page 返回空列表，分页器仍可回退；page<1 或非数字按 1 处理。" 实测 `/all?page=999` 经过 ~9s loading 后 URL 被前端改写为 `/all?page=16`（最后一页），渲染该页内容；`/all?category=ai-models&page=2`（超范围因为 ai-models 只 1 页）则 URL 被改写为 `/all?category=ai-models`（直接剥掉 page 参数）。两种行为都不是契约措辞的"返回空列表"。
- Recommendation: 二选一并写死：
  - (a) 实现回到契约："超范围 page = 空列表 + 分页器可回退 + URL 保留"；
  - (b) 契约跟实现："超范围 page = clamp 到 max page，URL 同步改写为 max；带 filter 且总页数 1 时剥掉 page 参数。"
  目前的混合行为让深链复用 / monitoring / 用户预期都不稳定。

## [resolved] 2026-05-29 07:15 [expansion] ux-contract 未覆盖 wechat（微信公众号）源类型及其"未 enrich 时抑制正文预览"的展示规则（closed 2026-06-15）

- Resolution (2026-06-15): 已在 TL-2 写入微信公众号来源归入"资讯"、未 enrich 时抑制正文预览、enrich 后显示中文摘要、标题回链 mp.weixin 原文。
- Discovered: execute-plan 实施 `20260528-wechat-oa-ingestion`（新增 `kind="wechat"` 源）后的 supervisor 收尾核查。
- Description: 新增 wechat 源（首批 歸藏的AI工具箱 / 十字路口Crossing）归入"资讯"频道（`kind != "x"`），在 `/` 与 `/all` 同普通 feed 源一并展示。但有一处 wechat 特有的展示规则未写入 ux-contract：出于合规（不公开转载公众号正文），wechat item 在 web 层**抑制 `content_preview`**——未 enrich 的 wechat 卡片正文区为空（仅中文标题 + 回链 mp.weixin），enrich 后才显示 `summary_zh`；而普通 feed 源未 enrich 时仍显示 `content_preview`（正文前 320 字）。当前 ux-contract（§TL-2 信源类型筛选只列 一手信源/资讯/推文；卡片展示默认有 preview/摘要）未反映这点，下游 test-ux 可能把"未 enrich 的 wechat 卡片无正文预览"误判为 bug。
- Recommendation: 在 ux-contract 补充 wechat 源的展示契约：(a) wechat 源归入"资讯"类型（feed/x/wechat 三类信源）；(b) 卡片正文：enrich 后显示中文摘要，未 enrich 时仅标题 + 回链（正文不对外公开，合规要求）；(c) 点击标题回链到 `mp.weixin.qq.com` 原文。

## [resolved] 2026-05-18 22:30 [expansion] ux-contract §Feature-CategoryFilter 未明确"无效 slug 静默回退时是否清掉 URL 上的脏参数"（closed 2026-06-15）

- Resolution (2026-06-15): 已在 HP-3 写入 `/?category=<无效 slug>` 静默回退到"全部"并由客户端剥除无效 `category` 参数。
- Discovered: 2026-05-18-r1 / s2-returning-power-user Issue 9（深链 `/?category=invalid-slug` 测试）
- Description: §Feature-CategoryFilter 边界："无效 slug 静默回退到「全部」（不报错）。" 实测 `/?category=invalid-slug` 行为：列表正确渲染全部精选 ≈5s 后地址栏被改写为公开站点根路径（脏参数被剥）。契约没说要清也没说要保留。两种行为各有理由：清 → 防止用户把坏链发出去再次复制；保留 → 让 admin / monitoring 看到误配。
- Recommendation: 在 §Feature-CategoryFilter 边界条目补一句明确，例如：「URL 保留无效参数以便排错」或「URL 清掉无效参数防止扩散」。同理 §Feature-ChannelFilter 也需补；§Feature-Pagination 的 page<1 / 非数字行为同样未说 URL 是否清——可以一并归类为"无效 query 参数的 URL 处理策略"统一段落。

## [resolved] 2026-06-08 · `/wechat` 搜索对空格敏感：`分享Claude Code`（库内无空格）能搜到，`分享 Claude Code`（带空格）搜不到

- 背景：用户 2026-06-08 反馈带空格搜不到内容，体验不好。
- 根因：当时搜索 helper（现位于 `src/airadar/web/routes/search.py` 的 `expand_st_variants` / `like_patterns_for_query`）只 `strip()` 首尾、不归一化内部空格，被匹配列（title/author/abstract/tags）也不归一化；LIKE pattern `%分享 Claude Code%` 命中不了存库的 `分享Claude Code`。
- 影响面：同一搜索通道也服务 timeline/curated，修一处全局受益。
- 修复：搜索做空格不敏感——查询 pattern 与被匹配列两侧都剥除空白（含全角空格）后比对，并补 FTS 路径（timeline/curated 长查询走 trigram FTS）。加回归测试复现该用例；89 测试通过、ruff clean。**需重启 serve 生效**（纯 Python web 层改动）。

## [resolved] 2026-06-08 · `/wechat` 公众号「赛博禅心」头像缺失（回退显示首字"赛"，被读作头像不对）

- 背景：用户 2026-06-08 反馈该公众号头像不对。
- 根因：生产库 `data/radar.db` 中它是 15 个账号里唯一 `avatar_url` 为空者——2026-06-02 Playwright 抓 `round_head_img` 失败，落 7 天负缓存（`fetcher/runner.py` `WECHAT_AVATAR_NEGATIVE_CACHE_TTL=7d`），到期前不重试，页面回退显示首字。
- 修复：新增 `admin wechat-avatar refresh --account <名>` CLI（清该账号缓存 + 实抓），对 赛博禅心 实跑 Playwright 重抓成功，`avatar_url` 填入有效 `mmbiz.qpic.cn` URL（非空头像 14→15，已生效无需重启）；抓取失败负缓存 TTL 由 7 天缩到 2 天让偶发失败更快自愈。手动覆盖未用到。加 CLI + TTL 回归测试。

## [wontfix] 2026-06-02 · 微信文章解读：解读内容质量验收（用户 2026-06-02 决定不验）

- 背景：create-ux-contract 时 de-scoped（功能流程 + 视觉交互优先）。execute-ux-contract round 1 的机制/功能/视觉验收（WX-1~9）全过后，用户决定**不再单独验内容质量**——渲染正确即足够；解读忠实/有用、"值得读"判定合理性、标签语义交由 summarizer（ai-assistant summarize-article）上游保证。
- 原计划（不再执行）：抽样 `/wechat` 详情对照原文微信文章判断忠实/有用 + 值得读判定是否合理 + 标签语义。
- 机制验收记录：`plans/20260602-2034-ux-contract-ux-test/`（TS-001~008 PASS，TS-009 不可自然到达）。

## [resolved] EdgeOne 从不缓存 `/img`，每个访客每张图都跨洋回源，并偶发 404

- 背景：2026-08-18 排查"某条推文的第二张图在浏览器里消失、curl 同 URL 却 200"时发现。根因取证已闭合，见下。
  - **直接证据**：生产日志里那张图确实 404 过一次——`serve-8000.log` 的 `2026-08-18T18:47:48+0800 "GET /img?url=...HP94k8hXoAAcZgM.jpg" 404 Not Found`，同一秒另一张图（`HP91Z3iWcAEGZxD.jpg`）也 404，来自不同边缘 IP。同一个 URL 在其余 9 次请求里都是 200。槽 8000 共 1044 次 `/img` 请求、3 次 404（0.29%）；槽 8001 的 1858 次全部 200。
  - **机制（已核实的部分）**：`src/airadar/web/routes/media.py` 把**每一种**失败都映射成 404——主机不在允许名单、上游非 200、重定向越界、以及 `httpx.HTTPError`（含超时），全部走同一个 `return Response(status_code=404)`（ADR-057 的有意设计：宁可快速失败）。前端 `onerror` 随即隐藏该图，于是任何失败在页面上都表现为"这张图不存在"，没有可见错误。
  - **未核实：那两次 404 到底是不是超时。** 上面那条正是原因——404 是所有失败的共同出口，日志里的状态码**区分不了**超时与"twimg 当时就返 404/403"。而 `/img` 的失败路径**不写任何日志**（异常被吞），`serve-8000.err.log` 586 行里与 img/timeout/httpx 相关的为 0 行。故服务端不存在能区分二者的读数，超时只是**待验怀疑**，不是结论。旁证（不足以定案）：18:47 那一分钟有 10 次 `/img` 请求，是相邻几分钟里最密的（18:40 为 4 次、18:48/18:49 各 1 次）；且并发压测下 p90 已达 9.2s、逼近 10s 读超时。若要定案，需要给 `/img` 的失败路径加上区分性日志（记下异常类型与上游状态码），那是一个独立改动。
  - **注意本条的行动项不依赖上面那个未核实点**：加边缘缓存的理由是延迟读数本身（p50 约 5s、p90 9.2s，而源站只用 0.5-0.7s），与 404 归因无关。
  - **为什么会慢到撞超时**：`/img` 源站已发 `Cache-Control: public, immutable, max-age=604800`，但**边缘一次都不缓存**——同一 URL 连测三次全是 `eo-cache-status: MISS`；`./run.sh admin edgeone check` 列出的两条缓存规则（`/wechat`+`/api/v1/wechat`、`/style.css`+`/app.js`）都不覆盖 `/img`。故每个访客每次刷新都要为每张图跨洋回源一次。实测 `server-timing: app;dur≈0.5-0.7s`（源站取图其实很快），而端到端 p50 约 5s、并发 24 时 p90 9.2s / max 11.9s——与 10s 读超时同一量级。
  - **缓存键风险已排除**（本来是这条最大的坑）：担心默认缓存键忽略 `?url=` 查询串、导致全站图片串到同一个缓存条目。实证否定——`/api/v1/wechat?page=1` 与 `?page=2` 返回不同内容且各自独立 MISS→HIT，说明 EdgeOne 默认缓存键**包含**查询串。且现有规则里没有任何 `CacheKeyParameters`（全为 null），即微信那条正是靠默认键工作的。
  - **建议的规则**（照搬现有 `news public lists follow origin cache` 的形状，只换条件）：
    - Condition：`${http.request.host} in ['news.aiplanet.live'] and ${http.request.uri.path} in ['/img']`
    - Action：`CacheParameters.FollowOrigin = {Switch: on, DefaultCache: on, DefaultCacheStrategy: on, DefaultCacheTime: 0}` —— 跟随源站已有的 7 天 immutable，不另设 CustomTime
  - 落地路径：仓内工具 `admin edgeone` 只有 `check` 与 `purge`，**没有写规则的能力**（ADR-039 把控制台定为仓外权威、仓内只镜像快照并查漂移）。SDK 侧 `CreateL7AccRulesRequest` 存在，但新增写能力本身是一个独立决策。规则加好后须跑 `./run.sh admin edgeone check --update-snapshot` 刷新 `web/edgeone-cache-rules.json`，否则下次 check 会报漂移。
  - **已落地（2026-08-19）**：规则 `news img proxy follow origin cache`（`rule-3tzlygp68ka0`）已在控制台创建并发布，仓内快照已用 `--update-snapshot` 同步。实测改善——同一 URL 由 3/3 `MISS` 变为 `MISS→HIT`，单次 2.7-4.0s → 0.60-0.68s；并发 16 全量 38 张在热缓存下 p50/p90/max 由 8.61/13.84/22.32s 降到 1.64/2.33/2.48s；37/37 命中（第 38 条因取证脚本末行无换行被 `while read` 漏读，非数据问题）。
  - **该规则引入了一个新风险，已修**：`/img` 的 404 不带 `Cache-Control`，而 FollowOrigin 在缺该头时套用「默认缓存策略」，于是**失败被负缓存**——发布后实测同一个必然失败的 URL `MISS` 一次后连续三次 `HIT`。这比原本的偶发失败更坏：一次瞬时超时会固化成"这张图不存在"直到 TTL 过期。修法是源站显式声明 `Cache-Control: no-store`（commit `0f0a6fd`），成功路径仍保留 7 天 immutable。**该修复须部署后才生效**；在部署前，边缘仍可能缓存住失败。
  - 仍未验证：负缓存的实际 TTL 有多长（未等待过期）；`no-store` 修复上线后的负缓存消除（待部署后复测）。

**Resolution（2026-08-19 / 归档 2026-08-20）**：缓存规则 `news img proxy follow origin cache`（`rule-3tzlygp68ka0`）已在 EdgeOne 控制台创建发布，仓内快照经 `--update-snapshot` 同步。实测改善：同一 URL 由 3/3 `MISS` 变为 `MISS→HIT`，单次 2.7-4.0s → 0.60-0.68s；并发 16 全量 38 张热缓存下 p50/p90/max 由 8.61/13.84/22.32s 降到 1.64/2.33/2.48s，37/37 命中。规则引入的负缓存风险已由源站 `Cache-Control: no-store`（commit `0f0a6fd`）修掉，成功路径保留 7 天 immutable。**留待办**：负缓存实际 TTL 未实测、`no-store` 上线后未复测、`0f0a6fd` 的生产部署未核实——这三项已另立一条窄 open 条目留在 `deploy.md`。

## [resolved] 2026-08-10：README 全面审查遗留 findings（两组独立审查，范围超出当次交付未就地修）

20260809-fts-rebuild-sync 收尾时对 README 跑了完整原则审查（readme-review-principles §1–§7），当次只修了本 plan diff 内的两处重复；以下为遗留清单（按修复价值排序）：

**需用户先拍的定位取舍（阻塞方向选择）**
- README 的 intended reader 在「generic fork 部署者」（your-org 占位、中性默认）与「本产线运维者」（腾讯服务器行、本部署 cron 排期）之间摇摆——决定 DB-sync/生产内容留 README 还是只留 services.md。
- install.sh 行为长段说明（README §install 依赖表两段 vs services.md 同语义整段）与响应式 UX 细节（README vs ux-contract.md RS-1/RS-2）各自的 home 归属。

**机械可修（方向单一）**
- 「U4 发现的 homepage 假阳性已修复」等修复史/评审编号语言混入用户文档（README 性能监控节、服务表 remediate 行）——读者只需当前 gate 步骤。
- performance-remediate 启用 gate 条件在 README×2 + services.md×2 + monitoring-alerting.md 至少五处副本。
- 快速开始步骤 3–4 的 8 条命令均无可观察成功信号/失败去处；步骤 gate 的样本文件位置（journey-samples.jsonl）未指给读者；「确认已配 LLM API Key」无非交互视角的检查命令。
- 「从零部署最小配置」与「站点身份与域名」相邻代码块重复同一组 `AI_RADAR_SITE_*` 变量。
- WeWe RSS 移除史两句、纯客户端预取括注等无行动内容可删。
- 性能监控引言段（~30 行内部采样机制）复述 monitoring-alerting.md 权威内容，可压至 ~10 行。
- 项目差异化定位（借鉴 AIHOT、人人可自部署）埋在文末致谢，入口读者在 clone 前看不到。

**services.md / 服务清单侧**
- 服务器侧生产栈（install-server.sh 的 serve/db-apply/alert、双槽 serve@8000/8001）不在任何服务清单；make-live 路径（git push tencent → post-receive → deploy_code.py）无文档。

**Resolution（裁决 2026-08-20 / 执行 2026-08-20）**：定位之争由用户裁决为**「通用 fork 部署者」**，README 已按此改写并落在本轮工作树（未提交）——开头新增「适合谁」段（明确不是多租户 SaaS、一份部署一个信源口径）、把差异化定位从文末致谢提到开头、把生产专属内容（腾讯 8010/8001、`news.aiplanet.live` 实例地址）替换为占位形态（`http://127.0.0.1:8000`、`https://your-site.example.com`）。同轮清掉的机械项（逐条复核）：快速开始步骤 3–5 各补了可观察成功读数（`migrated …` / `reloaded <N> sources` / `OK <source_id> fetched=…` / `stored=N alerts_sent=M`）与新库首轮 `selected=0` 的处置；「U4 发现的假阳性已修复」等修复史语言已删；性能监控引言由约 30 行压到 5 行；`AI_RADAR_SITE_*` 重复代码块已合并为一处（复核：README 内 `AI_RADAR_SITE_` 仅剩 L152-156 一个块 + L162 一处散文引用）；WeWe RSS 移除史已删（复核：README 内 `WeWe` 命中 0）。

**未随本条关闭、已另立窄条**：清单末尾「services.md / 服务清单侧」那条（服务器侧生产栈 install-server.sh、双槽 serve@8000/8001、make-live 路径 git push tencent → post-receive → deploy_code.py 不在任何服务清单）**未修**——复核 `docs/operations/services.md` 内 `install-server|deploy_code|post-receive|8001` 命中 0。它已作为独立 [open] 条目留在 `docs-quality.md`。

## [resolved] 2026-08-10：architecture.md 模块树多处过时（本 plan 范围外，独立审查发现）

- Type: content currency · Priority: low · Discovered: 2026-08-10, sync-docs 重审 wave

模块树漏 `runtime_env.py`、`web/routes/daily_metrics.py`；`performance/` 仅列 3/9 模块且无省略标记；`web/templates` 仅列 4/16（漏掉路由表自己引用的 hot/changelog/more/admin/admin_usage/bookmarks/wechat_404 与 partials）；`web/static` 漏 `wechat-icon.svg`。另：归档债——`general.md`、`ux-contract-issues.md`、`ux-issues.md` 与 `harness-issues.md` 均有终态条目未按协议 §4.8 移入 `archive/closed.md`。**2026-08-20 更新**：前三个文件已于本轮完成归档（general 20 条、ux-contract-issues 9 条、ux-issues 3 条），`harness-issues.md` 由并行的另一个写入者处理，本条剩余范围仅该文件。

**Resolution（2026-08-20）**：逐项复核，本条列出的六项全部已闭合，故整条归档。复核读数（2026-08-20，对当前工作树）：

| 原 finding | 复核读数 |
|---|---|
| 模块树漏 `runtime_env.py` | `docs/architecture.md:28` 已有该行（含 ADR-003 指针） |
| 模块树漏 `web/routes/daily_metrics.py` | `docs/architecture.md:136` 已有该行 |
| `performance/` 仅列 3/9 | 现列 9 条（browser_probe / http_probe / journey_monitor / budgets / config / context / stage_ledger / runner / remediation）；`ls src/airadar/performance/` 除 `__init__.py` 与 `__pycache__` 外恰为这 9 个 `.py`，无遗漏 |
| `web/templates` 仅列 4/16 | 现列 18 条；`ls web/templates \| wc -l` = 18，逐一对齐（含 hot/changelog/more/admin/admin_usage/bookmarks/wechat_404 与全部 `_*` partials 及 `_wechat_inline_style.css`） |
| `web/static` 漏 `wechat-icon.svg` | `docs/architecture.md:165` 已有该行 |
| 归档债剩余范围（`harness-issues.md`） | 该文件现存 11 个 `##` 条目全部为 `[open]`（含显式声明"部分已修故仍留 open"的 H8），无终态条目滞留；终态的 H2/H5/H7/H10/H13 已在本文件「2026-08-20 harness-issues.md lifecycle 清理迁入」批次内 |

**未随本条覆盖**：`architecture.md` 其它维度（描述文字是否与实现一致、数据流叙述是否过时）不在本条原始范围内，本次也未核。
