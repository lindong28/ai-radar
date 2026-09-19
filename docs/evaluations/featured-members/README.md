# O4 · 精选成员

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

本对象有两个不同消费者契约：当前 [aihot-featured-threshold / v1](aihot-featured-threshold/v1/README.md) 仅测逐条局部阈值，历史 `aihot-featured-members` 测完整候选池选择。两者不是同一 benchmark 的不同版本，成绩不可拼接。用户要求全池规则继续保守寻找连续时间窗口的完整候选组；AIHOT-only 原文扩题不包含 O4。

## 对象与边界

局部 benchmark：单条原始新闻关联我方固定预测分 → threshold → 成员标签。完整池 benchmark：完整候选池及固定预测分/类别 → 选择规则 → 精选成员；空归档初态显式固定，不重建历史生产归档。局部子集不能验证依赖池排序、来源配额、时效或排名映射的规则。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | 局部 threshold 与完整池选择各自定位；不将局部拟合外推为完整生产精选链已拟合。 |
| ② 评测题 | 局部题要求同来源 raw 与显式 featured 参考，关联同版我方预测，不输入 AIHOT 分数；全池题另需连续窗口完整候选组及参考。更换规则复用固定预测，不按 AIHOT 精选数量倒推 quota。 |
| ③ 判官 | 不适用；确定性指标直接比较参考 |
| ④ 自动指标 | precision、recall，无LLM判官。空参考召回N/A，不制造0或1；无有效参考时只能验证规则代码路径，不能验证拟合改善。 |
| ⑤ 判官校验 | 不适用；通过有相反结果的确定性测试核计数 |

执行与指标定义：[局部题库入口](../../../evals/featured-members/aihot-featured-threshold/README.md)、[metrics.json](../../../evals/featured-members/aihot-featured-threshold/metrics.json)。叶子 CLI 只校验资产，局部预测适配待接线；已有预测可复用 `metrics.score`。旧完整池执行见 [workflow](../workflow.md#历史-schema1共同窗口与全池运行)。

## L2 数据资产

本对象七类资产沿 [assets](../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。本对象无LLM判官，判词和人评校验不适用；对象自身LLM调用日志仍保存。题量与已有成绩在 [库存](../benchmarks/inventory.md) 和 [台账](../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
