# General Issues

> Mutable. 项目级未分类问题——按 lifecycle 维护。

---

**迁出记录 2026-09-05**：用户点名的「interpret 的 selector 收据与 domain-routing 策略之间存在写入竞态」在本分支基线中尚无条目；本轮已补录完整事实并直接按终态生命周期写入 [`archive/closed.md`](archive/closed.md)，未把已闭合事项留在 open 清单。

## [open] AI Assistant contract 对 archive import receipt 使用了无限定的 `receipt`

- Type: documentation / terminology · Priority: low · Discovered: 2026-09-05 selector receipt contract review（基线独立、非本任务边界）
- Description: `docs/references/ai-assistant-contract.md` 前段用 `receipt` 指 selector compatibility receipt，archive import consumer contract 后段又以无 qualifier 的 `receipt` 指 import receipt。两个对象同处一份跨仓接口契约，读者可能把 `postcheck` 错接到 selector receipt。
- Fix direction: 下一次修改 archive import contract 时，将该处限定为 `archive import receipt` 或权威 schema 类型名；本轮不修改无关基线文本。

## [open] ISSUE-GENERAL-20260904-d8c1 · Source contract duplicates main-timeline membership already determined by kind

- Type: schema debt · Priority: low · Discovered: 2026-09-04 T1 terminal schema review §3（baseline-independent / non-boundary finding）
- Source: `tests/fixtures/aihot_sources.json` stores `ai_radar_main_timeline_member` on every source row, while `src/airadar/sources/contract.py` requires it to be `false` for `kind="wechat"` and `true` for every other accepted kind; `tests/test_source_contract.py::test_contract_has_no_convenience_copies` asserts the same equivalence. Consumers in the renderer, membership transition checker, audit receipt path and public source counts nevertheless read the copied boolean.
- Risk: the field has no independent state under the current contract, so every author and consumer must maintain two representations of one fact. The validator currently rejects drift, which limits present runtime risk, but the duplicated authority adds schema surface and makes a future kind or membership evolution easier to implement inconsistently across writers, validators and consumers.
- Deferred direction: do not expand T1 or change the published source contract for the paused-source cutover. At the next intentional source-contract schema evolution, inventory all consumers, remove the convenience copy from the new schema, derive main-timeline membership from the authoritative `kind` rule at the shared contract boundary, and migrate renderer/checker/audit/API tests and receipts together. Preserve the current 161-member universe and treat any future exception where membership is not determined by kind as a separate explicit schema decision.
- Closure: a versioned contract evolution removes the independent field, every former consumer derives membership through one shared rule, negative tests reject unsupported kinds or ambiguous exceptions, and generated config/receipts/public counts retain their exact expected member sets.

## [open] ISSUE-GENERAL-20260901-b6ad · WeChat KB import receipt does not identify its target namespace

- Type: operability / observability · Priority: medium · Discovered: 2026-09-01 documentation sync final review
- Description: `./run.sh admin wechat-kb import` accepts `--assistant-root`, `--user`, and `--db-path`, but its receipt only prints the run id, counts, postcheck, changed flag, skipped reasons, and next action. A dry-run or real import can therefore report a plausible success after resolving to another existing ai-assistant checkout, Summary Agent user, or database, while the operator cannot verify the target identity from the receipt itself. The runbook currently mitigates this by requiring all three target arguments on first use and whenever the target changes, and explicitly warns that the CLI does not echo them.
- Fix direction: include normalized, non-secret target identity in every dry-run and real-import receipt: resolved assistant root, Summary Agent user, and resolved database path. Keep the fields present on success, no-op, validation failure, and postcheck failure so the receipt remains self-identifying across every terminal outcome; add CLI tests for explicit and default paths.
- Closure: tests run the command against two distinct temporary roots/users/databases and every receipt unambiguously identifies the selected target. The matrix covers explicit arguments and the default database path, plus success, no-op rerun, catalog/argument validation failure, and failed postcheck outcomes.

## [open] ISSUE-GENERAL-20260901-7c2e · Playwright Sync egress test depends on full-suite asyncio state

- Type: test reliability · Priority: low · Discovered: 2026-09-01 WeChat search/import full-suite verification
- Description: `tests/test_egress_routing.py::test_playwright_external_and_loopback_reach_the_selected_listener` fails in the full suite because `sync_playwright()` observes an already-running asyncio loop (`Playwright Sync API inside the asyncio loop`). The same node id passes in isolation. The product route and selector behavior therefore have not failed; the test result depends on suite execution context.
- Evidence: full suite result was `2404 passed, 4 skipped, 2 failed`; this node was one failure. Immediate isolated rerun was `1 passed in 3.13s`. The WeChat task did not modify `tests/test_egress_routing.py` or the egress implementation.
- Fix direction: give this sync Playwright probe an execution boundary whose event-loop state is owned by the test, or convert the probe to the async API consistently. Preserve the existing positive assertions for external-via-selector and loopback-direct routing; do not silence the failure by skipping whenever a loop exists.

## [open] 2026-08-26：全量单测有两条与 domain routing 无关的既有基线失败

- Type: test baseline · Priority: low · Discovered: 2026-08-26 domain-routing T2 full-suite 验证
- Description: 在 T2 隔离 worktree 与未带 T2 改动的 main checkout 上分别复现两条同形失败：`test_capture_writer_refuses_non_repo_root_and_existing_capture` 预期 `output_root_invalid`，实际先命中 `git_checkout_invalid`；`test_actual_candidate_app_search_endpoint_matches_manifest` 的本地 health request 被 ambient proxy 接管后返回 500/timeout。两条都不经过本次 selector-owned client，因此不能用 T2 的通过/失败归因其行为。
- Current gate: domain-routing 全量 non-live 回归显式排除这两个 nodeid，并单独报告排除集合；其余测试必须全绿。后续分别修正 capture writer 的检查优先级契约，以及给该 DB-sync 本地 health probe 显式 no-proxy transport。

## [open] 2026-08-26：X offline receipt 同时保存可派生 status 与 payload 自哈希

- Type: schema debt · Priority: low · Discovered: 2026-08-26 domain-routing T2 schema review（独立 finding）
- Description: `artifacts/x-pagination-offline-receipt.json` 同时保存 `status=success` 与 `pytest_exit_code=0`，当前 validator 下前者可由后者唯一推出；`report_payload_sha256` 又是同一 JSON 去掉自身后的自哈希，修改者可同时改 payload 与哈希，不能充当外部完整性锚。该 artifact 仅因 T2 更新 selector compatibility 字段而进入 diff，问题早于本次路由改造且不影响 T2 route contract。
- Fix direction: 下一次演化该 receipt 时删除可派生的 `status` 与同文件自哈希；需要完整性锚时使用外层 `offline_proof.receipt_sha256` 或 transport/release 层 digest，并同步 validator/tests。不要为本次 domain-routing 改造单独切 schema。

## [open] `curation_runs.input_eval_ids` 让主库超线性增长

