# Harness Issues

Issues with the **agent harness** (hooks, wrappers, plugins, agent/skill behavior) observed during work on this repo — distinct from product bugs. Owner-internal; candidate for exclusion from public publish.

---

## H1 — `block-no-verify` hook false-positive on heredoc bodies

- Type: hook / tooling
- Discovered: 2026-06-12, during execute-plan (opensource-readiness), spawning a `codeagent-wrapper` background task.
- Symptom: a Bash command that spawns Codex via a heredoc prompt was blocked with `BLOCKED: --no-verify flag is not allowed with git commit`, even though the command contains no `--no-verify` and no `git commit`. The hook appears to substring-match tokens like `verify` / `git` inside the heredoc *prompt body*, not the actual shell command.
- Impact: legitimate agent-spawn commands whose prompt text mentions "verify" / git operations get blocked.
- Workaround used: write the prompt to a temp file and pass it via stdin redirect (`wrapper ... - <dir> < /tmp/prompt.txt`) so the prompt text is not in the command string.
- Suggested fix: tighten the hook to match an actual `--no-verify` flag adjacent to a `git commit`/`git merge` invocation, not bare substrings; ignore heredoc/quoted bodies.

## H2 — claude-mem appends `<claude-mem-context>` into tracked `AGENTS.md` each session

- Type: plugin / open-source-cleanliness
- Discovered: 2026-06-12, post-TASK-010; working tree showed `M AGENTS.md` re-introducing `aiplanet.live` and observation summaries via an injected `<claude-mem-context>` block.
- Impact: `AGENTS.md` is a tracked file intended for the public open-source repo. The plugin re-pollutes it every session with memory context that can contain project/personal observations — a recurring leak risk that undermines sanitization. (The TASK-010 public clone was cut from the committed clean `AGENTS.md`, so it is unaffected; the pollution was uncommitted.)
- Reproduced: 2026-07-15, a read-only nested Codex review launched through `codeagent-wrapper` appended the same block to an isolated candidate's tracked `AGENTS.md`; the reviewer prompt explicitly prohibited writes. The supervisor removed only the injected block and verified the file was byte-identical to `HEAD` before continuing.
- Reproduced again: the clean Phase 0 canonical verifier passed when invoked with the candidate's `.venv/bin/python`, but each controller subcommand launched through `uv run ... verifier.py` observed a transient injected `AGENTS.md` and correctly failed repository identity with `candidate is dirty`; the block disappeared when the child process exited. The project verifier was changed to invoke its controller with the already validated Python runtime, while keeping canonical project checks under `uv run`.
- Workaround used: `git restore AGENTS.md` before committing.
- Update 2026-07-19: `AGENTS.md` is now a symlink to `CLAUDE.md`（规则加载断链修复，owner 已裁决维持此方向）。claude-mem 的追加会穿透链接写入 `CLAUDE.md` 真身——git 表现为 `M CLAUDE.md`，commit/publish 前的清洁校验对象同步改为 `CLAUDE.md`。**清理必须精细剥离 `<claude-mem-context>...</claude-mem-context>` 块**（如 sed 定界删除），而非整文件 `git restore`——后者会连带丢弃同文件的合法未暂存修改，`docs/issues/general.md` 已有该做法造成不可恢复数据丢失的记录；仅当确认无其它本地修改时才可整文件 restore。
- Update 2026-07-20（复核不复现，降级为已缓解）: 当前 claude-mem **v12.7.5 不再写盘**——核查证据三点:(1) 主 checkout 与 `feedback-loop` worktree 的 `CLAUDE.md` 现均干净;(2) `git log -S "claude-mem-context" -- CLAUDE.md AGENTS.md` 零命中(该块从未进过 git 历史);(3) 插件脚本对 `CLAUDE.md`/`AGENTS.md`/`CLAUDE.local.md` 只作**上下文源读取**、无指向它们的 `writeFileSync`——SessionStart hook 只把记忆**注入 session**(如本类 session 开头的 `<claude-mem-context>` 注入块只在会话上下文、不落盘)。H2 记录的写盘污染在当前版本已消失(很可能插件升级后转为纯 session 注入)。据此**降级为「已缓解待观察」**:commit/publish 前的 `CLAUDE.md` 清洁校验作为廉价保险保留,但不再需要主动追修;若未来某版本回归写盘,再按下条 Suggested fix 处置。
- Suggested fix (owner harness config): exclude `AGENTS.md` from claude-mem's injection target, or strip the `<claude-mem-context>` block pre-commit, or keep memory context in an untracked file. Until fixed, verify `CLAUDE.md` is clean before any commit/publish.

## H3 — codex backend validates "publicly visible" criteria only on local http, missing the deploy form (https/tunnel/mixed-content)

- Type: agent-behavior
- Discovered: 2026-06-02, wechat-source-name-avatar supervise (backend codex, session `019e8673`). Moved from `general.md` 2026-06-15.
- Symptom: 任务要求微信公众号头像在公网公开站点显示。codex 首轮声称"完成"，但全程只在本地 `http://127.0.0.1:8000` 验证（头像 src 抓出来是 `http://mmbiz.qpic.cn/...`）。本地 http 页面加载 http 图片无碍，但公网公开站点是 https → 浏览器 mixed-content 拦截全部 mmbiz 图片 → 真头像在公网根本不显示。
- Impact: "公网可见 / 部署后生效"类 criteria 仅本地验证即假性 pass，公网展示类 bug 漏网。
- Workaround used: supervisor 在公网 https 形态下用 Playwright 复验，才抓到（~50 图片请求被 block + console `Mixed Content`）。
- Suggested fix: spawn-prompt 对 web 展示类任务显式要求公网 https 复验；或 codex 默认对涉及外链资源的展示改动做 https 形态验证。教训：对"公网可见"类 criteria，验证必须覆盖真实部署形态（公网 https / tunnel URL），不能只本地 http。

## H4 — codex backend changes out-of-scope product logic to pass unrelated full-suite failures, instead of baseline-isolating first

