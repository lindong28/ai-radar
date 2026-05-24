# ADR-002: Prefilter 模型选用 deepseek-v4-flash 并禁用 thinking

- Status: accepted
- Date: 2026-05-15

## Context

Prefilter 阶段调用 DeepSeek API 对新抓取的条目做相关性过滤，返回结构化 JSON。原先默认模型 `deepseek-v3.2` 在 DeepSeek `/models` 端点中已不可用，需要选择新模型。

DeepSeek V4 系列默认启用 thinking（类似 CoT），API 响应中会将大量 token 消耗在 `reasoning_content` 字段，而 prefilter 需要的结构化 JSON 输出在 `content` 字段。如果不显式禁用 thinking，prefilter 的 token 成本会大幅膨胀且无实际收益。

## Options Considered

### Option A: deepseek-v4（标准版）
- Pros: 推理能力最强
- Cons: 对 prefilter 的简单过滤任务 overkill；thinking 默认开启需要额外处理

### Option B: deepseek-v4-flash
- Pros: 推理速度快、成本低，适合高频轻量的过滤任务；同样支持 JSON 模式
- Cons: 推理能力弱于标准版（prefilter 不需要深度推理）

## Decision

选择 `deepseek-v4-flash` 作为 prefilter 默认模型，并在 API 调用中显式设置 `thinking=disabled`，确保所有 token 预算用于生成 JSON `content` 而非 `reasoning_content`。

## Consequences

- Prefilter 的单次调用成本降低，适合 15 分钟调度频率下的高频调用
- 未来如果 DeepSeek 更新模型列表或弃用 v4-flash，需要再次调整默认模型
- Thinking 禁用的配置位于 prefilter 的 API 调用层——如果其他阶段也用 DeepSeek V4，需要根据任务复杂度独立决定是否禁用
