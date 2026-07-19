# AI Radar 运维监控 + 告警 MVP

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan §L2 的 verify 步骤并贴出输出。

## 输入与产物意识

- **无 spec**：本 plan 自带 L1 / L2 / L3 三层，是 review 与实施的唯一入口。
- **Consumer**：implementer（新 session 落地）+ reviewer（`/custom:review-plan`）。
- 关键事实（数据源、模块落点、调度）已 inline 进 §「现状与数据源」，implementer 无需重新勘探；但 §Risks 标注的待证实假设必须按指定探针先验证再依赖。

---

## L1 — 最终产物 + 使用方式

**产物**（两件，MVP）：
1. **运维监控 Web dashboard**：复用现有 FastAPI，新增 `/admin` 页，浏览器一页看全 4 板块（用户量 / 文章摄取 / pipeline 各阶段健康 / 当前告警）。经 **Cloudflare Access** 鉴权。
2. **飞书告警**：独立调度的检查脚本，命中告警规则时通过**飞书自定义机器人 webhook** 推送；恢复时推送 resolved。

**使用者**：用户本人（单人运维）。

**使用方式 / 下游用途**：日常打开 dashboard **一眼判断**当前用户量、文章摄取量、整条 pipeline 健康度 → 据此**采取行动**（典型：发现上游欠费就充值、某源持续失败就重启容器、某阶段慢就排查）。异常时不必主动看——飞书**主动找人**。这定义了设计深度：dashboard 要能 drill-down 看趋势与分阶段细节，不是单一数字；告警**消息内容必须足以让用户一眼判断故障类别并知道处置方向**，不只是"出事了"。

**范围 / 约束**：
- MVP：覆盖最高价值指标 + 4 类告警，快速上线，再迭代。
- 复用现有栈（FastAPI / SQLite / cron / launchd / cloudflared / 飞书），**不引入** Prometheus / Grafana / 时序库等重型监控栈（此约束有 verify 断言，见 V6）。
- 不做前端行为埋点（无停留时长 / 点击）——用户量只来自 server access log。

**假设**（必须先按 §Risks 指定探针证实，再依赖）：
- access log 里能拿到可用于 UV 的客户端 IP。⚠️ 实测该 IP 带 `:0` 端口（见 §现状），来源机制未证实，TASK-001 先验。

---

## 取舍偏好（横切 L1/L2/L3）

| 维度 | 偏好 | 三层影响 |
|---|---|---|
| 快速可用 vs 指标完备 | **快速可用**（MVP） | L1 产物砍到 dashboard+告警两件；L2 verify 只覆盖 4 板块 + 4 类告警；L3 不做时序存储；dashboard 准确性按"运维够用"验收（计数类精确、比率/分位类小容差，见 V1） |
| 复用现有栈 vs 新依赖 | **复用** | L3 全部基于 SQLite/日志/cron/httpx，零新服务依赖；V6 断言无新重型依赖 |
| 安全 | **不妥协（但 MVP 不过度）** | 鉴权 = Cloudflare Access 边缘（真实闸）+ origin header 存在性兜底；origin JWT 验签留 TODO（用户拍：边缘已足够，origin 仅本地可达、绕过面极小） |
| 实时性 | 告警分钟级（5min 检查）；dashboard 按需刷新 | L3 告警独立调度每 5min |

---

## 现状与数据源（implementer 直接用）

**DB**：`data/radar.db`（SQLite）。关键表与行数（勘探于 2026-06-01）：

| 表 | 用途 | 关键列 |
|---|---|---|
| `item_evaluations` (~28.6k) | **prefilter/scoring/enrich 健康金矿** | `stage` (CHECK IN `'prefilter','scoring','enrich'`)、`model_id`、`latency_ms`、`cost_usd`、`error`(NULL=成功)、`evaluated_at` |
| `items` (~11.4k) | 文章总量 | `fetched_at`、`source_id` |
| `curated_items` (~64.9k) | 策展产出 | `curation_run_id`、`rank` |
| `curation_runs` (~1.6k) | curate 轮次 | `created_at` |
| `sources` (43) | 源清单 | `id`、`kind` |
| `feedback` (0) | 空，目前无前端互动数据 | — |

