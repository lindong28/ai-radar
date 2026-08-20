# ADR-061：拆分共享 SSR 响应并按 subject 区分 AIHOT 验收报告

- Status: accepted
- Date: 2026-08-20

## Context

ADR-060 对尚未发布的 AIHOT benchmark v1 做根层规范化后，独立 schema review 仍发现两类结构问题。第一，window 按 item 重复保存同一 SSR list response 的 URL、raw path、hash 与 HTTP 元数据；同一 payload 内的 source date/OpenAPI digest、API canonical URL、pass 首末响应时间也同时保存了可从既有 authority 唯一推出的便利投影。第二，统一 validation report 只冻结了键名，没有标明实际被验证 artifact，也没有完整定义 capture 与 window 两类输入各自可解释的值域；若强迫 capture report 携带 window-only 统计，只能制造误导性空值或零值。

## Decision

在首次 data commit 前完成第二次 v1 结构归一。Window 将 SSR 观察拆成 `tag_observation_responses` 与 `tag_observation_bindings`：前者按 `response_raw_path` 唯一保存本 window 实际使用的 response 元数据，后者只保存 `item_id` 到 `response_raw_path` 的关系。每个 target item 必须恰有一个 binding，每个 response 必须至少被引用一次，引用必须存在；validator 从唯一 raw 重建并要求目标 item 恰出现一次。Capture 删除可由同一 manifest 内 authority 唯一推出的 `source.first_public_response_observed_at`、`source.openapi_saved_public_response_body_sha256_projection`、API `canonical_request_url_projection` 与 pass `first_response_date`/`last_response_date`，在 validator 与 report 中现场派生。

计数与摘要继续使用自解释实体名：`target_item_id_count`、`target_item_id_sha256`，三个 gap leaf 为 `missing_tag_observation_target_item_id_count`、`non_equivalent_tag_observation_target_item_id_count`、`api_ssr_identity_conflict_target_item_id_count`。正式窗口哈希比较使用 `formal_window_target_item_id_sha256_comparisons`，每项携带 `start_inclusive`、`end_exclusive` 与 `equal`，不使用位置式布尔数组。

`aihot_validation_report_v1` 保持单一入口，并以 `subject.artifact_type` 区分 `aihot_capture_v1` 与 `aihot_window_v1`。顶层固定为 `artifact_type`、`subject`、`identity`、`stability`、`window_validation`、`integrity`、`result`。`subject` 固定实际验证 manifest 的 type、canonical relative path 与 SHA-256；`identity` 从 subject 及其 capture authority 重算 source base URL、首个 public response 时间、OpenAPI saved-body digest、clean tool commit 与 schema path/hash；`stability` 固定派生 pass count、canonical pass index、accepted pass index pair、formal-day equality 与两个具名正式窗口哈希比较。Capture subject 的 `window_validation` 必须为 `null`；window subject 的 `window_validation` 才承载 window descriptor、target item id count/hash、tag reconciliation counts、pairing 与 field coverage。

`integrity` 的固定 key 集为 `capture_manifest`、`public_response_raw_replay`、`api_pass_raw_replay`、`item_schema`、`window_manifest`、`items_jsonl`、`tag_reconciliation`。前四项在两类 subject 下只能为 `pass`；后三项在 window subject 下只能为 `pass`，在 capture subject 下只能为 `not_applicable_for_capture_subject`。`result` 只能为 `pass`。所有 object 按精确 key 集而非 JSON 插入顺序校验；类型、范围、literal、额外键、subject 分支与跨字段组合全部 fail closed。Capture manifest validator 同时接收 manifest path，并要求其精确等于 `captures/{capture_id}/capture.json`；writer、window replay 与 report 共用该路径 gate。

## Alternatives

- 保留 per-item response 元数据并只增加一致性检查：仍复制同一 authority，不能消除漂移面。
- 用 body hash 代替 response reference：相同 body 可能来自不同请求，无法稳定表达 response identity。
- 保留同 payload 的便利投影：读者少查一步，但新增第二承载与持续一致性成本。
- 为 capture/window 分成两种 report artifact type：可表达差异，但扩大入口和版本面；subject-discriminated 单一入口已能闭合语义。
- 让 capture report 也携带 window-only 统计：没有自然数据源，只能产生误导性零值、空对象或借用其它 artifact。
- 将 report semantics 推迟到 Phase 2：与 ADR-060 要求在 Phase 1 冻结完整 machine semantics 冲突。

## Consequences

本决策明确 supersede ADR-060 中保留 `canonical_request_url_projection`、`source.first_public_response_observed_at`、`source.openapi_saved_public_response_body_sha256_projection`、pass `first_response_date`/`last_response_date` 与 per-item `tag_observation_refs` 的具体 persisted shape；ADR-060 的 machine-semantics freeze、clean tool provenance 分层、item v1 不变及首次 data commit 后只发 v2 的边界继续有效。ADR-047 不变；ADR-049 的“保留跨明细人读结论、删除重复权威”方向不变。

冻结前必须持久化、重开并由维护者直接阅读一份 synthetic capture manifest、一份 synthetic window manifest、一份 capture-subject report 与一份 window-subject report。机器负例覆盖 orphan/duplicate response、binding 缺失/重复/悬空、绑定 item 在 raw 中缺失或重复、capture path alias、report canonical serialize/reopen，以及两个 subject 分支的非法字段组合。首次 data commit 后再改变上述 shape 必须发布 v2并保留 v1 reader。

## Scope and unverified items

本决策只覆盖当前仓内尚未发布的 private AIHOT benchmark v1 manifest、validator、report 与 synthetic tests；不改变 item 的 16 个字段、AI Radar Web/API/DB/judge/renderer，也不声明 live AIHOT、真实 writer/reader、仓外 consumer 或 Git remote 行为。Phase 2 仍须生成并直读真实 CLI report；Phase 3 仍须对 live capture 与两个 window 重复该观察。
