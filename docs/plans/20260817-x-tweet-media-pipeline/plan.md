> **Archive status**: 已归档并上线。**正文首段自陈「未定稿 — 不得直接进 execute-plan」是写作当时的状态**，其后本 plan 已实施并落地：commit `42db1fb`（经新加坡出口代理取 X 推文媒体）与 `16f65d0`（缩略图 shrink-wrap + lightbox）。本 plan 不是长任务模式，无 `state.md` / `journal.md`；同目录 `plan-superseded-cos-design.md`（作废的自存 COS 那版）与 `review-findings.md`（round-1/2 评审）未随归档复制。
> 当前契约见 [ADR-057](../../adr/057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md)、[ADR-058](../../adr/058-shrink-wrap-x-media-thumbnails-and-add-a-lightbox.md)，以及 [architecture.md](../../architecture.md) 的 `/img` 与 External Dependencies 两处。以下为原 plan 正文，未修改。

# Plan：X 推文媒体经新加坡正向代理展示

> **未定稿 — 不得直接进 `/custom:execute-plan`**（尚未过评审）。
>
> 本 plan 取代同目录 `plan-superseded-cos-design.md`（自存到 COS 那版）。作废原因是一个新实测事实，不是评审意见：`tencent-webserver-sg`（新加坡）**直连 `pbs.twimg.com` 即 HTTP 200 / 41ms / 254KB**，上海 origin 经它做正向代理即可，无需把图转存到自己的公开桶。
>
> 随之消失的整块工作：COS 桶与凭据、SDK 依赖、上传链路、内容哈希去重、持久重试队列、operator 下架命令 + CDN purge + denylist、backfill 上传、以及 round-2 的两个 evidence blocker。**A2 定档的理由（我们成为再发布者）也随之消失**——代理不是再发布，与本站现在对微信图的做法同构。
>
> round-1/2 评审针对旧设计，findings 见 `review-findings.md`。其中**仍然适用**的已直接吸收进本 plan（consumer path、HP-7 子承诺、asset 发布单元、文档同步面、重验矩阵）。

## 输入

- **取证与硬约束**：`docs/issues/general.md` 的「X 推文媒体在列表里缺失」条目（主机可达性矩阵）。
- 相关既有决策：ADR-054（列表不渲染 RSS 正文图）、ADR-012（移动/桌面单套 DOM）、ADR-039 / ADR-042（EdgeOne 缓存与生产部署隔离）、ADR-055（CSR/SSR 必须同改）。
- 本 plan 承载 L1 / L2 / L3 三层（无 spec.md）。

## rigor

| 项 | 值 |
|---|---|
| 默认 | `(A1, V1)` |
| per-phase override | **Phase 0（SG 正向代理上线）→ `A2`** |
| label | `max` |
| 轴 R | 主体改动是本仓代码，可逆本地 → A1。**但 Phase 0 要在公网暴露一个正向代理**，那是安全边界 → 该 phase A2 |
| 轴 G | 会影响真实用户（破图/不显示），不涉资金、数据完整性零容忍 → V1 |

**风险搬家了，没有消失**：旧设计的 A2 来自"不可撤销的对外发布"；本设计的 A2 来自"运营一个面向公网的代理"。公网开放代理会被扫描并当作跳板滥用，且滥用流量记在你的服务器名下。

按 rigor-tiers 的 proportionality invariant：**只有 Phase 0 的 authority hunk 走独立对抗审查**；其余 phase 的行为验证保持 V1，不得整体抬到 V2（round-2 的 R2-F7 正是指出旧 plan 在这里过度加码）。

## 并发隔离声明

必须在独立 git worktree 落地，session 要进到那棵树。依据是实测：2026-08-17 本仓主 checkout 同时有 3 个活跃写入者，期间本地 `main` 前进 5 个 commit，并发生过 ADR 编号撞车。

- 可变运行时状态：测试用 `AI_RADAR_DB` 指向临时副本。
- 服务/端口：本地验证起隔离实例，端口避开 8000 / 8010 / 8791。
- **新增 ADR 前先 `ls docs/adr/`** 取当前最大编号 +1。

## L1：最终产物 + 使用方式

**产物**：`/` 与 `/all` 的列表卡片上，X 推文自带的图片出现；RSS 文章的正文抓取图仍不显示（ADR-054 不变）。

