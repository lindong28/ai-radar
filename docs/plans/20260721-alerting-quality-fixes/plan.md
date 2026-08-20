> **Archive status**: 已归档，执行完成（`state.md` 中 TASK-001..009 全部 done，ISSUE-001..003 resolved）。执行过程产物 `state.md` / `journal.md` / `baseline.patch` 按长任务协议不入档。
> **其中 F1（PERF busy→idle 降级 gate）与 F4（共因 rollup）已被后续 [ADR-011](../../adr/011-perf-idle-only-probing.md) 干净退休**——部署后审计发现该降噪目标在生产从未生效（idle 样本结构性饥饿），改为 idle-only 采样，见 [20260724-perf-idle-only-and-grounding](../20260724-perf-idle-only-and-grounding/plan.md)。仍然生效的部分：severity 生命周期 [ADR-008](../../adr/008-alert-severity-lifecycles.md)、通知留痕 [ADR-009](../../adr/009-alert-notification-ledger.md)、投递与抑制审计 [ADR-021](../../adr/021-audit-alert-delivery-and-suppression-decisions.md)、[ADR-022](../../adr/022-evaluate-a6-in-progress-cost-as-lower-bound.md)；运行口径见 [operations/monitoring-alerting.md](../../operations/monitoring-alerting.md)。以下为原 plan 正文，未修改。

# Plan：AI Radar 告警质量修复（F1–F6 核心 + F11–F12 留痕 + F13 文档）

> **Long-task mode** — 本 plan 按 `~/.claude/references/long-task-protocol.md` 执行。实施 session 先读同目录 `state.md`（任务进度真源）与 `journal.md`（决策/踩坑流水），每完成一个 phase 更新 state、交付前跑「交付前验证」。

## 输入

- **上游 handoff**：`handoffs/alerting-quality-fixes-handoff-20260720.md` —— 承载全部 finding 的 `文件:行` 定位 + 修复方向 + 背景（触发本次的那条飞书 PERF 误告警）。本 plan 是它的可落地实现版，L3 设计 + L2 verify 由本 plan 承载；finding 的原始定位不重述，需要时回查 handoff。
- **审查标准**：`~/.claude/references/alerting-review-principles.md`（P1–P8）。该档底部「反例改写（AI Radar，2026-07-20）」的好/坏对照即本次核心场景的**验收样例**，L2-1 直接据此断言。
- **投递契约**：`~/.claude/references/service-operations-protocol.md` §6 —— 本次不改投递层（已合规），但 F2 的 severity→通道映射依赖 `im-notify` 的既有能力（见下）。
- **本 plan 已过一轮独立 Codex `review-plan` 审查**，3 项取舍经用户拍板、8 项事实缺陷已修订（见文末「审查修订记录」）。

## L1：最终产物 + 使用者 + 使用方式

- **产物**：修正后的 AI Radar 告警子系统（`admin/alerts.py` + `performance/journey_monitor.py` + `admin/thresholds.py` + `admin/metrics.py` 的窗口口径扩展 + 新增 `data/alert-events.jsonl` 留痕）。
- **使用者**：依赖飞书告警值守生产 aiplanet.live 的运维（= 维护者本人）。
- **使用方式**：收到告警后据消息本身判断「哪个用户体验坏了 / 要不要现在放下手里的事 / 去哪看证据」，并决定是否立即处置。产物质量 = 让对的告警在对的时间以对的严重度到达，不狼来了、不静默漏报。
- **成功定义**：把 handoff 背景里那条 🔴 PERF 误告警（busy 齐发 6–7 条、埋结论、假精度、内部量）重放进新系统，产出符合「反例改写」后样例的**一条 🟡 rollup**（NOTIFICATION 通道、开篇即影响+免处置判定、无假精度、无内部量、指向 evidence）；同时**不削弱**对真实事故（idle 也退化 / public 公网路径退化 / items 摄取骤降 / 足样本下 5xx 飙升）的即时 page。

## 使用形态与调用

告警子系统以 **两个独立 cron/launchd 调度**运行：`admin alert-check`（每 5 分钟，A1–A4）与 `performance-probe`→`run_journey_monitor`（PERF）。**均与 serve:8000 用户请求路径解耦**。本 plan 不碰 web 服务路径，故对 aiplanet.live 在线服务零影响面（硬约束见下）。两个调度器会并发写同一 `alert-events.jsonl` —— 见 F11 的并发设计。

## im-notify 投递能力（load-bearing 事实，已 probe）

`im-notify` 提供两条独立 webhook：
- `im-notify --alert "…"` → **ALERT** webhook（`FEISHU_GENERAL_ALERT_WEBHOOK`）= page 级红线通道。
- `im-notify "…"`（不带 `--alert`）→ **NOTIFICATION** webhook（`FEISHU_GENERAL_NOTIFICATION_WEBHOOK`）= 低通道。

故 F2 两级 severity（已与用户对齐）：

| severity | emoji | 投递 |
|---|---|---|
| `page` | 🔴 | `im-notify --alert`（ALERT 通道，buzz 手机） |
| `notice` | 🟡 | `im-notify`（NOTIFICATION 通道，到手机但非红线） |

> **不设第三级 `status`/record-only**：审查确认本轮无「落盘但不推送」的消费者（个体被 rollup 的 busy cell 其数据折进 rollup 的 `values`，不单独走状态机——见 F4/F11）。保持枚举最小（page/notice），未来真需要 record-only 再加。

当前 `send_alert_message` 硬编码 `["im-notify", "--alert", text]`（`alerts.py:429`）—— F2 要让投递按 severity 选通道。**投递侧的 dedup-key/去重不在本次范围**（§6 已合规）；state-machine 的 debounce+COOLDOWN 保留并按 U7 扩展为 per-severity 生命周期。

## 取舍偏好 + 三层影响

偏向 alerting-review-principles 的总取舍：**更少、更高信噪比**，宁可降级到低通道也不 page 级轰炸；但**降级必须有覆盖同一失败面的独立干净信号背书**（P1），失背书时 fail-closed 保留 page（防静默漏报）。三层投影：
- **L1**：产物是「分级 + 合并」后的告警流，不是扁平单色流。
- **L2 verify 维度**：既验「误告警被降级/合并」（信噪比），也验「真事故仍即时 page」（不漏报）—— 两个方向都是验收，缺一不可。
- **L3 取舍**：降级 gate 一律 fail-closed（reference 信号缺失/不足/也在 firing → 保留 page），把「宁可多一条 🔴 也不静默掉一次真事故」编码进真值表。**public 不设 `public→origin` 跨-vantage gate**（origin 不覆盖 CF/tunnel 公网路径，见审查裁决 1）；public 的 busy cell 仍与 origin 一样受**同 vantage 的 busy→idle gate**。

## Rigor：`(A0, V1)` = standard（已与用户确认）

