# AI Radar

AI Radar 是一个公开只读的 AI 信息流站点。它从 RSS 信源抓取 AI 相关内容，经过 LLM 筛选、评分、翻译后，以时间线形式呈现精选内容。

## 快速开始

### 1. 环境准备

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/AIPlanetLive/ai-radar.git
cd ai-radar
uv sync
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
./run.sh fetch       # 从 RSS 信源抓取内容
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

## Web 页面

| 页面 | URL | 说明 |
|------|-----|------|
| 精选 | `/` | 高评分精选内容，按日期分组 |
| 全部 AI 动态 | `/all` | 完整时间线，最新优先 |
| AI 日报 | `/daily` | 每日精选归档，支持 `?date=YYYY-MM-DD` |
| 关于 | `/about` | 项目介绍和信源池 |

## 数据流水线

```
RSS 信源 → fetch → prefilter → score → enrich → curate → web 展示
```

各阶段说明：

- **fetch** — 从 `data/sources.toml` 中配置的 RSS/X 信源抓取内容
- **prefilter** — 使用 LLM 或规则引擎过滤 AI 相关内容
- **score** — 五维评分（relevance、density、recency、authority、engineering）
- **enrich** — LLM 生成中文标题和摘要
- **curate** — 按阈值精选高价值内容（默认阈值 6.5）

## 配置

### 信源

信源池配置在 `data/sources.toml`，每个信源包含 slug、名称、RSS URL、优先级层级（T1/T1.5/T2）等字段。

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

## 部署

### macOS (launchd)

复制模板并根据实际情况修改路径：

```bash
cp deploy/launchd/ai-radar-serve.plist.example ~/Library/LaunchAgents/ai-radar-serve.plist
cp deploy/launchd/ai-radar-tunnel.plist.example ~/Library/LaunchAgents/ai-radar-tunnel.plist
# 编辑 plist 文件中的路径
launchctl load ~/Library/LaunchAgents/ai-radar-serve.plist
```

### Cloudflare Tunnel

```bash
cp deploy/cloudflared/config.yml.example deploy/cloudflared/config.yml
# 编辑 config.yml 填入 tunnel UUID 和域名
```

### Docker / 其他平台

项目是标准 FastAPI 应用，可直接用 uvicorn 启动：

```bash
uv run uvicorn airadar.web.app:app --host 0.0.0.0 --port 8000
```

## 致谢

AI Radar 的 UX 设计和创意灵感来自 [AIHOT](http://aihot.virxact.com/)。本项目在时间线、精选和日报的形态上借鉴了 AIHOT 的设计理念，同时将其从选题创作工具转变为日常 AI 信息消费工具。

## License

[MIT](LICENSE)
