> **Archive status**: 已归档。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档；同目录 `measured-tokens.md` 是 plan-time 的一次性实测记录（2026-08-02 AIHOT 快照的编译后 CSS token），随 plan 归档作为 provenance，不是可重跑的 verifier。
> 移动端单 DOM 布局的最终裁决见 [ADR-012](../../adr/012-single-dom-mobile-layer.md)，当前前端分层与响应式实现见 [architecture.md](../../architecture.md)「Web Layer」的「页面路由」与「SSR preload contract」两节，用户可见验收见 [contracts/ux-contract.md](../../contracts/ux-contract.md)「响应式与视觉」节（RS-1 / RS-2 / RS-5）。以下为原 plan 正文，未修改。

> **Long-task mode**：本计划跨 session、含多轮视觉收敛。执行时以同目录 `state.md` 为恢复入口、`journal.md` 为追加式证据记录，并遵循 `~/.claude/references/long-task-protocol.md`。

# Plan — 把 ai-radar 前端 1-1 复刻到 AIHOT 的审美与体验

## 输入

| 来源 | 路径 | 承载什么 |
|---|---|---|
| 上一轮 handoff | `handoffs/aihot-visual-parity-handoff-20260802.md` | 用户原始目标陈述、两条已拍板边界、硬约束、上一轮改版（`2b8b66b`）内容 |
| AIHOT 编译 CSS | `reference/aihot-snapshot-20260802/css/*.css`（已冻结 7 个 bundle，共 217,672 bytes；其中 `/hot` 两份为 `cdf657f8b4e0d826.css` 11,253 bytes / SHA-256 `45c0692e539a7525d9018341970e33b2dd8754db37c10f97dead14e46e01dab2` 与 `a8424ce4b86e0e18.css` 51,054 bytes / SHA-256 `afa6a4cb01cd28cfae0a8a66da1d048f5b26d3b309a49d2dfcd307a51dce9073`） | **所有测得值的唯一权威来源**：完整 design token 表 + 组件规则 + 断点。`/hot` 资产可用性已在 plan 定稿前用 `hot.html` 的内容哈希 URL和真实浏览器 UA验证并固化，不再是执行期外部依赖 |
| AIHOT 页面 HTML | `reference/aihot-snapshot-20260802/html/{home,all,daily,changelog,topics,hot}.html` | DOM 结构、类名、aria-label 措辞 |
| 基线对照截图 | ~~`reference/aihot-parity/shots/r0-baseline/`~~ **已于 2026-08-05 清理时删除，无副本**。本 plan 完结后删除；删前该目录已按 L2-1 的 2026-08-04 裁决降级为辅助线索（对照权威是实时 AIHOT）。我方侧可用 `reference/aihot-parity/capture.py` 重新采集，AIHOT 侧 8/3 那一时点不可再生 | 原为 60 张冻结截图，实测覆盖：`home` 与 `daily` 各为 AIHOT/我方 × light/dark × 5 档（各 20）；`all` 为 AIHOT light + 我方 light/dark（15，缺 AIHOT dark 5 张）；`changelog` 只有 AIHOT light（5，缺 AIHOT dark 5 张；我方新增页无 before）。Phase 0.1 用冻结 HTML/CSS 离线补齐 AIHOT 缺帧，并按新增页规则记录我方 before N/A |
| 对照采集脚本 | `reference/aihot-parity/capture.py` | 每轮复采的工具，见 §L2 |
| 测得值清单 | `plans/20260803-aihot-visual-parity/measured-tokens.md` | 原范围**已完成**：133 个 token 映射、315 条组件规则、38 个 `@media` 块 / 345 条响应式规则；新增 `/hot` 后须在 Phase 0.0 追加 31 个 `.hot-*` selector 与对应 3 个 media block 的测得映射，之后才恢复为完整 authority |

`reference/` 与 `handoffs/` 被 gitignore（保护开源准备），已在 worktree 内 symlink 到主 checkout 同名目录，implementer 可直接读。

---

## L1 — 最终产物 + 使用方式

**产物**：ai-radar 前端源码的改动（`web/static/style.css`、`web/static/app.js`、`web/templates/*.html`、`web/static/*.html`），以及新增页面和扩展既有热点 payload 所需的最小后端改动（`src/airadar/web/app.py`、`src/airadar/web/routes/curated.py`）。

**使用者与使用方式**：

1. **站点访客**（主要）——在浏览器里浏览 `/`、`/all`、新增 `/hot`、`/daily`、`/wechat`、`/wechat/<slug>`、`/bookmarks`、`/about`、新增 `/changelog`，并在 ≤960px 通过新增 `/more` 进入本站既有的次级入口；在桌面、缩放到 125/150/200%、以及手机上都应获得与 AIHOT 同等质量的排版与交互。
2. **仓库拥有者**（验收者）——在 `http://<host>:8011/` 预览，判断"审美与体验是否已经追平 AIHOT"。

**成功定义**：用户原话 —— "尽可能 1-1 复刻 AIHOT 的效果，特别是审美和用户体验上的，除非明确的理由（比如我这边有 AIHOT 没有的功能）必须要导致 2 者不一致"。因此判据是**逐项对照**：本 plan §gap-inventory 列出的每一条差异，要么被消除，要么有一手证据证明受我方独有功能或缺失数据/产品模型所限，记为 `[accepted-divergence]` 并写明理由；不得用假数据伪造一致。

**范围内**：`/`、`/all`、`/daily`、`/wechat`、`/wechat/<slug>`、`/bookmarks`、`/about` 的视觉与交互；桌面/缩放/移动三态布局；新增 `/hot`、`/changelog`；新增只供 ≤960px 底栏第 4 项进入的 `/more` 页。桌面侧栏“内容”分组新增“热点榜”；移动底栏仍为精选/全部/日报/更多，移动用户从首页“完整榜单 →”进入 `/hot`。`/more` 的四入口白名单保持不变，不因新增 `/hot` 扩张。

**明确不做**：
- `/topics`（主题页）——用户已拍板延后。它是 AIHOT 侧 38 个 AI 聚合主题簇的产品功能（每簇带描述文案与精选条数），不是视觉问题。侧栏**不加**"主题"入口（加了会指向空页）。
- AIHOT 的 `/agent`（Agent 接入）、`/feedback`（反馈）——上一轮用户已排除。
- AIHOT `/daily` 的**周报 / 月报** tab——我方无周报月报数据管线，属产品功能缺失，不是视觉差异。`/daily` 只复刻日报形态。

**硬约束（违反造成实际损害）**：`aiplanet.live` 正在接受中国工信部审核，审核期间公网不得上线。下线状态**完全靠 8000 端口空置维持**——cloudflared tunnel 常驻并把 `aiplanet.live` 回源到 `127.0.0.1:8000`，任何进程绑上 8000，公网立即上线。

---

## 取舍偏好（用户已拍板）

| 取舍 | 用户选择 | 对三层的影响 |
|---|---|---|
| **CSS 复刻方式**：直接移植 AIHOT 编译 CSS vs 按测得值在我方 token/类名体系里重写 | **按测得值重写** | L1：产物是我方词汇表的 CSS，不含第三方编译产物（仓库正为开源做准备）。L3：多一道"从 AIHOT CSS 提取测得值 → 映射到我方 token 名"的工序（Phase 0）。L2 不变——判据仍是像素级对照。 |
| **主题页范围**：本轮做 vs 延后 | **延后** | 本项只约束 `/topics`：仍不实现、不验收。`/hot` 是用户随后独立拍板的唯一产品扩张，不推翻主题页延后。 |
| **保真度 vs 我方独有功能** | 用户原话已定：差异需有"明确的理由（比如我这边有 AIHOT 没有的功能）" | L3：我方独有元素（微信文章解读导航、卡片标签 chip、来源 favicon、收藏导出/导入）**保留功能、按 AIHOT 视觉语言重塑**，不因 AIHOT 没有就删。每条差异在 §gap-inventory 显式标注归属。 |
| **验收介入点** | 用户选择"跑到收敛再看" | L2：我（supervisor）先把 gap 清单跑到全 closed + 回归全绿，再请用户终审；不做逐 phase 打断。 |
| **日报非法日期反馈** | 回退到最近一期，并在报头附近显示一行**非警示色**的弱内联状态文字 | L2/L3：非法、不可解析或未来日期回退并改写 URL；有效但无内容的日期仍显示空状态，不回退。 |
| **日报阅读时长** | 采用我方本地透明公式，不声称等同 AIHOT | 固定 `M = max(1, ceil(C / 300))`；`C` 只计日报正文摘要的可见中文字符，速度常数固定为 300 中文字符/分钟。 |
| **首页日期前缀** | 取当前列表中最新一条内容的日期 | 每次筛选/搜索/刷新后按当前列表最新条目的 `published_at`（Asia/Shanghai）更新；空列表不显示日期前缀。 |
| **移动端“更多”** | 新增独立 `/more` 页 | 仅 ≤960px 底栏第 4 项指向 `/more`；只列微信文章解读、收藏、关于、更新日志；不含主题、Agent 接入或反馈，桌面侧栏不变。 |
| **热点入口与信息量** | 新增独立 `/hot`；首页改为 2 条并加“完整榜单 →” | 这是本轮唯一一次产品范围扩张，用户明确接受代价；桌面侧栏“内容”分组新增“热点榜”，移动底栏和 `/more` 四入口白名单不变。 |

---

## Rigor `(A,V)`

**默认向量：`(A0, V0)`**，人读 label `light`。

- **轴 R（反转成本）= light → A0**：CSS / 模板 / 前端 JS 改动落在独立 worktree 的独立分支，不部署、不 push，公网已下线。纯本地可逆。
- **轴 G（回归容忍）= light → V0**：视觉回归由每轮 5 档缩放矩阵截图直接捕获；站点当前离线，回归在 go-live 前一定被抓到。

**per-phase override（只升不降）**：

| 承载风险的 unit | override | 理由 | 满足机制 |
|---|---|---|---|
| 任何**服务启停 / 端口绑定**动作 | **A2** | 误绑 8000 = 工信部审核期公网上线 = 不可撤销的外部副作用 | ① 服务生命周期由 supervisor 主线程**独占**，委派 prompt 一律禁止 Codex 启动/重启任何服务；② 起服务前后各断言一次 `lsof -nP -iTCP:8000 -sTCP:LISTEN` 为空；③ 应用端口固定 8011、报告端口固定 8012，不从参数推导；④ 只终止已核验 PID/命令/工作目录的本任务进程，绝不按端口盲杀 |
| 改**交互行为**的 unit（无限滚动、热点信源展开、收藏、主题三态、搜索、日期分组折叠、回到顶部） | **V1** | 这些是真实用户功能，视觉截图抓不到行为回归 | 每个改行为的 unit 跑 §L2-3 的 Playwright 行为走查 + 单 reviewer（review-gate 中档） |
| **微信详情页渲染** unit（`web/templates/wechat_detail.html` 及其渲染路径） | **V2** | “正文容器无 script”是安全不变式，XSS 面零容忍 | 逐轮跑 `tests/test_wechat_interpretation.py::test_wechat_pages_render_preload_detail_and_sanitize_markdown` + 对抗审查该 unit 的 diff；等强替代必须同时覆盖整页允许脚本数、`.summary-body` 无 `<script>`、页面无事件处理属性三维 |

**有效强度**：有效 A = max(A0, phase override)；有效 V = max(V0, phase override, review-gate 本地定档)。纯样式 phase 保持 (A0,V0)，不陪跑高档。

**对称校验**：按 rigor-tiers proportionality invariant 回看——A0/V0 省去的机制（逐 unit 全矩阵、对抗审查、scope 绑定）在纯 CSS token 调整上确无对应失败模式（改错了下一轮截图立刻可见、改回即可）。三个抬高点覆盖了全部三类真实不可逆/零容忍面：端口、行为、XSS。未发现失去必要保护的面。

---

## 并发隔离声明

按 `~/.claude/references/concurrent-plan-isolation.md` 三层，**默认全上**（不声明单 session 独占——`git worktree list` 已显示另有 `worktree-feedback-loop` 存在，repo 被共享）。

| 层 | 本 plan 的隔离 | 状态 |
|---|---|---|
| 代码 | 独立 worktree `.claude/worktrees/aihot-parity`（分支 `worktree-aihot-parity`，从 main `d755244`） | ✅ 已建 |
| 可变运行时状态 | 独立 DB 副本 `.claude/worktrees/aihot-parity/data/radar.db`（由 `sqlite3 VACUUM INTO` 从生产库做的一致性在线快照，1.9GB）。**绝不读写生产 `data/radar.db`** | ✅ 已建 |
| 服务 / 端口 | supervisor 独占的 serve 实例 `0.0.0.0:8011`，`AI_RADAR_DB` 指向上面的副本；终审报告临时服务固定 `0.0.0.0:8012`。**不碰** launchd 常驻的 8010 生产实例，**绝不碰 8000** | 每次接手先按服务生命周期契约 preflight；当前 live 状态只记 `state.md`，不在静态 plan 假定 |

**登记**：在 `.claude/active-work.md` 追加本任务条目（worktree 名 / 运行时资源 / 文件面），完工删除。
**文件面重叠检测**：`.claude/active-work.md` 中既有的 `perf-idle-only` 条目已是完工遗留（其 worktree 已不在 `git worktree list` 中，工作已于 2026-07-27 合入 main），其文件面为后端 performance/alerts，与本 plan 的前端文件面无重叠。`worktree-feedback-loop` 未在登记中声明文件面——merge 前需复检重叠。

**部署耦合豁免不适用**：本 repo 不经 symlink 部署，worktree 内的独立实例能完整验证前端改动，全部开发与验证都在 worktree 完成。

---

