# docs/CLAUDE.md -- ai-radar 文档索引与协议规则

> Mutable snapshot. docs/ 下新增、重命名或删除文档时同步更新本索引。
>
> 协议正文：`~/.claude/references/docs-organization-protocol.md`
> 格式模板：`~/.claude/references/docs-format-templates.md`

---

## 文档索引

### 根目录 [User]

| 文件 | 性质 | 说明 |
|---|---|---|
| [README.md](../README.md) | Mutable snapshot | 产品说明、安装、使用 |
| [CHANGELOG.md](../CHANGELOG.md) | Append-only (newest first) | 用户可感知的变更记录 |

### docs/ [Developer]

| 文件 | 性质 | 说明 |
|---|---|---|
| [architecture.md](architecture.md) | Mutable snapshot | 系统模块结构、分层、数据流、数据库设计、Web 层、关键抽象 |

### docs/operations/ [User]

运维入口——系统在跑什么、怎么管理、怎么验证。

| 文件 | 说明 |
|---|---|
| [services.md](operations/services.md) | 服务清单 + 自启机制 + Instructions 位置 + 验证命令 + Cloudflare tunnel / Cache Rule 等 repo 外基础设施 |
| [monitoring-alerting.md](operations/monitoring-alerting.md) | `/admin` 运维 dashboard、A1–A6 与 D3 告警、周报、飞书 webhook、用户旅程性能监控 runbook |
| [wechat-ingestion.md](operations/wechat-ingestion.md) | 微信公众号摄取：Mp2RSS 接入、`MP2RSS_FEED_URL` 配置、真名头像 backfill、迁移留尾记录 |
| [ai-assistant-integration.md](operations/ai-assistant-integration.md) | 可选外部 summary-agent 集成：启用条件、`./run.sh interpret` 契约、默认关闭语义 |
| [db-slimming.md](operations/db-slimming.md) | `radar.db` 瘦身：`summary_json` 常驻保留、`admin db retain`/`admin db slim`、VACUUM 仅用于低频磁盘维护且不是 DB sync 前置、Mac 主库 apply+回滚 |

### docs/references/ [Developer]

操作参考——主文档需要引用但不适合放入 README 的细节步骤。

| 文件 | 说明 |
|---|---|
| [wechat-sources.md](references/wechat-sources.md) | 旧 WeWe RSS 微信源添加流程（已停用，微信摄取现走 Mp2RSS，见 operations/wechat-ingestion.md） |
| [web-contract-golden.md](references/web-contract-golden.md) | 行为等价 Web 重构的冻结 DB + HTTP golden 使用边界、命令与 re-baseline 规则 |

### docs/prd/ [Developer]

产品需求定义。只读参考——不在日常开发中修改，变更走 ADR 或新版 PRD。

| 文件 | 说明 |
|---|---|
| [VISION.md](prd/VISION.md) | 产品愿景与阶段路线图（v0.1 草案）；§4 核心原则 BINDING |
| [PRD_v0.md](prd/PRD_v0.md) | v0 MVP 实施 PRD：数据流、schema、接口契约、验收标准 |

### docs/contracts/ [Developer]

产品行为契约——用户可观察行为的 hard spec。

| 文件 | 说明 |
|---|---|
| [ux-contract.md](contracts/ux-contract.md) | AI Radar 对用户承诺的可观察行为：Personas、Surfaces、Journeys、Features、Quality Bar |

### docs/issues/ [Agent]

问题跟踪——agent 驱动的轻量 issue tracker，按 domain 分文件。

