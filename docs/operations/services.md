# 服务清单

> Mutable snapshot. ai-radar 长期运行的服务 + 自启机制 + 生命周期脚本 + Instructions 位置。

## 服务

| 服务 | 自动启动 | 当前状态 | 生命周期脚本 | Instructions |
|---|---|---|---|---|
| `live.aiplanet.ai-radar.serve` | launchd, KeepAlive=true | 已加载 | `./install.sh serve` / `./uninstall.sh serve` / `./status.sh serve` | [deploy/launchd/ai-radar-serve.plist.example](../../deploy/launchd/ai-radar-serve.plist.example) |
| `live.aiplanet.ai-radar.tunnel` | launchd, KeepAlive=true | 已加载 | `./install.sh tunnel` / `./uninstall.sh tunnel` / `./status.sh tunnel` | [deploy/launchd/ai-radar-tunnel.plist.example](../../deploy/launchd/ai-radar-tunnel.plist.example) · [deploy/cloudflared/config.yml.example](../../deploy/cloudflared/config.yml.example) |
| ai-radar pipeline (15min) | cron (`*/15 * * * *`) | 在 user crontab | `./install.sh pipeline` / `./uninstall.sh pipeline` / `./status.sh pipeline` | [deploy/cron/ai-radar-pipeline](../../deploy/cron/ai-radar-pipeline) · launchd 替代模板见 [ai-radar-pipeline.plist.example](../../deploy/launchd/ai-radar-pipeline.plist.example) |
| `live.aiplanet.ai-radar.wewe` (WeWe RSS docker bridge) | launchd, KeepAlive=true, ThrottleInterval=30 | 已加载 | `./install.sh wewe` / `./uninstall.sh wewe` / `./status.sh wewe` | [deploy/launchd/ai-radar-wewe.plist.example](../../deploy/launchd/ai-radar-wewe.plist.example) · [deploy/wewe-rss/RUNBOOK.md §Keeping It Running](../../deploy/wewe-rss/RUNBOOK.md#keeping-it-running) |

不带服务名时，`./install.sh` / `./uninstall.sh` / `./status.sh` 对全部 4 个服务生效。脚本契约见 [service-operations-protocol §3.3](~/.claude/references/service-operations-protocol.md)。

调度方式选择：pipeline 在 cron 和 launchd 之间二选一，**不要同时启用**——详见 [experiences/deployment.md 2026-05-15 条目](../experiences/deployment.md)。当前生产用 cron。

## 隐含依赖（repo 外）

| 依赖 | 必须的设置 | 验证方式 |
|---|---|---|
| Docker daemon (OrbStack / Docker Desktop) | "Start at login" / 同等开机自启选项 | `docker info >/dev/null 2>&1 && echo ok` |
| cron 守护 | macOS 自带，默认运行 | `pgrep cron` |
| launchd | 系统自带，登录后自动运行 | `launchctl print gui/$UID` |

`./install.sh wewe` 在 Docker daemon 未就绪时会先尝试 `open -a OrbStack` 并等待 ≤ 16s；若仍不可达则中止安装并提示。

## 验证（新机器 bring-up / 大改动后跑一遍）

```bash
./status.sh                                        # 4 行总览
curl -sf http://127.0.0.1:8000/api/v1/healthz && echo serve_ok
curl -sf http://127.0.0.1:4000/             -o /dev/null && echo wewe_ok
curl -sf https://aiplanet.live/             -o /dev/null && echo tunnel_ok
./run.sh fetch | tail -5                           # pipeline + wewe RSS 联通性
```

`./status.sh` 输出每个服务一行：是否 loaded / pid / 容器状态（wewe）/ crontab 状态（pipeline）/ 日志位置。

## 安装 / 卸载 / 切换

```bash
./install.sh              # 全部 4 个服务
./install.sh wewe         # 单个

./uninstall.sh            # 全部
./uninstall.sh wewe       # 单个；wewe 还会 docker compose down 停容器

./status.sh               # 只读面板
```

强制重启某个 launchd 服务：

```bash
launchctl kickstart -k gui/$UID/live.aiplanet.ai-radar.<serve|tunnel|wewe>
```

pipeline 在 cron ↔ launchd 之间切换：先 `./uninstall.sh pipeline`，再手动 `launchctl bootstrap` launchd plist（暂未做成脚本——cron 是当前生产选择）。

## 相关参考

- [README.md §服务](../../README.md#服务) — 用户视角的脚本入口表
- [docs/experiences/deployment.md](../experiences/deployment.md) — 历史踩坑（env 不继承、cron/launchd 共存、tunnel region、docker compose 守护）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 微信公众号源添加流程
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — WeWe RSS 详细运维手册