| 轴 | 档 | 理由 |
|---|---|---|
| R 反转成本 | **A0** | 全是 git-tracked、本地可 revert 的 Python/文档改动；F11 用独立 jsonl、不动 radar.db。U7 会把运行态 JSON 自动规范化为带 `lifecycles` 的新形状，但持续写出旧 reader 可读的 flat 顶层投影，代码回滚仍兼容；无生产切流、DB schema 或不可逆外部副作用。常规 commit 级授权边界即可。 |
| G 回归容忍 | **V1** | 告警跑在生产机 cron、维护者依赖它值守 aiplanet.live；误判会**静默漏报**真实事故（影响生产运维），故每个改行为的 unit 需配行为测试 + 单 reviewer。非零容忍数据完整性（漏一条 perf 告警 ≠ 数据丢失）→ 不到 V2。 |

**per-phase override**（默认取共同低基线 `(A0,V1)`，只对承载最高回归风险的 phase 内收紧验证深度，不改向量）：
- **Phase 1（F1 降级 gating）**：最高静默漏报风险面 —— 真值表须穷举 `idle-clean（足样本 not firing）/ idle-firing / idle-absent / idle-样本不足 × busy-firing`，每格断言 severity（page vs notice）。这是本 plan 的核心 V1 兑现点。
- 其余 phase：标准 V1（改行为 unit 配测）。

## 并发隔离声明

本 plan 的**写面**：`src/airadar/admin/alerts.py`、`src/airadar/performance/journey_monitor.py`、`src/airadar/performance/remediation.py`（仅兼容新 state 投影、拒绝 notice incident；F7 remediation 能力仍 defer）、`src/airadar/admin/thresholds.py`、`src/airadar/admin/metrics.py`（为 A2/A3 提供与告警阈值一致的窗口 numerator/denominator）、`deploy/lib/services.sh`、`.env.example`、`.gitignore`、`docs/operations/monitoring-alerting.md`、`docs/operations/services.md`、`docs/architecture.md`、`docs/adr/README.md`、新增 `docs/adr/008-alert-severity-lifecycles.md` 与 `docs/adr/009-alert-notification-ledger.md`、`docs/CLAUDE.md`、`README.md`、`CHANGELOG.md`（F13 同步）、`tests/test_admin_alerts.py` / `tests/test_performance_journey_monitor.py` / `tests/test_performance_remediation.py` / `tests/test_admin_metrics.py` / `tests/test_install_dependencies.py` / `tests/test_service_contract.py`（按受影响面）。运行时新增 `data/alert-events.jsonl` 与稳定 sidecar `data/alert-events.lock`（均 gitignore）。

**不写** radar.db（schema 或行）、**不写** `config/performance.toml`（A1–A4 阈值在 `thresholds.py`、journey 预算在 `journey_monitor.JOURNEY_SPECS`，均非 toml）。`remediation.py` 只做 U7 必需的 state-reader compatibility + page-only filter，不扩展 F7 的候选修复能力。故与并行的 `plans/20260720-db-slimming`（DB 维护 + VACUUM）**无文件/资源重叠**，可并发。若实施时发现须触碰上述写面外的文件，先回主线程确认。

---

## 实施 phase（严格依赖序：F2 keystone 先行）

### Phase 0 — F2：`AlertRuleResult` 加 `severity` + 消息槽 + 通道路由（keystone）

**Goal**：给共享 dataclass 加 severity，让 `_format_firing`/投递按 severity 选 emoji 与通道。F1/F3/F4/F5/F6/F11 全部依赖它。

**改动**：
1. `alerts.py:50-57` `AlertRuleResult` 加字段：
   - `severity: str = "page"`（`Literal["page","notice"]` 或模块常量；默认 `page` 保持 A1/A3 现状零回归）。
   - 两个消息槽：`impact: str = ""`（对用户的影响/结论）、`urgency: str = ""`（是否需立即处置）。默认空字符串以兼容现有构造点。
2. `_format_firing`（`:220-226`）：按 `severity` 选 emoji（page→🔴/notice→🟡）；模板在「故障类别/具体故障对象/处置方向」基础上，当 `impact`/`urgency` 非空时前置「影响：…」「需否立即处置：…」两行（对齐 P3「影响+行动+是否立即」）。
3. 投递路由：`send_alert_message(text, severity="page")`（`:428`）改为 severity-aware —— `page`→带 `--alert`；`notice`→不带 `--alert`。默认值保留 `remediation.py` 等现有单参数调用的 page 行为；`_apply_alert_results`（`:295-351`）在 fire/resolve 两处按统一 `sender(text, severity=...)` 契约传 severity，注入 fake sender 也遵循同一签名，不留下二选一接口。
4. **resolve 通道一致性 + delivery receipt**：每次实际 sender invocation（firing/resolved）形成一条统一 receipt：`{rule_id,type,effective_severity,channel,text,send_result}`；`sent` 返回值与 F11 ledger 都消费这组 transport-level receipts，而不是按输入 result 计数。resolve 使用被关闭 lifecycle 自己的 severity/channel。旧 flat state 缺 severity 时规范化为 `page`（保守：恢复宁可上 ALERT 也不掉低通道）；完整兼容模型见 Phase 4。
5. **部署依赖闭环**：`FEISHU_GENERAL_NOTIFICATION_WEBHOOK` 已是 notice 的 load-bearing 依赖，不能只改发送代码/文档。同步 `deploy/lib/services.sh` 的 alert 安装依赖检查、交互补配置与 launchd env 生成，以及 `.env.example`；ALERT 与 NOTIFICATION 任缺其一，alert 服务安装/preflight 均 fail-closed。对应更新 `tests/test_install_dependencies.py` / `tests/test_service_contract.py`，不发送真实消息。
6. **成功感知 firing（审查裁决 5 / U8）**：现状 `_apply_alert_results` 无论 `send_result` 如何都更新 `last_notified`（`alerts.py:328`）——失败的 page（receipt `send_result` 非 mapping 或 `skipped is not False`）也进 30min cooldown，之后还可能为从未真正投递过的 episode 发 resolved，导致真实 page 静默丢失（违背「不静默漏报」成功定义）。修复（**firing 成功感知、resolved best-effort**）：
   - 该 severity 子状态的 `last_notified` / announced **仅在 firing receipt 成功时**更新（成功判据同 F11：mapping 且 `skipped is False`）。firing 投递失败 → 不更新 `last_notified` → 子状态保持 `firing` 且 announced=false → 下次调度自然重试，不被 cooldown 压住。
   - resolved **仅对已 announced（成功 firing 过）的 episode 发送**；从未成功投递的 episode 关闭时静默置 ok、不发 resolved（本就无对应 firing 到达用户）。resolved 投递失败 → 关闭状态、**不**重试（best-effort，避免为恢复消息引入 `resolve_pending` 持久状态）。
   - 与 U7 复合：成功判据作用在每个 severity 子状态的 `last_notified`/announced 上；转换序列（旧 resolved→新 firing）中，新 severity firing 失败同样不置 announced、下轮重试。fixed-severity 规则同理（A1/A3 失败 page 下轮重试，不再静默进 cooldown）。