| 文件 | 说明 |
|---|---|
| [README.md](issues/README.md) | Domain 索引 |
| [ux-issues.md](issues/ux-issues.md) | 端到端测试发现的产品 UX 问题（contract 在实际产品中被 broken） |
| [ux-contract-issues.md](issues/ux-contract-issues.md) | contract 本身的问题（定义缺失 / 不准确 / 过时）；现存历史关闭项的归档债见 docs-quality.md |
| [deploy.md](issues/deploy.md) | 部署与 DB 同步链路的运维问题（sync/apply/cron/verifier，含影响其验收的测试基线） |
| [docs-quality.md](issues/docs-quality.md) | 文档自身的质量债（README 定位/重复/可观察性等审查遗留） |
| [cost-observability.md](issues/cost-observability.md) | LLM 成本计量、定价、报告与告警消费面的未闭合项；金额口径只覆盖 `llm_usage` 记录行 |
| [general.md](issues/general.md) | 项目级未分类问题（reliability / 工具链 / 文档错位等） |
| [harness-issues.md](issues/harness-issues.md) | Agent harness、wrapper、hook 或 skill 行为问题 |
| [archive/closed.md](issues/archive/closed.md) | 已 resolved / wontfix 的历史 issue 归档 |

### docs/adr/ [Developer]

架构决策记录——取舍、理由、被否的方案。每条决策独立文件。

