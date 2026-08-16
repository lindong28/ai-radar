# ADR-039：通过 DNS-only CNAME 将 news 入口接入 EdgeOne

- 状态：Accepted
- 日期：2026-08-13
- 范围：`news.aiplanet.live` 的 1 个月 EdgeOne 个人版试运行；不改变 `aiplanet.live` 其他子域的流量路径

## 背景

Mac mini 可见 Chrome 的首次冷访问显示，`https://news.aiplanet.live/wechat` 的连接耗时约 3075ms、TTFB 约 3133ms、FCP 约 3500ms；同条件 AIHOT 首页约为 933ms、1130ms、1492ms。连接复用后，AI Radar 的 `/wechat` TTFB 约 108ms、FCP 约 288ms；直连源站的热请求中，应用处理通常只有 24–26ms。读数把主要空白等待定位在首次连接网络路径，而不是数据库、SSR 或浏览器端渲染。

当前 `news.aiplanet.live` 以 DNS-only A 记录直连 `111.229.134.9`，权威 TTL 为 300 秒。AIHOT 使用 EdgeOne CNAME，响应可观察到 EdgeOne 缓存命中。用户选择腾讯云 EdgeOne 直连接入，并购买个人版 1 个月套餐；套餐提供 50GB 安全加速流量、300 万次请求、1 个站点与 200 个子域名，自动续费关闭。

## 考虑过的方案

1. 保持源站直连。该方案保留当前约 3 秒冷连接成本，不能满足与 AIHOT 差距不超过 10% 的目标。
2. 使用 Cloudflare 中国大陆 CDN。当前套餐不含中国大陆网络，需要 Enterprise，成本显著高于已选 EdgeOne 个人版。
3. 把整个站点默认缓存。`/admin`、搜索、详情、健康检查及多类 API 不应被无条件缓存；只有公开列表的安全参数变体明确发送 public `Cache-Control`。
4. 先优化数据库、SSR 或前端作为主修。热态服务端处理已经远小于冷连接成本，无法解释或消除首次空白等待。
5. 保留 Cloudflare 权威 DNS，但让其代理 EdgeOne CNAME。该方案会形成 `Cloudflare → EdgeOne → origin` 双层代理，使链路语义、预验证、性能验收和回滚判据失真，因此否决。

## 决策

保留 Cloudflare 作为 `aiplanet.live` 的权威 DNS，在 EdgeOne 中以 CNAME 模式创建 `aiplanet.live` 站点，只添加 `news.aiplanet.live` 为加速域名。用户明确选择一个月试运行同时覆盖中国大陆与境外，因此站点使用 EdgeOne `global` 可用区；该区域决定的是 `news.aiplanet.live` 的加速流量范围，不恢复已经退役的裸域 `aiplanet.live`。Cloudflare 上的 `news.aiplanet.live` 必须设置为 **DNS-only CNAME**，目标值为 EdgeOne 为该加速域名分配的专属 CNAME；禁止启用 Cloudflare 代理，目标链路是 `client → EdgeOne → origin`。

源站保持 `111.229.134.9`，固定使用 HTTPS 443 回源，并使用 `news.aiplanet.live` 作为 Host 与 SNI。EdgeOne 全局强制 HTTPS 使用 301；部署收敛后一条全局设置已经在公开域名及四个当前 Edge IP 上对普通路径、带随机 query 的路径和未知路径返回正确 301，因此不再叠加 per-host 重定向规则，也不通过改变回源协议间接依赖源站跳转。

缓存采用保守边界：全站默认不缓存，仅精确 `/wechat` 与 `/api/v1/wechat` 遵从源站 `Cache-Control`；缓存键保留全部 query string。搜索请求继续由源站的 `private, no-store` 排除缓存，详情、admin、健康检查和其他 API 不因本决策获得缓存权限。

首次接入后的真实浏览器瀑布显示，HTML 已命中边缘缓存后，`/style.css` 仍因每次回源等待约 1.8 秒，并在完成后约 80ms 才发生 FCP；`/app.js` 也因回源等待约 1.37 秒。两者连续请求均为 EdgeOne `MISS`，源站只提供 ETag/Last-Modified 而没有 `Cache-Control`。因此额外仅对 Host `news.aiplanet.live` 下的精确路径 `/style.css` 与 `/app.js` 配置节点强制缓存 7 天，不修改浏览器缓存 TTL；实现可以是一条双路径规则或两条语义等价的单路径规则。该 TTL 依赖以下发布契约：修改任一资源时，必须在同一发布单元中更新所有相关页面引用的 `?v=`；若不能同步更新，则发布前精确清除仍会被访问的旧 query URL。无法长期履行该契约时，须另行决策改用内容哈希文件名或缩短 TTL。

节点缓存收敛后，真正冷连接的成对浏览器样本中 `/wechat` median TTFB/FCP 仍约为 AIHOT 的 1.46 倍；代理链路的 waterfall 继续显示外链 CSS 的第二次请求阻塞 FCP。为消除该串行链路而不减少 50 条首屏卡片、不维护第二份 critical CSS，也不改变视觉，仅 `/wechat` 列表页把当前 `web/static/style.css` 的完整内容内联进 SSR HTML；CSS 文件保持单一源码，模板目录内的 git-tracked 相对 symlink 精确指向该文件，`wechat.html` 通过 Jinja include 输出其内容。`/app.js` modulepreload/async、详情页及其他页面继续沿用现有外链资源。用同一份本地 50 条真实数据渲染做成对压缩时，外链 HTML 与 CSS 的 gzip 总量为 47,266 字节，完整内联为 47,653 字节，只增加 387 字节（约 0.8%）；该调整主要改变请求拓扑，而非用明显增加传输量换取首屏速度。