## L2 — 用户视角 verify（交付 gate）

全部由 agent 自动执行，除 L2-6。

### L2-1　缩放矩阵逐档对照（覆盖用户点名的"缩放时候的排版"）

```bash
cd /Users/lindong/research/ai-radar/.claude/worktrees/aihot-parity
uv run python reference/aihot-parity/capture.py --round <rN> --theme light --only ours
uv run python reference/aihot-parity/capture.py --round <rN> --theme dark --only ours
```

**权威边界（2026-08-04 用户裁决改写，取代原冻结基线条款）**：对照权威是**实时 AIHOT**（`https://aihot.virxact.com`），不是冻结快照。冻结 CSS 与 `measured-tokens.md` 降级为**辅助线索**——它们是参照站的有损投影，本轮已证实其漏抄导致真缺陷不可见（`.timeline-day-head` 的 `display:grid` base 规则从未进入 ledger，见 GAP-58）。因此：

- 任何"我方值有出处 / ledger 每行忠于参照"的检查**都不足以**判定对齐，必须叠加 §L2-7 的成对测量。
- 参照站会漂移。每轮 live 采集须记录采集时刻；若同一元素在两轮 live 之间自身发生变化，记为参照漂移并在 journal 留证，以**最新一轮 live** 为准，不再重开 plan（原条款要求重开，与"live 为权威"矛盾，已作废）。
- 冻结快照仍保留，用途只剩两个：离线查 AIHOT 的 CSS 规则原文，以及为无法 live 复现的历史结论留证。

**固定 manifest**：① 配对页 `home / all / hot / daily / changelog` × `z100-1440 / z125-1152 / z150-0960 / z200-0720 / mobile-390` × `light / dark`；② 无 AIHOT 对应页 `wechat-list / wechat-detail-<真实 slug> / bookmarks / about` × 同一 5 viewport × `light / dark`；③ 仅属 ≤960px 用户面的 `/more` 受控降采样为 `z150-0960 / z200-0720 / mobile-390` × `light / dark`，因为用户已限定它只作为移动底栏目标且桌面无入口。Phase 0.1 把冻结 `all.html`、`hot.html`、`changelog.html` 与各自引用的本地 CSS bundle 离线注入同一 Chromium，阻断全部网络，生成现有基线缺失的 `all`/`changelog` dark 与新增 `hot` light/dark AIHOT frame；DOM/样式 provenance 随 manifest 记录，不从 live AIHOT 补图。Phase 0 在任何 UI 改动前补采我方完整 before manifest；新增页没有 before 时明确记 N/A。所有上述页面都必须有 after；`wechat-detail` slug 由隔离 DB 动态发现，若无可用详情则 preflight 硬失败。

**执行模型**：一个 Chromium 内最多并发 2 个我方 frame；可选实时 AIHOT 诊断最多并发 1 个。若出现资源耗尽，允许确定性降级为串行（manifest 与断言不变、仅变慢）并记录。脚本必须先生成 expected manifest，缺图、空图、打不开的 URL、找不到真实微信详情、报告资源缺失或任一采集异常均以非零退出，禁止 best-effort 跳过后返回 0。

**预期**：配对页与 AIHOT 同档截图在布局骨架、文字层级、间距、选中态、分隔线、标签和移动导航/搜索形态上逐项一致；无 AIHOT 对应页使用同一 token/组件语言且自身 light/dark/mobile 无断裂。
**判据**：报告为每个 `page × viewport × theme` 给出固定 rubric（几何/层级与间距/字体/选中态与分隔线/标签与媒体/移动导航与搜索/触控目标），每格只能是 `[closed]`、有证据与理由的 `[accepted-divergence]` 或 `[open]`，并引用 GAP ID。manifest 完整、无 `[open]`、§gap-inventory 无 `[open]` 才通过。

### L2-2　用户点名的五项差异专项闭环

用户原话点名（"包括但不限于"）：缩放排版 / 字体 / 选中态视觉 / 网页上的横线 / "精选"标注的位置与显示方式。
**预期**：五项各给一组 before-after-AIHOT 三联对照裁切图，且各自在 §gap-inventory 中对应的 GAP 条目为 `[closed]`。
**判据**：这五项是用户显式不满的样例，任何一项未闭环即不得进入 L2-6。

### L2-3　交互行为无回归（对应 V1 override）

```bash
AI_RADAR_PLAYWRIGHT_BASE_URL=http://127.0.0.1:8011 tests/run_user_verify.sh
```

使用仓库现有且唯一的 `tests/playwright/` harness。Phase 0 把 fixture 改为：设置 `AI_RADAR_PLAYWRIGHT_BASE_URL` 时只连接 supervisor 已启动的 8011，不复制 DB、不启动/停止服务；未设置时保留原有自管测试服务模式。不得另建 `tests/parity/behavior_walk.py`。

**覆盖路径与预期**：

| 路径 | 预期可观察结果 |
|---|---|
| `/` 滚到底 | 用快照数据动态选择一个非空且总量不超过 3 个 batch 的筛选条件（找不到即 preflight 失败）；滚到 terminal 后唯一 DOM key 数等于 API 报告 total，过程中条目数递增且无重复 |
| `/` 切分类 → 立刻再切 | 最终列表与最后选中的分类一致（代际防竞态未坏） |
| `/` 日期前缀 | 初始加载、分类/搜索变化及刷新后，以当前查询 API 的最新条目计算 Asia/Shanghai 中文长日期并与页头一致；空结果时日期前缀消失 |
| `/` 热点模块 | 严格显示 `/api/v1/hot?limit=2` 返回的前 2 条（不足 2 条则与响应实际数相等），排序/key/热度一致；≤960px 标题为“今日热点”，桌面为“当前热点”；右上“完整榜单 →”精确跳 `/hot` |
| `/hot` 榜单内容 | 页面 200；渲染 `/api/v1/hot?limit=10` 的全部响应项且 DOM 数、顺序、rank、title/original URL、heat、主信源与 API 逐项相等；显示响应级“过去 48 小时”和我方公式“加权分×10 + 关联讨论×5”，不声称 AIHOT 的报道密度/衰减语义 |
| `/hot` 时间与信源展开 | 相对时间从 hot payload 的有效事件时间计算；当 `related_discussions` 非空时，展开控件列出主信源 + 全部实际关联信源且去重，空时不伪造展开项；页面不渲染状态标签、趋势线、氛围票或站内 story 链接 |
| `/hot` 导航与移动入口 | 桌面侧栏“内容”分组有“热点榜”且当前页高亮；移动底栏仍为 4 项，首页“完整榜单”是移动入口，`/more` 仍严格只有已拍板四项 |
| `/all` 输入搜索词提交并持续加载 | 结果非空；terminal 时唯一 DOM key 数等于同查询 API total；快速换查询时最终列表只属于最后一代请求 |
| 卡片收藏按钮 → `/bookmarks` | 该条出现在收藏页；导出 JSON 可再导入且条目数一致 |
| `/wechat` → `/wechat/<slug>` → 返回 | 动态选择有结果的搜索/分页上下文；以同 `q/page/limit` 的 `/api/v1/wechat` 响应为 expected，页面卡片数与按 `detail_url` 标识的有序 identity 必须逐项相等；卡片进入对应站内详情且正文可见；“返回列表”恢复原 `q`/`page`，并再次与同一 expected 数量/identity 相等；分享 URL 仍指当前详情 |
| `/wechat` 首屏 → 搜索/翻页重渲染 | 首屏、命中搜索、无命中搜索、非首分页分别以对应 `/api/v1/wechat` 响应动态取得 expected item 数与有序 `detail_url`，SSR/CSR DOM 均逐项相等；对同一条目另比较规范化 skeleton，推荐徽标、原文链接、source line、tags、关键 href/data 属性和节点顺序一致 |
| `/about` 信源表 | 以 `/api/v1/sources` 的完整 `sources` 数量与有序 ID为 expected，初始页及清空筛选后的 DOM rows 均逐项相等；命中查询的 DOM 与用同一可见字段规则从 expected 过滤出的集合逐项相等；无命中显示明确空状态且为 0 行 |
| 我方独有导航、标签与 favicon 保留 | 所有渲染桌面侧栏的 L1 HTML consumer 都含唯一且 href 精确为 `/wechat` 的“微信文章解读”入口；用非 WeChat 且 `source_icon_url` 非空/为空各一条 fixture，断言 SSR 与 CSR 的来源头像分别保留实际 URL / 既有首字母 fallback；卡片标签的数量、顺序与文字逐项等于输入数据，仅视觉改为对应页面的 AIHOT 语言 |
| 侧栏主题三态切换 | `data-theme` 与 `data-theme-mode` 随选择变化，`meta[name=theme-color]` 与实际主题一致；刷新后保持，`system` 档跟随 `prefers-color-scheme` |
| 所有 L1 HTML consumer 的首屏主题 | 对每个静态页/模板页（含动态微信详情）拦截 `app.js` 后加载，让 inline prepaint 单独运行；在首屏 app init 前即有正确 `data-theme`、`data-theme-mode` 与 `meta[name=theme-color]`，无页面沿用旧单属性 bootstrap |
| 日期分组折叠 → 追加加载 | 新加载批次继承折叠态 |
| 日期分组标签 | 用固定快照/API 的 `published_at` 计算：桌面显示 Asia/Shanghai 下的绝对日期与星期；390px 对今天/昨天显示相对日名并在其他日期回退为绝对日名 |
| 回到顶部按钮 | 滚动位置归零 |
| 移动端首页（390px） | chip 单行横向滚动且搜索图标固定；搜索图标跳到 `/all#search`；底栏第 4 项跳 `/more`，页面只含微信文章解读、收藏、关于、更新日志四个入口；逐项点击后分别落到 `/wechat`、`/bookmarks`、`/about`、`/changelog` 且目标页主内容可见 |
| 移动端 `/all`（390px） | 保留可直接输入的完整搜索表单；来源维度为下拉、分类为单行 tab，不退化为首页搜索图标 |
| 排除入口与路由保持缺失 | 桌面侧栏、移动底栏、`/more` 及全部新增页面均不得出现主题、Agent 接入或反馈入口；`/topics`、`/agent`、`/feedback` 均不得新增路由或占位页 |
| `/daily` 归档、报头与正文 | 月份可展开/折叠且期数正确；日期导航可用；显示月份/期数与 API/数据集一致；报头品牌精确为 `AI RADAR 日报` / `AI RADAR DAILY`，`VOL.<YYYY.MM.DD>` 取当前有效日报日期，`<N> STORIES` 的 N 等于实际正文条目数；摘要 N 与正文条目数一致，各章节计数之和等于 N；测试从渲染后 DOM 独立提取 `.daily-article-summary` 可见文本、只计指定 CJK 范围并计算公式，显示值必须相等，同时用标题/导航/英文干扰文本证明它们不进入 C |
| `/daily` 非法/未来日期 | URL 改为最近一期；报头附近出现一行非警示色弱状态文字并包含实际回退日期；有效但无内容的日期只显示空状态、不回退 |

**判据**：全部步骤 pass，退出码 0。

### L2-4　微信详情页安全契约（对应 V2 override）

```bash
AI_RADAR_DB=/tmp/airadar-parity-$$.db uv run pytest tests/test_wechat_interpretation.py::test_wechat_pages_render_preload_detail_and_sanitize_markdown -q
```
**预期**：真实渲染详情页的契约全绿；该测试断言整页只含允许的两个脚本、`.summary-body` 正文不含 `<script>`，且页面不含 `onerror`。
**判据**：命令、文件与断言必须保持同一 authority anchor；该断言若因改版被删弱，必须恢复等强断言，不接受放宽。`tests/test_frontend_static_contract.py` 仍在内部回归运行，但不冒充 sanitization gate。

### L2-5　新增 `/hot`、`/changelog`、`/more` 页与导航

**预期**：`/hot`、`/changelog` 与 `/more` 均返回 200；`/hot` 满足 L2-3 的榜单/导航/数据条件；`/changelog` 完整渲染仓库根 `CHANGELOG.md`；桌面侧栏“内容”新增热点榜，“更多”分组出现更新日志并高亮当前页；`/more` 只在 ≤960px 底栏出现入口，页面只列已批准的四项且 href 精确指向 `/wechat`、`/bookmarks`、`/about`、`/changelog`。所有导航面均不出现主题、Agent 接入或反馈，且不存在 `/topics`、`/agent`、`/feedback` 路由或占位页。
**判据**：独立解析 `CHANGELOG.md` 的有序 block（heading、paragraph、list item、code）与 link target，规范化空白后逐项等于页面 changelog 容器的 DOM block/text/link 序列；只对标题计数不算通过。其余断言全部由 Playwright/HTTP 契约自动验证。

### L2-7　关系层与结构层成对测量（2026-08-04 新增，是 L2-6 的前置硬 gate）

**为什么新增**：r4 轮 L2-1~L2-5 全绿（649 值全查、65 条 Playwright、100 帧截图、700 格 rubric），用户打开两站一眼就找出两个真缺陷。根因是上述检查**全部只覆盖值层**；用户第一眼看到的是关系层（对齐、留白对称）与结构层（grid/flex、列数、轨道归属）。方法论权威：`~/.claude/references/web-ui-observation.md`。

```bash
cd /Users/lindong/research/ai-radar
uv run python reference/aihot-parity/probe.py --pages home,all,hot,daily,changelog --theme light --out <rN>-light
uv run python reference/aihot-parity/probe.py --pages home,all,hot,daily,changelog --theme dark  --out <rN>-dark
```

