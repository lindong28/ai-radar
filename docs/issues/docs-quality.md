# Docs Quality Issues

> 文档自身的质量债跟踪（README 定位、重复、可观察性等审查遗留）。协议：`~/.claude/references/docs-organization-protocol.md` §4.8。

## [open] 2026-09-01：ux-contract issue domain 混入非端到端观察

- Type: ownership drift · Priority: medium · Discovered: 2026-09-01 微信搜索与 KB 补录文档同步的 P1 审查。

`docs/issues/ux-contract-issues.md` 的文件头要求条目来自真实端到端产品观察，但其中多条 2026-08-20 条目明确标注来源是 `sync-docs` / 源码核对，而不是产品观察。当前 2026-08-31 微信搜索条目有用户真实入口失败报告，符合该 domain；旧条目则需要逐条选择：补真实观察后保留，或迁到一般产品/文档 issue domain。批量迁移会改变 issue provenance 与后续 UX contract intake 范围，本次不把它夹带进微信搜索手册更新。

## [open] 2026-09-01：README 私有 benchmark 内容与 docs/data authority 尚未收敛

- Type: information architecture · Priority: low · Discovered: 2026-09-01 微信搜索与 KB 补录文档同步的 P1/P2 审查。

根 README 的 `AIHOT 私有基准集` 章节承载了 private submodule 获取、capture/slice/validate 和双仓提交细节，面向维护基准的开发者而非入口部署者；同时仓库已消费大量外部源和物化 SQLite/benchmark 数据，却还没有 `docs/data/` 作为单一数据 authority。这两项需要一起决定内容迁移和新目录边界，不能只从 README 删除。当前事实仍由 README、`docs/architecture.md` 与相关 ADR 分担；后续应单独设计迁移，并按内容重新分配闸核对不丢信息。

## [open] 2026-09-01：README 的服务入口与 operations authority 边界尚未收敛

- Type: information architecture · Priority: low · Discovered: 2026-09-01 微信搜索与 KB 补录文档同步的 README P6 复审。

根 README 的服务/部署区域同时承载入口清单与较重的运维细节，包括 lifecycle 约束、依赖查找顺序、selector preflight 和 EdgeOne 对账退出码；相同事实也由 `docs/operations/services.md` 与 `docs/operations/monitoring-alerting.md` 维护。当前不能机械删除 README 段落：README 仍须满足服务协议的“服务清单 + 运维入口”，而 operations 文档又绑定维护者具体产线，并非全部适合 fork。

后续应按 fork 部署者与维护者两类任务重新划分：README 保留选服务、最短 bring-up/验证和 authority 路由；可变的产线状态、完整诊断与重操作步骤留在 operations。迁移前按内容重新分配闸逐段对账，避免在“去重”时删掉 fork 唯一可见的入口。

## [open] 2026-09-01：自托管 Tunnel 验证缺少当前 checkout 的身份锚

- Type: observability · Priority: medium · Discovered: 2026-09-01 微信搜索与 KB 补录文档同步的 README P4 复审。

README 的自托管 Cloudflare Tunnel 步骤可以分别证明 supervisor、本地 origin 与配置 hostname 可达，但 `/api/v1/healthz` 没有 checkout / commit 身份，公网响应来自当前树、旧实例或同机另一服务时都可能得到相同读数。README 已明确把 `public_ok` 收窄为 hostname reachability，不再据此宣称当前 checkout 已连通；仍缺的是能证明 origin 与公网响应身份相同的消费者侧读数。

闭合时应提供不会泄露敏感信息的实例身份锚，并让 bring-up 同时读取本地与公网两端进行比对；若身份只在部署时生成，要写清失效与轮换条件。不要用两个独立的 `200 OK` 或相同静态 `healthz` body 代替身份平账。

## [open] 2026-08-20：服务器侧 web/alert 与 make-live 路径仍未进入服务清单

- Type: coverage gap · Priority: medium · Discovered: 2026-08-10 README 全面审查（原属该条清单的「services.md / 服务清单侧」一项）；2026-08-20 归档母条目时复核仍未修，独立成条。