⚠️ **关键数据现实 1**：`item_evaluations` 只覆盖 **prefilter/scoring/enrich**。**fetch 和 curate 不写此表**。各阶段数据源分工：

| 阶段 | 数据源 | 取法 |
|---|---|---|
| prefilter / scoring / enrich | `item_evaluations` | 按 `stage` 聚合：处理量 `COUNT(*)`、错误率 `error IS NOT NULL` 占比、耗时 `latency_ms` 分位、成本 `SUM(cost_usd)` |
| fetch | `logs/pipeline-*.log` | 行 `OK <source> fetched=N inserted=M` / `FAIL <source> <err>` / `=== attempted=N inserted=M failed=K`；阶段耗时 = `=== fetch START ===` 与 `=== fetch OK ===` 时间戳差 |
| curate | `logs/pipeline-*.log` + `curation_runs` | 同上时间戳差 + curation_runs 增量 |

⚠️ **关键数据现实 2（stage token 不一致）**：pipeline 日志里 stage token 用 **`score`**（源自 `pipeline.sh` 的 `run_stage score` / `run.sh score`，日志写 `=== score START/OK ===`），但 DB `item_evaluations.stage` 值是 **`scoring`**。**聚合时必须映射 `score(日志) ↔ scoring(DB)`**，否则 board-3 跨数据源对齐错位。fetch/curate 在日志中 token 即 `fetch`/`curate`。

**pipeline 日志**：`logs/pipeline-<YYYYMMDD-HHMMSS>.log`（文件名时间戳为 **UTC+8**，与全局"今日"时区一致；文件 mtime 按运行环境 `$TZ` 显示、可能与文件名时区差若干小时——聚合时一律以解析出的 UTC+8 文件名时间为准，勿用 mtime）。每轮一个文件；**71 字节的文件是 `SKIP: already running`**（锁饥饿信号——上一轮超过 15min cron 间隔仍在跑）。stage 行格式：`[<ts>] === <stage> START|OK|FAIL (exit N) ===`；轮次成功标记 `=== PIPELINE DONE (failed=K) ===`。

**access log**：当前在 `/tmp/ai-radar-serve.log`（uvicorn stdout）。格式：`INFO:     <IP>:0 - "GET /path HTTP/1.1" <status> <reason>`。
- ⚠️ **IP 多态 + 来源未证实**（reviewer 实测）：地址列有三种形态——IPv4 带 `:0`（`149.112.116.68:0`，占绝大多数）、**IPv6 带 `:0`（地址本身含冒号，如 `2409:8a1e:...:f63b:0`）**、`127.0.0.1:<真实端口>`（本地探测，非零端口）。`app.py:245` 的 `uvicorn.run(create_app(), ...)` **没有** `proxy_headers`/`forwarded_allow_ips`，代码也无 `CF-Connecting-IP`/`X-Forwarded-For` 处理。`:0` 端口说明公网 IP 是**某处注入的 forwarded 地址、机制未知**（既非裸 cloudflare edge 也非干净 socket）。→ 解析器契约：**对最后一个 `:` 做 rsplit、strip 任意端口（0 或真实）、正确处理 IPv6 含冒号地址**（不可 naive split `:`）；IP 可信度由 TASK-001 探针判定。
- ⚠️ 在 `/tmp`、服务重启易失 → 本 plan 含「持久化」任务（D4 + V7）。

**服务存活与契约**：`status.sh`（已有，serve/tunnel/wewe/pipeline 状态）。服务契约集中在 `deploy/lib/services.sh` 的 `ALL_SERVICES=(serve tunnel pipeline wewe)` + `service_label()`/`service_plist()`/`service_description()` 三个 case；安装/卸载在 `install.sh`/`uninstall.sh`。`/api/v1/healthz` 返回 items/curation_runs 计数 + ruleset_version。