| 文件 | 说明 |
|---|---|
| [README.md](adr/README.md) | ADR 索引 |
| [001-deterministic-source-brand-tags.md](adr/001-deterministic-source-brand-tags.md) | 标签生成优先使用确定性 source/brand 标签 |
| [002-deepseek-v4-flash-prefilter.md](adr/002-deepseek-v4-flash-prefilter.md) | Prefilter 模型选用 deepseek-v4-flash 并禁用 thinking |
| [003-dual-dotenv-loader.md](adr/003-dual-dotenv-loader.md) | Runtime env loader 读取双层 .env 文件 |
| [004-n-plus-one-optimization-scope.md](adr/004-n-plus-one-optimization-scope.md) | N+1 优化仅限 timeline 路由 |
| [005-timeline-exact-count-with-cached-cte.md](adr/005-timeline-exact-count-with-cached-cte.md) | Timeline 真实总数计数 + CTE 公式 + 进程内 LRU 缓存 |
| [006-curated-archive-mode.md](adr/006-curated-archive-mode.md) | 精选页改为跨 run 去重的累积归档（复用 ADR-005 真实计数 pattern） |
| [007-interpret-via-ai-assistant-summarizer.md](adr/007-interpret-via-ai-assistant-summarizer.md) | 微信文章解读复用 ai-assistant summarizer，save_decision 作单一闸门 |
| [008-alert-severity-lifecycles.md](adr/008-alert-severity-lifecycles.md) | 告警按 severity 维护独立 lifecycle，并保留 page-preferring 兼容投影 |
| [009-alert-notification-ledger.md](adr/009-alert-notification-ledger.md) | 用有界、fail-open 的 JSONL 记录已送达告警通知 |
| [010-db-slimming-clear-regenerable-cache.md](adr/010-db-slimming-clear-regenerable-cache.md) | radar.db 瘦身选清可再生 summary 缓存（Option A）+ 常驻保留 + 历史 digest TTL |
| [011-perf-idle-only-probing.md](adr/011-perf-idle-only-probing.md) | PERF 改为 idle-only 探测并用 per-file launchd 调度（仅 supersede ADR-008 的 PERF F1/F4） |
| [012-single-dom-mobile-layer.md](adr/012-single-dom-mobile-layer.md) | 移动层用单套 DOM + media query 重塑，不复制参考站的双 DOM |
| [013-db-sync-cron-agent-socket-auth.md](adr/013-db-sync-cron-agent-socket-auth.md) | DB sync 自动化用 launchd ssh-agent socket 发现做 cron SSH 认证 |
| [014-ship-base-only-db-and-rebuild-fts.md](adr/014-ship-base-only-db-and-rebuild-fts.md) | DB sync 传输持久 base-only artifact，并在服务器候选槽重建与验证 FTS |
| [015-interval-aware-supplement-pricing.md](adr/015-interval-aware-supplement-pricing.md) | Supplement tariff 按 usage 时间选有效区间，防止调价静默重算历史 |
| [016-rollout-compatible-deprecated-cost-column.md](adr/016-rollout-compatible-deprecated-cost-column.md) | 废弃成本列在滚动发布期接受但不消费旧 numeric，拒绝写入显式失败 |
| [017-preserve-paid-results-on-metering-failure.md](adr/017-preserve-paid-results-on-metering-failure.md) | 已付费模型结果在计量失败时仍须保留，计量错误单独显式暴露 |
| [018-normalize-a6-to-current-tariff.md](adr/018-normalize-a6-to-current-tariff.md) | A6 用同一现行 tariff snapshot 重算当前窗与基线，只检测量结构突变 |
| [019-reference-interpret-unit-cost-to-comparable-window.md](adr/019-reference-interpret-unit-cost-to-comparable-window.md) | 单篇解读成本用前一等长窗口作参考 |
| [020-normalize-cost-comparisons-to-cache-all-miss.md](adr/020-normalize-cost-comparisons-to-cache-all-miss.md) | A6、周报与管理页的跨窗成本统一按 cache 未命中比较 |
| [021-audit-alert-delivery-and-suppression-decisions.md](adr/021-audit-alert-delivery-and-suppression-decisions.md) | 告警 ledger 区分投递、D3 生命周期与 INTERNAL 抑制决策 |
| [022-evaluate-a6-in-progress-cost-as-lower-bound.md](adr/022-evaluate-a6-in-progress-cost-as-lower-bound.md) | A6 在途成本下界可触发告警，但不能证明恢复 |
| [023-define-recorded-row-measurement-scope.md](adr/023-define-recorded-row-measurement-scope.md) | 从 `llm_usage` 记录行派生的加总量是下界，cohort 统计只描述已记录调用 |
| [024-shadow-wechat-admin-discovery-before-mp2rss-cutover.md](adr/024-shadow-wechat-admin-discovery-before-mp2rss-cutover.md) | 以默认关闭的 shadow canary 验证公众号后台发现适配器后再替换 Mp2RSS；cadence/page-size 已由 ADR-025 部分 supersede |
| [025-conservative-wechat-discovery-probe-defaults.md](adr/025-conservative-wechat-discovery-probe-defaults.md) | 单账号人工 probe 暂用每天一次、每页 5 篇的保守默认，scheduled canary 启用前仍需 live 验证 |
| [026-explicit-windowed-mp2rss-shadow-comparison.md](adr/026-explicit-windowed-mp2rss-shadow-comparison.md) | 用显式只读命令按账号、attempt 与观察窗比较 shadow 和 Mp2RSS，证据不足时返回不可比较 |
| [027-self-describing-recoverable-wechat-shadow-page-size.md](adr/027-self-describing-recoverable-wechat-shadow-page-size.md) | shadow 页大小增加显式来源，v1/v2 与部分迁移状态可安全接手，并串行化首次发现判定 |
| [028-resolve-wechat-fakeid-before-shadow-probe.md](adr/028-resolve-wechat-fakeid-before-shadow-probe.md) | 先解析并一次性消费已验证 fakeid，再执行微信 shadow probe；其 searchbiz 验证语义已由 ADR-040 supersede |
| [029-single-source-wechat-discovery-ledgers.md](adr/029-single-source-wechat-discovery-ledgers.md) | 微信发现 ledger 采用单一权威并保持崩溃可恢复 |
| [030-remove-derived-wechat-discovery-fields.md](adr/030-remove-derived-wechat-discovery-fields.md) | schema v6 移除可派生字段并用 config v3 明示 public biz |
| [031-preserve-only-provable-wechat-migration-facts.md](adr/031-preserve-only-provable-wechat-migration-facts.md) | 微信历史 ledger 迁移只保留可证明事实，缺 provenance 时降级、矛盾关系回滚 |
| [032-reject-duplicate-urls-before-wechat-shadow-comparison.md](adr/032-reject-duplicate-urls-before-wechat-shadow-comparison.md) | 微信后台单次响应含重复 URL 时显式失败，避免把去重快照误作完整窗口证据 |
| [033-version-weread-canary-shelf-request-evidence.md](adr/033-version-weread-canary-shelf-request-evidence.md) | 微信读书只读 canary 证据升为 v2，并显式保留书架请求状态与条件式平台错误码 |
| [034-use-a-single-auditable-weread-canary-evidence-ledger.md](adr/034-use-a-single-auditable-weread-canary-evidence-ledger.md) | 微信读书只读 canary v3 以单一校验器、请求账本与候选内嵌观察状态形成可审计证据 |
| [035-bind-weread-canary-evidence-to-targets-producer-and-relations.md](adr/035-bind-weread-canary-evidence-to-targets-producer-and-relations.md) | 微信读书只读 canary v4 将证据绑定到目标、源码摘要和经重算的身份关系 |
| [036-preserve-public-page-observation-outcomes.md](adr/036-preserve-public-page-observation-outcomes.md) | 微信读书只读 canary v5 区分已观察页面与客户端失败，并闭合请求顺序和返回页关系 |
| [037-retain-observed-captcha-target-at-attempt-end.md](adr/037-retain-observed-captcha-target-at-attempt-end.md) | 微信读书只读 canary v6 在 attempt 结束时保留验证码 target，并闭合失败关系 |
| [038-observe-weread-dynamic-header-presence-without-replay.md](adr/038-observe-weread-dynamic-header-presence-without-replay.md) | 微信读书只读 canary v7 仅观察既有列表请求的动态鉴权头名称是否出现，不捕获或回放头值 |
| [039-route-news-through-edgeone-dns-only-cname.md](adr/039-route-news-through-edgeone-dns-only-cname.md) | `news.aiplanet.live` 通过 DNS-only CNAME 接入 EdgeOne，并以内联 CSS 缩短 `/wechat` 冷首屏链路 |
| [040-verify-provisional-searchbiz-mapping-with-article-url-biz.md](adr/040-verify-provisional-searchbiz-mapping-with-article-url-biz.md) | searchbiz 只产 provisional mapping，再由返回文章 URL 的 public biz 完成身份验证 |
| [041-version-wechat-discovery-invariant-hardening.md](adr/041-version-wechat-discovery-invariant-hardening.md) | 微信 discovery 不变量加固以 schema v8 发布，不原地改写已落地 v7 |
| [042-isolate-production-deploy-commit-from-local-main.md](adr/042-isolate-production-deploy-commit-from-local-main.md) | 从本地 `main` 的未发布提交中隔离生产 deployment commit，并用普通 revert 与后续 merge 收口 |
| [043-waive-manual-wechat-probe-cooldown-once.md](adr/043-waive-manual-wechat-probe-cooldown-once.md) | 对一次获授权微信后台 probe 豁免本地 1440 分钟冷却 |
| [044-persist-wechat-platform-error-ret.md](adr/044-persist-wechat-platform-error-ret.md) | schema v9 持久化后台 exact ret，区分平台拒绝与可证明频控 |
| [045-require-integer-platform-ret-and-evidence-backed-cooldown.md](adr/045-require-integer-platform-ret-and-evidence-backed-cooldown.md) | schema v10 只接受整数后台错误码，特殊冷却仅由已记录频控证据触发 |
| [046-resolve-x-user-id-in-a-separate-fetch-round.md](adr/046-resolve-x-user-id-in-a-separate-fetch-round.md) | X user ID 解析与 timeline 读取分轮执行 |
| [047-use-controlled-original-web-lists-for-aihot-source-alignment.md](adr/047-use-controlled-original-web-lists-for-aihot-source-alignment.md) | AIHOT 来源对齐使用受控的原始 Web/API 列表 |
| [048-require-semantic-live-validation-receipts.md](adr/048-require-semantic-live-validation-receipts.md) | 用语义完整的收据验收来源读取能力 |
| [049-keep-human-audit-summaries-and-remove-duplicate-authorities.md](adr/049-keep-human-audit-summaries-and-remove-duplicate-authorities.md) | 保留人读审计摘要并移除重复权威 |
| [050-allow-versioned-data-configs-through-code-deploy.md](adr/050-allow-versioned-data-configs-through-code-deploy.md) | 代码部署仅放行已核验的版本化 data 配置 |
| [051-share-timeline-source-visibility-with-the-fts-oracle.md](adr/051-share-timeline-source-visibility-with-the-fts-oracle.md) | 由 timeline 单一持有 source visibility 谓词，FTS oracle 复用它 |
| [052-hold-pipeline-mutex-with-kernel-flock.md](adr/052-hold-pipeline-mutex-with-kernel-flock.md) | pipeline 互斥改由内核 flock 持有，删除用户态判活与 stale reclaim |
| [053-retry-startup-migration-on-database-locked.md](adr/053-retry-startup-migration-on-database-locked.md) | web 启动 migration 遇 database is locked 时有限退避重试 |

