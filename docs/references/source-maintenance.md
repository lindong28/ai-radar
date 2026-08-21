# 信源维护与验证 [Developer]

> Mutable snapshot. 信源契约、审计脚本与退休规则的单一说明；README 只保留一行指针。

改信源不是只改 `data/sources.toml`。`data/sources.toml` 是运行时配置，而 `tests/fixtures/aihot_sources.json` 是**完整机器契约**——两者必须一起动，否则测试会拦下来，或者更糟：来源悄悄改了身份而历史数据接不上。

## 机器契约：改一个来源要同步什么

新增、改名或退休来源时，下面这些必须同步：

| 同步对象 | 说明 |
|---|---|
| `derived_aihot_identity` / 显式 aliases | 稳定身份锚。改展示名不等于改身份，历史 SQLite 行按它接续 |
| 原始入口 | feed URL / 网页入口 / X handle |
| 解析器或规则 | `kind=web` 的来源另见下一行 |
| 正反 fixture | 该来源解析成功与失败各一份 |
| 公开投影 | `/about` 与 v2 source 接口上呈现的字段 |
| 数量锚点 | 测试里断言的来源计数 |
| 文档 | README 的来源概览、本文件、必要时 ADR |

`kind=web` 的来源还必须同步 `src/airadar/fetcher/web.py` 里的登记与解析边界——每个 web 来源使用代码登记的确定性解析器和允许范围，不做任意链接抓取。没有登记的域名会被拒绝，不是静默跳过。

## 审计脚本：什么时候跑哪个

- **改动来源成员集合前后** — `uv run python scripts/audit_aihot_sources.py --output <artifacts/aihot-observation-*.json>`：遍历 AIHOT 滚动 API，输出 comparison-only 观测；成功记录追加到 `artifacts/observations/index.json`。ambiguous / unmapped / conflict 会**失败**，不会自动改契约。
- **删除任何来源前** — `uv run python scripts/check_aihot_membership_transition.py --previous <旧契约或基线> --next tests/fixtures/aihot_sources.json --retirements data/aihot_retirements.json`：拒绝没有官方迁移、30 天观察加显式复核、或用户决定作为依据的来源删除。
- **改动 Feed / Web 解析路径后** — `uv run python scripts/audit_non_x_retrieval.py --config data/sources.toml --db <全新临时数据库> --output <持久收据>`：两轮实读全部 Feed 与 Web/API 来源，比较独立 oracle、生产解析与 SQLite 持久集合。**收据里的代码哈希变化后必须重跑**，旧收据不再代表当前代码。
- **验证 X 链路连通性时** — `uv run python scripts/probe_x_source.py --source x_openai --db <全新临时数据库> --output <持久收据>`：只验证 `x_openai` 一个来源，使用全新临时库，绝不回填、也绝不全量扫所有 X 账号。missing token、401、draining 与 terminal 分别出具**不含凭据**的结果。

用 `git grep` 而不是全盘文件扫描来做来源快照核对，否则生成物、被忽略文件和本地文件会造成假阳性。跑到数据库的脚本请设 `AI_RADAR_DB` 指向临时路径，避免与本机常驻服务撞库。

## 退休与身份连续性

配置 reload 只**禁用**已移除来源、保留历史 SQLite 行；所有公开 source / timeline / search / selected / wechat 消费面都会过滤 disabled 行，所以退休的来源不会从页面上冒出来，历史内容也不会被删。

合法退休仍须经 transition checker（上面第二个脚本）。**不能靠一次安静窗口或改展示名绕过身份连续性**：一个来源几天没出稿不构成它已经死了的证据，而改展示名如果不同步身份锚，历史行会与新配置断开、同一个来源在库里变成两个。
