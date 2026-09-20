# aihot-score-pointwise

对象：`visible-score`。逐条可见 AI 分拟合；输入来自可验证 Radar raw 或 AIHOT 原文，不把参考分传入模型。

本入口消费 schema 2 独立题库，版本为 `v1`、`v2`。当前版本说明见 [v1](../../../docs/evaluations/visible-score/aihot-score-pointwise/v1/README.md)，共享建题与扩展流程见 [object-datasets](../../../docs/evaluations/benchmarks/object-datasets.md)。

## 使用

在项目根执行（不调用模型）：

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py validate --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1
```

`validate` 仅验证身份、文件哈希和题目结构，不能证明模型效果。独立 [score_eval.py](../../_shared/score_eval.py) 的 `run` 使用冻结题目、显式prompt及 [metrics.score](../../_shared/metrics.py) 的O2 MAE，并自动归档；不经过准入、富化或旧全池runner。指标定义见 [metrics.json](metrics.json)，无LLM判官。

在项目根运行基线（`--env-file` 指向本机现有凭据文件，不复制进配置）：

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py run --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1 --config evals/_shared/configs/baseline-ark.json --env-file .env --prompt evals/visible-score/prompts/baseline-reason-first.json --mode dimensions --split dev --limit 200 --seed score-dev-20260920 --workers 8 --label B0-dev200
```

- 首次新执行链先将 `--limit` 改为小样本并加 `--smoke`；该标志只声明用途，不自动减少题量。
- `dimensions` 严格要求模型先输出非空reason，再输出六个有限0–10维度，按当前DEFAULT_WEIGHTS合成并执行与UI相同的整数化。它是普通条目的逐条展示公式，不是精选rank-linear分。
- 直接打分候选使用 `--mode direct`；C1 为 `evals/visible-score/prompts/direct-editorial-v1.json`，C2 为 `evals/visible-score/prompts/direct-news-importance-v2.json`。要求reason先于0–100整数score。候选独立保存，不覆盖生产。
- seed+split+limit固定实际题目，标签不参与抽样。`--split regression` 只在候选冻结后使用；不传limit跑指定分区全部，不等于整个benchmark。历史曝光不全时不得称完全未见。
- 失败后保持全部参数，`--reuse <原run路径>` 只复用同对象身份、同题同序、同manifest及原始响应复核成功的题。失败调用仍留在原run，新run只补未成功题；代码/prompt变更不得借此复用旧对象。
- `--exclude-run <run路径>` 可重复，选题前排除已有case IDs。不要用换seed反复看回归挑选好结果。
- 并发上限默认8（允许1–32），按共享provider容量调整；SDK无隐式重试、无模型/provider回退。失败题保留，MAE未完成而不是成功子集均值。

模型输入只取case.input的原始字段白名单，正文仍最多5000字符，不读取reference/provenance。原件归 `runs/visible-score/aihot-score-pointwise/<version>/<UTC-date>/<UTC-time>/`：started/config/prompt/cases/prompts、items、predictions、attempts及scores；元数据和metrics/summary归同结构 `experiments/`。逐题reason、原始响应和实际请求保留，费用未知不归零。不得写回题库；新v2等版本可使用同命令，不写死本轮题数。

## 作者结构启发的五维候选

`--mode five` 对应2026-09-21的离线研究：一次LLM调用先输出 `reason`，然后输出 `impact / novelty / substance / authority / relevance` 五个0–10整数，由[纯函数](../../../src/airadar/scorer/five.py)计算 `floor(sum(weight_percent * dimension) / 10 + 0.5)`。默认权重35/20/25/10/10；可在config的 `five_weights` 给出恰好这五个键、有限非负且合计100的百分比。错误权重在模型调用前拒绝；有效权重进入对象身份，变化后不能借 `--reuse` 复用旧对象。没有截距、后置校准、来源乘数或排名池。字段名称及精确权重是本项目的可检验假设，不是作者公开的五维定义；出处和研究边界见[52af](../../../docs/adr/20260921-52af-test-author-inspired-five-dimension-scores.md)。

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py run --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1 --config evals/_shared/configs/baseline-ark.json --env-file .env --prompt evals/visible-score/prompts/five-news-value-v2.json --mode five --split dev --limit 200 --seed score-dev-20260920 --workers 8 --label A2-five-dev200
```

`five-pro.json`配置只用于模型对照，不是生产默认。带示例的研究prompt包含其它开发题的原文/参考输出，保存在对应run的 `prompt.json`，不将样本复制进git或当前待测题输入；复现可直接以该文件作为 `--prompt`。扩题或选择新回归时必须同时排除示例及调参题的同源材料，不只排除run中的200个case IDs。示例集、选择规则和曝光核验随研究support保存。当前质量及全部实验位置见[status](../../../docs/evaluations/visible-score/status.md)，新增运行仍需使用冻结版本和明确题集用途。

## 三维语义候选

判断标准、权重及与旧六维的区别见[设计](../../../docs/evaluations/visible-score/semantic-design.md)。一次调用按影响、信息增量、实质支撑分别输出 `{"reason": "…", "score": 0}`，每维分数为0–10整数；顶层先给总理由，再给三维对象。`--mode semantic` 用代码计算 `5*impact + 3*information_gain + 2*evidence`，不使用校准、来源系数或排名池。

```bash
PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-pointwise/evaluate.py run --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-pointwise/v1 --config evals/_shared/configs/baseline-ark.json --env-file .env --prompt evals/visible-score/prompts/semantic-news-value-v2.json --mode semantic --split dev --limit 200 --seed score-dev-20260920 --workers 8 --label S2-semantic-dev200
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