**模块 / 部署落点**：
- `src/airadar/admin/`（已存在 `__init__.py`，仅一行 docstring）→ 监控/告警逻辑落点。
- `src/airadar/web/app.py` `create_app()`：现有页面直接定义在此闭包内（`app.py:186–231`）——其中 **`/`、`/all` 是动态 SSR（`templates.TemplateResponse`，行 189/214）**，`/daily`、`/about` 是 `FileResponse` 静态文件；`routes/` 下只放 `/api/v1` 的 JSON 路由。→ 新 `/admin` HTML 页沿用 `/`、`/all` 的 TemplateResponse 模式定义在 `app.py`（复用闭包内 `templates`），JSON API 放 `routes/admin.py`。
- `src/airadar/cli.py`：子命令注册处。**现有运维子命令挂在 `admin` 组下**（`admin db migrate` / `admin sources reload` / `admin curate`）→ 新告警检查命令为 `admin alert-check`。
- `web/templates/`：Jinja2 SSR（现有 index.html/all.html 可复用渲染模式）。
- 现有 HTTP 客户端：项目已依赖 `httpx`，用于 `fetcher/http_client.py`（`httpx.get`）和 `eval/judge.py:187`（`httpx.post`）→ 告警发送用 `httpx.post`，与 peer 一致。
- 调度：`crontab` `*/15 * * * * pipeline.sh`；launchd `deploy/launchd/ai-radar-{serve,tunnel,wewe}.plist`。
- tunnel：`deploy/cloudflared/config.yml` → `your-domain.example` 映射 `127.0.0.1:8000`（dashboard 会自动经此暴露公网）。
- provider→模型：`deepseek_v32`(prefilter, `deepseek-v4-flash`)、`deepseek_v4_pro`(scoring+enrich, `deepseek-v4-pro`)。上游欠费时返回误导性 404 `InvalidEndpointOrModel.NotFound`（见 journal 背景）。

---

## L3 — 设计决策 + 内部 verify

### 模块结构

| 文件 | 职责 |
|---|---|
| `src/airadar/admin/access_log.py`（新） | 解析 access log 行（IP/path/status/ts，**显式处理 `:0` 端口**）+ bot 过滤 + PV/UV/热门页/状态码聚合 |
| `src/airadar/admin/metrics.py`（新） | 聚合层：从 DB + access_log + pipeline 日志算所有 dashboard 指标（含 `score↔scoring` 映射），返回结构化 dict |
| `src/airadar/admin/alerts.py`（新） | 4 类告警规则评估 + 状态机（firing/ok + 冷却 + resolved）+ 飞书发送（**`httpx.post`**） |
| `src/airadar/web/routes/admin.py`（新） | `/api/v1/admin/metrics` JSON，调 metrics.py；origin 校验 `Cf-Access-Jwt-Assertion` 存在性 |
| `src/airadar/web/app.py`（改） | 在 `create_app()` 内新增 `/admin` HTML 页（沿用现有 SSR 模式，复用 `templates`）+ 挂载 admin router |
| `web/templates/admin.html`（新） | SSR dashboard，4 板块 |
| `src/airadar/cli.py`（改） | 新增 `admin alert-check` 子命令（挂 admin 组，供独立调度调用） |
| `deploy/launchd/ai-radar-alert.plist`(.example)（新） | 每 5min 跑 `run.sh admin alert-check`，**独立于 pipeline**。⚠️ alert 是**周期任务**，用 `StartInterval=300`（区别于现有 serve/tunnel/wewe 的 `RunAtLoad`+`KeepAlive` 常驻模型）；plist 文件名须与 `service_plist_name` 返回值一致，供 `ensure_plist` 从 `.example` 生成 |
| `deploy/launchd/ai-radar-serve.plist`（改） | stdout/stderr 重定向到 `logs/serve-access.log`（持久化 access log） |
| `deploy/lib/services.sh`（改） | `ALL_SERVICES`(line 7) 加 `alert`；三个 case 函数加 alert 分支——真实函数名是 `service_label`/`service_plist_name`/`service_desc`（**非** `service_plist`/`service_description`） |
| `status.sh`（改） | `case "$slug"`(line 71) 加 alert；per-service 日志行 case(line 32/46) 加 alert |
| `install.sh` / `uninstall.sh`（改） | `case "$slug"`(install.sh:55 / uninstall.sh:51) 加 alert 分支；`install.sh` 的 `Logs:` 帮助行 + usage 服务枚举加 alert |

