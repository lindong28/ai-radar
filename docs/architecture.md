# Architecture

> Mutable snapshot. 结构变更时更新本文件。

## Overview

AI Radar 是一个 AI 信息流聚合站点。从 RSS 信源抓取内容，经 LLM 多阶段处理（筛选、评分、翻译富化、精选），以时间线和日报形式通过 Web 展示。

技术栈：Python 3.12+ / FastAPI / SQLite (WAL) / Jinja2 页面模板 / 多 LLM Provider（DeepSeek、GLM、OpenAI）。包管理使用 uv。

## Modules

```
src/airadar/
├── cli.py              # CLI 入口，argparse 子命令分发
├── db.py               # 数据库连接、迁移执行
├── ruleset.py          # Ruleset 版本管理（日期.rev 格式）
├── topics.py           # 受控标签词表 + 确定性标签规则
├── migrations/         # SQL 迁移脚本（001-009），幂等执行
│
├── sources/            # 信源管理
│   ├── loader.py       #   解析 data/sources.toml -> SourceConfig
│   └── sync.py         #   同步信源配置到数据库
│
├── fetcher/            # 内容抓取
│   ├── runner.py       #   fetch_all 主流程
│   ├── rss.py          #   RSS/Atom 解析（feedparser）
│   ├── wechat.py       #   微信公众号原文抓取（Playwright + BeautifulSoup）
│   ├── content.py      #   HTML -> 纯文本（trafilatura）
│   ├── http_client.py  #   HTTP 请求 + 条件请求（ETag/Last-Modified）
│   ├── dedup.py        #   content_hash 去重 + upsert
│   └── urls.py         #   URL 规范化
│
├── provider/           # LLM Provider 抽象层
│   ├── base.py         #   Protocol 定义：PrefilterProvider / ScoringProvider / EnrichProvider
│   ├── deepseek_v32.py #   DeepSeek V3.2（prefilter）
│   ├── deepseek_v4_pro.py  # DeepSeek V4 Pro（scoring / enrich / eval judge）
│   ├── deepseek_v4_flash.py # DeepSeek V4 Flash（enrich 备选）
│   ├── deepseek_chat.py    # 通用 DeepSeek / ARK chat JSON 封装
│   ├── codex_gpt_mini.py   # Codex GPT Mini（scoring 备选）
│   ├── glm.py          #   GLM（prefilter 备选）
│   └── heuristics.py   #   纯规则后备（无 LLM）
│
├── prefilter/          # 阶段 1：AI 相关性筛选
│   ├── runner.py       #   run_prefilter 主流程
│   └── prompts.py      #   Prompt 模板
│
├── scorer/             # 阶段 2：五维评分
│   ├── runner.py       #   run_scoring 主流程
│   ├── prompts.py      #   Prompt 模板
│   └── schema.py       #   ScoringNumeric Pydantic schema
│
├── enrich/             # 阶段 3：中文翻译富化
│   ├── runner.py       #   run_enrich 主流程（支持并发 workers）
│   ├── prompts.py      #   Prompt 模板
│   └── schema.py       #   EnrichOutput Pydantic schema
│
├── curator/            # 阶段 4：精选
│   ├── select.py       #   curate 主流程：加权评分 + 新鲜度配额 + 去重 + 排名校准
│   ├── score.py        #   weighted_score 计算 + 信源层级乘数
│   ├── dedup.py        #   候选去重（content_hash / URL）
│   └── weights.py      #   五维权重定义（Weights dataclass）
│
├── interpret/          # 阶段 5：微信公众号文章解读
│   └── runner.py       #   调 ai-assistant summarize-article，写 wechat_interpretations + KB
│
├── eval/               # 质量评估（与 AIHOT 对比）
│   ├── judge.py        #   run_eval 主流程 + LLM judge + 报告生成
│   ├── compare_renderer.py # HTML 对比页渲染
│   └── distribution.py #   分数分布统计
│
├── web/                # Web 服务
│   ├── app.py          #   FastAPI app 工厂 + uvicorn 启动
│   ├── cors.py         #   CORS 配置（aiplanet.live + localhost）
│   ├── envelope.py     #   统一 API 响应包装 {success, data, error}
│   └── routes/
│       ├── common.py   #   共享查询逻辑（item_summary、去重、分类过滤、FTS）
│       ├── timeline.py #   GET /api/v1/timeline — 全量时间线
│       ├── curated.py  #   GET /api/v1/curated — 精选内容
│       ├── items.py    #   GET /api/v1/items/{id} — 单条详情
│       ├── sources.py  #   GET /api/v1/sources — 信源列表
│       ├── wechat.py   #   GET /api/v1/wechat — 微信文章解读列表 + markdown sanitize helper
│       └── health.py   #   GET /api/v1/healthz — 健康检查
│
└── admin/              # 管理命令（预留）

web/static/             # 前端静态文件（根目录 web/，非 src 内）
├── index.html          #   精选首页旧静态文件（deprecated，保留作回滚）
├── all.html            #   全量时间线旧静态文件（deprecated，保留作回滚）
├── daily.html          #   日报页
├── about.html          #   关于页
├── item.html           #   单条详情页
├── app.js              #   前端 JS
├── style.css           #   样式
└── daily-overrides-20260514c.css  #  日报页样式覆盖

web/templates/          # Jinja2 SSR 页面模板
├── index.html          #   精选首页 SSR + preload
├── all.html            #   全量时间线 SSR + preload
├── wechat.html         #   微信文章解读列表 SSR + preload
└── wechat_detail.html  #   微信文章解读详情页（sanitized markdown HTML）
```

