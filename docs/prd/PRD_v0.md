# ai-radar v0 PRD

> 状态：v0.1 草案 (2026-05-07)
> 维护者：lindong
> 前置文档：[VISION.md](VISION.md) — 范围只读，§4 BINDING 原则在本 PRD 中仍然有效

---

## §1. 范围

v0 = **能跑通的 Web MVP**。引用 VISION §8 v0 行：

> 信源配置（文件即可）/ RSS 抓取 / AI 预筛 / 5 维 LLM 评分 / 代码权重+阈值精选 / Web 时间线 + 精选页 / 公开只读 / admin 写入口

VISION §10 锁定的 D1–D14 在 v0 持续有效。本 PRD §13 新增 D15–D22 共 8 项实施层决策。

### 1.1 v0 明确不做（继承 VISION §5 + 增补）

| 不做 | 来源 |
|---|---|
| Embedding 事件聚类 / 主条选择 | v1 |
| Owner 反馈机制（👍/👎/comment） | v1 |
| 反馈→权重调节流程 | v1 |
| 回归评估系统（历史数据重跑对比） | v1（v0 仅做 schema 预留） |
| 信源健康监控 | v1 |
| Telegram 推送 / 每日报 | v2 |
| 趋势预测 / 热度指数 | v3 |
| 信源管理 web UI（v0 admin 走 CLI） | 可能进 v1，未承诺 |
| 公开页与 owner 页内容差异（VISION §5）| 始终不做 |
| 多用户写 | 始终不做 |

---

## §2. 数据流总览

```
                  ┌─────────────┐
                  │ sources.toml│ (owner 编辑，文件即数据库)
                  └──────┬──────┘
                         ▼
   ┌────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │  cron  │───▶│   fetcher    │───▶│  prefilter   │───▶│    scorer    │
   └────────┘    │ feedparser+  │    │ 便宜 LLM     │    │ 评分 LLM     │
                 │ http_get     │    │ AI 相关性    │    │ 5 维 0-10    │
                 └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
                        │                   │                   │
                        ▼                   ▼                   ▼
                ┌─────────────────────────────────────────────────┐
                │              sqlite (radar.db)                  │
                │  items / item_versions / source_state / runs    │
                └────────────────┬────────────────────────────────┘
                                 │
                                 ▼
                        ┌────────────────┐
                        │    curator     │ 代码：权重×阈值×排序×去重
                        └────────┬───────┘
                                 │
                                 ▼
                ┌─────────────────────────────────────┐
                │         FastAPI HTTP API            │
                │   /api/timeline  /api/curated       │
                │   /api/items/{id}                   │
                └────────┬────────────────────────────┘
                         ▼
                ┌─────────────────────────────────────┐
                │ static SPA: index.html (timeline)   │
                │            curated.html             │
                │            item.html                │
                └─────────────────────────────────────┘
                         ▲
                         │  (公开只读)
                         │
                  Cloudflare Tunnel
                  aiplanet.live
```

写入口（admin）独立 CLI 通道，**不**通过 HTTP：

```
owner CLI ──▶ admin 子命令 ──▶ sqlite + sources.toml
```

---

## §3. 模块清单

| 模块 | 路径 | 职责 | 入口 |
|---|---|---|---|
| `sources` | `apps/ai-radar/sources/` | 加载 / 校验 sources.toml；分级与权重 | Python lib |
| `fetcher` | `apps/ai-radar/fetcher/` | RSS 抓取、去重、原文持久化 | `run.sh fetch` |
| `prefilter` | `apps/ai-radar/prefilter/` | 调用便宜 LLM，AI 相关性二分类 | `run.sh prefilter` |
| `scorer` | `apps/ai-radar/scorer/` | 调用评分 LLM，输出 5 维 0–10 分 | `run.sh score` |
| `curator` | `apps/ai-radar/curator/` | 代码化加权、阈值、排序、去重，写精选名单 | `run.sh curate` |
| `web` | `apps/ai-radar/web/` | FastAPI 后端 + 静态前端（HTML/JS） | `run.sh serve` |
| `admin` | `apps/ai-radar/admin/` | CLI 写入口（信源管理、规则版本切换、数据维护） | `run.sh admin <cmd>` |
| `provider` | `apps/ai-radar/provider/` | LLM 抽象层（prefilter & scorer 共用） | Python lib |

