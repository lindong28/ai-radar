# ADR-017：计量失败不得伪装成模型失败

- 状态：Accepted
- 日期：2026-08-11
- 范围：已经取得付费模型结果后的 usage metering boundary

## 决策

`record_llm_usage()` 保持严格：record invariant、schema、序列化或 SQLite 拒绝继续抛出，直接调用者不会得到虚假的成功。DeepSeek/ARK `chat_json` 与 interpret runner 已经取得模型结果之后，统一改用 `record_llm_usage_best_effort()`；它把一个类型正确的 `LlmUsageRecord` 进入后的 provider usage 归一化、record value invariant 检查、路径准备、migration、序列化与 insert 作为一个局部计量操作捕获。该边界吸收这些步骤中的所有普通 `Exception`，包括 caller 构造出的非法字段值所触发的 `CacheUsageError`，写一条稳定、可计数的 `llm_usage_metering_failure` ERROR（含 stage/provider/model/item_id/异常类型），返回失败状态但不丢弃模型结果，也不进入 provider fallback、breaker 或 interpret retry。`KeyboardInterrupt`、`SystemExit` 等 `BaseException` 不在捕获范围；完全违反函数类型契约、使 `LlmUsageRecord` 身份字段都无法读取的对象也没有可记录的 metering context，不属于这项保证。

首次打开旧 usage DB 时，migration marker 检查、schema 检查与 `ALTER TABLE` 必须处于同一个 `BEGIN IMMEDIATE` write-lock unit。若并发 loser 仍观察到 duplicate-column 或 SQLite busy/locked，best-effort 边界重新读取实际 schema 并重试一次完整 insert；只有重试仍失败才记录 metering failure，不能直接丢掉第二个已付费调用的 usage row。

## 取舍与边界

把同一层 catch 复制到两个 consumer 会让事件形状和异常范围漂移；让底层 writer 全局吞错则破坏严格调用者的“拒绝必须响亮”契约；异步重试或旁路持久化超出 P1。共享 wrapper 是最小、统一的付费结果边界。这里捕获整个局部计量调用，而不是列举 SQLite 异常类型：例如父目录创建、JSON 序列化和非法 cache token invariant 都发生在付费结果之后，任一异常若逃逸都会被 ARK-first 外层误判成 provider failure 并触发第二次付费 fallback。

是否应让 caller programming error fail-fast 已被显式考虑。结论是在 strict `record_llm_usage()` 保留响亮失败，paid-result consumer 必须调用 best-effort wrapper：一旦 provider 结果已经计费，caller 构造错误与磁盘/SQLite 错误对业务处置具有相同后果——都不能撤销已取得的结果或触发第二次付费。此处选择吸收并记录 caller value bug，不是遗漏；若要在开发期 fail-fast，应直接测试 strict writer，而不是削弱付费结果边界。

这项决策只保证本进程日志可计数和已付费结果不被重跑，不承诺丢失计量行的补写或外部日志采集必达。故障注入测试必须分别证明 provider 只调用一次、interpret summary 只生成一次、结果保留、无 provider/interpret 失败，并保留 strict writer 的抛错负控。
