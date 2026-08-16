# Changelog

## 2026-08-16

- 微信公众号后台 shadow ledger 先以 schema v9 在 resolution 与 probe 保存 exact `base_resp.ret`，再以 schema v10 拒绝布尔、字符串、小数和 SQLite 非整数错误码，并把特殊次日冷却收窄为仅由已记录的频控证据触发。只有整数 `200013` 或明确 frequency 文本进入 `RATE_LIMITED`；整数 `200002` 和其他非认证、非频控拒绝进入 `PLATFORM_REJECTED`。v8 之前的旧 `AUTH_REQUIRED`、`RATE_LIMITED` 与 `RESPONSE_INVALID` 只标记为“错误码未记录”，不会追溯猜测、改判或生成虚假解禁时间。一次获授权 one-shot probe 仍未得到文章候选；它发生在 exact-ret 修复前，因此只能证明后台返回了旧 parser 归入宽分类的失败，不能证明微信官方存在 24 小时窗口。生产 Mp2RSS、`items`、scheduler 与默认关闭配置均未改变；本地私有库在 0600 SQLite backup 后迁移，3 条 resolution、4 条 probe、0 条 candidate 完整保留。
- 主时间线和精选已配置 AIHOT 审核滚动观察并集中的 109 个 X 账号、34 个原始 Feed、18 个原始 Web/API 列表，共 161 个主站来源。另保留可选 `wx_mp2rss`，但它只服务「微信文章解读」，不进入精选、全部动态、搜索或策展。Web/API 来源使用逐来源确定性解析器；被配置移除的旧来源及历史关系继续保存在 SQLite，但不再从公开 v2 消费面出现。验收已覆盖 2,020 条 AIHOT 完整滚动观察且来源 reconciliation 零缺口，以及全部 52 个 non-X 来源的两轮 live 读取和一次 immutable replay；这只证明来源成员集合和原始读取链路，不表示下游清理、筛选、标签、排序、评分、摘要或策展结果与 AIHOT 等价。
- X 读取不依赖付费 Mp2RSS，需要 `X_BEARER_TOKEN`；身份解析与 timeline 分轮，每个账号每轮至多一个请求，timeline 最多 5 条，只读原创帖子。首次窗口为最近 20 分钟，后续由持久 checkpoint/cursor 增量推进，不做接入前历史回填。仓库提供只允许 `x_openai` 的低成本探针；受限真实探针的一次身份请求和一次 timeline 请求均返回 HTTP 200，并提交了合法的空窗口 checkpoint。该结果没有实际帖子读取证据，也不代表 109 个账号均已逐一 live 验证。
- 缩短 `https://news.aiplanet.live/wechat` 首次打开时的空白等待：`news.aiplanet.live` 现通过 DNS-only CNAME 接入 EdgeOne 全球加速，公开列表与静态资源按明确边界使用边缘缓存，搜索、详情、管理页和健康检查继续保持不缓存；`/wechat` 列表页把共享样式表内联到 SSR HTML，消除冷首屏等待第二次 CSS 请求的串行阻塞，同时保留单一 CSS 源文件和原有页面视觉。

## 2026-08-14

- 微信公众号后台 shadow discovery 将 `searchbiz` 名称匹配收窄为一次性 provisional mapping；只有后续 probe 返回的全部文章 URL 都以唯一 `__biz` 匹配配置账号时，才形成可比较的身份验证证据。空列表、URL 无法提供 `__biz`、身份不匹配与请求失败分别持久化，CLI 不显示私有 `fakeid`，并将文章 URL public biz 矛盾明确报告为 `IDENTITY_MISMATCH`、安全失效 provisional mapping，不再误归入一般未验证状态；v6 成功历史不被升级成新证明。schema v8 在已落地 v7 之上加固 active mapping 与不可变 candidate snapshot，并让 disabled status 和 compare 使用同一 URL 身份判定；只读 `status` / `compare` 不再隐式迁移旧 shadow DB，升级必须经显式 `wechat-discovery migrate`。一次获授权的 live `searchbiz` 先以 `RESPONSE_INVALID` 结束，按新契约修正后的一次请求成功形成“歸藏的AI工具箱”的 provisional mapping；public biz 与文章发现仍待冷却后的 probe 验证。生产 `wx_mp2rss`、定时 pipeline 和 `items` 均未改变。边界见 ADR-040 与 ADR-041。
- 微信读书只读 canary 在获授权、可见的登录态 Chrome 中生成首份真实 schema v7 evidence：`/web/shelf/sync` 得到 HTTP 200 成功响应，但目标公众号不在书架，因而结果为 `blocked_no_shelf_entry`，article-list 请求与动态头观察均未执行，替换结论仍为 `not_validated`。本次运行没有修改书架、写生产 candidate 或改变 pipeline；目标书架变更仍需独立明确授权。边界见 ADR-038 与微信摄取运维文档。