### dashboard 4 板块（对应用户诉求 · 每个 user-readable 数字钉口径）

1. **用户量**（access log）：今日/近 7d 的 PV、**UV（已过滤 bot 后的去重 IP 数，标注"近似"）**、热门页 Top N、4xx/5xx 率、24h 流量趋势（按小时）。**"今日"按 `Asia/Shanghai (UTC+8)` 切**（用户拍；与 board-2 共用 metrics.py 固化的同一时区常量，避免两板块"今日"指不同 24h 窗口）。
2. **文章摄取**（pipeline 日志 + items）：最近一轮各 source `fetched/inserted/failed`、**今日 items 增量（"今日"按 `Asia/Shanghai (UTC+8)` 切，与 board-1 共用 metrics.py 固化的时区常量）**、近 7d 摄取趋势、**源健康表**（连续失败的源高亮，如微信源）。
3. **pipeline 各阶段**（item_evaluations + 日志）：每个阶段（fetch/prefilter/scoring/enrich/curate）的处理量、错误率、P50/P95 耗时、**成本（仅 LLM 三阶段；卡片标清时间窗口 today / 7d）**；最近 N 轮的成功/失败/SKIP 时间线。
4. **当前告警**：alerts 状态机里处于 firing 的规则 + since 时间。

每个会渲染成单一数字的累积量（成本、PV）卡片上标清窗口。默认时间范围：today + 近 7d 趋势。

### 告警规则草案（4 类 · 全部启用 · 阈值由 implementer 校准）

| # | 规则 | 触发信号（数据源） | default 阈值（**校准方法见下**） |
|---|---|---|---|
| A1 | 上游模型不可用 | `item_evaluations.error` 近窗口含 `endpoints failed`/`404`/`InvalidEndpoint` 的占比，或 prefilter/enrich 连续 N 轮全失败。⚠️ 须**排除 `schema validation failed` 类噪声**（A1 不能被它触发）。实测错误分布：主导是 `all DeepSeek provider endpoints failed`（~3247 行，正是 A1 该抓的上游故障），`schema validation failed` ~430 行为少数噪声；`Insufficient` 当前 **0 命中**（欠费以 `404 InvalidEndpoint` 形态出现）——保留作前瞻 token | 近 15min 该类（上游）错误占比 > 50% |
| A2 | 阶段错误率/耗时异常 + pipeline 卡死 | 某 stage 近窗口错误率 > 阈值；或 P95 `latency_ms` > 基线×K；或最新 `=== PIPELINE DONE` 距今 > M 分钟（含锁饥饿：连续多个 71B SKIP 日志） | 错误率 > 30%；P95 > 基线×3；无成功轮次 > 45min |
| A3 | 网站用户侧异常 | access log 近窗口 5xx 占比 > 阈值；或主动探测 `/api/v1/healthz` 失败/超时 | 5xx > 5% 或 healthz 探测连续 2 次失败 |
| A4 | 文章摄取骤降 | 最近一轮 `failed=K` 占比高；或当日 inserted 跌破基线；或多个 source 连续失败 | 单轮 failed 占比 > 40% 或日 inserted < 基线×0.3 |

**阈值校准方法**（implementer 必做，不在 plan 写死数字）：写一个一次性统计脚本，扫过去 7–30 天的 `item_evaluations`（各 stage 正常错误率、P95 latency）和 access log（日均 PV、正常 5xx 率）、pipeline 日志（日均 inserted、正常轮次时长），算出基线，把上表 default 阈值替换成基于真实基线的值，依据写进 `journal.md`。

