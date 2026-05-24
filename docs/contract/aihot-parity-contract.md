# AI Radar · AIHOT Parity Contract

> 装「AI Planet 用户体验足够接近 AIHOT」这一预期的可观察判据。与 [`ux-contract.md`](./ux-contract.md) 互补——它装 AI Radar 对自己用户的内部承诺；本 contract 装跨产品对比预期。
>
> 判定方式：**signal-only**。不订死「重叠率 ≥ N%」「数量差 ≤ M%」这类外部硬阈值。差异本身是触发调查的信号，调查结论是真问题（写入 [`../issues/ux-issues.md`](../issues/ux-issues.md)）还是故意差异（命中 §4 carve-out 不报）由 reviewer 判。
>
> 上位文档：[VISION.md](../prd/VISION.md)（特别是 §3 同/异、§4 核心原则）、[PRD_v0.md](../prd/PRD_v0.md)。
> 配对协议：`~/.claude/references/ux-test-protocol.md`（测试执行流），`~/.claude/references/ux-contract-proposal-protocol.md`（contract 演进流）。

---

## §0 参照锚点

| 项 | AIHOT (参照站) | AI Planet (本产品) |
|---|---|---|
| 精选页 | https://aihot.virxact.com/ | https://aiplanet.live/ |
| 时间线 | https://aihot.virxact.com/all | https://aiplanet.live/all |
| 日报 | https://aihot.virxact.com/daily | https://aiplanet.live/daily |
| API（本端可用） | — | `/api/v1/curated`, `/api/v1/timeline?limit=N&page=N` |
| 信源池真值 | 公开站点暴露源 + 抓取得到的 baseline 快照 | `data/sources.toml`（`enabled = true/false` + 同行注释） |

**抓取约定**（每次跑 parity 测试都遵守，避免环境差异造假阳）：

- 同一会话窗口、同一 timezone（默认 Asia/Shanghai）、同一 locale（zh-CN）、桌面分辨率 1440x900。
- 避开 AI Planet pipeline 抓取间隙（每 15 分钟 cron + buffer，见 `ux-contract.md` §QualityBar-DataFreshness）。
- AIHOT 是外部黑盒：仅基于其公开站点可见行为做对比，**不**假定其内部分数/权重/聚类细节。
- AI Planet 端可以直接读 API、读 `data/sources.toml`、读 `/about` 信源表格做对照。

---

## §1 Source Pool Parity（Layer 1）

预期：AIHOT 公开列出 / 可观察到的核心信源，在 AI Planet 上必须有，除非在 `data/sources.toml` 里显式 disabled 并附理由。

### §SourceParity-CoreSetCoverage — 核心信源池覆盖

- **预期**：AIHOT 暴露的每个 source（含 RSS、官方博客、官方 X、媒体源）都应在 `data/sources.toml` 出现一条 entry（`enabled = true` 或 `enabled = false`）。
- **显式 disable 机制**：禁用条目必须满足 `enabled = false` + 同行/紧邻行内联注释，沿用既有格式：`# disabled YYYY-MM-DD: <reason>`。已确立的合法 reason 范畴：
  - `probe failed; see <plan path>` —— 抓取探测失败
  - `not present in AI Hot baseline source set` —— 主动剔除非 AIHOT 基线源
  - `<owner 一句话理由>` —— 其他 owner 决策
- **signal — AIHOT 有但 AI Planet 完全没收录**：触发调查。可能是 baseline 漂移、新加源未跟进、或漏抓。
- **signal — AIHOT 有但 AI Planet `enabled = false` 且无理由注释**：触发调查。理由缺失意味着失去可审计性，下次回看不知道为什么禁。
- **signal — AI Planet `enabled = true` 但实际 pipeline 拿不到内容**：触发调查（信源健康），可能与 AIHOT 也无关，但 parity 测试会顺手暴露。
- **范围**：仅看 source-level 覆盖，不看条目级覆盖（条目级在 §2/§3）。

### §SourceParity-DisabledRationale — 禁用理由可审计

- **预期**：所有 `enabled = false` 条目的注释能读懂为什么禁、什么时候禁。
- **signal**：批量注释丢失 / 注释格式被改 / 出现「TODO: 待补」等暂存语 → 触发调查。
- **观察方式**：单次 grep `enabled = false` 配合人眼扫描。

### §SourceParity-AboutSurfaceReflection — `/about` 信源表格反映真实启停态

