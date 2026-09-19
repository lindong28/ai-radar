# 运维监控与告警 Runbook

> Mutable snapshot. 面向 AI Radar 运维者：怎么看 `/admin`、怎么处理告警、怎么配置飞书与 admin token。
>
> 本目录（`docs/operations/`）是维护者产线 runbook，绑定具体实机拓扑；fork 部署路径见 [README](../../README.md)。

## 入口

- 当前生产 Dashboard：`https://news.aiplanet.live/admin`
- 当前生产 Metrics API：`https://news.aiplanet.live/api/v1/admin/metrics`
- 当前生产 LLM 已记录用量：`https://news.aiplanet.live/admin/usage`
- 当前生产 LLM 已记录用量 API：`https://news.aiplanet.live/api/v1/admin/usage`
- generic fork：把上面 host 替换为自己的 `AI_RADAR_SITE_DOMAIN`
- 本地访问（需显式开启）：`AI_RADAR_ADMIN_ALLOW_LOCAL=1` 后访问实际 serve 端口；本产线为 `http://127.0.0.1:8010/admin`
- Alert 命令：`./run.sh admin alert-check`
- 用户旅程探针入口：`./run.sh performance-probe --help`（当前部署参数见下文）
- 性能候选修复 CLI（启用仍受下文 gate 约束）：`./run.sh performance-remediate --help`

`/admin` 和 `/admin/usage` 是运维面板，不挂公开导航。鉴权是共享密钥：`.env` 里的 `AI_RADAR_ADMIN_TOKEN`（≥16 字符；`read_value` 解析，即进程 env > 项目 `.env` > `~/.claude/.env`），请求带 `X-Admin-Token: <值>` 或 `Authorization: Bearer <值>`，应用侧用 `hmac.compare_digest` 常量时间比较；**未配置或过短时 fail-closed，所有远程请求 403**。本机 `127.0.0.1` / `::1` / `localhost` 的本地 bypass 默认**关闭**，仅在显式设置 `AI_RADAR_ADMIN_ALLOW_LOCAL=1/true/yes` 时放行。2026-09-19 之前的守卫只判 `Cf-Access-Jwt-Assertion` 是否非空（伪造即 200，见 [closed issue](../issues/archive/closed.md)）；该 header 现已不再有任何意义，nginx 也会把客户端带来的这个头清空。三个 admin 路由不进 OpenAPI schema，`/docs`、`/redoc`、`/openapi.json` 已整体关闭。

## Dashboard 怎么看

| 板块 | 口径 | 用法 |
|---|---|---|
| 用户量 | access log 过滤 bot/static/scanner 后的 PV/UV；`raw_unique_ips` 作为上界参考 | 看真实用户访问是否骤降，结合 5xx 率判断是否用户侧故障 |
| 文章摄取 | 今日 items 增量、最新 fetch 插入/失败、最近 curation run | 看内容是否仍在进入系统；fetch 失败率高或今日增量低会触发 A4 |
| Pipeline 阶段健康 | fetch/prefilter/scoring/enrich/curate 的处理量、错误率、P50/P95；prefilter P95 使用最近 2 小时滑动窗口，避免已恢复后旧慢样本保留到午夜 | 定位是哪一阶段异常；日志中的 `score` 已归一为 dashboard 的 `scoring` |
| LLM 已记录用量（`/admin/usage`） | 滚动 30 天 `llm_usage` 记录行的成本三态、来源单价、cache 覆盖、分阶段/Provider/模型/日聚合与前一等长窗口比较 | 定位 Top 驱动；跨窗金额统一按当前费率、cache 全未命中重算，真实 cache 事实仍用于各窗记录行金额 |
| 当前告警 | A1–A7 与 W1 当前状态；D3 定价提醒不进入 page lifecycle | 先看故障类别，再看具体对象和下一步动作 |

