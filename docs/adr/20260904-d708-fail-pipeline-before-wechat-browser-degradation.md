# 在微信浏览器缺失时于 fetch 前终止 pipeline

- Status: accepted
- Date: 2026-09-04
- Relates: [ADR-20260826-68e2](./20260826-68e2-route-ai-radar-through-domain-selector.md)（受管外部阶段使用 fail-closed preflight）；[ADR-059](./059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md)（微信双源与长链接跳过正文抓取语义不变）
- Supersedes in part: [ADR-009](./009-alert-notification-ledger.md) 与 [ADR-021](./021-audit-alert-delivery-and-suppression-decisions.md) 对共享 ledger 具备 64 MiB 硬上限和可靠 14 天边界的陈述；事件范围与查询语义不变

## Context

微信短链接正文由 Playwright Chromium 抓取。`WeChatScraper.fetch_article()` 捕获浏览器启动异常后返回失败结果，`fetcher.runner` 再记录 warning 并保留 RSS 条目，因此浏览器二进制缺失时 pipeline 仍可把 fetch 与整轮报告为成功。2026-09-02 的本机 pipeline 日志提供了区分性对照：15:00、15:15 两轮此类错误均为 0；15:30 一轮出现 83 条 `BrowserType.launch: Executable doesn't exist`，同时仍记录 `=== fetch OK ===` 与 `=== PIPELINE DONE (failed=0) ===`。已知事故持续 55 轮。统一日志、相关仓库脚本与 shell history 均未找到 15:00–15:30 删除 `~/Library/Caches/ms-playwright/` 的动作，因此删除来源未核实。

README 已要求部署时运行 `uv run playwright install chromium`，并用一次真实 `chromium.launch()` 验证浏览器可启动；`install.sh` 不下载或校验 Chromium。缺的是每轮 scheduled pipeline 在 fetch 前可执行、可告警的快速存在性检查。

## Decision

1. 新增独立的 `wechat-browser-preflight` CLI。它通过 Playwright 的 `chromium.executable_path` 取得当前运行时预期路径，只检查该路径是 regular executable，不启动浏览器、不访问网络。存在时输出 `status=present` 并 exit 0；缺失或不可执行时输出 `status=unavailable`、影响与 `uv run playwright install chromium` 处置命令并 exit 1；Playwright 自省无法完成时输出 `status=not_verified` 并 exit 2。
2. `pipeline.sh` 在 egress preflight 之后、fetch 及全部后续 stage 之前执行该命令。任何非零结果立即终止整轮。这会同时跳过本轮 RSS/X fetch 与 prefilter、score、enrich、curate、interpret backlog；这是“浏览器缺失时 pipeline 前置失败而非生成残缺微信正文”的显式可用性代价。恢复浏览器后由下一轮正常调度继续处理，本决策不改变调度频率。
3. 非零结果作为独立规则 `W1` 接入现有告警状态机，沿用共享状态文件、事件账本、pending notification 重试与 `im-notify --alert --dedup-key` transport 去重。账本在成功写入时裁掉 14 天前的行，但现有 64 MiB guard 不是硬上限，单批写过界后会持续 fail-open；该边界由 `ISSUE-ALERT-20260904-8f2c` 跟踪，不把它写成已具备的 boundedness。CLI 在 pipeline 日志中留下状态、影响、完整本机原因、处置和投递结果；推送只写故障类别与处置，不发送含本机绝对路径的原始错误。健康 preflight 不发送通知，也不据此宣告恢复。
4. `W1` 只在整轮 pipeline 的全部 stage 随后成功、且末端再次检查预期 executable 仍通过时转为 resolved，并通过同一状态机的 notice 通道显式通知恢复。CLI 的 `--resolve-after-pipeline` 不是可独立使用的人工声明：调用者必须继承 pipeline 在 unlink 后仍打开的 fd 8 capability（内容绑定当前 generation），同时传入 `.pipeline.flock` 的 fd 9；这避免仅凭同 inode 上的另一个 descriptor 借用其他持锁者，也避免持锁者恰在校验期间退出后由 decoy descriptor 自行取得锁。`.pipeline.activity` 的 generation 必须与传入的当前 `pipeline-YYYYMMDD-HHMMSS.log` 唯一匹配，日志控制序列必须严格证明 egress、前检和六个数据阶段依次 OK 并已进入 resolve START。任一证据缺失、重复、错序、失败、SKIP、提前 DONE、capability 或锁身份不符都 exit 2 且保持 W1 open。恢复通知或状态持久化失败不把已经完成的数据 pipeline 改判为失败；pipeline 记录 `DEGRADED`，状态机保留 pending notification，下一轮完整成功后重试；pending 期间浏览器再次失败时立即废弃过期的 resolved pending 并投影当前 firing。首次健康运行没有既往 firing episode，因此不产生通知。
5. `A2` 的 heartbeat 分支与 `W1` 可描述同一阻断事故。只有最近一次成功 pipeline 之后存在至少一轮非 SKIP 运行、这些运行全部终止于 `wechat_browser_preflight FAIL`、W1 episode 的起点不晚于“最后成功 pipeline 的精确时间戳 + A2 阈值”所得越线时刻、且把 `minutes_since_successful_pipeline` 反事实置为 0 后 A2 不再 firing 时，才由已宣告 firing 的 `W1` 承载 A2 并写入 INTERNAL suppression 事件。精确先后不从取整分钟年龄与稍后的状态机时钟反推。A2 的 stage error 与 P95 分支继续独立 page，从未有成功记录时不抑制。A4/A5/A7 不由 W1 抑制：它们覆盖摄取、解读与来源静默的独立失败面，规则 lifecycle 的 `since` 只能证明首次被 alert-check 观察的时间，不能证明底层症状始于 W1；为避免把并发的第二个事故静音，无法证明同因时优先保留独立告警。
6. `status=present` 只表示预期可执行文件路径存在且可执行，不表示 Chromium 能成功 launch、版本兼容、selector/网络健康或微信页面可达。部署时的完整验证仍是 README 中的真实 launch 命令。

