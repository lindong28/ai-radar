# Architecture

> Mutable snapshot. 结构变更时更新本文件。

## Overview

AI Radar 是一个 AI 信息流聚合站点。信源不止 RSS：`data/sources.toml` 当前 163 条全部 enabled，按 `kind` 分为 `x` 109（X/推文，官方 API 或 RSS adapter）、`feed` 34（RSS/Atom）、`web` 18（原始 Web/API 抓取）、`wechat` 2（微信公众号合集 feed，Mp2RSS + Wechat2RSS 双跑）。抓取后经 LLM 多阶段处理（筛选、评分、翻译富化、精选），以时间线和日报形式通过 Web 展示。

技术栈：Python 3.12+ / FastAPI / SQLite (WAL) / Jinja2 页面模板 / 多 LLM Provider。包管理使用 uv。prefilter/score/enrich 三阶段的默认 provider 都是 DeepSeek 系（`deepseek_v32` / `deepseek_v4_flash` / `deepseek_v4_pro`），同一封装按 key 走官方 DeepSeek 或火山 ARK（`DEEPSEEK_API_KEY` / `ARK_API_KEY`，ARK 侧另有 `provider/ark_breaker.py` 熔断）；GLM（prefilter）与 OpenAI 兼容的 `codex_gpt_mini`（score）是需显式设 `AI_RADAR_PREFILTER` / `AI_RADAR_SCORER` 才启用的备选。

## Modules

```
src/airadar/
├── cli.py              # CLI 入口，argparse 子命令分发
├── db.py               # 数据库连接、迁移执行
├── llm_usage.py        # LLM token 用量独立库记录
├── pricing.py          # LLM tariff catalog、有效区间、解析与查询时成本派生
├── pricing_fallback.json # LiteLLM catalog 不可用时的受管 fallback snapshot
├── ruleset.py          # Ruleset 版本管理（日期.rev 格式）
├── site_config.py      # 站点级环境配置
├── stage_common.py     # Pipeline stage 共享原语（时间、ProviderItem、evaluation 写入）
├── topics.py           # 受控标签词表 + 确定性标签规则
├── wechat_text.py      # 微信标题文本归一化
├── wechat_archive.py   # ai-assistant KB 保留来源身份、微信可见性与公开来源排除谓词
├── migrations/         # 编号递增的幂等 SQL 迁移；016/017 负责 deprecated cost carrier 清理
│
├── pipeline_lock.py    # pipeline 互斥锁的只读探针（非阻塞共享 flock，ADR-052）
├── runtime_env.py      # 共享 dotenv 加载：进程 env > 项目 .env > ~/.claude/.env（ADR-003）
│
├── sources/            # 信源管理
│   ├── loader.py       #   解析 data/sources.toml -> SourceConfig
│   ├── sync.py         #   同步信源配置到数据库
│   ├── contract.py     #   机器契约（schema v2）校验：kind/tier/slug 形状与 union 收据
│   └── x_state.py      #   X runtime metadata（cursor/checkpoint）的共享验证器
│
├── fetcher/            # 内容抓取
│   ├── runner.py       #   fetch_all 主流程
│   ├── rss.py          #   RSS/Atom 解析（feedparser）
│   ├── feed_rules.py   #   原始 Feed 的显式范围与 URL 规则
│   ├── web.py          #   官方 Web/API 列表的登记、范围与确定性解析
│   ├── x_api.py        #   X 用户时间线：冷启动窗口 + since_id/cursor，运行上限见 README 信源池
│   ├── wechat.py       #   微信公众号原文抓取（Playwright + BeautifulSoup）
│   ├── content.py      #   HTML -> 纯文本（trafilatura）
│   ├── http_client.py  #   HTTP 请求 + 条件请求（ETag/Last-Modified）
│   ├── dedup.py        #   content_hash 去重 + upsert
│   └── urls.py         #   URL 规范化
│
├── provider/           # LLM Provider 抽象层
│   ├── base.py         #   Protocol 定义：PrefilterProvider / ScoringProvider / EnrichProvider
│   ├── ark_breaker.py  #   ARK Provider 熔断器
│   ├── deepseek_v32.py #   历史文件/selector 名；默认 prefilter，class model_id=deepseek-v4-flash
│   ├── deepseek_v4_pro.py  # DeepSeek V4 Pro（scoring 备选 / 默认 enrich / eval judge）
│   ├── deepseek_v4_flash.py # DeepSeek V4 Flash（默认 scoring / enrich 备选）
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
│   ├── precompute.py   #   预计算最新精选轮的展示摘要
│   ├── score.py        #   weighted_score 计算 + 信源层级乘数
│   ├── dedup.py        #   候选去重（content_hash / URL）
│   └── weights.py      #   五维权重定义（Weights dataclass）
│
├── interpret/          # 阶段 5：微信公众号文章解读
│   └── runner.py       #   调 ai-assistant summarize-article，写 wechat_interpretations + KB
│
├── wechat_discovery/   # 默认关闭的私有微信公众号发现层（ADR-024 起）
│   ├── config.py       #   解析 data/wechat-discovery.toml -> DiscoveryConfig
│   ├── models.py       #   DiscoveryState/AccountConfig/DiscoveryArticle 等值对象
│   ├── protocol.py     #   后台响应解析与分类错误（auth/rate-limit/platform-rejected）
│   ├── login.py        #   Playwright 登录态捕获与凭据落盘（admin token 提取）
│   ├── provider.py     #   DiscoveryProvider Protocol + ProviderAccount/ProviderPage
│   ├── provider_store.py #  provider attempt/cursor checkpoint 与文章身份账本
│   ├── tikhub.py       #   TikHub provider 客户端与页面解析
│   ├── shadow.py       #   shadow 与 Mp2RSS 的窗口化比较（不可比较时显式失败）
│   ├── store.py        #   发现 ledger 单一权威（schema 版本、冷却、平台错误码）
│   └── status.py       #   手动 probe / 后台请求的有效冷却与可用状态判定
│
├── audit/              # 来源读取的完整性 oracle 与收据
│   ├── completeness_oracle.py # 从原始 feed/web 响应枚举 (slug,url) 全集
│   └── receipts.py     #   收据规范化哈希、字段与代码哈希校验、AIHOT 对账
│
├── performance/        # 用户旅程性能监控与候选修复
│   ├── browser_probe.py #  Chromium 四旅程测量
│   ├── http_probe.py   #   HTTP 层旅程测量 + 返回体身份匹配
│   ├── journey_monitor.py # idle-only 样本、PERF:* 规则与 14 天保留
│   ├── budgets.py      #   LocalBudget 与样本 P75/P95 判定
│   ├── config.py       #   config/performance.toml 加载与校验
│   ├── context.py      #   探针前后的 pipeline 活动上下文采集
│   ├── stage_ledger.py #   pipeline stage 账本与 idle/busy 区间分类
│   ├── runner.py       #   探针 adapter 入口：runtime 规范化 + 观测 payload 组装
│   └── remediation.py  #   fail-closed 隔离 worktree candidate worker
│
├── eval/               # 质量评估（与 AIHOT 对比）
│   ├── judge.py        #   run_eval 主流程 + LLM judge + 报告生成
│   ├── compare_renderer.py # HTML 对比页渲染
│   ├── distribution.py #   分数分布统计
│   ├── aihot_dataset.py #  private AIHOT capture/window 契约、离线 replay/validate/slice
│   └── schemas/aihot-item-v1.schema.json # benchmark item v1 的自持冻结 schema
│
├── presentation/       # 跨 Web/预计算的展示数据组装
│   ├── summary.py      #   item summary、enrichment 解析与可见推荐理由
│   ├── media.py        #   媒体资产提取与图片代理 URL
│   └── related.py      #   关联讨论单条/批量查找
│
├── web/                # Web 服务
│   ├── app.py          #   FastAPI app 工厂 + uvicorn 启动
│   ├── cors.py         #   CORS 配置（configured domain + localhost）
│   ├── envelope.py     #   统一 API 响应包装 {success, data, error}
│   ├── schemas.py      #   API 响应 schema 与 SSR preload 字段契约
│   └── routes/
│       ├── admin.py    #   内部运维页面与 API 路由
│       ├── categories.py #  分类单一契约 + SQL/Python matcher + URL 去重子句
│       ├── search.py   #   共享搜索原语：tokenize、FTS/LIKE、简繁体、受控别名与空白归一化
│       ├── request_db.py # 请求级 SQLite 连接生命周期
│       ├── pagination.py # 版本化 LRU 计数缓存 + 页码 clamp
│       ├── timeline.py #   GET /api/v1/timeline — 全量时间线
│       ├── curated.py  #   GET /api/v1/curated — archive/digest 路由分派 + GET /api/v1/hot
│       ├── curated_archive.py # 跨 run 去重归档、按日归档与真实计数
│       ├── curated_digest.py # 指定 run 的单轮 digest
│       ├── hot_cache.py #   热点候选后台缓存：版本键、窗口下推 SQL 与热度排序（docs/adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md）
│       ├── daily_metrics.py # 日报页头部指标（事件数、一手源、新模型、信源数）
│       ├── items.py    #   GET /api/v1/items/{id} — 单条详情
│       ├── sources.py  #   GET /api/v1/sources（兼容投影）+ /api/v2/sources（完整公开 inventory）
│       ├── wechat.py   #   GET /api/v1/wechat — 多词检索、分阶段兜底、排序、列表与 markdown sanitize
│       ├── media.py    #   GET /img — 受限图片同源代理：微信 CDN 直取、twimg 经出口代理（ADR-057）
│       └── health.py   #   GET /api/v1/healthz — 健康检查
│
└── admin/              # 运维聚合
    ├── usage.py        #   记录行用量、查询时成本、measurement_scope 与分组/跨窗聚合
    ├── cost_report.py  #   周报、暴露量核对与 recorded-cohort 比较
    ├── cost_audit.py   #   raw catalog、派生成本、anchor 与 residue 对账
    ├── alerts.py       #   A1–A7、D3、投递与 lifecycle
    ├── thresholds.py   #   A1–A7 告警阈值常量表（窗口、最小样本、比率）
    ├── calibration.py  #   从评估/访问日志/pipeline 历史基线校准上述阈值
    ├── access_log.py   #   uvicorn access log 行解析与按路径/状态聚合
    ├── metrics.py      #   /admin dashboard 指标聚合（pipeline 轮次、阶段、分位）
    ├── performance.py  #   performance 面板数据与 launchd 期望清单状态
    ├── edgeone.py      #   EdgeOne 规则引擎与仓内 pinned 快照逐条对账（ADR-039）
    ├── wechat_kb.py    #   版本化 ai-assistant catalog 校验、显式补导、事务后检查与 receipt
    └── x_media_backfill.py # 对已入库 item 一次性回填 X 媒体元数据，不动 checkpoint

web/asset-pins.json     # /app.js 与 /style.css 的内容摘要与 ?v= 版本串（刻意放在 static/ 之外，避免成为公开 URL）

web/static/             # 前端静态文件（根目录 web/，非 src 内）
├── index.html          #   精选首页旧静态文件（deprecated，保留作回滚）
├── all.html            #   全量时间线旧静态文件（deprecated，保留作回滚）
├── daily.html          #   日报页
├── item.html           #   单条详情页
├── app.js              #   前端 JS
├── style.css           #   样式
├── wechat-icon.svg     #   微信入口图标
└── daily-overrides-20260514c.css  #  日报页样式覆盖

web/templates/          # Jinja2 SSR 页面模板（目录在仓库根 web/，非 src 内）
├── index.html          #   精选首页 SSR + preload（含热点块）
├── all.html            #   全量时间线 SSR + preload
├── hot.html            #   热点榜页 SSR；含未就绪态与有界自动重载脚本
├── about.html          #   关于页 Jinja2 模板
├── bookmarks.html      #   收藏页外壳（条目存 localStorage，由 app.js 渲染）
├── changelog.html      #   CHANGELOG.md 请求时渲染
├── more.html           #   移动端「更多」入口页
├── wechat.html         #   微信文章解读列表 SSR + preload（内联 CSS，ADR-039）
├── wechat_detail.html  #   微信文章解读详情页（sanitized markdown HTML）
├── wechat_404.html     #   微信 slug 未命中的 404 页（带回列表链接）
├── admin.html          #   内部运维 dashboard
├── admin_usage.html    #   内部 LLM 成本视图
├── _hot_topics.html    #   首页热点块 partial（与 app.js renderHotTopics 逐节点同形）
├── _prepaint_list.html #   首屏前 12 条 .item-row 服务端直出
├── _mobile_topbar.html #   ≤960px 顶栏
├── _mobile_tabbar.html #   ≤960px 底部 tab 栏
├── _icp_footer.html    #   ICP 备案页脚
└── _wechat_inline_style.css # /wechat 内联进 SSR HTML 的样式（无 ?v= 可 bump）
```

