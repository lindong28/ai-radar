# Plan — /wechat 文章加「访问原文」链接

> 任务：让 https://your-domain.example/wechat 的每篇文章解读页（及列表卡片）显式提供一个可点击的「访问原文」入口，跳转到对应微信公众号原文。
> 形态：平铺单 plan（非长任务——单 session、约 5 处小改动、不触发 compaction，按 long-task-protocol §1 不启用状态外部化）。

---

## 输入 / 研究结论（implementer 直接采信，无需重查）

这是一个**可发现性（discoverability）修复**，不是缺数据的功能：原文链接其实已经存在，只是隐形。

| 事实 | 证据 |
|---|---|
| 原文 URL 已可靠存储 | `items.url`（`migrations/001_init.sql:18`，`TEXT NOT NULL`），内容是 `https://mp.weixin.qq.com/s/...` |
| 详情查询已 SELECT 该字段 | `src/airadar/web/routes/wechat.py:174`（`get_wechat_detail`）；列表查询 `:146` 同样有 `i.url` |
| 模板/JSON 上下文已带 `url` | `_item_from_row()` `wechat.py:84` → `"url": row["url"]`（详情页 Jinja + `/api/v1/wechat` JSON 都有）|
| **链接当前已存在但隐形** | 详情页 `wechat_detail.html:37` 与列表卡片 `wechat.html:58` 的「公众号名+头像」(`source-link`) 已 `href="{{ item.url }}" target="_blank"`——用户不知道作者名可点 |
| 列表卡片是**双渲染** | 服务端 Jinja `wechat.html:55-77` 做首屏；客户端 `app.js:427-448`(`wechatCard`) 在搜索/翻页后重渲染。两边都要改并保持一致 |
| 卡片整体可点跳详情，但内部 `<a>` 安全 | `app.js:544` `event.target.closest("a, button")` 已排除卡片内链接点击——新 `<a>` 不会误触发卡片→详情跳转 |
| `app.js` 是全站共享单文件 | 被 5 个模板 modulepreload：`index.html / all.html / wechat.html / wechat_detail.html / wechat_404.html`，统一版本 `?v=20260607-wechat-search1` |

结论：**纯前端改动（模板 + CSS + app.js + 测试），零 DB / 零查询改动。**

---

## L1 — 最终产物 + 使用方式

- **产物**：`/wechat` 解读页与列表卡片上一个**明确标注**的「访问原文」可点击入口。
- **使用者**：your-domain.example 的真实读者。
- **使用方式 / 下游动作**：读者看完解读（可复用认知 / 独特亮点 / 推荐语）后，若对这篇感兴趣 → 点「访问原文」→ 在新标签页打开微信公众号原文，去读全文。即：解读是"决策材料"，原文链接是"决策后的下一步动作"。
- **范围**（用户已拍）：
  - 详情页 `/wechat/<slug>`：链接放在**标题区下方、解读正文上方**（header 之后、summary-body 之前），**描边按钮**样式。
  - 列表页 `/wechat`：**每张卡片**也带一个紧凑的「原文 ↗」链接（服务端 Jinja + 客户端 JS 两条渲染路径都加）。
- **不做**：不改 DB / 查询；不动公众号名上已有的 `source-link`（保留，见 Defaulted Decision）；不引入新依赖。

## 取舍偏好（用户已对齐）

任务本质偏 binary feature，关键取舍只有一项：**可发现性 ＞ 视觉克制**。用户两个选择都印证了这个方向——
- 样式选「描边按钮 + 箭头」（最显眼）而非纯文字链接：因为根问题就是"现有隐形链接没人发现"，必须够显眼。
- 范围选「详情页 + 列表卡片」（覆盖更广）而非仅详情页：宁可列表略密，也要让读者在更多位置直达原文。

→ 实施时遇到"显眼 vs 克制"的细节抉择（按钮内边距、对比度、卡片上链接的位置），一律偏向"更容易被看到/点到"。

---

## L2 — 用户视角验收（交付 gate，implementer 必须实跑并贴证据）

