# 运维监控与告警 Runbook

> Mutable snapshot. 面向 AI Radar 运维者：怎么看 `/admin`、怎么处理告警、怎么配置飞书与 Cloudflare Access。

## 入口

- Dashboard：`https://${AI_RADAR_SITE_DOMAIN}/admin`
- Metrics API：`https://${AI_RADAR_SITE_DOMAIN}/api/v1/admin/metrics`
- LLM 用量：`https://${AI_RADAR_SITE_DOMAIN}/admin/usage`
- LLM 用量 API：`https://${AI_RADAR_SITE_DOMAIN}/api/v1/admin/usage`
- 本地访问（需显式开启）：`AI_RADAR_ADMIN_ALLOW_LOCAL=1` 后 `http://127.0.0.1:8000/admin`
- Alert 命令：`./run.sh admin alert-check`
- 用户旅程探针：`./run.sh performance-probe`
- 性能候选修复 CLI（启用仍受下文 gate 约束）：`./run.sh performance-remediate --help`

`/admin` 和 `/admin/usage` 是运维面板，不是公开页面，也不挂公开导航。公网访问必须通过 Cloudflare Access。本机 `127.0.0.1` / `::1` / `localhost` 的本地 bypass 默认**关闭**——仅在显式设置 `AI_RADAR_ADMIN_ALLOW_LOCAL=1/true/yes` 时放行，便于部署验证和故障排查；生产 serve 不设该变量，origin 仅认 Cloudflare Access 的 `Cf-Access-Jwt-Assertion`（存在性校验，验签为后续增强）。

## Dashboard 怎么看

| 板块 | 口径 | 用法 |
|---|---|---|
| 用户量 | access log 过滤 bot/static/scanner 后的 PV/UV；`raw_unique_ips` 作为上界参考 | 看真实用户访问是否骤降，结合 5xx 率判断是否用户侧故障 |
| 文章摄取 | 今日 items 增量、最新 fetch 插入/失败、最近 curation run | 看内容是否仍在进入系统；fetch 失败率高或今日增量低会触发 A4 |
| Pipeline 阶段健康 | fetch/prefilter/scoring/enrich/curate 的处理量、错误率、P50/P95；prefilter P95 使用最近 2 小时滑动窗口，避免已恢复后旧慢样本保留到午夜 | 定位是哪一阶段异常；日志中的 `score` 已归一为 dashboard 的 `scoring` |
| LLM 用量（`/admin/usage`） | 滚动 30 天成本三态、来源单价、cache 覆盖、分阶段/Provider/模型/日聚合与前一等长窗口比较 | 定位 Top 驱动；两窗 cache 覆盖不一致时环比明确不可用 |
| 当前告警 | A1–A6 当前状态；D3 定价提醒不进入 page lifecycle | 先看故障类别，再看具体对象和下一步动作 |

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
| A4 | 文章摄取骤降 | 只有 fetch 失败率高、但 items 仍正常时是 `notice`；今日 items 增量低于按当日已过分钟缩放的 floor 时是 `page`，两者同时命中也是 `page` | 查 RSS / X(fedi) / 微信 Mp2RSS 源可用性、`./run.sh fetch` 输出 |
| A5 | 微信解读产出停滞 | 解读启用、4 小时无成功解读，且存在 fetched 至少 4 小时、仍符合重试资格的微信 pending 时 page；无近期成功且 pending 因退避/冻结归零时标为不可评估，不发「已恢复」 | 先查近 4 小时 pipeline/interpret 日志与 provider 成功/错误，再核对余额/配额；`ark-breaker.json` 只有 `opened_at` 仍在 2 小时 cooldown 内才是当前证据 |
| A6 | LLM 近 24 小时成本突变 | 两侧按同一现行费率、cache 全未命中重算；超过 `max(¥20, 3×中位数)` 发 notice，超过 `max(¥100, 6×中位数)` 才 page。基线少于 3 日或近 24 小时真实缺数时标为不可评估；若唯一缺口是当前上海日且 `.pipeline.lock` 仍证明本轮在运行，则暂记 `in-progress`，把已记录金额作为下界继续允许首次 firing 与 notice→page，只在下界未越线时保留既有 episode、等待封口后再确认恢复 | 按消息中使用 `julianday()` 的严格 24 小时 SQL 定位 stage/provider/model；先看 Top 驱动与调用量，nominal 目录价不是账单实付 |

