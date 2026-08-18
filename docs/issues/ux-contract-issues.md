# UX Contract Issues

> append-only queue. test-ux 跑测中发现的、与 ux-contract / aihot-parity-contract 演化相关的观察。owner sweep 后决定是否升级为契约修订。
>
> 协议：`~/.claude/references/ux-test-protocol.md` §4。
> type 语义：`drift`（契约声 X 实际 Y）/ `expansion`（未覆盖但合理的扩展候选）/ `redesign`（契约结构本身改进建议）。

---

## 2026-08-18 [drift] HP-7 与首页 L1 的媒体条目未反映 ADR-058 的点击手势

- Discovered: 用户对比 aihot.virxact.com 后指出我方列表图片居中且有大片灰底空档（实测 945×811 的图在 1072×210 的盒子里只画出 245×210，左右各约 413px），以及截图缩略图信息不全。ADR-058 据此把媒体盒改为收缩包裹左对齐，并把点击改为「lightbox 增强原生链接」。
- 契约现状：`ux-contract.md` HP-7 仍写「点击图片在新标签页打开大图（查看原图；进入原文走标题链接）」，首页 L1（`ux-contract.md:39`）仍写「媒体资产（文章配图，可点击查看原图）」。后者还残留 ADR-054 之前的「文章配图」措辞——列表早已只渲染 X 推文自带媒体。
- **需要的修订 · L1 承诺新措辞**（HP-7 那条）：「列表卡片的媒体缩略图是**该媒体实际资源 URL 的普通链接**（`<a href target="_blank" rel="noopener noreferrer">`；该 URL 当前是同源 `/img` 代理地址，见 [ADR-057](../adr/057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md)，契约不承诺它是图床直链）。应用不阻止浏览器对该链接的原生处理。应用**只**改变一种手势：**无任何修饰键的主键（左键）点击**——此时在当前页原地放大查看（Esc 或点击遮罩关闭；多图可左右切换）。lightbox 未能建立（脚本未加载、构造抛错）时，该手势也退回浏览器原生处理。」
- **需要的修订 · 首页 L1 新措辞**（`ux-contract.md:39` 那行）：「媒体资产（仅 X 推文自带的媒体，非 RSS 正文配图；无修饰键左键点击原地放大，其余点击手势交由浏览器原生处理）」
- **配对的 L2 验证条件**（每条只断言应用可控的部分，不断言浏览器 UI 的菜单项文案或选中后的结果）：
  1. 无修饰键左键点击 → 当前页出现遮罩、URL 不变、列表未导航离开。
  2. macOS 上 Cmd+左键（Windows/Linux 上 Ctrl+左键）→ 新标签页打开该媒体资源，当前页停留原处。
  3. 中键点击 → 不出现 lightbox，交由浏览器原生处理。
  4. macOS 上 **Ctrl+左键 → 不出现 lightbox**（该手势在 macOS 上是上下文菜单，吞掉它会废掉右键）。
  5. Shift+左键与 Alt+左键 → 不出现 lightbox（不断言具体结果，那依平台与设置而异）。
  6. 该 `<a>` 的 `href` 与同一元素内 `<img>` 的 `src` 相同、是同源 `/img` 路径（既非 `#` 也非 `javascript:`），且 `contextmenu` 事件未被 `preventDefault`——这两条就是「右键可用」的可验证形式。
  7. 注入使处理器抛错后，无修饰键左键点击 → 退回原生链接，且当前页无残留遮罩、无残留 `inert`、无残留滚动锁。
  8. 遮罩打开时 Tab/Shift+Tab 只在「关闭/上一张/下一张」间循环；Esc 关闭后焦点按降级链归还（trigger → 同条目标题链接 → `#list` → body），断言焦点不停留在已移除的遮罩内并记录最终落点；四级全失败时记为「已知未归还」而非 FAIL。
  9. 首页 `/` 上带媒体的卡片，其媒体元素只来自 `source_kind === "x"` 的条目——RSS 条目卡片不出现媒体元素（这条同时是 ADR-054 的回归哨兵）。
