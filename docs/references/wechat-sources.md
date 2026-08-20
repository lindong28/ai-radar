# 如何添加微信公众号源

> 读者标注：**[User]**（部署者 / 运维者视角的操作步骤，非开发者内部细节）。
>
> **本流程已停用，仅作回滚参考。** 微信摄取现走 Mp2RSS + Wechat2RSS 双跑取并集，见 [operations/wechat-ingestion.md](../operations/wechat-ingestion.md)。WeWe RSS 桥接已于 2026-06-06 从服务层移除，其容器又于 2026-08-20 手动停止。
>
> 另注：下文 `data/sources.toml` 样例里的 `url =` 是 v1 schema 的字段名；当前 sources 文件是 **v2 schema，字段名为 `fetch_url`**（`src/airadar/sources/loader.py`）。真要回滚时按 v2 写。
>
> Operational reference（历史）：微信公众号没有公开 RSS，ai-radar 曾通过 WeWe RSS 做发现层，再由本项目抓取原文正文供内部 LLM 使用。

## Prerequisites

- 本机 WeWe RSS 已启动：`docker compose -f deploy/wewe-rss/docker-compose.sqlite.yml up -d`
- WeWe 已完成微信读书扫码登录，`http://localhost:4000/dash/accounts` 能看到启用的读书账号。
- Playwright Chromium 已安装：`uv run playwright install chromium`
- 若 Docker 不能直连微信读书服务，`deploy/wewe-rss/.env` 需要设置 `WEWE_HTTP_PROXY=http://host.docker.internal:59527` 和 `WEWE_HTTPS_PROXY=http://host.docker.internal:59527`。

## Add Flow

1. 打开 `http://localhost:4000/dash/sources`，用 `WEWE_AUTH_CODE` 登录 WeWe dashboard。
2. 在「公众号源」中提交一篇目标公众号的 `https://mp.weixin.qq.com/s/...` 分享链接。
3. 等 WeWe 创建 feed 后，记录 feedId 和 per-feed RSS URL，例如 `http://localhost:4000/feeds/MP_WXS_3540975510.rss`。
4. 为防止上游封控，批量添加公众号时每个订阅之间留出约 10 分钟，或至少等第一个号的文章出现在 `/feeds/all.rss` 后再继续。
5. 在 `data/sources.toml` 增加源：

```toml
[[source]]
slug = "wx_example"
name = "公众号名称"
url = "http://localhost:4000/feeds/MP_WXS_xxx.rss"
kind = "wechat"
tier = "T2"
homepage_url = "https://mp.weixin.qq.com/"
enabled = true
```

6. 重载 sources 并跑流水线：

```bash
./run.sh admin sources reload
./run.sh fetch --sources data/sources.toml
./run.sh prefilter
./run.sh score
./run.sh enrich
./run.sh curate
```

## Verification

```bash
curl -s http://localhost:4000/feeds/all.rss | rg 'mp.weixin.qq.com/s/'
sqlite3 data/radar.db "SELECT id, name, kind, url FROM sources WHERE kind='wechat';"
sqlite3 data/radar.db "SELECT source_id, title, url, length(content_text) FROM items WHERE source_id LIKE 'wx_%' ORDER BY fetched_at DESC LIMIT 10;"
```

站点/API 合规要求：

- `url` 必须回链到 `https://mp.weixin.qq.com/s/...`
- timeline JSON 不输出 `content_text`
- wechat item 的 `content_preview` 必须为 `null`
- enrich 后公开中文标题/摘要；未 enrich 时卡片只显示标题与回链，不显示正文预览。

