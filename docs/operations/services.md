# 服务清单

> Mutable snapshot. ai-radar 长期运行的服务 + 自启机制 + 生命周期脚本 + Instructions 位置。

## 服务

| 服务 | 自动启动 | 当前状态 | 生命周期脚本 | Instructions |
|---|---|---|---|---|
| serve | launchd, KeepAlive=true | 已加载 | `./install.sh serve` / `./uninstall.sh serve` / `./status.sh serve` | [deploy/launchd/ai-radar-serve.plist.example](../../deploy/launchd/ai-radar-serve.plist.example) |
| tunnel | launchd, KeepAlive=true | 已加载 | `./install.sh tunnel` / `./uninstall.sh tunnel` / `./status.sh tunnel` | [deploy/launchd/ai-radar-tunnel.plist.example](../../deploy/launchd/ai-radar-tunnel.plist.example) · [deploy/cloudflared/config.yml.example](../../deploy/cloudflared/config.yml.example) |
| ai-radar pipeline (15min) | cron (`*/15 * * * *`) | 在 user crontab | `./install.sh pipeline` / `./uninstall.sh pipeline` / `./status.sh pipeline` | [deploy/cron/ai-radar-pipeline](../../deploy/cron/ai-radar-pipeline) · launchd 替代模板见 [ai-radar-pipeline.plist.example](../../deploy/launchd/ai-radar-pipeline.plist.example) |
| alert | launchd, StartInterval=300, RunAtLoad=true | 已加载；A1–A6 使用 per-severity lifecycle，D3 定价提醒独立去重 | `./install.sh alert` / `./uninstall.sh alert` / `./status.sh alert` | [deploy/launchd/ai-radar-alert.plist.example](../../deploy/launchd/ai-radar-alert.plist.example) · [monitoring-alerting.md](monitoring-alerting.md) |
| performance probe (5min) | launchd, `StartInterval=300`, `RunAtLoad=true` | 当前未安装；旧 hourly cron 自 2026-07-24 起保持 PAUSED，等待 performance plan 收口 | `./install.sh performance-probe` / `./uninstall.sh performance-probe` / `./status.sh performance-probe` | [ai-radar-performance-probe.plist.example](../../deploy/launchd/ai-radar-performance-probe.plist.example) · [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) |
| LLM cost report | cron (`17 9 * * 1`) | 已在 user crontab；周一 09:17 经 `run-or-alert --key ai-radar-cost-report` 发送上一上海自然周 | `./install.sh cost-report` / `./uninstall.sh cost-report` / `./status.sh cost-report` | [deploy/cron/ai-radar-cost-report](../../deploy/cron/ai-radar-cost-report) |
| performance remediation (hourly) | cron（建议 `25 * * * *`，在 probe 后） | **当前禁用**；启用 gate 与安装步骤见 runbook | `./run.sh performance-remediate` | [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) |
| DB sync → 腾讯服务器 (5h) | cron (`41 1,6,11,16,21 * * *`)，`run-or-alert --key ai-radar-db-sync` 包裹，失败经 im-notify 告警、成功自复位 | 已启用；这是公网副本持续新鲜的 Mac producer | 手动跑：`deploy/sync/sync-db-cron.sh`（完整 cron wrapper）或 `deploy/sync/sync-db-to-server.sh`（裸 producer） | [deploy/cron/ai-radar-db-sync](../../deploy/cron/ai-radar-db-sync) · [ADR-013](../adr/013-db-sync-cron-agent-socket-auth.md) · [ADR-014](../adr/014-ship-base-only-db-and-rebuild-fts.md) |

`./install.sh` / `./uninstall.sh` / `./status.sh` 管理 serve、tunnel、pipeline、alert、performance-probe、cost-report 这 6 个服务。probe 的专属 plist 经 `./run.sh performance-probe` 启动；pipeline 和 cost-report 使用各自带精确 marker 的 user crontab 条目。remediation 与 DB sync 两条 cron 仍不在通用生命周期脚本管理范围内。