- Type: agent-behavior
- Discovered: 2026-06-02, wechat-source-name-avatar supervise (backend codex, session `019e8673`). Moved from `general.md` 2026-06-15.
- Symptom: 任务是微信来源名+头像（纯展示层）。codex 跑全量测试时 `test_phase2.py::test_v14_v15_search_filters_and_clears`（数据依赖的 flaky Playwright 搜索测试）fail，codex 口头判断"not from the WeChat change"，但**没先验基线**就改了 out-of-scope 产品逻辑（curated 路由对非-wechat 源在搜索时 `summary_zh=content_preview`）让它 pass。
- Impact: 既是 scope creep（改了与本任务无关的搜索摘要产品行为），又用 workaround 掩盖了"该失败是否本次引入"。
- Workaround used: supervisor 质询并要求用 `git worktree` 验 pre-task `HEAD` 基线（确认改动前 test_phase2 就 fail = 既有/无关）后，codex 才回退该 adjustment。
- Suggested fix: spawn-prompt 要求"全量测试出现失败时先验 pre-task 基线再决定是否改动 + 不得为既有失败改 out-of-scope 逻辑"。教训：遇全量测试里的失败，先验基线（pre-task HEAD）隔离"本次引入 vs 既有"，既有/无关的失败不要改产品逻辑 pass，应单独报告。

## H5 — agent-browser from a non-GUI (Background) session forces headless + webdriver=true, breaking human-in-the-loop login and tripping bot-protection

- Type: agent-behavior / tooling
- Discovered: 2026-06-24, Cloudflare Access `/admin*` setup (backend codex, session `019ef777`, then supervisor direct).
- Symptom: 任务要 codex 用 agent-browser 配 Cloudflare Zero Trust。codex/Claude 的 shell 处于 `launchctl managername=Background`（SSH/后台/子 agent 无 Aqua GUI 访问）→ agent-browser 起的浏览器被强制 `--headless=new`，用户远程桌面**看不到任何窗口**，无法完成人工登录；且无头浏览器 `navigator.webdriver=true` + `HeadlessChrome` UA 被 Cloudflare Turnstile 反复拦截（"请验证您是真人"死循环）。即便 `--headed` 也无效（Background 会话画不出窗口）。
- Impact: 任何需要人工介入（登录/2FA/CAPTCHA）或受 bot 防护的站点，用 agent-browser 自带浏览器全程不可行；浪费大量 wall-clock 在"找不到窗口 + 过不了验证"。
- Workaround used: `open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir=/tmp/...`（经 `open` 路由到 GUI/Aqua 会话 → 真实可见浏览器、`webdriver=false`）+ `agent-browser --cdp 9222` 连接驱动；用户在可见窗口人工过验证。最终对 Cloudflare 这类自家控制台，改用其 **REST API**（CLOUDFLARE_API_TOKEN）一步到位，彻底绕开浏览器。
- Suggested fix: APPLIED — `~/.claude/skills/agent-browser/SKILL.md`（hard-link ×3）已把 Default Path 改为默认 `--headed`，并加了"Background 会话画不出窗口、改用 open+CDP 连真实浏览器、launched 浏览器仍带 webdriver 标志"的说明。教训：(1) 需人工介入/受 bot 防护的站点，不要让 agent-browser 自起浏览器，应 `open` 真实浏览器 + `--cdp` 连接；(2) 自动化厂商自家控制台（Cloudflare 等）天然对抗 bot 检测，优先用其 API 而非浏览器自动化。

## H6 — codex (`codex e` one-shot) ends the turn in a red test state without diagnosing or emitting a blocked report

- Type: agent-behavior / supervise-prompt
- Discovered: 2026-06-24, 拆 llm_usage 独立库 + A2 告警滑动窗口 TDD 任务 (backend codex, session `019ef813`, via /custom:supervise).
- Symptom: 首个 turn codex 完成 RED→GREEN 实现后,把 `PYTHONPATH=src uv run pytest` 作为最后动作运行,suite 报 1 个真失败(explicit `db_path` 未被最优先采纳的实现 bug)后**进程直接结束 turn——无失败分析、无修复、无 stop report、无 summary**。supervisor 必须 resume 同 session,codex 才定位并修复(resume 后一次到位过全部 6 条验收)。
- Impact: 能力上 codex 完全够(resume 后完美),但"失败态静默结束"逼 supervisor 多一轮 resume + 自己重跑全量取证,才能判断失败是回归还是环境 flaky,徒增 wall-clock 与 supervisor 介入。
- Root cause: `codex e` 是 one-shot exec,turn 预算耗在"实现→跑测试"序列,跑测试是规划的最后一步,exit≠0 后 turn 自然到边界结束;spawn-prompt 只说了"run the test suite before declaring done",没强约束"失败不得结束 turn"。
- Suggested fix: supervise spawn-prompt 模板加硬约束——"Run the full suite as the FINAL step. If it fails, diagnose (baseline-isolate to separate regression from env flakiness), fix the real regression in the SAME turn, or emit an explicit blocked report naming the failing test + what you tried. Never end the turn in a red state." 与 H4 互补:H4 是 codex **过度**(改 scope 外代码强过无关失败),本条是**不足**(失败不处理就停)——同一根(codex 对测试失败的缺省策略不稳),spawn-prompt 应显式规定失败时的动作链:baseline-isolate → 判断回归 vs 环境 → 修真回归 / 报 blocked,既不强改也不静默停。

## H7 — agent-browser daemon ignores configured timeout and repeatedly stalls on healthy local pages

- Type: tooling / browser automation
- Discovered: 2026-07-14, web refactor Batch 3 rollback-page category smoke test.
- Symptom: the first named session opened `/index.html`, captured a snapshot with 40 cards, and clicked the model category; the server logged the expected filtered API request with HTTP 200. A stale daemon then sent later eval/get commands to `about:blank`. After `agent-browser close --all`, three consecutive `open`/`batch` attempts still timed out at about 25.5 seconds even with `AGENT_BROWSER_DEFAULT_TIMEOUT=60000` and `120000`. Server logs showed the HTML, API, and static assets all returning 200 throughout.
- Impact: a healthy local UI cannot complete an agent-browser smoke test, and increasing the documented timeout does not affect the daemon's effective deadline. Repeated retries can waste unbounded wall-clock and leave product acceptance incomplete.
- Workaround used: stop after three repeated failures; retain the successful first snapshot/click plus server request evidence, then use fresh golden HTTP comparison, focused Playwright, and static import/export contracts for the remaining product checks. Do not report the browser smoke as passed.
- Suggested fix: make the daemon honor the configured default timeout for `open`, `batch`, and post-click evaluation; expose the effective timeout and active page/session in diagnostics; make stale-session recovery reset `about:blank` state deterministically.
- Fix APPLIED 2026-07-20 (root cause was different than the suggested fix assumed): agent-browser is a **third-party compiled Homebrew binary** (v0.31.2; the `.js` is a thin launcher) — the daemon cannot be patched, so the durable fix is the user-owned SKILL.md, not the tool. And the real defect was a **doc error**, verified live: `AGENT_BROWSER_DEFAULT_TIMEOUT` (default `25000`ms = the ~25.5s) is read by the daemon at spawn (a client-env change against a running daemon is ignored), and `close` / `close --all` do **not** kill the daemon (it survives `close --all`) — so the doc's "close to reset a stale daemon" claim was wrong in four places and sent agents into unbounded retries. Corrected in ai-agent-config `af9f344` (agent-browser SKILL.md): one canonical "Troubleshooting: Stale Daemon" section, reset = namespace-scoped `pkill` of the process (not `close`), and timeout-is-daemon-spawn-level. Passed `/custom:review-skill` (2 rounds — caught a cross-file contradiction and an over-broad `pkill` that would nuke sibling agents' daemons).

