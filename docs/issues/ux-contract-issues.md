# UX Contract Issues

> Mutable。test-ux 跑测中发现的、与 ux-contract 演化相关的观察。domain 文件只存 **open** 条目；判定 resolved / wontfix 时整条移入 [archive/closed.md](archive/closed.md)。owner sweep 后决定是否升级为契约修订。
>
> 协议：`~/.claude/references/docs-organization-protocol.md` §4.8——该节同时承载「ux-issues.md 与本文件只能由真实端到端产品观察写入」这条约束。
> type 语义：`drift`（契约声 X 实际 Y）/ `expansion`（未覆盖但合理的扩展候选）/ `redesign`（契约结构本身改进建议）。
>
> 契约演化候选**不由 agent 直写契约**（协议 §4.6 fallback）：本文件的条目由用户经 `/custom:create-ux-contract` 处理。

---

## [open] 2026-08-31 [expansion] 微信搜索契约未覆盖跨字段必需词、正文检索与受控评测词同义语义

- Discovered: 用户在公开 `/wechat` 搜索栏输入《即梦 Seedance 2.5 实测》后得到 0 篇；本轮用当前数据库快照复现了严格字面词错位——目标标题写“狂测”，解读写“评测”，没有字面“实测”。这是用户真实入口的失败报告；新实现尚未部署，下面的 L2 是上线后需要执行的端到端确认。
- Description: 现有契约只承诺标题、公众号、摘要和标签的整串搜索，没有规定多词可分散在不同字段、正文与完整解读可搜索，也没有规定同一评测概念的受控词汇错位。实现现已选择每个查询词都必须满足、每词可跨字段命中，并只扩展 `实测/评测/测评/狂测`；如果契约不演化，未来既可合法退回原来的 0 结果，也可合法放宽成缺词召回或用“体验”造成噪声，两种相反回归都不会红。
- Recommendation: 在微信搜索 L1 中补充：查询按空白与标点拆成必需词，每词可跨标题、原始公众号、正文、摘要、标签和完整解读命中；简繁兼容保留；严格阶段为零结果时启用空白不敏感兜底；`实测/评测/测评/狂测`是受控同义组，但同义词不触发作者优先，raw 作者、raw 标题、alias 标题依次排序。**L2 验证条件**：部署后从 `/wechat` 搜索 `即梦 Seedance 2.5 实测`，目标《刚刚，即梦 Seedance 2.5来了！我狂测测测测...》出现在第 1 条；搜索 `Seedance2.0 分镜 Skill` 时目标分镜文章出现在第 1 条；搜索 `分享ClaudeCode` 能在严格阶段零结果后命中标题含 `分享Claude Code` 的文章；构造一条只在作者名含“评测”、其余字段均无同义词的反例，搜索“实测”时不出现；删除任一必需词命中的正例不应被多词查询召回。严格阶段已有其它结果时，compact-only 候选仍是已知未覆盖边界，发现与回退按 ADR-20260901-a31f。

## [open] 2026-08-20 [drift] 契约通篇钉 `aiplanet.live`，生产公开域名是 `news.aiplanet.live`

- Discovered: 2026-08-20 sync-docs 审查逐条核对契约与生产实况时发现（本条属文档核对，不是端到端观察结论——见下方 Recommendation 里的 L2 验证条件）。
- Description: `ux-contract.md` 有 4 处写死 `https://aiplanet.live`：L10「产品形态」、L225（精选页 HP-1 的验证步骤入口）、L562 与 L576（Quality Bar 的适用面与可用性判据）。而当前公开站点是 `https://news.aiplanet.live`（经 EdgeOne DNS-only CNAME 接入，见 [ADR-039](../adr/039-route-news-through-edgeone-dns-only-cname.md)），旧域名待下线且已知返回 502。照契约字面执行验收，会在一个不再服务的域名上取读数——失败形态是「契约测试红了，但产品是好的」，或更坏地把 502 当成产品故障。
- Recommendation: 4 处全量改写为 `https://news.aiplanet.live`；旧域名如需保留，只作为「已下线的历史域名」在一处注明，不出现在任何验证步骤里。**L2 验证条件**：浏览器打开 `https://news.aiplanet.live` 首页返回 200 且渲染出 `.item-row`；同时确认契约内不再有任何裸 `https://aiplanet.live` 的验收入口。