**内部 verify（L3）**：
- `AlertRuleResult` 仍 frozen、`asdict()` 可序列化（返回 payload 与 PERF `replace()` 依赖）；`mypy src` 过（severity 类型）。
- 单测：page→`im-notify --alert` argv；notice→`im-notify` 无 `--alert`；旧单参数 `send_alert_message(text)` 仍等价 page。用遵循统一签名的 fake sender 断言。
- receipt/resolve 通道：notice fire→notice resolve 均为 `effective_severity=notice`、无 `--alert`；page fire→page resolve 均为 page、带 `--alert`；旧 flat state 缺 severity 的 resolve 缺省 page；receipt 数量与实际 sender 调用完全相等。
- 安装契约：分别缺 ALERT / NOTIFICATION key 都拒绝安装或生成残缺 alert service；两枚都在时生成的 service env 同时含两者。测试只检查配置，不实发。
- **成功感知 firing（U8）**：firing sender 返回 `{"skipped": true}` → `last_notified` 不更新、子状态仍 `firing`、下轮重试再发；下轮成功后才写 `last_notified` 并允许 resolved。失败 page 不因单次投递失败被压 30min。从未 announced 的 episode 关闭时**不**发 resolved；已 announced 的 episode resolved 失败则关闭状态、不重试。A1/A3 fixed-severity 同样受此保护（回归：成功路径时序与改前一致）。
- 现有 `tests/test_admin_alerts.py` 全绿（默认 severity=page 保 A1/A3 行为不变）。

### Phase 1 — F1：PERF busy cell 降级 gating（最高回归风险，V1 核心兑现）

**Goal**：`busy` cell 的 page 资格 gate 在同 `(journey,vantage)` 的 `idle`。idle 覆盖同一失败面（同探针去掉同机争用）且同等敏感、当前干净 → 降 `notice`；无背书 → 保留 `page`（fail-closed）。**只去掉 `public→origin` gate**（见审查裁决 1：origin 不覆盖 CF/tunnel 公网路径）；public busy 仍走同-vantage idle gate。

**改动**：`journey_monitor.py:269-336` `evaluate_performance_rules`。当前每 cell 独立 `firing`、无 severity。改为：
1. 先算出所有 cell 的 firing + p75/p95（现逻辑不变）。
2. 为每个 firing cell 计算 severity，并保留可供消息层区分的 `gate_reason`：
   - 基线 `severity="page"`。
   - **busy→idle gate**（仅当 `load_class=="busy"`，任一 vantage 皆适用）：查同 `(journey,vantage,"idle")` cell。当且仅当该 idle cell **存在、样本 ≥ WARM_SAMPLES（可评估）、且 not firing** → 降 `notice`。idle cell **firing / 缺失 / 样本 < WARM_SAMPLES** → 保留 `page`（fail-closed），但分别记录 `idle_firing` / `idle_absent` / `idle_insufficient`，不得把后两者误写成已确认用户退化。
   - 非 busy 的 firing cell（`idle` 本身 firing、或未来其它 load_class）→ 保留 `page`。
3. severity 写入 `AlertRuleResult.severity`。firing 语义不变（仍代表越预算）；变的是它以何 severity 投递。

**内部 verify（L3）—— 真值表穷举（per-phase V1 收紧点）**：
busy cell（origin 与 public 各测一遍，验证 public 也仅受 idle gate、不受 origin 影响）×：
- idle not-firing 足样本 → notice
- idle firing → page
- idle 缺失 → page
- idle 样本 < WARM_SAMPLES → page
- `idle` load_class 自身 firing 的 cell → 恒 page（不被任何 gate 降级）
现有 `tests/test_performance_journey_monitor.py` 全绿。

### Phase 2 — F3：PERF 消息重写（结论化 + 真精度 + 内部量下沉）

**Goal**：detail 开篇给结论化影响 + 免处置判定；实测数值取整/按秒；`samples`/`advanced_window_streak` 移出正文（已在 `values`→evidence）；action 指向 `logs/performance/evidence/`。

**改动**：`journey_monitor.py:307-319`。
1. 填 `AlertRuleResult.impact` / `urgency`（F2 新槽），依据 `load_class` 与 Phase 1 的 `gate_reason` 下结论。busy+降级（notice）：`impact="同机合成探针，与 pipeline 并发、疑似主机 CPU 争用；idle 视角正常，用户大概率无感"`、`urgency="否——除非 idle 视角也超标"`。保留 page 时必须区分证据强度：`idle_firing` 可写同视角真实退化；public idle 自身退化可写公网路径退化；`idle_absent` / `idle_insufficient` 只能写「影响未知，缺少足量同视角 idle 背书，无法排除用户影响，故保守 page」，并指向补采/核查 idle evidence，不能伪称已确认退化。`urgency="是"`。
2. `detail` 去假精度：p75/p95 取整到整毫秒或按秒显示（`2.4s/1.0s`），带「实测 vs 预算」对照。
3. 移除正文里的 `samples=…, advanced_window_streak=…/…`（仍保留在 `values` 供 evidence）。
4. `action` 明确指向稳定可用的 `logs/performance/evidence/` 目录；首次 firing 的 detail 继续带当次具体 evidence path。用户验收只承诺可直达 evidence 落点，不额外引入为 cooldown 重复消息持久化单文件路径的状态协议。

**内部 verify**：单测断言 detail 无 `\d+\.\d{3,}ms` 假精度、无 `advanced_window_streak`/`samples=` 子串；impact/urgency 非空且随 severity/gate reason 切换措辞；至少分别断言 `idle_firing` 为已确认退化、`idle_absent`/`idle_insufficient` 为影响未知而保守 page。

### Phase 3 — F4：PERF 共因 rollup（fire + resolve 对称）

**Goal**：同一轮内共享 `load_class=busy` 且被降级为 `notice` 的多条 PERF rule 合并成**一条**带明细清单的合成通知（`notice`，🟡）。保留 `page` 的 cell（idle 也 firing 的真实退化、public 公网退化）**保持独立、不并入**（P5 边界）。resolve 侧对称合并。

**改动**：`journey_monitor.py:449-505` `run_performance_alerts`，在 `evaluate_performance_rules` 完成 gating 后、**生成 evidence 前**先做 rollup，再把合成后的 results 交给既有 first-transition evidence 步骤与 `run_alert_results_state_machine`：
1. 从 results 中挑出 `severity=="notice"` 且 `load_class=="busy"` 的降级 cell（= 主机争用那批），合并成一条合成 `AlertRuleResult`（`rule_id="PERF:rollup:busy"`, severity=`notice`），detail = 影响结论 + 明细清单（各 journey 的 p95/预算，标最严重项），action 指向 evidence 目录，**`values` 保存完整子 cell 清单**（每条 journey/vantage/p75/p95/预算——供留痕复盘，见 F11 裁决 3）。个体 notice cell **不进状态机、不进入 first-transition evidence 判断**（不各自投递、不各自留痕/产 evidence；其完整数据已折进 rollup）。first-transition identity 改由 `PERF:rollup:busy` lifecycle 承担：仅 rollup 从非 firing→firing 时写一份包含本批子 cell/samples 的合成 evidence，持续 firing 不重复。
2. `page` 级 PERF cell 独立进状态机、独立投递。
3. **resolve 对称**：rollup 作为一个 state key（`PERF:rollup:busy`）；当该批全部回落，发一条合成 resolved。与 `enabled_vantages` 的既有 auto-resolve 逻辑（`:465-486`）不冲突——auto-resolve 处理 vantage 停用的孤儿 firing，rollup 处理共因合并，key 空间不重叠。
   - 实现时不能依赖“结果消失”自动 resolve：若 previous state 的 rollup 为 firing、而本轮无任何 notice busy 子 cell，必须显式生成 `PERF:rollup:busy` 的 non-firing result 送入状态机；仍有子 cell 时继续生成 firing rollup。
