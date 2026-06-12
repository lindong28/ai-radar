# 运维监控与告警 Runbook

> Mutable snapshot. 面向 AI Radar 运维者：怎么看 `/admin`、怎么处理告警、怎么配置飞书与 Cloudflare Access。

## 入口

- Dashboard：`https://${AI_RADAR_SITE_DOMAIN}/admin`
- Metrics API：`https://${AI_RADAR_SITE_DOMAIN}/api/v1/admin/metrics`
- 本地 bypass：`http://127.0.0.1:8000/admin`
- Alert 命令：`./run.sh admin alert-check`

`/admin` 是运维面板，不是公开页面。公网访问必须通过 Cloudflare Access；本机 `127.0.0.1` / `::1` / `localhost` 保留 bypass，便于部署验证和故障排查。

## Dashboard 怎么看

| 板块 | 口径 | 用法 |
|---|---|---|
| 用户量 | access log 过滤 bot/static/scanner 后的 PV/UV；`raw_unique_ips` 作为上界参考 | 看真实用户访问是否骤降，结合 5xx 率判断是否用户侧故障 |
| 文章摄取 | 今日 items 增量、最新 fetch 插入/失败、最近 curation run | 看内容是否仍在进入系统；fetch 失败率高或今日增量低会触发 A4 |
| Pipeline 阶段健康 | fetch/prefilter/scoring/enrich/curate 的处理量、错误率、P50/P95、成本 | 定位是哪一阶段异常；日志中的 `score` 已归一为 dashboard 的 `scoring` |
| 当前告警 | A1-A4 规则的当前状态、触发数值、处置方向 | 先看故障类别，再看具体对象和下一步动作 |

时间口径固定为 `Asia/Shanghai`。access log 当前写入 `logs/serve-access.log`，pipeline 日志写入 `logs/pipeline-YYYYMMDD-HHMMSS.log`。

## 告警规则

| 规则 | 故障类别 | 典型含义 | 处置动作 |
|---|---|---|---|
| A1 | 上游模型不可用 | DeepSeek/OpenAI/GLM/ARK 返回 endpoint/model/权限/余额类错误；`schema validation failed` 已排除 | 查 provider 控制台余额、模型权限、API key；必要时切换 provider 或充值 |
| A2 | 阶段错误率/耗时异常 | prefilter/scoring/enrich 错误率或 P95 超阈值，或**超过 120 分钟没有成功 pipeline**。SKIP 日志=「pipeline 已在运行」=存活，不计故障，故长任务进行中不会告警；只有真停产/僵尸锁（长时间无成功且 SKIP 堆积）才触发 | 查 `logs/pipeline-*.log` 的失败阶段；必要时手动跑单阶段复现 |
| A3 | 网站用户侧异常 | `/admin` 以外用户访问出现高 5xx 率（healthz 主动探测尚未实现，原写死的 healthz 维度已移除，见 `docs/issues/general.md`） | 查 `logs/serve-access.err.log`、`logs/serve-access.log`、`./status.sh serve tunnel` |
| A4 | 文章摄取骤降 | fetch 失败率高或今日 items 增量低于基线 | 查 RSS/WeWe/X 源可用性、WeWe 容器、`./run.sh fetch` 输出 |

告警状态存储在 `data/alert-state.json`。同一规则 firing 后有 30 分钟冷却；恢复时发送 resolved。

## 飞书自定义机器人

1. 在飞书群里打开「群设置」→「机器人」→「添加机器人」→「自定义机器人」。
2. 复制 webhook URL，写入项目根目录 `.env` 或 `~/.claude/.env`，不要提交真实 URL：

```bash
FEISHU_GENERAL_ALERT_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

3. 在部署机执行 preflight：

```bash
./run.sh admin alert-check
```

4. 确认群里收到以 `【AI Radar】` 开头、包含「故障类别」「具体故障对象/数值」「处置方向」的消息。该前缀来自 `alerts.py` 的 `ALERT_SOURCE` 常量，用于在多个项目共用同一 webhook 时让收件人区分告警来源。
5. 安装周期告警服务：

```bash
./install.sh alert
```

`install.sh alert` 会从当前进程环境、`.env` 或 `~/.claude/.env` 读取 `FEISHU_GENERAL_ALERT_WEBHOOK`，并写入本机生成的 `deploy/launchd/ai-radar-alert.plist` 的 `EnvironmentVariables`。launchd 不继承交互式 zsh 的临时 `export`；如果只在 shell 里 export 但没有重新安装 alert，后台任务拿不到 webhook。安装后可用下面命令确认本机 plist 已带 webhook 环境键（不要把真实 URL 贴到 issue/commit/聊天里）：

```bash
plutil -p deploy/launchd/ai-radar-alert.plist | rg 'FEISHU_GENERAL_ALERT_WEBHOOK'
```

测试或自定义数据库路径时，`install.sh alert` 也会把已设置的 `AI_RADAR_DB` 写入同一个 `EnvironmentVariables`，让 launchd job 与手工 `./run.sh admin alert-check` 使用同一份 SQLite。

如果 webhook 变更，重新生成并加载 plist：

```bash
./uninstall.sh alert
./install.sh alert
```

没有 `FEISHU_GENERAL_ALERT_WEBHOOK` 时，`alert-check` 只评估规则；触发告警时日志会显示 `send <rule> <type> skipped reason=FEISHU_GENERAL_ALERT_WEBHOOK is not set`，不会发送消息。

## Cloudflare Access

Cloudflare Access 是公网真实鉴权边界；origin 只做 `Cf-Access-Jwt-Assertion` 存在性兜底，不验签。

### 控制台配置

1. 打开 Cloudflare Zero Trust 控制台。
2. 进入「Access」→「Applications」→「Add an application」→「Self-hosted」。
3. 创建 `AI Radar Admin` application。
4. Domain 填你的 `AI_RADAR_SITE_DOMAIN` 值。
5. Path 至少覆盖：
   - `/admin*`
   - `/api/v1/admin*`
6. 添加 policy，限制允许访问的用户身份，例如指定邮箱、邮箱域、或 Cloudflare Access group。
7. 保存后，用无登录态浏览器或 curl 验证公网入口不再直接进入 dashboard。

### 验证

公网无凭证预期返回 302/403：

```bash
curl -sS -o /tmp/ai-radar-admin-public.out -w '%{http_code}\n' "https://${AI_RADAR_SITE_DOMAIN}/admin"
```

origin 兜底预期：

```text
origin_no_header_api=403
origin_fake_header_api=200
origin_no_header_page=403
origin_fake_header_page=200
```

已知限制：origin 只检查 header 是否存在，所以任意非空 `Cf-Access-Jwt-Assertion: x` 会被 origin 放行。真实安全边界依赖 Cloudflare Access 在边缘拦截；origin 当前只通过本机/tunnel 暴露，不应直接暴露到公网。JWT 验签是后续增强项。

安全注意：origin 的本地 bypass 会放行 `127.0.0.1` / `::1` / `localhost`。当前公网请求经 tunnel 到达 FastAPI 时 `client.host` 仍是公网 IP，公网无凭证访问 `/admin` 已验证为 403；但如果未来 cloudflared 转发机制变化，让公网请求在 origin 看起来像 `127.0.0.1`，就会触发本地 bypass。后续增强应把生产 bypass 移到显式 dev 配置，或完成 Cloudflare Access JWT 验签 / origin token 校验。

## 常用命令

```bash
./status.sh serve tunnel pipeline alert
./run.sh admin alert-check
tail -n 50 logs/serve-access.log
tail -n 50 logs/alert-check.log
tail -n 50 logs/alert-check.err.log
```