时间口径固定为 `Asia/Shanghai`。access log 当前写入 `logs/serve-access.log`，pipeline 日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`。

`/admin/usage` 不读取历史 `cost_usd` 列：它使用受管定价 catalog 查询时派生成本。无定价的 pair 显示「未定价」，cache token 拆分未采集时显示「未采集」，命中率显示「无数据」，不会用 0 代替。可用 `AI_RADAR_USD_CNY` 调整人民币投影汇率；`AI_RADAR_LLM_PRICING_JSON` 已退役，必须从运行环境移除。

## 告警规则

| severity | 用途 | 消息 / 投递 |
|---|---|---|
| `page` | 需要立即关注的事故 | 🔴；`im-notify --alert` → `ALERT` webhook（`FEISHU_GENERAL_ALERT_WEBHOOK`） |
| `notice` | 需要知道、但无需立即起身的退化 | 🟡；`im-notify` → `NOTIFICATION` webhook（`FEISHU_GENERAL_NOTIFICATION_WEBHOOK`） |

firing 只在新 episode 首次确认、或同一 episode 在 notice 与 page 间转换时投递；持续 firing 不再周期重发。所有 resolved 都走 notice 通道，因为恢复无需立即处置。

| 规则 | 故障类别 | 典型含义 | 处置动作 |
|---|---|---|---|
| A1 | 上游模型不可用 | DeepSeek/OpenAI/GLM/ARK 返回 endpoint/model/权限/余额类错误；`schema validation failed` 已排除 | 查 provider 控制台余额、模型权限、API key；必要时切换 provider 或充值 |
| A2 | 阶段错误率/耗时异常 | prefilter/scoring/enrich 的错误 numerator/denominator **各自只取最近 15 分钟**；样本数分别至少为 `4/4/2` 才让错误率支路参与 page。独立 P95 仍用最近 2 小时口径，但单独越线只发 notice；stage 错误率或**超过 120 分钟没有成功 pipeline**才 page。窗口内三个 stage 都无样本时标为未评估，不以 0 样本证明恢复。SKIP 日志表示 pipeline 已在运行，不单独视为故障。**心跳支路的归因只读最近一个非 SKIP 轮**的 `egress-preflight status=…` 行；非 healthy 时推送只给规范化状态和监听检查动作，原始 reason 保留在 state/ledger 的 `values.egress_preflight_reason`，避免把无界诊断文本塞进主视图；出网正常、或最新非 SKIP 轮还没打印 preflight 行时保持锁排查文案 | **恢复要连续 2 次评估都读到不 firing**（`a2.resolve_debounce_rounds`），所以 ✅ 比条件清除晚一到两次评估属正常。出网 preflight 非 healthy → 先确认 `AI_RADAR_EGRESS_PROXY_PORT` 是 1–65535 的整数，再按下文「出网 selector 的 preflight 与实际 route」核对应端口有没有监听者（`lsof -nP -iTCP:<端口> -sTCP:LISTEN`），恢复后下一轮 cron 自动重跑；否则查 `logs/pipeline-*.log` 的失败阶段，必要时手动跑单阶段复现 |
| A3 | 网站用户侧异常 | `/admin` 以外用户访问的 5xx numerator 与 PV denominator **同取最近 15 分钟**，且 `PV >= 20` 时 5xx 率才参与 page；无法证明在窗口内的日志行不计入。healthz 主动探测从已安装 serve plist 的 `ProgramArguments` 解析端口，连续失败 2 次是独立 page 支路，计数跨轮持久化于 `data/alert-state.json` | 查 `logs/serve-access.err.log`、`logs/serve-access.log`、`./status.sh serve tunnel`；确认本地 serve 健康 |
| A4 | 文章摄取骤降 | fetch 只读最近一个同时含汇总行及其后 `fetch OK/FAIL` 终态行的完整轮；没有完整轮或该轮超过 `fetch_stale_minutes`（默认 90 分钟）时，fetch 维度明确标为未评估。普通 fetch 失败率超过 `fetch_failed_ratio`（默认 0.4）、但 items 正常时是 `notice`；401/402 账户层失败数占 attempted 的比例超过同一阈值时是 `page`；今日 items 增量低于动态 floor 时也是 `page`。items-floor 在当日已有任意完整 fetch（含 `attempted=0`），或当日任一非 SKIP 轮明确记录非 healthy egress preflight 后上膛；午夜首轮尚未开始时保持 `in_progress`，后续未完成轮不会重新遮蔽当日已观察到的真实 preflight 断流 | 账户层 page 按状态码和来源组给动作：402 检查并恢复对应 API 账户的付费层/额度；X API 的 401 核对 `X_BEARER_TOKEN`，其它来源的 401 按 `data/sources.toml` 的 `required_env` 核对运行环境。普通失败按 error 分组读 `logs/pipeline-*.log`，并见下方「出网 selector 的 preflight 与实际 route」；当前主动微信入口是 Wechat2RSS，paused Mp2RSS 不应有 OK/FAIL 行（见 [wechat-ingestion.md](wechat-ingestion.md)） |
| A5 | 微信解读产出停滞 | 解读启用、4 小时无成功解读，且存在 fetched 至少 4 小时、仍符合重试资格的微信 pending 时 page；无近期成功且 pending 因退避/冻结归零时标为不可评估，不发「已恢复」 | 先查 `logs/pipeline-*.log` 近 4 小时 interpret 阶段与 provider 成功/错误，再核对余额/配额；`ark-breaker.json` 只有 `opened_at` 仍在 2 小时 cooldown 内才是当前证据 |
| A6 | 已记录 LLM 调用近 24 小时成本突变 | 当前窗与基线按同一现行费率、cache 全未命中重算。阈值两档：超过 `max(¥20, 3×中位数)` 发 notice，超过 `max(¥100, 6×中位数)` 才 page。金额与次数只统计 `llm_usage` 记录行，所以**任何越线判定都是在一个下界上做的**——未写入该表的付费调用（失败链路、未接入计量的调用点）不在内，resolve 也因此不表示 attempt-level 健康。在途窗（`.pipeline.flock` 证明本轮在跑）按 `in-progress` 用下界继续判 firing 与 notice→page，下界未越线时保留既有 episode 等封口。`baseline_days` 的实际取值与「至少 3 个有记录日」的缺口见 [ISSUE-023](../issues/cost-observability.md#issue-023--a6-的至少-3-个基线日门当前不可达) | 先按消息中的 Top 驱动核查；它复用 A6 的 cache 中性已知成本聚合。未定价调用在 `/admin/usage` 单列，nominal 目录价不是账单实付 |
| A7 | 来源静默 | 逐源判定，不看全站总量：只评估 `enabled=true AND paused=false` 的来源；某个候选距最近一条 item 超过 `max(6 小时, 2×该源近 30 天平均出稿间隔)` 时进入静默。1 个可处置来源静默发 notice，2 个及以上才 page；paused 来源明确“不评估”，不进入静默、褪色、quiet-X 或无法评估计数 | 先看 `logs/pipeline-*.log` 里该来源的 OK/FAIL 行：多源同时静默优先查共同链路，见下方「出网 selector 的 preflight 与实际 route」；单源静默则查该源站点或其上游订阅服务。准备暂停前先走下述 identity prepare，不能让旧 episode 误发恢复 |
| W1 | 微信 Chromium 前置检查失败 | scheduled pipeline 在 fetch 前检查 Playwright 预期 Chromium 路径；缺失/不可执行为 `unavailable`，自省失败为 `not_verified`，两者都在整轮外部抓取前 fail closed 并立即 page。只有后续整轮数据阶段成功且末端复检仍通过才以 notice resolved；恢复投递失败保持 firing/pending 并在下一次完整成功轮自动重试 | `unavailable` 运行 `uv run playwright install chromium`；`not_verified` 查看同轮 `logs/pipeline-*.log` 的 Details 并修复 Playwright driver/runtime；再运行 `./run.sh wechat-browser-preflight` |

### 暂停来源前准备 A7 episode identity

暂停来源前从仓库根目录运行以下 dry-run；`$PWD` 会把 state/event 参数展开为本次实际检查的绝对路径，CLI 也会回显这两个路径与本 runbook：

```bash
./run.sh admin alert-prepare-source-pause --source-id <id> --state-path "$PWD/data/alert-state.json" --event-path "$PWD/data/alert-events.jsonl" --dry-run
```

legacy announced-firing episode 以同一 episode 最早追加的 firing ledger 行作为 opening 候选；只有该候选的 parsed `ts`、row `episode_since` 与 lifecycle `since` 表示同一时刻时，才把它的 source identity 作为 opening source snapshot。若有多条 firing 行同时满足该 opening timestamp identity，它们的 normalized source identities 必须完全一致，否则身份不唯一并 fail closed。后续同 episode 的来源集合扩缩不改写该身份，也不能在 true opening row 被 retention 裁掉后冒充 opening。opening timestamp identity 或 source identity 缺失、畸形、为空，或不含请求 source 时输出 `BLOCKED_MISSING_EPISODE_IDENTITY`，不得猜 source identity。若输出 `SEEDABLE`，取得授权后把同次 `input_digest` 传给默认模式的 `--expected-input-digest`，并继续显式传入同一组 state/event 绝对路径：

```bash
./run.sh admin alert-prepare-source-pause --source-id <id> --state-path "$PWD/data/alert-state.json" --event-path "$PWD/data/alert-events.jsonl" --expected-input-digest <dry-run-input-digest>
```

默认写入必须返回 `SEEDED`；随后立即重跑上述 dry-run 并得到 `READY`，完整交接为 `SEEDABLE → SEEDED → READY`。任何 state/ledger 漂移都会 fail closed，重新 dry-run 后再决定；新格式或已 seed 的 episode 会直接输出 `READY`。

已 announced 的 A7 episode 若其全部 opening 来源变为 paused，不调用 sender、不增加 `sent_count`，直接写 closed/ok；ledger 恰追加一条 `rule_id=A7, type=resolved, channel=INTERNAL, reason=source_paused`，带 episode identity 与该 episode 内的 paused source ids。对应查询身份为 `channel=INTERNAL,type=resolved,reason=source_paused`。同一 closed 状态重跑不重复记账；若同时出现不相干的新静默来源，它作为新 episode 立即通知，不继承旧 episode 的提醒间隔。

非暂停场景也按 opening source identity 区分事故：连续 firing 期间，若本轮 actionable source ids 与唯一 active lifecycle 的 opening source ids 完全不相交，旧 episode 尽力以 `channel=INTERNAL, type=resolved, reason=source_set_changed` 留痕，新集合立即开启并通知新 episode；INTERNAL 写入失败只丢失该审计行，不阻断状态转换或通知。集合仍有交集时不重新通知。这样 A→B 的真实故障换批不会被“持续 firing”无限吞掉，也不因同一批来源的小幅增减制造新告警。

若只有部分 opening 来源 paused，episode 不能仅凭集合差值结案：所有未暂停的 opening source ids 都必须出现在本轮 `evaluated_source_ids`，证明它们仍有当前逐源评估证据；否则保持 firing、sender 调用数为 0，且不写 resolved ledger。证据齐全但本轮 A7 仍为 degraded 时，沿用黄色“转为不可评估”结案，不覆盖成绿色恢复；证据齐全且非 degraded 时才发送 scope-limited resolved，分别点名本次恢复与因暂停退出评估的来源。普通 delivered firing/resolved ledger 不持久化 `paused_source_ids` 或 `evaluated_source_ids` 这两个状态机控制字段；只有上面的全暂停 INTERNAL 结案保留 episode-scoped `paused_source_ids`。

### A4 账户层失败（401/402）的处置与恢复判定

A4 只读**完整 fetch 轮**（`=== attempted=… failed=…` 汇总行、且其后有 `=== fetch OK|FAIL ===` 终态行）。从 `FAIL <source> … Client error '<状态码>'` 抽出 HTTP 状态码，401/402 计入账户层：

| 状态码 | 含义 | 处置（消息里也会写） |
|---|---|---|
| 402 Payment Required | 该来源组的 API 账户付费层 / 额度用尽 | 为该来源组的 API 账户充值或恢复付费层，然后重跑 `./run.sh fetch`（或等下一轮 cron） |
| 401 · 来源组 X API | `X_BEARER_TOKEN` 被拒 | 更换或确认 `X_BEARER_TOKEN`，重跑 fetch |
| 401 / 402 · 其它来源组 | 该组来源被上游拒绝；前缀组不是账户，原因要逐源核对 | 核对该组来源的配置与响应；来源声明了 `required_env` 时按 `data/sources.toml` 检查对应环境变量或付费层，重跑 fetch |

来源组按 source_id 前缀聚合：`x_*` → 「X API」是真实账户（共用一个 `X_BEARER_TOKEN`）；**其余前缀只是命名约定，不是账户身份**（如 `google_*` 是 5 个各自独立、不共享凭据的公开来源：4 个 feed + 1 个网页抓取），消息会写成「来源组 <前缀>（按 slug 前缀聚合，非账户身份）」，处置是核对该组来源的配置与响应、来源声明了 `required_env` 才查凭证。消息正文最多列 5 组、影响行最多点名 3 组，其余以「另有 N 组同此 / 等 N 组」计数。

- **入口**：账户层失败数 / attempted > `a4.fetch_failed_ratio`（0.4，与普通失败率同一阈值）→ 🔴 page，`page` debounce 为 0（首轮即发）。
- **恢复（无状态滞回）**：最近 `a4.account_resolve_rounds`（2）个 `completed_at` 互不相同的完整轮都回到阈值内才 resolved；同一轮被多次评估只算一轮；`attempted=0` 的轮不算证据（既不算恢复也不打断）；比最新可用轮早超过 `fetch_stale_minutes`（90 分钟）的旧轮不计——长时间断流后不会拿断流前的旧轮凑数。恢复消息会写明「无需立即处置」，断流期间的缺口按本节自行判断是否补抓。只有 1 个可用完整轮时消息写「恢复证据不足」，不写「已回落」。
- **未评估**：没有完整轮、最近完整轮超过 `a4.fetch_stale_minutes`（90 分钟）、该轮 `completed_at` 比现在晚超过 5 分钟（时钟异常，`stale_reason=future_timestamp`）、或该轮 `attempted=0` 时，fetch 维度标为未评估（不是健康）；items-floor 照常评估，两者同时命中时处置先查 pipeline 是否仍在跑（A2 心跳）与 preflight，再按 FAIL 行分流。
- **复核入口**：`logs/pipeline-*.log` 最近两个完整轮的 FAIL 行与 `/api/v1/admin/metrics` 的 `ingestion.latest_fetch.failed_by_status` / `recent_complete_fetches`。
- **已知边界**：无 HTTP 状态码的账户层失败（SDK 抛的配额异常）仍走普通 notice 路径；单个来源的凭证失效由 A7 兜底。


### 微信 Chromium 前置检查

`./run.sh wechat-browser-preflight` 的退出语义与 `admin edgeone check` 一样把“失败”和“未核实”分开：exit 0=`present`，仅证明 Playwright 当前预期路径是 regular executable；exit 1=`unavailable`，表示文件缺失或不可执行；exit 2=`not_verified`，表示 Playwright driver/runtime 无法给出可判定路径。exit 1 的明确修复是 `uv run playwright install chromium`；exit 2 不足以证明缺浏览器，先看输出的 Details 和同轮 `logs/pipeline-*.log`，修复 driver/runtime 后再跑。三种状态都不证明 Chromium 能 launch、版本兼容、网络健康或微信页面可达；需要完整验证时用 README 的真实 launch 命令。

`pipeline.sh` 在 egress 通过后、fetch 之前运行这项检查。任何非零都会终止整轮，因此本轮 RSS/X fetch 与 prefilter、score、enrich、curate、interpret 都不会启动；终端和同轮日志都会写明这项影响、日志路径及下一动作。这是避免残缺微信正文进入后续链路的显式代价。直接 `./run.sh fetch` 不在此边界内，仍保留逐条 RSS fallback。健康前检不发送通知、不提前清 W1；只有数据阶段 `failed=0` 后的末端复检才能结束 W1。内部 `--resolve-after-pipeline` 调用还必须继承 pipeline 创建后立即 unlink、且内容绑定当前 generation 的 fd 8 capability，同时传入 `.pipeline.flock` 的 fd 9，并提供与 `.pipeline.activity` generation 唯一匹配、严格记录 egress/前检/六阶段依次 OK 的本轮 `--pipeline-log`；裸调用、缺 capability、仅借用另一持锁者的同 inode fd、陈旧/错序/重复/失败日志或锁身份不符均 exit 2 并保持 W1 open。末端 resolve 失败不把已经完成的数据阶段改判失败，日志会同时写 `wechat_browser_preflight_resolve DEGRADED` 与最终 `PIPELINE DONE (failed=0; alert_recovery=DEGRADED)`，W1 保持 open 并在下一次完整成功轮自动重试；在此期间若路径再次失败，当前状态立即回到 firing，不继续显示“恢复通知待投递”。

W1 只会合并有严格因果锚的 A2 heartbeat：W1 已成功 page，最近一次成功 pipeline 之后存在非 SKIP 轮，且这些轮全都终止于 `wechat_browser_preflight FAIL`；W1 episode 起点不晚于“最后成功 pipeline 的精确时间戳 + A2 阈值”所得越线时刻，且把心跳年龄置 0 后 A2 不再 firing 时，才由 W1 承载 A2 并以 `channel=INTERNAL` 写入共享 ledger。越线先后不从取整后的分钟年龄和稍后的状态机时钟反推，秒级晚到的 W1 也不会吞掉更早的 heartbeat 事故。A2 的 stage error 与 P95 始终独立；从未有成功 pipeline 记录时也不抑制 A2。A4/A5/A7 覆盖摄取、解读与来源静默的独立失败面；现有 lifecycle `since` 只能证明首次被 alert-check 观察的时间，不能证明症状始于 W1，因此它们不由 W1 抑制。长时间阻断中若它们同时 page，这是保留无法排除的第二起事故，不以时间巧合冒充同因。

### 出网 selector 的 preflight 与实际 route（A4、A7 的处置指引都指向这里）

抓取整批失败时先读 `logs/pipeline-*.log` 最新一轮的 `=== egress preflight START/OK/FAIL ===`，再按结果分流：

1. `preflight FAIL`：reason 里点名了本地出口端口。`lsof -nP -iTCP:<该端口> -sTCP:LISTEN` 判有没有监听者，没有就起一个、或把 `AI_RADAR_EGRESS_PROXY_PORT` 指向真正在听的端口。探针经该端口实发一次请求，所以它同时覆盖了「没人在听」与「听着但出不去」；**它仍不证明逐 hostname 的线路对**，那一层现在由监听者决定、应用观测不到，走下面第 2 步。
2. `preflight OK` 但请求仍失败：运行 `agent-proxy-route-audit --format=jsonl`，按 hostname 联合读取 `selected_route`、`outcome` 与 `outcome_scope`。`upstream-application + unknown` 表示线路已归因但该事件不观测应用结果，`proxy-connect + success|failure` 表示代理 CONNECT 结果，`direct-sentinel + success` 只证明受控直连哨兵。`OK` 只证明 AI Radar 接受了当时的 selector machine status，不证明后续每个请求的 route 或 upstream 成功。

应用的 `airadar.egress.audit` 是调用点审计，不是 route authority。selector-owned transport 记录已知 hostname、launch、policy identity 与本地 outcome；显式 direct 的 loopback/synthetic 请求不依赖 selector status，也不产生带 policy identity 的应用 audit。`local_outcome=request:http:*|request:error:*` 表示真实请求结果；`subprocess_env:prepared` 与 `playwright_proxy_config:prepared` 只表示本地准备完成。managed-standard-env subprocess 使用 `hostname=null`，不表示子进程已经启动，也不表示其最终访问了哪个 hostname。不要用 listener 端口探活、父进程 proxy 环境或应用 intent 反推 GCP/Tencent/direct。

预期 policy：Anthropic-owned hostname → GCP SG，且线路失败不 direct/Tencent/ZYT fallback；OpenAI/ChatGPT/X → OpenAI provider route（Tencent primary，建隧道前失败时 ZYT fallback，两者均不可用则 fail closed）；Ark/DeepSeek/RSS/news/web → direct。域名表与实际 audit 只在 system-config；判断 provider 当前档位读 `tencent_route_mode`，判断单次实际出口读 `selected_route=tencent|zyt-fallback`。`/img` 仍是 ADR-057 的独立图片代理链路，不受本 selector 改造影响；排它的故障继续走 [services.md 的图片代理诊断](services.md#图片出口代理新加坡repo-外常驻服务)。

A7 补的是 A4 看不见的那一面：A4 用全站 item 增量与 fetch 失败率判定，单个来源死亡时其余来源仍把总量顶在 floor 之上。2026-08-14 至 08-17 微信来源零入库约 73 小时期间，A4 每天分别判定 firing 9 / 36 / 59 次，而 `send A4` 在 08-14、08-15 为 0 次、08-16 为 4 次。

**A4 的投递当前不可依赖**（未闭合，机制与证据见 [issues/alerting.md ISSUE-A01](../issues/alerting.md)），所以 A7 不是它的冗余，而是唯一覆盖单源静默的规则。

D3 每轮按 provider/model 检查 unpriced、stale、due-review 与 active tariff 变化，通过 `NOTIFICATION` webhook 发送，不带 `--alert`。未定价消息给出已记录调用数/已记录调用总数，stale/due-review 指名对象，price-changed 同时给旧值与新值。相同条件的调用计数变化不会重发；首次投递失败下轮重试，解除时 `im-notify --dedup-clear` 失败会保留 re-arm 义务，间歇未出现的模型仍保留旧价格签名。处置落点是 `src/airadar/pricing.py` 的 provider/model 条目、来源、生效区间与 `verified_at`。真实生产数据截至 P2 开发时尚未出现 stale、due-review 或 unpriced，这些分支目前只有 synthetic fixture 覆盖。

### LLM 成本报表与对账

**评测调用计入 `llm_usage`，所以 A6 也会被评测打响**（用户 2026-09-11 裁定：「不用区分评测和生产，
我只关心来自这个项目的总体 LLM usage」）。此前 `airadar.eval.aihot_fit.common.isolate_side_effects()`
把评测的 usage 行改道到 `data/eval-fit/llm-usage-eval.db`，现在**不再改道**——评测 token 是本项目的
真实支出，计入总账是有意的。运维含义两条：① 一次大评测（实测一轮约 3199 次调用 / 3.68M token）
足以越过 A6 的 `max(¥20, 3×中位数)` notice 档，收到 A6 时先看当天有没有跑评测；
② `/admin/usage` 与 `cost-report` 的单篇成本、按 stage 归因在评测日会偏高，**且没有字段能把两者分开**
（评测走的是同一批生产 stage 名与 model）。ARK 熔断器状态**仍然隔离**（`AI_RADAR_ARK_BREAKER_STATE`）——
它不是账，是生产每次调用都读的状态。

周报入口为 `./run.sh admin cost-report [--window-days N] [--send|--dry-run]`。默认取上一上海自然周；指定 N 后取 rolling N 天。`cost-report` cron 在周一 09:17 经 `run-or-alert` 发送。日序列用 durable `items.fetched_at` 与成功 processing rows 核对逐 stage 暴露：fetch>0 要有 prefilter success；成功且判为 AI 的 prefilter candidate>0 时分别要有 score/enrich success；wechat fetch>0 要有 interpret success。任何 stage 的 error row 只证明尝试过，不算成功；所以即使同日已有别的 stage 或 usage 行，partial stall 仍会关闭环比。pipeline 日志只补轮次、fetch inserted，以及 retained 日内明确出现的计量写入失败；旧日志缺失本身不关闭已由 durable 数据确认的比较，但文案会保留漏记风险。异常日在正文顶部单列。nominal 同时给目录价估算金额与占比；总额与单篇解读前窗比较都按当前费率、cache 全未命中重算，绝对金额仍使用窗口内真实 cache 事实。单次已知成本只除以 priced+nominal 已记录调用，不把 unpriced 当作 ¥0。调用次数、token 合计与同一计价口径的金额合计只统计 `llm_usage` 记录行，因此是全部付费调用对应总量的下界；任何未写入该表的付费调用均不在内（例如失败链路或未接入计量的调用点）。均值、占比和环比只描述已记录 cohort，相对全部付费调用真值的偏差方向未知。unpriced 不进入金额，stale/due-review 要先复核，所有金额均不表示账单实付。规范 owner 是 [ADR-023](../adr/023-define-recorded-row-measurement-scope.md)；ARK tariff/订阅权威性与付费 attempt 漏行仍由 [ISSUE-004](../issues/cost-observability.md#issue-004--ark-挂牌价来源非权威而它占已知成本的-876) 和 [ISSUE-021](../issues/cost-observability.md#issue-021--interpret-usage-只记录下游成功样本漏掉已计费的失败响应) 跟踪。

成本对账入口为 `./run.sh admin cost-audit [--format=kv|json]`。退出 0 表示 tariff arithmetic、anchor 与 deprecated-residue gates 全部通过；退出 1 表示至少一项失败，human 输出会提示改跑 `./run.sh admin cost-audit --format=kv` 定位每个 `FAIL` / `UNVERIFIED` / `CLEANUP_REQUIRED`。默认 human、KV 与 JSON 都携带与 `/api/v1/admin/usage` 相同的 `measurement_scope`；`CONSISTENT` / `PASS` 与退出 0 都不评价计量完整性或 tariff 权威，known cost 与记录行数也只按该作用域解释。

安装/核查周报前先做无真实发送的本机 preflight：

```bash
(
  set -e
  test -x "$HOME/.local/bin/im-notify"
  test -x "$HOME/.local/bin/run-or-alert"
  test -x ./run.sh
  ./run.sh admin cost-report --dry-run
  ./status.sh cost-report
)
```

installer 当前只检查 notification webhook，`status.sh` 只检查 crontab marker；上述 dry-run 也不覆盖 cron wrapper 或实际通知投递。首次计划执行后仍须检查 crontab 重定向目标 `logs/cost-report-cron.log`。这是 ISSUE-014 的已知 lifecycle 边界。

告警状态存储在 `data/alert-state.json`。每个 `rule_id` 内的 `page` / `notice` 有各自的 lifecycle、debounce、`since` 与投递状态。A4 的 `page` debounce 为 0（items-floor 或账户层失败首轮即 page），`notice` debounce 为 30 分钟（fetch-only 持续超窗才通知）。A4 账户层 page 的恢复证据无状态地取自日志：最近两个 `completed_at` 不同的完整 fetch 轮，其 401/402 失败数占 attempted 的比例都不超过 `fetch_failed_ratio` 后才 resolve；重复评估同一轮不算第二轮，少量残余失败允许关闭 page。fetch 过期或缺失时是未评估，不等于健康，也不能单独结束 episode。severity 转换沿同一个 `since` episode 递进：notice→page 与 page→notice 都只发送新 severity 的 firing，不发送中间 resolved；只有条件真正清除时才结束 episode（**A2 另加恢复滞回**：要连续 `a2.resolve_debounce_rounds`（2）次评估都读到不 firing 才宣告恢复）。同一 episode 持续 firing 不再按时间重复提醒；仍在 debounce 且从未成功投递的 lifecycle 可静默关闭，不伪造 resolved。成功投递过的 episode 在确认恢复时只发一条 notice resolved，并同时关闭该 rule 的其它未宣告 severity lifecycle。首次 firing 投递前，pending 同时持久化 notification nonce 与待投递消息快照；若 sender 失败，下一轮即使信号暂为 `in_progress`，仍复用同一 nonce 和原快照重试，不能把真实首次通知吞掉。投递语义是 at-least-once：由 `im-notify` 的持久 signature dedup 抑制同一意图的用户可见重复，不宣称 exactly-once。

### 已送达通知历史

A1–A7、W1、D3 与 PERF 共用 `data/alert-events.jsonl` 作为查询入口。**覆盖面仅限这四类**：`deploy/wechat2rss/healthcheck.sh` 那条 cron 走 `im-notify --alert --dedup-key wechat2rss-*` 直发，**不写这个 ledger**（它是外部探活脚本，不经 `alerts.py` 的 lifecycle）。所以「查最近告警」时 ledger 里没有 wechat2rss 记录不表示它没告过警，要另看该 cron 的执行与飞书 ALERT 通道。成功投递的 firing/resolved 写入对应 channel；只有具备严格因果锚的 W1→A2 heartbeat 合并会为被吸收规则另写 `type=suppressed, channel=INTERNAL`。A1/A2/A5 不再因同轮 co-fire 或心跳新鲜度互相吞并；没有规范化共因身份时宁可保留两起独立事故。投递行另含 `episode_since` 与 `notification_nonce`。失败 attempt 不写入。查询推送次数必须排除 INTERNAL，查询事故数必须按 episode identity 去重。例如：

```bash
tail -n 50 data/alert-events.jsonl | jq .
jq -c 'select(.channel != "INTERNAL" and .severity == "page" and .type == "firing")' data/alert-events.jsonl
jq -c 'select(.type == "suppressed" and .channel == "INTERNAL")' data/alert-events.jsonl
jq -c 'select(.channel == "INTERNAL" and .type == "resolved" and .reason == "source_paused")' data/alert-events.jsonl
jq -c 'select(.channel == "INTERNAL" and .type == "resolved" and .reason == "source_set_changed")' data/alert-events.jsonl
jq -c 'select(.rule_id | startswith("PERF:"))' data/alert-events.jsonl
jq -c 'select(.rule_id == "W1")' data/alert-events.jsonl
```

ledger 在每次成功写入时裁掉 14 天前的事件；INTERNAL 抑制行同样计入裁剪。当前 64 MiB 只是写入前 guard，不是对本批追加后文件的硬上限：单批可先写过界，后续批次将持续 fail-open，跟踪见 [ISSUE-ALERT-20260904-8f2c](../issues/alerting.md#issue-alert-20260904-8f2c--共享告警-ledger-可先写过上限后永久停录)。A1–A7、W1、D3 与 PERF 可并发写入，因此用稳定的 `data/alert-events.lock` sidecar 做 `flock`；锁等待最多 1 秒。损坏 JSON、非普通文件、锁超时、超限或写入失败都 fail-open：记错误日志并跳过本批 ledger，不覆盖原文件，不阻断通知投递或告警状态持久化。因此 ledger 是便于查询的非权威投递与抑制历史，不是 attempt、状态或 exactly-once 真源。普通通知或抑制留痕遇到损坏 JSON、非普通文件、锁超时、超限或写入失败时 fail-open：记错误日志并跳过本批 ledger，不覆盖原文件，也不撤销已完成的通知/状态动作。A7 `source_paused` 静默结案仍要求 INTERNAL 行先写成功，因为它没有用户可见通知；`source_set_changed` 则优先保留新事故可见性，INTERNAL 行写失败时仍转换状态并通知新 episode。ledger 仍只是便于查询的非权威历史，不是 attempt、状态或 exactly-once 真源。

### 已知限制 / 运维备注

- A2 rate 分支的最小样本门会在持续低量 pipeline 下产生低分母盲区：例如 15 分钟只有 3 次 prefilter 且 3 次全失败，因 `3 < min_samples 4` 不会由 A2 rate 分支 page。这是已接受的低样本取舍；持续总故障会让 items 停止产出，由 A4 items-floor 即时 page，并另有 A2 `no_success_minutes` 心跳支路兜底。排障时不要把「A2 rate 未 firing」当成 pipeline 健康的充分证据。
- A3 5xx 的 15 分钟窗依赖 access log timestamp 带 `%z` 时区偏移（生产当前输出 `+0800`）。若 A3 异常显示 `server_pv=0`，先检查 access log timestamp 是否仍含 offset；缺失时 naive timestamp 会按 UTC 解释，在 `Asia/Shanghai` 生产中错移 8 小时并把窗口内行静默排除。
- `logs/alert-check.log` 当前没有 rotation，长期会增长；`status.sh alert` 也不检查其大小。在补上有界 rotation 与状态暴露之前应人工监看文件大小；跟踪见 [ISSUE-013](../issues/cost-observability.md#issue-013--alert-checklog-无-rotationstatus-不暴露文件大小)。
- 2026-08-11 的生产快照有 152 篇微信解读达到重试上限；本次文档同步未刷新该数量。A5 状态 detail 与 `/admin` 会显示当前 frozen 数，但本轮没有为历史冻结积压新增独立 page；是否批量重试或另建 backlog notice 需在具备安全 replay 策略后单独裁决。

### serve 重启后 `/api/v1/hot` 短暂 503（预期，非故障）

serve 刚起来的那几秒到十几秒里，`/api/v1/hot`（以及 SSR 的 `/hot`）返回 **503 + `Retry-After: 2`** 是**设计行为**，不是事故。热点榜由后台刷新的候选缓存供给，请求路径永不同步计算；缓存尚未填好时接口宁可显式 503，也不返回 `200` + 空 items（[ADR-060](../adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md)：把「未就绪」编码成「没有热点」会被公共缓存放大成约 120 秒的假空结果，前端也不会重试）。

**这不是零成本的**：`/admin` 以外的 5xx 都进 A3 的用户侧 5xx 分子。冷启窗很短、A3 又要求 15 分钟内 `PV >= 20` 才让 5xx 率参与 page，所以正常重启不会把 A3 顶过线；但重启恰好撞上流量高峰时，A3 的 5xx 率会被这批 503 抬高一截——判 A3 时先看时间戳是否贴着一次 serve 重启。

判别方法（serve 的 stderr，launchd 部署下是 `logs/serve-access.err.log`；logger 名 `airadar.hot_cache`）：

| 日志行 | 含义 |
|---|---|
| `hot candidates unready (never populated); serving degraded for N.Ns` | 预热中。`N` 应在几秒到十几秒量级、且**只出现在重启后**；该行按 30 秒节流，不要按出现次数估请求数 |
| `hot candidates refreshed: <N> candidates in X.XXs` | 预热完成，此后 `/api/v1/hot` 恢复 200。没有这一行就说明刷新从未成功 |
| `hot candidate refresh failed`（带 traceback） | 刷新线程真的挂了——这才是故障 |

**持续 503 才是事故**：`unready` 行的 `serving degraded for` 持续增长、或只见 `refresh failed` 不见 `refreshed`，说明后台线程反复失败（典型是 DB 打不开或查询报错）。此时查该 traceback，并确认 serve 进程里名为 `hot-candidate-refresh` 的线程是否还在：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/api/v1/hot   # 冷启后应很快从 503 变 200
rg -n 'hot candidate' logs/serve-access.err.log | tail -n 20
```

