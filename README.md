# AI Radar

AI Radar 是一个公开只读的 AI 信息流站点。它从 RSS、X 和微信公众号信源抓取 AI 相关内容，经过 LLM 筛选、评分、翻译后，以时间线形式呈现精选内容。

## 快速开始

### 1. 环境准备

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/your-org/ai-radar.git
cd ai-radar
uv sync
uv run playwright install chromium  # 微信抓取必需；启用 performance-probe 时也复用它
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入至少一个 LLM API Key（DeepSeek、OpenAI、GLM 或 ARK 任选其一）：

```
DEEPSEEK_API_KEY=sk-xxx
```

其他配置项均有默认值，详见 `.env.example` 中的注释。第一次本地试跑可以先保留站点身份默认值；如果暂时没有 Mp2RSS 合集 feed，可以不设置 `MP2RSS_FEED_URL`，loader 会跳过 `wx_mp2rss` 并继续加载其他信源。

### 3. 初始化数据库

```bash
./run.sh admin db migrate
./run.sh admin sources reload
```

### 4. 运行数据处理流水线

```bash
./run.sh fetch       # 从 RSS/X/微信公众号信源抓取内容
./run.sh prefilter   # LLM 筛选 AI 相关内容
./run.sh score       # 五维评分
./run.sh enrich      # LLM 生成中文标题和摘要
./run.sh curate      # 精选高价值内容
./run.sh interpret   # 可选：微信文章解读 + ai-assistant 兼容知识库回写（默认关闭）
```

### 5. 启动 Web 服务

```bash
./run.sh serve --host 127.0.0.1 --port 8000
```

访问 `http://localhost:8000` 查看站点。

## 自动化调度

