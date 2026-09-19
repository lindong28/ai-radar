# O2 · 可见评分 状态

> [Developer] · Mutable snapshot · 2026-09-19。区分能力、实际运行与有效成绩。

新身份 `aihot-score-pointwise/v1` 为 3,475 题，已从 20260919-refresh-1010 无改题迁移并完成字节核验。逐条题库与 MAE 可用；scorer 加固定逐条展示分映射和自动归档尚待后续评测实施者接线，没有新模型成绩。当前题集见 [v1](aihot-score-pointwise/v1/README.md)，库存见 [inventory](../benchmarks/inventory.md)。旧全池拒答不阻止独立题库校验，但不能由此声称新推理链已完成。

## 历史共享池成绩（2026-09-17，aihot-visible-score）

| 层 | 当前状态 |
| --- | --- |
| L1 计算逻辑 | 34对分数题、MAE代码已实现；真实3题smoke MAE=15仅证明链路。完整池有1条准入拒答，排名映射不确定，正式MAE未计算。 |
| L2 数据资产 | 四轮真实推理/重试及零调用回放均保留，缺分为null而非0；当前基线UTC 15-20-15。 |
| L3 治理与剩余归属 | 固定ARK入口同一输入两次content_filter，未删题或自动换模型。完整基线受供应商该输入拒答阻塞；换供应商/改变对象需要用户明确选择，本轮未做。 |

运行入口见 [workflow](../workflow.md)，原件定位见 [ledger](../experiments/ledger.md)。
