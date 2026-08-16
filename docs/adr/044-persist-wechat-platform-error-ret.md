# ADR-044：持久化微信后台错误码并区分平台拒绝与频控

- 状态：Accepted
- 日期：2026-08-16
- 范围：微信公众号后台 shadow discovery 的 resolution/probe ledger、状态机和人读 CLI
- 关系：clarifies ADR-025 的失败状态与冷却语义；不改变 ADR-025 的 1440 分钟一般默认、`rate_limited` 次日规则、ADR-043 的 one-shot 范围或 Mp2RSS 生产链路

## 背景

2026-08-16 的获授权 one-shot probe 产生 attempt 4，并被当前程序记录为 `rate_limited`。但 `_platform_result` 同时把 `ret=200002` 和 `ret=200013` 归入这一状态，resolution/probe ledger 又没有保存 exact `ret`；因此 attempt 4 只能证明后台返回了被旧 parser 归入宽分类的非成功结果，不能证明真实频控，更不能证明官方存在 24 小时窗口。

当前公开实现把 `200013` 解释为 frequency control，而另一些实现和使用文档把 `200002` 展示为 `invalid args`。这些第三方材料可以暴露本地分类缺陷，但不是未公开后台接口的官方契约；系统必须保存自己的最小原始观察，避免未来继续从易漂移的解释反推事实。

## 考虑过的方案

1. 只在当次 CLI 显示 exact `ret`，不持久化。终端 capture 丢失后仍不可恢复，不能支撑无人值守排障，不采用。
2. 在 schema v9 的 resolution/probe ledger 保存 `platform_error_ret` 及其来源，新增 `platform_rejected` 终态。它保存足以区分已知错误类别的最小权威事实，选择此方案。
3. 同时保存 raw `err_msg`。诊断信息更多，但会把不受控的外部字符串带入私有 ledger 和人读面；当前只需 exact `ret` 即可修复误分类，因此不采用。

## 决策

schema v9 在两张 immutable attempt ledger 增加 `platform_error_ret` 与 `platform_error_ret_origin`。新 `auth_required`、`rate_limited`、`platform_rejected` 终态必须保存一个非零 exact `ret`，来源为 `recorded`；其他新终态不得携带平台错误码。v8 迁移无法恢复旧值，旧 `auth_required`、`rate_limited`、`response_invalid` 仅标为 `predates_persistence`，不得回填猜测值或追溯改判 attempt 4。

`ret=200013` 或明确 frequency 文本才归为 `rate_limited`；`ret=200002` 与其他不属于认证或频控的非零码归为 `platform_rejected`。CLI 对后者只报告 `PLATFORM_REJECTED` 和 exact `ret`，不声称 fakeid 错、账号权限错或接口已退役。

`platform_rejected` 是非频控、非自动重试终态。它不触发 `rate_limited` 的上海时区次日 00:00 特殊冷却，也不生成没有证据的 `next_request_at`。resolution 以该终态结束且不产 mapping；probe reservation 仍一次性消费 provisional mapping、存储 0 candidates。CLI 要求先检查请求目标、登录账号条件和平台契约，不得原样重复请求；修正后仍需显式授权新的 resolve。若其他账号已有 ready mapping，单账号的平台拒绝不阻止操作者选择另一个账号做获授权 probe。

## 后果与边界

- 后续错误即使终端 capture 丢失，仍可从 private ledger 恢复 exact `ret` 并重新解释。
- `rate_limited` 的特殊等待规则保持不变，但只适用于确有频控证据的新记录；v9 之前的宽分类必须同时显示其 exact ret 未记录。
- schema v9 迁移必须全事务完成，失败时保留 v8；只读消费者继续拒绝隐式迁移。
- 本决策不证明未公开后台接口长期稳定，不设正式 scheduler cadence，也不改变生产 `wx_mp2rss`。