入口聚合：`apps/ai-radar/run.sh` 子命令派发；`test.sh` 跑 pytest。符合项目 CLAUDE.md workflow contract。

---

## §4. 数据 schema (sqlite, `radar.db`)

> 设计原则：核心列稳，版本特定字段进 JSON blob（VISION §4.5）。

### 4.1 sources（仅运行时缓存，源 truth 是 sources.toml）

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 信源 slug，与 toml 中 key 一致 |
| name | TEXT | 显示名 |
| url | TEXT | RSS 地址 |
| tier | TEXT | T1 / T1.5 / T2 |
| enabled | INTEGER | 0/1 |
| meta_json | TEXT | 任意元信息（作者、官方账号链接等） |
| synced_at | TEXT | 上次从 toml 同步进 db 的时间 |

### 4.2 items（核心表，宽松保留）

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 内容 hash（url + canonical title 的 sha1 前 16） |
| source_id | TEXT FK | sources.id |
| url | TEXT | 原文 URL |
| title | TEXT | 标题 |
| author | TEXT | 作者，可空 |
| published_at | TEXT | RSS 中 pubDate（ISO8601） |
| fetched_at | TEXT | 我们入库时间 |
| content_text | TEXT | 抓取到的正文（去 HTML 后） |
| content_html | TEXT | 原始 HTML（备份） |
| content_hash | TEXT | content_text 的 sha1，用于去重 |
| extra_json | TEXT | RSS 原始字段、enclosure、tags 等 |

索引：`source_id, published_at DESC`；唯一约束：`(source_id, content_hash)`。

### 4.3 item_evaluations（评分中间结果，可重跑）

| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| item_id | TEXT FK | items.id |
| stage | TEXT | `prefilter` / `scoring` |
| ruleset_version | TEXT | 规则版本号（prompt + 权重 + 阈值的语义版本，例 `2026-05-07.r1`） |
| model_id | TEXT | 实际调用的模型 ID，如 `glm-4-flash` / `codex-gpt-mini` |
| input_json | TEXT | 喂给 LLM 的输入摘要（不是全文，只存 prompt + 字段） |
| output_json | TEXT | LLM 原始输出（结构化） |
| numeric_json | TEXT | 解析后的标准化数值（prefilter: `{is_ai_related: bool, confidence: float}`；scoring: `{relevance, density, recency, authority, engineering}`） |
| latency_ms | INTEGER | |
| cost_usd | REAL | 估算 token cost |
| evaluated_at | TEXT | |
| error | TEXT | 失败原因 |

索引：`(item_id, stage, ruleset_version)`。

### 4.4 curation_runs（每次精选计算的批次）

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | 批次 id（ISO 时间戳 + 随机 4 字符） |
| ruleset_version | TEXT | 与 evaluations 对齐 |
| weights_json | TEXT | 本次使用的权重 |
| threshold | REAL | 阈值 |
| input_eval_ids | TEXT | 参与计算的 evaluation ids（JSON array） |
| output_curated_ids | TEXT | 精选 item_ids（JSON array, 按排序） |
| created_at | TEXT | |

### 4.5 curated_items（curation_runs 的扁平视图，便于 web 查询）

| 列 | 类型 | 说明 |
|---|---|---|
| run_id | TEXT FK | curation_runs.id |
| item_id | TEXT FK | items.id |
| weighted_score | REAL | 加权后总分 |
| rank | INTEGER | 当批次排序 |
| reason_json | TEXT | 各维度分 + tier 系数 + 是否击穿阈值 |

索引：`(run_id, rank)`；唯一：`(run_id, item_id)`。

### 4.6 反馈 schema 预留（v0 不写入，v1 启用）

```sql
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT,
  signal TEXT,   -- 'thumbs_up' | 'thumbs_down' | 'comment'
  body TEXT,
  ruleset_version TEXT,
  created_at TEXT
);
```

v0 建表但无写入路径，避免 v1 加表时迁移历史数据（VISION §4.5）。

---

## §5. 接口契约

### 5.1 CLI

`./apps/ai-radar/run.sh <subcommand> [...]`

