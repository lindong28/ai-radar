# Changelog

## 2026-08-29（X 账号正常停更不再触发来源静默红色告警）

- A7 现在会区分“X 来源很久没发帖”和“本地抓取没有追上”：最近一次 X timeline 读取已完整排空、游标已提交且仍在 pipeline heartbeat 窗口内时，来源会显示为“上游未更新、最近读取已追平”，不再要求立即处置。
- X 抓取失败、分页尚未排空、收据非法或超过 120 分钟时不会被这个分支掩盖，仍按原有静默规则报警；RSS、Web 与微信来源的判定不变。
- 2026-08-29 的生产快照回放中，AI at Meta、Nathan Lambert 与 Alibaba Cloud 从 A7 firing 集合移入已追平集合；把同一快照中的 AI at Meta receipt 改成 `blocked` 后，它立即重新进入 firing，证明该分支没有把真实失败一并静音。

## 2026-08-29（明确写出判定理由的解读不再因遗漏末尾 JSON 而停滞）

- summary-agent 现可在末尾 JSON 遗漏 `criteria_reason` 时，从「价值判断」模块唯一且与最终推荐等级一致的同行括号理由中补取；冲突、多条、空理由或跨行理由仍按 schema 错误处理，不会猜测。已有的一次即时重试与数据库退避继续覆盖无法机械补取的输出。
- 每次 Markdown fallback 命中都会把来源 marker 与 Summary Agent 用户命名空间原子写入微信解读记录，并在 pipeline stdout 记录 item、用户、最终 slug 与原文 URL 的 SHA-256；完整 URL 不进入审计日志。审计坐标不再依赖可能缺失或写入失败的 LLM 计量记录，无论文章最终入不入知识库都能精确定位受影响结果。
- 外部 summary-agent 的出网兼容收据从只绑定两层 shell wrapper 的 v1 升为绑定实际 Python 实现闭包与生产 `summarize`、本地 `check-url`、known/unknown tag 保存路径的 v2。实现或 selector policy 漂移而尚未重新验证时，interpret 会安全跳过，不再把“wrapper 没变”误当成内部网络实现仍受控。

## 2026-08-28（微信解读遇到缺失判定理由时立即补试一次）

- summary-agent 偶发返回缺少 `criteria_reason` 的结构化结果时，微信解读会立刻用同一命令补试一次，不再一律等待至少 15 分钟后才重新处理。该补试只覆盖这一条精确错误，余额、配额、网络与其它 schema 错误不会被额外调用。
- pipeline 日志会分别标出开始补试、补试恢复与补试耗尽；第二次仍失败的文章继续进入原有数据库退避，不会无限重试。

## 2026-08-26（AI Radar 出网不再继承整进程 GCP 代理）

- pipeline 每轮先验证外部 `domain-routing-v1` selector，并绑定 `status_schema_id=agent-domain-routing-status-v1` 与 OpenAI provider aggregate scope；状态缺失、不完整、schema/policy 不匹配或任一路线非 healthy 时，在 fetch/LLM 等外部阶段启动前停止，不再从 `AI_RADAR_PROXY_FILE` 或父 Claude Code/Codex 的 proxy 环境自动选路。
- 已登记的 httpx、OpenAI-compatible SDK、urllib、Playwright 与受管子进程显式使用 status-derived selector：Anthropic 由 router 送 GCP SG 且 fail closed，OpenAI/ChatGPT/X 送 OpenAI provider route（Tencent primary、ZYT fallback），Ark/DeepSeek/RSS/新闻/网页默认 direct。应用 audit 只记录 callsite、hostname、launch、policy identity 与本地结果；实际 `tencent` / `zyt-fallback` route 与 outcome 仍以 system-config 的 route audit 为准。
- 外部 `AI_ASSISTANT_ROOT` 只有提供与脚本摘要绑定的 selector compatibility receipt 才会启用；未证明兼容时 `interpret` 按既有语义安全跳过。`/img` 的 ADR-057 独立图片代理保持不变。
- 本次代码验收使用隔离 fake status/selector 与动态 loopback listener；macmini 的真实 GCP/Tencent 出口、断线与 fail-closed 验收仍属于部署步骤，不能从本地测试外推。

## 2026-08-21（「AI 日报」「收藏」「更多」「关于」四页此前完全没有缓存）

- **这四页之前每次点开都要回源重算一遍。** 它们不发任何缓存指令，于是浏览器和边缘节点都无从缓存——实测这四个路由每一次请求都是 `eo-cache-status: MISS`，而同侧栏的「精选」「全部 AI 动态」早已是命中。现在它们与那两页发同一档指令（90 秒内可复用，之后 30 秒内可以先给旧的、后台去取新的）。
- **同一次实测里的对照读数**：在左侧栏点「精选」↔「全部 AI 动态」，首次约 1.3 秒、之后 71–95 毫秒；而点「AI 日报」约 631 毫秒、「收藏」约 692 毫秒——差出 7 到 9 倍的正是这一项。
- **带任何查询参数时一律不缓存**，包括 `?&&` 这种解析下来没有参数的写法：这四页本来就不读查询参数，出现参数说明这不是它们建模过的地址。
- **「收藏」能这样做的前提是它的内容与访客无关**——收藏项一直存在你自己的浏览器里，服务端返回的只是一个空壳。这一点由测试守着：只要将来某个页面自己声明了「不可共享」，中间件不会再把它改回可共享。
- **两边都已生效。** 边缘节点要遵循这些指令，还需要在 CDN 控制台把这四个路径加进对应规则——同日已一并改完。上线后从公网实测：四个路由都首次 `MISS`、随后 `HIT`；点开它们的等待从此前的 631 / 692 毫秒降到 155–391 毫秒（两次测量的完成判据不完全相同，方向与量级确凿，精确倍数不宜宣称）。

## 2026-08-20（在「精选」和「全部 AI 动态」之间来回点，不再等）

