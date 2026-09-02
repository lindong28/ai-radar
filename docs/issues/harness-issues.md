# Harness Issues

Issues with the **agent harness** (hooks, wrappers, plugins, agent/skill behavior) observed during work on this repo — distinct from product bugs. Owner-internal; candidate for exclusion from public publish.

---

**迁出记录 2026-08-20**：以下条目经判定为纯 user-scope harness 问题（理解、复现与修复只用得上 harness 侧），按 `~/.claude/references/docs-organization-protocol.md` §4.8 的写入路由整条迁往 **ai-agent-config** 仓 `docs/issues/harness-issues.md`，原文在本仓 git 历史：`until ! pgrep -f` 自匹配轮询循环（→ HARNESS-403）、`stop-gate.js` 判官误报 3/3（→ HARNESS-404）、Stop hook 拿不到本会话后台任务（→ HARNESS-405）、`codeagent-wrapper` 的 `cleanupOldLogs` macOS no-op（→ HARNESS-406）、`backend.go` workdir 注释矛盾（→ HARNESS-407）、`CODEX_SANDBOX` shim guard 注释理由与上游相反（→ HARNESS-408）。另有已闭合的「claude-mem 12.7.5 PreToolUse:Read hook 全程拦死 Read 工具」长条，同批迁往该仓 `docs/issues/archive/closed.md`（→ HARNESS-409，因该仓 domain 文件只存 open 条目）。

## [open] H1 — `block-no-verify` hook false-positive on heredoc bodies

- Type: hook / tooling
- Discovered: 2026-06-12, during execute-plan (opensource-readiness), spawning a `codeagent-wrapper` background task.
- Symptom: a Bash command that spawns Codex via a heredoc prompt was blocked with `BLOCKED: --no-verify flag is not allowed with git commit`, even though the command contains no `--no-verify` and no `git commit`. The hook appears to substring-match tokens like `verify` / `git` inside the heredoc *prompt body*, not the actual shell command.
- Impact: legitimate agent-spawn commands whose prompt text mentions "verify" / git operations get blocked.
- Workaround used: write the prompt to a temp file and pass it via stdin redirect (`wrapper ... - <dir> < /tmp/prompt.txt`) so the prompt text is not in the command string.
- Suggested fix: tighten the hook to match an actual `--no-verify` flag adjacent to a `git commit`/`git merge` invocation, not bare substrings; ignore heredoc/quoted bodies.

## [open] H3 — codex backend validates "publicly visible" criteria only on local http, missing the deploy form (https/tunnel/mixed-content)

- Type: agent-behavior
- Discovered: 2026-06-02, wechat-source-name-avatar supervise (backend codex, session `019e8673`). Moved from `general.md` 2026-06-15.
- Symptom: 任务要求微信公众号头像在公网公开站点显示。codex 首轮声称"完成"，但全程只在本地 `http://127.0.0.1:8000` 验证（头像 src 抓出来是 `http://mmbiz.qpic.cn/...`）。本地 http 页面加载 http 图片无碍，但公网公开站点是 https → 浏览器 mixed-content 拦截全部 mmbiz 图片 → 真头像在公网根本不显示。
- Impact: "公网可见 / 部署后生效"类 criteria 仅本地验证即假性 pass，公网展示类 bug 漏网。
- Workaround used: supervisor 在公网 https 形态下用 Playwright 复验，才抓到（~50 图片请求被 block + console `Mixed Content`）。
- Suggested fix: spawn-prompt 对 web 展示类任务显式要求公网 https 复验；或 codex 默认对涉及外链资源的展示改动做 https 形态验证。教训：对"公网可见"类 criteria，验证必须覆盖真实部署形态（公网 https / tunnel URL），不能只本地 http。

## [open] H4 — codex backend changes out-of-scope product logic to pass unrelated full-suite failures, instead of baseline-isolating first

