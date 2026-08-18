# ADR-057：X 推文媒体经新加坡出口代理取回

- 状态：已接受
- 日期：2026-08-18
- 相关：[ADR-054](054-stop-rendering-article-images-in-list-cards.md)

## 背景

[ADR-054](054-stop-rendering-article-images-in-list-cards.md) 移除了列表卡片里从 RSS 正文抓来的配图。上线后线上**一张图都不剩**，用户据此提问：参照站 aihot.virxact.com 上仍有文章显示图片，这是巧合还是设计不一致。

逐层取证的结论是「设计不一致」，且不一致点不在 ADR-054：

1. **参照站的规则不是"不显示图片"，而是"只显示 X 推文自带的媒体，不显示 RSS 正文图"。** ADR-054 砍掉的正是后者，方向一致；缺的是前者。
2. **ai-radar 从未取回过 X 媒体。** `x_api.py` 的请求参数里没有 `expansions=attachments.media_keys`，媒体在 X API v2 里只出现在 top-level `includes.media`，不带 expansion 就什么都不返回。所以即使前端愿意渲染，库里也没有可渲染的字段——生产库 4219 条 X 条目，带 `x_media` 的为 0。
3. **serve 主机（上海）到 `pbs.twimg.com` 的连接被上游阻断。** 该判定做过判别性取证：在上海主机上 tcpdump 抓包，对 loopback 的对照流量捕获到 45 个包（证明抓包仪器本身工作），对 twimg 的连接捕获到 0 个包——阻断发生在网卡之上游，不是本机防火墙或 DNS。新加坡主机 `tencent-webserver-sg` 同一 URL 返回 200 / 41ms。

于是即便取回了媒体 URL，浏览器直连 twimg 在国内也大量失败；而 serve 主机自己也到不了，无法代取。

## 决策

三段一起做，缺任一段都不产生用户可见效果：

1. **抓取层**：X timeline 请求加上 `expansions=attachments.media_keys` 与 `media.fields`，把解析出的媒体写进 `items.extra_json.x_media`，保持 `attachments.media_keys` 的原始顺序。photo 取 `url`，video / animated_gif 只有 `preview_image_url`（X 不在该字段返回视频流地址），一律取静态图。
2. **出口层**：twimg 的取图经新加坡主机上的 tinyproxy 转发，地址由 gitignored `.env` 的 `AI_RADAR_IMG_PROXY_URL` 提供。既有的 `/img` 同源代理承载这条链路。
3. **展示层**：CSR 与 SSR 两条渲染路径都渲染 `x_media`，且**只对 X 条目**渲染——RSS 正文图仍按 ADR-054 不显示。

### 为什么代理配置缺失时直接 404，而不是退回直连

`/img` 对 twimg 的请求在没有 `AI_RADAR_IMG_PROXY_URL` 时立即返回 404，不发出任何请求。退回直连的表现不是"取不到图"，而是**每张图都挂满整个连接超时**——上海到 twimg 的包被静默丢弃、不回 RST，所以 TCP 会一直重传到超时。一屏几十张图会把 serve 的连接池占满，故障从"图片缺失"升级为"整站变慢"。宁可快速失败。

同理，`/img` 的所有失败路径都返回 404 而非 5xx：前端的 `onerror` 会把 404 的图片元素隐藏掉，卡片布局保持完整；5xx 在部分浏览器上会留下破损图标。

### 为什么重定向要逐跳校验

`/img` 的主机允许名单同时是 SSRF 防线。若只校验最终 URL，一个指向内网的开放重定向仍然会被**实际请求出去**——校验发生在请求之后就已经晚了。实现改为手动跟随重定向（最多 3 跳），每一跳在发出之前重新过一次允许名单。

## 影响

- 上海 serve 主机新增一个 repo 外依赖：新加坡主机上的常驻 tinyproxy。该代理入站锁定上海主机单个 IP 且要求 BasicAuth，运维事实记在 [operations/services.md](../operations/services.md)。
- tinyproxy 当前版本存在未修补的 CVE-2026-31842。接受该风险：暴露面是单 IP + 认证 + 仅 CONNECT 443 + 目标域名过滤，且该主机不承载其他服务。记录在 services.md 以便上游发版后跟进。
- 存量数据不会自动获得媒体：普通抓取只取 checkpoint 之后的新帖。`./run.sh admin x-media backfill` 按 id 直接查 `/2/tweets` 回填，**不触碰 `sources.meta`**，因此与增量抓取的 checkpoint 互不干扰。
- X API 自 2026-02 起按返回的 Post 资源计费，回填成本 ≈ 候选数 × 单价，故该命令默认提供 `--dry-run` 先报候选数、`--limit` 支持分片跑。2026-08-18 已对生产库执行一次：候选 246、返回 245、写入 245，其中 135 条确实带媒体，1 条推文已删除或转为受保护（保留为候选，供以后重试）。这 246 条是 2026-06-12 切换到官方 X API 之后入库的部分；更早的 3976 条 nitter 时代条目没有存 `x_post_id`，且全部早已滚出展示窗口，不回填。
- 该命令必须能与每 15 分钟的 pipeline 并存。首次执行时两次都在**付费之后、写入之前**因 `database is locked` 失败——共享连接的 `busy_timeout=5000` 是按读者调的，而 pipeline 在数 GB 库上的写事务比它长。约 200 次查询因此白付。写入现改为最长 90 秒的退避重试，且只重试 `locked`：其他 `OperationalError`（schema 不符、磁盘满）必须立刻暴露，不能被拖进 90 秒的等待里再报出错误的原因。

## 备选方案

- **把图片转存到对象存储（COS + CDN）。** 最初的方案，理由是彻底摆脱对 twimg 可达性的依赖。用户随后告知已有一台新加坡主机可用，该方案的全部复杂度（存储桶、生命周期、回源、失效、下架链路）就只换来同一个效果，遂废弃。留档在 plan 目录。
- **让浏览器直连 twimg。** 零服务端成本，但国内访问成功率不可控，且失败率随用户网络环境变化——无法用任何服务端读数观测到，故障对运维不可见。