| 子命令 | 用途 | 频率 |
|---|---|---|
| `fetch` | 全量信源拉取一轮，写 items | cron 每 30 分钟 |
| `prefilter [--since=N]` | 跑 prefilter（默认最近 N=24h 未筛过的） | cron 每 30 分钟，紧跟 fetch |
| `score [--since=N] [--limit=M]` | 对 prefilter 通过的条目跑 5 维评分 | cron 每 30 分钟 |
| `curate [--ruleset=ID]` | 触发一次 curation_run | cron 每小时 |
| `serve [--port=8000]` | 起 FastAPI | launchd 常驻 |
| `admin sources add\|remove\|enable\|disable\|list` | 信源管理 | owner 手动 |
| `admin rerun-eval --since=DATE [--ruleset=ID]` | 历史回跑（v0 留接口，v1 接回归对比） | owner 手动 |
| `admin curate --ruleset=ID --weights=path` | 用指定权重重算精选 | owner 调参 |
| `admin db migrate` | schema 升级 | owner 手动 |

### 5.2 HTTP（公开只读）

base path: `/api/v1`

| 方法 | 路径 | 说明 | 响应包络 |
|---|---|---|---|
| GET | `/timeline?cursor=&limit=50` | 全量条目按 fetched_at 倒序，含基本元信息 | `{items, next_cursor, total}` |
| GET | `/curated?run_id=&date=` | 精选；不带 run_id 默认最近一次 | `{run_id, ruleset_version, items}` |
| GET | `/items/{id}` | 单条目详情，含评分维度 | `{item, evaluations}` |
| GET | `/sources` | 当前信源池（read-only） | `{sources}` |
| GET | `/healthz` | 健康检查 | `{ok: true, ruleset_version}` |

API 响应统一包络：`{success, data, error}`（依 VISION §共用模式 + 项目 patterns）。CORS 仅放行 `aiplanet.live` 自身。**没有任何 POST/PUT/DELETE 端点**。

### 5.3 LLM Provider 抽象

`provider/base.py`：

```python
class PrefilterProvider(Protocol):
    model_id: str
    def is_ai_related(self, item: Item) -> PrefilterResult: ...

class ScoringProvider(Protocol):
    model_id: str
    def score_5d(self, item: Item) -> ScoringResult: ...

@dataclass
class PrefilterResult:
    is_ai_related: bool
    confidence: float  # 0..1
    raw: dict

@dataclass
class ScoringResult:
    relevance: float       # 0..10
    density: float         # 0..10
    recency: float         # 0..10
    authority: float       # 0..10
    engineering: float     # 0..10
    raw: dict
```

实现（v0 提供 4 个，配置走 env + sources.toml 全局段）：

| Provider | 角色 | v0 是否实现 |
|---|---|---|
| `GLMPrefilter` | prefilter | ✅ 默认 |
| `CodexGptMiniScorer` | scoring | ✅ 默认 |
| `DeepSeekV32Prefilter` | prefilter | ✅ 长期方案，v0 接好接口可切 |
| `DeepSeekV4ProScorer` | scoring | ✅ 同上 |

切换通过环境变量 `AI_RADAR_PREFILTER=glm|deepseek_v32`、`AI_RADAR_SCORER=codex_gpt_mini|deepseek_v4_pro`。

---

## §6. 5 维评分定义

| 维度 | 名称 | 0–10 含义 | 喂给 LLM 的判断准则 |
|---|---|---|---|
| relevance | 相关性 | 0=与 AI/工程师视角无关；10=高度相关 | 是否涉及 AI 模型/系统/工程实践/工具链/研究 |
| density | 信息密度 | 0=营销/废话；10=每段都是新信息 | 单位字数承载的新事实/新洞见，去除 fluff/重复/转述 |
| recency | 时效性 | 0=陈旧/已被取代；10=反映当前最前沿状态 | 内容是否与 2026 年技术现状一致；不是按 pubDate，是按内容鲜度 |
| authority | 信源权威 | 0=道听途说；10=一手发布 | 作者是否就是事件主体 / 引用是否一手 / 是否经过核实 |
| engineering | 工程实践相关性 | 0=纯学术 / 纯八卦；10=可直接转化为代码/架构/工具链 | 是否给出可复现的方法、API、benchmark、踩坑、配置 |

LLM **只**输出这 5 个浮点数 + 一句话 reasoning（不写入决策，仅 debug 用）。**严禁**让 LLM 输出"是否精选 / 最终分 / 推荐度"（VISION §4.1 BINDING）。

---

## §7. 代码化精选规则（curator）

### 7.1 加权公式

```
weighted_score = ( w_rel * relevance
                 + w_den * density
                 + w_rec * recency
                 + w_auth * authority
                 + w_eng * engineering ) * tier_multiplier
```