`probe.py` 在**同一次运行**内把两站放到**同一 viewport / 主题**下各测一遍再比差。它的元素定位是**语义的**（按渲染文本形态与 DOM 角色，如 `^\d{1,2}:\d{2}$` 找时间戳、`N月N日|今天|昨天` 找日期头），不依赖类名——两站不共享类名，用 AIHOT 类名断言我方实现在本 plan 已产生过 8 次误报。文字位置一律用 `Range` 量字形盒，不用元素盒。

**必测的关系量**（每个 `page × viewport × theme` 一组）：

| 量 | 定义 | 通过判据 |
|---|---|---|
| 墨迹左右留白 | 主区内全部可见文本节点字形盒的 `min(left)` 与 `clientWidth - max(right)` | 两站同档差值有理由；我方自身 `\|L−R\|` 不显著大于 AIHOT 同档值 |
| 日期头与时间戳对齐 | 日期字形 right/left 减时间字形 right/left | 与 AIHOT 同档同号同量级（AIHOT 实测 `dR−tR = 0`，即共右边界） |
| 结构链 | 时间戳到主区的祖先链，逐层 `display` / `gridTemplateColumns` / padding 归属 | 布局机制与 AIHOT 同档一致，或有写明理由的接受差异 |
| 导航 regime | 该档下可见的 sidebar / tabbar 集合与几何 | 与 AIHOT 同档同形态 |
| 横向溢出 | `scrollWidth > clientWidth` | 两站均为 false，或与 AIHOT 一致 |

**必覆盖的 viewport 轴**：`640 / 960 / 1200` 三个断点各取 **B−1 / B / B+1**，加上用户实际按出的整数缩放档 `1440(100%) / 1152(125%) / 720(200%)` 与 `390`。共 13 档。只取整数缩放档会整段跳过断点中段，正是 GAP-59 的藏身处。

**反向完备性**：范围内的每个 AIHOT 组件，要**从参照侧枚举**其规则，逐条确认我方有对应决策（已实现 / 有理由的接受差异）。"我写的每个值都有出处" + "ledger 每行忠于参照" 仍漏第三个方向——**参照里有、ledger 没抄的规则**，它在前两个方向上完全不可见。GAP-58 就是这么漏掉的。

**判据**：上表每一行在全部 13 档 × light/dark 下，要么一致，要么在 §gap-inventory 有写明理由的 `[accepted-divergence]`。任一档存在未解释的关系层差异即 `[open]`，不得进入 L2-6。

### L2-6　用户终审（唯一人工 gate）

**进入条件只有两种**：① 正常收敛：L2-1~L2-5 **与 L2-7** 全部通过、无 `[open]`，且 §D8 的"亲眼对比"条件已满足；② ceiling 触发：停止自动迭代，把最新报告中仍未闭合的 GAP 作为同一个终审 gate 的异常分支，不能宣称通过。
**交付材料与最短查看路径**：① **用户主入口** `http://<本次实际可达 host>:8012/report-<rN>.html`，先在一页查看 self-contained 的 AIHOT 冻结基线 / 我方 before / 我方 after、完整 manifest、rubric 与最终 GAP 状态；无 AIHOT 对应页标 `N/A — no counterpart`。② 需要实际操作时再打开 `http://<同一 host>:8011/`。磁盘路径 `reference/aihot-parity/report-<rN>.html` 只作内部产物定位，不要求用户打开文件。supervisor 运行时解析实际可达 host，禁止写死 `macmini`，并在交付前 preflight 本地 URL 及**将要原样发给用户的两个最终 URL**可达、资源完整。
**回复格式**：正常收敛时，用户只需“确认达标”，或列出不满意的 GAP/页面/viewport；后者转为新 GAP 进入下一轮，前者进入整合流程。ceiling 分支额外在报告首屏列出每个残余 GAP、证据、影响、预计继续成本，并给三个可直接回复的选项：A. 继续处理指定 GAP；B. 将有充分理由的指定项改为 accepted-divergence；C. 停止本轮且不整合。supervisor 必须结合残余项影响给出当轮推荐与理由，用户选择前保持暂停；ceiling 分支不得以“确认达标”或默认选项放行。

### 内部 verify（L3，过程兜底，非交付证据）

```bash
cd /Users/lindong/research/ai-radar/.claude/worktrees/aihot-parity
AI_RADAR_DB=/tmp/airadar-parity-$$.db uv run pytest -q --ignore=tests/playwright --ignore=tests/test_performance_journey_monitor.py
uv run ruff check src tests
uv run mypy src
node --check web/static/app.js
node --test tests/pagination.test.mjs
```
**已知基线**：`tests/test_performance_journey_monitor.py` 10 个失败在干净 HEAD 上同样失败，继续作为非本次 gate 的已知遗留；`tests/playwright/` 当前 41 个 fixture/setup/旧分页断言问题是本 plan Phase 0/5 必须消除的基础设施缺口，L2-3 最终不得豁免任何 Playwright failure。

---

## UX 契约影响

**有影响。** 本 plan 大幅改变用户可感知行为，`docs/contracts/ux-contract.md` 存在（403 行）。

| 动的 section | 投影出的契约 delta | 对应 L2 |
|---|---|---|
| `### 精选页（/，首页）` | 卡片信息层级重排、页头去卡片化、分类 tab 选中态；以无限滚动替换 HP-8 的数字分页并写明 terminal 总量完整性；日期前缀取当前列表最新内容日期；热点模块固定 2 条、移动“今日热点”/桌面“当前热点”，右上“完整榜单 →”进入 `/hot` | L2-1 / L2-2 / L2-3 |
| **新增** `### 热点榜页（/hot）` | 近 48 小时榜单、我方热度公式、名次/标题/原文/热度/主信源/相对时间，以及有真实关联数据时的信源展开；状态/趋势/氛围票/聚合 story 明确为数据缺口导致的差异 | L2-1 / L2-3 / L2-5 |
| `### 时间线页（/all）` | 卡片层级；来源下拉 + 单行分类筛选；以无限滚动替换 TL-4 的数字分页并写明查询代际与 total 完整性 | L2-1 / L2-3 |
| `### 日报页（/daily）` | 改浅色、新增月份分组归档栏与“今日看点”、章节编号；报头保留本站 `AI RADAR` 归属且日期/正文条数动态；固定阅读公式；非法日期弱状态回退与有效空日期不回退；归档/章节数量完整性 | L2-1 / L2-3 |
| `### 响应式与视觉` | 断点改为 640/960；≤960px 改为底部 tab 栏、横向 chip、页面区分的移动搜索；底栏“更多”指 `/more`；同步 `data-theme-mode` 与浏览器 `theme-color` | L2-1 / L2-3 |
| **新增** `### 更新日志页（/changelog）` | 新页面的用户可感知契约 | L2-5 |
| **新增** `### 更多页（/more）` | 仅移动层入口；只含微信文章解读、收藏、关于、更新日志四项 | L2-3 / L2-5 |
| `### 微信文章解读页` / `### 解读详情页` | light/dark/mobile 视觉也纳入 L2；安全契约**不变** | L2-1 / L2-4 |
| **新增或补全** `### 收藏页（/bookmarks）` / `### 关于页（/about）` | 记录既有功能保持、全局视觉与移动层验收，不改变收藏导入导出语义 | L2-1 / L2-3 |
| `## 范围与约束` 的“所有公开页面”枚举 | 把 `/hot`、`/changelog`、`/more`、`/bookmarks` 纳入公开页面清单；`/more` 注明只在 ≤960px 有导航入口，避免新增 section 后总范围声明仍按旧页面集合验收 | L2-1 / L2-3 / L2-5 |

**取舍决策**：本段全部 delta 都是已对齐的 L1/L2 投影，未浮现超出现有 L1/L2 的新取舍，无需再问用户。

**给 execute 阶段的指令**：按上表 apply 契约 delta 进 `docs/contracts/ux-contract.md` 对应 section及跨页面范围声明，并按列出的 L2 条目验证。这是已批准意图；表中已覆盖本 plan 的全部用户可感知行为，不得保留与无限滚动、非法日期反馈、移动导航或公开页面全集相冲突的旧契约。

---

## Gap Inventory（对照 r0-baseline 得出，是本 plan 的执行清单）

状态取值：`[open]` / `[closed]` / `[accepted-divergence]`。归属列标 `★` 者为用户点名项。**本 plan 定稿时，未显式标为 `[accepted-divergence]` 的差异当前状态均为 `[open]`；实施时逐条把处置格改为 `[closed] + 证据`，不得靠本说明隐式视为完成。**

### A. Token 与全局（Phase 1）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-01 ★字体 | 我方 `--font-sans` 以 `-apple-system` 起头且含 `Noto Sans SC`；AIHOT 以 `system-ui` 起头，序列为 `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` | 采用 AIHOT 序列 → `[closed]`（`--font-sans` 已改 AIHOT 序列（system-ui 起头），Phase 1 运行时 token 断言命中（journal 11:15）） |
| GAP-02 ★字号 | 我方无字号 token，正文视觉约 15-16px；AIHOT 有 `--text-size-xs/sm/base/md/lg/xl/2xl` = `.75/.8125/.875/1/1.125/1.25/1.5rem`，正文 `base`=14px | 建立同名字号 token 并全站改用 → `[closed]`（七档 `--text-size-*` 建立并全站改用；`.timeline-time` 12.5px 等值经忠实度审计逐值回查（17:20）） |
| GAP-03 | 我方无行高 token；AIHOT `--line-height-tight/normal/relaxed` = `1.25/1.5/1.75` | 建立并应用 → `[closed]`（三档 `--line-height-*` 建立并应用（Phase 1 断言 + 审计 match）） |
| GAP-04 ★横线 | 我方只有 `--line`/`--line-strong` 两档；AIHOT 有 `--border`/`--border-strong`/`--border-soft`/`--border-emphasis`/`--border-card-subtle-solid` 五档，各承担不同语义 | 建立五档并按语义分配（分隔线用 soft、卡片边用 border、强调边框用 emphasis） → `[closed]`（五档 border 建立（`--border`/-strong/-soft/-emphasis/-card-subtle-solid），light+dark 运行时命中） |
| GAP-05 | 色板整体偏移：我方 bg `#f6f7f8` / 主色 `#0f766e`（绿）；AIHOT light bg `#f4f5f6` / 主色 `#135e6b`（青） | 全量替换为 AIHOT 测得值（light 与 dark 两套） → `[closed]`（全量替换为 AIHOT 测得值；light `--bg:#f4f5f6` / `--accent:#135e6b`，dark 对应值 20 token 断言全绿） |
| GAP-06 | 我方无间距 token；AIHOT `--space-1..6` = `4/8/12/16/24/32px` | 建立并应用 → `[closed]`（`--space-1..6` = 4/8/12/16/24/32px 建立并应用） |
| GAP-07 | 圆角：AIHOT `--radius/-sm/-lg` = `12/8/16px` | 对齐 → `[closed]`（`--radius`/-sm/-lg = 12/8/16px 对齐） |
| GAP-08 | 阴影：AIHOT light `--shadow-card:0 1px 2px rgba(28,39,51,.05)`，dark 为 `none` | 对齐（dark 主题卡片无阴影是关键差异） → `[closed]`（`--shadow-card` light `0 1px 2px rgba(28,39,51,.05)` / dark `none`，运行时两主题分别命中） |
| GAP-09 | 侧栏宽度：AIHOT `--nav-width:180px`，我方约 205px | 对齐 180px → `[closed]`（`--sidebar-width:180px`） |
| GAP-10 | AIHOT 有 `--theme-transition`（背景 220ms / 文字 180ms / 边框 180ms）统一主题切换过渡；我方无 | 建立并应用到主题相关属性 → `[closed]`（`--theme-transition`（背景 220ms / 文字 180ms / 边框 180ms）建立并应用于主题相关属性） |

### B. 页头与筛选（Phase 1）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-11 | 我方页头包在白色 `.card` 里（圆角+边框+阴影）；AIHOT 页头**无卡片**，标题直接落在页面底色上 | 去卡片化 → `[closed]`（页头去卡片化，标题直落页面底色（Phase 1 肉眼 + r1 帧确认）） |
| GAP-12 | AIHOT 副标题含日期（“2026年8月2日星期日 · AI 自动挑选的高价值内容”）；我方只有说明文字 | `[closed]`（首页副标题日期前缀取当前列表最新条目 `published_at`（Asia/Shanghai 中文长日期），筛选/搜索/刷新同步、空列表省略；Playwright `test_parity_home_date_prefix_tracks_query_refresh_and_empty_state` 覆盖）；原处置：首页取当前列表最新一条的 `published_at`，转 Asia/Shanghai 后输出中文长日期；每次筛选/搜索/刷新同步更新，空列表省略日期前缀 |
| GAP-13 ★选中态 | AIHOT `.segmented` 整条 tab 栏有 `border-bottom:1px solid var(--border-soft)` 贯穿；选中项 `color:var(--accent-cyan-fg)` + `font-weight:600` + `box-shadow:inset 0 -2px 0` 的 2px 下划线；间距 `gap:22px`、`padding:7px 1px 9px`、`font-size:13px`。我方 tab 栏无贯穿底线，下划线宽度与位置不同 | 按测得值重做 → `[closed]`（tab 栏贯穿底线 + 选中 2px 下划线 + `gap:22px` / `padding:7px 1px 9px` / 13px，运行时命中） |
| GAP-14 | 搜索框：AIHOT 输入框与按钮更紧凑、右对齐同一行；我方按钮色块更重 | 对齐 → `[closed]`（搜索框与按钮紧凑右对齐同行） |

