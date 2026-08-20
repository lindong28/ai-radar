# ADR-052：pipeline 互斥改由内核 flock 持有，删除用户态判活与 stale reclaim

- Status: accepted
- Date: 2026-08-17

## Context

`pipeline.sh` 原用目录锁 + 用户态判活（`owner_is_live`）+ stale reclaim 协议实现单实例互斥。判活依赖锁 owner 文件中记录的 `boot_id` 与当前读数**字符串相等**，而 macOS 的 `sysctl kern.boottime` 由内核按「当前时间 − uptime」倒推，NTP 校时使它在同一开机会话内持续漂移：39 个存留 owner 记录的 `boot_id` 39 个互不相同（usec 从 69253 漂到 995488，sec 跨 1786614650/651）。因此该相等判据在跨 cron 轮次的时间尺度上不可靠，存活的持有者被判死、锁被回收（两例受害进程实测存活），后续轮次与其并发写 `data/radar.db`，击穿 5 秒 `busy_timeout`，`enrich`/`curate`/`interpret` 以 `database is locked` 持续失败——后段阶段系统性饿死（2026-07-27 起 40 次回收；一手读数见 `handoffs/pipeline-stage-starvation-handoff-20260817.md`）。cron 临时改 6 小时（无并发）后同批 `enrich`/`curate` 正常完成，证明阶段本身无恙。其它失效路径（publish/cleanup 竞态）未被排除，但本决策不依赖对具体路径的归因——它删除整类用户态判活。

## Decision

删除 `pipeline.sh` 的目录锁、判活与 stale reclaim 全套（`prepare_lock_candidate` / `publish_lock_candidate` / `owner_is_live` / `lock_generation_identity` / stale 分支），改为：

- 入口 `exec 9>"$SCRIPT_DIR/.pipeline.flock"` 打开锁文件，随后 `python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)'` 取非阻塞排他锁（子进程继承 fd 9，flock 绑定在 open file description 上，python 退出不释放）。取不到 → 有限微重试（5 次、间隔 0.2s，跨过观察者共享锁的微秒级窗口）后记 SKIP 日志、退出 0。
- **锁由整棵继承 fd 9 的进程树持有，最后一个持有者退出时内核释放。** 这是有意契约而非泄漏：互斥要保护的是「会写 radar.db 的进程树」——SIGKILL 掉 bash 而 stage orphan 仍在写库时放行新轮次，正是旧协议在 owner pid 死亡时会犯的错。
- 观察者（journey_monitor idle 判定、A6 owner 判活）改为对同一文件试探 `LOCK_SH | LOCK_NB`，三态：加锁被排他锁挡住（`BlockingIOError`）→ busy；加锁成功 → idle，立即释放；探测自身出错（权限、I/O）→ unknown——unknown 不得当 busy 用，A6 只在确证 busy 时抑制。不再读 owner 文件、不再做 boot_id / lstart 比对（journey_monitor 内同款 boot_id bug 一并消除）。
- `.pipeline.activity` generation 改由 `python3 -c 'import uuid; print(uuid.uuid4())'` 每轮生成，替代原 `LOCK_OWNER_TOKEN`；消费方语义（每轮值不同、无 ABA）不变。

## Alternatives

- 最小修复：只把 boot 身份源换成稳定量（如 pid 1 的 lstart），保留协议。否决：整个协议的复杂度都在用用户态启发式重建「持有者活着吗」这一内核原生语义，逐个修读数源治标；任何一个平台相关读数（boot_id / lstart / stat mtime）再漂移即整体复发。
- flock(1) 命令：macOS 无此工具，引入第三方依赖不如用既有必需依赖 python3 的 stdlib。
- python 包一层 execv 重启 bash 主体：改变启动形态，fd 继承方案单文件即可达成同一语义。

## Consequences

- 进程死亡（含 SIGKILL、断电后重启）由内核释放锁，不存在 stale 判定，锁误回收整类失效一次消除；`.pipeline.lock.reclaim.*` 机制随之消失。
- 「进程活着但卡死（挂起不退出）」不再有任何机制推进——旧协议 grace/stale 极端下能强推。此风险被接受、未量化发生率；发现门为 A2 的 `no_success_minutes`（pipeline 长期无成功轮次告警）。
- 观察者持共享锁的微秒级窗口可能让 pipeline 的首次加锁瞬时失败，由微重试跨过；多观察者连续占用致假 SKIP 为已知残余风险（概率极低，同由 A2/A6 发现）。
- 若某 stage 派生脱离进程组的长驻进程且未关闭 fd 9，锁会被其延续持有——实现时审计各 stage 无 daemonize（audit 结论：`run.sh` 各 stage 均为前台 `uv run python`，无 daemonize 路径）。
- 迁移面：`tests/test_pipeline_scheduler.py` 的 reclaim 协议测试作废重写；`journey_monitor.py` / `alerts.py`（A6 owner 判活）/ `performance-probe --pipeline-lock` CLI / `docs/operations/db-slimming.md` 停写门 / README、architecture.md、monitoring-alerting.md、experiences/llm-pipeline.md 的锁契约描述同步迁移。
- 回滚锚点：改动收敛为单一线性提交串，`git revert` 即整体还原旧协议与其测试。

## Scope and unverified items

作用域：单机部署（macOS 生产 + Linux best-effort），同一本地文件系统上的互斥；NFS 上 flock 语义不可靠，本部署无 NFS，不外推。未验证项：macOS cron 非交互环境下的 fd 生命周期行为由新增回归测试在非交互 shell 中实测（杀整树 → 释放；只杀 bash、stage 活 → 仍持有；自然退出 → 释放），本 ADR 记录时测试尚未运行；「卡死不退出」发生率未量化。

## 修订记录

**2026-08-20 — 引用文件的可达性说明。** 上面 Context 段引用的 `handoffs/pipeline-stage-starvation-handoff-20260817.md` 是本机仓外文件，不在本仓内，读者按该路径在 repo 里找不到它。本注记不补录该文件，也不改写原句。

核心读数**曾实测**，以本注记确认：2026-07-27 起累计 40 次锁误回收，其中两例受害进程实测存活。当时的一手证据面是 39 个存留 owner 记录的 `boot_id` 互不相同，载体是 `.pipeline.lock.reclaim.*` 目录——**它们是本机工作树的残留物，不是仓内文件**（`git ls-files` 对该模式返回 0 条，从未被跟踪，因而不随仓库分发）。所以这份一手证据面**只在当时那台机器的工作树上仍可复核**；换一台机器或新 clone 打开本仓的读者手上没有它，本仓只承载上面那两句结论读数。本注记不补录该证据（其中含本机路径与 owner 记录）。该机制已随本决策消失，这些目录属于旧协议的遗留物。