`weighted_score ∈ [0, 10]`（权重和=1，tier 乘子接近 1）。

### 7.2 v0 默认权重（在 PRD 锁定，可被 admin curate 命令覆盖）

| 权重 | 默认值 | 取值理由 |
|---|---|---|
| w_rel | 0.20 | 预筛已过滤无关，剩余空间 |
| w_den | 0.25 | 工程师怕水分 |
| w_rec | 0.15 | 时效但不能压倒密度 |
| w_auth | 0.15 | 一手优于转述但不应一票否决 |
| w_eng | 0.25 | 工程实践相关性是本产品差异化 |

合计 1.00。

### 7.3 信源 tier 乘子

| Tier | 乘子 |
|---|---|
| T1 | 1.05 |
| T1.5 | 1.00 |
| T2 | 0.95 |

### 7.4 阈值

`weighted_score >= 6.5` 进精选。

### 7.5 排序与去重

1. 同一 `content_hash` 仅保留 weighted_score 最高的一条。
2. 同一 url 的不同时间抓取版本同上。
3. 精选名单按 `weighted_score` 倒序，相同分数按 `published_at` 倒序。
4. 单批次精选上限 30 条（VISION §4.2 Precision ≫ Recall）。

### 7.6 ruleset_version 命名

`<date>.r<n>`，例 `2026-05-07.r1`。任何权重 / 阈值 / 5 维 prompt 变更都必须 bump。Curator 把 ruleset_version 写入 curation_runs，evaluations 表保留对齐。

---

## §8. 信源池起步表（拟，待 owner redline）

> Tier 含义：T1=官方一手；T1.5=团队/专家个人 blog；T2=聚合/社区/媒体。
> 每条可在 `sources.toml` 启用/禁用。RSS URL 在实施期会现场验活，不可达的会标 `enabled=false` 并提交 issue。

### T1（一手官方，乘子 1.05）

| slug | 名称 | RSS |
|---|---|---|
| openai_blog | OpenAI Blog | https://openai.com/blog/rss.xml |
| anthropic_news | Anthropic News | https://www.anthropic.com/news/rss.xml |

### T1.5（专家/团队个人，乘子 1.00）

| slug | 名称 | RSS |
|---|---|---|
| simonw | Simon Willison's Weblog | https://simonwillison.net/atom/everything/ |
| lilianweng | Lilian Weng's Log | https://lilianweng.github.io/index.xml |
| sebastianraschka | Ahead of AI (Sebastian Raschka) | https://magazine.sebastianraschka.com/feed |
| latent_space | Latent Space | https://www.latent.space/feed |
| interconnects | Interconnects (Nathan Lambert) | https://www.interconnects.ai/feed |
| importai | Import AI (Jack Clark) | https://importai.substack.com/feed |

### T2（聚合/社区/媒体，乘子 0.95）

| slug | 名称 | RSS |
|---|---|---|
| hn_ai | Hacker News (AI/LLM 关键词) | https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT |
| lobsters_ai | lobste.rs AI tag | https://lobste.rs/t/ai.rss |
| the_batch | The Batch (deeplearning.ai) | https://www.deeplearning.ai/the-batch/feed/ |
| last_week_ai | Last Week in AI | https://lastweekin.ai/feed |

合计 12 条。**owner redline 项**：增删、调级、关键字过滤（HN 信源可能要进一步过滤）。

---

## §9. 部署方案

### 9.1 拓扑

```
[本地 macOS]
   ├── launchd job: ai-radar-fetch.plist (每 30 min)
   ├── launchd job: ai-radar-curate.plist (每小时)
   ├── launchd job: ai-radar-serve.plist (常驻 :8000)
   └── cloudflared service (Tunnel → aiplanet.live)
        │
        ▼
   Cloudflare edge → aiplanet.live (HTTPS, free SSL)
```

### 9.2 部署步骤（在 plan 阶段展开）

1. uv 装 deps
2. `radar.db` 初始化 + schema migrate
3. 写 launchd plist 文件（位于 `apps/ai-radar/deploy/launchd/`）
4. cloudflared 注册 tunnel + DNS（owner 已授权 Cloudflare 登录）
5. `cloudflared tunnel run` 作为 launchd 子进程或独立 service

### 9.3 secrets

`.env`（不入仓库）：