`pipeline.sh` 按顺序执行 `fetch → prefilter → score → enrich → curate → interpret`，每个阶段只处理尚未评估的新条目。单阶段失败会记录 `FAIL` 后继续，日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`，`.pipeline.lock` 跳过重叠运行。

默认 cron 频率是 `*/15 * * * *`，即每 15 分钟执行一次。

cron / launchd 不继承交互式 shell 的 `export` 变量——启用自动调度前确认项目根目录 `.env`、`~/.claude/.env` 或 supervisor 环境已配 LLM API Key。

```bash
./pipeline.sh             # 手动跑一次
./install.sh pipeline     # 注册到 user crontab，每 15 分钟一次
```

调度方式详情、launchd 备选模板见 §服务 + [docs/operations/services.md](docs/operations/services.md)。

不要把 `deploy/cron/ai-radar-pipeline` 或其展开结果直接送入 `crontab -`：这会替换该用户的整份 crontab，并删除 DB sync、cost-report 等无关排期。安装和更新 pipeline 排期统一使用 `./install.sh pipeline`。

### 用户旅程性能监控与候选修复

`performance-probe` 每次用浏览器从同机 origin 与同机 public（由 `AI_RADAR_PUBLIC_URL` 环境变量配置；未配置时跳过 public 视角、仅测 origin）两个视角测量首页首卡、微信列表首卡、微信详情可读和微信翻页稳定四条旅程。探针只在单条旅程测量前后都确认 pipeline 空闲时保存该样本；pipeline 正在运行或负载状态不确定时跳过该次旅程尝试，不让 non-idle 输入进入 PERF 窗口。每个旅程/视角保留 20+3 确认窗，首个 confirmed firing 需要 22 条有效 idle 样本，超预算后以 `page` 投递。所有结果均为 **same-host provisional**，不代表区域 SLO。确认退化后，`performance-remediate` 会在隔离 worktree 中启动一个 fail-closed Codex worker，最多生成一个仅供人工审阅的本地候选 commit；它不会 push、deploy、调用 launchctl 或写生产数据库。

先核对两个 CLI，并只手工运行 probe 来确认浏览器与两个站点视角可用；这不会证明告警 sender、webhook 或实际投递通道可用，告警配置的无发送 preflight 见 [监控与告警 runbook](docs/operations/monitoring-alerting.md#im-notify-飞书双通道)：

```bash
./run.sh performance-probe --help
./run.sh performance-remediate --help
./run.sh performance-probe --origin-url http://127.0.0.1:8010 --public-url https://news.aiplanet.live
```

`performance-probe` 由 `install.sh` 管理：安装后，专属 per-file LaunchAgent 以 `StartInterval=300` 每 5 分钟经 `./run.sh performance-probe` 启动，因此外部超时 watchdog 始终位于启动路径；pipeline 自身仍保留既有 `*/15` user crontab。当前部署未安装 probe，旧 hourly cron 保持 PAUSED；安装、查询和卸载 probe：

```bash
./install.sh performance-probe
./status.sh performance-probe
./uninstall.sh performance-probe
```

homepage `hard_failure=true` 的已知假阳性已修复。但 `performance-remediate` 仍不由 `install.sh` 管理并受运维 gate 约束：部署后按 [监控 runbook 的可执行 gate](docs/operations/monitoring-alerting.md#安装-5-分钟-launchd-调度) 确认 homepage 最新 idle 样本没有 hard failure、homepage `PERF:*` page lifecycle 不在 firing；同一 fail-fast 流程会先手工运行 remediation，成功后才安装它自己的 cron。

规则、预算、证据保留和处置边界见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md#用户旅程性能监控)。

## Web 页面

| 页面 | URL | 说明 |
|------|-----|------|
| 精选 | `/` | 高评分精选内容，按日期分组（可折叠），顶部为近 48 小时热点榜（2 条 + 「完整榜单 →」），无限下拉加载 |
| 全部 AI 动态 | `/all` | 完整时间线，最新优先，无限下拉加载（搜索态用页码分页） |
| 热点榜 | `/hot` | 近 48 小时热点完整榜单（热度 = 加权分×10 + 关联讨论×5）；桌面从侧栏进入，移动端从首页「完整榜单 →」进入 |
| 微信文章解读 | `/wechat` | 已订阅微信公众号文章的结构化总结，支持按标题、公众号、摘要和标签搜索；详情页为 `/wechat/<slug>` |
| AI 日报 | `/daily` | 每日精选归档，按月份分组的可折叠归档栏 + 今日看点，支持 `?date=YYYY-MM-DD` |
| 收藏 | `/bookmarks` | 本设备浏览器收藏的内容（localStorage），支持导出/导入 JSON |
| 关于 | `/about` | 项目介绍和信源池 |
| 更新日志 | `/changelog` | 渲染仓库根 `CHANGELOG.md` |
| 更多 | `/more` | **仅 ≤960px 有入口**（底部 tab 栏第 4 项）：微信文章解读 / 收藏 / 关于 / 更新日志 |
| 运维监控 | `/admin` | 用户量、文章摄取、pipeline 阶段健康与当前告警；公网需 Cloudflare Access |
| LLM 已记录用量 | `/admin/usage` | 内部页面，展示最近 30 天 `llm_usage` 记录行的成本三态、来源单价、未定价清单和 cache 采集覆盖；公网需 Cloudflare Access |

**响应式**：断点 640 / 960 / 1200px。`>960px` 为侧栏 + 内容区的桌面布局，内容区填满可用宽度；日期分组头与卡片共用同一套网格轨道（日期右对齐落在时间列内，与下方时间戳共一条右边界），一条连续竖线贯穿同一日期分组的全部条目。

`≤960px` 侧栏整体替换为底部 4 项 tab 栏（精选 / 全部 / 日报 / 更多），分类筛选改横向滚动药丸 chip，卡片全出血，日期分组头吸顶并分两段呈现（主段更大更重）。该档信息密度向紧凑收敛：行首只有时间 + 信源（左）与分数（右），不显示精选标记与收藏按钮（两者在桌面档保留）；日期分组头不提供折叠控件；顶部紧凑条在首页出现，`/all`、`/hot`、`/daily`、`/changelog` 无顶部条。**首页与 `/all` 卡片上的收藏按钮只在 `>960px` 显示**——`≤960px` 可经「更多 → 收藏」查看、取消、导出与导入（导入在任何档位都能新增收藏），但列表卡片上没有收藏按钮。话题标签在主信息流中只出现于「全部 AI 动态」页。

主题支持浅色 / 深色 / 跟随系统三态，当前档由滑动选中底板指示。

## 数据流水线

```
RSS / X / Mp2RSS 微信公众号源 → fetch → prefilter → score → enrich → curate → interpret → web 展示 / ai-assistant KB
```

各阶段说明：

- **fetch** — 从 `data/sources.toml` 中配置的 RSS/X/微信公众号信源抓取内容
- **prefilter** — 使用 LLM 或规则引擎过滤 AI 相关内容
- **score** — 五维评分（relevance、density、recency、authority、engineering）
- **enrich** — LLM 生成中文标题和摘要
- **curate** — 按阈值精选高价值内容（默认阈值 6.5）
- **interpret** — 可选阶段，默认关闭；启用后对微信公众号文章调用 ai-assistant 兼容的 summary-agent 脚本，保存独立网站解读数据，并把值得阅读的文章回写外部知识库

### 数据库维护

精选 digest 的预计算缓存（`curated_items.summary_json`）会随每次 curate 增长；常驻保留已在 curate 后自动把超过 `keep_days`（默认 7 天）的历史缓存清空，使 `radar.db` 长期有界。也可手动运维：

```bash
./run.sh admin db retain [--keep-days N] [--dry-run]  # 只清超窗口的历史 summary 缓存
./run.sh admin db slim   [--keep-days N] [--dry-run]  # 清缓存 + VACUUM 回收磁盘（仅低频磁盘维护）
```

`--dry-run` 零写、只报待清行数与字节。`slim` 显式返回 `retained`/`compacted` 两阶段结果；它只服务本机低频磁盘回收，独立于 DB sync——同步链路自行从 live 库取一致快照做逻辑增量，对 live DB 手动 VACUUM 不会改善它。瘦身细节见 [docs/operations/db-slimming.md](docs/operations/db-slimming.md)；同步机制、职责与故障证据见 [docs/operations/services.md](docs/operations/services.md#db-sync-职责验证与故障证据) 与 [docs/architecture.md](docs/architecture.md)。

## 配置

### 从零部署最小配置

`cp .env.example .env` 后，最少需要：

```bash
DEEPSEEK_API_KEY=sk-xxx
AI_RADAR_SITE_DOMAIN=                  # 本地开发可留空
AI_RADAR_SITE_REPO_URL=https://github.com/your-org/ai-radar
AI_RADAR_SITE_MAINTAINER=your-name
AI_RADAR_SITE_MAINTAINER_URL=
AI_RADAR_SITE_X_URL=
AI_RADAR_ENABLE_INTERPRET=false
AI_ASSISTANT_ROOT=
AI_RADAR_INTERPRET_USER=default
```

公网部署时把 `AI_RADAR_SITE_DOMAIN`、仓库链接和维护者链接改成你自己的值。微信文章解读是可选外部集成，默认关闭；只有在你提供 ai-assistant 兼容实现时才设置 `AI_RADAR_ENABLE_INTERPRET=true` 和 `AI_ASSISTANT_ROOT`。

### 站点身份与域名

`/about`、CORS 和 RSS 抓取 User-Agent 由以下环境变量控制，默认值适合 fork 后本地开发：

```bash
AI_RADAR_SITE_DOMAIN=                 # 未设置时仅允许 localhost CORS，User-Agent 为 ai-radar/0.1
AI_RADAR_SITE_REPO_URL=https://github.com/your-org/ai-radar
AI_RADAR_SITE_MAINTAINER=your-name
AI_RADAR_SITE_MAINTAINER_URL=
AI_RADAR_SITE_X_URL=
```

部署到公网时，将 `AI_RADAR_SITE_DOMAIN` 设置为你的域名（不带协议即可，例如 `example.com`）。此时 CORS 会允许 `https://example.com`，抓取 User-Agent 会变为 `ai-radar/0.1 (+https://example.com)`。

