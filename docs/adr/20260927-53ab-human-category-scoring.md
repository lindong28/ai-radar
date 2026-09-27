# 分类人评集合计分与 C5 修订

日期：2026-09-27。状态：accepted for offline evaluation。计分设计经独立 decision-review 七项放行；正文和分类边界按本日用户明确要求实施，不自动部署。

用户提供 98 条回票，其中 48 reviewed、50 pending。人评优先，但原 AIHOT reference、历史预测与成绩保持原样。新运行 scores.json 明确保持原 AIHOT 口径，human-priority-scores.json 为独立视图；人评只进入计分，不进入推理 prompt。

人评以 case_id 和原输入摘要严格关联。整体 accuracy 按预测是否属于 acceptable_labels 计算；单答案题计算传统六类 precision/recall，多答案题只不参与 P/R，仍计整体 accuracy。失败计错；未标注回退 AIHOT；同题输入不匹配报错。固定 P/R 题目集合，不要求 precision 的预测分母固定。拒绝强选人评第一标签、按预测改 gold，以及将合法替代标签计作 FN 的做法。

本轮以 C5 为基线修正分类边界与引用主次，并按用户点名的原文缺失问题补抓 feed 正文。9/22 各 ADR 的无新抓取约束属于当时实验；本轮新补充材料明确标记 9/27 抓取时间，不冒充历史快照，不改冻结 v1。原始输入及新正文分别保存；新正文若为别的文章或改变实质新闻则不自动迁移人评。模型仍 Ark Flash、temperature=0、thinking disabled、单题一次调用；全 361 题均已曝光，不称盲测。

作出本决策时，新计分器实现、修订分类收益、补正文的全量覆盖与历史同一性尚未验证。9/27 实施后，计分器及正文/本地引用输入路径已通过定点测试与独立审查；实测结果见[分类状态](../evaluations/content-enrichment/status.md)。C5＋正文在本轮单次全体读数上略高，分类规则修订存在整体回退；本决策不作为改善证明。当前补抓正文的历史同一性及全体原文完整性仍未建立，未部署生产。