4. **部署时关闭旧个体 key**：rollup 前读取 previous state；凡旧版已 announced 且仍为 `firing`、本轮被 rollup 抑制而不再进入状态机的 `PERF:*:*:busy` 个体 key，补一个显式 non-firing result，沿旧 state severity（缺字段默认 page）发送一次 resolved 并把个体 state 置 `ok`。不得让旧 key 因从输入消失而永久悬挂。

**内部 verify**：
- 重放「busy 齐发 N 条 + idle 干净」→ 恰一条 `PERF:rollup:busy` notice 进 sent，个体 busy cell 不在 sent，rollup `values` 含 N 条子 cell 明细。
- 「busy 齐发 + 某 idle 也 firing」→ rollup（notice）+ 该 idle cell（page）各一条，idle 未被并入。
- resolve：rollup firing 后全回落 → 恰一条合成 resolved。
- 迁移：预置旧版个体 busy firing state，本轮该 cell 进入 rollup → 旧个体恰一次 resolved、state 变 `ok`，同时新 rollup 正常 firing；下一轮不重复 resolve。
- evidence identity：同一 rollup 连续 firing 两轮，首轮仅新增一份 rollup evidence，第二轮 evidence 文件数不增；全清后再次 firing 才新增下一 episode 的 evidence。

### Phase 4 — F5：A4 OR 逻辑拆分（severity-aware）

**Goal**：`fetch_failed_ratio` 单独不 page（X/nitter 结构性单点 down 是已知退化生态）；仅 `items_today` 跌破 floor（干净的摄取量信号不再背书良性）才 page。

**改动**：`alerts.py:146-217` A4。当前 `a4_firing = fetch_failed_ratio > x OR items_today < floor`。改为分级：
- `items_today < daily_inserted_floor_elapsed` → firing, `severity="page"`（真实摄取骤降，ALERT 通道）。
- 仅 `fetch_failed_ratio > threshold`（items 正常）→ firing, `severity="notice"`（结构性 fetch 失败上下文，NOTIFICATION 通道）。
- 两者皆破 → page。
- detail 按命中项措辞；两个分支分别填 `impact` / `urgency`：fetch-only 明确「当前摄取量正常、无需立即处置」，items floor 命中明确「文章更新可能停滞、需立即核查」。action 保留现有 X(nitter)/Mp2RSS 分组指引，**不新增 F8 的具体 evidence/log 落点**。`thresholds.py` 将 A4 的单值改为 `debounce_minutes_by_severity={"page":0,"notice":30}`；`_debounce_window(..., severity)` 优先读该映射、无映射时回退既有 `debounce_minutes`/0，因而 fetch 短暂毛刺仍吸收、items 摄取骤降首轮即时 page，其它 rule 沿用现值。

**severity 转换生命周期（审查裁决 4 / U7：按 severity 独立生命周期，在状态机层做）**：A4 同一 `rule_id` 下 severity 可在轮间切换（fetch-only→notice、items floor→page）。当前 `_apply_alert_results` 每 `rule_id` 单一 `{state,since,last_notified}` + 单 COOLDOWN，会把 `notice→page` 升级压在 notice 的 cooldown 下，并可能让 resolved 走错通道。修复在**状态机层**（不是 A4 特例），使其覆盖任何变 severity 的 rule：

1. **兼容的持久化形状**：`state[rule_id]` 增加 `lifecycles` 映射，内部生命周期键为 `(rule_id,severity)`：
   ```python
   state[rule_id] = {
       "state": ..., "since": ..., "last_notified": ..., "detail": ..., "severity": ...,
       "lifecycles": {
           "page": {"state": ..., "since": ..., "last_notified": ..., "detail": ...},
           "notice": {"state": ..., "since": ..., "last_notified": ..., "detail": ...},
       },
   }
   ```
   `lifecycles` 是状态机真源；顶层 flat 字段是给 `journey_monitor.py` 的 disabled-vantage auto-resolve/首次 evidence 与 `remediation.py` 的既有 reader 保留的**兼容投影**：有 active lifecycle 时投影当前 result severity，全部 ok 时投影 ok。`healthz_probe` 等非 ruleset telemetry 形状不改。
2. **旧 state 规范化**：load 时若 entry 无 `lifecycles`，把旧 flat `{state,since,last_notified,detail,severity?}` 原样复制到 `lifecycles[severity or "page"]`，保留计时器与 firing/ok；缺 severity 保守视为 page。之后统一写新形状，重复 load/save 幂等，不因迁移重置 cooldown 或重复 firing/resolved。
3. **独立计时器**：每个 severity 子状态独立持有 `since`/`last_notified`、debounce 与 COOLDOWN；子状态回到 ok 后也保留自己的最近投递时间。其它 severity 的计时器绝不参与当前 severity 判定。fixed-severity 规则只有一个实际子状态，其 fire/debounce/cooldown/resolve 序列必须与改前完全一致。
4. **同 rule 的转换顺序**：处理 `(R,S,firing)` 时，先关闭 R 下其它 firing 子状态。只有旧 episode 已 announced（沿用现行判据 `last_notified >= since`）才在其自身通道发 resolved；尚在 debounce、从未投递的 pending episode 静默置 ok，不伪造 resolved。随后进入 S：仅当 outgoing episode 已 announced 时，才视为同一 incident 已确认并允许目标 S 绕过**首次 debounce**；outgoing 仍 pending 时，把其原 `since` 继承给目标 S，并按目标 severity 的 debounce 正常判定（因此 pending notice→page 仍因 page debounce=0 即时升级，而旧 flat pending page→新 notice 会继续累计、不会因部署提前通知）。目标 S 自己曾投递过时始终遵守它自己的 COOLDOWN。higher page 永不被 lesser notice 的计时器节流。正常同 severity 重复只走本子状态 debounce/cooldown。`(R)` not-firing 时关闭全部 firing 子状态，announced 各自 resolve、pending 静默清除。
5. **投递与身份**：同轮转换的 sender/receipt 顺序固定为「旧 severity resolved → 新 severity firing」，对外 `rule_id`、消息、ledger 与 ruleset 身份仍不拆；receipt/ledger 用 `(rule_id,effective_severity,type)` 区分同轮两次投递。
6. **兄弟 consumer 收口**：顶层兼容投影保证 `journey_monitor.py` 的停用 vantage 清理和 first-transition evidence 继续读到一致状态。`remediation.py` 只把顶层 `severity=="page"` 的 `PERF:*` firing 当 confirmed incident；旧 entry 缺 severity 按 page 兼容。它必须忽略新的 notice rollup，避免主机争用低通道触发 remediation；本项只修 state-reader compatibility，不扩展 defer 的 F7 修复能力。