- **两个页面本身变快了。** 之前打开「精选」时，服务端要为当页 40 条各拼一个模糊匹配、把全站 5 万多篇正文扫一遍，只为算出卡片上那个「关联讨论 N 条」的角标——这一步单独占掉 1 秒。现在这层关系是预先算好、带索引的。「全部 AI 动态」那边是另一个毛病：**检查缓存有没有过期比查数据本身还贵**，它要在一张 8 千行却占 688 MB 的表上做三次全表扫描；加了索引之后这三次几乎不花时间。
- **切换本身不再重新加载整个页面。** 在左侧栏这两项之间点击时只替换内容区，不白屏。切过去和普通跳转一样从顶部开始；**按后退回来时，会回到你原先滚动到的位置**（以前整页重载会把它丢掉）。鼠标移到链接上时就开始预取，所以点下去通常已经准备好了。
- **只有这两项之间如此。** 分类筛选、搜索、翻页，以及「热点榜」「AI 日报」「微信文章解读」和文章页，都还是原来的跳转方式——它们各自带着这套机制没有复制的状态。移动端那个指向「全部 AI 动态」并直接定位到搜索框的搜索按钮也保持原样，否则你点了搜索却落不到搜索框上。
- **任何一步出问题就退回普通跳转**：拿不到页面、拿到的页面不完整、初始化失败，都会交还给浏览器自己跳转，而不是把你留在一个半成品页面上。
- **切换本身快了多少，目前没有可靠数字。** 上面两页服务端变快是实测的；但"不重载文档"这件事到底为你省下多少毫秒，本地量不出来——试过的四种测法各自因不同原因不成立（详见 ADR-062）。能确定的是结构上的差别：不白屏、不丢滚动位置、侧栏状态跟着走。真实数字要等它上线后在公网上按既定口径量。
- 三层成因、实测读数与仍未验证的部分见 [ADR-062](https://github.com/lindong28/ai-radar/blob/main/docs/adr/062-cut-the-switch-cost-at-the-query-the-edge-and-the-navigation.md)。

## 2026-08-20（热点榜改由后台缓存供给：不再等它现算，名次可能有变化）

- **热点榜不再在你打开页面时现算。** 榜单候选由后台线程持续刷新，请求只取现成结果，所以首页热点块与 `/hot` 不会再因为一次冷算而长时间空着。
- **刚重启、缓存还没建好时不会给你一个假的空榜**：`/hot` 显示「榜单正在生成」并自动重载，首页热点块保持占位骨架、按退避自动重试（约十几秒内就绪），不会出现「明明有内容却显示空」的情况。
- **部分名次会与之前不同。** 旧实现只从最近 600 条里挑热点，窗口内更热但排在 600 条之外的条目会被静默丢掉；现在不再有这个截断，因此某些时段的榜单成员和顺序会变化——这是修正，不是排序规则改了。
- **代价是新鲜度**：你看到的榜单最多可能比实际数据陈旧约 5 分钟（后台刷新上界叠加边缘缓存）。热点取的是 48 小时窗口，这个滞后不改变榜单含义。
- 设计与实测读数见 [ADR-060](https://github.com/lindong28/ai-radar/blob/main/docs/adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md)。

## 2026-08-20（外部评审后的修复：去重键收窄，长链不再问那个缓存索引）

- **跨源去重的判据改窄了**：归一化不再剥离标点——两个源都出现的 126 篇里 125 篇原始标题逐字相同、唯一例外只差一个 U+00A0、标点差异为 0，所以那一层剥离折叠的是没人产生的噪声，代价却是把 `报告：1.0！` 与 `报告10` 并成同一篇（并掉就是永久丢文章）。同时排除条目自己所属的 source：同源身份由 URL 与正文哈希裁定，那是按"文章是什么"判的，不该被"它叫什么"推翻。
- **长链不再去问外部摘要索引**，连请求都不发。上一条只是丢弃它的答案，但那次查询本身用 `check=True`，非零退出会挡住这篇文章被总结；而比标题也救不了——生产历史里确实有 26 对同账号同标题的不同文章，同标题命中照样会把 A 的摘要发给 B。
- 跨源去重只匹配 **enabled** 的来源，这是一个有两面的取舍：停用一个源后它独有的文章会从 `/wechat` 消失（落在另一源 feed 窗口内的会被补回），而重新启用会让同一篇出现两张卡。选它是因为另一种写法下隐藏行会持续拦住每一次插入、那些文章**永久补不回来**。重新启用前的清重步骤写在 [operations/wechat-ingestion.md](https://github.com/lindong28/ai-radar/blob/main/docs/operations/wechat-ingestion.md)，取舍本身记在 [issues/general.md](https://github.com/lindong28/ai-radar/blob/main/docs/issues/general.md)。
- 某个 feed 不给 author 或 pubDate 时，同一篇仍会入库两次（两个源目前每条都给）。**没有静默接受**：这种情况现在会打一条 warning，把"两个 feed 总是给 author"从一个默默成立的假设变成一个会被看见的事实。

## 2026-08-20（修复：双跑首批 10 篇文章挂着同一份别人的摘要）

- 上一条双跑上线后，自建源首批入库的 **10 篇文章在 `/wechat` 上全部显示同一份摘要**（一篇讲留学生回国数据的文章），标题各不相同而正文、摘要、标签、`/wechat/<slug>` 地址全都指向那一篇。已修复，10 条错误解读已删除，下一轮 interpret 会重做。生产窗口约半小时。
- 根因不在双跑本身，而在解读阶段的一次缓存查询：它把文章 URL 交给外部 summary-agent 的索引问"这篇是不是已经总结过"。**那个索引按 URL 建键，而所有长链形态的微信文章 URL 路径都是 `/s`**（短链是 `/s/<token>`，各不相同），于是长链之间它一个也分不开。判别性对照：拿一个 `__biz` 完全虚构、根本不存在的 URL 去问，它同样返回 `found: true` 并给出那篇文章的 slug 与摘要文件。此前微信文章只从 Mp2RSS 来、全是短链，这个缺陷一直没有对象；自建源出长链，它就一次性命中了整批。
- 修法是**校验它的答案而不是猜它何时会撒谎**：缓存条目自带 `title`，与本篇标题不符时判为未命中、照常重新总结。缓存条目没有声明标题时保持原有行为——本次观察到的错误命中都带着（错的）标题，对"无标题"收紧只会白白多花模型钱而不修任何已观察到的问题。
- 教训记一条：错误摘要在库里、在页面上都长得完全正常——标题对、有正文、有标签、`processed=10 errors=0`。发现它靠的是核对 slug 与标题是否对应，而不是任何一处报错。

## 2026-08-20（微信来源双跑：自建 Wechat2RSS 与 Mp2RSS 并行，同一篇文章只存一次）

- 自建的 Wechat2RSS 作为第二个生产微信来源 `wx_wechat2rss` 上线，与付费的 Mp2RSS **并行运行**，`/wechat` 显示的是两者的并集。此前它只做只读影子对比、不进生产。**Mp2RSS 没有停用**——先双跑再决定停不停，因为只有真正同时跑过才知道停掉会丢什么。
- **同一篇文章不会出现两张卡片。** 两个源给同一篇文章的 URL 没有公共子串（Mp2RSS 出短链 `/s/<token>`，自建源出长链 `?__biz=…&mid=…&idx=…&sn=…`），而既有去重按 URL 与正文哈希、且作用域限于单个来源，两条都识别不出。新增的跨源去重用**账号 + 归一化标题 + 发布时间相差不超过 5 分钟**。若不带时间窗只按标题，会误并约 0.8% 的条目——生产库 3272 条微信条目里有 26 对是同账号真实重发的同标题文章（招聘启事、会议推广隔几小时再发一次），把它们并掉就是真丢文章。5 分钟这个值取自两侧读数之间的空隙：同一篇文章在两个源的发布时间**最大只差 58 秒**（中位 13 秒，118 篇样本），而真实重发**最近的一对隔了 3.33 小时**。上线前对生产库的只读预演：合集 feed 的 50 条中 40 条判为已有、10 条为并集新增，均为近两日文章，不是深度回填。
- 自建源的 feed 自带公众号原文全文（实测 50/50 条都有，5–11k 字符），因此长链形态的微信 URL 不再送去抓正文——内置 Playwright 打开这类长链会被导到 `wappoc_appmsgcaptcha`，三次有界重试后一无所获。跳过只省掉验证码的开销，不损失正文。
- 微信文章链接现在**原样保留发布方给出的形态**。此前的 URL 规范化会重建 query 串，把长链里 base64 的 `__biz=…==` 编码成 `%3D%3D`；微信接不接受这种形态没有证据——`curl` 对两种形态给出完全相同的读数（都 302 到「未知错误」），区分不了。而这个 URL 正是读者在 `/wechat` 上点的链接，所以不做无谓改写，只有确实要剔除 `utm_` 参数时才重建。既有 3278 条微信条目的 URL 全部不带 query，该改动不影响任何已存条目。
- 配套放开了两处此前写死"只能有一个微信来源、且必须是 Mp2RSS"的校验：来源契约现在按 `required_env` 与 `fetch_url` 自洽配对逐个校验，并要求两个微信来源不复用同一个环境变量。同时收窄了一处相关行为：**未设置的环境变量只对显式声明 `optional = true` 的来源才静默跳过**，其余仍然报错中断加载——此前这一跳过是按环境变量的名字硬编码给 `MP2RSS_FEED_URL` 的。
- **本次读数同时推翻了此前记录的替换进度。** 08-18 的记录是"真漏仅 1 条"；按同一口径（发布于自建部署首次抓取之后、且已超过 24 小时仍未出现）重跑，现在是 **26 条真漏**，分布在 7 个账号（量子位 9、AI科技评论 9、InfoQ 2、虎嗅 2、Draco 2、赛博禅心 1、Founder Park 1）。已排查这不是标题归一化造成的假阴性：这些标题在自建 feed 里连近似形态都没有。另一方向上自建源多出 376 条 Mp2RSS 没有的文章。并集因此比任一单源都完整，而"停掉 Mp2RSS"比 08-18 的记录所显示的更远。另需注意 Mp2RSS 覆盖约 21 个公众号而自建部署只订阅了 14 个，停用前这 7 个账号需要另行安排。
- 运维细节、去重键的完整读数与对 A7 告警计数的影响见 [operations/wechat-ingestion.md](https://github.com/lindong28/ai-radar/blob/main/docs/operations/wechat-ingestion.md)。

## 2026-08-18（晚间：推文图片改为贴合画面的缩略图，点击原地看大图）

- 列表卡片里的推文图片不再居中在一个灰底大盒子里，改为**贴着画面的缩略图、紧靠卡片左缘**，与标题文字同一条左边界。此前的盒子是定宽的，图片按比例缩进去之后两侧留下大片灰底——实测一张 945×811 的图在 1072×210 的盒子里只画出 245×210，左右各空约 413px。现在盒子由图片撑出来，边框贴着画面走，四档视口/主题组合下 44 张图的「渲染画面宽高比 vs 原图宽高比」偏差全部小于 0.02（此前单图偏差 0.29）。卡片也因此略矮：同页 40 张卡片的最大高度 432px → 415px，17 张带图卡片的高度合计减少 278px。
- **不做裁切**。参照站 aihot 对多图用的是固定 16:9 两列 + `object-fit: cover`，用它自己的样式复刻实测：每格是 208×235 的竖格，一张 1200×675 的横向产品截图被裁得只剩中间一条竖缝。推文里的图多是 benchmark 图表和发布截图，裁坏等于白放，所以只对齐布局、不采用它的裁切规则。
- **点击图片改为在当前页原地放大**（最大 92% 视口），Esc 或点击空白处关闭，一条推文有多张图时可左右切换、右下角显示第几张。这是**增强而不是替换**：图片仍然是一个指向真实资源的普通链接，所以 ⌘/Ctrl+点击、中键点击、右键「在新标签页打开」「图片另存为」全部照旧可用；脚本没加载或出错时，普通点击也退回原来的新标签页行为——不会出现「点了没反应」。遮罩支持键盘操作（Tab 在关闭/上一张/下一张之间循环，关闭后焦点回到刚才点的那张图），并对遮罩以外的页面内容置 `inert`，读屏用户不会串到背景里。
- 附带修掉一个只在窄屏出现的同类问题：多图的半宽约束原本写在图片元素上，而百分比的分母是那个由图片自己撑出来的盒子——实测把 130px 的图装进 269px 的盒子里，灰底空档在移动端原样复现。约束改挂在有确定宽度的容器上。窄屏单图也不再被 240px 卡住（390 视口下内容列 296px，此前只画到 240px）。
- 设计与实测读数见 [ADR-058](https://github.com/lindong28/ai-radar/blob/main/docs/adr/058-shrink-wrap-x-media-thumbnails-and-add-a-lightbox.md)。**契约文本尚未同步**：`ux-contract.md` HP-7 目前仍写「点击图片在新标签页打开大图」，演化候选（含配对的验证条件）已登记在 [issues/ux-contract-issues.md](https://github.com/lindong28/ai-radar/blob/main/docs/issues/ux-contract-issues.md)，待按契约流程修订。

## 2026-08-18（下午：A4/A7 处置指引更正）

- A4 与 A7 的处置指引把运维指向了三个错误方向，现更正。其一，A4 仍写着「X(nitter) 源整批 SSL/超时多为公共实例瞬态」，而 X 自 2026-08-17 13:42 起已改走官方 `api.x.com`；把整批失败读作源站瞬态会让人放过真实的出网中断。其二，两条规则都让运维「核对日志开头的 egress proxy 行」，而读这行不构成核实：08-18 00:04 那一轮头部是看着正常的 `=== egress proxy: http://127.0.0.1:59527 ===`，同轮 162 源全部 `Connection refused`。其三，判据本身要按**这行的值**而不是它的有无来分——本次修订的第一版曾写成「缺这行 = 代理未生效」，评审证伪：`pipeline.sh` 无条件打印该行，`PROXY_STATUS` 的四种取值（地址 / `not configured` / 两种 `FAILED:`）都会打印，08-17 那批日志缺这行只是因为该功能当天才由 `3f871a1` 引入。按「缺行」判会最常命中锁竞争的 SKIP 轮（682 份日志中 644 份缺该行，最新的缺失者全是 lock busy），把人指向一个没坏的 `.env`。现改为读该行的值分流，并要求经代理实发一次请求验通（`curl -x … https://api.github.com/zen`，200 才算通）而非探端口——端口探测区分不了「本地 listener 活着」与「上游隧道通」。验通目标特意选与被诊断对象无关的端点，避免拿 X 端点去验证一条正为 X 源失败而排查的链路。同时移除 A4 `impact` 里「fetch 失败主要反映结构性源站波动」这一归因：它渲染在处置方向之上，读者据它就已决定不动手。仅改消息文案与文档，fire 条件、严重度与投递契约均未动。
- 该时段的失败面同时更正两处既有记述：那 127 个失败源含 18 个非 X 的海外源（`claude_blog`、`google_ai`、`huggingface_blog`、`wx_mp2rss` 等），是**海外出网中断**而非「X 整批失败」；且告警覆盖并不完整——A4 在 08-17 00:31 与 01:16 两次发出「已恢复」，而中断持续到 13:30；此后到 14:00 之间 A4 判定 firing 76 次、`send A4` **0 次**。即缺陷不是「读成健康」而是**判定为 firing 却一次也没投递**：resolve 清空 `since`，下次 firing 从当时重算，而 `attempted=0` 造成的假 ok 每隔几轮就打断计时，notice 档 30 分钟连续去抖因此永远达不成。已登记为未闭合项，见 [issues/alerting.md](https://github.com/lindong28/ai-radar/blob/main/docs/issues/alerting.md)。
- 另核实：109 个启用的 X 来源中 55 个「近 30 天零产出」不是故障。官方 API 摄取自 08-17 13:42 才首次成功，设计上首窗只回看 20 分钟、不回填历史，故 30 天窗口内实际只有约 1 天覆盖；直调 API 抽样核对 GoogleDeepMind、`_akhaliq`、AndrewYNg 的最后原创帖分别为 08-13、08-13、08-14，均早于窗口起点，且 `exclude=retweets,replies` 使 thread 尾帖与转发不计入。`X_BEARER_TOKEN` 已配置可用。该集合会随覆盖时间累积自然收缩。

## 2026-08-18（下午：出口传输改为 SSH 隧道）

- X 图片的出口传输从「直连新加坡 tinyproxy」改为「经 SSH 隧道」。打开新加坡防火墙后端到端实测发现：明文正向代理把 `CONNECT pbs.twimg.com:443` 明文发在中国→新加坡这一跳，GFW 按主机名注入 RST——判别性对照下 CONNECT `example.com` 得 `403 Filtered`、CONNECT `pbs.twimg.com` 得 `Connection reset by peer`（3/3、~0.13s）。修复是把这一跳加密：上海主机新增 systemd 服务 `ai-radar-img-tunnel`（`ssh -L 39148:127.0.0.1:39147`，受限专用 key，`Restart=always`），`AI_RADAR_IMG_PROXY_URL` 改指 `127.0.0.1:39148`。`/img` 代码与 tinyproxy 认证均不变。实测经隧道取真实 twimg 图 `HTTP 200`、隧道 Restart 自愈通过。部署模板见 `deploy/systemd/ai-radar-img-tunnel.service.example`，运维与诊断见 [operations/services.md](https://github.com/lindong28/ai-radar/blob/main/docs/operations/services.md)，设计见 [ADR-057](https://github.com/lindong28/ai-radar/blob/main/docs/adr/057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md)。新加坡防火墙放行 39147 的入站规则隧道化后已不再需要（流量走 SSH 22），可作收尾移除。

## 2026-08-18

- X 推文的自带图片现在会显示在列表卡片上。此前**从未取回过**这些图：X API v2 的媒体只出现在 top-level `includes.media`，请求不带 `expansions=attachments.media_keys` 就什么都不返回，因此库里 4219 条 X 条目带媒体字段的为 0 条——2026-08-17 移除 RSS 正文配图后线上一张图都不剩，并非那次改动过度，而是这个缺口一直被正文图掩盖着。两者的分工保持与参照站一致：推文的图展示，RSS 正文图不展示（图多为论文首页或网页截图，缩到卡片尺寸后读不出内容）。一条推文的多张图按推文原顺序全部展示；视频与 GIF 展示静态封面（接口不返回可播放地址）。图片高度设上限且不裁切——实测 260px 上限会把桌面首屏的完整卡片压到 1 条，低于既定的 ≥2 条密度底线，故收到 170px（单图 210px）。
- 上海 serve 主机到 `pbs.twimg.com` 的连接被上游阻断（tcpdump 判别性取证：loopback 对照捕获 45 个包证明抓包工作，twimg 方向 0 个包），所以图片经新加坡主机上的转发代理取回，由 `.env` 的 `AI_RADAR_IMG_PROXY_URL` 配置，仍走既有的同源 `/img`。未配置该变量时对 twimg 的请求**立即返回 404、不发出任何请求**：退回直连的表现不是缺图而是每张图挂满整个超时（被丢弃的包不回 RST，TCP 会一直重传），一屏几十张图足以拖慢整站。`/img` 的重定向改为手动逐跳跟随，每一跳在**发出之前**重新过一次主机允许名单——只校验最终 URL 的话，指向内网的开放重定向已经被实际请求出去了。代理的运维事实、暴露面收窄与一条已接受的未修补 CVE 记在 [operations/services.md](https://github.com/lindong28/ai-radar/blob/main/docs/operations/services.md)。
- 修复媒体全部加载失败时卡片底部残留 8px 空白：`onerror` 隐藏的是链接元素、容器还在，而卡片是 column flex + `gap: 8px`，零高度的容器仍算一个 flex item 照样吃掉一个 gap（实测 87.7px vs 95.7px）。代理不可达时**每一条** X 卡片都处于该状态，所以这不是边界情况。
- 新增 `./run.sh admin x-media backfill`，为切换到官方 X API 之后入库、但抓取时还没有媒体字段的存量条目补齐媒体。它按 id 直查 `/2/tweets`、**不触碰来源 checkpoint**，因此与增量抓取互不干扰；X 自 2026-02 起按返回的 Post 计费，故默认提供 `--dry-run` 先报候选数与账单规模、`--limit` 支持分片，并拒绝非正数 `--limit`（SQLite 把 `LIMIT -1` 读作无上限，一个手误会让本想限账单的运行查询全部候选）。已对生产库执行一次：候选 246、写入 245、其中 135 条带媒体、1 条推文已删除。更早的 3976 条 nitter 时代条目没有存 post id 且已全部滚出展示窗口，不回填。写入带最长 90 秒退避重试——首次执行时两次都在付费之后、写入之前撞上 pipeline 的写事务而丢失结果，共享连接 5 秒的 `busy_timeout` 按读者调，撑不住数 GB 库上的写事务。
- 修复 `/img` 的两个既有缺陷（本轮扩大了它的使用面，故一并处理）。其一，主机允许名单此前对 `netloc` 取 `split(":")[0]`，而 `netloc` 是 `[userinfo@]host[:port]`，所以 `http://mmbiz.qpic.cn:80@169.254.169.254/` 会被判为允许、实际却去连云元数据服务——校验的字符串和连接的目标不是同一个，构成公网可达的盲打 SSRF；现改用 `urlparse` 的 `hostname`，且判定函数改为接收整个 URL 自行解析，调用方无从再传错。其二，10 MiB 上限此前在响应体**完整读入内存之后**才生效，限制的是回给浏览器的大小而非实际开销；现改为流式接收、超限即中止传输。
- X timeline 返回 `200` 且同时带 `data` 与 `errors` 时不再整页丢弃。这是 X 明确定义的部分成功（某个被展开的资源不可用，帖子本身有效）。此前整页抛错，加上本次新增的媒体 expansion 后会变成实际风险：某来源最新五条里只要有一张图被删，该来源每一轮都会失败且 checkpoint 永远推不过去。

- 新增 A7「来源静默」告警，补上单个来源停止产出时的盲区。此前的摄取告警 A4 以全站 item 增量与 fetch 失败率判定，单来源死亡时其余来源仍把总量顶在 floor 之上：2026-08-14 至 08-17 微信来源零入库约 73 小时，A4 逐轮触发但全程判为 `notice`、投递计数为 0，故障靠人工偶然发现。A7 改为逐源判定，阈值取 `max(6 小时, 2×该源近 30 天平均出稿间隔)`——固定 6 小时会对数天一更的来源常态误报，而被静音的告警不再保护任何东西。近 30 天不足 5 条的来源不足以刻画节奏，计入「无法评估」并在消息中给出计数，不按健康处理。所有静默来源合并为一条 page 通知并附清单：共享上游故障会让全部来源同时静默（2026-08-18 凌晨隧道中断即让 162 个来源同轮全失败），逐源推送正是会让人静音它的量。当前生产读数为 59 个来源可评估、103 个来源历史过稀无法评估。该条目原把后者归因为「尚未配置凭证」，同日的核实推翻了它：`X_BEARER_TOKEN` 已配置可用，零产出来自官方 API 摄取自 08-17 13:42 才首次成功、首窗只回看 20 分钟且不回填历史，30 天窗口内实际覆盖约 1 天，该集合会随覆盖时间累积自然收缩（见本日「另核实」条目）。同理，本条所述「A4 逐轮触发但全程判为 `notice`、投递计数为 0」也不准确——A4 的 firing 行不记录 severity，且 08-16 有 4 次投递；未投递的真实机制见 [issues/alerting.md ISSUE-A01](https://github.com/lindong28/ai-radar/blob/main/docs/issues/alerting.md)。A7 的 fire 条件与投递契约不依赖这两处归因，未受影响。

- 修复 pipeline 日志解析器对 SKIP 轮次的识别。互斥改为内核 flock（ADR-052）后，SKIP 消息不再带 `pid=`，而解析器的正则仍强制要求它，于是 SKIP 轮被当作**真实轮次**记入，其 `attempted=0` 成为 `latest_fetch` 的取值来源；A4 据此渲染出 `fetch 失败率 0.0%`，与该轮实际结果无关。也就是说「本轮被跳过」与「本轮全部来源抓取失败」在告警链路上呈现为同一个读数。正则现同时接受带 pid 与不带 pid 两种写法，`skip_pid` 在缺失时为 `None`；新增回归测试覆盖两种格式，并以一条真实失败轮次（`attempted=162 failed=162`）作反向对照，避免修复退化成「把所有轮次都判为 skip」。本次只修解析，不改任何告警阈值或分级。

## 2026-08-17

- 新闻列表的卡片不再显示从正文抓取的图片。改动前，同一时刻与 aihot.virxact.com 在 1440×900 下成对实测：本站首屏内**没有一条**卡片能完整显示（参照站 3 条），第二条卡片要往下滚约 1.4 屏才露头；80 张卡片里 8 张比整屏还高（最高 998px，视口 900px）。这些图多是论文首页或网页截图，且被裁切填充（1072×360 的格子里塞 534×610 的竖图），移动端缩到 143×107 后完全读不出内容。图片数据仍随接口返回，`/wechat` 详情页的配图不受影响。
- 卡片右上角的评分从裸数字 `89` 改为 `AI 评分 89`，读者不必再靠悬停提示才知道那个数字是什么（移动端本来就悬停不到）。不写「/100」——T1 信源的加权分带 1.25 倍系数，当前线上已有超过 10 的条目，写死分母会显示出「108/100」这种不可能的值。窄屏只留「AI」二字，实测完整标签会把最长的信源名挤成省略号。
- 没有存过主题偏好的新访客，现在跟随系统的浅色/深色设置，而不是一律深色。已经手动选过主题的用户不受影响；浏览器禁用本地存储时，主题切换在本次访问内仍即时生效（只是不跨会话保留）。
- `https://news.aiplanet.live/about` 的信源表格此前仍会列出 34 个已停用的历史来源（旧 nitter X 账号、旧 Feed 与旧 WeWe RSS 微信源），与 2026-08-16 记录的「被配置移除的旧来源……不再从公开 v2 消费面出现」不符。原因不在数据或代码：站点代码早已改为读取只返回启用来源的 v2 接口，但页面引用的 `?v=` 版本串在那次发布中没有更新，而 EdgeOne 对 `/app.js` 与 `/style.css` 强制节点缓存 7 天，部分边缘节点因此持续约 20 小时提供旧版脚本，旧版脚本读的是保留全量历史来源的 v1 接口。现已更新版本串使新脚本生效；数据库中的停用来源行与其历史内容按既有可见性规则保持不可见、未删除。同时新增 `scripts/bump_frontend_assets.py` 与配套测试，使版本串由资源内容摘要派生，漏更新在提交前即被拦下。
- 修复 pipeline 后段阶段（`enrich`/`curate`/`interpret`）被后续轮次以 `database is locked` 持续打断、从不完成的系统性饿死。根因是目录锁协议的用户态判活用 macOS `kern.boottime` 做 boot 身份，而该读数随 NTP 校时持续漂移（39 个被回收锁的 owner 记录 boot_id 39 个互不相同），存活的持有者被每一轮 cron 判死并回收锁，两个 pipeline 随即并发写库（2026-07-27 起累计 40 次误回收）。互斥现改由内核 BSD flock 持有（`.pipeline.flock`，[ADR-052](https://github.com/lindong28/ai-radar/blob/main/docs/adr/052-hold-pipeline-mutex-with-kernel-flock.md)）：锁由整棵 pipeline 进程树持有、最后一个进程退出时内核释放，不存在 stale 判定与误回收整类失效；性能探针与 A6 告警的 pipeline 判活同步迁移为对同一文件的非阻塞共享锁探测。web server 启动时的 `db.migrate()` 遇 `database is locked` 另增 30 秒内有限退避重试，耗尽后重抛回 launchd 既有恢复链（[ADR-053](https://github.com/lindong28/ai-radar/blob/main/docs/adr/053-retry-startup-migration-on-database-locked.md)）。锁修复消除的是 pipeline 自身并发；启动 migration 的 93 次历史锁失败是否全部由该并发引发无法从无时间戳日志判定，故重试作为独立保险保留。
- `pipeline.sh` 由 crontab 拉起时拿到的是非交互 shell，不加载交互式 rc，因此没有出网代理变量；`httpx` 的 `trust_env` 无从生效。`mp2rss.bugcode.dev` 直连被重置（走代理 HTTP 200，不走代理 `Recv failure: Connection reset by peer`，cron 空环境完整复现），`wx_mp2rss` 自 2026-08-14 12:16Z 起连续失败约 73 小时、微信零入库；2026-08-17 新增的约 120 个海外来源同样从未成功。现由 gitignored `.env` 提供 `AI_RADAR_PROXY_FILE` 指针，`pipeline.sh` 在运行时从该文件解析代理地址并导出，同时把解析结果写入日志。指针指向的地址由外部 tunnel 在每次重连时改写，因此不把端口固化进配置——固化会在下次重连后以完全相同的症状静默复发。修复后单轮 `attempted=162 / failed=0`（修复前 37 成功、125 失败），`wx_mp2rss` 单轮 `fetched=100 inserted=100` 且微信正文抓取零失败。该修复只覆盖出网链路：后段 `enrich`/`curate`/`interpret` 仍会因既有锁回收缺陷被后续轮次打断，消费面恢复不由本次改动保证。

## 2026-08-16

- 微信公众号后台 shadow ledger 先以 schema v9 在 resolution 与 probe 保存 exact `base_resp.ret`，再以 schema v10 拒绝布尔、字符串、小数和 SQLite 非整数错误码，并把特殊次日冷却收窄为仅由已记录的频控证据触发。只有整数 `200013` 或明确 frequency 文本进入 `RATE_LIMITED`；整数 `200002` 和其他非认证、非频控拒绝进入 `PLATFORM_REJECTED`。v8 之前的旧 `AUTH_REQUIRED`、`RATE_LIMITED` 与 `RESPONSE_INVALID` 只标记为“错误码未记录”，不会追溯猜测、改判或生成虚假解禁时间。一次获授权 one-shot probe 仍未得到文章候选；它发生在 exact-ret 修复前，因此只能证明后台返回了旧 parser 归入宽分类的失败，不能证明微信官方存在 24 小时窗口。生产 Mp2RSS、`items`、scheduler 与默认关闭配置均未改变；本地私有库在 0600 SQLite backup 后迁移，3 条 resolution、4 条 probe、0 条 candidate 完整保留。
- 主时间线和精选已配置 AIHOT 审核滚动观察并集中的 109 个 X 账号、34 个原始 Feed、18 个原始 Web/API 列表，共 161 个主站来源。另保留可选 `wx_mp2rss`，但它只服务「微信文章解读」，不进入精选、全部动态、搜索或策展。Web/API 来源使用逐来源确定性解析器；被配置移除的旧来源及历史关系继续保存在 SQLite，但不再从公开 v2 消费面出现。验收已覆盖 2,020 条 AIHOT 完整滚动观察且来源 reconciliation 零缺口，以及全部 52 个 non-X 来源的两轮 live 读取和一次 immutable replay；这只证明来源成员集合和原始读取链路，不表示下游清理、筛选、标签、排序、评分、摘要或策展结果与 AIHOT 等价。
- X 读取不依赖付费 Mp2RSS，需要 `X_BEARER_TOKEN`；身份解析与 timeline 分轮，每个账号每轮至多一个请求，timeline 最多 5 条，只读原创帖子。首次窗口为最近 20 分钟，后续由持久 checkpoint/cursor 增量推进，不做接入前历史回填。仓库提供只允许 `x_openai` 的低成本探针；受限真实探针的一次身份请求和一次 timeline 请求均返回 HTTP 200，并提交了合法的空窗口 checkpoint。该结果没有实际帖子读取证据，也不代表 109 个账号均已逐一 live 验证。
- 缩短 `https://news.aiplanet.live/wechat` 首次打开时的空白等待：`news.aiplanet.live` 现通过 DNS-only CNAME 接入 EdgeOne 全球加速，公开列表与静态资源按明确边界使用边缘缓存，搜索、详情、管理页和健康检查继续保持不缓存；`/wechat` 列表页把共享样式表内联到 SSR HTML，消除冷首屏等待第二次 CSS 请求的串行阻塞，同时保留单一 CSS 源文件和原有页面视觉。

## 2026-08-14

- 微信公众号后台 shadow discovery 将 `searchbiz` 名称匹配收窄为一次性 provisional mapping；只有后续 probe 返回的全部文章 URL 都以唯一 `__biz` 匹配配置账号时，才形成可比较的身份验证证据。空列表、URL 无法提供 `__biz`、身份不匹配与请求失败分别持久化，CLI 不显示私有 `fakeid`，并将文章 URL public biz 矛盾明确报告为 `IDENTITY_MISMATCH`、安全失效 provisional mapping，不再误归入一般未验证状态；v6 成功历史不被升级成新证明。schema v8 在已落地 v7 之上加固 active mapping 与不可变 candidate snapshot，并让 disabled status 和 compare 使用同一 URL 身份判定；只读 `status` / `compare` 不再隐式迁移旧 shadow DB，升级必须经显式 `wechat-discovery migrate`。一次获授权的 live `searchbiz` 先以 `RESPONSE_INVALID` 结束，按新契约修正后的一次请求成功形成“歸藏的AI工具箱”的 provisional mapping；public biz 与文章发现仍待冷却后的 probe 验证。生产 `wx_mp2rss`、定时 pipeline 和 `items` 均未改变。边界见 ADR-040 与 ADR-041。
- 微信读书只读 canary 在获授权、可见的登录态 Chrome 中生成首份真实 schema v7 evidence：`/web/shelf/sync` 得到 HTTP 200 成功响应，但目标公众号不在书架，因而结果为 `blocked_no_shelf_entry`，article-list 请求与动态头观察均未执行，替换结论仍为 `not_validated`。本次运行没有修改书架、写生产 candidate 或改变 pipeline；目标书架变更仍需独立明确授权。边界见 ADR-038 与微信摄取运维文档。

## 2026-08-13

- 新增默认关闭的微信公众号后台发现候选，用于在取消 Mp2RSS 前做低频 shadow 验证。首版包含 14 个现有公众号的非敏感 public biz 与逐号公开身份记录、headed Playwright 扫码入口、独立 shadow SQLite、状态 CLI，以及按账号、成功 attempt 和观察窗执行的只读 Mp2RSS 对比命令。后续源码审计确认公开 `biz` 不能作为通用安全的后台 `fakeid` 映射；resolution/probe ledger 因此增加单账号 `searchbiz` 身份解析、一次性 mapping 分配、请求前 `finished_at=NULL` 的 crash-safe reservation、文章 URL `__biz` 双层校验、失效与 supersession 历史，并用 probe 引用与不可变 attempt snapshot 取代双向消费和全局 candidate 双写。schema v6 进一步移除 verified probe/candidate 的重复身份、固定 kind/change-basis 与可派生成功子类型，所有时间统一 UTC；config v3 使用 `public_biz` / `observed_public_biz` 自描述字段。v3 及更早 attempt 标为 `predates_resolution`，不得形成覆盖结论；真实私有库已在 pre-v5、pre-v6 精确备份与副本演练后迁至 v6，两条旧失败证据完整保留。获授权后台登录与二次登录已在可见浏览器中完成；两次真实请求分别得到 `AUTH_REQUIRED` 与 `RATE_LIMITED`，没有文章候选，生产数据未变。人工请求临时默认成功后间隔 24 小时，probe 每次 5 篇；单次响应内重复 URL 会显式失败，不会去重后形成假窗口证据。尚无成功 `searchbiz`、文章列表、14 账号后台兼容、session 寿命或平台配额证据。所有请求仍不进入 pipeline，也不改变 Mp2RSS；当时边界由 ADR-024 至 ADR-032 约束，后续身份语义与 schema 演进见 2026-08-14 条目。
- 新增默认关闭的单账号微信读书只读 canary 作为第二条可行性路线。schema v7 在既有、至多一次的 article-list 请求周围被动观察 CDP Network 事件，只记录监听、精确请求匹配、ExtraInfo 与 `x-wrpa-0` / `x-wr-ticket` 两个头名的存在性；不保存、显示或回放头值，也不新增请求、修改书架或写入生产数据。证据继续保留 HTTP / WeRead API / response-shape、候选身份、public target 生命周期和 `NOT_VALIDATED` 替换结论；v1–v6 artifact 保持冻结可读。当前只有真实 v4 未登录失败样本，没有真实 v7 或登录态正例，不能据此取消 Mp2RSS；边界由 ADR-033 至 ADR-038 约束。
- 微信读书 canary 的请求前目标路径冲突现明确显示 `NOT_STARTED` 与 `Request dispatch: NOT_ATTEMPTED`，并保证既有 evidence 不变；帮助与运维文档同步公开 stdout、stderr 和退出码契约，避免把未发请求误报成 dispatch unknown。

## 2026-08-10

- 新增 A5 微信解读停滞与 A6 近 24 小时 LLM 量结构成本突变告警，并把 unpriced、stale、due-review、price-changed 作为独立 D3 notification 去重通知。A6 用同一现行 tariff snapshot、cache 全未命中基准重算当前窗与 14 个 UTC 基线日，不把纯调价或 cache 采集比例变化误报为量结构突变；超过 3×基线先 notice，超过 6×高档阈值才 page，已观测到的缺数会显式降级。当前 pipeline 运行造成的暂时测量缺口把已记录金额作为下界继续允许首次 firing 与升级；下界未越线时保留同一 firing episode，等待封口后只确认记录行金额是否回落，不宣称整体计量健康。D3 发送/clear 失败可重试，保留间歇模型价格签名，并把成功生命周期写入共享 ledger；A1/A2/A5 合并由 pipeline 心跳门控，INTERNAL suppression 同样可审计。A3 的主动 healthz 探针现从已安装 serve plist 解析端口，不再因本机 serve 使用 8010 而对 8000 持续假告警。`/admin/usage` 新增阶段、Provider/模型、日序列与 cache 中性等长前窗聚合，`/api/v1/admin/usage` 的 `measurement_scope` 明示调用数、token 合计和同口径金额合计是记录行下界，而均值、占比与环比只描述记录行 cohort、相对全部付费调用真值的偏差方向未知。新增 `admin cost-report` 和周一 09:17 的 `cost-report` cron lifecycle；报表用 durable items 与逐 stage 成功产出识别整日及 partial stall，错误行不冒充成功，顶部单列异常，并同时展示 nominal 估算金额/占比、记录行口径与只除以可定价记录行的单篇解读前窗参考；所有金额都不是账单实付，unpriced 不计入金额。
- LLM 成本改为查询时按 LiteLLM 社区定价、项目内 ARK 挂牌价补充表与 7 天缓存派生；三个 deprecated `cost_usd` carrier 的历史值迁移为 `NULL`，新 writer 只写 `NULL`，滚动发布期间允许但完全忽略旧 writer 遗留 numeric。严格计量写入仍会抛出 SQLite 拒绝；模型结果已经成功返回时，计量失败改走独立、可计数的错误日志并保留已付费结果，不再误触发 provider fallback 或 interpret 重试。旧 `AI_RADAR_LLM_PRICING_JSON` 配置已退役且设置时显式报错，由受管定价 catalog 和 `AI_RADAR_USD_CNY` 汇率配置取代。`/admin/usage` 收窄为窗口总额三态拆分、来源单价、unpriced 清单与 cache 覆盖率；ARK 来源币种与 USD 投影分开展示，supplement 按 usage 时间选有效区间。`item_evaluations.cost_usd` 不再伪造 `$0`，管理 metrics 与告警信号也不再发布该假值。`./run.sh admin cost-audit` 从 raw catalog 独立读取 matched key 与费率，对费率提取错误也会失败，并显式打印两种 CNY 口径的差额与三个 deprecated carrier 的退出计数；human、KV 与 JSON 都携带 `measurement_scope`，`CONSISTENT` / `PASS` 明确只表示 tariff arithmetic 一致，不表示计量完整或 tariff 权威；机器视图通过 `--format=kv|json` 显式选择。
- 数据同步现只传 Mac primary 的非 FTS base artifact，并在腾讯服务器 inactive candidate 上重建、逐字段验证 FTS 后切流；同步不再要求事先 `admin db slim` / VACUUM。真实 steady round 的 DB 传输从旧链路约 1.9GB 降到 16.39M（连同 822.90K manifest 约 17.21M，低于 20MB gate），两轮切换共 3500/3500 个公网 health 样本全为 200，title/content/source/author/title_zh 五字段搜索 IDs/count 均与 Mac snapshot oracle 一致。失败 snapshot 会保留旧 serving release并进入可诊断 quarantine或 manual-block，Mac producer 等待绑定本轮 identity 的 `committed` 后才报成功；既有每 5 小时 cron继续作为 freshness入口。

## 2026-08-09

- 站点数据同步（Mac 主库 → 腾讯服务器只读副本）从纯手动改为每 5 小时自动执行（cron + `run-or-alert` 失败告警；SSH 认证经 launchd ssh-agent socket 发现，见 ADR-013）。此前同步靠手动触发，8 月 8 日起无人执行导致公网整站停更约一天半；本次同时补上「远端拒绝快照」的检测（每轮同步前按服务器自身时钟核对已接受快照的年龄，超过 11 小时阈值后在下一采样点上报，最坏约 16 小时发现）。
- 修复「微信文章解读」页自 8 月 7 日起停更的故障并恢复更新。根因有三层：解读所用的两条 LLM 通路同时不可用（ARK 周配额耗尽 + DeepSeek 官方余额不足），失败被永久缓存——解读一旦报错就再也不会重试，即使通路恢复也不会补齐；另有一个自 6 月起存在的隐蔽缺陷：文章标题 slug 仅大小写不同时，批次文件复制在 macOS 大小写不敏感文件系统上抛 SameFileError，导致约 340 篇已判定值得保存的文章从未上页。本次修复：解读失败的条目改为按指数退避自动重试（首败 15 分钟后可重试、每再败翻倍、累计 8 次后放弃，成功即清零），同文件复制改为跳过；pipeline 每轮解读上限 30 条，防止大积压时单轮长时间占锁饿死抓取。存量报错文章随每轮 pipeline 自动补齐，此前被吞掉的历史文章会按各自发布时间陆续出现在列表中。

## 2026-08-04

- 前端第二轮视觉与体验打磨，重点是**对齐、留白与信息层次**。桌面端：日期分组标题现在与下方所有时间戳右侧对齐成一条线；正文区不再固定宽度上限，宽屏下可用宽度被充分利用；时间线的圆点之间新增一条贯穿整个日期分组的连接线，收藏某条后该条圆点变为琥珀色；侧栏图标换成更清晰的图标；搜索按钮改为实心主按钮；新增全站定制滚动条与按钮按下 / 禁用状态；主题切换器用一块滑块指示当前档位；热点浮层不再在鼠标移向它的途中消失。
- 手机与网页缩放（≤960px）：正文列变宽、左右留白对称；此区间的时间戳字号偏大的问题已修正；推荐理由不再被截断成两行；顶部条改为随页面滚动而非固定吸顶。列表行变得更清爽——行首只保留时间与信源、分数移到行尾，不再挤入精选标记、收藏按钮与标签，信源名不再被压缩省略。**首页与「全部 AI 动态」的卡片收藏按钮现在只在桌面显示**；手机上仍可经「更多 → 收藏」查看、取消、导出与导入收藏（导入依然能新增收藏）。手机上日期分组不再提供折叠。话题标签现在只出现在「全部 AI 动态」页。
- 更新日志页修复了手机与缩放档的排版错乱（正文此前会被挤成窄条且左右不对称）。日期标题改为中文长格式并附星期，标题下方新增一行说明。
- AI 日报页正文末尾新增四项数据概览：今日事件 / 一手报道 / 新模型 / 信源。四项均为本站自有定义——今日事件为该期条目数，一手报道为该期中非推文类且信源等级为 T1 的条目数，新模型为该期中属「模型发布」分类的条目数，信源为该期去重信源数；不声称与任何外部站点口径一致。日报摘要里的行内代码现在显示为等宽字体的浅底圆角块。
- 热点榜页在桌面、平板与手机三档的正文宽度分别调整到更合适的比例。
- 首页首批内容现在由服务端直出全部约 40 条，避免浏览器加载后用完整首批替换 12 条预渲染内容所造成的布局位移。手机上品牌 + 日期紧凑条改为只在首页出现；「更多」、「收藏」、「关于」和「微信文章解读」页改用各自的页面标题头，微信详情与 404 页的返回列表入口保留。

## 2026-08-02

- 前端全面改版（参照 aihot.virxact.com 的信息架构与视觉）：默认主题从深色科技风改为浅色简约风，新增暗色变体与侧栏底部三态主题切换（浅色/深色/跟随系统，localStorage 持久，head 内联脚本防闪烁）；精选与全部动态页从分页控件改为无限下拉（IntersectionObserver + 请求代际校验防筛选切换竞态；/all 搜索态改用页码分页以规避 timeline API 搜索时忽略 cursor 的语义）；卡片新增收藏按钮与 `/bookmarks` 收藏页（localStorage 快照 + 导出/导入 JSON，导入经字段与日期可解析校验；服务端同步接口已预留约定未实现）；首页新增"当前热点"榜（新端点 `GET /api/v1/hot`，近 48 小时按 加权分×10+关联讨论×5 排序，单次一致快照取样，纳入 90s 公共缓存白名单）；日期分组可折叠且追加加载继承折叠态；主站页面移除 Google Fonts 改用系统字体栈（消除跨境字体请求的首屏延迟），AI 日报页保留原深色报纸风（旧样式与 token 原样迁移）。微信详情页安全契约由"整页无 script"收窄为"正文容器无 script"（新增 head 主题引导与导航 module 两个可信脚本）。改版经 Codex 高档对抗审查两轮修复复核后合入。

## 2026-07-26

- 将 same-host `performance-probe` 从 busy/idle 双轨 gate + busy rollup 改为 idle-only：pipeline 运行或负载不确定时不保存/评估，只有 idle 窗的 22 样本确认窗超预算才直接 page，不再降为 notice。probe 调度同步从 hourly `:17` crontab 改为专属 per-file launchd（`StartInterval=300`，经 `./run.sh performance-probe` 进入 external watchdog），pipeline 仍保留既有 `*/15` crontab；install/uninstall/status 现管理该 plist 与 legacy symlink 迁移。Playwright Chromium 是微信抓取与默认 probe 共用的显式部署前置，安装器不会自动下载或校验。2026-07-26 live 证明中，全 8 个旅程 cell 在 4.93 小时取得第 22 条 idle 样本，满足 6 小时硬门槛但仅余约 1 小时负载裕度。PERF 投递契约明确为 at-least-once，并依赖 `im-notify` 持久 signature dedup 抑制同一 crash retry 的重复可见消息。

## 2026-07-22

- 新增 `curated_items.summary_json` 精选 digest 缓存的常驻保留：每次 curate 后自动清空超过 `keep_days`（默认 7 天）且非最新 run 的可再生预计算缓存，使 `radar.db` 体量长期有界（此前约 8MB/天持续膨胀），生产库一次性瘦身实测由约 2.28GB 降到约 1.5GB（省约 785MB / 34%）。同时新增 `./run.sh admin db retain`（只清列）与 `./run.sh admin db slim`（清列 + VACUUM 回收磁盘、DB 同步前跑）子命令，`slim` 返回 `retained`/`compacted` 两阶段结果，`--dry-run` 零写只报待清量。唯一用户可感知的行为变化：`/api/v1/curated?run_id=X` 访问超窗口的历史 run 时，其 digest 改为 live 现算，内容反映当前 enrichment 而非 curation 时快照（TTL 语义）；所有 HTML 用户页只服务最新 run，字节一致、不受影响。
- 将运维告警从单一 page 级别升级为 page/notice 分级：需立即处置的事故发往 `ALERT`，低打扰退化发往 `NOTIFICATION`。pipeline busy 期间但同视角 idle 正常的 PERF 超预算会合并成一条 `PERF:rollup:busy` notice，而真实 idle/公网退化仍保留 page。新增 `data/alert-events.jsonl` 已送达通知历史，可按规则、severity、firing/resolved 与通道查询最近 14 天的成功投递。

## 2026-07-19

- 修复精选归档计数缓存的过度失效和微信 SSR 请求连接生命周期，使首页与微信页面在 pipeline 写入期间保持稳定响应，同时保留精确总数和分页语义。
- 新增 `performance-probe` 用户旅程监控：从同机 origin/public 测量首页、微信列表、详情和翻页，区分 pipeline idle/busy，以 `PERF:*` 规则告警并保留 14 天诊断证据。结果明确标为 same-host provisional，不作为区域 SLO。
- 新增 `performance-remediate` 候选修复 worker：confirmed 性能退化可触发单个、最长 60 分钟的隔离 Codex worktree，生成未进入主分支且未部署的本地候选 commit；越界配置会 fail closed。
- 修复 `performance-probe` 首页浏览器探针把正常样本误判为 `hard_failure` 的缺陷。此前它拿完整渲染卡片列表与 12 项期望做全等比较，导致每个健康首页样本都被标记 hard_failure；现改为按前缀匹配期望，前缀不符仍会 hard-fail。同时收紧 `performance-remediate` 的启用门槛说明：缺陷虽已修复，运维仍须先确认 hard_failure=false 且首页 `PERF:*` 未告警，再安装 remediate cron。
- 为公开分页路径（`/`、`/wechat`、`/api/v1/curated`、`/api/v1/wechat`）在安全分页变体下让 origin 发 `Cache-Control: public, max-age=90, stale-while-revalidate=30`，而带 `q=`（含空 `q=`）、分类/日期/未知参数或非 200 响应一律 `private, no-store`（fail-closed 白名单，已验证这些路由无 cookie/会话变量）；前端 `app.js` 在 SSR 预载后预取下一页 API 并在点击翻页时复用同一 promise，搜索/分类请求绕过该 90 秒前端缓存。头部代码需重启 `serve` 生效。配套在 Cloudflare zone `aiplanet.live` 手动加了 Cache Rule「AI Radar short public pagination TTL」——这是 Cloudflare dashboard 侧配置、非 repo 代码，Edge TTL 与 Browser TTL 均 respect origin 头——使这些路径改由 CF 边缘缓存，实测翻页 API 从约 3-5s 的 DYNAMIC 回源降到约 0.5-1.4s 的 CF HIT。

## 2026-07-13

- A1-A4 告警的发送传输改为复用本机 `im-notify --alert`，不再由 AI Radar 直接调用飞书 webhook。原有 firing / resolved、debounce 与 30 分钟冷却状态机保持不变，且不叠加 `--dedup-key`；发送器失败会记录日志而不会终止周期告警检查。`alert` launchd 模板同时把 `~/.local/bin` 加入 `PATH`。

## 2026-06-24

- Split LLM usage accounting into `data/llm_usage.db` (`AI_RADAR_LLM_USAGE_DB`) so prefilter / score / enrich token writes no longer contend with the main `radar.db` writer. Existing `radar.db.llm_usage` history is copied into the dedicated DB on migration/first use, `/admin/usage` reads the dedicated DB while still showing item/source metadata from the main DB, and A2 prefilter P95 now uses a recent 2-hour sliding window so recovered latency incidents do not keep firing until midnight.
- Added an internal `/admin/usage` page and `/api/v1/admin/usage` endpoint for maintainers to inspect LLM usage attribution. DeepSeek/ARK `chat_json` calls now persist one `llm_usage` row per prefilter / score / enrich LLM call, including model, input/output tokens from `completion.usage`, item attribution, and input size. The page uses the same Cloudflare Access / dev-only local bypass guard as `/admin`, is not linked from public navigation, and rolls up the last 30 days by day, model, and pipeline stage.

## 2026-06-12

- Made fresh-clone setup degrade cleanly when optional private resources are missing. The Mp2RSS WeChat source now skips with a warning when `MP2RSS_FEED_URL` is unset or empty instead of aborting source loading, and `.env.example` no longer contains a fake Mp2RSS URL that would force a broken fetch. `./install.sh` now checks each service before installing: `serve` always installs, `pipeline` needs one LLM API key, `alert` needs `FEISHU_GENERAL_ALERT_WEBHOOK`, and `tunnel` needs `deploy/cloudflared/config.yml`. Missing promptable values can be entered interactively and are appended to `./.env`; non-interactive installs skip only the affected services and print a summary.

## 2026-06-09

- Stopped A4 (article ingestion) from alerting on transient fetch flaps. All X/Twitter sources fetch through the single public `nitter.net` instance, which intermittently times out for one ~15-minute fetch round and then self-heals; each flap fired and resolved A4 within ~15 minutes as pure noise, while the daily ingestion count was never actually affected (a round skipped by the flap is backfilled by the next). A4 now debounces: a fetch-failure condition must persist past a 30-minute window (≈2 fetch rounds) before it notifies, and a flap that recovers within the window is absorbed silently — no firing and no resolved. The debounce is per-rule and configured only for A4 (`a4.debounce_minutes`); A1/A3 still notify immediately, so this does not delay genuinely urgent alerts. Also corrected A4's disposition text, which pointed at the retired `wewe-rss/bridge` for WeChat — WeChat now ingests via Mp2RSS, and a batch X-source failure is the more common trigger. The `alert` service must be running the updated code for this to take effect.

## 2026-06-08

- Made `/wechat` search (and the shared timeline/curated search) whitespace-insensitive, so a query with extra internal spaces returns the same results as one without. Previously a stored title like `分享Claude Code` was found by `分享Claude Code` but not by `分享 Claude Code`, because the query and the matched columns were compared with their spaces intact. Both the query patterns and the searched columns (title, author, abstract, tags) now have all whitespace — including full-width spaces — stripped before matching, and the longer-query FTS path handles spaced queries too. Simplified/Traditional matching is unchanged. The web layer must be restarted for this to take effect.
- Fixed the missing avatar for the WeChat Official Account "赛博禅心", which showed a fallback initial instead of its real avatar. A single failed avatar scrape on 2026-06-02 had left its cache row empty and a 7-day negative cache prevented any retry. Added an `admin wechat-avatar refresh --account <name>` command that clears one account's cache row and re-scrapes immediately (used to repopulate 赛博禅心's avatar live), and shortened the failed-scrape negative-cache TTL from 7 days to 2 so a transient miss self-heals within days instead of a week.
- Fixed the 日报 page so navigating to a past day now shows that day's curated articles instead of an empty report. Previously `/daily/{date}` (前一日/后一日 and direct date URLs) only ever populated for today, because a dated request was answered from the single latest curation run, which curates only about one day of fresh items. A dated daily report now aggregates curated items published on that date across all curation runs (deduplicated to each item's latest curation), the same cumulative-archive logic the home page `/` uses, so any past date with curated content renders a populated report. The admin explicit-`run_id` path and the `/` and `/all` archive pagination are unchanged.

## 2026-06-07

- Reduced false alerts in the monitoring rules. A2 (pipeline health) no longer treats a long in-progress run as a fault: a `SKIP` log means "pipeline already running" (liveness), so it is no longer a standalone trigger, and the "no successful pipeline" heartbeat threshold was raised from 45 to 120 minutes — this eliminates the recurring fire/resolve flapping that produced the bulk of past alert noise. A3 (website) dropped its `healthz` dimension, which was a dead signal (hardcoded to never fire) that misleadingly displayed "healthz 连续失败 0 次"; A3 now reports only the real user-side 5xx rate. Each line in `logs/alert-check.log` is now timestamped (Asia/Shanghai) for incident forensics. A true active healthz probe and a time-aware A4 ingestion floor are tracked as follow-ups in `docs/issues/general.md`.
- Prefixed every Feishu monitoring alert (both firing and resolved) with a `【AI Radar】` project label. Because the alert webhook (`FEISHU_GENERAL_ALERT_WEBHOOK`) is shared across projects, the prefix lets a recipient tell at a glance which project an alert came from.
- Added an explicit "访问原文" (visit original) link to WeChat interpretations so readers can jump to the source Official Account article: a bordered button below the title on each `/wechat/<slug>` detail page, plus a compact "原文 ↗" link on every list card. Both open the original in a new tab and reuse the existing source URL. The shared frontend asset version was bumped so visitors receive the new behavior.
- Added search to `/wechat`, scoped to interpretation card fields: Chinese title, Official Account author, abstract, and tags. Search URLs are shareable with `?q=`, pagination and detail-page return links preserve the query, Simplified/Traditional Chinese variants match, and the shared frontend asset version was bumped so visitors receive the new behavior.

## 2026-06-06

- Removed the retired WeWe RSS bridge from the service layer. `./install.sh`, `./uninstall.sh`, and `./status.sh` now manage four services (`serve`, `tunnel`, `pipeline`, `alert`) instead of five, and a bare `./install.sh` no longer requires Docker or aborts when it is unavailable. WeChat ingestion continues through Mp2RSS; rollback material (`deploy/wewe-rss/` + RUNBOOK) is retained, with the launchd wiring recoverable from git history.
## 2026-06-04

- Changed the curated home page `/` from a single page of the latest curation round's top 40 into a cumulative archive of every item ever curated. It now aggregates all distinct items selected across past curation runs (deduplicated, currently about 1,793 items), ordered newest first, paginated about 40 per page (currently about 45 pages) using the same numbered page controls as `/all`. Page 1 still shows the latest curated picks, preserving the "skim in five minutes" use.
- Upgraded pagination on the `/all` timeline and the `/wechat` interpretation list to numbered page controls: first and last pages are always visible, the current page shows two neighbors on each side, gaps collapse to an ellipsis, any page number is directly clickable, and previous/next arrows remain. Both pages share one pagination component.
- Changed `/all` to show the true first and last pages. `/api/v1/timeline` now returns an exact total count instead of the previous forward-looking estimate, so the last page reflects real data (matching `/wechat`), and out-of-range requests such as `?page=9999` clamp to the real last page.

## 2026-06-02

- Added the `/wechat` tab for WeChat Official Account article interpretations, with structured summaries, tags, worth-reading filtering, shareable detail pages, and ai-assistant knowledge-base writeback for saved articles.
- Removed the disabled legacy WeWe source definitions `wx_guizang` and `wx_crossing` and deleted their historical item rows from the production DB after creating a verified backup.
- Migrated WeChat Official Account ingestion from the self-hosted WeWe RSS bridge to the hosted Mp2RSS feed, removing the local Docker container and WeRead QR-login maintenance that frequently broke ingestion in production. The feed URL with its embedded key is read from the `MP2RSS_FEED_URL` environment variable and never committed.
- Added real Official Account names and avatars to WeChat article cards. Cards now show each article's source account name and its avatar instead of the shared collection name, falling back to the WeChat icon when no avatar is cached.
- Added the `/admin` operations dashboard for user traffic, ingestion, pipeline health, and active alert status.
- Added the `alert` background service, which runs `admin alert-check` every five minutes and sends A1-A4 monitoring alerts through a Feishu custom-bot webhook.
- Documented the monitoring runbook, including Cloudflare Access setup for `/admin*` and `/api/v1/admin*`, Feishu webhook setup, and daily service verification commands.

## 2026-06-01

- Fixed Chinese-source search so recently fetched backfill articles are evaluated and visible even when their original publish date is older than the pipeline window.
- Improved source-name searches by ranking source/author matches ahead of content-only matches and rotating same-name sources so a prolific source no longer hides lower-volume WeChat sources on the first page.
- Added Simplified/Traditional Chinese query expansion, so searches such as `归藏` and `歸藏` find the same Chinese-source articles.

## 2026-05-30

- Expanded `/api/v1/timeline` and `/api/v1/curated` search to match source names, authors, and Chinese titles in addition to article title/body. Searches of 3+ characters use FTS; 1-2 character queries fall back to short-field LIKE matching.
- Changed search semantics intentionally: searching a source name can return all matching articles from that source, and scoring `reasoning` is no longer part of the search index.

## 2026-05-29

- Added WeChat Official Account ingestion through a local WeWe RSS bridge, with Playwright-based full-text scraping for internal LLM processing and public cards limited to generated summaries plus original article links.

## 2026-05-28

- Improved `/` and `/all` first-screen loading by serving SSR-preloaded feed HTML; production verification now shows no visible loading spinner and sub-1.5s median first content on the main feed URLs.
- Fixed `/all` timeline entries without scoring data so every visible card renders a numeric score pill.
- Fixed the About page repository link to point at `your-org/ai-radar`.

## 2026-05-24

- Improved `/api/v1/timeline` and `/all` load performance by adding SQLite indexes, preloading enrichment data in the timeline query, and replacing the exact count query with a pagination-safe estimate.
- Fixed timeline time display so visible times use the same Asia/Shanghai timezone as date grouping.
- Updated the About page repository link to `your-org/ai-radar`.
