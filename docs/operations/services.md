# 服务清单

> Mutable snapshot. ai-radar 长期运行的服务 + 自启机制 + 生命周期脚本 + Instructions 位置。

## 服务

| 服务 | 自动启动 | 当前状态 | 生命周期脚本 | Instructions |
|---|---|---|---|---|
| serve | launchd, KeepAlive=true | 已加载 | `./install.sh serve` / `./uninstall.sh serve` / `./status.sh serve` | [deploy/launchd/ai-radar-serve.plist.example](../../deploy/launchd/ai-radar-serve.plist.example) |
| tunnel | launchd, KeepAlive=true | 已加载 | `./install.sh tunnel` / `./uninstall.sh tunnel` / `./status.sh tunnel` | [deploy/launchd/ai-radar-tunnel.plist.example](../../deploy/launchd/ai-radar-tunnel.plist.example) · [deploy/cloudflared/config.yml.example](../../deploy/cloudflared/config.yml.example) |
| ai-radar pipeline (15min) | cron (`*/15 * * * *`) | 在 user crontab | `./install.sh pipeline` / `./uninstall.sh pipeline` / `./status.sh pipeline` | [deploy/cron/ai-radar-pipeline](../../deploy/cron/ai-radar-pipeline) · launchd 替代模板见 [ai-radar-pipeline.plist.example](../../deploy/launchd/ai-radar-pipeline.plist.example) |
| alert | launchd, StartInterval=300, RunAtLoad=true | 已加载；A1-A4 应用级健康失败由状态机判定后调用 `im-notify --alert`，launchd 进程崩溃由 fleet watchdog 覆盖 | `./install.sh alert` / `./uninstall.sh alert` / `./status.sh alert` | [deploy/launchd/ai-radar-alert.plist.example](../../deploy/launchd/ai-radar-alert.plist.example) · [monitoring-alerting.md](monitoring-alerting.md) |
| performance probe (hourly) | cron（建议 `17 * * * *`） | 需按文档手动安装；`install.sh` / `status.sh` 不管理 | `./run.sh performance-probe` | [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) |
| performance remediation (hourly) | cron（建议 `25 * * * *`，在 probe 后） | **当前禁用**：homepage hard-failure 误标关闭后才按文档手动安装 | `./run.sh performance-remediate` | [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) |

`./install.sh` / `./uninstall.sh` / `./status.sh` 仍只管理 serve、tunnel、pipeline、alert 这 4 个核心服务；performance cron job 用文档中的 crontab 样例手动管理，不出现在 `status.sh` 四行总览。当前只启用 probe；remediation 等 homepage hard-failure 误标关闭后再安装。脚本契约见 [service-operations-protocol §3.3](~/.claude/references/service-operations-protocol.md)。

`./install.sh` 会逐服务检查依赖。缺少 `pipeline` 的 LLM key 时，交互式终端会询问 `DEEPSEEK_API_KEY` 并写入项目 `.env`；缺少 `alert` 的 `FEISHU_GENERAL_ALERT_WEBHOOK` 时同理询问 webhook。非交互环境不会等待输入，会跳过缺依赖的服务并在 summary 中列原因。`alert` 还要求部署机已从 `ai-agent-config` 安装 `~/.local/bin/im-notify`；tracked launchd 模板已把 `~/.local/bin` 加入该作业的 `PATH`。`tunnel` 缺少 `deploy/cloudflared/config.yml` 时不会询问密钥，需先从 `deploy/cloudflared/config.yml.example` 创建自己的 Cloudflare tunnel 配置后重跑 `./install.sh tunnel`。依赖读取顺序为当前进程环境、项目 `.env`、`~/.claude/.env`。

> 已退役的 `wewe`（WeWe RSS docker bridge）已于 2026-06-06 从服务层移除（不再在脚本/注册表中）。微信摄取走 Mp2RSS（见 [wechat-ingestion.md](wechat-ingestion.md)）。如需回滚到 WeWe RSS：`deploy/wewe-rss/`（docker-compose + RUNBOOK）仍在，launchd plist 与脚本 wiring 从 git 历史恢复（移除 commit 见 git log）。

调度方式选择：pipeline 在 cron 和 launchd 之间二选一，**不要同时启用**——详见 [experiences/deployment.md 2026-05-15 条目](../experiences/deployment.md)。当前生产用 cron。

## 隐含依赖（repo 外）

