# Issues

> Agent 驱动的轻量 issue tracker，按 domain 分文件。
>
> 协议：`~/.claude/references/docs-organization-protocol.md` §4.8。

| 文件 | Scope |
|---|---|
| [ux-issues.md](ux-issues.md) | 端到端测试发现的产品 UX 问题（contract 在实际产品中被 broken） |
| [ux-contract-issues.md](ux-contract-issues.md) | contract 本身的问题（定义缺失 / 不准确 / 过时） |
| [deploy.md](deploy.md) | 部署与 DB 同步链路的运维问题（sync/apply/cron/verifier，含影响其验收的测试基线） |
| [docs-quality.md](docs-quality.md) | 文档自身的质量债（README 定位/重复/可观察性等审查遗留） |
| [alerting.md](alerting.md) | 服务故障告警的设计质量债（值不值得 page、严重度、消息说什么、要不要合并、基线可行性、留痕） |
| [cost-observability.md](cost-observability.md) | LLM 成本计量、定价、报告与告警消费面的未闭合项；金额口径只覆盖 `llm_usage` 记录行 |
| [general.md](general.md) | 项目级未分类问题（reliability / 工具链 / 文档错位等） |
| [harness-issues.md](harness-issues.md) | Agent harness、wrapper、hook、plugin 或 skill 行为问题 |
| [archive/closed.md](archive/closed.md) | 已 resolved / wontfix 的历史 issue 归档 |
