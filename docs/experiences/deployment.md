# Deployment 经验

> Append-only. 部署和调度相关的坑点和 pattern.

## 2026-09-05 容器有 restart 策略不等于重启后会回来——daemon 本身也要有人启动

`/wechat` 停更五天，根因是 Aug 31 21:49 那次重启后 OrbStack 没有自启（`uptime` 与停更窗口精确吻合，且 `~/Library/LaunchAgents` 下当时没有任何 OrbStack 条目）。Wechat2RSS 容器一直带着 `restart=unless-stopped`，但那条策略只在 Docker daemon 起来之后才起作用——daemon 不启动，它一次也不会被求值。

修法是 `./install.sh orbstack`：一个登录时跑 `orbctl start` 的 LaunchAgent。**不要指望 OrbStack 自带的 `app.start_at_login`**：`orbctl config set app.start_at_login true` 退出 0 而回读仍是 `false`，磁盘上也找不到该键——它只能从图形界面改，而这套部署的前提正是不依赖图形界面。

验证要覆盖真正起作用的那条路径：`launchctl print` 显示 `last exit code = 0` 只证明了 OrbStack 已在跑时的 no-op 分支。要证明它能**从停止状态**启动，得清空日志 → `orbctl stop` → 确认 `Stopped` → **单次** `kickstart`（带 `-k` 会让 job 跑两次，stderr 里那句 `already running` 会污染归因）→ 回读 `Running` 且 agent 输出为空。

## 2026-09-05 给 ai-radar 加一个 launchd 服务要改七处，而 plan 文档只写了三处

`docs/plans/20260601-monitoring-alerting/plan.md` 记的是「三个 dispatch 点对称闭合」（install.sh / uninstall.sh / status.sh）。实际注册面是七处：另有 `deploy/lib/services.sh` 的 `ALL_SERVICES`、`service_label`、`service_plist_name`、服务描述表、`service_dependency_missing_reason`，以及三份 usage 注释。

漏掉 `service_label` 的症状具有误导性——`./status.sh <slug>` 报 `status unavailable (unsafe HOME)`，因为 `service_launch_agent_path` 在标签为空时提前返回，而它的错误文案说的是 HOME。查的时候别从 HOME 入手。

`deploy/launchd/*.plist` 被 `.gitignore` 覆盖，仓内只跟踪 `.example`；实例文件由 `install.sh` 从模板生成。

## 2026-05-15 非交互调度不会继承 shell 环境变量

- Problem: cron/launchd 触发的 pipeline 不继承当前 shell session 中 `export` 的 API key。首次 launchd RunAtLoad 因缺少 `DEEPSEEK_API_KEY` 导致 enrich 阶段逐条报错。临时用 `launchctl setenv` 注入后才通过。
- Solution: 使用项目根目录 `.env` 或 `~/.claude/.env` 存放 API key，由 runtime env loader 在启动时加载（见 ADR-003）。不要依赖交互式 shell 的 `export`。
- Applies when: 配置任何非交互调度（cron、launchd、systemd）时——部署前确认 `.env` 文件包含所需 key，不要假设环境变量已存在。

## 2026-05-15 cron 与 launchd 不要同时启用

- Problem: 为绕过 macOS crontab TCC 阻塞，临时安装了 launchd fallback。之后 cron 恢复后如果不移除 launchd，会导致 pipeline 被双重触发（每 15 分钟执行两次）。
- Solution: 确保同一时间只启用一种调度方式。切换时先 bootout 旧的再安装新的。当前生产配置使用 cron。
- Applies when: 在调度方式之间切换时——检查是否有残留的 plist 或 crontab 条目。

## 2026-05-28 Cloudflare Tunnel 路径会主导 SSR 首屏延迟

