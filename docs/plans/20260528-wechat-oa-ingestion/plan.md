# Plan — 微信公众号文章接入 ai-radar / aiplanet.live

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

---

## 输入

- **无 spec.md**：本 plan 自带 L1（产物 + 使用方式）、取舍偏好、L2（用户视角 verify）、L3（设计 + 内部 verify）。
- **上游对齐**：3 个核心决策 + 1 个合规边界，已通过 AskUserQuestion 与用户锁定，见 §「已锁定决策」。
- **依赖的外部资产**：
  - `wexin-read-mcp`（用户自建微信文章 scraper，路径见文末索引）—— 本 plan 要 **port 其 Playwright/BS4 正文抓取逻辑**进 ai-radar。
  - WeWe RSS（开源 `cooderl/wewe-rss`，基于微信读书）—— 作为发现层 sidecar 部署。其 feed 输出契约已 probe 源码确认（见 §「WeWe feed 输出事实」）。

---

## L1 — 最终产物 + 使用方式

**Before（当前可观察事实）**：
- ai-radar 是 RSS 原生聚合器：`fetcher/runner.py:50-63 fetch_source()` 对所有源无条件走 `fetch_feed()` → `parse_feed()` → `upsert_item()`，下游 prefilter→score→enrich→curate 全自动。
- `sources/loader.py:11` `VALID_KINDS = {"feed", "x"}`，无 "wechat"。微信公众号文章**完全无法进入系统**——微信无公开 RSS，`mp.weixin.qq.com` 反爬。
- aiplanet.live 上看不到任何微信公众号内容。

**Change（产物）**：
- **WeWe RSS** Docker sidecar（localhost:4000），订阅指定公众号，输出标准 RSS（**发现层**：标题 + 原文 URL + 日期；摘要模式下无正文，见事实表）。
- ai-radar 新增 **`kind="wechat"` 原生 fetcher**：复用 RSS 解析做发现，再用 **port 自 `wexin-read-mcp` 的 Playwright scraper** 对每条**新**文章抓全文写入 `content_text`（**读取层**）。
- ai-radar web 层对 wechat 源**抑制正文预览**（合规①）。
- `data/sources.toml` 新增 2 条 `kind="wechat"` 源（首批：**歸藏的 AI 工具箱**、**十字路口 Crossing**）。
- 微信文章经现有流水线后出现在 aiplanet.live。

**使用者 + 使用方式**：
- **网站访问者**：浏览 feed，看到这些公众号最新文章（中文标题 + 自生成中文摘要），**点击回链到 `mp.weixin.qq.com` 原文阅读**。
- **用户本人（站长）**：未来通过 WeWe 后台加订阅 + `sources.toml` 加一行扩展更多公众号。

**范围 / 边界**：
- ✅ 做：首批 2 个公众号端到端打通；可复用的 `kind="wechat"` fetcher；防封增量抓取；合规展示（抑制正文）。
- ❌ 不做：公众号搜索/发现 UI；全文转载展示；阅读量/点赞数据；WeWe 高可用集群；独立"公众号"频道（MVP 归入 news，见 Defaulted）。

---

## 已锁定决策（用户通过 AskUserQuestion 对齐 — 用户视角签收面，§L2 verify 逐条覆盖）

| # | 决策 | 选择 | 对实现的约束 |
|---|---|---|---|
| ① | **前端内容深度 / 合规** | **标题 + 自生成中文摘要 + 回链原文**；正文**仅内部**用，**不公开转载**；**未 enrich 的 item 抑制正文预览** | (a) **禁止**给 wechat 加 `kind="x"` 式全文展示分支；(b) item 的 `url` 指向 mp.weixin 原文；(c) **web 层对 wechat 源不输出 `content_preview`**（未 enrich 时卡片仅标题+回链；enrich 后展示 `summary_zh`） |
| ② | **微信正文来源** | **WeWe 只负责发现 + ai-radar 侧 scraper 抓正文** | WeWe 跑摘要模式（不设 `FEED_MODE=fulltext`）→ RSS 裸条目（无正文）；正文由 ai-radar 的 sync Playwright fetcher 抓取，port 复用 `wexin-read-mcp` 的 scraper 代码（**不走 MCP 协议**） |
| ③ | **桥接方案** | **自托管 WeWe RSS**（基于微信读书） | 发现层用 WeWe；不引入需"拥有公众号"的方案；不引入付费 SaaS |
| ④ | **未 enrich 文章的预览降级** | **抑制正文预览，只显标题+回链** | 见 ①(c)。这是 ① 的边界细化（用户单独拍板） |

