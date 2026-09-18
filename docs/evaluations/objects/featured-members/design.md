# O4 · 精选成员

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

2026-09-18 已认可的[逐对象独立建题规则](../../benchmarks/featured-members/aihot-featured-members/object-specific-v2/README.md)覆盖本页旧题库准入条件。下文保留 v1 推理/指标语义；新题库与旧全池执行器的边界以新版说明为准。

## 对象与边界

完整候选池及固定预测分/类别 → 确定性选择规则 → 精选成员。空归档初态显式固定；不重建历史生产归档。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | 完整候选池及固定预测分/类别 → 确定性选择规则 → 精选成员。空归档初态显式固定；不重建历史生产归档。 |
| ② 评测题 | 按完整输入池与AIHOT同窗精选成员建集合题；保留参考缺raw的漏召回。更换threshold等规则复用同一pool，不按AIHOT精选数量倒推quota。 |
| ③ 判官 | 不适用；确定性指标直接比较参考 |
| ④ 自动指标 | precision、recall，无LLM判官。空参考召回N/A，不制造0或1；无有效参考时只能验证规则代码路径，不能验证拟合改善。 |
| ⑤ 判官校验 | 不适用；通过有相反结果的确定性测试核计数 |

执行与指标定义：[benchmark入口](../../../../evals/featured-members/aihot-featured-members/README.md)。本对象具体数值以机器 [metrics.json](../../../../evals/featured-members/aihot-featured-members/metrics.json) 为权威。

## L2 数据资产

本对象七类资产沿 [assets](../../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。本对象无LLM判官，判词和人评校验不适用；对象自身LLM调用日志仍保存。题量与已有成绩在 [库存](../../benchmarks/inventory.md) 和 [台账](../../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
