# 微信发现层候选路线的历史证据台账

> 读者 [Developer]。性质：历史证据台账，**该路线已因平台级不可用停止推进**（见 [ADR-061](../adr/061-deprecate-wechat-admin-discovery-line.md)；ADR 索引在 [../adr/README.md](../adr/README.md)）。本文只保存当时取得的读数与边界声明，不描述当前生产链路——当前微信摄取见 [../operations/wechat-ingestion.md](../operations/wechat-ingestion.md)。内容自 `operations/wechat-ingestion.md` 原样迁入，仅把开头对各条 ADR 决策内容的复述收敛为指针（各 ADR 正文自身是权威）。

## 公众号后台发现候选（默认关闭）

这条路线的设计取舍分散在 ADR-024、ADR-025、ADR-028、ADR-029、ADR-030、ADR-031、ADR-040、ADR-041、ADR-043、ADR-044 与 ADR-045；每条 ADR 的决策内容以其正文为准，索引见 [../adr/README.md](../adr/README.md)。其中 ADR-025 的「每次 5 篇、两次后台请求至少间隔 1440 分钟」是本项目的本地临时默认，不是微信官方公布的配额或冷却窗口——这一点在下文多处读数中被引用，故在此保留。该候选当时只是 Mp2RSS 的条件式替代候选：`data/wechat-discovery.toml` 记录当前配置公众号的名称与非敏感 `public_biz`，`manual_backend_requests_enabled=false`；`data/wechat-discovery-session.json`、原子写入临时文件、`data/wechat-discovery-browser/` 和独立的 `data/wechat-discovery.db*` 全部 gitignore。候选不会被 `fetch_all` 或 pipeline 调用，单账号 probe 的候选 URL 只写独立 shadow DB，不写生产 `items`，因此不会与 `wx_mp2rss` 产生跨 source 重复。

### 当前已实现与未验证边界

已实现：`searchbiz` 候选解析与唯一规范化名称选择、`publish_page.publish_list[].publish_info.appmsgex[]` fail-closed 解析、文章 URL `__biz` 与配置身份校验、认证失效、可证明频控、其他平台拒绝、网络请求失败和响应形状变化的独立错误分类，以及只面向单账号人工请求的 versioned shadow store。schema v9 把 resolution 与 probe 分成独立 ledger并保存 exact ret，schema v10 再拒绝非整数 ret、让特殊次日冷却只消费 `recorded` 频控证据：searchbiz 只建立可供一次 probe 使用的 provisional mapping，probe reservation 通过唯一 resolution 引用一次性消费它；只有至少一篇文章且全部返回 URL 的唯一 `__biz` 匹配配置账号，probe 才持久化 `article_url_public_biz_verified` 与不可变 candidate snapshot。旧失败显式标为 `predates_persistence`，不从宽分类反推错误码或冷却。空列表、URL identity unavailable、mismatch 与请求失败各自保留不同证据，v6 历史成功不升级为新证明。两类请求都在网络前提交 `finished_at=NULL` 的 reservation，崩溃后保留 `REQUEST_OUTCOME_UNKNOWN` 和本地冷却，不伪造完成时间。显式只读 `compare` 命令按账号、成功 attempt 和观察窗对比生产 `wx_mp2rss` URL；比较结果不另行持久化。

2026-08-13 实测：操作者用获授权后台账号在可见浏览器中完成登录与二次登录；旧程序把两次后台非成功响应分别记录成 `AUTH_REQUIRED` 与 `RATE_LIMITED`。当时 ledger 没有保存 exact `ret`，而旧 parser 又会把 `200002` 与 `200013` 合并为后一个状态，因此第二条记录不能证明真实频控，更不能证明官方存在 24 小时窗口。两条 attempt 均写入独立 shadow DB，候选数为 0，生产 `items` 与 Mp2RSS 未变化。二次登录后从同一受控后台首页重新生成项目格式 session，运行时加载验证通过，保存 12 个适用域 Cookie 且权限为 `0600`。这些读数发生在正确 fakeid 解析与 exact-ret 持久化实现之前，迁移后统一标为 `predates_resolution` / `predates_persistence`，不能用于 coverage 或平台配额归因。

2026-08-14 实测：一次获授权的 live `searchbiz` resolution 在约 1.2 秒后终止为 `RESPONSE_INVALID`；原始私有响应按当时契约未持久化，因此不能从 ledger 判断具体缺少哪个字段，也不能把它解释为平台不再支持该端点。