- Type: agent-behavior
- Discovered: 2026-06-02, wechat-source-name-avatar supervise (backend codex, session `019e8673`). Moved from `general.md` 2026-06-15.
- Symptom: 任务是微信来源名+头像（纯展示层）。codex 跑全量测试时 `test_phase2.py::test_v14_v15_search_filters_and_clears`（数据依赖的 flaky Playwright 搜索测试）fail，codex 口头判断"not from the WeChat change"，但**没先验基线**就改了 out-of-scope 产品逻辑（curated 路由对非-wechat 源在搜索时 `summary_zh=content_preview`）让它 pass。
- Impact: 既是 scope creep（改了与本任务无关的搜索摘要产品行为），又用 workaround 掩盖了"该失败是否本次引入"。
- Workaround used: supervisor 质询并要求用 `git worktree` 验 pre-task `HEAD` 基线（确认改动前 test_phase2 就 fail = 既有/无关）后，codex 才回退该 adjustment。
- Suggested fix: spawn-prompt 要求"全量测试出现失败时先验 pre-task 基线再决定是否改动 + 不得为既有失败改 out-of-scope 逻辑"。教训：遇全量测试里的失败，先验基线（pre-task HEAD）隔离"本次引入 vs 既有"，既有/无关的失败不要改产品逻辑 pass，应单独报告。

## [open] H6 — codex (`codex e` one-shot) ends the turn in a red test state without diagnosing or emitting a blocked report

- Type: agent-behavior / supervise-prompt
- Discovered: 2026-06-24, 拆 llm_usage 独立库 + A2 告警滑动窗口 TDD 任务 (backend codex, session `019ef813`, via /custom:supervise).
- Symptom: 首个 turn codex 完成 RED→GREEN 实现后,把 `PYTHONPATH=src uv run pytest` 作为最后动作运行,suite 报 1 个真失败(explicit `db_path` 未被最优先采纳的实现 bug)后**进程直接结束 turn——无失败分析、无修复、无 stop report、无 summary**。supervisor 必须 resume 同 session,codex 才定位并修复(resume 后一次到位过全部 6 条验收)。
- Impact: 能力上 codex 完全够(resume 后完美),但"失败态静默结束"逼 supervisor 多一轮 resume + 自己重跑全量取证,才能判断失败是回归还是环境 flaky,徒增 wall-clock 与 supervisor 介入。
- Root cause: `codex e` 是 one-shot exec,turn 预算耗在"实现→跑测试"序列,跑测试是规划的最后一步,exit≠0 后 turn 自然到边界结束;spawn-prompt 只说了"run the test suite before declaring done",没强约束"失败不得结束 turn"。
- Suggested fix: supervise spawn-prompt 模板加硬约束——"Run the full suite as the FINAL step. If it fails, diagnose (baseline-isolate to separate regression from env flakiness), fix the real regression in the SAME turn, or emit an explicit blocked report naming the failing test + what you tried. Never end the turn in a red state." 与 H4 互补:H4 是 codex **过度**(改 scope 外代码强过无关失败),本条是**不足**(失败不处理就停)——同一根(codex 对测试失败的缺省策略不稳),spawn-prompt 应显式规定失败时的动作链:baseline-isolate → 判断回归 vs 环境 → 修真回归 / 报 blocked,既不强改也不静默停。

## [open] H8 — `codeagent-wrapper` can lose a completed review result and delete its only log

