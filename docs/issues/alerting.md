# Issues — 告警设计质量

来源：2026-08-18 对 A1–A7 全告警面按 `~/.claude/references/alerting-review-principles.md` P1–P9 逐条评审（每条原则一个独立 reviewer）。本轮只交付了 A4/A7 处置指引的文案更正；下列各条是**同批发现、未在该次改动内闭合**的项，全部独立于那次文案改动而成立。

分工：投递与去重契约（`im-notify`、dedup-key）归 `~/.claude/references/service-operations-protocol.md` §6，不在本文件。成本口径相关的未闭合项在 [cost-observability.md](cost-observability.md)。

## ISSUE-A01 · A4 在故障持续中发出「已恢复」，此后长时间静默

**状态**：open · **优先级**：critical · **原则**：P7 / P1

两个缺陷叠加，后果是 A4 在它最该起作用的场景里失效：

1. **resolve 侧无对称确认**。`_apply_alert_results()` 的非 firing 分支不查 `_debounce_window`，任何一次非 firing 评估立即发「已恢复」；同时 `_ok_lifecycle` 清空 `since`，下次 firing 从头重算。fire 需要连续 30 分钟，clear 只需要一个采样点。
2. **fetch 崩溃时分母消失**。失败率由 `failed / attempted if attempted else 0.0` 得出，而 `attempted` 只来自 `=== attempted=` 汇总行。fetch 阶段崩溃或仍在运行时该行不存在，`attempted=0` 直接产出 0.0% 健康值。非 SKIP 轮中有数十轮无汇总行（两次复核用不同 SKIP 判据分别得 18/616 与 70/668，口径待统一；方向一致）。

生产实证：2026-08-17 的海外出网中断从 00:30 持续到 13:30，A4 在 00:31 与 01:16 两次发出「已恢复」；此后到 14:00 之间 A4 判定 **firing 76 次、ok 68 次、`send A4` 0 次**——而同期每轮仍是 `attempted=162 failed≈127`。所以症状不是「读成健康」，是**判定为 firing 却一次也没投递**：resolve 清空 `since` 使下次 firing 从当时重算，而假 ok 每隔几轮打断计时，notice 档的 30 分钟连续去抖永远达不成。同日 14:30 那轮 fetch 以 `sqlite3.OperationalError: database is locked` 崩溃、未落汇总行，两分钟后 alert-check 记 `A4 ok 最近 fetch 失败率 0.0%`，是假 ok 的一个已验证实例。

**闭合方向**：`attempted=0` 判为「不可评估」而非 0.0%（或跳过尚未落汇总行的当轮日志）；resolve 侧加与 fire 对称的确认窗，并把去抖语义从「连续 since」改为「M 轮中 N 轮失败」——占空比不足的持续故障当前不是被去抖，是被彻底静默。

## ISSUE-A02 · A3 的 5xx 支路自上线以来从未上膛

**状态**：open · **优先级**：critical · **原则**：P9

`min_pv=20` 在本部署基本不可达：`logs/serve-access.log` 的 5383 个 15 分钟窗中 PV 中位数为 5，只有 8.3% 的窗达到 20，且最近 400 行访问日志里 390 行是 127.0.0.1 的 healthz 探测。`data/alert-events.jsonl` 里 A3 的全部 firing（2026-08-18 复核时 52–53 次，随解析口径略有出入）**都**来自 healthz 支路，firing 时刻记录的 `server_pv` 从未达到过 20。

healthz 不覆盖同一失败面——它探本机 API 存活，探不到「站点活着但对用户返 5xx」。而消息在未评估时仍输出 `用户侧 5xx 率 0.0%`、`evaluation_state` 保持 `healthy`，即 P9 所说的「监控看着在、其实是瞎的」。

**闭合方向**：按真实 PV 分布重定 `min_pv`，或改用绝对 5xx 计数支路；无论如何，未达样本量时必须显式标为不可评估而非输出 0.0%。

## ISSUE-A03 · A7 的可评估集合常态只覆盖四成来源，且降级无出口

**状态**：open · **优先级**：high · **原则**：P9 / P1

`min_history=5`（近 30 天）使 162 个启用源中 98 个常态处于 unevaluable，其中 94 个是 X 源（109 个 X 源里只有 15 个可评估），51 个源至今零 item。零产出源在攒够 5 条之前永远进不了评估集，因此真正坏掉的（handle 失效、账号封禁、scope 不足）与「新源还没热身」在输出里完全同形。

降级没有任何出口：A7 上线后的 95 轮全部处于 `scope_limited`，一条通知都没发过；`metrics.py` 的 admin degraded 列表只收 `degraded` / `in_progress`，`scope_limited` 被排除，dashboard 上看不到；CLI 把它渲染成 `recorded-scope`——这个词是为 A6 的成本口径造的，对「98 个源没被监控」零指称。根因是 `scope_limited` 被 A6 当作正常态使用，于是无法再承载「监控降级」语义。

**2026-08-23 复核**：覆盖率已显著改善但缺口未闭合，且新增两项读数。按 `_silent_source_signal()` 实测：163 个启用源中 **evaluated=108、unevaluable=55**（原记 162 中 98），其中 X 源 109 个里 58 个已可评估（原记 15），至今零 item 的从 51 降到 12。

新增第一项：**阈值统计量存在自我遮蔽**。`threshold = 2×(720h / 近30天条数)` 随条数下降而变宽，而条数下降正是源正在死亡的签名；降到 `min_history` 以下即转入 unevaluable、永不 page。同一机制在实测中呈现为 `x_sama`（n=5，已静默 82h，阈值 288h，判健康）与 `x_googleai`（n=1，已静默 127h，不评估）。这是 P9 缺口在阈值公式一侧的根因，与 ISSUE-A05 第 2 条（死满 30 天反而 resolve）是同一机制的两个出口。

新增第二项：**该盲区当前是良性的，不构成在办漏检**。抽查 6 个盲区源（`x_polynoamial`/`x_googleai`/`x_jensenhuang`/`x_perplexity_ai`/`x_anthropicai`/`x_sama`）直查 X API 带各自 `since_id`，全部 `result_count=0`，即账号确实未发原创帖、我们没有漏抓。故本条按已记优先级排期即可，不需插队；但也因此**不宜用「现在会报几个」当作重标定的验收信号**——实测任何硬上限档位现在都会立刻 page 掉一批已验证为正常的源（120h 档 6 个、72h 档 21 个），那是噪声不是检出。

新增第三项：**firing 分支根本不披露降级，本条原文未覆盖这一面**——它成文时 A7 从未 firing，可观察的只有非 firing 那条路径。两处各自独立：

1. `a7_detail` 的 unevaluable 计数只写在 `else`（非 firing）分支，firing 时整句被丢弃。
2. `evaluation_state = "scope_limited" if unevaluable and not a7_firing else "healthy"` —— firing 时无条件落到 `healthy`。`data/alert-state.json` 今日实录即 `state=firing` 与 `evaluation_state=healthy` 并存。

后果比非 firing 那条更重：运维**正据此告警行动**的那一刻，「55/163 个源不在监控内」既不在消息里、也不在状态里，而 `values` 里虽带 `unevaluable_sources`，`_format_firing()` 从不渲染 `values`。今日 17 次投递没有一次带上它——本次诊断即因此从"整批出网故障"假设起步。

`docs/operations/monitoring-alerting.md` 的 A7 行写「计入「无法评估」并在消息中给出计数」，与 firing 分支的实际行为不符，闭合时一并订正。

**闭合方向**：把「监控降级」与 A6 的「记录行口径」拆成两个 evaluation_state；A5 已有正确对照（可选集成关闭时干净跳过、不 arm；被饥饿时显式发「转为不可评估」而非 ✅）。降级披露须覆盖 firing 与非 firing 两条分支，不能只在「没事」时才说。阈值一侧的自我遮蔽需单独处置，且重标定的验收不能只看当前 firing 数。

## ISSUE-A04 · A7 在评估源数为 0 时输出「均在各自节奏内」并判 ok

**状态**：open · **优先级**：high · **原则**：P9

`evaluated=0` 且 `unevaluable=0` 时缺零值守卫，`evaluation_state` 落到 `healthy`、CLI 判 `ok`。A7 上线首轮的真实输出即为 `A7 ok 来源静默 - 0 个源均在各自节奏内`——完全的信号丢失渲染成完全健康。

**闭合方向**：`evaluated_sources == 0` 判为不可评估。

## ISSUE-A05 · A7 的 resolve 会输出 A6 的成本文案，且故障越久越容易被判恢复

**状态**：open · **优先级**：high · **原则**：P7 / P9

两件事叠加：

1. `_format_resolved()` 把 `evaluation_state == "scope_limited"` 当作 A6 专有分支，返回「记录行金额已回落 / 记录行证据」。而 A7 只要存在 unevaluable 源（当前恒为 98）且非 firing 就是这个状态——本部署里 A7 的**每一次** resolve 都会走这个分支，运维拿到的收尾消息说的是另一条规则的事。
2. 一个真死掉的源先进入 firing；随旧 item 滑出 30 天窗，`recent_count` 衰减到低于 `min_history` → 转入 unevaluable → 移出 `silent_sources` → `a7_firing` 变 false → 发 resolve。**源死满 30 天反而触发「已恢复」。**

A7 尚未在生产 firing 过，故两条都是代码路径分析、无生产实例。

**2026-08-23 更新：第 1 条已有生产实例，A7 首次在生产 firing。** `data/alert-state.json` 记 A7 `state=firing`、`since=2026-08-23T07:10:57+08:00`、`notification_sequence=17`，静默源为 `x_artificialanlys`（37.9h / 阈值 31h）与 `x_elonmusk`（14.1h / 阈值 13h）。该 episode 一旦 resolve 就会走进上述分支——直接构造 `_format_resolved(evaluation_state="scope_limited")` 实测输出为：

```
【AI Radar】✅ A7 来源静默：记录行金额已回落（since …）
记录行证据：… 个源均在各自节奏内，55 个源历史过稀无法评估
```

即本条从「代码路径分析」升级为**待发生的确定事件**，而非潜在风险。第 2 条仍无生产实例。

**2026-08-23 处置：两条均已闭合。**

第 1 条：`_format_resolved()` 的 `scope_limited` 文案改为按 rule 分派（`_SCOPE_LIMITED_RESOLVED_COPY`），A6 输出逐字不变，A7 得到自己的收尾文案。

