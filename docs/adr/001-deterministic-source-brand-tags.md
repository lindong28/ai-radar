# ADR-001: 标签生成优先使用确定性 source/brand 标签

- Status: accepted
- Date: 2026-05-12

## Context

AI Radar 的标签系统最初完全依赖 LLM 从内容中推断 topic tags。对标 AI Hot 时发现，AI Hot 的标签中包含稳定的品牌/来源标签（如 Anthropic、OpenAI），这些标签可以从 source URL、作者、标题等元数据确定性推断，不需要 LLM。纯 LLM 标签存在两个问题：一是品牌标签可能丢失（LLM 不一定每次都生成）；二是同一来源的文章可能被标注为不同的品牌变体（如 "OpenAI" vs "GPT" vs "ChatGPT"），降低标签的聚合价值。

## Options Considered

### Option A: 完全依赖 LLM 生成标签

- Pros: 实现简单，单一路径
- Cons: 品牌标签不稳定，覆盖率取决于 prompt 和模型表现；同一来源可能产生不一致的标签

### Option B: 确定性标签优先，LLM 标签补充

- Pros: 品牌/来源标签 100% 稳定可复现；LLM 仍提供语义丰富度；标签总数受控（上限 4）
- Cons: 需要维护确定性规则集（source/url/title/content → brand 映射）

## Decision

选择 Option B。`topic_tags()` 先从 source、URL、标题、内容中提取确定性 brand 标签（当前覆盖 OpenAI / Anthropic / GitHub / arXiv 等），然后用 LLM 语义标签补齐至上限 4 个。确定性标签排在前面，保证用户总能看到来源归属。

## Consequences

- 可确定来源的文章（约 21/30 精选）始终携带稳定的品牌标签
- 标签总数固定上限 4，避免标签膨胀
- 新增品牌来源时需要在确定性规则集中添加映射——规则集在 `topic_tags()` 函数中维护
- LLM 标签仍然提供主题语义覆盖（如"行业动态"、"开源"等），两者互补
