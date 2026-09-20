# 资产与可信边界

> [Developer] · 路径均相对 AI Radar 项目根，题库除外。默认实现及校验见 `evals/_shared/assets.py`；本机存储是用户明确选择，不使用 DGX。

## L2 数据资产

### 活动题库与历史归档（2026-09-20）

活动入口仍为 `~/research/video-eval-arena/data/benchmarks/ai-radar/<target>/<benchmark>/vN/`。四个对象目录各只保留当前 benchmark：`aihot-observed-membership`、`aihot-score-pointwise`、`aihot-enrichment-fields`、`aihot-featured-threshold`。不再把旧命名迁移副本与当前题库并列展示。

五个旧 benchmark 目录已可恢复地移到 `~/research/video-eval-arena/data/benchmark-archives/ai-radar/20260920-cleanup/`，保持 `<target>/<benchmark>/<version>` 相对结构。当前评分、富化的 `v1` 也在该归档树中保留实体，活动入口的 `v1` 是相对符号链接：两份冻结 manifest 的 `shared_evidence` 仍引用同树下旧 `aihot-prefilter/v1/evidence`。该依赖不是废文件，不能删除归档树；没有修改 manifest、题目或参考字节，也没有制造新的数据版本。

日常评测和扩题继续传活动入口，现有 `load_dataset` 和 `read_bases` 会解析链接。新版本仍写活动根的真实 benchmark 目录，用尚未存在的 `v2/v3`；不要把归档根作为新输出根。旧实验或历史文档引用上述五个旧目录时，将原 `data/benchmarks/ai-radar/` 前缀换成 `data/benchmark-archives/ai-radar/20260920-cleanup/`，其余路径不变；原记录不追改。归档根 README 记录精确迁移清单和恢复方法。

本次仅清理四个对象目录的历史入口；根目录旧 schema1 共享池、`_build-reports`、项目 `runs/`、`experiments/`、`human-evals/` 及持续采集档案不在删除范围。它们不属于当前四个独立 benchmark，不能累计为新增题。此次是目录整理而非磁盘瘦身，没有永久删除数据。

2026-09-20 新增[人评优先标注层](human-labels.md)：用户原票及其材料在 `human-evals/<target>/reviews.json` 保存；批次、日期与来源归 `batches[].metadata`，字段字典和操作由该说明单一维护，不加日期/批次/`imported` 路径。明确人评覆盖同题同字段的自动参考，原 AIHOT 证据与成绩不覆盖。原票和 effective 计分视图分开保留，不能把改标签后的分数当作模型改进。

2026-09-19 O1 新增消费者契约 `news-admission/aihot-observed-membership/v1`，用于冻结批次已观察收录；它不是旧 `aihot-prefilter` 的改名。新 gold、逐题去向及来源覆盖独立冻结，原始证据不删；旧对象响应可严格按输入身份重评分，但不得把重评分称为新模型调用或涨分收益。运行与 metadata 沿下表同样分区，当前操作见 [新入口](news-admission/aihot-observed-membership/v1/README.md)。

新建题采用[逐对象规则](benchmarks/object-datasets.md)：题库为 `<target>/<benchmark>/vN`，按消费者契约区分 benchmark。共享证据随本次第一个目标落盘，不强绑 O1。O2/O3 可显式接入 AIHOT 原文，不混入 Radar raw-inputs；原 HTML 与输入授权 digest 随版本冻结。早先四份 v1 是旧命名 schema2 题库的身份迁移，O1 随后另建上段的新契约；旧题库、运行与成绩保持原样，schema1 的 `<benchmark>/<target>/<version>` 仅为历史兼容。O1 已接通独立模型运行与归档，命令见[prefilter入口](../../evals/news-admission/aihot-observed-membership/README.md)；其它对象的独立推理适配仍见各自 status，不由 O1 接通推定完成。

