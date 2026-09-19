# O3 · 字段富化

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

当前独立 benchmark 为 [aihot-enrichment-fields / v1](aihot-enrichment-fields/v1/README.md)，按字段子集消费。缺少 Radar raw 时接入获准 AIHOT 原标题与绑定原文是生产者侧来源适配，不改变输入/参考契约，因此不另开 benchmark。历史 `aihot-enrichment` 的全池执行及推荐理由投影保留为旧 benchmark，不将其成绩接到新题库。

## 对象与边界

原始新闻 → 共享 enrich → 可见分类、标签、标题、摘要及推荐理由；精选理由按生产投影处理，不把中间 why_recommend 冒充最终值。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | 原始新闻 → 共享 enrich → 可见分类、标签、标题、摘要及推荐理由；精选理由按生产投影处理，不把中间 why_recommend 冒充最终值。 |
| ② 评测题 | 原始输入可用且对应 AIHOT 字段已观察；输入先 Radar raw，缺失时沿获准 AIHOT 原文 fallback。category/tags/title/summary/reason 分别建子集，已观察空标签有效，未知不填空。共享一次 enrich，不因五字段重复调用五次。 |
| ③ 判官 | 三文本调用固定模型；分类/标签用确定性比较 |
| ④ 自动指标 | category_accuracy、tags_exact_set_accuracy；title/summary/reason各自0–2判官均分。固定可配置DeepSeek，模型/量表及调用参数入身份；十二题用户票分dev6/validation6，全一致才采信。未校验仍可保存诊断值，不可推动文本优化。 |
| ⑤ 判官校验 | 实现了用户票字节确认、分离开发/验证集与身份绑定；真实校验尚待用户票 |

执行与指标定义：[独立题库入口](../../../evals/content-enrichment/aihot-enrichment-fields/README.md)、[metrics.json](../../../evals/content-enrichment/aihot-enrichment-fields/metrics.json)。叶子 CLI 校验资产，现有 `metrics.score` 按字段计算已有预测；逐对象模型运行、独立推荐理由投影及自动归档仍待接线，文本判官采信仍需要用户校验票。旧全池执行见 [workflow](../workflow.md#历史-schema1共同窗口与全池运行)。

## L2 数据资产

本对象七类资产沿 [assets](../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。文本判词与用户票适用，当前零用户票。题量与已有成绩在 [库存](../benchmarks/inventory.md) 和 [台账](../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