```
GLM_API_KEY=...
OPENAI_API_KEY=...     # 复用现有
DEEPSEEK_API_KEY=...   # 留空，长期启用时填
CODEX_SUBSCRIPTION=... # codex CLI 账号侧凭证（具体形态待 codex CLI 文档确认）
AI_RADAR_PREFILTER=glm
AI_RADAR_SCORER=codex_gpt_mini
AI_RADAR_DB=./apps/ai-radar/data/radar.db
```

`.env.example` 同步更新。

---

## §10. 验收标准

### 10.1 端到端（必须全过）

| # | 验收项 | 验证方式 |
|---|---|---|
| E1 | 12 条信源至少 8 条能成功抓到当日新条目 | `run.sh fetch` 后 `select count(*) from items where fetched_at > date('now', '-1 day')` ≥ 50 |
| E2 | prefilter 能筛掉非 AI 内容 | 手工抽查 20 条 prefilter 结果，AI 相关识别准确率 ≥ 85% |
| E3 | 评分输出严格 5 个 0-10 浮点数 | pytest schema 校验 100% 通过 |
| E4 | curate 一次产出 ≤ 30 条精选 | 实际数据下跑通 |
| E5 | 浏览器访问 https://aiplanet.live 显示时间线 + 精选页 | E2E 手动验证 |
| E6 | 公开访问无任何写入口（无登录、无表单） | 静态 HTML 审查 + curl 探测 admin 路径 404 |
| E7 | admin CLI 能增删信源后下次 fetch 生效 | 端到端命令链 |

### 10.2 模块级（pytest，覆盖率 ≥ 80%，遵循项目 testing 规则）

| 模块 | 关键测试 |
|---|---|
| sources | toml 解析、tier 校验、slug 唯一性 |
| fetcher | feedparser mock、去重、content_hash 一致性 |
| prefilter | provider mock、错误注入、ruleset_version 写入 |
| scorer | 5 维边界值、provider mock、JSON schema 严格校验 |
| curator | 加权公式、阈值、排序、去重、上限 30 |
| web | 路由响应、CORS、404、CORS only `aiplanet.live` |
| provider | 4 个 provider 各自接口契约（mock 化）|
| admin | sources add/remove 双向、rerun-eval 不损坏现有数据 |

### 10.3 非功能

| 指标 | 目标 |
|---|---|
| `fetch + prefilter + score + curate` 端到端耗时（300 条新条目） | < 10 分钟 |
| LLM 月度成本 | 开发期 < 100 元；长期切到 DeepSeek 后预算 100-500 元 (D4) |
| 数据丢失 | 0（sqlite 文件需在 deploy doc 写入备份策略） |

---

## §11. 风险与未决项

### 11.1 持续风险（继承 VISION §9）

不重复列。

### 11.2 v0 阶段新发现风险

| 风险 | 缓解 |
|---|---|
| RSS URL 验活：列表里部分 URL 可能 404 / 改版 | 实施期挨个 curl 验证，失败的标 `enabled=false` 并记 issue |
| Codex subscription 调用 gpt-mini 的接口形态不确定 | 实施第一周专门做 provider 适配 spike，失败则 fallback 到 OpenAI API key |
| Cloudflare Tunnel + launchd 联动 macOS 重启后是否自起 | 部署文档需明确测试 |
| `published_at` 缺失或时区混乱 | fetcher 兜底为 `fetched_at`；时间一律 UTC ISO8601 |
| LLM 输出非合法 JSON | 强 schema validation + 1 次 retry + 失败标 evaluation.error |

### 11.3 v0 待决的实施层细节（不阻塞 PRD，但 plan 时要解）

- [ ] cron 用 launchd 还是单独 `python -m schedule` 长进程？倾向 launchd。
- [ ] 静态前端是手写 HTML+vanilla JS 还是引入 alpine.js 这种轻量库？倾向 vanilla（无构建链）。
- [ ] sqlite WAL 模式开启与否？实施期决定。
- [ ] 抓取 HTTP 客户端：feedparser 自带 vs 单独 `httpx`？倾向 `httpx` for fetch + feedparser for parse。

---

## §12. v0 不做（再强调，给 plan 阶段对齐用）

PRD 实施时如果出现以下诱惑，一律拒绝并 ping owner：