- Problem: `/` 和 `/all` 已经改成 SSR preload 后，本地 origin TTFB 大多只有 7-43ms，但公开站点仍出现 2-6s TTFB spikes 和偶发 Playwright timeout。应用层 spinner/API 都已经消失，瓶颈在 Cloudflare Tunnel adapter 层。
- Solution: Homebrew 管理的 `cloudflared` 要先升级到当前版本；本次从 2026.2.0 升到 2026.5.2。生产 tunnel config 中公开域名 origin 应显式使用 `http://127.0.0.1:8000`，并设置 `region: us`、`edge-ip-version: "4"`，让连接落在 `lax/sjc` 这类 US IPv4 edge，避免自动路径注册到 HKG 等远端 edge。
- Applies when: 通过 tunnel launchd 服务暴露本地 FastAPI 服务，且生产 FCP/TTFB 明显慢于 `127.0.0.1:8000` origin。先看 `/tmp/ai-radar-tunnel.err` 的 registered tunnel locations，再跑生产和本地 TTFB 对照。

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
- Solution: 安全重启顺序——先暂停 cron（注释 crontab 里 `*/15 ... pipeline.sh` 那行）或等当前 pipeline 跑完锁释放，`ps` 确认无 `pipeline.sh` / `airadar.cli prefilter|score|enrich` 在跑，再 `launchctl kickstart -k "gui/$UID/<launchd-label-for-serve>"`。migrate 成功后再恢复 crontab。
- Applies when: 重启 serve（应用迁移、改配置、升级）时——任何会触发 `db.migrate()` 的 serve 重启都要先确认没有调度任务正持 DB 写锁，否则 migrate 崩溃导致站点短暂 down。实际发生于 2026-06-02 上线 wechat 文章解读 pipeline 期间。

## 2026-07-19 Cloudflare Free plan 建 Cache Rule 不要碰 Cache key section

- Problem: 为给 `/`、`/wechat` 及对应 API 的无搜索分页按 `page` 参数分别缓存，在 dashboard 新建 Cache Rule 时显式设置了 Cache key -> Query string。点 Deploy 触发升级套餐弹层、规则保存失败，Cache Rules 列表显示 0 active（谓词看似正确但整条规则根本没生效）。根因：自定义 Cache key 是 Cloudflare 付费功能，Free plan 不可用。
- Solution: Free plan 建 Cache Rule 时保持 Cache key 默认、完全不进入该 section。默认 cache key 本就包含**全部 query string**，所以「按 `page` 分别缓存」这类需求根本不需要自定义 cache key——不同 `page` 天然落不同缓存条目。去掉 Cache key 自定义后 Deploy 成功、规则 Active。
- Applies when: 在 Cloudflare Free plan 上创建任何 Cache Rule 时——只配 Cache eligibility / Edge TTL / Browser TTL，别碰 Cache key。若确需按某 query 参数分缓存，确认默认 key 已覆盖（默认含全 query string），不要为此显式设 Cache key 而撞付费墙。

## 2026-07-19 Cloudflare zone 默认 Browser Cache TTL 会覆盖 origin 的 Cache-Control

- Problem: origin 对可缓存分页响应发 `Cache-Control: public, max-age=90`，但公网下游看到的却是 `max-age=14400`（4h）。edge 命中正常（edge TTL 早已 respect origin），问题只在 browser TTL 这一层：CF zone 级默认 Browser Cache TTL（4h）覆盖了 origin 的 `max-age`，下发给浏览器的是 zone 值而非 origin 的 90s。对每 15 分钟更新的站，浏览器缓存 4h 会让用户长时间看到陈旧内容。
- Solution: 在 Cache Rule 里显式把 Browser TTL 设为 "Respect origin TTL"，让下游浏览器也用 origin 的 90s，而不是继承 zone 默认的 4h。edge TTL 保持 respect origin 不变。修复后公网 `max-age` 回到 90s、翻页 API `CF-Cache-Status: HIT`。
- Applies when: 站点经 Cloudflare 暴露、origin 用短 `max-age` 控制客户端刷新频率时——不要假设 origin 的 `Cache-Control` 会原样透传到浏览器。分别核对 edge 与 browser 两层 TTL：edge 层 miss 看 zone/rule 的 Edge TTL，浏览器看到的 `max-age` 看 zone/rule 的 Browser TTL；zone 默认 Browser TTL 会静默覆盖 origin，需在 Cache Rule 里 respect origin 才透传。

## 2026-08-18 Lighthouse 实例的 metadata app-id 不是控制台归属账号

