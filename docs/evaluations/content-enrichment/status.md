# O3 · 字段富化 状态

> [Developer] · Mutable snapshot · 2026-09-21。区分能力、实际运行与有效成绩。

## 2026-09-21：增加每类精确率与召回率

### L1：确定性计分

保留整体 accuracy，另为六类各算 precision=TP/(TP+FP)、recall=TP/(TP+FN)，共13项。失败/非法/缺预测计真实类FN；零分母为未计算。正式evaluate和离线rescore共用 `evals/_shared/category_metrics.py`，定义及复用命令见[执行README](../../../evals/content-enrichment/aihot-category-navigation/README.md)。不新增F1或阈值，不改模型、prompt、gold及benchmark版本。

以下为相同76题回归（不是全量361题），A0仍是当前研究基线；整体准确率A0=69.74%、A2=68.42%。

| 分类 | 参考题数 | A0 Precision | A0 Recall | A2 Precision | A2 Recall |
|---|---:|---:|---:|---:|---:|
| 模型 | 7 | 53.85% | 100.00% | 60.00% | 85.71% |
| 产品 | 19 | 77.27% | 89.47% | 73.91% | 89.47% |
| 行业 | 9 | 50.00% | 88.89% | 46.15% | 66.67% |
| 论文 | 5 | 75.00% | 60.00% | 75.00% | 60.00% |
| 教程 | 13 | 100.00% | 61.54% | 88.89% | 61.54% |
| 观点 | 23 | 76.92% | 43.48% | 70.59% | 52.17% |

逐类读数揭示A0模型和行业的误收、观点的漏分，整体准确率会掩盖这些差异。A2观点召回改善但精确率下降，模型/行业召回退化；本轮没有重新优化或改变候选处置。每类题量5–23，不能把个别100%理解为稳定泛化保证。

### L2：六轮补算与查询

从既有6轮逐题预测补算，各产13项指标，共78条新增查询记录。原6条整体accuracy及原件不覆盖。UTC日期均2026-09-21，以下时间位于 `{runs,experiments}/content-enrichment/aihot-category-navigation/v1/<date>/<time>/`。

| 源推理 | 补算分区 | 题数 | 标签 |
|---|---|---:|---|
| 10-08-04 | 12-45-48 | 8 | category-a0-smoke |
| 10-08-21 | 12-45-50 | 200 | category-a0-dev |
| 10-11-22 | 12-45-51 | 200 | category-a1-dev |
| 10-13-19 | 12-45-53 | 200 | category-a2-dev |
| 10-15-13 | 12-45-54 | 76 | category-a0-regression |
| 10-15-14 | 12-45-55 | 76 | category-a2-regression |

每轮scores保留每类TP/FP/FN、P/R分母和逐题对照，conclusion为可读表；experiments metadata标明metric_recompute、source_run及原件哈希，指标进入统一 `experiments/metrics/summary.json`。零新增LLM调用，不把补算当新增题或新候选；开发三轮仍incomplete且各保留1个content_filter失败。

### L3：复用边界与验证

复用源冻结gold，不重应用后来人评；如需改标签，另开标注修订流程。补算前后源输入/计分代码身份一致，源整体accuracy一致。独立审查发现的跨root注册表及来源定位问题已修复并定向通过；实际归档定义必须与执行checkout一致，源run保存可解析绝对路径。123项定向测试覆盖六类计分、成功/失败/缺失/非法输出、零分母、跨root归档、源漂移拒绝及既有建题/指标/归档逻辑；这是确定性链路验证，不是新增模型质量样本。决策及实现审查记录归首个补算run的support。

额外检查旧资产迁移测试时，`test_eval_asset_relocations.py` 为4 passed/2 failed：两项旧迁移fixture缺metadata.target，经human_metrics.current_rows触发KeyError。未修改的main基线7984d31同样复现，归基线独立/非本轮边界；本次分类源metadata有target并已完成真实归档。此项在本节记账，归后续旧迁移测试维护，不扩展当前逐类指标任务去修改历史迁移语义。

## 当前：六类分类已建题并完成首轮优化实验（2026-09-21）

已按用户要求对齐模型、产品、行业、论文、教程、观点。AIHOT 网页分开教程/观点，旧 API 把两者合为 tip；新 [aihot-category-navigation/v1](aihot-category-navigation/v1/README.md) 有361题（dev285／regression76），旧字段题库保留原义并扩至 [v2](aihot-enrichment-fields/v2/README.md)。不把旧1,673条API分类直接当六类题。

