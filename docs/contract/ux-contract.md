# AI Radar · UX Contract

> 当前 product 对用户承诺的可观察行为。仅装 user-observable 内容；实现细节、路线图、未发现 issue 不在这里。
>
> 协议：`~/.claude/references/ux-test-protocol.md` §2。
> 配对 ledger：[`ux_issues.md`](./ux_issues.md)。
> 相关：[VISION.md](./VISION.md) / [PRD_v0.md](./PRD_v0.md)。

---

## Personas

### §Persona-Owner — 个人维护者（lindong，单一）

- 核心任务：晨起扫精选；维护信源池；保证 pipeline 健康。
- 权限：本机 CLI（`./run.sh admin ...`）写入；浏览体验与 Visitor 完全一致。
- 关系：每日 ≥1 次；高熟练度，了解信源 / 评分逻辑 / pipeline 节奏。
- 情境：本地 macOS，浏览器与 shell 并用；偶尔也从移动浏览器扫站。

### §Persona-Visitor — 公开访问者（匿名）

- 核心任务：扫今日精选 / 浏览全量 AI 时间线 / 看今日或历史日报 / 跳到原文深读。
- 权限：仅读，零鉴权；任何写入口都不存在。
- 关系：可能首次也可能回访；不需要任何熟练度即可消费内容。
- 情境：桌面或移动浏览器；可能从 IM / 社交媒体深链跳入特定页面 / 日期。

---

## Surfaces

### §Surface-Curated `/`

- 进入：根 URL；sidebar / 移动顶部菜单"精选"。
- 直接定位：`?category=<slug>&page=<n>&q=<keyword>`。
- 默认状态：最新一次 curation_run 的精选条目，按发布日期分组（每组一个日期 header）。
- 跨环境：桌面侧边栏 + 内容主区；移动折叠为顶部 bar + 汉堡菜单。

### §Surface-Timeline `/all`

- 进入：sidebar / 移动菜单"全部 AI 动态"。
- 直接定位：`?channel=<slug>&category=<slug>&page=<n>&q=<keyword>`，可叠加。
- 默认状态：未筛选全量条目按 `fetched_at` 倒序，底部分页器。
- 跨环境：同 Curated。

### §Surface-Daily `/daily`、`/daily/<YYYY-MM-DD>`

- 进入：sidebar / 移动菜单"AI 日报"；或直接深链 `/daily/<date>`。
- 直接定位：URL 中嵌入日期（`/daily/2026-05-18`）跳指定日期；不带日期 = 最新日报。
- 默认状态：当日报表，VOL.YYYY.MM.DD 主刊头 + 主题分节文章；左侧含归档列表。
- 跨环境：同上；移动端归档可能折叠在内容下方或菜单内。

### §Surface-About `/about`

- 进入：sidebar / 移动菜单"关于"。
- 直接定位：无参数支持。
- 默认状态：产品定位 / 信源池表格 / 设计原则 / 评分说明 / 致谢 / 联系方式 6 节。

### §Surface-AdminCLI `./run.sh admin <subcommand>` （owner-only）

- 进入：owner 本机 shell。
- 直接定位：subcommand + flag（如 `sources add/remove/enable/disable/list`、`db migrate`、`sources reload`）。
- 默认状态：无 subcommand 打印帮助。
- 跨环境：仅 owner 本地可达；改动通过其他 Surface（主要是 `/about` 信源表格）间接被 Visitor 观察。

---

## Journeys

### §Journey-MorningScan — 晨起扫精选

- 触发：Owner / Visitor 早晨打开 `/`。
- 步骤：扫精选卡（标题 / 推荐理由 / 来源 / 分数）→ 点感兴趣条目标题 → 新页签打开原文。
- 成功：无登录；条目按发布日期分组（非按 score 排序）；外链在新 tab 打开；原 tab 保留筛选 / 滚动位置。
- Near-miss 要捕捉：列表实际按 score 排序而非时间分组（肉眼像列表正常）；原文链接覆盖当前 tab → 失去阅读位置；精选数量异常（0 或显著超过历史规模）；同一事件被多源重复列出而未聚合到"关联讨论"下。

### §Journey-CategoryFilter — 按分类浏览

- 触发：Visitor 想只看某分类（模型 / 产品 / 行业 / 论文 / 技巧）。
- 步骤：在 `/` 或 `/all` 点分类 chip → URL 同步 `?category=<slug>` → 列表收敛到该分类 → 可叠加搜索 → 可点"全部"清除。
- 成功：所选 chip 高亮；URL 反映状态；结果在语义上属于该分类（不是 tag 字面匹配）；空结果时不显示分页器（避免误导）。
- Near-miss 要捕捉：chip 点击后 URL 未同步 → 刷新丢状态；筛选后翻页参数被吞；结果仍含其他分类条目；空结果时仍显示分页器。

