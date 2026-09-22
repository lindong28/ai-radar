# 分类实验提示

C阶段保留三个显式候选：`category-c1.txt`在A4上澄清研究成果/实践/观点边界；C2在C1上追加实际安全/法律/政策事件边界；C3在C1上改产品介绍/实践交付边界。C4复用C1加`--source-context`，C5复用C1加`--body-limit 0`，均保留B1的冻结引用及`--quote-contribution`。开关、rubric、模型、输入组合才定义方案，文件名不是完整身份；成绩与当前研究推荐见[状态](../../../docs/evaluations/content-enrichment/status.md)。C2/C3负结果也保留，不因文件存在就视为推荐。

本目录只保留非默认研究候选：`category-a1.txt` 收窄模型发布并扩大研究/观点定义，`category-a2.txt` 按文章内容贡献区分行业、教程和观点。A1/A2仅为研究候选，回归不支持将A2设为默认；禁止把文件编号当作已推广顺序。

当前默认 A0 由 `src/airadar/enrich/category.py:RUBRIC` 单一持有，evaluate.py 未传 `--rubric` 时直接使用它；重复的 `category-a0.txt` 已移除，不改变默认 prompt。精确复现历史轮次以该 run 的 prompt.json 为准，不假定将来的默认始终等于历史 A0。A1/A2通过 `--rubric` 显式选择。输入、执行命令、指标与历史run见[执行入口](../aihot-category-navigation/README.md)和[状态](../../../docs/evaluations/content-enrichment/status.md)。