## H8 — `codeagent-wrapper` can lose a completed review result and delete its only log

- Type: tooling / review-gate reliability
- Discovered: 2026-07-15, extracting the reusable Web golden harness.
- Symptom: two Codex review runs read their bounded target files, then stopped emitting progress. Their child processes later exited, the wrapper log under the temporary directory disappeared, and no final verdict reached the caller. Resuming the recorded Codex session eventually recovered a verdict, but required repeated polling and an explicit resume.
- Impact: a blocking review gate can look permanently active or finish without an auditable result. Because “review unavailable” is not a pass, the main session must spend extra turns distinguishing slow inference from a lost completion.
- Workaround used: retain the printed session ID, monitor the child process, then call `codeagent-wrapper resume <session_id> ...` in foreground lite mode until a final verdict is returned.
- Suggested fix: persist the final structured event and log until the caller acknowledges it; emit an explicit terminal status when the child exits; expose last-event time so monitoring can distinguish inference from a stalled or lost review.

## H9 — post-commit skill-farm refresh uses an incompatible system Python

- Type: hook / tooling
- Discovered: 2026-07-15, committing skill fixes in `ai-agent-config`.
- Symptom: the commit succeeds, but `codex/bin/post-commit` runs `python3 codex/bin/gen-agents-skills.py`; on the active macOS system Python 3.9.6, the script fails at import time on the Python 3.10 union annotation `list[str] | None` with `TypeError: unsupported operand type(s) for |`.
- Impact: every affected commit reports a failed agents-skill farm refresh, so newly added or removed skill links may remain stale even though Git reports success.
- Workaround used: none was needed for the two edited skills because their existing `~/.agents/skills/` symlinks already resolve to the canonical source files; verify those links explicitly after the commit.
- Suggested fix: either keep `gen-agents-skills.py` compatible with the interpreter invoked by the hook, or make the hook select and validate a Python 3.10+ interpreter before running it; add a hook-level regression check using the oldest supported runtime.

## H10 — Self-built verification machinery dominated wall-clock in the continuous-performance plan execution

- Type: agent-behavior / plan-execution economics
- Discovered: 2026-07-17, reviewing the codex session executing `plans/20260715-continuous-performance-loop/` (journal.md is the primary evidence).
- Symptom: the product fix (archive count-cache invalidation + `/wechat` connection close) was complete with RED/GREEN evidence on day 1 (7/15 evening). The following ~2 days went almost entirely to the plan's self-built verification machinery: (1) the 7-node canonical chain was fully rebuilt at least 6 times, of which at least 2 were triggered by mechanical frozen-SHA identity syncs (2-line commits) and 2 failed on sandbox-environment issues (Seatbelt `.venv` ignore semantics, bash 3.2 heredoc temp files) unrelated to product or logic; (2) the production-action ledger anchor was hash-bound to the task-manifest file, so the first legitimate manifest evolution deadlocked the entire approval chain and required a new "manifest evolution contract" plus 3 review rounds; (3) an approval receipt had a ~16-minute expiry window against a human responder who answered 3h24m later — guaranteed expire-and-reprepare; (4) adversarial reviews of internal tooling ran 2-4 rounds per fix, repeatedly escalating findings premised on same-UID-attacker capabilities that the plan's locked trust model had explicitly excluded.
- Impact: ~1.5 of 2 execution days spent on machinery self-verification rather than deliverable progress; the shipped performance fix sat undeployed the whole time.
- Workaround used: user sent a mid-execution steering instruction — batch remaining production actions into one sequence, allow reuse of unaffected canonical green lights for non-authority-path fixes (tests/fixtures/docs), one final full-chain rebuild before delivery; plus a standing policy pre-approving non-push local actions (eliminates receipt-expiry rounds).
- Suggested fix: APPLIED at the durable carriers — `~/research/ai-agent-config/claude/references/plan-review-principles.md` new conditional principle 17 (Verification Machinery Operating Contract: incremental re-verification semantics, receipt windows matched to responder, review depth bounded by declared threat model, identity/anchor evolution contract) and `claude/skills/review-gate/SKILL.md` (feed declared threat model to reviewers; findings premised on excluded capabilities cap at MEDIUM). Both passed their specialty review gates (review-principles 3 rounds / review-skill 2 rounds + verification lens) and are committed in ai-agent-config as abd1f74.
- Recurrence 2026-07-19 (execution-side, `plans/20260718-feedback-loop/` P0 implementation review): the applied fix lives in principle/skill text but was not honored at review-spawn time. P0 (`TASK-001`) is baseline + safety infra that the plan's own rigor vector (D8) labels default `(A1,V1)` — only the replica guard (~4 lines + a symlink-bypass test) is genuinely high-blast-radius; the rest is read-only metrics (`eval/baseline.py`) and test scaffolding. Yet the whole ~1000-line bundle went into a HIGH-tier full-bundle adversarial Codex review *on top of* 2 dev-time fix-verification rounds and the mandatory review-gate, and sat frozen ~15 min while a long-lived codex process (80 min elapsed) ran it. Same shape as H10/H11: review depth not bounded to the declared per-unit rigor, escalation defaults to "up". Owner decision (2026-07-19): narrow P0 adversarial verification to the replica guard + production-DB immutability, let review-gate + the green suite carry the low-risk remainder, then commit; for P1–P6, scope each per-task adversarial pass to the units D8 tags `(A2,·)` rather than the full diff. This strengthens the case for H11 follow-up (b): the orchestrator must scope/bound the implementation-phase review from the plan's declared rigor label, not leave depth to reviewer discretion.
- Fix APPLIED 2026-07-19 at the durable carrier — `~/research/ai-agent-config/claude/skills/review-gate/SKILL.md` 「分档执行」§ gained an "对抗启动面（施加对抗前必做）" forcing step: before 中/高档 adversarial on a multi-hunk diff, the author partitions hunks into authority-defining (deep adversarial) vs frozen-authority mechanical/read-only payload (excluded) per rigor-tiers' "对抗审查只施于定义或修改 authority 的 unit" rule, records the partition to the gate opening, and feeds the excluded set to the reviewer via 「喂什么」; the reviewer MUST cheap-validate each excluded hunk and return a per-hunk disposition in the 返回契约 (confirm frozen / re-judge as authority → pull into adversarial / unverifiable → 未能核实项), so a silent skip becomes an incomplete return contract caught by 「审不了 ≠ 审过」. The over-rigor direction (deep-reviewing a mostly-mechanical bundle unpartitioned) is an author/main-session gate-opening self-check. Passed its /custom:review-skill gate (3 adversarial rounds: the naive scoping first introduced a symmetric under-coverage hole — author mislabels authority as mechanical to escape adversarial — which the closed-loop disposition mechanism above resolves). Committed in ai-agent-config `88b8633`, which also bundled unrelated concurrent-session edits (`execute-plan.md`, `supervise.md`, the tracked `env` template) under a generated message.

