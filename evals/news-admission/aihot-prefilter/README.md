# aihot-prefilter

对象：`news-admission`。prefilter 逐条准入；主集保留有充分参照证据的正负例，recall-only.jsonl 不混入 precision。

本入口消费 schema 2 独立题库，版本为 `v1`、`v2`。当前版本说明见 [v1](../../../docs/evaluations/news-admission/aihot-prefilter/v1/README.md)，共享建题与扩展流程见 [object-datasets](../../../docs/evaluations/benchmarks/object-datasets.md)。

## 使用

在项目根执行（不调用模型）：

```bash
PYTHONPATH=src:. uv run python evals/news-admission/aihot-prefilter/evaluate.py validate --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-prefilter/v1
```

`validate` 仅验证身份、文件哈希和题目结构，不能证明模型效果。独立 `run` 入口只执行当前生产源码的 prefilter（不执行 scorer/enricher），将 `is_ai_related` 映射到 `member`，使用共享 [metrics.score](../../_shared/metrics.py) 计算 precision/recall，并自动归档。指标定义见 [metrics.json](metrics.json)。本对象不使用 LLM 判官。

先确认模型、端点和本轮预算，再运行（配置不含凭据）：

```bash
PYTHONPATH=src:. uv run python evals/news-admission/aihot-prefilter/evaluate.py run \
  --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-prefilter/v1 \
  --config evals/_shared/configs/baseline-ark.json --env-file .env \
  --split dev --limit 600 --seed prefilter-20260919 --workers 8 --label baseline-dev
```

抽样按 seed 与 case_id 的哈希排序，不按标签平衡；保存实际 case_ids。`--split regression` 用于候选冻结后的独立回归，不能据其调参。省略 `--limit` 会运行该 split 全部题目，须另行确认预算。链路检查使用 `--limit 3 --smoke`，其成绩不可作为验收。

候选用 `--prompt <JSON>` 显式加载仅含 `system`、`user_template` 的配置；Jinja 上下文仅有生产 ProviderItem 的 `item` 和原始字段派生的 `is_reply`、`is_title_only_web`，不能看到 reference/provenance。两个布尔的精确规则在 [prompt_context](../../_shared/prefilter_eval.py)，用于离线候选，不改变生产接口。基线省略该参数，直接复用生产 renderer。模型/端点不作自动 fallback，SDK 重试为零。

已有回归题被读取后，用 `--exclude-run <旧run目录>` 在抽样前排除旧 `cases.jsonl` 中的身份；可重复传入多个旧run。排除只看ID，不看标签，实际排除集、旧文件哈希和剩余选题写入 `started.json`。基线与候选须使用相同排除参数、seed和limit；剩余题数不足时报错，不悄悄重复旧题。示例：在上述命令上改用 `--split regression --limit 400 --exclude-run runs/news-admission/aihot-prefilter/v1/2026-09-19/08-28-00`，并为基线与冻结候选各运行一次。

失败/中断后的 `--reuse <旧 run 目录>` 只复用对象身份和题目内容均相同的成功逐题结果，其余请求会产生新的调用和费用；重跑前核对剩余预算。每次生成新目录、不覆盖旧轮；任一题失败时两项主指标均为未计算，保留分母，不仅统计成功题。

比较同题同尺的两个 experiment：`PYTHONPATH=src:. uv run python -m evals._shared.cli compare <baseline-experiment> <candidate-experiment>`。至少一项改善且另一项不退步才接受该 split；这只是相对改善判据。用户当前绝对目标是 **precision >90% 且 recall >90%**，需另对完整独立回归成绩逐项检查，不能把 compare 的 accepted 当成绝对达标，也不从开发集外推全库。

模型输入只取 case.input，不读取 reference/provenance。后续 adapter 需保持本 benchmark 契约，输出原件归 `runs/news-admission/aihot-prefilter/<version>/<UTC-date>/<UTC-time>/`，元数据及指标归同结构 `experiments/`，不得写回题库。

## 混合准入候选（离线，不是生产默认）

主题候选可替换同一个`--prompt`参数，随后应用同一policy投影：C6=`prompts/ai-capability.json`（实体中的实质AI能力），C7=`prompts/ai-context.json`（加已有来源／产品上下文），C8=`prompts/ai-primary-context.json`（再限定硬件／通用OS的主要AI对象）。它们是实验资产，不是默认配置或已达标保证；真实结果及是否采纳只看[对象状态](../../../docs/evaluations/news-admission/status.md)。复用失败题时必须保持同一prompt及其它身份，不能将另一候选的成功响应当缓存。

2026-09-19续轮完成后，新回归需在上述命令中同时传入`--exclude-run runs/news-admission/aihot-prefilter/v1/2026-09-19/08-28-00 --exclude-run runs/news-admission/aihot-prefilter/v1/2026-09-19/11-36-10 --exclude-run runs/news-admission/aihot-prefilter/v1/2026-09-19/12-48-32`，排除三批已见共1200题；即使第三批有拒答，该批也已被读取，不能再次称为未见留出。排除清单随实际已读回归增加。原题库不因排除而删除，排除只作用于新一轮的未见验收抽样。

持续`content_filter`不能按网络失败无限重试，也不能填false或删题。12-50-00保留正式未计算指标，另存缺失预测两种可能结果的边界诊断；后者不是实际预测或完整评测成绩。并行启动两个同分区run时，先等第一进程打印运行目录，再启动第二个：目前目录精确到秒，同秒碰撞会在API调用前拒绝创建，不能据此覆盖既有目录。

C5由 `direct-ai-impact.json` 模型主题判断与 `hn100-standalone-body-v1` 原始字段条件取AND。先用上面的 `run --prompt evals/news-admission/aihot-prefilter/prompts/direct-ai-impact.json` 产生模型run，再执行：

```bash
PYTHONPATH=src:. uv run python -m evals._shared.admission_policy \
  --source-run <模型run目录> --label c5-hybrid
```

投影调用模型0次、产生全新的混合对象run，不覆盖源run或修改题库。输入题目必须与冻结题库一致；仅接收未投影的独立prefilter输出，失败保持缺失。`model_output`及`stage_results`保留模型原件，`output.member`是最终混合预测，`admission_policy.rejection_reasons`说明规则拒绝；所有题仍进入原precision/recall分母。混合run的`source_run`和源码/预测摘要绑定模型组件与规则组件；投影成本为0只指本步，上游模型费用另计。规则是开发拟合候选，不是经证实的AIHOT内部算法。

`run --workers` 默认上限 8（允许 1–32），需按共享 API 的实际容量协调；配置值不是实测峰值。`started.json`、逐题 `items/`、逐调用 `attempts/`、`predictions.jsonl` 与原始分数在 run 中；机器 metadata/metrics 及跨轮查询索引在 experiments 中。费用未定价时为 null，逐调用 usage 保留。旧 [aihot-all-members](../aihot-all-members/README.md) 保留历史全池契约与运行入口，旧成绩不重命名到本 benchmark。
