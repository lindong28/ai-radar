# ADR-022：A6 在途成本作为下界继续正向评估

- 状态：Accepted
- 日期：2026-08-11
- 范围：A6 rolling 24 小时窗口唯一缺口为当前上海日、pipeline owner 仍 live 且没有明确计量失败的评估
- Supersedes：ADR-020 中「近 24 小时计量不完整时统一降级」对上述在途窗口的处理

## 决策

pipeline 仍在运行且当前日尚未封口时，A6 继续把已经记录的 cache 中性目录价成本与既有 notice/page 阈值比较。缺失的调用只能使金额少算，因此当前金额是真实完整窗口成本的下界：下界已越 notice 或 page 阈值时，允许首次 firing 和同一 episode 内的 notice→page 升级。下界未越阈值时，它不能证明事件已恢复；已有 firing episode 保持原状态，直到一次封口后的完整评估确认恢复。

该例外只适用于 `_a6_measurement_in_progress` 能证明的在途状态：问题日恰为当前上海日、没有已知 metering failure，且 lock owner 的 PID 与 process start identity 仍 live。旧日缺口、stale owner 或明确计量失败仍是不可评估，走 degraded lifecycle。

## 被否方案

不为 hold 增加固定 timeout 或 progress check。等待时间不会把不完整下界变成恢复证据；超时后允许向 ok/degraded 下行，会重新引入同一未解决事件的假恢复和 episode 分裂。pipeline 长跑或卡死由 pipeline health 告警负责，A6 只回答成本下界是否已经越线。

## 取舍与验证边界

连续运行可能延后一个既有 A6 episode 的恢复，但不会阻止首次告警或升级；这是宁可等待封口证据、也不以不完整金额宣布恢复的有意取舍。生产环境不能为了验收安全制造真实高成本 page，因此必须用端到端状态机测试覆盖在途首次 page、notice→page、低于阈值 hold 与封口恢复，并以生产 baseline/usage rows 的 volume 反事实验证阈值方向；报告不得把反事实称为真实投递。