**内部 verify**：
- 三分支 severity（仅 fetch 高→notice；仅 items 低→page；皆破→page）断言 severity、投递通道、分支对应 impact/urgency，且 action 保持既有 X(nitter)/Mp2RSS 指引、不扩入 F8。
- **首次 debounce**：direct fetch-only 在 15min 仍 pending/无 sender，持续 31min 才 notice；direct items-floor 第 1 轮即 page。
- **转换序列**（per-phase 收紧点，V1）：pending notice→page 静默关闭 notice并在同轮立即 page（无虚假 notice resolved）；announced notice→page 恰为 notice resolved 后 page firing；page→从未 announced 的 notice 恰为 page resolved 后 notice firing（跨 severity 绕首次 debounce）；page/notice→全清分别只 resolve 已 announced 的通道。每组断言 receipt 的有序序列及 `(rule_id,severity,type)` multiset。
- **往返与独立计时器**：notice→page→notice、page→notice→page 分别证明目标 severity 自己的 cooldown 会保留、另一 severity 的 cooldown 不会串扰；到自身 cooldown 后可再次 firing。
- **迁移与兄弟 consumer**：覆盖旧 announced A4 page、旧 pending A4、旧 A1/A3 cooldown state，load→save→reload 幂等且不重发；特别断言旧 flat pending A4 page→新 fetch-only notice 继承原 `since`，累计未满 30min 时无 sender、满窗后才 notice。新形状下 disabled-vantage 恰一次 resolve、rollup first-transition evidence 连续 firing 不重复；旧 busy key→rollup 仍收敛；remediation 接受 page/旧缺 severity PERF firing、拒绝 notice rollup。
- 回归：A1/A3 等 fixed-severity 规则的 fire/debounce/cooldown/resolve 时序与改前一致（现有 `tests/test_admin_alerts.py` 全绿）。

### Phase 5 — F6：A3 5xx / A2 错误率加最小样本门（最小数学门，审查裁决 2）

**Goal**：给 A3 5xx 与 A2 错误率加用户锁定的**最小数学门**：在 numerator/denominator 均来自各自配置窗口的前提下，小于 reciprocal boundary 的低分母窗即使单次错误会越率阈值也不 page；达到门且错误率越阈才 page。该选择把 blind spot 限定在最小低分母区间，A3 healthz 与 A2 `no_success_minutes` 独立 page 支路不动。

**改动**：
- `thresholds.py`：
  - a3 加 `min_pv`，**取 `ceil(1 / server_error_rate) = ceil(1/0.05) = 20`**。含义是：从 20 PV 起单条 500 不再严格越过 5%；用户已选择把 `<20` 的单错误越阈窗视为低分母毛刺、不参与 A3 5xx firing。
  - a2 每 stage 加 `min_samples`，**取 `ceil(1 / stage_error_threshold)`**（prefilter/scoring: `ceil(1/0.3)=4`；enrich: `ceil(1/0.95)=2`）。同理，低于各门时单错误虽会越率阈值，但按已选低分母策略不参与 A2 firing。配置测试必须直接锁定 `{prefilter: 4, scoring: 4, enrich: 2}`，不得从被测配置反推期望值。
  - 门值即上述闭式推导，**不留给 implementer 依聚合 baseline 自选**（baseline 无法唯一定门、自选后围绕自选值写测试会循环自证）。
- **窗口口径纠正（已 probe，非 TODO）**：当前 dashboard metrics 的 stage `processed/error_rate` 是 day-to-date，`users.pv/status_counts` 则聚合所有可用 access-log 行；两者都不是 `thresholds.py` 给 A2/A3 声明的 15 分钟窗口，不能直接作为门的 numerator/denominator。`collect_metrics` 增加仅供调用方选择的 `stage_since` / `access_since` 窗口参数（默认 `None`，dashboard 现有口径不变）；`collect_alert_signals` 分别传入 `now - a2.window_minutes` / `now - a3.window_minutes`，让 A2 的 processed/errors/rate 与 A3 的 PV/5xx/rate 各自在同一窗口内计算。`stage_since` 只约束 error-rate 的 processed/errors，不得顺带把既有 stage P95 的独立 2h 口径缩成 15min；access-log 只纳入 timestamp 可解析且落在窗口内的行。无法证明属于窗口的旧无 timestamp 行不混入 denominator，PV 不足时由 min gate fail-closed 为不 page，healthz 支路仍独立生效。
- `AlertSignals` 增加 `stage_sample_count: dict[str,int]` 与 `server_pv: int`；`alerts.py:105-136` 仅在样本达到对应门时让该窗口 rate 参与 firing。healthz / `no_success_minutes` 分支不变。

**内部 verify**：直接断言配置为 A3 `20`、A2 `{prefilter:4, scoring:4, enrich:2}`；在窗口边界两侧布置 timestamped access rows / evaluated rows，先断言窗口外数据不进入 numerator 或 denominator，再逐 stage 用固定 `N-1/N` 样本验证 firing 边界（不用被测配置生成 N）。另测「PV=19 + 1 条 500 → A3 不 page」「PV=20 + 5xx 超率 → page」。A3 healthz 连续失败≥2、A2 no-success 超时仍 page（回归）；dashboard 未传窗口参数时口径不变，传 `stage_since` 时既有 2h P95 口径也不变。

### Phase 6 — F11 + F12：结构化可查告警历史（notification ledger）+ retention

**Goal**：A1–A4 与 PERF **共享**的 append-only 通知历史，对齐 PERF evidence 的可复盘性，且有界、并发安全。

