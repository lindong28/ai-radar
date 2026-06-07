# Journal：微信文章解读页（/wechat）支持搜索

> 用法：每完成一步或踩到坑，追加一条带时间戳的条目。记**决策依据 / 踩坑 / verify 证据（命令+输出摘要）**，不复述 plan。
> 格式参考 `~/.claude/references/long-task-protocol.md` 的 journal 段。

## 2026-06-07 13:39 [fact]
启动执行。已读取 long-task-protocol、plan-execution-principles、docs-organization-protocol、plan.md/state.md/journal.md；确认 state.md 是当前真值，TASK-001..TASK-007 均为 pending。git 当前分支 main，工作树已有未跟踪 `.claude/`、`AGENTS.md`、`data/alert-state.json`、`data/recovery/`、`plans/`、`web/static/evidence/`、`web/static/wechat-interpretation-screenshots.html`，本任务只改 plan 范围文件并保留既有未跟踪状态。

## 2026-06-07 13:47 [lesson]
测试入口需使用 `uv run pytest`（或 `.venv/bin/pytest`）：裸 `pytest` 报 `zsh:1: command not found: pytest`，裸 `python3 -m pytest` 报 `No module named pytest`。项目 `docs/experiences/dev-environment.md` 已记录该约定。

## 2026-06-07 13:48 [fact]
RED gate 通过。命令：`uv run pytest tests/test_wechat_interpretation.py -q -k 'searches_interpretation_card_fields or search_supports_traditional_simplified or search_paginates_clamps'`。输出摘要：3 failed, 16 deselected；失败断言分别是 `q=具身智能` total 期望 1 实际 7、`q=歸藏/归藏` total 期望 1 实际 7、`q=searchterm` total 期望 3 实际 7，证明当前 `/api/v1/wechat` 忽略 q。

## 2026-06-07 13:58 [fact]
TASK-001 GREEN。实现范围：`/api/v1/wechat` 接收 q，`list_wechat_items` 用 title/author/abstract/tags 的 LIKE + 繁简 patterns 过滤，排序使用 author-only CASE，detail_url 透传 q+page。命令：`uv run pytest tests/test_wechat_interpretation.py -q -k 'searches_interpretation_card_fields or search_supports_traditional_simplified or search_paginates_clamps'` → 3 passed, 16 deselected；`uv run pytest tests/test_wechat_interpretation.py -q` → 19 passed。

## 2026-06-07 14:00 [fact]
TDD checkpoint commits：RED `7cb1fe5 test: add wechat search reproducer`，GREEN `015ed18 feat: add wechat interpretation API search`，均在 main 当前 HEAD 可达；未添加 Co-Authored-By。

## 2026-06-07 14:08 [fact]
页面层 RED gate 通过。命令：`uv run pytest tests/test_wechat_interpretation.py -q -k 'thread_search_query or adds_only_search_filter'`。输出摘要：2 failed, 19 deselected；`/wechat?q=具身智能` preload total 期望 1 实际 7，且 HTML 不含 `<form class="feed-filter">`。

## 2026-06-07 14:15 [fact]
TASK-002/TASK-003 页面与模板实现转绿。命令：`uv run pytest tests/test_wechat_interpretation.py -q -k 'thread_search_query or adds_only_search_filter'` → 2 passed, 19 deselected；`uv run pytest tests/test_wechat_interpretation.py -q` → 21 passed；`if rg -n "v=20260604-curated-archive1" web/; then exit 1; else echo "no old app.js cache-busting version remains"; fi` → no old app.js cache-busting version remains。

## 2026-06-07 14:24 [fact]
TASK-004 前端静态 RED/GREEN。RED 命令：`uv run pytest tests/test_frontend_static_contract.py -q -k 'wechat_search_url_state'` → 1 failed, 26 deselected，失败点为 `initWechat` 不含 `const search = document.querySelector("#search");`。实现后命令：`uv run pytest tests/test_frontend_static_contract.py -q -k 'wechat_search_url_state'` → 1 passed, 26 deselected。

## 2026-06-07 14:27 [lesson]
`uv run pytest tests/test_wechat_interpretation.py -q` 偶发在 import `airadar.web.app` 的全局 `app = create_app()` 处迁移默认 `data/radar.db` 时撞 `sqlite3.OperationalError: database is locked`。`lsof data/radar.db` 显示 PID 14226 和 42459 持有默认库。验证路径改用项目支持的 `AI_RADAR_DB=/tmp/...` 临时库，命令 `AI_RADAR_DB=/tmp/ai-radar-test-import-wechat.db uv run pytest tests/test_wechat_interpretation.py -q` → 21 passed；`AI_RADAR_DB=/tmp/ai-radar-test-import-frontend.db uv run pytest tests/test_frontend_static_contract.py -q` → 27 passed；`node --test tests/pagination.test.mjs` → 5 pass。

## 2026-06-07 14:37 [fact]
全量质量门最终通过。先安装缺失的 Playwright Chromium：`uv run playwright install chromium` → downloaded Chromium/FFmpeg/headless shell。用真实 DB 快照 `/tmp/ai-radar-wechat-search-verify.db`（`save_decision=1` count=202）运行：`AI_RADAR_DB=/tmp/ai-radar-wechat-search-verify.db uv run pytest -q` → 279 passed, 5 skipped in 49.26s；`uv run ruff check .` → All checks passed；`uv run mypy src` → Success: no issues found in 68 source files。

