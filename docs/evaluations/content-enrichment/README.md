# O3 · 字段富化

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

网站分类使用 [aihot-category-navigation / v1](aihot-category-navigation/v1/README.md)，其六类参考来自实际网页过滤成员，不与旧五类 API 的 category 混算。其它字段使用 [aihot-enrichment-fields / v2](aihot-enrichment-fields/v2/README.md)，按字段子集消费。缺少 Radar raw 时接入获准 AIHOT 原标题与绑定原文属于来源适配；网站六类 gold 则改变消费者语义，所以另开 benchmark。历史成绩保留原身份。

## 对象与边界

原始新闻 → 共享 enrich → 可见分类、标签、标题、摘要及推荐理由；精选理由按生产投影处理，不把中间 why_recommend 冒充最终值。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | 原始新闻 → 共享 enrich → 可见分类、标签、标题、摘要及推荐理由；精选理由按生产投影处理，不把中间 why_recommend 冒充最终值。 |
| ② 评测题 | 原始输入可用且对应 AIHOT 字段已观察；输入先 Radar raw，缺失时沿获准 AIHOT 原文 fallback。网站分类另取六类页面成员参考；tags/title/summary/reason 按字段建子集，已观察空标签有效，未知不填空。分类局部研究每题独立调用；完整 enrich 是另一组合测量，不外推。 |
| ③ 判官 | 三文本调用固定模型；分类/标签用确定性比较 |
| ④ 自动指标 | category_accuracy、tags_exact_set_accuracy；title/summary/reason各自0–2判官均分。固定可配置DeepSeek，模型/量表及调用参数入身份；十二题用户票分dev6/validation6，全一致才采信。未校验仍可保存诊断值，不可推动文本优化。 |
| ⑤ 判官校验 | 实现了用户票字节确认、分离开发/验证集与身份绑定；真实校验尚待用户票 |

分类执行与指标定义：[六类入口](../../../evals/content-enrichment/aihot-category-navigation/README.md)、[metrics.json](../../../evals/content-enrichment/aihot-category-navigation/metrics.json)。独立单次 Flash 调用使用原始 title/body，返回 reason-first 六类，复用确定性计分与自动归档；无需 LLM 判官。共享 enrich_v2 使用同一分类 rubric，但独立分类成绩不代表完整 enrich 已验证或生产已切换。其它字段入口仍为 [fields](../../../evals/content-enrichment/aihot-enrichment-fields/README.md)；其独立推理和推荐理由投影仍待接线，文本判官采信仍需要用户票。

## L2 数据资产

本对象七类资产沿 [assets](../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。文本判词与用户票适用，当前零用户票。题量与已有成绩在 [库存](../benchmarks/inventory.md) 和 [台账](../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