AIHOT benchmark 的代码/数据边界跨两个 Git 仓。主仓拥有 `scripts/capture_aihot_dataset.py`、`src/airadar/eval/aihot_dataset.py`、冻结 schema 与 synthetic tests；`benchmarks/aihot` 是 private submodule，gitlink 固定一个已远端发布的数据 commit。data 仓拥有 capture manifest、两遍 API raw、SSR raw、window manifest 与 JSONL，因此离线 replay/validate/slice 不依赖 7 天 live retention 后仍能访问 AIHOT。主仓 tree 不承载任何 AIHOT raw、JSONL、标题或 URL blob。

Capture 以采集时刻的 public API/RSS/OpenAPI/SSR surface 为 baseline。连续两遍相邻 API pass 的两个正式 UTC 日 target hashes 一致，才接受 canonical pass 并发布 window；这只排除同次采集内的 transient drift，不声称可见 public surface 等于 AIHOT 内部 snapshot、筛选、标签或排序 authority。发布顺序固定为 data local commit → 经授权 push exact data SHA → 主仓 gitlink commit；data push、远端设置、主仓 push与主分支整合是彼此独立的授权边界。

仓库级验证工具：`scripts/web_contract_golden.py` 提供可复用的 Web contract capture、manifest 校验、JSON/HTML 比较和 SQLite 逻辑摘要。任务专用请求、adapter、快照与冻结库仅由执行工作区临时持有；任务完成后删除，只有最小且可独立维护的行为契约才提升到测试。使用触发与生命周期见 [Web Contract Golden 验证](references/web-contract-golden.md)。

## Layers

系统按主职责分三层，另有一个跨层的共享组合模块：

```
┌──────────────────────────────────┐
│  CLI / Web（入口层）              │   cli.py, web/app.py
├──────────────────────────────────┤
│  Pipeline（业务逻辑层）           │   fetcher/ prefilter/ scorer/ enrich/ curator/ interpret/ eval/
├──────────────────────────────────┤
│  Infrastructure（基础设施层）      │   db.py, provider/, sources/, stage_common.py, topics.py, ruleset.py
└──────────────────────────────────┘

共享组合：`presentation/`（Web routes 和 `curator/precompute.py` 共用）
```

**边界规则**：

- **入口层** 负责参数解析和请求路由，不包含业务逻辑
- **Pipeline 层** 各阶段互相独立，通过数据库表（`items` + `item_evaluations`）传递数据，不直接调用彼此
- **Infrastructure 层** 提供数据库连接、LLM 调用、信源配置与 stage 通用原语等基础能力
- **`presentation/` 共享组合层** 是边界例外：它被 Web routes 与 `curator/precompute.py` 共用，同时 import `curator` 评分 helper 和 `enrich` schema，因此不是只能被单向依赖的底层 Infrastructure
- Pipeline 阶段通过 `provider/base.py` 中的 Protocol 与具体 LLM 实现解耦

