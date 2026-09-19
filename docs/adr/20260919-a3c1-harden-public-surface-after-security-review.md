# 安全审查后加固公网面：origin 自验 admin token、采集入口白名单、请求边界与依赖升级

- Status: accepted
- Date: 2026-09-19

## Context

2026-09-19 对开源仓与生产站 `news.aiplanet.live` 做了一次全项目安全审查（Web 层 / 采集管线 / 部署与基础设施 / 仓库卫生与供应链四路只读审查，报告落在维护者本机、不入库）。经主线程复核的承重发现：

- 管理面守卫只判 `Cf-Access-Jwt-Assertion` 头非空。设计前提是 Cloudflare Access 在前面验签，但生产自 [ADR-039](039-route-news-through-edgeone-dns-only-cname.md) 起走 EdgeOne、从未有过那道边缘；线上任意值即可拿到 `/admin`、`/admin/usage` 与 `/api/v1/admin/metrics` 的 200。该缺口自 2026-08-12 已记在 issue，未修。
- RSS 条目 `<link>` 入库无 scheme 白名单，`javascript:` / `data:` 可原样存进 `items.url` 并渲染成可点击 href；本地库已有一条 `about:blank`。微信正文抓取把 `items.url` 直接交给 headless Chromium 导航，没有 host 白名单。
- HTML 页面路由以普通函数调用 API handler，绕过了 API 路由自己的 `Query(ge/le)`：`limit=0` 打 500，`limit=-1` 变成 SQLite `LIMIT -1` 取全表；FTS 空短语（`q="""`）抛语法错误 500；`q` 无长度上限；timeline 的 `channel` 未归一即进缓存 key。
- `/docs`、`/redoc`、`/openapi.json` 公开，列出 admin 路由。
- 依赖含已知 CVE：starlette 1.0.0（5 条，含 StaticFiles SSRF 与 Host 校验）、json-repair、soupsieve、lxml-html-clean、anyio；公开仓 Dependabot alerts 关闭。
- 源站可绕过 EdgeOne 直连；裸 IP 的 HTTP 返回整站 200，与仓内 `-http.conf` 的 `default_server → 444` 不符（服务器上生效的是遗留 `sites-available/ai-radar` 的 default_server）。响应无安全头，nginx 版本外泄。
- `.gitignore` 未覆盖 `data/*.db.snapshot*`、`data/*.db.shipping*`、`data/raw-capture/`、`data/recovery/`，`git add -A` 会把十几 GB 生产数据推到公开仓。

## Options Considered

### Option A: 把生产重新放回 Cloudflare Access 之后，保留存在性检查
- Pros: 不改应用代码；与 2026-06 的设计一致。
- Cons: 与 ADR-039 选定的 EdgeOne 拓扑冲突；源站直连仍可绕过；这条路自 8 月起就是"待恢复"而从未恢复。

### Option B: origin 自验共享 token，边缘只做纵深（选定）
- Pros: 边界落在 origin，与前置 CDN 无关，直连源站也守得住；实现是几十行且可用常量时间比较；未配置即 fail-closed。
- Cons: token 要进服务器 `.env`，轮换靠人；没有身份、只有一把钥匙。

### Option C: origin 验 Cloudflare JWT 签名
- Pros: 真正的身份鉴权。
- Cons: 生产没有 Cloudflare 在前面，验签对象不存在。

### 采集入口：在展示层过滤 scheme（否决）vs 入库前丢弃（选定）
展示层只转义实体、不查 scheme，且 `items.url` 还被微信抓取与去重键消费；在 `feed_rules.normalized_entry_url` 一处丢弃非 http(s) 条目，所有下游同时受益。微信抓取另加 `mp.weixin.qq.com` host 白名单，因为 `items.url` 的来源不止 RSS 一条路径。

### 安全头：应用中间件 vs nginx（选 nginx）
两处都加会重复输出；nginx 那一处同时能 `server_tokens off`、封 `/docs` 与清空旧 header，是单一落点。CSP 先以 Report-Only 上线：模板仍有内联脚本与 `onload`/`onerror` 属性，强制策略会把页面打坏；不用 nonce，因为公开页在边缘缓存 90 秒。

## Decision

