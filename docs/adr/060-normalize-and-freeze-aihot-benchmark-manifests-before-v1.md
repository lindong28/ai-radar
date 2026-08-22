# ADR-060：在 v1 首发前规范并冻结 AIHOT benchmark manifests

- Status: accepted
- Date: 2026-08-20

## Context

AIHOT 私有基准集的未发布 v1 同时服务离线 evaluator 与直接阅读 raw JSONL、capture/window manifest 的维护者。独立 schema review 发现同一根因的多种表现：capture 重复保存可由有序 passes 推出的 topology，计数字段未说明实体，若干复制值未标出 projection 方向，capture/window 没有像 item v1 一样接手完整的机器语义版本责任，未来唯一 JSON 报告也没有版本身份。继续逐字段打补丁会保留多套权威与命名机制。

## Decision

首次发布前对 persisted contract 做一次根层规范化。Capture 只持久化有序 `passes` 与 `canonical_pass_index`，删除 `pass_count`、每个 pass 的 `index` 与 `accepted_pair`；报告现场派生 `pass_count` 和 `accepted_pass_index_pair`。Reconciliation 与报告中的所有整数计数在叶名中写明 item-record 或 target-item-id 实体。首个报告带 `artifact_type="aihot_validation_report_v1"`，并冻结完整 version-specific machine semantics。

API page 的 URL 副本命名为 `canonical_request_url_projection`；source 的 OpenAPI digest 命名为 `openapi_saved_public_response_body_sha256_projection`。Window 通过 capture reference 取得 schema/tool，不再复制这两组 authority；保留的 target hash 命名为 `canonical_pass_target_item_id_sha256_projection`。Capture/window 同样建立 version-specific machine-semantics authority；字段、必填性、类型、范围、literal 或 closed-object policy 变化必须发布 v2。

Item JSON Schema 继续冻结 machine semantics，而非 annotation prose 的完整 bytes。每份实际 artifact 仍保存并校验 exact schema bytes/hash，因此旧 artifact 不依赖当前工作树中的说明文字。工具 provenance 成功发布要求 clean checkout（`dirty=false`）：Phase 2 writer 从实际 checkout 产生 identity 并拒绝注入假 provenance，Phase 4 fresh consumer 验证 exact commit 可取得；纯离线 validator 只声明冻结 shape/hash/clean 状态，不声称仓外 Git object 当下可达。`original_url` 仍是 AIHOT 声明的 opaque pairing key，不声明解引用可达。

数据与工具采用双仓发布边界。主仓只保存工具、schema、synthetic tests 与 `benchmarks/aihot` gitlink；private data 仓保存 capture/window/raw/JSONL，并以 exact commit 固定。顺序必须是 data local commit → 经显式授权 push并验证远端 exact ref → 主仓记录 gitlink；data push、远端配置、主仓 push与主分支整合互不隐含授权。消费者须先取得主仓，并用具备 private data 仓 read 权限的 GitHub SSH 身份递归初始化 submodule；无权限时显式失败，不把缺数据降级成空 dataset。

## Alternatives

- 保留重复字段并增加 authority/unit companion map：会增加更多承载与核对成本。
- 把 manifest/report freeze 全推到 Phase 2：会让 Phase 1 的 schema 冻结目标与当前 validator 继续留洞。
- 按每条 finding 分别改名：会保留同 payload topology 重复和多套命名机制。
- 允许 dirty checkout 并保存 tree/patch/source digest：能恢复执行字节，但会把补丁或源码带入 data artifact，扩大内容边界与验证复杂度；本任务已有先 checkpoint、后 capture 的 clean 路径，因此不取。
- 冻结 schema 的完整 bytes：能阻止任何原地文本变化，但把不改变机器契约的说明订正也升级为 v2；exact artifact hash 已承担旧实例的 bytes identity，因此不取。

## Consequences

本决策 supersede 当前 task journal 中保留 `pass_count`/canonical 摘要及第九、十轮相关未发布字段名的局部决定；ADR-049 的方向不变：保留具名人工审计摘要，删除重复权威。旧 persisted keys fail closed。Phase 2 首份 synthetic `validate --report-json` 必须在 Phase 3 首次 data commit 前由维护者直接阅读；此时发现命名问题仍可修订未发布 v1并重跑 gate。首次 data commit 冻结后只能发布 v2、更新 writer/report/consumer，并保留 v1 reader 与旧 artifact 自带的 schema bytes/hash。

首个已冻结 data tip 为 `7d9de5e7e1dde9f3ef3f16361984832698bb6e29`：它包含一次 7 天 public-surface capture、两遍相邻稳定 API pass，以及 `[2026-08-19T00:00Z, 2026-08-20T00:00Z)` 的 348 条和 `[2026-08-20T00:00Z, 2026-08-21T00:00Z)` 的 330 条 window。两窗的 schema bytes/hash、raw response、canonical projection、SSR reconciliation 与验收报告均随 artifact 自持，故 AIHOT 约 7 天 live retention 到期后仍可离线 replay/validate/slice。该基准只陈述采集时刻公开 surface 的观察 universe，不外推 AIHOT 内部 snapshot、筛选或排序语义。

## Scope and unverified items

本决策只覆盖 private AIHOT benchmark v1 plan、module、tests、manifest/report 与双仓 gitlink；不改变 item 的 16 个字段、AI Radar Web/API/DB/judge/renderer，也不声明 AIHOT 内部实现或未知仓外消费者行为。已验证的消费者范围仅是从本地 preview superproject 递归初始化，并由实施环境当前配置的 GitHub SSH 身份读取 private data submodule；其他人员、机器、认证方式与 superproject 远端拉取路径仍未验证。长字段在未知 UI 的展示成本也不在本决策范围内。