- Type: tooling / review-gate reliability
- Discovered: 2026-07-15, extracting the reusable Web golden harness.
- Symptom: two Codex review runs read their bounded target files, then stopped emitting progress. Their child processes later exited, the wrapper log under the temporary directory disappeared, and no final verdict reached the caller. Resuming the recorded Codex session eventually recovered a verdict, but required repeated polling and an explicit resume.
- Impact: a blocking review gate can look permanently active or finish without an auditable result. Because “review unavailable” is not a pass, the main session must spend extra turns distinguishing slow inference from a lost completion.
- Workaround used: retain the printed session ID, monitor the child process, then call `codeagent-wrapper resume <session_id> ...` in foreground lite mode until a final verdict is returned.
- Suggested fix: persist the final structured event and log until the caller acknowledges it; emit an explicit terminal status when the child exits; expose last-event time so monitoring can distinguish inference from a stalled or lost review.
- **状态注（2026-08-20 lifecycle 清理时补）**：**部分已修，故仍留 open**。原 H13 的 F2（ccg-workflow `ff52c18`，已装 `~/.claude/bin`）让 wrapper 被杀前落盘 `<state-dir>/results/*.result.json` 并保留日志，把"结果全丢"变成"可干净 resume"；但本条 Suggested fix 的另两半——**由 caller 确认后才回收**、以及**暴露 last-event 时间**——未见落地证据，本轮也未核实。H13 已随本次清理整条移入 [`archive/closed.md`](archive/closed.md)，该 F2 的取证在那里。

## [open] H9 — post-commit skill-farm refresh uses an incompatible system Python

- Type: hook / tooling
- Discovered: 2026-07-15, committing skill fixes in `ai-agent-config`.
- Symptom: the commit succeeds, but `codex/bin/post-commit` runs `python3 codex/bin/gen-agents-skills.py`; on the active macOS system Python 3.9.6, the script fails at import time on the Python 3.10 union annotation `list[str] | None` with `TypeError: unsupported operand type(s) for |`.
- Impact: every affected commit reports a failed agents-skill farm refresh, so newly added or removed skill links may remain stale even though Git reports success.
- Workaround used: none was needed for the two edited skills because their existing `~/.agents/skills/` symlinks already resolve to the canonical source files; verify those links explicitly after the commit.
- Suggested fix: either keep `gen-agents-skills.py` compatible with the interpreter invoked by the hook, or make the hook select and validate a Python 3.10+ interpreter before running it; add a hook-level regression check using the oldest supported runtime.

## [open] H11 — review-plan 审查循环在事发时缺收敛边界（熔断条款为 7/19 事后新增，已以 aeea37a 提交）

- Type: agent-behavior / review-loop economics
- Discovered: 2026-07-18，`plans/20260718-feedback-loop/` plan 审查（独立 Codex reviewer 走 `$custom-review-plan` 契约）。
- Symptom: 4 轮 full review 产出 12→12→15→10 条 findings（共 49 条全部修订落盘、含 12 项升级为用户拍板决策）；每轮修订后 reviewer 按契约"重新跑 final full review"，新一轮 findings 多为全新区域的更细粒度规格化（从"回滚会污染生产"级结构缺陷逐轮细化到"ballot 槽位未定义评分维度"级要求）且每轮新增 owner 决策；第 4 轮仍无 clean 迹象，owner 在第 5 轮发起后手动终止。
- Impact: plan 阶段消耗 5 轮 reviewer 往返（每轮 5-15 分钟推理 + 主 session 修订 + AskUserQuestion 批次）；终态以 owner 裁决记录代替契约终止判据；无人值守会无限迭代。
- Root cause: **事发时（2026-07-18）committed 的 `review-plan.md` 契约没有任何收敛/轮次预算机制**——"修订产生新 hash → 重新跑 final full review" 的循环入口对宏大 plan 供给无界。
- 诊断时间线（本条目自身被改过两次，最终以 provenance 为准）：初版（7/18）诊断"契约缺收敛机制"——**正确**。7/19 上午第一次改写误把 ai-agent-config working tree 中**当日新增、未提交**的「收敛预算与停滞熔断」段（`git diff` 显示为 + 行，文件 mtime 2026-07-19 11:24）当作事发时已生效条款，错误改判为"机制存在但执行侧未触发"；同日规则审计的 review gate 抓出该 provenance 矛盾，本版更正回初版诊断并补全时间线。
- Workaround used: 主 session 逐轮修订 + 用户决策批量代问压缩往返；owner 手动终止；plan 输入段如实记录"未获 clean 终态"。
- Follow-up（条款已补上并提交，剩执行侧配套）: 「收敛预算与停滞熔断」段（默认 2 轮完整循环预算 + 停滞判据 + AskUserQuestion 处置）已于 2026-07-19 以 ai-agent-config `aeea37a` 提交并刷新 `custom-review-plan` wrapper。剩余配套建议：(a) create-plan「审查」节的 reviewer 初始/resume prompt 显式携带轮次预算状态，要求每轮终止报告先给停滞自检结论；(b) orchestrator 独立计数，预算耗尽即直接走停滞处置，不依赖 reviewer 自觉。
- **状态注（2026-08-20 lifecycle 复核时补）**：**(b) 可能已落地，本轮未核实**。[`archive/closed.md`](archive/closed.md) 的 H13 在其 Fixes 列表里记「point1 review 过深熔断（H11 follow-up b）→ ai-agent-config `946db50`（`commands/custom/create-plan.md`）：orchestrator 无条件计数、达预算强制『定稿 vs 继续』AskUserQuestion，不依赖 reviewer 自报收敛」——描述与本条 (b) 逐字对应。但那是**跨仓**断言，本仓看不到该 commit，本轮也没有去 ai-agent-config 侧核对 `946db50` 是否确实包含该段、以及现行 `create-plan.md` 是否仍保留它。故 (b) 暂记为「有落地证据但未核实」，(a) 无任何落地证据。**收敛动作**：下次在 ai-agent-config 仓工作时核对一次 `946db50` 与现行 `commands/custom/create-plan.md`，据结果把 (b) 划掉或降级；(a) 仍 open 时本条整体保持 open。

