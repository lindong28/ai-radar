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

| 规则 | 故障类别 | 典型含义 | 处置动作 |
|---|---|---|---|
| A1 | 上游模型不可用 | DeepSeek/OpenAI/GLM/ARK 返回 endpoint/model/权限/余额类错误；`schema validation failed` 已排除 | 查 provider 控制台余额、模型权限、API key；必要时切换 provider 或充值 |
| A2 | 阶段错误率/耗时异常 | prefilter/scoring/enrich 错误率超阈值，或**超过 120 分钟没有成功 pipeline**。prefilter 是后台非用户可见阶段，其 P95 是外部 LLM 单次调用的尾延迟、小样本下噪声大且总能自愈，故**不靠延迟分页**：阈值抬到「真挂起」地板 25s，只有大量调用持续 25s+ 才触发（旧 8478ms 在 06-24 切 ARK-first 后反复贴线误报，已废弃）；prefilter 真故障由错误率与「无成功轮次」兜底。SKIP 日志=「pipeline 已在运行」=存活，不计故障，长任务进行中不会告警；只有真停产/僵尸锁（长时间无成功且 SKIP 堆积）才触发 | 查 `logs/pipeline-*.log` 的失败阶段；必要时手动跑单阶段复现 |
| A3 | 网站用户侧异常 | `/admin` 以外用户访问出现高 5xx 率，**或** healthz 主动探测连续失败（每轮 alert-check 主动 GET 本地 `/api/v1/healthz`，连续失败计数跨轮持久化于 `data/alert-state.json`） | 查 `logs/serve-access.err.log`、`logs/serve-access.log`、`./status.sh serve tunnel`；确认本地 serve 健康 |
| A4 | 文章摄取骤降 | fetch 失败率高，**或**今日 items 增量低于**按当日已过时间缩放的**基线（`daily_inserted_floor` 按当日已过分钟 / 1440 缩放，避免清晨累积未满时假阳） | 查 RSS / X(fedi) / 微信 Mp2RSS 源可用性、`./run.sh fetch` 输出 |

告警状态存储在 `data/alert-state.json`。同一规则 firing 后有 30 分钟冷却；恢复时发送 resolved。

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

`performance-remediate` 读取状态机标为 confirmed 的 `PERF:*` incident；它不会二次判断上游 hard failure 的真伪，所以即使 homepage 误标缺陷已修复，仍必须以部署后 `hard_failure=false` 且 homepage `PERF:*` 非 firing 作为启用条件。worker 以 nonblocking lock 保证单 active，单次最长 3600 秒；Codex 固定使用 `--ignore-user-config --sandbox workspace-write` 和 `approval_policy="never"`，只允许隔离 worktree 写入。worker 不获得 push、deploy、launchctl 或生产数据库写入口；任何 preflight 无法证明边界时 fail closed、告警并留证。成功结果是 worktree 内的 detached 本地 candidate commit 和摘要，仍需站长审阅与显式授权后才能进入部署流程。

### 边缘缓存与旅程延迟

public vantage 的旅程延迟受 Cloudflare 边缘缓存直接影响：`/`、`/wechat` 及其分页 API 的安全分页变体经 `AI Radar short public pagination TTL` Cache Rule 在边缘命中后，翻页 API 实测从 3-5s 降到 0.5-1.4s。注意这是 **API 层**改善——完整浏览器旅程 `wechat.pagination.settle` 的 settle 时间因还含渲染/交互开销，边缘缓存后单样本仍略高于 1500ms 预算，其 P95 是否达标待 hourly probe 积累样本确认；`homepage.first_card` 同理以样本为准，不因 API 提速即判定旅程达标。评估 public 样本回归前，先确认缓存仍在生效——冷缓存或规则失效会让 public 延迟整体回升，但不代表 origin 或 pipeline 退化。验证同一 URL 第二次请求为 `CF-Cache-Status: HIT`、`q=` 请求为 `DYNAMIC` + `private, no-store`；Cache Rule 配置、origin 头契约与完整验证命令见 [services.md §Cloudflare Cache Rule](services.md#cloudflare-cache-rulepublic-分页边缘缓存)。origin vantage 不经 CF，故不反映边缘缓存效果，可用来区分"缓存回退"与"真实后端退化"。

## `im-notify` 飞书告警通道

1. 在 `ai-agent-config` 仓库运行 `./im-notify/install.sh`，确认部署机存在 `~/.local/bin/im-notify`。`alert` 的 tracked launchd 模板已把 `~/.local/bin` 加入作业 `PATH`。
2. 在飞书群里打开「群设置」→「机器人」→「添加机器人」→「自定义机器人」。
3. 复制 webhook URL，写入项目根目录 `.env` 或 `~/.claude/.env`，不要提交真实 URL：

```bash
FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

4. 在部署机执行 preflight：

```bash
./run.sh admin alert-check
```

5. 确认群里收到以 `【AI Radar】` 开头、包含「故障类别」「具体故障对象/数值」「处置方向」的消息。该前缀来自 `alerts.py` 的 `ALERT_SOURCE` 常量，用于在多个项目共用同一 webhook 时让收件人区分告警来源。消息内容、firing / resolved 与 30 分钟冷却仍由 AI Radar 状态机负责；传输层调用 `im-notify --alert` 时不传 `--dedup-key`，避免双重去重。
6. 安装周期告警服务：

```bash
./install.sh alert
```

`install.sh alert` 会从当前进程环境、`.env` 或 `~/.claude/.env` 读取 `FEISHU_GENERAL_ALERT_WEBHOOK`，并写入本机生成的 `deploy/launchd/ai-radar-alert.plist` 的 `EnvironmentVariables`。launchd 不继承交互式 zsh 的临时 `export`；如果只在 shell 里 export 但没有重新安装 alert，后台任务拿不到 webhook。安装后可用下面命令确认本机 plist 已带 webhook 环境键（不要把真实 URL 贴到 issue/commit/聊天里）：

```bash
plutil -p deploy/launchd/ai-radar-alert.plist | rg 'FEISHU_GENERAL_ALERT_WEBHOOK'
```

测试或自定义数据库路径时，`install.sh alert` 也会把已设置的 `AI_RADAR_DB` 写入同一个 `EnvironmentVariables`，让 launchd job 与手工 `./run.sh admin alert-check` 使用同一份 SQLite。

如果 webhook 变更，重新生成并加载 plist：

```bash
./uninstall.sh alert
./install.sh alert
```

没有 `FEISHU_GENERAL_ALERT_WEBHOOK`、找不到 `im-notify` 或 `im-notify` 发送失败时，触发告警的传输失败会写入 `logs/alert-check.err.log`，本轮状态机仍会完成，不会让周期告警进程崩溃。CLI 同时会显示 `send <rule> <type> skipped reason=...`。

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