用 `agent-browser` 对**本地 serve**（`./run.sh` 或现有 serve:8000）跑下列流程；截图存证。每条标注人机边界。

| # | 流程 | 期望可观察结果 | 人机边界 |
|---|---|---|---|
| V1 | 打开任一 `/wechat/<slug>` 详情页 | 标题/日期/标签之后、解读正文之前，出现一个**描边按钮**，文案含「访问原文」+ 箭头（↗）；视觉明显可点 | agent 自动 + 截图 |
| V2 | 点击该按钮 | 该 `<a>` 的 `href` == 该条 `items.url`（host 为 `mp.weixin.qq.com`）、带 `target="_blank"`；点击能发起新标签页、原详情页保持不变 | agent 自动（读 href + 确认可点；不强求新 tab 落地 host，见下注）|
| V3 | 打开 `/wechat` 列表 | **每张**卡片（含 url 的）都可见一个紧凑「原文 ↗」链接——链接数与本页有原文 url 的卡片数一致，无遗漏（不是"至少有一个"）| agent 自动 + 截图 |
| V4 | 点击某卡片上的「原文 ↗」 | 新标签页打开该条原文；**不跳转到详情页**（回归点）| agent 自动 |
| V5 | 点击同一卡片的**正文/标题区域**（非链接） | 正常跳转到该篇 `/wechat/<slug>` 详情（卡片导航未被破坏）| agent 自动 |
| V6 | 在 `/wechat` 搜索框输入词触发**客户端重渲染**，或翻到第 2 页 | 重渲染后每张卡片**仍**带「原文 ↗」（链接数==卡片数，证明 JS 路径未漏渲染部分卡片）且 V4/V5 行为一致（JS 渲染路径与 Jinja 一致）| agent 自动 |
| V7 | 移动端视口（窄屏）查看详情页按钮与卡片链接 | 不溢出、不重叠、可点击 | agent 自动 + 截图 |

交付响应必须贴出：V1/V3/V7 截图 + V2/V4/V5/V6 的可观察结论（打开的 host / 是否跳详情）。**internal verify 全绿不能替代本节。**

> 注：微信原文链接可能因公众号侧策略过期失效——这是上游数据性质，不在本任务可控范围，不作为 fail 判据（V2 只需验证 href 指向正确的 `mp.weixin.qq.com` URL 且能发起跳转）。

---

## L3 — 设计决策 + 实施步骤 + 内部验收

### 改动清单

1. **CSS**（`web/static/style.css`）
   - 新增详情页按钮类（建议 `.wechat-origin-link`）：描边/淡底 + 箭头，复用站点既有青色 accent 令牌（参考 `.detail-back`@1304、`.tag`@1174、`.wechat-card .hot-pill`@1295 的色调与圆角，保持视觉一致）。hover 高亮。
   - 新增列表卡片紧凑变体（建议 `.wechat-card-origin`，或 `.wechat-origin-link.is-compact`）：更小字号/内边距，适配 `.card-topline` 行内。
   - 移动端：确保窄屏不溢出（V7）。

2. **详情页模板**（`web/templates/wechat_detail.html`）
   - 在 `</header>`(行 57) 与 `<div class="summary-body ...">`(行 58) 之间插入按钮：
     ```html
     {% if item.url %}
     <a class="wechat-origin-link" href="{{ item.url }}" target="_blank" rel="noopener noreferrer">访问原文 <span aria-hidden="true">↗</span></a>
     {% endif %}
     ```

3. **列表卡片 — 服务端 Jinja**（`web/templates/wechat.html`，卡片 `:55-77`）
   - 在 `.card-topline`(行 56-69) 内、`recommendation` 之后加紧凑链接（右对齐），或放标题下方——以"可见且不与卡片点击冲突"为准：
     ```html
     {% if item.url %}<a class="wechat-card-origin" href="{{ item.url }}" target="_blank" rel="noopener noreferrer">原文 <span aria-hidden="true">↗</span></a>{% endif %}
     ```

