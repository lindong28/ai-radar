# ADR-016：废弃成本列在滚动发布期接受但不消费旧数值

- 状态：Accepted
- 日期：2026-08-11
- 范围：`llm_usage.cost_usd` 的滚动发布兼容；不改变查询时派生成本语义

## 决策

派生成本是唯一成本真相。active `llm_usage`、legacy main-db `llm_usage` 与 `item_evaluations` 三处历史 `cost_usd` 数值在迁移时清为 `NULL`，当前 writer 始终写 `NULL`，所有查询与审计都忽略该列。为了避免仍在运行的旧 writer 与 NULL-only CHECK 冲突后把整行 usage 静默丢弃，滚动发布兼容窗口暂时允许旧 writer 写入 numeric；严格 `record_llm_usage()` 对其它 SQLite 拒绝继续向调用者抛出。已经取得付费模型结果的 DeepSeek/ARK 与 interpret consumer boundary 使用 ADR-017 的 best-effort metering wrapper，把失败记录到独立日志而不把它误报成 provider/interpret 失败。

## 取舍

只保留 NULL-only CHECK 无法修复已经加载旧代码的进程，其拒绝仍会被旧函数静默吞掉；只移除 CHECK 而继续吞错则无法发现其它数据库拒绝。选择同时放宽兼容和显式失败，使旧 numeric 暂存不影响成本结果，同时让真正被拒绝的计量写入可观察。

## 边界与退出条件

本决策不恢复已经丢失的历史行，也不证明所有外部旧进程已经退出。旧进程完全退出后，`cost-audit` 必须同时确认 active `llm_usage`、legacy main-db `llm_usage` 与 `item_evaluations` 的 `cost_usd IS NOT NULL` 计数均为 0，再另行评估恢复 NULL-only CHECK；回滚同样需要先清值、重建约束并复验，不宣称近零成本可逆。poison numeric 回归必须持续证明 stored value 不改变 `/admin/usage` 或 `cost-audit` 的派生结果。

Migration 016 会整表重写 `item_evaluations`。Mac primary 已发生一次 388.6MB 重写；下一轮 db-sync 会因非 FTS schema 不同走 ADR-014 base-copy 自愈，预期产生 972MB–1.28GB 的一次性传输，不属于 `<20MB` 稳态 gate。曾短暂使用的孤儿 marker `014_nullable_evaluation_cost` 只有在 nullable schema 已存在时才视为 016 alias；migration 017 会删除该 marker、登记规范的 016/017 marker，避免同一大表再次重写。