1. `require_admin_access` 改为校验 `AI_RADAR_ADMIN_TOKEN`（`X-Admin-Token` 或 `Authorization: Bearer`，`hmac.compare_digest`，<16 字符视为未配置，fail-closed 403）。`AI_RADAR_ADMIN_ALLOW_LOCAL` 语义不变。全部 admin 响应带 `Cache-Control: private, no-store`（独立 reviewer 指出：此前这些路径不发任何缓存头，只靠 EdgeOne 控制台的"默认不缓存"规则挡着）。三个 admin 路由 `include_in_schema=False`；`FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`。
2. `normalized_entry_url` 只放行 scheme ∈ {http, https} 且 netloc 非空的条目；`author` 入库前去标签、折叠空白、截 200 字。`scrape_article` 在起浏览器前拒绝非 `mp.weixin.qq.com` 的 URL。
3. 四个 HTML 页面路由声明与 API 相同的 `Query(ge=1, le=…)`；所有 `q` 加 `max_length=200`；FTS 空短语退化为"无查询"；timeline `channel` 归一到已知集合后再进缓存 key。
4. `uv.lock` 升级 starlette 1.6.0、json-repair 0.63.4、soupsieve 2.9.2、lxml 6.1.3 + lxml-html-clean 0.4.5、anyio 4.14.2；`pytest-asyncio` 移到 dev 组；公开仓开启 Dependabot alerts 与 security updates，加 `.github/dependabot.yml` 与 `SECURITY.md`。
5. nginx：`server_tokens off`、nosniff / XFO DENY / Referrer-Policy / Permissions-Policy、Report-Only CSP、封 `/docs|/redoc|/openapi.json`、`proxy_set_header Cf-Access-Jwt-Assertion ""`。systemd serve 单元加 `NoNewPrivileges` / `PrivateTmp` / `ProtectSystem=full` 等沙箱指令（不加 `ProtectHome`：checkout 在服务用户 home 下）。wechat2rss 镜像按 digest 钉住。DB 同步脚本 SSH 默认 `StrictHostKeyChecking=accept-new`。
6. `.gitignore` 扩到全部运行时 DB 副本与采集/恢复目录；docs 中的源站 IP、新加坡代理 IP、腾讯云 AppId / Lighthouse / EdgeOne zone id 换成占位符。

## Consequences

- 生产要两步才生效：部署代码，并在服务器 `.env` 写入 `AI_RADAR_ADMIN_TOKEN`；nginx 变更由维护者 reload。服务器上遗留 `sites-available/ai-radar` 的 80 端口 default_server 仍会让裸 IP HTTP 请求命中整站，这条属服务器侧配置漂移，本 ADR 只记录、不由仓内文件解决。
- 本机 8010 预览与所有走 `/admin` 的脚本都要带 token；测试通过 `tests/conftest.py` 的 autouse fixture 统一注入。
- 采集侧：非 http(s) 链接的条目会被静默丢弃（原先入库后只是不可点），微信 feed 里指向别的 host 的文章不再抓正文（返回 `success: False`，走既有 fallback）。
- 未做、留待后续：sudoers 收窄与部署专用 key（`ubuntu` 用户 `NOPASSWD: ALL`，属服务器侧）、EdgeOne 回源鉴权头与 `limit_req`、CSP 从 Report-Only 转强制（要先把内联脚本改成 hash；当前 Report-Only 头没有 `report-uri`，不收集违规，只是占位）、RSS 抓取的响应大小与总 deadline、interpret 子进程的 env 白名单。公开仓分支保护未开（`gh api` 可写，但规则集要维护者定）。
- 独立审查列出、本轮不处置的 LOW：`q` 超长或 `limit` 越界时页面路由返回 FastAPI 原生 JSON 422 而非 HTML（此前是 500，属改善；模板 `<input name="q">` 未加 `maxlength`）；微信头像刷新路径收到 host 拒绝时不写日志、且把拒绝缓存成一次 miss（本地库 4658 条微信 item 只有 1 条非 `mp.weixin.qq.com`，现实影响为零）；starlette 1.6 对 `httpx` TestClient 打 deprecation 警告（下一大版本可能要求 `httpx2`）。
- 历史里 `ad9b705` 的 cloudflared `config.yml`（tunnel UUID，无凭据）与 docs 中的旧 IP 仍在 `origin/main` 历史中；按仓规不在本 checkout 改写历史。