## Layers

系统分三层，依赖方向严格向下：

```
┌──────────────────────────────────┐
│  CLI / Web（入口层）              │   cli.py, web/app.py
├──────────────────────────────────┤
│  Pipeline（业务逻辑层）           │   fetcher/ prefilter/ scorer/ enrich/ curator/ eval/
├──────────────────────────────────┤
│  Infrastructure（基础设施层）      │   db.py, provider/, sources/, topics.py, ruleset.py
└──────────────────────────────────┘
```

**边界规则**：

- **入口层** 负责参数解析和请求路由，不包含业务逻辑
- **Pipeline 层** 各阶段互相独立，通过数据库表（`items` + `item_evaluations`）传递数据，不直接调用彼此
- **Infrastructure 层** 提供数据库连接、LLM 调用、信源配置等基础能力
- Pipeline 阶段通过 `provider/base.py` 中的 Protocol 与具体 LLM 实现解耦

## Data Flow

完整的数据处理流水线：

```
data/sources.toml
       │
       ▼
   ┌────────┐    RSS/Atom     ┌─────────┐   content_hash   ┌───────────────┐
   │ sources │ ──────────────> │ fetcher │ ────────────────> │ items 表      │
   │ loader  │    feedparser   │ runner  │   去重 upsert     │ (raw content) │
   └────────┘                 └─────────┘                   └───────┬───────┘
                                                                    │
       ┌────────────────────────────────────────────────────────────┘
       │
       ▼
   ┌───────────┐  LLM 判断     ┌──────────────────────┐
   │ prefilter │ ─────────────> │ item_evaluations 表  │
   │ runner    │  is_ai_related │ stage='prefilter'     │
   └───────────┘  + confidence  └──────────┬───────────┘
                                           │ 仅 is_ai_related=true
       ┌───────────────────────────────────┘
       │
       ▼
   ┌─────────┐  LLM 五维评分    ┌──────────────────────┐
   │ scorer  │ ────────────────> │ item_evaluations 表  │
   │ runner  │  relevance/      │ stage='scoring'       │
   └─────────┘  density/...     └──────────┬───────────┘
                                           │
       ┌───────────────────────────────────┘
       │
       ▼
   ┌─────────┐  LLM 翻译富化    ┌──────────────────────┐
   │ enrich  │ ────────────────> │ item_evaluations 表  │
   │ runner  │  title_zh/       │ stage='enrich'        │
   └─────────┘  summary_zh/...  └──────────┬───────────┘
                                           │
       ┌───────────────────────────────────┘
       │
       ▼
   ┌──────────┐  加权评分 +      ┌─────────────────────┐
   │ curator  │  新鲜度配额 ───> │ curation_runs 表    │
   │ select   │  去重 + 排名     │ curated_items 表    │
   └──────────┘                  └─────────────────────┘
                                           │
       ┌───────────────────────────────────┘
       │ enabled wechat items only
       ▼
   ┌───────────┐  ai-assistant summarize   ┌──────────────────────────┐
   │ interpret │ ─────────────────────────> │ wechat_interpretations 表 │
   │ runner    │  + KB writeback if saved  │ summary_md/tags/decision │
   └───────────┘                            └──────────────────────────┘
```