### §Journey-TimelineFilterByChannel — 在 `/all` 上叠加信源类型

- 触发：Visitor 在 `/all` 上想只看某种信源类型（一手 / 资讯 / 推文），通常与 category / search / page 叠加。
- 步骤：在 `/all` 点 channel chip → URL 同步 `?channel=<slug>` → 与 category、search、page 任意组合 → URL 同步全部参数。
- 成功：两维 chip 各自高亮；多维度筛选**所有维度同时生效**（结果应同时满足所有所选条件）；URL 反映完整状态。
- Near-miss 要捕捉：channel 与 category 组合时其中一维静默失效；翻页后某维 filter 状态丢失；channel chip 点击不触发刷新；channel slug 在 `/` 上被误暴露。

### §Journey-CuratedTraceOnAll — 在 `/all` 上追溯精选条目

- 触发：Visitor 在 `/all` 浏览时间线时希望知道哪些条目曾入精选。
- 步骤：滚动 `/all` → 留意带"精选" badge / 推荐理由 / 数字分的条目。
- 成功：曾入精选的条目在 `/all` 仍带 badge + 数字分 + 推荐理由；精选元数据不在 `/all` 静默丢失。
- Near-miss 要捕捉：精选项在 `/all` 失去 badge（数据有但 UI 不显示）；推荐理由丢失；同一条目在 `/` 显数字分但 `/all` 不显。

### §Journey-DailyArchive — 浏览日报与历史

- 触发：Visitor 想看今日或历史某天的日报。
- 步骤：打开 `/daily` 看今日 → 点左侧归档列表 / "前一日"链接回溯 → 或直接深链 `/daily/<date>`。
- 成功：今日报有内容；归档列表可点；任意历史日期深链都能可读地渲染（有内容时按主题分节，无内容时显式空态文案）。
- Near-miss 要捕捉：归档列表为空但今日报正常显示；前/后一日链接跳到不存在的日期但 UI 不提示；最早 / 最新一日没有边界禁用态；URL 是日期但内容是别的日期的报表。

### §Journey-AdminEditSources — Owner 调整信源池

- 触发：Owner 想加 / 停用 / 删信源。
- 步骤：编辑 `data/sources.toml` 或运行 `./run.sh admin sources ...` → 跑 `admin sources reload` 或下一次 pipeline → 改动反映到 `/about` 信源表格。
- 成功：admin 命令明确反馈成功/失败；reload / pipeline 后 `/about` 信源表格反映新状态；停用源仍在表格中显示（仅停止继续抓取）。
- Near-miss 要捕捉：admin 报告成功但实际未写入；停用源在 `/about` 仍显"启用"；新增源不出现；已停用源完全消失在表格中。

---

## Features

### §Feature-CuratedCard

- 承诺：`/` 每条卡含「源（头像 + 名字 + 可选 @handle）、精选 badge、0-100 数字分（带分数）、中文标题、中文摘要、内容分类标签、推荐理由、可选媒体图、可选『关联讨论 N 条' hover 列表」。
- 边界：无媒体时不渲染媒体块；单源无重复事件时不显示"关联讨论"块；分类标签为空时不渲染 tags 块。
- 跨 Surface：与 `/all` 的 TimelineCard 共享视觉基底，差异见 TimelineCard。

### §Feature-TimelineCard

- 承诺：`/all` 每条卡含「源、中文标题、中文摘要、分类标签」；若该条已被评分，附加 0-100 数字分；若该条同时在**最新精选名单**内，附加"精选" badge + 推荐理由。精选元数据不在 `/all` 静默丢失。
- 边界：未评分条目仍渲染卡片其他部分；停用源的历史条目仍可能出现。

### §Feature-CategoryFilter

- 承诺：`/` 与 `/all` 提供分类 chip（全部 / 模型 / 产品 / 行业 / 论文 / 技巧），点击即筛，URL 同步 `?category=<slug>`，active chip 高亮。
- 不做：不支持多选；不做跨分类模糊匹配。
- 边界：无效 slug 静默回退到「全部」（不报错）；空结果不渲染分页器。
- 合法 slug：`ai-models` / `ai-products` / `industry` / `paper` / `tip`（以前端 chip 实际呈现为准；新增分类需同时更新 chip 与此条）。

### §Feature-ChannelFilter（仅 `/all`）

