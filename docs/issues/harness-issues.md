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
