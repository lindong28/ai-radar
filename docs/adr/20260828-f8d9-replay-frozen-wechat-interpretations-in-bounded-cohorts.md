# ADR-20260828-f8d9：先修零向量，再按有界 cohort 回放冻结的微信解读

Status: Accepted

Date: 2026-08-28

## Context

本次恢复决策输入快照曾有 279 个 KB 零向量、69 个从未解读的 enabled WeChat item、5 个普通可重试错误，以及 152 个 `error_retry_count >= 8` 的冻结错误；这些是执行基线，不是会随 pipeline 运行自动更新的当前状态。对应恢复记录是 `data/recovery/a5-interpret-recovery-decision-20260828.md`（写入于 2026-08-28T18:08:16+08:00），152 个冻结行另由 2026-08-28T18:04:16+08:00 的 `data/recovery/radar-pre-a5-frozen-replay-20260828T1804SGT.db` 锚定。152 个冻结错误全部属于历史 HTTP 402（落库错误文本包含 `Error code: 402`），且没有 URL 或标题匹配的 KB entry；当时 279 个零向量的 embedding text 合计 98,454 字符且 summary 文件无缺失，152 个冻结 item 的正文合计 628,760 字符。单篇真实 canary 证明当时 ARK summary、OpenAI embedding、DB 与 KB 写入路径可用，但不证明全量余额、容量、时长或长尾输入均可成功。

DB 的 `error IS NULL` 与 `kb_synced=true` 不能单独证明 KB 完整。ai-assistant 可能在 summary 文件与 index 已写入后才因 embedding 失败留下 partial KB；后续 retry 命中 cached URL 时，AI Radar 会把该 entry 视为已同步。因此本次恢复的成功 oracle 必须覆盖 DB、index、manifest 与非零向量的逐 item 对应关系。

## Decision

保持 `AI_RADAR_ENABLE_INTERPRET=true`，先通过 ai-assistant 的稳定入口 `run.sh --build --user dong_lin` 执行 incremental rebuild。该实现只计算缺失或全零的向量并复用现有非零向量；验收要求 index、manifest 与 vector 行精确对齐，决策前 279 个零向量全部变为非零。

冻结回放之前，先在 `.pipeline.flock` 排他锁保护下重取普通候选的精确 item ID，并用标准 `interpret --limit 30` 分批处理。首个冻结 cohort 为 5 个 item，后续 cohort 每批最多 25 个；只有当前普通候选数量不超过 `30 - cohort_size` 时才创建下一 cohort，避免标准 runner 被更高优先级候选占满而没有消费到 cohort。

每个 cohort 在删除前把完整原始 `wechat_interpretations` rows 保存到同一个 recovery SQLite artifact，并核对精确 item ID、条数与摘要。删除只命中仍属于 enabled WeChat、`error_retry_count >= 8`、错误文本包含 `Error code: 402` 且 item ID 属于该 cohort 的 rows；随后在同一排他锁轮次运行标准 `interpret --limit 30`。恢复快照只允许选择性恢复“已删除但 runner 尚未重建”的行，禁止覆盖新的 success 或 error。

Cohort verdict 按精确 item ID 判定。actual-consumed 不使用运行前候选快照推定，而是对比 runner 前后 `wechat_interpretations` 的完整行状态得出；变化行数超过 limit 或出现无法解释的并发写入时判为不可核实。未被 consumer 处理记为 `NO_VERDICT`，cohort 保持 open；新错误记为 `FAIL` 并停止扩大；`error IS NULL` 且 `save_decision=0` 记为合法成功且不要求 KB。

`save_decision=1` 时，验证器必须对 item 的真实 URL 调用 ai-assistant 稳定入口 `run.sh --verify-url-vector`，只接受 `status=ok`，并以消费者返回的 `kb_slug` 为准，不得用 AI Radar DB slug 推定 KB slug。该命令在 ai-assistant 的 per-user 锁内取得 index、manifest 与 vectors 的同一时刻快照，同时验证 URL 映射、三层行对齐、向量维度以及该行有限且非零。cached hit 不豁免向量检查，任一 zero、missing 或不可核实状态都停止后续 cohort。候选身份、actual-consumed、DB verdict、URL-to-KB 映射与向量完整性分别记录错误类别，禁止折叠成单个“失败”。

标准 runner 同轮消费的非 cohort item 也记录为 actual-consumed，并按相同的 `save_decision` 与向量 oracle 验收；它们不计入 cohort 成功。每轮释放 flock 后重新读取生产队列，只有当前 cohort 全部成功才进入下一 cohort。

## Rejected alternatives

- 一次性删除全部 152 行：会制造无批次身份的大 pending 集合，无法在小样本失败后停止扩大成本。
- 只修 279 个零向量：无法恢复原始 A5 告警中明确冻结的文章。
- 逐 slug 执行 279 次 `--add`：重复 API 调用与向量文件写入，而既有 incremental build 已提供更窄的批量语义。
- 只把 retry counter 改为 0：偏离现有“删除 row 后重试”的恢复契约，且仍受旧 `processed_at` 退避影响。
- 先新增批量 replay CLI：长期能力可能有价值，但不是本次一次性恢复的前置条件，会延迟生产数据修复并扩大代码变更面。

## Scope and stop conditions

本决策只覆盖上述执行快照中的 enabled WeChat 普通积压、152 个历史 HTTP 402 frozen item，以及基线中的 279 个 KB 零向量；不关闭增量 interpret，不修改成功 rows、disabled source rows 或非 HTTP 402 frozen rows。执行期间新进入的普通流量记录为边界，不作为历史回放永不终止的条件。

任一 provider 402/429、DB lock、partial KB、零向量、item oracle 不可达或 cohort item 未被实际处理，都只对当前 cohort 产生 `FAIL` 或 `NO_VERDICT`，并阻止创建后续 cohort。精确费用、全量耗时、279 inputs 的单次 provider limit 与 152 篇 ARK 余额仍未事前核实；首批成功不得外推为剩余输入必然成功。

Decision review: initial proposal was rejected because DB success could mask zero vectors and ordinary candidates could occupy all 30 runner slots. A later verifier review also rejected DB-slug-to-manifest lookup and pre-run candidate snapshots as proxy evidence. The final oracle uses runner before/after row diffs, the consumer's `--check-url` mapping, a locked KB snapshot, an ordinary-queue drain, a slot gate, complete-row recovery snapshots, and a distinct `NO_VERDICT` state. The replay decision was reviewed by `/root/a5_historical_replay_decision_review`; the corrected verifier must pass `/root/a5_recovery_review_gate` before its first production use.
