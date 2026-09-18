# 内容富化建题 · aihot-original-v3

> [Developer] · 2026-09-18 用户批准；只替代 [object-specific-v2](../object-specific-v2/README.md) 的 Radar 输入必需条件，不改变字段指标或旧题库。规则名 v3 不等于 manifest schema 升版。

## L1：对象、题目与计分

category、tags、title、summary、reason 分别建题，不取五字段交集。分类用 accuracy，标签用 exact-set accuracy；标题、摘要和推荐理由沿现有 0/1/2 文本判官及校验方案，未取得用户校验票的读数仍不能称已采信。

输入先取同来源、同 URL 的 Radar 过滤前 raw；完全没有该 raw 时，显式批准的 AIHOT 参照可提供 `original_title` 与身份绑定的详情页文章/推文正文。仅含缺正文提示的非空外壳不算原文；缺原标题/原文、内容多版本或多个 AIHOT ID 指向同一新闻时不猜，不得将 reference.title 或 reference.summary 复制到输入。推荐理由题不要求我方事先选中新闻，也不把参考理由作为推理上下文。

category、title、summary、reason 须为实际观测到的非空文本；tags 须为列表，已观测的 `[]` 有效，缺失/null 不等于空集合。参考冲突只排对应字段，其它无冲突字段照常入题。cases 保存新闻及字段并集，field-subsets 按字段引用 case_id，各字段独立分母。

## L2：资产与复用

- [统一建题与扩展说明](../../../object-datasets.md#仅扩充评分与富化aihot-原文输入) 给出 `--aihot-inputs`、`--base`、`--reference`、`--target content-enrichment` 的完整命令、授权继承及校验方式。
- 权威代码为 [object_datasets.py](../../../../../../evals/_shared/object_datasets.py)，指标及执行适用边界见 [evals 叶子](../../../../../../evals/content-enrichment/aihot-enrichment/README.md)。
- 数据保存在本机 `~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment/<version>/`，不用 DGX；实际新闻数和字段题数见 [inventory](../../../inventory.md)，不能混为同一个计数。
- cases/provenance 保留输入来源，evidence 冻结 AIHOT 原页，manifest 的 `aihot_input_references` 固定获准作为输入的参照。未来 base 扩展重读冻结原件、合并去重并重验，不拼 cases 或复制五份正文。

## L3：最小可信边界

AIHOT 生成内容只作参考，不是原文 fallback。该输入不会变成 O1 正样本或 O4 完整候选；已有 Radar 输入优先，未来补到 raw 的输入变化记 updated。新版本不覆盖旧题，同 URL 固定 split，同版同题比较模型候选。独立题库仍用 schema_version=2，旧 v1 全池 runner 不接受它；本轮扩题不代表逐条推理适配、判官校准或拟合质量已经完成。
