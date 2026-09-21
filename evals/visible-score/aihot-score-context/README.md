# 可见评分：归档上下文诊断

消费者契约不同于 `aihot-score-pointwise`：每题原新闻之外，带 `score_context.archive_first_observed_at`（冻结档案覆盖内首次观察，非真实首次采入）、`age_hours`（从原发布时间到观察的小时数）、`neighbors`（观察时已可见的候选原文）。原新闻的 AIHOT 可见分仍是唯一 target，不把另一篇代表报道的分当作原新闻 gold。

使用 `PYTHONPATH=src:. uv run python -m evals._shared.score_context_dataset --help` 查看建题参数；指定已有开发 run、父 benchmark、逐轮 raw-capture 根和新的数据版本输出路径。只能追加新版本，不覆盖旧题。数据放 `~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-score-context/vN/`。

`PYTHONPATH=src:. uv run python -m evals._shared.score_context_run --help` 给出顺序实验入口：时间字段消融、Flash 判断同事件候选、仅将模型选中的候选写入新版本、事件上下文消融。模型配对不是人评 gold；保留 reason、请求和全部 attempts。标记文件阻止同轮静默重跑；中断后先检查已有产物。该入口最多并发 8，有 1800 秒批次截止，不跑生产。

单臂通用入口为 `PYTHONPATH=src:. uv run python -m evals._shared.score_eval run --dataset <vN> --config <config.json> --prompt <prompt.json> --mode five --split dev --label <label> --env-file .env`。指标继续复用 O2 MAE/Spearman；运行 `PYTHONPATH=src:. uv run python evals/visible-score/aihot-score-context/evaluate.py validate --dataset <vN>` 校验，不把验证当作模型评测。逐轮结果按标准 `runs/visible-score/aihot-score-context/vN/日期/时刻` 与 `experiments/` 保存。

设计、边界和成绩见 [context-design](../../../docs/evaluations/visible-score/context-design.md) 与 [status](../../../docs/evaluations/visible-score/status.md)。本轮是复用开发题的局部诊断，不是新盲测，不代表完整聚类召回率；不向生产引入额外调用。

## 复用顺序

在项目根运行；下列尖括号是需要替换的路径，不是固定题量。`source-run`须为已冻结开发轮，来源/参数只能从开发侧产生。`support`每次指定新的研究材料目录，不能复用`.started`标记所在目录偷偷重跑；失败后先读attempts确认哪些已发出，再显式恢复，不删除标记重付全批。

```bash
# S1：source prompt增加冻结的source_name/source_kind；先3题smoke，再原开发run同题
PYTHONPATH=src:. uv run python -m evals._shared.score_context_study --dataset <pointwise-vN> --baseline-run <dev-run> --support <new-support> --env-file .env --prompt evals/visible-score/prompts/five-source-context-v1.json --label <new-label> --workers 8
# S2：相同开发输出，来源hash留出；无需新模型调用
PYTHONPATH=src:. uv run python -m evals._shared.score_context_analysis --baseline-run <baseline-dev> --candidate-run <S1-dev> --source-root <baseline-project-root> --output <new-analysis.json>
# S2回归：全开发拟合冻结后，精确复用原回归选题（含历史排除名单）；会新跑S1回归
PYTHONPATH=src:. uv run python -m evals._shared.score_context_validate --dataset <pointwise-vN> --baseline-dev <baseline-dev> --candidate-dev <S1-dev> --baseline-regression <baseline-regression> --source-root <baseline-project-root> --support <new-validation-support> --env-file .env
# S3/S4：新建输入契约不同的上下文题库（本轮v1），输出必须不存在
PYTHONPATH=src:. uv run python -m evals._shared.score_context_dataset --dataset <pointwise-vN> --source-run <dev-run> --raw-root <raw-capture-root> --output <new-context-vN>
# 时间评分→同事件识别→创建模型过滤版本→事件评分；两个dataset版本不得覆盖旧目录
PYTHONPATH=src:. uv run python -m evals._shared.score_context_run --dataset <context-vN> --event-dataset <new-context-vNext> --support <new-context-support> --env-file .env --workers 8
```

扩到新数据时，建题脚本沿父manifest的冻结raw-inputs/raw-manifests与实际raw gzip证明时间和输入；题数由有效记录决定。来源条件的66个已核渠道当前列于`score_context_analysis.py`，未覆盖的来源按unknown使用全局权重；不能把该映射覆盖率误称为全部新来源都已核验。输入语义变化另建benchmark；同契约扩题或消融按vN追加并在README说明重叠关系。

空上下文配对必须保持原40题、原gold、A14 prompt/config，只清空neighbors。本轮v3复现脚本在研究support中，不默认为扩题入口；通用`score_eval run`可以消费任意同契约新版本。所有实验记录unknown费用而非零；模型调用上限8是本研究共享API资源档位，不意味着生产并发或全局模型容量。
