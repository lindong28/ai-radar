# 微信公众号摄取运维

> Mutable snapshot. 本 checkout 的待发布配置把自建 Wechat2RSS（`wx_wechat2rss`）设为微信公众号主动抓取入口；托管 Mp2RSS（`wx_mp2rss`）为 `enabled=true, paused=true`，不进入 fetch/A7，但保留历史可见性与跨源去重身份。另有维护者显式触发的 ai-assistant KB 内部归档补录；文章卡片显示真实公众号名与头像。仓库内的公众号后台发现候选与微信读书 canary 默认关闭且已停止推进。暂停决策与尚未完成的生产收口边界见 [ADR-20260904-f427](../adr/20260904-f427-pause-source-fetch-without-hiding-history.md)；本 T1 单元未验证生产迁移、同步、发布或真实页面。
>
> 本目录（`docs/operations/`）是维护者产线 runbook，绑定具体实机拓扑；fork 部署路径见 [README](../../README.md)。

## 待发布接入：Wechat2RSS 主动，Mp2RSS paused

微信公众号没有公开 RSS。早先 ai-radar 用 WeWe RSS，随后改用托管 Mp2RSS，并在 ADR-059 阶段与自建 Wechat2RSS 双跑。本 checkout 的待发布路线定为 Wechat2RSS 主动抓取，Mp2RSS 只保留历史来源身份；这不是生产已切换或仍在双跑的读数。历史取舍见 [ADR-059](../adr/059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md)，暂停决策见 [ADR-20260904-f427](../adr/20260904-f427-pause-source-fetch-without-hiding-history.md)。

| 来源 | 当前角色 | 加载与主动工作 |
|---|---|---|
| `wx_wechat2rss` | 待发布的主动微信公众号抓取入口 | 配置 `WECHAT2RSS_FEED_URL` 后 `enabled=true, paused=false`，进入 fetch 与 A7 |
| `wx_mp2rss` | 历史可见与跨源身份锚点 | `enabled=true, paused=true`；缺少抓取地址也保留这个来源，不进入 fetch/A7 |
| WeWe RSS | 已停用的历史回滚材料 | 不在服务注册表或待发布来源清单中 |