## 2026-08-13

- 新增默认关闭的微信公众号后台发现候选，用于在取消 Mp2RSS 前做低频 shadow 验证。首版包含 14 个现有公众号的非敏感 public biz 与逐号公开身份记录、headed Playwright 扫码入口、独立 shadow SQLite、状态 CLI，以及按账号、成功 attempt 和观察窗执行的只读 Mp2RSS 对比命令。后续源码审计确认公开 `biz` 不能作为通用安全的后台 `fakeid` 映射；resolution/probe ledger 因此增加单账号 `searchbiz` 身份解析、一次性 mapping 分配、请求前 `finished_at=NULL` 的 crash-safe reservation、文章 URL `__biz` 双层校验、失效与 supersession 历史，并用 probe 引用与不可变 attempt snapshot 取代双向消费和全局 candidate 双写。schema v6 进一步移除 verified probe/candidate 的重复身份、固定 kind/change-basis 与可派生成功子类型，所有时间统一 UTC；config v3 使用 `public_biz` / `observed_public_biz` 自描述字段。v3 及更早 attempt 标为 `predates_resolution`，不得形成覆盖结论；真实私有库已在 pre-v5、pre-v6 精确备份与副本演练后迁至 v6，两条旧失败证据完整保留。获授权后台登录与二次登录已在可见浏览器中完成；两次真实请求分别得到 `AUTH_REQUIRED` 与 `RATE_LIMITED`，没有文章候选，生产数据未变。人工请求临时默认成功后间隔 24 小时，probe 每次 5 篇；单次响应内重复 URL 会显式失败，不会去重后形成假窗口证据。尚无成功 `searchbiz`、文章列表、14 账号后台兼容、session 寿命或平台配额证据。所有请求仍不进入 pipeline，也不改变 Mp2RSS；当时边界由 ADR-024 至 ADR-032 约束，后续身份语义与 schema 演进见 2026-08-14 条目。
- 新增默认关闭的单账号微信读书只读 canary 作为第二条可行性路线。schema v7 在既有、至多一次的 article-list 请求周围被动观察 CDP Network 事件，只记录监听、精确请求匹配、ExtraInfo 与 `x-wrpa-0` / `x-wr-ticket` 两个头名的存在性；不保存、显示或回放头值，也不新增请求、修改书架或写入生产数据。证据继续保留 HTTP / WeRead API / response-shape、候选身份、public target 生命周期和 `NOT_VALIDATED` 替换结论；v1–v6 artifact 保持冻结可读。当前只有真实 v4 未登录失败样本，没有真实 v7 或登录态正例，不能据此取消 Mp2RSS；边界由 ADR-033 至 ADR-038 约束。
- 微信读书 canary 的请求前目标路径冲突现明确显示 `NOT_STARTED` 与 `Request dispatch: NOT_ATTEMPTED`，并保证既有 evidence 不变；帮助与运维文档同步公开 stdout、stderr 和退出码契约，避免把未发请求误报成 dispatch unknown。

## 2026-08-10

