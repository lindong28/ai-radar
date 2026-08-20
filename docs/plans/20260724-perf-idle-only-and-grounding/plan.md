> **Archive status**: 已归档，执行完成（`state.md` 中 TASK-001..008 全部 done/resolved，L2-7 人工 gate 于 2026-07-26 通过）。执行过程产物 `state.md` / `journal.md` / `baseline.patch` 按长任务协议不入档。
> PERF idle-only 采样的当前契约见 [ADR-011](../../adr/011-perf-idle-only-probing.md)，运行口径见 [operations/monitoring-alerting.md](../../operations/monitoring-alerting.md)。产物 B（`review-alerting` / `alerting-review-principles` / `execute-plan` 三处 carrier 改动）落在 `~/.claude` 侧的 ai-agent-config 仓，不在本仓。以下为原 plan 正文，未修改。

# Plan：PERF 转 idle-only 采样（退休 F1/F4）+ 告警审查真实数据接地（3 个 infra carrier）

> **Long-task mode** — 本 plan 按 `~/.claude/references/long-task-protocol.md` 执行。实施 session 先读同目录 `state.md`（任务进度真源）与 `journal.md`（决策/踩坑流水），每完成一个 phase 更新 state、交付前跑「交付前验证」。经独立 Codex review-plan 4 轮审查收敛（principle gate clean；RP-1/2/3 → RR-RP1-A/B → TR-1..TR-5 全部并入；用户在收敛熔断点拍板定稿）。

## 输入

- **动因（已发生的事实）**：`plans/20260721-alerting-quality-fixes`（F1–F6+F11–F13，已 merge 到 main `4c5247b`）把 PERF 同机探针告警改成 busy→idle 降级 gate（F1）+ 共因 rollup（F4）。**部署后审计发现该降噪目标在生产完全没生效**：idle 样本结构性饥饿（全时段仅 5%、最近一天 0 条、每 `(journey,vantage)` 最多 6 条 << 降级所需 22），因为 probe 每小时 `:17` 跑而 pipeline 每 15min 跑（单次 ~7–11min），`:17` 总落在 busy 窗 → 每个 busy cell 恒 fail-closed page、不降级不 rollup，用户仍每小时收 ~5 条 🔴。
- **根因（H15，见 `docs/issues/harness-issues.md`）**：整条 create-plan→execute-plan→review 链只对**合成输入**验证、从不接触**真实生产数据**——L2-7 人工 gate 用的是手工构造的带 idle 样本，掩盖了"生产产不产 idle"这个前提问题。
- **用户已拍板**：(1) PERF 转 **idle-only 采样**（只在 pipeline 空闲时测，每条样本无同机争用、超预算即真、直接 page），F1/F4 **干净退休**；(2) 用本次 H15 作真实案例，改 **3 个 agent-infra carrier** 让以后自动抓到此类前提饥饿；(3) rigor **(A0,V1)**。

## L1：最终产物 + 使用者 + 使用方式

- **产物 A（ai-radar）**：简化后的 PERF 告警——same-host probe 只在 idle 窗采样，超预算旅程直接 page（无 busy 采样、无降级 gate、无 rollup）；A4 分级、A2/A3 最小门、per-severity lifecycle、notification ledger、fail-open（F5/F6/F11）**保持不变**。
- **产物 B（~/.claude agent-infra）**：`review-alerting` / `alerting-review-principles` / `execute-plan` 三处改动，使"依赖对照基线的告警设计"在审查/规划/交付时被强制用真实生产数据核查前提是否被满足。
- **使用者**：依赖飞书告警值守生产 aiplanet.live 的运维（= 维护者本人）；以及未来任何用这套 command/原则做告警设计与审查的 agent session。
- **使用方式**：运维收到告警据消息判断处置——目标是"busy-contention 假象噪声消失、只在真退化时被 page"；agent 在设计/审查告警时被 carrier 强制接地真实数据、不再 ship 前提饥饿的设计。
- **成功定义**：部署后**用真实数据实测**确认——(a) probe 逐 cell 采到足量新鲜 idle 样本、(b) busy-contention 的 ~5 条/小时 🔴 噪声消失、(c) 真实旅程退化在**确认窗 firing（22 样本）后以 page severity 投递**（非即时——需攒够 22 条 idle，且达标时长 ≤ 6h（U8 预固定））、(d) A1–A4 与 serve:8000 不受影响；且 carrier 改动后 `/custom:review-alerting` 能在类似前提饥饿场景产出 finding。

