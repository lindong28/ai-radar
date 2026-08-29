# ADR-20260828-c3a5：微信解读仅对缺失 criteria_reason 立即重试一次

Status: Accepted

Date: 2026-08-28

## Context

AI Radar 的微信解读通过 ai-assistant `summarize.sh` 生成结构化结果。即使调用温度固定为 0，冻结队列第二批 25 篇的生产回放仍有 3 篇因 `summary JSON missing non-empty criteria_reason` 失败，其余 22 篇成功；这条错误来自 provider 已返回文本之后的 summary schema 校验，不是 HTTP 402、429、网络错误或 KB 保存错误。

现有 DB 重试会让首次失败在 15 分钟后再次执行。2026-08-28 21:13，先到期的两篇通过同一生产调用链再次执行，2/2 成功、DB error 清空，并分别通过 exact URL、index、manifest 与 1536 维非零向量校验。该读数证明这条精确错误至少包含一次性可恢复抖动，但不证明每次重试都会成功，也不支持扩大到其它失败类型。

## Decision

只在 AI Radar fresh summarize 路径捕获 `subprocess.CalledProcessError`，且 stderr 命中 `summary JSON missing non-empty criteria_reason` 时，立即原样重试同一 summarize 命令一次。第二次仍失败则继续抛出，由既有 per-item error、15 分钟指数退避和 8 次冻结上限处理。

重试前输出一条包含 item ID、错误类型和 `attempt 2/2` 的诊断行；重试成功后输出一条对应的 recovered 行，第二次仍为同一错误则输出一条 exhausted 行。pipeline 日志可直接计数 retrying、recovered 与 exhausted，从首次真实触发起即可发现该策略是否只增加调用而没有救活文章。

## Rejected alternatives

- 在通用 ai-assistant summary-agent 内重试：会改变全部 summary-agent 消费者，而当前恢复对象是 AI Radar interpret 的生产积压。
- 只依赖 DB 延迟重试：真实读数证明后续尝试能救活，但每次至少等待 15 分钟，会继续拖慢有界冻结回放。
- 对所有 schema 或 subprocess 错误重试：会把 402、429、网络和其它契约错误也再次调用，扩大付费与故障面。

## Scope and known limits

本决策不改变 KB URL hit、`save_decision=0`、`save-from-batch`、重复 slug 保存重试或其它错误的语义；每篇文章最多增加一次立即 summarize 调用。当前 `llm_usage` 不是付费 attempt ledger，首次 schema-invalid 调用仍可能不入账；ADR-023 继续把消费面限定为 recorded cohort，完整 attempt 计量仍由 `docs/issues/cost-observability.md` 的 ISSUE-021 跟踪。本决策的日志只验证重试有效性，不宣称补齐成本账。

独立 decision review 首轮要求补证据区分瞬态与稳定失败，并要求给无效重试建立短期可见读数；上述生产 2/2 恢复与 retrying/recovered 日志设计补齐后，原评审者复核放行。尚未实测的第三篇失败输入不进入 2/2 结论，也不扩大本决策作用域。