- 新增 A5 微信解读停滞与 A6 近 24 小时 LLM 量结构成本突变告警，并把 unpriced、stale、due-review、price-changed 作为独立 D3 notification 去重通知。A6 用同一现行 tariff snapshot、cache 全未命中基准重算当前窗与 14 个 UTC 基线日，不把纯调价或 cache 采集比例变化误报为量结构突变；超过 3×基线先 notice，超过 6×高档阈值才 page，已观测到的缺数会显式降级。当前 pipeline 运行造成的暂时测量缺口把已记录金额作为下界继续允许首次 firing 与升级；下界未越线时保留同一 firing episode，等待封口后只确认记录行金额是否回落，不宣称整体计量健康。D3 发送/clear 失败可重试，保留间歇模型价格签名，并把成功生命周期写入共享 ledger；A1/A2/A5 合并由 pipeline 心跳门控，INTERNAL suppression 同样可审计。A3 的主动 healthz 探针现从已安装 serve plist 解析端口，不再因本机 serve 使用 8010 而对 8000 持续假告警。`/admin/usage` 新增阶段、Provider/模型、日序列与 cache 中性等长前窗聚合，`/api/v1/admin/usage` 的 `measurement_scope` 明示调用数、token 合计和同口径金额合计是记录行下界，而均值、占比与环比只描述记录行 cohort、相对全部付费调用真值的偏差方向未知。新增 `admin cost-report` 和周一 09:17 的 `cost-report` cron lifecycle；报表用 durable items 与逐 stage 成功产出识别整日及 partial stall，错误行不冒充成功，顶部单列异常，并同时展示 nominal 估算金额/占比、记录行口径与只除以可定价记录行的单篇解读前窗参考；所有金额都不是账单实付，unpriced 不计入金额。
- LLM 成本改为查询时按 LiteLLM 社区定价、项目内 ARK 挂牌价补充表与 7 天缓存派生；三个 deprecated `cost_usd` carrier 的历史值迁移为 `NULL`，新 writer 只写 `NULL`，滚动发布期间允许但完全忽略旧 writer 遗留 numeric。严格计量写入仍会抛出 SQLite 拒绝；模型结果已经成功返回时，计量失败改走独立、可计数的错误日志并保留已付费结果，不再误触发 provider fallback 或 interpret 重试。旧 `AI_RADAR_LLM_PRICING_JSON` 配置已退役且设置时显式报错，由受管定价 catalog 和 `AI_RADAR_USD_CNY` 汇率配置取代。`/admin/usage` 收窄为窗口总额三态拆分、来源单价、unpriced 清单与 cache 覆盖率；ARK 来源币种与 USD 投影分开展示，supplement 按 usage 时间选有效区间。`item_evaluations.cost_usd` 不再伪造 `$0`，管理 metrics 与告警信号也不再发布该假值。`./run.sh admin cost-audit` 从 raw catalog 独立读取 matched key 与费率，对费率提取错误也会失败，并显式打印两种 CNY 口径的差额与三个 deprecated carrier 的退出计数；human、KV 与 JSON 都携带 `measurement_scope`，`CONSISTENT` / `PASS` 明确只表示 tariff arithmetic 一致，不表示计量完整或 tariff 权威；机器视图通过 `--format=kv|json` 显式选择。
- 数据同步现只传 Mac primary 的非 FTS base artifact，并在腾讯服务器 inactive candidate 上重建、逐字段验证 FTS 后切流；同步不再要求事先 `admin db slim` / VACUUM。真实 steady round 的 DB 传输从旧链路约 1.9GB 降到 16.39M（连同 822.90K manifest 约 17.21M，低于 20MB gate），两轮切换共 3500/3500 个公网 health 样本全为 200，title/content/source/author/title_zh 五字段搜索 IDs/count 均与 Mac snapshot oracle 一致。失败 snapshot 会保留旧 serving release并进入可诊断 quarantine或 manual-block，Mac producer 等待绑定本轮 identity 的 `committed` 后才报成功；既有每 5 小时 cron继续作为 freshness入口。

## 2026-08-09