### L1：实现、指标与结果

独立分类输入为原始 title＋content_text 前5,000字，每题一次 `deepseek-v4-flash`，实际返回模型均 `deepseek-v4-flash-ga-260731`；JSON先reason后primary_category。类别枚举与网页映射已六类化；`enrich/prompts_v2.py`共享rubric，网站筛选、API/SSR、导航和日报支持观点；旧记录标签fallback保留，不重新标历史数据。**主生产runner仍走legacy enrich，本轮未切换或部署；独立分类成绩不等于完整enrich成绩。**

判官是确定性相等比较，主指标为 `category_accuracy=正确题/全部选中题`，失败照进分母；无需LLM-as-judge。计分器的错预测、格式错误、NaN和URL误配反例已验证。三候选同开发200题/43来源，回归76题/25来源，均覆盖六类。

| 候选 | 主要变化 | 开发200 | 回归76 | 处置 |
|---|---|---|---|---|
| A0 | 六类主要内容基线 | 138/200＝69.0% | 53/76＝69.74% | 保留为当前研究基线 |
| A1 | 收窄模型发布；论文含研究发现；观点含转述他人看法 | 149/200＝74.5% | 未跑 | 研究候选，不据开发结果推广 |
| A2 | 按内容贡献区分组织行动、研究、独立实测、趋势/案例解读 | 154/200＝77.0% | 52/76＝68.42% | 回归不支持替换A0 |

开发三轮均199条成功、同一题被供应商 `content_filter`，整轮标为incomplete，表中是保留该失败题的端到端准确率；两轮回归各76/76调用完成。多数类恒定基线开发20.0%、回归30.26%。smoke8为6/8，只覆盖4类/6来源，用于链路验证、不当六类质量验收。

A1相对A0修正15／退化4；A2相对A1修正14／退化9。A2回归相对A0修正5／退化6：观点10/23→12/23，模型7/7→6/7，行业8/9→6/9；产品17/19、论文3/5、教程8/13不变。**本轮未证明可泛化的准确率提升**，不因开发77%就将A2设默认；这不证明分类任务或Flash做不到。

### L2：代码、题目和实验资产

