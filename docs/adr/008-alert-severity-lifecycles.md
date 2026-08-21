# ADR-008: 告警按 severity 维护独立 lifecycle

- Status: accepted; PERF busy→idle gate (F1) 与 busy rollup (F4) 方向由 ADR-011 supersede，per-severity lifecycle 主体仍 accepted
- Date: 2026-07-22

## Context

A4 现在会在同一 `rule_id` 下根据失败面输出不同 severity：fetch-only 是 notice，items-floor 是 page。PERF 也可根据 busy→idle gate 在 page 与 notice 之间区分。旧状态每个 `rule_id` 只有一组 `state/since/last_notified`；notice 的 cooldown 可能压住随后的 page，resolved 也可能走错通道。这不能满足 U7：higher page 不得被 lesser notice 节流，同时各 severity 重入仍必须遵守自己的 cooldown。

修改还必须无损读取已有 flat JSON state，并为 `journey_monitor.py` 的旧 reader 保留兼容表面。`performance/remediation.py` 则必须只把 page 级 PERF episode 当成可处置 incident，不能让 notice rollup 启动候选修复。

## Options Considered

### Option A: 继续每个 rule 只有一个 flat lifecycle

- Pros: state 形状和 reader 都最简单。
- Cons: 不同 severity 共用 debounce/cooldown，升级会被低通道节流，无法稳定记录 resolved 应回到哪个通道。

### Option B: severity 变化时重置单一 lifecycle

- Pros: 避免共享当前计时器，持久化形状增量小。
- Cons: 往返转换会丢掉目标 severity 自己的 cooldown，可能 spam；重置也会模糊 announced/pending episode 和 resolved 语义。

### Option C: 每个 rule 按 severity 保存独立 lifecycle，另写 flat 兼容投影

- Pros: page/notice 有独立计时器和送达身份，可完整表达转换顺序；旧 reader 可继续读顶层字段。
- Cons: state 形状和迁移规则更复杂，必须明确哪一层是真源。

## Decision

选择 Option C。每个 rule entry 的 `lifecycles` map 是状态机真源：

```json
{
  "state": "firing",
  "severity": "page",
  "since": "...",
  "last_notified": "...",
  "detail": "...",
  "announced": true,
  "lifecycles": {
    "page": {
      "state": "firing",
      "since": "...",
      "last_notified": "...",
      "detail": "...",
      "announced": true
    },
    "notice": {
      "state": "ok",
      "since": null,
      "last_notified": "...",
      "detail": "...",
      "announced": false
    }
  }
}
```

旧 entry 无 `lifecycles` 时，将其 flat 字段原样规范化到 `lifecycles[severity]`；缺 severity 保守归入 `page`。保留 `since` 和 `last_notified`，不因迁移重置 debounce/cooldown 或重复通知。后续统一写新形状。

顶层 flat 字段是兼容投影，不是权威状态。有 firing lifecycle 时投影 active severity；异常 state 同时含 firing page 和 notice 时**优先 page**，不隐藏高严重度。全部 ok 时投影 ok 和上下文指定的 preferred severity。规范化只负责 schema shape，不静默修复异常 double-firing state；关闭与 resolved 必须由正常状态转换执行。

### Lifecycle invariants

- `page` 与 `notice` 分别持有 `since`、`last_notified`、debounce 和 cooldown；另一 severity 的计时器不参与当前判定。子状态回到 ok 后仍保留自己最近成功投递时间。
- firing transport 只有在 sender 返回成功时才置 `announced=true` 并推进 `last_notified`。失败 episode 保持 firing 但 unannounced，不进 cooldown，下轮重试。
- resolved 只发给 announced episode；从未成功投递的 pending episode 关闭时不伪造 resolved。resolved 是 best-effort，失败后仍关闭，不增加 `resolve_pending` 状态。
- 处理同 rule 的 severity 转换时，固定顺序是「已 announced 旧 severity resolved → 新 severity firing」，且 resolved 沿旧 severity 自己的通道。旧 episode 尚 pending 时静默关闭并继承其 `since`，然后按新 severity 的 debounce 判定；已 announced 的旧 episode 允许新 severity 绕过首次 debounce，但新 severity 自己保留的 cooldown 仍然生效。

## Consequences

- notice→page 不再被 notice cooldown 压住，page→notice 也不会绕过 notice 自己的重入 cooldown。A1/A3 等 fixed-severity rule 只产生一个实际子状态，原 fire/debounce/cooldown/resolve 时序保持。
- 旧 reader 可继续读 flat 投影，但新的安全相关 consumer 不应依赖它。`performance/remediation.py` 直接读权威 `lifecycles.page` firing；只有无 `lifecycles` 的旧 state 才回退到 flat，缺 severity 按 page。因此 notice-only `PERF:rollup:busy` 不会启动 remediation。
- state 文件会在下次正常读写后扩展为新 schema；回滚读取仍可依赖顶层兼容投影。