---

## WeWe feed 输出事实（已 probe `cooderl/wewe-rss` 源码确认，非凭记忆）

来源：`apps/server/src/feeds/feeds.service.ts` `renderFeed()`（commit e751c64）。

- **item `link` 与 `guid` = `https://mp.weixin.qq.com/s/${id}`** —— 即 mp.weixin **原文 URL**。→ ai-radar 的 dedup key、Playwright 抓取目标、前端回链全部成立（原 R2 风险**已消除**）。
- **摘要模式（不设 `FEED_MODE=fulltext`）下 `content = ''`**（`if (enableFullText) content = tryGetContent(id)`）。→ RSS 裸条目只有 title/link/date/封面图，**无正文无摘要**。`parse_feed` 解析后 `content_text = clean_content('', fallback=title) = title`。→ **坐实 ai-radar 必须自己抓全文**，否则 content_text 只剩标题。
- **默认 cron `35 5,17 * * *`（Asia/Shanghai，一天 2 次）**，`updateDelayTime` 默认 60s，每 feed 间隔 30s。→ 这是 freshness 瓶颈，本 plan 调勤（见 Defaulted）。
- 自带 `title_include` / `title_exclude` 过滤；输出 `feed.rss2()/atom1()/json1()`。
- 实锤环境变量（`docker-compose.sqlite.yml`）：`DATABASE_TYPE=sqlite`、`DATABASE_URL=file:../data/wewe-rss.db`（默认可不设）、`AUTH_CODE`、`FEED_MODE`、`CRON_EXPRESSION`、`SERVER_ORIGIN_URL`、卷 `./data:/app/data`、端口 4000。

---

## L3 — 架构设计

### 两层分工

```
┌─ 发现层 (WeWe RSS, docker, localhost:4000, 摘要模式) ──────────┐
│  · 扫码登录微信读书 · 订阅两个公众号                              │
│  · /feeds/<id>.rss → item = 标题 + link/guid(mp.weixin原文) + 日期  │  ← content 为空(摘要模式)
│  · CRON 调为每 ~2h(平衡 freshness/防封)                          │
└───────────────────────────────┬──────────────────────────────┘
                                 │ sources.toml: [[source]] kind="wechat", url=WeWe feed URL
                                 ▼
┌─ ai-radar fetch 阶段 (kind=="wechat" 分支) ────────────────────┐
│  1. fetch_feed()+parse_feed() → items(标题/url=原文/日期; content_text=title) │ ← 复用 RSS 路径
│  2. 对每条「未入库」item.url: sync Playwright 抓 mp.weixin 全文        │ ← port wexin-read-mcp
│       → clean_content() → content_text(全文); content_html(原始)      │
│       抓失败 → 降级保留 RSS 裸条目(content_text=title) + 记 warning     │
│  3. upsert_item()  (保留 parse_feed 的 title/url/published_at)        │
└───────────────────────────────┬──────────────────────────────┘
                                 ▼
   prefilter(content_text[:4000]) → score([:5000]) → enrich([:5000]) → curate
                                 ▼
   前端: wechat 源 → 抑制 content_preview;  enrich后显示 summary_zh, 未enrich仅标题; 点击回链 url(mp.weixin)
```

### 关键设计点（含 file:line 锚点）

