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
| Pipeline 阶段健康 | fetch/prefilter/scoring/enrich/curate 的处理量、错误率、P50/P95、成本；prefilter P95 使用最近 2 小时滑动窗口，避免已恢复后旧慢样本保留到午夜 | 定位是哪一阶段异常；日志中的 `score` 已归一为 dashboard 的 `scoring` |
| LLM 用量（`/admin/usage`） | 独立 `data/llm_usage.db` 中的 `llm_usage` per-call 行按最近 30 天查询时聚合：每天、每模型的 calls/input tokens/output tokens，并按 prefilter/score/enrich/interpret 展示 item_id、输入字符数和样例标题 | 看 LLM 花费来自哪个阶段、哪个模型、处理了多少条/多大输入；旧 `radar.db.llm_usage` 历史会在首次初始化时复制到独立库 |
| 当前告警 | A1-A4 规则的当前状态、触发数值、处置方向 | 先看故障类别，再看具体对象和下一步动作 |

时间口径固定为 `Asia/Shanghai`。access log 当前写入 `logs/serve-access.log`，pipeline 日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`。

`/admin/usage` 的成本列默认显示 0，因为 token 单价会随供应商和模型变化。需要估算美元成本时，在运行环境设置 `AI_RADAR_LLM_PRICING_JSON`，格式为每百万 token 价格：

```bash
AI_RADAR_LLM_PRICING_JSON='{"deepseek-v4-pro":{"input_per_million_tokens_usd":0.14,"output_per_million_tokens_usd":0.28}}'
```

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
| A3 | 网站用户侧异常 | `/admin` 以外用户访问的 5xx numerator 与 PV denominator **同取最近 15 分钟**，且 `PV >= 20` 时 5xx 率才参与 page；无法证明在窗口内的日志行不计入。healthz 主动探测连续失败 2 次是独立 page 支路，计数跨轮持久化于 `data/alert-state.json` | 查 `logs/serve-access.err.log`、`logs/serve-access.log`、`./status.sh serve tunnel`；确认本地 serve 健康 |
| A4 | 文章摄取骤降 | 只有 fetch 失败率高、但 items 仍正常时是 `notice`；今日 items 增量低于按当日已过分钟缩放的 floor 时是 `page`，两者同时命中也是 `page` | 查 RSS / X(fedi) / 微信 Mp2RSS 源可用性、`./run.sh fetch` 输出 |

告警状态存储在 `data/alert-state.json`。每个 `rule_id` 内的 `page` / `notice` 有各自的 lifecycle、debounce、`since`、`last_notified` 与 30 分钟 cooldown，不会被另一 severity 的计时器节流。A4 的 `page` debounce 为 0（items-floor 首轮即 page），`notice` debounce 为 30 分钟（fetch-only 持续超窗才通知）。severity 转换时，已成功 announced 的旧 episode 先在原通道 resolved，再在新通道 firing；仍在 debounce 且从未成功投递的旧 episode 静默关闭，不伪造 resolved。firing 仅在 transport 成功后才记为 announced 并进入 cooldown；失败会在下轮重试。resolved 是 best-effort，失败不重试。

### 已送达通知历史

A1–A4 与 PERF 共用 `data/alert-events.jsonl` 作为查询入口。它是 notification-only ledger：只有 transport 返回成功的 firing / resolved 才每次追加一行，不记失败 attempt。字段为 `ts`、`rule_id`、`severity`、`type`、`detail`、`values`、`channel`；PERF busy rollup 的子 cell 在 `values.cells` 中。例如：

```bash
tail -n 50 data/alert-events.jsonl | jq .
jq -c 'select(.severity == "page" and .type == "firing")' data/alert-events.jsonl
jq -c 'select(.rule_id == "PERF:rollup:busy")' data/alert-events.jsonl
```

ledger 在每次成功写入时裁掉 14 天前的事件。A1–A4 与 PERF 可并发写入，因此用稳定的 `data/alert-events.lock` sidecar 做 `flock`；锁等待最多 1 秒。读取前有 64 MiB 成本熔断上限。损坏 JSON、非普通文件、锁超时、超限或写入失败都 fail-open：记错误日志并跳过本批 ledger，不覆盖原文件，不阻断通知投递或告警状态持久化。因此 ledger 是便于查询的非权威已送达历史，不是 attempt 或状态真源。

### 已知限制 / 运维备注

- A2 rate 分支的最小样本门会在持续低量 pipeline 下产生低分母盲区：例如 15 分钟只有 3 次 prefilter 且 3 次全失败，因 `3 < min_samples 4` 不会由 A2 rate 分支 page。这是已接受的低样本取舍；持续总故障会让 items 停止产出，由 A4 items-floor 即时 page，并另有 A2 `no_success_minutes` 心跳支路兜底。排障时不要把「A2 rate 未 firing」当成 pipeline 健康的充分证据。
- A3 5xx 的 15 分钟窗依赖 access log timestamp 带 `%z` 时区偏移（生产当前输出 `+0800`）。如果修改 access-log format 时丢掉 offset，naive timestamp 会按 UTC 解释，在 `Asia/Shanghai` 生产中错移 8 小时，使窗口内行被静默排除、`server_pv=0`，从而关闭 A3 5xx 分支。任何日志格式变更都必须保留 `%z` 或同步增加显式时区处理与窗口测试。

## 用户旅程性能监控

`performance-probe` 用 Chromium 测量四条用户可感知旅程，并同时访问本机 origin 与经公网 tunnel 回到本站的 public URL（取 `AI_RADAR_PUBLIC_URL` 环境变量；未配置时跳过 public 视角，其历史告警状态会被自动 resolve 而非悬挂）。两个 vantage 都从部署主机发起，因此报告固定标为 **same-host provisional; not a regional SLO**，不能据此宣称 East Asia 或其他区域 SLO 达标。

| `PERF:*` 旅程 | P75 预算 | P95 预算 |
|---|---:|---:|
| `homepage.first_card` | 2000ms | 3000ms |
| `wechat.list.first_card` | 2000ms | 3000ms |
| `wechat.detail.readable` | 2000ms | 3000ms |
| `wechat.pagination.settle` | 1000ms | 1500ms |

规则 key 为 `PERF:<journey>:<vantage>:<load_class>`。探针在每条旅程测量前后读取 `.pipeline.lock/pid`：两次都确认同一个运行中 pipeline 时记为 `busy`，两次都无锁时记为 `idle`，死 PID、坏锁或测量期间状态变化记为 `unknown`。idle 与 busy 独立评估，unknown 只留样本、不进入合规窗口。

每个规则先积累 20 个样本，再用 nearest-rank P75/P95 评估最近窗口；P75/P95 任一超预算或窗口含 hard failure 都算该窗口违规，最近 3 个逐样本前进窗口都违规才进入 firing。按建议的每小时 cadence，且同一 `vantage × load_class` 每小时都恰好取得一个有效样本时，从零样本到 confirmed firing 的理论最短时间约 22 小时（20 个 warm samples + 2 个前进窗口）。idle/busy 分流或 unknown 样本会继续拉长确认时间，因此 22 小时不是检测延迟上界，更不能按分钟级告警理解。

busy cell 只在同一 `(journey, vantage)` 的 idle cell 同时满足两个条件时降为 `notice`：idle 至少有 `WARM_SAMPLES + CONFIRMATION_WINDOWS - 1 = 22` 个样本，且当前 not-firing。idle 也 firing、不存在或少于 22 个样本时都 fail-closed 保留 `page`；idle cell 自身 firing 始终是 `page`。本轮**只移除了 `public → origin` 的跨-vantage gate**：origin 无法覆盖 Cloudflare/tunnel 公网失败面，所以 public busy 不会因 origin 干净而降级；它仍由自己的 public idle cell 把关。

同一轮所有经上述 gate 降级的 busy notice cell（一条或多条）会合并成唯一 `PERF:rollup:busy` notice，明细与最严重子项保留在消息及 `values.cells`。个体 busy notice 不再各自进入状态机或 ledger；page 级 PERF cell 仍独立投递，不并入 rollup。全部 notice busy 子项恢复时，rollup 以同一 lifecycle 发一条 resolved。

| 资产 | 默认路径 | 保留策略 |
|---|---|---|
| 旅程样本 | `logs/performance/journey-samples.jsonl` | 每次写入裁剪 14 天前样本 |
| `PERF:*` 状态 | `logs/performance/alert-state.json` | firing / resolved、窗口 streak 与冷却状态 |
| 性能诊断证据 | `logs/performance/evidence/` | 每次写入清理 14 天前 JSON 证据 |
| remediation 状态/锁 | `logs/performance/remediation-state.json`、`logs/performance/remediation.lock` | 防止同一 firing episode 重复处理或并发启动 |
| remediation 证据 | `logs/performance/remediation-evidence/` | worker 成功、失败与边界拒绝记录 |

### 安装每小时调度

先用 `--help` 核对当前版本内置的 cron 样例，再手工冒烟：

```bash
./run.sh performance-probe --help
./run.sh performance-remediate --help
./run.sh performance-probe
```

U4 发现的 homepage `hard_failure=true` 假阳性已修复：浏览器现在把首 12 条 SSR/prepaint ID 当作完整渲染列表的前缀，不再要求两者长度相等。但这不代替部署后运维验证：在手工 probe 确认 homepage `hard_failure=false` 且 homepage `PERF:*` 非 firing 前，**只安装 probe，不启用 remediation cron**。下面命令通过 marker 替换旧 probe 行，重复执行保持幂等，并为 cron 显式设置 `uv` / `codex` 所需 PATH：

```bash
repo=$PWD
{ crontab -l 2>/dev/null | sed '/# ai-radar-performance-probe$/d'
  printf '17 * * * * cd "%s" && PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" ./run.sh performance-probe >> logs/performance-probe-cron.log 2>&1 # ai-radar-performance-probe\n' "$repo"
} | crontab -
```

部署包含上述修复的版本后，用手工 probe 确认 homepage `hard_failure=false`，并确认 `logs/performance/alert-state.json` 中 homepage `PERF:*` 已不处于 firing；两项都满足后，才先手工运行一次 remediation，再安装排在 probe 之后的 cron：

```bash
./run.sh performance-remediate

