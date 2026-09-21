# 归档上下文评分 · v2

与[v1](../v1/README.md)相同40题/原始输入/gold，输入契约不变：仅把每题top3候选换为Flash模型判为同事件的子集，12题有上下文、共16条；其余28题为空。它是同40题的输入变体，不与v1累加计题，不是人评配对标注，也不保证候选穷尽。

数据：`~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-context/v2/`。`construction.json`绑定v1 manifest及每次匹配记录的SHA；实际prompt、reason、response、attempts在O2 pointwise/v1/2026-09-21/02-35-00/support/context。执行[A14入口](../../../../../evals/visible-score/aihot-score-context/README.md)，运行原件context/v2/2026-09-21/03-16-13，MAE11.125/rho0.5427。不能把原文不同的代表报道拿原帖gold评分。

L1沿同一个MAE/Spearman计算器；L2保留输入、预测和匹配模型provenance；L3沿v1时间与无未来泄漏约束。模型的同事件判断含部分包含关系和产品边界疑点，尚无人票，不能据此训练真值判官。空上下文控制见[v3](../v3/README.md)，解释见[状态](../../status.md)。
