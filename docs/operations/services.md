# 服务清单

> Mutable snapshot. ai-radar 长期运行的服务 + 自启机制 + 生命周期脚本 + Instructions 位置。

## 服务

| 服务 | 自动启动 | 当前状态 | 生命周期脚本 | Instructions |
|---|---|---|---|---|
| serve | launchd, KeepAlive=true | 已加载 | `./install.sh serve` / `./uninstall.sh serve` / `./status.sh serve` | [deploy/launchd/ai-radar-serve.plist.example](../../deploy/launchd/ai-radar-serve.plist.example) |
| tunnel | launchd, KeepAlive=true | 已加载 | `./install.sh tunnel` / `./uninstall.sh tunnel` / `./status.sh tunnel` | [deploy/launchd/ai-radar-tunnel.plist.example](../../deploy/launchd/ai-radar-tunnel.plist.example) · [deploy/cloudflared/config.yml.example](../../deploy/cloudflared/config.yml.example) |
| ai-radar pipeline (15min) | cron (`*/15 * * * *`) | 在 user crontab | `./install.sh pipeline` / `./uninstall.sh pipeline` / `./status.sh pipeline` | [deploy/cron/ai-radar-pipeline](../../deploy/cron/ai-radar-pipeline) · launchd 替代模板见 [ai-radar-pipeline.plist.example](../../deploy/launchd/ai-radar-pipeline.plist.example) |
| alert | launchd, StartInterval=300, RunAtLoad=true | 已加载；A1–A4 以 per-severity lifecycle 决定 firing / resolved，page 走 `im-notify --alert` 的 `ALERT` webhook，notice 走 `im-notify` 的 `NOTIFICATION` webhook；launchd 进程崩溃由 fleet watchdog 覆盖 | `./install.sh alert` / `./uninstall.sh alert` / `./status.sh alert` | [deploy/launchd/ai-radar-alert.plist.example](../../deploy/launchd/ai-radar-alert.plist.example) · [monitoring-alerting.md](monitoring-alerting.md) |
| performance probe (5min) | launchd, `StartInterval=300`, `RunAtLoad=true` | per-file LaunchAgent；只在 pipeline idle 窗保存/评估样本 | `./install.sh performance-probe` / `./uninstall.sh performance-probe` / `./status.sh performance-probe` | [ai-radar-performance-probe.plist.example](../../deploy/launchd/ai-radar-performance-probe.plist.example) · [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) |
| performance remediation (hourly) | cron（建议 `25 * * * *`，在 probe 后） | **当前禁用**：homepage 误标缺陷已修复，但仍须部署后确认 `hard_failure=false` 且 homepage `PERF:*` 非 firing 才按文档手动安装 | `./run.sh performance-remediate` | [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) |

`./install.sh` / `./uninstall.sh` / `./status.sh` 管理 serve、tunnel、pipeline、alert、performance-probe 这 5 个服务。probe 的专属 plist 经 `./run.sh performance-probe` 启动，保留 external watchdog；pipeline 继续使用既有 `*/15` user crontab，未迁移到 launchd。只有 remediation 仍按上表 gate 手工管理自己的 cron。脚本契约见 [service-operations-protocol §3.3](~/.claude/references/service-operations-protocol.md)。