第 2 条：新增 `faded` 判据把「转为不可评估」从「已恢复」里分出来——一个源若累计条目 ≥ `min_history`、近 30 天 < `min_history`、且静默超过 `max(floor, 2 × 其最近 min_history 条的典型间隔)`，则判为褪色；A7 非 firing 且存在褪色源时 `evaluation_state` 报 `degraded`，走既有 🟡「转为不可评估」通道。**不需要跨轮状态持久化**，故对本改动上线前就已褪色的源同样有效。

节奏基线**按条目数取样、不按时间窗**：停更源在任何近期时间窗内都没有条目，时间窗基线对它要么为空、要么装着更早的东西。这一取值经三轮对抗审收敛，前两次取值各自失手且失败方向相反，值得记下以免重蹈：

| 基线取值 | 失败形态 |
|---|---|
| 固定 6h floor | 每周一更的源在第 5 老条目滑出窗口时被误判死亡，还会把**他源**的真实 ✅ 劫持成 🟡 |
| 全生命周期均值（MIN/MAX/COUNT） | 单条数年前的回填记录即可把阈值拉宽到任何静默都够不着，停更源因此漏检、继续误报恢复 |
| **最近 `min_history` 条的跨度**（采用） | 三个已知场景均判对；免疫离群旧记录与节奏切换 |

回归覆盖：`test_a7_faded_source_closes_as_unevaluable_not_recovered`（真死亡 → 🟡，断言状态机交给 sender 的完整文本）、`test_a7_low_cadence_source_is_not_faded_when_it_ages_out`（低频源反向对照）、`test_a7_stale_backfilled_item_does_not_hide_a_faded_source`（旧记录污染）。三者均做过变异检验。

上线时生产读数 `evaluated=108 unevaluable=55 faded=0`，即当前无源被判褪色，真恢复仍走 ✅。

同轮另有两项与本条相邻、但不改变其结论的实测：**这次 firing 的两个源判定正确**——各自当前静默均超过其自身近 30 天历史最大间隔（12.0h / 27.3h），阈值恰落在历史 max 之上；**抓取链路无故障**，代理实发 `api.github.com/zen` 得 200，两源直查 X API 均 `result_count=0`（阳性对照 `x_zho_zho_zho` 同查法得 5）。故本条要修的是消息文案，不是判定逻辑。

**闭合方向**：resolve 文案按 rule 分派而非按 evaluation_state；源转入 unevaluable 时沿用 A5 的「转为不可评估」通道，不发 ✅。

## ISSUE-A06 · 同一根因的多条告警无合并，且缺可核验的共因判据

**状态**：open · **优先级**：high · **原则**：P1 / P5

2026-08-17 15:35 → 08-18 08:57 的出网事故窗口内，A2 与 A4 在同一轮检测里同时 firing 但分两次独立投递，全窗口约 25 次 `send A2 firing` + 约 14 次 `send A4 firing`，零合并。`_correlate_alert_results()` 以 `A5.firing` 为总闸，A5 全程 `degraded`，关联逻辑一次都没进入函数体；且 A2/A4 这一对本就不在它的 `suppressed_ids` 覆盖内。A7 一旦上膛会成为同一事故的第三路。

前置缺口：`AlertSignals` 里没有任何字段承载 fetch 失败的**错误类别**，所以现有信号面只支持「同一轮都 firing 就并」这种时间巧合式合并，而 P5 恰恰点名这种做法有并掉第二个独立事故的风险。既有 A1/A2/A5 rollup 同样只按 heartbeat freshness 与 co-fire 选 carrier，没有 provider/error-class 或 episode onset 共因，可吞掉独立的模型、阶段和解读事故。要合规合并，先得把「本轮失败的主导 error class」提升为一个 signal 字段，并为既有 A1/A2/A5 补因果锚。

**闭合方向**：先加 error-class 信号，再以它为共因锚点做 rollup。

## ISSUE-A07 · A7 无条件 page，严重度不随影响缩放

**状态**：open · **优先级**：high · **原则**：P2

`severity=PAGE_SEVERITY` 硬编码，`a7_firing = bool(signals.silent_sources)`。1 个长尾源静默与 162 个源全挂产生同一枚 🔴；impact 文案随数量缩放，severity 不缩放。

值得记下的是这个档位的由来：commit `24a103c` 的论证是「A4 全程按 notice 处理、未投递」——即 severity 被当作**可见性杠杆**用来绕开 ISSUE-A01 的投递缺口，而不是按影响 × 紧迫定档。A4 自身已证明这套代码支持双 severity（`a4_severity` + `debounce_minutes_by_severity`），A7 未采用。

**闭合方向**：整批静默 → page；单源或少数源静默 → notice。前提是 ISSUE-A01 先修，否则会退回到当初促成硬编码 page 的那个状态。

## ISSUE-A08 · A4 的 page 支路去抖为 0，每天日界处结构性误 page

**状态**：open · **优先级**：high · **原则**：P7

page 支路判据是 `items_today < daily_inserted_floor_elapsed`，而 floor 按日内已过分钟线性缩放：日界后第 12 分钟 floor 就变成 1，而 pipeline 15 分钟一轮、当日首轮入库要到 00:15 之后。于是每天 00:12–00:2x 存在一个结构窗口 `items_today=0 < floor=1`，去抖 0 即首轮 page。

三次已投递并迅速撤回的实例：07-24 `00:14:24 → 00:19:51`（5.5 分钟，resolve 时 items 626）、08-13 `00:15:34 → 00:21:01`（5.5 分钟，items 2547）、08-17 `00:16:06 → 00:31:25`（15 分钟，items 333）。都不是事故，只是当日首轮尚未落库。

**闭合方向**：给 floor 一个 warm-up（至少放过一轮 pipeline 周期），不必给它加 30 分钟去抖——真实断流的即时性应当保留。

## ISSUE-A09 · A4 的 notice 分支把「已知良性」写死，而背书信号钝到测不出真实事故

**状态**：open · **优先级**：high · **原则**：P1

fetch 失败率破线、items floor 未破线时，A4 无条件降级为 notice。P1 要求这类降级必须有覆盖同一失败面、且至少同等敏感的独立干净信号背书，而这里唯一的背书是 `items_today` vs `daily_inserted_floor`，两条都不满足：

- **敏感度**：近期正常日增量 430–510，全日 floor 127 ≈ 正常量的 25–30%，即「丢掉 70% 的摄取量」仍判 items 正常；而 fetch 分支在 40% 的**源**失败即触发，两者之间有一整段宽带。
- **累计量 vs 事件**：`items_today` 是当日累计、floor 按日内进度缩放，所以它实际上只探测「今天是不是一开局就死了」。日中开始的全站中断当天不可能再触发 items_low。

另：`CALIBRATION_BASELINE.a4.daily_inserted_avg = 424` 是 2026-06-15 用 8 天标定的，今日实测 5069——背书信号的标定基线已过期一个量级。

本轮已移除 `impact` 里「fetch 失败主要反映结构性源站波动」这句归因（它渲染在处置方向之上、读者据它就已决定不动手），但 severity 分档本身未动。

**闭合方向**：重标 floor；或让 notice 降级以「同期 items 增速未偏离基线」这类同等敏感的信号为条件。

## ISSUE-A10 · A1/A2/A3 的消息缺「影响」与「需否立即处置」两行

**状态**：open · **优先级**：high · **原则**：P3

这三条 `AlertRuleResult` 未传 `impact` / `urgency`，`_format_firing()` 对空值直接跳过该行。读者拿到的是标题与 `故障类别：` 行的逐字重复加一个内部指标，没有一句说「哪个用户的什么体验坏了」。同一通道里 A4/A5/A6/A7 都写了这两行，读到没有的那条时「省略」与「漏写」不可区分。这不是体长预算问题——A1 正文只有四行。

**闭合方向**：三条补齐 `impact` / `urgency`；顺带去掉共享 formatter 的 `故障类别：` 行对标题的逐字重复（A1–A7 与 PERF 均有零信息增量）。

## ISSUE-A11 · alert-check 日志无 rotation：告警侧的消费面

**状态**：open · **优先级**：high · **原则**：P8

runbook 把「人工监看 `logs/alert-check.log` 大小」写成缓解措施，但 `status.sh alert` 只打印路径、不报大小（`status.sh:72`），该缓解因此不可执行；`logs/alert-check.err.log` 连这层纸面缓解都没有，而它是 ledger fail-open 的唯一证据通道——`_record_event_rows()` 捕获异常后只 `LOGGER.error("notification ledger write failed …")`，本批 ledger 行就此丢弃（阴性读数：至今 `grep -c "notification ledger"` = 0，尚未发生过写失败）。`data/alert-events.jsonl` 在成功写入时会裁掉 14 天前的行，runbook 也给了 jq 配方；但 64 MiB 只是写前 guard，并非硬上限，单批写过界后的持续停录由 `ISSUE-ALERT-20260904-8f2c` 跟踪，不能再称“14 天 + 64 MiB 双门合格”。

**实体缺口 defer 给 [cost-observability.md ISSUE-013](cost-observability.md)**——rotation 本身归那条跟踪。此处只登记告警侧的消费面：`status.sh alert` 应暴露这两个文件的大小，否则 runbook 里那句缓解永远是空的。

## ISSUE-A12 · 告警消息不指向 runbook，A1 三类落点一个不占

**状态**：open · **优先级**：medium · **原则**：P3 / P6

`grep -n "runbook\|monitoring-alerting\|docs/operations" src/airadar/admin/alerts.py` 在本轮之前零命中——项目有一份 300 行 runbook 和可查的 `data/alert-events.jsonl`，推送消息里都不出现。本轮已给 A4/A7/W1 各补一条指针，其余五条未补。`/admin` 当前告警摘要也只显示 A1–A7 的 rule/detail，没有影响、紧迫度与第一步；状态文件不可读/格式无效也只有内部症状，没有说明影响与处置。W1 已在 T4 单独补出 install、logs 与 preflight 动作，并把恢复通知 pending 与“Chromium 仍缺失”区分开，既有 A1–A7 仍待统一。PERF firing 会指向证据文件，但 resolved 只有测量值，没有 evidence/runbook 指针，同属本条 P6 缺口。

其中 A1 是唯一一条 evidence / 日志 / runbook 三类落点一个都不占的（「检查 DeepSeek/模型供应商余额、模型权限与 provider endpoint」，无 URL、无路径、无命令）。A2 不给 `logs/pipeline-*.log` 与 `.pipeline.flock`（ADR-052 之后判活方式已改为内核 flock，凭 `ps` 判不出来）；A3 给了 endpoint 但没给 host:port，也不给 `logs/serve-access*.log`；A5 只有「pipeline 与 interpret 日志」这半句缺路径。

