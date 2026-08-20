# ADR-063：AIHOT capture 的 public response Date 必须按声明顺序非递减

- Status: accepted
- Date: 2026-08-20

## Context

AIHOT capture 将 `public_responses` 固定为 RSS、OpenAPI 顺序，report 的 `first_public_response_observed_at` 从第一项 HTTP `Date` 派生。现有 validator 只检查 surface 顺序，不检查两条 Date 的 chronology；因此 RSS Date 晚于 OpenAPI 时，manifest 仍通过，report 却把较晚时间标为 first response identity。

## Decision

Validator 要求 persisted `public_responses` 按声明顺序的 HTTP Date 非递减，相等允许，逆序统一返回 `manifest_invalid`。该约束只证明 manifest 内声明顺序与 Date chronology 一致，并保护 report identity 语义；不据此声称真实请求时刻或调用顺序已被验证。API pass coverage 继续由 `raw_pages` 及其既有 Date 非递减 gate独立负责。

Phase 2 fake-transport/writer 测试必须另行证明实际调用顺序为 RSS→OpenAPI，且写入的 `public_responses` 顺序与调用顺序一致。真实 writer 与 live Date 行为当前尚未实测。

## Alternatives

- Report 取两条 Date 的最小值：会把“first response”偷换为“earliest header timestamp”，丢失请求序关系。
- 将字段改名为 request-order date 并允许逆序：表达更精确，但弱化 chronology identity，仍允许自相矛盾的持久化时间线。
- 保留当前行为：让同一 report identity 字段同时容纳正常与逆序 chronology，无法 fail closed。

## Consequences

异常或缓存导致的逆序 Date 会使 capture 失败，而不是生成 benchmark。首个 data commit 前可根据真实 AIHOT 行为重审；首次发布后若要放宽或改变该语义，必须发布 v2并保留 v1 reader。本决策不改变 item/window shape、产品 Web/API/DB 或 live 采集频率。
