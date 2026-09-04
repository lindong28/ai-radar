# 服务清单

> Mutable snapshot. ai-radar 长期运行的服务 + 自启机制 + 生命周期脚本 + Instructions 位置。
>
> **本目录（`docs/operations/`）是维护者产线 runbook，绑定这套具体实机拓扑**（主机名、端口、路径、外部账号都按本产线写）。fork 自己部署时按 [README](../../README.md) 走，本目录的读数只当参考。

## 服务

| 服务 | 自动启动 | 当前状态 | 生命周期脚本 | Instructions |
|---|---|---|---|---|
| serve | launchd, KeepAlive=true | 已加载 | `./install.sh serve` / `./uninstall.sh serve` / `./status.sh serve` | [deploy/launchd/ai-radar-serve.plist.example](../../deploy/launchd/ai-radar-serve.plist.example) |
| tunnel | launchd, KeepAlive=true | 已加载 | `./install.sh tunnel` / `./uninstall.sh tunnel` / `./status.sh tunnel` | [deploy/launchd/ai-radar-tunnel.plist.example](../../deploy/launchd/ai-radar-tunnel.plist.example) · [deploy/cloudflared/config.yml.example](../../deploy/cloudflared/config.yml.example) |
| ai-radar pipeline (15min) | cron (`*/15 * * * *`) | 在 user crontab | `./install.sh pipeline` / `./uninstall.sh pipeline` / `./status.sh pipeline` | [deploy/cron/ai-radar-pipeline](../../deploy/cron/ai-radar-pipeline) · launchd 替代模板见 [ai-radar-pipeline.plist.example](../../deploy/launchd/ai-radar-pipeline.plist.example) |
| alert | launchd, StartInterval=300, RunAtLoad=true | 已加载；A1–A7 使用 per-severity lifecycle，D3 定价提醒独立去重 | `./install.sh alert` / `./uninstall.sh alert` / `./status.sh alert` | [deploy/launchd/ai-radar-alert.plist.example](../../deploy/launchd/ai-radar-alert.plist.example) · [monitoring-alerting.md](monitoring-alerting.md) |
| performance probe (5min) | launchd, `StartInterval=300`, `RunAtLoad=true` | 当前未安装；旧 hourly cron 自 2026-07-24 起保持 PAUSED，等待 performance plan 收口 | `./install.sh performance-probe` / `./uninstall.sh performance-probe` / `./status.sh performance-probe` | [ai-radar-performance-probe.plist.example](../../deploy/launchd/ai-radar-performance-probe.plist.example) · [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) |
| LLM cost report | cron (`17 9 * * 1`) | 已在 user crontab；周一 09:17 经 `run-or-alert --key ai-radar-cost-report` 发送上一上海自然周 | `./install.sh cost-report` / `./uninstall.sh cost-report` / `./status.sh cost-report` | [deploy/cron/ai-radar-cost-report](../../deploy/cron/ai-radar-cost-report) |
| performance remediation (hourly) | cron（建议 `25 * * * *`，在 probe 后） | **当前禁用**；启用 gate 与安装步骤的唯一全文在 [monitoring-alerting.md §安装 remediation cron](monitoring-alerting.md#安装-remediation-cron启用-gate-全文) | `./run.sh performance-remediate` | [monitoring-alerting.md §安装 remediation cron](monitoring-alerting.md#安装-remediation-cron启用-gate-全文) |
| wechat2rss | docker compose（`restart: unless-stopped`），随 OrbStack 开机自启 | 运行中；`wx_wechat2rss` 源消费 `127.0.0.1:8080` | `cd deploy/wechat2rss && docker compose up -d / down`；`deploy/wechat2rss/healthcheck.sh`、`logs.sh` | [deploy/wechat2rss/RUNBOOK.md](../../deploy/wechat2rss/RUNBOOK.md) |
| wechat2rss healthcheck (20min) | cron (`11,31,51 * * * *`) | 在 user crontab | 手动跑：`deploy/wechat2rss/healthcheck.sh`（exit 0=健康；exit 1=发现异常并尝试通知，是否送达以同次输出为准） | 外部探活：崩掉的服务发不出自己的告警。五类终态各有 dedup-key `wechat2rss-{unreachable,apierr,noaccount,login,riskctl}`，经 `im-notify --alert` 直发，**不写 `data/alert-events.jsonl`**。健康时清除全部五个 key，并在「上一次探测报过告警 → 本轮健康」的转换上发一条恢复通知（探测结果记在 `deploy/wechat2rss/data/healthcheck.state`；清键或恢复通知发送失败时不写 healthy、下一轮重试；粒度是一次探测，两次 cron 之间的「恢复又复发」观察不到、按同一事故延续处理）；2026-09-04 前没有这一步，08-17 演练失败分支留下的签名把 08-30→09-04 停机的 335 次告警全部压成 `skipped(unchanged)`（`plans/20260816-mp2rss-replacement/state.md` ISSUE-016）（见 [monitoring-alerting.md §已送达通知历史](monitoring-alerting.md#已送达通知历史)） |
| shadow-observe (每 30 分钟) | cron (`7,37 * * * *`) | 在 user crontab | 维护者主工作树于 2026-09-01 实测 live 入口为 `/Users/lindong/research/ai-radar/plans/20260816-mp2rss-replacement/tools/shadow-observe.sh`（**未入 git，不随其它 checkout 分发**）；它调用的 [`shadow_compare.py` 归档副本](../plans/20260816-mp2rss-replacement/tools/shadow_compare.py)只用于保留计划期 provenance，不是 live 入口 | Mp2RSS ↔ Wechat2RSS 双跑覆盖率采样，直接读两个 feed、不读生产库。评估期临时项，路线定案后应移除该 cron |
| DB sync → 腾讯服务器 (5h) | cron (`41 1,6,11,16,21 * * *`)，`run-or-alert --key ai-radar-db-sync` 包裹，失败经 im-notify 告警、成功自复位 | 已启用；这是公网副本持续新鲜的 Mac producer | 手动跑：`deploy/sync/sync-db-cron.sh`（完整 cron wrapper）或 `deploy/sync/sync-db-to-server.sh`（裸 producer） | [deploy/cron/ai-radar-db-sync](../../deploy/cron/ai-radar-db-sync) · [ADR-013](../adr/013-db-sync-cron-agent-socket-auth.md) · [ADR-014](../adr/014-ship-base-only-db-and-rebuild-fts.md) |

probe 的专属 plist 经 `./run.sh performance-probe` 启动。crontab 条目的识别方式两种并存：**cost-report 用精确 marker**（行尾 `# ai-radar-cost-report`），**pipeline 没有 marker**——`install.sh` / `uninstall.sh` / `status.sh` 都按路径子串 `ai-radar/pipeline.sh` 匹配（三处 `grep`），所以同一台机器上有第二个 ai-radar checkout 时，卸载会连同名的另一行一起删掉。

下面四条 cron 不在 `install.sh` / `uninstall.sh` / `status.sh` 的管理范围内，改动只能手工改 crontab：DB sync、performance-remediate、shadow-observe、wechat2rss healthcheck。

`./install.sh` 会逐服务检查脚本可判定的依赖。`alert` 要求两个 webhook；`cost-report` installer 只检查 notification webhook，尚不验证部署机的 `~/.local/bin/im-notify`、`run-or-alert` 与仓库 `run.sh` 可执行性——**装完了也可能到点发不出周报**，先按 monitoring-alerting 的 preflight 手工验一遍（[ISSUE-014](../issues/cost-observability.md)）。cost-report 模板把 repo、命令和日志路径展开为绝对路径并显式设置 PATH；重复安装替换本条且保留无关 crontab，卸载只删除 `# ai-radar-cost-report` marker 条目。安装前置与 dry-run 见 [monitoring-alerting.md §LLM 成本报表与对账](monitoring-alerting.md#llm-成本报表与对账)。

## DB sync 职责、验证与故障证据

### 职责边界与 freshness path

| 位置 | 责任 | 不负责 |
|---|---|---|
| Mac cron + `deploy/sync/sync-db-cron.sh` | 每 5 小时启动 producer，恢复 cron 的 ssh-agent 环境，检查服务器 receipt age，并把 producer 非零退出交给 `run-or-alert` | 不接受 snapshot、不决定切流 |
| Mac `deploy/sync/sync-db-to-server.sh` | 以 `query_only` WAL reader 创建一致快照；更新并逐表对账持久 base-only shipping replica；生成 manifest v2；用 GNU rsync 发布 sidecar + DB；触发 server apply；轮询本轮 snapshot 直到 `committed`、`quarantined`、manual-block 或超时 | 不修改 live primary；不把 FTS 传到服务器；不把“上传完成”当“已服务” |
| Server `ai-radar-db-apply.service` | oneshot consumer：claim base-only artifact，在 inactive candidate 上重建 FTS，做 SQLite/HTTP/route gates，切换、回滚或 quarantine，并只在 consumer gates 全过后推进 basis/receipt | 不 pull Mac 数据，不产生新 snapshot，不承担 freshness 排期 |
| Server `ai-radar-db-apply.timer` | 安装但生产当前 disabled/inactive；若将来显式启用，只能 reconcile 已存在的 incoming/journal | 不是 producer，不能让公网数据自行变新 |

当前 5 小时 Mac cron 是持续新鲜的唯一生产入口。截至 2026-08-20，`logs/sync-cron.log` 里 2026-08-17 01:41 起的 **19 轮排期全部报 `sync OK`**，中途无失败轮；单轮耗时按同一轮 `sync start` 与 `sync OK` 的时间戳差算，最近 4 轮（08-20）为 **24–27 分钟**，最近 9 轮为 23–28 分钟（更早几轮到过 38 分钟）。

这些都是 **producer 侧日志读数**：它只证明 producer 认为本轮已 committed，本轮**未**核实 server 侧 receipt/journal 的 identity 一致性（怎么核实见下面「成功终态必须同时满足」）。最终频率也尚未根据传输量、端到端耗时、陈旧度与资源成本完成确认。

### 引入新表 / 新索引时的发布顺序（migration 019 起适用）

`deploy/sync/schema_gate.py` 在**代码部署**那一步用候选版本自己的 `migrate()` 去比对**活动库**，并要求它声明的每张表与索引在活动库中**已经存在**。所以顺序是单向的：

1. 本地 `./run.sh admin db migrate` —— 表与索引落进 Mac 的 primary。
2. 带回填的迁移要在这里跑完（如 `./run.sh admin db backfill-links`）。快照运的是**数据**，不是补数据的动作；服务器侧没有任何环节会替你回填。
3. `deploy/sync/sync-db-cron.sh`，等本轮报 `terminal state committed`。
4. 才能部署代码。

顺序反了不会得到一个含糊的错误：schema gate 会**拒绝这次代码部署**，旧 release 继续服务。这比"部署成功但查询失败"好，但排查时容易被读成"部署系统坏了"。

`item_links` 另有一层：它的账本（`item_links_backfill`）未标完成时，关联讨论会**回落到旧的全表扫描**——结果正确但慢。所以"忘了跑回填"的症状是页面变慢而不是报错，只能靠 `SELECT completed_at FROM item_links_backfill` 判。

### 只读验证入口

```bash
# Mac：下列匹配必须恰输出一条当前 DB sync entry；日志末尾应出现本轮 terminal state committed
crontab -l | rg '^[[:space:]]*41 1,6,11,16,21 \* \* \* .*run-or-alert --key ai-radar-db-sync -- .*/deploy/sync/sync-db-cron\.sh'
tail -n 200 logs/sync-cron.log

# Server：unit、active slot、serving snapshot、最近 apply 结果
ssh tencent-webserver-china 'cd ~/ai-radar && deploy/server/status-server.sh'

# Server authority：journal、已接受 receipt、apply 输出；不要用最新文件 mtime 代替 accepted receipt
ssh tencent-webserver-china 'cd ~/ai-radar && cat data/switch-journal.json && cat data/accepted-snapshot.json'
ssh tencent-webserver-china 'cd ~/ai-radar && tail -n 200 logs/db-apply.log && tail -n 200 logs/db-apply.err.log'

# 真实入口仍须健康；需要验证一轮内容时再按 manifest 的五字段 probe 比 IDs/count
curl -sf https://news.aiplanet.live/api/v1/healthz
```

成功终态必须同时满足：producer 报 `terminal state committed`；`switch-journal.json` 为 schema v2 `committed`；`accepted-snapshot.json` 的 `snapshot_id`、`manifest_sha256`、`serving_port` 与 journal 一致；server `basis/radar.db.upload` 的 SHA-256 等于该 `snapshot_id` 且没有 FTS objects；canonical health/search 命中新槽语义。只看到 rsync 成功、systemd oneshot 退出或 DB 文件出现都不是“已服务”的充分证据。

### 可能遇到的非成功状态

| journal state / producer 现象 | 含义与自动动作 | 证据位置 / 下一步 |
|---|---|---|
| `quarantining` / `quarantined` | 确定性 manifest、rebuild、等价或 consumer gate 失败，或 fresh retry 已耗尽；active release 保持或已回滚，失败 snapshot 不再自动回 incoming | `data/switch-journal.json` 给出 `failure_id/path/sha256` 绑定；`data/quarantine/<snapshot_id>/` 持久保存当时可捕获的 base/candidate/manifest 与 failure record，后者的 `evidence_status` 标明每项是 `captured`、`missing-at-failure` 或 `not-applicable`。先读 failure record 与 apply logs，不要删除 quarantine 或手改 journal |
| `retry_blocked_verifier_changed` | checkpoint 的 `verifier_identity` 与当前 verifier 不同，自动 fresh retry 被阻止，`recovery_action=manual-intervention` | journal 的旧/新 verifier identity + last failure；确认 consumer-first rollout 与 authority 后再决定处置，不能靠重启绕过 |
| `rollback_blocked_invalid_oracle` | post-switch 回滚 oracle 无法证明旧服务状态，basis/receipt 不推进，需人工裁决 | journal 的 `rollback_evidence`、`last_failure_*`，以及 apply logs；先保全两槽和旧 basis/receipt |
| `finalize_blocked_invalid_authority` | `consumer_verified` 后 artifact/manifest/active route authority 漂移，final commit 被阻止，需人工裁决 | journal 的 `authority_evidence`、`last_failure_*`；basis/receipt 仍未推进 |
| `rollback_failed` | consumer gate 失败后回切未收敛；journal 保留 `rollback-to-previous-serving` 恢复动作 | 先查 canonical health、active include、两槽 unit 与 journal；保留旧槽，按证据修复后才重新触发 apply reconcile |
| producer terminal poll timeout | 本轮是否已接受未知；apply 可能仍在运行，也可能停在非 terminal 状态 | 立即读 journal、receipt、`systemctl status ai-radar-db-apply.service` 与两份 apply log；不要直接再启动第二轮 producer |

`VERIFIER_VERSION` 当前为 `fts-apply-v5`，且属于 retry authority。若 journal 报 `retry_blocked_verifier_changed`，不要靠重启绕过；保全旧/新 verifier identity 与 last failure，交由代码 owner 按 [architecture.md 的 retry authority 契约](../architecture.md#mac-primary--tencent-serving-replica)裁决。

### Alert 判定与 lifecycle

`alert` 服务负责 A1–A7（`src/airadar/admin/alerts.py` 的 `RULESET` 七条），D3 定价提醒复用同一轮调度但不进入 page lifecycle。阈值、合并、degraded/in-progress 语义、severity 转换、投递与 ledger 的单一运行权威是 [monitoring-alerting.md §告警规则](monitoring-alerting.md#告警规则)；本服务清单只维护拓扑与生命周期入口，避免复制状态机细节后漂移。

> 已退役的 `wewe`（WeWe RSS docker bridge）已于 2026-06-06 从服务层移除（不再在脚本/注册表中）；其容器又于 **2026-08-20 手动停止**，当前为 `exited` 状态、**保留未删除**（其数据卷含已停用的微信读书登录态）。如需彻底清理，连同数据一起删除由用户执行，本仓不代劳。微信摄取现走 **Mp2RSS + Wechat2RSS 双跑取并集**（见 [wechat-ingestion.md](wechat-ingestion.md)）。如需回滚到 WeWe RSS：`deploy/wewe-rss/`（docker-compose + RUNBOOK）仍在，launchd plist 与脚本 wiring 从 git 历史恢复（移除 commit 见 git log）。

调度方式选择：pipeline 在 cron 和 launchd 之间二选一，**不要同时启用**——详见 [experiences/deployment.md 2026-05-15 条目](../experiences/deployment.md)。当前生产用 cron。

## 隐含依赖（repo 外）

| 依赖 | 必须的设置 | 验证方式 |
|---|---|---|
| cron 守护 | macOS 自带，默认运行 | `pgrep cron` |
| launchd | 系统自带，登录后自动运行 | `launchctl print gui/$UID` |
| pipeline LLM key | `DEEPSEEK_API_KEY` / `ARK_API_KEY` / `OPENAI_API_KEY` / `GLM_API_KEY` 任一 | 只读存在性：`grep -c '_API_KEY=.' .env ~/.claude/.env 2>/dev/null`（逐文件出计数，不回显值；`.env:0` 表示该文件里一个都没有）。**存在 ≠ 可用**：key 有效性只有真实调用才证明得了，日常由 A1 告警在生产调用上覆盖；要当场确认就实跑一次最小调用 `./run.sh prefilter --limit 1`（**会写一行 prefilter 结果，不是只读**），看它是否报 provider 错误。**不要**拿 `./install.sh pipeline` 当验证——它会写 crontab 与 `.env` |
| domain-routing selector | system-config 提供 `check-proxy-status --format=kv`、domain router 与 route audit；AI Radar 不安装或切换它 | `./run.sh egress-preflight` 应输出 `status=healthy policy_id=domain-routing-v2 policy_sha256=<64 hex>`；失败时不得安装/重跑 pipeline。此读数不证明真实出口，live 验收见下节边界 |
| alert 发送器 | `~/.local/bin/im-notify` + page 的 `FEISHU_GENERAL_ALERT_WEBHOOK` + notice 的 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK`；两个 webhook 任缺一个都拒绝 alert 安装 | `test -x "$HOME/.local/bin/im-notify"` 后运行下文无发送 preflight；已安装时检查 plist 同时有两个 key |
| Playwright Chromium | 微信原文抓取与默认 `performance-probe` | 部署前显式运行 `uv run playwright install chromium`；`install.sh` 不自动下载或校验 |
| Cloudflare tunnel | `deploy/cloudflared/config.yml` | 存在还不够，要判它不是 example 占位：`rg -c '^tunnel: [0-9a-f]{8}-' deploy/cloudflared/config.yml`（真实 tunnel UUID）与 `rg '^\s+- hostname: ' deploy/cloudflared/config.yml`（应列出实际托管的 hostname，不含 `example.com`） |
| OrbStack（Docker daemon） | 必须设为**开机自启**（OrbStack → Settings → General → "Start at login"） | `orbctl status`。未自启时 `wechat2rss` 容器在重启后静默缺席——`restart: unless-stopped` 只管容器进程崩溃，管不了 daemon 不在 |
| 图片出口代理（新加坡） | serve 主机 `.env` 的 `AI_RADAR_IMG_PROXY_URL`（现指 `127.0.0.1:39148`）+ 上海主机 systemd 服务 `ai-radar-img-tunnel`（SSH 隧道到 SG tinyproxy，见下节） | 走下节「诊断顺序」，**不要**只看公网 `/img` 的状态码——它对每种失败都回 404，读数区分不了故障层 |
| Cloudflare Cache Rule | zone `aiplanet.live` 上的 `AI Radar short public pagination TTL`（见下节） | 当前生产旁路不适用；将来重新经 Cloudflare 代理后，同一 public 分页 URL 第二次请求应为 `CF-Cache-Status: HIT` |

## AI Radar 域名 selector 出网

AI Radar 不再读取 `AI_RADAR_PROXY_FILE` 或 `current-proxy`，也不信任父进程的 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 及小写形式。每轮 pipeline 在第一个外部 stage 前运行 strict preflight；缺字段、重复/畸形字段、mode/policy/address mismatch、router/三路 upstream/route attribution/overall 任一非 healthy 或 status 命令失败，都会写 `=== egress preflight FAIL (exit N) ===` 并在外部 stage 前退出。

只读排障顺序：

1. 在 pipeline 日志确认 preflight 是 `OK` 还是 `FAIL`；不要再找旧的 `=== egress proxy:` 行。
2. `FAIL` 时运行 `check-proxy-status --format=kv`，核对 `domain-routing` mode、`domain-routing-v2` policy identity、projection matched、router/三路 upstream/route attribution/overall healthy。不要用端口探活或父进程 proxy env 代替这些字段。
3. preflight `OK` 但请求失败时，用 `agent-proxy-route-audit --format=jsonl` 按 hostname 联合查看 `selected_route`、`outcome` 与 `outcome_scope`。`outcome_scope=upstream-application` 且 `outcome=unknown` 表示线路已归因但该事件不观测应用结果；`proxy-connect` 的 success/failure 表示代理 CONNECT 结果；`direct-sentinel` 的 success 只证明受控直连哨兵。`airadar.egress.audit` 只证明调用点以哪个 policy identity 尝试 launch，不能证明实际走了 GCP、Tencent 或 direct。

路由契约是 Anthropic-owned hostname → GCP SG 且 fail closed；OpenAI/ChatGPT/X → OpenAI provider route（Tencent primary、ZYT fallback，两者均不可用时 fail closed）；Ark/DeepSeek/RSS/news/web → direct。域名表只在 system-config，AI Radar 不复制；preflight 的 aggregate healthy 不等于 Tencent primary healthy，实际档位与单次出口分别看 `tencent_route_mode` 和 route audit `selected_route`。应用侧调用点闭包由 `src/airadar/egress_registry.py` 与 guard test 持有；新增网络入口必须先分类。loopback/synthetic 请求与 `im-notify` 这类明确 direct 的本地工具不依赖 selector status，后者会先清除父进程六个 proxy 变量。外部 `AI_ASSISTANT_ROOT` 还必须满足 [summary-agent selector compatibility contract](../references/ai-assistant-contract.md#selector-compatibility-receipt)，仅传入清洗后的标准 env 不构成兼容证据。

部署边界：本仓的 offline tests 使用 fake status/selector 与动态 loopback listener；它们验证 strict parser、ambient cleanup、client/subprocess/Playwright 选择和 fail-closed，不验证 macmini 的真实出口 IP、GCP/Tencent 可达性、断线或 T1 route audit。上述 live route/exit/disconnect/fail-closed 验收由 system-config 的 macmini assembly 负责，完成前不得把本节状态写成“生产已验证”。

## 图片出口代理（新加坡，repo 外常驻服务）

上海 serve 主机**到 `pbs.twimg.com` 的连接被上游阻断**——判别性证据：在该主机上 tcpdump，对 loopback 的对照流量捕获 45 个包（证明抓包本身工作），对 twimg 的连接捕获 0 个包，即丢弃发生在网卡之上游，不是本机 iptables 或 DNS。新加坡主机 `tencent-webserver-sg` 同一 URL 返回 200 / 41ms。故 X 推文媒体经该主机上的 tinyproxy 转发（[ADR-057](../adr/057-fetch-x-tweet-media-through-a-singapore-egress-proxy.md)）。

### 传输拓扑：SSH 隧道，不是直连（GFW）

**明文直连 SG:39147 不成立**：正向代理把 `CONNECT pbs.twimg.com:443` 明文发在中国→新加坡这一跳，GFW 按主机名注入 RST（实测：CONNECT `example.com` 得 `403 Filtered`，CONNECT `pbs.twimg.com` 得 `Connection reset by peer`，3/3 确定性 ~0.13s）。故实际链路是：

```
上海 serve → 本机 127.0.0.1:39148 → [SSH 隧道, 加密] → SG 127.0.0.1:39147 (tinyproxy) → pbs.twimg.com
```

- 上海主机 systemd 服务 **`ai-radar-img-tunnel`**（`ssh -L 39148:127.0.0.1:39147 ubuntu@43.153.216.193`，`Restart=always`、开机自启）。用受限专用 key `~ubuntu/.ssh/sg_img_tunnel`，SG 侧 authorized_keys 前缀 `restrict,port-forwarding,permitopen="127.0.0.1:39147"`（纯隧道，无 shell）。
- 上海 `.env` 的 `AI_RADAR_IMG_PROXY_URL` 指 `http://<user>:<pw>@127.0.0.1:39148`（tinyproxy 认证不变，只是 host:port 变本地隧道口）。
- **SG 防火墙放行 39147 的入站规则（历史，已不承载流量）**：隧道化后流量走 SSH 22，该入站规则已不再被用到，可移除；留着无害。
- 运维：`ssh tencent-webserver-china sudo systemctl status ai-radar-img-tunnel`；隧道断则 `journalctl -u ai-radar-img-tunnel`。

tinyproxy 本身（在 SG）不由本仓的 `install.sh` / `status.sh` 管理，改动只在该主机上做。2026-08-18 读回的生效配置：

| 项 | 值 | 为什么是这个值 |
|---|---|---|
| 版本 / 服务 | tinyproxy 1.11.1，systemd `active` + `enabled`，`Restart=on-failure` | `Restart` 由 drop-in 显式设置——**默认是 `no`**，实测 `kill -9` 后服务停在 `failed` 不自愈；设置后验证 PID 979054 → 979086 自动重启 |
| 端口 / 监听 | `Port 39147`，`Listen 0.0.0.0` | 现由 SSH 隧道经 SG 回环访问，实际到达的源是 `127.0.0.1`；绑 `0.0.0.0` 是历史（直连时代），可收窄到 `127.0.0.1` |
| 入站 ACL | `Allow 111.229.134.9` + `Allow 127.0.0.1` | 隧道化后有效的是 **`Allow 127.0.0.1`**（SSH 转发在 SG 侧以回环发起）；`Allow 111.229.134.9`（历史，已不承载流量）随直连防火墙规则一并可清 |
| 认证 | `BasicAuth`，口令只存在于该机 `/etc/tinyproxy/.credpw`（600 root:root）与 serve 主机 `.env`（600） | 口令不进仓库、不进对话 |
| 出站限制 | `ConnectPort 443` + `Filter` + `FilterDefaultDeny Yes` + `FilterType ere`，名单仅 `^pbs\.twimg\.com$` | 默认拒绝、逐条放行：即使凭据泄露，它也只能连这一个域名的 443 |
| 云防火墙（历史） | 两台主机是 **Lighthouse（轻量应用服务器）** 实例，同属控制台账号 `AppId 1424748107`（`webserver-singapore` = `lhins-3nxwyynb` / `43.153.216.193`；`webserver-china` = `111.229.134.9`）。曾在 SG 实例防火墙加入站 `111.229.134.9/32 → TCP 39147 允许`（历史，已不承载流量：隧道化后不需要）。（查这两台机归属时的一条教训见 [experiences/deployment.md](../experiences/deployment.md) 2026-08-18 条目。） |

**已知风险（已接受）**：tinyproxy 1.11.1 存在未修补的 CVE-2026-31842。接受依据是暴露面已收窄到单 IP + 认证 + 仅 CONNECT 443 + 单域名白名单，且该主机不承载其他服务。上游发版后应跟进升级。

**故障表现是静默的**：代理不可达时 `/img` 返回 404，前端 `onerror` 把图隐藏掉，页面表现为「所有 X 卡片都没有图」——不报错、不降级提示、监控无告警。

**诊断顺序（`/img` 的 404 本身区分不了故障层）**。`/img` 对每一种失败都返回 404 是刻意的（见 ADR-057），代价是这个读数在「代理挂了」「图片本身被删了」「认证过期」几种情况下完全相同，所以**不要**从它开始判断。按下面从内到外走，每一步的读数只与一种原因相容：

1. **代理主机上自验**（回环不经安全组，故安全组未开时也能跑）。以 root 执行：

   ```bash
   U=$(awk '/^BasicAuth/{print $2}' /etc/tinyproxy/tinyproxy.conf); P=$(cat /etc/tinyproxy/.credpw)
   # 阳性：带认证 + 白名单域名 → 隧道建立（twimg 对裸根回 400，非 000 即通）
   https_proxy="http://$U:$P@127.0.0.1:39147" curl -sS -o /dev/null -w '%{http_code}\n' https://pbs.twimg.com/
   # 阴性 A：非白名单域名 → response 403（FilterDefaultDeny 在拦）
   https_proxy="http://$U:$P@127.0.0.1:39147" curl -sv https://example.com/ 2>&1 >/dev/null | grep response
   # 阴性 B：不带认证 → 407 Proxy Authentication Required
   curl -sv -x http://127.0.0.1:39147 https://pbs.twimg.com/ 2>&1 >/dev/null | grep response
   # 阴性 C：错口令 → 401（证明真在校验，而非接受任意口令）
   curl -sv -x "http://$U:wrongpassword@127.0.0.1:39147" https://pbs.twimg.com/ 2>&1 >/dev/null | grep response
   ```

   2026-08-18 实测：400 / 403 / 407 / 401，四项符合预期。四项都对 → tinyproxy 本身没问题，往下走第 2 步。

2. **SSH 隧道健康**（上海主机）：`systemctl is-active ai-radar-img-tunnel` 应为 `active`，`ss -lntp | grep 39148` 应在监听。不活 → `journalctl -u ai-radar-img-tunnel` 看 SSH 连不上 SG 的原因（key、SG sshd、22 端口）。

3. **从 serve 主机连代理**（`$AI_RADAR_IMG_PROXY_URL` 现指 `127.0.0.1:39148`，即隧道本地口）：

   ```bash
   printf 'proxy = "%s"\n' "$AI_RADAR_IMG_PROXY_URL" \
     | curl -K - --connect-timeout 5 -sv https://pbs.twimg.com/ 2>&1 >/dev/null \
     | sed -E 's/(Authorization: Basic ).*/\1<redacted>/' \
     | grep -E 'Connected|refused|timed out|response [0-9]{3}'
   ```

   代理 URL 含 BasicAuth 凭据，此命令有意让它**既不进 argv、也不进 stdout**——两条独立的泄露路径，各堵一处：

   - **不进 argv**：不要写成 `curl -x "$AI_RADAR_IMG_PROXY_URL"`——命令行参数对同机任何用户的 `ps`／进程监控可见。改为用 `printf`（shell 内建，不 fork，凭据不落任何进程的 argv）把 `proxy = "…"` 从 stdin 喂给 `curl -K -`（从 stdin 读配置）。实测：旧写法 argv 里是 `-x http://user:pass@…`，新写法 argv 只有 `curl -K - … https://pbs.twimg.com/`。
   - **不进 stdout**：`curl -v` 会打印它**发出**的 `> Proxy-Authorization: Basic <base64(user:password)>`。`sed` 先把任何 `Authorization: Basic …` 打码再进 grep（即使日后有人加宽 grep 也不泄露）；grep **不要**含裸 `Proxy` token，它会连那行请求头一起放出——要判的 `401`/`403`/`407` 由 `response [0-9]{3}` 抓取（curl 对失败的 CONNECT 输出 `* CONNECT tunnel failed, response NNN`，实测覆盖三个码）。

   连不上（`refused`/`timed out`）→ 查隧道服务（第 2 步）；`407`/`401` → `.env` 里的口令与该机 `/etc/tinyproxy/.credpw` 不一致。这一步经隧道打到 tinyproxy，所以隧道好、这步才有意义。

4. **公网入口**：`curl -s -o /dev/null -w '%{http_code}\n' 'https://news.aiplanet.live/img?url=<某条 X 条目当前实际引用的图片 URL>'`。**必须是 GET**——`/img` 只注册 GET，`curl -I` 发 HEAD 会得到 `405 Allow: GET`、根本走不到代理链路。前两步都正常而这里仍 404，先确认那个 URL 本身还活着（推文删除后图片即失效）——**不要拿一个写死在文档里的 URL 当探针**，它迟早会被删掉，届时这一步会稳定报 404 并把人指向代理。取当前 URL：
   `sqlite3 data/radar.db "SELECT json_extract(extra_json,'\$.x_media[0].url') FROM items WHERE json_array_length(json_extract(extra_json,'\$.x_media'))>0 ORDER BY published_at DESC LIMIT 1"`

## Cloudflare Cache Rule（public 分页边缘缓存）

`aiplanet.live` zone 上有一条 **repo 外**的 Cloudflare 边缘缓存配置（Cache Rule）。**当前生产不在其路径上**：`news.aiplanet.live` DNS 直解腾讯服务器 IP、不经 Cloudflare 代理（响应无 `cf-ray`/`CF-Cache-Status`），故边缘缓存与其 HIT 验证暂不适用；origin 侧缓存头契约仍在生效并已实测正确。本节保留规则事实与验证步骤，供公网主机将来重新经 Cloudflare 代理时启用。它不是 launchd / cron 服务，`install.sh` / `status.sh` 不管理，改动只在 Cloudflare dashboard 上做。

规则名 **`AI Radar short public pagination TTL`**（2026-07-19 留证时为 **Active**；本次未刷新 dashboard 状态），位置 Cloudflare dashboard → zone `aiplanet.live` → **Caching** → **Cache Rules**。公网主机名现为 `news.aiplanet.live` 且暂不经 Cloudflare（见上）；将来重新代理时先在 dashboard 核对规则仍启用且表达式覆盖该主机名。

| 项 | 值 |
|---|---|
| 匹配表达式 | `GET` 且 `http.request.uri.path in {"/" "/wechat" "/api/v1/curated" "/api/v1/wechat"}` 且 URL args 不含 `q`（`not any(http.request.uri.args.names[*] == "q")`） |
| Cache eligibility | **Eligible for cache**（HTML/JSON 默认不缓存，靠这一项开启） |
| Edge TTL | **Use cache-control header if present, bypass if not**（fail-closed：用 origin 的 90s 新鲜 + 30s SWR；origin 无头则不缓存，不设 origin-ignoring 固定 TTL） |
| Browser TTL | Respect origin TTL（不覆盖） |
| Cache key | **不要设自定义 Cache key** —— Free plan 下自定义 Cache key 是付费功能，会触发付费墙导致 Deploy 失败；保持默认（默认已保留全部 query 参数，page 1/page 2、limit 变体不会串味） |
| 规则顺序 | 放在任何同样设置 Cache eligibility / Edge TTL 的既有规则**之后**（last matching value wins） |

**origin 侧配套（缺一则边缘缓存不生效）**：serve 对上述四条路径的安全分页变体（HTML 只认 `page`，API 认 `page`+`limit`）发 `Cache-Control: public, max-age=90, stale-while-revalidate=30`；带 `q=`、其他筛选键（`category`/`date`/`run_id`/未知键）或非 200 响应发 `Cache-Control: private, no-store`。这些公开路由不读 cookie、不建 session、不发 `Set-Cookie`。header 代码改动需重启 serve 才生效；Cache Rule 可在重启前后应用，但 HIT 验证只有两者都上线后才有意义。

**运维验证**：

```bash
# 1. origin 直连——四条 cache-safe 路径应回 public,max-age=90,swr=30
curl -sS -D - -o /dev/null 'http://127.0.0.1:8010/wechat?page=1'   # 端口按本机 serve 实际绑定（本产线 Mac=8010）
curl -sS -D - -o /dev/null 'http://127.0.0.1:8010/api/v1/curated?page=2&limit=40'
# q= / 筛选 / 非 200 应回 private,no-store
curl -sS -D - -o /dev/null 'http://127.0.0.1:8010/wechat?q=&page=1'

# 2. 经 CF——同一 cache-safe URL 两秒内请求两次，第二次应 CF-Cache-Status: HIT 且带 Age
curl -sS --compressed -D - -o /dev/null 'https://news.aiplanet.live/wechat?page=1'; sleep 2
curl -sS --compressed -D - -o /dev/null 'https://news.aiplanet.live/wechat?page=1'
# 3. 搜索仍不可缓存——q= 请求应 private,no-store，无 Age、无 HIT
curl -sS --compressed -D - -o /dev/null 'https://news.aiplanet.live/wechat?q=OpenAI&page=1'
```

第二次仍是 `DYNAMIC`/`BYPASS` 时，依次检查规则顺序、表达式、Edge TTL 模式与 origin header，再考虑动应用代码。命中效果反映在 `performance-probe` 的旅程延迟样本上；实测数字与测量协议见 [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控)。

> 无法用 zone API 自动化：现有 `CLOUDFLARE_API_TOKEN` 能读 zone 但无 rulesets 权限（`/zones/<zone>/rulesets` → 403），故此规则只在 dashboard 手工维护；不要为此拓宽或替换该 token。

## EdgeOne 节点缓存规则对账与 purge

EdgeOne 对 `news.aiplanet.live` 下的精确路径 `/style.css` 与 `/app.js` 强制节点缓存 7 天（[ADR-039](../adr/039-route-news-through-edgeone-dns-only-cname.md)「决策」节）。该规则住在腾讯云控制台，是**仓外权威**：控制台新增一条强制缓存路径时，仓内 `tests/test_frontend_asset_versions.py` 仍会全绿，而那条路径上的资源若没有 `?v=` 版本串，发布后会在边缘陈旧整个 TTL——2026-08-17 的事故正是这个失效形态（见 [前端经验](../experiences/frontend.md)）。

### 凭据

腾讯云 CAM 最小权限子账号密钥，只需两个 action：`teo:DescribeL7AccRules`（读规则）与 `teo:CreatePurgeTask`（清缓存）。三个值放进 gitignored `.env`：

**这把密钥不能改规则，这是实测而非推断。** 2026-08-20 尝试 `teo:ModifyL7AccRule` 被拒：

```
AuthFailure.UnauthorizedOperation
you are not authorized to perform operation (teo:ModifyL7AccRule)
resource (qcs::teo:::zone/zone-3tqc1sj4x482) has no permission
```

调用在任何变更发生之前就被拒，事后读回确认线上规则与调用前**逐字相同**。所以规则变更只有两条路：在控制台改（ADR-039 本来就把控制台定为仓外权威），或先给该子账号加上 `teo:ModifyL7AccRule` ——后者会扩大这把密钥的爆炸半径，值不值得是一次单独的决定，不该顺手做。


| 键 | 说明 |
|---|---|
| `EDGEONE_SECRET_ID` | 子账号 SecretId |
| `EDGEONE_SECRET_KEY` | 子账号 SecretKey |
| `EDGEONE_ZONE_ID` | 站点 ID，形如 `zone-xxxx` |

### 密钥会因闲置被自动禁用

腾讯云自 2025-06-30 起分批**自动禁用超过 90 天未使用的 AccessKey**（登录页公告）。本密钥只在部署前对账时使用，闲置超 90 天是常态，因此它被自动禁用只是时间问题。

届时的表现是 `check` 返回 **exit 2（未核实）**并打印 API 错误——不会冒充通过，但也不会自己说清根因。排查时先到 CAM「访问密钥」页确认该密钥状态，而不是先怀疑权限或 ZoneId 配错。恢复方式是在控制台重新启用或新建密钥并更新 `.env`。

### 命令与退出码

```bash
./run.sh admin edgeone check                      # 对账；部署前跑
./run.sh admin edgeone check --update-snapshot    # 审阅后接受当前规则，刷新仓内快照
./run.sh admin edgeone purge --url https://news.aiplanet.live/style.css?v=xxx
```

| 退出码 | 含义 |
|---|---|
| 0 | 已核对，与 `web/edgeone-cache-rules.json` 一致 |
| 1 | 已核对，发现漂移 |
| 2 | **未核对**——凭据未配置或 API 调用失败 |

退出码刻意三值：把"未核实"与"已核实通过"分开，否则未配置的检查会以 exit 0 冒充通过。判定依据是**整份规则集与仓内快照的差异**，不是从规则条件里解析路径——新版规则引擎的匹配条件是表达式字符串，解析漏一条就会报出"无漂移"，那正是本机制要防的失效。

漂移属实且是有意变更时：先把新路径加进 `scripts/bump_frontend_assets.py` 的 `ASSETS`（使其获得内容派生的版本串），再 `--update-snapshot`。这个顺序是**强制**的——`--update-snapshot` 在发现强制缓存路径未被 `ASSETS` 覆盖、或匹配条件超出它能读懂的形态时会拒绝落盘并返回 2。因为快照只能证明「与上次一样」，不能证明上次接受的状态安全：把一个没有版本串的路径钉进基线，此后它就永远不会再被报出来。

**检查范围的边界**：只有让边缘**覆盖源站新鲜度**的规则（`CustomTime` / `IgnoreCacheControl`）才被要求有 `?v=` 覆盖。`FollowOrigin` 类规则（当前是 `/wechat` 与 `/api/v1/wechat`）由源站自己的 `Cache-Control` 治理，每次 `check` 会把它们列成 `ORIGIN-GOVERNED` 但**不判定**——因为「源站现在发不发这个头」从公网观察不到：一次公网 GET 可能命中边缘缓存（`EO-Cache-Status: HIT`），返回的是缓存对象的旧头而非源站当前的响应。这部分判断由录基线的人承担：`--update-snapshot` 就是「我看过这些路径并认可」的签字。

（排查缓存问题时注意：本站同一路径 **HEAD 返回 `private, no-store`、GET 返回 `max-age=90`**，用 HEAD 会得到相反结论。）

条件形态采用白名单：只有 `${http.request.uri.path} in ['…']`（可与 host 相等子句 AND）会被判为已理解，`not in`、`contains`、`full_uri`、文件扩展名匹配、以及任何嵌套在 `SubRules` 里的缓存规则一律判为未核实（exit 2），不会被当成已覆盖。

## Cloudflare tunnel shared ingress

The `ai-radar` tunnel configuration still contains one retired AI Radar ingress and one active shared-site ingress:

| Hostname | Local service | Owner repo | Notes |
|---|---|---|---|
| `aiplanet.live` | `http://127.0.0.1:8000` | `~/research/ai-radar` | **已退役入口**：Mac serve 已改绑 8010（本地 plist 明文禁止回绑 8000，仅限局域网/tailscale 预览），该 ingress 现回 502；域名下线待上游 P5。AI Radar 公网生产 = 腾讯服务器现存进程承载的 `news.aiplanet.live`；repo-owned 双槽 unit 当前未安装——**本表因此描述不了腾讯服务器上真正在跑的那个进程**，缺口记在 [docs-quality issue](../issues/docs-quality.md) |
| `sjtu.aiplanet.live` | `http://localhost:8100` | `~/research/sjtu-aaa` | SJTU 3A alumni site. `/admin` 门禁由 Cloudflare Access 承担（2026-08 起；**不得**在 tunnel 配置加回历史上的 `http_status:403` 规则——见 `~/research/sjtu-aaa/docs/operations/services.md` 的禁止说明，以该仓为权威）。 |

Before editing, reinstalling, or removing this tunnel, inspect `~/research/sjtu-aaa/docs/operations/services.md` and preserve the SJTU ingress rules. A catch-all or rewritten tunnel config that only keeps `aiplanet.live` will silently take the SJTU site offline even if AI Radar still looks healthy. After any tunnel change, verify both:

```bash
(
  set -e
  curl -sf https://news.aiplanet.live/api/v1/healthz   # AI Radar 公网生产（腾讯服务器，不经此 tunnel）
  curl -sf https://sjtu.aiplanet.live/api/v1/healthz  # SJTU 站仍经本 tunnel，改动前后必须验证
  test "$(curl -s -o /dev/null -w '%{http_code}' https://aiplanet.live)" = 502
  case "$(curl -s -o /dev/null -w '%{http_code}' https://sjtu.aiplanet.live/admin)" in 302|403) ;; *) exit 1 ;; esac
)
```

## 验证（新机器 bring-up / 大改动后跑一遍）

```bash
./status.sh                                        # 受管服务总览
curl -sf http://127.0.0.1:8010/api/v1/healthz && echo serve_ok   # 本产线 Mac serve 现绑 8010（generic fork 按自己的端口）
curl -sf https://news.aiplanet.live/api/v1/healthz && echo public_ok      # 公网生产（腾讯服务器，不经 tunnel）
# 本 tunnel 现只承载 SJTU 站，其验证组见 §Cloudflare tunnel shared ingress
./status.sh alert
uv run pytest tests/test_admin_alerts.py -q -k 'send_alert_message_calls_im_notify_alert_without_dedup or send_alert_message_routes_notice_without_alert_flag'
./run.sh performance-probe --origin-url http://127.0.0.1:8010 --public-url https://news.aiplanet.live
./run.sh fetch                                      # pipeline + Mp2RSS feed 联通性；保留真实退出码
```

alert 的双通道配置、无发送 preflight，以及「`./run.sh admin alert-check` 会发真实消息、不是无害 smoke」这条告诫，都以 [monitoring-alerting.md §im-notify 飞书双通道](monitoring-alerting.md#im-notify-飞书双通道) 为准；上述 pytest 只验证 mock 投递路由，不发送真实消息。

当前不要把 `performance-remediate` 当 bring-up smoke 执行；启用 gate 与安装步骤以 [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) 为准。

`./status.sh` 输出每个服务一行：是否 loaded / pid / crontab 状态（pipeline）/ 日志位置。`alert` 与 `performance-probe` 都是周期任务，正常完成单次运行后可能显示 `loaded ✓ (no pid)`。

## 安装 / 卸载 / 切换

```bash
./install.sh <服务>       # 安装，或对已装的服务重新生成配置并重载
./uninstall.sh <服务>     # 单个
./uninstall.sh            # 全部
./status.sh               # 只读面板
```

`./install.sh <服务>` 是**唯一**的「改配置后让它生效」入口：它重新生成 plist 并对已加载 job 执行 bootout/bootstrap。改了 alert 的任一 webhook 后**不要**只 `kickstart -k`——那不刷新 launchd 已烘焙的 `<EnvironmentVariables>`，旧 webhook 会继续用下去。

当前不要运行无参数 `./install.sh`：它会同时安装仍应保持停用、且默认 origin 仍错误的 `performance-probe`——**装上就会按错端口探测并可能误报**（[ISSUE-017](../issues/cost-observability.md#issue-017--performance-probe-默认-origin-仍假定-serve-在-8000)）。显式逐个安装需要的服务。

告警的 severity、重试、去重与 ledger 语义以 [monitoring-alerting.md §告警规则](monitoring-alerting.md#告警规则) 为单一运行权威。

pipeline 在 cron ↔ launchd 之间切换：先 `./uninstall.sh pipeline`，再手动 `launchctl bootstrap` launchd plist（暂未做成脚本——cron 是当前生产选择）。

## 相关参考

- [README.md §服务](../../README.md#服务) — 用户视角的脚本入口表
- [docs/operations/monitoring-alerting.md](monitoring-alerting.md) — `/admin` dashboard、A1–A7 与 D3 告警、周报、飞书 webhook 与旅程延迟
- [docs/experiences/deployment.md](../experiences/deployment.md) — 历史踩坑（env 不继承、cron/launchd 共存、tunnel region、docker compose 守护）
- [docs/operations/wechat-ingestion.md](wechat-ingestion.md) — 微信公众号摄取（Mp2RSS + Wechat2RSS 双跑与跨源去重、两个 feed URL 的配置、头像 backfill、迁移留尾记录）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 旧 WeWe RSS 微信源添加流程（已停用，仅回滚参考）