`deploy/server/install-server.sh` 定义的服务器侧生产栈包含双槽 `ai-radar-serve@8000/@8001`、`ai-radar-db-apply` 与 `ai-radar-alert`。当前 `docs/operations/services.md` 已列出 DB apply consumer/timer，但仍没有把服务器侧 web/alert 单元纳入服务表，也没有写清 `git push tencent` → `deploy/server/post-receive` → `deploy/sync/deploy_code.py` 的 make-live 路径。复核读数（2026-09-01）：`services.md` 能命中 `ai-radar-db-apply.service`，但 `ai-radar-serve|serve@|ai-radar-alert|post-receive|deploy_code` 仍为 **0**。

**为什么值得记**：services.md 是「系统在跑什么、谁拉起的」的单点权威；当前它虽已覆盖 DB apply，但生产 web/alert 与代码生效路径仍不在完整清单中。接手者按它排查仍无法还原公网服务和部署切换。ADR-042 已把生产 deploy commit 与本地 main 隔离，那条路径的存在性更需要写下来。

## [open] 2026-08-20：ADR 内的 `file:line` 锚点在 append-only 下必然衰减

- Type: content currency · Priority: low · Discovered: 2026-08-20 sync-docs 审查逐条核对 ADR 与源码时发现。

ADR 是 append-only、不回改的；被它引用的源文件却在持续变。于是 ADR 正文里的 `file.py:123` 形态锚点会随下一次插入静默失效——**失败形态是指到一行无关代码**，不是报错，读者照着看会得到一个看似合理的错误印象。

实测（2026-08-20，逐条 `sed -n '<N>p'` 核对 ADR-056 与 ADR-058 的全部 8 个 `file:line` 锚点）：

| 锚点 | ADR 说它是什么 | 该行实际是什么 |
|---|---|---|
| `app.js:767`（ADR-058） | `rebuildTimeline` 用 `innerHTML` 整块替换列表 | `<p>${esc(emptyBody)}</p>` |
| `web/static/app.js:2045`（ADR-058） | `bookmarkSnapshot` 字段白名单 | `function syncDateControls(...)` |
| `src/airadar/web/app.py:739`（ADR-058） | `/curated` 的 alias 路由 | `if payload is None:` |
| `src/airadar/presentation/summary.py:145`（ADR-056） | 给任何有 scoring 数据的条目算真分 | `if "numeric_json" in row.keys() …` |

同批中 `src/airadar/curator/score.py:8`（`TIER_MULTIPLIERS = …`）与 `:29` 仍然准确——即漂移是**逐锚点**发生的，不是整份 ADR 一起偏移，所以读者无法靠"整体加个偏移量"自行校正。

**约定（对后续 ADR 写作）**：只用**符号名或原文文案**作锚（`X_MEDIA_FIELDS`、`_x_media_assets()`、「热点榜单正在生成」），需要定位时给出可复跑的 `git grep` 而不是行号。**旧锚不回改**——回改会破坏 ADR 的 append-only 性质，且逐条追行号本身就是本条要消除的那种维护负担。

## [open] 2026-08-17：CLAUDE.md 首行 Python 版本与实跑不符（cache-busting 审查发现，本轮范围外）

- Type: content currency · Priority: low · Discovered: 2026-08-17, Frontend Asset Cache Busting 一节的 CLAUDE.md 审查

`CLAUDE.md` 第 5 行称 "AI Radar is a **Python 3.12** FastAPI application"，但实测 `pyproject.toml` 的 `requires-python = ">=3.12"` 是**下限**、`.python-version` 为 `3.13`（2026-08-09 提交）、`.venv/pyvenv.cfg` 的 `version_info = 3.13.12` 是实际运行时。准确写法是「Python ≥3.12（本地 pin 3.13）」，或去掉版本数字让 `pyproject.toml` 单独承载。属既有行、非本轮改动引入，故未就地修。

