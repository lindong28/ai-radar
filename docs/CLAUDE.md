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
| [services.md](operations/services.md) | 服务清单 + 自启机制 + Instructions 位置 + 验证命令 + DB sync 的职责分工、验证与"已服务"终态判据 + X 图片新加坡出口代理（隧道链路、凭据边界、从内到外的诊断顺序）+ Cloudflare tunnel / Cache Rule 等 repo 外基础设施 |
| [monitoring-alerting.md](operations/monitoring-alerting.md) | `/admin` 运维 dashboard、A1–A7 与 D3 告警、出网链路的两种签名、周报、飞书 webhook、用户旅程性能监控 runbook |
| [wechat-ingestion.md](operations/wechat-ingestion.md) | 微信公众号摄取：Mp2RSS + Wechat2RSS 双跑与跨源去重、真名头像 backfill、停用/重启用清重步骤；后台发现候选已停止推进（ADR-061） |
| [db-slimming.md](operations/db-slimming.md) | `radar.db` 瘦身：`summary_json` 常驻保留、`admin db retain`/`admin db slim`、VACUUM 仅用于低频磁盘维护且不是 DB sync 前置、Mac 主库 apply+回滚 |

### docs/references/ [Developer]

操作参考——主文档需要引用但不适合放入 README 的细节步骤。

| 文件 | 说明 |
|---|---|
| [ai-assistant-contract.md](references/ai-assistant-contract.md) | 可选外部 summary-agent 的接口契约 [Developer]：启用条件、`./run.sh interpret` 契约、title/跳过语义、验证入口 |
| [source-maintenance.md](references/source-maintenance.md) | 信源清单维护与验证规则 [Developer]（aihot_sources.json 机器契约、audit 脚本） |
| [wechat-discovery-evidence.md](references/wechat-discovery-evidence.md) | 公众号后台发现与微信读书只读 canary 的历史证据台账 [Developer]：两者同属一条替代计划，随该计划整体停止推进（读书 canary 是这条线的探路支，不是独立路线）；权威结论见 ADR-061，本档只留取证读数 |
| [wechat-sources.md](references/wechat-sources.md) | 旧 WeWe RSS 微信源添加流程 [User]（已停用，微信摄取现走 Mp2RSS + Wechat2RSS 双跑，见 operations/wechat-ingestion.md） |
| [web-contract-golden.md](references/web-contract-golden.md) | 行为等价 Web 重构的冻结 DB + HTTP golden 使用边界、命令与 re-baseline 规则 |

### docs/prd/ [Developer]

产品需求定义。只读参考——不在日常开发中修改，变更走 ADR 或新版 PRD。

| 文件 | 说明 |
|---|---|
| [VISION.md](prd/VISION.md) | 产品愿景与阶段路线图（v0.1 草案）；§4 核心原则 BINDING。注意：§8 路线图的 phase 状态列与 §10 部分 D 条款（如 D2「与 summary-agent 完全不关联」、D7「v0 RSS only」）已被实现推翻且未在文内标注，现状以 architecture.md 与 adr/ 为准 |
| [PRD_v0.md](prd/PRD_v0.md) | v0 MVP 实施 PRD：数据流、schema、接口契约、验收标准。注意：模块路径与评分口径与当前实现已漂移，实现以 architecture.md 为准；本档只读 |

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
| [ux-contract-issues.md](issues/ux-contract-issues.md) | contract 本身的问题（定义缺失 / 不准确 / 过时） |
| [deploy.md](issues/deploy.md) | 部署与 DB 同步链路的运维问题（sync/apply/cron/verifier，含影响其验收的测试基线） |
| [docs-quality.md](issues/docs-quality.md) | 文档自身的质量债（README 定位/重复/可观察性等审查遗留） |
| [alerting.md](issues/alerting.md) | 服务故障告警的设计质量债（值不值得 page、严重度、消息说什么、要不要合并、基线可行性、留痕） |
| [cost-observability.md](issues/cost-observability.md) | LLM 成本计量、定价、报告与告警消费面的未闭合项；金额口径只覆盖 `llm_usage` 记录行 |
| [general.md](issues/general.md) | 项目级未分类问题（reliability / 工具链 / 文档错位等） |
| [harness-issues.md](issues/harness-issues.md) | Agent harness、wrapper、hook、plugin 或 skill 行为问题，**限牵涉本项目的**；纯 user-scope 的 harness 问题按协议 §4.8 的写入路由归 harness 仓（`~/research/ai-agent-config`），不留在本仓 |
| [archive/closed.md](issues/archive/closed.md) | 已 resolved / wontfix 的历史 issue 归档 |

### docs/adr/ [Developer]

架构决策记录——取舍、理由、被否的方案。每条决策独立文件。

| 文件 | 说明 |
|---|---|
| [README.md](adr/README.md) | ADR 索引（单一权威：每条决策的标题与状态只在该索引维护） |

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

已完成 plan 的归档。每个 plan 一个子目录。归档件正文前带 `Archive status` 导航头：说明归档状态（含未完全收尾者的中止点）并指向当前结果入口（ADR / operations / 契约），正文本身不改写；个别随 plan 归档的 provenance 附件（一次性实测记录、shadow 期工具）在该头内自陈性质与边界，不是可重跑的 verifier。

