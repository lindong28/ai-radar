# ADR-011: PERF 改为 idle-only 探测并用 per-file launchd 调度

- Status: accepted
- Date: 2026-07-26
- Supersedes: ADR-008 中仅限 PERF 的 busy→idle gate / F1 与 busy rollup / F4 方向；ADR-008 的 per-severity lifecycle 主体仍 accepted

## Context

F1/F4 曾让 same-host PERF 同时采集 pipeline busy 与 idle 样本：busy 超预算只有在同 cell 的 idle 基线足量且健康时才降为 notice，再把 busy notice 合并成共因 rollup。这个设计依赖目标部署持续产出足量、新鲜的 idle 基线。

生产事实否定了该前提。probe 的旧 hourly `:17` 调度总落入 `*/15` pipeline 的运行窗；历史 idle 样本约占 5%，最近一天为 0，每个 `(journey,vantage)` 最多 6 条，远少于形成确认窗所需的 22 条。因此 gate 从未真正获得降级资格，系统退化为 fail-closed busy page，仍约每小时发出 5 条噪声。同期 `journey-samples.jsonl` 的损坏末行还让 probe crash-silent，暴露出“复杂采集控制面本身可使探针永久停采”的第二个风险。

需要同时满足：

- 只让无同机 pipeline 争用的旅程测量进入 PERF 判定；
- 保留 20 warm samples + 3 confirmation windows 的抗抖窗口；
- 让每个启用 cell 从零到 22 条有效样本不超过预固定的 6 小时；
- 启动路径和 lifecycle 在 crash、hang、旧 symlink 迁移及外来同名文件场景下 fail closed；
- pipeline 现有 `*/15` user crontab 不因 PERF 调度调整而迁移。

## Options Considered

### Option A: 修复 idle 采样，但保留 busy+idle gate 与 rollup

- Pros: 保留“只在 pipeline 争用时变慢”的诊断视角，理论上可将此类退化降为 notice。
- Cons: 仍把告警正确性建立在 idle 基线的量与新鲜度上；双 cell、gate、rollup 和迁移状态扩大失败面，生产前提再次饥饿时仍会退化为噪声或盲区。

### Option B: 只采 idle，超预算直接 page

- Pros: 每条样本都排除同机 pipeline 争用；删除 busy gate/rollup 后告警含义直接，状态与消息面更小。
- Cons: 不观察只在 pipeline 运行期间出现的退化；有效样本率受 pipeline 占用比例限制，22 样本时效必须持续监督。

### Option C: 改用 off-host synthetic 或真实用户监控（RUM）

- Pros: 可测区域网络、Cloudflare 与真实用户体验，不依赖 same-host idle 窗。
- Cons: 需要新的外部执行、认证、数据与告警基础设施，不能作为本轮 same-host 探针的有界替换。

## Decision

选择 **Option B**，并保留 Option C 作为后续演进方向。

1. **idle-only 语义**：每条旅程测量前后读取 pipeline owner 与持久 activity generation；只有两端都证明 idle 且 generation 未变时才保存、评估。pipeline 正在运行、owner 不可信或 generation 变化时跳过，不产生样本或告警。每个 `PERF:<journey>:<vantage>:idle` cell 保留 20+3 窗，首个 confirmed firing 需要第 22 条有效 idle 样本；违规后直接 page，不再生成 busy severity/message、降级 gate 或 rollup。旧 busy/rollup lifecycle 由一次性迁移明确关闭。
2. **U9：换采集原语**：不继续硬化长命 SQLite singleflight，也不保留跨 round 的 shared browser flock。pipeline 侧的原子 activity generation 是 idle 证明；样本/状态只使用短、有界的文件临界区，避免“锁活着但探针永久停采”。
3. **U11：投递契约改为 at-least-once + transport dedup**：发送与状态持久化不宣称 exactly-once。发送前持久化 notification nonce；同一 crash retry 复用 nonce，不同 reminder / severity 往返递增 nonce。真实 sender 把 rule、severity、event、nonce 与 episode identity 交给 `im-notify` 的持久 signature ledger 去重。
4. **U12：liveness 权威放在进程边界**：`./run.sh performance-probe` 用外部进程 watchdog 包住真实 probe，16 分钟超时后终止进程树；进程内 15 分钟 `SIGALRM` 负责杀 browser worker 进程组并退出，作为第二层兜底。
5. **U16：调度载体改为 per-file launchd**：probe 专属 plist 使用 `StartInterval=300`、`RunAtLoad=true`，`ProgramArguments` 经 `./run.sh performance-probe`。install/uninstall/status 以 destination 存在性和 label/path ownership 管理 regular plist，支持精确 legacy symlink 迁移并对外来同名文件 fail closed。probe 不再编辑共享 crontab；pipeline 继续使用既有 `*/15` user crontab。
6. **6 小时时效门槛以 live 数据放行**：2026-07-26 的 L2-4 实测覆盖 4 journey × origin/public 共 8 个 cell。生产 pipeline 约 60% busy，每 cell 约每 14 分钟得到 1 条 idle 样本；全部 cell 在 296 分钟（4.93 小时）取得第 22 条样本，低于 6 小时门槛，裕度约 1.07 小时。

## Consequences

- PERF page 现在表达“同机 pipeline 空闲时仍超过旅程预算”，不再用 busy 基线推导 notice；busy-contention 噪声由“不采 busy”消除。
- 20+3 窗仍在。`StartInterval=300` 是尝试 cadence，不等于每 5 分钟得到样本；“4.93 小时上膛”是该次生产负载下从零到 22 条的数据准备实测，不是固定检测延迟或区域 SLO。
- 6 小时 PASS 的约 1 小时裕度依赖 pipeline 不变得更忙。新增信源、interpret 变长或 pipeline 占用比例上升都可能吃掉裕度；任一启用 cell 从零到 22 条超过 6 小时都必须视为契约失败，不能事后放宽门槛。
- busy-only 退化可能不被 same-host probe 捕获；需要该视角时应建设 off-host synthetic/RUM，而不是恢复已被生产前提否定的 gate/rollup。
- accepted documented limitations：刻意持续超过 6 小时地 `SIGSTOP` probe/watchdog 整棵进程树需独立外部/fleet watchdog；并发 install+uninstall 不受支持；恶意 mid-operation HOME path swap 超出 threat model；精确 label + generated plist + destination 的冒充在 `launchctl` canonicalize 后不可区分。