D3 每轮按 provider/model 检查 unpriced、stale、due-review 与 active tariff 变化，通过 `NOTIFICATION` webhook 发送，不带 `--alert`。未定价消息给出调用数/总调用数，stale/due-review 指名对象，price-changed 同时给旧值与新值。相同条件的调用计数变化不会重发；首次投递失败下轮重试，解除时 `im-notify --dedup-clear` 失败会保留 re-arm 义务，间歇未出现的模型仍保留旧价格签名。处置落点是 `src/airadar/pricing.py` 的 provider/model 条目、来源、生效区间与 `verified_at`。真实生产数据截至 P2 开发时尚未出现 stale、due-review 或 unpriced，这些分支目前只有 synthetic fixture 覆盖。

周报入口为 `./run.sh admin cost-report [--window-days N] [--send|--dry-run]`。默认取上一上海自然周；指定 N 后取 rolling N 天。`cost-report` cron 在周一 09:17 经 `run-or-alert` 发送。日序列用 durable `items.fetched_at` 与成功 processing rows 核对逐 stage 暴露：fetch>0 要有 prefilter success；成功且判为 AI 的 prefilter candidate>0 时分别要有 score/enrich success；wechat fetch>0 要有 interpret success。任何 stage 的 error row 只证明尝试过，不算成功；所以即使同日已有别的 stage 或 usage 行，partial stall 仍会关闭环比。pipeline 日志只补轮次、fetch inserted，以及 retained 日内明确出现的计量写入失败；旧日志缺失本身不关闭已由 durable 数据确认的比较，但文案会保留漏记风险。异常日在正文顶部单列。nominal 同时给目录价估算金额与占比；总额与单篇解读前窗比较都按当前费率、cache 全未命中重算，绝对金额仍使用窗口内真实 cache 事实。单次已知成本只除以 priced+nominal 调用，不把 unpriced 当作 ¥0。unpriced 不进入金额，stale/due-review 要先复核，所有金额均不表示账单实付。

告警状态存储在 `data/alert-state.json`。每个 `rule_id` 内的 `page` / `notice` 有各自的 lifecycle、debounce、`since`、`last_notified` 与 30 分钟 cooldown，不会被另一 severity 的计时器节流。A4 的 `page` debounce 为 0（items-floor 首轮即 page），`notice` debounce 为 30 分钟（fetch-only 持续超窗才通知）。severity 转换沿同一个 `since` episode 递进：notice→page 只发送新的 firing，不发送中间 resolved；只有条件真正清除或证据真实降级时才结束 episode。仍在 debounce 且从未成功投递的旧 severity 可静默关闭，不伪造 resolved。firing 仅在 transport 成功后才记为 announced 并进入 cooldown；未投递成功的 pending firing 或 resolved 都在下轮重试。投递语义是 at-least-once：发送前持久化的 notification nonce 保持重试 signature 稳定，由 `im-notify` 的持久 signature dedup 抑制同一意图的用户可见重复，不宣称 exactly-once。

### 已送达通知历史

A1–A6、D3 与 PERF 共用 `data/alert-events.jsonl` 作为查询入口。成功投递的 firing/resolved 写入对应 channel；A1/A2/A5 合并时，被 carrier 吸收的规则另写 `type=suppressed, channel=INTERNAL`，并记录 carrier、reason 与 heartbeat freshness。投递行另含 `episode_since` 与 `notification_nonce`。失败 attempt 不写入。查询推送次数必须排除 INTERNAL，查询事故数必须按 episode identity 去重。例如：

```bash
tail -n 50 data/alert-events.jsonl | jq .
jq -c 'select(.channel != "INTERNAL" and .severity == "page" and .type == "firing")' data/alert-events.jsonl
jq -c 'select(.type == "suppressed" and .channel == "INTERNAL")' data/alert-events.jsonl
jq -c 'select(.rule_id | startswith("PERF:"))' data/alert-events.jsonl
```

ledger 在每次成功写入时裁掉 14 天前的事件；INTERNAL 抑制行同样计入裁剪与 64 MiB 上限。A1–A6、D3 与 PERF 可并发写入，因此用稳定的 `data/alert-events.lock` sidecar 做 `flock`；锁等待最多 1 秒。损坏 JSON、非普通文件、锁超时、超限或写入失败都 fail-open：记错误日志并跳过本批 ledger，不覆盖原文件，不阻断通知投递或告警状态持久化。因此 ledger 是便于查询的非权威投递与抑制历史，不是 attempt、状态或 exactly-once 真源。

