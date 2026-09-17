# O1 · 全部 AI 动态收录

> 设计登记 · 2026-09-17 · target: `news-admission` · benchmark: `aihot-all-members` · 尚未生成题集、实现本入口或运行本体系评测。

语义来源：[四对象设计](../../../docs/references/aihot-eval-foundations/objects-and-metrics.md)。本叶子只补实施定位，不扩大已认可范围。

## 对象、题目与计算

优化 [prefilter](../../../src/airadar/prefilter/runner.py) 及实际影响全部动态成员的后处理；[timeline](../../../src/airadar/web/routes/timeline.py) 的 relevance 条件属于输出边界。只返回 prefilter=true 不等于新闻已被收录。

输入 U 是固定完整窗口×共同来源的全部 Radar 过滤前 raw，不缩成双方交集。R 是同范围完整 AIHOT 全部动态成员，C 是候选准入成员；每个唯一新闻只计一次。U 中的二元参考由是否属于 R 得到，前提是 R 完整；0 只表示 AIHOT 未收录。AIHOT 有但 Radar 无 raw 的成员仍留在 R 的 recall 分母，另列缺输入数，不把这部分归因为 prefilter 失败。

只计算集合 precision、recall；不需要 LLM 判官。空分母 N/A，处理失败保留题与失败状态，不能当成功空集。实施时用完全一致、有额外成员、有遗漏、参考缺 raw、空分母及失败输出验证集合计算；这属于确定性评分代码测试，不另建判官。

## 执行与归档

这是设计登记，不是可执行评测入口：题集版本、runner、评分器接线及其 CLI 尚未建立。首次实现从[施工说明](../../../docs/references/aihot-eval-implementation.md)的原始数据核查开始；不要直接运行旧 eval-fit 并将其结果当成本 benchmark 的成绩。开发与回归题分开，参考答案不传给被优化对象。

运行环境沿用项目 Python/uv 配置；对象调用的模型、凭据取得方式、预算、案例并发参数及实际并发记录由实施 session 接通后补充，目前未设计本入口的并发控制。不得从旧线程数推定本题集能并发多少案例。

本叶子的代码负责对象适配和评分入口，公共组件保持单一实现。未来原始输出写入项目根 `runs/<target>/<benchmark>/<version>/<YYYY-MM-DD>/<HH-mm-ss>/`，指标及运行元数据写入同分区的 `experiments/`；时间用 UTC。题库共享位置、版本登记及人工评价见施工说明；这些运行路径尚未物化，本轮没有新成绩。

[metrics.json](metrics.json) 只登记已定指标的名称、单位、方向和定义，**不包含成绩**，也尚未被 runner 或分析页面消费。`task: null` 指整体任务，具体字段任务用稳定字段名；`ratio` 为 0—1 的比例，不是百分数。后续评分代码须与定义共同维护；失败、不适用及未计算状态不能写成零分。达标阈值未定，不凭方向自动宣称达标。
