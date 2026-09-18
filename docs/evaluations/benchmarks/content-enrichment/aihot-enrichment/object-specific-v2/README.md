# 内容富化建题 · object-specific-v2

> [Developer] · 用户于 2026-09-18 认可逐对象独立建题；这是建题规则版本，不是评测成绩。数据版本由 --version 独立命名。

## L1：对象、题目与计分

分类 accuracy、标签 exact-set accuracy；标题/摘要/推荐理由分别使用文本判官。

1. 取有原始输入和已观测富化字段的配对新闻；按 category、tags、title、summary、reason 分别筛题，五个字段不取交集。
2. category、title、summary、reason 必须是非空文本；tags 必须实际观测为列表，[] 是有效空标签参考，null/缺字段不是空标签。
3. 同一字段的重复参照值冲突时只排除该字段；单条新闻的其余字段照常入题。原始内容多版本或多个 AIHOT ID 指向同一新闻时，暂不猜配对。
4. 推荐理由题不要求我方已经选中该条新闻。原始数据缺失时只记配对缺口，不创造输入或参考。
5. cases.jsonl 保存字段并集；field-subsets.json 给出各字段 case_id 列表。读取某字段题集应按 ID 取输入，并只交该字段的参考给对应指标/判官。

每条新闻只存一次输入；五个字段子集引用同一批 cases，不复制正文。允许 title 子集有某条、summary 子集没有它；各字段独立分母，不能统一除以总新闻数。

分类/标签为确定性指标。文本 0/1/2 判官及校验方案沿现有对象文档；当前用户校验票不足，不把其读数称为已采信。题库不调用模型，也不伪造人评。现有 enrich 可复用同条输出，推荐理由的逐条推理适配仍需后续接线；旧全池 run 拒绝 v2。

## L2：代码、题库与复用

- 权威建题逻辑：[object_datasets.py](../../../../../../evals/_shared/object_datasets.py)，命令入口：[build_eval_datasets.py](../../../../../../scripts/build_eval_datasets.py)。
- 指标定义及旧执行入口：[aihot-enrichment](../../../../../../evals/content-enrichment/aihot-enrichment/README.md)。
- 数据位置：`~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment/<version>/`。文档不存大题库；不使用 DGX。
- 生成本对象：共享命令增加 `--target content-enrichment`；省略 --target 时生成四个对象。重建、增量扩窗、多参照、校验及失败恢复见[共用操作说明](../../../object-datasets.md)。

## L3：最小可信边界

同来源与 URL 配对不等于已证明跨站正文完全一致。保留原始正文、版本摘要、来源契约、AIHOT 原页及逐题 provenance；无法消歧的版本不猜。输入和参考分离，同 URL 固定 dev/regression；新数据产新版本，旧版本不覆盖。只比较同题集版本的候选，不将题数增加当效果提升。建题可用不代表推理已接线或对象质量达标。

