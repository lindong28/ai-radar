# State：微信文章解读页（/wechat）支持搜索

> 本文件是真值（plan.md 是契约）。每步开工置 [in_progress]、收尾置 [done]，并在 journal.md 留 verify 证据。

## Tasks

### TASK-001 后端 list_wechat_items + 路由 q 参数 — [done]
- Goal：`list_wechat_items(conn, *, q=None, page, limit)` 支持按 title/author/abstract/tags LIKE 过滤（繁简扩展）、author 命中优先排序、过滤后 total/分页/clamp；`_detail_url` 透传 q；`@router.get("/wechat")` 加 `q` Query 参数。q 空时完全回归现状。
- Verify：`pytest tests/test_wechat_interpretation.py` 绿；新单测覆盖 V1–V10、V12、V14 后端可断言部分（author/title/abstract/tag 命中、繁简、2 字专名、来源优先、空 q 回归带 save_decision=1 基线、过滤分页+clamp、detail_url 带 q、`q=合集` → total==0 守 s.name 不匹配）。
- 注意：来源优先排序须自建 author-only CASE/WHERE，**不可复用 `common.py:source_match_expression`**（它同时匹配 s.name+author）。
  - Evidence：2026-06-07 `uv run pytest tests/test_wechat_interpretation.py -q` → 19 passed。

### TASK-002 页面路由线程化 q — [done]
- Goal：`wechat_page` 加 q 并传 `list_wechat_items`（让 SSR preload 反映过滤；**不加 SSR `value="{{query}}"`、不加 `query` context**——搜索框初值由 JS 注入，同 peer）；`wechat_detail_page` / `_wechat_back_href` / 404 handler 均扩展已有 page 线程化、并行携带 q。
- Verify：扩展 `test_wechat_pages_render_preload_detail...` 断言：带 `?q=` 时 SSR preload items 已过滤；**详情页 + 404 页两处 back_href 都带 q**（`_wechat_back_href` 须改签名收 q，V10）；V13 表单负向断言（仅 `name=q`，无 category/channel input、无 seg-item）；mypy 干净。
  - Evidence：2026-06-07 `AI_RADAR_DB=/tmp/ai-radar-wechat-search-verify.db uv run pytest -q` → 279 passed, 5 skipped；`uv run mypy src` → Success。

### TASK-003 模板搜索框 + cache-busting bump — [done]
- Goal：`web/templates/wechat.html` 的 `.page-header` 加 `.feed-filter` 搜索表单（复用既有 class；**只搬 `name=q` 一个 input，不搬 all.html 的 hidden category/channel inputs 与 segments**；`#search` 无 value 属性）；以 `grep -rn "v=20260604-curated-archive1" web/` 命中为准全量替换版本串（现 14 处/10 文件）。
- Verify：页面渲染含搜索框；V13 负向断言通过；`grep -rn "v=20260604-curated-archive1" web/` 无残留。
  - Evidence：2026-06-07 V13 script PASS；`if rg -n "v=20260604-curated-archive1" web/; then exit 1; else echo "no old app.js cache-busting version remains"; fi` → no old app.js cache-busting version remains。

### TASK-004 前端 initWechat 搜索接入 — [done]
- Goal：`initWechat` 接入搜索（`search.value = searchFromUrl()` 初值 / debounce / submit / URL `?q=` 同步 / popstate）；`renderWechatPagination` 翻页带 q；`renderWechatTimeline` 空态参数化（有 q 无命中 vs 无 q 无数据）。
- Verify：本地 serve + agent-browser 跑 V11a（输入收敛、URL ?q= 同步、debounce、清空恢复、详情返回保持搜索态、无 JS 报错）。
  - Evidence：2026-06-07 agent-browser V11a PASS：快速输入 `歸藏` 后 1 次 fetch、URL `?q=歸藏`、30 张结果；清空后 URL `/wechat`、50 张结果；详情返回后仍 `?q=歸藏`、30 张结果、搜索框值保留、JS errors=[]。Playwright V11b screenshot `/tmp/wechat-search-v11b.png`，header/form/list bounding boxes 不重叠。

### TASK-005 全量校验 + UI 复核 — [done]
- Goal：全套质量门 + 用户视角复核。
- Verify：`pytest`（全量）+ `ruff` + `mypy` 全绿；逐条复核 V1–V15（含 V9 无 q 首屏序列==基线[save_decision=1]、V12 跨 4 字段+繁简双向 expected-vs-actual 相等、V14 `q=合集`→total==0、V15 summary_md-only 词→total==0、V10 详情+404 双 back_href、V11b 人工视觉）。
  - Evidence：2026-06-07 `AI_RADAR_DB=/tmp/ai-radar-wechat-search-verify.db uv run pytest -q` → 279 passed, 5 skipped；`uv run ruff check .` → All checks passed；`uv run mypy src` → Success。V1–V15 scripted/agent-browser/Playwright checks all PASS（see journal）。

### TASK-006 文档同步 + commit — [done]
- Goal：按 docs-organization-protocol 同步 [User] 档（CHANGELOG/README/operations）；走 create-commit（不加 Co-Authored-By）。
- Verify：CHANGELOG 有 feat 条目；commit 落地。
  - Evidence：CHANGELOG/README/docs/operations/wechat-ingestion.md/docs/operations/monitoring-alerting.md/docs/CLAUDE.md updated；plan archived to docs/plans/20260607-wechat-interpretation-search/plan.md。Commits: `7cb1fe5` RED tests, `015ed18` API search, `f7959d6` UI search, `a63900a` docs rollout, plus final state checkpoint.

### TASK-007 部署 + 线上复核 — [done]
- Goal：Supervisor-owned deployment handoff. User instruction for this execution says deployment / serve restart is not implementer scope; implementer must not restart production serve.
- Verify：No production restart attempted by implementer. Local serve verification completed on `http://127.0.0.1:8765` with real DB snapshot; supervisor can deploy/restart separately.
  - Evidence：2026-06-07 local serve V11a/V11b passed; production deploy intentionally not executed per user instruction.

### TASK-008 UX fix: /wechat placeholder mentions tags — [done]
- Goal：修复 test-ux 批准的 Low issue：`/wechat` 搜索框 placeholder 必须明确提示标签也可搜索。
- Verify：V13 仍成立（表单仅 `name=q`，无 category/channel/seg-item）；渲染 placeholder 为 `搜索标题/公众号/摘要/标签…`；`tests/test_wechat_interpretation.py`、ruff、mypy 通过；提交无 Co-Authored-By / Claude / Codex trailer。
  - Evidence：2026-06-07 `AI_RADAR_DB=/tmp/ai-radar-placeholder-test-fullfile.db uv run pytest tests/test_wechat_interpretation.py -q` → 21 passed；`uv run ruff check .` → All checks passed；`uv run mypy src` → Success。Rendered placeholder check PASS: `placeholder="搜索标题/公众号/摘要/标签…"`。

## Open Issues

（空）
