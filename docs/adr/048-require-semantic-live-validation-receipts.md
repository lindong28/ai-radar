# ADR-048：用语义完整的收据验收来源读取能力

- Status: accepted
- Date: 2026-08-13

## Context

AIHOT 来源对齐的交付门要求分别证明三类事实：AIHOT 当前来源遍历已到达终点并与当前来源契约完成 reconciliation；52 个非 X 主站来源在两轮 live 读取与一次 immutable replay 中满足独立 oracle、生产解析和持久集合一致；单账号 X 有界探测能区分身份连通、时间线连通、terminal checkpoint、draining connectivity、真实帖子读取和离线分页证明。原有 receipt validator 主要校验字段形状与 payload hash，仍可能接受零页 observation、零来源 non-X 或零请求 X 的 `success`，而仅保存计数也不足以证明集合相等。旧的 pre-schema observation 只保留为历史 raw evidence，不满足当前 live gate。

## Decision

Observation、non-X 和 X receipt 使用按状态区分的精确 schema，由 validator 从明细重新推导并拒绝任一状态矛盾。Non-X 每个来源按 `first_live`、`immutable_replay`、`second_live` 保存响应指纹、实际请求次数以及 oracle、production、persisted canonical set 的明细；X receipt 显式记录 `state_scope`、live connectivity、terminal checkpoint、live post retrieval、fetched/inserted counts 和有界请求结果；observation 绑定当前 source contract 并验证 endpoint、分页终态和 reconciliation 分区。路径字段声明相对基准；公开 `/api/v2/sources` 的 `retrieval_validation` 仅投影抓取进程的运行时读取状态，不是 ADR-048 receipt authority；Playwright 两个 Mp2RSS 分支必须绑定同一轮输入与各自 artifact hash 才能汇总为 success。

`/api/v1/sources` 按单独完成的兼容决策继续只公开既有 `feed`、`x`、`wechat` 类型；完整启用来源清单由 `/api/v2/sources` 与 About 消费。本 ADR 不引入 receipt 签名、WORM 存证、定时重验、109 个 X 账号全量 live 探测或历史回填。

## Alternatives

- 只增加空 `success` 的守卫、保留含混字段和仅计数证据：改动较小，但仍不能证明三集合相等、两轮 live/replay 或 X terminal/draining/post-retrieval 的区别。
- 删除结构化 receipts 与验证 UI、改用人工日志：实现更简单，但会撤掉已批准 plan 的验收面，无法支撑“稳定读取”结论。

## Consequences

收据 schema、producer 和测试需要一次性迁移。任何涉及 receipt producer、parser、runner、source contract 或验证代码的后续修改都会使相应 artifact 的代码哈希失效，并要求重新生成 live receipt。错误会在聚焦 validator 负控、两分支 Playwright、fresh AIHOT observation、52-source non-X audit 和单账号 X probe 中显现。

## Scope and unverified items

当前 receipt validators 已接受三份与最终实现哈希绑定的证据：fresh AIHOT observation 覆盖 2,020 条内容与 174 个可见来源且 reconciliation 零缺口；52 个 non-X 来源完成两轮 live + replay 且零失败；`x_openai` 有界 probe 的一次 identity 与一次 timeline 请求均返回 HTTP 200，并以零帖子窗口形成合法 terminal checkpoint。单账号结果仍不得外推为 109 个 X 账号均已 live 可用，零帖子窗口也不证明实际帖子读取。
