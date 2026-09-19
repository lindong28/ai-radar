# visible-score / aihot-visible-score

> 历史 benchmark 入口（全池 schema 1），不是当前独立题库的默认入口。当前契约及 `v1/v2` 版本见 [aihot-score-pointwise](../aihot-score-pointwise/README.md)。下面的 v1/v2/v3 属于历史规则或格式名称，不是新 benchmark 的输入版本；历史原件及成绩保留原身份。

当前独立题库的[设计与建题规则](../../../docs/evaluations/visible-score/aihot-score-pointwise/v1/README.md)允许在缺少 Radar raw 时显式使用 AIHOT 原标题与绑定原文，分数只作参考；[重建/扩展命令](../../../docs/evaluations/benchmarks/object-datasets.md)使用 scripts/build_eval_datasets.py 的 `--aihot-inputs` 与 `--base`。规则/数据名称 v3 仍使用 schema_version=2；下文 evaluate.py 是历史 v1 全池运行入口，明确拒绝这些独立题库，避免将其误当完整池。

本叶子是该对象历史全池 benchmark 的评测入口。运行环境：项目根执行 `uv sync`，然后 `PYTHONPATH=src:. uv run python evals/visible-score/aihot-visible-score/evaluate.py --help`。共享执行器一次物化完整预测池、为四对象分别评分归档，避免重复模型调用。

- 输入：本机 `~/research/video-eval-arena/data/benchmarks/ai-radar/aihot-visible-score/visible-score/<version>/`。参考字段不传给模型。具体建题和适用分母见[对象设计](../../../docs/evaluations/visible-score/README.md)。
- 计算：[共享推理](../../_shared/inference.py)、[确定性评分](../../_shared/metrics.py)、[指标定义](metrics.json)。本叶子不维护另一份指标方向。
- 执行、失败恢复、并发和新增数据版本见[workflow](../../../docs/evaluations/workflow.md)，存储见[assets](../../../docs/evaluations/assets.md)。
- 输出：项目根 `runs/visible-score/aihot-visible-score/<version>/<UTC-date>/<UTC-time>/` 与同分区 `experiments/`；当前进展见[status](../../../docs/evaluations/visible-score/status.md)。
