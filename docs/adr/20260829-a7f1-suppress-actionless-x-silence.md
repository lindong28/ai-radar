# ADR-20260829-a7f1：用新鲜终态收据抑制无处置价值的 X 来源静默告警

- Status: accepted
- Date: 2026-08-29

## Context

A7 只按来源最近一条本地 item 的 `fetched_at` 与历史节奏判断静默，因此无法区分“本地没有抓到上游新内容”和“上游本来就没有发布新内容”。2026-08-29 的生产告警点名 AI at Meta、Nathan Lambert 与 Alibaba Cloud；同一轮 pipeline 对三源均返回 `OK fetched=0 inserted=0`，官方 X API 不带本地游标查询得到的最新原创帖 ID 又与生产库 `x_since_id` 完全一致。三个来源确实已经数十至数百小时没有新帖，但本地抓取链路没有漏数据，🔴 page 没有可执行的恢复动作。

X 抓取器已经持久化一份与 item 更新同事务提交的 runtime receipt。成功解析 timeline 会写 `x_reference_status=verified` 与 `x_reference_validated_at`；分页完全排空后才写 `x_cursor_state=checkpointed`；失败会写 `blocked` 并清除 validation time。这个结构化终态比 pipeline 文本日志中的 `OK fetched=0` 更接近“最近一次读取已追平持久游标”的事实。

## Decision

A7 仍先按既有节奏算法找出静默候选。对于 `kind=x` 且 `adapter=x_api` 的候选，只有来源 runtime meta 同时满足以下条件时，才把它分类为“上游安静、本地最近一次读取已追平”，不进入 A7 firing 列表：

- `x_reference_status=verified`
- `x_cursor_state=checkpointed`
- `x_reference_validated_at` 可解析且不晚于当前评估时刻
- validation age 不超过 A2 已有的 `no_success_minutes` pipeline heartbeat 窗口

静默历史与 runtime meta 必须在同一个 SQLite snapshot 中读取，保留抓取器在 writer 侧已经提供的事务一致性。receipt 缺失、非法、来自非 `x_api` adapter、状态为 `blocked` / `pending` / `draining`、validation time 在未来或已过期时，均保留现有 A7 候选与告警行为。非 X 来源不变。A7 evaluator 不新增外部 API 请求。

被抑制的来源仍进入 A7 的健康详情，明确标成“上游未更新且最近一次 X 读取已追平”；它们不是从观察面消失，而是从需要立即处置的 firing 集合移到无需动作的分类。

## Options Considered

### 每轮 A7 直接请求 X API 比对最新 ID

否决。它会给告警判定新增外部依赖、额度与配额消耗，并制造“告警器因为上游故障而无法判断上游故障”的新失败面。现有 pipeline 已在真实抓取路径持久化所需终态。

### 解析 pipeline 日志中的 `OK fetched=0`

否决。该文本只证明 handler 返回成功，不能证明 X pagination 已排空；结构化 `checkpointed` receipt 的区分力更强，也不依赖日志保留与文本格式。

### 将最近一次 `OK fetched=0` 外推到全部 feed/web 来源

否决。普通 RSS/web 的 HTTP 200 与空或旧内容不能区分上游真的安静、订阅服务静默失效或 parser 漏读。本轮证据只覆盖 X API adapter。

### 禁用 A7 或扩大静默阈值

否决。A7 仍是单源死亡不被全站总量掩盖的覆盖面；整体降敏会同时隐藏真实抓取故障。

## Consequences

- 正常、低频的 X 账号在本地已追平且 receipt 新鲜时不再触发 🔴 A7。
- X 抓取失败、未排完分页或 heartbeat 过期后，抑制自动失效，A7 恢复原有告警资格。
- 一次成功读取后立刻发生的单源故障，最多会延迟到既有 120 分钟 heartbeat 窗口过期后重新暴露；本决策复用现行 pipeline 新鲜度档位，不引入新的 SLA。
- RSS、Web 与 WeChat 的 A7 语义保持不变；它们需要各自能证明“上游安静”的收据后才能采用同类抑制。

## Evidence and Review

2026-08-29 10:00 生产 pipeline 的 egress preflight 为 OK，三源均 `fetched=0 inserted=0`，整轮 `failed=0`。同日 10:53 的生产 DB 只读快照中，三源均为 `verified / checkpointed`，validation age 约 20 分钟，`x_since_id` 与官方 X API 返回的最新原创帖 ID 完全一致。

独立 decision review 逐项检查了备选、判据区分力、事务边界、P1/P7/P9、作用域与回滚成本后放行。评审要求实现用同一 SQLite snapshot 读取历史与 receipt，并用 `fresh checkpointed`、`blocked`、`draining`、`stale` 四个分支锁住区分力。

## Scope and Unverified Items

本决策只证明最近一次成功 X timeline 读取已经排空到持久 checkpoint，不证明账号未来持续健康，也不证明非 X adapter 的上游状态。它不解决 A7 已登记的全部覆盖率、零评估源、跨规则合并与严重度缩放问题；这些仍按 `docs/issues/alerting.md` 的既有条目单独处理。