## 使用形态与调用

- PERF probe：`run.sh performance-probe`（cron）→ `run_journey_monitor`（`cli.py:384`）→ `run_performance_alerts`（`journey_monitor.py:649`）→ 共享 `run_alert_results_state_machine`。load 分类见 `classify_pipeline_load`（`:82`）/`_interval_load_class`（`:96`），据 `.pipeline.lock` 判 busy/idle。
- A1–A4：独立 launchd `live.aiplanet.ai-radar.alert`（每 5min，已在本 session 恢复并修了 boot-persistence）。**本 plan 不改 A1–A4 逻辑**，仅在 install.sh 层补 boot-persistence（见 Phase 2）。

## 取舍偏好 + 三层影响

- **简化 ≫ 保留 busy 视角**：用户选择放弃"busy 期也测、用 idle 对照区分争用vs真退化"的能力，换取**不再依赖脆弱的 idle-累积前提** + 代码/文档大幅简化。代价：busy 期不测（idle 窗每 15min 有 4–8min，覆盖仍够）。
- **关键约束：不震荡保留的 F5/F6**——退休限在 `journey_monitor.py` 的 PERF 路径；共享 `alerts.py` 状态机 / `remediation.py` / ledger 本就不再被喂 busy/rollup 结果、自然 dormant，**不动它们**。
- 三层投影：**L1** 产物是"idle-only 直接 page"的 PERF 流 + 三处 infra 接地改动；**L2** 既验"busy 噪声消失"也验"真退化仍 page"（真实数据接地，非合成）；**L3** 退休 F1/F4 + 迁移旧 busy/rollup state + probe 自门控 idle。

## Rigor：`(A0, V1)` = standard（已与用户确认）

| 轴 | 档 | 理由 |
|---|---|---|
| R 反转成本 | **A0** | 全是 git-tracked、本地可 revert 的代码/调度/文档/指令改动；无不可逆外部副作用。退休代码限在 journey_monitor PERF 路径，可整体 revert。 |
| G 回归容忍 | **V1** | 改生产告警行为 + 退休与 F5/F6 交织的 rollup 路径；误伤会静默漏报真退化或破坏保留的 ledger/lifecycle/remediation。每个改行为 unit 配测 + 单 reviewer + **对保留的 F5/F6 做回归** + **真实数据接地验证**（不重蹈 H15）。 |

**per-phase override**：
- **Phase 1（退休 + idle-only 语义）**：最高回归风险面——退休 rollup 涉及与状态机/ledger 交织的路径。V1 收紧：显式断言"退休后 PERF 只产 idle-cell page 结果、不再产 busy/rollup 结果"，且保留的 F5/F6 测试全绿。
- **Phase 2（调度 + 真实数据接地）**：V1 核心兑现——**必须用真实近期 journey-samples 或让 probe 实跑几轮**，实测 idle 被采到、告警正确、噪声消失（H15 教训的直接兑现）。
- 其余 phase：标准 V1。

## 并发隔离声明