**D-1. `kind` 加白名单，无需 migration**
`sources/loader.py:11` 改 `VALID_KINDS = {"feed", "x", "wechat"}`。
DB 层 `migrations/002_*.sql:6` `kind TEXT NOT NULL DEFAULT 'feed'` **无 CHECK 约束**（已读源码确认）→ **不需要新 migration**。

**D-2. fetch 分支点**
`fetcher/runner.py:50-63` `fetch_source()` 改为（复用 fetch_feed/parse_feed，仅对 wechat 增强正文）：
```python
def fetch_source(conn, source):
    try:
        response = fetch_feed(source, conn)
        if response.not_modified:
            return SourceFetchSummary(source_id=source.slug)
        items = parse_feed(source, response.body)      # 发现：title/url(原文)/published_at; content_text=title
        if source.kind == "wechat":
            items = _enrich_wechat_bodies(conn, items)  # 读取：对新 URL 抓全文，覆盖 content_text/html
        inserted = 0
        for item in items:
            inserted += 1 if upsert_item(conn, item) else 0
        conn.commit()
        return SourceFetchSummary(source_id=source.slug, fetched=len(items), inserted=inserted)
    except Exception as exc:
        conn.rollback()
        return SourceFetchSummary(source_id=source.slug, error=f"{type(exc).__name__}: {exc}")
```
- **只覆盖 `content_text` / `content_html`，保留 parse_feed 的 `title` / `url` / `published_at`**（RSS 字段结构化更可靠；Playwright 仅负责正文）。
- `FetchedItem` 是 `@dataclass(frozen=True)`（`dedup.py:11-21`），用 `dataclasses.replace(...)` 产新对象。

**D-3. 增量抓取（防封核心，服务决策②防封动机）**
`_enrich_wechat_bodies` 对每条 item **先查 URL 是否已入库**（复用 `dedup.py` 的 URL 规范化 + `SELECT 1 FROM items WHERE source_id=? AND url=?`），已入库则跳过 Playwright → 最小化 mp.weixin 请求。

**D-4. Playwright scraper（port 自 wexin-read-mcp，改写为 sync）**
新建 `fetcher/wechat.py`。port 来源（scraper.py:24-167 / parser.py:11-61）：
- chromium headless，arg `--disable-blink-features=AutomationControlled`，UA 伪装（scraper.py:34）。
- `page.goto(url, wait_until="domcontentloaded", timeout=45000)` → `wait_for_selector("#js_content", timeout=20000)` → networkidle best-effort(5000ms 可超时忽略) → `page.content()`。
- BS4 选择器（parser.py:24-37）：title=`h1#activity-name`、author=`span#js_author_name`|`a#js_name`、publish_time=`em#publish_time`、正文=`div#js_content`（去 script/style）。
- 3 次指数退避重试（0.5→1→2s），最后一次重建 context。
- **改写为 `playwright.sync_api`**（ai-radar fetch 链路是 sync sqlite）。
- 正文 HTML 过 `content.py:clean_content(html, fallback=title)` 得 `content_text`（与现有源同一 trafilatura 路径）。
- **port 现成重试/降级逻辑，不为 MVP 重新简化**（re-simplify ported proven code 风险大于收益；见 journal 决策记录）。

**D-5. web 层抑制 wechat 正文预览（合规①(c)，决策④）**
现状（已读源码确认）：`web/routes/common.py:item_summary()` 对非 "x" 源默认输出 `content_preview = content_text[:320]`（common.py:257-270）；前端 `web/static/app.js:75` `excerpt()`：`summary_zh ?? content_preview ?? ""`；全文 `content_text` **仅 `kind="x"` 暴露**（common.py:455-456，app.js:74-75）。
**改动**：`item_summary()` 对 `source_kind == "wechat"` **不输出 `content_preview`**（置 None）。效果：enrich 后显示 `summary_zh`；未 enrich 显示空（仅标题）+ 回链。**不动** summary_zh 路径，**不**给 wechat 加 x 式全文分支。
> 注意：这打破了"前端零改动"，是为严格满足决策④的一处 API 层小改（单一条件）。

