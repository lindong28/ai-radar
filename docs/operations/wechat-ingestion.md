# 微信公众号摄取运维

> Mutable snapshot. 微信公众号源（`kind="wechat"`）当前生产链路是**托管 Mp2RSS 合集 feed 与自建 Wechat2RSS 双跑取并集**（`wx_mp2rss` + `wx_wechat2rss`，跨源去重见下文），文章卡片显示真实公众号名与头像。仓库内的公众号后台发现候选与微信读书 canary 默认关闭且已停止推进。本文记录这些路径的配置、运维机制和迁移门槛。
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

两个源给同一篇文章的 URL 没有公共子串——Mp2RSS 出短链 `/s/<token>`，Wechat2RSS 出长链 `?__biz=…&mid=…&idx=…&sn=…`——所以既有的按 URL / content hash 去重（`src/airadar/fetcher/dedup.py`，作用域是单个 source）看不出它们是同一篇。跨源去重改用**账号 + 归一化标题**，并要求两侧发布时间相差不超过 5 分钟；只匹配 enabled 的来源，且排除条目自己所属的 source（同源身份由上面那两条既有路径裁定，它们按"文章是什么"判而不是按"它叫什么"判）。

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

**所以重新启用一个曾被停用的微信源之前，先清一次重**：

```bash
uv run python - <<'EOF'
import sqlite3, unicodedata, re, collections
c = sqlite3.connect('data/radar.db'); c.row_factory = sqlite3.Row
n = lambda t: unicodedata.normalize('NFKC', re.sub(r'\s+', ' ', str(t or '')).strip()).casefold()
rows = c.execute("""SELECT i.id, i.author, i.title, i.published_at, i.source_id
FROM items i JOIN sources s ON s.id=i.source_id WHERE COALESCE(s.kind,'feed')='wechat'""").fetchall()
seen = collections.defaultdict(list)
for r in rows:
    seen[(r['author'], n(r['title']), r['published_at'][:16])].append(r)
for key, group in seen.items():
    if len(group) > 1:
        print(key[1][:40], '->', [(g['source_id'], g['id']) for g in group])
EOF
```

打印出来的每一组都是同一篇文章的多份；决定保留哪一份后再删另一份的 `items` 行与其 `wechat_interpretations` 行。

### 对告警与计数的影响

A7「来源静默」逐源判定，阈值公式与「无法评估」的判据以 [monitoring-alerting.md §告警规则](monitoring-alerting.md#告警规则) 为权威。双跑对它的意味只有一条：`wx_wechat2rss` 入库的只是 Mp2RSS 漏掉的那部分，入库速率天然低于 Mp2RSS，样本不足期间它计入「无法评估」而不是「健康」。**两个源的入库量不能互相比较**——先到的那个源建条目、后到的被去重丢弃，所以计数反映的是抢到手的次数而不是覆盖率。覆盖率仍由 [`docs/plans/20260816-mp2rss-replacement/tools/shadow_compare.py`](../plans/20260816-mp2rss-replacement/tools/shadow_compare.py) 直接从两个 feed 测量，它不读生产库，因而不受去重影响（采样 cron 见 [services.md §服务](services.md#服务) 的 shadow-observe 行）。

### 日志与凭据脱敏

`deploy/wechat2rss/logs.sh` 查看自建 Wechat2RSS 的部署日志时对 feed token 做脱敏。2026-08-20 起脱敏改为匹配到值末尾、在 `&` 处停下（此前只匹配 `[A-Za-z0-9_.~-]+`，`k=abc+def/ghi=` 这类合法 token 会漏出后缀），`&` 之后有诊断价值的 query 字段保留。注意脱敏要覆盖两条独立通道：服务自己打印的（启动横幅、配置回显），以及你构造的带 token URL 被对方记进访问日志的——只堵前一条时第二条原样漏出。

## 公众号后台发现候选与微信读书 canary（默认关闭，已停止推进）

仓库内还留着两条替代发现路线的实现：公众号后台 `searchbiz + appmsgpublish` 适配器与微信读书只读 canary。两者**默认关闭**（`data/wechat-discovery.toml` 的 `manual_backend_requests_enabled=false`），不被 `fetch_all` 或 pipeline 调用，也不写生产 `items`。

后台路线已因平台级不可用**停止推进**（跨账号文章列举在 2026-07-30 前后被平台限制，见 [ADR-061](../adr/061-deprecate-wechat-admin-discovery-line.md)）；微信摄取改由 Mp2RSS + Wechat2RSS 双跑承担。

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

`/wechat` 列表支持 `?q=` 搜索，匹配范围限定为解读卡片字段：`items.title`、`items.author`（公众号名）、`wechat_interpretations.abstract`、`wechat_interpretations.tags_json`。不匹配聚合 feed 名 `sources.name`，也不搜索 `summary_md` 全文。搜索使用 SQLite `LIKE` + 简繁扩展，且**空格不敏感**（查询与被匹配列两侧都剥除空白后比对，含全角空格——`分享 Claude Code` 与 `分享Claude Code` 等价）；详情链接和详情页返回链接会保留 `q` 与 `page`。

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
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — 旧 WeWe RSS 桥接运维手册（已停用）
