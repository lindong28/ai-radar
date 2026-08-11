# ADR-018：A6 只比较现行可报价 cohort 的量结构成本

- 状态：Accepted
- 日期：2026-08-11
- 范围：A6 近 24 小时成本突变；不改变周报与 `/admin/usage` 的历史定价口径

## 决策

A6 在一次评估内用同一份 evaluation-time tariff snapshot 重算当前 rolling 24 小时与 14 个已完成 UTC 日的成本。只有在该 snapshot 下仍可报价的 `(provider, model)` 进入 known cohort；当前无报价的调用统一记为 `unpriced`、排除成本并在消息中计数注明。这样 tariff-only 变化会同时作用于当前窗和基线窗，不会被误报为调用量、token 量或模型组合突变。

每个基线日分别与当前 24 小时窗比较 cache 测量覆盖。覆盖率用 `calls_with_split / calls_total` 的整数交叉相乘判定精确相等（两边均为零视为相等），不设容差。覆盖不等的日不进入中位数；少于 3 个可比日时 A6 保持未武装，并明确说明无法评估。周报的前窗比较沿用同一可比性 gate，但金额仍按 usage `created_at` 的历史 tariff 派生。

## 取舍

精确覆盖率会在采集口径过渡期更保守，可能暂时不报；这比把 cache 字段上线误读成成本下降或突增更可信。当前无报价的历史模型不会阻断整个规则，因为 A6 的承诺本来仅覆盖 priced + nominal；其遗漏通过 unpriced 注记与 D3 通知暴露。

## 验证边界

固定 tariff-only 负例、model-mix 正例、baseline-only discontinued pair、覆盖率相等/不等与少于 3 个可比日。D3 负责 price-changed 通知；A6 在 tariff-only fixture 中 firing 状态与 page 数均不变。