**另一处更危险**：`docs/operations/services.md` 有一份写得很好的出网代理诊断顺序，但那是 `/img` 的新加坡图片代理，**与 fetch 出网是两条链路**。运维按告警里的「出网链路」去 docs 里找会命中它——有一个看起来对、实际错的落点，比没有落点更糟。`AI_RADAR_PROXY_FILE` 在 docs/ 下零覆盖（不在服务清单、不在 `.env.example`）。

**闭合方向**：五条补 runbook 指针；把 `AI_RADAR_PROXY_FILE` 与 agent-proxy 这条链路写进 `docs/operations/services.md` 服务清单，并与图片代理那节明确区分。

## ISSUE-A13 · A6 的「至少 3 个基线日」门是死代码，14 个日桶含伪造日

**状态**：open · **优先级**：medium · **原则**：P9

已由 [cost-observability.md ISSUE-023](cost-observability.md) 跟踪，本轮复核确认记载与当前代码仍然一致、未修复。补充读数：当前基线窗（UTC 08-04…08-17）中 `llm_usage` 在 2026-08-08 零行（直接以 ¥0 进中位数），08-07 仅 243 行、08-15 仅 467 行，对比常态约 1000–1200 行/日。而实际告警文案写的是「14 UTC 日中 **14 个可比日**中位数」——「可比」是证据不支持的断言。

**闭合方向**：见 cost-observability.md ISSUE-023。此处只登记文案里的「可比日」措辞同属该缺口的消费面。

## ISSUE-A14 · A2 的 P95 支路以 page 投递，与它自己写下的影响判断矛盾

**状态**：open · **优先级**：medium · **原则**：P1 / P2 / P7

`thresholds.py` 的注释明写「延迟本身无用户影响、且总能自愈」「真故障由 stage_error_rate 和 no_success_minutes 兜底，不靠延迟分页」，但 P95 破线会追加进 `stage_reasons`，而 A2 的 `AlertRuleResult` 不带 `severity` 字段、落到 page 默认值。抬高阈值降低了 fire 频率，没改 fire 时的档位。

同条 A2 还把三类不对等的失败合并在一个 severity 下（stage 错误率、stage P95、超过 120 分钟无成功 pipeline）。心跳死确实该 page，但读者从 🔴 前缀读不出这次是哪一类，而两类的处置窗口相差一个数量级。A4 的 `a4_severity` 是同一文件里已有的正确范式。

**闭合方向**：P95 支路降为 notice 或不 arm；A2 按命中的支路定档。

## ISSUE-A15 · A2 rate 支路半数以上轮次未上膛，消息不声明

**状态**：open · **优先级**：medium · **原则**：P9

近 7 天 672 个 15 分钟槽的实测上膛比例：prefilter 46.3%（门 4，槽中位样本 3）、scoring **24.4%**（门 4，中位 1）、enrich 48.4%（门 2，中位 1）。未上膛时消息仍输出「各阶段错误率、耗时与 pipeline 心跳在阈值内」、`evaluation_state` 保持 `healthy`。

runbook 已自述该盲区、甚至写了「排障时不要把『A2 rate 未 firing』当成 pipeline 健康的充分证据」——但这句话只在文档里，不在消息里。有 A4 items floor 与 `no_success_minutes` 兜底，故 medium。

**闭合方向**：未达样本量时在消息里显式声明该支路本轮未评估。

## ISSUE-A16 · A5 的 urgency 把系统已算出的分支丢回给读者

**状态**：open · **优先级**：medium · **原则**：P3

`urgency` 是静态字符串「是——有合格积压时立即核查；无合格积压时先恢复可评估性」，两个分支都写着，但代码本轮已经判出是哪个（`a5_firing` 要求 `wechat_pending_count > 0`，`a5_degraded` 是它的反面），且 detail 里已印着等待篇数。读者要自己把 detail 的数字和 urgency 的条件对上才知道落在哪一档。A4 的 `impact` / `urgency` 按分支取值，是同一文件里的正确对照。

**闭合方向**：按分支取值。

## ISSUE-A17 · 阈值注释与测试注释仍以已下线的 nitter 立论

**状态**：open · **优先级**：low · **原则**：P7（周边）

`thresholds.py` 的 `Fetch sources (esp. the X/nitter feeds) can flap...` 与 `tests/test_admin_alerts.py` 里 debounce 测试的注释，仍把「为什么有 30 分钟去抖」的依据挂在 2026-08-17 已下线的 nitter 机制上。本轮清了 action 文案里的 nitter，这两处未动。

顺带：被删掉的那句 action 自述「（已加 30min 去抖，持续才告警）」与 `thresholds.py` 的 `{"page": 0, "notice": 30}` 不一致——它在唯一会把人半夜叫醒的那条支路上是假的。删除是对的，但它想描述的那件事（page 支路根本没有去抖）由 ISSUE-A08 承接。

## ISSUE-A18 · A7 正文给 name、处置指引要 source_id

**状态**：open · **优先级**：low · **原则**：P4

detail 用 `name` 渲染（如 `X: karpathy 静默 40.0h`），而处置指引要运维去 grep 日志里的 `source_id`；`source_id` 只存在于 `values`，不进 push 正文。本轮已把指引措辞从「该 source_id」改为「该来源」，但两者的映射仍需运维自己查。

**闭合方向**：detail 里带上 source_id，或在 `/admin` 告警详情暴露映射。

## ISSUE-A19 · A5 正文含无长度约束、且处置路径不消费的文章标题

**状态**：open · **优先级**：low · **原则**：P4

`最老待处理：{oldest_wechat_pending_title}` 无截断，标题任意长即可挤占整屏，而处置方向没有任何一步用到该标题。

**闭合方向**：截断，或移出正文。

## ISSUE-A20 · 热点候选缓存的 keeper 持续失败没有主动发现路径

**状态**：open · **优先级**：medium · **原则**：P1（值不值得告警）

ADR-060 引入的 `hot-candidate-keeper` 线程是热点榜唯一的生产者。它持续失败时（DB 长期 busy、水合每次抛异常、线程意外退出），当前只有日志：`hot candidates unready (...)` WARN 与 `hot candidate keeper iteration failed` ERROR。没有任何 fire 条件消费它们，`/healthz` 也不看热点就绪状态。

对用户的表现是「首页热点块偶尔不见了、`/hot` 一直说正在生成」——一个没有发现时限的静默降级，而不是具名事件。三态设计特意让降级变诚实，代价是它**看起来很正常**，因此更需要一条告警而不是更不需要。

**闭合方向**：以「距上次成功水合的时长」为 fire 条件（`max_stale` 的数倍即可判为异常），severity 取 notice 档；`/healthz` 或 `/admin` 暴露 `hot_candidates_age_seconds` 供其消费。注意别用「未就绪请求数」当判据——零流量时它恒为 0，与 keeper 健康时读数相同。

### 本地 sync 同一 streak 内原因升级（失败→失败+replica 已 stale）被 exit-code dedup 吞掉

- **现象**：2026-09-02 FTS manifest bug 致本地 sync 连续失败，02:34:58 最后一次成功→00:21:25 本地 sync 首次成功（该轮仍以 exit 4 报 replica stale），21h46m 零次成功。`run-or-alert --key ai-radar-db-sync` 在**第一轮**失败（07:04 本地，exit 3）就 `push=sent`（`~/.local/state/im-notify/alert-sent.log` 2026-09-01T23:04:35Z），延迟一个 cron 周期（4.5h）——本地失败并非无告警。但随后各轮 exit 仍是 3 → `skipped(unchanged)`；到 replica 超过 660min 阈值、消息内容已从"sync 失败"升级为"sync 失败 + replica 已 stale"时，exit code 未变，仍被 dedup 吞掉，直到 00:21 exit 4 才再次 `push=sent`。这与 `sync-db-cron.sh` 头注释"cause 变化会 re-alert"矛盾。
- **加固候选**：让 staleness 升级改变 dedup 身份（独立 exit code 或独立 key），使"已 stale"这一严重度跃迁能再次投递；对齐 alerting 设计原则（值不值得 page/严重度/去重）后再定。
- **附带竞态**：run-or-alert 退出时读的是 apply committed 之前的旧 replica 时间戳，即使本轮 apply 最终成功也会先报一次 stale 假阳性（00:21:25 同秒出现 `sync OK` 与 `FAIL(4)`；下个成功周期 re-arm 清除）。加固时一并看这个时点。
- **未核实的相邻重复面**：Mac sync wrapper 与 server healthcheck 都会在 accepted-snapshot receipt 超过 660 分钟时 page；目标部署是否同时启用两条 timer 本轮未读取远端状态，故尚不能断言已发生双告警。后续核实为同时启用时，将同一 replica-stale 事故的跨主机 rollup 纳入本条。

### Playwright 浏览器缺失时微信全文抓取静默降级为 RSS-only，无任何告警

**状态**：closed（2026-09-04） · **原则**：P1 / P3 / P5 / P6 / P7 / P8 / P9

- **现象**：2026-09-02 15:30 起 `~/Library/Caches/ms-playwright/` 为空（删除来源未核实），此后每轮 fetch 对全部微信条目打 `Failed to scrape WeChat article; using RSS item only … BrowserType.launch: Executable doesn't exist`，阶段仍 `fetch OK`、pipeline 全绿，持续 55 轮才被人读日志发现。
- **为什么值得告警**：这是「产出还在但质量明显变差」的一类——微信条目退化成只有标题/摘要的 RSS 项，`/wechat` 解读链路（interpret）拿到的是残缺正文。现有 A1–A7/D3 无「抓取降级路径命中率」维度。
- **闭合结果**：scheduled `pipeline.sh` 在 egress 通过后、fetch 前执行 `wechat-browser-preflight`；预期 executable 缺失/不可执行为 exit 1，自省失败为 exit 2，两者都在任何抓取前终止并建立 W1 page，终端和日志均给出停止范围、日志入口与动作。W1 复用共享 state/ledger，只有继承 pipeline 在 unlink 后仍打开、内容绑定 generation 的 fd capability，同时传入 flock fd、activity generation 与严格同轮 stage 日志共同证明整轮成功，且末端复检仍通过时才以 notice resolved；裸 `--resolve-after-pipeline`、缺 capability、借用其他持锁者的同 inode fd 或陈旧/错序/失败日志保持 W1 open。失败恢复保留 pending 自动重试；pending 期间再次失败会废弃过期恢复投影，恢复后 30 分钟内再次缺失也会作为新 episode 立即 page。只在连续 preflight 阻断、W1 onset 不晚于最后成功时间戳精确算出的 A2 心跳越线与 heartbeat-only 反事实同时成立时由 W1 合并 A2 heartbeat；A2 stage/P95 与 A4/A5/A7 保持独立，避免用取整分钟、不同采样时钟或首次观察时间冒充症状起点而吞掉同时事故。README、operations 与 CHANGELOG 已记录安装、退出码、整轮停止、直接 fetch 边界和 `alert_recovery` 语义。删除缓存的来源仍未核实：本机 09-02 15:00–15:30 统一日志、仓内清理脚本与 shell history 都没有提供可归因删除动作。

