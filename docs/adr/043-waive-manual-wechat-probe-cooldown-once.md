# ADR-043：对一次获授权的微信后台 shadow probe 豁免本地冷却

- 状态：Accepted
- 日期：2026-08-16
- 范围：账号“歸藏的AI工具箱”、provisional resolution 3、一次 `probe --count 5`
- 关系：仅对此次请求局部豁免 ADR-025 的 1440 分钟本地冷却；不改变 ADR-025 的持久默认，也不改变 ADR-040、ADR-041 的身份验证与 ledger 不变量

## 背景

ADR-025 的 1440 分钟间隔是 feasibility 阶段的本地保守默认，不是微信后台、官方 API、页面提示或 `Retry-After` 声明的 24 小时平台窗口。2026-08-13 的旧 attempt 被本地 parser 归入宽泛的 `rate_limited` 类别，但当时 ledger 没有保留原始 `ret` 与 `err_msg`；该记录不能区分真实频控和其他业务错误，因此不能作为官方 24 小时限制的证据。

当前登录态已通过一次 `searchbiz` 请求取得 resolution 3。用户在知道该 mapping 距创建约 1 小时 44 分钟、推荐项仍是等满 2 小时，以及下面的失败成本后，明确选择立即执行一次文章列表 probe。

## 考虑过的方案

1. 等满 2 小时后执行一次 probe。等待成本小于 ADR-025 的 24 小时默认，同时比立即执行多留出一段保守间隔；这是技术推荐项。
2. 现在立即执行一次局部 waiver。更早取得用于判断当前路线的真实响应，但会立即消费 resolution 3，并承担平台业务错误或风控状态仍未核实的风险；用户选择此项。
3. 等满 24 小时。最保守，但该等待来自本地默认而不是平台证据，会推迟对路线是否可行的区分性检查。
4. 永久降低全局冷却或增加通用 override。会改变所有未来账号与请求的长期风险面，本次证据不足，不采用。

## 决策

允许以临时配置副本绕过 ADR-025 的 1440 分钟 gate，立即对账号“歸藏的AI工具箱”使用 resolution 3 发出且只发出一次 `probe --count 5`。真实 `data/wechat-discovery.toml` 保持 `manual_backend_requests_enabled=false` 和原冷却值；不修改历史时间戳，不增加可复用 override，不自动重试。

请求仍必须经过正常 reservation ledger、一次性消费 provisional mapping，并保留全部 URL host/path、唯一 `__biz`、账号 public biz 和 candidate snapshot 校验。只有返回至少一篇文章，且每篇 canonical URL 的唯一 `__biz` 都匹配配置账号时，才形成可比较的身份验证证据。生产 `items`、`wx_mp2rss` 和定时 pipeline 均不改变。

## 成本与未验证项

- resolution 3 会被这次 reservation 一次性消费；失败后若要再试，需要重新 resolve。
- `IDENTITY_MISMATCH` 会使 mapping 安全失效；该状态只能说明返回 URL 身份与配置目标矛盾，不能据此给 fakeid 归因。
- 业务错误、认证失败、未知请求终态或其他失败都进入 immutable ledger；本决策不授权自动重试。
- 一次 probe 只能验证当前请求，不能证明 14 个账号的配额、长期 cadence、fakeid 可复用周期、分页覆盖或 Mp2RSS 替代已成立。
- 若单次 probe 成功，下一步仍须执行同账号、同 attempt、显式时间窗的只读 Mp2RSS shadow comparison；此后还要用多账号与持续运行证据决定正式调度策略。