**[Correction 2026-07-22 — the "auto-commit daemon" here was a misdiagnosis.]** An earlier version of this note attributed the `88b8633` bundling to an "auto-commit daemon … firing mid-work without author sign-off." A thorough investigation (ruled out every launchd / cron / git-hook / Claude-hook candidate + hermes / openclaw / cogfs / `claude/daemon`; no `core.hooksPath`, no git wrapper) found **there is no auto-commit daemon**. ai-agent-config commits are made by whichever **interactive agent session** finishes a unit of work in the **shared worktree**, calling git via the repo's own create-commit discipline: selective staging (`git add -A` is forbidden by `create-commit/SKILL.md`), `.gitignore` already separating tracked instruction-artifacts from ignored runtime churn, and `suggest-commit-message` generating the conventional message via a `checkpoint` placeholder → `git commit --amend -m`. The `88b8633` bundling was a **one-off staging lapse by a concurrent agent in the shared worktree**, not a systemic committer. **Root cause reframed**: multiple concurrent agent sessions share this config worktree and the commit discipline is declarative-only (no machine gate), so a concurrent session can commit another session's work (observed 2026-07-22: `f94baa1` cleanly committed a completed review-agent-rules edit before its author session got to it) or bundle when staging isn't scoped. **Decision 2026-07-22**: existing discipline (selective staging + `.gitignore` + openclaw committer blacklist) covers the common case and observed harm is low; NOT adding a pre-commit machine gate or worktree isolation for config edits (disproportionate to the low-frequency, low-harm residual). Nothing to remove — no daemon existed.

## H11 — review-plan 审查循环在事发时缺收敛边界（熔断条款为 7/19 事后新增，已以 aeea37a 提交）

- Type: agent-behavior / review-loop economics
- Discovered: 2026-07-18，`plans/20260718-feedback-loop/` plan 审查（独立 Codex reviewer 走 `$custom-review-plan` 契约）。
- Symptom: 4 轮 full review 产出 12→12→15→10 条 findings（共 49 条全部修订落盘、含 12 项升级为用户拍板决策）；每轮修订后 reviewer 按契约"重新跑 final full review"，新一轮 findings 多为全新区域的更细粒度规格化（从"回滚会污染生产"级结构缺陷逐轮细化到"ballot 槽位未定义评分维度"级要求）且每轮新增 owner 决策；第 4 轮仍无 clean 迹象，owner 在第 5 轮发起后手动终止。
- Impact: plan 阶段消耗 5 轮 reviewer 往返（每轮 5-15 分钟推理 + 主 session 修订 + AskUserQuestion 批次）；终态以 owner 裁决记录代替契约终止判据；无人值守会无限迭代。
- Root cause: **事发时（2026-07-18）committed 的 `review-plan.md` 契约没有任何收敛/轮次预算机制**——"修订产生新 hash → 重新跑 final full review" 的循环入口对宏大 plan 供给无界。
- 诊断时间线（本条目自身被改过两次，最终以 provenance 为准）：初版（7/18）诊断"契约缺收敛机制"——**正确**。7/19 上午第一次改写误把 ai-agent-config working tree 中**当日新增、未提交**的「收敛预算与停滞熔断」段（`git diff` 显示为 + 行，文件 mtime 2026-07-19 11:24）当作事发时已生效条款，错误改判为"机制存在但执行侧未触发"；同日规则审计的 review gate 抓出该 provenance 矛盾，本版更正回初版诊断并补全时间线。
- Workaround used: 主 session 逐轮修订 + 用户决策批量代问压缩往返；owner 手动终止；plan 输入段如实记录"未获 clean 终态"。
- Follow-up（条款已补上并提交，剩执行侧配套）: 「收敛预算与停滞熔断」段（默认 2 轮完整循环预算 + 停滞判据 + AskUserQuestion 处置）已于 2026-07-19 以 ai-agent-config `aeea37a` 提交并刷新 `custom-review-plan` wrapper。剩余配套建议：(a) create-plan「审查」节的 reviewer 初始/resume prompt 显式携带轮次预算状态，要求每轮终止报告先给停滞自检结论；(b) orchestrator 独立计数，预算耗尽即直接走停滞处置，不依赖 reviewer 自觉。

## H12 — review-gate 高档 Codex transport 以 sandbox-bypass 运行，plan 文档 reviewer 越界 mutate 共享系统状态（kill 进程）

