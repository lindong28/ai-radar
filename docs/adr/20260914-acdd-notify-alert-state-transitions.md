# 20260914-acdd：告警只按状态变化投递，并把不可评估与恢复分开

- Status: accepted
- Date: 2026-09-14
- Supersedes: ADR-008 的 episode 内 cooldown reminder 与 page resolved 通道；ADR-011 的“不同 reminder 递增 nonce”；ADR-20260904-51d2 中“当天尚无有效完整 fetch 轮时 items-floor 仍独立评估”的部分

## Context

2026-09-01 至 2026-09-14 的成功投递账本共有 831 条非 INTERNAL firing，但按 ADR-021 的 `(rule_id, severity, episode_since)` 身份只有 89 个 episode，742 条是同一 episode 的重复投递。A5 两次真实停更合计产生 196 条 firing，最长一次持续数天；高频重复没有让事故更早被处置，反而与用户报告的告警疲劳同时存在。

历史里还出现了四类会误导处置的信号：A4 在当天首轮正常 pipeline 尚未完成时以 `items 0 < floor 1` page，约 6 分钟后随正常入库恢复；A2 仅后台 P95 越线也立即 page；A1、A6 与 PERF 在样本或完整计量消失时把事故报成恢复；A1/A2/A5 的通用合并只凭同轮 firing 与 heartbeat freshness 宣称同因，缺少 provider、error class、onset 或共同 incident identity。A7 的 407 条 page firing 中有 299 条只有一个 actionable 静默源，单源故障是真信号，但不等于整个 Radar 需要立即处置。

用户在本轮明确选择“仅状态变化”“历史核心闭环”“单源改 notice”。行动前的独立决策审查首轮要求补齐 ADR-20260904-51d2 的交界；补入该选择的完整原文和 A4 两个正常首轮反例后，复核放行。第二轮实现审查随后指出：若把 A4 上膛条件收窄为 `attempted > 0`，当天首轮在 fetch 前被 egress preflight 阻断时，真实断流也会被 warm-up 遮蔽。用户据此明确批准修订本决策：以“当天已有任意完整 fetch”或“当天任一非 SKIP 轮已明确观察到非 healthy egress preflight”上膛，同时不扩大 A2 的 fire 条件。

## Options Considered

### Option A：保留 30 分钟 reminder，只调阈值

- Pros: 持续事故会反复出现在通知流里。
- Cons: 保留了本窗口 742 条重复投递的主噪声；没有阅读或确认信号证明重复提醒有效，也不能修复假恢复和无因果合并。

### Option B：改成每日或分阶 reminder

- Pros: 比每 30 分钟少，仍会周期性提醒。
- Cons: 仍把未验证有效的重复打断当成默认；新增时间阈值，但没有用户确认信号可校准它。

### Option C：只按状态变化投递，并修复有历史反例的判定与恢复语义

- Pros: 一次事故只产生一次 firing；投递失败仍可重试；severity 升级、新 episode 与真实恢复仍可见。修复范围直接对应生产反例。
- Cons: 持续事故不会再次打断用户；若首次通知被用户忽略，系统没有阅读/确认信号可据以再次提醒。

## Decision

选择 Option C。中央 A1–A7/PERF 状态机只在新 episode、severity 转换与真实 resolve 时投递；同 episode 已成功投递后不再按 cooldown 生成新 nonce，失败投递继续复用 pending nonce 和首次待投递快照重试，即使下一轮暂时不可评估也不把待投递事件吞掉。新 episode 不继承上一 episode 的通知节流。page episode 的 resolved 统一走 notice 通道。

A4 的 items-floor 在上海自然日内已有任意完整 fetch（包括合法的 `attempted = 0`），或当天任一非 SKIP 轮已明确记录非 healthy egress preflight 时 arm；A2 的心跳归因仍只读最新非 SKIP 轮。fetch 失败率、账户层判断与 A2 的 fire 条件保持独立。这样午夜首轮尚未开始时不 page，真实 preflight 断流不会因后续未完成轮而重新被 warm-up 遮蔽，完整空轮也不会让 items-floor 永久失效，且不引入固定 grace 或通用 debounce。

A2 仅 P95 越线时使用 notice；错误率或 heartbeat 同时越线时仍为 page。A7 恰有一个 actionable 静默源时使用 notice，两个及以上仍为 page；quiet-X 与 unevaluable 范围不计入 actionable 数。page 与 notice 双向转换都作为 severity 状态变化投递。A7 连续 firing 期间若当前 actionable source 集合与已宣布 episode 的 source 集合完全不相交，旧 episode 尽力写 INTERNAL 闭合事件并为新集合开启新 episode；审计行写失败不阻断新 episode 通知。有交集的集合变化继续视为同一 episode，避免把轻微抖动重新通知。

不可评估不再作为恢复证据：A1 样本少于既有最小样本数、A2 三个阶段样本全为零、A6 计量不可评估、PERF 没有新鲜样本时，结果进入 `in_progress`，保持已经宣布的 episode，不发送 resolved。删除 A1/A2/A5 仅凭共现的通用合并；W1 对 A2 heartbeat 的专用合并仍按 ADR-20260904-d708 的反事实锚执行。

消息只做与这些历史问题同域的最小修正：A1/A2/A3 补用户影响，A2/A5 给出具体日志入口，A5 直接呈现当前紧急度，删除标题后重复的“故障类别”，原始 preflight 调试载荷不进入主视图。

## Scope and Unverified Boundaries

本决策只覆盖中央 A1–A7/PERF 共享状态机与上述规则、消息。D3、W1 专用恢复与合并、Wechat2RSS 独立发送、部署 healthcheck 及未进入共享 ledger 的发送源不变。A2 的缺样本修复只覆盖三阶段全零，不宣称解决所有 partial-sample starvation；A7 只定义 1 与大于等于 2，不引入比例阈值。

账本证明 sender 成功调用，不证明飞书客户端 exactly-once、用户实际阅读或确认。窗口跨多个代码版本，历史实例未逐条绑定当时源码；当前代码的对应控制路径需由聚焦测试和历史回放验证。真实 sender 验证会发送通知，本次明确不执行。没有 reminder 后用户忽略首次通知的发现时延仍无上界；这是用户本轮明确接受的取舍。

## Consequences

按本窗口静态回放，同 episode 的 742 条重复 firing 不再投递；真实事故的首次 firing、升级、恢复、A7 完全换批和发送失败重试保留。恢复通知不再占用 page 通道，单源 A7 与 P95-only A2 仍被记录和知会，但不再作为立即处置事件。

状态机会更保守地保持无法证明恢复的 episode，因此管理面可能更久显示 `in_progress`。后续若要恢复周期提醒，必须先建立能区分“首次通知已被看见”和“未被看见”的确认信号，再另立决策；不得仅换一个 cooldown 数字恢复重复投递。