**使用者**：公开读者。推文的图常常就是内容本身（benchmark 图表、发布截图），只读文字会丢信息。

**范围**（与参照站 aihot 对齐，实测得出）：所有 X 推文的媒体，不限精选；只展示静帧（photo 用 `url`，video/animated_gif 用 `preview_image_url`）；不动 RSS / web / wechat 三类来源。

参照站实测（curl SSR HTML，2026-08-17）：首页 8 个 `<img>` = 4 头像 + 4 推文媒体；`/all` 26 个 = 16 + 10；**RSS 正文图 0 张**。`/all` 上 10 张媒体全部落在不带「精选」标记的卡片段内 → 非精选推文同样展示。

## 实测事实（不要重新验证）

| 主机 | 角色 | `pbs.twimg.com` | 备注 |
|---|---|---|---|
| macmini | 抓取 / LLM / DB 同步源 | 经本地 tunnel 代理 200 | pipeline 由 `*/15 * * * * /Users/lindong/research/ai-radar/pipeline.sh` 从**主 checkout** 跑 |
| 腾讯上海 `111.229.134.9` | serve 公网 | **000 / 超时**，进程无代理变量 | 国内 CDN（qpic.cn）可达 |
| `tencent-webserver-sg` | 目前**几乎空闲**（只有 sshd + 本地 DNS） | **200 / 41ms / 254KB，直连无需代理** | 出口 SG；2GB 内存、42GB 可用盘；ufw inactive |

- 上海 → SG **内网 `10.3.0.5` 不可达**（不同 VPC：上海内网 `10.0.0.15`，SG `10.3.0.5`）；**公网可达，RTT ~92ms**，`:22` 可达。
- **上海主机的出口 IP == 入口 IP == `111.229.134.9`**（实测：机内 `curl ipinfo.io/ip` 返回该值，而本地网卡是私网 `10.0.0.15`，即弹性 IP 做 1:1 NAT）。**防火墙规则按 `111.229.134.9` 写是对的**——若它在 NAT 网关后出口是别的地址，规则会静默挡掉一切，故此处已先验证。
- 上海直连 twimg 的失效形态是**挂起 8 秒后 `000`**（实测），不是快速失败。这是 Phase 0「缺配置时不得回退直连」那条的依据。
- 现有 `/img?url=` 同源代理（`src/airadar/web/routes/media.py`）已是成熟组件：host allowlist 兼作 SSRF 防护、失败返回 404 让前端 `onerror` 干净隐藏、响应带 `public, max-age=604800, immutable`。它用 `httpx.get`，**未关闭 `trust_env`**。
- 图片经 EdgeOne 边缘缓存，上海→SG 这一跳**每张图只发生一次**。

## 取舍偏好（用户已拍板）

| 维度 | 选择 |
|---|---|
| 图片通道 | **经 SG 正向代理的请求时代理**（不自存） |
| 展示范围 | 与 aihot 对齐 = 全部 X 推文媒体 |
| 代理暴露面 | **锁死到上海 IP + 认证**（双重防护） |

---

## Phase 0：SG 正向代理 —— **override `A2`** ✅ 已实施（2026-08-18），仅剩安全组一步

`(A2,V1)`。**这是本 plan 唯一在公网暴露服务的一步。**

### 已完成（不要重做）

`tencent-webserver-sg` 上已部署 tinyproxy 1.11.1，`systemctl is-enabled` = enabled：

| 配置项 | 值 |
|---|---|
| Port | `39147`（非默认口） |
| Listen | `0.0.0.0` |
| Allow | `111.229.134.9`（上海出口 IP，已验证 = 入口 IP）+ `127.0.0.1`（供本机自检/健康探测） |
| BasicAuth | 用户 `airadar`，密码在 SG 的 `/etc/tinyproxy/.credpw`（600 root:root），**已同步写入上海 `/home/ubuntu/ai-radar/.env` 的 `AI_RADAR_IMG_PROXY_URL`（600）** |
| ConnectPort | `443`（杜绝任意端口跳板） |
| Filter | `/etc/tinyproxy/filter`，`FilterDefaultDeny Yes`，`FilterType ere`；**只放行 `^pbs\.twimg\.com$`**（初版含 `^[a-z0-9-]+\.twimg\.com$` 单层通配，经评审指出过宽后收紧；实测 `video.twimg.com` 现被拒、`pbs.twimg.com` 仍 200。若将来 redirect 证明确需其它域，逐个加） |
| Restart | `Restart=on-failure` / `RestartSec=3s`，经 systemd drop-in 配置。**readback 抓到一处假陈述**：本 plan 初版写了该策略但实际从未配过，包自带 unit 是 `Restart=no`；`kill -9` 后服务停在 `failed` 不自愈。补配后重测：杀 PID 979054 → 自动起为 979086、状态 `active`，**这次是实测通过的** |
| ufw | `22/tcp` 对全网；`39147/tcp` 仅 `111.229.134.9`；default deny incoming。**按「先 allow 22、最后 enable」的顺序执行，已验证新建 SSH 连接正常** |

