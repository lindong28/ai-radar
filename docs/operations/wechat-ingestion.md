# 微信公众号摄取运维

> Mutable snapshot. 微信公众号实时生产链路是**托管 Mp2RSS 合集 feed 与自建 Wechat2RSS 双跑取并集**（`wx_mp2rss` + `wx_wechat2rss`，跨源去重见下文），另有维护者显式触发的 ai-assistant KB 内部归档补录；文章卡片显示真实公众号名与头像。仓库内的公众号后台发现候选与微信读书 canary 默认关闭且已停止推进。本文记录这些路径的配置、运维机制和迁移门槛。
>
> 本目录（`docs/operations/`）是维护者产线 runbook，绑定具体实机拓扑；fork 部署路径见 [README](../../README.md)。

## 接入方案：Mp2RSS（已替代 WeWe RSS）

微信公众号没有公开 RSS。早先 ai-radar 用自建 WeWe RSS（本地 Docker + 微信读书扫码登录）做发现层，但扫码频繁失效、稳定性差，反复影响线上摄取。现已迁移到托管付费 SaaS [Mp2RSS](https://mp2rss.com/)：上游维护登录态，本项目只消费一个合集 feed，无需自建容器或扫码。

| 项 | Mp2RSS（当前） | WeWe RSS（已停用） |
|---|---|---|
| 部署 | 无（hosted SaaS） | 本地 Docker `:4000` + launchd `wewe` 服务 |
| 登录维护 | 上游负责 | 需本机微信读书扫码，频繁失效 |
| ai-radar 侧 | 消费一个合集 feed URL | 每个公众号一个 per-feed URL |

WeWe RSS 的退役状态（服务层移除时间、容器当前状态、回滚材料在哪）以 [services.md §服务](services.md#服务) 那条引述为权威，此处不复述。

## 配置：`MP2RSS_FEED_URL`

合集源 `wx_mp2rss` 在 `data/sources.toml` 中 `fetch_url = "${MP2RSS_FEED_URL}"`（v2 schema 的字段名是 `fetch_url`，v1 才是 `url`，见 `src/airadar/sources/loader.py`）。真实 feed URL 含专属密钥，**不入 git**，存于 `.env`（项目根目录或 `~/.claude/.env`，均被 gitignore）：

```bash
MP2RSS_FEED_URL=https://mp2rss.com/feeds/<your-key>.xml
```

sources loader 用 `os.path.expandvars` 展开占位符（`src/airadar/sources/loader.py`）。`MP2RSS_FEED_URL` 未设置或设置为空时，loader 会记录 warning、跳过 `wx_mp2rss`，并继续加载其他信源；设置真实 feed URL 后该源自动生效。

cron / launchd 不继承交互式 shell 的 `export`。需要启用微信公众号摄取时，自动调度前确认 `.env` 或 `~/.claude/.env` 已落该变量（与 LLM API Key 同处理，见 README §自动化调度）；暂不启用时可以保持为空。

## 双跑：`WECHAT2RSS_FEED_URL` 与跨源去重

自建的 Wechat2RSS（部署见 `deploy/wechat2rss/RUNBOOK.md`）作为第二个生产微信源 `wx_wechat2rss` 与 Mp2RSS **并行运行**，生产取两者并集。这是替换 Mp2RSS 之前的一步：只有真正同时跑过，才知道停掉 Mp2RSS 会丢什么。**Mp2RSS 不因此停用**，停用是另一个需要单独决定的动作。

```bash
WECHAT2RSS_FEED_URL=http://127.0.0.1:8080/feed/all.xml?k=<RSS_TOKEN>
```

该地址是 loopback 加本部署的 token，只在跑着那个容器的机器上有效，因此放**项目根 `.env`**，不要放跨机器共享的 `~/.claude/.env`。`/feed/all.xml` 是合集端点，全局上限 50 条（各账号自己的 feed 各 20 条）。缺 `k` 参数时它返回 `HTTP 200` 加 `{"err":"k param is empty..."}`，所以判断它是否可用要看返回体、不能只看状态码。

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

### 停用其中一个微信源时会发生什么

跨源去重只匹配 **enabled** 的来源。这是一个取舍，两面都实测过：

- **停用后**，该源独有的文章立刻从 `/wechat` 消失——页面按同一个 `s.enabled=1` 过滤。落在另一个源当前 feed 窗口内的（Mp2RSS 一次给 100 条）会在随后几轮被它补回并重新可见；已经滚出窗口的补不回来。
- **重新启用后**，被补回的那一篇会与原来那一行同时可见，即**同一篇文章出现两张卡**。

选择匹配 enabled 而不是匹配全部行，是因为另一种写法下隐藏行会持续拦住每一次插入，那些文章**永久补不回来**。重复只在"停用后又重新启用"这一条路径上出现。

**所以在当前没有受测清重命令时，不要重新启用一个曾被停用的微信源。** 旧 runbook 曾内嵌一段一次性 SQL/Python，但它没有复用运行时的可见源谓词、同源排除和标题规范化，可能错删或漏删，现已移除。若业务确实要求重新启用，当前安全入口尚未实现，进展跟踪见 [`docs/issues/general.md`](../issues/general.md) 中的对应 open issue；不要直接拼 SQL 修改生产库。

### 对告警与计数的影响

A7「来源静默」逐源判定，阈值公式与「无法评估」的判据以 [monitoring-alerting.md §告警规则](monitoring-alerting.md#告警规则) 为权威。双跑对它的意味只有一条：`wx_wechat2rss` 入库的只是 Mp2RSS 漏掉的那部分，入库速率天然低于 Mp2RSS，样本不足期间它计入「无法评估」而不是「健康」。**两个源的入库量不能互相比较**——先到的那个源建条目、后到的被去重丢弃，所以计数反映的是抢到手的次数而不是覆盖率。覆盖率仍由 [`docs/plans/20260816-mp2rss-replacement/tools/shadow_compare.py`](../plans/20260816-mp2rss-replacement/tools/shadow_compare.py) 直接从两个 feed 测量，它不读生产库，因而不受去重影响（采样 cron 见 [services.md §服务](services.md#服务) 的 shadow-observe 行）。

### 日志与凭据脱敏

`deploy/wechat2rss/logs.sh` 查看自建 Wechat2RSS 的部署日志时对 feed token 做脱敏。2026-08-20 起脱敏改为匹配到值末尾、在 `&` 处停下（此前只匹配 `[A-Za-z0-9_.~-]+`，`k=abc+def/ghi=` 这类合法 token 会漏出后缀），`&` 之后有诊断价值的 query 字段保留。注意脱敏要覆盖两条独立通道：服务自己打印的（启动横幅、配置回显），以及你构造的带 token URL 被对方记进访问日志的——只堵前一条时第二条原样漏出。

## 公众号后台发现候选与微信读书 canary（默认关闭，已停止推进）

仓库内还留着两条替代发现路线的实现：公众号后台 `searchbiz + appmsgpublish` 适配器与微信读书只读 canary。两者**默认关闭**（`data/wechat-discovery.toml` 的 `manual_backend_requests_enabled=false`），不被 `fetch_all` 或 pipeline 调用，也不写生产 `items`。

后台路线已因平台级不可用**停止推进**（跨账号文章列举在 2026-07-30 前后被平台限制，见 [061-wechat-discovery](../adr/061-deprecate-wechat-admin-discovery-line.md)）；微信摄取改由 Mp2RSS + Wechat2RSS 双跑承担。

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
| 正常摄取之后发布的新文章 | 不用本命令；继续用 Mp2RSS / Wechat2RSS 与 pipeline |
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

这是一次性的数据修复；新摄取的文章 author 由 Mp2RSS feed 正常带出，无需再处理。

## 验证

```bash
# 设置 MP2RSS_FEED_URL 后，占位符已展开（不应再看到字面 ${MP2RSS_FEED_URL}）
./run.sh admin sources reload && sqlite3 data/radar.db \
  "SELECT id, enabled, substr(url,1,40) FROM sources WHERE kind='wechat';"

# Mp2RSS feed 联通性 + 新文章入库
# 不要写成 `... | tail -5`：管道会把 fetch 的真实退出码换成 tail 的 0，抓取失败看不出来
./run.sh fetch --sources data/sources.toml
sqlite3 data/radar.db \
  "SELECT author, title FROM items WHERE source_id='wx_mp2rss' ORDER BY fetched_at DESC LIMIT 10;"

# Wechat2RSS feed 联通性 + 新文章入库（同一轮 fetch；并集里被去重丢掉的不会出现在这里）
sqlite3 data/radar.db \
  "SELECT author, title FROM items WHERE source_id='wx_wechat2rss' ORDER BY fetched_at DESC LIMIT 10;"

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