本节只说明现象与判别，**没有为它新增告警**：短窗 503 会自愈；持续 503 会持续计入 A3 的用户侧 5xx 分子（`PV >= 20` 时参与 page），并在上面这些日志行里留下确定性痕迹。注意 healthz 支路对它是盲的——`/api/v1/healthz` 不读这个缓存。

## 用户旅程性能监控

`performance-probe` 用 Chromium 测量四条用户可感知旅程，并同时访问本机 origin 与配置的 public URL（取 `AI_RADAR_PUBLIC_URL` 环境变量；当前生产 URL 直达腾讯服务器，其他部署可经 tunnel 或代理）。明确禁用或未配置的 vantage 可退役旧状态；已经由新鲜观测建立的 firing 若只是样本窗口滑空，则保持 `in_progress`，不把“没有新样本”伪装成恢复。两个 vantage 都从部署主机发起，因此报告固定标为 **same-host provisional; not a regional SLO**，不能据此宣称 East Asia 或其他区域 SLO 达标。

| `PERF:*` 旅程 | P75 预算 | P95 预算 |
|---|---:|---:|
| `homepage.first_card` | 2000ms | 3000ms |
| `wechat.list.first_card` | 2000ms | 3000ms |
| `wechat.detail.readable` | 2000ms | 3000ms |
| `wechat.pagination.settle` | 1000ms | 1500ms |