### ⚠️ 未修复的已知漏洞（A2 必须记录）

`tinyproxy` 装的是 `1.11.1-3ubuntu0.1`（noble-security），`Installed == Candidate`、**无待装更新**。changelog 显示已 backport `CVE-2022-40468` 与 `CVE-2023-49606`（后者即上游 1.11.2 修的 UAF——上游版本号 1.11.1 有误导性，不能据此判未修）。

但 **`CVE-2026-31842` 未修复**（2026-04-07 发布，CNA 评 CVSS 8.7 HIGH，影响 tinyproxy ≤ 1.11.3，Ubuntu changelog 里没有它）：`is_chunked_transfer()` 用 `strcmp` 大小写敏感地比对 `Transfer-Encoding`，攻击者发 `Transfer-Encoding: Chunked` 可造成请求解析失同步 → 后端 worker 耗尽型 DoS，或绕过基于 body 的检查。

**本部署的实际暴露面**（不是"不要紧"，是有具体边界）：触发它需要能连到代理端口，而该端口经 ufw + 云安全组只对 `111.229.134.9` 开放（已实测第三方被拒），且需 BasicAuth；我们自己只发无 body 的 GET，也不做 body 检查。**用户已于 2026-08-18 裁决：接受该残余风险**，理由是触发它需连到只对我们自己一台主机开放且需认证的端口，而其危害（后端连接挂住）的"后端"就是 twimg 本身；换代理软件的运维成本高于该残余风险。**跟进义务**：发行版出修复时升级；`docs/operations/services.md` 里要记这一笔，使接手者不必重新调查。

**V-0 行为验收（在 SG 本机经 loopback 跑，绕过安全组）全部通过**：正确凭据取 twimg = `200`；错误凭据 = 断连；`example.com` = 被拒；`video.twimg.com` = `400`（twimg 自己返回，证明子域过滤没写过窄）。**第三方主机（macmini）连 `43.153.216.193:39147` 被拒**，证明 IP 锁生效。

### ⛔ 剩余阻塞：腾讯安全组（需控制台，agent 无权限）

上海 → `43.153.216.193:39147` 在 **TCP 层不通**。定位到 SG 侧云安全组的证据链（每条都能区分真假，不是"看起来像"）：

| 检查 | 读数 | 排除了什么 |
|---|---|---|
| 同一 tcpdump 过滤器抓 SG 本机 loopback 到 39147 的流量 | **45 个包**（含 SYN/SYN-ACK） | 排除"抓包工具没工作"——这是仪器的阳性对照 |
| 上海发起 3 次连接期间在 SG 抓包 | **0 个包** | 包**没到达 SG 网卡** → 排除 SG 的 ufw（丢包也会先看到 SYN 到达）与代理进程（端口没监听会回 SYN+RST） |
| 上海 → `1.1.1.1:853`（非标准端口） | 可达 | 排除"上海出站被限制非标准端口" |
| 上海主机 ufw | `inactive` | 排除上海侧主机防火墙 |

四条合起来只剩一个位置：**SG 侧的云安全组入站规则**。本机与 SG 均无 `tccli` 或云凭据，agent 改不了。

**需要在腾讯云控制台为 `tencent-webserver-sg` 的安全组添加一条入站规则**：来源 `111.229.134.9/32`，协议 TCP，端口 `39147`，允许。

加完后从上海重跑 V-0.1 即可（`.env` 已就位）：
```bash
ssh ubuntu@111.229.134.9 'set -a; . /home/ubuntu/ai-radar/.env; set +a; \
  curl -s -o /dev/null -w "%{http_code}\n" --max-time 20 -x "$AI_RADAR_IMG_PROXY_URL" \
  https://pbs.twimg.com/media/HP7L9v_WMAA9Rfz.jpg'
```
期望 `200`。