### 已知限制 / 运维备注

- A2 rate 分支的最小样本门会在持续低量 pipeline 下产生低分母盲区：例如 15 分钟只有 3 次 prefilter 且 3 次全失败，因 `3 < min_samples 4` 不会由 A2 rate 分支 page。这是已接受的低样本取舍；持续总故障会让 items 停止产出，由 A4 items-floor 即时 page，并另有 A2 `no_success_minutes` 心跳支路兜底。排障时不要把「A2 rate 未 firing」当成 pipeline 健康的充分证据。
- A3 5xx 的 15 分钟窗依赖 access log timestamp 带 `%z` 时区偏移（生产当前输出 `+0800`）。如果修改 access-log format 时丢掉 offset，naive timestamp 会按 UTC 解释，在 `Asia/Shanghai` 生产中错移 8 小时，使窗口内行被静默排除、`server_pv=0`，从而关闭 A3 5xx 分支。任何日志格式变更都必须保留 `%z` 或同步增加显式时区处理与窗口测试。
- `logs/alert-check.log` 当前没有 rotation，长期会增长；`status.sh alert` 也不检查其大小。P2 保留现有服务拓扑，后续运维单元需增加有界 rotation 与状态暴露，在此之前应人工监看文件大小。
- 生产现有 152 篇微信解读已达到重试上限。A5 状态 detail 与 `/admin` 会显示 frozen 数，但本轮没有为历史冻结积压新增独立 page；是否批量重试或另建 backlog notice 需在具备安全 replay 策略后单独裁决。

## 用户旅程性能监控

`performance-probe` 用 Chromium 测量四条用户可感知旅程，并同时访问本机 origin 与经公网 tunnel 回到本站的 public URL（取 `AI_RADAR_PUBLIC_URL` 环境变量；未配置时跳过 public 视角，其历史告警状态会被自动 resolve 而非悬挂）。两个 vantage 都从部署主机发起，因此报告固定标为 **same-host provisional; not a regional SLO**，不能据此宣称 East Asia 或其他区域 SLO 达标。

| `PERF:*` 旅程 | P75 预算 | P95 预算 |
|---|---:|---:|
| `homepage.first_card` | 2000ms | 3000ms |
| `wechat.list.first_card` | 2000ms | 3000ms |
| `wechat.detail.readable` | 2000ms | 3000ms |
| `wechat.pagination.settle` | 1000ms | 1500ms |

规则 key 固定为 `PERF:<journey>:<vantage>:idle`。探针在每条旅程测量前后读取 `.pipeline.lock` 的 owner 证明与 pipeline 持久 activity generation；只有两端都证明 pipeline 空闲且 generation 未变时，才保存该 idle 样本并让 PERF 窗口消费它。pipeline 正在运行、owner 不可信或测量期间 activity 变化时跳过该次旅程尝试：不保存对应样本、不让 non-idle 输入进入规则。PERF 不再采集或评估 busy cell，也没有 busy→idle 降级 gate、busy-specific severity/message 或共因 rollup。

每个 cell 先积累 20 个样本，再用 nearest-rank P75/P95 评估最近窗口；P75/P95 任一超预算或窗口含 hard failure 都算该窗口违规，最近 3 个逐样本前进窗口都违规才进入 firing。因而从零样本到首个可 confirmed firing 需要 `WARM_SAMPLES + CONFIRMATION_WINDOWS - 1 = 22` 条有效 idle 样本；达到确认窗后直接以 `page` severity 投递，不降为 notice。这是“上膛”时间：表示冷启动或样本清空后，cell 重新具备发出 confirmed page 的最短数据准备过程，不代表每个退化都固定延迟同样时长，更不是每 5 分钟即时 page。

