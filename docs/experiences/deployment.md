# Deployment 经验

> Append-only. 部署和调度相关的坑点和 pattern.

## 2026-05-15 非交互调度不会继承 shell 环境变量

- Problem: cron/launchd 触发的 pipeline 不继承当前 shell session 中 `export` 的 API key。首次 launchd RunAtLoad 因缺少 `DEEPSEEK_API_KEY` 导致 enrich 阶段逐条报错。临时用 `launchctl setenv` 注入后才通过。
- Solution: 使用项目根目录 `.env` 或 `~/.claude/.env` 存放 API key，由 runtime env loader 在启动时加载（见 ADR-003）。不要依赖交互式 shell 的 `export`。
- Applies when: 配置任何非交互调度（cron、launchd、systemd）时——部署前确认 `.env` 文件包含所需 key，不要假设环境变量已存在。

## 2026-05-15 cron 与 launchd 不要同时启用

- Problem: 为绕过 macOS crontab TCC 阻塞，临时安装了 launchd fallback。之后 cron 恢复后如果不移除 launchd，会导致 pipeline 被双重触发（每 15 分钟执行两次）。
- Solution: 确保同一时间只启用一种调度方式。切换时先 bootout 旧的再安装新的。当前生产配置使用 cron。
- Applies when: 在调度方式之间切换时——检查是否有残留的 plist 或 crontab 条目。

## 2026-05-28 Cloudflare Tunnel 路径会主导 SSR 首屏延迟

- Problem: `/` 和 `/all` 已经改成 SSR preload 后，本地 origin TTFB 大多只有 7-43ms，但 `https://aiplanet.live/` 仍出现 2-6s TTFB spikes 和偶发 Playwright timeout。应用层 spinner/API 都已经消失，瓶颈在 Cloudflare Tunnel adapter 层。
- Solution: Homebrew 管理的 `cloudflared` 要先升级到当前版本；本次从 2026.2.0 升到 2026.5.2。生产 tunnel config 中 `aiplanet.live` origin 应显式使用 `http://127.0.0.1:8000`，并设置 `region: us`、`edge-ip-version: "4"`，让连接落在 `lax/sjc` 这类 US IPv4 edge，避免自动路径注册到 HKG 等远端 edge。
- Applies when: 通过 `live.aiplanet.ai-radar.tunnel` 暴露本地 FastAPI 服务，且生产 FCP/TTFB 明显慢于 `127.0.0.1:8000` origin。先看 `/tmp/ai-radar-tunnel.err` 的 registered tunnel locations，再跑生产和本地 TTFB 对照。

## 2026-05-29 WeWe RSS 需要区分容器出站代理和本机 loopback 抓取

- Problem: WeWe 容器访问微信读书时需要走主机代理 `host.docker.internal:59527`，但 ai-radar 从宿主机抓取 `http://localhost:4000/feeds/...` 时如果继承 shell 的 `HTTP_PROXY`，httpx 会把 loopback 请求也送到代理，导致 `RemoteProtocolError: Server disconnected without sending a response`。
- Solution: `deploy/wewe-rss/.env` 只把容器内的 `WEWE_HTTP_PROXY` / `WEWE_HTTPS_PROXY` 指向 `http://host.docker.internal:59527`；ai-radar 的 fetcher 对 `localhost` / `127.0.0.1` / `::1` URL 设置 `trust_env=False`，绕过宿主机代理。
- Applies when: 新增本地桥接服务（WeWe RSS、mock feed、dev server）作为 ai-radar source URL，且开发 shell 配了 `HTTP_PROXY` / `HTTPS_PROXY`。

## 2026-05-29 本地 docker compose 桥接服务也要进 launchd

- Problem: WeWe RSS 通过 `docker compose up -d` 启动，容器有 `restart: unless-stopped`，但这只保证**容器进程**崩了自重启。Docker daemon（OrbStack）退出 / 系统重启 / 用户没把 OrbStack 设为开机自启 → 容器静默缺席，pipeline 跑到 `kind="wechat"` 源时只是 loopback 不可达，没有报警；现象是公众号源停更但 ai-radar 服务本身一切正常。
- Solution: 加 `deploy/launchd/ai-radar-wewe.plist`，与 `serve` / `tunnel` 一致：bash -lc 包装 `docker compose up`（**前台**，不加 `-d`，让 launchd 监督前台进程），`KeepAlive=true`，`ThrottleInterval=30`（给 OrbStack 启动留时间，避免 daemon 没就绪时 launchd 紧密重试刷日志）。RUNBOOK.md 同步加"Keeping It Running"小节，明确"OrbStack 必须设为开机自启"这层依赖。
- Applies when: 任何"本地 docker compose 桥接服务"打算长期运行而非临时手动启停时——单靠 `restart: unless-stopped` 解决不了 Docker daemon 缺席的情形，必须有外层守护或者显式声明 Docker daemon 自启依赖。

## 2026-06-02 重启 serve 前必须避开 cron pipeline 持有的 DB 写锁

- Problem: 生产 serve 启动时执行 `db.migrate()`（FTS 表 / trigger 重建需要 `data/radar.db` 写锁）。cron 每 15 分钟跑一次 `pipeline.sh`（prefilter/score/enrich/interpret），运行时持有同一个 `data/radar.db` 写锁。若在 cron 任务正持锁时 `kickstart` 重启 serve，migrate 因 `database is locked` 崩溃 → launchd 重试窗口内站点 down。注：interpret stage 串行（并发 1），因复用的 ai-assistant KB 写入器非并发安全（见 docs/issues/general.md），pipeline 持锁时间不短。
- Solution: 安全重启顺序——先暂停 cron（注释 crontab 里 `*/15 ... pipeline.sh` 那行）或等当前 pipeline 跑完锁释放，`ps` 确认无 `pipeline.sh` / `airadar.cli prefilter|score|enrich` 在跑，再 `launchctl kickstart -k gui/$UID/live.aiplanet.ai-radar.serve`。migrate 成功后再恢复 crontab。
- Applies when: 重启 serve（应用迁移、改配置、升级）时——任何会触发 `db.migrate()` 的 serve 重启都要先确认没有调度任务正持 DB 写锁，否则 migrate 崩溃导致站点短暂 down。实际发生于 2026-06-02 上线 wechat 文章解读 pipeline 期间。