### C. 热点模块（Phase 2）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-15 | AIHOT 标题行右侧有“完整榜单 →”链接；我方无 | `[closed]`（首页「完整榜单 →」href 精确 `/hot`，运行时断言命中（journal 13:05））；原处置：新增链接并精确指向 `/hot` |
| GAP-16 | AIHOT 首页只列 2 条；我方列 5 条 | `[closed]`（首页改 `limit=2`，运行时实测 2 行且与 API 前缀逐项一致）；原处置：首页改为 `/api/v1/hot?limit=2` 的前 2 条，不足时与实际响应数一致 |
| GAP-17 | 名次配色：AIHOT `--rank-1/2/3/rest` = `#b3402a/#c2703f/#b8873a/#6b7684`（light）；我方 1 为主色、其余灰 | 对齐四档 → `[closed]`（名次四档配色 light+dark × rank1/2/3/rest 共 16 条 computed style 断言全中 measured 值） |
| GAP-18 ★横线 | AIHOT 榜单行分隔线贯穿卡片内宽；我方分隔线内缩 | 对齐贯穿 → `[closed]`（榜单行分隔线贯穿卡片内宽（去外层横向 padding）） |

### D. 卡片与时间线（Phase 2）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-19 ★"精选"标注 | **AIHOT**：作者行内、@handle 之后，渲染为 chip（`✦ 精选`，青色字 + 淡青底 + 圆角药丸）。**我方**：右上角与评分并排的纯文本 | 移到作者行内并改为 chip → `[closed]`（「精选」移入来源行内 chip，DOM 为 `.source-line > [.source-link, .timeline-selected-badge]`，8 项 computed style 逐项命中（journal 11:55）） |
| GAP-20 | 评分：AIHOT 右上 `• 75` 小字；我方 `精选 ● 92` 与文本粘连 | 拆开，评分独立右上 → `[closed]`（评分独立右上（x=1290 vs badge x=600），与 badge 拆开） |
| GAP-21 | AIHOT 有 `/hot` 独立热点榜页与对应导航项；我方无 | `[closed]`（`/hot` 200 且 DOM 与 API 逐项 identity；12 个侧栏 consumer 全部有「热点榜」且当前页高亮；移动底栏未扩项）；原处置：新增 `/hot` 页面；桌面侧栏“内容”分组增加“热点榜”并支持当前页高亮；移动底栏不扩项，从首页完整榜单链接进入 |
| GAP-22 | 日期分组头：AIHOT 为 `8月2日 ⌄`（chevron 在后）+ 独立 meta `星期日 · 2 条`；我方 `⌄ 8月4日 ·1 条`（chevron 在前、无星期） | 对齐 → `[closed]`（日期分组头改 `8月2日 ⌄` + 同级 meta `星期二 · N 条`，运行时命中） |
| GAP-23 | 时间线 rail：AIHOT 时间戳在卡片外左侧、dot 贴卡片左边缘、卡片有完整边框；我方 dot 与边框关系不同、卡片边框弱 | 对齐 `timeline-rail`/`timeline-dot`/`timeline-card` 的测得几何 → `[closed]`（`--tl-time-w`/`--tl-rail-w`/`--tl-dot-top` 三档经忠实度审计更正为 64/22/20、64/22/16、44/16/20，1440/800/390 三档运行时逐值验证全绿（17:20）） |
| GAP-24 ★横线 | 推荐理由与正文之间：AIHOT 分隔线贯穿卡片内宽；我方内缩 | 对齐 → `[closed]`（推荐理由与正文间分隔线贯穿卡片内宽） |
| GAP-25 | AIHOT 在 `/all` 卡片有 `#其他`、`#具身智能` 形式的标签：井号前缀、无边框、弱化文字；我方渲染为有边框药丸 chip | `[closed]`（`/all` 标签改 `#tag` 弱文字形态；Playwright 断言全部标签以 `#` 起头且 `.tags .tag` 无「精选」）；原处置：这是同一元素的视觉差异，不是我方独有；`/all` 按 AIHOT `#tag` 弱文字形态重做，首页仍按对应冻结页面规则验收 |
| GAP-26 | 来源 favicon + 来源名：我方独有（AIHOT 用头像 + 大写作者名 + @handle）| 保留，字号/字重/间距对齐 AIHOT 作者行 → `[closed]`（来源 favicon + 来源名保留，字号/字重/间距对齐 AIHOT 作者行；SSR/CSR 双路径 favicon 与首字母 fallback 各有 fixture） |

### E. 侧栏与主题切换（Phase 1）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-27 | 侧栏底色：AIHOT light `--sidebar-bg:#ffffff` + `--sidebar-border:#e2e4e7`；我方近似但边界处理不同 | 对齐 → `[closed]`（侧栏底色 light `#ffffff` + border `#e2e4e7` 对齐） |
| GAP-28 | 选中项：AIHOT 为淡色填充圆角块 + 主色文字与图标；我方带左侧强调条 | 对齐（去左条） → `[closed]`（选中项改淡色填充圆角块 + 主色文字，去左侧强调条） |
| GAP-29 | 主题三态控件：AIHOT 顺序 `moon / monitor / sun`，选中项为白底 + 阴影的滑块；我方顺序 `sun / monitor / moon`、选中态更弱 | 对齐顺序与选中态 → `[closed]`（主题三态控件顺序 moon/monitor/sun + 白底阴影滑块选中态） |
| GAP-30 | 分组标签：AIHOT `内容 / 接入 / 更多`；我方 `内容 / 更多` | 保留两组（无"接入"功能）→ `[accepted-divergence]` |

### F. 缩放与移动层（Phase 3 —— 最大工作量，★用户点名）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-31 ★缩放 | 断点：AIHOT `max-width:960px`（13 条规则）与 `max-width:640px`（16 条），另有 `(min-width:641px) and (max-width:960px)`、`max-width:1200px`、`(hover:none),(max-width:960px)`；我方 `760px` / `1100px` / `761-1100px` | 全面改用 AIHOT 断点 → `[closed]`（断点集合仅剩 640/960/1200/641–960/hover-or-960/reduced-motion；761、1100 消失；残留 9 处 760px 均为内容宽度（AIHOT 亦如此），`min-width:961px` 为 960 精确补集） |
| GAP-32 ★缩放 | ≤960px：AIHOT **侧栏整体换成底部 tab 栏**（`--m-tabbar-h:54px`，4 项：精选/全部/日报/更多）；我方仍是桌面布局 + 汉堡抽屉 | `[closed]`（≤960px 底部 tabbar 高 54px、恰好 4 项、8 条路由 href 精确、当前页高亮；`app-hamburger` 全树 0 命中；`/more` 200 且主区 href 恰为四入口白名单）；原处置：新建移动层；底栏“更多”直接指向独立 `/more`，该页只含微信文章解读、收藏、关于、更新日志四项；不复用 `#app-sidebar`，桌面侧栏不变 |
| GAP-33 ★缩放 | ≤960px：首页分类筛选由下划线 tab 换成药丸 chip，搜索折叠为固定放大镜并跳 `/all#search`；AIHOT `/all` 移动页仍保留完整搜索表单 | `[closed]`（首页分类改横向滚动药丸 chip（radius 999px / h36 / active 命中 `--m-chip-active-bg`）+ 搜索折叠为 44×44 图标跳 `/all#search`；`/all` 保留可输入完整搜索表单且未退化）；原处置：按页面分别实现，不把首页的折叠搜索规则错误套到 `/all` |
| GAP-34 ★缩放 | ≤960px：AIHOT 卡片**去边框全出血**，条目间用发丝线分隔；日期分组头变为浅灰吸顶条 | 新建 → `[closed]`（≤960px 卡片 `borderTopWidth:0px` 全出血、条目间发丝线、日期分组头浅灰吸顶） |
| GAP-35 ★缩放 | ≤960px：AIHOT 顶部为紧凑条（logo + 右侧日期）；我方为汉堡 + 居中 logo | 对齐 → `[closed]`（≤960px 顶栏改 `.app-mobile-bar` 紧凑条（logo + 右侧日期），汉堡已移除） |
| GAP-36 ★缩放 | ≤960px：AIHOT 推荐理由渲染为浅灰底块（`--m-daybar-bg` 系）；我方为带上分隔线的纯文本 | 对齐 → `[closed]`（推荐理由改 `--note-bg` 浅灰底块 + `--radius-sm` + `8px 10px` 两行钳制） |
| GAP-37 | AIHOT 有整套 `--m-*` 移动专用 token（约 30 个）；我方无 | 建立对应 token → `[closed]`（36 个 `--m-*` 对应 token 按 A 表 111–146 建立（既有语义直接复用、新建按建议名）） |
| GAP-38 | `--touch-target:44px` / `--touch-target-sm:36px`：AIHOT 保证移动端点击区；我方无此约束 | 建立并应用 → `[closed]`（`--touch-target:44px` / `--touch-target-sm:36px` 建立并实测应用——`.m-tab`/`.mobile-search-link` ≥44px、`.seg-item` ≥36px，无一不达标） |

### G. 日报页（Phase 4）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-39 | 我方为深色报纸风（深蓝底 + 绿色巨型报头 + 衬线体）；AIHOT 为浅色、与全站同一套 token | 整体改浅色并接入全站 token（`b7fdde76251cc8ef.css` 的 `.daily-shell` 已把 `--d-*` 全部映射到全局 token，可直接照此结构） → `[closed]`（`/daily` 改浅色并按 B.3 桥接结构接入全站 token；light 下 `.daily-shell` 底色实测 `rgb(255,255,255)`） |
| GAP-40 | AIHOT 左栏为**按月份分组、可折叠**的归档列表（月份行带条数，展开后列出该月每期），底部有“全部日报 →”；我方为单层日期列表，文字换行严重挤压 | `[closed]`（归档栏改按月份分组可折叠，实测 23 个 `<details>`；月份/期数/日期集合与归档 API 全集逐项相等（`test_daily_archive_endpoint_returns_one_complete_snapshot`））；原处置：重做归档栏；显示月份数、各月期数和日期集合必须与隔离数据/API 的可用日报全集逐项相等 |
| GAP-41 | AIHOT 正文顶部有“今日看点”摘要卡（分类编号 + 标题 + 每类条数 + “N 篇报道 · 约 M 分钟”）；我方无 | `[closed]`（「今日看点」落地；N=2 等于实际正文条数、各类计数 1+1=2；M 由我从渲染 DOM 独立只取 `.daily-article-summary`、只计指定 CJK range 得 C=211，`max(1,ceil(211/300))=1` 与页面显示一致；公式边界 0/1/300/301 有单测）；原处置：新增；`N` 等于实际渲染正文条目数，各类计数之和等于 N。阅读时长采用我方透明契约：`C` 为日报正文各摘要的可见中文字符数（仅 Unicode CJK Unified Ideographs：`U+3400–4DBF`、`U+4E00–9FFF`、`U+F900–FAFF`；排除标题、导航、元数据和章节标题），`M = max(1, ceil(C / 300))`，显示 `N 篇报道 · 约 M 分钟`。300 中文字符/分钟是我方自定的保守、整数、可审计常数；冻结样本只能观测到 1777 个摘要中文字符显示 10 分钟，不能识别 AIHOT 的计数输入或算法，因此不声称一致 |
| GAP-42 | AIHOT 章节头为 `01 行业动态 INDUSTRY ... 1 篇`（编号 + 中文名 + 英文小标 + 右侧计数）；我方编号巨大且与标题脱节 | 对齐 → `[closed]`（章节头改 `01 · 模型发布/更新 · MODEL RELEASES · 1 篇`） |
| GAP-43 | AIHOT 报头为 `AIHOT 日报` 常规字重 + 上方 `VOL.2026.08.02 · 2 STORIES · AI HOT DAILY` 小标 + 下方中文长日期；我方报头为超大衬线双色字 | `[closed]`（报头固定 `AI RADAR日报` + `AI RADAR DAILY`，无 AIHOT 字样；`VOL.2026.08.04` 与报头 `datetime` 一致且随有效日期动态；`N STORIES` 等于实际条数，另有 N≠2 fixture）；原处置：只对齐视觉结构，不复制第三方品牌或冻结样本值；主标题固定 `AI RADAR 日报`、英文标固定 `AI RADAR DAILY`，`VOL.<YYYY.MM.DD>` 取当前有效日报日期，`<N> STORIES` 的 N 取实际渲染正文条目数，并用 N≠2 fixture 验证动态语义 |
| GAP-44 | 我方页面顶部出现警示横幅“日期 2026-08-04 无效或无内容，已切到最近一期 2026-08-02” | `[closed]`（未来/非法日期改写 URL 并显示 `role=status`/`aria-live=polite` 弱状态（色值 `rgb(107,118,132)`=`--muted` 非警示），旧警示横幅 CSS 亦已删除；合法但空日期不回退、URL 不变、显式空状态）；原处置：非法、不可解析或未来日期仍回退最近一期并改写 URL，但在报头附近只显示一行非警示色、低强调度的内联状态文字（`role=status` / polite，包含实际回退日期）；有效但无内容的日期保留显式空状态，不回退 |
| GAP-45 | AIHOT 有 `日报 / 周报 / 月报` 三 tab | 我方无周月报数据 → `[accepted-divergence]`，不加 tab |

### H. 新增页面（Phase 4）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-46 | AIHOT 有 `/changelog`；我方无 | 新建路由 + 页面，渲染仓库根 `CHANGELOG.md`；视觉照 `52481b03cf298d21.css` 的 `.cl-*`（`max-width:880px`、`padding:56px 24px 80px`、eyebrow 用 `--font-mono` + `letter-spacing:.16em` + 大写） → `[closed]`（`/changelog` 200；独立解析 CHANGELOG.md 得 18 个日期头 + 44 条目，与 DOM 的 `.cl-day-date`/`.cl-li` 数量与逐项文本全等（规范化空白 + 实体反转义 + 去行内标记后）；侧栏「更新日志」当前页高亮） |
| GAP-47 | AIHOT 侧栏"更新日志"项带未读红点 | 不做（需未读状态管理）→ `[accepted-divergence]` |