- **预期**：`/about` 上的 source table（见 ux-contract §Feature-SourceTable）所列的 enabled/disabled 状态与 `data/sources.toml` 一致；停用源仍展示（不能从表里消失）。
- **signal**：表格里某源显示 enabled 但 toml 里是 disabled，或反之 → 触发调查。
- **signal**：toml 里有但表格里没有 → 配置/前端渲染漂移。
- **关联**：ux-contract §Feature-SourceTable、§Surface-About。

---

## §2 Feed Reading Parity（Layer 2）

预期：在精选页、时间线、日报三个 surface 上，AI Planet 的整体阅读体感应足够接近 AIHOT——卡片可承诺信息、被挑选出的条目集合、分布、推荐理由 / tag / 分数的呈现，肉眼对比不应有「这看上去是另一个产品」的违和。

### §FeedParity-CuratedPickReadability — `/` 精选卡阅读体感

- **预期**：单卡可承诺信息与 AIHOT 同集——源（头像 + 名字 + 可选 handle）、精选标记、数字分（0-100）、中文标题、中文摘要、内容分类标签、推荐理由、可选媒体图、可选「关联讨论 N 条」。详见 ux-contract §Feature-CuratedCard。
- **signal — 卡片缺关键承诺要素**：AIHOT 同位置展示 X，AI Planet 完全不渲染 X，且 ux-contract 也未列入 OutOfScope → 触发调查。常见例：推荐理由系统性缺失、数字分不渲染、关联讨论标记不渲染。
- **signal — 视觉重量明显失衡**：AIHOT 的 X/社交卡接近正文体，AI Planet 用强标题体（粗体 + 大字号）；或反之 → 触发调查（影响扫读节奏，可能是组件错位）。
- **signal — 自然点击目标不一致**：AIHOT 上标题/正文/内容图是外链入口，AI Planet 强加额外「打开原文」按钮 / 把 source 头像变成主入口 → 触发调查。

### §FeedParity-CuratedPickSet — `/` 精选条目集合对比

- **预期**：同一时间窗口（建议同日，避免跨日噪音）AIHOT 精选与 AI Planet 精选应在「重要事件」上有显著重叠，但**不**要求一一对应。VISION §3 明示「工程师权重 vs 自媒体口味」会带来选条差异，命中 §4 carve-out 的不报。
- **signal — 重叠为零或近零**：AIHOT 当日精选与 AI Planet 当日精选肉眼判没有任何事件交集 → 高优先级调查（评分阈值 / 召回 / 抓取 任一环节可能失效）。
- **signal — AIHOT 反复出现的工程关键事件在 AI Planet 完全缺位**：例如模型发版、官方 benchmark、官方文档更新等明显属于「工程师权重」也该承接的条目，AI Planet 多日缺位 → 触发调查。命中 §IntDiv-SelfMediaTopic 的（KOL 八卦 / 流量话题）排除。
- **signal — AI Planet 精选条数极端**：当日 0 条或超出 AIHOT 同日量级一个数量级以上 → 触发调查。常态量级见 ux-contract Persona 描述（owner 体感 < 30 条/日）。

### §FeedParity-TimelineAffordance — `/all` 时间线对比

- **预期**：AI Planet `/all` 与 AIHOT `/all` 在「列表是可扫读的内容卡而非紧凑文本列」「曾入精选条目保留精选元数据」「有评分则展示数字分」三件事上一致。详见 ux-contract §Feature-TimelineCard、§Journey-CuratedTraceOnAll。
- **signal — `/all` 静默丢失精选元数据**：同事件在 `/` 显数字分 + 推荐理由 + 精选 badge，在 `/all` 全部丢失 → 触发调查（数据有但 UI 渲染丢）。
- **signal — `/all` 媒体呈现极端**：AIHOT 上多卡有内容图，AI Planet 全无图；或 AI Planet 单图占满首屏 → 触发调查（注意区分内容媒体与源头像，见 §FalsePositives）。
- **signal — `/all` 时序错位**：肉眼可见时间戳不是按 `fetched_at` 倒序排（与 ux-contract §Surface-Timeline 默认状态相违）→ 触发调查。
- **signal — `/all` 的筛选叠加在 AI Planet 上行为与 AIHOT 不一致**：例如 AIHOT 上 channel + category + page 同时生效，AI Planet 上某维静默失效 → 触发调查（ux-contract §Journey-TimelineFilterByChannel 已承诺多维同时生效）。

