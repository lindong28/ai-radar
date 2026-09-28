# ADR-20260928-7552: 定期重试 OrbStack 启动

- Status: accepted
- Date: 2026-09-28

## Context

2026-09-26 OrbStack 因磁盘空间不足停止，清理磁盘后仍未自动恢复。用户明确要求以后能够自动恢复。既有登录 LaunchAgent 只在加载时执行一次 `orbctl start`，不能补上运行期间停止后的重试。本决定延续 OrbStack 登录自启路线，只增加周期触发，不迁移运行时，也不提供无人登录时的启动保证。

## Options Considered

| 方案 | 取舍 |
|---|---|
| 既有 LaunchAgent 增加 `StartInterval` | 复用现有启动命令、安装与卸载入口；失败可等待下一次调度，但不能保证固定恢复时限 |
| `KeepAlive=true` | 一次性启动命令成功退出后仍会被反复拉起，不符合有间隔重试的目标 |
| 新增磁盘阈值 watcher 或 cron | 需要另一套启动策略或重复调度；本次没有足以确定安全空间阈值的证据 |
| 迁移运行时 | 超出本次 OrbStack 自动恢复目标；已放弃的 Lima 路线不作为当前实施前提 |

## Decision

在 `deploy/launchd/ai-radar-orbstack.plist.example` 增加 `StartInterval=1200`，保留 `/opt/homebrew/bin/orbctl start` 与 `RunAtLoad=true`。不添加 `KeepAlive`、自定义磁盘阈值或新的常驻 watcher。20 分钟沿用既有 Wechat2RSS 健康检查节奏，是重试间隔，不是恢复 deadline，也不是实测最优值。独立 L1 决策评审已放行此方案。

## Consequences

- 生效前提是用户已登录、LaunchAgent 已加载、主机能够调度；睡眠中或该 job 仍在运行时的 tick 会跳过。若启动命令挂住，本方案不保证将其恢复。
- 磁盘仍满时也会尝试启动，由 OrbStack 自身处理启动失败；下一次调度再重试。本机制不会自动清盘、修复数据或恢复微信登录。
- 手动维护前先执行 `./uninstall.sh orbstack`，避免有意停止后被定时启动；该命令卸载 job，不停止 OrbStack runtime，也不删除容器数据。维护后用 `./install.sh orbstack` 重新启用。
- 保留的无机器参数 `orbctl start` 可能恢复上次停止时运行的机器，作用范围不限定为某一个 Wechat2RSS 容器。容器是否随 runtime 恢复仍取决于其既有重启策略。
- 回退可恢复此前模板并重新安装；持续失败继续由既有健康检查和 job 日志观测，不增加通知通道。

## Evidence and Validation Boundary

2026-09-28 决策材料保存在本地事故记录 `logs/wechat-recovery-20260928/auto-recovery-decision.md` 和 `auto-recovery-decision-review.md`。前者记录原 job 无 `StartInterval`、运行中再次 `orbctl start` 返回 0 且容器启动时间不变；后者独立核对了 `launchd.plist` 的跳过 tick 语义及 `orbctl start --help` 的机器恢复范围。这些是来源记录，不代表本 ADR 作者重新执行过相同测试。

同目录 `launchd-timer-probe/result.json` 记录隔离 job 在 `StartInterval=2`、无 kickstart 的试验中自动执行两次，先退出 1、后退出 0。它支持失败后可再次调度，不证明实际 OrbStack job 已安装或真实 ENOSPC 后能够恢复。本 ADR 接受的是实施决定；真实安装、停止恢复和物理磁盘耗尽恢复均未由该试验验证。