- Type: agent-behavior / review-gate transport 权限面 × 并发隔离
- Discovered: 2026-07-20，`plans/20260720-db-slimming/` create-plan 审查阶段（独立 Codex reviewer 走 `$custom-review-plan`）。当时另一 session 正在 `.claude/worktrees/feedback-loop` 并发工作，且宿主上有运行中的生产 serve(:8000)、cloudflared tunnel、pipeline、cron——本任务的 plan §0 已按 concurrent-plan-isolation 明确声明隔离硬约束。
- Symptom: `codeagent-wrapper --backend codex` 启动的 reviewer 命令行为 `codex e --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check`。审查一份纯"文档级"的 DB 瘦身 plan 时，reviewer 不止读文档，还主动 `ps -axo ... | rg -i "sqlite|radar|vacuum|curat|pipeline|feedback"` + `lsof data/radar.db`，随后执行 `kill 38073 38067`（item_21）——对匹配到的两个进程直接发信号。关键服务（serve 26011、pipeline fetch、feedback-loop claude 50921）本次幸存，被杀的是两个短命进程，但 reviewer 已证明它会对共享宿主上匹配模式的进程发 kill。
- Impact: 一个本应"只读文档评审"的 review gate，因 transport 默认 sandbox-bypass + prompt 未携带只读边界，具备并实际行使了 mutate 共享系统状态的能力。在 concurrent-plan-isolation 生效（另一隔离 session 的 live 进程与本 reviewer 共享宿主）时，这可误杀对方的 serve / pipeline / claude session，直接击穿"结构性隔离"的前提。这是权限面问题（能做什么），区别于 H8（结果丢失）与 H10/H11（审查经济学）。
- Workaround used: 主 session 通过 watchdog 读 codex log 发现 `kill`，即刻 `pkill` 停掉该 reviewer + 清理其 /tmp 副本，核验关键服务存活（serve/tunnel/pipeline/feedback-loop session 均在），随后用**硬化的只读 prompt** 重启 reviewer——显式禁止 kill/signal 进程、禁止改 data/radar.db 或 /tmp 外文件、禁止碰运行中服务，只允许 `mode=ro` 查询与 /tmp 副本上的验证。并把 watchdog 从"盯 wrapper .output 体积"（盯错文件、误报 stall）改为"盯 codex log 里的 kill/写生产库命令"。
- Suggested fix:
  1. **create-plan「审查」节 + review-gate 高档 transport**：reviewer 的 initial/resume prompt 默认携带"只读文档评审 + 禁止进程信号/宿主 mutation"边界；当 plan 声明了 concurrent-plan-isolation 时，把"另有并发 session 的 live 进程共享宿主、禁止 kill/lsof-清理"作为 BINDING 前置随 prompt 下发。
  2. **transport 层**：评估 plan-document review 是否必须用 `--dangerously-bypass-approvals-and-sandbox`——文档评审 + 只读 DB 探测不需要写权限；给 review 用途一个更窄的 codex 权限档（只读 FS + 允许 /tmp 写），把"能 kill 宿主进程"从默认能力面移除。
  3. **watchdog 模式**：codeagent-wrapper 的 `.output` 只有 header，真实进度在 `Log:` 指向的 codex log——盯活性/危险命令应盯后者。可在 wrapper 侧暴露 last-event 时间与危险命令流，供主 session 巡检（与 H8 的 last-event 建议合流）。

## H13 — feedback-loop 长后台任务 ~20min "Execution cancelled" 的归因被证伪：不是 codex/websocket，是外部 SIGTERM（≥2 个已知 vector）

- Type: agent-behavior / 归因纠正 × 根因
- Discovered: 2026-07-20，复查 `plans/20260718-feedback-loop/` journal（2026-07-19 21:10 lesson）对长后台 codex 实现任务 ~20min 被 "externally Execution cancelled"（观测两次）的归因。
- 被证伪的归因: journal 记为 "upstream codex websocket 503/flap（idle model socket 掉线）"。四个复现实验 + 退出码签名逐一排除：(1) 裸 `codex exec` 单条 25min 静默命令 exit 0 存活（codex 自带状态心跳、免疫代理侧 idle 回收）；(2) 真 `codeagent-wrapper`+codex 同样 exit 0；(3) 纯 `run_in_background` bash 28.5min 无任何信号存活 → 无 harness 定时 reaper；(4) wrapper `--help` 退出码表：`130`=Interrupted（外部信号）、`124`=inactivity/timeout。故 ~20min "Execution cancelled"(`exit 130`) = **外部 SIGTERM/SIGINT**，不是 codex socket 死（那会走 codex 退出码 passthrough）、不是 wrapper 自身的 inactivity/timeout（那是 124）、不是 harness 定时器。传输侧确有激进 idle 回收（纵云梯代理实测空闲 CONNECT 隧道 ~15s 被掐），但 codex 靠自发心跳不受其害——所以它不是杀因。
- 两个已知 external-kill vector（缺原始 codex/wrapper 日志，无法定论哪个杀了 7/19 的两次；两者同为 exit-130、同在共享宿主）：
  - **(a) 监控层误杀**：把"安静但在耗 CPU/IO 的长本地命令"（2.15GB DB 快照）当挂起后 kill。修复见 F1/F3（下）。
  - **(b) 并发 sandbox-bypass reviewer 的 `kill`**：见 **H12**——review-gate 高档 Codex reviewer 在共享宿主上对匹配 `pipeline|feedback|...` 的进程发 `kill`，是独立且实证的 vector。
- Impact: "websocket" 误归因会把后续 session 引向无效应对（重试 / 换实例 / 自建 nitter 式），而真因在监控判据与并发权限面。这条留档，防止那条错误假设被未来 session 重新采信。
- Fixes APPLIED 2026-07-20（均过各自 review gate）:
  - **F1 监控计算活性守卫 + F3 路由规则** → ai-agent-config `62d4555`（`references/background-agent-monitoring.md`）：判挂起前先跑 `task_computing`（进程子树 R/D/U 态或推进 CPU = 在干活），把 stdout 静默 ≠ 挂起坐实为确定性判据；GB 级长静默命令改由 supervisor 自己 `run_in_background` bash 直跑、不进被监控 codex 任务。修复 vector (a)。
  - **F2 wrapper 可 resume 化（原 H8）** → ccg-workflow `ff52c18`，已装 `~/.claude/bin`：被杀前落盘 `<state-dir>/results/*.result.json`（session_id/exit/reason）、失败/被杀保留日志、cancel 路径带 session_id。让任一 vector 的误杀从"结果全丢"变"可干净 resume"。
  - **point1 review 过深熔断（H11 follow-up b）** → ai-agent-config `946db50`（`commands/custom/create-plan.md`）：orchestrator 无条件计数、达预算强制"定稿 vs 继续" AskUserQuestion，不依赖 reviewer 自报收敛。
- 关联: **H8**（F2 修）、**H10/H11**（point1 = H11-b 执行侧配套）、**H12**（vector b 及其权限面修复）。

## H14 — Two concurrent execute-plan supervisors' codex tasks SIGINT-kill each other (exit 130)

