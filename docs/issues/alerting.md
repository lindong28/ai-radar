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

**状态**：open · **优先级**：high · **原则**：P5

2026-08-17 15:35 → 08-18 08:57 的出网事故窗口内，A2 与 A4 在同一轮检测里同时 firing 但分两次独立投递，全窗口约 25 次 `send A2 firing` + 约 14 次 `send A4 firing`，零合并。`_correlate_alert_results()` 以 `A5.firing` 为总闸，A5 全程 `degraded`，关联逻辑一次都没进入函数体；且 A2/A4 这一对本就不在它的 `suppressed_ids` 覆盖内。A7 一旦上膛会成为同一事故的第三路。

前置缺口：`AlertSignals` 里没有任何字段承载 fetch 失败的**错误类别**，所以现有信号面只支持「同一轮都 firing 就并」这种时间巧合式合并，而 P5 恰恰点名这种做法有并掉第二个独立事故的风险。要合规合并，先得把「本轮失败的主导 error class」提升为一个 signal 字段。

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

**闭合方向**：三条补齐 `impact` / `urgency`；顺带去掉 `故障类别：` 行对标题的逐字重复（7 条规则全部命中，零信息增量）。

## ISSUE-A11 · alert-check 日志无 rotation：告警侧的消费面

**状态**：open · **优先级**：high · **原则**：P8

runbook 把「人工监看 `logs/alert-check.log` 大小」写成缓解措施，但 `status.sh alert` 只打印路径、不报大小（`status.sh:72`），该缓解因此不可执行；`logs/alert-check.err.log` 连这层纸面缓解都没有，而它是 ledger fail-open 的唯一证据通道——`_record_event_rows()` 捕获异常后只 `LOGGER.error("notification ledger write failed …")`，本批 ledger 行就此丢弃（阴性读数：至今 `grep -c "notification ledger"` = 0，尚未发生过写失败）。对照之下 `data/alert-events.jsonl` 侧是合格的（14 天 + 64 MiB 双门，当前 101 KB / 247 行，runbook 给了 jq 配方）。

**实体缺口 defer 给 [cost-observability.md ISSUE-013](cost-observability.md)**——rotation 本身归那条跟踪。此处只登记告警侧的消费面：`status.sh alert` 应暴露这两个文件的大小，否则 runbook 里那句缓解永远是空的。

## ISSUE-A12 · 告警消息不指向 runbook，A1 三类落点一个不占

**状态**：open · **优先级**：medium · **原则**：P6

`grep -n "runbook\|monitoring-alerting\|docs/operations" src/airadar/admin/alerts.py` 在本轮之前零命中——项目有一份 300 行 runbook 和可查的 `data/alert-events.jsonl`，推送消息里都不出现。本轮已给 A4/A7 各补一条指针，其余五条未补。

其中 A1 是唯一一条 evidence / 日志 / runbook 三类落点一个都不占的（「检查 DeepSeek/模型供应商余额、模型权限与 provider endpoint」，无 URL、无路径、无命令）。A2 不给 `logs/pipeline-*.log` 与 `.pipeline.flock`（ADR-052 之后判活方式已改为内核 flock，凭 `ps` 判不出来）；A3 给了 endpoint 但没给 host:port，也不给 `logs/serve-access*.log`；A5 只有「pipeline 与 interpret 日志」这半句缺路径。

**另一处更危险**：`docs/operations/services.md` 有一份写得很好的出网代理诊断顺序，但那是 `/img` 的新加坡图片代理，**与 fetch 出网是两条链路**。运维按告警里的「出网链路」去 docs 里找会命中它——有一个看起来对、实际错的落点，比没有落点更糟。`AI_RADAR_PROXY_FILE` 在 docs/ 下零覆盖（不在服务清单、不在 `.env.example`）。

**闭合方向**：五条补 runbook 指针；把 `AI_RADAR_PROXY_FILE` 与 agent-proxy 这条链路写进 `docs/operations/services.md` 服务清单，并与图片代理那节明确区分。

## ISSUE-A13 · A6 的「至少 3 个基线日」门是死代码，14 个日桶含伪造日

**状态**：open · **优先级**：medium · **原则**：P9

已由 [cost-observability.md ISSUE-023](cost-observability.md) 跟踪，本轮复核确认记载与当前代码仍然一致、未修复。补充读数：当前基线窗（UTC 08-04…08-17）中 `llm_usage` 在 2026-08-08 零行（直接以 ¥0 进中位数），08-07 仅 243 行、08-15 仅 467 行，对比常态约 1000–1200 行/日。而实际告警文案写的是「14 UTC 日中 **14 个可比日**中位数」——「可比」是证据不支持的断言。

**闭合方向**：见 cost-observability.md ISSUE-023。此处只登记文案里的「可比日」措辞同属该缺口的消费面。

## ISSUE-A14 · A2 的 P95 支路以 page 投递，与它自己写下的影响判断矛盾

**状态**：open · **优先级**：medium · **原则**：P2

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