- 站点数据同步（Mac 主库 → 腾讯服务器只读副本）从纯手动改为每 5 小时自动执行（cron + `run-or-alert` 失败告警；SSH 认证经 launchd ssh-agent socket 发现，见 ADR-013）。此前同步靠手动触发，8 月 8 日起无人执行导致公网整站停更约一天半；本次同时补上「远端拒绝快照」的检测（每轮同步前按服务器自身时钟核对已接受快照的年龄，超过 11 小时阈值后在下一采样点上报，最坏约 16 小时发现）。
- 修复「微信文章解读」页自 8 月 7 日起停更的故障并恢复更新。根因有三层：解读所用的两条 LLM 通路同时不可用（ARK 周配额耗尽 + DeepSeek 官方余额不足），失败被永久缓存——解读一旦报错就再也不会重试，即使通路恢复也不会补齐；另有一个自 6 月起存在的隐蔽缺陷：文章标题 slug 仅大小写不同时，批次文件复制在 macOS 大小写不敏感文件系统上抛 SameFileError，导致约 340 篇已判定值得保存的文章从未上页。本次修复：解读失败的条目改为按指数退避自动重试（首败 15 分钟后可重试、每再败翻倍、累计 8 次后放弃，成功即清零），同文件复制改为跳过；pipeline 每轮解读上限 30 条，防止大积压时单轮长时间占锁饿死抓取。存量报错文章随每轮 pipeline 自动补齐，此前被吞掉的历史文章会按各自发布时间陆续出现在列表中。

## 2026-08-04

- 前端第二轮视觉与体验打磨，重点是**对齐、留白与信息层次**。桌面端：日期分组标题现在与下方所有时间戳右侧对齐成一条线；正文区不再固定宽度上限，宽屏下可用宽度被充分利用；时间线的圆点之间新增一条贯穿整个日期分组的连接线，收藏某条后该条圆点变为琥珀色；侧栏图标换成更清晰的图标；搜索按钮改为实心主按钮；新增全站定制滚动条与按钮按下 / 禁用状态；主题切换器用一块滑块指示当前档位；热点浮层不再在鼠标移向它的途中消失。
- 手机与网页缩放（≤960px）：正文列变宽、左右留白对称；此区间的时间戳字号偏大的问题已修正；推荐理由不再被截断成两行；顶部条改为随页面滚动而非固定吸顶。列表行变得更清爽——行首只保留时间与信源、分数移到行尾，不再挤入精选标记、收藏按钮与标签，信源名不再被压缩省略。**首页与「全部 AI 动态」的卡片收藏按钮现在只在桌面显示**；手机上仍可经「更多 → 收藏」查看、取消、导出与导入收藏（导入依然能新增收藏）。手机上日期分组不再提供折叠。话题标签现在只出现在「全部 AI 动态」页。
- 更新日志页修复了手机与缩放档的排版错乱（正文此前会被挤成窄条且左右不对称）。日期标题改为中文长格式并附星期，标题下方新增一行说明。
- AI 日报页正文末尾新增四项数据概览：今日事件 / 一手报道 / 新模型 / 信源。四项均为本站自有定义——今日事件为该期条目数，一手报道为该期中非推文类且信源等级为 T1 的条目数，新模型为该期中属「模型发布」分类的条目数，信源为该期去重信源数；不声称与任何外部站点口径一致。日报摘要里的行内代码现在显示为等宽字体的浅底圆角块。
- 热点榜页在桌面、平板与手机三档的正文宽度分别调整到更合适的比例。
- 首页首批内容现在由服务端直出全部约 40 条，避免浏览器加载后用完整首批替换 12 条预渲染内容所造成的布局位移。手机上品牌 + 日期紧凑条改为只在首页出现；「更多」、「收藏」、「关于」和「微信文章解读」页改用各自的页面标题头，微信详情与 404 页的返回列表入口保留。

## 2026-08-02