- 未覆盖轴（不写进契约、如实留白）：125/150/200% 缩放档、断点两侧取点（959/960/961）、Windows/Linux 上 Ctrl+左键的语义。

## 2026-08-14 [expansion] ux-contract 未覆盖 `/wechat` 冷连接首屏性能

- Discovered: 用户从 MacBook 打开 `https://news.aiplanet.live/wechat` 时经历数秒白屏，并明确要求同条件体感不弱于 AIHOT；EdgeOne 接入与 render-blocking CSS 优化据此实施。
- Description: 现行契约约束 `/wechat` 的 SSR 内容、搜索、分页与详情行为，但没有约束真正冷连接下 HTML 首包与首次内容绘制。缺少这一层时，功能测试与热连接读数都可能通过，用户仍会在首次访问时看到明显白屏。
- Recommendation: 在微信文章解读页的 L1 承诺中补充「真正冷连接下首屏等待不得显著慢于 AIHOT 对照」；配套 L2 固定为用户 MacBook 可见浏览器中交替测试 `/wechat` 与 AIHOT 首页，每方至少 5 次新浏览器 profile / 新连接，分别比较 median TTFB 与 FCP，二者均须不超过 AIHOT 的 110%。搜索、分页和详情只做功能与非回归验证，不为没有 AIHOT 对应面的路径制造替代性能指标。

---

## 2026-06-07 [expansion] ux-contract §微信文章解读页 未覆盖新增的搜索功能

- Resolution (2026-06-15): 已在 ux-contract.md `/wechat` 页面描述与 WX-4 写入 WeChat 专属搜索字段、LIKE/繁简/2 字行为、URL/分页/详情/404 上下文和空态，修正"v1 无搜索"旧描述。
- Discovered: execute-plan 实施 `20260607-wechat-interpretation-search`（/wechat 新增搜索框）后的 supervisor 收尾核查 + test-ux 验收。已上线公开站点 `/wechat`。
- Description: 契约 §微信文章解读页 当前只描述"列表卡片 + 站内详情"、无搜索；但 `/wechat` 已新增搜索框，且语义**刻意不同于**精选/全部页（后者匹配 标题/正文/来源名/作者/中文标题、≥3 字走 FTS）：
  - 匹配字段：原文标题 / 公众号名(作者) / 摘要(abstract) / 标签(tags)——**不搜正文、不搜结构化解读全文 summary_md、不匹配聚合 feed 来源名 s.name「微信公众号（Mp2RSS 合集）」**（匹配 s.name 会让全部条目命中）。
  - 一律 LIKE（无 ≥3 字 FTS 分支），繁简互通，2 字专名可搜。
  - 排序：公众号名(作者)命中优先于其他字段命中，其余按发布时间倒序。
  - 行为：debounce 即时收敛；翻页保持 `q`；URL `?q=` 同步、刷新/分享保持；清空恢复全量；详情页与 404 页的站内返回链接保持搜索态（`/wechat?q=...&page=...`）；无匹配显示空状态；placeholder = `搜索标题/公众号/摘要/标签…`。
- Recommendation: 在 §微信文章解读页 增「搜索」契约段，写明上述匹配维度与语义，**特别标注与精选/全部页搜索的差异**（不搜正文/解读全文、不匹配聚合来源名、按公众号作者优先），并补 URL `?q=` 同步、清空恢复、详情/404 返回保持搜索态、空状态文案 这些可验证行为，供下游 test-ux 据以验收。

---

## 2026-06-01 [expansion] ux-contract 未明确搜来源名时的排序承诺