### 凭据轮换记录

配置过程中有两次密码在 shell 报错里被回显（`ssh host 'bash -s' <<EOF` 的 stdin 被脚本占用、管道进来的密码被当成脚本首行执行）。**那两个密码已作废**，现行密码是第三次生成的，用「脚本先落远端、再单独用 stdin 喂密码」的方式传递，未经过任何输出。

在 `tencent-webserver-sg` 上部署一个轻量正向代理（tinyproxy 量级即可，2GB 内存绰绰有余），并满足：

1. **绑定与端口**：非默认端口；不要 8080/3128 这类会被批量扫的。
2. **双重防护**（用户决策）：
   - 腾讯安全组 + ufw **只放行 `111.229.134.9`** 访问该端口；
   - **且**代理本身开基本认证。
   - 两者各自覆盖对方的失效面：只锁 IP → 上海换 IP 时静默全变破图且排查方向不直观；只加认证 → 端口仍暴露在公网供爆破。
3. **上游限制**：代理配置里限制可访问的目标域为 `pbs.twimg.com`（及必要的 twimg 子域）——即便代理凭据泄露，它也不能被当作通用跳板。这是把 SSRF/滥用面收窄到本任务实际需要的范围。
4. **不得先 `ufw enable` 再加规则**：该机 ufw 当前 inactive 且**只有 22 端口对外**，顺序错了会把自己锁在门外。先 `allow 22`，再 `allow from 111.229.134.9 to any port <PORT>`，最后 enable。
5. **常驻**：systemd unit，开机自启，`Restart=on-failure`。

**凭据交接**：代理的用户名/密码由用户自己写进上海那台的 gitignored `.env`（变量名 `AI_RADAR_IMG_PROXY_URL`，形如 `http://user:pass@<sg-host>:<port>`），**不要贴进对话**。

**未配置时 fail closed**：`AI_RADAR_IMG_PROXY_URL` 缺失 → twimg 图片一律不代理（`/img` 对该 host 返回 404，前端 `onerror` 隐藏），其余行为不变。**绝不回退成直连**——直连必然超时，只会把用户请求挂住 8 秒。

**A2 要求的独立对抗审查**：本 phase 落地后按 review-gate 高档走独立 reviewer，命题是「这个代理会不会被当作跳板」「凭据泄露的爆炸半径」「上海 IP 变更时的失效形态」。**只审这一 phase 的 authority hunk，不把其余 phase 一并抬档。**

**验收**（V-0）：
- 从上海：`curl -sS -o /dev/null -w '%{http_code}\n' -x "$AI_RADAR_IMG_PROXY_URL" https://pbs.twimg.com/media/<真实key>.jpg` → `200`
- 从**任意第三方主机**（如 macmini）用同一凭据访问该端口 → **连接被拒**（证明 IP 锁生效）
- 从上海用**错误凭据** → `407`（证明认证生效）
- 经代理访问一个非 twimg 目标（如 `https://example.com`）→ **被拒**（证明上游限制生效）

---

## Phase 1：抓取层——取回媒体元数据

`(A1,V1)`。改 `src/airadar/fetcher/x_api.py`。

**现状**（已核实）：`X_TWEET_FIELDS = "author_id,created_at,lang,note_tweet,public_metrics,referenced_tweets"`；全文件 0 处 `expansions` / `media.fields` / `media_keys`；`content_html=None`。

```python
X_TWEET_FIELDS = "attachments,author_id,created_at,lang,note_tweet,public_metrics,referenced_tweets"
X_EXPANSIONS = "attachments.media_keys"
X_MEDIA_FIELDS = "media_key,type,url,preview_image_url,width,height,alt_text"
# params 里加： "expansions": X_EXPANSIONS, "media.fields": X_MEDIA_FIELDS,
```

**响应形状**（外部规范，inline）：媒体在**顶层 `includes.media`**，tweet 经 `data[].attachments.media_keys` 引用：

```json
{
  "data": [{"id": "...", "attachments": {"media_keys": ["3_1489397927281840131"]}}],
  "includes": {"media": [
    {"media_key": "3_1489397927281840131", "type": "photo",
     "url": "https://pbs.twimg.com/media/FKtnhhDWUAMtyBi.jpg", "width": 1125, "height": 750},
    {"media_key": "13_1489018359819771906", "type": "video", "width": 1920, "height": 1080,
     "preview_image_url": "https://pbs.twimg.com/media/FKoOePDWYAA1ZZB.jpg"}
  ]}
}
```

