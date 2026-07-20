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
- Fix APPLIED 2026-07-19 at the durable carrier — `~/research/ai-agent-config/claude/skills/review-gate/SKILL.md` 「分档执行」§ gained an "对抗启动面（施加对抗前必做）" forcing step: before 中/高档 adversarial on a multi-hunk diff, the author partitions hunks into authority-defining (deep adversarial) vs frozen-authority mechanical/read-only payload (excluded) per rigor-tiers' "对抗审查只施于定义或修改 authority 的 unit" rule, records the partition to the gate opening, and feeds the excluded set to the reviewer via 「喂什么」; the reviewer MUST cheap-validate each excluded hunk and return a per-hunk disposition in the 返回契约 (confirm frozen / re-judge as authority → pull into adversarial / unverifiable → 未能核实项), so a silent skip becomes an incomplete return contract caught by 「审不了 ≠ 审过」. The over-rigor direction (deep-reviewing a mostly-mechanical bundle unpartitioned) is an author/main-session gate-opening self-check. Passed its /custom:review-skill gate (3 adversarial rounds: the naive scoping first introduced a symmetric under-coverage hole — author mislabels authority as mechanical to escape adversarial — which the closed-loop disposition mechanism above resolves). Committed in ai-agent-config `88b8633` — but note that commit was produced by an auto-commit daemon that bundled this review-gate fix with unrelated concurrent-session edits (`execute-plan.md`, `supervise.md`, the tracked `env` template) under a generated message, bypassing the mandated create-commit staging discipline. That auto-commit behavior is itself a harness observation (unrelated-change bundling + generated message + fires mid-work without author sign-off); no secret leak here (`env` is a pre-existing placeholder template), but the staging-hygiene bypass is worth a dedicated fix.

## H11 — review-plan 审查循环在事发时缺收敛边界（熔断条款为 7/19 事后新增，已以 aeea37a 提交）

- Type: agent-behavior / review-loop economics
- Discovered: 2026-07-18，`plans/20260718-feedback-loop/` plan 审查（独立 Codex reviewer 走 `$custom-review-plan` 契约）。
- Symptom: 4 轮 full review 产出 12→12→15→10 条 findings（共 49 条全部修订落盘、含 12 项升级为用户拍板决策）；每轮修订后 reviewer 按契约"重新跑 final full review"，新一轮 findings 多为全新区域的更细粒度规格化（从"回滚会污染生产"级结构缺陷逐轮细化到"ballot 槽位未定义评分维度"级要求）且每轮新增 owner 决策；第 4 轮仍无 clean 迹象，owner 在第 5 轮发起后手动终止。
- Impact: plan 阶段消耗 5 轮 reviewer 往返（每轮 5-15 分钟推理 + 主 session 修订 + AskUserQuestion 批次）；终态以 owner 裁决记录代替契约终止判据；无人值守会无限迭代。
- Root cause: **事发时（2026-07-18）committed 的 `review-plan.md` 契约没有任何收敛/轮次预算机制**——"修订产生新 hash → 重新跑 final full review" 的循环入口对宏大 plan 供给无界。
- 诊断时间线（本条目自身被改过两次，最终以 provenance 为准）：初版（7/18）诊断"契约缺收敛机制"——**正确**。7/19 上午第一次改写误把 ai-agent-config working tree 中**当日新增、未提交**的「收敛预算与停滞熔断」段（`git diff` 显示为 + 行，文件 mtime 2026-07-19 11:24）当作事发时已生效条款，错误改判为"机制存在但执行侧未触发"；同日规则审计的 review gate 抓出该 provenance 矛盾，本版更正回初版诊断并补全时间线。
- Workaround used: 主 session 逐轮修订 + 用户决策批量代问压缩往返；owner 手动终止；plan 输入段如实记录"未获 clean 终态"。
- Follow-up（条款已补上并提交，剩执行侧配套）: 「收敛预算与停滞熔断」段（默认 2 轮完整循环预算 + 停滞判据 + AskUserQuestion 处置）已于 2026-07-19 以 ai-agent-config `aeea37a` 提交并刷新 `custom-review-plan` wrapper。剩余配套建议：(a) create-plan「审查」节的 reviewer 初始/resume prompt 显式携带轮次预算状态，要求每轮终止报告先给停滞自检结论；(b) orchestrator 独立计数，预算耗尽即直接走停滞处置，不依赖 reviewer 自觉。
