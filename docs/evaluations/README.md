# AIHOT 对齐评测

> [Developer] · 2026-09-17 首版实现与真实实跑。设计来源：[已认可四对象设计](../references/aihot-eval-foundations/README.md)。旧实验不迁入新成绩。

本体系衡量同窗、共同来源的全部动态成员、可见分数、富化字段与精选成员。不评事实身份、内容组织、排序、去重质量、微信独有解读；身份匹配只服务正确计数。

## 当前建题默认（2026-09-18）

用户已认可逐对象独立建题，入口为[建题与扩展操作](benchmarks/object-datasets.md)。O2/O3 的当前规则在 `benchmarks/<target>/<benchmark>/aihot-original-v3/README.md`：仅缺少 Radar raw 时可显式接入 AIHOT 原标题与绑定原文；O1 不补这种仅可见正例，O4 本轮不扩题并保留连续完整候选组要求。四份 `object-specific-v2/README.md` 保留历史规则及适用边界。原始档案共享，但不先取符合所有对象条件的交集。新数据按 `<target>/<benchmark>/<version>` 存储；旧 v1 路径不迁移。下文 2026-09-17 的共同窗口、双侧完整性和全池实跑均为历史 v1 说明，不能作为所有独立对象的入题条件。

v2 建题/校验与 v1 全池推理分开：新脚本不改网站算法，旧 runner 明确拒绝 v2。逐条评分映射与精选阈值的运行适配状态见各建题说明；题库完成不等于新推理链完成。

后续扩题默认用 `build --base <既有版本>`，可重复传入多个 v1/v2 版本：合并冻结的原始输入和 AIHOT 证据，去重后按当前逐对象规则重验，输出不可覆盖的新版本与逐题变化清单。旧题库只用于比较，不能直接拼 cases 或把版本题数相加。完整命令与 added/retained/updated/removed 口径见[扩展操作](benchmarks/object-datasets.md#后续-session-默认合并去重按当前设计检查有效性)。

| 对象 | 代码与指标入口 | 优化维度 |
| --- | --- | --- |
| news-admission | [aihot-all-members](../../evals/news-admission/aihot-all-members/README.md) | prefilter 通过集合 precision / recall |
| visible-score | [aihot-visible-score](../../evals/visible-score/aihot-visible-score/README.md) | 最终展示分 MAE |
| content-enrichment | [aihot-enrichment](../../evals/content-enrichment/aihot-enrichment/README.md) | 分类 accuracy、标签 exact-set accuracy、标题/摘要/理由分别判分 |
| featured-members | [aihot-featured-members](../../evals/featured-members/aihot-featured-members/README.md) | 固定预测输入后的成员 precision / recall |

后续迭代使用 user-scope `eval-workflows iterate-eval-system`，从本入口定位对象，再读 workflow、assets 及对象 status。没有该 skill 的 agent 沿项目入口执行相同命令和归档规则。

## 本次明确选择

- 题库只放本机 `~/research/video-eval-arena/data/benchmarks/ai-radar/<benchmark>/<target>/<version>/`，不使用 DGX。原始输入保留过滤前负例，不取两站 URL 交集冒充完整候选池；不使用旧 T5。
- 首批取 2026-09-17 起可证明连续的共同窗口；现存日窗不能直接证明当日参照，补独立小时窗捕获而不改生产调度。
- 文本判官采用固定可配置 DeepSeek `deepseek-v4-flash-ga-260731`，每字段 0/1/2 分。12 个用户确认样本（每字段4个），开发6、独立验证6；验证全一致才在该判官身份下采信。设计认可不等于已经有用户票；小校验不能证明广泛可靠性。
- 同题逐指标比较：至少一项改善，其他不退步，无未完成题。不设绝对达标阈值，不声称统计显著或整体拟合完成。
- 模型调用不限总次数，但必须先小规模 smoke、成功后再扩大，按身份复用有效输出，不自动换模型/供应商。

## 实施进度

四对象的版本化建题、推理/缓存、自动指标、判官/校准、固定池规则回放、同题比较与资产查询已实现。已冻结首版五小时数据，完成真实smoke和完整候选池调用；O1已有完整读数，文本判官仅诊断。完整候选池存在一条供应商拒答，O2/O4不得冒称完整；当前无用户校准票、无被采纳的优化。实际数值与每项缺口分别读库存、台账和 `objects/<target>/status.md`。

## 2026-09-17 实施中的用户澄清

O1 对每条原始数据用它自身生成时间戳 t（无则 Radar 抓取时间），在 AIHOT 的 (t−12小时,t+12小时) 范围有匹配则预期通过；无匹配且该24小时采集完整才预期不通过，不完整则从O1题集排除，但原始数据保留。负例同时检查两侧完整性。O1直接评prefilter，不混入后置评分门槛；这项覆盖旧报告的窗口新增成员标签。其它三个对象范围不变。

执行与新增版本读 [workflow](workflow.md)，路径和三层归属读 [assets](assets.md)，实际存量读 [inventory](benchmarks/inventory.md)，实验与优先缺口读 [ledger](experiments/ledger.md) / [hypotheses](experiments/hypotheses.md)。各对象五槽与三层状态见 objects/ 下的 design.md / status.md。
