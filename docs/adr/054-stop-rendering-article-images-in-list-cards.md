# ADR-054: 列表卡片不再渲染正文抓取的图片

- Status: Accepted
- Date: 2026-08-17
- Context: 与参照站 aihot.virxact.com 的同条件成对 UI 对比

## Context

2026-08-17 在同一个可见 Chrome 151（非 headless——aihot 对无头浏览器返回 `{"error":"blocked","code":567}` 或把文档跳到 `about:blank`）、同一次会话内对两站各测一遍，视口 1440×900 与 390×844。骨架已逐值一致（侧栏 180px、内容列 1112px、卡片 `padding:15px 18px 14px`、`radius:12px`、标题 15.5px/700、卡片间距 12px），差异集中在卡片内容。

关键读数（1440×900）：

| 读数 | ai-radar | aihot |
|---|---|---|
| 首屏内完整可见条目数 | 0 | 3 |
| 第 2 条卡片顶边距视口顶 | 1276px（≈1.4 屏） | 422px |
| 卡片高度 min / 中位 / max（滚动加载后 80 / 60 条） | 189 / 211 / **998** | 125 / 182 / 421 |
| 高度 > 900px（超整屏）的卡片 | 8 张 | 0 |
| 带正文大图的卡片 | 11 / 40 | 2 / 20（均为 20×20 站标） |

图片本身也在丢信息：532×360 的格子里塞 1080×426（比例 2.54 → 1.48）、1072×360 里塞 534×610 竖图，`object-fit: cover` 大幅裁切；抓到的多为论文首页截图、网页截图、视频帧，移动端缩到 143×107 后完全不可读。

## Decision

列表卡片不再渲染 `media_assets`。**两条渲染路径同改**——只改其一会留下另一半：

- CSR：`web/static/app.js` 的 `itemCard()` 不再调用 `articleMedia()`，并删除 `articleMedia()` 本身
- SSR prepaint：`web/templates/_prepaint_list.html` 的 `{% if item.media_assets %}` 整块
- 样式：`web/static/style.css` 的 `.article-media*` 规则
- 契约测试：`tests/test_frontend_static_contract.py`、`tests/playwright/test_phase2.py` 中"列表必须有图"的断言反转为"列表不渲染图、数据仍在"

被否决的备选：**单张固定比例缩略图**（160×90，同样能把卡片高度变成固定值，且保留少数确有信息的图）——由用户选择完全移除，与参照站一致；**多图限高 160px**——`cover` 裁切问题原样保留，四图并排后每张仅 ≈260×160，依旧不可读；**不改**——见上表读数。

## Scope

- 结论只作用于共享列表渲染器（`itemCard` + `_prepaint_list`）覆盖的页面。
- 密度收益的证据只有 `/` 与 `/all` 两页、1440×900 与 390×844 两档。**不承诺改后的首屏条目数**——那是从卡片高度分布推算的，未实测。
- 不覆盖缩放档（125% / 150% / 200%）与断点两侧取点。

## Supersession 边界

本决策只 supersede `9b752d6 fix(web): recover WeChat images via data-src extraction + same-origin proxy` 中"列表卡片渲染正文图片"这一部分。

**API 契约不变**，其投影权威在 `src/airadar/presentation/summary.py:138`（`_visible_media_assets`）——不是 `src/airadar/web/app.py`。后者只是 `_prepaint_list.html` 的 SSR view model；模板删掉消费者后，那份 `media_assets` 成了无消费者的字段，应一并移除（**待办**：该文件当前被另一个 worktree 的写入者占用，未在本轮清理）。

**保留不变**：`/img?url=` 同源代理与其 host allowlist（兼作 SSRF 防护）、`data-src` 提取、以及仍在服务 `source-avatar` 的路径。

## 修订记录

**2026-08-20 — 撤回 Context 里关于 `about:blank` 的归因。** 上面 Context 第一段写着「aihot 对无头浏览器返回 `{"error":"blocked","code":567}` **或把文档跳到 `about:blank`**」。**前半句仍然成立且有判别性对照**：2026-08-20 用 curl 逐 header 隔离，仅替换 UA 令牌即在 `567` 与 `200` 之间切换，其余 header 不变。

**后半句撤回，但不换上另一个归因。** 撤回的依据是它失去了归因力——同一 `about:blank` 状态在**本地 `file://` 页面**上也复现，而本地文件不存在反自动化这回事。注意这只证明该症状**不是 aihot 特有**，**不足以排除**当次 aihot 流程也导致了它；两者的差别在这条证据上分不开。**当次真因未定**：有人提出是「跨调用的全局 flag 不一致」，该假说本轮未验证，故不写进正文替换原断言。

本条只影响 Context 里那半句的因果归因，**不影响本 ADR 的决策与其余读数**：决策依据是密度与裁切读数（首屏完整可见条目 0 vs 3、8 张卡片超整屏、`object-fit: cover` 的裁切），与浏览器为何呈现 `about:blank` 无关。

**2026-08-20 — 渲染范围被 ADR-057/058 收窄。** ADR-057 与 ADR-058 已恢复 X 推文自带媒体在列表卡片上的渲染（缩略图收缩包裹 + lightbox）。因此本决策的实际作用范围收窄为「不渲染**正文抓取**的图片」，而不是「列表卡片不渲染任何图片」——后者曾是本 ADR 标题与 Decision 段落的字面读法。判据是图片的来源：来自 RSS/网页正文抓取的仍不渲染，来自 X 推文自带 media 的渲染。

随之失效的还有上面「Supersession 边界」里那条待办——把 `src/airadar/web/app.py` 的 SSR view model 中「无消费者的 `media_assets` 字段」一并移除。X 媒体恢复渲染后该字段重新有了消费者，移除它不再是正确动作；这条待办撤销，不需另行跟进。

**2026-08-20（同日补正）— 上一条注记里「曾是本 ADR 标题的字面读法」这句不实。** 本 ADR 的 H1 自 git 首版（`0f827cd`）起就是今天这个形态：`# ADR-054: 列表卡片不再渲染正文抓取的图片`，**从来没有**写成「不渲染任何图片」，标题里的「正文抓取的」限定一直在。那句注记把一个从未存在过的旧标题当成了误读来源。

被误读的广义读法（「列表卡片不渲染任何图片」）实际来自另外两处，且**都不是标题**：Decision 段首句「列表卡片不再渲染 `media_assets`」——它按字段名一刀切，没带来源限定；以及文件名 slug `054-stop-rendering-article-images-in-list-cards`，其中 `article-images` 不区分「正文抓取的图」与「推文自带的 media」。上一条注记的**结论**（作用范围收窄为不渲染正文抓取的图片，判据是图片来源）不受本次更正影响，只更正它对误读来源的归因。

## 已知未验证项

- 改后首屏完整可见条目数（待交付后在同条件下实测补录）。
- 移除后卡片区留白与摘要行数的观感未评估。
- 未量对页面总高与无限滚动触发频次的影响。
