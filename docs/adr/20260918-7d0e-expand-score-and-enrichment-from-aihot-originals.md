# ADR-20260918-7d0e：仅评分与富化接入 AIHOT 归档原文

- Status: accepted（用户已批准扩题边界；本记录落盘时实现和题量尚未验证）
- Date: 2026-09-18
- Supersedes: 逐对象 `object-specific-v2` 设计中 O2/O3 必须存在 Radar 原始输入的限制；其余对象、指标与历史题库不变。

## Context

用户明确同意用 AIHOT 历史原文扩展新闻评分与内容富化；新闻准入不只补 AIHOT 正例，以免改变正负样本分布和 precision/recall 取舍；精选在不能证明仅依赖单条新闻特征时保留连续时间窗口要求。本轮授权不包含生产修改、旧 T5 导入、模型运行或 push。

现有归档包含 AIHOT 原标题与绑定文章身份的详情页原文容器，评分与富化不必因 Radar 当时没有采集而全部排除。AIHOT 改写标题、摘要、分数和标签仍是参考输出，不得回填为模型输入。生产精选实现还读取候选池排序、时效窗口、来源配额和分数映射；现有逐条 threshold 题不能代表完整生产精选链。

## Decision

建题入口增加显式 `--aihot-inputs`，仅允许 O2/O3 将已验证 AIHOT 归档中的原标题和身份绑定原文用于缺少 Radar raw 的新闻。已有同来源、同 URL 的 Radar 输入优先；原始内容多版本歧义仍排除，不借 AIHOT fallback 绕过。AIHOT 原标题或原文缺失、身份不能绑定、内容版本冲突时排除并记录原因，不用生成式摘要或改写标题代替。

这类输入与 Radar 过滤前候选池分离，不能进入 O1/O4。O1 沿用完整候选池与逐新闻时间范围的正负例判定；O4 本次不扩题，后续扩充须继续寻找连续窗口的完整候选组。现有 pointwise-threshold 题仅保留其局部用途，不外推为全池规则评测。

继续合并原始证据、去重、按当前设计检查有效性，不直接拼接题目。manifest 保存获准作为输入的 AIHOT reference digest 名单；`--base` 继承名单并使用冻结 HTML 重建，不依赖最初采集目录仍存在。相同来源/URL 的 case 身份与 split 不变；以后补到 Radar raw 时按优先级重建，输入变化记为 updated。只输出 O2/O3 的新版本，旧题库不覆盖。

## Alternatives

- 继续要求 Radar raw：不满足本次已批准扩充 O2/O3 的需求。
- 全部改用 AIHOT 输入：会无必要地替换已有可用 Radar 输入，故不采用。
- 把 AIHOT 输入伪装成共同 raw 池：会把仅可见正例带入 O1 或不完整候选带入 O4，违反用户边界。

## Review and verification boundary

独立决策审查由 `/root/aihot_input_decision` 完成，七项判据均成立。该读数只支持本次决定，不证明 parser、合并实现、题数或模型效果。实施验证和实际新增题量由本轮主执行者完成后记入 [benchmark inventory](../evaluations/benchmarks/inventory.md)，复用入口为 [逐对象建题](../evaluations/benchmarks/object-datasets.md)。

## 同日实施记录

已物化仅 O2/O3 的 `20260918-aihot-original-v3`，题量、输入来源、历史时间和排除原因记入 inventory。原文提取额外区分真实文章/推文正文与缺正文提示，不把非空外壳作为可用原文。独立实现审查及定向复核完成；真实新版本仅凭冻结 base、不再传原始参照或授权 flag 重建，题目与排除记录逐字节一致。O1/O4 旧叶子摘要未改变。本轮未运行模型、未改变生产或采集调度。
