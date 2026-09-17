# O3 · 内容富化

> 设计登记 · 2026-09-17 · target: `content-enrichment` · benchmark: `aihot-enrichment` · 尚未生成题集、实现本入口或运行本体系评测。

语义来源：[四对象设计](../../../docs/references/aihot-eval-foundations/objects-and-metrics.md)。本叶子只补实施定位，不扩大已认可范围。

## 对象、题目与计算

优化 [enrich-v2](../../../src/airadar/enrich/runner_v2.py)；[normalizer](../../../src/airadar/enrich/normalizers/production_enrich_provider_output_v2.py) 与 [presentation](../../../src/airadar/presentation/summary.py) 属于相应可见字段输出路径。保留联合调用，不因五类题而强制改成五次调用。

按 category、tags、title、summary、recommendation_reason 各建一个字段题集：每题都有该字段的可观察 AIHOT 参考与配对 raw。一个字段缺参考只排除该字段；题目彼此可重叠，共享同一原始输入引用。

分类固定两侧类别映射后算 accuracy，未知不填成某类别。标签算 exact-set accuracy，不比较顺序，也不先删除候选多余标签；明确空集和未知参考不同。候选失败/缺字段计不一致，哪怕标签参考为空也不能得分。分母为相应字段固定题数，无题时 N/A。实施时用完全一致、错类、多/少标签、乱序、明确空集、未知参考和缺候选字段验证。

标题、摘要、推荐理由需要文本 LLM 判官：看 raw、候选字段和对应 AIHOT 参考，判断足够接近且不奖励编造。模型、prompt、输出量表、指标公式、校验样本数和阈值均**尚未设计**，所以 metrics.json 不伪造这三个任务的指标。后续先取得少量用户核对的好坏样本，用开发样本调整判官，另留未参与调整的核对样本；校验结果连回判官身份。未通过校验不得据其读数优化，也不新增判官的判官。

## 执行与归档

这是设计登记，不是可执行评测入口：题集版本、runner、评分器接线及其 CLI 尚未建立。首次实现从[施工说明](../../../docs/references/aihot-eval-implementation.md)的原始数据核查开始；不要直接运行旧 eval-fit 并将其结果当成本 benchmark 的成绩。开发与回归题分开，参考答案不传给被优化对象。

运行环境沿用项目 Python/uv 配置；对象调用的模型、凭据取得方式、预算、案例并发参数及实际并发记录由实施 session 接通后补充，目前未设计本入口的并发控制。不得从旧线程数推定本题集能并发多少案例。

本叶子的代码负责对象适配和评分入口，公共组件保持单一实现。未来原始输出写入项目根 `runs/<target>/<benchmark>/<version>/<YYYY-MM-DD>/<HH-mm-ss>/`，指标及运行元数据写入同分区的 `experiments/`；时间用 UTC。题库共享位置、版本登记及人工评价见施工说明；这些运行路径尚未物化，本轮没有新成绩。

[metrics.json](metrics.json) 只登记已定指标的名称、单位、方向和定义，**不包含成绩**，也尚未被 runner 或分析页面消费。`task: null` 指整体任务，具体字段任务用稳定字段名；`ratio` 为 0—1 的比例，不是百分数。后续评分代码须与定义共同维护；失败、不适用及未计算状态不能写成零分。达标阈值未定，不凭方向自动宣称达标。