### I. 补充对照项（Phase 1–3）

| ID | 差异 | 处置 |
|---|---|---|
| GAP-48 | ≤960px：AIHOT 分类 chip 为可横向滚动的单行，放大镜固定在右侧；我方元素被压进一行 | `[closed]`（390px `.seg-list` `overflow-x:auto` 且 scrollWidth 353 > clientWidth 302 真溢出可滚；搜索图标固定 44×44）；原处置：chip 容器横向滚动，搜索按钮固定且满足触控目标 |
| GAP-49 | ≤960px：AIHOT 筛选条上方是独立紧凑标题“最新精选”；我方仍渲染占据近半屏的完整页头卡片 | `[closed]`（移动页头改紧凑「最新精选」，实测高 87px（远小于 844 半屏））；原处置：移动页头改为紧凑 section 标题 |
| GAP-50 | ≤960px：AIHOT 热点标题为“今日热点”并使用紧凑布局，右侧有查看全部；我方为“当前热点” | `[closed]`（桌面「当前热点」/390px「今日热点」运行时两主题各自命中；紧凑布局与 24px rank 由 Phase 3 补齐）；原处置：对齐“今日热点”文案与紧凑布局，右侧“完整榜单 →”进入 `/hot` |
| GAP-51 | ≤960px：AIHOT 日期条显示相对日名（“今天 8月2日 周日”），桌面显示绝对日期与星期；我方两档均无完整星期语义 | `[closed]`（390px 用 `.mobile-date-label`（今天/昨天相对日名，其他回退绝对日名），桌面用 `.desktop-date-label` + meta 星期；以 Asia/Shanghai 判日界，Playwright `test_parity_mobile_date_labels_use_today_yesterday_and_absolute_fallback` 覆盖）；原处置：移动档增加今天/昨天相对日名，桌面档增加星期；以 Asia/Shanghai 判日界 |
| GAP-52 | AIHOT 同时维护 `data-theme`、`data-theme-mode` 并动态更新 `<meta name="theme-color">`；我方只设 `data-theme` | `[closed]`（`data-theme` + `data-theme-mode` + `meta[theme-color]` 在 6 路由 × light/dark/system 全绿；prepaint 在 app init 前即设好三项）；原处置：补齐主题模式属性与实际主题对应的浏览器主题色，并纳入 system/刷新测试 |
| GAP-53 | `/all` 的 AIHOT 筛选为一行分类 tab + 右侧“来源: 全部”下拉；我方为信源、分类两行堆叠 tab | `[closed]`（`/all` 信源维度收进 `#channel-param` 下拉、只留一行分类 tab，筛选能力不减且 URL 状态可回溯）；原处置：信源维度收进下拉，只保留一行分类 tab；保留全部既有筛选能力 |
| GAP-54 | AIHOT 卡片可内联呈现媒体，并对视频/图库显示播放或计数角标；我方已有图片数据与渲染链路，但无结构化视频/图库元数据 | **拆分处置**：`[closed]` 现有图片媒体的几何/裁切/间距已对齐，打开大图 affordance 落地为 `.article-media-link`（`aria-label="打开大图：<标题>"`，新标签页打开原图，`onerror` 隐藏破图）；同轮发现 UX 契约 HP-7 旧文写「点击图片打开原文链接」与本处置及同文件卡片描述自相矛盾，已按 plan 批准的本处置更正契约（journal 16:05 MEDIUM-3）。`[accepted-divergence]` 不仿造视频播放角标或图库计数，因为 `summary.py`/API 只暴露 `media_assets` 图片、`app.js` 已消费该链路，隔离 DB 37,093 条中 20,829 条 `content_html` 含 `<img>`，而 schema/API 无结构化视频或 gallery-count 字段。补齐后两者会扩大到媒体抽取产品范围 |
| GAP-55 | AIHOT 对结构化引用内容使用浅色描边块；我方没有对应的 X 引用字段或抽取路径 | `[accepted-divergence]`：不新增 X 引用抽取产品功能；现有 WeChat HTML `<blockquote>` 与摘要块仍须继承本轮全局 token 并在微信详情视觉矩阵中验收 |
| GAP-56 | AIHOT `/hot` 每行有 rank、标题、主信源、相对时间、热度值和可展开信源名单；我方现有 hot payload 只暴露 `id/title/url/source_name/heat` | `[closed]`（`/api/v1/hot` 新增响应级 `generated_at` 与逐条 6 字段；`event_time` 回退在真实数据触发（top-1 `published_at=2026-08-04` 晚于 `generated_at` → 落 `fetched_at=2026-08-02T23:16:04`）；`<details>` 数 = related 非空数（当前 0，未伪造））；原处置：保持现有单次一致快照与排序公式；扩展 `/api/v1/hot` 响应级 `generated_at`，item 带出 `published_at`、`fetched_at`、`event_time`、`source_kind`、`author`、`related_discussions`。`event_time` 取可解析且不晚于 `generated_at` 的 `published_at`，否则回退 `fetched_at`；页面只基于它算相对时间。信源展开只列主信源 + 实际 related，去重且不伪造。只读样本依据：top-5 全部 related 为空；最近 100 条中 4 条有 related、样本最大 1，底层函数上限 3；top-1 的 `published_at=2026-08-04` 晚于响应日而 `fetched_at=2026-08-02`，故时间 fallback 必须可验收 |
| GAP-57 | AIHOT `/hot` 有“爆/新/发酵中”状态、24 小时趋势线、“另有 N 组氛围票”和站内聚合 story；我方无对应语义或数据模型 | `[accepted-divergence]`：不从时间/热度臆造状态，不伪造趋势或氛围票，不新增 story 聚合页；标题继续打开原文。依据：现有 hot/archive schema 没有 status、heat time-series、atmosphere-vote、story id/cluster，只能提供当前 heat 与最多 3 条 related discussions |

### J. 关系层与结构层差异（Phase 6 —— r4 之后由 live 成对测量发现）

来源：用户 2026-08-04 对着 live AIHOT 亲眼发现两点，加上 `probe.py` 成对测量补出的第三点。全部是**关系层/结构层**差异，全部落在 r4 已通过的值层检查覆盖之外。测量条件：`probe.py` 2026-08-04，Chromium + 真实 Chrome UA + `--no-proxy-server`，light 主题，两站同一次运行内同 viewport。

| ID | 差异 | 处置 |
|---|---|---|
| GAP-58 ★用户点名 | **桌面日期分组头与时间戳不共右边界**。AIHOT `.timeline-day-head` 是 `display:grid; grid-template-columns:64px 22px 1fr`，与 `.timeline-item` **同一套轨道**，日期 `h2.timeline-date` 落在 64px 时间轨内并**右对齐**——日期字形右边界 272 = 时间字形右边界 272，`dR−tR = 0`。我方 `.timeline-day-head` 是 `display:flex; padding-left:86px`，把日期推到卡片左缘 344 并左对齐，时间字形右边界 322 → **错开 68.7px**，在 961–1440 全部 6 档恒定。根因：AIHOT 这条 base 规则从未被抄进 `measured-tokens.md`（该文件只记了它的 ≤640px 覆盖），属反向完备性漏洞 | `[closed]`（`dR−tR` 68.7 → **0**，961–1440 全 6 档；`r7-verify` 全 7 档逐档与 AIHOT 精确一致） → 改 `.timeline-day-head` 为与 `.timeline-item` 同轨道的 grid，日期右对齐进时间轨。须保留我方独有的折叠按钮与星期 meta（AIHOT 第 3 轨亦承载 meta），不得为对齐删功能 |
| GAP-59 ★用户点名 | **≤960px 内容列比 AIHOT 窄 36px**。AIHOT 把 `max-width:640px; margin:0 auto` 放在**内层** `section.m-feed`，`padding:0 18px` 放在**外层** `main.app-main`，于是 feed 净宽 640；我方把 `max-width:640px` 与 `padding:0 18px` 放在**同一个** `main.app-main.home-page` 上，padding 从 640 内部扣除 → 净宽 **604**。实测 960 档：AIHOT 墨迹 640（留白 160/160），我方 604（178/178）；720 档：AIHOT 640（40/40），我方 604（58/58）。两边各自左右对称，差的是列宽本身 | `[closed]`（960 档 604/178/178 → **640/160/160**；720 档 → **640/40/40**；641/640 档未改坏） → 把移动档的 `max-width` 与横向 padding 拆到不同层，使净内容宽在 ≤960 达到 640 |
| GAP-60 | **桌面内容列比 AIHOT 窄 100px**（成对测量新发现，用户未点名）。AIHOT `main.app-main` 无 `max-width`，填满视口减侧栏；我方 `main.app-main{max-width:1160px}`。1440 档：AIHOT 内容 1204（`.timeline-item` 轨道 `64px 22px 1118px`，右留白 45），我方 1104（`64px 22px 1018px`，右留白 91）。≤1200 两站收敛（948 vs 952），差异只在宽视口出现 | `[closed]`（home @1440 1091/258/91 → **1191/208/41**，AIHOT 1187/208/45；`/hot` 与 `/changelog` 未改坏，`/daily` 精确对齐 1102/204/134） → 去掉我方桌面 `max-width` 上限，与 AIHOT 同为填满可用宽。理由：用户目标是 1-1 复刻、除非有明确理由必须不一致；1160px 上限是我方自定的可读性取舍，不属"我方独有功能"这一豁免类别 |

| GAP-61 | **`/changelog` 在 ≤960 布局塌掉**（成对测量新发现，用户未点名）。`main.app-main.cl-page` 是 `display:flex` 且 `flex-direction:row`。>960 时 `.app-mobile-bar` 为 `display:none`，flex 行里只剩 `.cl-shell`，桌面看不出问题；**≤960 时 mobile bar 变可见**，与 `.cl-shell` 并排挤在同一行。实测 960 档：`.app-mobile-bar` L=160 W=162、`.cl-shell` L=304 W=**478**（应为 L=178 W=604），正文比 AIHOT 窄 400px 且左右留白 328/202 不对称 126px；390 档正文右留白为 **0**（贴死边缘）。其余 7 页在 960 档均对称（首页 `flex-direction:column`，其余 `display:block`），只有 changelog 是 row | `[closed]`（960 档 429.9/328/202.1 → **832/64/64**，AIHOT 830/64/66；390 档 → 322/34/34 精确一致） → 让 changelog 的 mobile bar 不再与正文同处一个 flex 行。目标：960 档对齐 AIHOT 的 830/64/66 量级且 asym ≤ 4，390 档对齐 322/34/34；1440 档现已一致（831.9/394/214.1 vs 830/394/216），不得改坏 |

**本轮成对测量记录的候选项（证据尚不足以定性，Phase 6.3 后再判）**：

| 观察 | 实测 | 状态 |
|---|---|---|
| `/hot` 在 961–1200 我方比 AIHOT 宽约 100px | 1201 档：AIHOT 896/232/73，我方 1000/180/21；1440 档两站基本一致（1106.4 vs 1099）。**AIHOT 容器规格已实测定位**（见下） | 待修，排入下一轮 |
| ~~`/hot` 在 ≤960 两站容器不对称~~ **已澄清，不是缺陷** | AIHOT 960 档 `.hot-page` 实测 L=100 W=760 R=100，**容器完全对称**；墨迹的 100/114 是**文字没顶到右边缘**造成的，非布局问题 | 撤销——此项为测量口径误判，不是 AIHOT 的差异 |

**AIHOT `/hot` 容器规格（2026-08-04 实测，供下一轮使用）**：`main.app-main` 为 `display:grid`、无 `max-width`、padding 28（>960）/ 18（≤960）；`div.hot-page` 宽度 = `min(1120px, 主区内容宽 − 48px)`（1440 档 → 1120，L=250 R=70；1201 档 → 917，L=232 R=52），≤960 档收为 **760 居中**（L=100 R=100）；`header.hot-hero` `max-width:760px`，其 `<p>` `max-width:680px`。
| 桌面 961–1200 我方比 AIHOT 宽 4–9px | 例 1201 档 home：AIHOT 948/208/45，我方 952/208/41 | 低优先——量级 4px，用户不可辨；不单独派修 |

> **handoff 中一条继承判断已被 live 实测更正**：`handoffs/aihot-parity-live-comparison-handoff-20260804.md` §3.2 正文称"本站 `.timeline-item` 在所有宽度都是 `64px 22px 1fr`、保留 86px 时间+轨道列"。实测 ≤960 我方为 `40px 564px`、AIHOT `a.m-row` 为 flex 且首个 flex 子元素 `.m-row-time` 宽 40px——**两站在该档的时间列形态已经一致**，该 handoff 段落自带的表格（218 = 178 + 40）亦支持 40px 而非 86px。因此"移动层需要第二棵 DOM 树 / ADR-012 前提被证伪"这一推论**不成立**，ADR-012 不需因此改写。GAP-59 的真因是 max-width 与 padding 的归属层级，与 DOM 树数量无关。

### K. 反向完备性枚举发现的缺项（Phase 6.2 产出，2026-08-04）

来源：Codex 只读枚举，产物 `plans/20260803-aihot-visual-parity/aihot-rule-enumeration.md`（1039 行）。口径：7 个 bundle 共 2006 条 qualified rules，按「页面实际加载该 bundle 且 selector 在冻结 DOM 命中」筛出范围内 **953 条物理规则**（去重后 728 条逻辑规则）。结果：`MATCHED` 752 / `DIVERGENT` 24 / **`MISSING` 78**（其中结构类 35）/ `N-A` 99。

**这批 MISSING 正是 r4 全绿的原因**：它们在我方 CSS 里**连一条对应声明都没有**，所以「我方每个值都有出处」的方向 1 检查遍历不到；它们也**没进 `measured-tokens.md`**，所以方向 2 检查同样遍历不到。

