# ADR-058：X 媒体缩略图改为收缩包裹左对齐，并以 lightbox 增强原生链接

- 状态：已接受
- 日期：2026-08-18
- 相关：[ADR-054](054-stop-rendering-article-images-in-list-cards.md)、[ADR-057](057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md)

## 背景

[ADR-057](057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md) 恢复了 X 推文自带媒体的渲染。上线后用户对比参照站 aihot.virxact.com 提出两点：我方图片**居中**而参照站**靠左**；且某条推文（`x_ayi_ainotes`「Cursor 推出 Origin 代码托管平台」）的截图"信息展示不全"。

同条件成对测量（agent-browser / Chromium 147，`https://news.aiplanet.live/`，1440×900，light）：

| 读数 | ai-radar（改前） | aihot |
|---|---|---|
| 单图容器机制 | `display:grid` + `width:100%` 定宽格 + `object-fit:contain` | `display:flex;justify-content:flex-start;max-width:240px`，cell `width:auto`——收缩包裹 |
| 单图 `<img>` 盒 | 1072×210 | — |
| 单图**实际画面**（由 natural 与 rect 宽高比反推） | 245×210，**左右各约 413px 灰底空档** | 238×208（竖图）/ 238×134（横图），无灰底 |
| 用户点名条目 | 双列格 532×170，画面 302×170 = 原图 **25.2%**；第二张图浏览器内加载失败被 `onerror` 隐藏，右半格空着 | — |

"居中"是**结构**问题不是值问题：定宽盒 + `contain` 必然把画面居中并留出灰底，改 `text-align` 一类的值改不到它。

参照站规格取自其三份 `/_next/static/css/*.css`（该站对 UA 含 `HeadlessChrome` 的客户端返回 `{"error":"blocked","code":567}`；curl 逐 header 隔离确认是 **UA 令牌检查**，仅换 UA 即 200 ↔ 567），并用真实 Chrome UA 在其 `/all` 页线上确认（`@kimmonismus` 条目单张媒体约 236×134、紧贴卡片左缘、无底框）。

**参照站的多图规则被显式否决**：`.x-tweet-media-grid[data-count="2"]` 是 `max-width:420px` 容器 + `aspect-ratio:16/9` 两列 + `object-fit:cover`。用它自己的 CSS 内联 + 本仓这两张真实图片复刻渲染，实测每格 208×235 的**竖格**，1200×675 的横向产品截图 cover 后只剩中间一条竖缝——比现状信息更少，正好加重用户的原始抱怨。

## 决策

### 1. 布局：收缩包裹 + 左对齐，不裁切

`.x-media` 由 grid 改 flex（`flex-wrap:wrap;justify-content:flex-start;align-items:flex-start;gap:6px`）；`.x-media-link` 改 `width:auto;max-width:100%;line-height:0;background:transparent`；`.x-media-img` 改 `width:auto;height:auto` + max-width/max-height 约束。

因宽高皆 `auto`，**`object-fit` 不再参与**——画面尺寸由内在比例在 max 约束下决定，letterbox 由构造消除，不是靠调值消除。

尺寸档位：单图 `max-width:240px`（取自参照）+ `max-height:210px`；多图每格 `max-width:207px` + `max-height:170px`；≤960px 断点 `max-width:100%` / 多图 `calc(50% - 3px)`，max-height 190/150px。

**max-height 一律沿用本仓既有实测值，不取参照的 320px**——`style.css` 注释记着 260px 时桌面首屏完整卡片掉到 1（低于 ≥2 的底线），320 比 260 更高。

**不采用 `object-fit:cover`**：ADR-054 与 `style.css` 注释里"推文的图常是 benchmark 图表或发布截图，裁坏等于白放"的取舍原样保留。

### 2. 点击：lightbox 增强原生链接，而非取代它

`<a>` 仍带真实 `href` 与 `target="_blank" rel="noopener noreferrer"`。**仅当 `e.button === 0 && !metaKey && !ctrlKey && !shiftKey && !altKey`** 才走 lightbox 分支；其余手势一律不 `preventDefault`，交由浏览器原生处理（这同时保证 macOS 上 Ctrl+左键仍是上下文菜单）。

