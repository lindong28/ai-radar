# ADR-023：以记录行为 LLM 用量派生指标定义测量范围

- 状态：Accepted
- 日期：2026-08-12
- 范围：从 ai-radar 当前 `llm_usage` 记录行派生调用数、token、金额或 cohort 统计的查询、API、页面、告警、周报与报告

## 背景

`llm_usage` 是已落下的 usage 行集合，不是完整的付费 attempt ledger。早期消费面通过列举已知排除类说明缺口，但新的漏行入口反复使清单失真：provider 没有返回 usage、下游解析/校验/保存失败，以及没有接入 stage metering 的调用点都可能让付费调用没有记录行。ISSUE-021 继续跟踪 attempt ledger 缺口；在它关闭前，现有消费者仍需要一个不会随漏行类型增加而失效的稳定解释契约。

## 考虑过的方案

1. 新建跨消费面 ADR，以数据来源正向定义范围，并让 README、architecture、operations 与 API 字段作为消费者投影。
2. 继续让 ADR-020 的跨窗比较和 ADR-022 的 A6 生命周期各自携带局部措辞，不设全局 owner。
3. 只在 ISSUE-021 与可变运行文档中记录限制，等 attempt ledger 完成后再定义契约。

方案 2 会让新增消费面没有稳定 owner；方案 3 把已经接受的当前解释契约与待修缺口混在一起，ISSUE-021 关闭时还可能丢掉历史决策。因此选择方案 1。

## 决策

所有从当前 `llm_usage` 记录行派生指标的 ai-radar 消费面遵守同一个正向不变量：

- 对同一计价基础下的非负可加量，调用数、token 合计和金额合计只来自记录行，因此是该基础下全部相关付费调用对应总量的下界。它们不是 provider 账单或实际付款。
- 均值、占比、环比和比较只描述 recorded cohort；相对全部相关调用真值的偏差方向未知，不能把商或比例称为下界。
- 新增查询、API、页面、告警、周报或报告时，必须把对应 scope 带到消费者可见的结果中。ADR-023 是这一跨消费面约束的规范 owner；README、architecture、operations 和 `measurement_scope` 字段是其投影，ADR-020/022 中的相同表述是局部应用。
- 独立 provider 账单或未来完整 attempt ledger 必须声明自己的 measurement scope，不强行继承 recorded-row 措辞。

## 与既有决策和开放问题的边界

- ADR-017 决定已经取得付费结果后计量失败如何处置：保留结果并显式报错；它不保证一定产生 usage 行，也不定义聚合解释。
- ADR-020 决定跨窗比较的 tariff/cache 归一化；本 ADR 只限定这些比较描述哪个 cohort，不改变归一化算法。
- ADR-022 决定 A6 在途下界可以 firing/升级但不能据此 clear；它是本 ADR 在特定告警生命周期上的应用。
- ISSUE-004 继续负责 tariff 权威性、ARK 订阅与 billed/nominal 边界。本 ADR 不证明任何价格是账单实付。
- ISSUE-021 继续负责失败调用漏记、attempt identity 与 survivorship bias。本 ADR 是缺口存在期间的诚实消费契约，不是修复或关闭证据。

## 后果与验证边界

现有 API、管理页、周报、A6、cost-audit、README、architecture 和告警 runbook 已投影 additive 与 cohort 两类 scope；这些验证只覆盖当前消费面，不证明未来消费者自动合规。ISSUE-021 完成、数据来源改为完整 attempt ledger，或引入独立账单面时，应以新 ADR supersede 或收窄本决策，而不是原地改写本文件。