规则 key 固定为 `PERF:<journey>:<vantage>:idle`。探针在每条旅程测量前后以非阻塞共享锁探测 `.pipeline.flock` 并读取 pipeline 持久 activity generation；只有两端都证明 pipeline 空闲且 generation 未变时，才保存该 idle 样本并让 PERF 窗口消费它。pipeline 正在运行、锁探测失败或测量期间 activity 变化时跳过该次旅程尝试：不保存对应样本、不让 non-idle 输入进入规则。PERF 不再采集或评估 busy cell，也没有 busy→idle 降级 gate、busy-specific severity/message 或共因 rollup。

每个 cell 先积累 20 个样本，再用 nearest-rank P75/P95 评估最近窗口；P75/P95 任一超预算或窗口含 hard failure 都算该窗口违规，最近 3 个逐样本前进窗口都违规才进入 firing。因而从零样本到首个可 confirmed firing 需要 `WARM_SAMPLES + CONFIRMATION_WINDOWS - 1 = 22` 条有效 idle 样本；达到确认窗后直接以 `page` severity 投递，不降为 notice。这是“上膛”时间：表示冷启动或样本清空后，cell 重新具备发出 confirmed page 的最短数据准备过程，不代表每个退化都固定延迟同样时长，更不是每 5 分钟即时 page。