### 告警机制

- **状态机**：状态文件 `data/alert-state.json`，每条规则记 `{state: ok|firing, since, last_notified}`。
  - `ok → firing`：发 `🔴 <规则> <详情>`（详情须含**故障类别 + 处置提示**，呼应 L1 行动诉求）。
  - `firing` 持续：仅当距 `last_notified` 超过**冷却期 30min** 才再发（防风暴）。
  - `firing → ok`：发 `✅ <规则> 已恢复`。
- **发送**：飞书自定义机器人 webhook，`httpx.post` JSON `{"msg_type":"text","content":{"text":"..."}}`（与 `eval/judge.py` 一致，便于 V3 mock）。webhook URL 读环境变量 `AI_RADAR_FEISHU_WEBHOOK`。
- **调度**：独立 launchd `ai-radar-alert.plist`，每 5min 跑 `run.sh admin alert-check`。**必须独立于 pipeline.sh**——否则 A2「pipeline 卡死」永远触发不了。**每轮 alert-check 须在日志打印本轮评估的规则集**（供 V3 断言 4 类全启用）。

### 鉴权（Cloudflare Access + origin 兜底）

- Cloudflare Zero Trust 控制台为 `your-domain.example` 的 `/admin*` 与 `/api/v1/admin*` 建 Access application + policy，限用户身份。控制台导航步骤写进 runbook（见 §文档交付物）。**配完的可探测信号**：用户配置后 agent 跑 `curl -I https://your-domain.example/admin`，期望从"无拦截/200"翻转为 302/403（Access 登录重定向）——以此客观区分"已配且 path 绑对" vs "没配/配错"，再进 V4 联合验证（避免隐性 stop-and-wait）。
- **origin 兜底（仅存在性，不验签 — 用户拍 MVP）**：admin 路由校验请求头 `Cf-Access-Jwt-Assertion` **存在**；缺失则 403。⚠️ **已知限制**：带任意非空该 header 即可通过 origin 检查——origin 不是真实鉴权，真实闸是 Cloudflare Access 边缘拦截；origin 是 `127.0.0.1:8000` 仅 tunnel 可达、绕过面极小。**JWT 验签留后续 TODO**。

### 内部 verify（L3，agent 可独立跑）

- `access_log.py`(含 `:0` 端口解析 + bot 过滤) / `metrics.py`(含 `score↔scoring` 映射) / `alerts.py` 各自 pytest 单元测试：fixture DB + 样例日志行，断言聚合数值、规则触发/不触发、状态机 `ok→firing→ok` 序列与冷却。
- 飞书发送：monkeypatch `httpx.post`（或注入测试 webhook），断言 payload 正确，不依赖真实网络。
- `mypy` 类型 + `ruff` lint 全绿。
- 覆盖率 ≥ 80%（项目 testing 规则）。

---

## L2 — 用户视角 verify（交付 gate · implementer-executable）

> 这是**交付 gate**：只有以下 user-facing verify 通过 + 贴出可观察证据，才能声称完成。internal verify 全绿不算。