### wechat2rss healthcheck 的恢复通知与清键逻辑未经 /custom:review-alerting 全量审

**状态**：review complete（2026-09-04）；下列实体缺口仍 open

- **现象**：2026-09-04 修 `deploy/wechat2rss/healthcheck.sh`（健康时 `--dedup-clear` 全部五个 key、firing→healthy 转换发一条恢复通知，状态记 `deploy/wechat2rss/data/healthcheck.state`）。review-gate 要求服务告警改动叠加跑 `/custom:review-alerting`，用户本轮裁定 waive：只跑了 Codex 对抗审（含按 `human-facing-message-principles` 与成本 so-what 审这条恢复通知），未跑全项目告警审。
- **复核结果**：T4 的全量 P1–P9 独立审查已覆盖该面，原「尚未全量审」义务闭合。实体缺口仍在：五类 key 的严重度分档、与 A7 的共因合并、一次 unreachable 即 page、恢复 notice 通道、API/parse/noaccount/login 分支缺首个动作或具体 runbook、recovered 只泛指“下一轮 pipeline 日志”而未给 `logs/pipeline-*.log`、上游 raw error 与账号清单无长度上限、覆盖式 state 无有界 fire/resolve 历史，以及 `.env`/`RSS_TOKEN` 缺失时在告警函数建立前静默退出、使探针自身配置漂移不 page。它们独立于 W1 改动，本轮不顺带修改。
- **对抗审（Codex，4 轮）后按 stakes 保留为 MEDIUM、用户 2026-09-04 裁定收口不再修的残余**（都要探针自身管道先坏才触发）：① 两次 20 分钟探测之间「恢复又复发」观察不到，按同一事故延续；② 手动运行与 cron 重叠时状态/清键无串行化，可能一次错序或重复通知；③ 状态文件被改成持续不可写且跨两次同类事故时，第二次的恢复通知与上一次文案相同、被 `wechat2rss-recovered` 去重吞掉；④ 事故首次观测时间只到 UTC 分钟，同一分钟内同类事故结束又开始（仅手动与 cron 重叠）第二次恢复通知被吞；⑤ 恢复通知走 `--alert` 通道而正文写「无需动作」，是否改走 notification 通道待定；⑥ `im-notify --dedup-clear` 吞掉 unlink 错误并 exit 0（harness 仓 `HARNESS-20260904-ca88`）。修法候选：状态改为单调事故序号 + 不可写时恢复通知降为无去重。

### fetch 汇总把账户层失败（401/402）与网络层失败同形计入 failed，付费层用尽 9 小时无人被告知

- **现象**（2026-09-03T21:17Z → 09-04T06:17Z）：109/109 X 源每轮 `FAIL … 402 Payment Required`，`=== attempted=163 … failed=109/111` 连续 30+ 轮；现有 A 系告警没有按 HTTP 状态码分桶的维度，X 摄取归零 9 小时后才被人从日志读出（完整读数见 `archive/closed.md`「[resolved] X 源全部 402 Payment Required」）。
- **为什么值得告警**：402/401 只能由账户持有人处理（充值/换 key），与网络层失败的处置完全不同；混在同一个 failed 计数里，读者拿不到「要做什么」。
- **闭合方向**：fetch 汇总按状态码分桶输出（`failed_by_status={402: n, 5xx: m, …}`），当单一状态码占失败 ≥50% 且属账户层（401/402/403）时单独 page，文案指向账户而非网络；严重度/去重/文案按 `alerting-review-principles.md` 与 service-operations-protocol §6 走 `/custom:review-alerting` 落地。归属：独立单元，不在 program 20260820-content-align 内。

### 出网 preflight 连续失败 2h15m 使整条 pipeline 停跑，A2 虽 page 但归因文案指向「卡死/僵尸锁」（2026-09-04 18:15–20:30）

- **现象**：`logs/pipeline-20260904-{181501…203001}.log` 共 10 轮只有 `egress-preflight status=unavailable reason=status command returned 1` → `egress preflight FAIL (exit 1)`，fetch/score/curate 全未启动；20:45 轮 `status=healthy` 自愈（同类事件 09-03 06:29–07:45 也出现过）。`data/alert-events.jsonl`（键 `ts`，+08:00）：A2 page 于 19:32 firing（起停 77 分钟后，`no_success_minutes=120`）、20:05/20:38 续报，文案「最近成功 pipeline 已超过 122 分钟（期间连续 SKIP 1 次，疑似卡死/僵尸锁）」——真因是 preflight `status=unavailable`，日志里每轮都写着，告警没读它；A4 的 `latest_fetch` 读最近非 skip 轮，preflight FAIL 轮无 fetch 段、读数落回 0%（未 fire）。
- **缺口**：不是静默，是归因——A2 把「出网 preflight 不可用」报成「疑似卡死/僵尸锁」，读者会去查锁而不是 selector（消息原则 §1：系统已算出的结论没写进消息）；且 77 分钟的探测延迟对整条 pipeline 停跑偏长。
- **闭合方向**：① A2 的 detail/action 读最近轮的 preflight 状态行，`status=unavailable` 时归因写「出网 selector 不可用（reason=…）」并指向 selector runbook，而非「疑似卡死」；连续 ≥3 轮 preflight FAIL（45 min）可作为 A2 的提前触发；② A4/A2 的信号源不把无 fetch 段/preflight FAIL 的轮当作有效读数（与 plans/20260904-a4-account-layer-alert 的 (a) 同源，那一单元只修 A4 侧）。归属：独立单元，走 `/custom:review-alerting`。
- **追加读数（2026-09-04 21:30–23:00，同一根因再发）**：`domain-router-error.jsonl` 记 12:33Z（20:33 本地）router 进程 `exit_code=-15` 后重启，20:45 轮恢复；21:30/21:45/22:00 三轮再 `preflight FAIL`；22:15 与 22:45 两轮 preflight `status=healthy`（policy `domain-routing-v2`）**但 fetch 阶段 162/163 源 `EgressPreflightError: status command returned 1`**——preflight 与 fetch 是两个进程、各自跑一次 `check-proxy-status`，后者那一刻 rc=1，整轮 `inserted=0`；23:00 轮再 `preflight FAIL`；23:03 起交互与非交互 shell 各三次实测 rc=0、`overall_status=healthy`（每次约 5 s）。selector 侧 `domain-router-route.jsonl` 在 15:00Z 有 2 条 `outcome=failure` 与 2 条 `selected_route=zyt-fallback`。归因候选（未证实）：Tencent 线路瞬时失败 → `tencent_status_evidence=live-provider-probe` 判 degraded → 状态命令 rc=1 → 应用 fail-closed 让整轮作废。新 A4（完整轮 + 状态码分桶）对 22:49 轮的读数是 `fetch_evaluated=True、失败率 99.4%、failed_by_status={}` → notice（非账户层），与设计一致；但「同一次瞬时探测失败让整轮 163 源全废」这一放大效应属 selector/应用 preflight 的取舍，归 system-config 与 `airadar.egress`，不在 A4 单元内。
- **2026-09-04 23:20–09-05 07:00 续发与恢复读数**：受管 Tencent 隧道 master（`enable-proxy-tencent` 起的 pid 14796/74762，以及本 session 用逐字相同参数重建的 97908）都在建立后数分钟内停摆——ssh 与 ProxyCommand 的 `nc` 都活着、`ssh -O check` 报 Master running，但 socks/mux 不再响应；独立前台 `ssh -N -D` 隧道 3 分钟内 6/6 通；主机路由 mtu 列 1500；非交互 `check-proxy-status --repair` 两次 `repair=failed`。用户 09-05 早晨 `enable-proxy-domain-routing` 后 07:15 轮恢复（163/3 失败/35 入库）。根因未证实（候选：受管 MTU 主机路由未写入 → 大包黑洞；或守护流程干扰），归 system-config。
- **第二个实例（2026-09-08 11:00–14:30，同一归因缺陷原样复现）**：`logs/pipeline-20260908-{110000…143000}.log` 共 **15 轮**全是 `egress-preflight status=unavailable reason=missing status fields: direct_status,gcp_sg_standard_status,overall_status,policy_id,…` → `egress preflight FAIL (exit 1)`，零摄入 3h32m；根因是本机出网模式掉回 clash 而 `airadar.egress`（09256f0）钉死 `domain-routing-v2`、fail-closed 生效，用户跑 `enable-proxy-domain-routing` 后 14:45 轮 `PIPELINE DONE` 恢复。A2 于 12:11:01 首次 page（起停 71 分钟后），并在 12:47/13:21/13:55/14:29/15:02 续报，文案逐字仍是「最近成功 pipeline 已超过 N 分钟（期间连续 SKIP 2 次，疑似卡死/僵尸锁）」。**当时上面那条「闭合方向 ①」尚未实施，所以这次的读者仍被指向锁；实施进度见本条末尾。**
- **本实例新增的一条缺陷**：`consecutive_skip_logs` 读错了对象。它报 2（15:02 那次报 3），而这 15 轮里 SKIP 轮是 **0**——逐轮核过，它们全都取到了锁、跑到 preflight 才失败（`grep -o 'egress preflight FAIL\|pipeline SKIP: already running'` 逐文件确认）。这个计数器在本次事故中恒为噪声（来自事故窗口之外的轮次），却是消息里唯一的「证据」，把「出网不可用」写成「僵尸锁」的正是它。闭合方向 ① 的 `status=unavailable` 分支应把 `consecutive_skip_logs` 从该文案里撤掉，而不是与新归因并列展示。
- **一条被检验后否定的假说，写下来免得重新得出**：「卡住的 pipeline 会把自己的看门狗一起静音——SKIP 轮什么都不跑，包括告警阶段」。**两半都不成立。** ① `pipeline.sh` 里根本没有告警阶段（`run_stage` 只有 fetch/prefilter/score/enrich/curate/interpret），成功轮也不跑告警；alert-check 由**独立的** launchd agent `live.aiplanet.ai-radar.alert.plist` 驱动（`StartInterval` 300s），与 pipeline 的 flock 无关。② 实测：16:45 轮持锁 87 分钟（17:00/17:15/17:30/17:45 四轮 SKIP）期间，`logs/alert-check.log` 在 15:03–18:29 有 **29 轮**记录、每 ~7 分钟一轮，A2 在其中每一轮都判 `ok`。`data/alert-events.jsonl` 自 15:30:02 起 0 事件，是因为该文件只记 firing/resolved 的**状态跃迁**、不是每轮心跳——「无事件」在「看门狗被静音」与「看门狗跑了且判无事」两种情况下同形，不能据它判静音。
- **假说否定之后仍剩一条真实暴露，且与上面那条缺陷串联**：16:45 轮的 fetch 跑了 **76 分钟**（16:45:21→18:01:28，同日基线 19–24 分），整轮 87 分钟，而 A2 的心跳阈值是 120 分钟——这一轮吃掉 73% 的预算却全程 `ok`，没有任何按**阶段**计的时长闸看着它：`pipeline.sh` 全文 `grep -n 'timeout|TIMEOUT|alarm|SIGALRM'` 零命中，`run_stage` 就是一句裸 `./run.sh "$stage"`，唯一的界是 `enrich --limit 40` / `interpret --limit 30` 这两个**条数**上界，对单条目卡住无效。它这次自己跑完了；下一次若真卡死，唯一的探测通道仍是那个 120 分钟心跳，而它一旦开火，文案就是上面那条尚未修复的「疑似卡死/僵尸锁」。**先修文案再谈加超时**：没有阶段超时时至少还有心跳会响；归因错的时候，响了也没用。
- **那 76 分钟的成因追到一半，并否掉了最顺手的那个解释**：不是「出网恢复后的积压补抓」。同日各轮 fetch 的 `attempted` **恒为 162**，16:45 轮 `inserted=109` 居中，而 13.2 分钟的 16:15 轮做的是同样的活（162/40）、出网恢复首轮 14:45 反而只用 19.2 分钟（162/339，插入量最大）。**同样的活慢 5.8 倍 ⇒ 是卡，不是量。** 该轮 4 个失败源的签名全在出网层：`EgressPreflightError: status command returned 1`、`ProxyError: 503`、`SSL: UNEXPECTED_EOF_WHILE_READING`、`ConnectTimeout: handshake operation timed out`，与「出网 selector 在恢复后仍在抖」一致。
- **但定位不到是哪个源，因为 fetch 段没有逐源耗时**：整个 fetch 段（325 行）只有 2 行带时间戳（`=== fetch START ===` 与 `=== fetch OK ===`），逐源的 `OK <source> fetched=… inserted=…` 不带时刻，所以耗时分布**从来没有被测过**。
- **因此加阶段超时不是第一步，逐源耗时才是。** 三条理由：① 阈值要从耗时分布里取，而那个分布现在不存在——只有一个 76 分钟的观测，据它定阈是盲定；② 阶段级超时的粒度是错的：为了摆脱一个卡住的源而杀掉整个 fetch 阶段，会连带丢掉另外 161 个源这一轮的产出；③ 超时时长本身是一条**时效性/可用性**的非功能属性，追溯不到用户表达过的目标或既有契约，按 `~/.claude/CLAUDE.md`「非功能属性不自行加码」它的取值归用户，而「既有档位」此刻的诚实读数是**未实测**。逐源耗时是纯增量日志、无行为面，先加它，拿到分布再把阈值作为一次选择交出去。
- **第三个实例（2026-09-08 23:45 → 09-09 进行中）。它与前两个不同：真因分两段，A2 两段都报对了。** 逐轮实况（`grep -oE 'egress preflight (OK|FAIL)|pipeline SKIP'`，不折叠）：

  | 时段 | 实况 | A2 当时说什么 |
  |---|---|---|
  | 21:30 轮 | `egress preflight OK`，fetch 跑 **117.98 分钟**（21:30:09→23:28:07）后**正常完成**，整轮 23:38:43 `PIPELINE DONE (failed=1)` | — |
  | 21:45–23:30 | **连续 8 轮 `pipeline SKIP`**——上一轮还握着锁 | 22:34 / 23:06 / 23:43「连续 SKIP 7/9 次，疑似卡死/僵尸锁」← **当时是对的**，此刻确实没有任何一轮走到过 preflight |
  | 23:45 起 | 首个 `egress preflight FAIL`，至今 **35 轮**，零摄入 **8.8 小时** | 00:17 起「最近一轮出网 preflight status=unavailable，reason=missing status fields:…」← 也是对的 |

  **这正是闭合方向 ① 第一半的生产验证，但要按它真实的机制读，别读成「修复前报错了」**：该信号取**最近一个非 SKIP 轮**的 preflight 行，而 23:45 之前不存在这样的轮次——所以它在 23:45 前不说出网、23:45 后立刻说，是设计使然而非延迟。从首个可观测的 FAIL（23:45）到 A2 报出出网归因（00:17）是 **32 分钟**（即一个 alert-check 周期加一次轮转），不是前两次那种 71 / 77 分钟的探测延迟——**那个延迟量在本实例上不可比**，因为前 2 小时的真因本就不是出网。