- 前端全面改版（参照 aihot.virxact.com 的信息架构与视觉）：默认主题从深色科技风改为浅色简约风，新增暗色变体与侧栏底部三态主题切换（浅色/深色/跟随系统，localStorage 持久，head 内联脚本防闪烁）；精选与全部动态页从分页控件改为无限下拉（IntersectionObserver + 请求代际校验防筛选切换竞态；/all 搜索态改用页码分页以规避 timeline API 搜索时忽略 cursor 的语义）；卡片新增收藏按钮与 `/bookmarks` 收藏页（localStorage 快照 + 导出/导入 JSON，导入经字段与日期可解析校验；服务端同步接口已预留约定未实现）；首页新增"当前热点"榜（新端点 `GET /api/v1/hot`，近 48 小时按 加权分×10+关联讨论×5 排序，单次一致快照取样，纳入 90s 公共缓存白名单）；日期分组可折叠且追加加载继承折叠态；主站页面移除 Google Fonts 改用系统字体栈（消除跨境字体请求的首屏延迟），AI 日报页保留原深色报纸风（旧样式与 token 原样迁移）。微信详情页安全契约由"整页无 script"收窄为"正文容器无 script"（新增 head 主题引导与导航 module 两个可信脚本）。改版经 Codex 高档对抗审查两轮修复复核后合入。

## 2026-07-26

- 将 same-host `performance-probe` 从 busy/idle 双轨 gate + busy rollup 改为 idle-only：pipeline 运行或负载不确定时不保存/评估，只有 idle 窗的 22 样本确认窗超预算才直接 page，不再降为 notice。probe 调度同步从 hourly `:17` crontab 改为专属 per-file launchd（`StartInterval=300`，经 `./run.sh performance-probe` 进入 external watchdog），pipeline 仍保留既有 `*/15` crontab；install/uninstall/status 现管理该 plist 与 legacy symlink 迁移。Playwright Chromium 是微信抓取与默认 probe 共用的显式部署前置，安装器不会自动下载或校验。2026-07-26 live 证明中，全 8 个旅程 cell 在 4.93 小时取得第 22 条 idle 样本，满足 6 小时硬门槛但仅余约 1 小时负载裕度。PERF 投递契约明确为 at-least-once，并依赖 `im-notify` 持久 signature dedup 抑制同一 crash retry 的重复可见消息。

## 2026-07-22

- 新增 `curated_items.summary_json` 精选 digest 缓存的常驻保留：每次 curate 后自动清空超过 `keep_days`（默认 7 天）且非最新 run 的可再生预计算缓存，使 `radar.db` 体量长期有界（此前约 8MB/天持续膨胀），生产库一次性瘦身实测由约 2.28GB 降到约 1.5GB（省约 785MB / 34%）。同时新增 `./run.sh admin db retain`（只清列）与 `./run.sh admin db slim`（清列 + VACUUM 回收磁盘、DB 同步前跑）子命令，`slim` 返回 `retained`/`compacted` 两阶段结果，`--dry-run` 零写只报待清量。唯一用户可感知的行为变化：`/api/v1/curated?run_id=X` 访问超窗口的历史 run 时，其 digest 改为 live 现算，内容反映当前 enrichment 而非 curation 时快照（TTL 语义）；所有 HTML 用户页只服务最新 run，字节一致、不受影响。
- 将运维告警从单一 page 级别升级为 page/notice 分级：需立即处置的事故发往 `ALERT`，低打扰退化发往 `NOTIFICATION`。pipeline busy 期间但同视角 idle 正常的 PERF 超预算会合并成一条 `PERF:rollup:busy` notice，而真实 idle/公网退化仍保留 page。新增 `data/alert-events.jsonl` 已送达通知历史，可按规则、severity、firing/resolved 与通道查询最近 14 天的成功投递。

## 2026-07-19

