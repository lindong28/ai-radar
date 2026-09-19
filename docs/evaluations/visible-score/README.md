# O2 · 可见评分

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

当前独立 benchmark 为 [aihot-score-pointwise / v1](aihot-score-pointwise/v1/README.md)，缺少 Radar raw 时可使用获准的 AIHOT 原标题与绑定原文。这个来源扩展不改变消费者输入/参考契约，因此不是另一个 benchmark。历史 `aihot-visible-score` 的共享池排名映射依赖完整池，与当前逐条消费方式不同，保留为独立 benchmark。

## 对象与边界

固定原始新闻 → scorer + 明确固定的逐条展示分映射 → 预测可见分。当前生产映射仍依赖完整池；独立适配尚未完成，不将 scorer 中间分冒充网站显示分，也不把独立调用偷偷放入生产候选池。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | scorer 加明确固定的逐条展示分映射；当前题库和 MAE 可用，逐条映射及模型运行适配待后续实施。 |
| ② 评测题 | 有可用原始输入及有限 0–100 AIHOT 可见分；输入先 Radar raw、缺失时沿获准 AIHOT 原文 fallback。没有分的可用于其它对象；不依赖 O1 通过、其它字段或全日连续性。 |
| ③ 判官 | 不适用；确定性指标直接比较参考 |
| ④ 自动指标 | 0–100可见分 MAE，越低越好。缺预测则全体指标未完成，不以成功子集冒充全体；判官及校验不适用。 |
| ⑤ 判官校验 | 不适用；通过有相反结果的确定性测试核计数 |

执行与指标定义：[独立题库入口](../../../evals/visible-score/aihot-score-pointwise/README.md)、[metrics.json](../../../evals/visible-score/aihot-score-pointwise/metrics.json)。叶子 CLI 校验资产，现有 `metrics.score` 计算已有可见分预测的 MAE；不代表逐条模型运行已接线。历史共享池操作见 [workflow](../workflow.md#历史-schema1共同窗口与全池运行)。

## L2 数据资产

本对象七类资产沿 [assets](../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。本对象无LLM判官，判词和人评校验不适用；对象自身LLM调用日志仍保存。题量与已有成绩在 [库存](../benchmarks/inventory.md) 和 [台账](../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