| 目录 | 说明 |
|---|---|
| [20260817-x-tweet-media-pipeline/](plans/20260817-x-tweet-media-pipeline/) | X 推文媒体经新加坡正向代理展示；plan 自陈"未定稿、未过评审"，落地结果见 ADR-057/058 |
| [20260816-mp2rss-replacement/](plans/20260816-mp2rss-replacement/) | Mp2RSS 替代可行性验证：后台线平台级不可用归因、shadow 比较工具；结局见 ADR-059/061 |
| [20260812-aihot-original-source-alignment/](plans/20260812-aihot-original-source-alignment/) | AIHOT 原始来源对齐：161 个主时间线来源、可选 WeChat 隔离、语义收据与浏览器升级验收 |
| [20260810-llm-cost-observability/](plans/20260810-llm-cost-observability/) | 查询时派生成本、告警/周报消费面、cache split 与计量失败 paid-result 保护；金额加总只表示记录行下界 |
| [20260809-fts-rebuild-sync/](plans/20260809-fts-rebuild-sync/) | DB 跨洋同步改造：持久 base-only replica + 服务器候选槽重建 FTS，稳态 16.39MB<20MB、零停机 3500/3500 |
| [20260808-news-aiplanet-launch/](plans/20260808-news-aiplanet-launch/) | `news.aiplanet.live` 正式上线：生产部署迁到腾讯云国内服务器；r3 修订，含 ICP 备案通过后由"审核期故意下线"转正式上线的切换步骤 |
| [20260803-aihot-visual-parity/](plans/20260803-aihot-visual-parity/) | AIHOT 视觉复刻 plan（GAP 条目编号至 GAP-90）；含一条经 live 实测更正的继承判断——「移动层需第二棵 DOM 树、ADR-012 前提被证伪」不成立——与同目录 `measured-tokens.md` 实测 token 附件 |
| [20260802-aihot-redesign/](plans/20260802-aihot-redesign/) | 参照 AIHOT 的全面对标改版（前端 + 后端）；定稿时公网仍在备案审核期，只对内网 8010 实施 |
| [20260724-perf-idle-only-and-grounding/](plans/20260724-perf-idle-only-and-grounding/) | PERF 转 idle-only 采样（退休 F1/F4）+ 告警审查以真实数据接地的三个 infra carrier；经 Codex review-plan 四轮收敛后定稿 |
| [20260721-alerting-quality-fixes/](plans/20260721-alerting-quality-fixes/) | 告警质量修复（F1–F6 核心 + F11–F12 留痕 + F13 文档）；F1 降级 gating 真值表为核心兑现点，F2 两级 severity 决定投递通道；不改投递层 |
| [20260720-db-slimming/](plans/20260720-db-slimming/) | radar.db 瘦身：清可再生 `summary_json` 缓存 + VACUUM + 常驻保留（Option A），生产 2.28GB→1.495GB |
| [20260719-tencent-migration/](plans/20260719-tencent-migration/) | `aiplanet.live` 迁腾讯云备案版的读写分离方案；定稿于 2026-07-19、备案周期未完，作为跨 session 接续锚点 |
| [20260718-perf-safeguard/](plans/20260718-perf-safeguard/) | 持续性能保障（精简版）：改用 generation-triggers 方案（migration 013 双计数器），只解决三个核心目标；执行期的验证/验收证据附件不入档 |
| [20260718-feedback-loop/](plans/20260718-feedback-loop/) | AI 生成质量修根因 + 用户反馈闭环（采集 → 回测 → 迭代）；长任务模式 |
| [20260715-continuous-performance-loop/](plans/20260715-continuous-performance-loop/) | 持续性能保障与受控自治优化计划；被 20260718-perf-safeguard 判为相对单机单操作者形态严重过度工程后从零重规划取代，仅作历史参照、不沿用 |
| [20260611-opensource-readiness/](plans/20260611-opensource-readiness/) | 开源就绪：代码库中性化 + 一份历史清洗过的公开 repo；须同时满足 forker（clone→配 `.env`→`./install.sh` 可用，微信解读默认 OFF）与维护者（改动前后 aiplanet.live 行为观感不变）两类使用者 |
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
| [20260528-wechat-oa-ingestion/](plans/20260528-wechat-oa-ingestion/) | 微信公众号信源接入（Playwright 抓全文） |
| [20260528-0640-ux-contract-ux-test/](plans/20260528-0640-ux-contract-ux-test/) | UX 契约建立 + 测试修复（关于页 GitHub 链接、时间线无分数条目） |
| [20260524-timeline-perf/](plans/20260524-timeline-perf/) | Timeline API 性能优化（/all TTFB 14s -> <1s） |
| [20260515-pipeline-scheduler/](plans/20260515-pipeline-scheduler/) | 自动化增量数据抓取流水线（pipeline.sh + cron 15min 调度） |

---

## 尚未创建的文档类型

以下类型在协议中定义但本项目尚未创建，按需启用：

| 类型 | 路径 | 创建时机 |
|---|---|---|
| contracts/ux-test-patterns.md | `docs/contracts/ux-test-patterns.md` | 测试中发现值得长期留意的 pattern 时 |
| data/ | `docs/data/` | 需要外部源可信度分级或物化数据盘点时——本项目已有实据，按需启用 |
| experiments/ | `docs/experiments/` | 出现要与未来优化对比的 baseline 时 |

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
| 产品的用户可感知行为变化 | CHANGELOG.md；ux-contract.md 按协议 §4.6 执行路径（plan 工作流主路径 / 其余写入 issues/ux-contract-issues.md 候选，不直写） |
| 做了非平凡的设计决策 | adr/（新建 ADR 文件 + 更新 README.md 索引） |
| 花了非平凡时间解决一个问题 | experiences/（按 topic 写入对应文件 + 更新 README.md 索引） |
| 发现值得跟踪但不属于当前任务的问题 | issues/ 对应 domain 文件 |
| 新增/移除长期运行的服务，或自启机制变化 | operations/services.md（更新清单 + 验证步骤） |
| 长任务完成后提升 task 产物 | 按提升路径分流到对应文档 |
| docs/ 下新增、重命名或删除文档 | 本文件（更新索引） |