`./install.sh` 会逐服务检查脚本可判定的依赖。缺少 `pipeline` 的 LLM key 时，交互式终端会询问 `DEEPSEEK_API_KEY` 并写入项目 `.env`；`alert` 则要求 `FEISHU_GENERAL_ALERT_WEBHOOK` 和 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK` **两者同时存在**，任缺一个都 fail-closed，不生成部分 launchd 配置。交互式终端会逐个询问缺失 webhook 并写入 `.env`；非交互环境跳过 alert 并在 summary 列出缺失 key。`alert` 运行时还需要部署机已从 `ai-agent-config` 安装 `~/.local/bin/im-notify`；tracked launchd 模板已把 `~/.local/bin` 加入该作业的 `PATH`。默认服务 `performance-probe` 与微信原文抓取都依赖 Playwright Chromium；部署前必须显式运行 `uv run playwright install chromium`，`install.sh` 不自动下载或校验该浏览器。`tunnel` 缺少 `deploy/cloudflared/config.yml` 时不会询问密钥，需先从 `deploy/cloudflared/config.yml.example` 创建自己的 Cloudflare tunnel 配置后重跑 `./install.sh tunnel`。环境变量依赖读取顺序为当前进程环境、项目 `.env`、`~/.claude/.env`。

### Alert 判定与 lifecycle

- A2 的 prefilter/scoring/enrich 错误率 numerator/denominator 各自只取最近 15 分钟，最小样本门为 `4/4/2`；`no_success_minutes=120` 是不受样本门影响的独立 page 支路，stage P95 仍用自己的 2 小时口径。
- A3 的 5xx numerator 与 PV denominator 同取最近 15 分钟，只有 `PV >= 20` 才评估 5xx rate；healthz 连续失败 2 次是独立 page 支路。
- A4 只有 fetch 失败率超阈且 items 正常时是 notice（30 分钟 debounce）；items 低于按日内进度缩放的 floor 时是 page（0 debounce），两分支同时命中也是 page。
- 每个 `rule_id` 以 `lifecycles.page` / `lifecycles.notice` 分别保存 debounce、`since`、`last_notified`、announced 与 cooldown。severity 转换先在原通道 resolved 已 announced episode，再在新通道 firing；pending 未送达 episode 静默关闭。各 severity 的计时器不互相节流。

> 已退役的 `wewe`（WeWe RSS docker bridge）已于 2026-06-06 从服务层移除（不再在脚本/注册表中）。微信摄取走 Mp2RSS（见 [wechat-ingestion.md](wechat-ingestion.md)）。如需回滚到 WeWe RSS：`deploy/wewe-rss/`（docker-compose + RUNBOOK）仍在，launchd plist 与脚本 wiring 从 git 历史恢复（移除 commit 见 git log）。

调度方式选择：pipeline 在 cron 和 launchd 之间二选一，**不要同时启用**——详见 [experiences/deployment.md 2026-05-15 条目](../experiences/deployment.md)。当前生产用 cron。

## 隐含依赖（repo 外）

| 依赖 | 必须的设置 | 验证方式 |
|---|---|---|
| cron 守护 | macOS 自带，默认运行 | `pgrep cron` |
| launchd | 系统自带，登录后自动运行 | `launchctl print gui/$UID` |
| pipeline LLM key | `DEEPSEEK_API_KEY` / `ARK_API_KEY` / `OPENAI_API_KEY` / `GLM_API_KEY` 任一 | `./install.sh pipeline` summary 显示 installed |
| alert 发送器 | `~/.local/bin/im-notify` + page 的 `FEISHU_GENERAL_ALERT_WEBHOOK` + notice 的 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK`；两个 webhook 任缺一个都拒绝 alert 安装 | `test -x "$HOME/.local/bin/im-notify"` 后运行下文无发送 preflight；已安装时检查 plist 同时有两个 key |
| Playwright Chromium | 微信原文抓取与默认 `performance-probe` | 部署前显式运行 `uv run playwright install chromium`；`install.sh` 不自动下载或校验 |
| Cloudflare tunnel | `deploy/cloudflared/config.yml` | `test -f deploy/cloudflared/config.yml` |
| Cloudflare Cache Rule | zone `aiplanet.live` 上的 `AI Radar short public pagination TTL`（见下节） | 同一 public 分页 URL 第二次请求 `CF-Cache-Status: HIT` |

## Cloudflare Cache Rule（public 分页边缘缓存）

`aiplanet.live` 上有一条 **repo 外**的 Cloudflare 边缘缓存配置，用于把公开旅程的翻页延迟从边缘直接命中，而不是每次回源。它不是 launchd / cron 服务，`install.sh` / `status.sh` 不管理，改动只在 Cloudflare dashboard 上做；此处记录其运维事实，避免唯一副本留在会被清理的 `plans/` 工作区。

规则名 **`AI Radar short public pagination TTL`**（当前 **Active**），位置 Cloudflare dashboard → `aiplanet.live` → **Caching** → **Cache Rules**。

