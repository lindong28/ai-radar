# 已观察收录准入评测

使用共享 prefilter runner 和确定性 precision/recall，不调用下游 scorer/enricher，不写生产。输入版本、建题与执行命令见 [v1 文档](../../../docs/evaluations/news-admission/aihot-observed-membership/v1/README.md)。历史 `aihot-prefilter` 是不同参考语义，不能混合成绩。

## 可选的原始引用上下文

用户已批准仅离线测试已有原始引用上下文，见 [ADR](../../../docs/adr/20260919-7ac2-test-prefilter-archived-quote-context.md)。在既有 `run` 命令同时传 `--prompt evals/news-admission/aihot-prefilter/prompts/ai-context.json --quote-context` 即启用；不传 flag 保持原行为，不必重建题库或改变 gold。

候选只从当前 dataset manifest 校验过的 `shared_evidence/raw-inputs.jsonl` 读取 raw，按 `extra.referenced_tweets[type=quoted].id` 关联 X ID。引用必须在本题 `provenance.observed_at` 之前已观察到；仅一个实质版本、正文非空时注入单跳标题/正文/作者/URL，正文最多4000字符。不补网络、不用 AIHOT 输出、不递归、不注入 reply。未知或冲突维持原输入、不删题。额外原文是被评测候选的输入处理，不是 gold 变化。

每个 run 的 `quote-context.jsonl` 保存逐题 available / missing_as_of / ambiguous / empty_body 及所选 raw 摘要、来源轮和观测时间；对象身份保存 raw/代码/解析结果摘要。全部原 cases 不变。`--reuse` 支持同题集同候选的失败恢复；扩大题集不能复用小题集上下文 run（整体解析摘要不同会拒绝），需新 run。该限制是保守缓存边界，不会把旧预测冒充新增上下文结果。
