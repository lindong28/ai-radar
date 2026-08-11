# ADR-017：计量失败不得伪装成模型失败

- 状态：Accepted
- 日期：2026-08-11
- 范围：已经取得付费模型结果后的 usage metering boundary

## 决策

`record_llm_usage()` 保持严格：SQLite 拒绝继续抛出，直接调用者不会得到虚假的成功。DeepSeek/ARK `chat_json` 与 interpret runner 已经取得模型结果之后，统一改用 `record_llm_usage_best_effort()`；它只捕获 `sqlite3.Error`，写一条稳定、可计数的 `llm_usage_metering_failure` ERROR（含 stage/provider/model/item_id/异常类型），返回失败状态但不丢弃模型结果，也不进入 provider fallback、breaker 或 interpret retry。非 SQLite 编程错误仍外冒。

## 取舍与边界

把同一层 catch 复制到两个 consumer 会让事件形状和异常范围漂移；让底层 writer 全局吞错则破坏严格调用者的“拒绝必须响亮”契约；异步重试或旁路持久化超出 P1。共享 wrapper 是最小、统一的付费结果边界。

这项决策只保证本进程日志可计数和已付费结果不被重跑，不承诺丢失计量行的补写或外部日志采集必达。故障注入测试必须分别证明 provider 只调用一次、interpret summary 只生成一次、结果保留、无 provider/interpret 失败，并保留 strict writer 的抛错负控。
