# 微信公众号摄取运维

> Mutable snapshot. 微信公众号源（`kind="wechat"`）当前通过托管的 Mp2RSS 合集 feed 接入，文章卡片显示真实公众号名与头像。本文记录配置、运维机制和迁移留尾处理。

## 接入方案：Mp2RSS（已替代 WeWe RSS）

微信公众号没有公开 RSS。早先 ai-radar 用自建 WeWe RSS（本地 Docker + 微信读书扫码登录）做发现层，但扫码频繁失效、稳定性差，反复影响线上摄取。现已迁移到托管付费 SaaS [Mp2RSS](https://mp2rss.com/)：上游维护登录态，本项目只消费一个合集 feed，无需自建容器或扫码。

| 项 | Mp2RSS（当前） | WeWe RSS（已停用） |
|---|---|---|
| 部署 | 无（hosted SaaS） | 本地 Docker `:4000` + launchd `wewe` 服务 |
| 登录维护 | 上游负责 | 需本机微信读书扫码，频繁失效 |
| ai-radar 侧 | 消费一个合集 feed URL | 每个公众号一个 per-feed URL |

WeWe RSS 桥接已于 2026-06-06 从服务层移除（不再有 `wewe` launchd 服务或脚本 wiring）。回滚材料仍保留为文档：`deploy/wewe-rss/`（docker-compose + RUNBOOK）、`docs/references/wechat-sources.md`，launchd plist 与脚本 wiring 从 git 历史恢复。

## 配置：`MP2RSS_FEED_URL`

合集源 `wx_mp2rss` 在 `data/sources.toml` 中 `url = "${MP2RSS_FEED_URL}"`。真实 feed URL 含专属密钥，**不入 git**，存于 `.env`（项目根目录或 `~/.claude/.env`，均被 gitignore）：

```bash
MP2RSS_FEED_URL=https://mp2rss.com/feeds/<your-key>.xml
```

sources loader 用 `os.path.expandvars` 展开占位符（`src/airadar/sources/loader.py`）。`MP2RSS_FEED_URL` 未设置或设置为空时，loader 会记录 warning、跳过 `wx_mp2rss`，并继续加载其他信源；设置真实 feed URL 后该源自动生效。

cron / launchd 不继承交互式 shell 的 `export`。需要启用微信公众号摄取时，自动调度前确认 `.env` 或 `~/.claude/.env` 已落该变量（与 LLM API Key 同处理，见 README §自动化调度）；暂不启用时可以保持为空。

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

- 默认关闭：未设置 `AI_RADAR_ENABLE_INTERPRET=true` 时不会读取任何外部路径，打印 skipped 并 exit 0。
- 启用后必须设置 `AI_ASSISTANT_ROOT=/path/to/ai-assistant-compatible-root`；也可设置 `AI_RADAR_INTERPRET_USER`，默认 `default`。
- preflight 要求 `$AI_ASSISTANT_ROOT/agents/summary-agent/summarize.sh` 和 `$AI_ASSISTANT_ROOT/agents/summary-agent/run.sh` 存在且可执行；缺失时打印 `skip interpret...` 并 exit 0，不阻断前置 pipeline。
- `summarize.sh --input <tmpfile> --user "$AI_RADAR_INTERPRET_USER"` 负责生成 `<batch_dir>/<slug>_summary.md` 与 meta；`run.sh --save-from-batch ...` 负责写外部 KB、index 和 embedding。
- `run.sh --check-url <url> --user "$AI_RADAR_INTERPRET_USER"` 命中时不重新调用 LLM，直接读取 KB 中已有 summary 填充 `wechat_interpretations`。
- 脚本调用、stdout JSON、summary markdown 与 index.json 的完整契约见 [`ai-assistant-integration.md`](ai-assistant-integration.md)。

数据约定：

- `wechat_interpretations.save_decision=1` 是 `/wechat` 展示与 KB 回写的唯一闸门。
- `summary_md`、`abstract`、`tags_json` 在 `radar.db` 内保留独立网站副本；Web 请求不读取 ai-assistant 文件系统。
- `save_decision=0` 的文章只落库为已处理记录，不展示、不写 KB，避免每轮重复消耗 LLM。
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
./run.sh fetch --sources data/sources.toml | tail -5
sqlite3 data/radar.db \
  "SELECT author, title FROM items WHERE source_id='wx_mp2rss' ORDER BY fetched_at DESC LIMIT 10;"

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
curl -s http://localhost:8000/api/v1/wechat | jq '.data.total'
curl -s 'http://localhost:8000/api/v1/wechat?q=歸藏' | jq '.data.total'
curl -s 'http://localhost:8000/api/v1/wechat?q=合集' | jq '.data.total' # 应为 0；不匹配 Mp2RSS 合集源名

# KB 去重/可检索前置检查
cd "$AI_ASSISTANT_ROOT"
./agents/summary-agent/run.sh --check-url '<wechat-url>' --user "${AI_RADAR_INTERPRET_USER:-default}"
```

## 相关参考

- [README.md §信源](../../README.md#信源) — 用户视角的 `wechat` kind 说明
- [README.md §微信文章解读](../../README.md#微信文章解读) — `/wechat` 与 ai-assistant KB 回写说明
- [docs/operations/ai-assistant-integration.md](ai-assistant-integration.md) — 可选 summary-agent 脚本契约
- [docs/operations/services.md](services.md) — 服务清单（4 个活跃服务；`wewe` 已移除，附回滚说明）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 旧 WeWe RSS 添加流程（已停用，仅回滚参考）
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — 旧 WeWe RSS 桥接运维手册（已停用）