**D-6. 频道归类（Defaulted：归 news，不建独立频道）**
`web/routes/timeline.py:55-60`：`channel="news"` = `kind != 'x'`，wechat 自动归入 news/all。timeline.py 留好扩展点，将来要独立"公众号"频道再加 `elif channel == "wechat"` 分支。

---

## 实施步骤（build sequence）

> Phase A（ai-radar 代码）不依赖 WeWe，可先做；Phase B 人工 gate 产出 feed URL 后接 Phase C。任务编号对应 state.md。

### Phase A — ai-radar 代码（纯代码，可独立测）

- **TASK-001** `loader.py:11` 加 `"wechat"` 到 `VALID_KINDS`。
  - 内部 verify：`pytest tests/test_sources_schema.py`（新增：load `kind="wechat"` 源成功；非法 kind 仍报错）。
- **TASK-002** `pyproject.toml` 将 `playwright` 从 dev 移到 `[project] dependencies`；`uv sync`；`uv run playwright install chromium`。
  - 内部 verify：`uv run python -c "from playwright.sync_api import sync_playwright; print('ok')"`。
- **TASK-003** 新建 `fetcher/wechat.py`：`scrape_article(url) -> dict{title,author,publish_time,content_html,content_text,success}`（port D-4，sync）。
  - 内部 verify：① 离线单元——两篇种子文章 HTML fixture（`tests/fixtures/wechat/*.html`）喂 parser，断言提取正文含特征串（见 §L2 表）；② 集成（`@pytest.mark.integration`，联网）——对两种子 URL 实抓，断言 `success` 且 content_text 含特征串。
- **TASK-004** `fetcher/runner.py`：实现 `_enrich_wechat_bodies(conn, items)`（D-2/D-3：增量 + 降级）+ `fetch_source` 加 `kind=="wechat"` 分支。
  - 内部 verify：单元 mock `scrape_article`——① 已入库 URL 跳过(不调 scraper) ② 成功时 content_text 全文覆盖、title/url/published_at 保留 ③ 失败时降级保留 RSS 裸条目；`uv run mypy ...` + `uv run ruff check`。
- **TASK-005** `web/routes/common.py:item_summary()`：对 `source_kind == "wechat"` 抑制 `content_preview`（D-5，决策④）。
  - 内部 verify：单元——构造 wechat item（含全文 content_text），断言 `item_summary` 返回的 `content_preview` 为 None（有 summary_zh 时 summary_zh 仍在）；断言 wechat 未进入 content_text 全文暴露分支。

### Phase B — WeWe RSS 发现层（含人工 gate）

- **TASK-006** 部署 WeWe RSS（docker, sqlite, localhost:4000）。`deploy/wewe-rss/` 放 `docker-compose.sqlite.yml`（基于实锤模板，见下）+ `.env`（`AUTH_CODE` 强随机，**不提交真值**），并把 `CRON_EXPRESSION` 调为每 ~2h。
  - 实锤模板（来自官方 `docker-compose.sqlite.yml`，仅改 AUTH_CODE 与 CRON）：
    ```yaml
    services:
      app:
        image: cooderl/wewe-rss-sqlite:latest
        ports: ["4000:4000"]
        environment:
          - DATABASE_TYPE=sqlite
          - AUTH_CODE=${WEWE_AUTH_CODE}        # 强随机，.env 注入
          - CRON_EXPRESSION=7 */2 * * *        # 每2h(默认是 35 5,17 一天2次，太慢)
          # - FEED_MODE=fulltext               # 保持注释=摘要模式(决策②)
        volumes: ["./data:/app/data"]
    ```
  - 内部 verify（agent 自动）：`curl -sf http://localhost:4000/ -o /dev/null && echo up`；`docker ps` 健康。