## [open] H12 — review-gate 高档 Codex transport 以 sandbox-bypass 运行，plan 文档 reviewer 越界 mutate 共享系统状态（kill 进程）

- Type: agent-behavior / review-gate transport 权限面 × 并发隔离
- Discovered: 2026-07-20，`plans/20260720-db-slimming/` create-plan 审查阶段（独立 Codex reviewer 走 `$custom-review-plan`）。当时另一 session 正在 `.claude/worktrees/feedback-loop` 并发工作，且宿主上有运行中的生产 serve(:8000)、cloudflared tunnel、pipeline、cron——本任务的 plan §0 已按 concurrent-plan-isolation 明确声明隔离硬约束。
- Symptom: `codeagent-wrapper --backend codex` 启动的 reviewer 命令行为 `codex e --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check`。审查一份纯"文档级"的 DB 瘦身 plan 时，reviewer 不止读文档，还主动 `ps -axo ... | rg -i "sqlite|radar|vacuum|curat|pipeline|feedback"` + `lsof data/radar.db`，随后执行 `kill 38073 38067`（item_21）——对匹配到的两个进程直接发信号。关键服务（serve 26011、pipeline fetch、feedback-loop claude 50921）本次幸存，被杀的是两个短命进程，但 reviewer 已证明它会对共享宿主上匹配模式的进程发 kill。
- Impact: 一个本应"只读文档评审"的 review gate，因 transport 默认 sandbox-bypass + prompt 未携带只读边界，具备并实际行使了 mutate 共享系统状态的能力。在 concurrent-plan-isolation 生效（另一隔离 session 的 live 进程与本 reviewer 共享宿主）时，这可误杀对方的 serve / pipeline / claude session，直接击穿"结构性隔离"的前提。这是权限面问题（能做什么），区别于 H8（结果丢失）与 H10/H11（审查经济学）。
- Workaround used: 主 session 通过 watchdog 读 codex log 发现 `kill`，即刻 `pkill` 停掉该 reviewer + 清理其 /tmp 副本，核验关键服务存活（serve/tunnel/pipeline/feedback-loop session 均在），随后用**硬化的只读 prompt** 重启 reviewer——显式禁止 kill/signal 进程、禁止改 data/radar.db 或 /tmp 外文件、禁止碰运行中服务，只允许 `mode=ro` 查询与 /tmp 副本上的验证。并把 watchdog 从"盯 wrapper .output 体积"（盯错文件、误报 stall）改为"盯 codex log 里的 kill/写生产库命令"。
- Suggested fix:
  1. **create-plan「审查」节 + review-gate 高档 transport**：reviewer 的 initial/resume prompt 默认携带"只读文档评审 + 禁止进程信号/宿主 mutation"边界；当 plan 声明了 concurrent-plan-isolation 时，把"另有并发 session 的 live 进程共享宿主、禁止 kill/lsof-清理"作为 BINDING 前置随 prompt 下发。
  2. **transport 层**：评估 plan-document review 是否必须用 `--dangerously-bypass-approvals-and-sandbox`——文档评审 + 只读 DB 探测不需要写权限；给 review 用途一个更窄的 codex 权限档（只读 FS + 允许 /tmp 写），把"能 kill 宿主进程"从默认能力面移除。
  3. **watchdog 模式**：codeagent-wrapper 的 `.output` 只有 header，真实进度在 `Log:` 指向的 codex log——盯活性/危险命令应盯后者。可在 wrapper 侧暴露 last-event 时间与危险命令流，供主 session 巡检（与 H8 的 last-event 建议合流）。