`--base` 合并版仍使用 schema2，每个叶子附 `merge-summary.json`（相对父版本去重并集的变化计数）与 `changes.jsonl`（字段级身份、输入/参考摘要、主集/补充集归属、移出原因）；共享 evidence/parents.json 记录直接父版本路径和 SHA。所有文件纳入 manifest 字节校验。新 evidence 保留完整合并后的紧凑输入及参照，不依赖祖先仍在线；合并不修改旧资产、不复制旧成绩，也不把重叠抓取观察次数相加。

| 资产 | 唯一落点与产生时机 |
| --- | --- |
| 评测题（输入集） | `~/research/video-eval-arena/data/benchmarks/ai-radar/<target>/<benchmark>/vN/`；build 生成 cases/manifest，input/reference 分离；紧凑原始证据由本批首个目标持有，其它叶子通过 shared_evidence 与摘要引用；迁移版自带必需证据 |
| 逐题输出 | 项目根 `runs/<target>/<benchmark>/<version>/<UTC-date>/<UTC-time>/`；共享 pool 原件只存一份，其他对象记录引用；predict 失败也有状态 |
| 自动指标数值 | runs 的 scores.json 为数值源；`experiments/` 同分区存 metadata 和 metrics/summary.json；跨 benchmark 总表为 `experiments/metrics/summary.json`，可重建 |
| 人评结果 | 项目根 `human-evals/`；材料、用户原票、确认 SHA、校准结果；无票时明确为零，不以 agent 代评补位 |
| 判官原始判词与调用日志 | 文本轮 runs 的 judgments 与 attempts；请求发出前创建 attempt，响应先保留 raw/usage 再解码；失败/未知费用不归零 |
| 归因记录 | [hypotheses.md](experiments/hypotheses.md)，一条因果命题一行，不把猜测写成已证实 |
| 逐轮台账 | 每轮 experiment metadata + 原始 run；人读索引见 [ledger.md](experiments/ledger.md)，当前库存见 [inventory](benchmarks/inventory.md) |

`metrics.json` 位于各 evals 叶子，只存指标定义/方向/单位，不存成绩。代码是 Git 权威，题库、运行和缓存不进 Git；本机备份责任随现有工作目录，不冒称有异地副本。数据版本和已完成轮不可覆盖；扩展采集天数使用新版本，同类新闻 split 稳定，不删旧题掩盖回归。旧 T5、旧 eval-fit 与其它资产不迁入新成绩。

## L3 治理

| 主题 | 本项目最小约束 |
| --- | --- |
| 身份 | 固定输入/参考字节，记录当前源码、模型请求与实际响应、选择参数、固定时钟、空归档初态；不声称重建生产历史。判官与指标各有独立身份 |
| 可比性 | 同题集、同指标、同判官与校验条件比较；任一测量条件变化重建基线。未计算与未采信不是零，不参与胜负 |
| 评价 provenance | 用户票由用户实际确认的文件取得；model/agent 输出是判词，不是人评。SHA 证明字节，不证明作者 |
| 归因准入 | 改动前记录可反驳假设及对照；测量缺陷先修尺子，不把错误标签当优化目标 |
| caller 与调用成本 | CLI 显式使用固定 provider，不改变生产 DB/调度；smoke 后扩量；开始前留 attempt，失败仍计入尝试，价目缺失 cost=null，不自动换模型或重试 |

可见评分与推荐理由通过当前生产纯函数投影，不拿中间 score/enrich 返回冒充网页值。O1 在用户 2026-09-17 最新裁决后**直接评 prefilter 的通过/不通过**，不再把 relevance 后置 gate 混进本对象。

以下是旧完整池成绩的解释边界，不是新逐条题库的统一入题门：任一候选准入/评分缺失会使完整池精选及排名映射不确定，因此 O2/O4 保留未完成，不猜该条会被过滤。O3 分类、标签、标题、摘要按该条 enrich 状态独立计分，只有被精选覆盖的理由受完整池失败影响。失败轮的分类/标签 0 值不能脱离 complete=false 解释为模型质量；首轮这类接线问题已在零调用重算轮纠正，旧原件不覆盖。
