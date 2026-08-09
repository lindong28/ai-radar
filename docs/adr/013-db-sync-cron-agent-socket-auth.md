# ADR-013: DB sync 自动化用 launchd ssh-agent socket 发现做 cron SSH 认证

- Status: accepted
- Date: 2026-08-09
- 关联: 迁移 plan 20260719 遗留的「DB 同步挂自动化」TODO

## Context

news.aiplanet.live 由腾讯服务器上的只读 DB 副本承载，副本更新靠 Mac 侧 `deploy/sync/sync-db-to-server.sh` 推送。该步骤一直是手动的，2026-08-08 起无人执行导致公网停更一天半。用户选定每 4–6 小时的自动同步档位后，需要让 cron 环境下的 SSH 认证可用：cron 是非交互 shell，无 `SSH_AUTH_SOCK`，而同步 key（ssh 对该 host 解析出的 IdentityFile，当前 `~/.ssh/id_rsa`）有 passphrase，只有经登录用户的 ssh-agent 才能认证。

## Options Considered

### Option A（选定）: 运行时发现 launchd ssh-agent socket

wrapper（`deploy/sync/sync-db-cron.sh`）逐个探测 `/var/run/com.apple.launchd.*/Listeners`，用 `ssh-add -l` 按目标 key 指纹（默认从 `ssh -G <host>` 解析出的**全部** IdentityFile 推导、命中任一即可，`AI_RADAR_SYNC_KEY_SHA` 可覆盖）找到持有同步 key 的 agent。

- Pros: 复用现有 agent 通路，不改共享 SSH 配置，不新增长期凭据。
- Cons: 依赖用户保持登录且 agent 已加载 key；重启后未登录期间同步失败（非静默，见告警链）。

### Option B: ssh config 加 `UseKeychain yes` 从 keychain 取 passphrase

- 否决：passphrase 是否已入 keychain 未确认；改动 `~/.ssh/config` 影响该 host 的所有 ssh 使用；同样依赖登录会话（keychain 解锁），约束面与 A 相同但改动面更大。

### Option C: 专用免 passphrase 部署 key

- 否决：在磁盘上留一把任何本机进程可直接使用的生产服务器明文 key，扩大长期凭据面。

### 不改（保持手动）

- 已被用户否决：手动即本次公网停更的直接原因。

## Decision

Option A，配套告警闭环：

- crontab 条目用 `run-or-alert --key ai-radar-db-sync --` 包住 wrapper——非零退出经 im-notify 告警（dedup，成功自复位）。run-or-alert 的 dedup 身份含退出码，wrapper 按故障类别用不同退出码（2=无可用 agent / 推导不出指纹，3=sync 本身失败，4=上传成功但副本 stale，5=receipt 连续多轮不可读、staleness 检测已失明），故障类型切换会重新告警而不是被上一条压住。
- wrapper 每轮开跑前在**服务器自身时钟**上计算 `accepted-snapshot.json` 的年龄做 freshness 预检（阈值 660 分钟 = 2×cadence + 1h slack，两端时钟偏差不影响）。**确认超阈**不阻断本轮 sync（修复优先），sync 完成后以退出码 4 上报「上一周期未被接受」，覆盖 `sync-db-to-server.sh` 以 `--no-block` 交接后远端 apply 拒绝快照的盲区；receipt **读不到**单次只记日志不告警——它不是 stale 的证据，且服务器真不可达时 sync 本身就会失败并告警；但连续 3 轮（可配）读不到时以退出码 5 上报「staleness 检测已失明」，防止路径错误 / 权限异常造成的永久盲区。
- cadence 取每 5 小时（用户选定 4–6h 档内取中值），分钟取 41 避开整点。

## Scope 与已知未验证项

**作用域**：仅本 Mac、单用户、用户保持登录且 agent 已加载同步 key 时成立。重启后未登录期间每轮失败并告警，登录后自愈。不适用于多用户机器或无人值守服务器。

**检测延迟**：远端持续拒绝快照时，年龄先要越过 660 分钟阈值、再等到下一个 cron 采样点才被上报——最坏约 16 小时（660 分钟 + 一个 5 小时 cadence）。cron 自身停跑（crontab 被删、crond 不在跑、主机长期关机错过调度）没有独立观察点，run-or-alert 观察不到"没被启动"，见下方未验证项。

**已验证**：真实 crond 环境探针（agent 发现 + 目标指纹匹配 + SSH 认证）通过；最小 env 下 wrapper 走通指纹推导→agent 探测→receipt 预检→进入传输。

**未验证 / 残余风险**：重启后未登录时段的失败路径（仅推理）；Option B 的 keychain 状态；**cron 调度自身缺席不可被本方案检测**（告警器在同一 cron 调用内，任务没被启动就没有退出码可观察）——补上独立观察点的路径是启用远端 health-check 链路（`ai-radar-alert.timer`，需先在服务器装 im-notify 并把 `AI_RADAR_FRESHNESS_MAX_AGE_MIN` 从默认 150 调到 ≥660）或在本机 launchd alert 作业里加副本 freshness 规则，均未实施。该残余风险经 review 上报后由用户于 2026-08-09 显式 waive（理由：常开生产机上 crond 缺席属低频事件，且与既有 pipeline cron 的同类盲区同级，非本次新引入的风险类型）。