WeWe RSS 的退役状态（服务层移除时间、容器当前状态、回滚材料在哪）以 [services.md §服务](services.md#服务) 那条引述为权威，此处不复述。

## 配置与状态集合

`wx_wechat2rss` 的 URL 是 loopback 加本部署 token，只在运行容器的机器上有效，放项目根 `.env`，不要放跨机器共享的 `~/.claude/.env`：

## 短链正文抓取与 Chromium 前置检查

微信短链接的完整正文由 Playwright Chromium 抓取。部署时必须显式运行 `uv run playwright install chromium`，再按 README 用真实 `chromium.launch()` 验证；`install.sh` 不下载或校验浏览器。scheduled `pipeline.sh` 还会在 fetch 前运行 `./run.sh wechat-browser-preflight`：exit 0 只证明预期 executable 路径存在且可执行，exit 1 表示缺失/不可执行，exit 2 表示 driver/runtime 无法核实。后两者都会在任何 RSS/X/微信 fetch 前终止整轮并建立 W1 page，处置与恢复语义见 [monitoring-alerting.md §微信 Chromium 前置检查](monitoring-alerting.md#微信-chromium-前置检查)。

这条 fail-closed 边界只覆盖 scheduled pipeline。维护者直接运行 `./run.sh fetch` 时，短链正文抓取失败仍会记录 `Failed to scrape WeChat article; using RSS item only` 并保留 feed 自带内容；它不会被本前检强制中断。Wechat2RSS 的 `?__biz=` 长链则按下一节既有规则主动跳过 Playwright，因为 feed 自带全文，不属于 W1 所防的静默降级。


```bash
WECHAT2RSS_FEED_URL=http://127.0.0.1:8080/feed/all.xml?k=<RSS_TOKEN>
```

`wx_mp2rss` 在 v2 contract 中仍保留 `fetch_url = "${MP2RSS_FEED_URL}"` 与公开 landing URL，但 `paused=true`。真实 feed URL 若存在仍是 secret，只能放 `.env` 或进程环境，不入 git；缺失或空值时 loader 不跳过这条 paused row，而是保留原占位符形成 inert `enabled=1, paused=1` 行。仅设置 `MP2RSS_FEED_URL` 不会解除暂停，也不会产生请求。

四个集合的边界如下：

| 状态/集合 | 判据 | 作用 |
|---|---|---|
| disabled | `enabled=false` | 普通来源退出 `/api/v2/sources`、About 与常规内容页；兼容用的 `/api/v1/sources` 仍可能返回它。内部归档来源 `wx_ai_assistant_kb_archive` 是显式例外，其文章仍可在 `/wechat` 使用并参与去重 |
| paused | `enabled=true, paused=true` | 保留 inventory、历史可见、interpret、A5、dedup 和 discovery compare；排除 fetch/A7 |
| fetchable / A7 evaluable | `enabled=true AND paused=false` | 允许主动读取，并纳入来源静默评估 |
| visible | 各消费者既有 enabled 谓词 | 与 paused 无关，避免暂停时隐藏历史 |

About 上的“已启用”只表示已收录、历史仍可见，不表示此刻主动抓取；runtime configuration status 只表示相应抓取入口是否可用。公共 API 不增加 `paused` 字段。

cron / launchd 不继承交互式 shell 的 `export`。启用 Wechat2RSS 自动调度前确认项目 `.env` 已配置该变量（与 LLM API Key 同处理，见 README §自动化调度）；暂不启用时可以保持为空，普通未暂停 optional source 会被 loader 跳过。

## Wechat2RSS 与跨源去重

自建 Wechat2RSS 的部署见 `deploy/wechat2rss/RUNBOOK.md`。`/feed/all.xml` 是合集端点，全局上限 50 条（各账号自己的 feed 各 20 条）。缺 `k` 参数时它返回 `HTTP 200` 加 `{"err":"k param is empty..."}`，所以判断它是否可用要看返回体、不能只看状态码。

**重启后自启（2026-09-05 起）**：`./install.sh orbstack` 装一个登录时跑 `orbctl start` 的 LaunchAgent，Wechat2RSS 容器靠既有的 `restart=unless-stopped` 随之自回。它修的是一个实际发生过的故障——Aug 31 21:49 那次重启后 OrbStack 一直没起来，`/wechat` 停更五天。OrbStack 自带的 `app.start_at_login` 不能用：`orbctl config set` 退出 0 但值不变，只能从图形界面改。用 `./status.sh orbstack` 查状态。

原计划把运行时迁到 Lima 以取得「开机前（无人登录也能起）」这一档，已放弃：两次尝试都卡在 guest 出网（默认 provisioning 拉不到 docker 包；SOCKS 隧道下安装器内部 curl 报 TLS error），而现有 ai-radar 服务全部是 LaunchAgent + 用户 crontab、无任何 LaunchDaemon——无人登录时 serve / tunnel / alert 本来就都不起，所以那一档不会带来实际可用性提升。

生产收口前必须按 [monitoring-alerting 的暂停来源准备步骤](monitoring-alerting.md#暂停来源前准备-a7-episode-identity) 操作；该权威步骤要求对实际 state/event 文件显式传入绝对路径。legacy episode 需要时的完整交接为 `SEEDABLE → SEEDED → READY`；`READY` 或 `NO_ACTIVE_EPISODE` 才能继续，`BLOCKED_MISSING_EPISODE_IDENTITY` 表示缺少唯一匹配的 firing ledger，不能猜测归因。

### 去重键：账号 + 归一化标题 + 5 分钟发布窗

两个源给同一篇文章的 URL 没有公共子串——Mp2RSS 出短链 `/s/<token>`，Wechat2RSS 出长链 `?__biz=…&mid=…&idx=…&sn=…`——所以既有的按 URL / content hash 去重（`src/airadar/fetcher/dedup.py`，作用域是单个 source）看不出它们是同一篇。跨源去重改用**账号 + 归一化标题**，并要求两侧发布时间相差不超过 5 分钟；正常信源只匹配 enabled 的来源，且排除条目自己所属的 source（同源身份由上面那两条既有路径裁定，它们按"文章是什么"判而不是按"它叫什么"判）。唯一例外是内部归档源 `wx_ai_assistant_kb_archive`：它必须保持 disabled 以避开抓取和告警，但仍作为同篇文章的去重锚点。

归一化只做 **NFKC + 空白折叠 + casefold**，不剥离标点、不做词干化或截断。

5 分钟这个窗口、以及"为什么不多剥一层标点"，都是量出来的取舍：窗口要同时躲开"同一篇在两侧的发布时间差"和"同账号真实重发同标题的最近间隔"这两个分布，而多折叠一层就会把真正不同的标题并掉、**永久丢一篇文章**。逐条实测读数、样本量与被否的写法见 [ADR-059](../adr/059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md)——运维上只需记住：这个键的误并代价是丢文章，不要凭直觉放宽它。

### 长链不送去抓正文

Wechat2RSS 的 feed 自带 `content:encoded` 全文（实测 50/50 条都有，正文 5–11k 字符），而内置 Playwright 抓 `?__biz=` 长链会被导到 `wappoc_appmsgcaptcha`、三次有界重试后一无所获。所以长链形态的微信 URL 直接跳过抓取（`_is_unscrapable_wechat_url`）：正文本来就在 feed 里，跳过只省掉验证码的开销、不损失文本。

### 长链的 URL 原样保留

URL 规范化原本会重建 query 串，把长链里 base64 的 `__biz=…==` 编码成 `%3D%3D`。微信的解析器接不接受这种形态**没有证据**——`curl` 对两种形态给出相同读数（都 302 到「未知错误」页），区分不了。这个 URL 是读者在 `/wechat` 上点的那个链接，所以对 `mp.weixin.qq.com` 改为**原样保留发布方给出的 query**（`VERBATIM_QUERY_HOSTS`），只有确实需要剔除 `utm_` 参数时才重建。既有 3278 条微信条目的 URL 全部无 query，因此该改动不影响任何已存条目、不会产生重复行。

### 解读阶段的缓存查询：长链之间它分不开

interpret 在总结一篇文章前，会拿它的 URL 问外部 summary-agent 的索引"这篇是不是已经总结过"。**那个索引按 URL 建键，而所有长链的路径都是 `/s`**，因此长链彼此之间它一个也分不开。判别性对照：用一个 `__biz` 完全虚构的 URL 去查，同样返回 `found: true` 并给出某篇既有文章的 slug 与摘要文件路径。

2026-08-20 双跑首批 10 篇因此全部复用了同一篇文章的摘要（见 CHANGELOG 同日条目）。

现在**长链根本不去问它**，连请求都不发。比标题救不了这个洞：这个索引对每个长链都会答"某一篇"，而同账号同标题重发是常态（生产历史 26 对），同标题命中照样把 A 的摘要发给 B；而且那次查询用 `check=True`，即便丢弃答案，它非零退出仍会挡住这篇文章被总结。短链的路径唯一、索引能正确建键，仍走缓存，但命中条目的标题与本篇不符时判为未命中。

排查同类问题时注意：**错误的摘要在库里和页面上都长得完全正常**——标题对、正文非空、标签齐全、`interpret processed=N errors=0`。可用的读数是 `wechat_interpretations.slug` 与 `items.title` 是否对应，以及同一 slug 基名下是否挂了一串 `-2`/`-3` 后缀却分属不同标题。

### paused 与 disabled 的恢复边界

跨源去重只匹配 **enabled** 的来源，paused row 因而仍能挡住 Wechat2RSS 重复写入；disabled row 不再匹配。这是 ADR-059 的身份语义，不因暂停而改变。

- **paused**：历史仍在 `/wechat`、搜索与详情中，跨源 dedup 仍命中；恢复主动抓取需先验证上游、补齐 env、把 contract 的 `paused` 改为 false、重新生成并 reload。
- **disabled**：历史立即从 enabled-only 消费面消失，去重锚点退出；以后重新启用可能把停用期间另一来源补回的同篇文章同时显示。

选择匹配 enabled 而不是匹配全部行，是因为另一种写法下隐藏行会持续拦住每一次插入，那些文章**永久补不回来**。重复只在"停用后又重新启用"这一条路径上出现。

**所以在当前没有受测清重命令时，不要重新启用一个曾被停用的微信源。** 旧 runbook 曾内嵌一段一次性 SQL/Python，但它没有复用运行时的可见源谓词、同源排除和标题规范化，可能错删或漏删，现已移除。若业务确实要求重新启用，当前安全入口尚未实现，进展跟踪见 [`docs/issues/general.md`](../issues/general.md) 中的对应 open issue；不要直接拼 SQL 修改生产库。

### 对告警与计数的影响

A7「来源静默」只评估 `enabled=true AND paused=false`；paused 来源不进入 silent、faded、quiet-X 或 unevaluable，不能把它写成健康。若已 announced 的 A7 episode 的全部 opening 来源后来都 paused，它直接 closed/ok，sender 调用数为 0，只在 `data/alert-events.jsonl` 写一条 `type=resolved, channel=INTERNAL, reason=source_paused`。若只暂停其中一部分，必须先有其余 opening 来源本轮仍被逐源评估的证据；缺证据时 episode 保持 firing 且不调用 sender，不能把“退出评估”误写成“已恢复”。完整分流见 [monitoring-alerting.md §告警规则](monitoring-alerting.md#告警规则)。

A5 与 interpret 仍按 enabled 语义处理历史微信条目，不受 paused 影响。两个来源的历史入库量也不能当覆盖率比较：双跑时期先到的来源建条目、后到的被去重丢弃，计数只反映抢到手的次数。仓库外 `shadow-observe` 直接读两个 feed，应用 pause 不会阻止它请求 Mp2RSS；生产收口时只精确退役现场实际存在的目标行，若目标行原本不存在，则记录保留其余 cron 行的 no-op 证据。

### 日志与凭据脱敏

program assembly 后，`deploy/wechat2rss/logs.sh` 才通过 Lima socket-aware `compose.sh` 查看部署日志，并对 feed token 做脱敏。2026-08-20 起脱敏改为匹配到值末尾、在 `&` 处停下（此前只匹配 `[A-Za-z0-9_.~-]+`，`k=abc+def/ghi=` 这类合法 token 会漏出后缀），`&` 之后有诊断价值的 query 字段保留。注意脱敏要覆盖两条独立通道：服务自己打印的（启动横幅、配置回显），以及你构造的带 token URL 被对方记进访问日志的——只堵前一条时第二条原样漏出。

## 公众号后台发现候选与微信读书 canary（默认关闭，已停止推进）

仓库内还留着两条替代发现路线的实现：公众号后台 `searchbiz + appmsgpublish` 适配器与微信读书只读 canary。两者**默认关闭**（`data/wechat-discovery.toml` 的 `manual_backend_requests_enabled=false`），不被 `fetch_all` 或 pipeline 调用，也不写生产 `items`。

后台路线已因平台级不可用**停止推进**（跨账号文章列举在 2026-07-30 前后被平台限制，见 [061-wechat-discovery](../adr/061-deprecate-wechat-admin-discovery-line.md)）；待发布配置改由 Wechat2RSS 承担微信主动摄取，paused Mp2RSS 只保留历史身份。

只读地查看当前状态（不读私有 session、不发后台请求）：

```bash
./run.sh admin wechat-discovery status
```

当时取得的全部实测读数、错误分类语义、CLI 退出码契约与未验证边界，见 [../references/wechat-discovery-evidence.md](../references/wechat-discovery-evidence.md)。

## 已移除的旧源

以下两个 WeWe 源已在 2026-06-02 从 `data/sources.toml` 移除，并在生产 DB 中删除其历史 item 及直接子表记录。删除前备份为 `data/radar.db.bak-20260602-180557`，备份 gate 已确认文件存在、size>0、`PRAGMA integrity_check=ok`、items 计数与源库一致。

| slug | 公众号 | 停用原因 |
|---|---|---|
| `wx_guizang` | 歸藏的AI工具箱 | 由 Mp2RSS 合集取代 |
| `wx_crossing` | 十字路口Crossing | Mp2RSS 无法订阅 |

删除范围：`items` 144 条、`item_evaluations` 433 条、`curated_items` 7 条、`feedback` 0 条；`items_fts` 由 trigger 自动清理。`curation_runs` 保留，历史 run 的去规范化 id blob 可能残留旧 id，仅作审计记录；`wechat_account_avatars` 保留，因为头像仍可被 Mp2RSS 文章复用。

## 真实公众号名 + 头像显示

微信文章卡片显示真实公众号名（按文章 author，而非 feed 合集名「微信公众号（Mp2RSS 合集）」）+ 公众号头像。

- **抓取**：fetcher 从微信文章页 `round_head_img` 提取头像 URL（兼容 `=` 和 `:` 两种 JS 写法），按源 backfill 并写缓存（`src/airadar/fetcher/runner.py`、`src/airadar/fetcher/wechat.py`）。
- **缓存表**：`wechat_account_avatars`（account → avatar_url），migration `007_wechat_account_avatars.sql` 建表。命中缓存的 account 不再重复抓取；负缓存（抓不到头像）有 TTL，到期重试——失败负缓存 TTL 为 2 天（偶发抓取失败不再卡一周）。
- **手动刷新单个账号**：`./run.sh admin wechat-avatar refresh --account '<公众号名>'` 清该账号缓存行并立即实抓（Playwright），用于某账号头像缺失/不对时强制重取。例：2026-06-08 赛博禅心 头像为空（一次抓取失败被负缓存困住），用此命令实抓填回。
- **URL 归一化**：头像 URL 统一 http → https（`mmbiz.qpic.cn`），避免 https 站点的 mixed-content 拦截。migration `008_wechat_avatar_https.sql` 回填存量，抓取侧 `normalize_wechat_avatar_url` 保证新写入也归一。
- **展示**：timeline / curated / items 路由 + SSR prepaint + client `app.js` 均 `LEFT JOIN wechat_account_avatars` 按 author 取头像；无头像时 fallback 到 `/wechat-icon.svg`。

## 微信文章解读与知识库回写

`interpret` 是 pipeline 最后一个阶段：`fetch → prefilter → score → enrich → curate → interpret`。它只处理启用的微信公众号源 item：

```sql
SELECT COUNT(*)
FROM items i
JOIN sources s ON s.id=i.source_id
WHERE s.kind='wechat' AND s.enabled=1;
```

运行方式：

```bash
./run.sh interpret            # 默认关闭时输出 skipped=true 并成功退出
AI_RADAR_ENABLE_INTERPRET=true \
AI_ASSISTANT_ROOT=/path/to/ai-assistant-compatible-root \
./run.sh interpret            # 增量，跳过已有 wechat_interpretations 行
AI_RADAR_ENABLE_INTERPRET=true \
AI_ASSISTANT_ROOT=/path/to/ai-assistant-compatible-root \
./run.sh interpret --backfill # 回填启用源全集；已处理行仍跳过
```

跨 repo 依赖：

- **默认关闭且 fail-open**：未设 `AI_RADAR_ENABLE_INTERPRET=true`、未设 `AI_ASSISTANT_ROOT`、或两个 summary-agent 脚本缺失/不可执行时，interpret 一律打印 skipped 并 exit 0，不读取任何外部路径、不阻断前置 pipeline。启用后 `AI_ASSISTANT_ROOT` 必填，`AI_RADAR_INTERPRET_USER` 未设时为 `default`。
- 启用条件、脚本布局、`summarize.sh` / `run.sh --check-url` / `--save-from-batch` 的调用形态、stdout JSON、summary markdown 与 index.json 的完整契约见 [`../references/ai-assistant-contract.md`](../references/ai-assistant-contract.md)。

数据约定：

- `wechat_interpretations.save_decision=1` 是 `/wechat` 展示与 KB 回写的唯一闸门。
- `summary_md`、`abstract`、`tags_json` 在 `radar.db` 内保留独立网站副本；Web 请求不读取 ai-assistant 文件系统。
- `save_decision=0` 的文章只落库为已处理记录，不展示、不写 KB，避免每轮重复消耗 LLM。
- fresh summarize 若精确报 `summary JSON missing non-empty criteria_reason`，会立刻原样重试一次；日志用 `retrying` / `recovered` / `exhausted` 区分三种结果。其它错误不走这次即时重试，仍按既有 DB 退避处理。
- cron 内不 git commit/push ai-assistant data 子模块；本地 KB 文件和 embedding 立即可被 `search-knowledgebase` 使用，提交子模块留给人工低频处理。

详情页 `/wechat/<slug>` 使用 `markdown-it-py==4.0.0` 渲染 markdown，并用 `nh3==0.3.1` sanitize。LLM 生成的 `summary_md` 一律视为不可信 HTML 输入。

`/wechat` 列表支持 `?q=` 搜索。查询先按空白和标点拆成必需词，每个词可在 `items.title`、`items.author`（公众号名）、`items.content_text`、`wechat_interpretations.abstract`、`tags_json` 或 `summary_md` 中满足；不匹配聚合 feed 名 `sources.name`。第一阶段用 trigram FTS 检索 3 字及以上的标题、正文和作者，短词与解读字段用 escaped `LIKE`；严格阶段为零结果时，才为长 item 字段追加空白不敏感 `LIKE` 兜底。`实测`、`评测`、`测评`、`狂测`是唯一受控同义组；同义词不触发作者优先，排序依次是原始作者命中、原始标题词数、仅同义标题词数、时间。详情链接和详情页返回链接会保留 `q` 与 `page`。严格阶段已有其它结果时不会运行慢速兜底，因此不承诺召回只靠压缩空白才能匹配的额外候选；发现与回退条件见 [ADR-20260901-a31f](../adr/20260901-a31f-stage-wechat-whitespace-fallback-after-empty-results.md)。

## 手动补录 ai-assistant 知识库归档

这条命令只解决一种缺口：ai-assistant Summary Agent 的知识库里已经有微信文章，但 AI Radar 按 canonical URL 查不到该文章。它不属于 pipeline，也不自动运行；实际导入会把目录中完整、有效且尚未入库的微信文章及其既有解读写入 `radar.db`。

### 什么时候运行，什么时候不要运行

| 场景 | 动作 |
|---|---|
| 第一次补录，或更换了 `--assistant-root`、`--user`、`--db-path` | 在命令中显式写出三个目标，再跑 `--dry-run` 核对候选数、已有条目和聚合后的跳过原因；CLI 不回显目标路径，dry-run 不写数据库 |
| dry-run 显示 `eligible>0`，且聚合计数符合预期 | 去掉 `--dry-run` 实际导入；数量大时加 `--limit N` 分批执行。若关心某一篇，先按下文目录命令核对它的逐篇状态 |
| 正常摄取之后发布的新文章 | 不用本命令；继续用 active Wechat2RSS 与 pipeline |
| URL 已在 AI Radar，但没有 `wechat_interpretations` | 不用本命令；它只增加 `existing_without_interpretation` 计数，不修改该条目。启用源走正常 `./run.sh interpret`，其它情况按单独数据修复处理 |
| 想导入非微信文章，或目录记录缺文章、摘要、有效向量等完整性条件 | 不用本命令绕过校验；修复 ai-assistant 侧记录后重跑，具体原因会列在 `Skipped reasons` |
| 想撤销已提交批次 | 不要按 run id 直接删除；见下文「成功批次没有删除式回滚」 |

### dry-run 与实际导入

从 AI Radar 仓库根目录运行。`--assistant-root` 必须指向包含 `agents/summary-agent/run.sh` 的 ai-assistant checkout 根目录，不是 `data/summary_agent/` 知识库目录；同时显式写出 Summary Agent user 与目标数据库：

```bash
# 预演：检查全部候选，不写数据库
./run.sh admin wechat-kb import \
  --dry-run \
  --assistant-root /path/to/ai-assistant \
  --user your-summary-agent-user \
  --db-path /path/to/radar.db

# 实际导入全部仍缺失的合格文章
./run.sh admin wechat-kb import \
  --assistant-root /path/to/ai-assistant \
  --user your-summary-agent-user \
  --db-path /path/to/radar.db

# 分批示例：本次由操作者选择最多导入 100 篇；按输出提示重跑，直到 remaining=0
./run.sh admin wechat-kb import \
  --limit 100 \
  --assistant-root /path/to/ai-assistant \
  --user your-summary-agent-user \
  --db-path /path/to/radar.db
```

首次操作或切换实例时，每条命令都显式写 `--db-path`；省略时 CLI 会使用 `data/radar.db`，但成功输出不会回显实际路径，容易把正确收据误归到错误实例。`--limit` 接受任意正整数，只限制本批实际导入数，不改变 `eligible` 总数；分批运行看到 `remaining>0` 时继续执行同一条实际导入命令。dry-run 是当时目录的预览，不会冻结候选；实际导入会重新读取目录。命令按 canonical URL 幂等，重跑不会重复导入已有文章。

`Skipped reasons` 只给按原因聚合的数量，不列文章标题。若你在找某一篇，先在 ai-assistant 仓完整导出 catalog，再按标题或 URL 查看该行的三个状态；不要把 exporter 直接接 `head`，它要求消费者完整读取 stdout：

```bash
cd /path/to/ai-assistant
mkdir -p tmp
./agents/summary-agent/run.sh --list-article-records --user your-summary-agent-user \
  > tmp/summary-agent-article-catalog.jsonl
jq 'select(.record_type == "article" and (.title | contains("目标标题片段"))) |
    {title, url, entry_status, file_status, vector_status}' \
  tmp/summary-agent-article-catalog.jsonl
```

### 如何判读输出

| 结果 | 退出码与关键输出 | 下一步 |
|---|---|---|
| dry-run 完成 | exit 0；`DRY RUN (no database changes)`、`Postcheck: not_run`、`Changed: no` | `eligible>0` 才有可导入候选；先审聚合的 `skipped` / `Skipped reasons` 和 `existing_without_interpretation`，需要逐篇身份时再查 catalog，确认范围后去掉 `--dry-run` |
| 导入并提交 | exit 0；`COMPLETE` 或 `BATCH COMPLETE`、`imported>0`、`Postcheck: passed`、`Changed: yes` | `remaining>0` 时继续下一批；否则从 `/wechat` 搜一篇本批标题确认消费者可见 |
| 没有新内容 | exit 0；`COMPLETE (nothing new to import)`、`imported=0`、`Changed: no`，通常 `Postcheck: not_needed` | 查看 `already_present`、`existing_without_interpretation` 和 `skipped`，判断候选是已存在、需另行补解读，还是上游记录不合格 |
| 有跳过 | 整体仍可 exit 0；`skipped>0` 并打印 `Skipped reasons` | 混合知识库里的非微信文章等跳过可以是预期；若某篇本应导入，按原因修复 ai-assistant 侧记录后重跑 |
| 操作失败 | exit 1；`WeChat KB operation: FAILED`、`Reason: ...`、`Changed: no committed changes from this operation` | 修复报出的数据库、目录、目录对齐、文件或 postcheck 问题，再原样重跑 |

`Counts` 各字段的运维含义：`catalog` 是目录文章总数，`eligible` 是完整且尚未入库的微信文章数，`imported` 是本批提交数，`already_present` 是已有文章且已有解读，`existing_without_interpretation` 是已有文章但缺解读，`skipped` 是不合格或非微信记录，`remaining` 是受 `--limit` 影响而留到后续批次的合格候选。

实际导入的 `Run id` 会随新条目落库，用于审计本批 provenance 和 postcheck；dry-run 虽会打印 run id，但不持久化任何批次记录。CLI 的 `Postcheck: passed` 证明本批数据库事务内的 item、解读、搜索索引、`/wechat` 可见性与公开来源隔离一致；再从正在服务这份数据库的 Web 入口搜索一篇本批标题，确认用户真正读到的是同一份结果：

```bash
query='替换成一篇本批导入标题中的唯一片段'
wechat_base_url=${AI_RADAR_WECHAT_BASE_URL:-http://localhost:8000}
set -o pipefail
curl --fail-with-body -sSG --data-urlencode "q=$query" "$wechat_base_url/api/v1/wechat" \
  | jq '.data | {total, titles: [.items[].title]}'
```

示例默认使用 fork 的 8000 端口；其它实例先把 `AI_RADAR_WECHAT_BASE_URL` 设为实际 origin（维护者产线 Mac 是 `http://localhost:8010`）。返回的 `total` 应大于 0，`titles` 应包含目标文章；若没有，先确认该 Web 进程实际连接的是本次 `--db-path` 指向的数据库。

### 归档源与成功批次没有删除式回滚

导入项归属保留源 `wx_ai_assistant_kb_archive`。该源固定 `enabled=0`，所以不会被 fetch、pipeline 或 A7 调度，也不会进入公开 source API；`/wechat`、详情页和微信跨源去重显式包含它。后续实时 feed 遇到同篇文章时会复用既有归档身份，不再生成重复卡片。

命令执行失败或 postcheck 不一致时，尚未提交的事务会整体回滚；成功提交后不提供批量删除命令。原因是归档项会作为后续实时 feed 的去重锚点，按 run id 删除可能同时删掉系统里唯一存储的一份文章身份。

如果以后确实需要撤销已提交批次，先停在这里，不要直接按 `extra_json.import_run_id` 删除。该动作应作为单独的数据修复接受评审，逐项确认是否已有可安全接替的实时源身份；需要频繁处理时再设计 promotion/claim ledger。目录 JSONL 字段与 ai-assistant 端生成规则见 [`../references/ai-assistant-contract.md`](../references/ai-assistant-contract.md#只读文章目录-jsonl)。

## 运维记录

### 2026-06-02 迁移留尾 author 回填（直接写生产 DB）

迁移后旧源历史文章因 author 全 NULL，导致名字/头像 fallback。已直接在生产 DB 修复（非代码改动）：

- 回填 author：歸藏 42 条、十字路口 102 条。
- 删除与新 Mp2RSS 源重复的 9 条文章。
- 补抓十字路口公众号头像写入 `wechat_account_avatars` 缓存。

这是一次性的数据修复；该句只记录当时迁移结果，不是当前 Mp2RSS 运行状态。待发布配置中，新摄取的文章 author 由 Wechat2RSS feed 带出，无需再处理。

## 验证

```bash
# reload 后检查状态；不要 SELECT url，避免把 feed token 回显到终端或日志
./run.sh admin sources reload
sqlite3 data/radar.db \
  "SELECT id, enabled, paused FROM sources WHERE kind='wechat' ORDER BY id;"

# fetch 只验证 active 来源；保留完整退出码与日志，wx_mp2rss 不应出现 OK/FAIL 行
./run.sh fetch

# Wechat2RSS 新文章入库
sqlite3 data/radar.db \
  "SELECT author, title FROM items WHERE source_id='wx_wechat2rss' ORDER BY fetched_at DESC LIMIT 10;"

# Wechat2RSS 服务自身联通性走外部 healthcheck，不拿 paused Mp2RSS 作探针
deploy/wechat2rss/healthcheck.sh

# 头像缓存
sqlite3 data/radar.db \
  "SELECT account, substr(avatar_url,1,8), checked_at FROM wechat_account_avatars;"
```

`substr(avatar_url,1,8)` 应为 `https://`；若出现 `http://` 说明归一化漏掉，检查 `normalize_wechat_avatar_url`。

```bash
# 解读覆盖与展示数量
sqlite3 data/radar.db \
  "SELECT COUNT(*) FROM items i JOIN sources s ON s.id=i.source_id WHERE s.kind='wechat' AND s.enabled=1;"
sqlite3 data/radar.db \
  "SELECT save_decision, kb_synced, COUNT(*) FROM wechat_interpretations GROUP BY save_decision, kb_synced;"
# 端口按本机 serve 实际绑定：本产线 Mac serve 绑 8010（其 plist 明禁回绑 8000），generic fork 默认 8000
curl -s http://localhost:8010/api/v1/wechat | jq '.data.total'
curl -s 'http://localhost:8010/api/v1/wechat?q=歸藏' | jq '.data.total'
curl -s 'http://localhost:8010/api/v1/wechat?q=合集' | jq '.data.total' # 应为 0；不匹配 Mp2RSS 合集源名

# KB 去重/可检索前置检查
cd "$AI_ASSISTANT_ROOT"
./agents/summary-agent/run.sh --check-url '<wechat-url>' --user "${AI_RADAR_INTERPRET_USER:-default}"
```

## 相关参考

- [README.md §信源](../../README.md#信源) — 用户视角的 `wechat` kind 说明
- [README.md §微信文章解读](../../README.md#微信文章解读) — `/wechat` 与 ai-assistant KB 回写说明
- [docs/references/ai-assistant-contract.md](../references/ai-assistant-contract.md) — 可选 summary-agent 脚本契约
- [docs/operations/services.md](services.md) — 服务清单（以该文件的服务表为准；`wewe` 已移除，附回滚说明）
- [docs/references/wechat-discovery-evidence.md](../references/wechat-discovery-evidence.md) — 后台发现候选与微信读书 canary 的历史证据台账（已停止推进）
- [deploy/wechat2rss/RUNBOOK.md](../../deploy/wechat2rss/RUNBOOK.md) — 自建 Wechat2RSS 部署与运维手册
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 旧 WeWe RSS 添加流程（已停用，仅回滚参考）