取图规则：`photo` → `url`；`video` / `animated_gif` → `preview_image_url`；两者都无 → 跳过。**按 `attachments.media_keys` 的原顺序保存**（round-2 R2-F2：顺序是上游语义的一部分，渲染也按此顺序）。分页时 `includes` 每页各自，不得跨页复用 map。

**没有持久重试队列**——本设计不需要：URL 存下来即可，取不到是请求时的事，`/img` 返回 404、前端 `onerror` 隐藏，下次请求自然重试。

**内部 verify**：
- fixture 含 photo + video + animated_gif + 无媒体推文：映射与类型分派正确；缺 `includes` 不抛错；**保存顺序与 `media_keys` 一致**。
- 第二页 `includes` 不污染第一页 map。
- `uv run ruff check src tests`、`uv run mypy src`。

**X 计费**（2026-08-18 查，provenance 见文末）：X 于 2026-02 转 pay-per-use，按返回资源计费（Post read `$0.005`/条，2M/月上限）。公开价目表**没有 media 这一类**，但来源是第三方汇总、非官方文档。V-1 用 Developer Console 实际扣费对照加 expansions 前后；**若 Console 无法区分 expansions 成本，结论必须是「未核实」，不得推断「不额外计费」**（round-2 R2-F6）。

---

## Phase 2：代理层——让 twimg 走 SG

`(A1,V1)`。改 `src/airadar/presentation/media.py` 与 `src/airadar/web/routes/media.py`。

1. `PROXY_IMAGE_HOST_SUFFIXES` 增加 `pbs.twimg.com`（当前只有 `qpic.cn`）。
2. `/img` 端点按**目标 host 选择出口**：twimg → 走 `AI_RADAR_IMG_PROXY_URL`；qpic.cn → 保持直连。
   **不要用进程级 `HTTPS_PROXY` 环境变量**——那会把 serve 进程的**全部**出站 HTTPS 都绕到 SG，包括本来直连更快的微信 CDN，属于在没要求的维度上顺带改动。用 `httpx` 的 per-request/per-mount proxy。
3. 跟随 redirect 后**重新校验 allowlist**——只校验初始 URL 会被开放重定向绕过。
4. 保持既有语义不变：失败一律 404（让前端 `onerror` 干净隐藏）、`_MAX_IMAGE_BYTES` 上限、`public, max-age=604800, immutable`。

**内部 verify**：
- 单元测试：twimg URL 走代理、qpic.cn 不走代理（断言传给 httpx 的 proxy 参数）。
- 单元测试：allowlist 拒非白名单 host、拒非 http(s)、**拒 redirect 到非白名单 host**。
- 单元测试：`AI_RADAR_IMG_PROXY_URL` 缺失时 twimg 返回 404 且**不发起直连**。
- 单元测试：上游超时 / 非图片 Content-Type / 超限 → 404，不抛错。

---

## Phase 3：存储层

`(A1,V1)`。载体已确定（已核实）：`FetchedItem.extra`（`src/airadar/fetcher/dedup.py:21`）→ `items.extra_json` 列（已存在）。

**`content_html` 对 X 恒为 `None`**，不为 X 造假 HTML。必须有测试守住 X 媒体**不借 `_media_assets_from_html` 绕过 ADR-054 的 RSS 判据**。

**consumer path 必须一并改**（round-2 R2-F4，我核实过这是真缺口）：`src/airadar/presentation/media.py` 的 `_visible_media_assets` 与 `presentation/summary.py` —— X 分支从 `extra_json` 读取媒体并**先于**现有 RSS 的 rank/单图截断逻辑返回；RSS 继续走 HTML 解析与既有 rank 规则。否则 X 多图会被 `assets[:1]` 或 rank 分档截掉。

**对外形状不变**：`FeedItem.media_assets`（`src/airadar/web/schemas.py`）保持 `[{"type": "image", "url": "..."}]`；URL 是 `/img?url=<encoded twimg>` 形态（经 `proxy_image_url()`），**不是** twimg 裸链。