- Resolution (2026-06-15): 已在 HP-4/TL-3 写入 `q` 生效时来源名/作者命中优先、同层按 `source_id` 轮转进入分页结果、无 `q` 保持时间倒序。
- Discovered: 中文/微信公众号源搜索可用性修复（#6）落地后，产品实现已在搜索态将 source name / author 命中的条目排在内容命中之前，并在同名来源之间用 source_id 轮转，避免高产同名源淹没低产公众号源。
- Description: `ux-contract.md` HP-4 已承诺"搜源名返回该源内容"，但未定义首屏排序语义。没有排序契约时，未来重构可能回退到纯时间序，导致 `歸藏` 这类同名 X + 微信公众号场景再次让公众号在首屏外。
- Recommendation: 在搜索契约中补充：有 `q` 时，source name / author 命中优先于 title/content-only 命中；同一命中层内按来源轮转保证每个命中来源首条在 page1 可见；无 `q` 时保留原时间/日期排序。

---

## 2026-05-28 19:20 [expansion] ux-contract 未明确 `/` 和 `/all` 首屏应 SSR 预载且不显示 loading spinner

- Resolution (2026-06-15): 已在 HP-1/TL-1/RS-3 写入 `/` 与 `/all` 首屏 SSR preload、HTML 到达即有 `.item-row`、不依赖初始 `/api/v1/*` fetch、无可感知 spinner。
- Discovered: SSR preload plan production verification for the public site after comparing the existing CSR loading behavior with AIHOT-style inline/preloaded content.
- Description: 当前实现已让 `/`、`/all` 和三个常见 deep link 在生产环境首屏直出 `.item-row`，Playwright gate 结果为 spinner 0、initial API 0，FCP median 均低于 1.5s。但 ux-contract 还没有把"主 feed 首屏应在 HTML/preload 阶段可见，不依赖初始 API fetch，也不出现可感知 loading spinner"作为行为契约写死。
- Recommendation: 在对应 Feed Reading / Initial Load contract 中补充：`/` 与 `/all` 的首屏内容必须通过 SSR preload 或等价机制在 HTML 到达后即可渲染；生产验证以 spinner 出现次数、首个 `.item-row` 时间、initial `/api/v1/*` 请求数为指标。

---

## 2026-05-29 [expansion] ux-contract 未约定图片加载行为（图床可达性 / 不阻塞首屏 / 懒加载），与 AIHOT 实现存在 parity gap

- Resolution (2026-06-15): 已在 HP-7 写入当前 shipped 图片 lazy loading 与失败隔离契约；未改变产品行为，未引入图片代理或额外属性。
- Discovered: 对比 `https://aihot.virxact.com/all` 加载机制的讨论收尾。AIHOT 首屏初次加载发起 26 个 `/api/img-proxy?u=<encoded-image-url>` 请求代理外部图床（主要是 X `pbs.twimg.com` 头像），并行下载且不阻塞 HTML 首屏渲染。AI Planet 现状是 `app.js` 渲染卡片时直接引用原始外部图床 URL（X `pbs.twimg.com`、各家 OG image 等），无服务端代理、无懒加载属性。
- Description: 现行 `ux-contract.md` Feed Reading 段只约束文本/标签/分数的首屏可见性，对图片只字未提。实际后果至少三条：(a) X 图床在国内网络不稳定，图片偶发失败/超时但 contract 未声明"图片失败不应影响阅读"或"图片必须可达"；(b) 大量并行图片请求与文本首屏共享 HTTP 连接预算，理论上可能拖累 `.item-row` 渲染（已通过 SSR prepaint 缓解但未量化）；(c) Off-screen 图片随 HTML 一并加载，浪费首屏带宽。AIHOT 通过 `/api/img-proxy` 同源代理把图床可达性收敛到自家 CF/服务器，并隐式启用浏览器 connection coalescing。
- Recommendation: 三选一或组合：
  - (a) **快胜**：现有 `<img>` 加 `loading="lazy" decoding="async"`，约束 contract："首屏外可视区域的图片不应在初次 HTML 加载阶段下载完成；图片失败不应影响 `.item-row` 文本可读性。" 工作量极低，立刻可做。
  - (b) **中期**：实现 `/api/img-proxy?u=<url>` 同源代理 + 服务端缓存（参照 AIHOT 命名约定保持 parity），契约约束图片源可达性 SLO（如 p95 < 500ms）。涉及缓存层与带宽成本，需要单独 plan 评估。
  - (c) **观测先行**：在做 (a)/(b) 之前，加一次 Playwright 性能 probe 测量当前生产 X 图床失败率与首屏阻塞情况，用数据决定优先级。
  推荐顺序：(c) probe → (a) 快胜立刻做 → (b) 视 probe 结果决定是否独立 plan。