- Type: reliability / capacity
- Priority: low
- Discovered: 2026-08-21 服务器磁盘占用分析（tencent-webserver-china）
- Description: `curation_runs` 每行把当轮涉及的全部 eval id 列表整体快照进 `input_eval_ids`。实测该列**平均 81,886 字节/行**，而 `output_curated_ids` 只有 761 字节。8,284 行占 **692MB**，是 2.6G 主库里最大的单表——超过 `items` 本身（407MB）和 FTS 索引（669MB）。
- 为什么是超线性: 近 7 天跑了 519 轮（~74/天），按当前均值约 6MB/天；而 `input_eval_ids` 的长度随 eval 总量（当前 121,811 行）一起涨，所以行数与行宽同时增长。
- 取证: `pragma freelist_count` 仅 438 页（1.8MB），**VACUUM 回收不到东西**——这 692MB 是真实存活数据，不是碎片。`dbstat` 分表读数：`curation_runs` 0.692G / `items_fts_data` 0.669G / `item_evaluations` 0.506G / `items` 0.407G / `curated_items` 0.272G。
- 为什么优先级低: 6MB/天在 69G 盘上不构成近期压力，且蓝绿双槽让它以 2× 计入磁盘。真正的成本是它会顶着 [operations/db-slimming.md](../operations/db-slimming.md) 的瘦身收益一起长。
- Fix 方向: `input_eval_ids` 存 id 列表是为了可复盘"这轮看了哪些 eval"。可改为存范围/游标（起止 eval id + 过滤条件）而非全量枚举，或对超过 N 天的 run 只保留 `output_curated_ids`。需确认没有消费端依赖逐 id 回放。

---

## [open] 停用一个微信源会藏起它独有的文章，重新启用又会让同一篇出现两次

- Type: reliability
- Priority: medium
- Discovered: 2026-08-20 双跑改动的 review-gate（独立 Codex reviewer，session 01a01dd3）；用户裁决保持现状、记 issue。
- Description: 跨源去重（`src/airadar/fetcher/dedup.py` 的 `wechat_duplicate_id`）只匹配 `s.enabled=1` 的来源，`/wechat` 也按同一个 flag 过滤。实测四步：候选源先发现一篇 → `items=1` 可见 1；停用该源 → 可见 0（文章从站上消失）；另一源随后带来同一篇 → `items=2` 可见 1；重新启用候选源 → **可见 2，同一篇两张卡**。
- 为什么不改成匹配全部行: 那样隐藏行会持续拦住每一次插入，停用源独有的文章**永久补不回来**（这是 reviewer 报的原 HIGH-1）。当前取舍选了"补得回来"这一面，重复只出现在"停用后又重新启用"这一条路径上，而当前方向是停掉后不再启用。
- Fix 方向: 命中的行属于已停用来源时，不插新行而是把该行改归属到新来源（`source_id` / `url` / 正文一并更新），始终只保留一行；需处理 `items` 上的唯一索引冲突，并另走一轮评审。
- 当前缓解: 不重新启用已停用的微信源。旧 runbook 的一次性清重脚本没有完整复用运行时的可见源谓词、同源排除与标题规范化，已在 2026-09-01 移除。
- 闭合条件: 提供受测的只读重复候选枚举入口，逐对显示身份依据与来源；删除必须是另一个显式、可审计的操作，并验证 `items`、`wechat_interpretations`、FTS 与 `/wechat` 消费面一致。完成前不要用 ad hoc SQL 修改生产库。

---

## [open] 未配置的 optional source 在校验之前就被跳过

- Type: reliability
- Priority: low
- Discovered: 2026-08-20 双跑改动的 review-gate（独立 Codex reviewer，session 01a01dd3）；用户裁决保持现状、记 issue。
- Description: `src/airadar/sources/loader.py` 的 `load_sources` 对"`fetch_url` 是未设置的单一 env 占位符"的 optional source 直接 `continue`，发生在 `_validate_source` 之前。因此该行若同时还有别的错误（重复 slug、错 kind、缺 `public_url_override`），未配置时看起来一切正常，等到某天设上环境变量，整个 163 源的 load 一起失败。
- 为什么优先级低: `data/sources.toml` 由 `tests/fixtures/aihot_sources.json` 生成，而 contract 校验覆盖这些字段。实测：往 fixture 里注入一个坏配置（`wechat_only` 改 false），`tests/test_source_contract.py` 立刻 **10 个测试变红**。从"坏配置"到"潜伏至设环境变量那天"的路径在 contract 层已被先拦住。
- Fix 方向: 在决定跳过之前，先校验该行中不依赖 URL 展开的字段。改动落在全部 163 个 source 共用的加载路径上，需权衡新增的回归面。

## [open] feed URL 无 scheme 白名单，`javascript:` 等非 http(s) 协议可进入卡片与热点榜的 `href`

- Type: security-hardening
- Priority: medium
- Discovered: 2026-08-03 AIHOT 视觉复刻 review-gate（独立 Codex reviewer，session 019fc69a）；用户裁决记录 issue、暂不修复。
- Description: `src/airadar/fetcher/rss.py` 经 `urls.py` canonicalizer 处理 `entry.link` 时没有 `http/https` 白名单——reviewer 用本地 feedparser 实验证实 `javascript:alert(1)` 这类无 netloc 的值会原样返回并入库。前端模板/JS 只做 HTML 属性转义（约束不了 URL scheme），因此该值会进入 timeline 卡片、`/hot` 页与首页热点模块的 `href`。触发前提：一个被收录的信源（41 个、由维护者管理）被放入恶意 link，风险低但非零。浏览器对 `javascript:` + `target="_blank"` 的最终执行行为未在真实浏览器验证（review sandbox 限制）。
- Fix 方向: 在 fetcher 的 URL canonicalizer 层加 http/https scheme 白名单（一次覆盖全部消费方），非白名单值置空或丢弃该 link；配套单测覆盖 `javascript:`、`data:`、相对路径与大小写变体。渲染层可另加纵深（非 http(s) 渲染为无链接文本）。

---

## [open] shared Cloudflare tunnel has unstable origin-to-edge throughput for larger dynamic responses

- Type: reliability
- Priority: medium
- Discovered: 2026-06-24 `/wechat` production latency incident
- Description: Public requests through the shared `ai-radar` cloudflared tunnel showed response-size-correlated latency while local origins were fast. Before mitigation, `https://aiplanet.live/wechat` transferred 171 KB in 10-23s while `http://127.0.0.1:8000/wechat` returned in ~0.01s; `sjtu.aiplanet.live` showed the same pattern on a different local origin. cloudflared logs also contained QUIC `no recent network activity` timeouts and stream cancellations. Restarting cloudflared and temporarily switching `protocol: http2` did not resolve the underlying tunnel throughput problem.
- Current mitigation: AI Radar now gzip-compresses large origin responses and keeps `/wechat` initial payload to 20 items, reducing `/wechat` from ~171 KB uncompressed to ~13 KB gzipped and bringing the public page under 3s in verification. This does not fix the shared tunnel for other unoptimized origins or larger pages.
- Follow-up: Investigate Cloudflare Tunnel/network health outside app code: compare from an external client not on the local host, review Cloudflare tunnel diagnostics/status, consider a separate tunnel/connector, and decide whether Cloudflare account/network changes are needed. Preserve the shared `sjtu.aiplanet.live` ingress rules during any tunnel config work.