`kind="wechat"` 源的 URL 指向托管 Mp2RSS 合集 feed。fetch 阶段通过 RSS 发现新文章链接，再只对尚未入库的 `mp.weixin.qq.com/s/...` 原文用 Playwright 抓全文；抓取失败时降级保留 RSS 裸条目，后续 Web 层仍只公开中文摘要与原文回链。`interpret` 阶段只读取启用的 wechat 源 item，调用 ai-assistant `summarize-article` 逻辑生成结构化总结；`save_decision=1` 的条目展示在 `/wechat` 并回写 ai-assistant KB，`save_decision=0` 只在本库留处理记录。

每个阶段只处理尚未完成对应评估的新条目。`pipeline.sh` 按顺序调度全部阶段，`interpret` 位于最后且 preflight 缺 ai-assistant 依赖时跳过，不阻断前置抓取/精选。

## Database

SQLite 单文件数据库，路径 `data/radar.db`（可通过 `AI_RADAR_DB` 环境变量覆盖）。开启 WAL 模式、`busy_timeout=5000`。

### 核心表

| 表 | 用途 | 主键 |
|---|---|---|
| `sources` | 信源配置（slug、名称、URL、层级、类型） | `id` (TEXT, slug) |
| `items` | 抓取的内容条目 | `id` (TEXT, SHA1 前 16 位) |
| `item_evaluations` | LLM 评估结果，stage 区分阶段 | `id` (INTEGER, 自增) |
| `curation_runs` | 精选运行记录 | `id` (TEXT, 时间戳+随机) |
| `curated_items` | 精选条目（关联 run） | `(run_id, item_id)` |
| `wechat_interpretations` | 微信文章解读结果（summary_md、tags、save_decision、KB 同步状态） | `item_id` |
| `items_fts` | FTS5 搜索虚拟表（trigram 分词），列为 `item_id/title/content_text/source_name/author/title_zh` | -- |
| `feedback` | 用户反馈（预留） | `id` (INTEGER, 自增) |
| `airadar_migrations` | 迁移记录 | `id` (TEXT) |

### 关键设计