**改动**：
1. **hook 点 = `_apply_alert_results`（`alerts.py:295-351`）**——这是 A1–A4（经 `run_alert_state_machine:410`）与 PERF（经 `run_alert_results_state_machine:368`）的**真实共享汇流点**。新增 `DEFAULT_EVENT_PATH=data/alert-events.jsonl`；`event_path` 从 `run_alert_state_machine` / `run_alert_results_state_machine` / `run_performance_alerts` 逐层传到该 hook，生产省略即用默认，测试必须显式传 `tmp_path`，不得 monkeypatch 常量或写真实 `data/`。
2. **notification ledger（裁决 3）**：只记 transport 返回成功的 firing/resolved。ledger 直接消费 Phase 0 的逐 sender receipt；成功判据为 receipt 的 `send_result` 必须是 mapping 且 `skipped is False`，其它 `None`/畸形结果与 `skipped=True` 一律不入 ledger。每次成功 sender invocation 恰一行 `{ts, rule_id, severity: effective_severity, type: firing|resolved, detail, values, channel}`；同一 input result 的 severity 转换可产生两行，identity 以 `(rule_id,severity,type)` 区分。rollup 行的 `values` 已含完整子 cell 清单（F4）。失败 attempt 继续沿 receipt/日志可见，但不得伪装成已送达历史；本 plan 不改 transport dedup。
3. **并发安全 + 有界成本（审查修订）**：两个调度器并发写同一文件。使用**稳定且不被 replace 的 sidecar** `<event_path 去后缀>.lock`（默认 `data/alert-events.lock`）作为 `flock` 目标；不得锁 ledger 本身后再 replace（锁会留在旧 inode）。用 `LOCK_EX|LOCK_NB` + 短退避重试，`LEDGER_LOCK_TIMEOUT_SECONDS=1.0` 到期即按 best-effort 故障路径记录并跳过本批 ledger，不阻塞告警 state 持久化。拿锁前与锁内重查 ledger 大小，超过 `MAX_LEDGER_BYTES=64 MiB` 时同样不读/不重写、不覆盖原文件，记录明确错误并跳过。本上限远高于当前 14 天、现有 rule 数与 cooldown 下的预期账本，仅作为损坏/失控文件的操作成本熔断。在锁内正常路径执行：读 ledger → append本批成功 receipt → age cutoff 过滤 → 写同目录 tmp → `os.replace` ledger → release。
4. **retention（F12）**：写时按 age 门裁剪（`RETENTION_DAYS=14`，与 PERF 一致）。时间解析**复用 alerts.py 已 import 的 `_parse_dt`（from .metrics）**——**不** back-import `journey_monitor._parse_timestamp`（journey_monitor 已 import alerts，反向 import 成循环依赖，审查已纠正）。
5. **路径 + gitignore**：`.gitignore` 新增 `data/alert-events.jsonl`、`data/alert-events.lock` 与现有未跟踪运行态 `data/alert-state.json`。
6. **故障优先级**：ledger 是非权威、best-effort 的审计副作用，不能成为告警循环的新同步故障依赖。ledger 读取/JSON 解析/锁超时/超大小/写 tmp/replace 任一步异常时，捕获并记录包含 `event_path` 与异常类型的 error log，不覆盖损坏或超限 ledger、不伪造成功行；sender receipt 与 state-machine state 仍照常返回/持久化，公开入口正常返回。告警投递与状态连续性优先于单次历史完整性。

**内部 verify**：
- 一次 firing→resolved 周期后 jsonl 恰两行（均为成功投递）、字段完整、severity 正确、可 `jq`/`python -c` 查。
- A1–A4 路径与 PERF 路径都产生历史行（验证 hook 在真实共享点：分别驱动 `run_alert_state_machine` 与 `run_performance_alerts`，各断言落行）。
- rollup 场景：ledger 只落 rollup 一行，个体 busy cell 不落行，rollup 行 `values` 含子 cell 清单。
- retention：注入超 14 天旧事件 + 新事件 → 写入后旧的被裁、新的留存。
- 投递结果：成功 fake sender → ledger +1；返回 `{"skipped": true}` 的失败 sender → ledger +0，且历史不声称已送达。
- **同批/转换完整性**：从任一公开 state-machine 入口一次产生 `N>=2` 次 sender invocation，fake sender 混合返回成功、`skipped=True` 与畸形结果；以成功 receipt 的 `(rule_id,effective_severity,type)` multiset 为 expected，断言 ledger 本批 actual multiset 完全相等。另对 announced notice→page 与 page→notice 各断言 sender/receipt 两次、ledger 两行且 channel/severity/type 对应；一成一败时只落成功的那次，防止 result-level 只写一条或把失败写入。
- **并发完整性（expected-vs-actual）**：用两个独立子进程、同一个临时 `event_path` 与其稳定 sidecar lock，各追加 K 个成功事件；断言最终行数 == 2K、事件 identity 集合与两个 writer 的 expected 集合完全相等、每行 JSON 可解析。测试必须经过生产 writer，不用线程或另写测试专用锁逻辑。
- **ledger 故障隔离与时限**：从公开 state-machine 入口分别预置损坏 JSON、`>64 MiB` 超限文件、由独立进程预持 sidecar lock 超过 1s，并用生产 writer 的 replace 异常注入覆盖 I/O 分支；断言公开入口在锁 deadline 的小容差内返回、成功 receipt 仍在返回值中、state 已持久化、原 ledger 未被覆盖且 error log 含路径/异常。下一轮不因 state 未落盘而重复发送。

### Phase 7 — F13：文档同步（runbook + services + architecture/ADR + 根 README/CHANGELOG）

**Goal**：把「所有告警一个 🔴」的扁平模型同步为 severity 分级 + rollup + 留痕。

