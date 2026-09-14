# ADR-20260914-2307：AIHOT 原始 capture 默认保留 30 天

- Status: accepted
- Date: 2026-09-14
- Supersedes: `scripts/capture_aihot_daily.sh` 中 2026-09-07 采用的 14 天默认保留决策

## Context

AIHOT 原始 capture 是本仓每日采集任务保存的验证证据，不是 AIHOT 服务端强制只留 14 天。AIHOT 服务端约 7 天的滚动覆盖只决定可重抓窗口；本仓此前另外选择 14 天作为本地清理默认值。

2026-09-14 的现场读数为 4 份 capture、合计 188 MB，单份大小并不固定。旧的 14 天估算不能可靠外推 30 天容量，但更长的本地保留能为持续积累同时间段的 AIHOT 与 AI Radar 数据留下更多原始证据。

用户 2026-09-14 明确要求保留 30 天数据。该要求只指 AIHOT raw capture，不包括 `data/eval-fit/runs`、validated windows、抓取频率、生产部署或历史回填。

## Options Considered

### Option A：把仓库默认值改为 30 天

- Pros: 后续部署共享同一默认值；直接满足 30 天原始数据保留目标；仍可由环境变量显式覆盖。
- Cons: 长期磁盘占用高于 14 天，实际容量要随数据积累观察。

### Option B：只修改当前机器的 `.env`

- Pros: 不改变仓库默认。
- Cons: 采集脚本本身不读取 `.env`；只改 `.env` 不能证明实际调度环境导出了该变量，也不能改变其它部署的默认值。

### Option C：同时把 eval run 保留期改为 30 天

- Pros: 两类目录继续使用相同天数。
- Cons: eval runs 与 raw capture 是不同资产；扩大 eval run 范围不在用户本次要求内，并增加额外存储成本。

### Option D：保持 14 天

- Pros: 维持较低存储占用。
- Cons: 不满足用户明确提出的 30 天保留目标。

## Decision

选择 Option A：`scripts/capture_aihot_daily.sh` 的 `AIHOT_CAPTURE_RETAIN_DAYS` 缺省值改为 30。显式环境覆盖继续有效，`0` 继续表示全部保留。

`data/eval-fit/runs` 独立保持默认 14 天；同步移除把它描述为“与 AIHOT capture 同窗口”的旧说明。validated windows 继续不由这段 retention 清理，抓取频率不变。

决策评审首轮指出 `.env` 备选的原否决依据不准确，并发现两处会因 30/14 分离而失真的 eval-run 注释；修正后由同一评审者复核放行。

## Consequences

- 后续未显式覆盖的日常 capture 清理会保留最近 30 天，而不是 14 天。
- 已被清理的旧 capture 不会因此恢复，本决策也不声称已有 30 天历史。
- 30 天后的实际占用尚未实测；现场 188 MB/4 份只描述 2026-09-14 的当前状态，不是容量保证。
- 本次只修改代码与文档并运行隔离 fixture，不执行真实抓取、不删除现有数据，也不宣称生产 cron 已消费新默认。