- **写面 A（ai-radar，独立 worktree 落地）**：`src/airadar/performance/journey_monitor.py`（退休 busy/gate/rollup + idle-only；保留 `classify_pipeline_load`/`_interval_load_class`）、`src/airadar/cli.py`（performance-probe 入口若需自门控）、`deploy/lib/services.sh` + `install.sh` + `uninstall.sh`（alert boot-persistence 符号链的创建与对称删除，TR-5）、probe cron 调度（crontab，运维动作非代码）、`tests/test_performance_journey_monitor.py`、`docs/operations/monitoring-alerting.md`、新增 `docs/adr/011-perf-idle-only-probing.md` + `docs/adr/README.md` + `docs/CLAUDE.md` 索引、`CHANGELOG.md`。**不写** `src/airadar/admin/alerts.py`、`remediation.py`、`admin/thresholds.py`、`admin/metrics.py`（F5/F6 保留面）——若实施发现必须动，先回主线程确认。
- **写面 B（~/.claude，跨项目 agent-infra，各走 owning review 流程，不在 ai-radar worktree）**：`~/.claude/commands/custom/review-alerting.md`、`~/.claude/references/alerting-review-principles.md`、`~/.claude/commands/custom/execute-plan.md`。
- 开工前按 `~/.claude/references/concurrent-plan-isolation.md` 检测并发；ai-radar 改动走独立 worktree、测试用临时 `AI_RADAR_DB`。

---

## 实施 phase

### Phase 1 — PERF idle-only 语义 + 退休 F1/F4（ai-radar，最高回归风险）

**Goal**：`evaluate_performance_rules` 只处理 idle cell（超预算 → page），退休 busy→idle 降级 gate、rollup、busy-specific severity/message；probe 只在确认 idle 时产出样本。共享状态机/ledger/remediation 不动、自然 dormant。

**改动**：
1. **probe 自门控 idle**（`journey_monitor.py` probe 采集处 / `run_journey_monitor`）：仅当 `_interval_load_class(before,after)=="idle"` 时才 measure/store/emit 该样本；busy/unknown 时跳过（不 store、不 alert）。保留 `classify_pipeline_load`/`_interval_load_class`。
2. **`evaluate_performance_rules`（`:318`）退休 busy 分支**：删除 busy→idle 降级 gate（idle_clean/idle_firing/idle_absent/idle_insufficient 判定）、severity 计算、`PERF:rollup:busy` 构造与旧个体 busy key 迁移（`:483-586`）。idle cell 超预算 → `page`（severity 上不再降级为 notice——"直接 page"指 severity，**非即时**）。**关键澄清（RP-1）**：firing 门槛 `WARM_SAMPLES=20 + CONFIRMATION_WINDOWS=3` → 仍需 **22 个有效 idle 样本**才 fire，与 load_class 无关。用户拍板**保留 20+3 抗抖窗**；因此本 plan 的成立前提变成"每个启用 `(journey,vantage)` 的 idle cell 能在可接受时长内攒够 22 条"——这正是 Phase 2/L2-4 必须用真实数据逐 cell 证明的时效契约（不证明就是 H15 翻版）。
3. **`_with_firing_message`（`:284`）简化**：只保留 idle-cell 的 page 措辞；删 busy/rollup/gate_reason 分支。
4. **probe 采集鲁棒性（RP-1 现场 bug 修复，load-bearing）**：现 probe 存在两个真实故障——(a) 两次 probe 并发写 `journey-samples.jsonl` 的 torn-write（`os.replace` 竞争）产生非法末行 `}`，现有严格 JSON 解析致 probe **每轮崩溃**（已实测 `JSONDecodeError`，probe 现已 crash-silent、cron 已暂停）；(b) 单轮 probe ~253s 接近 `*/5`=300s cadence，且现锁只覆盖单次浏览器测量、不覆盖整轮。修复：整轮 singleflight/并发安全写（防 torn-write）+ **corrupt-input 容错**（读 samples 时跳过/隔离坏行、不崩）+ `skipped_overlap` 不再误映射成 `hard_failure`（避免污染 idle 告警窗）+ **bounded-liveness 恢复 invariant（TR-2+TR-4，实现无关，load-bearing）**：整轮锁必须保证"持锁 owner **异常死亡（被杀/崩溃/异常退出）或活着但卡死（hung）** 都不得永久抑制后续轮次采样至违反 U8 的 6h 上限"——否则 owner 死在或卡在持锁态 → 之后每轮 cron 都静默判 overlap 跳过 → 因 overlap 不产 `hard_failure`，probe **永久静默停采、不告警**（正是本 plan 要修的那类静默探针死亡，绝不能被 singleflight 重新引入）。不指定具体锁实现（PID+存活检查 / 带 TTL 或 deadline 的锁 / flock+进程死亡自动释放 + 卡死轮次超时终止等均可），只锁 invariant：**无论 owner 死还是卡，采样中断都不得超过 U8 允许的检测准备时长**。
5. **删对应死测试**：`tests/test_performance_journey_monitor.py` 中 busy 降级真值表、rollup、迁移、gate_reason 相关测试。
6. **部署迁移（一次性）**：idle-only 首轮部署时，previous_state 里任何仍 firing 的 `PERF:*:*:busy` 个体 key 与 `PERF:rollup:busy` 必须显式送 non-firing result → 状态机发 resolved 并置 ok，不悬挂。**共享语义在执行期钉清**（reviewer 交叉 fixture 细化，非改共享代码）：已公告 lifecycle 每 severity 各发一次 resolved receipt/ledger；pending 未公告 lifecycle 静默置 ok；发送失败沿用现状态机语义；下一轮不重复。（现生产 5 个 firing busy key 均为已公告 page lifecycle，`PERF:rollup:busy` 当前无 firing。）

