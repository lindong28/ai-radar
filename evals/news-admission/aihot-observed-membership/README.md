# 已观察收录准入评测

使用共享 prefilter runner 和确定性 precision/recall，不调用下游 scorer/enricher，不写生产。输入版本、建题与执行命令见 [v1 文档](../../../docs/evaluations/news-admission/aihot-observed-membership/v1/README.md)。历史 `aihot-prefilter` 是不同参考语义，不能混合成绩。

## 当前操作入口

在项目根运行 `PYTHONPATH=src:. uv run python evals/news-admission/aihot-observed-membership/evaluate.py --help`。`validate --dataset <题库>` 只校验冻结资产；`run` 使用相同 dataset 并传 `--config`、`--env-file`、`--split dev|regression`、`--seed`、`--label`。`--limit` 控制题数，不传则运行所选 split 全部；`--workers` 控制逐题模型并发，按共享 API 容量安排。模型调用会产生费用，文档整理和 validate 不调用模型。

候选用 `--prompt evals/news-admission/prompts/<name>.json`；省略时使用生产源码模板。`--exclude-run` 可重复指定已见轮次，抽样按 seed 与 case_id、不按标签平衡。失败恢复用 `--reuse <同身份run>`，只复用成功逐题结果；任一题失败时不从成功子集报告正式整体 P/R。原始输出及失败尝试归 `runs/news-admission/aihot-observed-membership/<version>/<UTC-date>/<UTC-time>/`，元数据归 experiments 同分区。

计分前按[人评说明](../../../docs/evaluations/human-labels.md)应用已有用户票；原 AIHOT 观测与人评优先结果分开保存。指标定义见 [metrics.json](metrics.json)，当前成绩及候选是否采用见[status](../../../docs/evaluations/news-admission/status.md)。不把建题成功、validate 成功或同题重评分称为模型优化。

## 可选的原始引用上下文

用户已批准仅离线测试已有原始引用上下文，见 [ADR](../../../docs/adr/20260919-7ac2-test-prefilter-archived-quote-context.md)。在既有 `run` 命令同时传 `--prompt evals/news-admission/prompts/ai-context.json --quote-context` 即启用；不传 flag 保持原行为，不必重建题库或改变 gold。

候选只从当前 dataset manifest 校验过的 `shared_evidence/raw-inputs.jsonl` 读取 raw，按 `extra.referenced_tweets[type=quoted].id` 关联 X ID。引用必须在本题 `provenance.observed_at` 之前已观察到；仅一个实质版本、正文非空时注入单跳标题/正文/作者/URL，正文最多4000字符。不补网络、不用 AIHOT 输出、不递归、不注入 reply。未知或冲突维持原输入、不删题。额外原文是被评测候选的输入处理，不是 gold 变化。

每个 run 的 `quote-context.jsonl` 保存逐题 available / missing_as_of / ambiguous / empty_body 及所选 raw 摘要、来源轮和观测时间；对象身份保存 raw/代码/解析结果摘要。全部原 cases 不变。`--reuse` 支持同题集同候选的失败恢复；扩大题集不能复用小题集上下文 run（整体解析摘要不同会拒绝），需新 run。该限制是保守缓存边界，不会把旧预测冒充新增上下文结果。

## 专业流上下文候选

`--prompt evals/news-admission/prompts/professional-sharing.json` 是C9，`professional-sharing-business-focus.json` 是C10；均与 `--quote-context` 配合，仅供离线实验，不改变生产默认。C9允许依据专业上下文理解短分享，C10再检验一般投资商业报道的AI主次关系，依据和边界见[决策](../../../docs/adr/20260920-91bd-test-prefilter-professional-sharing.md)。

沿用本入口的 `run --dataset ... --config evals/_shared/configs/baseline-ark.json --env-file ... --split dev --limit ... --seed ... --label ... --prompt ... --quote-context`。新验收须用重复的 `--exclude-run <历史run>` 排除全部已见身份（含开发、诊断、先前验收以及另存的规则校验 `cases.jsonl`）；原split与实际实验角色分别记录。失败恢复添加 `--reuse <本次同身份run>`，只补失败，不抹掉原attempt；确定性规则用 `PYTHONPATH=src:. uv run python -m evals._shared.admission_policy --source-run <完整模型run> --label ...`，零新增调用。

归档按UTC秒命名；同对象同版本的并行启动若碰撞会在调用前拒绝，不能覆盖旧目录。模型运行和零调用投影都占该分区；确认首路已输出独立运行目录、且UTC秒已变更后再启动下一路，之后模型调用可并发。实际质量与预算以[对象状态](../../../docs/evaluations/news-admission/status.md)为准，不从候选名称推断已达标。