2026-07-26 的 L2-4 live 证明结论：8 个 cell（4 旅程 × origin/public）都在 4.93 小时取得第 22 条样本，勉强满足预固定的 6 小时硬门槛，裕度约 1.07 小时——且该裕度会被源数量、interpret 时长或 pipeline 占比的任何上升吃掉。逐 cell 读数与推导见 `docs/plans/20260601-monitoring-alerting/` 归档与 git 历史。运维必须持续监督“每个启用 cell 从零到 22 条 ≤6h”；任一 cell 超过 6 小时都表示 idle-only + 20+3 在当前负载下不再满足时效契约，不能靠放宽门槛结案。

### Liveness、投递语义与已知限制

- LaunchAgent 的 `ProgramArguments` 经 `./run.sh performance-probe` 启动。`run.sh` 的外部进程 watchdog 在 16 分钟终止超时 probe；进程内另有 15 分钟 `SIGALRM`，负责杀 browser worker 进程组并退出，作为第二层兜底。两层都远短于 6 小时样本时效门槛。
- 单次旅程测量在父进程 primary cutoff（`timeout + startup grace`）后，基于 worker 结果发布或进程退出的**有界 readiness**（`BROWSER_WORKER_EXIT_GRACE_SECONDS`）收集结果。已接受的取舍：worker 若在 cutoff 后超过该 grace 才发布一个已判定的真实 site 故障，该故障会被归为 `worker_unavailable` infra、不进入 22 样本窗口。放宽等待会违反上面两层 watchdog 门槛；真正静默的 worker 仍确定性进入 infra。
- PERF 通知契约是 **at-least-once + `im-notify` dedup**，不是 exactly-once。发送和状态持久化无法原子提交；状态机在发送前持久化 notification nonce，同一意图的 crash retry 复用 nonce，severity 升级与最终恢复各有独立 nonce。真实 sender 把 rule/severity/event/nonce/episode identity 交给 `im-notify` 的持久 signature ledger，抑制同一意图的重复可见消息。`data/alert-events.jsonl` 只是成功投递历史，不承担去重权威。
- 生命周期脚本按单操作员设计：并发对同一服务执行 install + uninstall 会产生最终状态竞争，别这么用。

