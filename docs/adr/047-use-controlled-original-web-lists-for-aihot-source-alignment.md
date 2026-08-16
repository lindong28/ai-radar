# ADR-047：为 AIHOT 来源对齐使用受控的原始 Web/API 列表

- 状态：Accepted
- 日期：2026-08-13
- 范围：AIHOT 主站来源中没有可用原始 RSS/Atom 的 18 个官方网页或列表 API

## 决策

新增 `kind="web"`，只承载经过逐来源审查的官方累计列表或 API。每个来源必须在代码登记表中明确 fetch URL、允许的最终 host、item URL 范围、解析器和最小结果数；结构漂移、零结果、越界链接或错误最终 host 使该来源本轮失败，并保留历史数据。生产摄取不调用 AIHOT、Mp2RSS、第三方 RSS 生成器或任意链接抓取作为 fallback。

机器契约、生产 parser 和独立 completeness oracle 分开维护。真实审计对同一响应 bytes 比较独立 expected set、生产结果集和 SQLite 持久集，随后重放同一响应验证去重，再做第二轮 live freshness 检查。改变 parser、registry、feed rule、transport、dedup、oracle 或 audit driver 会使既有收据失效。

`kind="web"` 是公开 About/API 可见的来源类型；它与 Feed、X 一样可进入主站精选和全部动态。可选 `kind="wechat"` 仍只服务 `/wechat`，两者不能因为都读取网页内容而合并。

## 被否方案

- 镜像 AIHOT 条目：无法证明原始来源能力，并让生产依赖对比站。
- 用 Mp2RSS 或第三方 RSS 生成器覆盖 18 个来源：扩大付费/第三方依赖，违背主站不依赖 Mp2RSS 的边界。
- 通用 CSS selector 或“抓所有站内链接”：在导航、分页、团队页和分类页上产生静默误收，无法给出逐来源完整性边界。
- 为每个站建立独立 fetch stack：重复 transport、条件请求、持久化和错误处理，维护成本高于共享 transport + 受控 parser registry。

## 结果与验证边界

当前契约是 18 个 Web/API 来源；它们与 34 个原始 Feed、109 个 X 账号组成 161 个主站来源。浏览器矩阵验证 Feed/Web/X 在精选和全部动态共现，而 WeChat 被隔离。该决策只解决来源入口和确定性解析，不声明下游清理、筛选、标签、排序、评分、摘要或策展与 AIHOT 等价。

外部页面会变化，因此零结果或结构漂移是显式 source failure，不是自动换源的理由。维护者按 README 的四个 authority/verification 命令更新 alias、contract、parser、fixture、retirement ledger 和 live receipt；不能只改 TOML。
