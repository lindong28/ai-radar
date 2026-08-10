# ADR-014: 传输 base-only DB 并在服务器候选槽重建 FTS

- Status: accepted
- Date: 2026-08-10
- 关联: [docs/plans/20260809-fts-rebuild-sync/](../plans/20260809-fts-rebuild-sync/)；ADR-013 的 5 小时 Mac producer 调度

## Context

AI Radar 的 Mac primary 持续写入 SQLite WAL 库，腾讯服务器只服务只读副本。旧同步把含 FTS 的完整 snapshot 与服务器 basis 做 rsync delta；真实增量窗口实发约 1.9GB，其中 `items_fts_data` 的 merge/rewrite 占主要变化，基础表真实新数据只占很小一部分。直接传完整 FTS 会把索引物理 churn 当业务增量付费，但 server serving DB 又必须保持 title、content_text、source_name、author、title_zh 五字段搜索语义与 primary 一致。

初始 strip spike 还证明，每轮 DROP+VACUUM 或每轮 fresh-copy base-only DB 都会重编号大量 SQLite 页面，分别产生约 972MB / 1.28GB delta；“不传 FTS”本身不够，base artifact 的跨轮物理布局也必须稳定。最终生产验收还要求：Mac live DB 不被写、snapshot/manifest identity 可审计、candidate 切流前后都过真实搜索 gate、失败后保留旧服务、崩溃不能无限重付全量 verifier 成本。

## Options Considered

### Option A（选定）: 持久 base-only shipping replica + server candidate 重建 FTS

Mac 从 query-only 一致 snapshot 计算非 FTS 逻辑差异，就地更新跨轮持久 shipping replica并做全表对账；传输该 base-only artifact 与 snapshot-bound manifest v2。Server 保留 immutable claimed base，复制成 mutable candidate，在 candidate 上重建 FTS并验证后才切流。

- Pros: 消除 FTS merge churn，又保留跨轮 SQLite 页面 locality；server 可在 inactive slot 吸收重建/验证耗时；manifest 以 primary 真实 FTS 与应用 HTTP visibility 同时作 oracle；base-only basis 与下一轮 source 同形。
- Cons: 引入 producer logical diff/reconcile、双 artifact identity、manifest sidecar与 crash-consistent apply 状态机；单轮生产仍需约 32–35 分钟并占用数 GB 临时磁盘，频率必须据实测决定。

### Option B: 继续传含 FTS 的完整 snapshot

- Pros: transfer artifact 与 serving DB 是同一字节身份，恢复逻辑简单；server 无需重建 FTS。
- Cons: 实测稳态传输约 1.9GB，绝大多数是与新内容无关的 FTS 页面重写，不满足 `<20MB` 稳态目标。

### Option C: 每轮重新物化 stripped DB

包括在 snapshot 上 DROP FTS + VACUUM，或每轮从基础表 fresh-copy 新 base-only DB。

- Pros: 不传 FTS，且实现表面上比 persistent logical delta 简单。
- Cons: SQLite 页号与内部指针在每轮物化时广泛改变，实测 delta 约 972MB / 1.28GB；VACUUM 的页面重排正好破坏 rsync locality，因此否决。

## Decision

选择 Option A。Mac `sync-db-to-server.sh` 是唯一 producer；5 小时 cron 通过 `sync-db-cron.sh` 启动它。Producer 用 SQLite backup API 从已设置并回读 `PRAGMA query_only=ON` 的 WAL reader 生成 point-in-time snapshot，不修改 live DB；`logical_delta.py` 对 persistent shipping replica 应用全部非 FTS 表的 PK-based INSERT/UPDATE/DELETE并逐表 count/digest 对账，失配则整建自愈。传输使用 GNU rsync 4KiB delta blocks，两端支持时启用 zstd。

同一轮明确两种 artifact identity：

