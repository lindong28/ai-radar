# O2 · 可见 AI 评分

> 设计登记 · 2026-09-17 · target: `visible-score` · benchmark: `aihot-visible-score` · 尚未生成题集、实现本入口或运行本体系评测。

语义来源：[四对象设计](../../../docs/references/aihot-eval-foundations/objects-and-metrics.md)。本叶子只补实施定位，不扩大已认可范围。

## 对象、题目与计算

优化 [scorer](../../../src/airadar/scorer/runner.py) 及必要的确定性分值转换。每题只选 AIHOT 有可见数值分数且可配对 Radar raw 的新闻，输入 raw，参考为同条新闻的可见分数；没有参考分的新闻只排除本对象，不影响其他对象。

预测必须是最终展示分，和参考同量纲；内部六维分不是参考真值。[curator/select.py](../../../src/airadar/curator/select.py) 的排名映射与 [app.js](../../../web/static/app.js) 的显示换算是实施时必须处理的边界；映射方案尚未确定，本登记不改变生产规则。

唯一指标 MAE，无 LLM 判官。固定 N 道评分题，失败不填 0、不删题；有失败时全题集 MAE 为未计算，成功子集误差只能作诊断。实施时用相同分、已知非零偏差、量纲换算、缺参考及候选失败验证；不要沿用旧 score_spearman 代替 MAE。

## 执行与归档

这是设计登记，不是可执行评测入口：题集版本、runner、评分器接线及其 CLI 尚未建立。首次实现从[施工说明](../../../docs/references/aihot-eval-implementation.md)的原始数据核查开始；不要直接运行旧 eval-fit 并将其结果当成本 benchmark 的成绩。开发与回归题分开，参考答案不传给被优化对象。

运行环境沿用项目 Python/uv 配置；对象调用的模型、凭据取得方式、预算、案例并发参数及实际并发记录由实施 session 接通后补充，目前未设计本入口的并发控制。不得从旧线程数推定本题集能并发多少案例。

本叶子的代码负责对象适配和评分入口，公共组件保持单一实现。未来原始输出写入项目根 `runs/<target>/<benchmark>/<version>/<YYYY-MM-DD>/<HH-mm-ss>/`，指标及运行元数据写入同分区的 `experiments/`；时间用 UTC。题库共享位置、版本登记及人工评价见施工说明；这些运行路径尚未物化，本轮没有新成绩。

[metrics.json](metrics.json) 只登记已定指标的名称、单位、方向和定义，**不包含成绩**，也尚未被 runner 或分析页面消费。`task: null` 指整体任务，具体字段任务用稳定字段名；`ratio` 为 0—1 的比例，不是百分数。后续评分代码须与定义共同维护；失败、不适用及未计算状态不能写成零分。达标阈值未定，不凭方向自动宣称达标。
