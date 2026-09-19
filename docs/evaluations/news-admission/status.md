# O1 · prefilter 准入 状态

> [Developer] · Mutable snapshot · 2026-09-19。区分能力、实际运行与有效成绩。

新身份 `aihot-prefilter/v1` 主集 11,804 题（1,379 正、10,425 负），另有 180 题仅召回；已从 20260919-refresh-1010 无改题迁移并完成字节核验。资产校验与确定性指标可复用，逐对象模型运行/自动归档未接线，本轮没有新模型成绩。当前题集见 [v1](aihot-prefilter/v1/README.md)，规则与库存见 [inventory](../benchmarks/inventory.md)。后续评测实施者负责模型适配，不由命名迁移自动实现。

## 历史共享池成绩（2026-09-17，aihot-all-members）

| 层 | 当前状态 |
| --- | --- |
| L1 计算逻辑 | 243题已完成；precision 85/108 = 78.70%，recall 85/107 = 79.44%。确定性计数，不用LLM判官。 |
| L2 数据资产 | 已有五小时版本及全池原件；当前基线UTC 15-20-15。dev199题、regression44题，首次基线均已读取，不能称未见留出。 |
| L3 治理与剩余归属 | 同题比较已实跑；threshold候选O1读数不变，不采纳。未来优化先登记假设，再对两个split分别查逐题差异。 |

运行入口见 [workflow](../workflow.md)，原件定位见 [ledger](../experiments/ledger.md)。
