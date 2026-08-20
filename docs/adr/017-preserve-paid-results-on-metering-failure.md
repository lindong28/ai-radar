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

## 背景（补记，2026-08-20）

本 ADR 原文写的是最终形状，没有交代它为什么必须存在。补记如下——标注「据正文推断」的部分是从原文取舍段的线索反推的。

**处境。**成本改为查询时派生之后（ADR-015 定单价区间、ADR-016 定派生成本为唯一真相），每一次 LLM 调用之后都要写一行 usage。写这行的 `record_llm_usage()` 是严格的：record invariant、schema、序列化、SQLite 拒绝一律抛出。这对直接调用者是正确的——计量失败必须响亮。

但在**已经拿到付费模型结果之后**，同一个抛出会穿过 ARK-first 的外层，被误判成 provider failure。后果不是少一行统计，而是触发 fallback**再付一次费**；interpret 侧同理，会触发一次重跑与第二次摘要生成。也就是说，一个写 SQLite 的失败会被翻译成「模型挂了」，而这个翻译在钱和产出上都是错的。触发它的东西相当琐碎：父目录不存在、JSON 序列化失败、caller 构造了非法的 cache token 值——全都发生在付费结果拿到之后。

**被否的方案。**

- **让底层 writer 全局吞错。**一改就把严格调用者的「拒绝必须响亮」契约一起破坏了，而那个契约正是发现计量问题的唯一手段。
- **把同一层 catch 复制到两个 consumer**（DeepSeek/ARK `chat_json` 与 interpret runner 各写一份）。功能上等价，但两份 catch 的异常范围与日志事件形状会随时间漂移，届时「计量失败」这个指标在两条路径上不再是同一个东西。选中的是一个共享 wrapper：`record_llm_usage_best_effort()`（`src/airadar/llm_usage.py`），两个 consumer 各调一次（`src/airadar/provider/deepseek_chat.py`、`src/airadar/interpret/runner.py`）。
- **让 caller 的 programming error fail-fast。**这一条原文明说「已被显式考虑」，不是遗漏。结论是：结果一旦计费，caller 构造错误与磁盘/SQLite 错误对业务处置**后果相同**——都不能撤销已取得的结果，也都不该触发第二次付费。要在开发期 fail-fast，应该去测严格 writer，而不是削弱付费结果这道边界。
- **异步重试或旁路持久化**丢失的计量行。据正文推断，被判为超出当时的 P1 范围；本 ADR 因此只承诺「本进程日志可计数 + 已付费结果不被重跑」，不承诺补写。

**捕获范围是有意划的，不是「catch Exception」图省事。**吸收的是一个类型正确的 `LlmUsageRecord` 进入之后的整段局部计量操作（归一化、invariant 检查、路径准备、migration、序列化、insert）中的普通 `Exception`；`KeyboardInterrupt` / `SystemExit` 等 `BaseException` 不在内；连身份字段都读不出来的对象没有可记录的 metering context，也不在保证范围内。并发迁移的那一步单独处理：marker 检查、schema 检查与 `ALTER TABLE` 必须在同一个 `BEGIN IMMEDIATE` 里，loser 观察到 duplicate-column 或 busy 时重读 schema 并整条 insert 重试一次——为的是不让第二个已付费调用的 usage 行因为一次并发就直接丢掉。