78 条物理规则收敛为下列特性。supervisor 已抽查 `.cl-meta`/`.cl-day-head`/`.daily-metrics`/`.theme-toggle-thumb`/`.cl-kind`/`.timeline-item-starred` 在我方 CSS **与**模板中均 0 命中，枚举可信。

**K-1 纯视觉复刻，无需新数据（可直接实施）**

| ID | 缺项 | 用户可见后果 |
|---|---|---|
| GAP-62 ★ | `.timeline-day-items::before` 与其 `position:relative` 包含块 | AIHOT 用**一条贯穿整个日期分组的连续 1px 竖线**（`left:calc(--tl-time-w + --tl-rail-w/2)`、`top:6px`、`bottom:6px`）；我方用**逐条目**的 `.timeline-rail` 元素，在 `gap:22px` 的桌面档条目之间会断开 → 线是断续的而非通的。疑似对应用户最初点名的「网页上的横线」 |
| GAP-63 | `::-webkit-scrollbar` 全站 6px 定制滚动条（4 条规则） | 我方只在 `.seg-list`（隐藏）与 `.daily-side`（6px）局部有，全站滚动 chrome 不同 |
| GAP-64 | `.theme-toggle-thumb`（含三个 `data-pos` 态） | AIHOT 三档切换器有跨档滑动的选中底板；我方只靠单个按钮底色表示当前档 |
| GAP-65 | `.timeline-item-starred` | 我方**有收藏功能**，但收藏后时间线轨道不切琥珀强调色，状态只在按钮内可见 |
| GAP-66 | `.feed-channel-select-icon` / `-active` / hover 组合 | 来源下拉缺右侧固定 14px 箭头；选中非「全部」时缺青色 active 强调 |
| GAP-67 | `.hot-topics-index::after` 悬停桥 | 8px 悬停桥缺失 → 鼠标从热度值移向 tooltip 气泡时会闪退。这是**可用性缺陷**不只是视觉 |
| GAP-68 | `.btn:active` / `.btn:disabled` / `.btn:disabled:hover` / `.field:disabled` | 按钮缺按下 0.98 缩放反馈；筛选/翻页按钮与输入控件缺统一 disabled 视觉与 hover 抑制 |
| GAP-69 | `touch-action:manipulation` + tap highlight（`:where(a,button,input,…)` 与 `[role=button|radio|switch]`） | 移动端可点击控件缺统一触控反馈 |
| GAP-70 | `.tag::before` | 标签缺 `#` 前缀，标签识别形态不同 |
| GAP-71 | `.md-inline-code` | 日报摘要行内代码缺等宽字体 + 浅底 + code pill |
| GAP-72 | `.cl-day-head` / `.cl-day-weekday` / `.cl-tag` | 更新日志日期头缺日期/星期关系布局与弱化星期标签；标题下缺引导语（导致首个日期组提前约 64px） |

> ~~K-1 全部 11 条（GAP-62~72）当前状态均为 `[open]`。~~ **已全部闭合**（见 `state.md` 的「K-1 十二组已闭合」条，Codex `019fcb12`，逐条记录了处置）。此表无状态列，故各条状态由本行承载——修改闭合情况时必须同步这里，否则 L2-1 / L2-7 的「无 `[open]`」判据会被一句过期陈述判为不成立。

**K-2 需要我方没有的数据或产品语义（须用户拍板，不得擅自实施或擅自记为 accepted-divergence）**

| ID | 缺项 | 阻塞点 |
|---|---|---|
| ~~GAP-73~~ | `.cl-meta` / `.cl-meta-time` / `.cl-kind*`——更新日志 110px 元信息列 + 变更类型彩色圆点 | **2026-08-04 用户裁决：不做，记为 `[accepted-divergence]`。** 理由：我方 changelog 是「每次发布一段综述」的内容形态，一段长散文旁挂一个空的 110px 元信息列比不挂更差；且 AIHOT 每条带时刻（实测 `02:15`/`19:39`）与类型，我方 `CHANGELOG.md` 只有日期无时刻。用户未采纳「改写 CHANGELOG 写作契约后完整复刻」。日期头与引导语（GAP-72）已按 AIHOT 对齐，该部分视觉收益已取得 |
| GAP-74 | `.daily-metrics` / `.daily-metric*`（10 条规则）——日报底部四项数据概览 | **2026-08-04 重新定性：不需要用户拍板，四项我方全都算得出。** 先前判定「一手报道无对应字段」有误——`src/airadar/web/routes/timeline.py:211` 的 `firstParty` 已定义为 `kind != 'x' AND tier = 'T1'`（非 X 的一级信源），可承担该语义。四项映射：今日事件 = 正文条数；一手报道 = 该期中 firstParty 条数；新模型 = 「模型发布」分类条数；信源 = 去重信源数。沿用 GAP-41 阅读时长的既有先例——**用我方自己的透明定义并写明，不声称与 AIHOT 口径一致** → `[closed]`（四项由独立重算核对；桌面 1×4 / ≤960 2×2 实测；空期容器隐藏；已补 DY-1 的 L2 条件），正常实施 |
| ~~GAP-75~~ | `.feed-skel*` / `.daily-skel*` / `.skel-delayed`——加载骨架 | **2026-08-04 重新定性，移出 K-2，不再需要用户拍板。** 冻结 HTML 实测：AIHOT `home.html` **同时**含骨架标记（36 处 `feed-skel`）与真实 SSR 内容（444 处 `m-row`），且 `.skel-delayed{opacity:0;animation:… .3s forwards}` 让骨架**前 300ms 完全隐藏**——即 AIHOT 的骨架服务于慢加载/客户端导航，**不是首屏**。我方同样 SSR 真实内容（`PREPAINT_ITEM_LIMIT = 12`）。故真正的问题不是"要不要加骨架"，而是**我方 12 条 prepaint → CSR 完整首批的替换是否造成可见跳变**——这是 supervisor 可测的量，不占用用户判断。改列为 GAP-79 |

| GAP-79 | SSR prepaint → CSR 首批替换是否造成可见跳变 | **`[closed]`（2026-08-04，用户裁决「改，但带性能护栏」）。** `PREPAINT_ITEM_LIMIT` 12 → **40**（与 CSR 首批一致）。CLS 实测 @1440 **0.0709 → 0**（位移次数 2 → 0），卡片 40 → 40 不再替换，页高 12026 恒定；@390 保持 0。**性能护栏实测**（`performance-probe --origin-url http://127.0.0.1:8011 --public-url ""`，样本/状态落临时目录不污染真实告警态；预算取 `journey_monitor.py` 的 `homepage.first_card` 2000/3000ms）：PREPAINT=12 → P75 **595** / P95 **1350**；PREPAINT=40 → P75 **949** / P95 **1161**（各 n=10，全部 `load_class=idle`、`hard_failure=false`）。即 **P75 +354ms、P95 −189ms** —— 中位延迟涨（HTML 更大），**尾延迟反而降**（不再需要 CSR 替换那一轮）。两项均大幅在预算内（P75 用到 47%），故按裁决**保留改动、不回退** |---|---|---|
| GAP-76 | `/hot` 在 ≤960 内容列 640 vs AIHOT 760 | Phase 6.1 为避免 GAP-59 的改动把 `/hot` 一并撑宽，给 `.app-main.hot-page` 显式加了 `max-width:640px`，**锁定了现状**。AIHOT 同档 `.hot-page` 实测 L=100 W=**760** R=100。这是一处**知情保留**的差异，不是已闭合项 → `[closed]`（`/hot` 三档容器按 AIHOT 规格对齐） |
| GAP-77 | GAP-58 的 chevron 落在 rail 轨，与 GAP-62 将要新增的竖线位置重合 | GAP-58 的解法把 chevron 放进 22px rail 轨并 `justify-self:center`；GAP-62 的日期分组竖线画在 `left: calc(--tl-time-w + --tl-rail-w/2)`，正是该轨中心。两者会**在日期头处重叠**。AIHOT 无此问题（它的日期头没有 chevron）。实施 GAP-62 时必须一并处理 → `[closed]`（几何实测 chevron 底边 ≤ 竖线顶边、二者不相交——竖线画在 `.timeline-day-items`，日期头是其兄弟节点，故无需处置；给出证据而非断言） |
| **GAP-78** ★★ | **抄了 AIHOT 死代码：其 ≤960 的 `.timeline-*` 规则作用于隐藏子树，在我方单树架构里变成可见样式** | 见下方专节。**这是用户点名的「缩放时候的排版、字体」的一个直接来源** → `[closed]`（641–960 时间戳 16px → **12px** 并整条对齐 AIHOT 可见的 `.m-row-time`；热点模块两条同源死值一并修正；`measured-tokens.md` 补 15 处可见性标注；微信页用 `.wechat-timeline` 隔离未被扩散） |
| GAP-80 | **移动顶栏滚动行为与高度不一致**（成对测量新发现，行为层） | AIHOT `.m-topbar` 是 `position:static`、高 **45px**，滚动 600px 后**随内容滚走**（rectTop = −600）；我方 `.app-mobile-bar` 是 `position:sticky; top:0; z-index:30`、高 **52px**，滚动后**钉在顶部**（rectTop = 0）。底部 tab 栏两站均 `fixed`（一致）。这条只在**滚动之后**才可见——静态截图矩阵、逐值审计、rubric 均抓不到 → `[closed]`（移动顶栏 `sticky`→`static`、52px→45px，滚动 600px 后随内容离开视口；底栏仍 `fixed`/54px 未动） |
| — | `/more` 的 `width: min(640px,100%)` 是失效声明 | 被更高优先级规则覆盖，桌面档该页实际 1160 宽（computed `width:1160px`）。属既有行为、本轮未改动，且该页无桌面入口 → 观察项，不列 GAP |

#### GAP-78 详述：单树架构下「忠实抄录」会把参照站的死代码变成我方的可见缺陷

**机制**。AIHOT 在 ≤960 时 `.feed-desktop{display:none}`，整棵 `.timeline-*` 子树隐藏，移动端改用另一棵 `.m-*` 树（`.m-feed` / `.m-row` / `.m-row-time`）。但 AIHOT 的 CSS 里**仍留着 34 条 `.timeline-*` 规则写在 `≤640` / `≤960` 媒体块内**——在 AIHOT 那边它们全部作用于隐藏子树，是引入 `.m-feed` 之前的**遗留死代码**，一个像素都不渲染。

我方按 ADR-012 只用**一棵 DOM 树**，`.timeline-*` 就是我方在 ≤960 实际可见的 UI。因此每抄一条 AIHOT 的死规则，就把它从「不渲染」变成「我方可见样式」。

**已确认的实例**。`measured-tokens C.1 M03` 记录了 AIHOT 的 `@media(max-width:960px){.timeline-time{font-size:16px}}`，我方照抄。实测 641–960 档：

| | 时间戳字号 | 字形宽 |
|---|---|---|
| AIHOT 可见值（`.m-row-time`） | **12px** | 36.1 |
| 我方（`.timeline-time`） | **16px** | 48.2 |

大 33%。而 390 档（两站均 12px）与 1440 档（两站均 12.5px）都一致——**只有中间这一带错**，而这一带正是 1440 窗口按 `⌘-` 缩到 150–200% 时落入的区间，即用户点名的「缩放时候的排版、字体」。16px 恰好也是浏览器默认字号，容易被误读为"没匹配到规则"，实则是抄来的显式值。

**这是一类缺陷不是一个**。同批可疑的还有 `.timeline-date{font-size:13px}`、`.timeline-card{padding:10px 12px}`、`.timeline-title{font-size:14px}`、`.timeline{--tl-dot-top:16px;gap:14px}`（我方以 `measured-tokens B.0` 名义抄了 `--tl-dot-top:16px`）等。

**r4 的忠实度审计放大了它**。handoff §4.3 记录该轮把 `--tl-time-w/rail-w/dot-top` 中档"修正"为 `64/22/16`、把 `.timeline-time` 从 `12px/1/6px` "改回" `12.5px/1.1/0`——正是朝这些死规则的方向修正。**审计越忠实，可见缺陷越多。**

**为什么既有检查全都看不见**。逐值溯源问"我方这条值有没有出处"——有，出处就是 AIHOT 那条真实存在的规则；ledger 忠实度问"清单这行是否忠于 AIHOT"——忠实。两个方向都通过。**没有任何一个方向问「AIHOT 这条规则在 AIHOT 上到底渲染不渲染」。**

**处置**。逐条审计 AIHOT 那 34 条 `.timeline-*` 移动规则：确认它在 AIHOT 是否作用于隐藏子树；若是，我方不得照抄，应改为对齐 AIHOT **可见**的 `.m-*` 对应件，或在无对应件时明确记为不适用。`measured-tokens.md` 相应条目须加可见性标注。

**顺带更正 GAP-62 的作用域**：AIHOT 的 `.timeline-day-items:before` 只出现在 BASE 与 `@media(max-width:640px)`，两处都落在 ≤960 隐藏的树上——那条连续竖线**只在 >960 桌面渲染**。AIHOT 可见的移动 feed 用 `.m-row-wrap{border-bottom:1px solid var(--m-border)}` 横向发丝线分隔、**没有竖线**（我方 ≤960 已是 border-bottom，本就一致）。故 GAP-62 只做桌面档。

### L. 亲眼对比（D8′ 第 4 条）发现的差异，2026-08-04

supervisor 用 `eyeball.py` 抓两站同条件成对截图并**逐屏看过**（home × 5 缩放档 × light）。下列各条**全部落在**关系层探针、逐值审计、规则枚举与 85 条 Playwright 的覆盖之外——它们只能靠看发现，随后逐条用运行时测量证实。

