# 20260929-bd7c：采用 V2 Pro 作为新闻分类正式主方案

- Status: accepted for production implementation；发布状态见[分类状态](../evaluations/content-enrichment/status.md)
- Date: 2026-09-29
- Scope: 新闻六类主分类，正式版本 `0.1.0`；不涉及评分、准入或其余富化字段的方案更换

## Context

用户要求“挑选目前为止的最好的结果，作为线上使用的主方案，放到 main branch，对目前为止做的所有评测和优化工作进行收尾”。这次授权把已完成的分类研究收敛为正式实现，不再以修满开发题为无限搜索条件。此前 [b7e1](20260929-b7e1-separate-category-eligibility-and-selection.md) 和 [9a0d](20260929-9a0d-repair-v1-category-boundaries.md) 只批准离线研究；它们的实验结论、当时生产边界和冻结资产保持原样，本记录新增生产采用决定。

在同一最新人票、同一 R8 冻结材料的 48 道已见开发题上，U2 两次为 45/48、44/48，V1 为 39/48、43/48，V2 两次均为 46/48（95.83%）。原始共同口径记录为 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-29/01-44-28/v-final-comparison.json`；逐类指标、配对修复/回退、实际输入与剩余错误在[分类状态 V 阶段](../evaluations/content-enrichment/status.md#v阶段类别准入与主分类选择分层)。这些是选型证据，不是未见题、完整 361 题或线上总体效果的证明。

## Options Considered

| 方案 | 可取之处 | 本次不选的理由或代价 |
|---|---|---|
| V2 Pro，两层分类规则 | 类别证据准入与主分类选择职责分开；恢复 V1 已暴露的定义边界后，两次固定题命中相同的 46 题 | 仍有 2 题错误；独立调用与 reasoning 增加资源需求，尚无同口径 Flash 成本/延迟比较或未见集读数 |
| 保留 U2 Pro | 同模型与材料下已有两次较高读数，可作冻结对照 | 新票口径 45/48、44/48，且没有采用本轮已验证的两层规则收敛 |
| 采用 V1 | 已具备两层结构 | 准入定义漂移带来新回退，两轮 39/48、43/48，V2 已作针对性修正 |
| 继续采用历史 C5 Flash 或联合富化直接分类 | 旧运行及消费者已有证据；联合富化无需额外分类调用 | 不据不同输入/人票的旧分数宣称与 V2 同题孰优；也不能直接假定 Flash 承接 V2 后仍有 Pro 的效果。当前用户要求先采用现有最佳结果，本次不另开模型迁移实验 |

## Decision

采用 V2 Pro，正式分类版本 `0.1.0`。判定先看六类所需证据是否成立，再在成立的类别中按材料主要贡献选一个主类；不是固定类别优先级，也不以当前帖“有观点”压掉引用中更具体的成果、方法或行动。冻结分类 rubric 为 [`src/airadar/enrich/category_v2.txt`](../../src/airadar/enrich/category_v2.txt)，完整 system/user prompt 由 [`category_release.py`](../../src/airadar/enrich/category_release.py) 的 `render_prompt()` 组装，以运行 trace 为准；历史 `evals/content-enrichment/prompts/category-v2.txt` 保留作为冻结研究来源。

通过既有 gateway 固定调用 `personal_ark::deepseek-v4-pro-ga-260813`，`thinking=enabled`、`reasoning_effort=high`、`max_tokens=32768`、`temperature=0`、单次超时 90 秒。分类采用一次成功调用，失败最多三次业务尝试；不因结果不符合预期而重抽，不静默回退到旧分类。SDK 传输自动重试保持关闭。其余富化调用不随分类重试重新执行。

接入 `runner_v2.py` 的默认 provider 路径；保留标题、摘要、推荐理由和标签的既有生成逻辑，独立分类只覆盖 `primary_category`，并将 `is_opinion` 映射为主类是否为 `opinion`。后者是消费者兼容投影，不是本轮独立评测的新能力。显式注入 provider 的开发/测试调用不隐式追加真实模型调用。legacy runner 不在本次切换范围；CLI 通过 `--v2` 或 `AI_RADAR_ENRICH_V2=1` 进入 v2 路径。

运行时分类材料复用已经收集的原文、直接引用及文章补充，按来源角色分块；不把人评或其他模型的标签作为输入。生产与评测共享材料渲染器，不由生产代码导入 `evals/`。成功、失败的分类尝试均保留实际 prompt、模型配置、返回分类依据及调用身份；分类轨迹存入 enrich evaluation 的 `input_json.category_trace`，不向严格的富化输出 schema 增加字段。

将分类版本、prompt 和调用配置计入 v2 ruleset digest。标准调度中的近期窗口因新 stamp 可能重新处理；本轮不主动发起历史全库重算，也不把“未主动重算”写成“历史条目永远不变”。

## Consequences

- 这是用户认可的生产主方案，不再只是研究候选；本地 main 整合、远端部署、真实链路生效是不同状态，验收及发布读数由[分类状态](../evaluations/content-enrichment/status.md)单一维护，不以代码落盘宣称部署成功。
- 默认 v2 富化每条成功路径增加一次独立分类调用，最多三次分类尝试；Pro reasoning 与更多 token 带来额外费用/时延。历史运行金额未定价时仍记 unknown，不记零；本次不宣称已测得生产吞吐或成本。
- 48 题是反复用于归因优化的开发集，重复两次不变成 96 道独立题。六类 precision/recall 尚未全部达到 90%，产品 precision 与教程 recall 尤其未达；两条残余错题保留，不改 gold、不删题来制造通过。
- OpenAI 异常行为研究 `c0e253ca0a4644662a04c1d2` 和 Browser Use 工程拆解 `ea5b6370a7ee474eecea5174` 的实际材料已送达，剩余是研究/事件、方法/产品的选择边界。它们留在现有[逐题归因](../evaluations/content-enrichment/status.md#v阶段类别准入与主分类选择分层)，本次收尾不继续修改冻结 V2。
- 既有 benchmark、用户原票、模型意见、prompt、逐轮输入输出和评分记录继续保留；未来迭代以正式 `0.1.0` 为生产对照，研究候选仍单独命名。正式版本档案与复现导航见[方案履历](../evaluations/content-enrichment/versions.md)。