- **去重策略**：`items` 表通过 `(source_id, content_hash)` 唯一约束去重。`content_hash` 是内容文本的 SHA1 前 16 位。同 URL 不同内容视为更新
- **多阶段评估**：`item_evaluations` 通过 `stage` 字段区分 prefilter / scoring / enrich，共用同一张表。每条记录保存完整的 input/output/numeric JSON
- **Ruleset 版本**：格式 `YYYY-MM-DD.rN`，用于跟踪 prompt 和规则的变更。同一条目可以有不同 ruleset 版本的评估记录
- **信源层级**：T1（官方一手源，乘数 1.25）/ T1.5（高质量聚合，乘数 1.0）/ T2（社区源，乘数 0.75）
- **搜索索引**：`003_add_fts5_search.sql` 是当前 `items_fts` schema 的权威定义，每次 `migrate()` 都会重建 FTS 表和触发器。索引覆盖标题、正文、来源名、作者和 enrich 生成的中文标题；scoring `reasoning` 不再进入搜索索引。`sources.name` 更新和成功的 enrich 写入会通过 trigger 同步到 FTS。
- **短查询兜底**：timeline 和 curated 共用 `search_id_subquery()`。3 字及以上使用 `items_fts MATCH`；1-2 字只在标题、来源名、作者和中文标题上用 escaped LIKE，避免对正文做短词全表扫。
- **微信解读闸门**：`wechat_interpretations.save_decision=1` 是 `/wechat` 展示和 ai-assistant KB 写入的唯一闸门。详情页从本库 `summary_md` 渲染，不在请求时读取 ai-assistant 文件。

### 索引

| 索引 | 覆盖列 |
|---|---|
| `idx_items_source_published` | `(source_id, published_at DESC)` |
| `idx_items_source_url_norm` | `(source_id, lower(rtrim(url, '/')))` |
| `idx_items_published_fetched_id` | `(published_at DESC, fetched_at DESC, id DESC)` |
| `idx_evaluations_item_stage_ruleset` | `(item_id, stage, ruleset_version)` |
| `idx_curated_items_run_rank` | `(run_id, rank)` |
| `idx_wechat_interp_decision` | `(save_decision, processed_at DESC)` |
| `idx_wechat_interp_slug` | `(slug)` unique |

## Web Layer

FastAPI 应用，通过 `create_app()` 工厂函数创建。前端是 HTML + JS：`/` 和 `/all` 使用 Jinja2 SSR 预载首屏数据，后续交互继续通过 API 获取数据；`/daily`、`/about` 和 `/item.html` 仍由静态文件提供。

### API 端点

所有 API 以 `/api/v1` 为前缀，返回统一信封 `{success, data, error}`。

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/v1/timeline` | GET | 全量时间线，支持页码分页（返回真实总数 COUNT）、channel 过滤（x/news/firstParty）、category 过滤、混合 FTS/LIKE 搜索 |
| `/api/v1/curated` | GET | 精选内容。无 `run_id`/`date` 时进入归档模式（跨 run 去重的累积归档，页码分页 + 真实总数）；带 `run_id`/`date` 时为单轮/单日 digest（`/daily` 复用）。支持 category、混合 FTS/LIKE 搜索 |
| `/api/v1/items/{id}` | GET | 单条详情 + 评估历史 |
| `/api/v1/wechat` | GET | 微信文章解读列表，仅返回 `save_decision=1`，字段含 slug/title/abstract/tags/author/avatar/published_at/url |
| `/api/v1/sources` | GET | 信源列表 |
| `/api/v1/healthz` | GET | 健康检查（条目数、运行数、ruleset 版本） |

### 页面路由

| URL | 渲染方式 | 说明 |
|---|---|---|
| `/` | `web/templates/index.html` | 精选累积归档首页（跨 run 去重，页码分页，第 1 页为最新精选），Jinja2 SSR，内联 `/api/v1/curated` 归档形状的 preload JSON |
| `/all` | `web/templates/all.html` | 全量时间线，Jinja2 SSR，内联 `/api/v1/timeline` 形状的 preload JSON |
| `/wechat` | `web/templates/wechat.html` | 微信文章解读列表，Jinja2 SSR，内联 `/api/v1/wechat` 形状的 preload JSON |
| `/wechat/{slug}` | `web/templates/wechat_detail.html` | 微信文章解读详情页，`summary_md` 经 markdown-it-py 渲染后用 nh3 sanitize |
| `/daily` | `web/static/daily.html` | 日报（支持 `?date=` 或 `/daily/YYYY-MM-DD`） |
| `/about` | `web/static/about.html` | 关于页 |
| `/item.html` | `web/static/item.html` | 单条详情页（StaticFiles 隐式提供） |

### SSR preload contract

新增首屏数据页面时，模板需要在页面 module script 前放置 JSON preload slot：

```html
<link rel="modulepreload" href="/app.js?v=...">
<section id="list" class="timeline" aria-live="polite">
  {% include "_prepaint_list.html" %}
