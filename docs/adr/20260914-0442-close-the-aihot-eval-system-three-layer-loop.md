# ADR-20260914-0442：接通 AIHOT 拟合评测体系的三层闭环

- Status: accepted
- Date: 2026-09-14
- Relates: [ADR-006](./006-curated-archive-mode.md), [ADR-060](./060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md), [ADR-20260905-499e](./20260905-499e-aihot-reference-fit-eval-system.md), [ADR-20260910-3f8b](./20260910-3f8b-demote-papers-in-the-ordering-key-only.md)

## Context

项目已经有逐条 `eval-fit`、归档构成量具、若干历史基线和达标线，但它们没有组成一套可从建立、日常评测、归因优化、回归走到报告的长期体系。零调用审计把若干事实写死在代码里，不能区分“历史记录声称存在”与“当前资产可核验”；付费 `run` / `judge` 在模型调用前没有统一身份前检；轮次没有独立账本；归档权威记录与逐条拟合结果之间没有可核的引用关系；题集只覆盖上游标签和原始加权分，没有直接量最终展示标签、最终展示评分与标题。现有 ADR 索引还把已经设定的 v1 达标线写成“未定”。

用户要求把目标扩展到所有用户可见内容，并审核 L1 五槽、L2 数据资产与逐轮记录、L3 身份和防错治理，以及建立体系、日常评测、归因优化、回归和报告之间的接续；同时明确保留已有有效成果与历史证据，改变题集、参考答案、判官模型、目标标准或新增付费调用前必须另行确认。

## Decision

### 两个权威观察面各司其职

`eval-fit` 继续作为逐条参考输出拟合的 canonical harness；`measure_archive_composition.py --record` 继续作为真实归档首页构成的生产消费者权威观察面。前者不冒充最终归档，后者不替代逐条字段质量。`composition-history.jsonl` 仍是归档构成唯一权威记录；新增稳定 `record_id`，轮次账本只保存 `composition_record_id`、精确行摘要和相对路径，不复制构成指标。

### L1：保持 v1，平行建立 v2 直接展示面

保留 v1 题集、参考答案、判官模型和已批准阈值，不原地改写。平行的 v2 题集 schema 增加生产最终标签所需的 `source_name` / `source_kind`，自动指标同时保留上游原始标签与加权分代理指标，并直接计算最终 `topic_tags_v2` 标签；最终展示评分只在能重放完整精选集合时直接计算，逐题模式不得伪造 62–92 的版面分。标题进入同一语义判官的独立维度，但默认 judge 仍只运行既有 `summary,reason`，只有调用者显式传入 `--dimensions title,...` 才产生标题调用；标题读数固定标记 `accepted=false`、原因为“缺少标题专用判官校验”，在取得新的校验读数和目标标准授权前，不进入 threshold、baseline、regression 或优化结论。

v2 manifest 明示 `thresholds_status=absent`，不得自动继承 v1 阈值或基线。任何 v2 阈值、目标标准、参考答案或判官模型的变化仍须用户另行批准。

### L2：一份追加式轮次账本，只索引权威资产

新增独立 JSONL 轮次账本，记录 build、run、judge、canonical archive record 和 report 的状态迁移、输入身份、产物引用和调用者。`audit` 保持严格只读，不因检查而补写账本。每条记录有不可复用的 `event_id` 和 `attempt_id`；同一 `event_id` 的字节级重复可合并，内容冲突则 fail closed。自定义 `--record` 是探索性历史，默认不进 canonical ledger；显式把自定义 history 指向 canonical ledger 会在写入前拒绝。跨 worktree 的实时全局一致性不作虚假承诺：权威账本以当前已整合分支为准，整合时做语义并集。

历史 ADR 对人工判官票据的陈述保留为历史证据，但当前未找到可核验的对应资产时，审计必须写成“历史记录存在、当前资产未核实”，不得写成“从未存在”或“已经可用”。手工归因假设继续要求 `differential_prediction`；不再把一个并不能证明归因成立的机械字段检查冒充完成闸。

### L3：身份前检先于凭据和付费调用