**事务序**：try 内「构造 detached 遮罩 → 挂载 → 施加 `inert`/滚动锁/移焦」全部成功后**才**调用 `preventDefault()`；任一步抛错 → `teardown()` → **不** `preventDefault` → 浏览器按原生链接继续跳转。`teardown()` 的每一步各自独立 try/catch，逐项 best-effort（`inert` 的逐元素恢复也各自隔离），某步失败不中断其余。

**modal 契约**：`role="dialog" aria-modal="true"` + `aria-label`（取该条目标题）；可聚焦关闭按钮（`aria-label="关闭"`），打开时焦点落其上；Tab/Shift+Tab 在「关闭/上一张/下一张」间循环，同时对 `document.body` 的直接子元素（遮罩除外）施加 `inert`（**施加前逐个保存原有值，teardown 时逐个恢复**）；Esc 与点击遮罩关闭。

**焦点归还降级链**（逐级 `focus()` 后校验 `document.activeElement`，不相等才降级）：① `trigger`（须 `isConnected`）→ ② 按 `data-item-id` **重查**到的同条目媒体链接 → ③ 同条目标题链接 → ④ `#list`（临时 `tabindex="-1"`，**teardown 时挂一次性 blur 监听恢复**）→ ⑤ `document.body`。全部失败则如实记为"焦点未归还"，不假装成功。

②③ 必须**重查**而不是在打开时钉住节点：跨 960px 断点时 `rebuildTimeline` 用 `innerHTML` 整块替换列表（`app.js:767`），钉住的节点会与 `trigger` 一起断开，降级链一路掉到 `#list`、丢掉原条目位置。同理也不能用 `trigger.closest(".item-row")`——trigger 脱离 DOM 后它只在游离子树里向上找，那一级静默永不命中。

**序列变化时先移焦再隐藏**：当前图加载失败被剔除、序列从 2 掉到 1 时，焦点可能正停在即将 `hidden` 的导航按钮上；直接隐藏会把焦点甩回 document，遮罩还开着却没有焦点落点。`render()` 在隐藏前把焦点移到关闭按钮。

**切图对读屏的可感知性**：焦点始终停在同名的「下一张」按钮上，图片又只能声明为装饰图（见「已知未验证项」的 `alt_text` 缺口），故另设一个视觉隐藏的 `role="status" aria-live="polite" aria-atomic="true"` 区域播报「第 N 张，共 M 张」；可见计数器保持紧凑的「N / M」。

**序列收敛**：切换序列打开时按"未被 `onerror` 隐藏"筛一次；lightbox 内当前图 `error` 时把该项移出序列并自动前进；序列空则关闭并归还焦点。鼠标、键盘、读屏三条路径拿到同一序列，且与 ADR-057 的失败隐藏规则一致。

用事件委托挂在 `document` 上，故 CSR 与 SSR prepaint 两条路径同形。

### 3. `href` 的语义边界

`media_assets[].url` 本身就是 ADR-057 的**同源 `/img` 代理路径**（形如 `/img?url=https%3A%2F%2Fpbs.twimg.com%2F…`），不是图床直链。契约措辞一律用「该媒体的实际资源 URL / 查看大图」，**不用「原图直链」**，以免与 ADR-057 的"不直连外部图床"条款冲突。

## 提议方案的实测

把提议 CSS 注入线上页面，在**同一次会话、同一页、同一批 40 条卡片**上只切换该 stylesheet 的 `disabled`，前后各量一遍：