- Context: `plans/20260721-alerting-quality-fixes` ran its execute-plan supervisor in worktree `.claude/worktrees/alerting` WHILE `plans/20260720-db-slimming` ran its own execute-plan supervisor in `.claude/worktrees/db-slimming`. Both spawn `codeagent-wrapper --backend codex` tasks; the host also had high pageouts.
- Symptom: long-running codex tasks (especially deep review passes) were repeatedly killed with `exit 130` (SIGINT) mid-work — observed ~6 times this run (a Phase 0 fix, Phase 3/4/6 reviews, a Phase 6 closure). result.json `reason: "execution cancelled"`. This matches the documented "ai-radar 误杀" shape in `background-agent-monitoring.md` (external SIGINT, not a codex/websocket death).
- Impact: no data loss — codex sessions are resumable and implementer work usually LANDED before the kill (only the final report text was lost); the supervisor recovers by reading result.json + `git status` and resuming the preserved session. But it adds wall-clock (each killed review needs a resume round) and requires the supervisor to distinguish "killed-after-completion" (work landed) from "killed-mid-investigation" (must resume to finish). Reviews are the most vulnerable (longest wall-clock).
- Not root-caused: no persistent watchdog/reaper found; suspected resource/concurrency contention between the two supervisors (or a global cap on concurrent codex sessions where one supervisor's spawn preempts the other's). The alerting supervisor could not pause the db-slimming supervisor (separate session).
- Workaround used: treat `killed` as transport failure first — check result.json + `git status`; if work landed, independently re-verify (supervisor runs the suites itself) and continue; if mid-investigation, resume the preserved session with a "complete your verdict" prompt. Supervisor ran long local verification itself rather than inside a codex task (avoids the zero-stdout misjudgment surface).
- Suggested fix: investigate whether concurrent `codeagent-wrapper` codex sessions across worktrees contend on a shared resource/lock or a session cap that manifests as SIGINT; if a cap exists, queue rather than SIGINT-preempt, or surface the preemption cause in result.json `reason` (currently just "execution cancelled"). Consider a supervisor-side advisory lock / registration so two execute-plan supervisors serialize heavy codex spawns instead of colliding.

## H15 — Alerting design/review/gate flow never grounds verification in real production data → a starved design precondition ships undetected

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

## 2026-08-02 claude-mem 12.7.5 PreToolUse:Read hook 全程拦死 Read 工具

