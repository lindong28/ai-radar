# 服务清单

> Mutable snapshot. ai-radar 长期运行的服务 + 自启机制 + Instructions 位置。

## 服务

| 服务 | 自动启动 | 当前状态 | Instructions |
|---|---|---|---|
| `live.aiplanet.ai-radar.serve` | launchd, KeepAlive=true | 已加载 | [deploy/launchd/ai-radar-serve.plist.example](../../deploy/launchd/ai-radar-serve.plist.example) · [README §部署](../../README.md#部署) |
| `live.aiplanet.ai-radar.tunnel` | launchd, KeepAlive=true | 已加载 | [deploy/launchd/ai-radar-tunnel.plist.example](../../deploy/launchd/ai-radar-tunnel.plist.example) · [deploy/cloudflared/config.yml.example](../../deploy/cloudflared/config.yml.example) |
| ai-radar pipeline (15min) | cron (`*/15 * * * *`) | 在 user crontab | [deploy/cron/ai-radar-pipeline](../../deploy/cron/ai-radar-pipeline) · [README §自动调度](../../README.md#自动调度) · launchd 替代模板见 [ai-radar-pipeline.plist.example](../../deploy/launchd/ai-radar-pipeline.plist.example) |
| `live.aiplanet.ai-radar.wewe` (WeWe RSS docker bridge) | launchd, KeepAlive=true, ThrottleInterval=30 | 待 load (依赖 OrbStack) | [deploy/launchd/ai-radar-wewe.plist.example](../../deploy/launchd/ai-radar-wewe.plist.example) · [deploy/wewe-rss/RUNBOOK.md §Keeping It Running](../../deploy/wewe-rss/RUNBOOK.md#keeping-it-running) |

调度方式选择：pipeline 在 cron 和 launchd 之间二选一，**不要同时启用**——详见 [experiences/deployment.md 2026-05-15 条目](../experiences/deployment.md)。当前生产用 cron。

## 隐含依赖（repo 外）

| 依赖 | 必须的设置 | 验证方式 |
|---|---|---|
| Docker daemon (OrbStack) | OrbStack → Settings → General → "Start at login" 勾选 | `docker info >/dev/null 2>&1 && echo ok` |
| cron 守护 (cron.plist) | macOS 自带，默认运行 | `pgrep cron` |
| launchd | 系统自带，登录后自动运行 | `launchctl print gui/$UID` |

## 验证（新机器 bring-up / 大改动后跑一遍）

```bash
# 1. launchd 守护服务
launchctl list | grep -E 'ai-radar|aiplanet'
# 期待: serve / tunnel / wewe 三行

# 2. 进程角度
lsof -nP -iTCP:8000 -sTCP:LISTEN | head -1     # serve
docker ps --filter name=ai-radar-wewe-rss      # wewe
crontab -l | grep ai-radar                     # pipeline

# 3. 应用角度
curl -sf http://127.0.0.1:8000/api/v1/healthz && echo serve_ok
curl -sf http://127.0.0.1:4000/ -o /dev/null && echo wewe_ok
curl -sf https://aiplanet.live/ -o /dev/null && echo tunnel_ok

# 4. 日志
tail -n 30 /tmp/ai-radar-serve.err
tail -n 30 /tmp/ai-radar-tunnel.err
tail -n 30 /tmp/ai-radar-wewe.err
ls -lt logs/pipeline-*.log | head -3
```

## 安装 / 卸载 / 切换

每个 plist 安装步骤一致：

```bash
cp deploy/launchd/<name>.plist.example deploy/launchd/<name>.plist
# 修改 <name>.plist 里的 /path/to/ai-radar 为真实绝对路径
launchctl bootstrap gui/$UID deploy/launchd/<name>.plist
launchctl enable gui/$UID/<service-label>
launchctl kickstart -k gui/$UID/<service-label>
```

强制重启某个服务：

```bash
launchctl kickstart -k gui/$UID/live.aiplanet.ai-radar.<serve|tunnel|wewe>
```

卸载某个服务：

```bash
launchctl bootout gui/$UID/live.aiplanet.ai-radar.<serve|tunnel|wewe>
```

pipeline 在 cron ↔ launchd 之间切换：先卸载旧的（删 crontab 行 / `launchctl bootout`），再安装新的。同时启用会双触发。

## 相关参考

- [README.md §部署 / §自动调度](../../README.md#部署) — 初次部署的 step-by-step
- [docs/experiences/deployment.md](../experiences/deployment.md) — 历史踩坑（env 不继承、cron/launchd 共存、tunnel region、docker compose 守护）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 微信公众号源添加流程
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — WeWe RSS 详细运维手册
