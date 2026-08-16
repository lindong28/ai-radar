# ADR-045：平台错误码必须是整数，特殊冷却只由已记录频控证据触发

- 状态：Accepted
- 日期：2026-08-16
- Clarifies：ADR-025 的本地冷却语义与 ADR-044 的 exact-ret 契约

## 背景

schema v9 开始保存微信后台 `base_resp.ret`，但 SQLite 的动态类型允许 `INTEGER` 列接受实数，协议解析器也曾用 `int(...)` 把布尔、字符串或小数转换为错误码。与此同时，调度 helper 仍会把所有历史 `RATE_LIMITED` 行都解释成已证明频控，即使该行明确标记为 `platform_error_ret_origin=predates_persistence`。这会让不可恢复的旧宽分类继续生成“等到次日”的操作结论。

## 决策

schema v10 为 resolution 与 probe ledger 增加 insert/update trigger，只允许 `platform_error_ret` 以 SQLite integer 类型持久化；v9→v10 迁移先检查既有非空值，遇到非整数时整笔回滚并保持 v9。协议层只接受 JSON integer，明确拒绝布尔、字符串与小数，store writer 同样只接受 Python `int` 且拒绝 `bool`。

特殊次日冷却的唯一判据收窄为：终态是 `RATE_LIMITED`，同时 exact ret 已保存且来源为 `recorded`。历史 `RATE_LIMITED + predates_persistence` 仍保留为不可改写的失败证据，但不再生成特殊冷却或虚假解禁时间。disabled 与 enabled `status` 都必须把 exact ret 或历史缺失状态投影给操作者。

## 后果

- 这项本地调度修复不声明微信官方存在 24 小时、48 小时或任何固定冷却窗口。
- schema v9 引入的字段与历史 provenance 保持不变；v10 只加固值类型和消费语义，不重写既有 attempt。
- 当前 1440 分钟配置仍是默认关闭的 feasibility 阶段保守值，不是最终 multi-account scheduler cadence。
- 未来若观测到新的频控错误码集合，必须先保存 exact ret 和对应实例，再显式扩展分类；不得从旧宽状态反推平台规则。