`cli.py` 只解析 `admin wechat-kb import` 的参数并格式化 receipt；catalog 读取、候选判定和事务写入属于 `admin/wechat_kb.py`。保留来源的身份及“内部微信可见、公开 source 不可见”谓词集中在 `wechat_archive.py`，由 importer、Web routes 与跨源去重共同复用，避免各消费者各写一份 `enabled` 例外。

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
          │                                │
          └── chat_json usage ───────────> │ llm_usage.db
                                           │ 仅 is_ai_related=true
       ┌───────────────────────────────────┘
       │
       ▼
   ┌─────────┐  LLM 五维评分    ┌──────────────────────┐
   │ scorer  │ ────────────────> │ item_evaluations 表  │
   │ runner  │  relevance/      │ stage='scoring'       │
   └─────────┘  density/...     └──────────┬───────────┘
        │                                  │
        └── chat_json usage ─────────────> │ llm_usage.db
                                           │
       ┌───────────────────────────────────┘
       │
       ▼
   ┌─────────┐  LLM 翻译富化    ┌──────────────────────┐
   │ enrich  │ ────────────────> │ item_evaluations 表  │
   │ runner  │  title_zh/       │ stage='enrich'        │
   └─────────┘  summary_zh/...  └──────────┬───────────┘
        │                                  │
        └── chat_json usage ─────────────> │ llm_usage.db
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

ai-assistant KB ── versioned article catalog ──> admin/wechat_kb.py
                                                    │ explicit import only
                                                    ▼
                                      reserved archive items + interpretations
```

`kind="feed"` 由 `rss.py` 解析 RSS/Atom；只有经来源证据批准的范围收窄进入 `feed_rules.py`。`kind="web"` 只用于没有合适原始 Feed 的官方列表或 API，由 `fetcher/web.py` 的代码登记表约束 fetch host、最终 host、item URL 范围、解析器和最小结果数；零结果、越界链接、错误最终 host 或结构漂移会让该来源本轮显式失败，不会切换到 AIHOT、Mp2RSS、第三方镜像或通用任意链接抓取。

`kind="x"` 且 `meta.adapter="x_api"` 的源由 `fetcher/x_api.py` 走 X 官方 API；X RSS 源推荐显式使用 `meta.adapter="rss"`，无 adapter 的 v1 历史配置仍走 RSS。尚无 `x_user_id` 时只做 username identity lookup 并持久化首次时间边界，下一轮才读 user timeline；因此每源每轮仍至多一个远端请求。空页提交 `x_since_time`，拿到帖子后以 `x_since_id` 为 high-water mark；若响应有下一页，只把 cursor 与本批 high-water mark 写入 source runtime metadata，下一轮继续该 cursor，直到排空才推进 committed checkpoint。timeline 每次最多 5 条并排除 replies/retweets；runtime state 在写入、reload 与读取处共享同一验证器，配置文件不能伪造内部 cursor，runner 通过 identity + runtime snapshot + SQL CAS 拒绝陈旧覆盖。冷启动窗口不追接入前历史，因此不代表历史召回对齐。详细取舍见 ADR-046。

`kind="wechat"` 生产源有两个并行运行、取并集：`wx_mp2rss`（托管 Mp2RSS 合集 feed，`MP2RSS_FEED_URL`）与 `wx_wechat2rss`（本机自建 Wechat2RSS，`WECHAT2RSS_FEED_URL`）。两者互有漏文、谁都不是对方的超集，所以并行而非切换（[ADR-059](adr/059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md)）。既有去重按 URL / 正文哈希且作用域限于单个 `source_id`，识别不出跨源重复——两个源给同一篇文章的 URL 没有公共子串（短链 `/s/<token>` vs 长链 `?__biz=…&mid=…&idx=…&sn=…`），正文也来自不同渲染方。因此 `fetcher/dedup.py` 另加一层**跨源身份**，只对 `kind='wechat'` 且 enabled 的来源在 INSERT 前判定：账号（`items.author`）+ 归一化标题（`wechat_text.wechat_identity_title`）+ 发布时间差 ≤ `WECHAT_IDENTITY_WINDOW`（5 分钟），命中即丢弃后到的一条，谁先到留谁。

fetch 阶段通过 RSS 发现新文章链接，再只对尚未入库的 `mp.weixin.qq.com/s/...` 原文用 Playwright 抓全文；抓取失败时降级保留 RSS 裸条目，后续 Web 层仍只公开中文摘要与原文回链。`interpret` 阶段只读取启用的 wechat 源 item，调用 ai-assistant `summarize-article` 逻辑生成结构化总结；`save_decision=1` 的条目展示在 `/wechat` 并回写 ai-assistant KB，`save_decision=0` 只在本库留处理记录。X API 接入不读取或替换 `wx_mp2rss`。

反向补录不是自动 pipeline：`admin/wechat_kb.py` 通过 ai-assistant 的 `--list-article-records` JSONL 契约读取一致的 index/file/vector 快照，只把缺失的合格微信文章写入保留来源 `wx_ai_assistant_kb_archive`。`wechat_archive.py` 单一持有这个来源身份与两组消费谓词：`/wechat` 和跨源去重包含它，公开 source API、fetch 和 A7 排除它。来源固定 disabled，避免配置 reload 把内部归档误当实时 feed；每个 import run 用 `extra_json.import_run_id` 绑定同一事务内的后检查并保留 provenance，失败时整笔事务回滚，成功批次不提供通用删除命令。

每个阶段只处理尚未完成对应评估的新条目。`pipeline.sh` 按顺序调度全部阶段，`interpret` 位于最后且 preflight 缺 ai-assistant 依赖时跳过，不阻断前置抓取/精选。

### Domain-selector egress boundary

`pipeline.sh` 在外部阶段之前调用 `egress-preflight`，严格解析 `check-proxy-status --format=kv`：只有 stored/effective mode 均为 `domain-routing`、`policy_id=domain-routing-v1`、policy digest 合法且 matched、router/三条 upstream/route attribution/overall 全部 healthy，并且 status 给出稳定 selector 地址时才继续。应用不读取 `AI_RADAR_PROXY_FILE`、`current-proxy`、假定导出的 selector 环境变量或父进程六个 proxy 变量。

`egress.py` 为 checked-in httpx、OpenAI-compatible SDK、urllib、Playwright 与 managed standard-env subprocess 提供 owned transport。外部请求显式进入 status-derived selector，loopback/synthetic 请求显式 direct 且不依赖 selector status；`im-notify` 等明确 direct 的本地工具获得清除六个 ambient proxy 变量的环境。router 按最终 hostname 持有 Anthropic → GCP SG、OpenAI/ChatGPT/X → OpenAI provider route（Tencent primary、ZYT fallback）、Ark/DeepSeek/RSS/news/web → direct 的单一 policy 与实际 route audit；AI Radar 只消费 provider aggregate readiness，实际线路由 system-config 的 `tencent_route_mode` 与 audit `selected_route` 给出。`egress_registry.py` 保存可枚举的调用点闭包，测试在新增未分类网络入口时失败。selector-owned 请求的应用 audit 只含 `callsite_id`、hostname、launch、policy identity 与本地 outcome，不记录 URL path/query、headers、body、token 或 proxy URL，也不冒充 route authority；显式 direct 的 loopback/synthetic 请求不产生带 policy identity 的应用 audit。`local_outcome` 的值自带阶段：真实请求为 `request:http:*`/`request:error:*`，managed subprocess 为 `subprocess_env:prepared`，Playwright 为 `playwright_proxy_config:prepared`。后两者不宣称子进程或浏览器已发出请求；managed-standard-env 的准备记录还使用 `hostname=null`，不宣称已知最终目标。`airadar.egress.audit` 的六字段 shape 作为首版兼容面冻结；未来任何增删改字段必须切换新的 logger/channel，而不能在现有 stream 原地演化。

仓外 `AI_ASSISTANT_ROOT` 不因继承标准 env 自动进入保证；其两个脚本及兼容性 receipt 通过检查后才启动，否则 `interpret` 按 ADR-007 skip。Playwright 的外部 navigation 传显式 selector launch proxy，本地/合成 probe 用 no-proxy。ADR-057 `/img` 的独立图片 proxy、`trust_env=False` 与缺配置即 404 语义不变。

### Mac primary → Tencent serving replica

公网副本同步采用“传 base、服务器重建 FTS”，而不是传输 primary 的 FTS 页面。FTS5 segment merge 会重写、重定位大量索引页；旧链路因此在真实增量窗口实发约 1.9GB，而基础表逻辑变化只有很小一部分。每轮重新 DROP/VACUUM 或 fresh-copy base-only DB 同样会重编号大部分 SQLite 页面，所以当前机制维护一份跨轮持久的 base-only shipping replica，并在原布局上应用逻辑差异。

```text
Mac live radar.db (WAL, pipeline keeps writing)
        │ query_only SQLite backup API
        ▼