---

**迁出记录 2026-08-20**：以下三条经判定为纯 user-scope harness 问题（理解、复现与修复只用得上 harness 侧，与本项目产品代码无关），按 `~/.claude/references/docs-organization-protocol.md` §4.8 的写入路由整条迁往 **ai-agent-config** 仓 `docs/issues/harness-issues.md`，原文在本仓 git 历史：`codeagent-wrapper` 下的 codex 无法中断前台长跑进程、只能 kill by PID（→ HARNESS-410）、wrapped agent 为让全量 pytest 绿而扩大 scope 修 pre-existing failure（→ HARNESS-411）、codex 把 runtime context dump 写入 `AGENTS.md`（→ HARNESS-412）。

---

## [open] SSR prepaint 与 hydrated JavaScript 卡片是两套并行 renderer

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: 首屏 Jinja/prepaint 与 hydration 后 JavaScript 分别实现 feed 和 WeChat 卡片。字段、点击目标或展示规则只改一侧时，会产生闪烁或前后不一致。Fix 方向：先建立 feed/WeChat 的 SSR 与 hydrated DOM parity 契约，再决定共享模板输出或保留双 renderer。

---

## [open] `web/static/app.js` 承担全部页面行为，缺少页面级边界

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: 单文件同时承载共享 DOM/API/date 工具、feed 卡片、分页及 `/`、`/all`、`/wechat`、`/daily`、`/about`、`/item` 初始化，页面级修改需要加载无关上下文。Fix 方向：按共享 core、feed/pagination 和页面 initializer 渐进拆成 ESM，并在过渡期保持现有公开 initializer。

---

## [open] `web/static/style.css` 的页面样式与响应式区段相互交叠

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: 全局 shell、feed、daily、WeChat 与多个重叠 breakpoint 共用同一长 cascade，移动端修复容易依赖远距离覆盖顺序。Fix 方向：先统一 breakpoint 结构，再按页面/组件拆分；实施时配套桌面与移动端视觉回归。

---

## [open] `interpret/runner.py` 混合跨仓库 adapter、解析、持久化与编排

- Type: refactor
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构审计
- Description: runner 同时负责 ai-assistant 子进程协议、KB/index 查询、结果解析、slug 冲突、DB 写入、usage 记账与 stage loop，容易在清理时破坏 ADR-007 的串行 writeback 与单一 `save_decision` gate。Fix 方向：抽取 client/adapter、typed normalizer、KB lookup 与 repository；顶层编排继续保持串行。

---

## [open] Playwright 服务启动窗口小于大型数据库 migration 时间

- Type: test-infrastructure
- Priority: medium
- Discovered: 2026-07-11 Web / pipeline 结构重构最终验证
- Description: `tests/playwright/conftest.py` 的固定 30 秒 healthz 等待窗口，小于大型冻结数据库执行幂等 migration 003/FTS 重建所需时间，导致整套 Playwright 在 setup 共因失败。临时放宽到 120 秒可启动，但不应永久以大 timeout 掩盖 fixture 成本。Fix 方向：让 Playwright 使用预迁移的小型隔离 DB，或把一次性 migration 明确移出每次服务启动路径。

---

## [open] continuous-performance fleet 把 `incompatible` 计为 infra failure，public vantage 未配置时无法真正"干净跳过"

- Type: integration
- Priority: low（该集成当前休眠：registry 为空、checkout 无 `config/performance-adapter` 入口、历史 wrapper source-hash 与当前源码不符）
- Discovered: 2026-07-19 规则审计的 review gate（`AI_RADAR_PUBLIC_URL` 中性化改造复核）
- Description: `run_adapter` 现对未配置的 `same_host_public` vantage 以 `ProbeInfrastructureError("vantage_unconfigured")` 干净拒绝（wire-schema safe 的 bare slug reason，exit 78 → status=incompatible），不再用空 base URL 产生伪 hard_failure 样本——这是 repo 侧能做到的最干净语义。但外层 continuous-performance fleet 会把 `incompatible` 计为 infrastructure failure，令 `run-all` 失败并触发外层告警，因此"未配置 = 静默跳过"在 fleet 层面不成立。Fix 方向（激活该集成时执行）：在 fleet/journey 注册配置层面按 `AI_RADAR_PUBLIC_URL` 是否配置决定是否注册 `same_host_public` vantage（未配置就不调度，而不是调度后靠 incompatible 兜底）；或在 fleet 侧为 `vantage_unconfigured` reason 增加"配置性跳过"的非告警处置。在此之前不要在未配置 public URL 的环境注册 public vantage journeys。

---

## [open] PERF probe `value_ms` 含 Chromium 启动耗时 → 慢启动可伪造站点性能页

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；pre-existing（HEAD `browser_probe.py` 即在 `chromium.launch()`/`new_context()`/`new_page()` 之前记 `started`），idle-only follow-up 未引入，按用户裁决单独排期。
- Description: `value_ms = (perf_counter_ns() - started)/1e6` 的 `started` 在浏览器启动前记录，故上报延迟包含 Chromium 启动+context/page 创建耗时。若某轮启动异常慢（如 4s）而站点本身瞬时返回正确内容，样本仍为 `observed, hard_failure=False, value_ms≈4000`；连续 22 轮慢启动会把 p75/p95 顶过 2/3s 预算，产生并非站点退化的 PERF page（reviewer 最小实验已复现）。这是探针基础设施耗时污染站点测量的方向（与 browser-launch-failure 分类正交）。Fix 方向：把测量起点移到 page 就绪之后（site_deadline 已如此计算），使 `value_ms` 只覆盖站点旅程、不含浏览器启动；或从 value 中扣除已知启动区间。改动需配套确认 2/3s 预算校准仍成立。

---

## [open] PERF probe 不检查 `goto()` HTTP 状态，500 + 有效 DOM 判成功

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；pre-existing（HEAD `browser_probe.py:72` 即丢弃 `page.goto()` 返回值），按用户裁决单独排期。
- Description: `page.goto()` 的 response 返回值被完全丢弃，无 status 检查。服务返回 HTTP 500 但错误页/缓存页/fallback shell 仍含预期 selector、文本与 item IDs 时，全部 DOM 断言通过，结果为 `observed, hard_failure=False`，持续 500 永不 page（reviewer 已复现 `status=500` 且 DOM 匹配→observed/non-failure）。空白 500 通常因 selector timeout 兜底 firing，但一般 500 不覆盖。Fix 方向：捕获 `goto()` 返回的 response，对 5xx（及按契约的 4xx）计 `hard_failure=True`；注意与既有 blank-page/selector-timeout 路径去重。

---