- 承诺：信源类型 chip（全部 / 一手信源 / 资讯 / 推文），URL 同步 `?channel=<slug>`，可与 category / search / page 任意叠加。
- 不做：不支持多选 channel；不在 `/` 暴露（精选不按信源类型筛）。
- 边界：与其他维度组合时**所有**维度都生效；无效 slug 行为与 Category 一致。
- 合法 slug：`firstParty` / `news` / `x`。

### §Feature-Search

- 承诺：`/` 与 `/all` 提供关键词搜索框，对标题与摘要文本匹配；可与分类 / 信源筛选叠加；URL 同步 `?q=<keyword>`。
- 不做：不做正文全文搜索；不做拼写纠错 / 同义词扩展。
- 边界：空结果显式提示（不显示空分页器）；`/about` 的搜索框是页面内静态匹配，与 timeline 搜索语义不同。

### §Feature-Pagination（仅 `/all`）

- 承诺：底部分页器（首页 / 中间 / 末页 + 上下页），`?page=<n>` 同步 URL。
- 不做：不在 `/` 与 `/daily` 暴露分页器（这两页面按日期分组 / 归档列表组织）。
- 边界：超范围 page 返回空列表，分页器仍可回退；page<1 或非数字按 1 处理。

### §Feature-Score

- 承诺：精选卡和已评分的时间线卡显示 0-100 数字分；评分体系（LLM 5 维 + 代码权重 + 阈值 6.5）在 `/about` 评分说明节公开。
- 不做：不在 UI 暴露阈值或权重调节入口（owner 改通过 ruleset / admin）。
- 边界：同一条目的数字分应稳定（同一 ruleset 内不会跨刷新跳变）。

### §Feature-ExternalOpen

- 承诺：所有原文链接以新页签打开（`target="_blank"` + `rel="noopener noreferrer"`）；原 tab 保留筛选 / 滚动 / 分页上下文。
- 不做：不在 iframe / 模态内打开原文；不做"原文预览"中间页。
- 跨 Surface：`/`、`/all`、`/daily` 行为一致。

### §Feature-DupAggregation（仅 `/`）

- 承诺：精选卡若同一事件被多源覆盖，显示"关联讨论 N 条"标记并 hover 列出各源（含 X 用户名等元数据）。
- 不做：不在 `/all` 上展示重复聚合标记（仅 `/`）。

### §Feature-DailyMasthead

- 承诺：`/daily` 主刊头展示 VOL（`VOL.YYYY.MM.DD`）、日期 + "每日八时"标语、AI RADAR DAILY 品牌字样。
- 边界：加载中显示"LOADING"占位；无数据日期 VOL 仍按 URL 日期显示。

### §Feature-DailySections

- 承诺：日报按下面 5 个主题节展示，每节含编号、中英标题、本节文章数；文章标题为外链（遵循 §Feature-ExternalOpen）：
  - 01 模型发布/更新 · MODEL RELEASES
  - 02 产品发布/更新 · PRODUCT
  - 03 行业动态 · INDUSTRY
  - 04 论文研究 · RESEARCH
  - 05 技巧与观点 · TIPS & TAKES
- 边界：某节当日 0 篇时该节不渲染（页面只显示有内容的节）；某日全节皆空时整个 sections 区显示明确空态文案而非白屏。

### §Feature-DailyNav

- 承诺：左侧"最新一期 + 历史归档列表"；底部"前一日 / 后一日"；可直接通过 `/daily/<date>` 深链。
- 边界：
  - 最早 / 最新一日无对应方向目标时链接行为以实际为准（非数据日显示空态文案）。
  - 访问 `/daily/<无效或无内容日期>` 时静默切到最近一期，并显示 fallback banner 提示"日期 X 无效或无内容，已切到最近一期 Y"——不白屏、不静默掉。

### §Feature-SourceTable（仅 `/about`）

- 承诺：信源池表格列出每个源的 slug、名称、tier（T1 / T1.5 / T2）、状态（启用 / 停用）、kind。
- 不做：不允许在 UI 编辑 / 启停信源（owner 用 CLI）。
- 边界：停用源仍保留在表格中显示（仅停止继续抓取，历史精选条目仍可能在 `/` 与 `/all` 中展示）。

### §Feature-Sidebar

- 承诺：四个主页面共享侧边栏导航（精选 / 全部 AI 动态 / AI 日报 / 关于），当前页高亮；移动端折叠为顶部 bar + 汉堡按钮（带"打开导航 / 关闭导航"aria）。
- 不做：不在导航中暴露 admin / owner-only 链接；不做用户头像 / 登录入口。
- 跨 Surface：四个主页面行为与外观一致。