- **本次真正的根因，与前两次同类但恢复路径不同**：`~/.config/agent-proxy/current-proxy` 于 **09-08 21:41:22** 被写成 `http://127.0.0.1:7897`（clash），而 `egress.py` 要 `stored_mode/effective_mode=domain-routing` + `policy_id=domain-routing-v2`，clash 的状态输出不含 `direct_status/overall_status/policy_id` 等字段 → `missing status fields` → fail-closed。
- **但出网通路本身没坏，坏的是那份出网证明记录**——这条区分决定了恢复动作：21:30 那轮在 21:41 掉模式**之后**仍把 fetch 跑到 23:28、enrich 跑到 23:37 且**全部成功**，因为它已在 21:30 通过 preflight 并带着代理环境跑；domain-router 服务自始至终活着（`agent-domain-router-run-service` 与 `tencent-zyt-selector.py` 两进程，服务于 127.0.0.1:59627）。**掉的只是模式记录，不是路由。**
- **恢复动作卡在机器层**：`_sc_clash_managed` 判为 **no**（clash 未接管，故 `egress.py` 的 pin 未过期，方向是恢复而非改 pin）；但重跑 `enable-proxy-domain-routing` 报 `agent-domain-router: parent lock handoff is not from this process parent`、`mode 未提交`——那个活着的 router 服务持着父锁而不是该命令的子进程。收敛需要先处置在跑的服务，属机器层动作，不在本仓范围内。
- **顺带确认了一条与告警无关、但属于 pipeline 稳定性的事实**：长 fetch 是**复发**的，不是孤例。09-06→09-09 四天内超过 60 分钟的 fetch 有三轮——`20260908-213000` 118.0m、`20260908-164500` 76.1m、`20260907-170001` 67.5m——**三轮全部自行完成**，但每一轮都让后续数轮 SKIP。它与本条告警不同源，处置见 pipeline 阶段超时的判断（结论：超时不适用于 fetch，因 70–118m 落在它自己的 p95 47m–max 118m 区间内，任何杀得掉它的阈值都会误杀正常轮）。

- **闭合方向 ① 第二半的价值已实测（2026-09-09），结果与本文档此前的陈述不符，据此订正**。此前记的是「若已实施可省 71 / 77 分钟」——那只描述了 09-04 与 09-08 两次。把判据（连续 ≥3 轮 `preflight FAIL` = 45 分钟）回放到 09-03 以来**全部 11 次**满足该条件的事故上，与 A2 实际首次 page 时刻对比：

  | 提前量 | 次数 | 说明 |
  |---|---|---|
  | **+918 分钟** | 1 | 09-03 20:45 那次，A2 直到 09-04 12:33 才报——**这一例是该触发器的全部价值所在** |
  | +36 ~ +63 分钟 | 4 | 09-03 06:45、09-04 18:15、09-06 05:15、09-08 11:00 |
  | +3 ~ +11 分钟 | 2 | 09-07 12:15、09-08 23:45 |
  | **−5 ~ −19 分钟** | **4** | 09-04 21:30 / 23:00、09-05 11:15、09-09 10:15——**它比 A2 实际开火更晚** |

  **所以它不是严格改进，设计上只能并联、不能替换**：A2 取「心跳支路」与「连续 FAIL 支路」谁先到算谁；写成替换会让那 4 次退化。中位提前量约 11 分钟，均值被那一例支配——**据均值论证它、或据「省 71/77 分钟」论证它，都会高估**。要不要为一个尾部事件付一条常驻规则的维护与告警量，是取舍不是读数，留给裁决。
  **复算方法**（判据变了要重跑）：逐轮扫 `logs/pipeline-*.log` 判 `egress preflight FAIL` / `OK` / `pipeline SKIP`，取连续 FAIL 段中第 3 轮的时刻，与 `data/alert-events.jsonl` 里该段之后首个 `A2 firing` 的 `ts` 相减。