### docs/experiences/ [Agent]

开发经验与坑点——让后续 agent 不用重新踩坑。按 topic 分文件。

| 文件 | 说明 |
|---|---|
| [README.md](experiences/README.md) | Topic 索引 |
| [performance.md](experiences/performance.md) | 性能优化相关（索引策略、查询优化） |
| [frontend.md](experiences/frontend.md) | 前端开发相关（时区、渲染） |
| [dev-environment.md](experiences/dev-environment.md) | 开发环境配置和工具使用 |
| [deployment.md](experiences/deployment.md) | 部署和调度相关的坑点和 pattern |
| [llm-pipeline.md](experiences/llm-pipeline.md) | LLM 调用、模型选型、prompt 调优、eval 管线 |
| [integration.md](experiences/integration.md) | 跨系统 / 外部工具接口约定（ai-assistant、summarize.sh、KB 写入器） |

### docs/plans/ [Developer]

已完成 plan 的归档。每个 plan 一个子目录。

| 目录 | 说明 |
|---|---|
| [20260812-aihot-original-source-alignment/](plans/20260812-aihot-original-source-alignment/) | AIHOT 原始来源对齐：161 个主时间线来源、可选 WeChat 隔离、语义收据与浏览器升级验收 |
| [20260810-llm-cost-observability/](plans/20260810-llm-cost-observability/) | 查询时派生成本、告警/周报消费面、cache split 与计量失败 paid-result 保护；金额加总只表示记录行下界 |
| [20260809-fts-rebuild-sync/](plans/20260809-fts-rebuild-sync/) | DB 跨洋同步改造：持久 base-only replica + 服务器候选槽重建 FTS，稳态 16.39MB<20MB、零停机 3500/3500 |
| [20260720-db-slimming/](plans/20260720-db-slimming/) | radar.db 瘦身：清可再生 `summary_json` 缓存 + VACUUM + 常驻保留（Option A），生产 2.28GB→1.495GB |
| [20260607-wechat-read-original-link/](plans/20260607-wechat-read-original-link/) | `/wechat` 详情页与列表卡片新增显式「访问原文」链接跳转公众号原文 |
| [20260607-wechat-interpretation-search/](plans/20260607-wechat-interpretation-search/) | 微信文章解读页 `/wechat` 支持按标题、公众号、摘要和标签搜索 |
| [20260604-2104-curated-archive-ux-test/](plans/20260604-2104-curated-archive-ux-test/) | 精选页改为累积归档 + 分页的 UX 契约测试与修复 |
| [20260604-1156-ux-contract-ux-test/](plans/20260604-1156-ux-contract-ux-test/) | 分页升级（数字页码 + 省略号）的 UX 契约测试与修复 |
| [20260602-wechat-article-interpretation/](plans/20260602-wechat-article-interpretation/) | 微信文章解读 tab、ai-assistant summarize 复用、KB 回写与旧 WeWe 源移除 |
| [20260602-2034-ux-contract-ux-test/](plans/20260602-2034-ux-contract-ux-test/) | 微信解读功能的 UX 契约测试与修复（侧栏入口、列表/分页、详情渲染） |
| [20260601-monitoring-alerting/](plans/20260601-monitoring-alerting/) | 运维监控 dashboard + A1-A4 飞书告警 MVP |
| [20260531-wechat-search-usability/](plans/20260531-wechat-search-usability/) | 中文/微信公众号源搜索可用性修复（backfill 可见性、来源优先、多源轮转、简繁互通） |
| [20260529-timeline-search-source-author/](plans/20260529-timeline-search-source-author/) | Timeline/curated 搜索覆盖来源名、作者和中文标题 |
| [20260528-ssr-preload/](plans/20260528-ssr-preload/) | SSR preload 首屏加载优化（/ 和 /all 无可感知 spinner） |
| [20260528-wechat-oa-ingestion/](plans/20260528-wechat-oa-ingestion/) | 微信公众号信源接入（Playwright 抓全文，后迁移 Mp2RSS） |
| [20260528-0640-ux-contract-ux-test/](plans/20260528-0640-ux-contract-ux-test/) | UX 契约建立 + 测试修复（关于页 GitHub 链接、时间线无分数条目） |
| [20260524-timeline-perf/](plans/20260524-timeline-perf/) | Timeline API 性能优化（/all TTFB 14s -> <1s） |
| [20260515-pipeline-scheduler/](plans/20260515-pipeline-scheduler/) | 自动化增量数据抓取流水线（pipeline.sh + cron 15min 调度） |