---

## 2026-05-18 22:30 [drift] aihot-parity-contract §SourceParity-AboutSurfaceReflection 假设 AIHOT 通过 /about 暴露 source pool，实际 AIHOT /about 是个人介绍页 + 公众号 QR

- Resolution (2026-06-15): Obsolete/resolved：`aihot-parity-contract.md` 已在开源清理中移除，目标契约不存在，不再需要修订该 parity 条目。
- Discovered: 2026-05-18-r1 / s3-parity-auditor / Layer 1 跑测时对照 AIHOT `/about`
- Description: `aihot-parity-contract.md §SourceParity-AboutSurfaceReflection` 暗含"两端 /about 都暴露 source table"的假设；实测 AIHOT `/about` (`evidence/s3/aihot-about.png`) 是"嗨,我是数字生命卡兹克 / 这个站是我做的,免费给大家用" + 公众号 QR，不暴露任何 source pool。AIHOT 的源池只能从 `/all` / `/curated` 卡片头像 + handle 推断。AI Planet `/about` 暴露 41 行 source table 是设计差异，不算 issue（VISION §6 透明原则），但当前契约措辞会让下游 test-ux 误以为可以两端 `/about` 直接对照。
- Recommendation: 修改 §0 参照锚点表中 `信源池真值` 一栏，对 AIHOT 改为 "公开站点暴露源（卡片头像 + handle，不通过 /about）"；并把 §SourceParity-AboutSurfaceReflection 改为 AI Planet 内部一致性测试（`sources.toml` ↔ `/about table`），不再要求与 AIHOT 对照。

---

## 2026-05-18 22:30 [drift] ux-contract §Feature-DailyNav 与 §Feature-DailySections 在"合法日期 + 无内容"上承诺重叠/冲突

- Resolution (2026-06-15): 已在 DY-2 拆分边界：非法/不可解析日期切最近一期并显示 fallback banner；合法但无数据日期保留该日期并显示明确空态。
- Discovered: 2026-05-18-r1 / s4-responsive-and-edges / Issue 6（也被 s1-first-time-visitor Issue 2 在 `/daily/1999-01-01` 上独立交叉验证）
- Description: §Feature-DailyNav 边界承诺：「访问 `/daily/<无效或无内容日期>` 时静默切到最近一期，并显示 fallback banner」；§Feature-DailySections 边界承诺：「某日全节皆空时整个 sections 区显示明确空态文案而非白屏」。两条边界在"合法日期格式但无数据"上重叠：当前实现是 `/daily/9999-99-99`（非法格式）走 §Feature-DailyNav fallback banner，`/daily/2000-01-01` 或 `/daily/1999-01-01`（合法格式 + 无数据）走 §Feature-DailySections 空态文案。契约没区分"非法格式 vs 合法 + 无内容"两种情形，导致同样是无内容用户拿到两种不同体验。
- Recommendation: 拆分边界承诺。建议措辞：
  - §Feature-DailyNav 边界："访问 `/daily/<非法日期格式>` 时静默切到最近一期 + fallback banner。"
  - §Feature-DailySections 边界（保留）："某日全节皆空时显式空态文案，不白屏。"
  - 或者反之：合法 + 无内容也走 fallback。两选一并写死。

---

## 2026-05-18 22:30 [drift] ux-contract §Feature-Pagination 措辞"超范围 page 返回空列表"，实现是 clamp 到 max page