**内部 verify**：
- X item 经完整链路后 `media_assets` 形状正确、每个 URL 都以 `/img?url=` 开头、**零 `pbs.twimg.com` 裸链**。
- 多图 X 卡片不受 `assets[:1]` 或 rank 分档限制。
- X item 的 `content_html` 仍为 `None`，`_media_assets_from_html` 对它返回空。
- 回归：`uv run pytest tests/ --ignore=tests/playwright` 全绿（基线 1590 passed / 4 skipped）。

---

## Phase 4：渲染层——只对 X 放行

`(A1,V1)`。**四处**都要改（漏任何一处都出不了图）：

| 面 | 文件 |
|---|---|
| SSR view model | `src/airadar/web/app.py`（约 352 行，**当前显式排除 `media_assets`**——ADR-054 删的）：`source_kind == "x"` 时投影，其余仍排除 |
| SSR 模板 | `web/templates/_prepaint_list.html` |
| CSR | `web/static/app.js` 的 `itemCard()` |
| 样式 | `web/static/style.css`，新增 `.x-media*`（**不复用已删的 `.article-media*`**，ADR-054 的契约测试断言它已不存在） |

判据是 `source_kind === "x"`，不是"有没有 media_assets"。CSR 与 SSR 必须同形（ADR-055 的教训）。按 `media_keys` 原顺序渲染。

**布局（用户决策：可读优先 + 密度底线）**：不做破坏性裁切；同时硬性要求 1440×900 首屏完整可见卡片 **≥ 2**、单卡高度 **≤ 900px**（ADR-054 实测基线）。冲突时**缩小媒体**，不裁切。

**发布单元**（round-2 R2-F7）：改完跑 `uv run python scripts/bump_frontend_assets.py`，并把它改写的**全部 HTML 与 `web/asset-pins.json` 纳入同一个 commit**——漏挑任何一个都破 ADR-039 的缓存契约。

**内部 verify**：
- 静态契约：CSR 与 SSR 媒体标记逐字相同；判据是 `source_kind === "x"`；ADR-054 既有断言仍过。
- **变异对照**：每条新断言都把实现改回错误形态确认变红。

---

## Phase 5：整合与上线（顺序不可交换）

`(A1,V1)`。

1. 本地 feature commit（独立 worktree，通过全部内部 verify + review gate）。
2. **整合回本地 `main`** —— **需显式许可**。不可省：生产 pipeline 从**主 checkout** 跑（已核实 crontab），改动不进主 checkout 的 `main`，Mac 抓取端不会启用新链路。整合后确认主 checkout HEAD 含该 commit。
3. **抓取先行**：主 checkout 跑一轮真实抓取，让媒体 URL 进 DB。**必须在 code deploy 之前**，否则线上代码会渲染一批还没有媒体的条目。
4. **Phase 0 的代理必须已上线并通过 V-0** —— 否则部署后线上图片全 404。
5. **code deploy** —— **需单独的 push 许可**（整合许可不能替代）。按 ADR-042 以 freshly-fetched `tencent/main` 为父节点复放成单个 commit `D`，逐条列出 `tencent/main..D` 确认只含本任务改动，fast-forward push。部署前跑 `./run.sh admin edgeone check`（exit 2 记为未核实）。
6. **等一轮 DB sync**（cron 每 5h：01:41 / 06:41 / 11:41 / 16:41 / 21:41 本地）把媒体元数据送到公网副本，确认该轮 `terminal state committed`，再做公网验收。

---

## 验证

**内部 / 集成**（agent 自主）：

| # | 内容 | 判据 |
|---|---|---|
| V-0 | SG 代理四项 | 见 Phase 0（可达 200 / 第三方被拒 / 错误凭据 407 / 非 twimg 目标被拒） |
| V-1 | 抓取层取回媒体 + 计费对照 | 临时库：`cp data/radar.db /tmp/x-media-test.db`；单源 TOML：从 `data/sources.toml` 抽一个 `kind="x"` 块另存 `/tmp/one-x.toml`；跑 `AI_RADAR_DB=/tmp/x-media-test.db uv run python -m airadar.cli fetch --sources /tmp/one-x.toml`；查 `SELECT json_extract(extra_json,'$.media') FROM items WHERE source_id='<该源 id>' AND json_extract(extra_json,'$.media') IS NOT NULL LIMIT 5`。计费对照见 Phase 1（Console 分不出即记「未核实」） |
| V-2 | 经 `/img` 能取到 twimg 图 | 本地起隔离实例 + 配好 `AI_RADAR_IMG_PROXY_URL`，`curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' 'http://127.0.0.1:<port>/img?url=<encoded twimg url>'` → `200 image/*` |
| V-3 | 覆盖对账 | 一批 X item 的 `media_assets` 数量 == 其 `attachments.media_keys` 中**可交付**（有静帧 URL）的条数。**不是"有图就行"** |

