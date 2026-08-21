# ADR-009: 用有界 JSONL 记录已送达告警通知

- Status: accepted；「notification-only、D3 不写入」的范围陈述由 ADR-021 supersede
- Date: 2026-07-22

## Context

U6 需要让 A1–A4 与 PERF 具有同一个可查的通知历史，便于回答「哪条规则、哪个 severity、在什么通道成功送达了 firing/resolved」。这份历史不应增加 `radar.db` 的 schema/写锁耦合，也不能把调用 attempt 误表达为用户已收到的通知。`admin alert-check` 与 `performance-probe` 是独立调度器，会并发写同一历史；该副作用不得阻断告警投递或状态持久化。

## Options Considered

### Option A: 写入 `radar.db`

- Pros: 可用 SQL 查询，事务与 schema 工具现成。
- Cons: 告警审计会和 serve/pipeline 争用主业务 DB 写锁，需要 schema 迁移与额外生命周期管理；一个 best-effort 运维副作用不应成为主库依赖。

### Option B: 记录每次 sender attempt

- Pros: 能复盘失败次数和 transport 错误。
- Cons: attempt 不等于 notification；将失败、`skipped=True` 或畸形返回写入同一历史会让运维者误以为消息已送达，也会无界放大重试噪声。

### Option C: 独立的 notification-only JSONL

- Pros: 不改主 DB，人和 CLI 都易读；可直接以 transport receipt 的成功快照作为边界，并为 PERF rollup 保留结构化 `values`。
- Cons: 需要自行处理两调度器并发、retention、损坏文件和重写成本；它不是强一致账本。

## Decision

选择 Option C。A1–A4 和 PERF 在共享的 `_apply_alert_results` sender 汇流点生成 receipt，每个**成功 sender invocation**在 `data/alert-events.jsonl` 中写一行：

```json
{
  "ts": "2026-07-22T22:00:00+08:00",
  "rule_id": "A4",
  "severity": "page",
  "type": "firing",
  "detail": "...",
  "values": {},
  "channel": "ALERT"
}
```

「成功」在 sender 返回当刻快照：只有返回值是 mapping 且 `skipped is False` 才成立。`None`、畸形值与 `skipped=True` 不写 ledger。severity 转换如果成功执行「旧 resolved → 新 firing」，就写两行，用 `(rule_id,severity,type)` 区分。`PERF:rollup:busy` 只写合成事件，子 cell 保留在 `values.cells`。

根据 D1/D4/D5，JSONL 写入采用下列有界策略：

- 写时按 `ts` 裁掉 14 天前的事件；它是短期运维查询面，不是长期数据仓库。
- 以稳定、不随 ledger `os.replace` 换 inode 的 `data/alert-events.lock` sidecar 做 `flock(LOCK_EX|LOCK_NB)`；短退避重试，1 秒超时后跳过本批。
- 拿锁前和锁内都检查 64 MiB 上限。超限时不读、不重写、不覆盖原 ledger；这是对损坏或失控文件的操作成本熔断，不是常态容量目标。
- 锁内顺序为读取现有行 → 加本批成功通知 → retention 过滤 → 同目录临时文件 → `os.replace`。ledger 与 lock 都必须是 regular file。
- ledger 读取/JSON 解析、非 regular file、锁超时、超限、临时文件或 replace 任一失败都 fail-open：记录包含 `event_path` 和异常类型的 error，保留原 ledger，并继续返回 sender receipt 与持久化告警 state。`state_path`、`event_path` 与稳定 lock path 必须互不 alias；公开入口在调用 sender 前对 alias 明确拒绝，不把它当成可跳过的 ledger I/O 故障。

## Consequences

- 运维者可用 `tail`/`jq` 直接查询最近成功投递，不需要读主业务 SQLite，也不会把失败 attempt 当成已送达。
- ledger 是 notification-only、best-effort 的派生历史，不是告警 state 或 transport attempt 的权威账本。ledger 自身故障期可能缺行；失败原因应查 alert/performance 错误日志，不能从「无 ledger 行」推导「无发送 attempt」。
- retention 只在有新的成功通知写入时执行；长时间没有新通知时，超期行会留到下一次成功写入。