- Problem: 排查新加坡图片代理主机时，从主机 metadata 读到 `app-id 1301555531`，与手头控制台账号 `AppId 1424748107` 不符，据此断言「这台机属于另一个腾讯云账号、需要换登录」。同时 CVM `DescribeInstances` 返回 0 台实例，被当作佐证。两个读数都被误读。
- Solution: 两台主机都是 **Lighthouse（轻量应用服务器）** 实例（`webserver-singapore` = `lhins-3nxwyynb` / `43.153.216.193`，`webserver-china` = `111.229.134.9`），跑在腾讯托管 VPC 里——metadata 里的 app-id 是**底层资源账号**，不是控制台归属账号；用个人微信登录即在同一账号下看到两台机器。`DescribeInstances` 全 0 也是同因：Lighthouse 不在 CVM 命名空间，要用 Lighthouse 的接口查。
- Applies when: 判断一台腾讯云主机归谁、或某个云 API 返回空列表时——先确认实例类型（Lighthouse / CVM）再解释读数。空列表与 app-id 不符都会伪装成「账号不对」，把人推向换登录这条昂贵且方向错误的处置。

## 2026-08-20 WeWe RSS 路线已退役，且本文早先两条条目引用的文件从未入 git

- Problem: 上面 2026-05-29 的两条 WeWe RSS 条目（容器出站代理与 loopback 抓取、docker compose 桥接服务进 launchd）描述的是**已退役路线**：`wewe` 服务已于 2026-06-06 从服务层移除，其容器又于 2026-08-20 手动停止（`exited`，保留未删除）。微信摄取现走 Mp2RSS + Wechat2RSS 双跑，见 [operations/wechat-ingestion.md](../operations/wechat-ingestion.md)。
- Solution: 后续读者按那两条去找文件时注意三件事（`git log --all -- <path>` 实查）：`deploy/launchd/ai-radar-wewe.plist` 这个**生成物**从未进入 git（入过的是 `ai-radar-wewe.plist.example`，且已随服务移除、只在历史里）；条目里写的 `RUNBOOK.md` 指的是 `deploy/wewe-rss/RUNBOOK.md`（**仍在**，「Keeping It Running」小节也还在），不是仓库根的 `RUNBOOK.md`——根目录那个文件从未存在。两条条目的**一般化教训仍然成立**且已在新路线上复用：容器的 `restart: unless-stopped` 管不了 Docker daemon 缺席，所以 `wechat2rss` 同样把「OrbStack 必须开机自启」写成显式依赖（见 [operations/services.md §隐含依赖](../operations/services.md#隐含依赖repo-外)）；宿主机代理变量会污染 loopback 抓取这一条也照旧适用。
- Applies when: 读本文 2026-05-29 那两条时，或按历史条目去寻找部署文件而遍寻不着时。

## 2026-08-20 上面 2026-05-28 的 Cloudflare Tunnel region 处方只对仍走 tunnel 的部署形态成立

- Problem: 上面 2026-05-28 那条给出的处方（升级 `cloudflared`、origin 显式指 `http://127.0.0.1:8000`、设 `region: us` 与 `edge-ip-version: "4"`）绑定的是「公网流量经 Cloudflare Tunnel 出去」这个前提。该前提对本产线**已不再成立**：公网生产现为 `news.aiplanet.live`，DNS 直解腾讯服务器、经 EdgeOne，**不经过这条 tunnel**；tunnel 上的 `aiplanet.live` 入口已退役并返回 502（见 [operations/services.md §Cloudflare tunnel shared ingress](../operations/services.md#cloudflare-tunnel-shared-ingress)）。照那条去调 region 不会影响当前公网延迟。
- Solution: 按该条动手前先确认自己的流量确实走 tunnel（`aiplanet.live` 一类经 tunnel 的 hostname，而不是 `news.aiplanet.live`）。当前 tunnel 仅承载 SJTU 站，那条处方对它仍适用。本产线公网侧的延迟排查改看 EdgeOne 与源站，不要再调 tunnel region。
- Applies when: 读上面 2026-05-28 那条时——它是**形态限定**的处方，不是通用的公网提速手段。