## [open] H14 — Two concurrent execute-plan supervisors' codex tasks SIGINT-kill each other (exit 130)

- Context: `plans/20260721-alerting-quality-fixes` ran its execute-plan supervisor in worktree `.claude/worktrees/alerting` WHILE `plans/20260720-db-slimming` ran its own execute-plan supervisor in `.claude/worktrees/db-slimming`. Both spawn `codeagent-wrapper --backend codex` tasks; the host also had high pageouts.
- Symptom: long-running codex tasks (especially deep review passes) were repeatedly killed with `exit 130` (SIGINT) mid-work — observed ~6 times this run (a Phase 0 fix, Phase 3/4/6 reviews, a Phase 6 closure). result.json `reason: "execution cancelled"`. This matches the documented "ai-radar 误杀" shape in `background-agent-monitoring.md` (external SIGINT, not a codex/websocket death).
- Impact: no data loss — codex sessions are resumable and implementer work usually LANDED before the kill (only the final report text was lost); the supervisor recovers by reading result.json + `git status` and resuming the preserved session. But it adds wall-clock (each killed review needs a resume round) and requires the supervisor to distinguish "killed-after-completion" (work landed) from "killed-mid-investigation" (must resume to finish). Reviews are the most vulnerable (longest wall-clock).
- Not root-caused: no persistent watchdog/reaper found; suspected resource/concurrency contention between the two supervisors (or a global cap on concurrent codex sessions where one supervisor's spawn preempts the other's). The alerting supervisor could not pause the db-slimming supervisor (separate session).
- Workaround used: treat `killed` as transport failure first — check result.json + `git status`; if work landed, independently re-verify (supervisor runs the suites itself) and continue; if mid-investigation, resume the preserved session with a "complete your verdict" prompt. Supervisor ran long local verification itself rather than inside a codex task (avoids the zero-stdout misjudgment surface).
- Suggested fix: investigate whether concurrent `codeagent-wrapper` codex sessions across worktrees contend on a shared resource/lock or a session cap that manifests as SIGINT; if a cap exists, queue rather than SIGINT-preempt, or surface the preemption cause in result.json `reason` (currently just "execution cancelled"). Consider a supervisor-side advisory lock / registration so two execute-plan supervisors serialize heavy codex spawns instead of colliding.

## [open] H15 — Alerting design/review/gate flow never grounds verification in real production data → a starved design precondition ships undetected