- 执行与扩题：[叶子README](../../../evals/content-enrichment/aihot-category-navigation/README.md)，含capture/build/evaluate命令；实现为 `evals/_shared/category_dataset.py`、`category_eval.py`，共享分类逻辑及当前 A0 rubric 由 `src/airadar/enrich/category.py` 持有；非默认候选为 `evals/content-enrichment/prompts/category-a1.txt`、`category-a2.txt`。冻结prompt以每轮prompt.json为准；重复 A0 文本副本已清理，默认行为未改。
- 题目：`~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-category-navigation/v1/`，合并去重且按当前规则重验后输出新vN；旧API题库及六类题库不是互斥的新闻集合，不相加。
- 本轮6个run在 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-21/`，对应机器元数据/指标在同分区 `experiments/`；UTC时间依次为smoke `10-08-04`、A0开发 `10-08-21`、A1 `10-11-22`、A2 `10-13-19`、A0回归 `10-15-13`、A2回归 `10-15-14`。
- 每轮保存cases/prompts/predictions/reason/attempts/scores/diagnostics/conclusion，统一指标 `experiments/metrics/summary.json`。首轮support含 `study-summary.json`、可复算 `summarize.py`、真实网页证据及消费者验收记录；不把运行原件塞进docs。
- 共760次调用、757成功/3次内容过滤、651,712已报告tokens，usage无缺失、金额未知；276个唯一计分ID，不是全量361。暂无本目标人评票，未来票优先；未改原gold。

### L3：比较边界与当前接续

六轮都实际执行eval-identity，code/behavior/inputs一致且终态未漂移；同dev题三个版本、同reg题两个版本各自哈希一致。开发与回归case_id不重叠，候选A2在读取回归前冻结；跨历史或同事件曝光未全面核验，只称本轮分类开发留出，不称从未见过的盲测。回归已用于诊断，未来若据此继续调参，它就是已见回归，不可再冒称未见验证。

下一轮分类实施者应先检查“短原文是否缺分类所需信息”及“网页类别与合理语义的分歧”，用逐题原文/reason提出可检验规则或请用户标注；不默认剔题、猜gold或引入实时抓外链。已有错例中纯转发短句不足以说明模型/产品身份，但这是输入局限的线索，不是已证明无法分类。后续可使用尚未运行的85个dev题做开发对照，真正新增验证仍需新材料并核曝光。A1/A2不自动推广，O2继续暂停、tags仍归下一阶段；本轮没有后台模型任务。

验证范围：定向170个Python测试覆盖建题、计分、六类API/SSR及共享归档；另有146个既有Web回归与真实JS renderer。隔离headless在9条fixture上实际点过两列表观点筛选和日报观点锚点，非生产/移动端/全站性能验收。独立测量链与消费者审查通过；最初NaN归档中断和URL身份缺检查已修复并复验，无遗留审查finding。

## 历史接续决定（实施前）

<a id="2026-09-21-接续先做网站分类与标签"></a>

用户决定暂停 [O2 评分优化](../visible-score/status.md#2026-09-21-收尾评分优化暂停)，下一阶段先做网站展示的 `category` / `tags`，之后再评估这些字段是否有助评分。这里的字段属于 O3，不是 Q1–Q3 使用的评分内部信息性质分类。本次仅记录接续，没有实现或启动 O3 模型运行，没有新成绩或 gold 变更。

下一阶段实施者先接通独立题库的字段预测与归档，用现有 `category_accuracy`、`tags_exact_set_accuracy` 做确定性评测；题集、字段覆盖和运行缺口见下方及 [v1](aihot-enrichment-fields/v1/README.md)，代码/指标定义见 [执行入口](../../../evals/content-enrichment/aihot-enrichment-fields/README.md)。先得到字段自身的同题结果与错误归因，再考虑作为评分输入，不能以评分涨分替代分类/标签质量。标题、摘要、理由的文本判官校验仍归其原范围，**不阻塞 category / tags 的确定性评测**。

评分接续只把**我方由原始新闻生成的预测 category / tags**交给评分器，在相同新闻、参考分和冻结评分配置下做有／无预测字段消融，并同时报告 MAE / Spearman。AIHOT category / tags 留在参考侧；若另做 oracle 上限诊断，须单列身份与结论，不把参考标签送入正常推理后宣称可部署收益。输入新增预测字段前按[benchmark 身份规则](../README.md#当前建题默认2026-09-19)核消费者契约：格式、语义或处理逻辑改变则另建 benchmark，契约不变的题目修订才递增版本；不得静默改写 `aihot-score-pointwise/v1` 或其冻结题库。字段预测、分数参考与旧输出各自保留来源，明确用户人评仍优先，不能用泄漏换取好分数。

以上工作归下一阶段实施者；本轮没有后台续跑。O2 的 P1 只是可复用研究对照，不因这个路线决定成为生产默认。

## 独立题库与能力快照（2026-09-19）

新身份 `aihot-enrichment-fields/v1` 为 3,476 条新闻、12,006 道字段题，已从 20260919-refresh-1010 无改题迁移并完成字节核验。字段子集及确定性计分可用；逐对象模型运行、独立理由适配及自动归档由后续评测实施者接线。本轮无新模型成绩或用户票；文本判官校验仍待用户真实标注。当前题集见 [v1](aihot-enrichment-fields/v1/README.md)，字段题数见 [inventory](../benchmarks/inventory.md)。已有 109 条理由参考，不再沿用下表历史的“0 理由”作为当前题库缺口。

## 历史共享池成绩（2026-09-17，aihot-enrichment）

2026-09-20 清退说明：本节原始实验资产已按用户要求移出项目，不再进入指标查询；下表保留当时状态，其中“当前基线”“留存”“已保存”不描述现在。不可据此继续回读／重评分；当前独立题库及后续实验不受影响，范围见[资产说明](../assets.md#原始参照历史运行与-support-分区)。

| 层 | 当前状态 |
| --- | --- |
| L1 计算逻辑 | 分类12/18=66.67%，标签exact-set 2/34=5.88%。标题/摘要判官真实6字段smoke已运行但未采信；推荐理由无参考。生产primary-category经既有网站slug映射后计分类。 |
| L2 数据资产 | 34标题、32摘要、0理由参考；原模型输出与判词留存。当前确定性基线UTC 15-20-15；文本smoke UTC 15-07-22。 |
| L3 治理与剩余归属 | 判官校准程序已实现，12个真实用户票未取得；当前0条同时有三文本参考，export-calibration明确拒绝编造材料。等待新增理由参考后由用户标票，不用agent代签；文本优化尚未启动。 |

运行入口见 [workflow](../workflow.md)，原件定位见 [ledger](../experiments/ledger.md)。
