# O1 · prefilter 准入

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

## 对象与边界

原始新闻 → prefilter 的通过/不通过。按用户最新逐条时间匹配规则，不再用后置 relevance 门槛替代 prefilter。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | 原始新闻 → prefilter 的通过/不通过。按用户最新逐条时间匹配规则，不再用后置 relevance 门槛替代 prefilter。 |
| ② 评测题 | 对共同来源窗口内每条唯一 raw，用自身 published_at 为 t，无则 fetched_at；AIHOT timeline 严格在 (t−12h,t+12h) 且同来源/URL 匹配为正例。无匹配且 Radar 全 S 的应采集槽与 AIHOT 参照完整才为负例；其余仅从 O1 题集中排除，完整 raw 保留。没有 raw 的 AIHOT 项不再属于这个逐 raw prefilter 题集。 |
| ③ 判官 | 不适用；确定性指标直接比较参考 |
| ④ 自动指标 | precision、recall。判官及判官校验不适用。只有正例时精确率无区分拒绝能力，不据此宣称过滤已优化。 |
| ⑤ 判官校验 | 不适用；通过有相反结果的确定性测试核计数 |

执行与指标定义：[benchmark入口](../../../../evals/news-admission/aihot-all-members/README.md)。本对象具体数值以机器 [metrics.json](../../../../evals/news-admission/aihot-all-members/metrics.json) 为权威。

## L2 数据资产

本对象七类资产沿 [assets](../../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。本对象无LLM判官，判词和人评校验不适用；对象自身LLM调用日志仍保存。题量与已有成绩在 [库存](../../benchmarks/inventory.md) 和 [台账](../../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
