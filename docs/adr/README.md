# Architecture Decision Records

> 每条决策独立文件。历史数字前缀已出现重复，不再把裸 `ADR-NNN` 当全局唯一标识；新建或修改 mutable 文档时，对重复号使用本表 alias 并链接到完整文件名。append-only 历史可保留原 label，但链接目标必须是完整文件名。新决策使用 `YYYYMMDD-4位随机后缀-slug` 文件名。Status: accepted / superseded / deprecated.

| # | Title | Status | Date |
|---|---|---|---|
| [20260919-9365](./20260919-9365-publish-admission-time-label-proposal.md) | 仅出版新闻准入时间标注修订草案；新 benchmark 与 gold 变更待审核 | accepted for proposal publication only | 2026-09-19 |
| [20260919-b7e2](./20260919-b7e2-second-security-round-deploy-chain-credentials-csp.md) | 第二轮安全审查：部署链验签与根路径保护、凭据面 allowlist、读时 URL 门、CSP 迁移 | accepted；部分 supersede ADR-003（只加载声明键）；CSP 强制待一周 Report-Only 后切换 | 2026-09-19 |
| [20260919-d62b](./20260919-d62b-test-prefilter-ai-capabilities.md) | 离线测试AI设备实质能力及来源上下文；双90累计6000次，不改题库或生产 | accepted for experiment | 2026-09-19 |
| [20260919-c31a](./20260919-c31a-test-prefilter-standalone-input.md) | 离线测试原始回复关系、正文条件及准入构框；不改题库和生产 | accepted for experiment | 2026-09-19 |
| [20260919-bc72](./20260919-bc72-test-prefilter-hn-heat.md) | 仅离线测试 prefilter 的 HN 热度条件；双 >90% 目标、3,000 次请求上限、不改题库 | accepted for experiment | 2026-09-19 |
| [20260919-e1d3](./20260919-e1d3-test-prefilter-direct-ai-impact.md) | 仅离线测试 prefilter 纳入 AI 直接社会/经济/资源影响的单句候选，不改生产 | accepted for experiment | 2026-09-19 |
| [20260919-a3c1](./20260919-a3c1-harden-public-surface-after-security-review.md) | 安全审查后加固公网面：origin 自验 admin token、采集入口白名单、请求边界、安全头与依赖升级 | accepted；生产生效待部署 + 服务器写入 token | 2026-09-19 |
| [20260918-7d0e](./20260918-7d0e-expand-score-and-enrichment-from-aihot-originals.md) | 仅 O2/O3 以 AIHOT 原标题与绑定原文扩题，O1/O4 保持保守候选窗口 | accepted；已物化新 O2/O3 题库，读数见 inventory | 2026-09-18 |
| [20260917-8b86](./20260917-8b86-wait-for-collector-process-groups.md) | 有界等待采集进程组退出，监督失败纳入现有告警 | accepted；已获批启用，实际入口退出 0 | 2026-09-17 |
| [20260917-b8e2](./20260917-b8e2-recover-continuous-raw-capture.md) | 恢复后续 raw 采集：提取互斥、版本化补充探针、有界重试与独立健康检查 | accepted；已批准并安装调度，新日窗与连续性待验收 | 2026-09-17 |
| [20260916-74b2](./20260916-74b2-release-db-sync-lock-on-process-exit.md) | DB sync 用内核锁消除重启后的永久阻塞，日志裁剪共用锁 | accepted | 2026-09-16 |
| [20260916-e3a8](./20260916-e3a8-decouple-collection-from-processing.md) | 采集独立于 AI 处理，以事务性 outbox 可靠交接；仅本地实现 | accepted | 2026-09-16 |
| [20260916-d362](./20260916-d362-defer-busy-outbox-cleanup.md) | 已确认 outbox 清理争锁时延后，继续主库导入 | accepted | 2026-09-16 |
| [20260915-1cc7](./20260915-1cc7-retain-continuous-prefilter-inputs.md) | 可选保留过滤前完整输入，并版本化补收 AIHOT 缺窗；仅本地实现 | accepted | 2026-09-15 |
| [20260915-0eeb](./20260915-0eeb-publish-user-visible-eval-redesign-proposal.md) | 先交付面向最终展示的评测重建设计（仅出版草案，未批准实施） | accepted | 2026-09-15 |
| [001](./001-deterministic-source-brand-tags.md) | 标签生成优先使用确定性 source/brand 标签 | accepted | 2026-05-12 |
| [002](./002-deepseek-v4-flash-prefilter.md) | Prefilter 模型选用 deepseek-v4-flash 并禁用 thinking | accepted | 2026-05-15 |
| [003](./003-dual-dotenv-loader.md) | Runtime env loader 读取双层 .env 文件 | accepted；「加载全部键」语义被 [20260919-b7e2](./20260919-b7e2-second-security-round-deploy-chain-credentials-csp.md) 收窄为只加载声明键，见文末修订 | 2026-05-15 |
| [004](./004-n-plus-one-optimization-scope.md) | N+1 优化仅限 timeline 路由 | accepted；前提部分失效（hot 复用 curated archive 路径），见 [060-hot-cache](./060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md) | 2026-05-24 |
| [005](./005-timeline-exact-count-with-cached-cte.md) | Timeline 真实总数计数 + CTE 公式 + 进程内 LRU 缓存 | accepted | 2026-06-04 |
| [006](./006-curated-archive-mode.md) | 精选页改为跨 run 去重的累积归档（复用 ADR-005 真实计数 pattern） | accepted | 2026-06-04 |
| [007](./007-interpret-via-ai-assistant-summarizer.md) | 微信文章解读复用 ai-assistant summarizer，save_decision 作单一闸门 | accepted；「interpret 回填无法并发」issue 已 resolved 移入 archive，见文末修订记录 | 2026-06-06 |
| [008](./008-alert-severity-lifecycles.md) | 告警按 severity 维护独立 lifecycle | accepted; PERF F1/F4 superseded by ADR-011 | 2026-07-22 |
| [009](./009-alert-notification-ledger.md) | 用有界 JSONL 记录已送达告警通知 | accepted; scope superseded by ADR-021; 64 MiB boundedness superseded by ADR-20260904-d708 | 2026-07-22 |
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
| [021](./021-audit-alert-delivery-and-suppression-decisions.md) | 告警事件 ledger 同时审计投递与合并抑制决策 | accepted; supersedes ADR-009 scope; boundedness superseded by ADR-20260904-d708 | 2026-08-11 |
| [022](./022-evaluate-a6-in-progress-cost-as-lower-bound.md) | A6 在途成本作为下界继续正向评估 | accepted; supersedes ADR-020 in-progress handling | 2026-08-11 |
| [023](./023-define-recorded-row-measurement-scope.md) | 以记录行为 LLM 用量派生指标定义测量范围 | accepted | 2026-08-12 |
| [024](./024-shadow-wechat-admin-discovery-before-mp2rss-cutover.md) | 以 shadow canary 验证公众号后台发现适配器后再替换 Mp2RSS | deprecated（后台 family 平台级不可用，见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; cadence/page-size superseded by ADR-025; identity mapping 与旧 evidence 语义 superseded by ADR-028 | 2026-08-13 |
| [025](./025-conservative-wechat-discovery-probe-defaults.md) | 公众号后台发现探测采用低频、小页的临时保守默认 | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-024 cadence/page-size only | 2026-08-13 |
| [026](./026-explicit-windowed-mp2rss-shadow-comparison.md) | 以显式只读命令执行窗口化 Mp2RSS shadow 对比 | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; persistence evolution superseded by ADR-027 | 2026-08-13 |
| [027](./027-self-describing-recoverable-wechat-shadow-page-size.md) | 让微信 shadow 页大小记录自描述且可恢复迁移 | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-026 persistence evolution only | 2026-08-13 |
| [028](./028-resolve-wechat-fakeid-before-shadow-probe.md) | 先解析并一次性消费已验证 fakeid，再执行微信 shadow probe | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; 仅 searchbiz 验证语义 superseded by ADR-040 | 2026-08-13 |
| [029](./029-single-source-wechat-discovery-ledgers.md) | 微信发现 ledger 采用单一权威并保持崩溃可恢复 | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; duplicated fields superseded by ADR-030 | 2026-08-13 |
| [030](./030-remove-derived-wechat-discovery-fields.md) | schema v6 移除可派生字段、统一成功终态，并以 config v3 明示 public biz | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-029 duplicated fields | 2026-08-13 |
| [031](./031-preserve-only-provable-wechat-migration-facts.md) | 微信历史 ledger 迁移只保留可证明事实，缺 provenance 时降级、矛盾关系整笔回滚 | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; clarifies ADR-028 through ADR-030 migration semantics | 2026-08-13 |
| [032](./032-reject-duplicate-urls-before-wechat-shadow-comparison.md) | 微信后台单次响应含重复 URL 时显式失败，禁止去重后误判窗口覆盖 | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; clarifies ADR-026 and ADR-030 snapshot semantics | 2026-08-13 |
| [033](./033-version-weread-canary-shelf-request-evidence.md) | 微信读书 canary 证据升为 v2 并保留书架请求平台错误码 | deprecated（canary 线随替代计划停止，见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; superseded by ADR-034 for new evidence | 2026-08-13 |
| [034](./034-use-a-single-auditable-weread-canary-evidence-ledger.md) | 微信读书 canary v3 采用单一可审计请求与候选证据权威 | deprecated（canary 线随替代计划停止，见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-033 / superseded by ADR-035 for new evidence | 2026-08-13 |
| [035](./035-bind-weread-canary-evidence-to-targets-producer-and-relations.md) | 微信读书 canary v4 绑定请求目标、生产者源码与身份关系 | deprecated（canary 线随替代计划停止，见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-034 / superseded by ADR-036 for new evidence | 2026-08-13 |
| [036](./036-preserve-public-page-observation-outcomes.md) | 微信读书 canary v5 区分已观察页面与客户端失败，并闭合请求顺序和返回页关系 | deprecated（canary 线随替代计划停止，见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-035 / superseded by ADR-037 for new evidence | 2026-08-13 |
| [037](./037-retain-observed-captcha-target-at-attempt-end.md) | 微信读书 canary v6 在 attempt 结束时保留已观察到的验证码 target，并闭合失败关系 | deprecated（canary 线随替代计划停止，见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-036 / superseded by ADR-038 for new evidence | 2026-08-13 |
| [038](./038-observe-weread-dynamic-header-presence-without-replay.md) | 微信读书 canary v7 仅观察既有列表请求的动态鉴权头名称是否出现，不捕获或回放头值 | deprecated（canary 线随替代计划停止，见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-037 for new evidence | 2026-08-13 |
| [039](./039-route-news-through-edgeone-dns-only-cname.md) | 通过 DNS-only CNAME 将 news 入口接入 EdgeOne | accepted | 2026-08-13 |
| [040](./040-verify-provisional-searchbiz-mapping-with-article-url-biz.md) | searchbiz 只产 provisional mapping，再由返回文章 URL 的 public biz 完成身份验证 | deprecated（见 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md)）; supersedes ADR-028 searchbiz verification semantics | 2026-08-14 |
| [041](./041-version-wechat-discovery-invariant-hardening.md) | 微信 discovery 不变量加固以 schema v8 发布，不原地改写已落地 v7 | accepted；依赖后台 family 的部分已随 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md) 失效，schema 演化与证据纪律仍成立; clarifies ADR-029 through ADR-031 and ADR-040 | 2026-08-14 |
| [042](./042-isolate-production-deploy-commit-from-local-main.md) | 从本地 main 的未发布提交中隔离生产部署 commit | accepted | 2026-08-16 |
| [043](./043-waive-manual-wechat-probe-cooldown-once.md) | 对一次获授权微信后台 probe 豁免本地 1440 分钟冷却 | accepted；依赖后台 family 的部分已随 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md) 失效，一次性豁免的记账纪律仍成立; one-shot exception to ADR-025 only | 2026-08-16 |
| [044](./044-persist-wechat-platform-error-ret.md) | schema v9 持久化后台 exact ret，区分平台拒绝与可证明频控 | accepted；依赖后台 family 的部分已随 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md) 失效，schema 演化与证据纪律仍成立; clarifies ADR-025 failure and cooldown semantics | 2026-08-16 |
| [045](./045-require-integer-platform-ret-and-evidence-backed-cooldown.md) | schema v10 只接受整数后台错误码，特殊冷却仅由已记录频控证据触发 | accepted；依赖后台 family 的部分已随 [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md) 失效，schema 演化与证据纪律仍成立; clarifies ADR-025 and ADR-044 | 2026-08-16 |
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
| [059](./059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md) | 两个微信来源并行取并集，按账号+归一化标题+5 分钟发布窗跨源去重 | accepted；Mp2RSS 主动运行状态由 [20260904-f427](./20260904-f427-pause-source-fetch-without-hiding-history.md) supersede，跨源身份设计保留 | 2026-08-20 |
| [060-hot-cache](./060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md) | 热点榜由后台刷新的候选缓存供给，请求路径永不同步计算，未就绪返回 503 | accepted; revisits ADR-004 scope | 2026-08-20 |
| [061-wechat-discovery](./061-deprecate-wechat-admin-discovery-line.md) | 公众号后台发现线整体废弃，发现层改由自建 Wechat2RSS 承担 | accepted; deprecates ADR-024–032、ADR-040 与探路支线 ADR-033–038 | 2026-08-20 |
| [062-page-switch](./062-cut-the-switch-cost-at-the-query-the-edge-and-the-navigation.md) | 精选 ↔ 全部 AI 动态 的切换成本在查询、边缘与导航三层同时切掉 | accepted; 推翻 ADR-004 的范围结论；沿用 ADR-005 的失效契约但记录其既有缺口；生产侧沿用 ADR-039 的规则权威 | 2026-08-20 |
| [060-aihot-manifest](./060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md) | AIHOT benchmark manifests 在 v1 首发前删除重复 topology、标明投影并冻结版本化机器语义 | accepted; clarifies ADR-047 and ADR-049 | 2026-08-20 |
| [061-aihot-reports](./061-split-shared-ssr-responses-and-discriminate-aihot-reports.md) | AIHOT window 拆分共享 SSR response/binding，验收报告按 subject 类型冻结严格语义 | accepted; refines [060-aihot-manifest](./060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md) | 2026-08-20 |
| [062-aihot-pairing](./062-carry-pairing-strategy-in-aihot-validation-reports.md) | AIHOT window 验收报告自持 primary/assistance/fallback pairing strategy | accepted; clarifies [061-aihot-reports](./061-split-shared-ssr-responses-and-discriminate-aihot-reports.md) | 2026-08-20 |
| [063-aihot-dates](./063-require-ordered-public-response-dates-in-aihot-captures.md) | AIHOT capture 的 RSS/OpenAPI public response Date 按声明顺序非递减 | accepted; clarifies [060-aihot-manifest](./060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md) and [061-aihot-reports](./061-split-shared-ssr-responses-and-discriminate-aihot-reports.md) | 2026-08-20 |
| [20260826-68e2](./20260826-68e2-route-ai-radar-through-domain-selector.md) | AI Radar 经 status 验证的域名 selector 隔离出网 | accepted; **部分失效 2026-09-11** — interpret 收据跳过那条 consequence 已不成立，其余有效 | 2026-08-26 |
| [20260828-f8d9](./20260828-f8d9-replay-frozen-wechat-interpretations-in-bounded-cohorts.md) | 先修零向量，再按有界 cohort 回放冻结的微信解读 | accepted | 2026-08-28 |
| [20260828-c3a5](./20260828-c3a5-retry-missing-criteria-reason-once.md) | 微信解读仅对缺失 criteria_reason 立即重试一次 | accepted | 2026-08-28 |
| [20260829-c0e8](./20260829-c0e8-bind-egress-receipt-to-implementation-and-paths.md) | 将 AI Assistant 出网收据绑定到实现闭包与生产路径 | accepted; **retired 2026-09-11** — 收据与 receipt_writer 已移除，AI_ASSISTANT_ROOT 由所有者裁定为可信第一方代码 | 2026-08-29 |
| [20260829-a7f1](./20260829-a7f1-suppress-actionless-x-silence.md) | 用新鲜终态收据抑制无处置价值的 X 来源静默告警 | accepted | 2026-08-29 |
| [20260831-30ad](./20260831-30ad-hybrid-wechat-search-and-kb-archive-import.md) | 微信搜索采用多词混合检索，并显式补录 ai-assistant KB 归档 | accepted | 2026-08-31 |
| [20260831-8b7c](./20260831-8b7c-control-wechat-review-term-aliases.md) | 微信搜索用受控评测词别名修复词汇错位，不放宽多词交集 | accepted | 2026-08-31 |
| [20260904-9890](./20260904-9890-wechat2rss-lima-boot-runtime.md) | Wechat2RSS 使用 Lima generated system LaunchDaemon 实现无 GUI 登录的 boot runtime | accepted；生产切换与真实 reboot 仍须 live gate | 2026-09-04 |
| [20260904-f427](./20260904-f427-pause-source-fetch-without-hiding-history.md) | 暂停来源的抓取与 A7，同时保留历史可见性、跨源身份和 enabled 语义 | accepted；supersedes ADR-059 的 Mp2RSS 主动运行状态；生产迁移与发布尚未执行 | 2026-09-04 |
| [20260901-a31f](./20260901-a31f-stage-wechat-whitespace-fallback-after-empty-results.md) | 微信搜索先走索引严格匹配，只在零结果时启用空白标准化兜底 | accepted | 2026-09-01 |
| [20260903-bc36](./20260903-bc36-quota-curated-selection-by-source-form.md) | 精选按来源形态配额（X ≤20%、单源 ≤7.5%），同轮记无配额基线并支持定向回退 | accepted; partially supersedes ADR-010（配额独有行可定向删除） | 2026-09-03 |
| [20260904-51d2](./20260904-51d2-a4-complete-fetch-signal-and-account-layer-page.md) | A4 只读完整 fetch 轮的信号；账户层失败（401/402）升为 page 并按来源组给处置 | accepted | 2026-09-04 |
| [20260905-b00f](./20260905-b00f-write-egress-receipt-after-live-policy-recheck.md) | 出网收据只在写盘前复核生产策略后生成 | accepted; **retired 2026-09-11** — 随 ADR-20260829-c0e8 一同退役，receipt_writer 已删除 | 2026-09-05 |
| [20260904-d708](./20260904-d708-fail-pipeline-before-wechat-browser-degradation.md) | 微信浏览器缺失时在 fetch 前终止 scheduled pipeline，W1 复用共享告警状态机 | accepted; relates ADR-20260826-68e2 and ADR-059; supersedes ADR-009/021 boundedness only | 2026-09-04 |
| [20260905-499e](./20260905-499e-aihot-reference-fit-eval-system.md) | 以 AIHOT 历史输出为参考输出建立内容链拟合评测体系（eval-fit 四槽位） | accepted；v1 全量基线与 8 条达标线已于 2026-09-06 写入 | 2026-09-05 |
| [20260906-7c31](./20260906-7c31-rank-on-weights-fitted-to-the-reference.md) | 网站排序改用拟合 AIHOT 分数的权重（density/authority/significance），取消来源分层乘数 | accepted; extends ADR-20260905-499e（该 ADR 曾把生产排序留在原地） | 2026-09-06 |
| [20260907-a1c4](./20260907-a1c4-align-architecture-not-just-fields.md) | 与 AIHOT 对齐**架构**而非只对齐字段：M1 理由只给精选写、M2 打分加类别项（held-out ρ +0.029）、M3 精选不是分数的函数（建议不做）、**M5 已同日撤回**、M4 两段分类实测：定向 +15pp 但整体 −3pp、需重设计（tutorial→industry 205 条是最大误判格） | **proposed**，待 decision-review；四条可分别裁决 | 2026-09-07 |
| [20260910-3f8b](./20260910-3f8b-demote-papers-in-the-ordering-key-only.md) | 论文类在**排序键**上降权 0.95（不进两道绝对闸、不改归档分）；同时修好三处量具：跨标注器 TV 地板 0.129、单日 TV 饱和于抽样噪声 0.253、版面深度混淆（据此撤回「model 缺口是排序缺陷」的归因） | accepted（2026-09-10 上线，paper 3/40 = 7.5% vs 参照 8.2%）；**部分 supersede ADR-20260906-7c31**「排序由单一拟合向量完整定义」 | 2026-09-10 |
| [20260910-9e21](./20260910-9e21-pin-the-category-snapshot-with-one-integer.md) | 类别快照用一个整数固定（`enrich_watermark` = 载入前的 `MAX(item_evaluations.id)`，8 字节 vs 全量快照 6.93 GB/年）；**不**按 enrich 戳收窄系数施加面（旧戳 paper 精确率 98.5% > 拟合口径 94.1%，收窄会停掉 141/244 条仍正确的修正）；评测台**追加** `selected_auc_ranked` 而不替换既有指标 | accepted | 2026-09-10 |
| [20260913-e21a](./20260913-e21a-clamp-rss-pubdate-to-fetch-time.md) | RSS 入库把未来 pubDate 钳到**解析时刻**，**只在 RSS 路径上**（`web.py:_date()` 同形无上界但 0 实测越界，有意不修）；起因是上游 `openai.com/news/rss.xml` 自报 `Mon, 14 Sep 2026 00:00:00 GMT`，在 ADR-006 归档面的 `ORDER BY published_at DESC` 上把自己钉在首位约 22 小时 | accepted；决策评审判据 1 `应修` 经 waive 放行（作用域取得用户要求锚、恢复条件已告知） | 2026-09-13 |
| [20260914-0442](./20260914-0442-close-the-aihot-eval-system-three-layer-loop.md) | 接通 AIHOT 拟合评测的 L1 五槽、L2 轮次资产、L3 身份治理与 build→run→judge→archive→report 闭环 | accepted；v1 保持不变，v2 与标题维度未获新标准前不得采信 | 2026-09-14 |
| [20260914-f1a8](./20260914-f1a8-classify-legacy-eval-outside-aihot-fit.md) | 把旧 `eval` 保留为 canonical AIHOT 评测链之外的 legacy snapshot comparison/reporting tool | accepted；旧 reports 保留为 non-comparable，不补造 producer-time identity | 2026-09-14 |
| [20260914-acdd](./20260914-acdd-notify-alert-state-transitions.md) | 告警只按新事故、严重度变化与可证明恢复投递；低证据状态保持 in-progress | accepted；supersedes ADR-008 episode reminder 与 page resolved 通道、ADR-011 reminder nonce、ADR-20260904-51d2 的当日 items-floor warm-up 语义 | 2026-09-14 |
| [20260914-2307](./20260914-2307-retain-aihot-raw-captures-for-thirty-days.md) | AIHOT 原始 capture 默认保留 30 天，eval runs 独立保持 14 天 | accepted；supersedes 2026-09-07 的 raw capture 14 天默认 | 2026-09-14 |
