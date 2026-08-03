# ADR-012: 移动层用单套 DOM + media query 重塑，不复制参考站的双 DOM

- Status: accepted
- Date: 2026-08-03

## Context

把前端视觉与体验对齐参考站 AIHOT 时，`≤960px` 的移动层是最大单项工作量。从参考站冻结的编译 CSS 与 HTML 快照测得一个结构性事实（记于 `measured-tokens.md` C.0）：AIHOT 在 `≤960px` 不是把同一套 DOM 变形，而是**整套 DOM 切换**——`.feed-desktop` 与 `.m-feed` 两棵子树同时存在于 HTML 里，靠 `display` 互斥；卡片换成 `.m-row-wrap > .m-row` 另一套节点，分类 tab 换成 `.m-chips > .m-chip`，日报同样是 `.daily-desktop` / `.m-daily` 双份。

任务目标是"尽可能 1-1 复刻，除非有明确理由必须不一致"，所以是否照搬这个结构需要一个显式决策。

关键的不对称在于两边的渲染架构成本不同：

- AIHOT 是 Next.js RSC 应用，两棵子树由同一组件树产出，双 DOM 在作者侧近乎零成本。
- 本项目每个用户 surface 都是 **Jinja SSR ↔ `app.js` CSR 成对维护**（主 feed 是 `web/templates/_prepaint_list.html` ↔ `itemCard`，微信列表是 `wechat.html` ↔ `wechatCard`）。plan 的 Phase 2A 已把"同一 surface 的 SSR/CSR 必须成对同步"确立为硬契约，并把"两套渲染改不同步导致首屏闪烁/结构跳变"列为风险 R4。

再加一套移动 DOM，等于把每个 surface 变成**四份渲染路径**。

## Options Considered

### Option A: 照搬双 DOM（内容区也做移动专用子树）

- Pros: 与参考站结构 1-1；每套 DOM 可各自最优化，移动卡片不必受桌面结构约束。
- Cons: 每 surface 四份渲染路径，直接放大 R4；SSR/CSR 成对契约的断言面翻倍；DOM 重量翻倍（两套内容同时在文档里）；而这一切对用户不可见——用户只看得到视觉结果。

### Option B: 单套内容 DOM + media query 重塑，chrome 允许独立节点（采用）

- Pros: 渲染路径仍是每 surface 两份，既有成对契约与断言直接复用；DOM 不含冗余内容拷贝；改动集中在样式层，反转成本低。
- Cons: 移动几何受桌面 DOM 结构约束；某些效果可能重塑不出来。

判据不是"哪种更像参考实现"，而是"哪种在**本项目的**渲染结构下更不容易出 bug"。逐条比对 `measured-tokens.md` B.2 的 `.m-row*` 规则后确认 Option B 可行：AIHOT 移动行相对桌面卡片的差异**全是几何**——两行钳制标题、钳制摘要、推荐理由改灰底块、时间列宽 40px——没有一个"桌面上不存在的信息节点"。既有卡片 DOM 已承载全部所需信息，重设 `-webkit-line-clamp` / `background` / flex 方向即可达成。

## Decision

分层处理：

| 层 | 做法 |
|---|---|
| 内容区（feed / 卡片 / 日期分组 / 日报正文） | **单套 DOM + media query 重塑**。不新增第二棵内容树 |
| chrome（底部 tab 栏、移动顶栏、移动 chip 行与搜索图标） | **允许独立元素**，照参考站做法常驻 HTML、桌面 `display:none`。它们在桌面无对应物（桌面是侧栏 + 完整搜索表单），属**增补**而非同一内容的第二份拷贝 |

**逃生口**：某个具体元素确实无法靠重塑达成视觉一致时，只为**那一个元素**加移动专用节点并逐个报备，不因一处受阻整体切回双 DOM。若逃生口用量失控（如过半元素都需要），说明本决策的事实前提不成立，届时重新评估。

## Consequences

- `≤960px` 的导航由 `web/templates/_mobile_tabbar.html`（`.m-tabbar`，54px，4 项）与 `_mobile_topbar.html`（`.app-mobile-bar`）承担，二者在全部渲染桌面侧栏的 L1 HTML consumer 上同步；原有的汉堡按钮与 `#app-sidebar` 抽屉移动形态被移除。
- 本项目的 mobile 类名词汇表与参考站不同（用户已拍板"按测得值在我方 token/类名体系里重写"）：分类容器是 `.seg-list`/`.seg-item`（非 `.segmented`）、移动顶栏是 `.app-mobile-bar`（非 `.m-topbar`）、日期头是 `.timeline-day-head`。`.m-tabbar`/`.m-tab` 是有意保留的少数同名项。
- 实施后实际只用了**两处**逃生口，均符合判据（文字语义不同而非纯几何）：① 标题「精选」/「最新精选」两个 span；② 日期的桌面绝对日名 / 移动相对日名两个 span（`.desktop-date-label` / `.mobile-date-label`）。
- 副作用：桌面与移动共享同一 DOM，意味着移动专属几何必须靠 media query 覆盖而非独立规则集，`style.css` 的 `≤960px` 块因此较大。这是有意的取舍。
