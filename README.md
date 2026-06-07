# AI Radar

AI Radar 是一个公开只读的 AI 信息流站点。它从 RSS、X 和微信公众号信源抓取 AI 相关内容，经过 LLM 筛选、评分、翻译后，以时间线形式呈现精选内容。

## 快速开始

### 1. 环境准备

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/lindong28/ai-radar.git
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

其他配置项均有默认值，详见 `.env.example` 中的注释。

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
./run.sh interpret   # 微信文章解读 + ai-assistant 知识库回写
```

### 5. 启动 Web 服务

```bash
./run.sh serve --host 127.0.0.1 --port 8000
```

访问 `http://localhost:8000` 查看站点。

## 自动化调度

`pipeline.sh` 按顺序执行 `fetch → prefilter → score → enrich → curate → interpret`，每个阶段只处理尚未评估的新条目。单阶段失败会记录 `FAIL` 后继续，日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`，`.pipeline.lock` 跳过重叠运行。

默认 cron 频率是 `*/15 * * * *`，即每 15 分钟执行一次。

cron / launchd 不继承交互式 shell 的 `export` 变量——启用自动调度前确认 `.env`（项目根目录或 `~/.claude/.env`）已配 LLM API Key。

```bash
./pipeline.sh             # 手动跑一次
./install.sh pipeline     # 注册到 user crontab，每 15 分钟一次
crontab deploy/cron/ai-radar-pipeline  # 手动加载 cron 条目
```

调度方式详情、`launchctl bootstrap` launchd 备选模板见 §服务 + [docs/operations/services.md](docs/operations/services.md)。

## Web 页面

| 页面 | URL | 说明 |
|------|-----|------|
| 精选 | `/` | 高评分精选内容，按日期分组 |
| 全部 AI 动态 | `/all` | 完整时间线，最新优先 |
| 微信文章解读 | `/wechat` | 已订阅微信公众号文章的结构化总结，支持按标题、公众号、摘要和标签搜索；详情页为 `/wechat/<slug>` |
| AI 日报 | `/daily` | 每日精选归档，支持 `?date=YYYY-MM-DD` |
| 关于 | `/about` | 项目介绍和信源池 |
| 运维监控 | `/admin` | 用户量、文章摄取、pipeline 阶段健康与当前告警；公网需 Cloudflare Access |

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
- **interpret** — 对已启用微信公众号文章调用 ai-assistant `summarize-article` 逻辑，保存独立网站解读数据，并把值得阅读的文章回写 ai-assistant 知识库

## 配置

### 信源

信源池配置在 `data/sources.toml`，每个信源包含 slug、名称、URL、优先级层级（T1/T1.5/T2）等字段。`kind` 支持：

- `feed`：普通 RSS/Atom 信源
- `x`：X/Twitter 导出的 RSS 信源，前端允许展示完整 thread
- `wechat`：微信公众号源，通过托管的 [Mp2RSS](https://mp2rss.com/) 合集 feed 接入（已替代自建 WeWe RSS）。合集源 `wx_mp2rss` 的 URL 用环境变量占位符 `${MP2RSS_FEED_URL}`（feed URL 含专属密钥，不入库；loader 用 `os.path.expandvars` 展开，未设置时启动报错）。文章卡片按 author 显示真实公众号名与头像。配置和运维记录见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md)

### 微信文章解读

`interpret` 阶段只处理启用的微信公众号源（当前 `wx_mp2rss`）。它通过 `AI_ASSISTANT_ROOT`（默认 `/Users/lindong/research/ai-assistant`）零拷贝调用 ai-assistant 的 `agents/summary-agent/summarize.sh` / `run.sh`，将 `save_decision=1` 的文章展示到 `/wechat` 并回写 ai-assistant 知识库；`save_decision=0` 的文章只在 `radar.db` 留处理记录，不上站点、不写 KB。`/wechat` 支持 `?q=` 搜索解读卡片字段（标题、公众号 author、abstract、tags），分页和详情页返回链接会保留搜索状态。网站请求只读 `data/radar.db`，不依赖 ai-assistant 文件系统。运维细节见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md#微信文章解读与知识库回写)。

### LLM Provider

通过环境变量选择使用的 LLM 后端：

```bash
AI_RADAR_PREFILTER=deepseek_v32   # prefilter 阶段
AI_RADAR_SCORER=deepseek_v4_pro   # scoring 阶段
AI_RADAR_ENRICHER=deepseek_v4_pro # enrichment 阶段
```

也支持 `heuristics` 作为无 LLM 的纯规则后备方案。

## 测试

```bash
./test.sh
```

## 服务

下表只列**长期在后台运行**的服务（一次性 CLI 不在此列）。

| 服务 | Supervisor | 作用 |
|---|---|---|
| `serve` | launchd | FastAPI web server on :8000 |
| `tunnel` | launchd | Cloudflare tunnel 到 aiplanet.live |
| `pipeline` | cron | 每 15 分钟增量 fetch / prefilter / score / enrich / curate / interpret |
| `alert` | launchd, StartInterval=300 | 每 5 分钟执行 `admin alert-check`，按 A1-A4 规则发送飞书告警 |

### 部署 / 移除 / 查状态

```bash
./install.sh   [service]   # 部署 + 启动；不带 service 则全部
./status.sh    [service]   # 只读面板；不修改任何状态
./uninstall.sh [service]   # 注销 supervisor，停服务，保留数据/日志
```

服务名是可选位置参数（`serve` / `tunnel` / `pipeline` / `alert`）；不带参数作用于全部。脚本幂等——重复跑不报错。

完整运维细节（验证命令、隐含依赖、各服务 instructions 链接）见 [`docs/operations/services.md`](docs/operations/services.md)。`/admin` 与 A1-A4 告警 runbook 见 [`docs/operations/monitoring-alerting.md`](docs/operations/monitoring-alerting.md)。微信公众号源（Mp2RSS 接入、头像 backfill、文章解读、KB 回写）见 [`docs/operations/wechat-ingestion.md`](docs/operations/wechat-ingestion.md)；旧 WeWe RSS 桥接已从服务层移除（仅回滚时参考 [`deploy/wewe-rss/RUNBOOK.md`](deploy/wewe-rss/RUNBOOK.md)）。

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