## [open] 2026-08-20 [drift] 信源数量三处口径互相矛盾，且都与 `sources.toml` 实数不符

- Discovered: 2026-08-20 sync-docs 审查交叉核对 L20 / L308 / L417 / L595 与 `data/sources.toml`。
- Description: 契约里同一个事实有三个不同取值：L20「40+ 个信源」、L595 的 Quality Bar 表「当前约 41 个信源，20 为保守下限」、L308 与 AB-1（L417）「精确 161 个：109 个 X、34 个原始 Feed、18 个原始 Web/API 列表」。实测 `data/sources.toml` 当前 **163 条、全部 enabled**（`tomllib` 计数，2026-08-20）。「约 41」是 AIHOT 来源对齐（ADR-047 那一轮）之前的旧数，早已失效；而 161 与 163 的差恰是 [ADR-059](../adr/059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md) 双跑引入的两个微信源。AB-1 把「精确 161 行」写成 hard contract，意味着每加一个源契约就红一次——这类断言的维护成本会持续把注意力从真问题上引开。
- Recommendation: 单点化并去精确数字：L20 与 L595 改为「百余个信源」一类不随增删漂移的措辞，把可核数字交给命令而非散文；AB-1 改为断言「About 页展示的主站来源行数等于 `sources.toml` 中主时间线来源的数量」这一**关系**，而不是某个具体常数。**L2 验证条件**：`python3 -c "import tomllib;d=tomllib.load(open('data/sources.toml','rb'));print(len(d['source']))"` 的输出与 `/about` 页面实际渲染的来源行数相等；契约内不再有互相矛盾的信源常数。

## [open] 2026-08-20 [drift] 契约假设单一微信来源，ADR-059 后是双源并集 + 跨源去重

- Discovered: 2026-08-20 sync-docs 审查核对 [ADR-059](../adr/059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md) 与契约措辞。
- Description: 契约多处把微信摄取写成单一 Mp2RSS 聚合源：L179 与 L455 的搜索契约明确排除聚合来源名「微信公众号（Mp2RSS 合集）」，L308 与 L417 的 About 页契约写「配置后另显示**一行** WeChat 来源」。ADR-059 之后实际是 Mp2RSS 与 Wechat2RSS **两个源并行取并集**（`data/sources.toml` 中 `kind = "wechat"` 两条，其一 slug `wx_wechat2rss`），按「账号 + 归一化标题 + 5 分钟发布窗」跨源去重。契约照旧读会产生两类误判：About 行数对不上被判回归；双源期间同一篇若真出现两张卡，反而因为契约没写过这个不变量而无人拦。
- Recommendation: 把 About 页那条改为「每个已配置的微信来源各一行」，搜索契约里的聚合来源名改为按来源集合表述；并新增一条跨源去重的可观察承诺：「同一公众号文章在双源期间只出现一张卡片」。**L2 验证条件**：在两个微信源都启用的窗口内，于 `/wechat` 搜索一篇已知双源都收录的文章标题，结果只有一条；`/about` 的 WeChat 来源行数等于已配置的微信源数。

## [open] 2026-08-20 [expansion] 评分标签形态（`AI 评分 n` / 窄屏 `AI n`）已上线，契约只写「数字分数」

- Discovered: 2026-08-20 sync-docs 审查核对 [ADR-056](../adr/056-label-the-score-instead-of-showing-a-bare-number.md) 与契约措辞。
- Description: 契约 L38 写「分数标签：独立位于卡片右上（数字分数，颜色编码：≥80 高分、65-79 中等、<65 低调）」，L68、L230、L233、L299 同样只说「分数标签」。实际实现（`web/static/app.js:336` 与 `web/templates/_prepaint_list.html:35`）渲染的是 `<span class="timeline-score-label">AI<span class="timeline-score-label-rest"> 评分</span></span> {score}`——桌面读作「AI 评分 89」，≤960px 时 `-rest` 被收起、视觉上只剩「AI 89」，而「评分」二字仍留在可访问性树里。契约没记这个形态，于是「裸数字」这个已被明确否决的旧形态在契约上仍然合法，下一轮重构可以合规地退回去。
- Recommendation: 在 L38 补写标签形态与其窄屏收起规则，并明确「不写死分母」（ADR-056 的另一半决策，理由见 `ux-issues.md` 里 T1 条目 weighted_score 超 10 的实测）。**L2 验证条件**：桌面视口下列表卡片的评分元素文本匹配「AI 评分 <数字>」；≤960px 下可见文本为「AI <数字>」而其可访问名仍含「评分」。