**内部 verify（L3，per-phase 收紧）**：
- 单测：喂 idle cell 超预算样本 → 恰产出该 `PERF:*:*:idle` page 结果；喂 busy 样本 → probe 层跳过、`evaluate_performance_rules` 不产 busy/rollup 结果；断言 `PERF:rollup:busy` 永不出现在 results/sent。
- 迁移测试：预置旧 firing `PERF:*:*:busy` + `PERF:rollup:busy` state → 首轮各恰一次 resolved、置 ok、下轮不重复。
- 保留面回归：`tests/test_admin_alerts.py`、`test_performance_remediation.py`、`test_admin_metrics.py` 全绿（F5/F6/ledger/lifecycle/remediation 未受影响）；`uv run ruff check src tests` + `uv run mypy src` 全绿。

### Phase 2 — probe 调度 + install.sh boot-persistence + 真实数据接地（ai-radar，V1 核心兑现）

**Goal**：让 probe 可靠命中 idle 窗并**用真实数据实测**验证 idle-only 生效；顺带修 alert 服务 boot-persistence。

**改动**：
1. **probe cron 调度**：现 `17 * * * *`（hourly、总 busy）改为能命中 idle 窗的调度——default：提高频率（如 `*/5 * * * *`）配合 Phase 1 的 idle 自门控（只在 idle 时 emit）。pipeline ~7–11min/15min → idle 窗 ~4–8min，`*/5` 可稳定命中。
2. **install.sh boot-persistence + 卸载对称（TR-5）**：`install.sh`/`deploy/lib/services.sh` 的 alert（及其它服务）安装时创建 `~/Library/LaunchAgents/<label>.plist` 符号链（现只 bootstrap 不 symlink → 重启丢失，本 session 已手工补 alert 符号链）。**对称地把 `uninstall.sh` 纳入写面**：卸载时除 `launchctl bootout` 外**必须删除对应 symlink**（现 `uninstall.sh:15` 只 bootout、留 symlink → 卸载后下次登录会重新加载，违反生命周期）。修复后 `install.sh alert` 装出的服务重启后仍在、`uninstall.sh alert` 后 job 未加载且 symlink 不存在。

