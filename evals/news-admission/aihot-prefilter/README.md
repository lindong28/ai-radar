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

候选用 `--prompt <JSON>` 显式加载仅含 `system`、`user_template` 的配置；Jinja 模板只能看到生产 ProviderItem 的输入字段，不能看到 reference/provenance。基线省略该参数，直接复用生产 renderer。模型/端点不作自动 fallback，SDK 重试为零。

失败/中断后的 `--reuse <旧 run 目录>` 只复用对象身份和题目内容均相同的成功逐题结果，其余请求会产生新的调用和费用；重跑前核对剩余预算。每次生成新目录、不覆盖旧轮；任一题失败时两项主指标均为未计算，保留分母，不仅统计成功题。

比较同题同尺的两个 experiment：`PYTHONPATH=src:. uv run python -m evals._shared.cli compare <baseline-experiment> <candidate-experiment>`。至少一项改善且另一项不退步才接受该 split；最终结论另查独立回归，不从开发集外推全库。

模型输入只取 case.input，不读取 reference/provenance。后续 adapter 需保持本 benchmark 契约，输出原件归 `runs/news-admission/aihot-prefilter/<version>/<UTC-date>/<UTC-time>/`，元数据及指标归同结构 `experiments/`，不得写回题库。

`run --workers` 默认上限 8（允许 1–32），需按共享 API 的实际容量协调；配置值不是实测峰值。`started.json`、逐题 `items/`、逐调用 `attempts/`、`predictions.jsonl` 与原始分数在 run 中；机器 metadata/metrics 及跨轮查询索引在 experiments 中。费用未定价时为 null，逐调用 usage 保留。旧 [aihot-all-members](../aihot-all-members/README.md) 保留历史全池契约与运行入口，旧成绩不重命名到本 benchmark。
