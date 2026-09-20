# 逐条可见分基线与离线优化

日期：2026-09-20。状态：accepted for offline experiment。用户在本轮明确选择“逐条评分基线”；一轮独立 decision-review 七项成立，无 blocker。

## 决定与范围

消费既有 `visible-score/aihot-score-pointwise/v1` 冻结题库，固定 MAE 为主指标。基线为当前六维定义、当前 DEFAULT_WEIGHTS（density .40、authority .10、significance .50）与 UI 相同整数化 `floor(weighted_score * 10 + .5)`。基线 prompt 仅将解释字段改为先输出 `reason`，明确是 reason-first 适配，不称生产 prompt 的逐字复现。

这一公式来自 `presentation/summary.py:item_summary`、`web/routes/timeline.py` 非最新精选成员分支及 `web/static/app.js:scorePill`。精选覆盖分与 rank_linear_v1 不在本次对象内；不临时将抽样题拼成候选池。不改生产默认、题库、参考或旧实验资产，不自动 push/部署。

先少量真实 smoke 验证，再以 seed `score-dev-20260920` 固定开发分区200题，比较 Flash 的 prompt 或固定映射候选。拟合映射只用开发题；候选冻结后使用 seed `score-regression-20260920` 的回归分区200题，同题建立基线。日期/题量仅为本轮安排，不是通用标准。新调用输出 reason 在分数前，原始响应、输入 prompt、失败与映射结果均保留。无效输出不补零、不删题。

用户既有不限制合理预算授权继续适用；本轮不用每候选全量，不因预算截断有依据的优化。无绝对达标线，只报告同题 MAE 改善；历史曝光不全则只称本轮未用于优化，不称从未见过。

## 备选与依据

- 完整精选展示：需完整池、不同 benchmark；用户本轮未选，不扩大到它。
- 只 validate：现有叶子已可验证3475题，但没有模型成绩，无法完成用户的评测优化要求。
- 每候选全量：用户要求按用途分阶段选题，先固定开发集可支持选型，不把开发结果当泛化。

既有交界：20260906-7c31 的加权决策、20260910-3f8b 的仅排序键类别系数、20260918-7d0e 的 O2/O3 原文扩题、当前 human-labels.md 的 reason-first。旧全池标准和已清退原件不移作本次成绩。

## 尚未验证

模型 MAE、吞吐与历史题目曝光尚未由本决定证明。候选质量由实际实验决定；固定映射/直接打分都只能作为待验假设。确定性计分器独立对照：参考20/80，预测相同得MAE0，同时偏移10得MAE10，缺一题则未完成。