### §FeedParity-DailyShape — `/daily` 日报对比

- **预期**：日报形态本身可以与 AIHOT 不一致（节数、节标题、刊头版式属 §IntDiv-DailyReportShape）。但「当日有内容时按主题分节展示、文章是外链、归档可点」三件事应一致。
- **signal — 当日 `/daily` 完全空白**（既非节皆空的空态文案，也非 `LOADING`）→ 触发调查（与 ux-contract §QualityBar-DailyCompleteness 关联）。
- **signal — 历史日深链白屏 / 跳错日期**：与 ux-contract §Feature-DailyNav 边界承诺不符 → 触发调查。

---

## §3 Algorithm Soundness Signals（Layer 3）

层级背景：**当 Layer 2 出现明显差异时，往往不是 UI 问题而是算法侧问题**。本节定义「该往哪里追」。signal-only — 每条都不给阈值，列出"出现 X 现象时该怀疑 Y 环节"。

> 跑 Layer 3 时建议至少跨 3-7 天滚动窗口取样，单日数据噪音过大不足以下结论。

### §AlgoSignal-ScoreDistribution — 评分分布

- **观察**：AI Planet 精选条目数字分的分布形状（堆叠区间、中位数、是否堆在阈值边缘）。
- **signal — 大量条目堆在 65–70**（刚过 6.5 阈值）：可能 LLM 5 维评分压缩到中间段，代码权重未拉开区分度，或阈值刚好卡在密集区。
- **signal — AI Planet 精选下沿（如 < 60）频繁出现，而同期 AIHOT 精选肉眼判都是更重要事件**：可能阈值/权重失衡，召回压过 precision（与 VISION §4.2「Precision ≫ Recall」相违）。
- **signal — 同一条目分数在不同刷新间跳变**：与 ux-contract §Feature-Score「同一 ruleset 内不应跨刷新跳变」相违 → 调查 ruleset 边界 / 缓存。
- **追查方向**：评分 prompt / 权重公式 / 阈值（VISION §4.1）。
- **数据源**：`/api/v1/curated`（含分数）、`/api/v1/timeline?limit=N`（含分数）。

### §AlgoSignal-CategoryDistribution — 分类分布

- **观察**：同窗口 AIHOT vs AI Planet 精选中「模型 / 产品 / 行业 / 论文 / 技巧」5 类的比例。
- **预期方向**（来自 VISION §3）：AI Planet 应在「模型 / 论文 / 技巧」上比 AIHOT 略偏重；AIHOT 应在「产品 / 行业」（含自媒体流量话题）上略偏重。
- **signal — AI Planet 上「产品」「行业」中混入大量营销 / KOL 二次创作**：与「工程师权重」相违 → 调查权重或预筛（VISION §4.1 cheap-filter 环节）。
- **signal — AI Planet 上某分类长期 0 条**（如连续 5+ 天没有「论文」）→ 调查信源池在该分类的覆盖（Layer 1 联动）或预筛把该类全打掉。
- **signal — AI Planet 分类与 AIHOT 在「模型」上差距巨大且工程师方向也没承接**：例如 AIHOT 一周内出 5 条模型发版，AI Planet 一条没收 → 召回严重不足。
- **追查方向**：分类标签语义（ux-contract §Feature-CategoryFilter「不是 tag 字面匹配」）、预筛模型选型、信源池倾斜。

### §AlgoSignal-EventCoverage — 单事件多源覆盖

- **观察**：AIHOT「关联讨论 N 条」事件 vs AI Planet 同事件「关联讨论」（ux-contract §Feature-DupAggregation）。
- **signal — AIHOT 显示 5 条关联讨论，AI Planet 同事件只 1 条主条**：抓取漏或聚类没匹配上。
- **signal — AI Planet 同事件以多主条形式出现而非聚合**：dedup / 聚类失效（与 VISION §3「embedding 事件聚类」复用机制相违）。
- **追查方向**：embedding 聚类相似度阈值、官方源选主条策略、URL/内容指纹 dedup。

### §AlgoSignal-FreshnessLag — 时效差

- **观察**：同一事件在两站 timeline 首次出现的时间差。
- **signal — AI Planet 落后 AIHOT > 1 个 pipeline 周期**（15 min cron + buffer，与 ux-contract §QualityBar-DataFreshness 关联）：抓取调度 / 评分流水线延迟。
- **signal — AI Planet 完全早于 AIHOT 出现某事件**：本身不算 issue（信源接入差异可解释），但若叠加分数异常或类别异常，可能是评分误吞。