**内部 verify（真实数据接地 + 时效契约，H15 兑现，RP-1）**：
- **合成层**：Phase 1 单测全绿。
- **时效契约（必须 live，不可用历史回放替代）**：改调度后让 probe **实跑新 cadence 足够久**，**逐 `(journey,vantage)` cell** 断言 expected-vs-actual：(a) idle 样本以何速率产出（每小时几条 idle）、(b) 达到 22 条有效 idle 的**实测最大时长** ≤ 6h（U8 预固定，不可后放宽）、(c) 新鲜度（最近 idle 样本距今 ≤ 阈值）、(d) 对每个启用 cell 都成立、不是"至少出现一条 idle"就算过。**历史回放只能做首轮 smoke，不能替代 live 证据**（回放不证明新调度真能采到）。
- **降噪 + 不漏报**：live 数据上——busy-contention 的个体 page 噪声消失；构造/等到一个真实 idle 超预算 → 在 ≤ 上限时长内 page。证据贴 journal（命令 + 逐 cell 观测表）。
- boot-persistence + 卸载对称（TR-5）：`install.sh alert` 后确认 `~/Library/LaunchAgents` 有 alert 符号链、`launchctl print` 显示 RunAtLoad；**install→uninstall 往返验证**：`uninstall.sh alert` 后断言 job 未加载**且 symlink 不存在**（下次登录不会重新加载）。不实发告警（A1–A4 当前 clean，仅确认加载与配置）。

### Phase 3 — 文档同步（ai-radar）

**Goal**：runbook/ADR/CHANGELOG 从 busy→idle 降级+rollup 改为 idle-only 语义。

**改动**：
- `docs/operations/monitoring-alerting.md`：PERF 段改为"same-host probe 只在 pipeline 空闲窗采样、超预算 page（22 样本确认窗仍在）；无 busy 采样/降级/rollup"；删 busy→idle gate、rollup、gate_reason 描述；保留 A4/A2/A3/lifecycle/ledger 段。同步"为什么放弃 busy 对照"（idle 前提在本部署饥饿）+ 更新原 Phase 5 低量盲区/`%z` 注意事项（仍适用）。
- **RP-3：所有仍声明旧调度/busy/rollup 的当前态入口一并同步**（否则用户照 README 或 CLI help 重装会复活造成 H15 的 `:17` 饥饿调度）——`README.md`（`:96`、`:229` 附近的 PERF 调度/busy/rollup 表述）、`docs/operations/services.md`（`:13` 附近 performance-probe 调度）、`docs/architecture.md`（`:220` 附近 PERF 数据流）、以及 **CLI `--help` 用到的 `journey_monitor.py:34` 常量/文案**（若含 hourly/busy/rollup 措辞）。改新调度与 idle-only 语义。**历史 ADR/issues 不改写**（审计留痕）。
- 新增 `docs/adr/011-perf-idle-only-probing.md`：记录从 busy+idle 对照（ADR-008 相关部分 / F1+F4）转向 idle-only 的 context（生产 idle 饥饿实测数据 + probe 崩溃事件）、备选（修 idle 采样 vs idle-only vs off-host/RUM）、决策、保留 20+3 窗与时效契约、后果；在 `docs/adr/README.md` + `docs/CLAUDE.md` 索引登记；注明它 supersede F1/F4 的 PERF-gating 方向（不动 ADR-008 的 severity-lifecycle 主体）。
- `CHANGELOG.md`：一条用户视角变更——PERF 告警从 busy 降级+rollup 改为 idle-only（page，无 notice 降级），噪声消除方式变化。
- 遵循 `docs/CLAUDE.md`。

**内部 verify**：`git grep` 覆盖**全部 tracked current-state 文档 + CLI help 文案**，确认不再声称 busy→idle 降级/rollup/`:17` hourly 为当前行为（历史 ADR/issues 除外）；ADR-011 两索引可达；CHANGELOG 有本次变化。

### Phase 4 — infra carrier：review-alerting 加真实数据接地（~/.claude，走 review-skill 流程）

**Goal**：`~/.claude/commands/custom/review-alerting.md` 加两步——(a) 回放最近 N 条真实生产样本/信号过告警评估、报告**实际**会 fire 的 severity/rollup/投递；(b) 核查每个依赖对照基线的 gate/rollup（idle≥阈值、min_pv、min_samples 等）在目标部署是否真产出该基线（量+新鲜度）——前提饥饿即 finding。

**owning 流程**：主-session 交互式 `/custom:review-skill`（指令 artifact），非 codex implementer。
**内部 verify**：改后 review-skill gate 干净终止；以本次 H15 场景反推——若对 alerting-quality-fixes 跑改后的 review-alerting，能否产出"idle 前提在生产饥饿"的 finding（用真实 journey-samples 数据核验）。