| 项 | 值 |
|---|---|
| 匹配表达式 | `GET` 且 `http.request.uri.path in {"/" "/wechat" "/api/v1/curated" "/api/v1/wechat"}` 且 URL args 不含 `q`（`not any(http.request.uri.args.names[*] == "q")`） |
| Cache eligibility | **Eligible for cache**（HTML/JSON 默认不缓存，靠这一项开启） |
| Edge TTL | **Use cache-control header if present, bypass if not**（fail-closed：用 origin 的 90s 新鲜 + 30s SWR；origin 无头则不缓存，不设 origin-ignoring 固定 TTL） |
| Browser TTL | Respect origin TTL（不覆盖） |
| Cache key | **不要设自定义 Cache key** —— Free plan 下自定义 Cache key 是付费功能，会触发付费墙导致 Deploy 失败；保持默认（默认已保留全部 query 参数，page 1/page 2、limit 变体不会串味） |
| 规则顺序 | 放在任何同样设置 Cache eligibility / Edge TTL 的既有规则**之后**（last matching value wins） |

**origin 侧配套（缺一则边缘缓存不生效）**：serve 对上述四条路径的安全分页变体（HTML 只认 `page`，API 认 `page`+`limit`）发 `Cache-Control: public, max-age=90, stale-while-revalidate=30`；带 `q=`、其他筛选键（`category`/`date`/`run_id`/未知键）或非 200 响应发 `Cache-Control: private, no-store`。这些公开路由不读 cookie、不建 session、不发 `Set-Cookie`。header 代码改动需重启 serve 才生效；Cache Rule 可在重启前后应用，但 HIT 验证只有两者都上线后才有意义。

**运维验证**：

```bash
# 1. origin 直连——四条 cache-safe 路径应回 public,max-age=90,swr=30
curl -sS -D - -o /dev/null 'http://127.0.0.1:8000/wechat?page=1'
curl -sS -D - -o /dev/null 'http://127.0.0.1:8000/api/v1/curated?page=2&limit=40'
# q= / 筛选 / 非 200 应回 private,no-store
curl -sS -D - -o /dev/null 'http://127.0.0.1:8000/wechat?q=&page=1'

# 2. 经 CF——同一 cache-safe URL 两秒内请求两次，第二次应 CF-Cache-Status: HIT 且带 Age
curl -sS --compressed -D - -o /dev/null 'https://aiplanet.live/wechat?page=1'; sleep 2
curl -sS --compressed -D - -o /dev/null 'https://aiplanet.live/wechat?page=1'
# 3. 搜索仍不可缓存——q= 请求应 private,no-store，无 Age、无 HIT
curl -sS --compressed -D - -o /dev/null 'https://aiplanet.live/wechat?q=OpenAI&page=1'
```

第二次仍是 `DYNAMIC`/`BYPASS` 时，依次检查规则顺序、表达式、Edge TTL 模式与 origin header，再考虑动应用代码。命中效果反映在 `performance-probe` 的旅程延迟样本上（翻页 API 实测 3-5s → 0.5-1.4s），细节见 [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控)。

> 无法用 zone API 自动化：现有 `CLOUDFLARE_API_TOKEN` 能读 zone 但无 rulesets 权限（`/zones/<zone>/rulesets` → 403），故此规则只在 dashboard 手工维护；不要为此拓宽或替换该 token。

## Cloudflare tunnel shared ingress

The `ai-radar` tunnel is now a shared production dependency for two public sites:

| Hostname | Local service | Owner repo | Notes |
|---|---|---|---|
| `aiplanet.live` | `http://127.0.0.1:8000` | `~/research/ai-radar` | Primary AI Radar web app. |
| `sjtu.aiplanet.live` | `http://localhost:8100` | `~/research/sjtu-aaa` | SJTU 3A alumni site. `/admin` and `/api/admin` must stay blocked by the tunnel-level `http_status:403` rule above the SJTU main ingress rule. |

Before editing, reinstalling, or removing this tunnel, inspect `~/research/sjtu-aaa/docs/operations/services.md` and preserve the SJTU ingress rules. A catch-all or rewritten tunnel config that only keeps `aiplanet.live` will silently take the SJTU site offline even if AI Radar still looks healthy. After any tunnel change, verify both:

```bash
curl -sf https://aiplanet.live/api/v1/healthz
curl -sf https://sjtu.aiplanet.live/api/v1/healthz
curl -s -o /dev/null -w '%{http_code}\n' https://sjtu.aiplanet.live/admin
```

## 验证（新机器 bring-up / 大改动后跑一遍）