## Rejected alternatives

- 等第一条微信文章触发 `chromium.launch()` 再让 fetch 失败：发现时 feed I/O 与数据库处理已开始，且必须改写当前逐条 RSS 回退语义才能终止。
- fetch 完成后统计 RSS 回退并告警：残缺条目已进入数据库且后续阶段仍会消费，不能满足前置失败。
- scheduled pipeline 与直接 `./run.sh fetch` 都强制预检：scheduled 路径会重复检查和清键；本次目标与验收只要求 pipeline 前置。直接 fetch 继续保留既有 RSS 回退，并作为作用域边界披露。
- 实际 launch Chromium 作为每轮 preflight：它能覆盖更多启动失败，但增加每轮进程启动成本；本次已知故障的直接判据是 expected executable 缺失，完整 launch 验证继续留在部署步骤。

## Evidence and rollback

Playwright 官方文档将 `browser_type.executable_path` 定义为预期 bundled browser executable 的路径，并允许通过 `PLAYWRIGHT_BROWSERS_PATH` 改变运行时查找目录。本机隔离正例以一个空临时目录设置该变量，得到不存在的 `chromium-1217/.../Google Chrome for Testing` 预期路径；未读写真实缓存。反向对照在当前已安装环境得到 existing regular executable。

上线不在本任务范围。未来上线后，每轮 pipeline 日志的 `START/PRESENT/FAIL` 是首个发现面；若已安装路径正例仍被阻断，则回滚触发成立，回滚范围仅是 pipeline 的新 preflight 调用及对应 CLI，实现恢复原 RSS fallback。具体 release/revert 仍遵循生产部署 ADR，不由本 ADR授权。

## Scope and unverified

- 成立范围：scheduled `pipeline.sh` 与显式 `./run.sh wechat-browser-preflight`；不覆盖 direct `./run.sh fetch`、`performance-probe`、头像 refresh 或 discovery CLI。
- 未验证：Chromium 实际 launch、版本兼容、出网与页面抓取；真实 `im-notify` 送达；删除缓存的来源。
- 已知 transport 边界：`docs/issues/cost-observability.md` ISSUE-012 仍记录 installed `im-notify --dedup-clear` 可能把底层 clear 失败误报成功。`W1` 不直接清 transport dedup，而是复用告警状态机按 episode/nonce 生成 transport key；本决策仍不把 transport 接受等同于用户实际收到消息。

## Decision review

独立只读对抗评审首轮给出 4 个应修：披露整轮停止代价、把成功态收窄为 path present、补误报发现与回滚触发、不得把 dedup clear 当作可靠 re-arm。告警九原则评审随后指出直接发送会绕过共享账本与 resolve 生命周期，并与 `A2` heartbeat 重复 page，因此改为 `W1` 状态机方案。生命周期决策复审要求用可观察因果条件约束 `A2` 抑制；补入全失败 stage、heartbeat-only 反事实、“从未成功则不抑制”与“W1 不晚于 heartbeat 阈值越界”后，复核结论为成立。后续全量告警审又补出 resolve 通道、恢复后快速复发的新 episode、push 一屏预算、runbook 指针、恢复结论作用域与 resolve 成功证据绑定；后者最终采用 unlink 后的继承 fd capability、flock fd、activity generation 与严格同轮日志，避免裸 flag、锁校验交错或陈旧成功日志错误关闭 W1。评审曾尝试把 A4/A5/A7 一并交给 W1，但反例证明 rule `since` 只表示首次观察时间：底层症状可早于 W1 却在 W1 后首次被 alert-check 看到。按 P1 高于 P5 的冲突顺序，最终收窄为只合并有严格反事实证据的 A2 heartbeat；长故障期间 A4/A5/A7 可能另发 page，这是保留无法排除的独立失败面的显式代价。保留“存在但不可 launch”、ledger 非硬上界与 direct fetch 入口差异三项显式边界。