## [open] PERF probe 无 timeout 的 `locator.count()/evaluate_all()` renderer hang → 被判 infra 而非站点故障

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；renderer-hang 子路径 pre-existing（无 timeout 的协议调用），idle-only follow-up 的 grace-race 半边已单独修复，此半边按用户裁决单独排期。
- Description: `locator.count()` 与 `locators.evaluate_all()` 在当前 Playwright 实现下经无 timeout 的协议调用执行。页面已渲染预期 DOM 后 JS/renderer 卡死于这些调用时，worker 永不发布 site result，父进程在 `timeout+grace` 后杀 worker 返回 `probe_infra_failure/worker_unavailable`——真实站点/renderer 挂死被改判成 infra、连续发生也不 page。Fix 方向：给这些 locator 操作套用与 goto 一致的剩余 site deadline（`remaining_site_timeout_ms()`），使 renderer hang 触发 `PlaywrightTimeoutError`→observed hard_failure（site fire），而非 worker 沉默→infra。

---

## [open] PERF probe `Pipe()/Process()` 构造失败未包成 infra marker

- Type: reliability
- Priority: low
- Discovered: 2026-07-26 idle-only probe 收尾对抗复审（reviewer 019f92af）；按用户裁决单独排期。
- Description: `process.start()` 已被 try 包裹并转 `probe_infra_failure`，但 `get_context()`、`Pipe()`、`Process()` 在 try 之外。文件描述符耗尽等导致 `Pipe()` 抛 `OSError` 时整个 probe 异常退出、无持久 `probe_infra_failure` marker（stderr 留堆栈、非完全静默，但 worker-start infra 的可见性契约不完整）。Fix 方向：把 context/Pipe/Process 构造纳入同一 infra 兜底，构造失败也产出可见的 `browser_runtime:*` infra 样本。

---

## [open] PERF probe 把 invalid selector/protocol PlaywrightError（探针自身故障）当站点故障 → 误 page

- Type: reliability / measurement-correctness
- Priority: medium
- Discovered: 2026-07-26 idle-only probe 收尾第五轮对抗复审（reviewer 019f92af）；pre-existing（HEAD 即 `except (OSError, PlaywrightError) → hard_failure=True`），按用户裁决单独排期。
- Description: page 就绪后的 site-operation 阶段，若探针自身的 selector/API 契约不兼容（如 `PlaywrightError: Unexpected token "?" while parsing css selector` 或 `Protocol error: Invalid parameters`），该错误既不匹配 `_is_browser_runtime_loss` 的 target/browser-closed marker、也不是 `AttributeError`，于是落到 `outcome="observed", hard_failure=True`（browser_probe.py:246）当成真实站点故障；连续 22 次后 evaluator 产生 firing 并写入可信 `firing_basis="observed"`，误发"站点慢" page，且此后即使 probe 转 infra，round-5 统一迁移规则还会把这个错误 provenance 当可信 observed firing 持有（reviewer 只读复现 classification observed True→count 22→evaluation True observed）。这是探针自身故障被误报为站点退化的方向——与 launch/crash 分类同类，但发生在启动后、且原授权曾把"启动后 error 保留 page"划在 scope 外，故本轮 defer。实务风险低：selector 是代码静态常量、部署测试全绿，仅 Playwright 升级破坏 API 契约才触发（可检测的部署/升级时风险）。Fix 方向：新增一个 invalid-API 分类谓词（匹配 invalid selector/protocol 的 PlaywrightError 签名，与 `AttributeError→browser_runtime:invalid_api` 同归），把探针自身契约错误归为 non-firing `browser_runtime:invalid_api` infra，使其永不 page、也不取得 observed provenance；注意与真实站点驱动的 PlaywrightError（导航/连接失败）区分，后者仍 fire。

## [open] 2026-08-04 Playwright 套件对隔离快照过拟合：换真实数据 5 条失败

**现象**：同一份代码，对隔离快照实例（8011，`data/radar.db` 为 VACUUM 快照）跑 `tests/playwright` 是 **114 passed**；对生产库实例（8010，默认 DB，pipeline 每 15 分钟写入）跑同一套是 **109 passed / 5 failed**。逐条查证，**5 条都不是产品缺陷**：

| 用例 | 失败形态 | 判定 |
|---|---|---|
| `test_parity_home_scroll_reaches_api_total_without_duplicates` | `KeyError: 12`（不是断言失败） | 并发写入使批次键漂移。**测试健壮性问题**——它应当以清晰断言失败，而不是 KeyError。契约本身只在"滚动期间无采集写入"时承诺精确等式 |
| `test_parity_hot_times_and_related_sources_are_data_conditional` | 期望 1 个信源名，得到 `['', '']` | **测试提取逻辑过拟合**。实测生产数据下展开正确渲染 `Hacker News AI/LLM (ddp26)` 与 `(mckennameyer)` 两个真实名（API 返回 3 条同名 related，DOM 已按信源+作者去重）；测试期望 `<li>` 结构而实际名字在子 span 里（实测 `li` 计数为 0） |
| `test_parity_unique_navigation_tags_and_favicons_are_preserved` | 期望 Google favicon 代理 URL，实得真实微信头像 `mmbiz.qpic.cn` | **测试断言的是 fallback 分支**。生产数据已 backfill 真实公众号头像，走的是更好的那条路径 |
| `test_parity_daily_and_changelog_journeys_match_dynamic_content` | `all(...)` 为 False | 依赖当期日报内容形态 |
| `test_v10d_all_page_preserves_aihot_media_score_and_selected_reason` | `assert 27 == 0` | 生产库 `/all` 有 27 条精选条目带标记，快照里是 0。断言把"快照恰好为 0"当成了不变量 |

**为什么值得记**：契约规定的 L2-3 gate 用隔离实例，所以这 5 条不阻塞本轮交付。但它意味着**这套 Playwright 不能对真实数据运行**——若将来接 CI 或用生产快照验收，会得到 5 个假失败。修复方向是让断言表达数据无关的不变量（"每个展开项都有非空信源名"而非"恰好是这一个名字"；"精选标记数等于 API 报告的精选数"而非"等于 0"），而不是继续绑定某一份快照的具体取值。

**并发写入的证据**：本次运行期间 `data/radar.db` mtime 为 18:01、运行时刻 18:08，crontab 有 5 条 pipeline 条目（每 15 分钟一轮）。

## [open] 2026-08-04 补充：`test_parity_feed_column_keeps_reference_net_width` 在全量运行下顺序依赖

同一份代码（commit `5b25f62`）：

| 运行方式 | 结果 |
|---|---|
| 全量 `tests/playwright` | `[720-640-40]` 失败——`.timeline` 实测 **704**，期望 640 |
| 只跑该测试的 4 个参数化用例 | **4 passed** |
| 直接用浏览器量（3 次试验，DCL 与稳定后各一次） | `.timeline` 恒为 x=40 / w=**640**，父 `.app-main` 676、内容 640 —— **与期望一致** |

即产品状态正确，失败只在全量顺序下出现。`704 = 720 − 2×8`，说明失败那一刻横向 gutter 是 8px 而非 18px——与前一个用例遗留的视口/样式状态一致，而非本页真实布局。

该测试用 `page.set_viewport_size()` + `page.goto(..., wait_until="domcontentloaded")` 后立即量盒，**没有等待布局在新视口下稳定**；同文件其它用例已有 `_settle_card_count()` 一类的稳定等待，这条没有。