## [open] 2026-08-20 [expansion] RS-5 未记「新访客默认跟随系统主题」

- Discovered: 2026-08-20 sync-docs 审查核对 [ADR-055](../adr/055-default-new-visitors-to-system-theme.md) 与 RS-5。
- Description: RS-5「主题三态」只承诺三态切换、localStorage 持久、滑动底板指示与无闪烁，**没有说没有 localStorage 时默认落哪一档**。实现（`web/templates/index.html:8` 的 head 内联脚本）在读不到 `ai-radar:theme` 时取 `"system"`，再按 `prefers-color-scheme` 决定明暗；ADR-055 正是为此改的默认值。契约缺这一条，意味着「新访客默认深色」这个改动前的行为在契约上依然合法。
- Recommendation: RS-5 补一条：「无持久化偏好的首次访问默认为『跟随系统』档，实际明暗由 `prefers-color-scheme` 决定。」**L2 验证条件**：清空该站 localStorage 后首次访问，`data-theme-mode` 为 `system`，且 `data-theme` 与操作系统当前明暗一致（切换系统外观后刷新，`data-theme` 随之变化）。

## [open] 2026-08-20 [expansion] HT-1 / HP-10 无「榜单尚未就绪」态，会把 ADR-060 的正确行为判成空态缺失

- Discovered: 2026-08-20 sync-docs 审查核对 [ADR-060](../adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md) 与 HT-1 / HP-10。
- Description: ADR-060 之后热点榜由后台刷新的候选缓存供给、请求路径永不同步计算：缓存未就绪时 `/hot` 返回 **503**（`src/airadar/web/routes/curated.py:171`）而非 200 + 空列表，页面显示「热点榜单正在生成，稍后自动刷新」并有界自动重载（`web/templates/hot.html:98`）；首页 HP-10 的热点块在冷态下退避而不是渲染空模块。契约只有两态——HT-1 的「渲染 API 全部响应项」与「榜单为空时显示显式空状态」，HP-10 的「严格显示前 2 条」。第三态（未就绪）没有位置，于是**每次重启后的冷窗口都会被验收判成 FAIL**：503 不是 200，「正在生成」也不是空状态文案。这类误判最坏的走向是驱动把 ADR-060 刚拆掉的同步计算改回去。
- Recommendation: HT-1 与 HP-10 各补一条未就绪态：`/hot` 在候选缓存未就绪时返回 503 并显示「正在生成」提示与自动重载，**且该态必须自愈**；首页热点块在该态下不渲染空模块。同时把「空」与「未就绪」在契约措辞上分开——两者的正确产品行为不同。**L2 验证条件**：重启 serve 后立即访问 `/hot`，观察到 503 与「热点榜单正在生成」提示；不做任何操作，在后台刷新完成后页面自动恢复为正常榜单（同一次访问内自愈，无需手动刷新）。

## [open] 2026-08-18 [drift] HP-7 与首页 L1 的媒体条目未反映 ADR-058 的点击手势

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

## [open] 2026-08-14 [expansion] ux-contract 未覆盖 `/wechat` 冷连接首屏性能

- Discovered: 用户从 MacBook 打开 `https://news.aiplanet.live/wechat` 时经历数秒白屏，并明确要求同条件体感不弱于 AIHOT；EdgeOne 接入与 render-blocking CSS 优化据此实施。
- Description: 现行契约约束 `/wechat` 的 SSR 内容、搜索、分页与详情行为，但没有约束真正冷连接下 HTML 首包与首次内容绘制。缺少这一层时，功能测试与热连接读数都可能通过，用户仍会在首次访问时看到明显白屏。
- Recommendation: 在微信文章解读页的 L1 承诺中补充「真正冷连接下首屏等待不得显著慢于 AIHOT 对照」；配套 L2 固定为用户 MacBook 可见浏览器中交替测试 `/wechat` 与 AIHOT 首页，每方至少 5 次新浏览器 profile / 新连接，分别比较 median TTFB 与 FCP，二者均须不超过 AIHOT 的 110%。搜索、分页和详情只做功能与非回归验证，不为没有 AIHOT 对应面的路径制造替代性能指标。

