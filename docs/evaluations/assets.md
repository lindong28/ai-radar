# 资产与可信边界

> [Developer] · 路径均相对 AI Radar 项目根，题库除外。默认实现及校验见 `evals/_shared/assets.py`；本机存储是用户明确选择，不使用 DGX。

## L2 数据资产

新建题默认采用 [object-specific-v2](benchmarks/object-datasets.md)：题库路径改为 `<target>/<benchmark>/<version>`，共享证据随本次第一个目标落盘，不强绑 O1。紧凑原始输入、完整 AIHOT 引用证据与逐轮原件的区别见该说明。下表题库一行与文末全池约束仅描述已有 v1，历史数据与运行资产不迁移；v2 的逐对象运行适配尚未在本轮建设。

| 资产 | 唯一落点与产生时机 |
| --- | --- |
| 评测题（输入集） | `~/research/video-eval-arena/data/benchmarks/ai-radar/<benchmark>/<target>/<version>/`；build 生成 cases/manifest，输入与 reference 分离；原始全观察及两侧证据保留在该版本 news-admission 叶子，共享引用与校验和绑定 |
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

任一候选的准入/评分缺失会使完整池的精选及排名映射不确定，因此O2/O4保留未完成；不猜该条会被过滤。O3分类、标签、标题、摘要按该条自己的enrich状态独立计分，只有会被精选覆盖的推荐理由受完整池失败影响。失败轮的分类/标签0值不能脱离轮次complete=false解释为模型质量；首轮这类接线问题已在零调用重算轮纠正，旧原件不覆盖。
