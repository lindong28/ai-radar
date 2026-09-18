# 新闻准入建题 · object-specific-v2

> [Developer] · 用户于 2026-09-18 认可逐对象独立建题；这是建题规则版本，不是评测成绩。数据版本由 --version 独立命名。

## L1：对象、题目与计分

prefilter 的通过集合；precision / recall，确定性集合比较，不需要 LLM 判官。

1. 取原始预过滤新闻，按共同来源 + URL 去重，固定最早观察到的输入，不先运行 prefilter、评分或精选。
2. 每题时间 t 优先取 published_at，缺失才用原始 fetched_at；只在 AIHOT 的开放区间 (t−12h, t+12h) 匹配同来源、同 URL。
3. 主集要求 AIHOT 两次完整终态遍历的有效覆盖包含整个区间，且区间内成员 ID 集合稳定；正例与负例都应用这一要求。不再要求 Radar 在整个24小时内所有来源/轮次无缺口。
4. 有匹配为 member=true；无匹配且参照完整为 false。参照不完整却有已见正例，进入 recall-only.jsonl；无匹配且不完整则写 excluded.jsonl，不当负例。
5. 补充正例只报告召回，不与主集合并计算 precision，也不把仅在 AIHOT 出现、Radar 没抓到的新闻当 prefilter 漏召回。

cases.jsonl 的 reference 只有 member。recall-only.jsonl 是独立补充集，manifest.counts.positive / main 可推导主集正负数；excluded.jsonl 解释未知项。

单条 prefilter 推理；计分入口 evals/_shared/metrics.py 的 news-admission。v2 不经旧全池 run；题库建设已实现，逐对象自动运行/归档适配不在本轮建题任务内。

## L2：代码、题库与复用

- 权威建题逻辑：[object_datasets.py](../../../../../../evals/_shared/object_datasets.py)，命令入口：[build_eval_datasets.py](../../../../../../scripts/build_eval_datasets.py)。
- 指标定义及旧执行入口：[aihot-all-members](../../../../../../evals/news-admission/aihot-all-members/README.md)。
- 数据位置：`~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-all-members/<version>/`。文档不存大题库；不使用 DGX。
- 生成本对象：共享命令增加 `--target news-admission`；省略 --target 时生成四个对象。后续扩展用可重复的 `--base` 合并旧版冻结原始证据及新增数据，去重后按本页规则重验；不直接拼旧题，旧版本保持不变。命令、逐题变更计数、校验及失败恢复见[共用操作说明](../../../object-datasets.md)。

## L3：最小可信边界

同来源与 URL 配对不等于已证明跨站正文完全一致。保留原始正文、版本摘要、来源契约、AIHOT 原页及逐题 provenance；无法消歧的版本不猜。输入和参考分离，同 URL 固定 dev/regression；新数据产新版本，旧版本不覆盖。只比较同题集版本的候选，不将题数增加当效果提升。建题可用不代表推理已接线或对象质量达标。
