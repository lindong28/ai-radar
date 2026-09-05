# ADR-021：告警事件 ledger 同时审计投递与合并抑制决策

- 状态：Accepted
- 日期：2026-08-11
- 范围：`data/alert-events.jsonl`；不把它提升为告警状态或 transport 去重真源
- Supersedes：ADR-009 中「notification-only、D3 不写入」的范围陈述
- Superseded in part：64 MiB 硬上限与可靠 14 天边界陈述由 ADR-20260904-d708 supersede；当前仅在成功写入时裁掉 14 天前记录

## 决策

保留同一个 14 天有界、sidecar lock 保护的 JSONL，但显式区分三类事实：成功投递的 firing/resolved、D3 成功投递与解除、以及规则合并产生的 `type=suppressed` 内部决策。抑制行使用 `channel=INTERNAL`，记录 carrier、suppressed、reason 与 heartbeat freshness；投递行记录 episode identity 与 notification nonce。查询推送次数必须筛选 `channel != "INTERNAL"`，查询事故 episode 必须按 `episode_since` 去重，不能直接把总行数当事故数。

D3 只有 transport 成功后才进入 active；首次发送失败下一轮重试，dedup clear 失败保留 re-arm 义务，间歇未出现的模型继续保留旧价格签名，调价通知成功后才推进签名。D3 的成功 firing/resolved 同样进入 ledger。

## 原因

心跳门控的 A1/A2/A5 合并会主动不发送部分规则；若该决定只存在内存中，错误抑制无法回放。另一方面，D3 原覆盖式状态会在条件解除后抹掉历史，且失败发送仍被当作 active，造成永久漏报。把「已投递」与「被抑制」用字段分开，既保留可追溯性，也不把内部决策混成用户收到的消息。

## 取舍与边界

ledger 仍 fail-open，发送与状态持久化仍不是原子事务；它是便于审计的历史，不是 exactly-once 证明。INTERNAL 行同样计入 64 MiB 上限和 14 天裁剪。现有 reader 若统计投递，必须按 `type`/`channel` 过滤。