`./install.sh` 会逐服务检查脚本可判定的依赖。`alert` 要求两个 webhook；`cost-report` installer 只检查 notification webhook，尚不验证部署机的 `~/.local/bin/im-notify`、`run-or-alert` 与仓库 `run.sh` 可执行性（ISSUE-014）。cost-report 模板把 repo、命令和日志路径展开为绝对路径并显式设置 PATH；重复安装替换本条且保留无关 crontab，卸载只删除 `# ai-radar-cost-report` marker 条目。安装前置与 dry-run 见 [monitoring-alerting.md §LLM 成本报表与对账](monitoring-alerting.md#llm-成本报表与对账)。

## DB sync 职责、验证与故障证据

### 职责边界与 freshness path

| 位置 | 责任 | 不负责 |
|---|---|---|
| Mac cron + `deploy/sync/sync-db-cron.sh` | 每 5 小时启动 producer，恢复 cron 的 ssh-agent 环境，检查服务器 receipt age，并把 producer 非零退出交给 `run-or-alert` | 不接受 snapshot、不决定切流 |
| Mac `deploy/sync/sync-db-to-server.sh` | 以 `query_only` WAL reader 创建一致快照；更新并逐表对账持久 base-only shipping replica；生成 manifest v2；用 GNU rsync 发布 sidecar + DB；触发 server apply；轮询本轮 snapshot 直到 `committed`、`quarantined`、manual-block 或超时 | 不修改 live primary；不把 FTS 传到服务器；不把“上传完成”当“已服务” |
| Server `ai-radar-db-apply.service` | oneshot consumer：claim base-only artifact，在 inactive candidate 上重建 FTS，做 SQLite/HTTP/route gates，切换、回滚或 quarantine，并只在 consumer gates 全过后推进 basis/receipt | 不 pull Mac 数据，不产生新 snapshot，不承担 freshness 排期 |
| Server `ai-radar-db-apply.timer` | 安装但生产当前 disabled/inactive；若将来显式启用，只能 reconcile 已存在的 incoming/journal | 不是 producer，不能让公网数据自行变新 |

当前 5 小时 Mac cron 是持续新鲜的唯一生产入口；单轮生产实测约 32–35 分钟。该排期已启用，但仍缺三轮连续自动成功证据，最终频率也尚未根据传输量、端到端耗时、陈旧度与资源成本完成确认；“cron 存在”本身不等于这些验证已完成。

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

`alert` 服务负责 A1–A6，D3 定价提醒复用同一轮调度但不进入 page lifecycle。阈值、合并、degraded/in-progress 语义、severity 转换、投递与 ledger 的单一运行权威是 [monitoring-alerting.md §告警规则](monitoring-alerting.md#告警规则)；本服务清单只维护拓扑与生命周期入口，避免复制状态机细节后漂移。

> 已退役的 `wewe`（WeWe RSS docker bridge）已于 2026-06-06 从服务层移除（不再在脚本/注册表中）。微信摄取走 Mp2RSS（见 [wechat-ingestion.md](wechat-ingestion.md)）。如需回滚到 WeWe RSS：`deploy/wewe-rss/`（docker-compose + RUNBOOK）仍在，launchd plist 与脚本 wiring 从 git 历史恢复（移除 commit 见 git log）。

调度方式选择：pipeline 在 cron 和 launchd 之间二选一，**不要同时启用**——详见 [experiences/deployment.md 2026-05-15 条目](../experiences/deployment.md)。当前生产用 cron。

## 隐含依赖（repo 外）

| 依赖 | 必须的设置 | 验证方式 |
|---|---|---|
| cron 守护 | macOS 自带，默认运行 | `pgrep cron` |
| launchd | 系统自带，登录后自动运行 | `launchctl print gui/$UID` |
| pipeline LLM key | `DEEPSEEK_API_KEY` / `ARK_API_KEY` / `OPENAI_API_KEY` / `GLM_API_KEY` 任一 | `./install.sh pipeline` summary 显示 installed |
| alert 发送器 | `~/.local/bin/im-notify` + page 的 `FEISHU_GENERAL_ALERT_WEBHOOK` + notice 的 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK`；两个 webhook 任缺一个都拒绝 alert 安装 | `test -x "$HOME/.local/bin/im-notify"` 后运行下文无发送 preflight；已安装时检查 plist 同时有两个 key |
| Playwright Chromium | 微信原文抓取与默认 `performance-probe` | 部署前显式运行 `uv run playwright install chromium`；`install.sh` 不自动下载或校验 |
| Cloudflare tunnel | `deploy/cloudflared/config.yml` | `test -f deploy/cloudflared/config.yml` |
| Cloudflare Cache Rule | zone `aiplanet.live` 上的 `AI Radar short public pagination TTL`（见下节） | 当前生产旁路不适用；将来重新经 Cloudflare 代理后，同一 public 分页 URL 第二次请求应为 `CF-Cache-Status: HIT` |

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

第二次仍是 `DYNAMIC`/`BYPASS` 时，依次检查规则顺序、表达式、Edge TTL 模式与 origin header，再考虑动应用代码。命中效果反映在 `performance-probe` 的旅程延迟样本上（翻页 API 实测 3-5s → 0.5-1.4s），细节见 [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控)。

> 无法用 zone API 自动化：现有 `CLOUDFLARE_API_TOKEN` 能读 zone 但无 rulesets 权限（`/zones/<zone>/rulesets` → 403），故此规则只在 dashboard 手工维护；不要为此拓宽或替换该 token。

## EdgeOne 节点缓存规则对账与 purge

EdgeOne 对 `news.aiplanet.live` 下的精确路径 `/style.css` 与 `/app.js` 强制节点缓存 7 天（[ADR-039](../adr/039-route-news-through-edgeone-dns-only-cname.md)「决策」节）。该规则住在腾讯云控制台，是**仓外权威**：控制台新增一条强制缓存路径时，仓内 `tests/test_frontend_asset_versions.py` 仍会全绿，而那条路径上的资源若没有 `?v=` 版本串，发布后会在边缘陈旧整个 TTL——2026-08-17 的事故正是这个失效形态（见 [前端经验](../experiences/frontend.md)）。

### 凭据

腾讯云 CAM 最小权限子账号密钥，只需两个 action：`teo:DescribeL7AccRules`（读规则）与 `teo:CreatePurgeTask`（清缓存）。三个值放进 gitignored `.env`：

| 键 | 说明 |
|---|---|
| `EDGEONE_SECRET_ID` | 子账号 SecretId |
| `EDGEONE_SECRET_KEY` | 子账号 SecretKey |
| `EDGEONE_ZONE_ID` | 站点 ID，形如 `zone-xxxx` |

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

条件形态采用白名单：只有 `${http.request.uri.path} in ['…']`（可与 host 相等子句 AND）会被判为已理解，`not in`、`contains`、`full_uri`、文件扩展名匹配、以及任何嵌套在 `SubRules` 里的缓存规则一律判为未核实（exit 2），不会被当成已覆盖。

## Cloudflare tunnel shared ingress

The `ai-radar` tunnel configuration still contains one retired AI Radar ingress and one active shared-site ingress:

| Hostname | Local service | Owner repo | Notes |
|---|---|---|---|
| `aiplanet.live` | `http://127.0.0.1:8000` | `~/research/ai-radar` | **已退役入口**：Mac serve 已改绑 8010（本地 plist 明文禁止回绑 8000，仅限局域网/tailscale 预览），该 ingress 现回 502；域名下线待上游 P5。AI Radar 公网生产 = 腾讯服务器现存进程承载的 `news.aiplanet.live`；repo-owned 双槽 unit 当前未安装，服务清单缺口见 [docs-quality issue](../issues/docs-quality.md) |
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
curl -sf "https://${AI_RADAR_SITE_DOMAIN}/" -o /dev/null && echo tunnel_ok   # 仅适用于经本 tunnel 发布的 fork；本产线该检查已不适用（本机 env 的 SITE_DOMAIN 仍指向已退役的 aiplanet.live，公网核查用 https://news.aiplanet.live）
./status.sh alert
uv run pytest tests/test_admin_alerts.py -q -k 'send_alert_message_calls_im_notify_alert_without_dedup or send_alert_message_routes_notice_without_alert_flag'
./run.sh performance-probe --origin-url http://127.0.0.1:8010 --public-url https://news.aiplanet.live
./run.sh fetch                                      # pipeline + Mp2RSS feed 联通性；保留真实退出码
```

alert 的双通道配置与无发送 preflight 以 [monitoring-alerting.md §im-notify 飞书双通道](monitoring-alerting.md#im-notify-飞书双通道) 为准；上述 pytest 只验证 mock 投递路由，不发送真实消息。`./run.sh admin alert-check` 不是无害 smoke：当前状态如果触发 firing / resolved，它会按 page/notice 实际调用 `im-notify`。

当前不要把 `performance-remediate` 当 bring-up smoke 执行；启用 gate 与安装步骤以 [monitoring-alerting.md §用户旅程性能监控](monitoring-alerting.md#用户旅程性能监控) 为准。

`./status.sh` 输出每个服务一行：是否 loaded / pid / crontab 状态（pipeline）/ 日志位置。`alert` 与 `performance-probe` 都是周期任务，正常完成单次运行后可能显示 `loaded ✓ (no pid)`。

## 安装 / 卸载 / 切换

```bash
./install.sh alert        # 单个

./uninstall.sh            # 全部
./uninstall.sh alert      # 单个

./status.sh               # 只读面板
```

当前不要运行无参数 `./install.sh`：它会同时安装仍应保持停用、且默认 origin 仍错误的 `performance-probe`（[ISSUE-017](../issues/cost-observability.md#issue-017--performance-probe-默认-origin-仍假定-serve-在-8000)）。显式逐个安装需要的服务。

重新生成配置并重载某个受管 launchd 服务，统一重跑其安装入口：

```bash
./install.sh serve
./install.sh tunnel
./install.sh alert
```

告警的 severity、重试、去重与 ledger 语义以 [monitoring-alerting.md §告警规则](monitoring-alerting.md#告警规则) 为单一运行权威。

⚠ 改了 alert 的任一 webhook 后，单独 `kickstart -k` 不会刷新 launchd 烘焙的 `<EnvironmentVariables>`。重跑 alert 安装会重新生成 plist，并对已加载 job 执行 bootout/bootstrap：

```bash
./install.sh alert
```

pipeline 在 cron ↔ launchd 之间切换：先 `./uninstall.sh pipeline`，再手动 `launchctl bootstrap` launchd plist（暂未做成脚本——cron 是当前生产选择）。

## 相关参考

- [README.md §服务](../../README.md#服务) — 用户视角的脚本入口表
- [docs/operations/monitoring-alerting.md](monitoring-alerting.md) — `/admin` dashboard、A1–A6 与 D3 告警、周报、飞书 webhook 与旅程延迟
- [docs/experiences/deployment.md](../experiences/deployment.md) — 历史踩坑（env 不继承、cron/launchd 共存、tunnel region、docker compose 守护）
- [docs/operations/wechat-ingestion.md](wechat-ingestion.md) — 微信公众号摄取（Mp2RSS 接入、`MP2RSS_FEED_URL` 配置、头像 backfill、迁移留尾记录）
- [docs/references/wechat-sources.md](../references/wechat-sources.md) — 旧 WeWe RSS 微信源添加流程（已停用，仅回滚参考）
- [deploy/wewe-rss/RUNBOOK.md](../../deploy/wewe-rss/RUNBOOK.md) — 旧 WeWe RSS 详细运维手册（已停用）