### Phase 5 — infra carrier：alerting-review-principles 加前提基线原则（~/.claude，走 review-principles 流程）

**Goal**：`~/.claude/references/alerting-review-principles.md` 加一条原则——*依赖对照基线（idle 视角 / 最小分母）的 gate/降级/rollup，必须验证该基线在目标部署以所需量级+新鲜度真实产出；前提结构性不成立的设计会退化成其 fail-closed 默认（噪声或盲区）。*

**owning 流程**：主-session 交互式 `/custom:review-principles`。
**内部 verify**：新原则过 principle meta-审查；与既有 P1–P8 不冲突、不重复。

### Phase 6 — infra carrier：execute-plan L2-7 优先真实输入（~/.claude，走 review-skill 流程）

**Goal（TR-1+TR-3：强制而非 advisory，且 unavailable 只延期不关 gate）**：`~/.claude/commands/custom/execute-plan.md` 的 L2-7/交付验证——**当真实近期生产输入可取得时，MUST 用真实数据渲染人工审查/交付工件；synthetic 仅在明确论证"真实输入不可得"（如尚未部署）时允许，且须记录该论证**。不是"优先"这种可绕过的 advisory 措辞。**关键（TR-3）：unavailable 例外只能 DEFER、不能 CLOSE gate**——记录一项"部署后强制 live 补验真实基线"的义务；在补验通过前，相关告警**不得视为已激活**、plan **不得视为已最终交付**；补验失败按既有不 ship/escalate 语义处理。否则"尚未部署→记 unavailable→synthetic 交付→部署后基线继续饥饿但所有 gate 已过"正好重现 H15。

**owning 流程**：主-session 交互式 `/custom:review-skill`。
**内部 verify（TR-1：能失败的行为断言）**：改后 review-skill gate 干净终止；且**改动本身含一个会在"真实输入可取得却 synthetic-only 且无 unavailable 论证"时失败的检查点**（不能只靠 advisory 措辞——要有 execute-plan 可执行/可审的 gate 语义），否则未来执行者仍能用合成数据让同类 H15 设计过交付。措辞不与 execute-plan 现有 L2-7/§4 逻辑冲突。

---

## L2：用户视角 verify（真实数据接地，覆盖降噪 + 不漏报两向）