- **2026-09-09 实现过一次并撤回。** 形态：新信号 `consecutive_egress_preflight_failures`（逐轮扫最新非 SKIP 轮，跨过 SKIP、遇 healthy 或遇无 preflight 读数即 break）+ A2 现有规则里并联一条开火路径（按 P5 不新起规则）。测试 171 passed、四个变异被抓。**独立对抗审查判定不可落，两条决定性 finding 我已各自独立复现**：

  1. **故障进行中会发出「✅ 已恢复」并随后静音 30 分钟**（比它要省的 45 分钟更坏）。新一轮起跑后、`egress-preflight` 那行落盘前的窗口里，最新非 SKIP 轮没有 preflight 读数 → 那个 `break` 给出 `streak=0`；此时若心跳未越 120 分钟，A2 整条翻成 not-firing → `_format_resolved` 发「恢复证据：各阶段错误率、耗时与 pipeline 心跳在阈值内」→ `COOLDOWN=30min` 让重新 firing 不再 page。**我的复现读数**：三轮被拒后 11:50 `streak=3 / mins=50`，12:00 新一轮刚起时 `streak=0 / mins=60`，故障全程未中断。该窗口的宽度正是本文档 N1 条实测的 3–18 秒、约 0.7–2% 的求值命中率。**这条同时把 N1 的定级作废**：N1 写着那个盲窗「朝『回落旧文案』坏，不朝『断言错原因』坏」并据此判 LOW——加了开火支路之后，同一个盲窗变成 firing bit 翻转 + 假 resolve。
  2. **两个生产级灾难变异完整通过全部 7 个新测试**（我复跑确认，各 `7 passed`）：① 去掉 `reversed` 改正序迭代——生产里会去数 7 天日志里**最早**那段被拒轮次，一次早已恢复的故障让 A2 永久 firing；② 无 preflight 读数由 `break` 改 `continue`——正是上面那条的开关。用例表 `[B,H,B]` 在正反两个方向都给 1，所以**结构上看不见迭代方向**；要杀掉它至少得有一个 `[B,B,H,B]`（正序 2 / 逆序 1）。我自己那轮四个变异全被抓，是因为它们恰好落在我写测试时想到的点上。

  **结构性根因，也是重做时要先解决的那个**：fire 侧去抖 3 轮、resolve 侧去抖 0 —— 非对称去抖必然 flap（P7）。所以正确的实现要给 resolve 侧加迟滞，那是改 A2 的生命周期，不是调一个开火条件；**按此重估，它不再是一个「小改动」**，而收益中位仅约 11 分钟。

  **重做的第一步已定位（2026-09-09，用户裁定重做，先加 resolve 迟滞）**，并订正上一段的一处措辞：
  - **开火侧已有可配置去抖**：`_debounce_window(thresholds, rule_id, severity)` 读各规则 section 的 `debounce_minutes` / `debounce_minutes_by_severity`（默认 0），由 `_transition_since` 用来算 firing 起点。**A2 的 section 里连 `debounce` 键都没有。**
  - **resolve 侧结构上没有迟滞**：`_ok_lifecycle` 直接返回 `state="ok" / since=None / announced=False`，不收 debounce 参数、也没有"连续 N 次判 ok 才宣告恢复"的概念。所以非对称不是配漏了，是那一侧没建。
  - **因此波及面比上一段写的大**：上面说「改 A2 的生命周期」——**不准确**，`_ok_lifecycle` 是**全部规则共用**的，加迟滞会改变每一条规则的恢复语义。重做时这一步要单独定档与评审，不能挂在 A2 的账上做。
  - **闸的位置已定位到一处，比上一条写的窄**：`_ok_lifecycle` 有 **7 个调用点**，各有其义，**只有一个是真正的"宣告恢复"**——`alerts.py:2570`，在 resolved 通知**已成功投递之后**。其余六处都不该加迟滞：`:2308` 是来源被人为暂停、`:2403` 是从没通知过（没有恢复可宣告）、`:2501` 是刚发完 firing、`:2583` 是兜底。**给这些加迟滞会朝反方向坏**——探针坏掉、这轮评不出来时告警反而一直挂着，正是 P9 的反面。所以闸要加在**决定构造并发出 resolved 通知之前**，不是加在 `_ok_lifecycle` 里面。
  - **`evaluation_state` 不能当判别器**（我查过才知道）：7 个调用点全部传默认的 `healthy`，它是在 `AlertRuleResult` 上设的、不在这里分叉。
  - **仍欠一个状态字段**：`_ok_lifecycle` 把 `since` 置 `None`，而迟滞要知道"连续不 firing 多久了"。持久化在 `data/alert-state.json`（`json.dump`，无 schema 版本号），加字段前要确认旧状态文件读得进来。
  - **同一处已有一个现成先例，照它写即可**：`alerts.py:2530-2537`，`_entry_announced(lifecycle)` 分支里的 `_a7_mixed_pause_resolution_has_current_evidence(...)` —— 它返回 False 时的动作就是 `state[rule_id] = project(...)` 然后 `continue`，即**保持 firing、跳过这一次 resolve**，正是迟滞要的形状。所以闸是"在这个分支里并联第二个守卫"，不必新造机制。欠的那个状态字段（"连续不 firing 多久了"）挂在 lifecycle 上即可，`since` 仍保持"firing 起点"的原义不变。

  - **做成按规则可选**（threshold section 里一个键，默认 0 = 今天的行为），这样只有 A2 改变恢复语义，其余规则一个字节不动——这也是它能留在 A2 这个授权范围内的前提。

  - **可复用的形状**：开火侧那套（threshold section 里的 `debounce_minutes` + 一个算"确认时刻"的纯函数）就是 resolve 侧该照的样子——不必另发明一套。

  **同一次审查另外指出、重做时一并要处理的**：消息从不说是哪条支路触发（正文头号数字仍是那个**未越线**的心跳分钟数，而 runbook 写着 120 分钟）；「已连续 N 轮」没有时间锚也没有阈值参照，实测可横跨 3 天或 9 个日志文件（`_load_pipeline_runs` 对 `logs/` 全量 glob，无时间下界，而 `pipeline.sh` 的 `find -mtime +7 -delete` 只在有轮次拿到锁之后才跑）；新支路专挑 `heartbeat_fresh=True` 的窗口开火，而那正是既有 correlation 把 A2 并进 A5 的窗口，归因会被丢掉；手动跑 `./pipeline.sh` 三次即可在生产完全健康时凑出 streak=3；阈值 `3` 只以调用点魔数存在、未进 `thresholds.py`、也无 `max(1, …)` 下限（配 0 则恒 firing）；新量未进 `values`，事故账上分不出是哪条支路开的火。
  **还有一条元问题**：上面那个「复算方法」在触发器上线后会**自我度量**——它要减的那个 `A2 firing` 从此就是本触发器产生的，差值恒 ≈0。重做时复算要固定在上线前的历史窗口上。

- **闭合进度（2026-09-08 更新）**：闭合方向 ① 有两半，本次只做了第一半。**已实施**：A2 的心跳支路读最近一个**非 SKIP** 轮的 `egress-preflight status=…` 行，非 healthy 时正文写「最近一轮出网 preflight status=… reason=…」、撤掉那个恒为噪声的 SKIP 计数，处置改为 `check-proxy-status --format=kv` 并指向本文档「出网 selector 的 preflight 与实际 route」节。**未实施、无人认领**：① 的第二半「连续 ≥3 轮 preflight FAIL（45 min）作为 A2 的提前触发」——本次明确排除（不改开火条件），于是同节测得的**探测延迟仍在**：2026-09-04 迟 77 分钟、2026-09-08 迟 71 分钟。这两项留在本单元里，不另开条目。
- **审出来的几条，写下为什么没在本次修**：
  - **`consecutive_skip_logs` 名不副实，else 分支与 `values` 里照旧**（基线独立 / 边界命中）。它数的是「距上次成功以来的全部 SKIP 轮」而非连续轮数，本次只在出网归因那一支把它藏起来；出网健康而心跳过期时读者在正文里拿到它；**而且两条支路都照旧把它写进 `values["consecutive_skip_logs"]`**——也就是进每一行 A2 事件账与 metrics API，下一次事故复盘读的正是那份账（09-08 这次复盘就被这个数字绊了一下）。**改它要动 A2 的既有语义、且会改变出网健康时的文案**，超出「只改归因」这次的范围，交用户裁决要不要单独做。
  - **`\breason=` 会命中非下划线分隔的后缀键**（如假想的 `route-reason=`）。当前 `egress-preflight` 输出里无此类字段，实测下划线形式（`comparison_reason=`）不命中。属推测性加固，按「不基于推测的风险预先加码」不改。
  - **消息转述的是 parser 诊断而非结论**：同一次事故的两种 reason（`missing status fields: …` 与 `status command returned 1`）都意味着「selector 掉回非 domain-routing」，连续两次 page 读起来像两个故障。要写结论就得在告警侧复刻 selector 的判定，那是 `airadar.egress` 的职责边界；本次选择原样转述 + 指向 runbook。
  - **N1（本轮修复引入，LOW，朝安全侧坏）**：只读「最近一个非 SKIP 轮」开了一个每轮 3–18 秒的盲窗。133 个真实轮次实测，从 `=== pipeline RUN ===` 到 `=== egress preflight OK|FAIL ===` 的间隔最小 3s / 中位 6s / 最大 18s（最大那个正是 `pipeline-20260908-111500.log`）。窗内磁盘上最新的非 SKIP 轮只有 RUN 行、还没有 preflight 行，于是**出网故障进行中的 A2 会回落到锁文案**。alert-check 每 300s、pipeline 每 900s ⇒ 约 0.7–2% 的求值落在窗内；09-08 那 3.5 小时共 6 次 page，其中至少一次带错文案的概率约 4%。**这是一次交换不是净赚**——改之前不存在这个窗（回读上一轮，而故障期间上一轮同样 `unavailable`、恰好是对的）。方向仍对：它朝「回落旧文案」坏，不朝「断言错原因」坏。要收掉有个便宜的判别式：要防的挂起轮是**老的**、启动中的轮是**新的**，`started_at` 分得开，且只有最新那一轮可能是新的。现有四个 `_collect` 测试全部用 `now=14:00` 对着 ≥3 小时前的轮次，覆盖不到这一档。
  - **N4（基线独立 / 边界命中，LOW）**：上面那条 `break` 让 L12 换了形状——一个没有 preflight 读数的非 SKIP 轮不再只是「不刷新读数」，而是**终止扫描**。于是任何非时间戳命名的 `pipeline-*.log`（按 ASCII 排在全部数字名之后、非 skip、无 preflight 行）会**静默且永久地关掉整个归因通道**，而不只是钉住一个陈旧读数。已核 `pipeline.sh:27` 恒写 `pipeline-$(date +%Y%m%d-%H%M%S).log`，`logs/` 里 770 份全是时间戳名、无实例。朝安全侧坏，但没有任何东西会告诉你归因通道已经死了。

### A4 账户层 page 的分母与"来源组"身份：按 source_id 前缀聚合不等于账户（2026-09-05）