- **Immutable base-only transfer artifact**：对账后的 shipping replica 在本轮 publish/apply/commit 期间冻结，完整 SHA-256 是 `snapshot_id`；它持有 rollback与复制 authority，也是 committed basis 的唯一来源，不含 FTS objects。
- **Mutable serving candidate**：server 从 claimed transfer artifact 复制到 inactive slot，再创建/重建 FTS；重建改变其字节身份。它可以成为 active serving DB，但永远不能成为 rsync basis或恢复 snapshot。

Manifest v2 是与 transfer artifact 原子配对的 sidecar oracle。它以 `snapshot_id` 和自身 `manifest_sha256` 绑定 identity，记录 primary 真实 `items_fts` 六字段全表 count/digest；每个搜索字段另记录 raw FTS5 `matches`、逐字段 exclusivity、`unqualified_matches` 和应用真实 visibility 下的 `timeline_http_matches`。Server 必须 consumer-first 部署对应 schema，再接受 producer sidecar；missing、malformed、版本或 identity mismatch 都 fail closed。

采用 D5 delayed-final-commit invariant：切换前后 consumer gates 通过以前，旧槽、旧 basis、旧 receipt 都保持可恢复。post-switch public search、canonical route或可用性 gate 失败时，server 自动切回并复验旧状态，quarantine candidate，且不推进 basis/receipt。只有 durable `consumer_verified` 状态可以从 immutable claimed base 写新 basis/receipt并进入 `committed`。

采用 R4-F09 有界重试契约：retry authority 固定为 `(snapshot_id, manifest_sha256, verifier_identity)`。三者不变时，pre-switch crash 每 snapshot 最多一次 automatic fresh rebuild retry；第二次 crash进入 quarantine。确定性 manifest/rebuild/equality/search failure 不消费重试，立即 quarantine。post-switch pending states只允许回滚/quarantine，不允许向前恢复。

`VERIFIER_VERSION` 是 retry authority 的语义版本，当前为 `fts-apply-v4`。当 base verification、candidate rebuild、manifest/row equality、raw MATCH probes、candidate/public HTTP probes或这些 verifier 的直接契约输入发生语义变化时，必须在同一改动显式 bump。若已绑定 artifact/manifest 的 `rebuilding` / `prepared` retry checkpoint 与运行中 verifier 不同，状态进入 `retry_blocked_verifier_changed` 与 `recovery_action=manual-intervention`；若漂移发生在尚未绑定 manifest 的 `claiming`，则 fail closed 并 quarantine。新 verifier在两种情形下都不得静默继承旧 checkpoint 的一次重试权限。

## Consequences

- 2026-08-10 生产 steady round 的 DB rsync `Total bytes sent=16.39M`，manifest 822.90K，合计约 17.21M，低于 `<20MB` gate；相对旧约 1.9GB 降约 99.1%。Bootstrap 转换轮仍可能传输 GB 级，明确免除 steady cap。
- Candidate 在旧槽继续服务时完成 FTS rebuild、全量等价与 HTTP probes；生产两轮切换窗口共 3500/3500 个 canonical HTTPS health samples 全为 200，五字段 IDs/count 与 title_zh exclusivity 均匹配 manifest。
- 复制 authority 不再等于 serving DB bytes。所有恢复、receipt、journal、basis、sidecar cleanup与 operator 验证都必须按 base-only snapshot identity判断，不能对 serving candidate 取 hash后反推 basis。
- `quarantined` 会把当时可捕获的 base/candidate/manifest 与 failure record 持久保存到 `data/quarantine/<snapshot_id>/`；failure record 的 `evidence_status` 明确记录未捕获或不适用的 artifact。`retry_blocked_verifier_changed`、`rollback_blocked_invalid_oracle`、`finalize_blocked_invalid_authority` 是人工处置状态。运维入口与证据路径见 [services.md](../operations/services.md#db-sync-职责验证与故障证据)。
- 当前每轮生产约 32–35 分钟，Mac临时磁盘约 3.7GiB，Tencent apply峰值内存约 2.3–2.9GB。5 小时 cron 已是当前 freshness path，但最终频率与连续自动三轮验收仍由上游 G2/P3决定；本 ADR 不把 scheduler存在等同于完成这些 gate。
