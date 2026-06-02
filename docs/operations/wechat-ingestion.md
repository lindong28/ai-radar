# 微信公众号摄取运维

> Mutable snapshot. 微信公众号源（`kind="wechat"`）当前通过托管的 Mp2RSS 合集 feed 接入，文章卡片显示真实公众号名与头像。本文记录配置、运维机制和迁移留尾处理。

## 接入方案：Mp2RSS（已替代 WeWe RSS）

微信公众号没有公开 RSS。早先 ai-radar 用自建 WeWe RSS（本地 Docker + 微信读书扫码登录）做发现层，但扫码频繁失效、稳定性差，反复影响线上摄取。现已迁移到托管付费 SaaS [Mp2RSS](https://mp2rss.com/)：上游维护登录态，本项目只消费一个合集 feed，无需自建容器或扫码。

| 项 | Mp2RSS（当前） | WeWe RSS（已停用） |
|---|---|---|
| 部署 | 无（hosted SaaS） | 本地 Docker `:4000` + launchd `wewe` 服务 |
| 登录维护 | 上游负责 | 需本机微信读书扫码，频繁失效 |
| ai-radar 侧 | 消费一个合集 feed URL | 每个公众号一个 per-feed URL |

WeWe RSS 桥接（`wewe` launchd 服务、`deploy/wewe-rss/`、`docs/references/wechat-sources.md`）仅作回滚锚点保留，新部署无需启用。

## 配置：`MP2RSS_FEED_URL`

合集源 `wx_mp2rss` 在 `data/sources.toml` 中 `url = "${MP2RSS_FEED_URL}"`。真实 feed URL 含专属密钥，**不入 git**，存于 `.env`（项目根目录或 `~/.claude/.env`，均被 gitignore）：

```bash
MP2RSS_FEED_URL=https://mp2rss.com/feeds/<your-key>.xml
```

sources loader 用 `os.path.expandvars` 展开占位符（`src/airadar/sources/loader.py`）。环境变量未设置时占位符无法展开，启动即报错——这是有意的 fail-fast，避免静默抓空 feed。

cron / launchd 不继承交互式 shell 的 `export`，自动调度前确认 `.env` 已落该变量（与 LLM API Key 同处理，见 README §自动化调度）。

## 已停用的旧源

以下两个 WeWe 源已 `enabled = false`，暂留作回滚锚点，正式迁移确认后再删除：

| slug | 公众号 | 停用原因 |
|---|---|---|
| `wx_guizang` | 歸藏的AI工具箱 | 由 Mp2RSS 合集取代 |
| `wx_crossing` | 十字路口Crossing | Mp2RSS 无法订阅 |

## 真实公众号名 + 头像显示

微信文章卡片显示真实公众号名（按文章 author，而非 feed 合集名「微信公众号（Mp2RSS 合集）」）+ 公众号头像。

- **抓取**：fetcher 从微信文章页 `round_head_img` 提取头像 URL（兼容 `=` 和 `:` 两种 JS 写法），按源 backfill 并写缓存（`src/airadar/fetcher/runner.py`、`src/airadar/fetcher/wechat.py`）。
- **缓存表**：`wechat_account_avatars`（account → avatar_url），migration `007_wechat_account_avatars.sql` 建表。命中缓存的 account 不再重复抓取；负缓存（抓不到头像）有 TTL，到期重试。
- **URL 归一化**：头像 URL 统一 http → https（`mmbiz.qpic.cn`），避免 https 站点的 mixed-content 拦截。migration `008_wechat_avatar_https.sql` 回填存量，抓取侧 `normalize_wechat_avatar_url` 保证新写入也归一。
- **展示**：timeline / curated / items 路由 + SSR prepaint + client `app.js` 均 `LEFT JOIN wechat_account_avatars` 按 author 取头像；无头像时 fallback 到 `/wechat-icon.svg`。

## 运维记录

### 2026-06-02 迁移留尾 author 回填（直接写生产 DB）

迁移后旧源历史文章因 author 全 NULL，导致名字/头像 fallback。已直接在生产 DB 修复（非代码改动）：

- 回填 author：歸藏 42 条、十字路口 102 条。
- 删除与新 Mp2RSS 源重复的 9 条文章。
- 补抓十字路口公众号头像写入 `wechat_account_avatars` 缓存。

这是一次性的数据修复；新摄取的文章 author 由 Mp2RSS feed 正常带出，无需再处理。

## 验证

```bash
# 占位符已展开（不应再看到字面 ${MP2RSS_FEED_URL}）
./run.sh admin sources reload && sqlite3 data/radar.db \
  "SELECT slug, enabled, substr(url,1,40) FROM sources WHERE kind='wechat';"

# Mp2RSS feed 联通性 + 新文章入库
./run.sh fetch --sources data/sources.toml | tail -5
sqlite3 data/radar.db \
  "SELECT author, title FROM items WHERE source_id='wx_mp2rss' ORDER BY fetched_at DESC LIMIT 10;"

# 头像缓存
sqlite3 data/radar.db \
  "SELECT account, substr(avatar_url,1,8), checked_at FROM wechat_account_avatars;"
```

`substr(avatar_url,1,8)` 应为 `https://`；若出现 `http://` 说明归一化漏掉，检查 `normalize_wechat_avatar_url`。

## 相关参考

- [README.md §信源](../../README.md#信源) — 用户视角的 `wechat` kind 说明
- [docs/operations/services.md](services.md) — 服务清单（含已停用的 `wewe`）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 旧 WeWe RSS 添加流程（已停用，仅回滚参考）
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — 旧 WeWe RSS 桥接运维手册（已停用）