**为什么值得记**：它与本文件上一条（对隔离快照过拟合）同族但成因不同——那批是断言绑定了某份数据快照，这条是**断言绑定了单跑时的时序**。两者共同的后果是：这套 Playwright 目前**不能作为无人值守的 CI gate**，因为它在"换数据"和"换运行顺序"两个维度都会产生假失败。修法是给该用例补视口切换后的布局稳定等待（对 `.timeline` 的盒做两次采样直到一致，或复用既有的 settle 助手），而不是放宽断言容差——容差放宽会把真回归一起放过。

## [open] 2026-08-17：`test_reclaimer_cannot_steal_atomically_published_live_initializer` 在满负载下 flaky

- Type: test reliability · Priority: low · Discovered: 2026-08-17，前端 cache-busting 改动的独立评审中由 reviewer 观测到

该用例用 `subprocess.run(..., timeout=30)` 起外部脚本，属负载敏感。reviewer 在满负载全量跑中观测到一次 `1 failed`，单独重跑 3/3 绿；本地三次全量（`--ignore=tests/playwright`，1554 passed）与单独重跑均未复现。与前端资源改动无共享代码路径。**影响**：把"全量绿"当发布 gate 时，它在这台机器上不是稳定可复现的。仓库根目录当前遗留 79 个 `.pipeline.lock.reclaim.*` 目录，可能与该用例的环境假设相互作用，值得一并排查。

## [open] 2026-08-17：首屏密度守卫用相交判可见、用均值守最坏形态，两条都放行真失败

- Type: test reliability（守卫失效）· Priority: medium · Discovered: 2026-08-17，与 AIHOT 做同条件成对 UI 对比时实测线上首屏

线上 `news.aiplanet.live` 当日 15:01 那版在 1440×900 下**首屏完整可见条目数 = 0**：首张卡片 949px、高过 900px 的视口，第二条卡片顶边在 y=1276；滚动加载后 80 条里有 8 条超过 900px，最高 998px。而 `tests/playwright/test_phase2.py` 里三条本该防住它的断言全部通过。

`0f827cd` 已把根因（列表卡片渲染正文配图）整条移除，随之删掉了三条里的第一条（`max_ratio <= 0.4`）。**剩下两条原封不动，仍然是坏的**：

| 断言 | 位置 | 为什么放行 |
|---|---|---|
| `firstViewportCards >= 2` | `test_phase2.py:264,269` 与 `456,462` | 判据是 `top < innerHeight && bottom > 0`，即**相交**而非完整可见；一张占满整屏的卡片照样计 1 |
| `avgHeight <= 520`（`/all` 档为 420） | `test_phase2.py:265,270` 与 `457,463` | **均值**摊平最坏形态：一条 998px 混进九条约 210px，均值 289 |

作为参照，已删的那条错在口径是**单张图**不是整张卡片——生产上 4 图拼贴每张 360/900 = 恰好 0.400 逐张合规，两行合计 0.8 视口，加标题摘要推荐理由就是 949px。

更根本的一层在夹具：`tests/playwright/conftest.py:176` 只给 index 0 和 30 两条配图、每条 1 张，且 URL 是 `http://127.0.0.1:9/playwright-media.png`（discard 端口，**永不加载**）。于是当时那条名为"配图不得主导视口"的断言，在"图确实不主导"与"根本没有配图"两种情况下读数完全相同——它从未在会让它失败的输入上跑过。配图移除后这条具体夹具缺口不再有消费者，但同一形态的下一条守卫仍会踩。

**怎么修**：这两条断言的正确量法已经落成可执行仪器 `~/.claude/bin/first-screen-density`（完整可见而非相交、max 而非均值、band 要扣 fixed/sticky 头且经祖先裁剪、两条可见性路径不一致即报 unresolved），17 条夹具矩阵在同目录 `.test.py`。要么在 Playwright 里照它的判据重写，要么直接在 CI 里调它。**先让新断言在会失败的输入上红一次再改实现**——当前实现已无正文配图，最坏形态得靠夹具造（一条超过视口高的卡片）。

**关联**：`docs/contracts/ux-contract.md` 的 HP-1 与「精选页"正常日"最少条数 ≥10 条（首屏）」把"首屏"定义成 SSR 直出条数，与"视口里看得见几条"脱钩；改守卫时一并收。HP-7 的配图条款已由 `0f827cd` 记入 `ux-contract-issues.md`。

## [open] 2026-08-20：prefilter / score 缺 key 时逐条静默回退纯规则，无日志无计数，输出与 LLM 路径同形

- Type: observability · Priority: medium · Discovered: 2026-08-20 sync-docs 审查核对 README 的 LLM key 说明与 provider 实现时发现。

`src/airadar/provider/` 下五个 provider 都在**每一条**目上做同一件事：key 不在就调纯规则实现，然后照常返回。逐条读源码（2026-08-20）：

| provider | 缺什么就回退 | 回退到 |
|---|---|---|
| `deepseek_v32.py`（prefilter，`model_id = "deepseek-v4-flash"`） | `DEEPSEEK_API_KEY` 与 `ARK_API_KEY` 都为空，或设了 `AI_RADAR_FORCE_HEURISTIC` | `heuristic_prefilter(item)` |
| `glm.py`（prefilter） | 无条件——该类的 `is_ai_related` 直接 `return heuristic_prefilter(item)`，从不调 LLM | `heuristic_prefilter(item)` |
| `codex_gpt_mini.py`（score） | `OPENAI_API_KEY` 为空，或设了 `AI_RADAR_FORCE_HEURISTIC` | `heuristic_score(item)` |
| `deepseek_v4_flash.py` / `deepseek_v4_pro.py`（score） | 同 `deepseek_v32` 的两个 key 条件 | `heuristic_score(item)` |

**问题不是有回退，是回退不可观测**：`git grep -n 'logg\|print(' src/airadar/provider/heuristics.py src/airadar/provider/deepseek_v4_pro.py` 命中 **0**——回退路径不打日志、不记计数、不改返回结构。于是 pipeline 的逐 stage 读数（`prefiltered=N`、`scored=N`）在"真跑了 LLM"与"整轮全部走关键词表"两种情况下**完全相同**。`smoke_test()` 确实会返回 `"ok (offline fallback)"` 这一线索，但它只在显式跑 smoke test 时出现，不在正常 pipeline 路径上。

**后果**：一次 key 过期、`.env` 未加载（cron 的非交互 shell 是典型）、或余额耗尽，表现为「站还在更新、评分还在出、只是选出来的东西变差了」——没有任何一处会红。这正是 `~/.claude/references/evidence-sufficiency.md` 说的那类读数：它在结论为真和为假时取值相同。

**当前判别法（写进 README 的那条）**：确认 `.env` 中 `DEEPSEEK_API_KEY` / `ARK_API_KEY` 非空（`grep -c`），不能靠 pipeline 输出判。

**Fix 方向**：回退发生时打一行结构化日志（stage / provider / 原因：`missing_key` vs `AI_RADAR_FORCE_HEURISTIC`），并在 stage 汇总里带一个 `fallback=N` 计数，使「本轮走没走 LLM」在正常输出里就分得开。`glm.py` 另需单独裁决——它当前是无条件回退，即这个 provider 名义上是 LLM 实现、实际从不调用，属另一层的名实不符。