内联 CSS 的刷新契约不同于其他页面的 `?v=` 资产契约：发布链必须保留 tracked symlink 语义，且 CSS 变更与模板引用仍属于同一个发布单元；Jinja 当前的 auto-reload 会让后续 render 读取更新后的目标文件，不额外要求应用重启。真实公网允许继续遵循 `/wechat` HTML 既有 `max-age=90, stale-while-revalidate=30`，不为这项优化新增逐次精确 purge，因此在 EdgeOne 遵从 stale-while-revalidate 的节点上，旧 HTML 的用户侧可见窗口约为 120 秒量级。发布验证必须在该窗口后从真实公网 `/wechat` 检查内联内容；若某次发布另有“立即生效”要求，那次发布再显式执行精确 purge，不能把 purge 静默当作日常前提。模板安全检查同时要求 CSS 不含 Jinja 分隔符与 `</style`、symlink 的解析目标仍精确等于 `web/static/style.css`；任一条件不满足时不得直接以内联标签输出。

切换 DNS 前，必须用 EdgeOne 分配的 CNAME/边缘地址预验证 Host、SNI、证书、回源响应与关键路径。切换后的验收前置是：权威查询与明确列出的公共递归解析器采样均显示 `news.aiplanet.live` 链到 EdgeOne 分配的 CNAME，EdgeOne 控制台报告接入域名 active，响应具有 EdgeOne 链路特征。仅证明业务可访问不足以证明该架构已落地。

若配置 Nginx real IP，只信任站点启用后 EdgeOne 官方列出的回源 IP 段，并使用 `EO-Connecting-IP`；直连源站并伪造该头必须不能改变可信客户端 IP。X-Forwarded-For 不作为单独信任依据。源站防护与回源 IP 白名单不是本次性能切换的前置条件；若启用，必须接受并维护 EdgeOne 回源网段变更契约。

## 验收与回滚

切换后从真实公开入口回归 `/`、`/all`、`/daily`、`/wechat`、分页、搜索、有效与无效详情、`/api/v1/wechat`、`/admin`、管理 API、health、CSS/JS、HTTP 跳转与未知 Host。作为同一主机的最小代表面，`https://news.aiplanet.live/` 与 `https://news.aiplanet.live/api/v1/healthz` 必须分别返回现有成功响应，不能只用 `/wechat` 证明整条主机链路正常。缓存验证必须区分安全列表变体的 HIT/MISS 与搜索、详情、admin、health 的不缓存状态。

最终性能验收在用户的 MacBook 可见浏览器中执行，news 与 AIHOT 交替测试，每方至少 5 次真正冷连接。主判据是 `/wechat` 的 median FCP 与 TTFB 分别不超过 AIHOT 对照值的 110%；其他 `/wechat` 变体做功能与非回归验证，不为不存在的 AIHOT 对应页面制造代理指标。

最终验收不达标时先区分相对部署前 EdgeOne 基线是回归还是改善：本次代码 deployment 的功能、视觉、缓存或冷访问性能回归按 ADR-042 的普通 revert 路径恢复；性能已有改善但仍高于 AIHOT 的 110% 时保留收益并继续优化。只有独立证据指向 EdgeOne/DNS 本身时，才另行决策并取得授权，把 Cloudflare 记录恢复为 DNS-only A `111.229.134.9`。提交 DNS 回切动作不等于用户侧已恢复；权威 TTL 为 300 秒，递归缓存与边缘传播窗口内必须继续从消费者入口观测，直到公开解析和真实请求都证明源站直连已恢复。

## 作用域与未验证项

本决策不解决当前 `/admin` 仅检查 Cloudflare Access header 存在性的认证缺口，不证明其他 `aiplanet.live` 子域已接入 EdgeOne，也不证明完整 30 日流量一定低于套餐或不会产生超额后付费。15 日源站日志约 1.426GiB、13862 次请求，只说明当前样本远低于套餐量级，不能替代月度账单观测。

截至 2026-08-16，EdgeOne 站点、专属 CNAME、托管证书、DNS-only CNAME、全局 HTTPS 跳转与缓存规则均已配置；公共解析已链到 `news.aiplanet.live.eo.dnse2.com`，真实 HTTPS 响应带有 EdgeOne 缓存状态。内联 CSS 已随生产 commit `aeeccfdf69f81051ac6131d9e60c8d427d58edbf` 部署，缓存窗口后公开 `/wechat` 返回的内联内容与共享样式表逐字节一致；公开路由、HTTP 跳转、未知 Host、缓存与不缓存边界均通过回归，桌面和移动视口未见布局或横向溢出回归。用户 MacBook 的可见 Chrome 以新进程和新 profile 交替运行 7 轮冷访问，`/wechat` 与 AIHOT 的 median TTFB 分别为 203.3ms 与 1157.4ms，median FCP 分别为 332ms 与 1396ms；比值为 0.176 与 0.238，均低于 1.10 验收上限。每个样本的 UA 均不含 `HeadlessChrome`、页面状态为 `visible`，结束后的残留进程检查通过阳性对照并归零。独立决策评审在补充 DNS-only 交界面与公开解析验收后给出放行，报告保存在仓库外的 `/Users/lindong/.codex/reviews/ai-radar-edgeone-cname-20260813.md`。
