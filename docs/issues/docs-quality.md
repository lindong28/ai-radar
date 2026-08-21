# Docs Quality Issues

> 文档自身的质量债跟踪（README 定位、重复、可观察性等审查遗留）。协议：`~/.claude/references/docs-organization-protocol.md` §4.8。

## [open] 2026-08-20：服务器侧生产栈与 make-live 路径不在任何服务清单

- Type: coverage gap · Priority: medium · Discovered: 2026-08-10 README 全面审查（原属该条清单的「services.md / 服务清单侧」一项）；2026-08-20 归档母条目时复核仍未修，独立成条。

`deploy/install-server.sh` 装出的服务器侧生产栈（serve / db-apply / alert，双槽 serve@8000 与 @8001）不在 `docs/operations/services.md` 的服务表里；`git push tencent` → `post-receive` → `deploy_code.py` 这条 make-live 路径也没有任何文档。复核读数（2026-08-20）：`docs/operations/services.md` 内 `install-server|deploy_code|post-receive|8001` 命中 **0**。

**为什么值得记**：services.md 是「系统在跑什么、谁拉起的」的单点权威，而生产上真正在服务公网的那一套恰好不在其中——接手者按它排查会得到一份只覆盖 Mac 本机的图景。ADR-042 已把生产 deploy commit 与本地 main 隔离，那条路径的存在性更需要写下来。

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