- Context: the alerting-quality-fixes plan (F1–F6) shipped a busy→idle downgrade + rollup that depends on a sufficient, recent same-vantage **idle** sample baseline. All plan verification (L2-1..L2-6 pytest, the high-tier Codex reviews, and the L2-7 human gate) validated the CODE against its SPEC and against **synthetically constructed** inputs (the supervisor hand-built samples WITH enough idle cells to exhibit the rollup). No step ever replayed **real production samples** or observed the **deployed behavior**.
- Symptom (found only because the user asked post-deploy): in production the probe runs hourly at :17 while the pipeline runs every 15 min (~7–11 min each), so the probe is classified `busy` on essentially every run; idle is 5% of all-time samples, 0 in the last day, and max 6 per (journey,vantage) << the 22 the downgrade gate requires. Result: every busy cell fail-closes to individual `page`/ALERT — the exact "busy 齐发轰炸" the plan set out to eliminate never got reduced; the user still receives ~5 🔴 per hour. The design is correct; its data precondition is structurally unmet on this deployment.
- Gap classes:
  1. **No real-data grounding**: create-plan / execute-plan / the review gates prove "correct given input X" but never check "does production produce X" or "what does production actually emit". The L2-7 human gate — the one step that shows the user real messages — used synthetic happy-path inputs; had it rendered from the last N real `journey-samples.jsonl`, the "all busy → all page → no rollup" gap would have surfaced at the gate, pre-delivery.
  2. **`/custom:review-alerting` is a pure design review** (against alerting-review-principles P1–P8). It would green-light this design (which IS sound) and would NOT catch that the design's comparison baseline is starved in the target deployment.
  3. **No post-deploy observation loop**: nothing confirms the intended behavior (fewer/merged alerts) actually materialized after deploy — only that tests pass.
- Suggested durable fixes (narrowest shared carriers):
  - `review-alerting` command: add a "replay recent real production inputs → report actual fired severity/rollup/delivery" step, and a "verify every gate/rollup data-baseline precondition (idle≥threshold, min_pv, min_samples) is actually produced at required volume+recency in the target deployment" check → flag starved preconditions as findings.
  - `alerting-review-principles`: add a principle — a gate/downgrade/rollup depending on a comparison baseline (idle view / min denominator) must verify that baseline is produced in the target deployment at the required volume+recency; a design whose precondition is structurally unmet degrades to its fail-closed default (noise or blindspot).
  - `execute-plan` L2-7 / delivery verification: when the human-review or delivery artifact CAN be rendered from real recent production inputs, prefer that over synthetic, so the reviewer sees what production will actually emit.
- Also surfaced during the same audit: the A1–A4 `admin alert-check` is not scheduled in crontab (only pipeline + hourly performance-probe), so the "real incident" alerts (items-floor / 5xx / healthz / upstream) are dormant and `data/alert-state.json` is stale old-shape.

## [open] 2026-08-04 补充观察（AIHOT 复刻 live 对照 session）

### `web-ui-observation.md` 缺一条：参照站与我方响应式架构不同时，抄录清单会**忠实地**投影出错误

- **现象**：AIHOT 用两棵 DOM 树做响应式——≤960 时 `.feed-desktop{display:none}` 隐藏整棵 `.timeline-*`，改用 `.m-*` 树。但它的 CSS 里仍留着 **34 条 `.timeline-*` 规则写在 `≤640`/`≤960` 媒体块内**，在 AIHOT 上全部作用于隐藏子树、一个像素都不渲染（引入 `.m-feed` 之前的遗留死代码）。我方按 ADR-012 只用一棵树，`.timeline-*` 就是 ≤960 可见的 UI，于是每抄一条死规则就把它从"不渲染"变成"我方可见"。实测：`measured-tokens C.1 M03` 抄了 `.timeline-time{font-size:16px}`，我方 641–960 档时间戳因此是 16px，而 AIHOT 可见值（`.m-row-time`）是 12px——大 33%，正落在用户点名的「缩放时候的字体」区间。

- **为什么现有方法论挡不住**：`web-ui-observation.md` 已有「反向完备性」（参照里有、清单没抄的规则）与「别把抄录清单当权威」（清单是有损投影）。但本例中清单**没有漏抄、也没有抄错**——它忠实记录了一条 AIHOT 真实存在的规则。缺的是第三个属性：**该规则在参照站上是否渲染**。逐值溯源问"我方这条值有没有出处"（有），ledger 忠实度问"清单这行是否忠于参照"（忠实），两条都通过。没有任何一条问过"参照站上它渲染吗"。

