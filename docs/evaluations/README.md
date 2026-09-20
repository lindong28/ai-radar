# AIHOT 对齐评测

> [Developer] · 2026-09-17 首版实现与真实实跑。设计来源：[已认可四对象设计](../references/aihot-eval-foundations/README.md)。旧实验不迁入新成绩。

本体系衡量同窗、共同来源的全部动态成员、可见分数、富化字段与精选成员。不评事实身份、内容组织、排序、去重质量、微信独有解读；身份匹配只服务正确计数。

代码入口及历史兼容边界见 [evals 导航](../../evals/README.md)；新运行只从下表四个当前 benchmark 进入。准入候选统一位于 `evals/news-admission/prompts/`，旧路径兼容但不再作为新文档默认。

## 当前建题默认（2026-09-19）

2026-09-20 已整理活动题库：四个对象目录各只留下表所列的当前 benchmark，旧目录移出至独立归档树。当前评分和富化的 `v1` 通过链接继续使用冻结原件，日常命令路径不变；归档位置、共享证据依赖及历史路径转换见[资产说明](assets.md#活动题库与历史归档2026-09-20)。

2026-09-20 起，先应用[人评标注与优先级](human-labels.md)：明确用户人评优先于 AIHOT/模型派生参考，按对象和字段保存，建题后显式应用；历史观测标签不覆盖。该页包含本批原票位置、导入/复用命令、判官分歧处理和后续 reason-first 要求。

按消费者契约区分 benchmark：输入格式、评测语义或处理逻辑不同，使用不同 benchmark；契约不变的扩题、去重或来源适配，只递增数据版本 `v1`、`v2`，变化写进该版 README。`schema_version` 是载荷格式，历史 `object-specific-v2` / `aihot-original-v3` 是建题规则标签，都不是新目录的版本名。共享池与独立逐条题集分开，不以改目录名掩盖运行语义差异。

原始档案共享，但各对象独立选题，不先取四对象条件交集。O2/O3 缺少 Radar raw 时可显式使用 AIHOT 原标题与绑定原文；O1 不补这种只有可见正例的数据；O4 局部阈值不冒充完整候选组。操作见[建题与扩展](benchmarks/object-datasets.md)，原始输入与题数见[库存](benchmarks/inventory.md)。早先 v1 身份迁移不改题目；随后获批的 O1 时间语义修订已另建 `aihot-observed-membership/v1`，不能与旧 gold 的成绩直接相减。

判断原始数据能否使用，先按[数据充分性口径](benchmarks/object-datasets.md#data-sufficiency)分别核各对象所需的输入、参照与候选组。恢复／补采后的内容足够可以使用；“连续窗口”不等于要求每次计划抓取都成功。

schema2 独立建题/校验与 schema1 全池推理分开：O1 的[独立 prefilter 入口](../../evals/news-admission/aihot-observed-membership/README.md)支持 validate、模型 run、固定抽样与恢复归档，真实成绩见[状态](news-admission/status.md)；其余新叶子 CLI 仍只做 validate，已有预测可复用 `evals._shared.metrics.score`。旧 runner 不接独立题库；逐条评分映射、富化理由和精选阈值的推理/自动归档适配仍未完成，具体缺口归各对象 status，不属于本轮 prefilter 任务。

后续扩题默认用 `build --base <既有版本>`，可重复传入多个 schema1/schema2 版本：合并冻结的原始输入和 AIHOT 证据，去重后按当前逐对象规则重验，输出不可覆盖的新 vN 与逐题变化清单。旧题库只用于比较，不能直接拼 cases 或把版本题数相加。完整命令与 added/retained/updated/removed 口径见[扩展操作](benchmarks/object-datasets.md#后续-session-默认合并去重按当前设计检查有效性)。

2026-09-18 URL/版本修订：X 别名按同来源推文 ID 配对；只以评测对象相关的实质内容变化判多版本，不再因 published_at、HTML、来源标签或标题标点变化排题。规则与迁移边界见[实质性规则](benchmarks/object-datasets.md#实质内容版本与-url-身份2026-09-18-用户修订)，当前版本及精确题数以[库存](benchmarks/inventory.md)顶部为准。

| 对象与三层说明 | 当前 benchmark / 输入版本 | 优化维度 |
| --- | --- | --- |
| [news-admission](news-admission/README.md) | [aihot-observed-membership / v1](news-admission/aihot-observed-membership/v1/README.md) | 冻结批次已观察收录主集 precision / recall，补充集只看 recall |
| [visible-score](visible-score/README.md) | [aihot-score-pointwise / v1](visible-score/aihot-score-pointwise/v1/README.md) | 最终展示分 MAE |
| [content-enrichment](content-enrichment/README.md) | [aihot-enrichment-fields / v1](content-enrichment/aihot-enrichment-fields/v1/README.md) | 分类、标签确定性指标；标题/摘要/理由分别判分 |
| [featured-members](featured-members/README.md) | [aihot-featured-threshold / v1](featured-members/aihot-featured-threshold/v1/README.md) | 局部阈值成员 precision / recall；不覆盖全池 |

后续迭代使用 user-scope `eval-workflows iterate-eval-system`，从本入口定位对象，再读 workflow、assets 及对象 status。没有该 skill 的 agent 沿项目入口执行相同命令和归档规则。

## 历史首版选择（2026-09-17）

- 题库只放本机 `~/research/video-eval-arena/data/benchmarks/ai-radar/<benchmark>/<target>/<version>/`，不使用 DGX。原始输入保留过滤前负例，不取两站 URL 交集冒充完整候选池；不使用旧 T5。
- 首批取 2026-09-17 起可证明连续的共同窗口；现存日窗不能直接证明当日参照，补独立小时窗捕获而不改生产调度。
- 文本判官采用固定可配置 DeepSeek `deepseek-v4-flash-ga-260731`，每字段 0/1/2 分。12 个用户确认样本（每字段4个），开发6、独立验证6；验证全一致才在该判官身份下采信。设计认可不等于已经有用户票；小校验不能证明广泛可靠性。
- 同题逐指标比较：至少一项改善，其他不退步，无未完成题。不设绝对达标阈值，不声称统计显著或整体拟合完成。
- 模型调用不限总次数，但必须先小规模 smoke、成功后再扩大，按身份复用有效输出，不自动换模型/供应商。

## 当前独立评分入口（2026-09-20）

O2 的 schema 2 逐条评分、reason-first 原始响应归档、MAE/Spearman与零调用指标补算已接通；当前方向为作者结构启发的五维语义研究，历史校准仅保留为对照。已有固定开发200、历史回归200及最新回归100等不同题集，不是全量3,475题。当前结果、候选及生产边界见 [visible-score/status](visible-score/status.md)，复用命令见[执行入口](../../evals/visible-score/aihot-score-pointwise/README.md)。

## 历史共享池实施进度（2026-09-17）

2026-09-20：该批 33 份实验原件已按用户要求清退，85 行旧指标退出查询。以下仅保留当时实施记录，不代表仍能读取旧预测或以此继续优化；当前题库、prefilter 实验与人评不受影响，见[资产说明](assets.md#原始参照历史运行与-support-分区)。

旧共享池四对象的推理/缓存、自动指标、判官/校准、固定池规则回放、同题比较与资产查询已有实现。该轮冻结五小时数据，完成真实 smoke 和完整候选池调用；O1 有完整读数，文本判官仅诊断。完整候选池存在一条供应商拒答，O2/O4 不得冒称完整；这些是旧 benchmark 成绩，不代表新逐条链已运行。实际数值及当前缺口见库存、台账和 `<target>/status.md`。

## 2026-09-17 历史用户澄清（O1 时间语义已由 9/19 修订替代）

O1 对每条原始数据用它自身生成时间戳 t（无则 Radar 抓取时间），在 AIHOT 的 (t−12小时,t+12小时) 范围有匹配则预期通过；无匹配且该24小时采集完整才预期不通过，不完整则从O1题集排除，但原始数据保留。负例同时检查两侧完整性。O1直接评prefilter，不混入后置评分门槛；这项覆盖旧报告的窗口新增成员标签。其它三个对象范围不变。

定位改进先读对应 `<target>/status.md`；执行与新增版本读 [workflow](workflow.md) 及对应 `<target>/<benchmark>/<version>/README.md`；路径和三层归属读 [assets](assets.md)，实际存量读 [inventory](benchmarks/inventory.md)，比较归因读 [ledger](experiments/ledger.md) / [hypotheses](experiments/hypotheses.md)。旧规则文档有效语义已并入对象与 v1 README，历史修订保留在库存与 Git，不另留一套权威路径。