- **现象**：ADR 20260904-51d2 (b) 用「Σ(401+402 失败) / 全站 attempted > 0.4」判账户层 page，来源组只用于消息文案、按 source_id 前缀聚合。review-alerting P1 指出两向偏离：小份额账户（如 5 个源的组）100% 失效只贡献 3%，永不 page；两个各自轻微的失效（22% + 20%）跨账户、跨状态码求和可叠加成 page。
- **决策评审读数（`plans/20260904-a4-account-layer-alert/decision-p1-per-group.md`，Codex 01a06eef…）**：「按前缀组分母」方案 6 条 blocker——前缀 ≠ 账户（`google_*` 是 5 个互不相关的公开 feed，却会被当成一个 API 账户给出充值/换凭证处置）；小组交 A7 兜底不成立（A7 要 30 天 ≥5 条 item 且静默超阈值）；ADR-008 状态机按 `rule_id+severity` 分 lifecycle、无组身份，多组先后失效串成同一 episode；新公式仍把 401+402 相加；`min_group=5` 只来自单轮拓扑；漏报无有界发现时间。
- **闭合方向**：需要「账户身份」作为一等契约（`data/sources.toml` 的 adapter/`required_env`，不是前缀）→ metrics 按账户输出 attempted 与按状态码分桶 → 按账户 × 状态码判定 → 账户级 lifecycle 或合并规则。属新决策包 + schema 改动，用户 2026-09-05 裁决另开；本轮保留 v5 全站分母并写进 ADR 已知边界。归属：独立单元（先 `/custom:create-plan`，实现前重走 decision-review）。

### 同一 X API 402 会让 A4 与 A7 各发一条 page 且处置方向冲突（2026-09-05）

- **现象**：review-alerting P5：X 组 402 持续超过 A7 的静默阈值（对高频 X 账号约 6h）后，A4 以 page 发「X API 109/163 源返回 402…充值」，A7 同时以 page 发「来源静默」并给「查出网 selector」的通用文案；`_correlate_alert_results` 只覆盖 A1/A2/A5，不含 A4/A7。ADR 20260829-a7f1 的新鲜收据抑制只在 X 读取仍成功时生效，402 时不成立。
- **闭合方向**：A7 计算静默时，对「最近一次失败可追溯到 A4 当前 firing 的同一账户/状态码」的来源标 `suppressed_by="A4"`（复用既有 carrier 模式），或让 A7 的 action 在与 A4 账户层重合时改用账户层归因。归属：与上一条同一决策包（账户身份是前提）。

### A4 共享一条 lifecycle：账户层已恢复而普通 fetch 仍超阈时 resolve 被掩盖（2026-09-05）

- **现象**：review-alerting P7：A4 三个子条件共享一条 page/notice lifecycle。账户层 page 宣告后，若账户已连续两轮回落但普通 `fetch_failed_ratio` 仍 > 0.4（如 egress 抖动接踵而至），本轮结果为 notice，状态机命中「已宣告 page 扛住 notice」分支——不发 resolved、不重发，运维等不到「账户已恢复」；最终 resolved 文案用那一刻的 detail，不提账户。2026-09-04 同一天先后出现 402 断流与 preflight 断流，这个交叉窗口真实存在。
- **闭合方向**：让 lifecycle 记住触发原因标签、resolved 引用最初原因；或给账户层独立 dedup 身份。需补「账户先恢复、普通条件仍 firing」的组合测试。归属：与账户身份决策包一并处理。

### A4 items 跌破 floor 时 `evaluation_state` 把「fetch 未评估」塌缩成 healthy（2026-09-05）

- **现象**：review-alerting P9-F2：`evaluation_state` 只在 `not firing` 时才取 `in_progress`；fetch 未评估 + items_low 同时成立时结果 firing=True、`evaluation_state="healthy"`，尽管 `values.fetch_evaluated=false` 与 detail 文字仍在。受影响的是依赖该结构化字段的下游（`/admin` 降级栏、`_format_resolved` 分支），读消息的人不受影响。
- **闭合方向**：允许 firing 与「部分维度未评估」并存（新增取值或独立字段）；需先确认状态机对 firing + in_progress 的投影语义。归属：独立小单元，走 `/custom:review-alerting`。

### fetch 失败里无 HTTP 状态码的部分没有覆盖率提示（2026-09-05）

- **现象**：review-alerting P9-F4：只有匹配 `Client error '<3 位>'` 的 FAIL 行进 `failed_by_status`；若 401/402 的错误文案形态变化（依赖库改消息、不同 HTTP 客户端），这批真实账户层失败会从 page 静默降为普通 notice，且没有信号说明「有 N 条失败未能分类」。ADR 51d2 只声明了「非 HTTP 的账户层异常走 notice」这一已知边界，未覆盖「是 HTTP 但正则不匹配」。
- **闭合方向**：metrics 统计 `unclassified_failed = failed − Σfailed_by_status` 并透传；差额大时在 detail 提示「部分失败原因未能分类」。归属：独立小单元。

### 同一 selector 事故会让 A2、A4（长期还有 A7）各自通知，无跨规则 rollup（2026-09-05）

- **现象**：review-alerting P5-2：selector/preflight 故障让 fetch 阶段整批 `EgressPreflightError` 并阻断成功 pipeline——A4 经 notice debounce 通知，A2 随 120 分钟心跳过期 page，持续更久后 A7 再 page；`_correlate_alert_results` 只覆盖 A1/A2/A5。本轮 A4 不再被未完成轮重置，反而让第二条通知更可能发生。2026-09-04 21:30–23:45 的断流正是这一形态。
- **闭合方向**：从完整轮携带规范化的 preflight/error-class/route 事故键（`egress-preflight status=unavailable reason=…` 与 FAIL 行的 error class），A2/A4/A7 键一致时合成一条带各规则症状清单的通知。归属：与「A2 归因」条同一单元。

### A4 普通 fetch notice 的 fire 与 resolve 确认强度不对称（2026-09-05）

- **现象**：review-alerting P7-2：notice 的 fire 要 30 分钟 debounce，但任一轮 `firing=False` 立即 resolve（`test_admin_alerts.py` 也固化了一次健康评估即 resolve）；持续故障中偶尔一个健康完整轮会发 ✅ 并把 30 分钟计时归零，交替抖动可让事故在错误 resolve 后长期静默。账户层 page 已有两轮滞回，普通 notice 没有。基线独立、边界命中。
- **闭合方向**：resolve 也按多个 `completed_at` 不同的健康完整轮确认（复用 `account_resolve_rounds` 的机制），或 M/N 轮判据。归属：独立小单元。

### review-alerting 2026-09-05 第 2 轮的基线独立发现（汇总，各自独立成立）

均为本轮 A4 改动之前就存在、与 A4 diff 非边界的问题，按原则编号列出，供后续按规则逐条立项：

- P1/P2：A2 的后台阶段 P95 单独命中即 page，代码注释自陈"无用户影响、总能自愈"（见 ISSUE-A14）；A7 单源到全站静默都无条件 page（见 ISSUE-A07）；A4 普通 fetch 故障靠当日累计 items floor 降为 notice，该背书不覆盖同一失败面（见 ISSUE-A09）。
- P3：A1 标题把窗口内多数失败扩大成「上游模型不可用」且无 impact；A3 healthz-only 分支把本地探针失败写成「网站用户侧异常」（见 ISSUE-A02/A10）；A5 的 urgency 把已判定的互斥分支塞回同一句（见 ISSUE-A16）。
- P4：A7 把需处置与「已追平无需处置」的 X 源等权放进主视图；A6 主视图同时承载结论、比较值、全部降级说明与计价方法；A5 首次处置正文塞进 breaker/错误码分流；A1 附带已排除的 schema 噪声率；所有 firing 消息把标题重复为「故障类别」一行。
- P5：A1/A2/A5 关联器在没有共因证据（provider/model/stage 身份）时会把独立事故误压成一条；一次 pricing catalog 刷新失败按 model 扇出多条 D3 stale 通知。
- P6：A1/A2/A3/A5/A6 的 resolved 没有「去哪看」（`_RESOLVED_EVIDENCE_POINTERS` 只有 A4/A7，边界命中）；A1 firing 无具体证据落点（见 ISSUE-A12）；A2「pipeline 最新日志」无路径；A3 5xx 支路、A5 主日志、A6 已定价成本突变的入口均为泛称。
- P7：A4 items-floor 在每日首轮完成前把调度空窗当事故（见 ISSUE-A08）；A1/A2 rate/A3 5xx 把滑动窗口饿空当成恢复；A2 P95 单个慢样本即 page；A5 功能被关闭时把「退役」报成「恢复」；（P7 报告第 7、8 条未在本汇总展开，见 `.label-serve` 外的评审原文——本仓不保存评审输出，若要立项按原则 7 重审 A5/A6/D3 的 resolve 证据）。
- P8：`alert-check` 的 stdout/stderr 无 rotation（见 ISSUE-A11）；D3 resolved 的 `episode_since` 固定为 `None`，无法与 firing 按 episode 配对；64 MiB 熔断触发后永久生效且不可观测；未成功投递的 fire 在 ledger 与 state 两侧都不留痕（ADR-009 有意取舍，缺"发生过"的最小可查事实）。
- P9：A1/A2/A3 样本门不足时统一塌缩为健康并可 resolve 既有 episode（见 ISSUE-A15/A02）；A5 无法区分「明确关闭」与「启用配置丢失」；A6/D3 看不到已付费但未落 `llm_usage` 的调用（见 cost-observability ISSUE-023 相关）。

### A4 单元 review-gate 保留的 LOW（2026-09-05，不阻塞）

- `stages["fetch"]` 的 status/duration 取最近非 skip 轮而 processed/errors 取最近完整轮，两者可能不是同一轮（只影响 `/admin` 阶段面板与 JSON）。
- `recent_complete_fetches` 的「最近」按文件名（启动时间）排序而非 `completed_at`；依赖 pipeline.sh 的 flock 互斥使其等价（包络内不可达，已在 `AlertSignals` 注释写明依赖）。
- 负向对照缺口：无「403/429/5xx 不进账户层、只出 notice」的直接用例；`Server error '5xx'` 不抽取也无用例。
- A2 的 action 只说「查看 pipeline 最新日志」，没有 `logs/pipeline-*.log` 与 runbook 指针（P6 边界命中，A4 新文案把 A2 心跳列为第一步）。
- A4 的 resolve 消息（`_format_resolved` 通用分支）不提这次恢复的是哪个账户/状态码（本轮已加证据指针，原因标签随 lifecycle 记忆一并在上面「共享 lifecycle」条处理）。
- 「另有 N 组同此」按组计数后，同一组的第二个状态码行（如 a 组既有 401 又有 402）不再单独显示也不计入 N；数据仍在 `values.failed_by_status`。
- A4 resolved 的证据指针对账户层 / 出网两条路径并列给出、不按触发分支收窄（中性入口是有意选择，分支记忆见「共享 lifecycle」条）。

## ISSUE-A21 · A7 full-pause close assumes at most one qualifying firing lifecycle

**状态**：open · **优先级**：low · **原则**：P7 · **来源**：2026-09-05 T1 rollover decision review（基线独立 / 非边界）