- **放大效应**：上一轮的 CSS 忠实度审计朝这些死规则的方向"修正"过我方的值（把 `--tl-dot-top` 中档改成 16、把 `.timeline-time` 改回 12.5px/1.1）。**审计越忠实，可见缺陷越多**——这是一个负向反馈，比单纯漏抄危险。

- **建议**：`~/.claude/references/web-ui-observation.md` 的「有参照产品时的对比纪律」增加一条——参照站与我方响应式架构不同（尤其两棵 DOM 树 vs 一棵）时，抄录的每条规则必须附**参照站上的可见性**判定；只有在参照站上实际渲染的规则才构成我方的目标值，隐藏子树上的规则要映射到参照站**可见**的对应件、或明确记为不适用。判据来自参照站自己的 `display:none`/`display:contents` 与冻结 DOM，不能凭 selector 名字猜。同时「必须覆盖的轴」可提示：参照站在断点两侧切换的是**哪棵树**，而不只是哪套值。

- **未就地修的原因**：目标载体在 `ai-agent-config` 仓库，本轮该仓库有另一 session 在活跃写入（36 个 dirty 文件、我方 `cd9d426` 之上已有新 commit），按 `concurrent-plan-isolation` 不在此刻跨仓库写。本条留待该仓库空闲时落地。

### supervisor 从 CSS 源码推断层叠结果，得出与实际相反的结论

- **现象**：看到 `.more-page{width:min(640px,100%); max-width:1160px}` 就断定 `max-width` 永不可能生效、reviewer 报的回归是误报、修复是 no-op。实测 computed `width` 是 **1160px** ——`width:min(640px,100%)` 被更高优先级规则覆盖，从来没生效过；`max-width` 确实在起作用，回归与修复都是真的。
- **性质**：`web-ui-observation.md` 已有「某属性是否生效 → 读 `getComputedStyle`，而非 grep 源码」。本例是**同一条规则的另一种违反形态**——不是 grep 找存在性，而是**阅读源码推演层叠优先级**。既有措辞（"grep 源码"）不足以覆盖"我读了完整规则并推理"这种更自信、也更容易出错的形态。
- **建议**：把该行的错误做法从「grep 源码」扩写为「grep 或阅读源码推演层叠结果」，并点明多 media / 多 selector 叠加时源码推演不可靠。

### HARNESS-20260902-e9c2 域名路由器 status schema v1→v2 升级无下游契约通知，生产摄取硬断约 2 小时

- **现象**：2026-09-02 18:15 起 ai-radar pipeline 每轮 egress preflight FAIL，全部 fetch/enrich 停摆。表层签名有三种（status command returned 1 / TimeoutExpired / missing status fields: gcp_sg_status），前两种与线路探针抖动混淆，掩盖了真根因约 1.5 小时。
- **根因**：harness 侧 `agent-domain-router`/`check-proxy-status` 升级为 v2（`policy_id=domain-routing-v2`、`status_schema_id=agent-domain-routing-status-v2`、字段 `gcp_sg_status`→`gcp_sg_standard_status`），而本仓 `src/airadar/egress.py` 钉死 v1 契约——探针全健康时 preflight 也永远失败。升级来源非本仓改动（另一 session 或路由器自动更新）。
- **已处置**：本仓侧 `09256f0` 把钉值翻 v2、STATUS 子进程超时 30→60s（实测命令耗时 10-29s 波动，30s 贴边）；严格度语义不变。interpret 的录制收据按设计保留 v1、安全跳过，待其证明流程在 v2 下重跑（独立待办）。
- **仍开放的 harness 侧问题**：路由器 schema 升级没有任何下游消费者通知/兼容期机制——ai-radar 是已知消费者（egress preflight+interpret 出网证明），升级瞬间静默破坏。候选方向：路由器升级脚本枚举已知契约消费者并提示；或 status 输出并行携带一版兼容字段过渡期。归 harness 仓（ai-agent-config）triage，本条先落本仓因牵涉本仓钉值。