### §AlgoSignal-ReasonAndTagQuality — 推荐理由 / tag 质量

- **观察**：同一条目（或同类条目）的推荐理由文案、tag 集合。
- **signal — AI Planet 推荐理由系统性短于/长于 AIHOT 一个数量级 / 大段重复模板**：评分 prompt 输出不稳定或后处理裁切失衡。
- **signal — tag 里出现「精选」「热门」等非分类语义文案 / 重复堆叠 / 全空**：tag 生成或后处理漂移（与 ux-contract §Feature-CategoryFilter 语义分类相违）。
- **追查方向**：LLM 评分 prompt（VISION §4.1 把 LLM 职责限定在 5 维评分）、tag 提取与后处理。

---

## §4 Intentional Divergence（Parity Out-of-Scope）

VISION §3 已明示的差异方向。**parity 测试若命中本节描述的情形，不报为 issue**——它们是产品设计选择，不是产品缺陷。

### §IntDiv-EngineerWeight — 工程师权重 vs 自媒体口味

- AI Planet 给「工程实践 / 工具链 / 模型工程」类条目更高权重。
- 例：同事件 AIHOT 选「AI 大佬八卦」、AI Planet 选「模型 RL 训练细节」——by design。
- 来源：VISION §3 异-表「评分权重偏向」、§4「五条核心原则」。

### §IntDiv-SelfMediaTopic — 自媒体 / 流量选题

- AIHOT 服务「找选题」，收录大量 KOL 二次创作 / 流量话题 / X 转发。AI Planet 服务「日常消费」，对纯自媒体话题不强追。
- 例：AIHOT 有 / AI Planet 无「某 KOL 吐槽 GPT-X」「某大 V 转发评论」——不算 issue。
- 来源：VISION §2「非用户/非场景」、§3 异-表「工作目标」。

### §IntDiv-VolumeDifference — 精选数量差

- 两站每日精选数量不必一致。AI Planet 偏 Precision，可能更少。
- 仅当出现极端情形（0 条 / 10x 量级）才作算法信号（见 §AlgoSignal-CategoryDistribution / §AlgoSignal-ScoreDistribution）。
- 来源：VISION §4.2「Precision ≫ Recall」。

### §IntDiv-DailyReportShape — 日报形态差异

- AIHOT 是否有 `/daily`、节数、节标题措辞、刊头排版可能与 AI Planet 不同。
- AI Planet 5 节固定（模型 / 产品 / 行业 / 论文 / 技巧，见 ux-contract §Feature-DailySections）。
- 节标题措辞、节顺序、刊头视觉差异不算 issue；仅「该有内容但全节皆空」才算（已被 ux-contract §QualityBar-DailyCompleteness 覆盖）。
- 来源：VISION §3 + ux-contract §Feature-DailySections。

### §IntDiv-IMandFutureCapabilities — IM 推送 / 趋势预测 等 v2+ 能力

- AIHOT 无 / AI Planet 有：不是 issue。
- AIHOT 有 / AI Planet 无：若属于 VISION §8 phase 路线图中尚未到达的 phase（v2+ Telegram 推送 / v3 趋势预测 / 反馈调权 UI 等），不是 issue。
- 来源：VISION §8 阶段路线图。

### §IntDiv-WriteSurfaceShape — 写入口形态差异

- AIHOT 写入口是 MCN 团队的内部系统；AI Planet 写入口仅 owner CLI（`./run.sh admin ...`，见 ux-contract §Surface-AdminCLI）。
- 两端 Web 是否暴露任何写控件不参与 parity（AI Planet 公开页面零鉴权零写入是核心设计，见 ux-contract §OutOfScope-LoginGate）。
- 来源：VISION §3 异-表「写入用户」、§5 反例。

---

## §5 测试协议

### 何时跑

- **周期性**：建议每月一次抽查（频率可按 owner 实际节奏调整）。
- **触发性**：评分 ruleset / 信源池 / 阈值 任一发生大改后，**必须**跑一次 parity 测试，作为 VISION §4.3「可回测、可对比」的副产物。
- **验证性**：owner 主观体感「AI Planet 看上去不像 AIHOT 了」时立刻跑。