`run_alert_results_state_machine()` 的 non-firing full-pause 路径会收集所有满足 opening source IDs 全部 paused 的 announced firing lifecycles，但把它们合并成一条 INTERNAL `source_paused` ledger，并取列表第一项的 severity、`since` 作为 episode identity。正常状态转换预期不会同时留下多个这样的 lifecycle，但异常或迁移状态若出现多个，单条事件无法准确代表多个 episode。

本次 T1 rollover 修复只在“总共恰有一个 firing lifecycle”时工作，明确不把多 lifecycle 状态吞并为一个事件。后续若要支持该异常形状，应先决定是逐 lifecycle 写独立 ledger 后分别结案，还是把状态判为需要人工修复；需补多 severity/episode 的判别测试，并保持 identity、notification nonce 与 ledger 写失败时的 fail-closed 语义。
## ISSUE-ALERT-20260904-a21d · server healthcheck 的 page 缺动作与有界事件历史

**状态**：open · **优先级**：high · **原则**：P1 / P3 / P4 / P5 / P6 / P7 / P8 / P9

`deploy/server/health-check.sh` 的 serve、healthz、disk、sync freshness 与 deploy stuck 消息主要给内部症状值，没有正文内影响、可执行第一步或具体日志/runbook；恢复只写 key。active port 已配置但 serve unit inactive 时，它会先发 `serve`、随后仍 curl 同一端口再发 `healthz`，同一 serving-down 根因可扇出两个 key。每条当前状态只靠空的 `*.firing` marker，resolve 在投递前删除 marker 且吞掉发送失败，恢复通知失败后无状态供下一轮重试；仓内也没有结构化、带时间和值且有 retention 的 fire/resolve 历史。repo 外日志是否补足历史能力未核实。

**闭合方向**：先让每类消息写清影响、第一步与证据入口，再把事件接入有界 ledger；当前 marker 只继续承担“是否 firing”的瞬时状态，不冒充历史。

## ISSUE-ALERT-20260904-a22d · performance remediation 的 page 不说明动作，证据目录 retention 未定义

**状态**：open · **优先级**：medium · **原则**：P1 / P2 / P3 / P4 / P8

worker 失败与候选待审都走默认 page，但正文主要是 `reason/violations/evidence` 或 `candidate/worktree/summary` 机器字段，没有说明当前影响、是否需立即处理和首个动作。失败会写 `logs/performance/remediation-evidence/*.json`，但仓内没有 retention/prune 契约，长期是否有界未核实。

**闭合方向**：按“执行失败”和“候选待审”分别重写人读消息并重新定 severity；为 evidence 目录建立可验证 retention，或明确它由哪一现有生命周期清理。

## ISSUE-ALERT-20260904-c30e · 共享 lifecycle 的新 episode 与 resolved 通道未统一校准

**状态**：open · **优先级**：medium · **原则**：P2 / P3 / P7

共享状态机把 `last_notified` 保留到 resolved 后，A1–A7 等规则若在 30 分钟内重新进入同一 severity，新的 episode 仍可能被上一 episode 的 reminder cooldown 压住；T4 已仅对 W1 的 `ok → firing` 新 episode 旁路旧 cooldown，以满足浏览器缺失立即 page，不借此改动既有规则。另一面是除 W1 外的 page episode 仍把 resolved 发到 ALERT 通道，即使恢复无需立即处置；W1 已显式改为 notice resolved。

**闭合方向**：把 cooldown 明确定义为 episode 内 reminder，而不是跨 episode 限流，并为所有规则补 `fire → resolve → 30 分钟内 recur` 对照；随后统一评估 page resolved 是否改走 notice，避免通道迁移与既有 consumer/dedup 契约脱节。

## ISSUE-ALERT-20260904-8f2c · 共享告警 ledger 可先写过上限后永久停录

**状态**：open · **优先级**：medium · **原则**：P8

`_record_event_rows()` 只在写入前检查现有文件是否已超过 64 MiB，对本批追加后的字节数没有边界。因此一批正常事件可以先把文件写过上限，下一批开始后每次都 fail-open：通知可已被 transport 接受、当前 lifecycle 也可已持久化为 firing，但新的 W1/A1–A7/D3/PERF 事件不再有历史行。现有 oversize 测试只证明不覆盖旧文件且状态机继续，没有证明 ledger 会自动恢复。

**闭合方向**：在 replace 前对 retention 后的整批输出实施真正的字节上限或可恢复轮转，并加回归证明「本批写入自身越界后，下一个事件仍可记录」；不把当前 fail-open 当成有界 retention。

## ISSUE-ALERT-20260904-4c8a · central alert collector 的前提异常没有独立失败面

**状态**：open · **优先级**：high · **原则**：P9

全量告警审查发现，central alert 轮在进入共享状态机与 sender 之前若因日志/DB/signal 收集异常退出，本轮不会产出任何规则结果，也没有独立的“alert evaluator 未完成”事件；launchd 进程退出码与项目内告警是否能被外部发现尚未核实。失败形态因此可能与“本轮所有规则都健康且无需发送”同形。

**闭合方向**：为 evaluator 自身建立不依赖其内部规则状态机的失败面，明确谁承载异常退出、如何去重与恢复；用 collector 抛异常的阴性对照证明能发出，正常空结果证明不误发。

## ISSUE-ALERT-20260904-73ad · PERF 样本饥饿可能让旧事故过期关闭

**状态**：open · **优先级**：high · **原则**：P7 / P9

PERF journey monitor 的 lifecycle 以现有样本判定；样本生产者长期不产出、文件损坏或观测窗滑过旧失败样本时，没有独立 freshness/producer-health gate。一个先前 firing 的事故可能在没有新的成功观测时转为 resolved，或探针完全不运行却没有新的 page。当前 `performance-probe` 未安装/禁用只来自版本化服务文档，live 状态未核实。

**闭合方向**：把“最新有效样本年龄/生产者运行状态”作为独立可评估前提；缺新鲜证据时保持事故或转 degraded，不以旧样本滑窗消失证明恢复。

## ISSUE-ALERT-20260904-b19f · A5 与 D3 的非 firing 状态没有完整恢复契约

**状态**：open · **优先级**：medium · **原则**：P7

A5 在微信解读被配置关闭时可从既有 firing 直接进入 resolved，正文没有说明这是能力关闭而非解读链路恢复；D3 定价通知与 `run-or-alert` 型告警则没有统一、用户可见的 resolved 生命周期，读者无法从同一通道知道事故何时真正结束。这两项撤回 T4 后仍成立，本轮不改既有 lifecycle。

**闭合方向**：A5 把“能力关闭”与“有新成功解读”分成不同终态；D3 与 wrapper 告警明确是否需要恢复通知，若需要则保留可重试状态，若不需要则在 firing 文案中声明关闭观察入口。

## ISSUE-ALERT-20260904-d2e6 · sync 与 cost-report 故障证据的有界保留不完整

**状态**：open · **优先级**：medium · **原则**：P2 / P3 / P6 / P8 / P9

`sync-db-cron.sh` 仅在 `.sync.lock` 不存在时裁剪日志；inner sync 崩溃遗留 stale lock 后，每轮失败仍可继续追加而 rotation 永久跳过。DB sync 的 exit 2 page 只给 fingerprint/agent 内部症状与登录动作，没有正文内用户影响；同一 page 通道还同时承载首次/单次同步失败（replica 仍可能新鲜）与已 stale/连续不可核实，影响与紧迫度不等价。`deploy/cron/ai-radar-cost-report` 追加写 `logs/cost-report-cron.log`，仓内未定义 rotation；作为每周 notification 的周报若一次发送失败，也被 `run-or-alert` 统一提升为 page。两个 wrapper 都只有被 cron 实际启动后才可告警，调度器自身缺席对本机制不可见；DB sync 的 scheduler gap 已由 ADR-013 显式 waive，cost-report 尚无独立 owner。外部 transport decision history不含每次故障值，不能替代本地值历史。真实文件增长量与远端 timer 共存状态本轮未核实。

**闭合方向**：把 stale-lock 故障路径纳入 rotation，给 sync exit 2 补用户影响与首个证据入口，并按 replica freshness/连续失败给 sync 与周报失败重新分档；为 cost-report 日志定义可验证的 retention，或迁入保留值的共享事件账本；为 cost-report 调度器缺席指定独立探测 owner。

## ISSUE-ALERT-20260904-e6a4 · D3 定价通知未拒绝 state/event 路径别名

**状态**：open · **优先级**：low · **原则**：P8

`run_pricing_notifications()` 接受独立的 notification state 与 event ledger 路径，却没有像 A1–A7/W1 状态机一样调用 `_validate_alert_paths`。两者指向同一文件时，通知仍可能被 transport 接受且 state 存在，但 event 写入因 JSON 形状冲突而 fail-open，留下无 D3 历史的已送达事件。该机制撤回 T4 后仍成立，本轮只登记，不顺带改 D3。

**闭合方向**：D3 在 sender/state 写入前校验其实际使用的 state/event/ledger-lock，并补 distinct path 正例与 state=event、state/event 对应 ledger-lock 冲突的负例；若后续同时把 D3 纳入 `_alert_state_lock`，再复用 A1–A7/W1 的四向校验。该问题与 [cost-observability.md ISSUE-018](cost-observability.md) 的 episode 配对缺口分别验收。

### A1 的「上游错误率」分母混了全部 stage，enrich 重算期会把它稀释（2026-09-08，未修）

- **现象**：`src/airadar/admin/alerts.py` 的 `_recent_upstream_stats` 把**所有 stage** 的 `item_evaluations` 行放进同一个分母。基线日产出约 3000–4000 行；enrich 版本戳移动后的重算期按每轮 40 条 × 实际轮频推算另加约 1900 行/天，且几乎全部 `error IS NULL`，分母涨 50–100%，`upstream_error_rate` 相应下降同一比例，而阈值是常数 `0.1817`。
- **后果**：一次本该 page 的上游故障，在重算期可能被稀释到阈值以下。
- **执行约束（无需改代码）**：`calibrate_thresholds` 不在 cron 上，但**重算期不要跑它**——`thresholds.py` 的阈值是从实测数据推的，被稀释的分母一旦被校准固化，这个偏差就永久写进阈值。
- **归类**：基线独立 / 边界命中——缺陷本来就在，是 2026-09-08 的 enrich 戳位移动把它的触发概率抬起来了。未修：改分母要动 A1 的既有语义（它现在量的是"全链路 LLM 调用错误率"，按 stage 拆分是另一个设计决定），不在那次改动的范围内。