**用户视角验收（L2）**——真实公网：

| # | 条件 | 怎么验 | 人机 |
|---|---|---|---|
| L2-1 | X 卡片出图、RSS 不出图，**数量对得上** | 对 `/` 与 `/all` 抓 SSR HTML 统计 `<img>` 分类，与同批条目 API 的 `media_assets` 做 expected-vs-actual 集合对账 | agent |
| L2-2 | SSR 首绘 / hydration 后 / 追加一页三态一致 | 禁用 JS 取首绘计数 → 启用后取 hydration 计数 → 滚动追加一页再计数，三者对同批条目的媒体集合一致 | agent |
| L2-3 | 桌面密度底线 | 1440×900 `/`：首屏完整卡片 **≥2**、前 20 张卡片高度 **≤900px**。任一不满足 FAIL | agent |
| L2-4 | 窄屏不塌 | 390×844 同两项，且媒体块不把信源名挤成省略号（`scrollWidth > clientWidth`） | agent |
| L2-5 | **无破图** | `/all` 中**滚动遍历全部 lazy 图片之后**，对每个 `<img>` 断言 `naturalWidth > 0`（不滚动会漏掉未加载的） | agent |
| L2-6 | HP-7 子承诺逐条 | 点击图片开新标签页；无媒体 fixture 不生成空媒体容器；图片带 lazy-loading 属性；注入一张必然失败的图后标题/来源/正文仍可见 | agent |
| L2-7 | 代理缺失时干净降级 | 临时清空 `AI_RADAR_IMG_PROXY_URL`：twimg 图 404、页面无破图、其余功能不变、**不出现 8 秒挂起** | agent |
| L2-8 | 观感成对审阅 | 生成可直接打开的成对证据页（本站 vs aihot，桌面+窄屏），四项 rubric（裁切 / 可读性 / 卡片密度 / 响应式）逐项 PASS/FAIL ballot 与回复格式；交付前做可达性 preflight；任一 FAIL 回 Phase 4 | **人工**（前置 L2-1~L2-7 全过） |

**重验矩阵**（round-2 R2-F8）：

| 改了什么 | 什么失效 |
|---|---|
| 抓取 / 存储 | V-1、V-3、L2-1 的集合对账 |
| 代理层 / SG 配置 | V-0、V-2、L2-7 |
| renderer / CSS | L2-1 ~ L2-6、L2-8（**不必**重跑 X 计费对照） |
| 仅测试 / 文档 / 证据页措辞 | 都不失效 |
| 生产 code 或 data 快照变化 | **不得复用旧公网截图或人工 ballot** |

## UX 契约影响

**有影响。** `docs/contracts/ux-contract.md` 的 HP-7「媒体资产」当前与实现相反（ADR-054 后已判 drift）。改写为——**保留全部既有子承诺**：

- 列表卡片渲染 X 推文自带的媒体（静帧）；RSS 文章的正文抓取图不渲染
- 媒体经本站同源代理提供，不热链第三方
- 点击图片在新标签页打开大图
- 无媒体的卡片正常展示，不出现空白区域
- 图片 lazy loading
- 单张图片加载失败不影响卡片文字内容的展示

逐条映射：第 1 条 → L2-1；第 2 条 → V-3 + L2-1（零裸链断言）；第 3–6 条 → L2-6；不出现空白 → L2-3/L2-4。**给 execute-plan 的指令**：apply 这份改写进 HP-7，按上述映射验证，不自行新增本段未记录的契约改动。

## 文档与配置的同步面

`.env.example`（新增 `AI_RADAR_IMG_PROXY_URL`，**只有变量名与说明、不含值**）、`docs/operations/services.md`（SG 代理这个**新增常驻服务**：部署位置、systemd unit、防护策略、失效症状与排查入口；README 链到该节）、`CHANGELOG.md`、`docs/contracts/ux-contract.md`（HP-7）、新增 ADR（该架构：为什么经 SG 代理而非自存；`ls docs/adr/` 取号并更新 `docs/adr/README.md`）。