- **TASK-007**〔**人工 gate**〕扫码登录微信读书 + 订阅 2 个公众号。
  - agent 兜底（gate 前完成）：TASK-006 部署就绪 + 健康检查通过；输出操作路径 `http://localhost:4000` → 账号管理→扫码 → 公众号源→提交分享链接。
  - **用户动作（自包含 checklist，逐条间隔，避免 24h 封控）**：
    1. 微信扫码登录微信读书。
    2. 提交 `https://mp.weixin.qq.com/s/KWtnToEa7K-13k002K-nRw`（歸藏的 AI 工具箱）。
    3. **等其文章在 `/feeds/all.rss` 出现（或至少间隔 ~10 分钟）**，再提交 `https://mp.weixin.qq.com/s/kORnjtyhEntmcQH4j8H4nw`（十字路口 Crossing）。
  - 验收（agent 自动，gate 后）：`curl -s http://localhost:4000/feeds/all.rss` 含两号文章；记录各号 `feedId` 与 `/feeds/<feedId>.rss` URL；确认 item `<link>` 为 `mp.weixin.qq.com/s/...`（已 probe 预期，复核）。

### Phase C — 接入 + 端到端 + 文档

- **TASK-008** `data/sources.toml` 加 2 条 `kind="wechat"` 源（slug=`wx_guizang`/`wx_crossing`，url=各自 WeWe feed URL，tier=`T2`，homepage_url 指向公众号），`./run.sh` reload。
  - 内部 verify：reload 后 DB `sources` 有 2 条 kind=wechat。
- **TASK-009** 跑完整 pipeline 打通端到端。
  - verify：见 §L2（交付 gate）。
- **TASK-010** 文档更新（按 Docs Organization Protocol）。
  - 必含（planner 已定位的现有首看面，不下放）：
    - `docs/architecture.md` 的 **External Dependencies 表**（:284 附近）加 **Playwright + Chromium**（最重运行时依赖）。
    - `docs/architecture.md` 的 **Key Files for Common Tasks 表**（:298 附近）：「添加新信源」行注明 **wechat 源需先在 WeWe 加订阅**（非仅 `sources.toml` 一行）。
    - `README.md` §配置→信源（现写"RSS/X 信源"）补 wechat 第三类。
  - 新增（按协议定文件）：「如何添加一个微信公众号源」操作指南；`deploy/wewe-rss/` runbook（部署、微信读书凭证过期重扫、防封注意）。
  - 内部 verify：上述文件存在且含关键步骤；交叉引用不悬空。

---

## L2 — 用户视角 verify

> **两类分开**（P3）：**消费者 gate** = 使用者眼里算交付的可观察证据（交付 gate，必须贴证据）；**内部机制检查** = 支撑性过程兜底（全绿不替代消费者 gate）。
> 人机：🤖=agent 独立执行；🧑=需人工。每个 🧑 前有 🤖 兜底（P12）。

### 消费者 gate（交付证据）

| # | 验证（对应承诺） | 步骤 | 判据 | 人机 |
|---|---|---|---|---|
| **C1** | **站点上能看到**（L1 产物） | agent-browser 打开站点 news/all 截图，定位两号文章卡片 | 截图可见两号文章卡片；enrich 的显示中文标题+摘要、**未 enrich 的卡片正文区为空（仅标题+回链，体现决策④）**；点击跳转 mp.weixin 原文 | 🤖 截图 → 🧑 可选肉眼终审 |
| **C2** | **不公开正文 + 回链 + 抑制预览**（决策①④, preservation） | `curl -s '<timeline API>?channel=news'` 取 wechat item JSON | wechat item **无**全文 `content_text` 字段；**`content_preview` 为 null/缺失**（对所有 wechat item，非仅未 enrich）；`url` 匹配 `^https://mp\.weixin\.qq\.com/s/`；**对有 enrich 记录的 item** `summary_zh` 非空（若两种子均未 enrich 则此子句 N/A——决策④的确定性主 gate 是 TASK-005 单元测试断言 `content_preview=None`，不依赖运行时 enrich 状态） | 🤖 |
| **C3** | **未给 wechat 开 x 式全文分支**（决策①, preservation） | `grep -n 'source_kind"\] == "x"' src/airadar/web/routes/common.py`（repo-root `web/static/app.js:74-75` 同核对） | 全文暴露分支条件**只** `== "x"`，**不含** `"wechat"`、也非 `!= "feed"` 之类宽条件 | 🤖 |