- **letterbox 消除（决定性读数）**：改后每张图 `|rect宽高比 − natural宽高比| < 0.02` 全部为 true，且 `<a>` 盒 = 图 + 2px 边框（img 240×206 / link 242×208；img 207×116 / link 209×118）。改前同一读数在单图上是 1072×210 vs natural 945×811，比值差 0.29。**这条读数在"盒子贴合画面"与"定宽盒内居中"两种情况下取值不同**；只读 rect 则两者相同。
- **左对齐**：对标题**文本节点建 Range** 取 rect（非元素盒），媒体 `<a>` 的 left 与标题文字 left 逐条相等，delta = 0.0（4/4）。
- **密度（同页 before/after，40 卡 / 17 带媒体）**：卡片高度 min/中位/max = 188/212/**432** → 188/212/**415**；17 张带媒体卡片高度合计 5235 → 4957px（−278px，−5.3%）。密度不劣化、略有改善。
- **移动断点 390×844**：5/5 ratioOK=true，`link.right − container.right` 最大 0（无横向溢出）。

`style.css` 注释要求"改 max-height 数值前照同样方法重量一次"——本轮 max-height 一个都没改，但盒模型变了，故没有拿"数值没改"当豁免，而是做了上述真实页面 before/after 对照。

## 作用域

- **确定生效**：`/`、`/all`、`/curated`（`src/airadar/web/app.py:739` 的 alias，复用首页渲染），以及根静态挂载直接暴露的 `/index.html`、`/all.html`。
- **`/bookmarks` 仅在导入快照携带 `media_assets` 时命中**——`web/static/app.js:2045` 的 `bookmarkSnapshot` 字段白名单不含 `media_assets`，正常收藏流程不产生 `.x-media`。本次**不**把媒体纳入收藏快照（那是独立决策，牵动旧快照迁移与导入导出边界）。
- 只对 `source_kind === "x"` 生效；ADR-054 的"RSS 正文图不渲染"不变。
- 读数只覆盖 **1440×900（light）与 390×844** 两档。

## 影响

**ux-contract 演化走 §4.6 fallback**：本轮是自由 session，agent 不直接改 `docs/contracts/ux-contract.md`，只把含配对 L1/L2 的演化候选写进 `docs/issues/ux-contract-issues.md`，由用户跑 `/custom:create-ux-contract` 处理。**代码合入到用户跑该 command 之间，HP-7 的字面与实现存在窗口期不一致。** 同时把 2026-08-17 那条已被 ADR-057 部分推翻的 [drift]（要求「HP-7 整条撤下」）按 §4.8 lifecycle **整条移入** `docs/issues/archive/closed.md`。

**回退路径**：`git revert` **不足以**让线上恢复。按 `CLAUDE.md`「Frontend Asset Cache Busting」，`app.js` / `style.css` 在 EdgeOne 上有 7 天精确路径强缓存，回退必须 `revert → 重跑 scripts/bump_frontend_assets.py → 重新发布全部 HTML 引用 → 从真实公网核验`。

**失败发现时点**：布局与交互回归在验收矩阵 M1 当场发现；**lightbox 的可访问性回归无自动哨兵**，依赖人工矩阵，无持续监测。

**取证时的一次仪器教训**（值得留给下一个跑跨引擎矩阵的人）：首轮 WebKit 读数是 `allFailedDelta = 10`、容器仍 `display:flex`，看起来像一个 Safari 专属的 `:has()` 失效缺陷。隔离复现证伪了它——`:has()` 在 WebKit 上工作正常，含动态置 `hidden` 后。真实原因是**测量抢在 `loading="lazy"` 的图片开始加载之前**：那两张必然失败的夹具图还没 `error`，链接自然还没被 `onerror` 隐藏，容器于是还没坍缩。同一轮的 `imgs 10`（Chromium 是 11）就是同一个抢跑的旁证。修法是先把每个夹具滚进视口一次、再等一个显式的 settle 条件（所有图 `complete` 且 all-failed 的链接全部 `hidden`）。**差一点就报出一个不存在的引擎缺陷**——跨引擎比较里，"引擎差异"和"我的仪器比这个引擎快"产生的读数完全同形。

## 已知未验证项

- **已由 M1 覆盖、不再是未验证项**（本条记录收敛结果，避免后来读者按旧措辞重做）：lightbox 的焦点归还（含五级降级链与跨断点重渲染）、滚动锁恢复、Esc、修饰键与中键放行、抛错回滚、序列收敛、SSR 同形标记上的事件委托，以及"媒体全部加载失败时卡片高度与无媒体卡片一致"（同卡对照 delta = 0，flex gap 下仍成立）——四格矩阵均已实测通过。
- **未覆盖轴**：Windows / Linux 上 Ctrl+左键的语义（本机 macOS，不推断）。dark 主题、125/150/200% 缩放档与断点两侧取点均已覆盖，见下条。
- "600×2400 极端竖图 + 每卡 2 图"这个 `style.css` 注释里的具体夹具，**已作为 M1 的 `multi-2-extreme` 夹具复现**（600×2400 两张，桌面单卡 368px、每格 43×170）；未复现的只有当年那次读数的"首屏完整卡片数"口径，本轮改用同页 before/after 高度分布对照。
- **多图跨断点重渲染后，若可见媒体条数变了，焦点可能落到另一张**（被隐藏的那张排在原媒体之后时，原序号仍指向同一张）。重查按 `data-item-id` + 该媒体在可见序列里的序号，条数不变时能回到用户原先打开的那一张（已实测：打开第 3 张、媒体节点整体替换后仍归还第 3 张）；但若重渲染改变了条数（某张这次加载失败被 `onerror` 隐藏），序号就会错位到**另一张**——隐藏一张时是相邻项，同时隐藏多张则未必相邻。属已知降级，不是回落到 `#list`。
- 用户点名条目第二张图在浏览器内加载失败（curl 同 URL 返回 HTTP 200 / 27902 bytes）的**根因未定位**，假说是 `/img` 首次取图超时。本决策不修它，只在 lightbox 切换序列里排除它。
- 参照站**多图**规则的线上表现未直接观测到（只观测到单图那条）；多图结论来自其 CSS + 本地复刻渲染。
- **上游 `alt_text` 未穿过展示投影**：`x_api.py` 已请求并保存 `alt_text`，但 `presentation/media.py` 的投影只产出 `{"type","url"}`，故 CSR / SSR / lightbox 三处一律 `alt=""`。这是 ADR-058 **之前就存在**的独立缺口，本轮的 `aria-live` 计数器能播报"第几张"、播报不了"是什么"。修它要动数据契约（走 `/custom:review-schema`），不随本轮做，已登记在 [issues/ux-issues.md](../issues/ux-issues.md)。
- **跨引擎 × 缩放 × 断点覆盖：3 引擎 × 7 档 = 21/21 通过**。引擎为 Chromium 147、WebKit 26.4、Firefox 148.0.2（均为 Playwright 构建）。7 档 = 缩放 100/125/150/200%（浏览器缩放 Z% 在窗口物理尺寸不变时缩小 CSS 视口，故按视口宽 1440/1152/960/720 取点）+ 断点两侧 959/960/961。每档断言：布局违例 0、同卡「全部失败 vs 无媒体」delta = 0、手势矩阵全过、抛错回滚与 Esc 后无残留、焦点归还成功。另在 {1440×900 light, 390×844 dark} 上逐项比过 dark 主题；`inert` / `:has()` / `CSS.escape` 三项特性支持在三个引擎上均为 true。
  **仍未覆盖**：真机 Safari / iOS 与真机 Firefox——Playwright 的 WebKit / Firefox 是**定制构建**，与厂商发行版不等价。
  （取证抖动：Firefox 的 `bp-961` 首跑 settle 等待超时，复测 3/3 通过，是等待抖动而非缺陷；同引擎相邻的 959/960 首跑即通过。不复测就会记成一个不存在的引擎差异——与本 ADR「失败发现时点」节记的那次同源。）
- 未用真实读屏器（VoiceOver / NVDA）验证，只验证了可访问性树的输入（`role`/`aria-live`/可聚焦性/焦点落点）。
- 快速切图时旧 `img.src` 的延迟 `error` 是否可能在新图已成为 current 之后到达、从而让 `dropCurrent()` 删错条目，取决于浏览器的图片请求取消与事件排队语义，**未判定**。
- `/wechat`、`/daily` 是否也消费该渲染器未最终确认（初步看有各自的渲染路径）。

## 决策评审

本决策过 `decision-review` gate。首轮判 blocker（"新标签页会丢失滚动位置"这一否决理由经核 `app.js:393` 的 `target="_blank"` **不成立**，且漏了混合方案）→ 交用户 → 用户改选混合方案 → 重走完整 gate → 判"复核" → 四轮复核后通过（Codex read-only，session `01a01492-6269-7863-affc-d40a11e980be`）。