immutable point-in-time snapshot (含真实 items_fts oracle)
        ├── manifest v2: snapshot-bound FTS digest + raw/HTTP probes
        │
        └── PK logical diff ──▶ persistent base-only shipping replica
                                  │ full non-FTS schema/table reconcile
                                  │ GNU rsync 4KiB delta vs last accepted basis
                                  ▼
Tencent immutable claimed base-only artifact + hash-keyed manifest sidecar
                                  │ copy to inactive slot
                                  ▼
mutable serving candidate: create/rebuild FTS → equality/MATCH/HTTP gates
                                  │ nginx switch → canonical consumer/route gates
                                  ▼
committed serving slot + base-only basis/receipt
```

Mac `sync-db-to-server.sh` 是 producer：它从 live DB 的标准 WAL reader 连接立即设置并回读 `PRAGMA query_only=ON`，经 SQLite backup API 创建一致 snapshot；live DB 与 snapshot/replica/manifest 必须 path 与 inode 均不同。`logical_delta.py` 动态枚举全部非 FTS schema/table，以稳定 PK 计算 INSERT/UPDATE/DELETE（`sqlite_sequence` 单独处理），暂停普通 trigger 后就地更新 persistent replica，再做全表 count/digest 双向对账。对账失败时丢弃 replica 并 base-only bootstrap 自愈；该轮不伪装成稳态小增量。

每轮对账后的 shipping replica 字节态成为 immutable base-only transfer artifact，其完整 SHA-256 是 `snapshot_id`。它不含 `items_fts`、FTS shadow tables 或写这些对象的 triggers，但保留 `items`、`sources`、`item_evaluations` 等重建依赖。GNU rsync 使用 `--no-whole-file --block-size=4096`，两端支持时加 zstd level 3；delta basis 只能是服务器最后接受的同形 base-only artifact。2026-08-10 生产 steady round 的 DB 实发 16.39M（manifest 822.90K，合计约 17.21M），相对旧约 1.9G 降约 99.1%。

`build_fts_manifest.py` 生成 snapshot-bound manifest v2 sidecar。`snapshot_id` 绑定 transfer artifact，`manifest_sha256` 是 canonical manifest 自哈希；FTS oracle 包含六字段全表 row count/digest，以及 title/content_text/source_name/author/title_zh 五个字段专属 probe。每个 probe 同时保存 raw FTS5 `matches`/`field_matches`/`unqualified_matches`，以及按应用真实去重、prefilter/scoring visibility 计算的 `timeline_http_matches`。前者裁判 SQLite MATCH 与全量 FTS 重建，后者裁判 candidate/canonical `/api/v1/timeline`；两套集合不能互相替代。sidecar 先按完整 snapshot hash 发布，再原子发布 `radar.db.incoming`，consumer 只接受 identity 完全匹配的组合。

Server `apply_db_update.py` 是 consumer：claim 后保留 immutable base-only artifact，复制成 inactive slot 的 mutable serving candidate，在 candidate 上按 migration/trigger 语义创建并重建 FTS，再依次验证 base fidelity、六字段全量等价、raw MATCH、candidate HTTP、canonical route/public search。transfer artifact 与 serving candidate 是两种 identity：前者持有 rollback/snapshot authority，也是下一轮 basis 的唯一来源；后者因 FTS rebuild 改变字节，只能服务，绝不能反向成为 basis。

切流采用 delayed final commit。post-switch consumer gates 全部通过前，旧槽、旧 basis 与旧 receipt 始终可恢复；失败时自动切回旧槽、复验 canonical 旧状态、quarantine 新 candidate，且不推进 basis/receipt。只有 durable `consumer_verified` 可以 finalize：从 immutable claimed base 推进 `basis/radar.db.upload`，写 schema v2 receipt，再进入 `committed`。因此 public traffic 已短暂指向 candidate 也不等于 snapshot 已获复制 authority。

崩溃重试 authority 是 `(snapshot_id, manifest_sha256, verifier_identity)`。当前 verifier identity 为 `fts-apply-v5`；identity 不变时，pre-switch crash 每 snapshot 最多允许一次 fresh rebuild retry，第二次 crash 或 deterministic gate failure 进入 durable quarantine。verifier 语义变化必须显式 bump `VERIFIER_VERSION`；已绑定 artifact/manifest 的 `rebuilding` / `prepared` retry checkpoint 遇到新版本时进入 `retry_blocked_verifier_changed`，禁止新 verifier 静默继承 retry。若漂移发生在尚未绑定 manifest 的 `claiming`，则 fail closed 并 quarantine。post-switch pending states 只允许回滚/quarantine，不允许向前猜测；journal、receipt、quarantine failure record 都持久绑定证据。完整决策见 [ADR-014](adr/014-ship-base-only-db-and-rebuild-fts.md)，运维入口见 [operations/services.md](operations/services.md#db-sync-职责验证与故障证据)。

## Performance Monitoring and Remediation

`performance-probe` 是 CLI 入口层下的只读浏览器探针：专属 per-file LaunchAgent 以 `StartInterval=300` 经 `./run.sh performance-probe` 启动，依次从同机 origin 与同机 public（`AI_RADAR_PUBLIC_URL`；未配置时跳过该 vantage、其告警评估同步排除并自动 resolve 既有 firing 状态）测量首页首卡、微信列表首卡、微信详情可读和微信翻页稳定。每条旅程测量前后都以非阻塞共享锁探测 `.pipeline.flock`（pipeline 进程树对它持内核排他锁，见 [ADR-052](adr/052-hold-pipeline-mutex-with-kernel-flock.md)）并读取 pipeline 持久 activity generation；只有两端都证明 idle 且 generation 未变时，`performance/journey_monitor.py` 才把该样本写入 `logs/performance/`，并让 `PERF:<journey>:<vantage>:idle` 窗口消费它。pipeline 正在运行、锁探测失败或测量期间 activity 变化时跳过该次旅程尝试，不保存对应样本、不让 non-idle 输入进入规则。每个 cell 保留 20 个 warm samples + 3 个逐样本窗口，首个 confirmed firing 需要 22 条有效 idle 样本，P75/P95 超预算或 hard failure 连续满足确认窗后直接输出 page。样本和诊断证据保留 14 天；两个 vantage 都来自部署主机，语义固定为 same-host provisional，不是区域 SLO。

### Alert state → delivery → ledger → remediation

```text
A1–A7 / PERF results
        │
        ▼
per-rule lifecycles.page|notice   ──真源──▶ severity-aware sender
        │                                      │
        ├─▶ top-level flat projection                ├─▶ page: ALERT / --alert
        │   (legacy readers; page-preferring)     └─▶ notice: NOTIFICATION
        │                                      │ transport success only
        │                                      ▼
        │                              data/alert-events.jsonl
        ▼