- 修复精选归档计数缓存的过度失效和微信 SSR 请求连接生命周期，使首页与微信页面在 pipeline 写入期间保持稳定响应，同时保留精确总数和分页语义。
- 新增 `performance-probe` 用户旅程监控：从同机 origin/public 测量首页、微信列表、详情和翻页，区分 pipeline idle/busy，以 `PERF:*` 规则告警并保留 14 天诊断证据。结果明确标为 same-host provisional，不作为区域 SLO。
- 新增 `performance-remediate` 候选修复 worker：confirmed 性能退化可触发单个、最长 60 分钟的隔离 Codex worktree，生成未进入主分支且未部署的本地候选 commit；越界配置会 fail closed。
- 修复 `performance-probe` 首页浏览器探针把正常样本误判为 `hard_failure` 的缺陷。此前它拿完整渲染卡片列表与 12 项期望做全等比较，导致每个健康首页样本都被标记 hard_failure；现改为按前缀匹配期望，前缀不符仍会 hard-fail。同时收紧 `performance-remediate` 的启用门槛说明：缺陷虽已修复，运维仍须先确认 hard_failure=false 且首页 `PERF:*` 未告警，再安装 remediate cron。
- 为公开分页路径（`/`、`/wechat`、`/api/v1/curated`、`/api/v1/wechat`）在安全分页变体下让 origin 发 `Cache-Control: public, max-age=90, stale-while-revalidate=30`，而带 `q=`（含空 `q=`）、分类/日期/未知参数或非 200 响应一律 `private, no-store`（fail-closed 白名单，已验证这些路由无 cookie/会话变量）；前端 `app.js` 在 SSR 预载后预取下一页 API 并在点击翻页时复用同一 promise，搜索/分类请求绕过该 90 秒前端缓存。头部代码需重启 `serve` 生效。配套在 Cloudflare zone `aiplanet.live` 手动加了 Cache Rule「AI Radar short public pagination TTL」——这是 Cloudflare dashboard 侧配置、非 repo 代码，Edge TTL 与 Browser TTL 均 respect origin 头——使这些路径改由 CF 边缘缓存，实测翻页 API 从约 3-5s 的 DYNAMIC 回源降到约 0.5-1.4s 的 CF HIT。

## 2026-07-13

- A1-A4 告警的发送传输改为复用本机 `im-notify --alert`，不再由 AI Radar 直接调用飞书 webhook。原有 firing / resolved、debounce 与 30 分钟冷却状态机保持不变，且不叠加 `--dedup-key`；发送器失败会记录日志而不会终止周期告警检查。`alert` launchd 模板同时把 `~/.local/bin` 加入 `PATH`。

## 2026-06-24

- Split LLM usage accounting into `data/llm_usage.db` (`AI_RADAR_LLM_USAGE_DB`) so prefilter / score / enrich token writes no longer contend with the main `radar.db` writer. Existing `radar.db.llm_usage` history is copied into the dedicated DB on migration/first use, `/admin/usage` reads the dedicated DB while still showing item/source metadata from the main DB, and A2 prefilter P95 now uses a recent 2-hour sliding window so recovered latency incidents do not keep firing until midnight.
- Added an internal `/admin/usage` page and `/api/v1/admin/usage` endpoint for maintainers to inspect LLM usage attribution. DeepSeek/ARK `chat_json` calls now persist one `llm_usage` row per prefilter / score / enrich LLM call, including model, input/output tokens from `completion.usage`, item attribution, and input size. The page uses the same Cloudflare Access / dev-only local bypass guard as `/admin`, is not linked from public navigation, and rolls up the last 30 days by day, model, and pipeline stage.

## 2026-06-12

- Made fresh-clone setup degrade cleanly when optional private resources are missing. The Mp2RSS WeChat source now skips with a warning when `MP2RSS_FEED_URL` is unset or empty instead of aborting source loading, and `.env.example` no longer contains a fake Mp2RSS URL that would force a broken fetch. `./install.sh` now checks each service before installing: `serve` always installs, `pipeline` needs one LLM API key, `alert` needs `FEISHU_GENERAL_ALERT_WEBHOOK`, and `tunnel` needs `deploy/cloudflared/config.yml`. Missing promptable values can be entered interactively and are appended to `./.env`; non-interactive installs skip only the affected services and print a summary.

## 2026-06-09

- Stopped A4 (article ingestion) from alerting on transient fetch flaps. All X/Twitter sources fetch through the single public `nitter.net` instance, which intermittently times out for one ~15-minute fetch round and then self-heals; each flap fired and resolved A4 within ~15 minutes as pure noise, while the daily ingestion count was never actually affected (a round skipped by the flap is backfilled by the next). A4 now debounces: a fetch-failure condition must persist past a 30-minute window (≈2 fetch rounds) before it notifies, and a flap that recovers within the window is absorbed silently — no firing and no resolved. The debounce is per-rule and configured only for A4 (`a4.debounce_minutes`); A1/A3 still notify immediately, so this does not delay genuinely urgent alerts. Also corrected A4's disposition text, which pointed at the retired `wewe-rss/bridge` for WeChat — WeChat now ingests via Mp2RSS, and a batch X-source failure is the more common trigger. The `alert` service must be running the updated code for this to take effect.

