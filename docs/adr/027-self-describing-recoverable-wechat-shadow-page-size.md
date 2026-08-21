# ADR-027：让微信 shadow 页大小记录自描述且可恢复迁移

- 状态：Accepted；deprecated——后台 family 平台级不可用，见 ADR-061
- 日期：2026-08-13
- 范围：ADR-026 的持久状态演化部分
- Supersedes：ADR-026 的“持久状态演化”段落；其余比较契约不变

## 背景

ADR-026 的 v2 方案用可空 `requested_count` 表示一次 probe 的请求页大小，并把 `NULL` 解释为“该 attempt 早于字段持久化”。这有三个缺口：列名不能直接表达它是请求页大小，维护者只读 SQLite 行时无法区分合法历史未知值与损坏写入，而且“列已添加、`user_version` 尚未更新”的中断迁移无法自行恢复。

`success_*` 和 `new_to_shadow_state` 又依赖写入时的全局 shadow URL 集。如果两个 writer 在写锁外读取同一旧集合，它们可能都把同一 URL 判成首次出现。

## 考虑过的方案

1. 保留 v2，仅在文档里解释 `requested_count=NULL`。这不能让持久行自描述，也不修复中断迁移。
2. 另建 migration ledger 记录每行来源。对于单列演化过重，且把一次 attempt 的解释拆到另一张表。
3. 升到 v3，使用语义明确的页大小列和同屏来源判别，并让迁移及首次发现判断都在单写者事务内完成。选择此方案。

## 决策

`discovery_attempts` 使用 `requested_page_size`，并增加 `requested_page_size_origin`：

- `recorded`：新 writer 实际使用并记录了 1 到 20 的页大小；数值不得为空。
- `predates_persistence`：历史 attempt 当时没有记录页大小；数值必须为空，compare 返回 `NOT_COMPARABLE`。

新写入只允许 `recorded`。v1 历史行迁移为 `predates_persistence`；v2 的非空 `requested_count` 重命名后保留为 `recorded`，空值转为 `predates_persistence`。迁移先取得 `BEGIN IMMEDIATE`，再按实际列存在性补齐或重命名，最后更新 `user_version=3`；重复接手一个部分迁移的库不会再次盲目添加同名列。

`record_attempt` 在读取现有 URL 集之前取得 `BEGIN IMMEDIATE`。首次发现判定、success state 校验、attempt snapshot 与最新 shadow state 在同一事务中完成；基于旧集合构造的第二个竞争写入会被拒绝，不能固化第二条假的“首次发现”。

`DiscoveryAttempt` 直接携带不可拆分的完整候选快照；新写入和窗口比较都不再单独接收第二份 candidate 列表。页大小、观察终点、URL 和发布时间因此不能与另一 attempt 的值混配。

## 结果

- raw SQLite 行能够直接解释页大小是否为真实记录还是历史未知；不靠 `NULL` 猜来源。
- v1、v2 和部分迁移状态都能被同一 v3 opener 接手；未知未来版本仍 fail closed。
- 并发 writer 不能把相同 URL 重复标成 `new_to_shadow_state=1`。
- 现有历史 attempt 不会被伪造页大小，因而不会获得虚假的窗口覆盖结论。

候选发布时间晚于本地 attempt 完成时间仍作为原始发现证据持久化，但在 compare 阶段降为 `NOT_COMPARABLE`，不在 writer 阶段永久拒绝。`success_*` 只表示取得并保存了结构合法的发现响应，不表示它已经满足覆盖比较门禁。这样处理是因为平台时间与本机时钟可能存在偏差，当前没有足够 live 证据定义安全容差；回归测试同时固定“证据保留”和“禁止 coverage 结论”两侧行为。
