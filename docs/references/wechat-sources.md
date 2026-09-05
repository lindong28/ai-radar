# 历史配方：通过 WeWe RSS 添加微信公众号源（当前 checkout 不可直接执行）

> 读者标注：**[User]**（部署者 / 运维者视角的操作步骤，非开发者内部细节）。
>
> **这是一份不可直接执行的历史 recipe，不是当前 rollback runbook。** 仓内待发布语义由 Wechat2RSS 主动抓取；Mp2RSS 保持 `enabled=true, paused=true`，只保留历史可见性与跨源去重身份，不发请求、不参加 A7。生产迁移与发布尚未执行，终态见 [operations/wechat-ingestion.md](../operations/wechat-ingestion.md) 的现场 gate。WeWe RSS 桥接已于 2026-06-06 从服务层移除，其容器又于 2026-08-20 手动停止。
>
> 当前 checkout 已不包含 `deploy/wewe-rss/` 的 compose、env 示例与 runbook；真正恢复时必须先从 git tree `29ca189^` 取回完整四文件 package，不能从零散片段拼装。package 恢复后，下文服务与 feed 发现步骤才有执行对象；到来源配置步骤时必须舍弃历史 v1 `url =` 写法，改在 `tests/fixtures/aihot_sources.json` 显式声明 `fetch_url`、`enabled` 和 `paused`，再由 `scripts/render_sources_from_contract.py --write` 生成 `data/sources.toml`。本页没有提供一套可在当前 checkout 原样执行的完整恢复流程，尤其不得把 TOML 片段直接追加到当前生成物。权威维护入口见 [信源维护与验证](source-maintenance.md)。
>
> Operational reference（历史）：微信公众号没有公开 RSS，ai-radar 曾通过 WeWe RSS 做发现层，再由本项目抓取原文正文供内部 LLM 使用。

## 历史前置条件（仅在完整 package 恢复后适用）

- 本机 WeWe RSS 已启动：`docker compose -f deploy/wewe-rss/docker-compose.sqlite.yml up -d`
- WeWe 已完成微信读书扫码登录，`http://localhost:4000/dash/accounts` 能看到启用的读书账号。
- Playwright Chromium 已安装：`uv run playwright install chromium`
- 若 Docker 不能直连微信读书服务，`deploy/wewe-rss/.env` 需要设置 `WEWE_HTTP_PROXY=http://host.docker.internal:59527` 和 `WEWE_HTTPS_PROXY=http://host.docker.internal:59527`。

## 历史添加流程

1. 打开 `http://localhost:4000/dash/sources`，用 `WEWE_AUTH_CODE` 登录 WeWe dashboard。
2. 在「公众号源」中提交一篇目标公众号的 `https://mp.weixin.qq.com/s/...` 分享链接。
3. 等 WeWe 创建 feed 后，记录 feedId 和 per-feed RSS URL，例如 `http://localhost:4000/feeds/MP_WXS_3540975510.rss`。
4. 为防止上游封控，批量添加公众号时每个订阅之间留出约 10 分钟，或至少等第一个号的文章出现在 `/feeds/all.rss` 后再继续。
5. （历史步骤）当时曾直接在 `data/sources.toml` 增加源；现行 v2 不得如此操作：

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

6. （历史步骤）重载 sources 并跑流水线：

```bash
./run.sh admin sources reload
./run.sh fetch --sources data/sources.toml
./run.sh prefilter
./run.sh score
./run.sh enrich
./run.sh curate
```

## 历史验证

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