| # | verify | 可观察证据 | 人机边界 |
|---|---|---|---|
| V1 | **dashboard 数据准确**：独立脚本直接从 DB/日志算真值，对比 `/api/v1/admin/metrics`。**容差**：(a) 计数/比率/成本/分位类**精确相等**（diff≠0 即聚合 bug）；(b) PV/UV 等时间窗口/过滤敏感类——对比脚本与 metrics.py **用同一时间边界 + 同一 bot 过滤函数**取数，故也应精确相等。**fetch 一致性（注意单位区分）**：① 源计数 `attempted == #OK行 + #FAIL行` 且 `failed == #FAIL行`；② 条目计数 `summary.inserted == SUM(逐源 inserted)`（`attempted`/`failed` 是**源数**、`inserted` 是**文章条数**，单位不同不可互等）；③ `今日经 pipeline 的 inserted 之和 ≤ items 今日增量`，差额 = 非 pipeline 路径（WeChat bridge / recovery 回填）摄取量且可点名归因（**这是上界不是等式**——items 含非 pipeline 入库），两侧用同一时区窗口。**UV 上界**：raw distinct-IP（绕过过滤）作上界、过滤后作 UV，断言差额 = 被判 bot 的 IP 数且可解释 | 对比脚本 stdout：每项 expected/actual/diff，含 fetch 源计数/条目计数/items 上界三类 + UV 上下界 | agent 独立 |
| V2 | **4 板块可见且有数据**：带 Access 凭证（或本地 bypass）访问 `/admin`，确认 4 板块均渲染真实数据 | 页面截图 + 关键数字 | agent 独立（本地 bypass）；**人工 acceptance：授权邮箱进得去 + 非授权邮箱被挡 + 4 板块有真实数据** |
| V3 | **告警端到端**：逐类注入故障验证飞书收到 + resolved。注入：A1 临时改错模型名/mock provider；A2 构造 item_evaluations 错误样本 + 模拟无成功轮次；A3 构造 access log 5xx 样本 / healthz 返 5xx；A4 mock 一轮 fetch 多源失败。**+ 4 类全启用断言**：alert-check 单次运行日志列出本轮评估规则集 = {A1,A2,A3,A4}。**+ 每类 negative case**：喂正常基线数据断言对应规则 `state=ok` 不发；**A1 的 negative case 须显式注入一批 `schema validation failed` 样本，断言 A1 仍 `ok`（验证对 schema 噪声免疫）** | 每类：alert-check 日志显示检测到 + `httpx.post` payload（mock 抓取）；resolved payload；规则集日志；negative case 的 ok 状态 | agent 用 mock/测试 webhook 验逻辑；**人工 acceptance（per-类 ballot，逐条勾）：每类飞书消息是否点明 ①故障类别 ②具体故障对象/数值 ③处置方向**（此 ballot 同时是 implementer 写告警文案的 spec） |
| V4 | **鉴权边界**：无凭证 `curl -I https://your-domain.example/admin` 期望 302/403；origin 端缺 `Cf-Access-Jwt-Assertion`→403。**+ 明示已知限制**：带任意伪造 `Cf-Access-Jwt-Assertion: x` 会被 origin 放行（依赖 Access 边缘为真实闸） | curl 输出状态码；伪造 header 放行的演示输出 | agent 验 origin 端；**人工 acceptance：公网无凭证访问拿到 302/403**（用户配好 Access 后联合验证） |
| V5 | **防骚扰**：同一 firing 连续两次 alert-check（冷却期内）只发一次；超冷却期再发 | 两次运行日志 + 发送计数 | agent 独立 |
| V6 | **无新重型依赖（约束保持）**：`git diff` 检查 `pyproject.toml`/`uv.lock` 未新增 prometheus/grafana/时序库类包；新增 launchd 仅 1 个（alert） | diff 输出 + launchd 清单 | agent 独立 |
| V7 | **access log 持久化生效**：重载改后的 serve launchd，curl 一次 `/api/v1/healthz`，断言 `logs/serve-access.log` 存在且出现对应新行 | 文件存在 + 新增行 grep 输出 | agent 独立（launchd 重载本地可做）；真实重启不丢由运维确认 |
| V8 | **服务契约接线 + 生命周期（D11 锁定决策）· 三个 dispatch 点对称闭合**：dispatch `case "$slug"` 在 **install.sh:56 / uninstall.sh:52 / status.sh:72 是三个独立代码点**，只改 `ALL_SERVICES`/usage 而漏改任一会静默 fall-through——故每点都要一条对其敏感的断言：① **status.sh:72** → `./status.sh alert` **实际产出 alert 状态行**（stdout 含以 `alert` 起头的 loaded/not-installed 行；注意"不报 unknown service" 对此 arm 改动**不**敏感、不可用作断言，因 `unknown` 由 dispatch 之前的 `validate_service` 判定）；② **install.sh:56** → `./install.sh alert` 后 `launchctl print gui/$UID/com.example.ai-radar.alert` 成功（label 已 load）；③ **uninstall.sh:52** → `./uninstall.sh alert` 后该 label 消失。另：`./install.sh`/`./uninstall.sh` usage 与枚举含 `alert`、`alert` 被 `ALL_SERVICES` 识别 | status.sh 含 alert 行 + launchctl print 前后对比 + usage 输出 | agent 独立 |

