# ADR-049：保留人读审计摘要并移除重复权威

- Status: accepted
- Date: 2026-08-13

## Context

AIHOT 来源对齐的未发布数据契约同时服务机器校验与人工审计。Schema closure 发现两类相反风险：删掉全部可派生字段会迫使读者从分页、来源明细和失败列表重算系统已经得出的覆盖面结论；保留所有便利字段又会让 observation index、失败阶段、可选来源状态和来源身份出现重复承载或不明确的权威方向。

## Decision

保留人工审计直接消费、且写入端会从明细重算并拒绝漂移的终态与覆盖面摘要，包括 observation、non-X、source-union 和 v2 source inventory 的状态与计数。删除没有独立消费者的重复承载：observation index 的 `latest` 与 `capture_date_utc`、与顶层 phase 相同的嵌套 `failure_stage`、以及可由 `runtime_configuration_status` 唯一推出的 `enabled_and_loaded`。

Machine source contract 将旧 identity 字段改名为 `derived_aihot_identity`，明确它是由 X `meta.username` 或非 X `kind + slug` 派生的稳定 reconciliation key；validator 继续拒绝不一致值，membership-transition 消费链同轮迁移。Observation index 只保留无语义 UUID 路径和 artifact SHA-256，日期、终态与内容事实由所指 receipt 承载。Source-union receipt 收窄为 `generated_current_contract_projection`：只绑定当前 contract 哈希与 identity 明细，并明确它不是逐 identity observation 或 live retrieval 证据；不再把可变 plan 路径或哈希称为用户审批证明。

## Alternatives

- 删除所有可派生状态与计数：字段更少，但人工审计者必须重算系统已有结论，与人读消息应直接给出影响范围和总体结论的要求冲突。
- 保留全部现有字段：改动最小，但继续留下 index、失败阶段、运行状态和 identity 的第二承载。
- 新增根级 identity authority 描述而不改字段：会增加一份说明副本，且读者仍要把规则映射回每行。
- 用当前 plan 哈希证明用户审批：哈希只能证明当前字节，不能证明用户批准了后续编辑，还会未经授权提高审计强度。
- 删除 source-union receipt：避免权威混淆，但丢失当前 contract 投影的人读审计载体。

## Consequences

未发布 contract、receipt、index、source-union 和 v2 API shape 会一次性调整。Exact-schema、mutation、index-integrity、旧字段零命中和 generated-config byte-equality gates 是首个失败面；当前没有成功的新 schema observation index 或 live receipt 需要迁移，错误可以在 live gates 前对称改回或重新生成。人工摘要仍不是另一份来源权威：producer 和 validator 必须从明细重算并拒绝不一致。

## Scope and unverified items

本决策只覆盖当前未发布的来源对齐契约与验证 artifacts，不改变 `/api/v1/sources` 的 `web` kind 兼容行为，也不增加签名、WORM、周期重验、109 个 X 账号全量 live 探测或历史回填。Fresh AIHOT observation、52-source non-X two-live/replay 和单账号 X live probe 仍须在最终交付前真实运行。仓外是否已有消费者依赖这些未发布字段尚未验证；仓内消费者必须同轮迁移并以旧名零命中收口。
