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
- Workaround used: `git restore AGENTS.md` before committing.
- Suggested fix (owner harness config): exclude `AGENTS.md` from claude-mem's injection target, or strip the `<claude-mem-context>` block pre-commit, or keep memory context in an untracked file. Until fixed, verify `AGENTS.md` is clean before any commit/publish.

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
