# aihot-enrichment-fields

对象：`content-enrichment`。逐字段富化；仅评价 reference 中实际存在的字段，field-subsets.json 指向这些题的子集。

本入口消费 schema 2 独立题库，版本为 `v1`、`v2`。当前版本说明见 [v1](../../../docs/evaluations/content-enrichment/aihot-enrichment-fields/v1/README.md)，共享建题与扩展流程见 [object-datasets](../../../docs/evaluations/benchmarks/object-datasets.md)。

## 使用

在项目根执行（不调用模型）：

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-enrichment-fields/evaluate.py validate --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v1
```

`validate` 仅验证身份、文件哈希和题目结构，不能证明模型效果。当前没有此 benchmark 的端到端推理 CLI；不能把它交给旧全池 runner。已有输出可由共享 [metrics.score](../../_shared/metrics.py) 计算：输入是 cases 与按 case_id 关联的 `{case_id,status,output}` 预测行，输出仅供评分，不会自动归档。指标定义见 [metrics.json](metrics.json)。未取得文本判官校验时不把文本分数当作有效成绩。

模型输入只取 case.input，不读取 reference/provenance。后续 adapter 需保持本 benchmark 契约，输出原件归 `runs/content-enrichment/aihot-enrichment-fields/<version>/<UTC-date>/<UTC-time>/`，元数据及指标归同结构 `experiments/`，不得写回题库。

本校验 CLI 单进程、无并发参数；不对模型推理并发作承诺。旧 [aihot-enrichment](../aihot-enrichment/README.md) 保留历史全池契约与运行入口，旧成绩不重命名到本 benchmark。
