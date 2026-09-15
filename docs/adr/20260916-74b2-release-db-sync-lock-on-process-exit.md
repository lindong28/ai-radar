# DB sync：用内核锁消除重启后的永久阻塞

- Status: accepted
- Date: 2026-09-16

## Context

用户报告公网 `/wechat` 停在 9 月 13 日，要求先恢复，再从根源避免、自动恢复或及时告警。现场本地已有 9 月 15 日已解读文章，线上 accepted receipt 却停在 `2026-09-13T09:42:18Z`。本地空目录 `data/.sync.lock` 创建于 9 月 13 日 16:41（UTC+8），早于本机 17:24 的启动；9 月 14–15 日的后续同步均因目录存在报 `another sync is running`。原实现只在 `EXIT` trap 中删除目录，重启后没有进程能执行这条清理。

发送侧账本记录 9 月 13 日 21:41、14 日 01:41 的 `ai-radar-db-sync` 告警发送成功，之后同退出码去重；这不证明用户已读。此次修复解决任务无法自愈，不把重复提醒当作替代。现场证据在维护机 `logs/sync-cron.log`、`logs/wechat-recovery-sync-20260916.log` 与 `~/.local/state/im-notify/alert-sent.log`，它们不随仓库分发。

## Decision

`AI_RADAR_SYNC_LOCK` 继续表示锁目录，但互斥来自其中永久保留的 `owner.flock`：bash 打开 fd 9，用 Python stdlib 获取非阻塞 BSD 排他锁。所有本地持有者结束或主机重启后，内核释放锁；不按 PID、开机时间或文件年龄猜测是否可以回收，不 unlink 锁文件。

直接 SSH 调用由持有 fd 9 的等待子 shell 承接。现场实测 GNU rsync 3.4.4 保留继承描述符，而 OpenSSH 关闭它；因此不能只依赖入口 bash 存活来保护 SSH 发布窗口。日志裁剪使用相同锁，并在持锁期间完成替换，不能以永久目录存在判断忙闲，也不能先释放锁再裁剪。

保留 ADR-013 的每五小时同步、SSH agent 发现、失败告警与去重；保留远端 snapshot identity、apply 和 committed receipt 验收。不改变采集、解读、数据内容或远端代码。

## Alternatives

- PID/boot/mtime 回收目录：需重建用户态判活，ADR-052 已记录同类误回收；不采用。
- 开机删除目录：不能覆盖 SIGKILL，且清理与活任务存在竞态；不采用。
- 仅保留目录锁并增加提醒：此次已有告警但任务仍持续失败；不采用。

## Activation and rollback

先等待旧 producer 及子进程退出、确认不再占用同步资源，再同时启用 producer 与 cron wrapper 的改动。新旧锁机制不得并行运行。回退也必须等所有持有者退出，再移除本次新增的锁文件及空目录后恢复旧脚本，否则旧脚本会把永久目录当成忙。

## Verification and limits

独立 decision-review 七项判据放行；真实进程测试覆盖遗留目录、并发排斥、自然结束、杀父进程但子进程存活、杀整树后重试、真实 rsync 和 SSH 关闭 fd 的路径；日志裁剪覆盖忙/闲两态。以实际测试结果及公网恢复读数收口，不把本决策当成验收结果。

适用本机 macOS、本地文件系统，不外推 NFS 或未经本轮验证的 Linux。未诱发真实重启。活进程永久挂起、调度器根本未启动、主机关机或 SSH agent 不可用不由内核锁自动修复，告警及已接受的调度器观察边界仍见 ADR-013。此修复消除的是所有持有者退出后仍被遗留锁永久挡住的故障。
