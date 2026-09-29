# O3 · 字段富化

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

实验标记、实际父方案、输入窗口和运行关联见[方案履历](versions.md)。2026-09-29 用户授权挑选最佳结果作为线上主方案并整合本地 main，本次采用 V2 Pro 为分类正式版 **0.1.0**；采用决定、代码接入和线上部署是三个不同状态，当前边界见 [status](status.md#当前采用分类-010v2-pro)。较早研究阶段的“未晋级”记录保留当时语义。

网站分类使用 [aihot-category-navigation / v1](aihot-category-navigation/v1/README.md)，其六类参考来自实际网页过滤成员，不与旧五类 API 的 category 混算。其它字段使用 [aihot-enrichment-fields / v2](aihot-enrichment-fields/v2/README.md)，按字段子集消费。缺少 Radar raw 时接入获准 AIHOT 原标题与绑定原文属于来源适配；网站六类 gold 则改变消费者语义，所以另开 benchmark。历史成绩保留原身份。

## 对象与边界

原始新闻及已取得的正文/引用 → enrich_v2 的其它字段富化 + 独立分类 → 可见分类、标签、标题、摘要及推荐理由。分类 0.1.0 只接管 primary_category，is_opinion 按主类是否为 opinion 映射；不把该兼容布尔值当成独立观点检测的评测结论。精选理由按生产投影处理，不把中间 why_recommend 冒充最终值。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | 分类使用独立 V2 Pro 调用，沿用其它字段的 enrich_v2 调用及投影；完整 enrich 组合与分类局部成绩分开。 |
| ② 评测题 | 原始输入可用且对应 AIHOT 字段已观察；输入先 Radar raw，缺失时沿获准 AIHOT 原文 fallback。网站分类另取六类页面成员参考；tags/title/summary/reason 按字段建子集，已观察空标签有效，未知不填空。分类局部研究每题独立调用；完整 enrich 是另一组合测量，不外推。 |
| ③ 判官 | 三文本调用固定模型；分类/标签用确定性比较 |
| ④ 自动指标 | 分类为可接受集合命中与单标签子集六类P/R；标签为tags_exact_set_accuracy；title/summary/reason各自0–2判官均分。文本判官使用固定可配置DeepSeek，模型/量表及调用参数入身份；十二题用户票分dev6/validation6，全一致才采信。未校验仍可保存诊断值，不可推动文本优化。 |
| ⑤ 判官校验 | 文本判官实现了用户票字节确认、分离开发/验证集与身份绑定，真实校验尚待文本用户票；分类用确定性计分，不另加LLM判官 |

分类执行与指标定义：[六类入口](../../../evals/content-enrichment/aihot-category-navigation/README.md)、[metrics.json](../../../evals/content-enrichment/aihot-category-navigation/metrics.json)。分类 0.1.0 的运行实现为 [category_release.py](../../../src/airadar/enrich/category_release.py)，冻结规则为 [category_v2.txt](../../../src/airadar/enrich/category_v2.txt)；Ark Pro/high/32768、reason-first 六类及来源分明的 documents 材料呈现共同定义方案，不能仅换 rubric 后沿用默认 Flash。固定48题的复跑入口、模型参数和成绩边界见 [状态 V 阶段](status.md#v阶段类别准入与主分类选择分层)，无需 LLM 判官。默认评测 CLI 未显式选择这些参数时仍是原 A0 控制，不代表 0.1.0。其它字段入口仍为 [fields](../../../evals/content-enrichment/aihot-enrichment-fields/README.md)；其独立推理和推荐理由投影仍待接线，文本判官采信仍需要用户票。

## L2 数据资产

本对象七类资产沿 [assets](../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。分类已有真实人票，统一存于 [reviews.json](../../../human-evals/content-enrichment/reviews.json)，读取与优先级见 [分类人评](category-human-review.md)；不能把这些分类票算作文本判官校验票。题量与已有成绩在 [库存](../benchmarks/inventory.md) 和 [台账](../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