| 资产 | 默认路径 | 保留策略 |
|---|---|---|
| 旅程样本 | `logs/performance/journey-samples.jsonl` | 每次写入裁剪 14 天前样本 |
| `PERF:*` 状态 | `logs/performance/alert-state.json` | firing / resolved、窗口 streak 与冷却状态 |
| 性能诊断证据 | `logs/performance/evidence/` | 每次写入清理 14 天前 JSON 证据 |
| remediation 状态/锁 | `logs/performance/remediation-state.json`、`logs/performance/remediation.lock` | 防止同一 firing episode 重复处理或并发启动 |
| remediation 证据 | `logs/performance/remediation-evidence/` | worker 成功、失败与边界拒绝记录 |

### 安装 5 分钟 launchd 调度

先用 `--help` 核对当前版本给出的 launchd 安装入口，再手工冒烟：

```bash
./run.sh performance-probe --help
./run.sh performance-remediate --help
./run.sh performance-probe --origin-url http://127.0.0.1:8010 --public-url https://news.aiplanet.live
```

先只安装 probe，**不启用 remediation cron**——启用 gate 的全文与可执行形式在下面「安装 remediation cron」那段，本节不复述。

probe 使用专属 `live.aiplanet.ai-radar.performance-probe.plist`，`StartInterval=300`、`RunAtLoad=true`，并始终经 `./run.sh performance-probe` 进入 external watchdog。`install.sh` 以 per-file regular plist 放置到 `~/Library/LaunchAgents/`，按 destination + label/path ownership fail closed，并迁移精确指向本仓库 generated plist 的 legacy symlink；它不会编辑共享 crontab。pipeline 自身仍由既有 `*/15` user crontab 调度，未迁移。