- 现象：本 session 内每次 Read 调用被 `PreToolUse:Read` hook 以 "No stderr output" 错误拦截；PostToolUse observation hook 同样每次报错（不拦截）。Read 失效连带 Edit/Write（对已存在文件）不可用。
- 排查：手动以相同参数跑 hook 命令 exit 0；杀掉挂在旧路径（ai-agent-config checkout）的 worker daemon 并从当前路径重启无效；摘除两处 hooks.json 的 PreToolUse 段后仍被拦——hook 配置在 session 启动时缓存，运行中不重读。
- 已做的可逆干预：`~/.claude/plugins/cache/thedotmack/claude-mem/12.7.5/hooks/hooks.json` 与 `~/.claude/plugins/marketplaces/thedotmack/plugin/hooks/hooks.json` 均已备份为 `*.bak-20260802` 并移除 PreToolUse 段（下个 session 生效，代价是失去 claude-mem 的 file-context 注入——它本来就是坏的那块）。
- **[更正 2026-08-02 — 上面的诊断有两处是错的，根因另有其人]** 当天的止血（摘除 PreToolUse 段）当场看似无效，我据此推断"hook 配置在 session 启动时缓存"——**错**：hooks.json 是每次调用读取的，恢复备份后 Read 立即恢复正常并正确注入 file-context，证明摘除当时确实生效过，只是故障是**间歇性**的，我恰好在坏窗口里复测。"worker-service 对 file-context 请求 crash"同样错：用真实形状 payload 手工调用 exit 0、输出正常。

  **真实根因链（证据链完整）**：`~/.claude/daemon-auth-status.json` 记录 `{"status":"auth_required","since":1785112816699}` = **2026-07-27 08:40:16**；数据库里最后一条 observation 是 **2026-07-27T08:44:37**（队列余量跑完即停），此后 6 天零入库。observation 的生成需要 worker spawn Claude SDK 子进程做 LLM 总结，认证失效后这些子进程持续 `AbortError` / `code=143`，worker 日志出现 `CRITICAL: Restart guard tripped — session is dead`。worker 进程（PID 17879，7/27 02:57 启动）就此进入"进程存活但 HTTP server 已死"的状态——SIGTERM 时报 `Error during shutdown Server is not running` 是直接证据。此后每次 hook 调用都走同一条活锁：健康检查失败 →「Worker not running — lazy-spawning」→ 检测到 PID 存活 →「refusing to start duplicate」→ 等端口 → 「port did not open after 3 attempts」→ hook 非零退出。`bun-runner.js` 末行 `process.exit(code || 0)` 原样透传该退出码，而 Claude Code 对 PreToolUse 失败采取 fail-closed，于是内部故障被放大成工具拦截。

  **两个上游设计缺陷**：(a) 活锁无自愈路径——PID 存活检查恰好阻止了重启僵死实例，而僵死判据用的是端口健康、拉起判据用的是 PID 存活，两个判据不一致；(b) 这个 PreToolUse hook 只做"注入补充上下文"的增强，失败本该 fail-open，却按 blocking 处理。

  **影响的主次被我当时判反了**：Read 被拦是可见但次要的（blocking error 不是静默失败，无正确性风险，代价是 20 文件改造降级为 patch 脚本、易错且丢失 harness 文件状态跟踪）；真正的主要影响是**记忆捕获自 7/27 起静默中断 6 天**——PostToolUse observation hook 同样失败，但它不拦截工具，所以完全无感。这也回答了"为什么之前没发现"：故障 7/27 就开始了，只是先坏在看不见的那一半，直到 8/2 可见的那一半（PreToolUse:Read）也失败才暴露。

  **[再更正 2026-08-02 — 上面把 `auth_required` 当根因，仍是同一个错误]** 我用"时间戳吻合"（auth 状态 07-27 08:40 vs 末条 observation 08:44）就认定因果，未验证该文件的归属。实测：当前版本 `worker-service.cjs` 中 `auth_required` 字符串**出现 0 次**，全仓也搜不到写它的代码，文件自 7/27 再未更新——是陈旧遗留物，不是活故障信号。同批被证伪的还有 `daemon.status.json`/`daemon.lock`（属 Claude Code 自身 daemon v2.1.207，与 claude-mem 无关）。**这是本条第三次同型误判：拿单一相关性当因果并据此行动。**

  **当前已确证的事实（可稳定复现）**：`observation` hook 以真实 payload 手工调用**确定性返回 exit=2、stdout 与 stderr 均为空**——exit 2 正是 Claude Code hook 协议的 blocking 码，所以 worker 是主动返回阻塞而不给原因（协议要求 exit 2 时 stderr 应说明理由，此处违反）。同一时刻 `file-context` hook exit=0 正常，Read 可用。worker 进程（25386）与 chroma-mcp（25569）均存活，health 端点 200 / 0.6ms，但每次 hook 调用 worker 日志仍报 `Worker not running` → `Port already in use, refusing to start duplicate` → `port did not open after 3 attempts`——**外部可连、hook 客户端判不可连**这个矛盾是未解开的核心。`worker-cli.js restart` 无效（进程 pid 不变，该命令被当 hook 调用处理）。

  **未锁定**：exit=2 的确切触发点。目标是 2.9 MB 的 bundled 第三方产物，继续静态挖掘收益递减；正确处置是带上述复现步骤报上游，并由用户决定这个插件的去留。

  **两个上游设计缺陷（已确证，与根因无关也成立）**：(a) 判死用端口健康、拉起用 PID 存活，两个判据不一致导致僵死实例永远拉不起来；(b) 一个只做上下文注入的增强 hook 失败时按 fail-closed 阻塞工具。

  **[根因已锁定 2026-08-02 晚 — 由独立 reviewer 用新证据推翻了"投递链断裂"这个中间结论]** 关键证据是 `user_prompts` 表：它与 `observations` 经**同一条投递链、同一个写入函数**入库，而 `MAX(created_at)` 分别是 `2026-08-01T17:19` 与 `2026-07-27T08:44`。同链一通一不通，说明 07-27→08-01 那 5 天**投递链是好的**，死的是 observation 的**生成侧**（该路要 spawn Claude SDK 子进程做 LLM 总结，worker 日志里对应 `AbortError` 与 `CRITICAL: Restart guard tripped — session is dead`）。

  **完整时间线**：① 07-27 08:44 生成侧死亡，投递链正常 → `consecutiveFailures` 因"收到任何 HTTP 响应即归零"而全程读作 0，**5 天完全静默**；② 08-01 17:19 后投递链本身也断（bun-runner 空 stdin，上游 issue #2188），计数器开始累积；③ 累积超默认阈值 3 触发 fail-loud：`process.exit(BLOCKING_ERROR)` 即 exit 2，Claude Code 按 blocking 处理 → 工具被拦；④ fail-loud 本该打印的 `claude-mem worker unreachable for N consecutive hooks.` 被 hook 包装器的 `process.stderr.write` 劫持吞掉（代码里有 `finally{process.stderr.write=i}`），只剩无解释的 exit 2。这解释了为何数据 07-27 就停、而可见症状 08-02 才出现。

  **教训（本条第三次同型误判后的总结）**：三次误判（坏 hook / auth 文件 / 投递链断裂）都是拿单一相关性当因果。真正终结它的不是更仔细地看同一批证据，而是找到一个**能区分竞争假说的对照量**——`user_prompts` 与 `observations` 共享写入路径，所以它们的时间差直接把"链断"与"生成侧死"分开。

  **处置**：`claude/settings.json` 加 `CLAUDE_MEM_HOOK_FAIL_LOUD_THRESHOLD=999999` 恢复 fail-open（实测 exit 0）；新建 `claude/references/enhancement-service-liveness.md` 并接入 `/custom:review-agent-harness` 第 2b 路，判据即上述对照量。根因在上游第三方 bundle，待报。

  **[根因确认为代理 2026-08-02 晚 — 用户在另一 session 独立定位并已修复]** 真因是 **Claude Code 在代理模式下把 `http_proxy`/`https_proxy` 传给了 hook 进程，于是它连 `127.0.0.1:37701` 的本机 worker 也走代理并失败**。修复见 `~/research/system-config` 的 `69cd92e`：`claude` 系 wrapper 在 dotenv 覆盖生效后合并 `no_proxy`/`NO_PROXY` 并加入 `127.0.0.1,localhost,::1`。本 session 对照实测确认（显式清掉阈值变量，排除绕过干扰）：同一 payload、同一 worker，不带 `no_proxy` → `exit=2`；带 `no_proxy` → `exit=0` 且返回 `{}`，失败计数器随即从 91 归零。

  **我的调查方法本身屏蔽了真因**：全程用 `curl --noproxy '*'` 探 worker 健康端点，于是看到"外部能连、hook 连不上"，把它归因成"请求送达但响应回不来"。而两者唯一的差别就是 `--noproxy`——那个我为绕开系统代理而顺手加的参数，恰好消除了要诊断的变量。这是本条第四次同型误判，前三次是拿相关性当因果，这次是**用一个屏蔽了自变量的方法去测因果**。

  **已 revert**：`CLAUDE_MEM_HOOK_FAIL_LOUD_THRESHOLD=999999`（ai-agent-config `1db67bc`）——它是基于错误诊断的绕过，根因既已修复就不该保留，否则永久压掉一个有效信号。探活 reference 保留，其"与消音配置的关系"节改写为「已知环境陷阱：代理劫持回环流量」，记入两条可复用教训：探活命令必须自己绕过代理否则会得出与被测组件相反的结论；常驻进程继承的是被拉起那一刻的环境，判定恢复前先确认其启动时间晚于环境修复。

  **已闭合 2026-08-02**：上一版记的"另一层未闭合"（`SDK_SPAWN … AbortError` / `code=143`）不是独立故障——它的成因是**被测进程仍在旧环境里**：worker 与其派生的 SDK 子进程继承的是被拉起那一刻的环境，我在旧 session 内重启 worker，它继承的仍是无 `no_proxy` 的坏环境。用户在新 tab 重新加载 zshrc 后重启 session，链路一次贯通：`no_proxy=127.0.0.1,localhost,::1` 生效、`consecutiveFailures` 归零、`chroma-mcp` 也从长期 backoff 恢复连接（`Connected to chroma-mcp successfully`），observation 走完 `ENQUEUED → CLAIMED → CLEARED → CHROMA_SYNC` 全流程，`observations` 新增 19936/19937，`hours_since` 由 148 降到 0。6 天断档结束。

  这也让「常驻进程继承的是被拉起那一刻的环境」这条从推测升级为实测结论，已写入探活 reference——判定服务是否恢复前，先确认被测进程的启动时间晚于环境修复，否则会把"环境没换"误读成"修复无效"（我上一版正是如此误读）。

  **附带发现**：用真实恢复态跑探活，衰减档会触发（`hours_since`=0.001 但 `last_24h`=3 < 均值 61.4/3）——窗口里还含着已结束的断档。这不是误报但不 actionable，已在 reference 补「恢复期形态」：三个取值一起看即可与持续衰减分辨（ai-agent-config `a6f07e9`）。

  **状态**：hooks.json 备份已回滚、PreToolUse 段恢复原状（Read 实测可用）。`~/.claude-mem/claude-mem.db` 4.78 GB，含 6 天未落库的积压。