**人工 gate 的兜底**：V2/V3/V4 的人工部分之前，agent 已用本地 bypass / mock webhook / origin header 校验把主要风险压低——人工只确认"真的进得去且挡得住 / 真的收到且可据此行动 / 真的拦住"。

---

## 文档交付物（一等交付 · 非 cleanup 时发现）

新功能对用户/协作者可感知 → 文档是 plan 交付物。每处 touch 明确列出：

1. `README.md` §Web 页面表加 `/admin` 行；§服务表加 `alert` 行；并改 §服务下方位置参数枚举（README:143 `serve/tunnel/pipeline/wewe`）加 alert。
2. `docs/operations/services.md`：服务清单表加一行（**第一列用完整 launchd label `com.example.ai-radar.alert`，五列全填**：自动启动/当前状态/生命周期脚本/Instructions/验证命令）；并改该文件里硬编码的"**4 个服务**"字面量与「验证」小节命令块为含 alert 的版本。
3. 新建 `docs/operations/<runbook>.md`：dashboard 怎么看（4 板块含义）、4 类告警含义与处置动作、**飞书自定义机器人 webhook 配置步骤**、**Cloudflare Access 控制台配置步骤**（导航：Zero Trust → Access → Applications → Add → Self-hosted，domain=`your-domain.example`，path=`/admin*` 与 `/api/v1/admin*`，policy 限用户身份）。
4. `docs/CLAUDE.md` 文档索引收录新 runbook（项目协议：docs/ 下新增文档须更新索引）。
5. `CHANGELOG.md` 增监控告警条目。
6. runbook 链接从 `README` / `docs/operations/services.md` 上游引用（避免孤儿文档）。

---

## Defaulted Decisions（planner 拍 / 用户拍 · reviewer 审）

| # | 决策 | 理由 / 来源 |
|---|---|---|
| D1 | 告警用独立 launchd（每 5min），不挂 pipeline | pipeline 卡死时告警须独立存活，否则 A2 失效 |
| D2 | 告警阈值由 implementer 用真实数据校准，plan 不写死 | 避免拍脑袋；基线只有真实数据能给 |
| D3 | 防骚扰：状态机 + 30min 冷却 + resolved 通知 | 避免告警风暴；恢复也要可见 |
| D4 | access log 从 `/tmp` 移到 `logs/serve-access.log` | `/tmp` 重启易失（V7 验证生效） |
| D5 | UV 用 IP 去重（标注"近似"），V1 用 raw 上界比对 | MVP 无前端埋点，IP 是唯一可得维度 |
| D6 | dashboard 用 Jinja2 SSR | 最小新增，与现有 index/all 页一致 |
| D7 | dashboard 时间范围 today + 近 7d 趋势 | 覆盖「当下判断 + 趋势」，不做长期时序 |
| D8 | 飞书 webhook 直发（`httpx.post`），不用 claude-to-im | claude-to-im 明示不支持无人值守/webhook；httpx 是 peer pattern |
| D9 | fetch/curate 耗时从 pipeline 日志时间戳算 | 这两阶段不写 item_evaluations |
| D10 | 告警 A1/A2 主读 `item_evaluations.error`，日志解析仅作 fetch/curate 补充 | 结构化数据比日志正则可靠 |
| D11 | **alert 服务纳入完整服务契约**（services.sh/status.sh/install.sh/uninstall.sh/services.md） | **用户拍**：与现有 4 服务一致，./install.sh 一键管理 |
| D12 | **origin 仅验 `Cf-Access-Jwt-Assertion` 存在性，JWT 验签留 TODO** | **用户拍 MVP**：Access 边缘为真实闸，origin 仅本地可达、绕过面极小 |
| D13 | `/admin` HTML 页在 `app.py create_app()`（沿用现有 SSR），JSON API 在 `routes/admin.py` | 与现有 `/`、`/all` 落点一致 |
| D14 | 告警检查命令为 `admin alert-check`（挂 admin 子命令组） | 与现有 `admin db/sources/curate` 一致 |