| # | 维度 | 可执行验收 | 人机 |
|---|---|---|---|
| **L2-1** | idle-only 语义（合成层） | 构造 ≥22 条 idle 超预算样本 → 恰一条 `PERF:*:*:idle` page（severity=page 不降级；22 确认窗仍在）；构造 busy 样本 → probe 跳过、无 busy/rollup 结果、`PERF:rollup:busy` 不出现；坏样本行 → probe 跳过不崩 | agent |
| **L2-1b** | **probe 鲁棒性能失败验证（RR-RP1-B + TR-2）** | 并发交叉测试：第一轮持整轮锁时启动第二轮 → 断言第二轮在测量/写入**前**安全跳过、JSONL 原子无 torn-write/不丢行、`skipped_overlap` 不落成 `hard_failure`、不污染 idle 窗。**并加 bounded-liveness 恢复测试（TR-2+TR-4）**：分别模拟持锁 owner **异常死亡**（残留 lock）与**活着但卡死**（hung 持锁）→ 断言两种情形后续轮次都**不被永久抑制**、采样中断不超过 U8 上限、能正常恢复（不会静默停采）。每个新增机制（整轮 singleflight / corrupt 容错 / overlap 不误映射 / bounded-liveness 恢复）都有能失败的断言 | agent |
| **L2-2** | 退休迁移不悬挂 | 预置旧 firing busy/rollup state（含已公告 page lifecycle）→ 首轮每 key 恰一次 resolved+置 ok、pending 静默清、下轮不重复 | agent |
| **L2-3** | 保留 F5/F6 零回归 + **锁定文件未改（RP-2）** | `tests/test_admin_alerts.py` / `test_performance_remediation.py` / `test_admin_metrics.py` 全绿；**且 baseline→final 的 changed-path 断言：`alerts.py`/`remediation.py`/`thresholds.py`/`metrics.py` 字节未改**（防实施者动锁定文件仍靠测试绿蒙混） | agent |
| **L2-4** | **时效契约真实数据接地（H15 兑现，核心）** | live 跑新 cadence 足够久，**逐 cell** 断言 expected-vs-actual：idle 产出率、达 22 条实测最大时长 **≤ 6h（U8 预固定，不可后放宽）**、新鲜度，对每个启用 cell 成立；busy 噪声消失；真实 idle 超预算在 ≤ 6h 内 page。历史回放仅 smoke、不替代 live。**任一 cell 超 6h → 不 ship、升级**（不得改大 6h 使其通过）。逐 cell 观测表贴 journal | agent（事实判定，非人工） |
| **L2-5** | 全链不炸 | `uv run pytest`（在具 fixture 环境或 main checkout）/ `ruff` / `mypy` 全绿；serve:8000 与 A1–A4 不受影响 | agent |
| **L2-6** | 三 infra carrier 生效 | 各 carrier 过其 owning review 流程（review-skill×2 / review-principles×1）干净终止；review-alerting 改动能在 H15 类场景产 finding | agent + 人工 |
| **L2-7** | 消息可读性（真实渲染，人工 gate） | 用**真实近期数据**渲染**启用版本明确**的 idle-only 告警**完整消息全文**交用户判读（不用合成），并给"通过 / 需修改（指出哪句）"回复入口——一眼读出影响/要不要起身/去哪看 | **人工** |

## UX 契约影响

**无影响**。改动全在运维告警面（飞书消息 + 探针调度 + agent-infra 指令），不改公开 web UI 任何用户可感知行为。`docs/contracts/ux-contract.md` 不触碰。skip。

## 硬约束

- **不破坏 aiplanet.live / serve:8000 / A1–A4**：PERF probe 与 alert-check 是独立调度，本 plan 不碰 web 服务路径与 A1–A4 逻辑；退休限在 journey_monitor PERF 路径。
- **真实数据接地不可省**（H15）：Phase 2 / L2-4 的真实数据验证是本 plan 存在的理由，不得只用合成样本结案。
- **DB 测试**：设临时 `AI_RADAR_DB`；probe 实跑用只读/副本，不写生产库。

## 交付前验证（long-task 协议）

1. L2-1..L2-6 全绿（命令级证据贴 journal），其中 **L2-4 真实数据接地必须有实测证据**。
2. `uv run pytest`（具 fixture 环境）/ `ruff` / `mypy` 三绿。
3. L2-7 用真实数据渲染 idle-only 消息交用户判可读性。
4. 过 `review-gate`；三个 infra carrier 各过其 owning review 流程。
5. 文档与代码一致（`git grep` 自证）；ADR-011 两索引可达。

## 决策记录

### 用户已拍板

| # | 决策 | 结论 |
|---|---|---|
| U1 | PERF 方向 | **idle-only 采样**（放弃 busy+idle 对照），F1/F4 退休 |
| U2 | 死代码处理 | **干净退休**，限在 journey_monitor PERF 路径；不动 F5/F6 共享面 |
| U3 | rigor | `(A0,V1)` standard |
| U4 | infra carrier | **三个都改**（review-alerting + alerting-review-principles + execute-plan），各走 owning review 流程 |
| U5 | A1–A4 | 重装恢复值守（本 session 已做）；本 plan 补 install.sh boot-persistence |
| U6 | 确认窗（RP-1） | **保留 20+3 抗抖窗**，不缩窗；plan 必须逐 cell 用真实数据补时效证明（达 22 条最大时长 + 新鲜度 + 每 cell 成立） |
| U7 | 当下 live probe | **暂停 probe cron**（`17 * * * *` 已注释）直到本 plan 落地——现 probe 崩溃静默（损坏样本文件），等重设计。plan 落地时随新调度重启 |
| U8 | 检测时效上限（RR-RP1-A） | **预固定 ≤6h**（每个启用 cell 攒够 22 条有效 idle 的实测最大时长）作为**不可后放宽**的 PASS/FAIL 上限。**禁止看 live 结果后调大**（否则=H15 循环自证）。live 实测任一 cell 超 6h → idle-only+20+3 在本部署**不可行**、停交付、升级回退缩窗或 off-host/RUM |