| ID | 差异 | 实测证据 |
|---|---|---|
| GAP-81 | **搜索按钮：填充 vs 描边** | AIHOT `bg rgb(19,94,107)` / 白字 / 宽 74（实心主按钮）；我方 `bg rgb(255,255,255)` / 青字 / `border rgb(216,219,223)` / 宽 52（描边幽灵按钮） → `[closed]`（搜索按钮改实心主按钮，computed 背景/前景与 AIHOT 同档一致） |
| GAP-82 | **侧栏图标：真 SVG vs CSS 伪元素拼图** | AIHOT 每个导航项含带真实路径的 `<svg>`（书签、时钟、文档等）；我方 `svg=False`，`.side-icon` 是空的 16×16 盒子，用 `::before`/`::after` 拿边框+圆点粗略拼近似形状。**规则枚举把 `.side-icon` 判为 `MATCHED`**——因为两站的 CSS 规则（`width:16px;flex-shrink:0`）确实相同，枚举比对的是选择器、对 DOM 内容差异是盲的 → `[closed]`（侧栏与移动底栏图标换为真 inline SVG，用 MIT 图标集、未从 AIHOT bundle 复制 path；`currentColor` 继承使选中态变色仍生效） |
| GAP-83 | **移动端推荐理由被截断** | AIHOT `.m-row-reason-block`：`overflow:visible`、无 clamp、`scrollH == clientH == 55`，**完整显示**；我方 `.reason`：`-webkit-line-clamp:2` + `overflow:hidden`，内容实际 **74px 被压进 55px**（`OVERFLOWING: true`）。行高亦不同（19.375 vs 21px） → `[closed]`（移动端推荐理由去掉 `-webkit-line-clamp`，`scrollHeight == clientHeight` 无溢出；行高对齐 21px） |
| GAP-84 | **首页是否显示标签** | AIHOT 的 `.tag` 只在 `/all` 渲染（实测 108 个可见），`/`、`/hot`、`/daily` 均为 **0**；我方首页与 `/all` 都显示。注意这条**不影响 GAP-70 的合法性**——AIHOT 确实渲染带 `#` 的 `.tag`，只是页面范围不同 → `[closed]`（首页 `.tag` 实测 0，`/all` 保留；未改数据与 API），属信息密度取舍 |
| GAP-85 | **移动行首信息密度** | AIHOT 移动行首为「时间 + 信源」在左、**分数在同一行右端**，不显示精选 badge、收藏按钮、标签；我方同一行塞入 时间 + 信源 + `✦精选` + `●分数` + 收藏图标 + 下方标签行，信源名被截断为 `OpenAI Blog: 官网动...` → `[closed]`（390 档行首只余 时间+信源（左）/ 分数（右）；badge/收藏/标签/头像该档均 0；1440 档全部保留），属信息密度取舍 |
| GAP-86 | **移动端日期头有折叠控件** | AIHOT 移动档 `.timeline-day-chevron` 计 **0**、可见 `[aria-expanded]` **0**（桌面有 5 个）；我方移动档 chevron **5** 个 → `[closed]`（390 档 chevron / `[aria-expanded]` / 可聚焦日期头元素全 0 且标签为 `DIV`；1440 档各 5 个且为 `BUTTON`），属交互取舍 |

### M. gate 的「不可比」信号追出的差异，2026-08-04

`probe.py --gate` 在第二批交付后报 8 处 `NOT COMPARABLE`（`/all` 与 `/daily` 的 ≤960 日期元素）。**没有加白名单，而是逐条查根因**——结果它们不是仪器局限，是三条真差异。

**方法论收获**：当两站语义等价的元素落在**结构不同的位置**上，那往往**就是**差异本身。所以「不可比」信号本身是缺陷探测器，把它 allowlist 掉等于让这类差异永久隐形。

| ID | 差异 | 实测 |
|---|---|---|
| GAP-87 | **移动顶栏日期的页面范围** | AIHOT 的移动顶栏**只在 `/` 存在**；`/all`、`/hot`、`/daily`、`/changelog` 在 390 档**完全没有顶栏元素**。我方**五个页面都有**顶栏且都带 `8月4日 · 周二` → `[closed]`（`/`有顶栏，`/all`、`/hot`、`/daily`、`/changelog` 该档无顶栏。**注意**：`/more`、`/bookmarks`、`/about`、`/wechat` 仍有，另立 GAP-90） |
| GAP-88 | **移动日期条缺少两段层次** | AIHOT 是两段构成：`.m-daybar-main`「今天」**13.5px / 字重 900** + `.m-daybar-sub`「8月4日 周二」**11.5px / 字重 700**。我方是单段 `.mobile-date-label`「今天 8月4日 周二」**13px / 700**，扁平无层次 → `[closed]`（日期条拆两段，主/次段字号字重命中 AIHOT 的 13.5px/900 与 11.5px/700；今天/昨天/更早三种日期各验一次） |
| GAP-89 | **`/changelog` 日期文案格式** | AIHOT `.cl-day-date` = `2026 年 8 月 4 日`（中文长格式带空格）、`.cl-day-weekday` = `周二`；我方 = `2026-08-03`（ISO 格式）、`星期一`。字号两站已一致（19px/600 与 12px/400），差的是**文案格式与星期措辞** → `[closed]`（`2026 年 8 月 3 日` + `周一` 已在重启后正式路由验证；`<time datetime>` 保留 ISO 原值，与源文件一致性契约不受影响） |

| GAP-90 | **非 feed 页在移动档仍用 feed 的「品牌 + 日期」紧凑条，AIHOT 用的是页面标题头** | 实测 390 档：我方 `/more`、`/bookmarks`、`/about`、`/wechat` 各有 1 个 `.app-mobile-bar`（品牌 + 日期）；五个 AIHOT 对照页已正确（只有 `/` 有）。AIHOT 同档：`/about` 是 `header.about-hero`、`/more` 是 `header.m-pagehead`（内容为「更多」页面标题），**不是品牌+日期条**；`/bookmarks` 在 AIHOT 是 404（无对应页）。由 docs 审查指出我把「顶栏只在首页」写成了全站绝对断言而实际只验了 5 页 → `[closed]`（2026-08-04：`/more` `/bookmarks` `/about` `/wechat` 的移动顶栏改为各自的页面标题头——实测 390 档这四页顶栏计数 1 → **0**，且各有标题头「更多」/「收藏」/「关于这个站」/「微信文章解读」；`/` 仍为 1。底部 tab 栏在这些页面仍可用） |

> **GAP-84/85/86：2026-08-04 用户裁决「全部按 AIHOT 复刻」。** 用户未选「只做视觉密度、保留移动端折叠」，也未选「保持现状」。执行边界：收藏**功能本身不删**，只是入口从移动行首收起；这些元素在桌面档**全部保留**，只在移动端与首页收敛。

**DIVERGENT 24 条**中几何影响最大的：`.cl-entry`（AIHOT `110px 1fr` 两列 vs 我方单列，与 GAP-73 同根）、`.app-main`（AIHOT grid + `gap:12px` + `padding:24px 28px 72px`）、`.timeline-day`（AIHOT `display:grid; gap:10px`，我方缺同层间距关系）、`.timeline-day-items`（AIHOT `position:relative`，与 GAP-62 同根）、`.sidebar` / `.side-nav` / `.theme-toggle` 的容器机制差异。逐条见枚举产物。

> Phase 5 收敛期若发现新差异，从 GAP-58 起追加，不改既有编号。

---

## L3 — 分阶段设计与内部 verify

**执行模型**：supervisor（Claude 主线程）负责需求解释、gap 判定、服务生命周期、验收；实施委派给 Codex session（`codeagent-wrapper --backend codex`，workdir 为 worktree）。

**对所有 Codex 委派 prompt 的强制约束**（每次委派都必须带）：

1. **禁止启动、重启、停止任何服务或绑定任何端口**。应用与报告服务都由 supervisor 独占。需要看渲染结果时只连接现有 `http://127.0.0.1:8011`；`capture.py` 只能作为客户端，绝不能自行拉起服务。
2. **禁止使用 `rm`**——Codex exec policy 硬拦截 `rm` 类命令，含 `rm` 的整段复合命令会被整体拒绝；被拒后换等价形态重构，不原样重试。临时产物留系统临时目录自然回收。
3. **禁止把 AIHOT 的 CSS 规则原样粘贴进 `web/static/style.css`**——只使用 `measured-tokens.md` 的测得值，用我方 token/DOM selector 词汇重写；返回的每个 GAP 必须引用该文件的 token/组件规则位置。
4. **只改 worktree 内文件**，不碰主 checkout（`/Users/lindong/research/ai-radar/web/`、`/src/`）。`reference/` 与 `handoffs/` 是 symlink 到主 checkout 的**只读**参考。
5. 返回结构化摘要：改了哪些文件、每个 GAP 的处置与测得值出处、跑过的命令与退出码、未验证的边界。

### Supervisor 服务生命周期契约（A2）

1. 任何启停前后都断言 8000 无监听；若非空，记录 `lsof` 的 PID/命令/工作目录并立即停止本 plan，不终止未知进程，交回 root/user 处理。
2. 8011 与 8012 都由 supervisor 独占。操作前以 PID、完整命令和 cwd 三项核验所有权；只终止匹配本任务身份的已知进程，禁止按端口盲杀。
3. `src/airadar/web/app.py`、`src/airadar/web/routes/curated.py` 或模板路由变更后，supervisor 重启已核验的 8011 进程（当前服务无 reload），随后断言现有真实健康端点 `/api/v1/healthz`、新增 `/hot`、`/changelog`、`/more` 均为 200，且 8000 仍为空；Codex 只在重启后继续客户端验证。
4. L2-6 前由 supervisor 在固定 `0.0.0.0:8012` 启报告服务；解析本次实际可达 host，本地地址与最终交付的同 host 地址均 preflight 后才能发给用户，终审结束再按同一身份核验停止。所有动作及前后断言写入 `journal.md`。

### Phase 0 — 基础设施与测得值提取

| # | 工作 | 内部 verify |
|---|---|---|
| 0.0 | **supervisor 独占、所有 UI 实施的硬前置**：校验输入表已冻结的两份 `/hot` CSS 大小与 SHA-256；从这些本地资产把 `.hot-*` 组件/响应式测得值追加进 `measured-tokens.md`。该步骤无实时 AIHOT 依赖；校验不符视为冻结输入被改动，停止并恢复权威资产，不允许联网补漂移版本或截图猜值 | 两份本地资产大小与输入表哈希精确相等；`cdf657…` 可枚举 31 个唯一 `.hot-*` selector / 3 个 media block，每项可从 measured mapping 回溯原 CSS |
| 0.1 | **supervisor 独占**：`reference/` 是指向主 checkout 的只读参考，Codex 不得写；由 supervisor 把 `capture.py` 改为 L2-1 的固定 manifest、硬失败和有界并发模型，`OURS` 已为 `http://127.0.0.1:8011`，不得改回；标准模式只采 ours 并从完整冻结 `r0-baseline` 组装 AIHOT 面板；用冻结 `all.html` / `hot.html` / `changelog.html` + 各自本地 CSS bundle 离线 `set_content`/注入样式、明确设置 light/dark 状态并阻断全部网络，分别补齐 `all`/`changelog` dark 5 档与 `hot` light/dark 5 档，live AIHOT 另设显式 diagnostic-only 入口；在任何 UI 改动前补采完整 before，并生成 self-contained 报告与逐格 rubric | 标准命令在网络层断言没有 AIHOT 请求；离线生成的 20 张 frame（`all` dark 5 + `changelog` dark 5 + `hot` light/dark 10）均有 DOM/CSS/theme provenance，AIHOT 配对 manifest 达到 5 页 × 5 viewport × 2 theme，无重复/空图；preflight 预期 URL、真实微信 slug与冻结基线；故意制造一个缺 frame 时必须非零；完整跑一次后 manifest 无缺项、报告本地资源无 404 |
| 0.2 | 只扩展现有 `tests/playwright/`：fixture 支持 `AI_RADAR_PLAYWRIGHT_BASE_URL` 外部服务模式；新增 parity journey 并更新受影响旧断言；同步 `tests/playwright/README.md`，记录自管与外部两种模式、环境变量语义，以及外部模式不复制 DB/不启停服务。**不新建第二套 harness** | 外部 base URL 模式断言未创建 DB 副本、未启动或终止子进程；README 命令与 fixture 一致；`tests/run_user_verify.sh` 从单一 harness 跑完整套件 |
| 0.3 | 校验原范围 measured 基线：133 个 token 映射、315 条组件规则、38 个 `@media` 块 / 345 条响应式规则；hot 增量由 0.0 单独追加，不重做原提取 | 校验原计数与 0.0 增量边界；原范围抽查 10 条、hot 抽查 5 条，均可从 GAP → measured 条目 → 冻结 CSS 追溯 |

**设计决策**：把测得值集中到一份 `measured-tokens.md`，而不是让每个 phase 各自去啃 minified CSS——避免同一个值在不同 phase 被读成不同结果，也让 reviewer 能一处核验。

> **0.1 的执行调整（2026-08-03，supervisor 决定，理由见 journal 同日 `[decision-revision]`）**
>
> 0.1 原文要求用冻结 HTML + 本地 CSS `set_content` **离线生成**缺失的 20 张 AIHOT frame。实际执行时这 20 张已在 plan 定稿前、冻结规则生效前由 **live 采集**补齐（`all` dark ×5、`changelog` dark ×5、`hot` light/dark ×10，另加 `more` light/dark ×10，共 30 张）。
>
> 保留 live 采集结果、不再离线重做，理由：AIHOT 是 Next.js RSC 应用，`set_content` 注入静态 HTML 无法触发 hydration，产出的 frame 会与真实渲染有系统性差异，反而降低对照基线的保真度。live 采集的是真实渲染。
>
> **0.1 仍然必须满足的部分不变**：标准 gate 只采 ours、AIHOT 面板只读冻结 `r0-baseline`、网络层断言标准命令不发起任何 AIHOT 请求、live AIHOT 仅保留显式 diagnostic-only 入口。基线自此**冻结**——`r0-baseline/` 下的 90 张 frame 不再重采（AIHOT 已于 2026-08-03 上线 `/hot`，参考站正在漂移，见 journal）。