当前部署状态由 [services.md §服务](services.md#服务) 维护。以下命令描述安装后的目标 lifecycle，不表示 probe 当前正在运行；恢复前还必须处理默认 origin 仍为 `http://127.0.0.1:8000` 的 ISSUE-017。

```bash
./install.sh performance-probe
./status.sh performance-probe
# 移除时：
./uninstall.sh performance-probe
```

### 安装 remediation cron（启用 gate 全文）

**这是 remediation 启用条件的唯一全文**，本仓其余各处（[services.md §服务](services.md#服务) 等）只应指向这里、不复述。homepage `hard_failure=true` 的已知假阳性虽已修复，但仍必须以部署后的实测样本为准：先手工 probe，再用最新 homepage idle 样本（`hard_failure=false`）和权威 page lifecycle（homepage `PERF:*` 非 firing）做可失败 gate；两项都满足后才手工运行一次 remediation。只有这次手工运行返回 0，才继续安装独立 cron：

```bash
(
  set -e
  latest_homepage="$(jq -sc '[.[] | select(.journey == "homepage.first_card" and .load_class == "idle")] | last // error("no homepage idle sample")' logs/performance/journey-samples.jsonl)"
  test "$(jq -r '.hard_failure' <<< "$latest_homepage")" = false
  jq -e '
    [to_entries[] | select(.key | startswith("PERF:homepage.first_card:"))] as $rows
    | ($rows | length) > 0
      and ($rows | all((.value.lifecycles.page.state? // .value.state? // "ok") != "firing"))
  ' logs/performance/alert-state.json > /dev/null
  ./run.sh performance-remediate

  repo=$PWD
  existing="$(mktemp)"
  read_error="$(mktemp)"
  updated="$(mktemp)"
  trap 'rm -f "$existing" "$read_error" "$updated"' EXIT
  if ! crontab -l > "$existing" 2> "$read_error"; then
    if grep -q '^crontab: no crontab for ' "$read_error"; then
      : > "$existing"
    else
      cat "$read_error" >&2
      exit 1
    fi
  fi
  { sed '/# ai-radar-performance-remediate$/d' "$existing"
    printf '25 * * * * cd "%s" && PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" ./run.sh performance-remediate >> logs/performance-remediate-cron.log 2>&1 # ai-radar-performance-remediate\n' "$repo"
  } > "$updated"
  crontab "$updated"
  test "$(crontab -l | grep -c '# ai-radar-performance-remediate$')" = 1
  crontab -l | grep '# ai-radar-performance-remediate$'
)
```

`performance-remediate` **只消费 page incident**：对新状态它直接读取权威的 `lifecycles.page` firing episode，不信任顶层兼容投影；只有无 `lifecycles` 的旧 flat state 才回退到顶层，缺 severity 时按 page 兼容。它不会二次判断上游 hard failure 的真伪——这正是上面那道启用 gate 存在的理由。worker 以 nonblocking lock 保证单 active，单次最长 3600 秒；Codex 固定使用 `--ignore-user-config --sandbox workspace-write` 和 `approval_policy="never"`，只允许隔离 worktree 写入。worker 不获得 push、deploy、launchctl 或生产数据库写入口；任何 preflight 无法证明边界时 fail closed、告警并留证。成功结果是 worktree 内的 detached 本地 candidate commit 和摘要，仍需站长审阅与显式授权后才能进入部署流程。

### 边缘缓存与旅程延迟

当前生产 `news.aiplanet.live` 直解腾讯源站，不经过 Cloudflare 代理，因此 public vantage 现阶段也不受 `AI Radar short public pagination TTL` Cache Rule 影响，不能用缺少 `CF-Cache-Status` 或未见 HIT 判断缓存故障。历史上经 Cloudflare 代理时，安全分页变体的边缘命中曾把翻页 API 从 3-5s 降到 0.5-1.4s；若将来恢复代理，再先验证同一 URL 第二次请求为 `CF-Cache-Status: HIT`、`q=` 请求为 `DYNAMIC` + `private, no-store`，再把 public/origin 差异用于区分缓存回退与后端退化。Cache Rule、当前旁路状态、origin 头契约与完整验证命令见 [services.md §Cloudflare Cache Rule](services.md#cloudflare-cache-rulepublic-分页边缘缓存)。无论是否代理，完整浏览器旅程仍以 idle-only probe 样本为准，不能从 API 单点延迟直接推断旅程 P95。

## `im-notify` 飞书双通道

1. 在 `ai-agent-config` 仓库运行 `./im-notify/install.sh`，确认部署机存在 `~/.local/bin/im-notify`。`alert` 的 tracked launchd 模板已把 `~/.local/bin` 加入作业 `PATH`。
2. 在飞书中为 page 和 notice 准备对应 webhook：`ALERT` 承接 page 红线，`NOTIFICATION` 承接 notice 低打扰通知。
3. 把两个 webhook URL 写入项目根目录 `.env` 或 `~/.claude/.env`，不要提交真实 URL：

```bash
FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
FEISHU_GENERAL_NOTIFICATION_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

4. 不发送生产告警的 preflight：下面只检查可执行文件、实际 env 读取链是否同时命中两个 key，以及发送路由的 mock 测试；不调用真实 `im-notify`。

```bash
test -x "$HOME/.local/bin/im-notify"
bash -lc 'source deploy/lib/services.sh; if missing="$(alert_webhook_missing_keys)"; then echo "missing: $missing"; exit 1; else echo "both webhook keys configured"; fi'
uv run pytest tests/test_admin_alerts.py -q -k 'send_alert_message_calls_im_notify_alert_without_dedup or send_alert_message_routes_notice_without_alert_flag'
```

5. 安装周期告警服务：

```bash
./install.sh alert
```

`install.sh alert` 会从当前进程环境、`.env` 或 `~/.claude/.env` 读取两个 key。任缺一个都会拒绝生成部分 launchd 配置：交互式终端会逐个询问并写入 `.env`，非交互环境跳过 alert 安装并在 summary 列出缺失 key。已加载的 alert job 也会在重跑安装时被 bootout/bootstrap，使新 env 生效。launchd 不继承交互式 zsh 的临时 `export`；只 export 而不重跑安装，后台任务拿不到新值。安装后用下面命令只打印键名，确认 plist 同时带两个 webhook，不泄露 URL：

```bash
plutil -p deploy/launchd/ai-radar-alert.plist \
  | rg -o 'FEISHU_GENERAL_(ALERT|NOTIFICATION)_WEBHOOK' \
  | sort -u
```

测试或自定义数据库路径时，`install.sh alert` 也会把已设置的 `AI_RADAR_DB` 写入同一个 `EnvironmentVariables`，让 launchd job 与手工 `./run.sh admin alert-check` 使用同一份 SQLite。

如果任一 webhook 变更，重跑安装即会重新生成并重载 plist：

```bash
./install.sh alert
```

任一 webhook 缺失时，首先跑上面的无发送 preflight 确认是 `ALERT` 还是 `NOTIFICATION` key 缺失，然后补齐并重跑 `./install.sh alert`。如果两个 key 都在但运行时仍失败，检查 `~/.local/bin/im-notify` 可执行性、plist 中两个键名、`logs/alert-check.err.log` 的 `im-notify` 退出状态，并按 receipt 的 `channel=ALERT|NOTIFICATION` 判断故障通道。运行时 `im-notify` 不可执行、超时或非零退出时，firing 不会进入 cooldown，下轮会重试；本轮告警进程与状态持久化仍继续。不要为诊断而直接跑 `./run.sh admin alert-check`，当前状态如果恰好触发转换，它会发送真实生产消息。

## 验证 admin 鉴权

生产鉴权由 origin 自己完成（见文首），不依赖任何边缘代理。把 URL 换成实际生产 hostname 后，下面四条读数分别应为 `403 / 403 / 200 / 200`——第二条是伪造旧 header 的阴性对照，它必须被拒：

```bash
public="https://${AI_RADAR_SITE_DOMAIN}"
curl -sS -o /dev/null -w 'no_token=%{http_code}\n' "$public/api/v1/admin/metrics"
curl -sS -o /dev/null -w 'legacy_cf_header=%{http_code}\n' -H 'Cf-Access-Jwt-Assertion: x' "$public/api/v1/admin/metrics"
curl -sS -o /dev/null -w 'token_api=%{http_code}\n' -H "X-Admin-Token: $AI_RADAR_ADMIN_TOKEN" "$public/api/v1/admin/metrics"
curl -sS -o /dev/null -w 'token_page=%{http_code}\n' -H "X-Admin-Token: $AI_RADAR_ADMIN_TOKEN" "$public/admin"
```

`/docs`、`/redoc`、`/openapi.json` 应为 404。

## Cloudflare Access（历史）

Cloudflare Access 曾是设计中的公网鉴权边界，origin 只做 header 存在性兜底。当前生产走 EdgeOne、origin 自行验 token（见上节），本节只为解释旧文档里的引用而保留；控制台配置步骤与旧验证块已删除，不再是待恢复的目标拓扑。

## 常用命令

```bash
./status.sh
./run.sh performance-probe --origin-url http://127.0.0.1:8010 --public-url https://news.aiplanet.live
tail -n 50 logs/serve-access.log
tail -n 50 logs/alert-check.log
tail -n 50 logs/alert-check.err.log
tail -n 8 logs/performance/journey-samples.jsonl
```

⚠ 上面这些是只读的；`./run.sh admin alert-check` **不是**——它会发真实生产消息，别拿它做诊断（见 [§im-notify 飞书双通道](#im-notify-飞书双通道)末段）。
