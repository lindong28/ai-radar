# ADR-053：web 启动 migration 遇 database is locked 时有限退避重试

- Status: accepted
- Date: 2026-08-17

## Context

`logs/serve-access.err.log` 累计 93 次 web server 启动在 `create_app → db.migrate()` 处 `sqlite3.OperationalError: database is locked` 失败（连接已有 5 秒 `busy_timeout`），进程退出后由 launchd `KeepAlive` 以默认 10 秒节流重启循环恢复。日志无时间戳，无法判断这些失败是否全部由 ADR-052 所修的锁误回收造成的双 pipeline 并发引发，锁修复后是否归零未知。曾考虑的「全面加固 DB 写并发」（全局提高 busy_timeout + 各写事务重试）经评审否决：候选的非 pipeline 写者经核实根本不写 radar.db（sync 对生产源 `query_only`、shadow 写独立 `wechat-discovery.db`、wechat2rss healthcheck 不连库），全局重试还会在各入口引入不均匀副作用（延长阻塞、可能重复抓取 / LLM 调用）。

## Decision

只给 web 启动路径的 `db.migrate()` 调用加有限退避重试：遇 `database is locked` 时整次重跑 `db.migrate()`，退避 0.5s 起指数至上限 5s，**总窗口 30s**（= 3× launchd 默认节流间隔：短于一个间隔则重试相对重启无增益，长于 ~3 个间隔后进程内等待相对交还 launchd 无增益）。每次重试写一条含累计等待时长的 warning 日志；窗口耗尽**重新抛出原异常**（进程退出 → 回到 launchd 既有恢复链 + stderr 可见），不吞。作用域仅 `AI_RADAR_PRE_MIGRATED_DB != 1` 的启动路径；稳态请求路径与其余写入口的 DB 行为一律不变。

「整次重跑」依赖的是迁移器既有的可重入设计（混合机制：004/016/017 走 `airadar_migrations` 表、003 走 schema 比对、其余靠 SQL 可重入）——launchd 重启循环本就是这条路径的每日实证。

## Alternatives

- 全局提高 busy_timeout + 写事务重试：无证据对象（见 Context），副作用不均，评审判 blocker。
- 完全不动、锁修复后观察：93 次实测记录在，migration 幂等、重试无外部副作用，修复成本与风险都低，故不采。
- 无限重试：把持续争用隐藏成永久未就绪，且劣于既有 launchd 循环的可观测性。

## Consequences

- 非 pre-migrated 启动遇锁时从「整进程重启循环」变为「进程内有限等待」，可用性下界与现状持平（耗尽后回到同一循环）。
- 验证规格（评审处方，全部采纳）：故障注入测试构造真实历史 schema（按迁移序列正跑到中间步，不用 user_version 倒退），在被测连接让出自身写事务后由**独立连接/进程**取得写锁，断言下一条真实迁移 SQL 收到 locked（含阳性对照）；注入点须选已有部分状态持久化、后续仍有真实写 SQL 的迁移边界，不得用 monkeypatch 插入生产中不存在的 commit 制造假事务边界，也不得只选无破坏性的 `CREATE IF NOT EXISTS`。恢复后 oracle 用语义比较：`sqlite_master` 归一化集合 + `airadar_migrations` 语义字段（迁移 ID，不比 applied_at）+ `PRAGMA integrity_check`=ok + 植入哨兵业务数据比对恢复路径与直跑路径的不变量 + FTS integrity-check；`user_version` 无判别力，移出 oracle。
- 重试日志、耗尽重抛均入测试。

## Scope and unverified items

93 次失败的时间分布不可得（日志无时间戳）；锁修复（ADR-052）后该失败是否自然归零未知，若归零则本重试成为纯保险。Linux systemd 路径用 `--pre-migrated-db`，不进入该分支。