1. "顺手把反馈打标做了" — 不行，schema 有但路径无。
2. "用 embedding 去重比 hash 好" — 不行，v1 才有 embedding。
3. "在 web 上做信源管理 UI" — 不行，v0 admin = CLI。
4. "让 LLM 直接输出最终分省事" — 红线，违反 VISION §4.1 BINDING。
5. "把 5 维定义嵌进 prompt 里加规则" — 红线，VISION §5 反例第 8 条。
6. "复用 summary-agent 的 sqlite/embedding 代码" — 违反 VISION D2，必须 greenfield。

---

## §13. 决策记录（D15–D22）

> 半年后回看，先 audit 这一节看前提是否还成立。

| # | 决策 | 选定 | 备择 | 理由 |
|---|---|---|---|---|
| D15 | plan 命令 | 先 PRD 再 plan | 直接 plan / 跳过 PRD | handoff 明示 PRD→plan 顺序；PRD 锁接口契约后 plan 能少返工 |
| D16 | 部署位置 | 本地 macOS + Cloudflare Tunnel | VPS / Vercel / 云函数 | 零运维零成本；机器关了即下线，对 v0 验证够 |
| D17 | Web 框架 + 数据库 | FastAPI + 静态前端 + sqlite | Next.js+Postgres / Jinja SSR | Python 单语言栈与项目 uv 体系一致；sqlite 单文件备份方便 |
| D18 | LLM 评分模型（开发期） | GLM 预筛 + Codex gpt-mini 评分 | DeepSeek 全套 / Anthropic 全套 | 利用 owner 现有 codex 订阅，降本；provider 抽象保证后续切换 |
| D19 | LLM 评分模型（长期） | DeepSeek V3.2 预筛 + DeepSeek V4 Pro 评分 | 继续用开发期组合 | AIHOT 同款；国内访问稳定；本预算 100-500 元/月 |
| D20 | 信源池起步规模 | 17 条具体 RSS（owner redline） | placeholder / 模板化 | 一开始就有真实数据可调参 |
| D21 | 公开域名 | aiplanet.live (Cloudflare DNS+Tunnel+SSL) | 不绑域名 / 个人子域 | owner 已持有；Cloudflare 一站式免费 SSL |
| D22 | admin 写入口 | CLI（`run.sh admin ...`） | Web admin + token / IP 白名单 | 单户场景下 CLI 最省心，零鉴权代码 |

---

## §14. 与 VISION 章节对齐表（便于 audit）

| VISION 节 | PRD 落实位置 |
|---|---|
| §4.1 LLM 只输出 5 维分 | §6 + §7 + §12 红线 |
| §4.2 Precision ≫ Recall | §7.4 阈值 6.5 + §7.5 上限 30 条 |
| §4.3 可回测可对比 | §4.3 evaluations 全量持久化 + §5.1 admin rerun-eval |
| §4.4 反馈不过拟合 | §4.6 schema 预留但 v0 不实现 |
| §4.5 数据是资产 | §4 schema 宽松保留 + extra_json/raw_json blob |
| §5 反例 | §1.1 + §12 |
| §8 v0 范围 | §1 全文 |

---

## 附录 A. sources.toml 模板

```toml
# v0 信源池。owner 编辑此文件即生效，下次 fetch 自动同步进 db。

[[source]]
slug = "openai_blog"
name = "OpenAI Blog"
url = "https://openai.com/blog/rss.xml"
tier = "T1"
enabled = true

[[source]]
slug = "simonw"
name = "Simon Willison's Weblog"
url = "https://simonwillison.net/atom/everything/"
tier = "T1.5"
enabled = true

# ... 共 17 条
```

## 附录 B. 评分 prompt 骨架（v0.1）

```
你是工程师视角的 AI 信息评分员。读完下面这条文章后，输出严格的 JSON：

{
  "relevance": <0-10>,
  "density": <0-10>,
  "recency": <0-10>,
  "authority": <0-10>,
  "engineering": <0-10>,
  "reasoning": "<不超过 200 字>"
}

5 个维度的判分准则（不要混用，不要输出"是否推荐"或"总分"）：
- relevance：与 AI 模型/系统/工程实践/工具链/研究的相关程度。
- density：单位字数承载的新信息密度，去除 fluff/重复/转述。
- recency：内容反映 2026 年技术现状的鲜度（不是发布时间）。
- authority：作者是否就是事件主体 / 是否一手发布 / 是否经过核实。
- engineering：能否转化为可复现的代码/API/架构/benchmark/踩坑实践。

文章信源 tier：{tier}
文章作者：{author}
文章标题：{title}
文章正文：{content_text}
```

具体微调留 v0 实施期。