## [open] 2026-08-20 [drift] 契约称站内详情页「唯一」，而 `/item.html` 是可达的第二个详情页，NG-1 也没把它列进负向断言

- Discovered: 2026-08-20 sync-docs 审查核对契约与路由表时发现。**来源是代码核对，不是端到端观察**——本条按本文件的写入约束标注取证等级，L2 是端到端确认它，不是重复这次核对。
- Description: 契约两处把站内详情页写成独占：L189「产品中唯一的站内条目详情页（其余页面点标题跳原文）」、L200「**唯一例外**是微信文章解读」。而 `web/static/item.html` 是一个完整的独立页面（自带侧栏、主题内联脚本、`/style.css?v=` 引用），由 StaticFiles 隐式提供于 `/item.html`，`web/static/app.js` 里有配套的 `GET /api/v1/items/<id>` 取数逻辑；`docs/architecture.md` 的路由表也把它列为「单条详情页」。仓内核对（`git grep item\.html` 于 `web/templates/`、`web/static/*.js`、`src/airadar/web/`）显示**没有任何页面链接到它**——它是一个孤立但可直接访问的入口。
- 两个后果：契约的「唯一」在字面上是假的；而 NG-1「明确不提供的入口与路由」只点了 `/topics`、`/agent`、`/feedback`，没有对 `/item.html` 表态——于是它既不算承诺的功能，也不算明确不提供，处在契约的盲区里。盲区的坏处是双向的：删掉它不会红任何断言，留着它也没有任何可验收的行为。
- Recommendation: 先由用户裁决它的去留，再择一入契约——(a) 认可它是产品面，把它写进 Surfaces 并给出 L1 承诺（至少：无导航入口、仅凭 URL 可达、渲染哪些字段、条目不存在时的行为），同时把 L189/L200 的「唯一」改为「唯一由卡片点击进入的站内详情页」；(b) 判定它是历史遗留，从 `web/static/` 移除并在 NG-1 补一行。**L2 验证条件**：取一个真实条目 id，浏览器访问 `/item.html?id=<id>` 并记录实际渲染结果（选 (a) 时断言它符合新写的 L1；选 (b) 时断言该路径返回 404 且站内无任何链接指向它）；同时断言契约内不再有与之矛盾的「唯一」措辞。

## [open] 2026-08-20 [expansion] ICP 备案页脚在 10 个页面上渲染，契约零覆盖

- Discovered: 2026-08-20 sync-docs 审查核对模板与契约时发现。**来源是代码核对，不是端到端观察**——L2 是端到端确认它。
- Description: `web/templates/_icp_footer.html` 由 10 个模板 include（`index` / `all` / `hot` / `wechat` / `wechat_detail` / `wechat_404` / `about` / `bookmarks` / `changelog` / `more`），在配置了 `icp_beian` 时渲染一个指向备案查询站的外链页脚。仓内核对：`ux-contract.md` 内 `ICP|备案` 命中 **0**。于是一个出现在几乎全部公开页面、且带合规含义的元素，没有任何可观察承诺——它渲染错了、链错了、或在某个页面漏了，都不会红任何断言。
- 与前一条的区别: 那条是契约说了假话，这条是契约没说话。合规性元素属于「漏掉比说错更危险」的一类——它的缺席在页面上看起来完全正常。
- Recommendation: 在全站层（NG-1 同级的全站断言处）补一条：「配置了备案号时，全部公开页面底部渲染备案号，且链接指向备案查询站并以新标签页打开；未配置时该页脚整体不渲染，不留空盒。」注意 fork 部署者通常不配备案号，所以「未配置时不渲染」和「配置时渲染」同等重要，契约要覆盖两侧。**L2 验证条件**：配置 `icp_beian` 后逐一访问上述 10 个页面，各断言底部存在备案链接且 `target="_blank"`、`rel` 含 `noopener`；清空该配置后重复一遍，断言 10 个页面均无 `.icp-footer` 节点（而非渲染为空）。