## 热点候选缓存：keeper 可在换库后发布上一个库的行

**状态**：open · **优先级**：low

`HotCandidateCache.bind()` 换库时会清空候选（[060-hot-cache](../adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md)），但那只覆盖**顺序调用**。并发时序仍有一个缺口：keeper 已在旧库上开始水合 → `bind(db-b)` 清空 → 旧那轮水合返回并**无条件发布**它从 db-a 读到的行。只读探针实测：`after_bind` 为 `None`，`after_old_publish` 为 `[{'id': 'db-a'}]`，而此时 `_db_path` 已是 `db-b`。

**触发前提**：同一进程内换库，且恰好与一轮旧库水合重叠。生产是单 app 单库、`bind` 只在 lifespan 调用一次，碰不到；进程内 lifespan 重启、同进程嵌入第二个 app、以及测试套件会碰到（表现为偶发的跨库串数据）。

**闭合方向**：`_refresh_if_needed` 在水合开始时把 `_db_path` 取快照，发布前在锁内比对，不一致就丢弃这轮结果并记一行日志。约四行，但它是并发不变量，值得单独过一次 review 而不是顺手塞进别的改动里——本轮 review-gate 的 MEDIUM 就地修轮次已用尽，故留账。
## [open] 2026-08-20：`_timeline_data_version()` 漏掉三类会改变 `/all` 总数的写入，缓存因此可能发陈旧计数

- Type: correctness · Priority: medium · Discovered: 2026-08-20 做 `/` 与 `/all` 的 TTFB 优化时，为决定该不该顺手改这个函数而逐维核对它的覆盖面。

ADR-005 在 Consequences 里写下的契约是「缓存正确性依赖 `_timeline_data_version()` 覆盖所有影响计数的数据维度」。逐项对照 `src/airadar/web/routes/timeline.py` 的当前实现，它取的六个维度是：最新 run 的 `id` / `ruleset_version` / `created_at`、`MAX(rowid) FROM items`、`COUNT(*) FROM items`、`MAX(id) FROM item_evaluations`。

据此，下面三类写入会改变 `/all` 的真实总数、却**不会**推进这个 tuple：

| 写入 | 为什么计数变了 | 为什么 tuple 不变 |
| --- | --- | --- |
| `UPDATE sources SET enabled=0`（或改 `kind`） | `TIMELINE_SOURCE_VISIBILITY_CLAUSES` 用 `s.enabled=1` 与 `kind != 'wechat'` 过滤，停用一个来源直接减少可见条目 | 六个维度全部只看 `items` / `item_evaluations` / `curation_runs`，没有一个看 `sources` |
| 既有 item 的原地 `UPDATE`（`upsert_item` 的 existing_url 分支会改 `url` / `published_at`） | `deduped_item_clause` 按 `(source_id, 规范化 url)` 判重，改 url 会改变哪一条被判为重复 | 行数不变、`MAX(rowid)` 不变 |
| 既有 evaluation 的原地 `UPDATE` | prefilter / scoring 的判定值变了，`_PREFILTER_SCORING_CLAUSE` 的结果随之变 | `MAX(id)` 只在**新增**行时前进 |

失败形态是**静默且没有时间上界**：`/all` 的分页总数与末页号会对不上，而 `VersionedTotalCache`（`src/airadar/web/routes/pagination.py`）**没有 TTL**——它只是一个带锁的 `OrderedDict`。所以陈旧值会一直留到tuple 里某个维度真的变化（下一次 pipeline 写入新行、或新建一次 curation run）、被 LRU（maxsize 64）挤出、或进程重启为止。`PUBLIC_PAGINATION_CACHE_CONTROL` 那 90 秒是 HTTP / 边缘层的，管不到进程内这一份。

（本条早先写的是「最长约 90 秒自愈」，把两层缓存混为一谈；当场读源确认该类无任何 TTL 字段。）

**本轮刻意没有在这里改**：同一次改动里既动性能又动"哪些写入使缓存失效"，会让日后总数真的开始发陈旧时无法分辨是哪一半造成的。当前那次改动只给这三个 `curation_runs` 子查询加了索引与 `id DESC` tie-break，覆盖面一个维度没动。

**Fix 方向**：items / evaluations 的原地更新需要一个随更新前进的信号（`updated_at` 列，或由触发器维护的 generation——`archive_cache_generations` 是现成的形态）。`sources` 那一维**没有现成范式可抄**：把 `COUNT(*)` / `SUM(enabled)` / `kind` 取值集合并进 tuple 是最直觉的写法，但它分不出「停用 A、同时启用 B」这种等量交换——三个聚合值都不变而可见集合已经变了。所以这一维要么按 `sources` 的 `MAX(rowid)` 加一个由触发器维护的 generation，要么把 enabled 集合的某种顺序无关摘要（如 `group_concat(id ORDER BY id)` 的哈希）并进去；两种都比三个聚合值贵，值不值得要连着「这个缺口实际造成过什么」一起判。

### 精选 freshness_quota 疑似饿死历史高分条目（v1 curator 既有机制，未实证）

- **现象**（2026-09-02 精选对齐诊断 §3.3 推断）：AIHOT 精选而我站未选的 37 条中，9 条（24%）按当前公式重算 ≥ 阈值 6.5、却从未被任何一次 curation run 选中。推断机制：`freshness_quota` 每天先填满 36 名额，`filtered` 池只剩 ~4 名额与全历史最高分竞争，稍旧的高分条目永远排不进。
- **状态**：推断未逐条实证；与 v2 无关（v1 既有）。诊断报告 `.label-serve/round45-human/curation-gap/report.md` §3.3（工具目录不入 git，读数已摘入本条）。
- **处置候选**：验证饿死是否真实（对这 9 条逐 run 查 rank/quota 路径）；若属实，配额策略是精选对齐方案 B（来源形态配额）要一并设计的对象，不单独修。

### enrich v2 约 8% 条目因标签词表/数量校验重试后仍失败，且每轮重复重试（成本泄漏）