---

## Quality Bar

### §QualityBar-DataFreshness

- 维度：`/all` 时间线最新条目入库延迟。
- fail 阈值：`/all` 首条 `fetched_at` 距当前时间 > 30 分钟（pipeline 15 分钟 cron + buffer）。
- 关联锚点：§Surface-Timeline 首屏首条；间接影响 §Surface-Curated 与 §Surface-Daily 的当日内容。
- 观察：刷新 `/all` 读首条 fetched_at；单次抽查即可判 fail。

### §QualityBar-DailyCompleteness

- 维度：`/daily` 在当日（系统时间所在日期）的内容完整度。
- fail 阈值：当日 `/daily` sections 区域无任何文章（即"今日尚无内容"空态）。
- 关联锚点：§Surface-Daily / §Feature-DailySections。
- 观察：直接访问 `/daily`（不带日期），看 sections 是否有文章；当日 0 篇视为 fail（owner 期望"每日 8 时后必有日报内容"）。

### §QualityBar-PublicReadable

- 维度：所有公开页面在无任何认证 / cookie / 注册的前提下可见完整内容。
- fail 阈值：任意公开页面出现 paywall / 登录墙 / 注册引导 / 弹窗阻塞。
- 关联锚点：所有 §Surface-* 除 §Surface-AdminCLI。
- 观察：隐身浏览器或 curl 直接访问；正常返回完整 HTML 即 pass。

### §QualityBar-Responsive

- 维度：桌面 / 移动两种主断点下布局不允许碎裂。
- fail 阈值：内容溢出、控件不可点、导航不可达、文字截断；以 CSS 实际断点为准（不绑具体 px）。
- 关联锚点：§Surface-Curated、§Surface-Timeline、§Surface-Daily、§Surface-About。
- 观察：桌面常见宽与典型移动宽各刷新一次；切换断点动态 resize 也不应碎裂。

### §QualityBar-ExternalLinkBehavior

- 维度：所有原文链接行为一致遵循 §Feature-ExternalOpen。
- fail 阈值：任一条目原文链接覆盖当前 tab / 缺少 rel 属性 / 在 iframe 中打开。
- 关联锚点：§Feature-ExternalOpen 跨所有 surface。
- 观察：抽样若干条目检查 `target` + `rel`。

---

## Out of Scope

仅装"故意永不做"与"v0 不做未来不限"两类。具体 phase 路线图项（v1 反馈 / v2 Telegram 推送 / v3 趋势预测等）不在此列——它们是"未来会做"，不是"不做"。

### §OutOfScope-MultiUserWrite — 多用户写入

- 为什么：单户即终态；多人写超出产品定位。
- 用户易误期：可能期待评论、协作、分享标记等"社区"行为。
- 来源：VISION §2、§5。

### §OutOfScope-SourceDiscovery — 信源发现 / 推荐

- 为什么：owner 手动管理信源是设计选择，系统不主动建议或自动订阅。
- 用户易误期：可能期待"推荐信源 / RSS 商店"。
- 来源：VISION §2 非用户场景 + §5。

### §OutOfScope-ContentProduction — 内容生产 / 选题辅助

- 为什么：本产品是消费工具不是创作工具；与 AIHOT 等"选题辅助"型产品差异化定位。
- 用户易误期：可能期待"AI 总结成观点文章 / 导出选题列表"。
- 来源：VISION §2 + §5。

### §OutOfScope-LoginGate — 登录 / 注册 / 个性化收藏

- 为什么：公开页面零鉴权是核心设计；admin 写口只走 CLI 不走 Web。
- 用户易误期：可能想注册账号收藏 / 关注 / 个性化推送。
- 来源：VISION §5。

### §OutOfScope-InternalVsPublicSplit — 公开版与 owner 内部版的内容差异

- 为什么：访问者看到的与 owner 一致；owner 仅在写入口（CLI）上有区别。
- 用户易误期：可能猜 owner 在 Web 上看到更"全"或更"深"的内部数据。
- 来源：VISION §5。

### §OutOfScope-LocalLLM — GPU 部署 / 本地大模型推理

- 为什么：评分一律走 API，不内置本地模型，运维零 GPU。
- 用户易误期：可能期待"本地隐私推理 / 离线模式"。
- 来源：VISION §5。

### §OutOfScope-RealtimePush — 实时（秒级）Web 推送

- 为什么：v0 不做实时推送；未来 IM 渠道（v2+）也按"批量节奏"（例如每日 8 时）。
- 用户易误期：可能期待"AI 大事件秒级 push / WebSocket 推流"。
- 来源：VISION §5。