---

## 尚未创建的文档类型

以下类型在协议中定义但本项目尚未创建，按需启用：

| 类型 | 路径 | 创建时机 |
|---|---|---|
| contracts/ux-test-patterns.md | `docs/contracts/ux-test-patterns.md` | 测试中发现值得长期留意的 pattern 时 |

---

## 读写触发

### 何时读 docs/

| 场景 | 读什么 |
|---|---|
| 新 session 第一次接触项目 | 本文件（索引）-> architecture.md（系统结构）-> 按需深入 |
| 设计新功能或架构变更前 | architecture.md（模块定位）+ prd/（需求边界）+ contracts/（行为承诺） |
| 执行产品测试 / UX 测试前 | contracts/ux-contract.md |
| 规划"接下来做什么" | issues/（待解决问题） |
| 遇到报错或"感觉有坑"时 | experiences/（按文件名选 topic） |
| 做新的架构或 API 设计决策前 | adr/README.md（索引）-> 相关 ADR |
| 想知道"系统在跑什么 / 谁拉起的 / 怎么自启" | operations/services.md（服务清单总览） |
| 修改 Web 输出但要求结构变化、行为等价 | references/web-contract-golden.md |

### 何时写 docs/

| 场景 | 写什么 |
|---|---|
| 产品的用户可感知行为变化 | contracts/ux-contract.md + CHANGELOG.md |
| 做了非平凡的设计决策 | adr/（新建 ADR 文件 + 更新 README.md 索引） |
| 花了非平凡时间解决一个问题 | experiences/（按 topic 写入对应文件 + 更新 README.md 索引） |
| 发现值得跟踪但不属于当前任务的问题 | issues/ 对应 domain 文件 |
| 新增/移除长期运行的服务，或自启机制变化 | operations/services.md（更新清单 + 验证步骤） |
| 长任务完成后提升 task 产物 | 按提升路径分流到对应文档 |
| docs/ 下新增、重命名或删除文档 | 本文件（更新索引） |
