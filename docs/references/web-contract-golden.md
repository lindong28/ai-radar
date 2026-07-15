# Web Contract Golden 验证

> Developer reference. 用冻结数据库和 HTTP 快照证明一次 Web 重构没有改变既有输出契约。

## 何时使用

当改动目标是“结构变化、行为等价”，且触及下列任一边界时使用：

- `src/airadar/web/routes/` 的查询、分页、搜索或响应组装
- `src/airadar/presentation/` 的展示字段组合
- SSR preload、API JSON 序列化或要求字节稳定的 HTML 模板

有意改变 API 或页面行为时，应先明确新契约，再建立新的临时基线。纯 pipeline 内部改动若不影响 Web 输出，不需要运行本工具。

## 长期资产与临时资产

| 资产 | 位置 | 生命周期 |
|---|---|---|
| capture、manifest、JSON/HTML 比较和 SQLite 逻辑摘要引擎 | `scripts/web_contract_golden.py` | 长期维护、Git tracked |
| 引擎回归测试 | `tests/test_web_contract_golden.py` | 长期维护、Git tracked |
| 稳定响应字段和 SSR preload 示例 | `tests/test_web_schemas.py` | 小型、自包含、长期维护 |
| 特定任务的 URL/ID/date/slug、业务非空断言、adapter、HTTP 快照和冻结 DB | 执行 plan 或 `/tmp` 工作区 | 临时；任务完成后删除 |

临时基线只有在对应冻结数据库的逻辑摘要一致时才有判定力，不应因“以后可能有用”而永久提交。若其中某个行为确实成为持续回归契约，只把最小、自包含、可读且不依赖本地数据库的样本提升到测试中，并由现有测试入口维护。

## 接入方式

通用引擎不内置 AI Radar 路由或样本。任务应在临时 adapter 中定义 `HttpSpec` 和业务 validator，然后调用：

- `capture(base_url, output, concurrency, specs=...)`：并发抓取并规范化 API JSON、SSR preload JSON 或 HTML，同时生成 manifest。
- `record_db(db_path, output)`：记录 SQLite schema 与逐表逻辑摘要。
- `verify(db_path, golden, actual, specs=..., validate_nonempty=...)`：校验数据库、manifest 和 HTTP 产物。

`verify` 对 API/SSR JSON 做语义比较，对 HTML 做字节比较。capture 会拒绝 redirect、危险 artifact 名称和符号链接目标，发布中途失败时回滚本轮写入。数据库存在非空 `-wal` 时会拒绝摘要；冻结数据库必须使用 SQLite `.backup` 创建，不能裸 `cp`。

## 基线生命周期

1. 在任务工作区定义请求样本、`HttpSpec`、业务断言与 adapter。
2. 从目标数据库用 SQLite `.backup` 创建冻结库，记录 DB invariant，再抓取 golden。
3. 用待测代码启动独立服务，抓取 actual 并执行 `verify`。
4. 只有确认行为变化符合新契约后才 re-baseline；数据库摘要、HTTP 快照和 manifest 必须作为同一组刷新。
5. 任务完成后删除 adapter、快照和冻结库；只迁移仍需长期维护的最小契约或未解决问题。
