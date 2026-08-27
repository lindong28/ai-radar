# Architecture Decision Records

> 每条决策独立文件，编号递增。Status: accepted / superseded / deprecated.

| # | Title | Status | Date |
|---|---|---|---|
| [001](./001-deterministic-source-brand-tags.md) | 标签生成优先使用确定性 source/brand 标签 | accepted | 2026-05-12 |
| [002](./002-deepseek-v4-flash-prefilter.md) | Prefilter 模型选用 deepseek-v4-flash 并禁用 thinking | accepted | 2026-05-15 |
| [003](./003-dual-dotenv-loader.md) | Runtime env loader 读取双层 .env 文件 | accepted | 2026-05-15 |
| [004](./004-n-plus-one-optimization-scope.md) | N+1 优化仅限 timeline 路由 | accepted；前提部分失效（hot 复用 curated archive 路径），见 ADR-060 | 2026-05-24 |
| [005](./005-timeline-exact-count-with-cached-cte.md) | Timeline 真实总数计数 + CTE 公式 + 进程内 LRU 缓存 | accepted | 2026-06-04 |
| [006](./006-curated-archive-mode.md) | 精选页改为跨 run 去重的累积归档（复用 ADR-005 真实计数 pattern） | accepted | 2026-06-04 |
| [007](./007-interpret-via-ai-assistant-summarizer.md) | 微信文章解读复用 ai-assistant summarizer，save_decision 作单一闸门 | accepted；「interpret 回填无法并发」issue 已 resolved 移入 archive，见文末修订记录 | 2026-06-06 |
| [008](./008-alert-severity-lifecycles.md) | 告警按 severity 维护独立 lifecycle | accepted; PERF F1/F4 superseded by ADR-011 | 2026-07-22 |
| [009](./009-alert-notification-ledger.md) | 用有界 JSONL 记录已送达告警通知 | accepted; scope superseded by ADR-021 | 2026-07-22 |
| [010](./010-db-slimming-clear-regenerable-cache.md) | radar.db 瘦身选清可再生 summary 缓存（Option A）+ 常驻保留 + 历史 digest TTL | accepted | 2026-07-22 |
| [011](./011-perf-idle-only-probing.md) | PERF 改为 idle-only 探测并用 per-file launchd 调度 | accepted; supersedes ADR-008 PERF F1/F4 only | 2026-07-26 |
| [012](./012-single-dom-mobile-layer.md) | 移动层用单套 DOM + media query 重塑，不复制参考站的双 DOM | accepted | 2026-08-03 |
| [013](./013-db-sync-cron-agent-socket-auth.md) | DB sync 自动化用 launchd ssh-agent socket 发现做 cron SSH 认证 | accepted | 2026-08-09 |
| [014](./014-ship-base-only-db-and-rebuild-fts.md) | 传输 base-only DB 并在服务器候选槽重建 FTS | accepted | 2026-08-10 |
| [015](./015-interval-aware-supplement-pricing.md) | Supplement 定价按调用时间选择有效区间 | accepted | 2026-08-11 |
| [016](./016-rollout-compatible-deprecated-cost-column.md) | 废弃成本列在滚动发布期接受但不消费旧数值 | accepted | 2026-08-11 |
| [017](./017-preserve-paid-results-on-metering-failure.md) | 计量失败不得伪装成模型失败 | accepted | 2026-08-11 |
| [018](./018-normalize-a6-to-current-tariff.md) | A6 只比较现行可报价 cohort 的量结构成本 | accepted; cache gates superseded by ADR-020 | 2026-08-11 |
| [019](./019-reference-interpret-unit-cost-to-comparable-window.md) | 单篇解读成本只与自身可比前窗对照 | accepted; interpret cache gate superseded by ADR-020 | 2026-08-11 |
| [020](./020-normalize-cost-comparisons-to-cache-all-miss.md) | 成本比较统一归一化为 cache 全未命中 | accepted; supersedes ADR-018/019 cache gates; in-progress handling superseded by ADR-022 | 2026-08-11 |
| [021](./021-audit-alert-delivery-and-suppression-decisions.md) | 告警事件 ledger 同时审计投递与合并抑制决策 | accepted; supersedes ADR-009 scope | 2026-08-11 |
| [022](./022-evaluate-a6-in-progress-cost-as-lower-bound.md) | A6 在途成本作为下界继续正向评估 | accepted; supersedes ADR-020 in-progress handling | 2026-08-11 |
| [023](./023-define-recorded-row-measurement-scope.md) | 以记录行为 LLM 用量派生指标定义测量范围 | accepted | 2026-08-12 |
| [024](./024-shadow-wechat-admin-discovery-before-mp2rss-cutover.md) | 以 shadow canary 验证公众号后台发现适配器后再替换 Mp2RSS | deprecated（后台 family 平台级不可用，见 ADR-061）; cadence/page-size superseded by ADR-025; identity mapping 与旧 evidence 语义 superseded by ADR-028 | 2026-08-13 |
| [025](./025-conservative-wechat-discovery-probe-defaults.md) | 公众号后台发现探测采用低频、小页的临时保守默认 | deprecated（见 ADR-061）; supersedes ADR-024 cadence/page-size only | 2026-08-13 |
| [026](./026-explicit-windowed-mp2rss-shadow-comparison.md) | 以显式只读命令执行窗口化 Mp2RSS shadow 对比 | deprecated（见 ADR-061）; persistence evolution superseded by ADR-027 | 2026-08-13 |
| [027](./027-self-describing-recoverable-wechat-shadow-page-size.md) | 让微信 shadow 页大小记录自描述且可恢复迁移 | deprecated（见 ADR-061）; supersedes ADR-026 persistence evolution only | 2026-08-13 |
| [028](./028-resolve-wechat-fakeid-before-shadow-probe.md) | 先解析并一次性消费已验证 fakeid，再执行微信 shadow probe | deprecated（见 ADR-061）; 仅 searchbiz 验证语义 superseded by ADR-040 | 2026-08-13 |
| [029](./029-single-source-wechat-discovery-ledgers.md) | 微信发现 ledger 采用单一权威并保持崩溃可恢复 | deprecated（见 ADR-061）; duplicated fields superseded by ADR-030 | 2026-08-13 |
| [030](./030-remove-derived-wechat-discovery-fields.md) | schema v6 移除可派生字段、统一成功终态，并以 config v3 明示 public biz | deprecated（见 ADR-061）; supersedes ADR-029 duplicated fields | 2026-08-13 |
| [031](./031-preserve-only-provable-wechat-migration-facts.md) | 微信历史 ledger 迁移只保留可证明事实，缺 provenance 时降级、矛盾关系整笔回滚 | deprecated（见 ADR-061）; clarifies ADR-028 through ADR-030 migration semantics | 2026-08-13 |
| [032](./032-reject-duplicate-urls-before-wechat-shadow-comparison.md) | 微信后台单次响应含重复 URL 时显式失败，禁止去重后误判窗口覆盖 | deprecated（见 ADR-061）; clarifies ADR-026 and ADR-030 snapshot semantics | 2026-08-13 |
| [033](./033-version-weread-canary-shelf-request-evidence.md) | 微信读书 canary 证据升为 v2 并保留书架请求平台错误码 | deprecated（canary 线随替代计划停止，见 ADR-061）; superseded by ADR-034 for new evidence | 2026-08-13 |
| [034](./034-use-a-single-auditable-weread-canary-evidence-ledger.md) | 微信读书 canary v3 采用单一可审计请求与候选证据权威 | deprecated（canary 线随替代计划停止，见 ADR-061）; supersedes ADR-033 / superseded by ADR-035 for new evidence | 2026-08-13 |
| [035](./035-bind-weread-canary-evidence-to-targets-producer-and-relations.md) | 微信读书 canary v4 绑定请求目标、生产者源码与身份关系 | deprecated（canary 线随替代计划停止，见 ADR-061）; supersedes ADR-034 / superseded by ADR-036 for new evidence | 2026-08-13 |
| [036](./036-preserve-public-page-observation-outcomes.md) | 微信读书 canary v5 区分已观察页面与客户端失败，并闭合请求顺序和返回页关系 | deprecated（canary 线随替代计划停止，见 ADR-061）; supersedes ADR-035 / superseded by ADR-037 for new evidence | 2026-08-13 |
| [037](./037-retain-observed-captcha-target-at-attempt-end.md) | 微信读书 canary v6 在 attempt 结束时保留已观察到的验证码 target，并闭合失败关系 | deprecated（canary 线随替代计划停止，见 ADR-061）; supersedes ADR-036 / superseded by ADR-038 for new evidence | 2026-08-13 |
| [038](./038-observe-weread-dynamic-header-presence-without-replay.md) | 微信读书 canary v7 仅观察既有列表请求的动态鉴权头名称是否出现，不捕获或回放头值 | deprecated（canary 线随替代计划停止，见 ADR-061）; supersedes ADR-037 for new evidence | 2026-08-13 |
| [039](./039-route-news-through-edgeone-dns-only-cname.md) | 通过 DNS-only CNAME 将 news 入口接入 EdgeOne | accepted | 2026-08-13 |
| [040](./040-verify-provisional-searchbiz-mapping-with-article-url-biz.md) | searchbiz 只产 provisional mapping，再由返回文章 URL 的 public biz 完成身份验证 | deprecated（见 ADR-061）; supersedes ADR-028 searchbiz verification semantics | 2026-08-14 |
| [041](./041-version-wechat-discovery-invariant-hardening.md) | 微信 discovery 不变量加固以 schema v8 发布，不原地改写已落地 v7 | accepted；依赖后台 family 的部分已随 ADR-061 失效，schema 演化与证据纪律仍成立; clarifies ADR-029 through ADR-031 and ADR-040 | 2026-08-14 |
| [042](./042-isolate-production-deploy-commit-from-local-main.md) | 从本地 main 的未发布提交中隔离生产部署 commit | accepted | 2026-08-16 |
| [043](./043-waive-manual-wechat-probe-cooldown-once.md) | 对一次获授权微信后台 probe 豁免本地 1440 分钟冷却 | accepted；依赖后台 family 的部分已随 ADR-061 失效，一次性豁免的记账纪律仍成立; one-shot exception to ADR-025 only | 2026-08-16 |
| [044](./044-persist-wechat-platform-error-ret.md) | schema v9 持久化后台 exact ret，区分平台拒绝与可证明频控 | accepted；依赖后台 family 的部分已随 ADR-061 失效，schema 演化与证据纪律仍成立; clarifies ADR-025 failure and cooldown semantics | 2026-08-16 |
| [045](./045-require-integer-platform-ret-and-evidence-backed-cooldown.md) | schema v10 只接受整数平台错误码，特殊冷却仅由已记录频控证据触发 | accepted；依赖后台 family 的部分已随 ADR-061 失效，schema 演化与证据纪律仍成立; clarifies ADR-025 and ADR-044 | 2026-08-16 |
| [046](./046-resolve-x-user-id-in-a-separate-fetch-round.md) | X user ID 解析与 timeline 读取分轮执行 | accepted | 2026-08-13 |
| [047](./047-use-controlled-original-web-lists-for-aihot-source-alignment.md) | AIHOT 来源对齐使用受控的原始 Web/API 列表 | accepted | 2026-08-13 |
| [048](./048-require-semantic-live-validation-receipts.md) | 用语义完整的收据验收来源读取能力 | accepted | 2026-08-13 |
| [049](./049-keep-human-audit-summaries-and-remove-duplicate-authorities.md) | 保留人读审计摘要并移除重复权威 | accepted | 2026-08-13 |
| [050](./050-allow-versioned-data-configs-through-code-deploy.md) | 代码部署仅放行已核验的版本化 data 配置 | accepted | 2026-08-17 |
| [051](./051-share-timeline-source-visibility-with-the-fts-oracle.md) | 由 timeline 单一持有 source visibility 谓词，FTS oracle 复用它 | accepted | 2026-08-17 |
| [052](./052-hold-pipeline-mutex-with-kernel-flock.md) | pipeline 互斥改由内核 flock 持有，删除用户态判活与 stale reclaim | accepted | 2026-08-17 |
| [053](./053-retry-startup-migration-on-database-locked.md) | web 启动 migration 遇 database is locked 时有限退避重试 | accepted | 2026-08-17 |
| [054](./054-stop-rendering-article-images-in-list-cards.md) | 列表卡片不再渲染正文抓取的图片 | accepted；Context 部分归因已于 2026-08-20 撤回；渲染范围由 ADR-057/058 收窄 | 2026-08-17 |
| [055](./055-default-new-visitors-to-system-theme.md) | 新访客默认主题改为跟随系统 | accepted | 2026-08-17 |
| [056](./056-label-the-score-instead-of-showing-a-bare-number.md) | 评分显示语义标签，且不写死分母 | accepted | 2026-08-17 |
| [057](./057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md) | X 推文媒体经新加坡出口代理取回，RSS 正文图仍不展示 | accepted | 2026-08-18 |
| [058](./058-shrink-wrap-x-media-thumbnails-and-add-a-lightbox.md) | X 媒体缩略图改为收缩包裹左对齐，lightbox 增强而非取代原生链接 | accepted; refines ADR-054 and ADR-057；正文四处 file:line 已漂移，符号名对照见文末修订记录 | 2026-08-18 |
| [059](./059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md) | 两个微信来源并行取并集，按账号+归一化标题+5 分钟发布窗跨源去重 | accepted | 2026-08-20 |
| [060](./060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md) | 热点榜由后台刷新的候选缓存供给，请求路径永不同步计算，未就绪返回 503 | accepted; revisits ADR-004 scope | 2026-08-20 |
| [061](./061-deprecate-wechat-admin-discovery-line.md) | 公众号后台发现线整体废弃，发现层改由自建 Wechat2RSS 承担 | accepted; deprecates ADR-024–032、ADR-040 与探路支线 ADR-033–038 | 2026-08-20 |
| [062](./062-cut-the-switch-cost-at-the-query-the-edge-and-the-navigation.md) | 精选 ↔ 全部 AI 动态 的切换成本在查询、边缘与导航三层同时切掉 | accepted; 推翻 ADR-004 的范围结论；沿用 ADR-005 的失效契约但记录其既有缺口；生产侧沿用 ADR-039 的规则权威 | 2026-08-20 |
| [060](./060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md) | AIHOT benchmark manifests 在 v1 首发前删除重复 topology、标明投影并冻结版本化机器语义 | accepted; clarifies ADR-047 and ADR-049 | 2026-08-20 |
| [061](./061-split-shared-ssr-responses-and-discriminate-aihot-reports.md) | AIHOT window 拆分共享 SSR response/binding，验收报告按 subject 类型冻结严格语义 | accepted; refines ADR-060 | 2026-08-20 |
| [062](./062-carry-pairing-strategy-in-aihot-validation-reports.md) | AIHOT window 验收报告自持 primary/assistance/fallback pairing strategy | accepted; clarifies ADR-061 | 2026-08-20 |
| [063](./063-require-ordered-public-response-dates-in-aihot-captures.md) | AIHOT capture 的 RSS/OpenAPI public response Date 按声明顺序非递减 | accepted; clarifies ADR-060 and ADR-061 | 2026-08-20 |
| [20260826-68e2](./20260826-68e2-route-ai-radar-through-domain-selector.md) | AI Radar 经 status 验证的域名 selector 隔离出网 | accepted | 2026-08-26 |
