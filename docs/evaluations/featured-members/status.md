# O4 · 精选成员 状态

> [Developer] · Mutable snapshot · 2026-09-19。区分能力、实际运行与有效成绩。

新身份 `aihot-featured-threshold/v1` 为 341 题（6 正、335 负），已从 20260919-refresh-o4-1010 无改题迁移并完成字节核验；这批独立数据的完整候选组仍为 0。资产校验与集合指标可用，局部固定预测适配/自动归档由后续评测实施者接线，没有新模型成绩。当前题集见 [v1](aihot-featured-threshold/v1/README.md)，窗口缺口见 [inventory](../benchmarks/inventory.md)。后续全池扩题仍按完整窗口需求判断。

## 历史共享池成绩（2026-09-17，aihot-featured-members）

| 层 | 当前状态 |
| --- | --- |
| L1 计算逻辑 | 成员precision/recall及固定池规则重放已实现。现有窗口参考精选0条，另有1条候选准入拒答，不能取得完整可采信成员成绩。 |
| L2 数据资产 | UTC 15-20-15基线与15-20-25 threshold=7.5候选共用已保存pool，两轮均0模型调用；comparison.json已归档。 |
| L3 治理与剩余归属 | 比较器拒绝把缺分当改善，未采纳规则、未改生产。精选质量验收需新增真实精选参考及完整预测；本轮仅完成规则迭代链路验证。 |

运行入口见 [workflow](../workflow.md)，原件定位见 [ledger](../experiments/ledger.md)。