同日按 provisional-only 解析契约修正后，后一条获授权 live `searchbiz` 在约 1.3 秒后得到 `PROVISIONAL_MATCH`：目标账号“歸藏的AI工具箱”恰好有一个规范化名称匹配，私有 mapping 已持久化但未在 CLI 或审计查询中显示。这个正例只接地一条账号解析，不证明 public biz、映射可跨 probe 复用、其他账号可解析或端点长期稳定。

2026-08-16 实测：在 ADR-043 的一次性授权范围内，先取得新的 `PROVISIONAL_MATCH`，随后立即执行一次 `probe --count 5`。后台在约 0.25 秒内返回非成功结果，旧 parser 将其写为 `RATE_LIMITED`，mapping 被正常一次性消费，0 个 candidate 入库且没有自动重试；但 exact `ret` 当时尚未持久化，因此这个 attempt 只能证明“请求被平台业务层拒绝”，不能证明频控。随后代码和私有库先升到 schema v9 保存 exact ret，再升到 schema v10 拒绝非整数 ret，并修正历史宽分类触发特殊冷却的问题；未来整数 `200013` 或明确 frequency 文本才进入 `RATE_LIMITED`，整数 `200002` 与其他非认证、非频控码进入 `PLATFORM_REJECTED`。v9→v10 前的权限 `0600` SQLite backup SHA-256 为 `f48300bf0a93e30db08129c80df703e07a6ca271e12800a5703dd7bfd0cfad4d`；迁移后的私有库同为 `0600`，`user_version=10`、`quick_check=ok`、无外键违规，仍为 3 条 resolution、4 条 probe、0 条 candidate，且四个整数类型 trigger 均存在。4 条历史 probe 的错误码来源仍为 `predates_persistence`；disabled `status` 已直接显示 exact ret 未被旧 schema 记录，不再据此生成次日解禁时间。默认人工请求开关保持关闭，生产链路不变。