## [open] 2026-08-20：`docs/prd/` 是一簇陈旧内容，且没有任何标记告诉读者它已被推翻

- Type: content currency · Priority: medium · Discovered: 2026-08-20 sync-docs 审查逐条核对 prd/ 与实现。

`docs/prd/` 按 `docs/CLAUDE.md` 的定位是「只读参考——不在日常开发中修改，变更走 ADR 或新版 PRD」。问题不是它旧，而是**旧到与现实相反、却没有任何就地标记**：读者按索引进来，读到的每一句都长得像仍然生效。四处实测（2026-08-20）：

| 位置 | 文档怎么说 | 实际 |
|---|---|---|
| `VISION.md` 附录 B（文件末尾「经验文档」一行） | 指向 `~/.claude/experiences/verify-discipline.md` | 该目录整个不存在（`test -d` 为假）——死链 |
| `VISION.md` §路线图 v0 行 | 状态列写「待启动」 | v0 全部能力早已上线并在生产服务公网 |
| `VISION.md` D2（决策表） | 「与 summary-agent 完全 greenfield，不关联」 | 已被 [ADR-007](../adr/007-interpret-via-ai-assistant-summarizer.md)「微信文章解读复用 ai-assistant summarizer」推翻，PRD 侧无任何标注 |
| `PRD_v0.md` §8 信源池 vs §13 D20 | §8 表末写「合计 12 条」，§13 D20 与 §14 附录均写「17 条」 | 两个数字在同一份文档内自相矛盾（与当前 `data/sources.toml` 的 163 条都不符，但那属正常演化，不是本条要点） |

**为什么值得记**：`PRD_v0.md` 的索引行已注明「模块路径与评分口径与当前实现已漂移」，`VISION.md` 的索引行没有同类提示，而 `VISION.md` §4 核心原则在索引里被标为 **BINDING**——于是同一份文件里既有仍然生效的条款，也有已被推翻的条款，读者无从分辨哪句还算数。

**修复候选（留用户决定，本轮不改 prd/）**：(a) 给两份文件各加一个状态头，逐条标出已被推翻的条款与其接替者；(b) 写一版新 PRD 让旧版整体降级为历史件；(c) 只修死链与 §8/§13 的数字矛盾，其余留白。(a) 与 (b) 都属对只读件的实质改动，超出 sync-docs 的授权面。

## [open] 2026-08-20：docs 写作约定有三处未定义，各自已产生实际漂移

- Type: convention gap · Priority: low · Discovered: 2026-08-20 sync-docs 审查跨文件比对时归纳。

三条约定目前只活在个别文件的局部实践里，没有写进 `docs/CLAUDE.md` 或协议，于是每个写入者各按自己的理解办：

1. **CHANGELOG entry 的视角**：`CHANGELOG.md` 定位是「用户可感知的变更记录」，但既有条目里混着面向 agent 的 docs 深链（ADR 编号、issue 编号、plan 目录）。约定应是：entry 正文写用户视角的变化，开发者细节留给 docs/，正文里最多一处总指针。
2. **引用源码的锚点形态**：本文件已有一条专记 ADR 内 `file:line` 必然衰减（见上）。同一失败形态在 `issues/`、`operations/`、`architecture.md` 里同样存在——本轮就在 `ux-issues.md` 修了两处漂移的 `app.js:NNNN`。约定应扩面到**全部 docs**：一律用符号名或原文文案作锚，需要定位时给可复跑的 `git grep`。
3. **ADR 的 `Date` 字段语义未定义**：是决策做出那天，还是 ADR 落盘那天？两者在本仓已实际分叉（多份 ADR 是事后补写的），而索引与文件各写各的，无从校对。约定应显式选一个并在 `docs/CLAUDE.md` 的 ADR 节写明。

**为什么值得记**：三条都不是单个文件的错，而是**缺一条共用约定**；不写下来，下一个写入者会以同样的方式再漂一次，而漂移在 review 里看不出来（每一处单独看都合理）。