2026-07-26 的 L2-4 live 证明中，生产 pipeline 约 60% 时间处于运行态，idle 窗稀疏；每个启用 cell 约每 14 分钟取得 1 条 idle 样本。4 条旅程 × origin/public 共 8 个 cell 都在 296 分钟（4.93 小时）取得第 22 条样本，满足预固定的 6 小时硬门槛，但只剩约 1.07 小时裕度。这个 PASS 依赖 pipeline 不比测量时更忙：源数量、interpret 时长或单轮 pipeline 占比继续上升都会吃掉裕度。运维必须持续监督“每个启用 cell 从零到 22 条 ≤6h”；任一 cell 超过 6 小时都表示 idle-only + 20+3 在当前负载下不再满足时效契约，不能靠放宽门槛结案。

### Liveness、投递语义与已知限制

- LaunchAgent 的 `ProgramArguments` 经 `./run.sh performance-probe` 启动。`run.sh` 的外部进程 watchdog 在 16 分钟终止超时 probe；进程内另有 15 分钟 `SIGALRM`，负责杀 browser worker 进程组并退出，作为第二层兜底。两层都远短于 6 小时样本时效门槛。
- 单次旅程测量在父进程 primary cutoff（`timeout + startup grace`）后，基于 worker 结果发布或进程退出的**有界 readiness**（`BROWSER_WORKER_EXIT_GRACE_SECONDS`）收集结果。已接受的 documented limitation：worker 若在 cutoff 后**超过该 grace** 才发布已判定的真实 site 故障（需恰在超时边界发生 >grace 的 scheduler 暂停，天文级罕见），该真故障会被归为 `worker_unavailable` infra、不进入 22 样本窗口。这是"严格 bounded liveness"与"无界 scheduler pause 零丢失"不可兼得的取舍——放宽等待会违反上面两层 watchdog 门槛；真正静默的 worker 仍确定性进入 infra。
- PERF 通知契约是 **at-least-once + `im-notify` dedup**，不是 exactly-once。发送和状态持久化无法原子提交；状态机在发送前持久化 notification nonce，同一意图的 crash retry 复用 nonce，不同 cooldown reminder / severity 往返分配新 nonce。真实 sender 把 rule/severity/event/nonce/episode identity 交给 `im-notify` 的持久 signature ledger，抑制同一意图的重复可见消息。`data/alert-events.jsonl` 只是成功投递历史，不承担去重权威。
- 刻意把 probe、外部 watchdog 及其整棵进程树持续 `SIGSTOP` 超过 6 小时且不恢复，会同时冻结两层 in-tree liveness 机制；需要独立于该进程树的外部/fleet watchdog 或操作员解除冻结。这是已接受的 documented limitation。
- 生命周期脚本按单操作员使用设计，不支持并发执行同一服务的 install + uninstall；并发调用可能产生最终状态竞争。
- 恶意调用者在生命周期操作中途替换受信 `HOME` / `Library/LaunchAgents` 路径组件，超出该手动部署工具的 threat model。
- 若外来 job 刻意同时冒用本项目的精确 launchd label、精确 generated plist 路径，且本项目 destination 也存在，`launchctl` canonicalize 后无法与 genuine legacy/current job 区分。不要把 label 或路径交给不受信调用者控制。

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
./run.sh performance-probe
```

U4 发现的 homepage `hard_failure=true` 假阳性已修复：浏览器现在把首 12 条 SSR/prepaint ID 当作完整渲染列表的前缀，不再要求两者长度相等。但这不代替部署后运维验证：在手工 probe 确认 homepage `hard_failure=false` 且 homepage `PERF:*` 非 firing 前，**只安装 probe，不启用 remediation cron**。

probe 使用专属 `live.aiplanet.ai-radar.performance-probe.plist`，`StartInterval=300`、`RunAtLoad=true`，并始终经 `./run.sh performance-probe` 进入 external watchdog。`install.sh` 以 per-file regular plist 放置到 `~/Library/LaunchAgents/`，按 destination + label/path ownership fail closed，并迁移精确指向本仓库 generated plist 的 legacy symlink；它不会编辑共享 crontab。pipeline 自身仍由既有 `*/15` user crontab 调度，未迁移。

```bash
./install.sh performance-probe
./status.sh performance-probe
# 移除时：
./uninstall.sh performance-probe
```

部署包含上述修复的版本后，用手工 probe 确认 homepage `hard_failure=false`，并确认 `logs/performance/alert-state.json` 中 homepage `PERF:*` 已不处于 firing；两项都满足后，才先手工运行一次 remediation，再按需安装它自己的独立 cron：

```bash
./run.sh performance-remediate