- **现象**（2026-09-02 首个 v2 全量回填轮，3249 条）：256 条 `enrich failed after retry`（7.9%），按 DB 复核构成：`tags must contain 2-4 provider-selected values` 129、`tags outside controlled vocabulary` 113（NVIDIA 38 / Mistral 20 / PyTorch 6 …，另有 `Nvidia`、`Mistral AI` 等变体）、`why_recommend` 超长 11、`summary_zh` 校验 3（超 400 字 2、句数不在 3–5 之间 1）。这些是 v2 prompt 与受控词表（`enrich/prompts_v2.py` / `schema_v2.py`）之间的系统性错配，不是偶发。
- **成本面**：候选 SQL 只把 `error IS NULL` 的成功行视为已处理；而 `items.fetched_at` 每次被信源重新列出都会 bump（`fetcher/dedup.py`），所以失败项只要还在 feed 里就**永不**滑出 `--since 24h` 窗口。2026-09-03 已加 24h 失败退避（`stage_common.ENRICH_FAILED_RETRY_BACKOFF_HOURS`，只对 `schema validation failed` / `output rejected` 两类确定性错误生效，供应商瞬时故障与显式 `--item-id-file` 路径不退避）：每条这类失败项每天最多重试一次（含一次即时重试=2 次调用），不再与新条目争单轮 40 个名额；根因（词表错配）仍未修。事故当日的 242 行旧前缀为 `enrich failed after retry: tags…`（另 14 行已是 `schema validation failed`），已作为 legacy 前缀纳入退避判据，不再有过渡期重试。
- **处置候选**：① 词表侧把高频厂商/框架名（NVIDIA、Mistral、PyTorch…）纳入受控词表或在 normalizer 里做别名映射；② 数量校验不足 2 个时降级为接受 1 个而非整条失败。修改 prompt 前先读 `prompt-writing-guidelines.md`；改动走 T3 回归。
- **可观测性缺口**（review 附带）：`cli._enrich` 无论 errors 多少都 `return 0`，pipeline 日志永远是 `=== enrich OK ===`；40/40 失败也看不出。

### enrich 错误分类只活在字符串前缀里，且写方/读方已有两套并行定义

- **现象**（2026-09-03 review 附带，基线独立）：错误类别由 runner 写入的 error 前缀承载（写方三处：enrich v1/v2 runner、`scorer/runner.py`），读方三处各认自己的词表：候选 SQL 的 `stage_common.DETERMINISTIC_ENRICH_ERROR_PREFIXES`、`admin/calibration.py` 的 `SCHEMA_ERROR_RE`（只认 `schema validation failed`）、`alerts._recent_upstream_stats` 复用的 `UPSTREAM_ERROR_RE`。v2 主导失败类（`output rejected` / 旧 `enrich failed after retry: tags…`，事故当日 242/256）在 A2 schema 错误率里过去看不见、现在也看不见。
- **处置候选**：让 calibration 消费 `DETERMINISTIC_ENRICH_ERROR_PREFIXES`；或给 `item_evaluations` 加 `error_kind` 列（schema 改动，走 review-schema）。

### fetch 阶段 lxml HTML 解析在线程池内 SIGABRT（首次出现，2026-09-03 08:31）

- **现象**：pipeline 08:30 轮 `fetch FAIL (exit 134)`，`~/Library/Logs/DiagnosticReports/python3.13-2026-09-03-083152.ips`：faulting thread 栈 `lxml etree._fixHtmlDictNames ← _BaseParser._parseUnicodeDoc ← fromstring`，libmalloc 报 `pointer being freed was not allocated`。fetch 用 `ThreadPoolExecutor` 并发抓源（`fetcher/runner.py:311,546`），HTML 解析发生在工作线程；lxml 6.1.0 / libxml2 2.14.6（uv.lock 未变）。该轮其余阶段照常，下一轮 fetch 正常与否见 journal。
- **判断**：libxml2 HTML parser 的 dict/名字表在多线程下的已知类缺陷形态；单次出现不足以定复现率。
- **处置候选**：① 观察复发率（DiagnosticReports 有无新 python 崩溃）；② 复发则给 HTML 解析加进程级互斥、或改用 `html.parser`/`selectolax` 等纯 Python/独立实现；③ 把 fetch 的非零退出与 crash report 关联进 A 系告警（现状是 `fetch FAIL` 记日志、pipeline 继续、无告警）。

### `rollback-quota` 的 source-quota-v1 校验器只核形状与计数，不核引用与语义一致（review-schema 复审保留项，2026-09-03）

- **现状**：`cli.py` 的 `_validate_source_quota_shadow` / `_validate_source_quota_block` 拒绝缺键、错类型、`quota_only_count` 与逐行 `baseline_selected=false` 不一致的 run；不核：① `baseline_only[].item_id` 是否存在于 `items` 且与当前精选集互斥；② 逐行 `kind` 是否等于 `sources.kind`、同 run 各行 `kind_cap`/`source_cap` 是否一致、实际逐 kind/逐源计数是否 ≤ cap；③ `shadow_json` 未知顶层键与回退后 `rollback:{at, removed_item_ids}` 块的形状；④ `reason_json.raw_weighted_score` 与 `weighted_score` 同时存在且不等时取前者、不拒绝。
- **为什么值得跟踪**：这些漂移在当前唯一写入端（`curate()` 同一事务写入）下不会自然发生，只在人工改库或未来第二写入端出现时才会；届时回退会把不一致输入带进成功路径（重算 rank/展示分）。
- **处置候选**：把校验器扩成接收 `conn`/`run_id` 的语义校验（join `items`/`sources`、整 run cap 一致性、rollback 块封闭键集），复用于 curate 落库与 rollback 两处；配套否定用例。`source_cap:null` 分支尚无真实实例（默认 policy 总配单源上限），若上线后需要该分支，先在副本用 `per_source=None` 产一份实例接地。

### interpret 的 selector 收据与 domain-routing 策略之间存在写入竞态，收据落地即失效

- **现象**：`interpret` 自 2026-09-01 15:00 起每轮跳过（`skip interpret: selector compatibility is unproven (receipt does not match egress implementation)`），累计 138 轮；215 篇微信文章无解读（`wx_mp2rss` 130 / `wx_wechat2rss` 85），`/wechat` 因 `JOIN wechat_interpretations WHERE save_decision=1` 而停更。fetch 与告警全绿，无任何告警触发。
- **逐字段定位**：收据 `$AI_ASSISTANT_ROOT/ai-radar-egress-contract-v2.json` 只有 `policy_sha256` 不匹配（收据 `6fdcfb9f…` vs 生产 `58a97e64…`）；`egress_implementation_sha256` 完全一致（`9d7d950a…`），`./run.sh egress-preflight` 当时即 `status=healthy`。
- **根因（竞态，四分钟）**：策略文件 `system-config:config/agent-proxy/policies/domain-routing-v2.tsv` 的两次相邻提交——`a5f3433` 09-02 16:38（SG Standard）产出 sha `6fdcfb9f…`，`236d165` 09-02 20:32（`googleapis.com` 由 5 条 exact 收敛为 1 条 suffix）产出 sha `58a97e64…`。ai-radar 的 `02fce04 fix(interpret): re-attest the selector receipt under domain-routing v2` 于 09-02 20:40 提交、收据文件 mtime 20:36，其 attestation 基于 `a5f3433` 那版策略跑完，写盘时生产已被 `236d165` 换掉 → **收据从落地那一刻起就与生产策略不一致**。
- **为什么没人发现**：闸 fail-closed 且**干净退出 0**（契约文档明写"no external script is started"），pipeline 阶段报 `interpret OK`；现有 A1–A7 无「interpret 连续 N 轮 skipped」维度。
- **本次处置（2026-09-05，用户裁决先恢复）**：收据 `policy_sha256` 直接改为 `58a97e64…`（原文件备份 `.bak-20260905-095616`），`_preflight()` 返回 ok。**残余**：收据现 attest 一个未经 attestation 的策略，该闸暂时退化为形式；是否补跑完整 attestation 待定。
- **闭合方向**：(1) attestation 流程收尾时校验生产 `policy_sha256` 未变，变了即重跑，消除竞态；(2) 给 `interpret skipped=true` 连续 N 轮加一条告警（属「产出还在但质量变差」类静默降级，与 Playwright 那条同族）；(3) 策略仓与收据消费方之间缺跨仓变更通知，考虑让 `egress-preflight` 在 sha 漂移时显式提示收据需重签。

