# 运维监控与告警 Runbook

> Mutable snapshot. 面向 AI Radar 运维者：怎么看 `/admin`、怎么处理告警、怎么配置飞书与 Cloudflare Access。
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

`/admin` 和 `/admin/usage` 是运维面板，不挂公开导航。本机 `127.0.0.1` / `::1` / `localhost` 的本地 bypass 默认**关闭**，仅在显式设置 `AI_RADAR_ADMIN_ALLOW_LOCAL=1/true/yes` 时放行。**已知限制（本文其余各处只引用这一条，不复述）**：应用对公网请求只检查 `Cf-Access-Jwt-Assertion` 是否非空，**不验签**；这只有在请求先经过 Cloudflare Access 时才是有效边界。当前生产 `news.aiplanet.live` 直解腾讯源站、未经过 Cloudflare，2026-08-12 实测无 header 为 403、伪造 header 为 200，因此当前 admin 不能视为已认证入口；开放修复见 [deploy issue](../issues/deploy.md#open-2026-08-12当前生产-admin-入口绕过-cloudflare-access).

## Dashboard 怎么看

| 板块 | 口径 | 用法 |
|---|---|---|
| 用户量 | access log 过滤 bot/static/scanner 后的 PV/UV；`raw_unique_ips` 作为上界参考 | 看真实用户访问是否骤降，结合 5xx 率判断是否用户侧故障 |
| 文章摄取 | 今日 items 增量、最新 fetch 插入/失败、最近 curation run | 看内容是否仍在进入系统；fetch 失败率高或今日增量低会触发 A4 |
| Pipeline 阶段健康 | fetch/prefilter/scoring/enrich/curate 的处理量、错误率、P50/P95；prefilter P95 使用最近 2 小时滑动窗口，避免已恢复后旧慢样本保留到午夜 | 定位是哪一阶段异常；日志中的 `score` 已归一为 dashboard 的 `scoring` |
| LLM 已记录用量（`/admin/usage`） | 滚动 30 天 `llm_usage` 记录行的成本三态、来源单价、cache 覆盖、分阶段/Provider/模型/日聚合与前一等长窗口比较 | 定位 Top 驱动；跨窗金额统一按当前费率、cache 全未命中重算，真实 cache 事实仍用于各窗记录行金额 |
| 当前告警 | A1–A7 当前状态；D3 定价提醒不进入 page lifecycle | 先看故障类别，再看具体对象和下一步动作 |

时间口径固定为 `Asia/Shanghai`。access log 当前写入 `logs/serve-access.log`，pipeline 日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`。

`/admin/usage` 不读取历史 `cost_usd` 列：它使用受管定价 catalog 查询时派生成本。无定价的 pair 显示「未定价」，cache token 拆分未采集时显示「未采集」，命中率显示「无数据」，不会用 0 代替。可用 `AI_RADAR_USD_CNY` 调整人民币投影汇率；`AI_RADAR_LLM_PRICING_JSON` 已退役，必须从运行环境移除。

## 告警规则

| severity | 用途 | 消息 / 投递 |
|---|---|---|
| `page` | 需要立即关注的事故 | 🔴；`im-notify --alert` → `ALERT` webhook（`FEISHU_GENERAL_ALERT_WEBHOOK`） |
| `notice` | 需要知道、但无需立即起身的退化 | 🟡；`im-notify` → `NOTIFICATION` webhook（`FEISHU_GENERAL_NOTIFICATION_WEBHOOK`） |

firing 与 resolved 都沿该 episode 所在 severity 的通道投递；不再把所有规则当成同一个 page 级别。

| 规则 | 故障类别 | 典型含义 | 处置动作 |
|---|---|---|---|
| A1 | 上游模型不可用 | DeepSeek/OpenAI/GLM/ARK 返回 endpoint/model/权限/余额类错误；`schema validation failed` 已排除 | 查 provider 控制台余额、模型权限、API key；必要时切换 provider 或充值 |
| A2 | 阶段错误率/耗时异常 | prefilter/scoring/enrich 的错误 numerator/denominator **各自只取最近 15 分钟**；样本数分别至少为 `4/4/2` 才让错误率支路参与 page。独立的 P95 与**超过 120 分钟没有成功 pipeline**支路不受该样本门影响。prefilter 等后台 LLM 阶段的 P95 仍用最近 2 小时口径，只在持续达到真挂起量级时 page。SKIP 日志表示 pipeline 已在运行，不单独视为故障 | 查 `logs/pipeline-*.log` 的失败阶段；必要时手动跑单阶段复现 |
| A3 | 网站用户侧异常 | `/admin` 以外用户访问的 5xx numerator 与 PV denominator **同取最近 15 分钟**，且 `PV >= 20` 时 5xx 率才参与 page；无法证明在窗口内的日志行不计入。healthz 主动探测从已安装 serve plist 的 `ProgramArguments` 解析端口，连续失败 2 次是独立 page 支路，计数跨轮持久化于 `data/alert-state.json` | 查 `logs/serve-access.err.log`、`logs/serve-access.log`、`./status.sh serve tunnel`；确认本地 serve 健康 |
| A4 | 文章摄取骤降 | 只有 fetch 失败率高、但 items 仍正常时是 `notice`；今日 items 增量低于按当日已过分钟缩放的 floor 时是 `page`，两者同时命中也是 `page` | 按 error 分组读 `logs/pipeline-*.log` 最新一轮的 FAIL 行，见下方「出网 selector 的 preflight 与实际 route」；X 源走官方 `api.x.com`（另查 bearer token 与配额），微信源走 Mp2RSS + Wechat2RSS 双跑（见 [wechat-ingestion.md](wechat-ingestion.md)） |
| A5 | 微信解读产出停滞 | 解读启用、4 小时无成功解读，且存在 fetched 至少 4 小时、仍符合重试资格的微信 pending 时 page；无近期成功且 pending 因退避/冻结归零时标为不可评估，不发「已恢复」 | 先查近 4 小时 pipeline/interpret 日志与 provider 成功/错误，再核对余额/配额；`ark-breaker.json` 只有 `opened_at` 仍在 2 小时 cooldown 内才是当前证据 |
| A6 | 已记录 LLM 调用近 24 小时成本突变 | 当前窗与基线按同一现行费率、cache 全未命中重算。阈值两档：超过 `max(¥20, 3×中位数)` 发 notice，超过 `max(¥100, 6×中位数)` 才 page。金额与次数只统计 `llm_usage` 记录行，所以**任何越线判定都是在一个下界上做的**——未写入该表的付费调用（失败链路、未接入计量的调用点）不在内，resolve 也因此不表示 attempt-level 健康。在途窗（`.pipeline.flock` 证明本轮在跑）按 `in-progress` 用下界继续判 firing 与 notice→page，下界未越线时保留既有 episode 等封口。`baseline_days` 的实际取值与「至少 3 个有记录日」的缺口见 [ISSUE-023](../issues/cost-observability.md#issue-023--a6-的至少-3-个基线日门当前不可达) | 先按消息中的 Top 驱动核查；它复用 A6 的 cache 中性已知成本聚合。未定价调用在 `/admin/usage` 单列，nominal 目录价不是账单实付 |
| A7 | 来源静默 | 逐源判定，不看全站总量：某个启用来源距最近一条 item 超过 `max(6 小时, 2×该源近 30 天平均出稿间隔)` 时进入静默候选。阈值按源缩放，因为固定 6 小时会对数天一更的来源常态误报，而被静音的告警等于没有告警。X API 来源若最近一次 timeline 读取仍在 A2 的 120 分钟 heartbeat 内、状态为 `verified` 且 pagination 已到 `checkpointed`，说明本地最近一次读取已追平持久游标：该来源保留在健康详情中并标成上游未更新，但不 page；`blocked`、`pending`、`draining`、非法、未来时间或过期 receipt 均不抑制。近 30 天不足 5 条的来源无法刻画节奏，计入「无法评估」并在消息中给出计数（firing 与非 firing 两条分支都给，2026-08-23 前只在非 firing 分支给），不按健康处理。这批来源里，累计条目已达 5 条、且静默超过其最近 5 条典型间隔两倍的判为**褪色**——一个真死掉的来源会先 firing，再随旧条目滑出 30 天窗掉出评估集，若不加区分就会在它最彻底死掉的那一刻发出 ✅「已恢复」；褪色存在时 A7 改发 🟡「转为不可评估」并点名该来源。所有仍需处置的静默来源合并为一条通知并附清单——共享上游故障会让全部来源同时静默，逐源 page 正是会让人静音它的量 | 先看 `logs/pipeline-*.log` 里该来源的 OK/FAIL 行：整批同时静默多为出网链路，见下方「出网 selector 的 preflight 与实际 route」；单源静默则查该源站点或其上游订阅服务 |

### 出网 selector 的 preflight 与实际 route（A4、A7 的处置指引都指向这里）

抓取整批失败时先读 `logs/pipeline-*.log` 最新一轮的 `=== egress preflight START/OK/FAIL ===`，再按结果分流：

1. `preflight FAIL`：运行 `check-proxy-status --format=kv`。只有 stored/effective `domain-routing`、`domain-routing-v1` identity、policy projection matched、router/三路 upstream/route attribution/overall 全部 healthy 才可重跑；缺字段、重复/畸形字段、mismatch 或 status 命令失败都不是可降级状态。
2. `preflight OK` 但请求仍失败：运行 `agent-proxy-route-audit --format=jsonl`，按 hostname 联合读取 `selected_route`、`outcome` 与 `outcome_scope`。`upstream-application + unknown` 表示线路已归因但该事件不观测应用结果，`proxy-connect + success|failure` 表示代理 CONNECT 结果，`direct-sentinel + success` 只证明受控直连哨兵。`OK` 只证明 AI Radar 接受了当时的 selector machine status，不证明后续每个请求的 route 或 upstream 成功。

应用的 `airadar.egress.audit` 是调用点审计，不是 route authority。selector-owned transport 记录已知 hostname、launch、policy identity 与本地 outcome；显式 direct 的 loopback/synthetic 请求不依赖 selector status，也不产生带 policy identity 的应用 audit。`local_outcome=request:http:*|request:error:*` 表示真实请求结果；`subprocess_env:prepared` 与 `playwright_proxy_config:prepared` 只表示本地准备完成。managed-standard-env subprocess 使用 `hostname=null`，不表示子进程已经启动，也不表示其最终访问了哪个 hostname。不要用 listener 端口探活、父进程 proxy 环境或应用 intent 反推 GCP/Tencent/direct。

预期 policy：Anthropic-owned hostname → GCP SG，且线路失败不 direct/Tencent/ZYT fallback；OpenAI/ChatGPT/X → OpenAI provider route（Tencent primary，建隧道前失败时 ZYT fallback，两者均不可用则 fail closed）；Ark/DeepSeek/RSS/news/web → direct。域名表与实际 audit 只在 system-config；判断 provider 当前档位读 `tencent_route_mode`，判断单次实际出口读 `selected_route=tencent|zyt-fallback`。`/img` 仍是 ADR-057 的独立图片代理链路，不受本 selector 改造影响；排它的故障继续走 [services.md 的图片代理诊断](services.md#图片出口代理新加坡repo-外常驻服务)。

A7 补的是 A4 看不见的那一面：A4 用全站 item 增量与 fetch 失败率判定，单个来源死亡时其余来源仍把总量顶在 floor 之上。2026-08-14 至 08-17 微信来源零入库约 73 小时期间，A4 每天分别判定 firing 9 / 36 / 59 次，而 `send A4` 在 08-14、08-15 为 0 次、08-16 为 4 次。

**A4 的投递当前不可依赖**（未闭合，机制与证据见 [issues/alerting.md ISSUE-A01](../issues/alerting.md)），所以 A7 不是它的冗余，而是唯一覆盖单源静默的规则。

D3 每轮按 provider/model 检查 unpriced、stale、due-review 与 active tariff 变化，通过 `NOTIFICATION` webhook 发送，不带 `--alert`。未定价消息给出已记录调用数/已记录调用总数，stale/due-review 指名对象，price-changed 同时给旧值与新值。相同条件的调用计数变化不会重发；首次投递失败下轮重试，解除时 `im-notify --dedup-clear` 失败会保留 re-arm 义务，间歇未出现的模型仍保留旧价格签名。处置落点是 `src/airadar/pricing.py` 的 provider/model 条目、来源、生效区间与 `verified_at`。真实生产数据截至 P2 开发时尚未出现 stale、due-review 或 unpriced，这些分支目前只有 synthetic fixture 覆盖。

### LLM 成本报表与对账

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

告警状态存储在 `data/alert-state.json`。每个 `rule_id` 内的 `page` / `notice` 有各自的 lifecycle、debounce、`since`、`last_notified` 与 30 分钟 cooldown，不会被另一 severity 的计时器节流。A4 的 `page` debounce 为 0（items-floor 首轮即 page），`notice` debounce 为 30 分钟（fetch-only 持续超窗才通知）。severity 转换沿同一个 `since` episode 递进：notice→page 只发送新的 firing，不发送中间 resolved；只有条件真正清除或证据真实降级时才结束 episode。仍在 debounce 且从未成功投递的旧 severity 可静默关闭，不伪造 resolved。firing 仅在 transport 成功后才记为 announced 并进入 cooldown；未投递成功的 pending firing 或 resolved 都在下轮重试。投递语义是 at-least-once：发送前持久化的 notification nonce 保持重试 signature 稳定，由 `im-notify` 的持久 signature dedup 抑制同一意图的用户可见重复，不宣称 exactly-once。

### 已送达通知历史

A1–A7、D3 与 PERF 共用 `data/alert-events.jsonl` 作为查询入口。**覆盖面仅限这三类**：`deploy/wechat2rss/healthcheck.sh` 那条 cron 走 `im-notify --alert --dedup-key wechat2rss-*` 直发，**不写这个 ledger**（它是外部探活脚本，不经 `alerts.py` 的 lifecycle）。所以「查最近告警」时 ledger 里没有 wechat2rss 记录不表示它没告过警，要另看该 cron 的执行与飞书 ALERT 通道。成功投递的 firing/resolved 写入对应 channel；A1/A2/A5 合并时，被 carrier 吸收的规则另写 `type=suppressed, channel=INTERNAL`，并记录 carrier、reason 与 heartbeat freshness。投递行另含 `episode_since` 与 `notification_nonce`。失败 attempt 不写入。查询推送次数必须排除 INTERNAL，查询事故数必须按 episode identity 去重。例如：

```bash
tail -n 50 data/alert-events.jsonl | jq .
jq -c 'select(.channel != "INTERNAL" and .severity == "page" and .type == "firing")' data/alert-events.jsonl
jq -c 'select(.type == "suppressed" and .channel == "INTERNAL")' data/alert-events.jsonl
jq -c 'select(.rule_id | startswith("PERF:"))' data/alert-events.jsonl
```

ledger 在每次成功写入时裁掉 14 天前的事件；INTERNAL 抑制行同样计入裁剪与 64 MiB 上限。A1–A7、D3 与 PERF 可并发写入，因此用稳定的 `data/alert-events.lock` sidecar 做 `flock`；锁等待最多 1 秒。损坏 JSON、非普通文件、锁超时、超限或写入失败都 fail-open：记错误日志并跳过本批 ledger，不覆盖原文件，不阻断通知投递或告警状态持久化。因此 ledger 是便于查询的非权威投递与抑制历史，不是 attempt、状态或 exactly-once 真源。

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

`performance-probe` 用 Chromium 测量四条用户可感知旅程，并同时访问本机 origin 与配置的 public URL（取 `AI_RADAR_PUBLIC_URL` 环境变量；当前生产 URL 直达腾讯服务器，其他部署可经 tunnel 或代理；未配置时跳过 public 视角，其历史告警状态会被自动 resolve 而非悬挂）。两个 vantage 都从部署主机发起，因此报告固定标为 **same-host provisional; not a regional SLO**，不能据此宣称 East Asia 或其他区域 SLO 达标。

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
- PERF 通知契约是 **at-least-once + `im-notify` dedup**，不是 exactly-once。发送和状态持久化无法原子提交；状态机在发送前持久化 notification nonce，同一意图的 crash retry 复用 nonce，不同 cooldown reminder / severity 往返分配新 nonce。真实 sender 把 rule/severity/event/nonce/episode identity 交给 `im-notify` 的持久 signature ledger，抑制同一意图的重复可见消息。`data/alert-events.jsonl` 只是成功投递历史，不承担去重权威。
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

## Cloudflare Access

Cloudflare Access 是经其代理部署时的公网鉴权边界；origin 侧只做存在性兜底（见文首「已知限制」）。当前生产没有经过 Cloudflare，本节是待恢复的目标拓扑，不是当前保护状态。

### 控制台配置

1. 打开 Cloudflare Zero Trust 控制台。
2. 进入「Access」→「Applications」→「Add an application」→「Self-hosted」。
3. 创建 `AI Radar Admin` application。
4. Domain 填你的 `AI_RADAR_SITE_DOMAIN` 值。
5. Path 至少覆盖：
   - `/admin*`
   - `/api/v1/admin*`
6. 添加 policy，限制允许访问的用户身份，例如指定邮箱、邮箱域、或 Cloudflare Access group。
7. 保存后，用无登录态浏览器或 curl 验证公网入口不再直接进入 dashboard。

### 验证

以下是完成 gate，不是当前生产已通过的检查。把 URL 换成实际生产 hostname 后，必须同时证明响应经过 Cloudflare edge、无凭证被拦截、伪造 origin 所信任的 header 也不能得到 `200`（为什么伪造 header 是必测项，见文首「已知限制」）；只看到无 header 的 `302/403` 会被当前直达 origin 的坏状态骗过：

```bash
(
  set -e
  public_admin="https://${AI_RADAR_SITE_DOMAIN}/admin"
  headers="$(mktemp)"
  trap 'rm -f "$headers"' EXIT
  code="$(curl -sS -D "$headers" -o /dev/null -w '%{http_code}' "$public_admin")"
  grep -qi '^cf-ray:' "$headers"
  case "$code" in 302|403) ;; *) exit 1 ;; esac
  fake_code="$(curl -sS -o /dev/null -w '%{http_code}' -H 'Cf-Access-Jwt-Assertion: x' "$public_admin")"
  test "$fake_code" != 200
)
```

origin 兜底可在当前本机 serve 端口 8010 直接验证（generic fork 改成自己的 serve 端口）：

```bash
origin=http://127.0.0.1:8010
curl -sS -o /dev/null -w 'origin_no_header_api=%{http_code}\n' "$origin/api/v1/admin/metrics"
curl -sS -o /dev/null -w 'origin_fake_header_api=%{http_code}\n' -H 'Cf-Access-Jwt-Assertion: x' "$origin/api/v1/admin/metrics"
curl -sS -o /dev/null -w 'origin_no_header_page=%{http_code}\n' "$origin/admin"
curl -sS -o /dev/null -w 'origin_fake_header_page=%{http_code}\n' -H 'Cf-Access-Jwt-Assertion: x' "$origin/admin"
```

预期依次为 `403 / 200 / 403 / 200`。

预期读数中两个 `200` 正是文首「已知限制」的表现，不复述；剩余增强也在那里。

安全注意：origin 的本地 bypass（放行 `127.0.0.1` / `::1` / `localhost`）已**默认关闭**——仅在显式设置 `AI_RADAR_ADMIN_ALLOW_LOCAL` 时生效，生产 serve 不设该变量，故即便未来 cloudflared 转发机制变化让公网请求在 origin 看起来像 `127.0.0.1`，也不会触发本地 bypass。公网无凭证访问 `/admin` 已验证为 403。

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
