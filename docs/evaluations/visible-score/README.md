# O2 · 可见评分

> [Developer] · 2026-09-17 已认可设计及本轮澄清。当前进度见 [status](status.md)。

当前独立 benchmark 为 [aihot-score-pointwise / v1](aihot-score-pointwise/v1/README.md)，缺少 Radar raw 时可使用获准的 AIHOT 原标题与绑定原文。这个来源扩展不改变消费者输入/参考契约，因此不是另一个 benchmark。历史 `aihot-visible-score` 的共享池排名映射依赖完整池，与当前逐条消费方式不同，保留为独立 benchmark。

2026-09-21 新增 [aihot-score-context / v1](aihot-score-context/v1/README.md) 局部诊断：输入额外依赖当时归档时钟和候选原文，与单条契约不同，因此独立命名；不是替换或重复扩充主题库。[顺序消融设计](context-design.md)保存来源、条件权重、时间与事件机制的假设、反证和采用边界。

## 对象与边界

固定原始新闻 → scorer + 明确固定的逐条展示分映射 → 预测可见分。2026-09-20 用户选择当前普通条目展示公式作为基线：六维加权×10后按UI整数化；prompt采用reason-first适配。独立入口已接通。精选新闻另有完整池排名映射，不在本benchmark内，不把独立题临时拼成生产池。决定见 [6a27](../../adr/20260920-6a27-evaluate-pointwise-visible-scores.md)。

## L1 计算逻辑

| 槽位 | 设计与当前能力 |
| --- | --- |
| ① 优化对象 | reason-first 六维 scorer＋固定普通展示公式为历史基线；2026-09-21 起[优先研究作者公开的五维判断＋代码组合机制](status.md#当前方向作者公开机制优先)。`--mode five`、五维校验及显式百分比组合已实现并用于离线实验；具体维度/公式仍是研究假设，不冒充原作者定义。已测[三维语义候选](semantic-design.md)、直接总分与数值校准仅保留为历史对照；生产未替换。 |
| ② 评测题 | 有可用原始输入及有限 0–100 AIHOT 可见分；输入先 Radar raw、缺失时沿获准 AIHOT 原文 fallback。没有分的可用于其它对象；不依赖 O1 通过、其它字段或全日连续性。 |
| ③ 判官 | 不适用；确定性指标直接比较参考 |
| ④ 自动指标 | 0–100可见分 MAE，越低越好，目标 **MAE < 3**；新增同题Spearman（同分平均秩的Pearson），越高越好，用户未指定达标阈值。两项分开报告，不用排序收益替代分数达标；缺预测则两项不可计算，不以成功子集冒充全体。常数或不足两题时仅Spearman不可计算；无LLM判官。 |
| ⑤ 判官校验 | 不适用；通过有相反结果的确定性测试核计数 |

执行与指标定义：[独立题库入口](../../../evals/visible-score/aihot-score-pointwise/README.md)、[metrics.json](../../../evals/visible-score/aihot-score-pointwise/metrics.json)。叶子CLI支持validate、独立run与原预测零调用rescore，prompt位于 `evals/visible-score/prompts/`。历史共享池操作见 [workflow](../workflow.md#历史-schema1共同窗口与全池运行)。

## L2 数据资产

本对象七类资产沿 [assets](../assets.md) 统一保存：评测题、逐题输出、自动指标数值、人评结果、判官原始判词与调用日志、归因记录、逐轮台账。本对象无LLM判官，判词和人评校验不适用；对象自身LLM调用日志仍保存。题量与已有成绩在 [库存](../benchmarks/inventory.md) 和 [台账](../experiments/ledger.md)，不由测试数量推出。

## L3 治理

身份、可比性、评价 provenance、归因准入、caller 与调用成本共用 [assets](../assets.md#l3-治理) 五主题。固定参考不进入被测模型；数据/尺子变更重建基线，失败题不默默删除；仅采用同题逐指标改善，不追加旧k/5等指标。
