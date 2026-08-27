# AI Radar

AI Radar 是一个公开只读的 AI 信息流站点。它从 RSS、X 和微信公众号信源抓取 AI 相关内容，经过 LLM 筛选、评分、翻译后，以时间线形式呈现精选内容。维护者运行的实例在 <https://news.aiplanet.live>，可以先去看成品形态。

**适合谁**：想自建一份个性化 AI 信息流的个人或小团队。信源池、筛选口径、评分权重、精选阈值都在你自己的仓库里，fork 后按自己的关注面改。它不是多租户 SaaS，也没有账号体系——一份部署服务一个信源口径，读者侧完全只读。

UX 设计与信息架构参照 [AIHOT](http://aihot.virxact.com/)。差异在定位：AIHOT 是一个统一口径的公共站点，AI Radar 把信源、筛选和评分做成每个人都能按自己需求配置、自行部署的个性化信息消费工具。

## 快速开始

### 1. 环境准备

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/your-org/ai-radar.git
cd ai-radar
uv sync
uv run playwright install chromium  # 微信抓取必需；启用 performance-probe 时也复用它
```

装完验证浏览器真的能起来（只看 `playwright --version` 证明不了浏览器已下载）：

```bash
uv run python -c "from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); print('chromium ok', b.version); b.close()"
```

打印 `chromium ok <版本号>` 即可。

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入一个 LLM API Key——`prefilter` / `score` / `enrich` 的默认后端只认 `DEEPSEEK_API_KEY` 或 `ARK_API_KEY`（详见下文「LLM Provider」）：

```
DEEPSEEK_API_KEY=sk-xxx
```

其他配置项都有可用的默认值，第一次本地试跑不用动（部署到公网前再按下文「站点身份与最小配置」改）。X 信源要 `X_BEARER_TOKEN`、微信信源要 `MP2RSS_FEED_URL` / `WECHAT2RSS_FEED_URL`，都可以先不填——不填时对应来源被跳过、其余信源照常加载，口径见下文「信源」。

下面两个变量 `.env.example` **未收录，需要时手动新增**：`AI_RADAR_PUBLIC_URL`（公网站点地址，只被 `performance-probe` 用作 public 视角的测量目标，未设置时探针只测 origin）、`AI_RADAR_ADMIN_ALLOW_LOCAL`（设为 `1`/`true`/`yes` 时允许来自 `127.0.0.1`、`::1`、`localhost` 的请求直接访问 `/admin`，本地开发用；不设置则 `/admin` 要求请求带 Cloudflare Access header）。出网 selector 不从 `.env` 读取代理地址；不要再配置 `AI_RADAR_PROXY_FILE`。

### 3. 初始化数据库

```bash
./run.sh admin db migrate
./run.sh admin db backfill-links
./run.sh admin sources reload
```

成功读数：`migrate` 打印 `migrated <radar.db 路径>` 与 `migrated llm_usage <llm_usage.db 路径>` 两行；`backfill-links` 打印 `item_links backfilled for <路径>: <N> links`；`reload` 打印 `reloaded <N> sources`，N 应等于 `data/sources.toml` 里启用的来源数。

`backfill-links` 把文章正文里的外链抽成一张带索引的表，「关联讨论」靠它回答。**可续跑，也可以晚些再补**——没跑完之前该功能回落到旧的全表扫描：结果正确，但每次打开精选页会多花约 1 秒。只需在已有数据库上跑一次，此后由抓取流程自行维护；重跑是安全的。

### 4. 运行数据处理流水线

```bash
./run.sh fetch       # 从 RSS/X/微信公众号信源抓取内容
./run.sh prefilter   # LLM 筛选 AI 相关内容
./run.sh score       # 五维评分（relevance、density、recency、authority、engineering）
./run.sh enrich      # LLM 生成中文标题和摘要
./run.sh curate      # 精选高价值内容（默认阈值 6.5）
./run.sh interpret   # 可选：微信文章解读 + ai-assistant 兼容知识库回写（默认关闭）
```

成功读数：`fetch` 逐源打印 `OK <source_id> fetched=… inserted=…`（失败的源打 `FAIL <source_id> <错误>`），末行汇总 `=== attempted=… inserted=… failed=…`——先看 `failed` 是不是 0，再看 `inserted` 是不是大于 0。`prefilter` / `score` 打印 `processed=… errors=…`，`enrich` 同形，`curate` 打印 `curate run_id=… selected=… threshold=…`，`interpret` 未启用时打印 `interpret skipped=true message=…` 并正常退出。

**新库首轮 `curate` 很可能 `selected=0`，首页因此是空的，这是正常的**：默认阈值 6.5，而首轮抓到的多是普通条目。此时用 `/all`（完整时间线，不过阈值）确认数据确实进来了，或用 `./run.sh curate --threshold 3` 把阈值调低重跑一次，观察 `selected` 是否变大。阈值没有环境变量入口，只有这个命令行参数。

### 5. 启动 Web 服务

```bash
./run.sh serve --host 127.0.0.1 --port 8000
```

访问 `http://localhost:8000`：应看到左侧栏 + 精选时间线的首页；若精选为空（见上），改看 `http://localhost:8000/all` 应列出已入库的条目。`curl http://localhost:8000/api/v1/healthz` 返回 200 说明服务本身起来了。

## 自动化调度

`pipeline.sh` 按顺序执行 `fetch → prefilter → score → enrich → curate → interpret`，每个阶段只处理尚未评估的新条目。单阶段失败会记录 `FAIL` 后继续，日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`，`.pipeline.flock` 上的内核排他锁跳过重叠运行。

每轮在第一个外部阶段前执行 `./run.sh egress-preflight`。它只接受 `check-proxy-status --format=kv` 返回完整、healthy、policy matched 的 `domain-routing-v1` 状态；失败时整轮在发出外部请求前退出，不会退回父 Claude Code/Codex 的 proxy 环境或直连。域名路由由外部 domain router 持有：Anthropic → GCP SG 且 fail closed，OpenAI/ChatGPT/X → OpenAI provider route（Tencent primary，建隧道前失败时 ZYT fallback；两者均不可用则 fail closed），Ark/DeepSeek/RSS/新闻/网页 → direct。AI Radar 不维护第二份域名表；实际出口以 system-config 的 `tencent_route_mode` 与 route audit `selected_route` 为准，生产安装前置与排障见 [服务 runbook](docs/operations/services.md#ai-radar-域名-selector出网)。

默认 cron 频率是 `*/15 * * * *`，即每 15 分钟执行一次。

cron / launchd 不继承交互式 shell 的 `export` 变量——启用自动调度前确认项目根目录 `.env`、`~/.claude/.env` 或 supervisor 环境已配 LLM API Key。

```bash
./pipeline.sh             # 手动跑一次
./install.sh pipeline     # 注册到 user crontab，每 15 分钟一次
```

`install.sh pipeline` 的成功读数：先打印 `✓ pipeline: installed in user crontab (every 15 min)`（已装过则是 `✓ pipeline: already in crontab`），末尾 `Install summary:` 块里 `installed:` 一行列出本次装上的服务、`skipped:` 列出因缺依赖跳过的服务及原因。装完再核一遍排期与产出：`crontab -l | grep pipeline.sh` 应见一条 `*/15` 开头的行；每轮结果写在 `logs/pipeline-YYYYMMDD-HHMMSS.log`。

调度方式详情、launchd 备选模板见 §服务 + [docs/operations/services.md](docs/operations/services.md)。

不要把 `deploy/cron/ai-radar-pipeline` 或其展开结果直接送入 `crontab -`：这会替换该用户的整份 crontab，并删除 DB sync、cost-report 等无关排期。安装和更新 pipeline 排期统一使用 `./install.sh pipeline`。

### 用户旅程性能监控与候选修复

`performance-probe` 是一个可选的同机探针：用浏览器从 origin 与 public 两个视角测量四条用户旅程（首页首卡、微信列表首卡、微信详情可读、微信翻页稳定），只在 pipeline 空闲时保存样本，确认退化后可由 `performance-remediate` 生成一个仅供人工审阅的本地候选 commit（不 push、不 deploy、不写生产库）。所有读数都是 same-host provisional，不代表区域 SLO。

```bash
./run.sh performance-probe --help
./run.sh performance-probe --origin-url http://127.0.0.1:8000 --public-url https://your-site.example.com
```

`--help` 只证明这个子命令存在，不证明探针能工作。**判活看真实运行的输出**：每条被测旅程会打印一行 `<journey> vantage=… latency_ms=… load_class=… provisional=true`，末行是 `stored=N alerts_sent=M`——`stored` 才是这一轮真正保存了几条样本，`stored=0` 表示所有旅程都被跳过（pipeline 非空闲、浏览器不可用或 public 视角未配置），此时它没有在监控任何东西。`--public-url` 未给且 `AI_RADAR_PUBLIC_URL` 未设置时只测 origin。

窗口规则、告警预算、证据保留、安装/卸载步骤与 `performance-remediate` 的运维 gate 见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md#用户旅程性能监控) 与 [`docs/operations/services.md`](docs/operations/services.md)；各服务当前是否安装以那两份文档为准。告警通道本身的无发送 preflight 见 [监控与告警 runbook](docs/operations/monitoring-alerting.md#im-notify-飞书双通道)。

## Web 页面

| 页面 | URL | 说明 |
|------|-----|------|
| 精选 | `/` | 高评分精选内容，按日期分组（可折叠），顶部为近 48 小时热点榜（2 条 + 「完整榜单 →」），无限下拉加载 |
| 全部 AI 动态 | `/all` | 完整时间线，最新优先，无限下拉加载（搜索态用页码分页） |
| 热点榜 | `/hot` | 近 48 小时热点完整榜单（热度 = 加权分×10 + 关联讨论×5）；桌面从侧栏进入，移动端从首页「完整榜单 →」进入。serve 重启后的短窗内接口返回 503、页面显示「热点榜单正在生成」是预期行为（会自愈），见 [monitoring-alerting.md](docs/operations/monitoring-alerting.md#serve-重启后-apiv1hot-短暂-503预期非故障) |
| 微信文章解读 | `/wechat` | 已订阅微信公众号文章的结构化总结，支持按标题、公众号、摘要和标签搜索；详情页为 `/wechat/<slug>` |
| AI 日报 | `/daily` | 每日精选归档，按月份分组的可折叠归档栏 + 今日看点，支持 `?date=YYYY-MM-DD` |
| 收藏 | `/bookmarks` | 本设备浏览器收藏的内容（localStorage），支持导出/导入 JSON |
| 关于 | `/about` | 项目介绍和信源池 |
| 更新日志 | `/changelog` | 渲染仓库根 `CHANGELOG.md` |
| 更多 | `/more` | **仅 ≤960px 有入口**（底部 tab 栏第 4 项）：微信文章解读 / 收藏 / 关于 / 更新日志 |
| 运维监控 | `/admin` | 用户量、文章摄取、pipeline 阶段健康与当前告警。本机可用 `AI_RADAR_ADMIN_ALLOW_LOCAL=1` 放行；公网侧的访问控制要求见下文「部署 → 运维监控」 |
| LLM 已记录用量 | `/admin/usage` | 内部页面，展示最近 30 天 `llm_usage` 记录行的成本三态、来源单价、未定价清单和 cache 采集覆盖；访问控制同 `/admin` |

**响应式**：桌面（`>960px`）是侧栏 + 内容区，移动档（`≤960px`）侧栏换成底部 4 项 tab 栏、信息密度向紧凑收敛（列表卡片上不显示收藏按钮，改经「更多 → 收藏」）。完整的断点、布局与各档取舍是可验收契约，见 [`docs/contracts/ux-contract.md`](docs/contracts/ux-contract.md) 的 RS-* 节。

主题支持浅色 / 深色 / 跟随系统三态，当前档由滑动选中底板指示。

## 数据流水线

```
RSS / X / 微信公众号源（Mp2RSS + 自建 Wechat2RSS，取并集） → fetch → prefilter → score → enrich → curate → interpret → web 展示 / ai-assistant KB
```

各阶段做什么见「快速开始 → 4. 运行数据处理流水线」的命令注释；`interpret` 是可选阶段，启用后对微信公众号文章调用 ai-assistant 兼容的 summary-agent 脚本，保存独立解读数据并把值得阅读的文章回写外部知识库（见下文「微信文章解读」）。

### 数据库维护

精选 digest 的预计算缓存（`curated_items.summary_json`）会随每次 curate 增长；常驻保留已在 curate 后自动把超过 `keep_days`（默认 7 天）的历史缓存清空，使 `radar.db` 长期有界。也可手动运维：

```bash
./run.sh admin db retain [--keep-days N] [--dry-run]  # 只清超窗口的历史 summary 缓存
./run.sh admin db slim   [--keep-days N] [--dry-run]  # 清缓存 + VACUUM 回收磁盘（仅低频磁盘维护）
```

`--dry-run` 零写、只报待清行数与字节。这两条命令的语义边界、`slim` 与 DB sync 的关系、以及回滚步骤见 [docs/operations/db-slimming.md](docs/operations/db-slimming.md)。

## 配置

### 站点身份与最小配置

`cp .env.example .env` 后，最少需要下面这些。前五个是**站点身份**：它们决定 `/about` 页展示什么、CORS 允许谁、以及 RSS 抓取时发出的 User-Agent，默认值适合 fork 后本地开发。

```bash
DEEPSEEK_API_KEY=sk-xxx
AI_RADAR_SITE_DOMAIN=                  # 未设置时仅允许 localhost CORS，User-Agent 为 ai-radar/0.1
AI_RADAR_SITE_REPO_URL=https://github.com/your-org/ai-radar
AI_RADAR_SITE_MAINTAINER=your-name
AI_RADAR_SITE_MAINTAINER_URL=
AI_RADAR_SITE_X_URL=
AI_RADAR_ENABLE_INTERPRET=false
AI_ASSISTANT_ROOT=
AI_RADAR_INTERPRET_USER=default
```

部署到公网时把 `AI_RADAR_SITE_DOMAIN` 设为你的域名（不带协议，例如 `example.com`），仓库链接与维护者链接改成你自己的：此时 CORS 会允许 `https://example.com`，抓取 User-Agent 会变为 `ai-radar/0.1 (+https://example.com)`。微信文章解读是可选外部集成，默认关闭；只有在你提供 ai-assistant 兼容实现时才设置 `AI_RADAR_ENABLE_INTERPRET=true` 和 `AI_ASSISTANT_ROOT`。

### 信源

信源池配置在 `data/sources.toml`，每个信源包含 slug、名称、URL、优先级层级（T1/T1.5/T2）等字段。`kind` 支持：

- `feed`：普通 RSS/Atom 信源
- `web`：没有可用原始 RSS/Atom 的官方网页或列表 API；每个来源使用代码登记的确定性解析器和允许范围，不做任意链接抓取
- `x`：X/Twitter 信源。`meta.adapter="x_api"` 的源通过 X API 读取原创帖子，不抓回复或转推；首次只看最近 20 分钟，之后以 checkpoint 增量读取，每轮每源只请求一页、`max_results=5`，繁忙账号通过持久 cursor 在后续轮次逐页排空，不做接入前历史回填；需要 `X_BEARER_TOKEN`。X RSS 源推荐显式声明 `meta.adapter="rss"`，未声明 adapter 的历史配置继续按 RSS 兼容读取
- `wechat`：微信公众号源。**两个来源并行运行、取并集**：托管的 [Mp2RSS](https://mp2rss.bugcode.dev/) 合集（源 `wx_mp2rss`，URL 用占位符 `${MP2RSS_FEED_URL}`）与自建的 Wechat2RSS 合集（源 `wx_wechat2rss`，占位符 `${WECHAT2RSS_FEED_URL}`）。feed URL 含专属密钥、不入库，loader 用 `os.path.expandvars` 展开；任一未设置时记录 warning、跳过该源并继续加载其余信源，两个都不设置也能跑。同一篇文章只入库一次（按公众号 + 归一化标题 + 5 分钟发布时间窗跨源去重）。文章卡片按 author 显示真实公众号名与头像。配置、跨源去重口径与运维见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md)

维护者实例当前配置了 161 个主站来源（109 个 X 账号、34 个原始 Feed、18 个原始 Web/API 列表）；两个微信来源另计，它们只服务「微信文章解读」，不进入精选、全部动态、搜索或策展。fork 后按自己的关注面增删即可，来源数量不是硬性契约。

X 的 20 分钟窗口只用于首次接入；空窗口提交时间 checkpoint，有帖子后改用 post ID checkpoint，积压由 cursor 按上述单轮上限排空——不做接入前历史回填，所以刚接入时 X 来源看起来「没产出」是预期行为。**不要用全量账号抓取做连通性测试**，先跑单源探针：

```bash
uv run python scripts/probe_x_source.py --source x_openai --db <全新临时数据库> --output <持久收据>
```

### 信源维护与验证

改信源不是只改 `data/sources.toml`——还牵动机器契约 fixture、Web/API 解析器登记、退休来源的身份连续性检查与几个审计脚本。完整规则与命令见 [docs/references/source-maintenance.md](docs/references/source-maintenance.md)。

配置 reload 只禁用已移除来源、保留历史 SQLite 行；所有公开 source/timeline/search/selected/wechat 消费面会过滤 disabled 行。

### AIHOT 私有基准集

AIHOT benchmark 数据位于 private submodule `benchmarks/aihot`；主仓只保存工具、冻结 schema 和 gitlink，不保存 AIHOT raw、JSONL、标题或 URL 内容。使用者须先取得 AI Radar 主仓，并让用于 Git submodule 的 GitHub SSH 身份获得 `lindong28/ai-radar-data` 读取权限，再递归克隆；已有 checkout 可单独初始化该 submodule：

```bash
git clone --recurse-submodules <AI Radar repository URL>
git submodule update --init --recursive benchmarks/aihot
```

采集、离线切窗与验收共用同一 CLI。`capture` 必须从 clean、已固定的工具 commit 运行，输出根必须是 data submodule，并严格遵守 AIHOT 的 30 requests/minute 与 `Retry-After`；`slice` 和 `validate` 只读已保存的 raw/schema，不依赖 AIHOT 继续在线：

```bash
uv run python scripts/capture_aihot_dataset.py capture --start 2026-08-19T00:00:00Z --end 2026-08-21T00:00:00Z --output-root benchmarks/aihot
uv run python scripts/capture_aihot_dataset.py slice --capture benchmarks/aihot/captures/<capture-id>/capture.json --start 2026-08-19T00:00:00Z --end 2026-08-20T00:00:00Z --output <output-path>
uv run python scripts/capture_aihot_dataset.py validate --report-json benchmarks/aihot/windows/2026-08-19T000000Z--2026-08-20T000000Z/manifest.json
```

当前首个基准提交固定两个半开 UTC 日窗口：`[2026-08-19T00:00Z, 2026-08-20T00:00Z)` 共 348 条，`[2026-08-20T00:00Z, 2026-08-21T00:00Z)` 共 330 条。capture 保存两遍一致的公开 API 观察、SSR 标签证据、冻结 schema bytes/hash 与离线重放所需 raw；它的“完整”只覆盖采集时刻可见的 AIHOT public-surface baseline，不表示 AIHOT 内部 snapshot、筛选或排序实现等价。AIHOT live 仅保留约 7 天，因此 data commit 是窗口过期后的可复现 authority。

代码与数据采用双仓提交：先在 private data 仓提交并经显式许可 push，使 exact data SHA 可远端取得；主仓随后才记录对应 gitlink。任一 data push、远端配置、主仓 push 或主分支整合都各自经过适用的显式授权 gate。契约理由与边界见 [ADR-060](docs/adr/060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md)。

### 微信文章解读

`interpret` 阶段只处理启用的微信公众号源。**该外部集成默认关闭**：未设置 `AI_RADAR_ENABLE_INTERPRET=true` 时，`./run.sh interpret` 打印 `interpret skipped=true` 并成功退出，不读取任何外部路径。

启用时需设置 `AI_ASSISTANT_ROOT=/path/to/ai-assistant-compatible-root`，可用 `AI_RADAR_INTERPRET_USER` 指定外部知识库 user（默认 `default`）。被判定值得保存的文章展示到 `/wechat` 并回写外部知识库，其余只在 `radar.db` 留处理记录。`/wechat` 支持 `?q=` 搜索解读卡片字段（标题、公众号 author、abstract、tags），分页和详情页返回链接会保留搜索状态；网站请求只读 `data/radar.db`，不依赖 ai-assistant 文件系统。脚本 I/O 契约与启用条件见 [`docs/references/ai-assistant-contract.md`](docs/references/ai-assistant-contract.md)，运维细节见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md#微信文章解读与知识库回写)。

### LLM Provider

通过环境变量选择使用的 LLM 后端：

```bash
AI_RADAR_PREFILTER=deepseek_v32     # prefilter 阶段（默认值）
AI_RADAR_SCORER=deepseek_v4_flash   # scoring 阶段（默认值；另可选 deepseek_v4_pro）
AI_RADAR_ENRICHER=deepseek_v4_pro   # enrichment 阶段（默认值）
```

上面写的就是各阶段不设置时的默认后端，**三个默认后端都只认 `DEEPSEEK_API_KEY` 或 `ARK_API_KEY`**（两个都设置时先试 ARK、失败再落回 DeepSeek）。`OPENAI_API_KEY` 与 `GLM_API_KEY` 只服务特定备选后端，必须显式改变量才会被读到：`AI_RADAR_SCORER=codex_gpt_mini` 用 `OPENAI_API_KEY`，`AI_RADAR_PREFILTER=glm` 用 `GLM_API_KEY`。`enrich` 只有 deepseek 系实现，缺 key 时该阶段直接失败（`DEEPSEEK_API_KEY or ARK_API_KEY is required for DeepSeek provider`）。

**`prefilter` 与 `score` 缺 key 时不会失败，而是逐条静默退回内置的纯规则打分**（也可用 `AI_RADAR_FORCE_HEURISTIC=1` 强制），质量不代表启用 LLM 的结果。它的输出读数（`processed=… errors=…`）与走 LLM 时完全相同，日志里也没有区分标记——**要判断这一轮是不是真的走了 LLM，只能确认 key 存在**：

```bash
grep -c '^DEEPSEEK_API_KEY=.\|^ARK_API_KEY=.' .env   # 返回 0 表示这两个阶段跑的是纯规则
```

（key 也可能来自进程环境或 `~/.claude/.env`，这几处都要看过才算确认。）

LLM 用量写入独立 SQLite 文件 `data/llm_usage.db`（可用 `AI_RADAR_LLM_USAGE_DB` 覆盖）的 `llm_usage` 表。**页面、周报和告警里的金额是按已加载 tariff 派生的记录行估算，不是账单**，各项合计只是全部付费调用的下界。这项口径的完整定义、命令与当前已知缺口见 [监控与告警 runbook 的 LLM 成本段](docs/operations/monitoring-alerting.md#llm-成本报表与对账)，规范 owner 见 [ADR-023](docs/adr/023-define-recorded-row-measurement-scope.md)。

## 测试

```bash
./test.sh
```

`test.sh` 就是 `uv run python -m pytest tests -v`（额外参数会透传），只跑 Python 测试；仓内当前收集到约 1800 条用例（2026-08-20 的 `--collect-only` 读数为 1835），预期全绿。

套件里带 `integration` 标记的用例会访问外部服务，无网或未配 key 时会失败（当前 2 条）——排除它们跑：

```bash
./test.sh -m 'not integration'
```

## 服务

下表只列**长期在后台运行**的服务（一次性 CLI 不在此列）。

| 服务 | 作用 |
|---|---|
| `serve` | FastAPI web server，默认 :8000 |
| `tunnel` | Cloudflare tunnel，把本机 serve 暴露到你的公网域名 |
| `pipeline` | 增量 fetch / prefilter / score / enrich / curate / interpret |
| `alert` | 定期执行 `admin alert-check`；A1–A7 使用 severity lifecycle，D3 定价提醒独立去重 |
| `performance-probe` | 可选的同机用户旅程性能探针（见上文） |
| `cost-report` | 定期发送上一自然周的 LLM 成本报表 |
| `performance-remediate` | 探针确认退化后生成仅供人工审阅的候选修复 commit |
| DB sync | 把主库同步到只读副本主机（维护者实例用；单机部署不需要） |
| Wechat2RSS | 自建微信公众号 feed 服务，用 `docker compose` 起在 `127.0.0.1:8080`，供 `WECHAT2RSS_FEED_URL` 消费；**配这个变量前先把它起起来**。bring-up 与运维见 [`deploy/wechat2rss/RUNBOOK.md`](deploy/wechat2rss/RUNBOOK.md) |

**每个服务当前装没装、跑在哪个端口、排期是多少，权威在 [`docs/operations/services.md`](docs/operations/services.md)**——这张表只说明各服务是干什么的。

X 的图片还需要一条出口代理：`.env` 未配 `AI_RADAR_IMG_PROXY_URL` 时 `/img` 对 `pbs.twimg.com` 直接返回 404（这是有意的快速失败，退回直连会让每张图挂满超时）。维护者实例的产线拓扑与诊断顺序见 [`docs/operations/services.md`](docs/operations/services.md)。

### 部署 / 移除 / 查状态

```bash
./install.sh   <service>   # 部署 + 启动指定服务；当前不要用无参数全量安装
./status.sh    [service]   # 只读面板；不修改任何状态
./uninstall.sh [service]   # 注销 supervisor，停服务，保留数据/日志
```

服务名是位置参数（`serve` / `tunnel` / `pipeline` / `alert` / `performance-probe` / `cost-report`）。**显式逐个安装你需要的服务**，不要用无参数全量安装。单服务安装幂等——重复跑不报错。DB sync 与 Wechat2RSS 不由这三个脚本管理。

`./install.sh` 会先检查该服务脚本可判定的依赖（LLM key、飞书 webhook、`im-notify`、tunnel 配置文件等），缺失时在交互式终端询问并追加到 `./.env`、非交互环境自动跳过，跳过原因列在命令末尾的 summary 里。变量按当前进程环境、项目 `./.env`、`~/.claude/.env` 依次查找。Playwright Chromium 是 `install.sh` **不会**自动下载或校验的运行时前置，按快速开始那一步先装好。逐服务的依赖清单、隐含依赖与验证命令见 [`docs/operations/services.md`](docs/operations/services.md)。

pipeline 所在主机还必须先由 system-config 安装并启用 healthy `domain-routing-v1` selector；AI Radar 的 installer 不创建、不切换也不修复这项外部服务。部署前先跑 `./run.sh egress-preflight`，看到 `status=healthy` 与 policy identity 后再安装 pipeline；这只验证应用可接受机器状态，不等于真实 GCP/Tencent 出口与断线行为已经在该主机验收。

`/admin`、A1–A7 与 D3 告警 runbook 见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md)。微信公众号摄取（两个来源的接入、头像 backfill、文章解读、KB 回写）见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md)。架构、设计决策记录与待办清单等开发者细节都在 [`docs/`](docs/)。

## 部署

`./install.sh` 覆盖服务的注册与启动（见上）。此外需要一次性的配置：

### Cloudflare Tunnel

```bash
cp deploy/cloudflared/config.yml.example deploy/cloudflared/config.yml
# 编辑 config.yml 填入 tunnel UUID 和域名
```

起完之后按 [`docs/operations/services.md`](docs/operations/services.md) 的「验证（新机器 bring-up / 大改动后跑一遍）」一节确认公网入口确实打得开本机 serve，而不是只看 tunnel 进程活着。同一台机器上的 tunnel 常同时承载别的站点，ingress 规则的写法见同文档「Cloudflare tunnel shared ingress」。

### 运维监控

公网 `/admin` 需要一层边缘访问控制（Cloudflare Access application + policy 之类）。**origin 只检查 Access header 存在、不验签**，所以这条边界必须由边缘提供；当前维护者实例的已知限制记在 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md)。飞书告警需要从 `ai-agent-config` 安装 `im-notify`，并在项目 `.env` 或 `~/.claude/.env` 同时配置 `FEISHU_GENERAL_ALERT_WEBHOOK`（page → `ALERT`）与 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK`（notice → `NOTIFICATION`）。具体步骤及无真实发送的配置 preflight 见同一份 runbook。

### 边缘缓存与前端资源版本串

serve 会自动对公开分页路径（`/`、`/wechat` 及其 API）的安全变体发 `Cache-Control` 缓存头——无需任何配置。是否真的从边缘命中，取决于你在前面挂了什么。

维护者实例当前的边缘层是 **EdgeOne**（`news.aiplanet.live` 以 DNS-only CNAME 接入，见 [ADR-039](docs/adr/039-route-news-through-edgeone-dns-only-cname.md)）。它对 `/app.js` 与 `/style.css` 两个精确路径强制节点缓存 7 天。

因此改前端资产（`web/static/app.js` / `web/static/style.css`）后必须重算 `?v=` 版本串（`uv run python scripts/bump_frontend_assets.py`），并在部署前用 `./run.sh admin edgeone check` 核对边缘强制缓存规则有没有漂移——它的退出码 `0`=无漂移、`1`=有漂移、`2`=**未核实（不等于通过）**。漏做就是「部署了但线上不生效」；完整纪律见项目 [`CLAUDE.md`](CLAUDE.md) 的「Frontend Asset Cache Busting」，凭据配置与 purge 步骤见 [`docs/operations/services.md`](docs/operations/services.md) 的「EdgeOne 节点缓存规则对账与 purge」。

（前端 app.js 会自动预取下一页，这是纯客户端优化，与任何边缘缓存独立。）

Cloudflare Cache Rule 是一条**旁路拓扑**——当前生产不在它的路径上。若你把站点挂在 Cloudflare 后面，规则表达式、Edge TTL 设置和 `CF-Cache-Status: HIT` 验证步骤见 [`docs/operations/services.md`](docs/operations/services.md) 的「Cloudflare Cache Rule」节。

### 直接用 uvicorn 运行

项目本身是标准 FastAPI 应用，可不走 launchd 直接起（仓库不提供应用镜像或 Dockerfile，容器化需要自己写）：

```bash
uv run uvicorn airadar.web.app:app --host 0.0.0.0 --port 8000
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/healthz   # 期望 200
```

## 致谢

AI Radar 的 UX 设计和创意灵感来自 [AIHOT](http://aihot.virxact.com/)（[项目介绍](https://mp.weixin.qq.com/s/r6CE2U3Y0-pU05wF3_PuTQ)）。本项目在时间线、精选和日报的形态上借鉴了 AIHOT 的设计理念。

## License

[MIT](LICENSE)
