# news-admission / aihot-all-members

新题库（object-specific-v2）的[设计与建题规则](../../../docs/evaluations/benchmarks/news-admission/aihot-all-members/object-specific-v2/README.md)已独立于共同窗口；[重建/扩展命令](../../../docs/evaluations/benchmarks/object-datasets.md)使用 scripts/build_eval_datasets.py。下文 evaluate.py 是历史 v1 全池运行入口，明确拒绝 v2，避免将独立题库误当完整池。

本叶子是该对象的权威评测入口。运行环境：项目根执行 `uv sync`，然后 `PYTHONPATH=src:. uv run python evals/news-admission/aihot-all-members/evaluate.py --help`。共享执行器一次物化完整预测池、为四对象分别评分归档，避免重复模型调用。

- 输入：本机 `~/research/video-eval-arena/data/benchmarks/ai-radar/aihot-all-members/news-admission/<version>/`。参考字段不传给模型。具体建题和适用分母见[对象设计](../../../docs/evaluations/objects/news-admission/design.md)。
- 计算：[共享推理](../../_shared/inference.py)、[确定性评分](../../_shared/metrics.py)、[指标定义](metrics.json)。本叶子不维护另一份指标方向。
- 执行、失败恢复、并发和新增数据版本见[workflow](../../../docs/evaluations/workflow.md)，存储见[assets](../../../docs/evaluations/assets.md)。
- 输出：项目根 `runs/news-admission/aihot-all-members/<version>/<UTC-date>/<UTC-time>/` 与同分区 `experiments/`；当前进展见[status](../../../docs/evaluations/objects/news-admission/status.md)。