| 依赖 | 必须的设置 | 验证方式 |
|---|---|---|
| cron 守护 | macOS 自带，默认运行 | `pgrep cron` |
| launchd | 系统自带，登录后自动运行 | `launchctl print gui/$UID` |
| pipeline LLM key | `DEEPSEEK_API_KEY` / `ARK_API_KEY` / `OPENAI_API_KEY` / `GLM_API_KEY` 任一 | `./install.sh pipeline` summary 显示 installed |
| alert 发送器 | `~/.local/bin/im-notify` + `FEISHU_GENERAL_ALERT_WEBHOOK` | `test -x "$HOME/.local/bin/im-notify" && plutil -p deploy/launchd/ai-radar-alert.plist \| rg FEISHU_GENERAL_ALERT_WEBHOOK` |
| Cloudflare tunnel | `deploy/cloudflared/config.yml` | `test -f deploy/cloudflared/config.yml` |

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
./status.sh                                        # 4 行总览
curl -sf http://127.0.0.1:8000/api/v1/healthz && echo serve_ok
curl -sf "https://${AI_RADAR_SITE_DOMAIN}/" -o /dev/null && echo tunnel_ok
./run.sh admin alert-check                         # 执行 alert 规则；仅在状态机决定 firing / resolved 时调用 im-notify
./run.sh performance-probe                         # 同机 provisional 四旅程采样 + PERF:* 状态机
./run.sh fetch | tail -5                           # pipeline + Mp2RSS feed 联通性
```

当前不要把 `performance-remediate` 当 bring-up smoke 执行：homepage hard-failure 误标可能已形成假 firing。只有该缺陷关闭、手工 probe 显示 homepage `hard_failure=false`，且 homepage `PERF:*` 状态已非 firing 后，才按 [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) 的 gate 手工验证并安装 remediation cron。

`./status.sh` 输出每个服务一行：是否 loaded / pid / crontab 状态（pipeline）/ 日志位置。`alert` 是周期任务，正常完成单次检查后可能显示 `loaded ✓ (no pid)`。

## 安装 / 卸载 / 切换

```bash
./install.sh              # 全部 4 个服务
./install.sh alert        # 单个

./uninstall.sh            # 全部
./uninstall.sh alert      # 单个

./status.sh               # 只读面板
```

强制重启某个 launchd 服务：

```bash
launchctl kickstart -k "gui/$UID/<launchd-label-for-serve|tunnel|alert>"
```

告警消息由 `alert` 自己的 firing / resolved / 30 分钟冷却状态机决定；传输层只调用 `im-notify --alert`，**不使用** `--dedup-key`，避免双重去重吞掉状态机决定发送的真实通知。`im-notify` 非零退出或不可执行时，`alert-check` 会把失败写入错误日志并继续完成本轮，不会让告警循环崩溃。

⚠ 改了 alert 的 `FEISHU_GENERAL_ALERT_WEBHOOK`（或任何 launchd 服务的环境变量）后，`kickstart -k` 和"已加载时重跑 `./install.sh`"都**不会**让新值生效——plist 的 `<EnvironmentVariables>` 在生成时烘焙，launchd 持有 bootstrap 那一刻的快照。必须先 bootout 再 bootstrap：

```bash
./uninstall.sh alert && ./install.sh alert    # 推荐：重新生成 plist 并干净重载
# 或手动：launchctl bootout "gui/$UID/<launchd-label-for-alert>" && launchctl bootstrap gui/$UID "$PWD/deploy/launchd/ai-radar-alert.plist"
```

pipeline 在 cron ↔ launchd 之间切换：先 `./uninstall.sh pipeline`，再手动 `launchctl bootstrap` launchd plist（暂未做成脚本——cron 是当前生产选择）。

## 相关参考

- [README.md §服务](../../README.md#服务) — 用户视角的脚本入口表
- [docs/operations/monitoring-alerting.md](monitoring-alerting.md) — `/admin` dashboard、A1-A4 告警、飞书 webhook、Cloudflare Access 配置
- [docs/experiences/deployment.md](../experiences/deployment.md) — 历史踩坑（env 不继承、cron/launchd 共存、tunnel region、docker compose 守护）
- [docs/operations/wechat-ingestion.md](wechat-ingestion.md) — 微信公众号摄取（Mp2RSS 接入、`MP2RSS_FEED_URL` 配置、头像 backfill、迁移留尾记录）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 旧 WeWe RSS 微信源添加流程（已停用，仅回滚参考）
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — 旧 WeWe RSS 详细运维手册（已停用）