repo=$PWD
{ crontab -l 2>/dev/null | sed '/# ai-radar-performance-remediate$/d'
  printf '25 * * * * cd "%s" && PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" ./run.sh performance-remediate >> logs/performance-remediate-cron.log 2>&1 # ai-radar-performance-remediate\n' "$repo"
} | crontab -
```

`performance-remediate` **只消费 page incident**：对新状态它直接读取权威的 `lifecycles.page` firing episode，不信任顶层兼容投影；只有无 `lifecycles` 的旧 flat state 才回退到顶层，缺 severity 时按 page 兼容。因此 `PERF:rollup:busy` notice 不会启动 remediation。它不会二次判断上游 hard failure 的真伪，所以即使 homepage 误标缺陷已修复，仍必须以部署后 `hard_failure=false` 且 homepage page lifecycle 非 firing 作为启用条件。worker 以 nonblocking lock 保证单 active，单次最长 3600 秒；Codex 固定使用 `--ignore-user-config --sandbox workspace-write` 和 `approval_policy="never"`，只允许隔离 worktree 写入。worker 不获得 push、deploy、launchctl 或生产数据库写入口；任何 preflight 无法证明边界时 fail closed、告警并留证。成功结果是 worktree 内的 detached 本地 candidate commit 和摘要，仍需站长审阅与显式授权后才能进入部署流程。

### 边缘缓存与旅程延迟

public vantage 的旅程延迟受 Cloudflare 边缘缓存直接影响：`/`、`/wechat` 及其分页 API 的安全分页变体经 `AI Radar short public pagination TTL` Cache Rule 在边缘命中后，翻页 API 实测从 3-5s 降到 0.5-1.4s。注意这是 **API 层**改善——完整浏览器旅程 `wechat.pagination.settle` 的 settle 时间因还含渲染/交互开销，边缘缓存后单样本仍略高于 1500ms 预算，其 P95 是否达标待 hourly probe 积累样本确认；`homepage.first_card` 同理以样本为准，不因 API 提速即判定旅程达标。评估 public 样本回归前，先确认缓存仍在生效——冷缓存或规则失效会让 public 延迟整体回升，但不代表 origin 或 pipeline 退化。验证同一 URL 第二次请求为 `CF-Cache-Status: HIT`、`q=` 请求为 `DYNAMIC` + `private, no-store`；Cache Rule 配置、origin 头契约与完整验证命令见 [services.md §Cloudflare Cache Rule](services.md#cloudflare-cache-rulepublic-分页边缘缓存)。origin vantage 不经 CF，故不反映边缘缓存效果，可用来区分"缓存回退"与"真实后端退化"。

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