### interpret 把 `check-proxy-status` 的整体健康当前置，任一无关远端抖动即让整轮 FAIL

- **现象**：收据 `policy_sha256` 修好、闸已放行之后，interpret 仍间歇整轮失败——`2026-09-05 11:11:03 === interpret FAIL (exit 1) ===`，异常为 `airadar.egress.EgressPreflightError: status command returned 1`（`egress.py:135`，经 `interpret/runner.py:192` 的 `require_selector_policy()`）。同形态在 09-04 18:00 / 20:45 / 22:15 三轮已各发生一次，当时被收据不匹配的跳过掩盖。
- **机理**：`require_selector_policy()` 只需要「模式是 domain-routing、policy_id/sha 已知」这两项事实，却把 `check-proxy-status --format=kv` 的**退出码**（= 整体健康）当作前置。该命令的非 0 分支包含与 interpret 无关的远端探测结果：`AGENT_PROXY_ADDR` 与 `DOMAIN_ROUTER_PROXY` 不一致（`effective_mode=custom`）、`gcp-data-unreachable`、`gcp-tunnel-unavailable`、`dgx-mode-stale`、`dgx-resources-unavailable`。因此 GCP 隧道或 DGX 侧任一抖动，都会让本轮**一篇文章都不处理**。
- **归因订正（2026-09-05 03:46Z，据新证据）**：初判「无关远端瞬时抖动」**不成立**。后续读数表明 11:11–11:45 是一次**真实的 egress 不可用窗口**：11:15 / 11:30 / 11:45 三轮 pipeline 连 `egress preflight` 阶段都 `FAIL (exit 1)`（`status=unavailable reason=status command returned 1`），整轮外部阶段一个没跑；11:46 手动跑即恢复 `status=healthy exit 0`，`overall_status=healthy`、`router_status=running`、端口与 `DOMAIN_ROUTER_PROXY` 一致。故 11:11 那次 interpret FAIL 是**正确的 fail-closed**，不是误杀。
- **排除项**：与并行的 attestation 任务无关——该任务 wrapper 11:00:48 启动，其 `.output` 里 103 条命令全为只读 `rg`/`git show`，`enable_proxy|disable_proxy|agent-proxy |switch` 命中数为 0。也不是 pipeline 自身环境所致：按 `.env` 复现 pipeline 环境（`set -a; . ./.env`）跑同一条 status 命令得 `effective_mode=domain-routing overall_status=healthy exit 0`。
- **因此本条的可议面收窄**：`egress preflight` 阶段按整体健康 fail-closed 是**合理的**（所有外部阶段都不该在选择器不健康时发流量）；真正重复的只是 `interpret` 又独立判了一次同样的整体健康——它需要的只是策略身份字段。收窄那一层可减少一次冗余失败面，但**不会**让上述 34 分钟窗口里的 interpret 跑起来（那段时间外呼本就不该发）。
- **另一个未覆盖面（新）**：那 34 分钟 egress 中断**零告警**。pipeline 每轮 `PIPELINE DONE (failed=1)` 但无人看；A1–A7 无「egress preflight 连续 N 轮 FAIL」维度。这与本文件里 Playwright 那条、以及 interpret 静默跳过那条同族——都是「阶段失败但整体报 OK / 无人看」。
- **影响**：解读积压的排空被间歇打断——09:30 轮 `interpret processed=30 errors=0`，10:45 轮 FAIL 处理 0 条。不阻断（下一轮重试），但拖长恢复且每次都以 `PIPELINE DONE (failed=1)` 计入失败。
- **闭合方向**：把 interpret 的前置收窄到它真正依赖的字段——解析 `--format=kv` 的 `stored_mode` / `effective_mode` / `policy_id` / `policy_sha256` / `policy_projection`，仅当这几项不满足时才拒绝；`check-proxy-status` 的整体退出码留给它自己的消费者。注意 `parse_proxy_status()` 已在解析 stdout，问题只在**先按退出码 fail-closed** 这一步。

### cron 的 PATH 缺 `/usr/sbin`，`lsof` 找不到 → egress preflight fail-closed → 整条 pipeline 的外部阶段全部不跑

- **现象**：2026-09-05 11:15 / 11:30 / 11:45 / 12:00 连续四轮 `=== egress preflight FAIL (exit 1) ===`（`status=unavailable reason=status command returned 1`），整轮**连 fetch 都没跑**；同期手动跑 `./run.sh egress-preflight` 恒为 `status=healthy exit 0`。11:11 那次 `interpret FAIL (exit 1)` 与 09-04 18:00 / 20:45 / 22:15 三次同源。
- **根因（阴阳性对照已做）**：`check-proxy-status` 的健康探测调用 `system-config:bin/agent-proxy-wait-launchd-listener`，后者以**裸 `lsof`** shell 出去，而 `lsof` 只在 `/usr/sbin/lsof`。crontab 的 `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` 不含 `/usr/sbin`，`pipeline.sh` 原本的 `export PATH` 也没补，于是该 helper 抛 `FileNotFoundError: [Errno 2] No such file or directory: 'lsof'` → `check-proxy-status` 判 `overall_status=degraded` 并 return 1 → `require_selector_policy()` / egress preflight 按设计 fail-closed。
  - 阴性对照：`env -i HOME PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin ./run.sh egress-preflight` → `status=unavailable`。
  - 阳性对照：同环境 PATH 末尾加 `:/usr/sbin` → `status=healthy policy_sha256=58a97e64…`。
- **为什么表现为「间歇」**：该 helper 只在走到 `listener_owned()` 那一步才调 `lsof`；deadline 已过或 pid 解析为 None 时提前返回、不触发。故同一缺陷时而命中时而不命中，掩盖了它是确定性的 PATH 缺失。这正是 user-scope CLAUDE.md「非交互 Shell 里执行命令」点名的形态：报错指向别处（这里是 `status command returned 1`），而真因是环境缺失；判据是**手动跑成功、cron 跑失败**。
- **本次处置（2026-09-05）**：`pipeline.sh` 的 `export PATH` 插入 `/usr/sbin`（备份 `scratchpad/pipeline.sh.bak`），并在该行上方写明理由。cron 环境下复验 `status=healthy`。
- **闭合方向（根上）**：`system-config:bin/agent-proxy-wait-launchd-listener` 应以绝对路径调 `lsof`（或在其 PATH 上补 `/usr/sbin`）——否则每个非交互调用方都要各自记得补，这已是第二次由 PATH 缺失伪装成别的故障（前一次见 `plans/20260816-mp2rss-replacement/state.md` ISSUE-008 的代理变量缺失）。另：连续四轮 egress preflight FAIL **零告警**，与本文件 Playwright、interpret 静默跳过两条同族。
