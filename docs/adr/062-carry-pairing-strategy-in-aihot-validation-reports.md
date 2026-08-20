# ADR-062：在 AIHOT 验收报告中自持 pairing strategy

- Status: accepted
- Date: 2026-08-20

## Context

ADR-061 将 `aihot_validation_report_v1` 冻结为按 subject 判别的严格模型，window report 的 `pairing` 只保存四项字段存在计数。FINAL plan 同时规定配对 authority/order 为 `original_url` primary、`original_title` assistance、`aihot_title` fallback。作为唯一下游验收报告接口，现有 JSON 不能仅凭自身表达这三个角色；读者即使看到所有 presence count 相等，仍需查外部 plan 才能知道优先级。

## Decision

在未发布 v1 的 `window_validation` 中新增 `pairing_strategy` closed object，固定为 `primary="original_url"`、`assistance="original_title"`、`fallback="aihot_title"`。既有 `pairing` 继续只保存四项 presence counts；策略与计数分开承担职责。Capture subject 继续要求 `window_validation=null`。

Validator 必须拒绝 `pairing_strategy` 缺键、额外键、role swap、其它 literal、位置式数组及旧 window report shape。报告只声明 pairing key 的 authority/order，不声称实际 live matching quality、`originalTitle` 非空覆盖或下游已经执行配对。

## Alternatives

- `pairing_authority_order` 位置式数组：更短，但角色依赖数组位置，单看一项不能解释其职责。
- 不在 report 携带策略并删除 plan 的自持要求：会让唯一验收接口继续依赖外部文档。
- 把 role literals 混入 `pairing` counts object：把策略与观测计数混成一个职责不闭合的对象。

## Consequences

本决策只 clarification ADR-061 的 `window_validation` 具体 shape；不改变 item schema、capture/window manifest、pairing counts、subject/integrity/tool 边界或产品 Web/API/DB/live 行为。首次 data commit 前可原位修订 v1；首次发布后再改变该 shape 必须发布 v2并保留 v1 reader。首份 synthetic/CLI report 的 direct-read gate同时检查三项角色是否一眼可解释。