```bash
./status.sh                                        # 5 行总览
curl -sf http://127.0.0.1:8000/api/v1/healthz && echo serve_ok
curl -sf "https://${AI_RADAR_SITE_DOMAIN}/" -o /dev/null && echo tunnel_ok
test -x "$HOME/.local/bin/im-notify"
bash -lc 'source deploy/lib/services.sh; if missing="$(alert_webhook_missing_keys)"; then echo "missing: $missing"; exit 1; else echo "both webhook keys configured"; fi'
plutil -p deploy/launchd/ai-radar-alert.plist | rg -o 'FEISHU_GENERAL_(ALERT|NOTIFICATION)_WEBHOOK' | sort -u
uv run pytest tests/test_admin_alerts.py -q -k 'send_alert_message_calls_im_notify_alert_without_dedup or send_alert_message_routes_notice_without_alert_flag'
./run.sh performance-probe                         # 同机 provisional 四旅程采样 + PERF:* 状态机
./run.sh fetch | tail -5                           # pipeline + Mp2RSS feed 联通性
```

上述 alert 验证只检查可执行文件、两个配置 key 和 mock 投递路由，不显示 webhook URL，也不发送真实消息。`./run.sh admin alert-check` 不是无害 smoke：当前状态如果触发 firing / resolved，它会按 page/notice 实际调用 `im-notify`。

当前不要把 `performance-remediate` 当 bring-up smoke 执行：homepage hard-failure 误标缺陷虽已修复，旧样本仍可能已形成假 firing。只有部署该修复、手工 probe 显示 homepage `hard_failure=false`，且 homepage `PERF:*` 状态已非 firing 后，才按 [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) 的 gate 手工验证并安装 remediation cron。

`./status.sh` 输出每个服务一行：是否 loaded / pid / crontab 状态（pipeline）/ 日志位置。`alert` 与 `performance-probe` 都是周期任务，正常完成单次运行后可能显示 `loaded ✓ (no pid)`。

## 安装 / 卸载 / 切换

```bash
./install.sh              # 全部 5 个服务
./install.sh alert        # 单个

./uninstall.sh            # 全部
./uninstall.sh alert      # 单个

./status.sh               # 只读面板
```

强制重启某个 launchd 服务：

```bash
launchctl kickstart -k "gui/$UID/<launchd-label-for-serve|tunnel|alert>"
```

告警消息由 `alert` 自己的 per-severity firing / resolved / 30 分钟 cooldown 决定；page 调用 `im-notify --alert`，notice 调用不带 `--alert` 的 `im-notify`。两者都会把 rule/severity/event/notification nonce 组成的稳定 identity 通过 `--dedup-key` / `--dedup-text` 交给 `im-notify`。`im-notify` 非零退出或不可执行时，`alert-check` 会把失败写入错误日志并继续完成本轮；未投递的 pending firing / resolved 会在下轮重试，由该稳定 signature 抑制同一意图的重复可见消息。

⚠ 改了 alert 的任一 webhook 后，单独 `kickstart -k` 不会刷新 launchd 烘焙的 `<EnvironmentVariables>`。重跑 alert 安装会重新生成 plist，并对已加载 job 执行 bootout/bootstrap：

```bash
./install.sh alert
# 或手动：launchctl bootout "gui/$UID/<launchd-label-for-alert>" && launchctl bootstrap gui/$UID "$PWD/deploy/launchd/ai-radar-alert.plist"
```

pipeline 在 cron ↔ launchd 之间切换：先 `./uninstall.sh pipeline`，再手动 `launchctl bootstrap` launchd plist（暂未做成脚本——cron 是当前生产选择）。

## 相关参考

- [README.md §服务](../../README.md#服务) — 用户视角的脚本入口表
- [docs/operations/monitoring-alerting.md](monitoring-alerting.md) — `/admin` dashboard、A1-A4 告警、飞书 webhook、Cloudflare Access 配置、边缘缓存与旅程延迟
- [docs/experiences/deployment.md](../experiences/deployment.md) — 历史踩坑（env 不继承、cron/launchd 共存、tunnel region、docker compose 守护）
- [docs/operations/wechat-ingestion.md](wechat-ingestion.md) — 微信公众号摄取（Mp2RSS 接入、`MP2RSS_FEED_URL` 配置、头像 backfill、迁移留尾记录）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 旧 WeWe RSS 微信源添加流程（已停用，仅回滚参考）
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — 旧 WeWe RSS 详细运维手册（已停用）
