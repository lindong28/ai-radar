# General Issues

> Mutable. 项目级未分类问题——按 lifecycle 维护。

---

## [open] 停用一个微信源会藏起它独有的文章，重新启用又会让同一篇出现两次

- Type: reliability
- Priority: medium
- Discovered: 2026-08-20 双跑改动的 review-gate（独立 Codex reviewer，session 01a01dd3）；用户裁决保持现状、记 issue。
- Description: 跨源去重（`src/airadar/fetcher/dedup.py` 的 `wechat_duplicate_id`）只匹配 `s.enabled=1` 的来源，`/wechat` 也按同一个 flag 过滤。实测四步：候选源先发现一篇 → `items=1` 可见 1；停用该源 → 可见 0（文章从站上消失）；另一源随后带来同一篇 → `items=2` 可见 1；重新启用候选源 → **可见 2，同一篇两张卡**。
- 为什么不改成匹配全部行: 那样隐藏行会持续拦住每一次插入，停用源独有的文章**永久补不回来**（这是 reviewer 报的原 HIGH-1）。当前取舍选了"补得回来"这一面，重复只出现在"停用后又重新启用"这一条路径上，而当前方向是停掉后不再启用。
- Fix 方向: 命中的行属于已停用来源时，不插新行而是把该行改归属到新来源（`source_id` / `url` / 正文一并更新），始终只保留一行；需处理 `items` 上的唯一索引冲突，并另走一轮评审。
- 缓解: 重新启用前先跑 [operations/wechat-ingestion.md](../operations/wechat-ingestion.md)「停用其中一个微信源时会发生什么」里的清重查询。

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

`HotCandidateCache.bind()` 换库时会清空候选（ADR-060），但那只覆盖**顺序调用**。并发时序仍有一个缺口：keeper 已在旧库上开始水合 → `bind(db-b)` 清空 → 旧那轮水合返回并**无条件发布**它从 db-a 读到的行。只读探针实测：`after_bind` 为 `None`，`after_old_publish` 为 `[{'id': 'db-a'}]`，而此时 `_db_path` 已是 `db-b`。

**触发前提**：同一进程内换库，且恰好与一轮旧库水合重叠。生产是单 app 单库、`bind` 只在 lifespan 调用一次，碰不到；进程内 lifespan 重启、同进程嵌入第二个 app、以及测试套件会碰到（表现为偶发的跨库串数据）。

**闭合方向**：`_refresh_if_needed` 在水合开始时把 `_db_path` 取快照，发布前在锁内比对，不一致就丢弃这轮结果并记一行日志。约四行，但它是并发不变量，值得单独过一次 review 而不是顺手塞进别的改动里——本轮 review-gate 的 MEDIUM 就地修轮次已用尽，故留账。
