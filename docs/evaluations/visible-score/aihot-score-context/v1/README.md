# 归档上下文评分 · v1

> [Developer] · 2026-09-21。与单条 `aihot-score-pointwise` 不同的输入契约；只用于离线诊断，不修改网站评分和旧题。

数据位于 `~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-context/v1/`，40 道开发题，来自 A11 固定 200 题中的 Radar 原始输入。145 题仅有 AIHOT 绑定原文、不具备 Radar 观察时钟；15 题发表于冻结档案对应来源最早成功观测之前，保守排除。原新闻、case_id、split、reference.score 原样保留，新增 score_context。40 题发表到档案首次观察为 0.006–14.847 小时，不包含隔天旧闻。

## L1：如何使用

目标仍是原新闻的 AIHOT 可见分，不是聚合事件新造的分数。时间消融使用 archive_first_observed_at（冻结档案覆盖内首次观察，非真实首次采入）、age_hours；候选报道按既有冻结原文、当时已经观察、发表距观察不超过 48 小时筛选，每个来源/规范 URL 只保留当时最新可见版本，字符三元组相似度取前三条。相似度不是同事件标签，不把全部候选直接当真聚合结果。

事件消融由 reason-first 模型判断候选是否属于同一具体事件，再生成同契约新版本 v2；该标签是模型判断，不是用户人评。原文与原文＋上下文采用同一 gold；另一篇代表报道如无自己的参考分，不计算代表评分 MAE。确定性指标沿 O2 MAE/Spearman，无评分 LLM 判官；检索/同事件判断的误差须在归因中独立说明。

## L2：建设和复用

运行 [evals 入口](../../../../../evals/visible-score/aihot-score-context/README.md) 的 `score_context_dataset`：传父题库、已冻结开发 run、Radar 逐轮归档根、新的 `.../aihot-score-context/vN` 输出路径。脚本核父题输入完全相同、raw gzip SHA、来源/规范 URL/正文/来源观察时刻一致，保存 construction.json 的排除数、路径、哈希和检索规则；manifest 绑定 cases 与 construction 及共享原证据。再次扩充使用新的版本，不覆盖 v1。题数由输入自动决定，没有写死 40。

运行 `score_context_run` 依次做时钟评分、同事件模型选择、过滤上下文版本、事件评分；模型请求、reason、逐题输出与全部尝试留标准 runs。需要增加天数时先重建新版本，再以相同脚本运行；不能把不同输入版本成绩直接相减当作优化。

## L3：解释边界

这批是此前开发题的子集，不是独立盲测；缺原始时钟不代表新闻不可用于其他评分题，只是不适合本消融。不是完整时间窗或完整事件池，不测聚类召回率，不据无效结果否定所有时效/事件机制。AIHOT 的生成摘要、分数、精选状态、聚合关系均不作为我方推理输入。未来观测不得倒灌到较早时刻。原始输入按允许字段进入 prompt，来源 evidence 只用于身份与时间核查。