现行开源实现 [wechat-download-api](https://github.com/tmwgsicp/wechat-download-api) 提供了同源的独立实现证据：它用 `searchbiz` 保存 fakeid，再由后台 poller 直接以 `begin=0`、可配置 `count` 请求 `appmsgpublish`，并不在每轮重新解析 fakeid。这说明“解析一次、后续复用”的状态机值得在本项目 live 成功后验证，但不是平台稳定性证明，也不能直接复用它的失败语义。该 poller 对除特定 invalid-fakeid 外的微信业务错误返回空数组，调用方仍会更新 `last_poll`；`publish_page` 解析失败同样返回空数组，因此可能把请求失败误记成已轮询并静默漏文。其源码所称“只取元数据，零风控风险”没有官方契约或本项目长期正例支撑；本项目的旧宽分类失败同样不足以证明具体频控规则。可借鉴的是 fakeid 复用、逐账号间隔与本地缓存形状；必须保留的是本项目现有 reservation、错误分类、失败不推进游标与同窗 coverage gate。

### 微信读书只读 canary（第二候选，默认关闭）

[ADR-034](../adr/034-use-a-single-auditable-weread-canary-evidence-ledger.md)、[ADR-035](../adr/035-bind-weread-canary-evidence-to-targets-producer-and-relations.md)、[ADR-036](../adr/036-preserve-public-page-observation-outcomes.md)、[ADR-037](../adr/037-retain-observed-captcha-target-at-attempt-end.md) 与 [ADR-038](../adr/038-observe-weread-dynamic-header-presence-without-replay.md) 定义了一个不依赖公众号后台 `fakeid` 的单账号本地 canary。它复用一个专用 headed Chrome 的微信读书登录态，只允许一次 `GET https://weread.qq.com/web/shelf/sync`、至多一次 `GET https://weread.qq.com/web/mp/articles?bookId=...&offset=0` 和最多五次候选微信短链公开页面身份观察；实现没有添加书架、写生产 candidate store 或改变 pipeline 的方法。所有新 evidence 经过同一个 v7 validator，记录请求计划、真实 attempt、dispatch 证据、HTTP / WeRead API / response-shape 结果、逐候选 URL、账号身份观察、public target 在 attempt 结束时的可证明状态、producer 源码摘要和明确的 `replacementAssessment=not_validated`。v7 还在现有 article-list 请求周围被动监听 CDP Network 事件，只记录监听是否成功、精确目标请求与 ExtraInfo 是否被观察到，以及 `x-wrpa-0` / `x-wr-ticket` 两个头名的存在布尔值；多条精确匹配请求或无法归属到唯一 redirect leg 的 ExtraInfo 会明确标为 `NOT_MEASURED`，不会合并或猜测头名存在性。实现不新增或回放请求，不把任何头值写入 artifact、终端或日志。公共文章页的已观察验证码、身份字段不完整和超时不会再被误报为未知 dispatch；遇到验证码时只保留该 target 供操作者检查，不承诺它后续仍可见或可操作。

专用 Chrome 当前使用独立 profile 与本机 CDP 端口 9333；运行前必须由操作者确认这是预期账号的可见窗口。以下命令只生成一个不可覆盖、权限 `0600` 的本地 evidence 文件，输出文件名不承载 schema 或时间事实；运行它不等于授权登录、验证码处理或书架变更：

```bash
node scripts/wechat_weread_canary/cli.mjs \
  --account-name '歸藏的AI工具箱' \
  --public-biz 'MzU0MDk3NTUxMA==' \
  --book-id 'MP_WXS_3540975510' \
  --identity-limit 2 \
  --port 9333 \
  --out data/recovery/wechat-discovery/weread-canary-next-session-control.json
```

该命令只会创建新的 evidence 路径，不覆盖既有文件。已有目标路径、参数或其他请求前检查失败时，stderr 明确显示 `NOT_STARTED` 与 `Request dispatch: NOT_ATTEMPTED`，既有文件保持不变；进入请求边界后却未能形成可信 ledger 的执行失败才显示 dispatch `UNKNOWN`。带 evidence 的正常摘要写 stdout，参数、preflight 与执行失败写 stderr。`--help`、全部候选身份已验证或至少部分候选身份已验证时返回 `0`；其他 canary 结果与所有失败返回 `2`，因此仅凭退出码 `0` 仍不代表可以替换 Mp2RSS，最终结论始终以摘要中的 `Replacement readiness` 和同窗比较为准。

2026-08-13 的真实 v4 未登录控制样本观察到 shelf attempt 的 HTTP 200 和 WeRead API `-2010`；它明确将书架内容、候选与返回页覆盖标为 `NOT_MEASURED`，将 Mp2RSS 替换条件标为 `NOT_VALIDATED`，没有把 API failure 当成成功空书架。该错误码的因果含义尚未独立核实，不能据此声称是认证、权限或限流。2026-08-14 在操作者确认可见窗口并完成获授权微信读书登录后，真实 v7 canary 对同一目标得到一次 `weread_shelf` 的 `response_observed / HTTP 200 / success`，随后以 `shelfEntry.state=absent` 和 `overallState=blocked_no_shelf_entry` 停止；它没有发 article-list 或 public-page 请求，动态头观察为 `not_attempted`，替换结论仍为 `not_validated`。证据保存在权限 `0600` 的 `data/recovery/wechat-discovery/weread-canary-next-session-control.json`，SHA-256 为 `93f8067f5574dee19b4aae24ed613ac3b8ddcd38b197c5ed20418347a95eb563`；同一次校验确认 artifact 的 producer digest 与当前五个 canary 源文件一致，JSON 往返后仍被唯一消费 validator 接受。临时 Chrome 登录 profile 已停止并移入系统废纸篓，生产数据与书架均未改变。

这份真实登录态证据证明书架请求边界与“目标缺席”分支可区分，但不是文章发现正例。微信读书路线仍只是 feasibility candidate：目标不在当前账号书架，因而尚未验证 article-list endpoint、真实动态头存在性、文章列表分页、`createTime` 语义、短链持续性、账号覆盖、登录寿命或稳定调用预算，更不能据此切换生产。把一个测试公众号加入书架属于外部账号状态变更，只有取得独立明确许可后才能执行；在此之前不得把 `blocked_no_shelf_entry` 绕过成匿名 article-list 探测。

同日的追加只读探测进一步收窄了微信读书路线。匿名同一 session 下，`weread.qq.com` 首页返回 HTTP 200，`/web/shelf/sync` 与 `/web/mp/articles?bookId=MP_WXS_3540975510&offset=0` 都返回 HTTP 200 + `errCode=-2010` / `errMsg=用户不存在`；`/api/mp/cover?bookId=...` 返回 HTTP 401。活跃的 [We-MP-RSS](https://github.com/rachelos/we-mp-rss) 当前源码把 `/web/mp/articles` 标成新版已废弃，并将生产路径切到 `/api/mp/cover`；该路径只返回最新一篇、无法回补漏过的文章，而且实现会在公众号不在书架时调用 `/web/shelf/add`。但另一个截至 2026-08-12 仍在维护的 [weread.koplugin](https://github.com/finlater/weread.koplugin) 仍请求 `/web/mp/articles?bookId=...&maxIdx=...&count=100`，同时发送 `x-wr-ticket` 与 `x-wrpa-0`，其续期逻辑还会从响应头更新这两个值；该仓的验证脚本却只把 ticket 当作可选输入，因此它也没有独立证明 WPA 与 ticket 各自的必要性。这些相互冲突的上游实现声明与匿名边界读数不足以证明列表接口已经退役或带头后必然成功。后续真实登录态 v7 只到达书架成功且目标缺席，article-list request 没有发生，所以同样没有裁决这项分歧；任何未观察状态都不能被解释为“头缺失”或“接口退役”。若最终只能使用 `cover`，它只能作为最新文章提示器，不能在没有额外来源的情况下满足完整发现。

公开第三路径也已做最小对照。已知文章 HTML 匿名返回 HTTP 200，并包含 `biz`、`user_name` 与只能由微信 WebView 打开的 `mp/profile_ext?action=home&__biz=...` 入口；直接匿名打开该历史入口只得到 `Verify` 页。搜狗微信搜索对“歸藏的AI工具箱”返回两条可归属结果但最新停在 2026-03-05，对 InfoQ 和虎嗅 APP 的精确作者结果分别停在 2022 和 2019；“量子位”虽出现当日结果且解析后的文章 `biz` 与配置匹配，标题实际对应已被 Mp2RSS 多次收录的旧招聘文章，说明搜索时间不能直接作为文章发布时间。RSSHub 的[搜狗路由](https://github.com/DIYgod/RSSHub/blob/master/lib/routes/wechat/sogou.ts)也明确标记 `antiCrawler=true` 并依赖中转链接。因此搜狗可用于找 seed 或做独立人工抽样，不能作为 14 个账号的统一连续发现层；新榜路由另需第三方 Cookie 且同样标记反爬，不优于现有获授权后台路径。

生产基准的只读容量分析给出了分页下限。`wx_mp2rss` 当前 2929 条记录覆盖全部 14 个配置作者；单账号滚动 24 小时峰值为虎嗅 APP 18 篇、量子位 17 篇、AI 科技评论和 InfoQ 各 12 篇，其余账号为 1–7 篇。因而后台默认 5 篇与微信读书单页都不能覆盖真实峰值，正式路线必须有可证明追到上次成功游标的有界分页，或以独立来源补齐缺口。现有 config 的全局 1440 分钟冷却与一次性 resolution mapping 还意味着，按当前状态机对 14 个账号逐个执行 `resolve + probe` 需要 28 个全局请求槽；这是本项目安全 canary 的临时形状，不是平台配额结论，也不是可部署 cadence。上线前必须实测 fakeid 是否可跨 probe 复用并据真实限流预算重设状态机。

未验证：provisional mapping 是否能成功驱动 `appmsgpublish` 并返回带真实 `__biz` 的文章；同一 mapping 能否安全跨 probe 复用；其余 13 个配置账号的 `searchbiz` 响应；session 寿命和一个授权账号读取 14 个目标的调用限额；5 篇单页容量、分页、正式 cadence 和多日覆盖率是否满足最终要求；专属告警、scheduled canary 和正式切换均未实现。已有 live 读数只证明一个账号可形成 provisional resolution，以及多个非成功请求终态；由于历史 exact `ret` 缺失，连“认证、频控与其他平台拒绝均已真实区分”也尚不能声称。替代方案仍未验证可用。

### 安全试跑

先检查默认状态；此命令不读取私有 session：

```bash
./run.sh admin wechat-discovery status
```

`status` 和 `compare` 严格只读，不会在打开旧 shadow DB 时自动迁移。若任一命令返回 `UNAVAILABLE` / `NOT_COMPARABLE` 并要求显式迁移，先保存该私有库的精确备份，再运行下列命令；它只修改 private shadow state，不发送公众号后台请求，也不修改 Mp2RSS、生产 `items` 或 scheduler：

```bash
./run.sh admin wechat-discovery migrate
./run.sh admin wechat-discovery status
```

只有操作者确认账号与合规边界后才运行登录。命令打开可见 Chromium，等待扫码后只保存 `mp.weixin.qq.com` 可用 cookie 与 URL token；浏览器子进程只继承显示、临时目录、语言和基础路径等允许项，不继承项目 dotenv 中的 LLM key、webhook 或 Mp2RSS URL。session 原子写入并强制 `0600`，登录完成后仍不会启用 canary：

```bash
./run.sh admin wechat-discovery login
```

第一次只选一个账号做人工 feasibility 请求，禁止循环重试。请求前必须使用 config v3，将 `manual_backend_requests_enabled=true`，并提供经复核的公开 seed 身份记录；记录字段为 seed URL、当时观测到的公众号名、`public_biz`、`observed_public_biz` 和日期。先运行 `resolve`；后台搜索结果中恰好一个候选匹配规范化名称时，返回的 `fakeid` 只作为 provisional mapping 进入私有 shadow DB，CLI 不显示它，也不声称 public biz 已被 searchbiz 验证。成功 resolve 会进入本项目配置的 1440 分钟全局人工请求冷却；它是 feasibility 安全阀，不是微信平台声明。冷却结束后，一条 mapping 只能分配给一次 probe reservation。未传 `--count` 时 probe 只请求 5 篇：

```bash
./run.sh admin wechat-discovery status
./run.sh admin wechat-discovery resolve --account '歸藏的AI工具箱'
# status 到达 READY_TO_PROBE 后，且仅在下一允许时间运行：
./run.sh admin wechat-discovery probe --account '歸藏的AI工具箱'
./run.sh admin wechat-discovery status
```

`status` 分别显示 request gate、provisional mapping 状态与 replacement readiness；`REQUEST_OUTCOME_UNKNOWN` 表示 reservation 已提交但没有可信终态，该请求仍占用冷却，probe mapping 也保持分配给该 reservation。成功 probe 会打印 attempt id，并只有在全部返回文章 URL 的 public biz 匹配时显示 `Target identity: VERIFIED`；空列表或 URL 无法提供身份时显示 `NOT_VERIFIED` 且禁止 compare，mismatch 会拒绝整批候选并使 mapping 安全失效。是否真正覆盖 Mp2RSS 仍由带账号、窗口和 baseline 的 `compare` 判定。按 [ADR-026](../adr/026-explicit-windowed-mp2rss-shadow-comparison.md) 使用同账号、该 attempt 和明确起点执行只读比较；shadow schema 的页大小字段与迁移约束见 [ADR-027](../adr/027-self-describing-recoverable-wechat-shadow-page-size.md)，v4→v5 单一权威迁移见 [ADR-029](../adr/029-single-source-wechat-discovery-ledgers.md)，v5→v6 去重、UTC 归一与 config v3 见 [ADR-030](../adr/030-remove-derived-wechat-discovery-fields.md)，历史关系的降级与拒绝边界见 [ADR-031](../adr/031-preserve-only-provable-wechat-migration-facts.md)，单次响应重复 URL 的 fail-closed 规则见 [ADR-032](../adr/032-reject-duplicate-urls-before-wechat-shadow-comparison.md)，provisional 与 article URL 验证契约见 [ADR-040](../adr/040-verify-provisional-searchbiz-mapping-with-article-url-biz.md)，schema v8 不变量加固见 [ADR-041](../adr/041-version-wechat-discovery-invariant-hardening.md)，exact ret 与平台拒绝分类见 [ADR-044](../adr/044-persist-wechat-platform-error-ret.md)：

```bash
./run.sh admin wechat-discovery compare --account '歸藏的AI工具箱' --attempt ATTEMPT_ID --since '2026-08-13T00:00:00+08:00'
```

`COVERED_IN_WINDOW` 只说明这个账号、attempt 和窗口内的 Mp2RSS 基准 URL 均被候选覆盖；`MISSING_IN_WINDOW` 会逐条列出差异；失败 attempt、未验证或后来失效的 identity resolution、页大小来源为历史未知、生产账号分桶不匹配、空基准、不可证明等价的 URL 形态或单页未触达窗口起点都会返回 `NOT_COMPARABLE`。单次后台响应若含重复 URL，会直接记录为 `RESPONSE_INVALID` 且不形成候选快照，避免去重后把满页误作短页。返回码固定为 `0=COVERED_IN_WINDOW`、`1=MISSING_IN_WINDOW`、`2=NOT_COMPARABLE`。三种结果都不写生产数据，也不能证明双方没有共同漏文。

`status` 返回 `0` 表示请求 gate 已关闭或当前存在可执行的下一步，`1` 表示未配置、冷却或请求终态未知，`2` 表示配置、旧 schema 或私有状态不可读；`compare` 遇到旧 schema 也返回 `2=NOT_COMPARABLE`，两者都保持数据库版本不变。`migrate` 返回 `0` 表示 schema 已升级或本来就是当前版本，`2` 表示没有形成可信迁移结果。`resolve`/`probe` 返回 `0` 表示对应动作成功完成，`1` 表示安全前置条件或后台业务终态未满足，`2` 表示私有状态不可用或 reservation 后无法可信落终态。所有人类可读结果写 stdout；命令不输出 cookie、token 或 `fakeid`。

上述 live provisional resolution 只接地 ledger 与状态机；由于默认配置关闭，enabled `status` 的 provisional 消费界面尚未由真实 v10 实例接地。成功 probe 仍只经过隔离临时库和合成客户端回归，recorded exact-ret 分支也尚无 live v9/v10 实例。compare 的 source disabled、author bucket、空基准、URL identity、历史 identity/page-size 未知和满页未触窗分支已有 CLI 级回归。旧式 `/s?__biz=...&mid=...&idx=...&sn=...` 四元组目前是保守实现规则，尚无 redacted live 样本证明后台文章列表会返回这种形态。首次 live probe 后必须再次观察 resolution、已分配 mapping、新 probe 与旧证据的同屏关系；完成该消费者界面核验和同窗 compare 前，文章发现与稳定替代能力均保持 `UNVERIFIED`。

平台文章时间若晚于本机 attempt 完成时间，store 会保留这份原始发现证据，但 compare 必须返回 `NOT_COMPARABLE`；`success` 不等于已经可以形成 coverage 结论。当前没有 live 数据可据以定义安全时钟偏差容差。

`NO_MATCH` / `AMBIGUOUS_MATCH` 先检查公开 seed 与配置身份，不得人工猜 `fakeid`；`IDENTITY_MISMATCH` 会拒绝候选并使 mapping 失效；`AUTH_REQUIRED` 只在仍有获授权后台账号时重新登录；只有保存了 exact `ret` 的新 `RATE_LIMITED` 才能作为频控证据，并按 CLI 的本地策略停止请求，旧 `RATE_LIMITED` 必须同时读作“exact ret 未记录”。`PLATFORM_REJECTED` 应先检查请求目标、获授权账号条件和未公开接口行为，不生成特殊冷却，也不能原样循环重试；`REQUEST_FAILED` 先核对网络/HTTP；`RESPONSE_INVALID` 只允许检查脱敏后的字段形状，不得把原始响应、cookie、token 或 `fakeid` 写进日志、issue 或 fixture。

### scheduled canary 与切换门槛

当前没有 scheduled canary 命令或服务，`manual_backend_requests_enabled=true` 只授权人工 resolve/probe 请求，不会启用调度。进入定时观察前必须先取得成功 live probe，证明可用接口、游标与分页语义，再通过显式 schema migration 增加 multi-account canary scope 与聚合/失败账号投影；随后实测 14 个目标的配额、fakeid 复用边界与发文峰值，实现适配器专属告警、真实 scheduler owner、重启后仍成立的持久当日熔断，并完成认证过期、限流、网络失败、解析失败、单账号失败和恢复通知的故障注入。最终 cadence 必须由覆盖率、时效与限流预算的真实读数决定，不能把当前 24 小时临时默认当成平台 SLA。A1 是 LLM provider 告警，不适用；A4 是聚合摄取失败率，可能被其他来源稀释，不能替代专属告警。

切换只能声称：在明确观察窗、明确账号集合和给定延迟上限内，候选覆盖全部 Mp2RSS 基准 URL，并通过不依赖 Mp2RSS 的人工抽样。它不能证明双方不会共同漏文，也不能证明未来永不漏文。完成逐条差异归因、最新一页突发容量验证、回切演练和真实窗口可见性验证前，保留 Mp2RSS 且不取消订阅。