repo=$PWD
{ crontab -l 2>/dev/null | sed '/# ai-radar-performance-remediate$/d'
  printf '25 * * * * cd "%s" && PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" ./run.sh performance-remediate >> logs/performance-remediate-cron.log 2>&1 # ai-radar-performance-remediate\n' "$repo"
} | crontab -
```

`performance-remediate` **只消费 page incident**：对新状态它直接读取权威的 `lifecycles.page` firing episode，不信任顶层兼容投影；只有无 `lifecycles` 的旧 flat state 才回退到顶层，缺 severity 时按 page 兼容。它不会二次判断上游 hard failure 的真伪，所以即使 homepage 误标缺陷已修复，仍必须以部署后 `hard_failure=false` 且 homepage page lifecycle 非 firing 作为启用条件。worker 以 nonblocking lock 保证单 active，单次最长 3600 秒；Codex 固定使用 `--ignore-user-config --sandbox workspace-write` 和 `approval_policy="never"`，只允许隔离 worktree 写入。worker 不获得 push、deploy、launchctl 或生产数据库写入口；任何 preflight 无法证明边界时 fail closed、告警并留证。成功结果是 worktree 内的 detached 本地 candidate commit 和摘要，仍需站长审阅与显式授权后才能进入部署流程。

### 边缘缓存与旅程延迟

public vantage 的旅程延迟受 Cloudflare 边缘缓存直接影响：`/`、`/wechat` 及其分页 API 的安全分页变体经 `AI Radar short public pagination TTL` Cache Rule 在边缘命中后，翻页 API 实测从 3-5s 降到 0.5-1.4s。注意这是 **API 层**改善——完整浏览器旅程 `wechat.pagination.settle` 的 settle 时间因还含渲染/交互开销，边缘缓存后单样本仍略高于 1500ms 预算，其 P95 是否达标待 idle-only probe 积累样本确认；`homepage.first_card` 同理以样本为准，不因 API 提速即判定旅程达标。评估 public 样本回归前，先确认缓存仍在生效——冷缓存或规则失效会让 public 延迟整体回升，但不代表 origin 或 pipeline 退化。验证同一 URL 第二次请求为 `CF-Cache-Status: HIT`、`q=` 请求为 `DYNAMIC` + `private, no-store`；Cache Rule 配置、origin 头契约与完整验证命令见 [services.md §Cloudflare Cache Rule](services.md#cloudflare-cache-rulepublic-分页边缘缓存)。origin vantage 不经 CF，故不反映边缘缓存效果，可用来区分"缓存回退"与"真实后端退化"。

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

Cloudflare Access 是公网真实鉴权边界；origin 只做 `Cf-Access-Jwt-Assertion` 存在性兜底，不验签。

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

公网无凭证预期返回 302/403：

```bash
curl -sS -o /tmp/ai-radar-admin-public.out -w '%{http_code}\n' "https://${AI_RADAR_SITE_DOMAIN}/admin"
```

origin 兜底预期：

```text
origin_no_header_api=403
origin_fake_header_api=200
origin_no_header_page=403
origin_fake_header_page=200
```

已知限制：origin 只检查 header 是否存在，所以任意非空 `Cf-Access-Jwt-Assertion: x` 会被 origin 放行。真实安全边界依赖 Cloudflare Access 在边缘拦截；origin 当前只通过本机/tunnel 暴露，不应直接暴露到公网。JWT 验签是后续增强项。

安全注意：origin 的本地 bypass（放行 `127.0.0.1` / `::1` / `localhost`）已**默认关闭**——仅在显式设置 `AI_RADAR_ADMIN_ALLOW_LOCAL` 时生效，生产 serve 不设该变量，故即便未来 cloudflared 转发机制变化让公网请求在 origin 看起来像 `127.0.0.1`，也不会触发本地 bypass。公网无凭证访问 `/admin` 已验证为 403。剩余增强：完成 Cloudflare Access JWT 验签 / origin token 校验（当前 origin 仅校验 `Cf-Access-Jwt-Assertion` 存在性，见上「已知限制」）。

## 常用命令

```bash
./status.sh serve tunnel pipeline alert
./run.sh admin alert-check
./run.sh performance-probe
tail -n 50 logs/serve-access.log
tail -n 50 logs/alert-check.log
tail -n 50 logs/alert-check.err.log
tail -n 8 logs/performance/journey-samples.jsonl
```