performance remediation ──只读 lifecycles.page firing
```

`admin/alerts.py` 以每个 `rule_id` 下的 `lifecycles` map 作为状态真源；`page` 与 `notice` 分别保存 `state`、`since`、`last_notified`、`detail` 和 `announced`。无 `lifecycles` 的旧 flat entry 在读取时被规范化到其记录的 severity（缺失则保守视为 page），之后统一写新形状。顶层 `state/since/last_notified/detail/severity/announced` 仅是供旧 reader 使用的兼容投影；当异常 state 同时含 firing page 和 notice 时，投影优先 page，避免隐藏高严重度。

状态机只在 firing transport 成功后更新 announced / `last_notified`；未投递成功的 pending firing 或 resolved 都在下轮重试。notice→page 只发送新的 page firing，不发送中间 resolved，并在新 firing 送达后内部关闭旧 notice lifecycle；page 条件降到 notice 档时继续保持同一个 page incident，直到真正恢复。pending 且从未成功公告的旧 firing episode 静默关闭。每个 severity 保留自己的 debounce / cooldown 计时器。投递契约是 at-least-once：发送和状态持久化之间不能原子提交，重试使用发送前持久化的 notification nonce，并把 rule/severity/event/nonce/episode identity 传给 `im-notify` 的 signature ledger 抑制同一意图的重复可见消息；不宣称 exactly-once。成功 sender invocation 与内部合并抑制决策共同写入 `data/alert-events.jsonl`：投递事件含 `{ts,rule_id,severity,type,detail,values,channel}`，`channel=INTERNAL,type=suppressed` 的事件另含 carrier、reason 与 heartbeat freshness。ledger 不是状态真源，写入失败 fail-open，不阻断 delivery 或状态持久化；统计实际推送必须排除 `channel=INTERNAL`。

confirmed `PERF:*` page incident 可由后续的 `performance-remediate` cron 读取。`performance/remediation.py` 对新 state 直接以 `lifecycles.page` firing 为权威 incident，不依赖可能 stale 的顶层投影；仅对无 `lifecycles` 的旧 entry 回退为 flat page。remediation 以 nonblocking lock 和 incident fingerprint 保证单 active、每个 firing episode 单次处置，在独立 git worktree 内用 fail-closed Codex workspace-write 生成 detached candidate commit。生产数据库被固定为 worktree 外的只读诊断输入，worker 无 push/deploy/launchctl 入口；候选必须经人工 review 与显式部署授权才会进入主分支或生产。

## Database

主业务 SQLite 数据库路径为 `data/radar.db`（可通过 `AI_RADAR_DB` 环境变量覆盖）。LLM token 用量写入独立 SQLite 文件 `data/llm_usage.db`（可通过 `AI_RADAR_LLM_USAGE_DB` 覆盖），避免 prefilter/score/enrich 的 per-call usage 写入与主库 pipeline/serve 写锁竞争。两者均由 `db.get_conn()` 开启 WAL 模式、`busy_timeout=5000`。

### 核心表

| 表 | 用途 | 主键 |
|---|---|---|
| `sources` | 信源配置（slug、名称、URL、层级、类型） | `id` (TEXT, slug) |
| `items` | 抓取的内容条目 | `id` (TEXT, SHA1 前 16 位) |
| `item_evaluations` | LLM 评估结果，stage 区分阶段 | `id` (INTEGER, 自增) |
| `curation_runs` | 精选运行记录 | `id` (TEXT, 时间戳+随机) |
| `curated_items` | 精选条目（关联 run） | `(run_id, item_id)` |
| `llm_usage` | DeepSeek/ARK `chat_json` 的 per-call token 用量、模型、阶段与输入归因；存放在独立 `llm_usage.db` | `id` (INTEGER, 自增) |
| `wechat_interpretations` | 微信文章解读结果（summary_md、tags、save_decision、KB 同步状态） | `item_id` |
| `items_fts` | FTS5 搜索虚拟表（trigram 分词），列为 `item_id/title/content_text/source_name/author/title_zh` | -- |
| `wechat_account_avatars` | 公众号真名头像缓存（`avatar_url`、`checked_at`、`updated_at`）；migration `007` 建表，`008` 把 `mmbiz.qpic.cn` 的 http 改写成 https | `account` (TEXT) |
| `archive_cache_generations` | 归档计数缓存的双计数器（`archive_generation` / `category_generation`），由 migration `013` 的一组 trigger 维护；单行表 | `id` (INTEGER, CHECK id=1) |
| `feedback` | 用户反馈（预留） | `id` (INTEGER, 自增) |
| `airadar_migrations` | 迁移记录 | `id` (TEXT) |

### 关键设计

- **去重策略**：`items` 表通过 `(source_id, content_hash)` 唯一约束去重。`content_hash` 是内容文本的 SHA1 前 16 位。同 URL 不同内容视为更新
- **多阶段评估**：`item_evaluations` 通过 `stage` 字段区分 prefilter / scoring / enrich，共用同一张表。每条记录保存完整的 input/output/numeric JSON
- **LLM 用量与派生成本**：`llm_usage` 是已写入的计量行集合，不是 attempt ledger。调用次数、token 合计与同一计价口径的金额合计只从该表记录行派生，因此是全部付费调用对应总量的下界；任何未写入该表的付费调用均不在内（已知例子包括失败链路或未接入计量的调用点，非完整清单）。均值、占比和环比只描述已记录 cohort，相对全部付费调用真值的偏差方向未知。`admin/usage.py` 通过 `measurement_scope` 把两类解释带到 API 响应本身，并按 usage `created_at` 的有效 tariff 生成记录行金额、阶段、Provider、模型组与日序列；跨窗金额和分组比较另把两窗统一按当前费率、cache 全未命中重算，避免 provider cache 字段覆盖率随 stage mix 浮动而永久关闭比较。单次已知成本分母只含 priced+nominal 的已记录调用。`admin/cost_report.py` 消费同一聚合，用 durable `items.fetched_at` 判断每日是否有入库，pipeline 日志只补轮次、fetch inserted 与已记录的 `llm_usage_metering_failure` 证据，不能证明 attempt-level 计量完整；已观测的缺日志/计量标 unknown，不把缺行当作零成本。A6 复用相同归一化，只比较评估时仍可报价的已记录 known cohort；已观测的缺数时降级，只有 live pipeline 造成的当前日未封口例外把已记录金额作为下界继续正向求值，允许 firing/升级。记录行金额回落时只 resolve 这个已记录 cohort，不宣称整体计量健康。两个库中的 `cost_usd` 都是 deprecated carrier，表内已记录调用的派生成本只在查询时计算。

  ```text
  provider / interpret result
            │ best-effort usage landing
            ▼
      data/llm_usage.db ──▶ pricing.py（调用时间有效 tariff）
                                   │
                                   ▼
                           admin/usage.py
                         ┌─────────┼──────────┐
                         ▼         ▼          ▼
                    admin API   cost-report  A6 / cost-audit
                    + HTML      + weekly     + D3 diagnostics
  ```
- **Ruleset 版本**：格式 `YYYY-MM-DD.rN`，用于跟踪 prompt 和规则的变更。同一条目可以有不同 ruleset 版本的评估记录
- **信源层级**：T1（官方一手源，乘数 1.25）/ T1.5（高质量聚合，乘数 1.0）/ T2（社区源，乘数 0.75）
- **搜索索引**：`003_add_fts5_search.sql` 是当前 `items_fts` schema 的权威定义，每次 `migrate()` 都会重建 FTS 表和触发器。索引覆盖标题、正文、来源名、作者和 enrich 生成的中文标题；scoring `reasoning` 不再进入搜索索引。`sources.name` 更新和成功的 enrich 写入会通过 trigger 同步到 FTS。
- **短查询兜底**：timeline 和 curated 共用 `search_id_subquery()`。3 字及以上使用 `items_fts MATCH`；1-2 字只在标题、来源名、作者和中文标题上用 escaped LIKE，避免对正文做短词全表扫。
- **微信解读闸门**：`wechat_interpretations.save_decision=1` 是 `/wechat` 展示和 ai-assistant KB 写入的唯一闸门。详情页从本库 `summary_md` 渲染，不在请求时读取 ai-assistant 文件。
- **微信解读搜索**：查询拆成必需词，每词跨标题、作者、正文、摘要、标签和完整解读满足；严格阶段的长 item 字段走 trigram FTS，短词与解读字段走 escaped LIKE，严格阶段为零结果时才为长 item 字段追加空白不敏感 LIKE。受控评测词同义组只扩展非作者召回，排序保持 raw 作者、raw 标题、alias 标题的优先级。执行策略与残余召回边界见 [ADR-20260901-a31f](adr/20260901-a31f-stage-wechat-whitespace-fallback-after-empty-results.md)。
- **KB 归档边界**：ai-assistant 私有 store 只能经 versioned article catalog CLI 读取。归档导入后网站仍只消费本地 SQLite；内部来源不公开、不调度，但参与微信可见性与跨源身份。

### 索引

| 索引 | 覆盖列 |
|---|---|
| `idx_items_source_published` | `(source_id, published_at DESC)` |
| `idx_items_source_url_norm` | `(source_id, lower(rtrim(url, '/')))` |
| `idx_items_published_fetched_id` | `(published_at DESC, fetched_at DESC, id DESC)` |
| `idx_evaluations_item_stage_ruleset` | `(item_id, stage, ruleset_version)` |
| `idx_curated_items_run_rank` | `(run_id, rank)` |
| `idx_llm_usage_created_model` | `(created_at, model)` |
| `idx_llm_usage_stage_created` | `(stage, created_at)` |
| `idx_llm_usage_item` | `(item_id)` |
| `idx_wechat_interp_decision` | `(save_decision, processed_at DESC)` |
| `idx_wechat_interp_slug` | `(slug)` unique |

## Web Layer

FastAPI 应用，通过 `create_app()` 工厂函数创建。前端是 HTML + JS：`/`、`/all`、`/hot`、`/wechat`、`/wechat/{slug}`、`/bookmarks`、`/more`、`/changelog`、`/about` 以及门控的 `/admin`、`/admin/usage` 都由 Jinja2 模板渲染（`/`、`/all`、`/wechat` SSR 预载首屏数据，后续交互继续通过 API 获取数据）；`/daily` 与 `/item.html` 仍由静态文件提供，`/about.html` 308 重定向到 `/about`、`/curated.html` 308 重定向到 `/`。

**响应式分层**（断点 640/960px）：`>960px` 为侧栏 + 内容区；`≤960px` 侧栏整体隐藏、由常驻 HTML 的 `.m-tabbar`（`web/templates/_mobile_tabbar.html`）与 `.app-mobile-bar`（`_mobile_topbar.html`）接管导航。**内容区不做 DOM 双份**——同一套卡片 DOM 由 media query 重塑几何（见 [ADR-012](adr/012-single-dom-mobile-layer.md)），只有桌面无对应物的 chrome 才是独立节点。

### API 端点

除完整公开信源 inventory 使用 `/api/v2/sources` 外，现有 API 以 `/api/v1` 为前缀；两者都返回统一信封 `{success, data, error}`。

| 端点 | 方法 | 用途 |
|---|---|---|
| `/api/v1/timeline` | GET | 全量时间线，支持页码分页（返回真实总数 COUNT）、channel 过滤（x/news/firstParty）、category 过滤、混合 FTS/LIKE 搜索 |
| `/api/v1/curated` | GET | 精选内容。无 `run_id`/`date` 时返回跨 run 去重的累积归档（页码分页 + 真实总数）；仅带 `date` 时返回该日的跨 run 归档（`/daily` 复用），带 `run_id` 时才返回单轮 digest（可再用 `date` 筛选）。支持 category、混合 FTS/LIKE 搜索 |
| `/api/v1/curated/daily-archive` | GET | 日报归档全集（单次 SQLite 读快照，按 Asia/Shanghai 日期分桶并计数）。**排除晚于今天的桶**——feed 的 `published_at` 不受信任，未来日期若入档会被前端当成「最近一期」 |
| `/api/v1/hot` | GET | 近 N 小时热点榜（`hours` 默认 48、范围 6–168；`limit` 默认 5、上限 10）。`heat = round(加权分×10 + 关联讨论数×5)`；响应级 `generated_at`，逐条含 `published_at`/`fetched_at`/`event_time`/`source_kind`/`author`/`related_discussions`。`event_time` 取可解析且不晚于 `generated_at` 的 `published_at`，否则回退 `fetched_at`（页面相对时间只用它）。候选集由后台缓存供给、无固定条数截断；缓存未就绪时返回 **503 + `Retry-After: 2`**，绝不用 200 + 空 items 冒充（见下） |
| `/api/v1/items/{id}` | GET | 单条详情 + 评估历史 |
| `/api/v1/wechat` | GET | 微信文章解读列表，仅返回 `save_decision=1`，字段含 slug/title/abstract/tags/author/avatar/published_at/url；`q` 按必需词混合检索，严格阶段零结果时才启用空白标准化兜底 |
| `/api/v1/sources` | GET | 兼容信源投影，仅公开既有 `feed` / `x` / `wechat` kind；不会把新增 `web` kind 静默加入已发布 v1 集合 |
| `/api/v2/sources` | GET | 完整的已启用公开信源 inventory，包含 `web` kind、配置状态、公开入口与作用域明确的读取验证状态；不公开付费 Mp2RSS URL 或 X cursor |
| `/api/v1/healthz` | GET | 健康检查（条目数、运行数、ruleset 版本） |
| `/api/v1/admin/metrics` | GET | 内部运维指标；与 `/admin` 同一访问门控 |
| `/api/v1/admin/usage` | GET | 内部 LLM 已记录用量 rollup；`measurement_scope` 分开限定加总量与派生统计口径；与 `/admin` 同一访问门控 |

`/api/v1/curated?run_id=X` 的历史 run digest 有 **TTL 语义**：常驻保留会把超过 `keep_days`（默认 7 天）且非最新 run 的 `curated_items.summary_json` 预计算缓存清空，此后该 run 的 digest 改由 `_compute_items` live 现算，内容反映**当前** enrichment 而非 curation 时的快照。最新 run 的 summary 永不清、字节一致；HTML 用户页只服务最新 run，不受影响。瘦身机制见 [operations/db-slimming.md](operations/db-slimming.md)。

### 热点榜的后台候选缓存

`/api/v1/hot`、`/hot` 页面与首页热点块都从 `web/routes/hot_cache.py` 的单条目候选缓存取数，**请求路径只 peek、从不计算也从不等待**（[060-hot-cache](adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md)）。缓存只保存 hydrate 过的候选行（含 related discussions），不保存成品 payload——`generated_at`、相对时间与「是否已滑出窗口」全部由 `rank_hot_items()` 按调用方时钟现算，缓存住成品会把这三者一起冻住。

填充只发生在后台：`create_app()` 的 lifespan 调 `prewarm_hot_candidates()` 起一次异步 prewarm（刻意不同步——一次冷 hydration 要数秒，而 readiness 门控部署健康检查），此后由 daemon 线程 `hot-candidate-refresh` 用自己的 SQLite 连接刷新。候选集按 `WINDOW_HOURS_MAX=168` 小时下推 SQL 窗口取全量（`limit=-1`，无 600 条截断），因为每个更小的 `hours` 都是它的子集，一个 key 就能服务全部窗口。缓存版本键 `candidate_version()` = 归档 data version + `curation_runs` 的 `COUNT` 与 `MAX(id)`；版本不符按 miss 处理而非陈旧命中。条目超过 `REFRESH_AFTER_SECONDS=120` 触发后台刷新，超过 `MAX_STALE_SECONDS=180` 即不可用——后者不是调参旋钮，而是替代「证明 generation trigger 覆盖了所有会改变 payload 的写入」（`_batch_related_discussions` 读全部 items，而 `archive_cache_items_ai` 只对已精选的 bump）。

三个消费面因此各有一个**未就绪态**，与「确实没有热点」严格区分：

| 消费面 | 未就绪时的行为 |
|---|---|
| `/api/v1/hot` | 503 + `Retry-After: 2`；非 200 被中间件强制 `private, no-store`，冷态不会被边缘缓存放大 |
| `/hot` 页面 | 渲染「热点榜单正在生成，稍后自动刷新」（`hot_ready=False`）+ `no-store`；页内脚本按 4s/6s/10s 有界自动重载，计数存 sessionStorage 防死循环 |
| 首页热点块 | SSR 只读已热的缓存，读不到就不渲染该块；由 `app.js` 的 `renderHotTopics()` 兜底 fetch，遇 503 按 2s/4s/8s 退避重试并保持骨架占位 |

### 页面路由

| URL | 渲染方式 | 说明 |
|---|---|---|
| `/` | `web/templates/index.html` | 精选累积归档首页（跨 run 去重，**无限滚动**，首屏为最新精选），Jinja2 SSR，内联 `/api/v1/curated` 归档形状的 preload JSON |
| `/all` | `web/templates/all.html` | 全量时间线，**无限滚动**（搜索态仍用页码分页——timeline API 搜索时忽略 cursor），Jinja2 SSR，内联 `/api/v1/timeline` 形状的 preload JSON |
| `/hot` | `web/templates/hot.html` | 热点榜页，SSR 渲染 `limit=10, hours=48` 的热点 payload；桌面侧栏可达，移动端从首页「完整榜单 →」进入。候选缓存未就绪时改渲染「榜单正在生成」态（`no-store` + 有界自动重载），不冒充「暂无热点」 |
| `/changelog` | `web/templates/changelog.html` | 渲染仓库根 `CHANGELOG.md`（markdown-it-py 逐 token 赋 `.cl-*` class 后渲染），**请求时读取**故编辑源文件即时生效 |
| `/bookmarks` | `web/templates/bookmarks.html` | 收藏页；条目存浏览器 `localStorage`（`ai-radar:bookmarks:v1`），模板只出外壳与导入/导出工具条，列表由 `app.js` 渲染，无服务端读写 |
| `/more` | `web/templates/more.html` | 移动端「更多」页，只含 `/wechat`、`/bookmarks`、`/about`、`/changelog` 四个入口；**桌面无导航入口**，仅 ≤960px 底部 tab 栏第 4 项指向它 |
| `/wechat` | `web/templates/wechat.html` | 微信文章解读列表，Jinja2 SSR，内联 `/api/v1/wechat` 形状的 preload JSON |
| `/wechat/{slug}` | `web/templates/wechat_detail.html` | 微信文章解读详情页，`summary_md` 经 markdown-it-py 渲染后用 nh3 sanitize |
| `/daily` | `web/static/daily.html` | 日报（支持 `?date=` 或 `/daily/YYYY-MM-DD`） |
| `/about` | `web/templates/about.html` | 关于页 Jinja2 模板；`/about.html` 308 重定向到此路由 |
| `/curated` | `web/templates/index.html` | 首页别名——直接复用 `index_page()` 同一渲染（同样支持 `category`/`q`/`page`/`limit`），不是重定向；`/curated.html` 则 308 重定向到 `/` |
| `/admin` | `web/templates/admin.html` | 内部运维 dashboard；需 Cloudflare Access 或显式本地 bypass |
| `/admin/usage` | `web/templates/admin_usage.html` | 内部 LLM 成本最小视图：窗口总额三态、来源单价、未定价清单与 cache 采集覆盖；需 Cloudflare Access 或显式本地 bypass，不挂公开导航 |
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

`/`、`/all` 的动态路由定义必须在 `app.mount("/", StaticFiles(...))` 之前。

### 分类系统

前端的 category 过滤在后端 SQL 层实现。分类基于 enrich 阶段产生的标签，`web/routes/categories.py` 的 `CATEGORY_CONTRACT` 是分类规则的单一契约：SQL 谓词与 Python matcher 均由该契约生成，前端不再保留分类规则副本。

| Category | 包含的标签 | 冲突处理 |
|---|---|---|
| `ai-models` | 模型发布 | 同时是教程/实践时排除 |
| `ai-products` | 产品更新、MCP/工具 | 含模型发布且无产品更新时排除，即使同时有 MCP/工具 |
| `industry` | 行业动态、安全/对齐、现象/趋势 | 无 |
| `paper` | 论文/研究 | 无 |
| `tip` | 教程/实践、部署/工程 | 仅有部署/工程且同时属于行业类时排除 |

### 真实计数与分页

`/`（精选归档）、`/all`（timeline）、`/wechat` 都用数字页码分页（首末页固定、当前页相邻页、… 省略），共用 `web/static/app.js` 的一套分页组件，API 均返回真实总数并用 `web/routes/pagination.py` 的 `clamp_page()` 把越界页收敛到真实末页。Timeline 与精选归档另共用 `VersionedTotalCache`：过滤签名 + 数据版本作 LRU key（上限 64，带锁），search 路径不缓存，FastAPI lifespan 启动时预热两个默认视图计数。WeChat 列表每次直接计数，只共用 clamp，不使用数据版本缓存或 prewarm。决策与性能数据见 ADR-005（timeline）与 ADR-006（精选归档）。

**Timeline（`/api/v1/timeline`）**：返回真实总数 COUNT（非 ADR-004 时期的前向估算）。计数与 rows 查询是**两套独立 SQL**：rows 用 EXISTS-per-row 子句判定每条 item 的最新 prefilter/scoring 评估，计数用 `latest_prefilter` / `latest_scoring` CTE + JOIN 的集合公式（`_count_timeline_items_with_prefilter()`），避免 per-row 子查询随数据量退化——改 timeline 过滤逻辑时两处需同步。计数缓存数据版本为 `_timeline_data_version()`（最新 curation_run id/ruleset、items 行数与 max rowid、max eval id）。CTE 计数依赖 migration `010` 的 `item_evaluations(stage,error,item_id,id DESC)` 索引。

**精选归档（`/api/v1/curated` 无 `run_id`）**：跨 run 去重的累积归档——`curated_archive.py` 的 `_latest_curated_join()` 用 `c.run_id = (SELECT MAX(run_id) FROM curated_items WHERE item_id=i.id)` 相关子查询，每个 item 只保留其最近一次被精选的元数据。真实计数走 `_count_archive_items()` + `_cached_archive_total()`，数据版本由 migration `013` 的 `archive_cache_generations` 双计数器提供：归档成员变化 bump `archive_generation`，影响分类的 enrich 变化 bump `category_generation`；migration `014` 进一步让同一 item 的后续 curate run 不再把非成员变化误判为失效。归档每页用 `_compute_archive_page()` **现算 item_summary**（不依赖 `summary_json`——预计算只覆盖约 30%），enrichment 一次 `LEFT JOIN` 取出，关联讨论按页 `_batch_related_discussions()` 批量正/反查（`items_fts` 反查）。去重子查询依赖 migration `011` 的 `idx_curated_items_item_run(item_id, run_id)`。仅带 `date` 时仍走 `curated_archive.py` 的按日跨 run 归档（`/daily` 复用）；带 `run_id` 时才进 `curated_digest.py` 的单轮 digest，可再用 `date` 限定该轮内日期。

### 公开分页的边缘缓存

上面的进程内计数缓存之外，公开分页路径还叠了一层**边缘缓存**：`web/app.py` 的响应中间件对白名单路径（`/`、`/wechat`、`/api/v1/curated`、`/api/v1/wechat`）发 `Cache-Control`。判据 fail-closed——仅当请求是 GET/HEAD、响应 200、且查询参数是安全分页变体（子集 ⊆ `{page}`，API 再允许 `limit`）时发 `public, max-age=90, stale-while-revalidate=30`；带 `q=`/过滤/未知参数或非 200 一律回落 `private, no-store`，保证个性化与搜索结果不进共享缓存。**当前生产边缘是 EdgeOne**：`news.aiplanet.live` 经 DNS-only CNAME 接入腾讯云 EdgeOne（[ADR-039](adr/039-route-news-through-edgeone-dns-only-cname.md)），上面这套 `Cache-Control` 由它 respect-origin 消费。Cloudflare 的 respect-origin Cache Rule 属**历史/旁路**配置（旧域名链路），不是当前公网热路径；其规则记录见 [operations/services.md](operations/services.md)，runbook 见 [operations/monitoring-alerting.md](operations/monitoring-alerting.md)。

EdgeOne 另对 `/app.js` 与 `/style.css` 两个精确路径强制节点缓存 7 天，因此这两个资源改动后必须跑 `scripts/bump_frontend_assets.py`——它按内容摘要重算 `?v=` 版本串、改写全部 HTML 引用并更新 `web/asset-pins.json`；漏 bump 就是「部署了但线上不生效」。`/wechat` 把 style.css 内联进 SSR HTML（同一 ADR），没有 `?v=` 可 bump，改 CSS 时改为在缓存窗口后从真实公网验证内联内容。强制缓存规则本身住在腾讯云控制台、是仓外权威，由 `admin/edgeone.py` 拉取规则并与仓内 pinned 快照逐条对账（`./run.sh admin edgeone check`，exit 0=无漂移 / 1=有漂移 / 2=未核实）。

前端 `web/static/app.js` 在 SSR preload 后预取下一页 API 并在点击时复用同一 promise，命中同一 90s 边缘窗口；search/category 请求绕过该 memo。

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
| Playwright + Chromium | 微信公众号原文抓取 + 默认 `performance-probe` 四旅程测量的浏览器运行时 |
| Mp2RSS | 生产微信公众号发现层之一（托管付费服务），将已订阅公众号暴露为 RSS/Atom；`MP2RSS_FEED_URL` |
| Wechat2RSS | 生产微信公众号发现层之二（自建，`deploy/wechat2rss/` 下 docker-compose 拉起 `ttttmr/wechat2rss` 容器）；`WECHAT2RSS_FEED_URL`，与 Mp2RSS 双跑取并集（ADR-059） |
| X 官方 API | `kind="x"` 且 `meta.adapter="x_api"` 源的主路径：`fetcher/x_api.py` 走 `https://api.x.com/2`，需 `X_BEARER_TOKEN` |
| 图片出口代理（repo 外常驻） | `AI_RADAR_IMG_PROXY_URL` 指向的 HTTP 正向代理，`/img` 取 `pbs.twimg.com` 时**强制**走它（serve 主机到 twimg 的连接被上游阻断）；未配置即直接 404，绝不退化成直连（[ADR-057](adr/057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md)）。产线实机拓扑（新加坡 tinyproxy + SSH 隧道）与诊断顺序见 [operations/services.md](operations/services.md) |
| Domain-routing selector（repo 外常驻） | pipeline 与已登记外部 client 的唯一出网入口；AI Radar 消费严格 status-derived `agent_proxy`，system-config 持有域名 policy、GCP/Tencent/direct upstream 与 route audit。状态不健康时 pipeline fail closed；见 [ADR-20260826-68e2](adr/20260826-68e2-route-ai-radar-through-domain-selector.md) 与 [operations/services.md](operations/services.md#ai-radar-域名-selector-出网) |
| 微信公众号后台（候选） | 默认关闭的 shadow 发现路径；私有登录态只供显式单账号 probe，尚未进入 pipeline |
| markdown-it-py | 微信文章解读详情页 markdown 渲染 |
| nh3 | 微信文章解读详情页 HTML sanitizer |
| json-repair | 容错 JSON 解析（LLM 输出修复） |
| Jinja2 | 页面 SSR preload 与 eval 报告模板渲染 |
| python-dotenv | 环境变量加载（.env 文件） |

## Key Files for Common Tasks

| 任务 | 关键文件 |
|---|---|
| 添加新信源 | 更新 `tests/fixtures/aihot_sources.json` 机器契约并生成 `data/sources.toml`；生产 wechat 源是 `wx_mp2rss` + `wx_wechat2rss` 两个合集 feed 并行（`MP2RSS_FEED_URL` / `WECHAT2RSS_FEED_URL`，ADR-059），改动要同时考虑跨源去重键；后台发现候选账号在 `data/wechat-discovery.toml`，不得把凭据写入该文件 |
| 维护 AIHOT 对齐信源 | `tests/fixtures/aihot_sources.json` + README「信源维护与验证」四个命令；稳定 identity/aliases、retirement ledger、解析器/规则、公开投影和收据必须同步，不能只改 TOML |
| 采集或消费 AIHOT 私有基准 | `scripts/capture_aihot_dataset.py` + `src/airadar/eval/aihot_dataset.py` + `src/airadar/eval/schemas/aihot-item-v1.schema.json` + private submodule `benchmarks/aihot`；先核对 gitlink/tool/schema identity，再离线 validate/slice |
| 添加原始 Web/API 信源 | `src/airadar/fetcher/web.py` 登记确定性 fetch/item 边界与 parser + 正反 fixture + 真实 `audit_non_x_retrieval.py` 收据 |
| 调整微信发现候选 | `wechat_discovery/`、`data/wechat-discovery.toml`、[ADR-024](adr/024-shadow-wechat-admin-discovery-before-mp2rss-cutover.md) 至 [ADR-032](adr/032-reject-duplicate-urls-before-wechat-shadow-comparison.md)、[ADR-040](adr/040-verify-provisional-searchbiz-mapping-with-article-url-biz.md)、[ADR-041](adr/041-version-wechat-discovery-invariant-hardening.md)、[摄取 runbook](operations/wechat-ingestion.md) |
| 调整微信读书只读 canary | `scripts/wechat_weread_canary/`、[ADR-033](adr/033-version-weread-canary-shelf-request-evidence.md) 至 [ADR-038](adr/038-observe-weread-dynamic-header-presence-without-replay.md)、[摄取 runbook](operations/wechat-ingestion.md) |
| 修改 LLM prompt | `prefilter/prompts.py`, `scorer/prompts.py`, `enrich/prompts.py` |
| 添加新 LLM provider | `provider/base.py`（Protocol）+ 新实现文件 + 对应 runner 的 `_provider_from_env` |
| 修改评分权重 | `curator/weights.py`（DEFAULT_WEIGHTS） |
| 修改精选逻辑 | `curator/select.py`（curate 函数） |
| 修改展示摘要、媒体或关联讨论 | `presentation/summary.py`, `presentation/media.py`, `presentation/related.py` |
| 添加新 API 端点 | `web/routes/` 下新建路由文件 + `web/app.py` 注册 |
| 修改精选 API 模式分派 | `web/routes/curated.py`, `web/routes/curated_archive.py`, `web/routes/curated_digest.py` |
| 改热点榜（口径、缓存或未就绪态） | `web/routes/hot_cache.py`（候选缓存与热度公式）、`web/routes/curated.py`（`hot_payload`/`/api/v1/hot`）、`web/templates/hot.html`、`web/templates/_hot_topics.html`、`web/static/app.js`（`renderHotTopics`）+ [060-hot-cache](adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md)；SSR partial 与 CSR 渲染必须逐节点同形 |
| 修改分类契约 | `web/routes/categories.py`（`CATEGORY_CONTRACT`） |
| 修改 timeline/curated 搜索 | `web/routes/search.py` + 对应路由的查询字段；共享原语的改动还要核对微信搜索是否被连带影响 |
| 修改微信解读搜索 | `web/routes/wechat.py`（必需词组合、严格/兜底阶段、排序）+ `web/routes/search.py`（tokenize、简繁体、受控别名、空白归一化）+ `tests/test_wechat_interpretation.py`；取舍见 [ADR-20260831-30ad](adr/20260831-30ad-hybrid-wechat-search-and-kb-archive-import.md)、[ADR-20260831-8b7c](adr/20260831-8b7c-control-wechat-review-term-aliases.md) 与 [ADR-20260901-a31f](adr/20260901-a31f-stage-wechat-whitespace-fallback-after-empty-results.md) |
| 补录 ai-assistant KB 微信文章 | `admin/wechat_kb.py`、`wechat_archive.py`、`cli.py` + [摄取 runbook](operations/wechat-ingestion.md#手动补录-ai-assistant-知识库归档) + [跨仓契约](references/ai-assistant-contract.md#只读文章目录-jsonl) |
| 修改真实计数缓存或页码 clamp | `web/routes/pagination.py`, `web/routes/timeline.py`, `web/routes/curated_archive.py`, `web/routes/wechat.py` |
| 修改 pipeline stage 通用 evaluation 原语 | `stage_common.py` + `prefilter/runner.py`, `scorer/runner.py`, `enrich/runner.py` |
| 修改数据库 schema | `migrations/` 下新建 SQL 文件 |
| 修改 Mac→Tencent DB 同步、FTS oracle 或 server apply 状态机 | `deploy/sync/sync-db-to-server.sh`, `logical_delta.py`, `snapshot_db.py`, `build_fts_manifest.py`, `apply_db_update.py` + [ADR-014](adr/014-ship-base-only-db-and-rebuild-fts.md) |
| 修改标签词表 | `topics.py`（CONTROLLED_VOCABULARY） |
| 前端页面修改 | `web/templates/`（`/`、`/all` SSR 首屏）+ `web/static/`（JS/CSS 与静态页面） |
| 调整微信文章解读 | `interpret/runner.py`、`web/routes/wechat.py`、`web/templates/wechat*.html`、`wechat_archive.py` |
| 行为等价的 Web route / presentation / SSR 重构 | `scripts/web_contract_golden.py` + [Web Contract Golden 验证](references/web-contract-golden.md) |
| 改动 `web/static/app.js` 或 `web/static/style.css` | `scripts/bump_frontend_assets.py`（重算 `?v=`）+ [ADR-039](adr/039-route-news-through-edgeone-dns-only-cname.md)、[前端经验](experiences/frontend.md) |
