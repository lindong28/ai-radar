# 信源维护与验证 [Developer]

> Mutable snapshot. 信源契约、审计脚本与退休规则的单一说明；README 只保留一行指针。

改信源不是只改 `data/sources.toml`。`data/sources.toml` 是运行时配置，而 `tests/fixtures/aihot_sources.json` 是**完整机器契约**——两者必须一起动，否则测试会拦下来，或者更糟：来源悄悄改了身份而历史数据接不上。

## v2 状态字段与生成入口

v2 契约中的每一行都必须显式包含 boolean `enabled` 与 `paused`；缺字段和非布尔值都在 contract boundary 失败。`tests/fixtures/aihot_sources.json` 是权威，`scripts/render_sources_from_contract.py --write` 生成 `data/sources.toml`，不得只手改生成物；改完再运行 `--write-union-receipt` 更新 `artifacts/source-union-receipt.json` 的 exact projection。

运行集合与兼容投影不能混用：ordinary disabled（`enabled=false`）退出 enabled-only 内容消费者、v2 inventory、历史可见性及常规去重/解读/比较；paused（`enabled=true, paused=true`）仍属 v2 inventory、历史可见、interpret、A5、跨源去重和 discovery compare，但不进入 fetch 与 A7；fetchable 精确等于 `enabled=true AND paused=false`；visible 仍按各消费者既有的 enabled 语义。保留来源 `wx_ai_assistant_kb_archive` 是唯一明确例外：它固定 disabled，却由 `wechat_archive.py` 的集中谓词纳入 `/wechat` 与跨源去重，同时排除在公开 source API、runtime source loading、fetch 与 A7 之外。

公共 source surfaces 也不是同一个集合：`/api/v2/sources` 与 About 只列 enabled inventory，About 的“已启用”只表示已收录、历史仍可见；`/api/v1/sources` 为兼容既有 collection，继续返回全部非 archive legacy 行，包括 ordinary disabled 行及其 `enabled=false`。两版 API 都不增加 `paused` 字段。

暂停 optional WeChat source 时，保留其稳定 slug、`required_env` 占位符、`public_url_override` 与 `enabled=true`，只把 `paused=true`；即使 env 缺失，loader 也会把这条显式 paused v2 row 作为 inert identity 加载和 reload。恢复必须同时确认上游可用、补齐 env、将 contract 的 `paused` 改回 false、重新生成并验证；仅设置 env 不会解除暂停。普通未暂停 optional source 缺 env 时仍按旧语义跳过。仅 versionless 或 `schema_version=1` 的兼容配置可在未声明 `paused` 时默认 false；任何 `schema_version=2` 配置（包括 ad-hoc v2）都必须显式声明 boolean `paused`。

## 机器契约：改一个来源要同步什么

新增、改名、暂停、恢复或退休来源时，下面这些必须同步：

| 同步对象 | 说明 |
|---|---|
| `derived_aihot_identity` / 显式 aliases | 稳定身份锚。改展示名不等于改身份，历史 SQLite 行按它接续 |
| 原始入口 | feed URL / 网页入口 / X handle |
| 解析器或规则 | `kind=web` 的来源另见下一行 |
| 正反 fixture | 该来源解析成功与失败各一份 |
| 公开投影 | `/about` 与 v2 source 接口上呈现的字段 |
| 数量锚点 | 测试里断言的来源计数 |
| 文档 | README 的来源概览、本文件、必要时 ADR |

暂停/恢复不改来源 identity：必须从契约中修改 `paused`、重新生成 TOML 与 exact union receipt，并同时验证 enabled inventory 和 fetchable/A7 投影分别保持预期成员集。微信暂停的 A7 identity prepare、reload 顺序与恢复门槛见 [微信公众号摄取运维](../operations/wechat-ingestion.md)。

`kind=web` 的来源还必须同步 `src/airadar/fetcher/web.py` 里的登记与解析边界——每个 web 来源使用代码登记的确定性解析器和允许范围，不做任意链接抓取。没有登记的域名会被拒绝，不是静默跳过。

## 审计脚本：什么时候跑哪个

- **改动来源成员集合前后** — `uv run python scripts/audit_aihot_sources.py --output <artifacts/aihot-observation-*.json>`：遍历 AIHOT 滚动 API，输出 comparison-only 观测；成功记录追加到 `artifacts/observations/index.json`。ambiguous / unmapped / conflict 会**失败**，不会自动改契约。
- **删除任何来源前** — `uv run python scripts/check_aihot_membership_transition.py --previous <旧契约或基线> --next tests/fixtures/aihot_sources.json --retirements data/aihot_retirements.json`：拒绝没有官方迁移、30 天观察加显式复核、或用户决定作为依据的来源删除。
- **改动 Feed / Web 解析路径后** — `uv run python scripts/audit_non_x_retrieval.py --config data/sources.toml --db <全新临时数据库> --output <持久收据>`：两轮实读全部 Feed 与 Web/API 来源，比较独立 oracle、生产解析与 SQLite 持久集合。**收据里的代码哈希变化后必须重跑**，旧收据不再代表当前代码。
- **验证 X 链路连通性时** — `uv run python scripts/probe_x_source.py --source x_openai --db <全新临时数据库> --output <持久收据>`：只验证 `x_openai` 一个来源，使用全新临时库，绝不回填、也绝不全量扫所有 X 账号。missing token、401、draining 与 terminal 分别出具**不含凭据**的结果。

用 `git grep` 而不是全盘文件扫描来做来源快照核对，否则生成物、被忽略文件和本地文件会造成假阳性。跑到数据库的脚本请设 `AI_RADAR_DB` 指向临时路径，避免与本机常驻服务撞库。

## 退休与身份连续性

配置 reload 只**禁用**已移除来源、保留历史 SQLite 行。timeline、search、selected、v2 source inventory 与普通 `/wechat` 内容按 enabled 过滤 ordinary disabled 行，所以退休来源不再从内容页面出现；`wx_ai_assistant_kb_archive` 只按上面的集中例外继续服务 `/wechat` 与跨源去重。v1 compatibility source collection 刻意保留非 archive 的 ordinary disabled 行并回显 `enabled=false`，不得拿它断言退休来源仍在内容消费面；任何一种禁用都不会删除历史 SQLite 内容。

合法退休仍须经 transition checker（上面第二个脚本）。**不能靠一次安静窗口或改展示名绕过身份连续性**：一个来源几天没出稿不构成它已经死了的证据，而改展示名如果不同步身份锚，历史行会与新配置断开、同一个来源在库里变成两个。
