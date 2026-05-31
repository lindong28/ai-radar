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
```

### 5. 启动 Web 服务

```bash
./run.sh serve --host 127.0.0.1 --port 8000
```

访问 `http://localhost:8000` 查看站点。

## 自动化调度

`pipeline.sh` 按顺序执行 `fetch → prefilter → score → enrich → curate`，每个阶段只处理尚未评估的新条目。单阶段失败会记录 `FAIL` 后继续，日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`，`.pipeline.lock` 跳过重叠运行。

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
| AI 日报 | `/daily` | 每日精选归档，支持 `?date=YYYY-MM-DD` |
| 关于 | `/about` | 项目介绍和信源池 |

## 数据流水线

```
RSS / X / WeWe RSS 微信公众号源 → fetch → prefilter → score → enrich → curate → web 展示
```

各阶段说明：

- **fetch** — 从 `data/sources.toml` 中配置的 RSS/X/微信公众号信源抓取内容
- **prefilter** — 使用 LLM 或规则引擎过滤 AI 相关内容
- **score** — 五维评分（relevance、density、recency、authority、engineering）
- **enrich** — LLM 生成中文标题和摘要
- **curate** — 按阈值精选高价值内容（默认阈值 6.5）

## 配置

### 信源

信源池配置在 `data/sources.toml`，每个信源包含 slug、名称、URL、优先级层级（T1/T1.5/T2）等字段。`kind` 支持：

- `feed`：普通 RSS/Atom 信源
- `x`：X/Twitter 导出的 RSS 信源，前端允许展示完整 thread
- `wechat`：微信公众号源，URL 指向 WeWe RSS per-feed URL；新增公众号前需先在 WeWe dashboard 订阅，详见 `docs/references/wechat-sources.md` 和 `deploy/wewe-rss/RUNBOOK.md`

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
| `pipeline` | cron | 每 15 分钟增量 fetch / prefilter / score / enrich / curate |
| `wewe` | launchd（包装 docker compose） | WeWe RSS 桥接 :4000（微信公众号 ingestion） |

### 部署 / 移除 / 查状态

```bash
./install.sh   [service]   # 部署 + 启动；不带 service 则全部
./status.sh    [service]   # 只读面板；不修改任何状态
./uninstall.sh [service]   # 注销 supervisor，停服务，保留数据/日志
```

服务名是可选位置参数（`serve` / `tunnel` / `pipeline` / `wewe`）；不带参数作用于全部。脚本幂等——重复跑不报错。

完整运维细节（验证命令、隐含依赖、各服务 instructions 链接）见 [`docs/operations/services.md`](docs/operations/services.md)。`wewe` 微信源 onboarding 见 [`deploy/wewe-rss/RUNBOOK.md`](deploy/wewe-rss/RUNBOOK.md)。

## 部署

`./install.sh` 覆盖服务的注册与启动（见上）。此外需要一次性的配置：

### Cloudflare Tunnel

```bash
cp deploy/cloudflared/config.yml.example deploy/cloudflared/config.yml
# 编辑 config.yml 填入 tunnel UUID 和域名
```

### Docker / 其他平台

项目本身是标准 FastAPI 应用，可不走 launchd 直接起：

```bash
uv run uvicorn airadar.web.app:app --host 0.0.0.0 --port 8000
```

## 致谢

AI Radar 的 UX 设计和创意灵感来自 [AIHOT](http://aihot.virxact.com/)（[项目介绍](https://mp.weixin.qq.com/s/r6CE2U3Y0-pU05wF3_PuTQ)）。本项目在时间线、精选和日报的形态上借鉴了 AIHOT 的设计理念，并计划将项目改造为每个人都可以根据自己的需求进行配置和部署的的个性化信息消费工具。

## License

[MIT](LICENSE)