### Defaulted Decisions（planner 拍，reviewer 审）

| # | 决策 | 默认 | 理由 |
|---|---|---|---|
| D1 | probe idle 采样机制 | 提频（`*/5`）+ Phase 1 idle 自门控（只在 idle emit） | 对 timing 鲁棒、不依赖精确 idle 窗对点；实施者可据实测调频率 |
| D2 | ADR 承载 | 新增 ADR-011（supersede F1/F4 的 PERF-gating 方向），不改 ADR-008 主体 | pivot 是 PERF gating 方法论变更，独立 ADR 最清晰；ADR-008 severity-lifecycle 主体仍有效 |
| D3 | install.sh boot-persistence 纳入本 plan | 是（Phase 2） | 本 session 发现的真实 deploy bug，直接服务"A1–A4 durable"，与 probe 调度同属 deploy 面、顺带修 |
| D4 | 保留面不动 | alerts.py/remediation.py/thresholds.py/metrics.py 不改 | 退休 rollup 使其自然 dormant，改它们只增回归风险 |
| ~~D5~~→U8 | 检测准备时长上限 | 见下 U8（已升为用户拍板的预固定值，不再是 planner default） | 原 default 的"据 live 结果定阈"是循环自证（RR-RP1-A），已改为预固定 |

## TODO / Risk

- **Risk**：退休 rollup 涉及与共享状态机/ledger 交织的迁移路径（`journey_monitor.py:483-586`）。若迁移 result 未正确送状态机，旧 busy/rollup state 会悬挂。缓解：Phase 1 迁移测试 + L2-2 显式覆盖首轮 resolved 与下轮不重复。
- **Risk**：idle-only 后覆盖率降到 ~25–50% 时间（只在 idle 窗测）。若真退化只在 busy 期显现，会漏。已接受（用户 U1 选择）——真退化通常 idle 期也可见；off-host/RUM 是更彻底方向（ADR-011 备选，留未来）。
- **Risk（RP-1 核心，可能否定方案）**：idle-only + 保留 20+3 的成立前提是"每个启用 cell 能在 **≤6h（U8 预固定）** 内攒够 22 条有效 idle"。若 execute 期 live 实测发现 idle 产出率太低（任一 cell 达 22 条 > 6h），则 **idle-only + 20+3 在本部署不可行**——真实退化会因攒不够 22 而永不 page。此时**不得强行 ship、不得把 6h 改大使其通过**，回主线程升级：回退到"缩短确认窗"（U6 备选）或 off-host/RUM 方向（ADR-011 备选）。这是本 plan 必须 live 证明、不能假设、更不能循环自证的 load-bearing 前提（H15 教训）。
- **Risk**：真实数据接地（L2-4）依赖生产 probe 实跑足够久，需 wall-clock。缓解：历史真实 journey-samples 仅做首轮 smoke；时效契约必须 live 实测、逐 cell（不可回放替代）。
- **Risk**：退休 rollup 涉及与共享状态机/ledger 交织的迁移路径（`journey_monitor.py:483-586`）。reviewer 已确认无隐藏共享代码依赖、迁移方向成立；Phase 1 迁移测试 + L2-2 锁住首轮 resolved 与下轮不重复。
- **现场状态记录**：本 session 已暂停 probe cron（U7）；`journey-samples.jsonl` 末行损坏、probe 崩溃已确认——Phase 1 item 4 的 corrupt-input 容错 + 整轮 singleflight 是修复项，非可选。
- **TODO**：probe 提频后的探针负载与对 serve 的影响，实施者实测确认（`*/5` vs 更低频）。