## 2026-06-08

- Made `/wechat` search (and the shared timeline/curated search) whitespace-insensitive, so a query with extra internal spaces returns the same results as one without. Previously a stored title like `分享Claude Code` was found by `分享Claude Code` but not by `分享 Claude Code`, because the query and the matched columns were compared with their spaces intact. Both the query patterns and the searched columns (title, author, abstract, tags) now have all whitespace — including full-width spaces — stripped before matching, and the longer-query FTS path handles spaced queries too. Simplified/Traditional matching is unchanged. The web layer must be restarted for this to take effect.
- Fixed the missing avatar for the WeChat Official Account "赛博禅心", which showed a fallback initial instead of its real avatar. A single failed avatar scrape on 2026-06-02 had left its cache row empty and a 7-day negative cache prevented any retry. Added an `admin wechat-avatar refresh --account <name>` command that clears one account's cache row and re-scrapes immediately (used to repopulate 赛博禅心's avatar live), and shortened the failed-scrape negative-cache TTL from 7 days to 2 so a transient miss self-heals within days instead of a week.
- Fixed the 日报 page so navigating to a past day now shows that day's curated articles instead of an empty report. Previously `/daily/{date}` (前一日/后一日 and direct date URLs) only ever populated for today, because a dated request was answered from the single latest curation run, which curates only about one day of fresh items. A dated daily report now aggregates curated items published on that date across all curation runs (deduplicated to each item's latest curation), the same cumulative-archive logic the home page `/` uses, so any past date with curated content renders a populated report. The admin explicit-`run_id` path and the `/` and `/all` archive pagination are unchanged.

## 2026-06-07

- Reduced false alerts in the monitoring rules. A2 (pipeline health) no longer treats a long in-progress run as a fault: a `SKIP` log means "pipeline already running" (liveness), so it is no longer a standalone trigger, and the "no successful pipeline" heartbeat threshold was raised from 45 to 120 minutes — this eliminates the recurring fire/resolve flapping that produced the bulk of past alert noise. A3 (website) dropped its `healthz` dimension, which was a dead signal (hardcoded to never fire) that misleadingly displayed "healthz 连续失败 0 次"; A3 now reports only the real user-side 5xx rate. Each line in `logs/alert-check.log` is now timestamped (Asia/Shanghai) for incident forensics. A true active healthz probe and a time-aware A4 ingestion floor are tracked as follow-ups in `docs/issues/general.md`.
- Prefixed every Feishu monitoring alert (both firing and resolved) with a `【AI Radar】` project label. Because the alert webhook (`FEISHU_GENERAL_ALERT_WEBHOOK`) is shared across projects, the prefix lets a recipient tell at a glance which project an alert came from.
- Added an explicit "访问原文" (visit original) link to WeChat interpretations so readers can jump to the source Official Account article: a bordered button below the title on each `/wechat/<slug>` detail page, plus a compact "原文 ↗" link on every list card. Both open the original in a new tab and reuse the existing source URL. The shared frontend asset version was bumped so visitors receive the new behavior.
- Added search to `/wechat`, scoped to interpretation card fields: Chinese title, Official Account author, abstract, and tags. Search URLs are shareable with `?q=`, pagination and detail-page return links preserve the query, Simplified/Traditional Chinese variants match, and the shared frontend asset version was bumped so visitors receive the new behavior.

## 2026-06-06

- Removed the retired WeWe RSS bridge from the service layer. `./install.sh`, `./uninstall.sh`, and `./status.sh` now manage four services (`serve`, `tunnel`, `pipeline`, `alert`) instead of five, and a bare `./install.sh` no longer requires Docker or aborts when it is unavailable. WeChat ingestion continues through Mp2RSS; rollback material (`deploy/wewe-rss/` + RUNBOOK) is retained, with the launchd wiring recoverable from git history.
## 2026-06-04

