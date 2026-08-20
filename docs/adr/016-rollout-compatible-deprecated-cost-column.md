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

## 背景（补记，2026-08-20）

本 ADR 原文直接从决策写起，没有交代它在解一个什么形状的问题。补记如下——标注「据正文推断」的部分是从原文取舍段的线索反推的。

**处境。**成本改为查询时派生（usage 行 × 当时有效单价，单价的区间语义见 ADR-015）之后，历史遗留的 `cost_usd` 列成了**第二个成本来源**。它有三处：active `llm_usage`、legacy main-db `llm_usage`、以及 `item_evaluations`。两个来源同时存在，读者与审计脚本迟早会读到不一致的两个数，而且没有办法判断哪个对——存进去的那个数当时用的是哪份单价、算的是哪种口径，都没有记录。所以决策是把三处历史值一律清为 `NULL`，当前 writer 也始终写 `NULL`，查询与审计全部忽略该列。代码侧现在的形状与此一致：`src/airadar/llm_usage.py` 的建表语句是 `cost_usd REAL DEFAULT NULL`，迁移里有一句直白的 `UPDATE llm_usage SET cost_usd = NULL`。

真正的难点不是清值，而是**滚动发布**：数据库是共享的，而清值和改 writer 不可能同时发生在所有进程上。已经加载了旧代码、仍在写 numeric `cost_usd` 的进程会继续存在一段时间，长度不受本次改动控制。

**被否的方案。**

- **加 NULL-only CHECK 约束、不放宽。**语义上最干净：数据库直接拒绝任何 numeric 写入。但拒绝发生在旧 writer 那一侧，而旧函数会把 SQLite 的拒绝**静默吞掉**——结果不是「旧数据被挡住」，而是整行 usage 消失。用一次数据丢失换一次约束正确性，方向反了。
- **移除 CHECK、继续吞错。**旧 writer 不再撞墙，但吞错的行为原样保留，于是**其它**数据库拒绝（schema 不符、磁盘问题）同样查不出来。这是把一个可见问题换成一个不可见问题。
- 选中的是**同时放宽兼容与显式失败**：兼容窗口内允许旧 writer 写 numeric（那个值不进任何成本结果，无害），同时让严格的 `record_llm_usage()` 对其它 SQLite 拒绝继续向调用者抛出——已取得付费结果的调用路径则改走 ADR-017 的 best-effort wrapper，把计量失败记进独立日志而不误报成模型失败。据正文推断，这三条是一起设计的：没有 ADR-017 的边界，「让拒绝响亮」会直接把付费结果打成 provider 失败并触发第二次付费。

**代价被明确接受。**Migration 016 整表重写 `item_evaluations`，Mac primary 上实测一次 388.6MB 重写，并预期让下一轮 db-sync 走 ADR-014 的 base-copy 自愈路径、产生 972MB–1.28GB 的一次性传输——明确不算进 `<20MB` 的稳态 gate。恢复 NULL-only CHECK 被推迟为一个**有前置条件的后续动作**（三处 `cost_usd IS NOT NULL` 计数均为 0 之后再另行评估），不是本次的一部分。