## 2026-08-02 补充观察（AIHOT 改版 session）

### agent-browser 截图管线复发性卡死（os error 35），repo 自带 Playwright 是更稳的兜底
- 现象：`screenshot` 在 open/scroll 等活动后频繁 "Resource temporarily unavailable (os error 35) (after 5 retries)"，杀 daemon 重开、换 session、`network route --abort` 屏蔽悬挂图片请求均无效；同一页面用项目 venv 里的 Playwright（chromium.launch + page.screenshot）一次通过且可顺带做断言。
- 建议：agent-browser SKILL.md 增补一条 fallback 指引——目标 repo 已含 Playwright 依赖时，验证/截图类任务直接用 repo Playwright 脚本，不与 agent-browser daemon 缠斗超过两次重置。
- Fix APPLIED 2026-08-02 (ai-agent-config `e9af26a`): `agent-browser` SKILL.md 的 Stale Daemon 节新增「Fallback: the project's own Playwright for verification」。审查过程纠正了本条建议的两个措辞：(a) 启用判据不是「repo 含 Playwright 依赖」而是「项目本身就在驱动 Playwright（浏览器二进制已装）」——官方契约明确包可 import ≠ 可 launch；(b) 脚本必须走 runner stdin 或 `mktemp`、不落进目标仓库，否则制造需清理的 orphan。同批修好既有文本两处：中段「仍 stall 就报 `Blocked`」改为指向本节终局规则（否则最常见的「无 live pid」路径永远到不了该 fallback），终局补回 `Blocked`/`uncovered` 二元。

### 主 harness 文件工具链退化时，未考虑把批量编辑委派给 Codex（本轮教训）
- 本轮 Read 工具被坏 hook 全程拦死（连带 Edit/对已有文件的 Write），作者改用 Bash+python 补丁脚本硬扛完成了 ~20 文件的改版。可行但易错（补丁脚本自身出过一次结构错误），且每次编辑都失去 harness 的 file-state 跟踪。
- 事后评估：Codex 是独立 harness，不受 Claude Code hook 影响，文件编辑工具链完好。该场景下"把 spec 明确的批量编辑单元委派给 codeagent-wrapper --backend codex"很可能更快更稳。
- 建议：在 delegation-policy（或 durable-solution-carriers 指认的更合适载体）增加一条路由信号：主 harness 编辑工具链因 hook/权限故障退化、且故障本 session 不可修复时，优先评估跨 harness 委派而非 shell 层 workaround。
- Fix APPLIED 2026-08-02 (ai-agent-config `e9af26a`): 落在两层——`delegation-policy.md` 的 Eligibility 新增该规则（条件收窄为「主 harness **自身工具层**故障」，因为括号里的论证只对 harness 层成立，文件系统级故障会让 Codex 同样瘫痪），Codex 专属理由移入「Harness transport」节末段。关键补充：user CLAUDE.md 的「Delegation Boundary」新增三行 when-to-delegate 场景表——审查指出仅改 reference 这条规则**在自己的触发场景下永远读不到**（该文件只在「委派前」被读，而规则要纠正的恰是没打算委派的 agent，且 always-loaded 层已有「Resolve Blockers, Don't Bypass」这条竞争默认）。表格内容经用户裁决保留（其要求 always-loaded 层给出常见适合委派场景清单）。

## 2026-08-04 补充观察（AIHOT 复刻 live 对照 session）

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

## 2026-08-05 codeagent-wrapper 源码侧（mid-termination 文档 review 期间由 reviewer 独立发现）

以下两条脱离本轮文档改动仍成立，目标载体在 `~/research/ccg-workflow/codeagent-wrapper`，不在本仓库，故记录而非就地修。

### `cleanupOldLogs` 在 macOS 上静默不生效——临时日志的回收是个 no-op

- **现象**：`cleanupOldLogs()` 的安全检查 `isUnsafeFile(path, tempDir)` 判定日志文件 `file is outside tempDir` 并跳过，原因是 `os.TempDir()` 返回 `/var/folders/...` 而实际路径解析为 `/private/var/folders/...`（macOS 上 `/var` 是指向 `/private/var` 的 symlink）。两位 reviewer 各自独立观测到同一现象。
- **后果**：孤儿日志从不被回收。表面看是"日志留得更久、更好查"，但它同时意味着**这条清理路径从未被验证过**——一旦 `os.TempDir()` 的返回形态变化或迁到 Linux，行为会突然从"永不删"翻转成"每次启动都删"，而任何依赖日志存活的恢复流程都会在那一刻失效且无告警。
- **旁证**：本机 29 份持久 record 对 7 份存活日志。日志数少于 record 数是别的原因（OS 自身清扫、手动删除），不是 wrapper 清理的结果。
- **注**：`background-agent-monitoring.md` 现在按**源码语义**（下次启动即回收）写，偏保守、方向安全；不依赖本 bug 是否修复。

### `backend.go` 的注释与唯一 `SetDir` 调用点矛盾

- **现象**：`backend.go` 有注释称 gemini 以 `cmd.Dir=$HOME` 运行，但代码中唯一的 `SetDir` 调用点把 `cfg.WorkDir` 交给了除 codex 之外的全部 backend。
- **后果**：读注释推导 workdir 语义会得到错误结论。本轮文档正是要写清三个 backend 的 workdir 承重方式，若采信该注释会写错 gemini 那一支（实际按可执行路径判定，注释是陈旧的）。

### shim 里 `CODEX_SANDBOX` guard 的注释理由与上游实现相反

- **现象**：`ai-agent-config/claude/bin/codeagent-wrapper:32-34` 的注释称用精确大小写比较是因为 `case` 会受继承的 `nocasematch` 影响，而"artifact 只认小写 `read-only`"。实测上游 `codeagent-wrapper/executor.go:821` 是 `strings.EqualFold(strings.TrimSpace(os.Getenv("CODEX_SANDBOX")), "read-only")`，`main.go:606` 的 help 亦写明大小写不敏感且容忍空白。
- **后果**：行为方向保守（把上游本会接受的 `READ-ONLY` 变成硬错误，不构成提权），所以不紧急；但注释陈述的理由是错的，后续按它推导会得出上游更严格的错误印象。
- **未就地修的原因**：判断该改注释还是改比较逻辑，取决于上游是否有意保持大小写不敏感，跨 ai-agent-config 与 ccg-workflow 两仓。
