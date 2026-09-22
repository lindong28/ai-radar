# aihot-score-pointwise

对象：`visible-score`。逐条可见 AI 分拟合；输入来自可验证 Radar raw 或 AIHOT 原文，不把参考分传入模型。

2026-09-21 用户决定暂停评分优化，先做 O3 网站 category / tags；本页保留既有执行与复现接口，不表示继续跑评分实验。P1 仅为最近同题组较优研究对照，非跨历史最优或生产默认；当前收尾、原件和机器指标入口见 [O2 status](../../../docs/evaluations/visible-score/status.md#2026-09-21-收尾评分优化暂停)，下一阶段及预测字段的防泄漏边界见 [O3 status](../../../docs/evaluations/content-enrichment/status.md#2026-09-21-接续先做网站分类与标签)。

本入口消费 schema 2 独立题库，版本为 `v1`、`v2`。当前版本说明见 [v1](../../../docs/evaluations/visible-score/aihot-score-pointwise/v1/README.md)，共享建题与扩展流程见 [object-datasets](../../../docs/evaluations/benchmarks/object-datasets.md)。

## 使用

### 评分内部分类修订实验

2026-09-21候选：`five-editorial-workflow-v2.json`（Q1新分类→P1）、`five-information-workflow-v1.json`（Q2同分类→A11）、`five-editorial-workflow-v3.json`（Q3核心消息分类＋虚构边界示例→P1）。均是研究对象prompt，不是网站分类标签或生产默认；指标/是否晋级见[状态](../../../docs/evaluations/visible-score/status.md)。

复用现有 `score_context_study --mode five-editorial`，传入新的完整基线run、题库、prompt和独有label/support目录即可扩到更多原始题；基线题必须由同题库、seed、split及排除集精确重建。它先跑3题smoke再跑基线题数，最大8并发、单臂1800秒，已存在label拒绝静默重跑。不要复用本轮目录写新实验。

```bash
PYTHONPATH=src:. uv run python -m evals._shared.score_context_study --dataset <题库vN> --baseline-run <同题完整run> --source-root <源项目根> --support <新support目录> --env-file .env --prompt evals/visible-score/prompts/five-editorial-workflow-v3.json --label <唯一实验名> --mode five-editorial --workers 8
PYTHONPATH=src:. uv run python -m evals._shared.score_editorial_classify --baseline <同题完整五维run> --candidate-run <已冻结Q3-run> --output <不存在的新重复目录> --env-file .env --source-root <源项目根>
```

第二条只重复分类、不重跑评分；核验来源身份后，按case_id配对分类输出并验证两次实际prompt/request相同，不依赖文件行序。标签一致率不是正确率，不能用分类repeat替代父评分评测。原始分类与评分各自的prompt/reason/attempt/raw已由共享runner保存，未引入新的输入契约或benchmark版本。

本研究复算脚本及结果在 `runs/visible-score/aihot-score-pointwise/v1/2026-09-21/08-27-40/support/`；`analyze.py`保存每个候选集合的不可覆盖分析快照，`summarize.py`核验6个run及Q3-repeat并生成总账。本轮这些脚本固定其历史候选；新研究应传入自己的实际run，不把历史指针当最新成绩。

### 基础验证

在项目根执行（不调用模型）：

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py validate --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1
```

`validate` 仅验证身份、文件哈希和题目结构，不能证明模型效果。独立 [score_eval.py](../../_shared/score_eval.py) 的 `run` 使用冻结题目、显式prompt及 [metrics.score](../../_shared/metrics.py) 的O2 MAE与Spearman，并自动归档；不经过准入、富化或旧全池runner。指标定义见 [metrics.json](metrics.json)，无LLM判官。

### 两个指标与历史预测补算

MAE衡量0–100分尺度上的绝对误差，目标仍为 **MAE < 3**。Spearman衡量同一批新闻的分数相对次序：两侧同分分别取平均名次，再计算名次的Pearson相关系数，范围[-1,1]，越高越一致；0.67不等于67%的排序正确率。它对严格单调变换不敏感，不能替代MAE；也不是网站实际首页顺序或某个完整日窗的指标。两项只在相同case集合上比较，用户尚未指定Spearman达标阈值。

两项采用相同合格题分母；缺失/失败/非法分数不从分母移除，两项均不可计算。少于两题或任一侧全部同分时，仅Spearman为 `null / not_computed` 并说明原因，不能当作0；`complete`仅表示预测完整，不表示每个统计量有定义或质量达标。

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py rescore --source-run <已归档run目录> --label <候选名>-mae-spearman
```

无需凭据、不构建模型transport；`--json`提供机器输出。`rescore`核对原题/原分与题库manifest，在新UTC分区追加 `kind=metric-rescore` 记录，保留原对象身份、来源SHA及原推理时间；不改变case、gold、模型输出或旧成绩。新增API调用及本次模型费用为0，原推理用量仍归source_run，不能把复制预测中的usage累计为新费用。旧MAE-only运行仍逐字段复核后可读；新增Spearman不要求重建benchmark版本。当前索引会同时保留原成绩与补算记录，应通过metadata的kind/source_run区分，不能统计成两次模型实验。秒级归档分区相撞时明确拒绝，下一秒重试，不覆盖已有分区。

该入口要求来源有冻结的 `dataset_manifest_sha256`：支持保存该身份的推理run及其补算run，缺少此身份的旧校准run明确拒绝，不替历史记录补造身份。再次补算时，`source_run`指向直接来源，`source_started_at`仍指向原推理时间，不改成上一次补算时间。

在项目根运行基线（`--env-file` 指向本机现有凭据文件，不复制进配置）：

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py run --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1 --config evals/_shared/configs/baseline-gateway.json --env-file .env --prompt evals/visible-score/prompts/baseline-reason-first.json --mode dimensions --split dev --limit 200 --seed score-dev-20260920 --workers 8 --label B0-dev200
```

- 首次新执行链先将 `--limit` 改为小样本并加 `--smoke`；该标志只声明用途，不自动减少题量。
- `dimensions` 严格要求模型先输出非空reason，再输出六个有限0–10维度，按当前DEFAULT_WEIGHTS合成并执行与UI相同的整数化。它是普通条目的逐条展示公式，不是精选rank-linear分。
- 直接打分候选使用 `--mode direct`；C1 为 `evals/visible-score/prompts/direct-editorial-v1.json`，C2 为 `evals/visible-score/prompts/direct-news-importance-v2.json`。要求reason先于0–100整数score。候选独立保存，不覆盖生产。
- seed+split+limit固定实际题目，标签不参与抽样。`--split regression` 只在候选冻结后使用；不传limit跑指定分区全部，不等于整个benchmark。历史曝光不全时不得称完全未见。
- 失败后保持全部参数，`--reuse <原run路径>` 只复用同对象身份、同题同序、同manifest及原始响应复核成功的题。失败调用仍留在原run，新run只补未成功题；代码/prompt变更不得借此复用旧对象。
- `--exclude-run <run路径>` 可重复，选题前排除已有case IDs。不要用换seed反复看回归挑选好结果。
- 并发上限默认8（允许1–32），按共享provider容量调整；SDK无隐式重试、无模型/provider回退。失败题保留，MAE未完成而不是成功子集均值。

模型输入只取case.input的原始字段白名单，正文仍最多5000字符，不读取reference/provenance。原件归 `runs/visible-score/aihot-score-pointwise/<version>/<UTC-date>/<UTC-time>/`：started/config/prompt/cases/prompts、items、predictions、attempts及scores；元数据和metrics/summary归同结构 `experiments/`。逐题reason、原始响应和实际请求保留，费用未知不归零。不得写回题库；新v2等版本可使用同命令，不写死本轮题数。

## 编辑边界、独立识别与公式规则

2026-09-21 用户授权测试 few-shot；本研究的示例为虚构语义边界，不含真实题目的 AIHOT 分数。三个候选 prompt 分别为 `five-editorial-boundary-v1.json`（P1）、`five-editorial-examples-v1.json`（P2）、`five-editorial-workflow-v1.json`（P3）。前两者 `--mode five`、每题一次调用；P3 使用 `--mode five-editorial`，先 reason-first 识别新闻类型，再以相同原文＋可质疑的分类辅助生成五维，共两次调用。分类是对象内部推理，不是 gold 或质量判官，unknown 不强行归类。

复现同题实验可直接消费完整基线 run，重建其 split、seed、排除项和题序；支持 dev 与冻结候选后的 regression，不固定题数。每次先3题 smoke，再完整基线题集；输出必须新目录/新 label。1800秒截止、无自动重试/回退，中断后检查 attempts，通过叶子 runner 的 `--reuse` 显式恢复同身份失败题，不能删除标记重跑全批。

```bash
PYTHONPATH=src:. uv run python -m evals._shared.score_context_study --dataset <题库版本目录> --baseline-run <完整基线run> --source-root <基线所在项目根> --support <新support目录> --env-file .env --prompt evals/visible-score/prompts/five-editorial-workflow-v1.json --mode five-editorial --label P3-dev --authority 'user-approved editorial ablation; ADR f6b3' --workers 8
PYTHONPATH=src:. uv run python -m evals._shared.score_editorial_rules fit --baseline <A11-dev-run> --editorial <同题P3-dev-run> --source-root <原件项目根> --mapping <新support目录>/rules-fit.json
PYTHONPATH=src:. uv run python -m evals._shared.score_editorial_rules replay --baseline <A11-dev或regression-run> --editorial <同题P3-run> --source-root <原件项目根> --mapping <support目录>/rules-fit.json --family combined
```

验证P4时只需要分类，不必重复执行P3的第二次评分。先 `python -m evals._shared.score_editorial_classify --baseline <A11-regression-run> --candidate-run <冻结P3-dev-run> --output <新support目录>/reg-classifier --env-file .env --source-root <原件项目根>`（同样加 `PYTHONPATH=src:. uv run`），再将 replay 的 `--editorial` 指向这个目录。该入口与原P3核对完整对象身份，实际每题只有一次分类调用，原A11五维仍从缓存读取。辅助目录有 started/classifications/items/attempts/result；没有新评分，不冒充完整P3运行。题序、输入、原始分类、来源哈希和终态均由实际loader核验。

P4规则仅改冻结A11五维，不重新抽取维度、不改变35/20/25/10/10权重。`promotion` 对 impact/novelty/substance 设上限3或4；已核官方渠道且 `substantive_release` 对 impact 设下限5或6；`promotion/official/combined` 三个 family 分别归档。参数只用既有来源hash训练分区选择，留出只检验；回归拒绝其它评分器、分类器、变动来源或开发重叠。每种规则的效果必须对同一份旧A11缓存比较，不能归因到同期重跑差异。官方名单不是逐条一手性证明，更不是无条件加分。

每次真实调用保存实际 prompt/reason/raw/usage/attempt；`editorial_call` 保存第一次分类，`scoring_prompt` 保存实际第二次输入，`attempt_ids` 连接两次请求。第一次成功而第二次失败时不丢第一次证据，正式指标保持 incomplete。规则重放新增调用0；分类和原评分的调用/费用归其源run，实际使用此方案仍需分类＋评分。结果及选择边界见[状态](../../../docs/evaluations/visible-score/status.md)。

## 新闻类型条件权重研究

`score_type_study`复用一个完整五维开发run，不固定200题。分类仅渲染该run原始输入模板，先输出reason再输出新闻类型；不把参考分、AIHOT分类或富化字段交给模型。其结果是辅助推理，不是gold。

```bash
PYTHONPATH=src:. uv run python -m evals._shared.score_type_study classify --run <完整五维dev-run> --source-root <原run所属项目根> --output <新support目录>/type-dev --env-file .env --workers 8
PYTHONPATH=src:. uv run python -m evals._shared.score_type_study fit --run <同一dev-run> --source-root <原run所属项目根> --classifications <support目录>/type-dev --output <support目录>/type-fit.json
```

分类有1800秒截止、最多8并发、无隐式重试/回退；共用既有离线API资源档位。保存原始响应及全部attempts，失败题不删除；只有complete与identity_unchanged均为真才能拟合。source SHA和每题输入、原始响应顺序须一致；中断后先看attempts，不删除输出标记重付全批。输出路径必须新建，不覆盖已发布结果。

拟合不调用模型：开发题按来源hash固定划分训练／内部留出，每类型至少15训练题才拟合正权重，否则退回global。两臂同训练集、LAD、权重5%–60%、和100。`type-fit.json`保存各组case_ids、权重、MAE/Spearman和来源哈希；只有留出两指标同时严格改善才保存`full_dev_mapping`，否则为null。true不等于上线许可，也不是已完成独立回归；回归只能消费冻结映射，不能训练。输出属于研究support；正式指标重放按标准runs/experiments追加，2026-09-21完整可复算驱动见[状态](../../../docs/evaluations/visible-score/status.md)。

## 作者结构启发的五维候选

`--mode five` 对应2026-09-21的离线研究：一次LLM调用先输出 `reason`，然后输出 `impact / novelty / substance / authority / relevance` 五个0–10整数，由[纯函数](../../../src/airadar/scorer/five.py)计算 `floor(sum(weight_percent * dimension) / 10 + 0.5)`。默认权重35/20/25/10/10；可在config的 `five_weights` 给出恰好这五个键、有限非负且合计100的百分比。错误权重在模型调用前拒绝；有效权重进入对象身份，变化后不能借 `--reuse` 复用旧对象。没有截距、后置校准、来源乘数或排名池。字段名称及精确权重是本项目的可检验假设，不是作者公开的五维定义；出处和研究边界见[52af](../../../docs/adr/20260921-52af-test-author-inspired-five-dimension-scores.md)。

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py run --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1 --config evals/_shared/configs/baseline-gateway.json --env-file .env --prompt evals/visible-score/prompts/five-news-value-v2.json --mode five --split dev --limit 200 --seed score-dev-20260920 --workers 8 --label A2-five-dev200
```

`five-pro.json`配置只用于模型对照，不是生产默认。带示例的研究prompt包含其它开发题的原文/参考输出，保存在对应run的 `prompt.json`，不将样本复制进git或当前待测题输入；复现可直接以该文件作为 `--prompt`。扩题或选择新回归时必须同时排除示例及调参题的同源材料，不只排除run中的200个case IDs。示例集、选择规则和曝光核验随研究support保存。当前质量及全部实验位置见[status](../../../docs/evaluations/visible-score/status.md)，新增运行仍需使用冻结版本和明确题集用途。

## 固定五维输出，拟合权重

`evals._shared.score_weights`提供开发集拟合与冻结重放，不请求LLM。LAD把未取整MAE写成线性规划（SciPy/HiGHS），五维权重默认各5%–60%、合计100%；不添加截距、统一缩放或来源系数。最终仍由`five_score`取整，再用原O2计分器报告MAE和Spearman。连续目标最优不等于整数目标最优，两者都保存。SciPy固定在dev依赖组，供离线优化与测试，不加入生产依赖；非dev环境也可按下方`uv --with`临时启用。

```bash
PYTHONPATH=src:. uv run --with scipy==1.17.1 python -m evals._shared.score_weights fit --run <完整的five模式dev-run> --output <研究run/support/weights-lad.json>
PYTHONPATH=src:. uv run python -m evals._shared.score_weights replay --run <同对象完整regression-run> --mapping <研究run/support/weights-lad.json> --label A11-weights-lad-regression
```

先冻结mapping，再读取回归结果；回归不能用于拟合。`fit --method grid`提供与历史相同的5个百分点网格对照（3,701组，优化整数MAE）；默认LAD无需枚举，可用于更多已完成的开发题。`--lower/--upper`只适用于LAD的实验边界，不自动改变生产。不要据回归结果反复更换边界。输出默认人读，`--json`用于程序消费；失败非零退出，历史文件不可覆盖。秒级run目录碰撞须下一秒重试。

mapping中的`optimization.weights_percent`为重放权重权威；`objective`说明实际优化的目标，`unrounded_mae/rounded_mae`是该开发集的拟合结果，`fit_seconds`是求解段计时（不含依赖导入、I/O和归档，不作普遍性能保证）。`fit_run/fit_source_sha256/original_object_identity`绑定原始输入、参考和模型输出；`mapping_id`绑定整份冻结参数。重放核对来源SHA、原五维重现原总分及对象身份；回归按ID/URL/精确正文排除与拟合题重叠，不宣称事件级独立。

新产物按正常`runs/...`、`experiments/...`分区追加，`mode=five-weight-replay`、`new_model_calls=0`，原推理成本仍属于source run。逐题`source_prediction`可定位原prompt、reason、五维分与原始响应，不复制usage冒充新请求。若将已冻结权重用于**新推理**，把`optimization.weights_percent`作为现有评分config的`five_weights`，仍显式选择原prompt与`--mode five`；那是有模型调用的新实验，不是本重放命令。当前实验不改变生产默认，结果见[状态](../../../docs/evaluations/visible-score/status.md)。

## 无示例与逐维独立调用

2026-09-21 用户偏好无示例。A9用 `five-ai-impact-v3.json --mode five`，A11用 `five-evidence-boundary-v3.json --mode five`；均沿上方命令替换prompt，不改变题库/权重。旧示例prompt保留为历史证据，不是当前建议路线。

A10用 `five-independent-v2.json --mode five-separate`。prompt的 `dimension_rubrics` 必须按impact/novelty/substance/authority/relevance显式列出五项；一条新闻的五次调用共享同一原始输入，但只读自己的rubric，不读其它维度分数或参考分。每次先reason再0–10整数，代码仍使用同一 `five_score`。按新闻有界并发、单题五次串行，`--workers 8`意味着最多8个在飞请求，不是40个。任一维失败则该题无总分，完整分母保留；`--reuse`仅复用完整成功且身份复核通过的题，不自动复用半题或隐藏失败。

`items/*.json`及 `predictions.jsonl` 的 `dimension_calls` 保存逐维actual prompt、request、response、reason、usage、模型及attempt身份；顶层 `reason_origin=aggregated-dimension-calls` 表示代码拼接，不是模型原生总理由。该模式已测但没有优于A2，不作为生产默认。实际质量、调用数及采用状态看[status](../../../docs/evaluations/visible-score/status.md)。

## 评分题复核页

生成器只读取冻结运行，不重跑模型、不改题库。分析JSON是有序数组，每条 `case_id/category/analysis` 必填，`counterargument`可选；分类为疑似参考分、疑似Radar错误、输入缺口或待判断。分析是agent意见，不是人评。新增批次写尚不存在的输出目录，保留旧页面。

```bash
PYTHONPATH=src:. uv run python -m evals._shared.score_review --source-run <A2冻结run> --analysis-json <support/review-analysis.json> --comparison-run <候选冻结run> --output-dir <研究run/review>
```

`--comparison-run`可重复。生成器核题目/预测/原分、实际prompt及比较题目身份；页面展开原始输入、实际模型reason和prompt，提供四类资格票、自由理由、浏览器本地草稿和一键复制整批JSON；复制失败显示整批文本，不假报成功。草稿同时绑定源cases/predictions SHA和完整批次SHA，分析或候选变化不得复用旧票。票型字段及收到回票后的处理见[人评说明](../../../docs/evaluations/human-labels.md#评分题资格复核票)。本轮20条页面位于 `runs/visible-score/aihot-score-pointwise/v1/2026-09-20/18-01-06/review/index.html`；须通过隔离本地HTTP服务展示，不对外发布。

## 三维语义候选

判断标准、权重及与旧六维的区别见[设计](../../../docs/evaluations/visible-score/semantic-design.md)。一次调用按影响、信息增量、实质支撑分别输出 `{"reason": "…", "score": 0}`，每维分数为0–10整数；顶层先给总理由，再给三维对象。`--mode semantic` 用代码计算 `5*impact + 3*information_gain + 2*evidence`，不使用校准、来源系数或排名池。

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py run --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1 --config evals/_shared/configs/baseline-gateway.json --env-file .env --prompt evals/visible-score/prompts/semantic-news-value-v2.json --mode semantic --split dev --limit 200 --seed score-dev-20260920 --workers 8 --label S2-semantic-dev200
```

同标尺的直接总分control使用 `semantic-direct-control-v1.json --mode direct`，仅对应初版S1的机制对照。扩题后替换dataset路径、冻结对应版本和抽样seed；候选冻结前不读回归结果。选新回归时用 `--exclude-run` 排除已用于诊断的历史题。题数不是写死在实现里的。所有新运行保留原始维度理由和分数于 `items/*.json` 的 `response_json`，预测总分在 `output.score`；失败不返回伪分数。

## 零调用分数校准

2026-09-20 后续用户选择优先[多维语义评分](../../../docs/evaluations/visible-score/semantic-design.md)。本节保留历史诊断工具，不代表当前采用后置校准。

[score_calibration.py](../../_shared/score_calibration.py) 只在完整 dev 运行拟合统一映射 `floor(clamp(scale * score + offset, 0, 100) + 0.5)`，保留原预测。搜索61个scale（0.20至1.40、步长0.02），每个取残差中位数offset，再按实际MAE选取；不是所有连续仿射参数的全局最优，也不是逐来源/逐题记分表。参考分只在 dev 拟合时读取，应用到回归时映射已冻结，不需要 AIHOT 分数作为推理输入。

```bash
PYTHONPATH=src:. uv run python -m evals._shared.score_calibration fit --run <完整开发run目录> --output <该run目录>/calibration.json
PYTHONPATH=src:. uv run python -m evals._shared.score_calibration apply --run <同对象开发或回归run目录> --mapping <开发run目录>/calibration.json --label <校准候选名>
```

fit拒绝regression、空集、失败题；映射文件不可覆盖，保存原对象/题目身份与来源SHA。apply核对这些身份，输出新的runs/experiments分区，保留原题和失败分母，不覆写原成绩。每次apply零新增模型调用；原推理费用仍属于源run。当前分区以秒命名，同一秒内多个归档会明确拒绝碰撞；逐条CLI执行并在碰撞后下一秒重试，不覆盖目录。应在持久的项目资产目录上拟合/应用，避免把即将删除的临时worktree路径写入来源。

本校验CLI仍单进程且零模型调用；只有run使用并发。旧 [aihot-visible-score](../aihot-visible-score/README.md) 保留历史全池契约与运行入口，旧成绩不重命名到本benchmark。当前结果、采纳及未验证边界见[status](../../../docs/evaluations/visible-score/status.md)。