4. **列表卡片 — 客户端 JS**（`web/static/app.js` `wechatCard()` `:427-448`）
   - 镜像第 3 步，**DOM 结构/类名与 Jinja 完全一致**（这是双渲染一致性的关键）：
     ```js
     const origin = item.url ? `<a class="wechat-card-origin" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">原文 <span aria-hidden="true">↗</span></a>` : "";
     ```
     插入到与 Jinja 相同的位置（`card-topline` 内 recommendation 之后）。

5. **缓存版本号 bump**（共享资源失效）
   - 改了 `app.js` → app.js 是全站共享单文件、**只有一条**版本线 `app.js?v=20260607-wechat-search1`，命中 5 个模板（`grep -rl "app.js?v=" web/templates/` 实测正好 5 个：index/all/wechat/wechat_detail/wechat_404）。全部统一改成新串（如 `?v=20260607-wechat-origin1`）。
   - 改了 `style.css` → bump 范围依据是「**哪些模板引用本次新增的 CSS 类**」，不是版本线归属：新类 `.wechat-origin-link` / `.wechat-card-origin` 只被 `wechat.html`（列表）与 `wechat_detail.html`（详情）引用，故**只 bump 这 2 个模板**的 `style.css?v=`（`20260602-wechat-uxfix1` → 如 `20260607-wechat-origin1`）。
   - 实测版本线分布（已 grep 核实，勿凭记忆）：`style.css?v=20260602-wechat-uxfix1` 共命中 **5** 个模板（all / index / wechat_404 / wechat_detail / wechat），`20260515-ux5` 仅 `admin.html`。其中 all / index / wechat_404 / admin **都不引用新类**——留旧版本串无害（它们的缓存 CSS 里没有也不需要新规则），不动属 P10 范围内克制，不要顺手一起 bump。
   - **原因**：style.css 是浏览器按完整 URL（含 `?v=`）缓存的共享单文件；wechat 两页 bump 后才会重新拉取含新规则的 CSS，新链接样式才生效。

6. **测试**（TDD：先写断言看 RED，再实现到 GREEN）
   - `tests/test_wechat_interpretation.py`：在 `test_wechat_pages_render_preload_detail_and_sanitize_markdown`(`:521`) 同款 fixture 上加断言——
     - **详情页存在性**：`detail.text` 含 `class="wechat-origin-link"`、`访问原文`、且其 `href` 等于该条 `items.url`、含 `target="_blank"`。
     - **列表页 count 一致性**（不是 existence！）：`listing.text`（Jinja 首屏）中 `wechat-card-origin` 出现次数 == 该 fixture 中 `save_decision=1 且 url 非空` 的条目数。期望值**从 fixture 数据动态导出**（数 seed 的条目数），不写死。这样某些卡片漏渲染链接（双渲染漂移 / 守卫误伤）会 fail，而非只在全缺失时 fail。分母是「有 url 的卡片」——空 url 按 R3 不渲染，属预期，需排除在分母外。
     - **保留断言（preservation）**：详情页与列表首屏仍含既有 `class="source-link"` 且其 `href` 仍等于 `items.url`——锁住「公众号名链接保留不动」这条已拍决策，防止 implementer 当作冗余删掉而所有其他断言仍绿。
   - `tests/test_frontend_static_contract.py`：加断言 `app.js` 源码中 `wechatCard` 含 `wechat-card-origin` 与 `target="_blank"`（锁住 JS 渲染路径，防止与 Jinja 漂移）。
   - 可选：若有 Playwright wechat 用例，补一条点击「原文」开新 tab 且不跳详情的断言。

7. **CHANGELOG（用户可感知变化，一等 deliverable）**
   - 本改动新增可见 UX 入口，属用户可感知变化。按 docs-organization-protocol，落 commit 前 `CHANGELOG.md` 加一条（如「/wechat 详情页与列表卡片新增『访问原文』入口」）。
   - README.md / docs/operations 判定**不需改**：README 是 Layout/Services 层级文档、operations 是运维 runbook，本次只加一个 UI 链接、不改架构/运维流程，无对应章节需要同步。implementer 若发现确有相关描述需更新再补。

### 内部验收（L3，过程兜底，agent 独立可跑）

- `pytest tests/test_wechat_interpretation.py tests/test_frontend_static_contract.py -q`：新断言 RED→GREEN。
- `./test.sh`（或 `pytest -q`）全绿，无回归。
- `ruff check src tests` / `ruff format --check`、`mypy`（按项目既有配置）通过。
- 人工/agent 扫一眼 diff：每行改动都能追溯到本 plan（无顺手重构）。

---

## Defaulted Decisions（planner 自拍，reviewer 可审）

| 决策 | 默认 | 理由 |
|---|---|---|
| 新标签页打开 | `target="_blank" rel="noopener noreferrer"` | 与既有 `source-link` 一致；读者读完解读不希望丢失当前页 |
| 保留公众号名上已有的 `source-link` | 保留不动 | 与新按钮指向同一 URL 虽冗余但无害；"作者名可点"是常见模式，移除反而是范围外改动 |
| 详情按钮文案 | 「访问原文 ↗」 | 用户原话；语义清晰，避免微信原生「阅读原文」的歧义（后者指公众号文章内的外链） |
| 列表卡片文案 | 「原文 ↗」 | 卡片空间紧凑，短词即可；与详情按钮同义不同长 |
| 新建 CSS 类而非复用 `.tag`/`.detail-back` | 新建 `.wechat-origin-link` / `.wechat-card-origin`，仅复用色彩令牌 | 语义独立、便于后续单独调样式；避免改既有类波及他处 |
| 不启用长任务模式 | 平铺单 plan，无 state.md/journal.md | 按 long-task-protocol §1，单 session 小改动不启用 |

## Risks / TODO

- **R1 双渲染漂移**：Jinja 与 app.js 两条路径必须输出一致 DOM——靠第 6 步两处测试同时锁住；改一处务必改另一处。
- **R2 缓存版本漏 bump**：只改 app.js/style.css 不 bump `?v=`，线上旧缓存看不到新链接（V1/V3 会在已部署环境失败）。*Acceptance*：手动 bump 是本仓既有约定（与现有 `?v=20260607-wechat-search1` 同源），引入内容指纹/构建步骤超出本纯前端任务范围，故接受手动 + grep 兜底。*Trigger response*：第 5 步按「哪些模板引用改动的资源/新类」决定 bump 范围——app.js 改→其 5 个引用模板全 bump；style.css 改→仅 wechat.html / wechat_detail.html 这 2 个引用新类的模板 bump；交付 checklist 复核。
- **R3 `item.url` 理论空值**：`NOT NULL` 但不挡空串——已用 `{% if item.url %}` / `item.url ? ...` 守卫，空则不渲染链接。
- **R4 卡片点击冲突**：新 `<a>` 已被 `app.js:544` 的 `closest("a, button")` 排除，V4/V5 专门回归；若调整卡片 DOM 层级注意别让链接落到判定之外。
- **TODO 部署验证**：本地 V1–V7 过后，按既有部署流程（serve 重启 + tunnel；注意 pipeline cron 锁，见 docs/operations）上线，再在 your-domain.example 复跑 V1–V3 抽样确认缓存已刷新。*Pre-set move*：若线上抽样仍取到旧 `?v=`（缓存未刷新），按 docs/operations 重启 serve 强制资源失效后复跑一次；仍不刷新才升级排查（如 tunnel/CDN 层缓存）。

---

## 交付前 checklist（implementer 自检）

- [ ] L2 V1–V7 全部实跑，截图/结论已贴
- [ ] 内部验收命令全绿，输出已贴
- [ ] 详情 + 列表（Jinja & JS）三处渲染一致，测试覆盖双路径
- [ ] app.js 改动 → 5 个引用模板的 `app.js?v=` 全 bump；style.css 改动 → wechat.html / wechat_detail.html 两个模板的 `style.css?v=` bump（其余引用同串模板不动）
- [ ] 用户可感知变化已按 docs-organization-protocol 同步 [User] 档（README/CHANGELOG/operations 视情况）
- [ ] commit 走 create-commit skill，不加 Co-Authored-By
