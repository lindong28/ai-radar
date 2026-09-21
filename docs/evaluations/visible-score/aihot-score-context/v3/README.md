# 归档上下文评分 · v3（空上下文控制）

与[v2](../v2/README.md)相同40题、原文和gold，只将全部neighbors置空；是同一benchmark契约的消融版本，不是更大的题集或新标准答案。数据在`~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-context/v3/`；construction绑定v2 SHA和变换语义。

用途：使用A14完全相同的prompt/config隔离上下文贡献；不能把v3比v2分数低说成题库改善。生成与运行脚本保存在pointwise/v1/2026-09-21/02-35-00/support/context/empty_control.py；它仅是本轮固定路径的复现记录，通用运行用[score_eval入口](../../../../../evals/visible-score/aihot-score-context/README.md)，扩大题量时先构建新的只改neighbors视图，保留manifest和原gold不变。

运行原件context/v3/2026-09-21/03-18-26，MAE9.675/rho0.5781；28条原本无候选的题两轮prompt相同却22条变分，不能把单次低值当成新冠军。L1/L2/L3与v1/v2一致，详细分层与归因在[状态](../../status.md)。