### 怎么跑

1. **环境对齐**：同窗口并排打开两端 `/`、`/all`、`/daily`，按 §0 抓取约定校齐 timezone / locale / 分辨率 / 时段。
2. **跑 Layer 1**：用 §1 对照 `data/sources.toml` 与 AIHOT 实际暴露源（页面源 chip / `/about` 上的源列表 / 已捕获的 baseline 快照三者交叉）。产出 source diff 表。
3. **跑 Layer 2**：用 §2 的 4 条 §FeedParity-* 逐 surface 比较。每个 signal 命中即记 evidence（截图 + DOM 抽样 + 对应 API 响应片段）。
4. **跑 Layer 3**：用 §3 的 5 条 §AlgoSignal-* 做分布 / 时效 / 文本质量取样。**至少跨 3-7 天滚动窗口**，避免单日噪音。
5. **过 carve-out**：每条信号过一遍 §4，命中故意差异的直接排除并标注命中的 §IntDiv-* 锚点（方便回看）。
6. **落地**：剩下的信号按性质分流：
   - 实现端缺陷 / 数据漂移 → `docs/issues/ux-issues.md`
   - ux-contract 与实际行为不一致 / parity contract 与实际行为不一致 → `docs/issues/ux-contract-issues.md`（带 `drift` 或 `expansion` type）

### 抓取与证据建议

- **AIHOT 端**（外部黑盒）：
  - 桌面截图（`/`、`/all`、`/daily` 各一张）
  - 首屏前 ~20 卡的肉眼或 DOM 抽样：source / 标题 / 是否带精选标 / 是否带数字分 / 是否带推荐理由 / 是否带媒体图（区分内容图与源头像，见 §FalsePositives）
  - 分布统计：分类比例 / 媒体覆盖率 / 推荐理由覆盖率
- **AI Planet 端**：
  - 同位置同样的截图与 DOM 抽样
  - API：`/api/v1/curated`、`/api/v1/timeline?limit=40&page=1`
  - 配置：`data/sources.toml` 当前快照、`/about` 信源表渲染结果
- **取样窗口**：Layer 1/2 同日即可；Layer 3 至少跨 3-7 天滚动窗口。
- 证据落点：`plans/<product-slug>-user-test-<YYYY-MM-DD>/evidence/`（与 `~/.claude/references/ux-test-protocol.md` 一致）。

### 常见误判（§FalsePositives）

- **把源头像当内容媒体**：AIHOT 与 AI Planet 都会渲染 source avatar；它不是内容图，不参与「媒体覆盖」统计。
- **把 §4 carve-out 当 issue**：AIHOT 上的 KOL 八卦 / 自媒体话题 / 大 V 转发在 AI Planet 缺位 ≠ 缺陷（命中 §IntDiv-SelfMediaTopic）。下结论前必过一遍 §4。
- **用单日数据下 Layer 3 结论**：分布、时效、覆盖类信号都需要跨 3-7 天滚动窗口才稳。单日噪音可能把 ScoreDistribution / CategoryDistribution 推到极端。
- **只比 `/` 不比 `/all`**：评分逻辑差异往往在 `/all` 上更明显（`/` 是滤后下游）。`/` 看上去正常 ≠ 评分链路正常。
- **拿 API 有分数当 UI 有分数**：API 返回字段不代表 UI 已渲染。Layer 2 的「曾入精选条目在 `/all` 保留元数据」必须看 UI 不能只看 API。
- **拿 baseline 快照当 AIHOT 当前态**：`data/sources.toml` 注释里的 baseline 日期（2026-05-12）是历史快照；AIHOT 之后可能加 / 删源。Layer 1 跑测时若 baseline 已过 30 天，先刷新 baseline 再做 diff。

---

## §6 演进

- 跑测中发现「Layer X 没承接但确实是个 parity 维度」时，作为 `expansion` 候选写入 `docs/issues/ux-contract-issues.md`，由 owner 评审是否升级为本 contract 的 entry。
- AIHOT 自身演进（去掉某个 source、改了日报版式等）时，**先**问「这是不是应该跟」，**后**改本 contract。AIHOT 不是规范来源，是参照锚点；VISION 才是规范来源。
- ID（§SourceParity-* / §FeedParity-* / §AlgoSignal-* / §IntDiv-*）允许改名、重排、合并、拆分；改时确认 `docs/issues/` 下没有悬空引用。
