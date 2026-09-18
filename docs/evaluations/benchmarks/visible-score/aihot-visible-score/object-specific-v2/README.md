# 可见评分建题 · object-specific-v2

> 历史规则保留：2026-09-18 起，输入来源限制由 [aihot-original-v3](../aihot-original-v3/README.md) 部分替代；旧版本和以下历史设计正文不改写。

> [Developer] · 用户于 2026-09-18 认可逐对象独立建题；这是建题规则版本，不是评测成绩。数据版本由 --version 独立命名。

## L1：对象、题目与计分

scorer 拟合 AIHOT 可见 AI 分数；主指标 MAE，不需要 LLM 判官。

1. 只取 AIHOT 已观测到有限的 0–100 数值分数、且有同来源同 URL 的 Radar 原始输入的新闻。
2. 不要求新闻通过我方 prefilter，不要求全日 Radar 连续性、精选资格、分类或标签齐全。
3. 同一原始新闻在所选范围有多个内容版本时排除该配对；重复参照中分数冲突，只排除评分题，不连带删除无冲突的富化字段。
4. 缺分数不是 0 分，不补推算分。分数只放 reference.score，不放模型 input。

每行一条 raw → reference.score。案例 ID 和 split 与其它对象保持同一 URL 归属，但题集、版本和分母独立。

期望的适配是 scorer + 固定逐条映射，输出最终展示分。当前生产的可见分仍有全池排名映射，旧执行器不适用于此题集。该推理改造不属于本轮，禁止拿 scorer 中间分冒充当前网站显示分；现有 MAE 计算可复用。

## L2：代码、题库与复用

- 权威建题逻辑：[object_datasets.py](../../../../../../evals/_shared/object_datasets.py)，命令入口：[build_eval_datasets.py](../../../../../../scripts/build_eval_datasets.py)。
- 指标定义及旧执行入口：[aihot-visible-score](../../../../../../evals/visible-score/aihot-visible-score/README.md)。
- 数据位置：`~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-visible-score/<version>/`。文档不存大题库；不使用 DGX。
- 生成本对象：共享命令增加 `--target visible-score`；省略 --target 时生成四个对象。后续扩展用可重复的 `--base` 合并旧版冻结原始证据及新增数据，去重后按本页规则重验；不直接拼旧题，旧版本保持不变。命令、逐题变更计数、校验及失败恢复见[共用操作说明](../../../object-datasets.md)。

## L3：最小可信边界

同来源与 URL 配对不等于已证明跨站正文完全一致。保留原始正文、版本摘要、来源契约、AIHOT 原页及逐题 provenance；无法消歧的版本不猜。输入和参考分离，同 URL 固定 dev/regression；新数据产新版本，旧版本不覆盖。只比较同题集版本的候选，不将题数增加当效果提升。建题可用不代表推理已接线或对象质量达标。
