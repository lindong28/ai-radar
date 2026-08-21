# ADR-020：成本比较统一归一化为 cache 全未命中

- 状态：Accepted；「近 24 小时计量不完整时统一降级」对 A6 在途窗口的处理由 ADR-022 supersede
- 日期：2026-08-11
- 范围：A6、周报与 `/admin/usage` 的跨窗成本比较；不改变各窗口绝对成本的已观测 cache 口径
- Supersedes：ADR-018 的 cache 精确覆盖 gate 与「过渡期」判断；ADR-019 的 interpret cache 精确覆盖 gate

## 决策

A6、周报总额环比以及阶段/Provider/模型组/interpret 单次成本参考，都用同一评估时点费率并把两窗每行输入 token 统一按 cache 未命中重算。绝对成本仍按该窗口真实采集到的 cache 字段派生；归一化值只用于比较。A6 的 baseline 不再因每日 cache 测量覆盖比例不同而排除，只在少于 3 个已完成 UTC 基线日或近 24 小时已记录调用的日志证据不足时转为不可评估。调用次数、token 合计与同一计价口径的金额合计只统计 `llm_usage` 记录行，因此是全部付费调用对应总量的下界；任何未写入该表的付费调用均不在内。单次成本、占比与环比只描述已记录 cohort，相对全部付费调用真值的偏差方向未知。日志证据只能说明记录行的已知写入失败，不能证明 attempt-level 计量完整。

已记录调用的日志可观测性是同一不变量的一部分：`llm_usage_metering_failure` 计数或 pipeline 日志覆盖不完整时，不能把缺行当作零成本。A6 降级而不报恢复；周报显示低估风险，并在无法证明前窗处理暴露量时停止环比。这些防线只能处理它们看得见的缺行，不把 surviving-call 数据升格为 billed-attempt 总额。

## 原因

生产的 interpret backend 不提供 cache 字段，而 prefilter/score/enrich 稳定提供，因此每日覆盖率随 stage mix 浮动。17 个真实日没有任意两日覆盖率精确相同，原 gate 让 A6 永久没有基线；P3 上线 cache 字段也不会改变这种 stage 差异。去掉 gate 后的生产反事实为 14 个基线日、中位数 ¥32.97、notice 阈值 ¥98.92、`firing=False`，说明原 gate 没有避免一次误报，却关闭了唯一花费探测路径。

全未命中是保守且可重算的共同基准：它不需要猜测缺失 cache 值，也不会把 cache 采集比例变化冒充流量或模型组合变化。ADR-018 的 evaluation-time tariff 归一化继续有效；本 ADR 只替换其 cache 判据。

## 验证边界

测试必须包含每日 cache 覆盖率不均匀的 14 日基线、tariff-only 变化、priced 与 unpriced 混合的单次成本分母、计量失败与日志缺口。生产验证必须直接运行 A6 driver 并打印 baseline 数、阈值、已记录调用的日志证据与 firing，而不是只用 fixture 证明。