- Resolution (2026-06-15): Resolved：ux-contract.md HP-8/TL-4/WX-4 现均明确越界页码 clamp 到最后一页，契约已与实现对齐。
- Discovered: 2026-05-18-r1 / s4-responsive-and-edges Issue 5 + Issue 8（s2-returning-power-user Issue 5 也在 `?page=999` 上看到了长 loading 后才发生 clamp）
- Description: §Feature-Pagination 边界："超范围 page 返回空列表，分页器仍可回退；page<1 或非数字按 1 处理。" 实测 `/all?page=999` 经过 ~9s loading 后 URL 被前端改写为 `/all?page=16`（最后一页），渲染该页内容；`/all?category=ai-models&page=2`（超范围因为 ai-models 只 1 页）则 URL 被改写为 `/all?category=ai-models`（直接剥掉 page 参数）。两种行为都不是契约措辞的"返回空列表"。
- Recommendation: 二选一并写死：
  - (a) 实现回到契约："超范围 page = 空列表 + 分页器可回退 + URL 保留"；
  - (b) 契约跟实现："超范围 page = clamp 到 max page，URL 同步改写为 max；带 filter 且总页数 1 时剥掉 page 参数。"
  目前的混合行为让深链复用 / monitoring / 用户预期都不稳定。

---

## 2026-05-29 07:15 [expansion] ux-contract 未覆盖 wechat（微信公众号）源类型及其"未 enrich 时抑制正文预览"的展示规则

- Resolution (2026-06-15): 已在 TL-2 写入微信公众号来源归入"资讯"、未 enrich 时抑制正文预览、enrich 后显示中文摘要、标题回链 mp.weixin 原文。
- Discovered: execute-plan 实施 `20260528-wechat-oa-ingestion`（新增 `kind="wechat"` 源）后的 supervisor 收尾核查。
- Description: 新增 wechat 源（首批 歸藏的AI工具箱 / 十字路口Crossing）归入"资讯"频道（`kind != "x"`），在 `/` 与 `/all` 同普通 feed 源一并展示。但有一处 wechat 特有的展示规则未写入 ux-contract：出于合规（不公开转载公众号正文），wechat item 在 web 层**抑制 `content_preview`**——未 enrich 的 wechat 卡片正文区为空（仅中文标题 + 回链 mp.weixin），enrich 后才显示 `summary_zh`；而普通 feed 源未 enrich 时仍显示 `content_preview`（正文前 320 字）。当前 ux-contract（§TL-2 信源类型筛选只列 一手信源/资讯/推文；卡片展示默认有 preview/摘要）未反映这点，下游 test-ux 可能把"未 enrich 的 wechat 卡片无正文预览"误判为 bug。
- Recommendation: 在 ux-contract 补充 wechat 源的展示契约：(a) wechat 源归入"资讯"类型（feed/x/wechat 三类信源）；(b) 卡片正文：enrich 后显示中文摘要，未 enrich 时仅标题 + 回链（正文不对外公开，合规要求）；(c) 点击标题回链到 `mp.weixin.qq.com` 原文。

---

## 2026-05-18 22:30 [expansion] ux-contract §Feature-CategoryFilter 未明确"无效 slug 静默回退时是否清掉 URL 上的脏参数"

- Resolution (2026-06-15): 已在 HP-3 写入 `/?category=<无效 slug>` 静默回退到"全部"并由客户端剥除无效 `category` 参数。
- Discovered: 2026-05-18-r1 / s2-returning-power-user Issue 9（深链 `/?category=invalid-slug` 测试）
- Description: §Feature-CategoryFilter 边界："无效 slug 静默回退到「全部」（不报错）。" 实测 `/?category=invalid-slug` 行为：列表正确渲染全部精选 ≈5s 后地址栏被改写为公开站点根路径（脏参数被剥）。契约没说要清也没说要保留。两种行为各有理由：清 → 防止用户把坏链发出去再次复制；保留 → 让 admin / monitoring 看到误配。
- Recommendation: 在 §Feature-CategoryFilter 边界条目补一句明确，例如：「URL 保留无效参数以便排错」或「URL 清掉无效参数防止扩散」。同理 §Feature-ChannelFilter 也需补；§Feature-Pagination 的 page<1 / 非数字行为同样未说 URL 是否清——可以一并归类为"无效 query 参数的 URL 处理策略"统一段落。