</section>
<script id="__PRELOAD__" type="application/json">
  {{ preload | tojson | safe }}
</script>
```

`_prepaint_list.html` 服务端直出前 12 条首屏 `.item-row`，让浏览器解析到 feed 区域时立即有内容；`web/static/app.js` 的页面初始化函数随后调用 `readPreload()` 做权威渲染和交互绑定。preload 存在且 `items` 为数组时不显示 `正在加载` spinner；无 preload 时保留原 CSR fetch fallback，保证 `web/static/{index,all}.html` 仍可作为回滚文件使用。

SSR 模板中的 Google Fonts 样式必须用非阻塞 `rel="preload" as="style"` 加载，避免远端字体 CSS 抵消 preload 收益。`/`、`/all` 的动态路由定义必须在 `app.mount("/", StaticFiles(...))` 之前。

### 分类系统

前端的 category 过滤在后端 SQL 层实现。分类基于 enrich 阶段产生的标签，映射关系定义在 `web/routes/common.py` 的 `CATEGORY_TAGS`：

| Category | 包含的标签 |
|---|---|
| `ai-models` | 模型发布 |
| `ai-products` | 产品更新、MCP/工具 |
| `industry` | 行业动态、安全/对齐、现象/趋势 |
| `paper` | 论文/研究 |
| `tip` | 教程/实践、部署/工程 |

### 真实计数与分页

`/`（精选归档）、`/all`（timeline）、`/wechat` 都用数字页码分页（首末页固定、当前页相邻页、… 省略），共用 `web/static/app.js` 的一套分页组件。这要求 API 返回真实总数以定位末页。三者共享同一"真实计数 + 数据版本缓存"骨架：独立 count 函数、过滤签名 + 数据版本作 LRU key（上限 64，带锁）、search 路径不缓存直接算、越界页 clamp 到真实末页、FastAPI lifespan 启动 prewarm 默认视图计数。决策与性能数据见 ADR-005（timeline）与 ADR-006（精选归档）。

**Timeline（`/api/v1/timeline`）**：返回真实总数 COUNT（非 ADR-004 时期的前向估算）。计数与 rows 查询是**两套独立 SQL**：rows 用 EXISTS-per-row 子句判定每条 item 的最新 prefilter/scoring 评估，计数用 `latest_prefilter` / `latest_scoring` CTE + JOIN 的集合公式（`_count_timeline_items_with_prefilter()`），避免 per-row 子查询随数据量退化——改 timeline 过滤逻辑时两处需同步。计数缓存数据版本为 `_timeline_data_version()`（最新 curation_run id/ruleset、items 行数与 max rowid、max eval id）。CTE 计数依赖 migration `010` 的 `item_evaluations(stage,error,item_id,id DESC)` 索引。

**精选归档（`/api/v1/curated` 无 `run_id`/`date`）**：跨 run 去重的累积归档——`_latest_curated_join()` 用 `c.run_id = (SELECT MAX(run_id) FROM curated_items WHERE item_id=i.id)` 相关子查询，每个 item 只保留其最近一次被精选的元数据。真实计数走 `_count_archive_items()` + `_cached_archive_total()`，数据版本为 `_curated_data_version()`（latest run_id、curated_items 计数/max rowid、items 计数/max rowid、max eval id）。归档每页用 `_compute_archive_page()` **现算 item_summary**（不依赖 `summary_json`——预计算只覆盖约 30%），enrichment 一次 `LEFT JOIN` 取出，关联讨论按页 `_batch_related_discussions()` 批量正/反查（`items_fts` 反查）。去重子查询依赖 migration `011` 的 `idx_curated_items_item_run(item_id, run_id)`。带 `run_id`/`date` 时走原单轮/单日 digest 路径（`/daily` 复用），不进归档逻辑。

## Key Abstractions

### Provider Protocol

`provider/base.py` 定义三个 Protocol，Pipeline 阶段通过 Protocol 调用 LLM，不依赖具体实现：

- `PrefilterProvider.is_ai_related(item) -> PrefilterResult` -- 返回 `{is_ai_related, confidence}`
- `ScoringProvider.score_5d(item) -> ScoringResult` -- 返回五维分数 + reasoning + topics
- `EnrichProvider.enrich(item) -> EnrichResult` -- 返回 `{title_zh, summary_zh, why_recommend, tags}`

Provider 通过环境变量选择：`AI_RADAR_PREFILTER` / `AI_RADAR_SCORER` / `AI_RADAR_ENRICHER`。

### ProviderItem

Pipeline 各阶段使用的统一数据传输对象。从 `items` + `sources` 表 JOIN 构建，包含 id、title、url、source_id、tier、author、published_at、content_text。

### Weighted Score

精选评分公式：`sum(dimension_score * weight) * tier_multiplier`。

默认权重：relevance=0.10, density=0.40, recency=0.30, authority=0.10, engineering=0.10。

精选阈值默认 6.5，展示分数经过排名线性校准（62-92 分映射）。

### 受控标签词表

`topics.py` 定义 26 个受控标签（如"智能体"、"产品更新"、"OpenAI"等）。标签来源两部分：LLM enrich 产生的标签 + 基于 URL/source 的确定性标签（如 github.com -> "GitHub"）。合并后取前 4 个。

## External Dependencies

| 依赖 | 用途 |
|---|---|
| FastAPI + Uvicorn | Web 框架和 ASGI 服务器 |
| Pydantic | 数据验证（评估结果 schema） |
| feedparser | RSS/Atom 解析 |
| httpx | HTTP 客户端（信源抓取） |
| openai | LLM API 客户端（OpenAI SDK 兼容接口） |
| trafilatura | HTML 正文提取 |
| beautifulsoup4 | 微信公众号 HTML 解析 |
| Playwright + Chromium | 微信公众号原文抓取浏览器运行时 |
| Mp2RSS | 微信公众号发现层，将已订阅公众号暴露为 RSS/Atom |
| markdown-it-py | 微信文章解读详情页 markdown 渲染 |
| nh3 | 微信文章解读详情页 HTML sanitizer |
| json-repair | 容错 JSON 解析（LLM 输出修复） |
| Jinja2 | 页面 SSR preload 与 eval 报告模板渲染 |
| python-dotenv | 环境变量加载（.env 文件） |

## Key Files for Common Tasks

| 任务 | 关键文件 |
|---|---|
| 添加新信源 | `data/sources.toml`；wechat 源通过 Mp2RSS 合集 feed 配置 |
| 修改 LLM prompt | `prefilter/prompts.py`, `scorer/prompts.py`, `enrich/prompts.py` |
| 添加新 LLM provider | `provider/base.py`（Protocol）+ 新实现文件 + 对应 runner 的 `_provider_from_env` |
| 修改评分权重 | `curator/weights.py`（DEFAULT_WEIGHTS） |
| 修改精选逻辑 | `curator/select.py`（curate 函数） |
| 添加新 API 端点 | `web/routes/` 下新建路由文件 + `web/app.py` 注册 |
| 修改数据库 schema | `migrations/` 下新建 SQL 文件 |
| 修改标签词表 | `topics.py`（CONTROLLED_VOCABULARY） |
| 前端页面修改 | `web/templates/`（`/`、`/all` SSR 首屏）+ `web/static/`（JS/CSS 与静态页面） |
| 调整微信文章解读 | `interpret/runner.py`、`web/routes/wechat.py`、`web/templates/wechat*.html` |