---

## Risks / TODO

- **[risk · load-bearing] access log IP 第三态（机制未证实）**：IP 带 `:0` 端口、`uvicorn.run` 无 proxy_headers、代码无 forwarded 头处理——IP 既非裸 CF edge、也非干净 socket。board-1 UV 与 A3 5xx 归因都压在它上。
  - **TASK-001 探针 + 判定**：(a) 从已知设备发一条请求，比对 access-log 记录的 IP 与该设备真实公网出口 IP → 判 IP 是否可信；(b) 解析器按"rsplit 最后一个 `:`、strip 任意端口、处理 IPv6 含冒号"实现，并用 IPv4:0 / IPv6:0 / `127.0.0.1:<ephemeral>` 三种 fixture 覆盖。
  - **trigger response（no-impact alternative，implementer 自主执行）**：若 IP 不可信，加 serve middleware 读 `CF-Connecting-IP` 并记入持久化 access log。⚠️ 连带范围：该 middleware 同时改 access_log 数据源，board-1 UV 与 A3 5xx 归因均依赖它，需一并验证。
- **[risk] bot 流量占比**：access log 含 robots.txt 扫描等 bot，PV/UV 需过滤；过滤规则可能迭代（先按 UA/路径/已知扫描 IP 粗过滤）。V1 的 UV 上下界比对让过滤过激（把真人滤掉）可被发现。
- **[risk] origin 鉴权为存在性兜底、非真实闸**：见 D12，已知限制，依赖 Cloudflare Access 边缘；JWT 验签 = 后续 TODO。
- **[blocker] 前置依赖**（用户行动，缺则对应功能 blocked）：
  1. 飞书自定义机器人 webhook URL → 环境变量 `AI_RADAR_FEISHU_WEBHOOK`（告警发送依赖）。**implementer preflight**：拿到 URL 后先 `httpx.post` 一条测试消息验 HTTP 200，再请用户确认手机收到（两段证据分开）。
  2. Cloudflare Zero Trust 控制台配 Access application + policy（dashboard 鉴权依赖）。implementer 先把 runbook 导航步骤交付用户。

---

## 实施顺序建议（详见 state.md 任务分解）

1. **TASK-001 先行**：验证 access log IP 真实性（探针 + `:0` 端口），决定数据层走真实 socket IP 还是 `CF-Connecting-IP` middleware。
2. 数据层：access_log 解析（含 `:0`、bot 过滤）+ metrics 聚合（含 `score↔scoring` 映射），带单测，跑通 V1（含 fetch 三源一致性、UV 上下界）。
3. dashboard：`app.py` 加 `/admin` SSR 页 + `routes/admin.py` JSON + 模板，本地 bypass 跑通 V2。
4. 阈值校准脚本，落基线（写 journal）。
5. 告警：alerts.py 规则 + 状态机 + `httpx.post` 发送（带单测），mock webhook 跑通 V3（含 4 类全启用 + negative case）/V5。
6. 持久化 access log（改 serve plist）跑通 V7；新增 alert launchd + **纳入服务契约**（services.sh/status.sh/install.sh/uninstall.sh）。
7. 先交付 Cloudflare Access runbook 步骤给用户 → 用户配置 → V4。
8. 真实飞书 webhook（用户提供）+ preflight + V3 人工确认。
9. 文档交付物（§文档交付物全部 6 项）。
10. V6 无新依赖断言 + 交付前全量 L2 verify（V1–V7）贴证据。