## 2026-06-07 14:38 [decision]
全量 pytest 起初暴露 `tests/test_service_contract.py` 两个既有失败：测试仍期望已退役的 `wewe` 服务在服务清单和 usage 文案中存在，但 CHANGELOG/README/deploy scripts 当前契约均为四服务（serve/tunnel/pipeline/alert）。为满足全量质量门且不改生产服务行为，只更新 stale test 断言为当前四服务契约；同时修正 `docs/operations/monitoring-alerting.md` 残留的 `./status.sh serve tunnel pipeline wewe alert`。

## 2026-06-07 14:44 [fact]
V1–V15 scripted/API/HTML verify 通过。本地 serve：`PYTHONPATH=src AI_RADAR_DB=/tmp/ai-radar-wechat-search-verify.db uv run uvicorn airadar.web.app:app --host 127.0.0.1 --port 8765`。脚本输出摘要：V9 total=202 且首屏 50 条 `(slug,published_at)` 匹配 DB baseline；V1 q=`暗涌Waves` total=3 且 author hits before non-author hits；V12 title q=`对话奇点灵智` expected=actual=1，author q=`AI寒武纪` expected=actual=17，abstract q=`一篇与奇点灵` expected=actual=1，tags q=`toC Ag` expected=actual=27；V5/V6 q=`歸藏` and `归藏` total=30 and include `歸藏的AI工具箱`；V7 q=`AI` total=175 page=2 ok page=999 clamp=18；V8 q=`zzz不存在zzz` items=[] total=0 page=1；V10 detail+404 both render `href="/wechat?q=foo&amp;page=2"`；V13 only `name=q`, no category/channel/seg-item, no SSR value; V14 q=`合集` total=0；V15 clean summary_md-only q=`的硬件应用范` summary_count=1 card_field_count=0 API total=0。

## 2026-06-07 14:48 [fact]
V11a/V11b UI verify 通过。agent-browser 初始 `/wechat` → cards=50, preloadTotal=202, searchValue=""。快速输入 `歸藏` 两次 input event 后等待 800ms → `fetchCalls=["/api/v1/wechat?page=1&q=%E6%AD%B8%E8%97%8F&limit=50"]`, cards=30, URL `/wechat?q=歸藏`, errors=[]。清空搜索 → fetch `"/api/v1/wechat?page=1&limit=50"`, cards=50, URL `/wechat`, errors=[]。再次搜索并点击首张卡 → detailUrl contains q, detail backHref=`/wechat?q=%E6%AD%B8%E8%97%8F`; 点击返回 → URL `/wechat?q=歸藏`, searchValue=`歸藏`, cards=30, errors=[]。agent-browser screenshot command hung;替代路径 Playwright screenshot `/tmp/wechat-search-v11b.png` size=233208，DOM boxes show form bottom 139.39 <= header bottom 156.39, header bottom 156.39 <= list y 188.39, search width 650。

## 2026-06-07 14:58 [fact]
Docs sync completed: CHANGELOG 2026-06-07 added `/wechat` search feature; README `/wechat` page and 微信文章解读 section mention searchable title/author/abstract/tags with `?q=` state; docs/operations/wechat-ingestion.md documents the search match face and negative boundaries (`sources.name`/`summary_md` excluded); docs/operations/monitoring-alerting.md four-service status command fixed; docs/CLAUDE.md plans index updated; plan archived to `docs/plans/20260607-wechat-interpretation-search/plan.md`.

## 2026-06-07 15:01 [fact]
Commit checkpoint: `a63900a docs: document wechat search rollout` created without Co-Authored-By. Final state checkpoint now marks TASK-006 done; TASK-007 marked done as supervisor-owned deployment handoff per user instruction (no production restart attempted by implementer).

## 2026-06-07 15:09 [fact]
UX fix start. 用户批准修复 test-ux Low issue：`/wechat` placeholder 当前为 `搜索标题/公众号/摘要…`，但标签也可搜索（issue 证据：`plans/20260607-wechat-search-user-test/issues/r1/main-issues.md`）。计划内最小改动：更新 placeholder 为 `搜索标题/公众号/摘要/标签…`，并用 V13 表单测试继续守住 only `name=q`。

## 2026-06-07 15:10 [fact]
UX placeholder RED gate 通过。命令：`AI_RADAR_DB=/tmp/ai-radar-placeholder-test.db uv run pytest tests/test_wechat_interpretation.py -q -k 'adds_only_search_filter'` → 1 failed, 20 deselected；失败断言为期望 `placeholder="搜索标题/公众号/摘要/标签…"`，实际 HTML 仍是 `placeholder="搜索标题/公众号/摘要…"`。

## 2026-06-07 15:14 [fact]
UX placeholder fix GREEN。模板 placeholder 改为 `搜索标题/公众号/摘要/标签…`；V13 表单结构仍 only `name=q`。命令：`AI_RADAR_DB=/tmp/ai-radar-placeholder-test.db uv run pytest tests/test_wechat_interpretation.py -q -k 'adds_only_search_filter'` → 1 passed, 20 deselected；rendered HTML quick check → `placeholder check PASS: placeholder="搜索标题/公众号/摘要/标签…"`；`AI_RADAR_DB=/tmp/ai-radar-placeholder-test-fullfile.db uv run pytest tests/test_wechat_interpretation.py -q` → 21 passed；`uv run ruff check .` → All checks passed；`uv run mypy src` → Success: no issues found in 68 source files。
