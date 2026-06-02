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
| [services.md](operations/services.md) | 服务清单 + 自启机制 + Instructions 位置 + 验证命令 |
| [monitoring-alerting.md](operations/monitoring-alerting.md) | `/admin` 运维 dashboard、A1-A4 告警、飞书 webhook、Cloudflare Access 配置 runbook |
| [wechat-ingestion.md](operations/wechat-ingestion.md) | 微信公众号摄取：Mp2RSS 接入、`MP2RSS_FEED_URL` 配置、真名头像 backfill、迁移留尾记录 |

### docs/references/ [Developer]

操作参考——主文档需要引用但不适合放入 README 的细节步骤。

| 文件 | 说明 |
|---|---|
| [wechat-sources.md](references/wechat-sources.md) | 旧 WeWe RSS 微信源添加流程（已停用，微信摄取现走 Mp2RSS，见 operations/wechat-ingestion.md） |

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
| [aihot-parity-contract.md](contracts/aihot-parity-contract.md) | 与 AIHOT 的跨产品对比预期：Source Pool / Feed Reading / Algorithm Soundness Parity |

### docs/issues/ [Agent]

问题跟踪——agent 驱动的轻量 issue tracker，按 domain 分文件。

| 文件 | 说明 |
|---|---|
| [README.md](issues/README.md) | Domain 索引 |
| [ux-issues.md](issues/ux-issues.md) | 端到端测试发现的产品 UX 问题（contract 在实际产品中被 broken） |
| [ux-contract-issues.md](issues/ux-contract-issues.md) | contract 本身的问题（定义缺失 / 不准确 / 过时）；append-only queue |
| [general.md](issues/general.md) | 项目级未分类问题（reliability / 工具链 / 文档错位等） |

### docs/adr/ [Developer]

架构决策记录——取舍、理由、被否的方案。每条决策独立文件。

| 文件 | 说明 |
|---|---|
| [README.md](adr/README.md) | ADR 索引 |
| [001-deterministic-source-brand-tags.md](adr/001-deterministic-source-brand-tags.md) | 标签生成优先使用确定性 source/brand 标签 |
| [002-deepseek-v4-flash-prefilter.md](adr/002-deepseek-v4-flash-prefilter.md) | Prefilter 模型选用 deepseek-v4-flash 并禁用 thinking |
| [003-dual-dotenv-loader.md](adr/003-dual-dotenv-loader.md) | Runtime env loader 读取双层 .env 文件 |
| [004-n-plus-one-optimization-scope.md](adr/004-n-plus-one-optimization-scope.md) | N+1 优化仅限 timeline 路由 |

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

### docs/plans/ [Developer]

已完成 plan 的归档。每个 plan 一个子目录。

| 目录 | 说明 |
|---|---|
| [20260601-monitoring-alerting/](plans/20260601-monitoring-alerting/) | 运维监控 dashboard + A1-A4 飞书告警 MVP |
| [20260531-wechat-search-usability/](plans/20260531-wechat-search-usability/) | 中文/微信公众号源搜索可用性修复（backfill 可见性、来源优先、多源轮转、简繁互通） |
| [20260529-timeline-search-source-author/](plans/20260529-timeline-search-source-author/) | Timeline/curated 搜索覆盖来源名、作者和中文标题 |
| [20260528-ssr-preload/](plans/20260528-ssr-preload/) | SSR preload 首屏加载优化（/ 和 /all 无可感知 spinner） |
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
| 执行产品测试 / UX 测试前 | contracts/ux-contract.md + contracts/aihot-parity-contract.md |
| 规划"接下来做什么" | issues/（待解决问题） |
| 遇到报错或"感觉有坑"时 | experiences/（按文件名选 topic） |
| 做新的架构或 API 设计决策前 | adr/README.md（索引）-> 相关 ADR |
| 想知道"系统在跑什么 / 谁拉起的 / 怎么自启" | operations/services.md（服务清单总览） |

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