每次 `run` / `judge` 都要求显式传入本次 identity spec。执行器先把题集、run 与 outputs（按阶段适用）冻结成一次内存字节快照，用该快照计算行为身份并在后续执行中消费同一快照；成功写入 observer-only 的 `started` 事件后调用 `eval-identity`。只有 exit 0 且身份收据成功保存，才允许读取 provider 凭据和初始化付费调用。exit 1、2、64 都写入 `identity_rejected` 并 fail closed；普通异常写终态事件，SIGKILL 等不可捕获中止留下 orphan `started`，由只读审计明确报为 interrupted / incomplete。

每次 attempt 使用不可覆盖的文件名。完整 spec 与 readout 放在 gitignored 的 run 目录；经 schema 约束且去除敏感信息的身份 manifest 进入主仓持久保存，并在短期 run 产物清理后继续存在。账本和身份 manifest 是 observer-only，不进入被评测对象的 behavior identity。

### 接通五个执行阶段

`build` 产出题集与 manifest 并登记资产；`run` 在身份放行后执行生产对象并固定 outputs 摘要；`judge` 独立完成判官调用和校验并固定 judgments/calibration 摘要；归档记录由权威量具写入后登记引用；`report` 先复算题集、outputs、judgments 与 calibration 的实际 SHA 并与各自生产阶段身份一致才计算指标，只消费身份相容、采信状态明确的读数并登记报告。`audit` 从真实题集、轮次账本、身份 manifest、归档记录和报告反推 L1/L2/L3/caller 状态，校验账本 schema/状态迁移并复算归档权威行摘要，不再维护一份硬编码自评答案。标题或其他未校验维度可以显示为诊断读数，但必须从阈值、基线、回归和优化结论中剔除。

## Options Considered

### 原地扩写 v1

否决。它会让既有基线和阈值的对象身份发生变化，并把历史读数伪装成仍可比较。

### 让归档量具取代逐条 eval-fit

否决。归档构成能直接量首页类别比例，却不能逐条评估摘要、理由、标题和标签语义；逐条评测反过来也不能伪装完整归档选择过程。

### 只补审计文案，不接真实执行入口

否决。硬编码自评即使改得更准确，也不能证明付费调用经过身份前检、轮次资产已登记或报告读数来自所声称的对象。

### 把账本作为归档指标的新权威副本

否决。复制 `composition-history.jsonl` 会产生两个可漂移的真相源；账本只保存可校验引用。

### 直接启用标题指标

否决。当前没有标题专用判官校验，也没有用户批准的目标标准；先接入但不采信，既暴露缺口又不制造假结论。

### 沿用 v1 阈值到 v2

否决。v2 增加输入字段和最终展示面，阈值对象已改变；自动继承会越过用户保留的目标标准裁决。

### 用一次成功终态代表所有中止

否决。SIGKILL 无法可靠写终态；保留 orphan `started` 并由审计报告，比伪造完成收据更可核验。

## Consequences

- 主仓可以在不发起任何付费调用、不改变现有题集与阈值的前提下完成 schema、账本、身份前检、审计、v2 builder/reader 和诊断指标接线。
- v2 权威题集属于私有 `benchmarks/aihot` 数据仓；在取得该仓归属授权并创建本地提交前，主仓只能把该资产明确报告为未完成，不能宣称 v2 已建立。
- 标题判官虽然可运行，但在标题专用校验与目标标准获批前，其结果只用于诊断，不得驱动回归或优化。
- 完整最终展示评分依赖同一轮完整精选集合。逐条 run 继续报告原始/排序代理分；归档重放面负责直接评分与最终标签的消费者读数。
- 轮次账本解决的是已整合历史的可追溯性，不承诺多个未整合 worktree 的实时单写入者一致性。

## Decision Review

独立评审先后指出：标题维度不得在未校验时进入采信结论；身份拒绝路径也必须保存收据；归档记录不能被账本复制成第二权威；跨 worktree 账本不能宣称实时全局一致；人工票据的历史存在性与当前资产可核验性必须分开。逐项收窄后最终 gate 为 `放行`，决策指纹为 `cc54e12a8e4aa577f30e17ba1e492f861c3db07486f4d84ffe292cee835194f8`。