- Changed the curated home page `/` from a single page of the latest curation round's top 40 into a cumulative archive of every item ever curated. It now aggregates all distinct items selected across past curation runs (deduplicated, currently about 1,793 items), ordered newest first, paginated about 40 per page (currently about 45 pages) using the same numbered page controls as `/all`. Page 1 still shows the latest curated picks, preserving the "skim in five minutes" use.
- Upgraded pagination on the `/all` timeline and the `/wechat` interpretation list to numbered page controls: first and last pages are always visible, the current page shows two neighbors on each side, gaps collapse to an ellipsis, any page number is directly clickable, and previous/next arrows remain. Both pages share one pagination component.
- Changed `/all` to show the true first and last pages. `/api/v1/timeline` now returns an exact total count instead of the previous forward-looking estimate, so the last page reflects real data (matching `/wechat`), and out-of-range requests such as `?page=9999` clamp to the real last page.

## 2026-06-02

- Added the `/wechat` tab for WeChat Official Account article interpretations, with structured summaries, tags, worth-reading filtering, shareable detail pages, and ai-assistant knowledge-base writeback for saved articles.
- Removed the disabled legacy WeWe source definitions `wx_guizang` and `wx_crossing` and deleted their historical item rows from the production DB after creating a verified backup.
- Migrated WeChat Official Account ingestion from the self-hosted WeWe RSS bridge to the hosted Mp2RSS feed, removing the local Docker container and WeRead QR-login maintenance that frequently broke ingestion in production. The feed URL with its embedded key is read from the `MP2RSS_FEED_URL` environment variable and never committed.
- Added real Official Account names and avatars to WeChat article cards. Cards now show each article's source account name and its avatar instead of the shared collection name, falling back to the WeChat icon when no avatar is cached.
- Added the `/admin` operations dashboard for user traffic, ingestion, pipeline health, and active alert status.
- Added the `alert` background service, which runs `admin alert-check` every five minutes and sends A1-A4 monitoring alerts through a Feishu custom-bot webhook.
- Documented the monitoring runbook, including Cloudflare Access setup for `/admin*` and `/api/v1/admin*`, Feishu webhook setup, and daily service verification commands.

## 2026-06-01

- Fixed Chinese-source search so recently fetched backfill articles are evaluated and visible even when their original publish date is older than the pipeline window.
- Improved source-name searches by ranking source/author matches ahead of content-only matches and rotating same-name sources so a prolific source no longer hides lower-volume WeChat sources on the first page.
- Added Simplified/Traditional Chinese query expansion, so searches such as `归藏` and `歸藏` find the same Chinese-source articles.

## 2026-05-30

- Expanded `/api/v1/timeline` and `/api/v1/curated` search to match source names, authors, and Chinese titles in addition to article title/body. Searches of 3+ characters use FTS; 1-2 character queries fall back to short-field LIKE matching.
- Changed search semantics intentionally: searching a source name can return all matching articles from that source, and scoring `reasoning` is no longer part of the search index.

## 2026-05-29

- Added WeChat Official Account ingestion through a local WeWe RSS bridge, with Playwright-based full-text scraping for internal LLM processing and public cards limited to generated summaries plus original article links.

## 2026-05-28

- Improved `/` and `/all` first-screen loading by serving SSR-preloaded feed HTML; production verification now shows no visible loading spinner and sub-1.5s median first content on the main feed URLs.
- Fixed `/all` timeline entries without scoring data so every visible card renders a numeric score pill.
- Fixed the About page repository link to point at `your-org/ai-radar`.

## 2026-05-24

- Improved `/api/v1/timeline` and `/all` load performance by adding SQLite indexes, preloading enrichment data in the timeline query, and replacing the exact count query with a pagination-safe estimate.
- Fixed timeline time display so visible times use the same Asia/Shanghai timezone as date grouping.
- Updated the About page repository link to `your-org/ai-radar`.
