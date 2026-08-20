# ADR-004: N+1 优化仅限 timeline 路由

- Status: accepted；前提部分失效——热点榜已改为复用 curated archive 路径，见 ADR-060
- Date: 2026-05-24

## Context

Timeline API 存在 N+1 查询问题——每条 item 的 enrichment 数据单独查询（约 50 次），是 TTFB 14s 中的主要瓶颈之一。项目中有三个消费 `item_summary()` 的路由：timeline（分页列表，默认 50 条）、curated（固定上限 30 条）、items（单条详情）。

## Options Considered

### Option A: 三个路由统一优化
- Pros: 一致性好，所有路由都受益
- Cons: curated/items 的数据量小（<=30 / 1），N+1 的绝对耗时可忽略；修改面更大，ROI 不成比例

### Option B: 仅优化 timeline 路由
- Pros: ROI 最高——timeline 是唯一有性能问题的路由；改动范围小
- Cons: curated/items 保持 N+1 模式，未来数据量增长可能需要回头优化

## Decision

选择 Option B。`item_summary()` 增加可选的 `enrichment` 参数（预加载数据直接传入），保持向后兼容——不传则走原有的逐条查询逻辑。这样 timeline 路由可以批量预加载后传入，而其他路由不需要改动，未来有需要时也可以复用同一机制。

## Consequences

- timeline 路由的 enrichment 查询从约 50 次降为 1 次批量查询
- curated 和 items 路由保持原有行为不变，无回归风险
- `item_summary()` 的接口保持向后兼容，后续其他路由可按需接入
