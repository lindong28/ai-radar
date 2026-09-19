# 精选成员建题 · object-specific-v2

> 2026-09-18 扩题边界：本页仅是逐条 threshold 的局部题库，不代表生产精选链。用户要求精选继续保守寻找连续时间窗口的完整候选组，本次不使用 AIHOT-only 新闻扩充。生产 select 还依赖池排序、时效、来源配额和分数映射；完整池工作沿旧 v1 路径，不能用本页不完整配对子集替代。决定见 [ADR-20260918-7d0e](../../../../../adr/20260918-7d0e-expand-score-and-enrichment-from-aihot-originals.md)，以下历史规则保留。

> [Developer] · 用户于 2026-09-18 认可逐对象独立建题；这是建题规则版本，不是评测成绩。数据版本由 --version 独立命名。

## L1：对象、题目与计分

2026-09-18 URL/版本修复使用原 `20260918-merged-v2` 冻结窗口重建为 `20260918-substantive-o4-v4-r1`；后续当前数据版本与精确题数以[库存顶部](../../../inventory.md)为准。配对与实质性口径见[共用规则](../../../object-datasets.md#实质内容版本与-url-身份2026-09-18-用户修订)。局部题重验不加入 AIHOT-only 输入、不声称形成完整池。

条件在 AIHOT 已收录新闻上的本地精选规则；成员 precision / recall，不需要 LLM 判官。

1. 取 AIHOT 已收录、有同来源同 URL 原始输入、且 aihot_selected 为显式 true/false 的新闻；不要求 AIHOT 数值分数或富化字段齐全。
2. 选中标签来自已验证 API/页面投影；不能用截断首页没有出现来构造 false，缺值也不是 false。
3. missing_raw 只记录配对覆盖缺口，不计成本地规则的假阴性。原始多版本/成员参考冲突排除并保留原因。
4. 运行规则时应关联同一题集、同一版本的我方固定预测分数；AIHOT 分数不进入 input。比较 threshold 候选不必重新调用模型。
5. 本版本是 pointwise-threshold，不支持用不完整配对子集评 TopK、来源配额或全池新鲜度规则。那类规则需单独按完整候选组建题，不能宣称此题集已经覆盖。

reference 只有 featured；input 是未过滤原始新闻，不带 AIHOT 分数、成员标签或参考成员数量。evaluation_mode 明确为 pointwise-threshold。

复用集合指标，后续逐条规则适配关联我方固定预测。旧全池执行器明确拒绝 v2；它仍可运行已有 v1 完整池实验，但不得把两种成绩直接拼接。

## L2：代码、题库与复用

- 权威建题逻辑：[object_datasets.py](../../../../../../evals/_shared/object_datasets.py)，命令入口：[build_eval_datasets.py](../../../../../../scripts/build_eval_datasets.py)。
- 指标定义及旧执行入口：[aihot-featured-members](../../../../../../evals/featured-members/aihot-featured-members/README.md)。
- 数据位置：`~/research/video-eval-arena/data/benchmarks/ai-radar/featured-members/aihot-featured-members/<version>/`。文档不存大题库；不使用 DGX。
- 生成本对象：共享命令增加 `--target featured-members`；省略 --target 时生成四个对象。后续扩展用可重复的 `--base` 合并旧版冻结原始证据及新增数据，去重后按本页规则重验；不直接拼旧题，旧版本保持不变。命令、逐题变更计数、校验及失败恢复见[共用操作说明](../../../object-datasets.md)。

## L3：最小可信边界

同来源与 URL 配对不等于已证明跨站正文完全一致。保留原始正文、版本摘要、来源契约、AIHOT 原页及逐题 provenance；无法消歧的版本不猜。输入和参考分离，同 URL 固定 dev/regression；新数据产新版本，旧版本不覆盖。只比较同题集版本的候选，不将题数增加当效果提升。建题可用不代表推理已接线或对象质量达标。
