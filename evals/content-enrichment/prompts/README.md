# 分类实验提示

`category-a0.txt` 为六类基线说明，`category-a1.txt` 收窄模型发布并扩大研究/观点定义，`category-a2.txt` 按文章内容贡献区分行业、教程和观点。A1/A2仅为研究候选，回归不支持将A2设为默认；禁止把文件编号当作已推广顺序。

当前默认由 `src/airadar/enrich/category.py:RUBRIC` 单一持有。精确复现A0用evaluate.py默认rubric；文本文件末尾换行可能与常量不同，每轮实际prompt以run中的prompt.json为准。A1/A2通过 `--rubric` 显式选择。输入、执行命令、指标与历史run见[执行入口](../aihot-category-navigation/README.md)和[状态](../../../docs/evaluations/content-enrichment/status.md)。