### Phase 1 — Token 层 + 全局 chrome（GAP-01~14, 27~30）

先落 token（颜色/字号/行高/间距/圆角/阴影/边框五档/过渡/侧栏宽），再改页头去卡片化、tab 选中态、侧栏与主题控件；同时处理 GAP-52/53 的主题元数据与 `/all` 筛选结构。

**内部 verify**：`node --check`；`ruff`/`mypy`；跑 L2-1 采集 `home`/`all` 的 z100 与 mobile 档并逐条对照；`grep` 确认旧 token 名已无残留引用；建立全页面 head/prepaint 契约清单，对每个 L1 route 拦截外部 app init 后断言 inline bootstrap 已同步 `data-theme`、`data-theme-mode`、`theme-color`；Playwright 另覆盖切换/持久化/system、筛选两维、首页日期各状态。

### Phase 2A — 卡片与时间线（GAP-19~20, 22~26, 51, 54~55）

**关键设计决策**：所有同一用户 surface 的 SSR/CSR 双渲染都必须成对维护：主 feed 的 `web/templates/_prepaint_list.html` ↔ `app.js itemCard`，微信列表的 `web/templates/wechat.html` ↔ `app.js wechatCard/renderWechatTimeline`。任何卡片结构改动必须同步对应 pair。
**内部 verify**：为两组 pair 建立同一规范化 DOM skeleton 契约——对同一条目比较节点顺序、关键文本槽位、`href`/`data-*`、按钮/徽标/标签与媒体节点的数量、顺序及身份值，禁止只比 class set 或节点存在性；非 WeChat 来源另以有/无 `source_icon_url` fixture 验证 SSR/CSR 真实 favicon 与首字母 fallback；微信另触发首屏、搜索、翻页、返回，并把每一状态的 API expected 数量/identity 与 DOM 逐项比较；`tests/test_frontend_static_contract.py` 全绿；Playwright 用固定数据断言桌面星期与移动今天/昨天均按 Asia/Shanghai 判日界。**此 phase 有效 V=V1** → 跑 L2-3。

### Phase 2B — 首页热点 + 新增热点榜（GAP-15~18, 21, 56~57；GAP-50 与 Phase 3 协同）

这是用户明确接受代价的唯一产品范围扩张。新增 `/hot` 页面与桌面导航，首页缩成 2 条并增加完整榜单入口；扩展既有 `/api/v1/hot`，不新增第二套排名或缓存模型。页面只呈现现有数据可证明的字段，并用我方公式说明替代 AIHOT 方法说明。

**内部 verify**：API fixture 固定 `weighted_score`、0/1/多条 `related_discussions`、正常/未来/非法 `published_at`，断言 `heat=round(weighted_score×10 + related_count×5)`、排序稳定、`generated_at/event_time` fallback 与响应字段；Playwright 断言首页前 2 条等于同一 API 排序前缀，`/api/v1/hot?limit=10` 的响应与 `/hot` DOM 在数量/顺序/字段上逐项相等，关联信源展开数据条件化，accepted-divergence 元素不存在，桌面侧栏高亮和移动首页入口可达。**有效 V=V1，且触碰后端/API/路由** → `mypy`、相关 pytest、L2-1 hot 全矩阵与 L2-3 热点路径必跑；完成改动后由 supervisor 按 A2 契约重启 8011。

### Phase 3 — 缩放与移动层（GAP-31~38, 48~52）★最大工作量

断点改 640/960；新建 `--m-*` token 层；≤960px 实现底部 tab 栏、横向 chip、页面区分的搜索、全出血卡片、吸顶日期条、紧凑顶栏；新增 `/more` 路由与模板，底栏第 4 项直接导航至该页。

**用户已拍板的设计决策**：底部 tab 栏只放 4 项（精选/全部/日报/更多），“更多”跳独立 `/more`；该页只收纳微信文章解读、收藏、关于、更新日志。不含主题、Agent 接入、反馈，不复用 `#app-sidebar`，桌面侧栏结构不变。
**内部 verify**：z150 / z200 / mobile 三档截图；`node --check`；HTTP 验证 `/more` 200。**有效 V=V1** → 跑 L2-3 的全部移动路径并断言页面专属搜索形态与四入口白名单。

### Phase 4 — 日报页重做 + 新增 changelog 页（GAP-39~47）

**内部 verify**：`/daily` 与 `/changelog` 各返回 200；changelog 的有序 block/text/link 序列与 `CHANGELOG.md` 独立解析结果逐项一致；日报用数据/API 生成 expected archive/date/month/section/summary counts 并逐项比对；报头断言本站中英文品牌、有效日期与实际正文 N，并至少用 N≠2 fixture 防止复制冻结样本；阅读公式既做 0/1/300/301 边界，也用含摘要 CJK + 标题/导航/英文干扰的渲染 fixture 从 DOM 独立计算 C，证明排除面；非法/未来日期断言回退 URL + 弱状态，合法空日期断言不回退。**Phase 4 触碰后端路由 → `mypy`/`pytest` 必跑。**

### Phase 5 — 收敛

更新 `tests/playwright/` 中断言旧分页 UI 的用例，保持单一 harness；跑完整 L2-1~L2-5；执行 CSS provenance gate：审查 CSS diff 不含 minified bundle/source-map/Next hash、AIHOT 专属且我方 DOM 不存在的 selector 或整段第三方规则，每个新增 token/几何值形成 `GAP → measured-tokens 位置 → 我方 selector/token` 表；过 `review-gate`（含微信渲染 unit 的 V2 对抗审查）；apply UX 契约全部 delta；同步 `README`/`CHANGELOG`/`docs/`（按 docs-organization-protocol §5）。

**收敛判据与轮次**：见 §Defaulted Decisions 的 **D8′**（2026-08-04 取代原 D8）。原文"连续 2 轮无新差异即收敛"已作废——r4 在该判据下被判为清洁，而缺陷仍在。

### Phase 6 — 关系层收敛（2026-08-04 新增，r4 之后重开）

r4 自称收敛后用户对着 live AIHOT 找出真缺陷，本 phase 是重开后的第一轮。

| 步骤 | 内容 | 执行者 |
|---|---|---|
| 6.0 | 建立成对 live 探针 `reference/aihot-parity/probe.py`——同一次运行内两站同 viewport/主题，语义定位（按渲染文本形态，不用类名），`Range` 量字形盒，输出墨迹留白 / 对齐差 / 结构链 / 导航 regime / 横向溢出 | supervisor（**已完成**） |
| 6.1 | 修 GAP-58 / 59 / 60 | 委派 Codex |
| 6.2 | **反向完备性枚举**：从 live AIHOT 侧逐组件枚举其规则，对每条确认我方有对应决策（已实现 / 有理由的接受差异）。产物落盘为清单，不是"我读过"。范围：`/`、`/all`、`/hot`、`/daily`、`/changelog` | 委派 Codex（只读） |
| 6.3 | 复测：`probe.py` 全 13 档 × light/dark 重跑，逐档比差 | supervisor |
| 6.4 | **亲眼对比**（D8′ 第 4 条）：两站同开、逐档逐屏看过、对点名元素做关系测量 | supervisor，不可委派、不可用代理证据替代 |
| 6.5 | 把 L2-7 差异表与残余 GAP 交用户判断是否继续 | supervisor → 用户 |

**并发写入边界**：6.1 写 `web/static/style.css` 等前端源码；6.2 只读，产出落 `plans/20260803-aihot-visual-parity/aihot-rule-enumeration.md`。两者无共享写入边界，可并行。

---

## 用户决策 gate

全流程只有一个：**L2-6 终审**（用户已选"跑到收敛再看"）。其余 phase 边界全自动执行，无需用户介入。
L2-6 的材料、最短查看路径与回复格式见 §L2-6。

此外，两处**已存在的独立许可要求**（来自 `~/.claude/CLAUDE.md`，非本 plan 新设）：worktree 分支整合回本地 `main` 需先提议再执行；`git push` 需显式许可。二者都在 L2-6 通过之后。

---

## 顶层入口文档同步

需要。`README.md` 描述了站点页面构成，新增 `/hot`、`/changelog`、仅移动入口的 `/more` 与移动端形态变化需同步；`CHANGELOG.md` 追加本次改版条目。二者在 Phase 5 一并处理。

---

## Defaulted Decisions（访谈中未问、我自己拍的，供 reviewer 审）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 侧栏**不加**"主题"入口 | 用户拍板主题页延后；加一个指向 404 的导航项是明确的体验倒退 |
| D4 | 保留来源 favicon（GAP-26） | 这是既有功能，删除是功能倒退；只对齐视觉语言。GAP-25 已经冻结 `/all` 证据推翻，不再视为我方独有 |
| D5 | 不做"更新日志"未读红点（GAP-47） | 需要未读状态持久化，属新功能而非视觉复刻 |
| D6 | `/daily` 无效日期的警示横幅属缺陷 | 用户已进一步拍板为“回退 + 非警示色弱内联状态”，具体契约见已拍板表与 GAP-44 |
| D8 | ~~收敛判据取"连续 2 轮无新差异"，轮次上限 6~~ **已作废，见下方 D8′** | 该判据在 r4 判定为"第 1 轮清洁"，而用户随后在同一状态下一眼找出两个真缺陷。判据失效的原因不是轮数不够，是每轮的"无新差异"只由值层证据支撑 |

**D8′（2026-08-04 补强判据，取代 D8）**——收敛需要**同时**满足下列四条，缺一不可：

1. **值层**：L2-1~L2-5 全绿，§gap-inventory 无 `[open]`。
2. **关系层与结构层**：L2-7 成对测量在 13 档 × light/dark 全部一致或有理由的接受差异。
3. **反向完备性**：本轮范围内的每个 AIHOT 组件，已从**参照侧**枚举其规则并逐条对上我方决策；枚举结果落盘（不是"我读过"）。
4. **亲眼对比**：supervisor 必须真做一次用户会做的那个对比——两站同开、在同一缩放档下逐屏看过、并对点名元素做关系测量。这条**不可用任何代理证据替代**（逐值审计、行为测试、截图矩阵、rubric 判定都不覆盖关系层）。报告里要如实写明每条判定来自哪种证据："依据运行时逐值证据"与"我逐屏看过"是两件不同的事，不得互相冒充。

**轮次**：不再设"连续 2 轮无新差异"的自动收敛出口——该出口在 r4 已被证明可以在缺陷仍存在时触发。改为：每轮结束把 L2-7 差异表与残余 GAP 交用户，由用户判断是否继续（用户 2026-08-04 原话预期"有可能要经过多次循环和迭代"）。上限 6 轮的 ceiling 分支保留作为兜底。

**原 D7 已被事实证据与用户决策取代**：冻结 AIHOT DOM 表明移动“更多”是独立 `/more` 目标，不是复用侧栏的抽屉；用户已拍板采用独立 `/more` 及四入口白名单，故该项不再是 defaulted decision，执行契约见“取舍偏好”、GAP-32 与 Phase 3。

**原 D2/D3 已被用户决策取代**：用户未采纳“不做 `/hot`、首页保留 5 条”的推荐，已拍板新增 `/hot`、桌面侧栏增加热点榜，首页改 2 条 + 完整榜单入口；两项不再是 defaulted decision，执行契约见“取舍偏好”、GAP-15/16/21 与 Phase 2B。

---

## Risks

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 冻结 AIHOT 与我方快照内容不同（条目数、标题）会干扰判断 | rubric 分离内容与布局；用同 viewport 的几何/层级/组件规则和 `measured-tokens.md` 判视觉，不把文本相同当成通过条件 |
| R2 | AIHOT 实时站点改版导致参考漂移 | 标准 gate 只读 `reference/aihot-snapshot-20260802/`、`r0-baseline` 与 `measured-tokens.md`；实时复采仅诊断，发现漂移须 re-plan，不能替换权威基线 |
| R3 | Codex 或 supervisor 误绑 8000 导致公网上线 | A2 生命周期契约：Codex 禁止服务动作；supervisor 只操作核验身份的 8011/8012 进程，所有动作前后验证 8000；非空立即停而不盲杀 |
| R4 | SSR 与客户端两套卡片渲染改不同步，出现首屏闪烁/结构跳变 | Phase 2 新增结构一致性契约断言（见该 phase） |
| R5 | 现有 `tests/playwright/` fixture 会自启服务，旧断言仍面向数字分页 | Phase 0 先加入外部 base URL 模式、更新受影响断言；所有 parity journey 归入这一 harness，禁止第二套测试或服务所有权 |
| R6 | 1.9GB DB 副本 + worktree 占盘 | 完工清理时一并删除（清理由 supervisor 主线程执行，不委派——Codex 无法用 `rm`） |
| R7 | capture 的 best-effort 跳过会生成看似完整、实则缺页的报告 | expected manifest 先行；缺帧、空图、资源 404 或报告缺格一律非零；L2-6 前同时 preflight 本地与交付地址 |
| R8 | `/hot` 当前样本关联信源稀疏，照 AIHOT 画出密集名单会制造假数据 | API/DOM expected-vs-actual；只有实际 related 非空才显示展开，空时不伪造；状态/趋势/氛围票/story 按 GAP-57 固定为 accepted-divergence |