### 信源

信源池配置在 `data/sources.toml`，每个信源包含 slug、名称、URL、优先级层级（T1/T1.5/T2）等字段。`kind` 支持：

- `feed`：普通 RSS/Atom 信源
- `x`：X/Twitter 导出的 RSS 信源，前端允许展示完整 thread
- `wechat`：微信公众号源，通过托管的 [Mp2RSS](https://mp2rss.com/) 合集 feed 接入（已替代自建 WeWe RSS）。合集源 `wx_mp2rss` 的 URL 用环境变量占位符 `${MP2RSS_FEED_URL}`（feed URL 含专属密钥，不入库；loader 用 `os.path.expandvars` 展开）。未设置或设置为空时，loader 会记录 warning、跳过该源，并继续加载其他信源；设置 `MP2RSS_FEED_URL` 后该源自动启用。文章卡片按 author 显示真实公众号名与头像。配置和运维记录见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md)

### 微信文章解读

`interpret` 阶段只处理启用的微信公众号源（当前 `wx_mp2rss`）。该外部集成默认关闭：未设置 `AI_RADAR_ENABLE_INTERPRET=true` 时，`./run.sh interpret` 会输出 skipped 并成功退出，不读取任何外部路径。

启用时需设置 `AI_ASSISTANT_ROOT=/path/to/ai-assistant-compatible-root`，并可用 `AI_RADAR_INTERPRET_USER` 指定外部知识库 user（默认 `default`）。AI Radar 会调用 `$AI_ASSISTANT_ROOT/agents/summary-agent/summarize.sh` / `run.sh`，调用 summarizer 时显式传入 `--model ai-radar-interpret-deepseek`，以便 interpret 单独走 ARK 优先、DeepSeek 官方 fallback 的路由；`save_decision=1` 的文章展示到 `/wechat` 并回写外部知识库；`save_decision=0` 的文章只在 `radar.db` 留处理记录，不上站点、不写 KB。`/wechat` 支持 `?q=` 搜索解读卡片字段（标题、公众号 author、abstract、tags），分页和详情页返回链接会保留搜索状态。网站请求只读 `data/radar.db`，不依赖 ai-assistant 文件系统。脚本 I/O 契约见 [`docs/operations/ai-assistant-integration.md`](docs/operations/ai-assistant-integration.md)，运维细节见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md#微信文章解读与知识库回写)。

### LLM Provider

通过环境变量选择使用的 LLM 后端：

```bash
AI_RADAR_PREFILTER=deepseek_v32   # prefilter 阶段
AI_RADAR_SCORER=deepseek_v4_pro   # scoring 阶段
AI_RADAR_ENRICHER=deepseek_v4_pro # enrichment 阶段
```

也支持 `heuristics` 作为无 LLM 的纯规则后备方案。

LLM 用量写入独立 SQLite 文件 `data/llm_usage.db`（可用 `AI_RADAR_LLM_USAGE_DB` 覆盖）的 `llm_usage` 表。页面、周报和告警中的金额是按已加载 tariff 在查询时派生的记录行估算，不是 provider 账单或实际付款；调用数、token 合计与同一计价口径的金额合计是全部相关付费调用对应总量的下界，均值、占比和环比只描述 recorded cohort，不能推断相对全部调用真值的偏差方向。`/admin/usage`、`./run.sh admin cost-report --dry-run` 与 `./run.sh admin cost-audit [--format=kv|json]` 都携带或展示这项 scope。完整运行口径与命令见 [监控与告警 runbook 的 LLM 成本段](docs/operations/monitoring-alerting.md#llm-成本报表与对账)，规范 owner 见 [ADR-023](docs/adr/023-define-recorded-row-measurement-scope.md)；ARK tariff/订阅权威性与付费 attempt 漏行分别由 [ISSUE-004](docs/issues/cost-observability.md#issue-004--ark-挂牌价来源非权威而它占已知成本的-876) 和 [ISSUE-021](docs/issues/cost-observability.md#issue-021--interpret-usage-只记录下游成功样本漏掉已计费的失败响应) 保持开放。

## 测试

```bash
./test.sh
```

## 服务

下表只列**长期在后台运行**的服务（一次性 CLI 不在此列）。

| 服务 | Supervisor | 作用 |
|---|---|---|
| `serve` | launchd | FastAPI web server（fork 默认 :8000；本产线 Mac 实例绑 8010 仅作局域网预览，公网生产由腾讯服务器上的现存进程承载 `news.aiplanet.live`；repo-owned 双槽 unit 当前未安装，服务所有权清单仍待补齐） |
| `tunnel` | launchd | Cloudflare tunnel 到你的公网域名（本产线旧 `aiplanet.live` 入口已退役待域名下线；tunnel 仍承载同机其他站点） |
| `pipeline` | cron | 每 15 分钟增量 fetch / prefilter / score / enrich / curate / interpret |
| `alert` | launchd, StartInterval=300 | 每 5 分钟执行 `admin alert-check`；A1–A6 使用 severity lifecycle，D3 定价提醒独立去重 |
| `performance-probe` | launchd, StartInterval=300 | 当前未安装、旧 hourly cron 保持 PAUSED；恢复前先处理 [ISSUE-017](docs/issues/cost-observability.md#issue-017--performance-probe-默认-origin-仍假定-serve-在-8000)，安装后才每 5 分钟运行 |
| `cost-report` | cron (`17 9 * * 1`) | 周一 09:17 经 `run-or-alert` 发送上一上海自然周 LLM 成本报表 |
| `performance-remediate` | cron（建议每小时 :25） | 候选修复功能已交付；homepage 误标缺陷已修复，但仍须在部署后确认 `hard_failure=false` 且 homepage `PERF:*` 非 firing，才可在 probe 后启用 |
| `DB sync → 腾讯服务器` | cron（当前 5 小时级，最终频率待验证） | Mac producer 同步 base-only 副本并等服务器重建/验证到 `committed`；公网副本新鲜度的生产入口，排期与细节见 [services.md](docs/operations/services.md#db-sync-职责验证与故障证据) |

### 部署 / 移除 / 查状态

```bash
./install.sh   <service>   # 部署 + 启动指定服务；当前不要用无参数全量安装
./status.sh    [service]   # 只读面板；不修改任何状态
./uninstall.sh [service]   # 注销 supervisor，停服务，保留数据/日志
```

服务名是位置参数（`serve` / `tunnel` / `pipeline` / `alert` / `performance-probe` / `cost-report`）。脚本本身仍支持不带参数作用于全部，但当前 `performance-probe` 因 [ISSUE-017](docs/issues/cost-observability.md#issue-017--performance-probe-默认-origin-仍假定-serve-在-8000) 保持停用，所以不要使用无参数全量安装；显式逐个安装需要的服务。单服务安装幂等——重复跑不报错。

DB sync cron 不由上述三个通用生命周期脚本管理；用 `crontab -l` 核对排期，以 `deploy/sync/sync-db-cron.sh` 手动走完整 cron wrapper。详见 [服务清单的 DB sync runbook](docs/operations/services.md#db-sync-职责验证与故障证据)。

`./install.sh` 会检查各服务能由脚本判定的依赖；Playwright Chromium 是需按快速开始步骤显式安装的运行时前置：

| 服务 | 依赖 | 缺失时 |
|---|---|---|
| `serve` | 无 | 始终安装 |
| `pipeline` | 至少一个 LLM key：`DEEPSEEK_API_KEY` / `ARK_API_KEY` / `OPENAI_API_KEY` / `GLM_API_KEY` | 交互式终端会询问 `DEEPSEEK_API_KEY` 并追加到 `./.env`；非交互环境自动跳过 |
| `alert` | `~/.local/bin/im-notify` + `FEISHU_GENERAL_ALERT_WEBHOOK` + `FEISHU_GENERAL_NOTIFICATION_WEBHOOK` | 先从 `ai-agent-config` 安装 `im-notify`；两个 webhook 分别承接 page/notice，任缺一个都拒绝部分安装。交互式终端逐个询问并追加到 `./.env`，非交互环境自动跳过 |
| `tunnel` | `deploy/cloudflared/config.yml` | 提示从 `deploy/cloudflared/config.yml.example` 创建自己的 Cloudflare tunnel 配置，本次跳过 |
| `performance-probe` | Playwright Chromium | 安装服务前先显式运行 `uv run playwright install chromium`；该浏览器同时供微信抓取使用，`install.sh` 不自动下载或校验 |
| `cost-report` | `im-notify`、`run-or-alert`、`FEISHU_GENERAL_NOTIFICATION_WEBHOOK` | installer 只检查 webhook；当前不验证两个可执行文件，安装前按 [runbook](docs/operations/monitoring-alerting.md#llm-成本报表与对账) 的 preflight 核对（ISSUE-014） |

脚本可判定的环境变量依赖按当前进程环境、项目 `./.env`、`~/.claude/.env` 查找。因此已有密钥放在 `~/.claude/.env` 的本机部署不会出现提示。任何自动跳过都会在命令末尾的 summary 中列出原因。

完整运维细节（验证命令、隐含依赖、各服务 instructions 链接）见 [`docs/operations/services.md`](docs/operations/services.md)。`/admin`、A1–A6 与 D3 告警 runbook 见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md)。微信公众号源（Mp2RSS 接入、头像 backfill、文章解读、KB 回写）见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md)；旧 WeWe RSS 桥接已从服务层移除，不再作为发布快照的一部分维护。

## 部署

`./install.sh` 覆盖服务的注册与启动（见上）。此外需要一次性的配置：

### Cloudflare Tunnel

```bash
cp deploy/cloudflared/config.yml.example deploy/cloudflared/config.yml
# 编辑 config.yml 填入 tunnel UUID 和域名
```

### 运维监控

目标拓扑中的公网 `/admin` 需要 Cloudflare Access application + policy；当前生产 hostname 直达 origin，伪造非空 Access header 可绕过存在性检查，因此尚未具备这条认证边界，开放修复见 [deploy issue](docs/issues/deploy.md#open-2026-08-12当前生产-admin-入口绕过-cloudflare-access)。飞书告警需要从 `ai-agent-config` 安装 `im-notify`，并在项目 `.env` 或 `~/.claude/.env` 同时配置 `FEISHU_GENERAL_ALERT_WEBHOOK`（page → `ALERT`）与 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK`（notice → `NOTIFICATION`）。告警状态机按 severity 分别负责 debounce、cooldown 与恢复通知。具体步骤及无真实发送的配置 preflight 见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md)。

### Cloudflare 边缘缓存（可选）

serve 会自动对公开分页路径（`/`、`/wechat` 及其 API）的安全变体（HTML 只认 `page`，API 认 `page`+`limit`）发 `Cache-Control` 缓存头——无需任何配置。要让翻页真正从 Cloudflare 边缘直接命中而非每次回源，还需在 Cloudflare dashboard 手动建一条名为 `AI Radar short public pagination TTL` 的 Cache Rule（origin 头是它的配套，缺一则边缘缓存不生效）。（前端 app.js 另会自动预取下一页，这是纯客户端优化，与 Cloudflare 缓存独立、同样无需配置。）规则表达式、Edge TTL 设置和 `CF-Cache-Status: HIT` 验证步骤见 [`docs/operations/services.md`](docs/operations/services.md) 的「Cloudflare Cache Rule」节。

### Docker / 其他平台

项目本身是标准 FastAPI 应用，可不走 launchd 直接起：

```bash
uv run uvicorn airadar.web.app:app --host 0.0.0.0 --port 8000
```

## 致谢

AI Radar 的 UX 设计和创意灵感来自 [AIHOT](http://aihot.virxact.com/)（[项目介绍](https://mp.weixin.qq.com/s/r6CE2U3Y0-pU05wF3_PuTQ)）。本项目在时间线、精选和日报的形态上借鉴了 AIHOT 的设计理念，并计划将项目改造为每个人都可以根据自己的需求进行配置和部署的的个性化信息消费工具。

## License

[MIT](LICENSE)
