# O1 · prefilter 准入

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

当前独立 benchmark 为 [aihot-prefilter / v1](aihot-prefilter/v1/README.md)。它以单条原始输入建题，主集与仅召回补充集分开。历史 `aihot-all-members` 保留共享池消费方式和原成绩，不是同一 benchmark 的上一版。规则变化的解释归 README，数据版本仅用 `v1`、`v2` 递增。

## 对象与边界

原始新闻 → prefilter 的通过/不通过。按用户最新逐条时间匹配规则，不再用后置 relevance 门槛替代 prefilter。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | 原始新闻 → prefilter 的通过/不通过。按用户最新逐条时间匹配规则，不再用后置 relevance 门槛替代 prefilter。 |
| ② 评测题 | 共同来源未过滤 raw；自身 published_at 为 t，无则 fetched_at；AIHOT 在 (t−12h,t+12h) 同来源/规范 URL 匹配为正例。主集正负例均要求完整稳定的参照覆盖；不完整但已见正例单列仅召回，未匹配且不完整为未知。Radar 过程缺口不统一否决已取得题，不补 AIHOT-only 正例。 |
| ③ 判官 | 不适用；确定性指标直接比较参考 |
| ④ 自动指标 | precision、recall。判官及判官校验不适用。只有正例时精确率无区分拒绝能力，不据此宣称过滤已优化。 |
| ⑤ 判官校验 | 不适用；通过有相反结果的确定性测试核计数 |

执行与指标定义：[独立题库入口](../../../evals/news-admission/aihot-prefilter/README.md)、[metrics.json](../../../evals/news-admission/aihot-prefilter/metrics.json)。叶子 CLI 负责资产校验；现有 `metrics.score` 可对已有预测作确定性计分，逐对象模型运行与自动归档尚未接线。历史共享池操作见 [workflow](../workflow.md#历史-schema1共同窗口与全池运行)。

## L2 数据资产

本对象七类资产沿 [assets](../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。本对象无LLM判官，判词和人评校验不适用；对象自身LLM调用日志仍保存。题量与已有成绩在 [库存](../benchmarks/inventory.md) 和 [台账](../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
