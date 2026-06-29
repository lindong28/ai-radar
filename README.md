# AI Radar

AI Radar 是一个公开只读的 AI 信息流站点。它从 RSS、X 和微信公众号信源抓取 AI 相关内容，经过 LLM 筛选、评分、翻译后，以时间线形式呈现精选内容。

## 快速开始

### 1. 环境准备

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/your-org/ai-radar.git
cd ai-radar
uv sync
uv run playwright install chromium  # 仅启用微信公众号抓取时需要
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

如需手动写入 cron，不要直接 `crontab deploy/cron/ai-radar-pipeline`，因为仓库内文件保留 `/path/to/ai-radar` 占位符。使用当前仓库路径展开后再写入：

```bash
sed "s|/path/to/ai-radar|$PWD|g" deploy/cron/ai-radar-pipeline | crontab -
```

## Web 页面

| 页面 | URL | 说明 |
|------|-----|------|
| 精选 | `/` | 高评分精选内容，按日期分组 |
| 全部 AI 动态 | `/all` | 完整时间线，最新优先 |
| 微信文章解读 | `/wechat` | 已订阅微信公众号文章的结构化总结，支持按标题、公众号、摘要和标签搜索；详情页为 `/wechat/<slug>` |
| AI 日报 | `/daily` | 每日精选归档，支持 `?date=YYYY-MM-DD` |
| 关于 | `/about` | 项目介绍和信源池 |
| 运维监控 | `/admin` | 用户量、文章摄取、pipeline 阶段健康与当前告警；公网需 Cloudflare Access |
| LLM 用量 | `/admin/usage` | 内部页面，展示最近 30 天 prefilter / score / enrich / interpret 的按天、按模型 token 用量与输入归因；公网需 Cloudflare Access |

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

DeepSeek / ARK 的 `chat_json` 调用，以及 interpret 透传的 summary-agent LLM usage，都会把 `completion.usage` 写入独立 SQLite 文件 `data/llm_usage.db`（可用 `AI_RADAR_LLM_USAGE_DB` 覆盖）中的 `llm_usage` 表，每次 LLM call 一行，记录阶段（prefilter / score / enrich / interpret）、provider、模型、input/output token、item_id 和输入字符规模。内部 `/admin/usage` 页面按查询时聚合展示最近 30 天用量；可选设置 `AI_RADAR_LLM_PRICING_JSON` 为模型提供 per-million-token 价格，用于估算成本。

## 测试

```bash
./test.sh
```

## 服务

下表只列**长期在后台运行**的服务（一次性 CLI 不在此列）。

| 服务 | Supervisor | 作用 |
|---|---|---|
| `serve` | launchd | FastAPI web server on :8000 |
| `tunnel` | launchd | Cloudflare tunnel 到你的公网域名 |
| `pipeline` | cron | 每 15 分钟增量 fetch / prefilter / score / enrich / curate / interpret |
| `alert` | launchd, StartInterval=300 | 每 5 分钟执行 `admin alert-check`，按 A1-A4 规则发送飞书告警 |

### 部署 / 移除 / 查状态

```bash
./install.sh   [service]   # 部署 + 启动；不带 service 则全部
./status.sh    [service]   # 只读面板；不修改任何状态
./uninstall.sh [service]   # 注销 supervisor，停服务，保留数据/日志
```

服务名是可选位置参数（`serve` / `tunnel` / `pipeline` / `alert`）；不带参数作用于全部。脚本幂等——重复跑不报错。

`./install.sh` 会在安装每个服务前检查依赖：

| 服务 | 依赖 | 缺失时 |
|---|---|---|
| `serve` | 无 | 始终安装 |
| `pipeline` | 至少一个 LLM key：`DEEPSEEK_API_KEY` / `ARK_API_KEY` / `OPENAI_API_KEY` / `GLM_API_KEY` | 交互式终端会询问 `DEEPSEEK_API_KEY` 并追加到 `./.env`；非交互环境自动跳过 |
| `alert` | `FEISHU_GENERAL_ALERT_WEBHOOK` | 交互式终端会询问 webhook 并追加到 `./.env`；非交互环境自动跳过 |
| `tunnel` | `deploy/cloudflared/config.yml` | 提示从 `deploy/cloudflared/config.yml.example` 创建自己的 Cloudflare tunnel 配置，本次跳过 |

依赖查找顺序是当前进程环境、项目 `./.env`、`~/.claude/.env`。因此已有密钥放在 `~/.claude/.env` 的本机部署不会出现提示。任何自动跳过都会在命令末尾的 summary 中列出原因。

完整运维细节（验证命令、隐含依赖、各服务 instructions 链接）见 [`docs/operations/services.md`](docs/operations/services.md)。`/admin` 与 A1-A4 告警 runbook 见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md)。微信公众号源（Mp2RSS 接入、头像 backfill、文章解读、KB 回写）见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md)；旧 WeWe RSS 桥接已从服务层移除，不再作为发布快照的一部分维护。

## 部署

`./install.sh` 覆盖服务的注册与启动（见上）。此外需要一次性的配置：

### Cloudflare Tunnel

```bash
cp deploy/cloudflared/config.yml.example deploy/cloudflared/config.yml
# 编辑 config.yml 填入 tunnel UUID 和域名
```

### 运维监控

公网 `/admin` 需要 Cloudflare Access application + policy；飞书告警需要配置 `FEISHU_GENERAL_ALERT_WEBHOOK`。具体步骤见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md)。

### Docker / 其他平台

项目本身是标准 FastAPI 应用，可不走 launchd 直接起：

```bash
uv run uvicorn airadar.web.app:app --host 0.0.0.0 --port 8000
```

## 致谢

AI Radar 的 UX 设计和创意灵感来自 [AIHOT](http://aihot.virxact.com/)（[项目介绍](https://mp.weixin.qq.com/s/r6CE2U3Y0-pU05wF3_PuTQ)）。本项目在时间线、精选和日报的形态上借鉴了 AIHOT 的设计理念，并计划将项目改造为每个人都可以根据自己的需求进行配置和部署的的个性化信息消费工具。

## License

[MIT](LICENSE)