### 内部机制检查（支撑，非交付证据）

| # | 验证 | 步骤 | 判据 | 人机 |
|---|---|---|---|---|
| I1 | 发现层可用 + 裸条目（决策②③） | `curl -s http://localhost:4000/feeds/all.rss` | 含两号文章；item `<link>` 为 mp.weixin/s/；摘要模式下 item **无正文**（`<content>` 缺失或为空，仅 title/link/date/cover） | 🤖（TASK-007 gate 后） |
| I2a | **全文抓取/解析正确（离线确定性）** | `pytest tests/.../test_wechat.py`（种子 HTML fixture） | parser 从两篇种子 HTML 提取正文含特征串：歸藏→`guizang-social-card-skill`\|`28 个版式骨架`；十字路口→`TCC`\|`dump_ui` | 🤖 |
| I2b | **全文抓取（实时，区分降级）** | 对两种子 URL 实跑 fetch | scrape **成功→ `content_text` 含特征串**；或**降级→保留裸条目 + 记 warning**（二者可区分，不混为"系统坏了"） | 🤖 |
| I3 | 种子文章入库 | DB 查 items 标题/url | 出现「开源个 Skill…」「实测腾讯…Marvis…」，`url` 形如 mp.weixin/s/ | 🤖 |
| I4 | 走完流水线 | 查 `item_evaluations` / `curated_items` | 两源有 prefilter(is_ai_related)/score/enrich(summary_zh,title_zh) 记录；AI 相关文章进入某 curated_run | 🤖 |
| I5 | freshness（P7 阈值） | 说明上限 + 观察：新文章发布后应在 ≤ 1 个 WeWe cron 周期(~2h) + 1 个 pipeline 周期(15min) ≈ **2.5h 内**出现 | 实测一次新文章出现延迟，或在 runbook 记录该上限；**防封优先，不为压低延迟加大 mp.weixin 请求频率** | 🤖 观察 |

**交付 gate**：C1–C3 全绿且贴证据（截图 / curl 输出 / grep 输出）方可声称完成。内部机制检查（I*）全绿不替代。

---

## Defaulted Decisions（planner 自拍，reviewer / 用户可审）

| 决策 | 选择 | 理由 | 反转成本 |
|---|---|---|---|
| 正文抓取位置 | fetch 阶段内（非单独 stage） | prefilter/score/enrich 只读 content_text，正文须 prefilter 前就位；fetch 内抓最简单、下游零改 | 低 |
| Playwright API | `sync_playwright` | fetch 链路是 sync sqlite，sync 更自然、无事件循环嵌套 | 低 |
| 频道归类 | 归 news/all，不建独立频道 | MVP 最小；timeline.py 留扩展点 | 低 |
| WeWe `FEED_MODE` | 默认摘要模式 | 正文由 ai-radar 抓；WeWe 请求最少=最防封 | 低 |
| **WeWe `CRON_EXPRESSION`** | **`7 */2 * * *`（每 ~2h）** | 默认 `35 5,17`(一天2次)对"最新文章"太慢；每2h 平衡 freshness(延迟≤~2.5h)与防封（微信读书请求量仍很低） | 低 |
| **freshness vs 防封排序** | **防封优先**；可见新文章延迟 ≤ ~2.5h 视为可接受 | 决策②已选防封；给 freshness 一个非任意的 bar（落地 I5） | 低 |
| 抓失败降级 | 保留 RSS 裸条目(content_text=title) + warning，不中断 | 单篇失败不拖垮整源（no-impact 自治） | 低 |
| source tier | 首批 `T2` | 新源未知质量，靠 score 平权竞争 | 低 |

---

## Risks（acceptance + trigger response，P11）