**新增失败面的可观测性自检**（round-2 R2-F9，`~/.claude/references/service-operations-protocol.md` §6.1）：SG 代理是新增的常驻服务，它挂掉的症状是**图片全部静默变 404**——页面不报错、日志不一定有异常。要么扩展既有告警覆盖它，要么在 git-tracked issue 里记下负责人与后续告警工作；**不能只留日志**。

## 运行时成本审计

| 成本要素 | so-what | 结论 |
|---|---|---|
| 上海 → SG 的代理跳（每张图一次） | 少了它图取不到；EdgeOne 边缘缓存 + 7 天 immutable 让它每张图只发生一次 | **保留** |
| SG 服务器常驻一个代理进程 | 该机目前几乎空闲（只有 sshd），2GB 内存足够 | **保留**，边际成本近零 |
| X API expansions 可能的额外计费 | 若 media 单独计费会显著抬高月支出 | **未证实**；V-1 实测对照，超预期回到用户 |

## 外部事实的 provenance

| 事实 | 来源 | 强度 |
|---|---|---|
| X API v2 媒体 expansion 语法与 `includes.media` 形状 | X 官方开发者博客（`dev.to/xdevs` 的 X 官方账号发布）+ 多个独立实现示例，字段与响应结构一致 | 高；且 V-1 会用真实响应验证 |
| X 计费：2026-02 起 pay-per-use、Post read `$0.005`/条、2M/月上限 | 第三方定价汇总（tweetstream.io、tweetapi.com、xpoz.ai，三家一致） | **中——非官方文档**；权威是 Developer Console，V-1 必须实测 |
| SG 主机直连 twimg 200/41ms、上海不可达、上海→SG 公网可达 RTT 92ms | 本 session 实测 | 高 |

## Defaulted Decisions（reviewer 请审）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 不为 X 造假 `content_html`，走 `items.extra_json` | 造 HTML 再解析回来是两次无谓转换；载体已存在 |
| D2 | video / animated_gif 只取 `preview_image_url` 静帧 | 与参照站一致；播放要引入播放器与带宽，超出范围 |
| D3 | 新样式用 `.x-media*` | ADR-054 的契约测试断言 `.article-media*` 已不存在 |
| D4 | 抓取先于 code deploy | 否则线上代码渲染一批还没有媒体的条目 |
| D5 | 用 per-request proxy 而非进程级 `HTTPS_PROXY` | 后者会把 serve 进程全部出站流量绕到 SG，包括本该直连的微信 CDN |
| D6 | 代理层限制上游只能访问 twimg | 凭据泄露时把爆炸半径收窄到本任务实际需要的范围 |

## Bounded TODO

| # | 细化哪一处 | 内容 |
|---|---|---|
| T1 | Phase 0 | 具体代理软件与端口；systemd unit 的确切形态 |
| T2 | Phase 4 | 媒体块尺寸与宽高比——由 L2-3 / L2-4 实测收敛 |
| T3 | Phase 2 | `httpx` per-request proxy 的确切写法（mounts vs per-call） |

## Risks

| 风险 | 缓解 |
|---|---|
| 公网代理被扫描滥用 | 双重防护（IP 锁 + 认证）+ 上游域限制；Phase 0 的 A2 对抗审查专审此项 |
| 上海主机 IP 变更 → 图片静默全 404 | 认证层仍在（不会变成开放代理）；`services.md` 记明该症状与排查入口；可观测性自检要求告警覆盖 |
| SG 主机或代理进程挂掉 | 同上——症状是图片静默 404，必须有告警而非只留日志 |
| X API 加 expansions 后计费上升 | V-1 用 Console 实测；分不出则记「未核实」，不推断 |
| 规模估算基于单日 27 条 X 条目 | V-1 用真实数据复核；偏离一个数量级回到用户 |

## 交付物的到达位置与判据

本仓自持 + 一次生产部署 + 一台新服务器上的常驻代理。「到达」判据是 L2-1 / L2-5：**从真实公网**取到的 `/` 与 `/all` 里，X 媒体图数量与 API 的 expected 集合对账一致，且滚动遍历后每张图 `naturalWidth > 0`。SG 主机与其防火墙规则归用户所有。