**改动**：
- `docs/operations/monitoring-alerting.md`（约 :38-45 扁平模型、:51-67 per-rule PERF）：更新严重度分级表（page/notice→通道）、PERF rollup 与 busy→idle 降级 gating 语义（含「只移除 public→origin gate」）、A4 fetch-only notice/items-floor page 及 per-severity debounce/cooldown/转换时序、A2/A3 各自 15min numerator/denominator 与最小样本门、告警历史查询入口（`data/alert-events.jsonl` + 字段 + retention/lock timeout/size bound），并明确 remediation 只消费 page incident。
- `README.md`（**审查新增**）：`:217` 表述「A1–A4 状态机…通过 `im-notify --alert`」需改为 severity 分级（page→ALERT / notice→NOTIFICATION）；`:237`/`:257` 的 webhook 配置补 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK` 语义（notice 级去向）。
- `docs/operations/monitoring-alerting.md` 的 webhook 安装、preflight 与失败诊断段同步双 webhook，明确怎样分别验证 page/notice 配置而不误发生产告警。
- `docs/operations/services.md` 的 alert 服务说明、依赖表与验证入口同步双通道、A2/A3 窗口/门、A4 分支与独立 lifecycle；不能继续声称 alert 只依赖/只走 `FEISHU_GENERAL_ALERT_WEBHOOK`。
- `docs/architecture.md` 更新 alert state→投递→ledger→remediation 数据流：`lifecycles` 为真源、flat 顶层为兼容投影、remediation 只承接 page。
- 新增 `docs/adr/008-alert-severity-lifecycles.md`，记录 U7 的 context、备选方案、按 severity 独立生命周期的决策、旧 state 规范化、兼容投影、转换/announced/计时器不变量与结果；同步 `docs/adr/README.md` 和 `docs/CLAUDE.md` 的导航索引。
- 新增 `docs/adr/009-alert-notification-ledger.md`，记录 U6 + D1/D4/D5：为何选 notification-only JSONL 而非 radar.db/attempt ledger、为何只记 transport success、为何 retention/sidecar lock/成本熔断且 ledger 故障 fail-open，以及这些选择的后果；同样加入两个索引。
- `CHANGELOG.md` 追加用户视角逻辑变更：告警流从单一 page 变为 page/notice 分级、PERF busy rollup 与成功投递 ledger。
- 遵循 `docs/CLAUDE.md`。

**内部 verify**：`git grep` 确认 runbook/README/services 不再声称「所有告警一个 🔴 / 全走 --alert / 单 webhook / 无 rollup」，且能命中 A2/A3 窗口与最小门、A4 两分支/per-severity lifecycle、remediation page-only、ledger 边界；ADR-008/009 在两个索引可达；architecture/ADR/runbook 的 state 形状、转换不变量与 ledger 成功/fail-open 语义一致；CHANGELOG 有本次用户可感知变化；分级表、安装依赖与代码 severity 常量一致。

---

## L2：用户视角 verify（implementer-executable，覆盖信噪比 + 不漏报两向）

均可 agent 独立跑（pytest，在库入口 `run_alert_state_machine` / `run_performance_alerts` 注入统一签名的 `send=` fake sender；CLI 无 dry-run、真跑会调真实 `im-notify`，故 e2e 不走发送 CLI）。每次显式传临时 `AI_RADAR_DB`、state path 与 `event_path`，避开本地生产库和真实 ledger。

| # | 维度 | 可执行验收 | 人机 |
|---|---|---|---|
| **L2-1** | 核心场景重放（F1+F3+F4，= 反例改写验收样例） | 构造 handoff 背景那批 sample（busy 齐发越预算 + idle 干净），过 `evaluate_performance_rules`→rollup→format，断言：**恰一条 🟡 notice** 进 sent 且走 **NOTIFICATION**（fake sender 收到无 `--alert` 语义）；消息开篇含「影响：…」「需否立即处置：否」；detail 无 `\d+\.\d{3,}ms` 假精度、无 `advanced_window_streak`/`samples=`；action 含 `logs/performance/evidence/` | agent |
| **L2-2** | 不漏报守卫（F1 fail-closed + public 不受 origin gate） | idle **也 firing** → busy 保留 🔴 page，文案明确已确认同视角退化；idle **缺失/样本不足** → busy 保留 page，但文案必须写影响未知/因证据不足保守 page；**public busy cell 在 origin 干净时仍按 idle gate 判（不被 origin 降级）**。真值表每格断言 severity + gate reason + 证据强度措辞 | agent |
| **L2-3** | A4 分级（F5） | fetch 失败率高但 items 正常 → 🟡 notice（NOTIFICATION，不 page），明确无需立即处置；items 跌破 floor → 🔴 page（ALERT），明确文章更新风险与立即核查；两者保留既有 X(nitter)/Mp2RSS action，不新增 scope 外 F8 evidence 落点 | agent |
| **L2-3b** | A4 severity 生命周期（F5/U7，不漏报守卫） | direct fetch-only 30min 内不发、持续后 notice；direct items-floor 首轮即时 page。pending notice→page 不发虚假 notice resolved、同轮 page；announced notice→page 与 page→首次 notice 均按「旧 resolved→新 firing」双通道投递，receipt/ledger identity 完整；往返时各 severity 只受自己的 cooldown。旧 flat state 迁移幂等；A1/A3 fixed-severity 时序、PERF disabled-vantage/evidence/rollup 收敛不变，remediation 接受 page/旧 state、拒绝 notice rollup | agent |
| **L2-4** | 最小数学门（F6） | 先直接断言 A3=20、A2={prefilter:4, scoring:4, enrich:2}；A2/A3 numerator 与 denominator 均只取各自 15min 配置窗，窗外样本不计；PV=19 + 单条 500 → A3 不 page；PV=20 + 5xx 超率 → page；各 A2 stage 用固定 N-1/N 样本验证边界。healthz/no-success 独立 page、stage 既有 2h P95 口径不变 | agent |
| **L2-5** | 留痕可查 + 有界 + 并发安全（F11+F12） | 成功 firing→resolved 后 ledger 恰两行；失败/畸形 sender 不落“已送达”行；同批与 severity 转换的 successful receipt multiset == ledger actual；A1–A4 与 PERF 都经显式临时 `event_path` 落行；rollup 只落一行且 `values` 含子 cell；旧事件被裁；两个子进程各 K 个 expected identity → 最终集合完全相等且 JSON 可解析；损坏/超 64 MiB/I/O 失败不覆盖原 ledger，sidecar lock 被占时约 1s 有界退出，所有故障下 state 仍落盘且不会下轮重复 page | agent |
| **L2-6** | 端到端不炸（库边界，不发真实告警） | 以注入 fake sender 驱动 `run_alert_state_machine` 与 `run_performance_alerts` 全链跑通、产出新格式；`uv run pytest` / `uv run ruff check src tests` / `uv run mypy src` 全绿 | agent |
| **L2-7** | 消息可读性（结构化人工 decision gate） | 见下「L2-7 decision packet」 | **人工** |

**L2-7 decision packet**（implementer 交付前给用户）：
- **看什么**：把语义不同的**消息全文**都贴出：L2-1 🟡 busy rollup、L2-3 🟡 A4 fetch、L2-3 🔴 A4 items、L2-2 的三种 🔴 PERF page（idle/同机真实退化、public 公网路径退化、idle 缺失或不足时的影响未知/保守 page；缺失与不足若同文案可只贴其一）。agent 已自动兜底结构属性，人工只判「读起来」。
- **判据**：一个被吵醒的人能否一眼读出「影响是什么 / 要不要现在起身 / 去哪看」？🟡 是否确实不会被误当红线？
- **最短路径**：消息全文 inline 贴在 handoff，不需用户翻文件。
- **怎么回**：逐条「OK / 改措辞（指出哪句）」；PERF 反馈回 Phase 2，A4 反馈回 Phase 4，只重跑对应自动断言并重渲染受影响样本，再提交同一 decision packet。

**门值已闭式锁定**：L2-4 的 `min_pv=20`、A2 `min_samples=ceil(1/threshold)` 由 5%/stage 阈值直接推导（审查裁决 2），不依赖 implementer 自选。

## UX 契约影响

**无影响**。改动全在运维告警面（飞书消息 + 内部留痕），不改公开 web UI 的任何用户可感知行为；`docs/contracts/ux-contract.md` 覆盖的是终端用户产品面，本次不触碰。故 skip。

## 硬约束

- **不破坏 aiplanet.live**：告警子系统是独立 cron/launchd，与 serve:8000 解耦；本 plan 写面不含 web 服务路径。交付前跑 `uv run pytest` 全绿即证告警链不炸；serve 不需重启（改的是 cron 调用的 eval/format 逻辑）。
- **DB 相关测试**：设 `AI_RADAR_DB` 临时路径，避免撞本地生产库。
- **不改 transport dedup 协议**（§6 已合规）；`--alert` 通道语义只在 F2 按 severity 扩展。U7 仅重构状态机内部 lifecycle 与兼容投影，不改变外部 rule_id/dedup-key 身份。

## 交付前验证（long-task 协议）

1. L2-1..L2-6 全绿（pytest 命令级证据贴 journal）。
2. `uv run pytest` / `uv run ruff check src tests` / `uv run mypy src` 三项全绿。
3. L2-7 结构化人工 gate：渲染消息全文交用户判可读性后再 commit。
4. 过 `~/.claude/skills/review-gate/SKILL.md` 生成后 review gate。
5. F13 runbook + services + architecture/ADR + README + CHANGELOG 与代码 severity/rollup/双 webhook、窗口门、lifecycle 与 remediation page-only 语义一致（`git grep` 自证），安装契约测试证明两枚 webhook 均为必需依赖。

## 决策记录

### 用户已拍板（本轮 + 审查轮，勿再动方向）

| # | 决策 | 结论 |
|---|---|---|
| U1 | scope | F1–F6 核心 + F11–F12 留痕 + F13 文档 |
| U2 | rigor 向量 | `(A0,V1)` standard |
| U3 | notice 投递 | 🟡→NOTIFICATION webhook（page→ALERT） |
| U4 | public vantage gate（审查裁决 1） | **去掉 public→origin gate**；public-only 故障保留 page（origin 不覆盖 CF/tunnel 公网路径） |
| U5 | A2/A3 低分母门（审查裁决 2） | **锁定最小数学门**：A3 `min_pv=20`、A2 `min_samples=ceil(1/threshold)` |
| U6 | 告警历史粒度（审查裁决 3） | **notification ledger**：只记成功投递；rollup `values` 存子 cell 清单；无 record-only 个体事件 |
| U7 | A4 severity 转换生命周期（审查裁决 4） | **按 severity 独立生命周期**（状态机层内部键 `(rule_id,severity)`、对外 rule_id 不变）：各自 debounce/cooldown，跨 severity 首次进入绕目标首次 debounce、但重入仍守目标自身 cooldown；announced 旧状态各通道 resolve、pending 静默清除；旧 flat state 幂等规范化并保留兄弟 consumer 的兼容投影 |
| U8 | 投递失败语义（审查裁决 5） | **firing 成功感知、resolved best-effort**：仅成功 firing 才更新 `last_notified`/announced，失败下轮重试（不被 cooldown 压）；仅为已 announced episode 发 resolved，resolved 失败关状态不重试 |

### Defaulted Decisions（planner 拍，reviewer 已审过）

| # | 决策 | 默认 | 理由 |
|---|---|---|---|
| D1 | 告警历史载体 | `data/alert-events.jsonl`（retention 窗内逻辑 append-only + 稳定 sidecar flock），**非** radar.db 表 | 匹配 PERF evidence jsonl 模式；无 schema 迁移；避开并行 db-slimming；运行时 ledger/lock 均 gitignore |
| D2 | severity 枚举 | `page`/`notice` 两级（`status`/record-only 本轮无消费者，不设） | 保 A1/A3 零回归；最小枚举，YAGNI |
| D3 | rollup key 空间 | `PERF:rollup:busy` 单 key，与个体 `PERF:j:v:l` 及 vantage auto-resolve key 不重叠 | 共因合并与孤儿 auto-resolve 正交，分 key 避免串扰 |
| D4 | retention 天数 | 14 天，复用 PERF `RETENTION_DAYS` + alerts 侧 `_parse_dt` | 与 PERF 一致，运维心智单一；避 back-import 循环依赖 |
| D5 | ledger 操作成本熔断 | sidecar lock 最多等 1.0s；ledger 超 64 MiB 不读写 | ledger 非权威且不能阻塞告警；上限远高于现有规则数/cooldown 下 14 天预期，仅隔离锁争用或失控文件 |

## TODO / Risk

- **Risk**：F4 rollup 与 F1 severity/gating 耦合（rollup 只收 `notice` 级 busy cell）——若 F1 降级判据实现有偏，rollup 会漏收/错收。缓解：Phase 1 真值表先绿再做 Phase 3；Phase 3 重放测试（L2-1）是二者联合验收闸。
- **Risk**：旧 `alert-state.json` 只有 flat entry，而 U7 以 `lifecycles` 为真源；load 规范化必须原样保留 `since/last_notified/detail/state`、缺 severity 默认 page，并继续提供顶层兼容投影，否则会重发、错通道 resolve，或破坏 journey/remediation reader。Phase 4 的旧 state/兄弟 consumer 回归测试锁住该迁移面。
- **Risk**：ledger 可能因损坏、权限或磁盘故障漏记单次成功通知。已接受的优先级是 fail-open：保住真实投递与 state 连续性、记录可定位 error log，不让非权威历史反向制造重复 page；修复 ledger 后后续事件恢复记录，不回填无法证明的历史。
- **Risk**：并发 ledger 写与整体重写成本未来随调度频率上升；本轮用 1s lock deadline、64 MiB 上限与 L2-5 并发完整性在「不阻塞告警」前提下兜住当前规模，超过边界时留明确 error 而不覆盖，届时再改分片/独立 rotation。

## 审查修订记录（独立 Codex review-plan，2026-07-21）

首轮 `final full review` 未过，11 violation cluster；3 项取舍经用户拍板（U4–U6），8 项事实缺陷已并入上文：
1. F11 hook 点纠正为 `_apply_alert_results`（A1–A4 不经 `run_alert_results_state_machine`）。
2. `.gitignore` 补 `alert-events.jsonl` + `alert-state.json`。
3. A2/A3 的 rate 与样本门都改为使用各自配置窗口内、同口径的 numerator/denominator（非 TODO）。
4. 移除 `config/performance.toml` 出写面（阈值在 `thresholds.py`）。
5. F13 加根 `README.md` 同步。
6. L2-6 改库边界注入 fake sender（CLI 无 dry-run、会发真实告警）。
7. F11 加稳定 sidecar flock 并发保护 + expected-vs-actual 完整性断言。
8. retention 用 alerts 侧 `_parse_dt`，不 back-import journey_monitor（避循环依赖）。

后续 full re-review 发现的机械缺口也已并入正文：成功投递与同批完整性判定、稳定 sidecar lock、旧个体 busy/rollup 状态迁移、A2/A3 同窗口口径及固定边界断言、证据不足 page 的诚实措辞、sender/event_path 注入契约、双 webhook 安装依赖，以及 services/README/CHANGELOG 文档闭环。

**第二轮 re-review（round 2）**：8 项修订确认成立、reviewer 自行修正 ledger fail-open；新发 1 个 High `FPR-P135-SEVERITY-TRANSITION-001`（A4 同 rule_id 下 notice→page 升级被 cooldown 节流）经用户拍板 U7（按 severity 独立生命周期），已并入 Phase 4。待续回 reviewer 做最终 full re-review；principle gate clean 后进目标反证与终止判据。

**最终 full re-review（round 3）**：U7 主方向成立，目标反例发现 5 个后继机制缺口。4 个已按既定决策机械并入：pending severity 迁移继承 `since`、rollup first-transition evidence 绑定合成 key、notification ledger 独立 ADR、删除 scope 外 F8 的 A4 新日志落点。余 1 个 High `FPR-P1-P11-DELIVERY-SUCCESS-001`（sender 失败后仍进 cooldown、真实 page 静默丢失）经用户拍板 U8（firing 成功感知、resolved best-effort），已并入 Phase 0 item 6。

**收敛定稿（round 3 后，用户拍板）**：findings/轮 11→1→1 收敛、每轮单 High 均落核心「不静默漏报」轴，plan 已充分且部分逼近 `(A0,V1)` proportionality 上限。用户经收敛熔断 AskUserQuestion 选**定稿**——U8 这一处小改动由主 session 做聚焦自审（见下），不再开新一轮 codex 全审。review-plan gate 视为收敛终止（severity 门控已清：无遗留未决 High；收敛预算内由用户拍板定稿）。