| # | 风险 | acceptance | trigger response |
|---|---|---|---|
| ~~R1~~ | ~~WeWe sqlite env/tag 不明~~ | **已解决** | probe 官方 `docker-compose.sqlite.yml` 实锤（见事实表）；TASK-006 直接用 |
| ~~R2~~ | ~~WeWe item link 是否原文~~ | **已解决** | probe `feeds.service.ts` 确认 `link`/`guid` = `mp.weixin.qq.com/s/${id}` 原文 |
| R3 | 某公众号微信读书未收录 → WeWe 订不到 | 两种子号均知名 AI 号，概率低 | 实测某号订不到 → **stop-and-ask**：是否为该号单独找发现源（无干净 fallback，需用户定） |
| R4 | 微信读书登录凭证过期（需重扫码） | 第三方机制无法消除 | runbook 记重扫流程；WeWe 自带过期提醒；运维项，记 state.md Open Issue 跟踪 |
| R5 | 微信反爬升级致 Playwright 抓取失败率升高 | 反爬动态对抗 | 单篇失败走降级（裸条目兜底，I2b 可区分）；整源持续失败 → state.md 开 Issue，评估引入 wexin-read-mcp 同款 context 重建/代理 |
| R6 | 加订阅频率过高被封 24h | 微信风控 | TASK-007 明确逐条、间隔 ~10min；首批仅 2 号，风险低 |
| R7 | Playwright/chromium 引入部署（二进制体积、CI） | 决策②必然代价（用户已接受） | runbook 记 `playwright install chromium` 为部署前置；docs/architecture.md External Dependencies 表登记（TASK-010） |

---

## 引用索引

| 引用 | 路径 / 值 | 用途 |
|---|---|---|
| wexin-read-mcp scraper | `~/.claude/mcp-servers/wexin-read-mcp/src/scraper.py:24-167` | port 来源：Playwright 流程、超时、重试、UA |
| wexin-read-mcp parser | `~/.claude/mcp-servers/wexin-read-mcp/src/parser.py:11-61` | port 来源：BS4 选择器 |
| WeWe feed 生成 | `cooderl/wewe-rss` `apps/server/src/feeds/feeds.service.ts` `renderFeed()` | feed 输出契约：link/guid=原文、摘要模式 content 空、cron |
| WeWe 部署模板 | `cooderl/wewe-rss` `docker-compose.sqlite.yml` | 实锤 env / 端口 / 卷 |
| ai-radar kind 白名单 | `src/airadar/sources/loader.py:11` | 加 "wechat" |
| ai-radar fetch 分支点 | `src/airadar/fetcher/runner.py:50-63` | 加 kind=="wechat" 分支 |
| FetchedItem 定义 | `src/airadar/fetcher/dedup.py:11-21` | frozen dataclass 字段 |
| 内容清洗 | `src/airadar/fetcher/content.py:clean_content()` | HTML→content_text |
| 前端 item 渲染 | `src/airadar/web/routes/common.py:item_summary()`(257-270, 406-468) + repo-root `web/static/app.js:74-75` | 抑制 wechat 预览(D-5)；确认全文仅 kind=x 暴露。**注意 `web/` 是独立顶层目录，非 `src/airadar/web/`** |
| 频道过滤 | `src/airadar/web/routes/timeline.py:55-60` | wechat 归 news |
| 流水线编排 | `pipeline.sh` + `src/airadar/cli.py` | fetch→prefilter→score→enrich→curate |
| WeWe RSS（官方） | `cooderl/wewe-rss`（docker `cooderl/wewe-rss-sqlite:latest`, 端口 4000） | 发现层 sidecar；feed URL `/feeds/<id>.rss`、`/feeds/all.rss` |
| 种子①（歸藏的 AI 工具箱） | `https://mp.weixin.qq.com/s/KWtnToEa7K-13k002K-nRw` | 种子；特征串 `guizang-social-card-skill` / `28 个版式骨架` |
| 种子②（十字路口 Crossing） | `https://mp.weixin.qq.com/s/kORnjtyhEntmcQH4j8H4nw` | 种子；特征串 `TCC` / `dump_ui` |
