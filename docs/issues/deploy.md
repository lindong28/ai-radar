# Deploy / DB-sync Issues

> 部署与 DB 同步链路的运维问题跟踪（含影响其验收的测试基线）。协议：`~/.claude/references/docs-organization-protocol.md` §4.8。

## [open] 2026-08-10：sync 链路人读终端输出未过 cli-output 专项审

- Type: docs/review debt · Priority: medium · Discovered: 2026-08-10, execute-plan 收尾

`sync-db-to-server.sh` / `apply_db_update.py` 的人读输出（SELF-HEAL、capability-gate fallback、quarantine/manual-block 提示、producer 终态行）在 plans/20260809-fts-rebuild-sync 交付时未跑 `/custom:review-cli-output` 专项审——该审要求真实输出 capture，交付时已产生（重跑一轮 sync 即可再取）。运维要据这些输出判断"成功了吗/要不要动手"，值得补一轮专项审。

## [open] 2026-08-10：全仓 pytest 存在三组既有失败（与 db-sync 改动无关，多轮独立复现）

- Type: test baseline · Priority: medium · Discovered: 2026-08-10, U2–U5 多轮全仓 pytest

| 组 | 现象 | 成因 |
|---|---|---|
| `tests/test_eval_judge.py` | 2 failed | 依赖缺失的 `plans/ai-radar-alignment-20260512/state.md` |
| `tests/test_performance_journey_monitor.py` | 10 failed | 历史 2026-07-18 样本被当前时间窗过滤 |
| `tests/playwright/` | 112 setup errors | 需预置含历史数据的 `AI_RADAR_DB`，空库/缺库直接批量报错 |

后果：全仓 `uv run pytest` 无法作为绿灯基线；任何单元想以"全仓绿"作 gate 前需先修这三组或显式排除。

## [open] 2026-08-10：sync-db-cron.sh receipt-staleness fallback 文案与分类

- Type: cli-output · Priority: medium（review 判级） · Discovered: 2026-08-10, U4a 对抗审

本轮同步最终 committed、但 cron 启动时 receipt 已超龄的场景下，wrapper 仍按旧的现在时文案报 exit 4（"replica 已陈旧"），与实际终态矛盾（review 判 MEDIUM）。修正需获准编辑 cron wrapper 的单元（该文件在 20260809 plan 中冻结未动）。

## [open] 2026-08-10：VERIFIER_VERSION 依赖人工 bump（现 fts-apply-v4）

- Type: ops discipline · Priority: low · Discovered: 2026-08-10, U4c 决策评审

apply 的 retry authority 三元组含 `VERIFIER_VERSION` 常量，verifier-relevant 改动（HTTP gate 判据、manifest 消费语义、rebuild SQL）必须按 ADR-014 记录的政策同步 bump；漏 bump 会让旧 checkpoint 在新判定输入下错误获得一次自动 fresh retry。无机械强制（内容哈希方案已被决策评审否决——闭包不止代码文件），依赖 review 纪律，值得跟踪。

## [open] 2026-08-10：Mac 本机 env 的 AI_RADAR_SITE_DOMAIN 仍指向已退役域名

- Type: config currency · Priority: low · Discovered: 2026-08-10, sync-docs 终审取证

`~/.claude/.env` 的 `AI_RADAR_SITE_DOMAIN=aiplanet.live`（旧域名，公网已 502）。它只影响 Mac 本机 serve 的 CORS/UA（8010 局域网预览），不影响腾讯生产（服务器有自己的 server.env），但按文档跑 `tunnel_ok` 检查会对 502 域名 curl。宜择机改为 news.aiplanet.live 或清空。另：本机 serve 进程（Aug 4 启动）早于 Aug 9 模板改动，HTML 路由现 500（`site_config` undefined），需 kickstart 重启。

## ISSUE-009 · Feishu webhook 明文写进 LaunchAgent plist，任何服务检视都会泄露它

**状态**：open · **优先级**：high · **发现**：2026-08-11，plan `20260810-llm-cost-observability` P2 的 I2 preflight

`deploy/lib/services.sh:574-580` 在生成 alert plist 时把 `FEISHU_GENERAL_ALERT_WEBHOOK` 与 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK` 的**明文值**写入 plist 的 environment 条目。后果是凭据以明文常驻磁盘，且**任何对该服务配置的常规检视都会完整回显它**——`launchctl print gui/$UID/live.aiplanet.ai-radar.alert`、`plutil -p` 那个 plist、乃至 `cat`。

实测范围（2026-08-11 核，只查存在性、未回显值）：这两个值出现在 **12 个本地 Codex transcript 文件**中，最早 `2026-06-16`、最晚 `2026-08-10`，另加 1 个 shell snapshot。也就是说这不是一次性事故，而是持续约两个月的复发模式——每次有人为排障读一次服务配置就再落一份。今天这次由 P2 的 preflight 触发；`~/.codex/sessions/2026/08/11/` 当时尚无文件，故**无法从磁盘确认或排除今天这一份**（"0 命中"与"没文件"读数相同）。

**为什么不能只加一条「不要回显」的纪律**：那是把设计缺陷转成对每个读者注意力的持续要求，而读服务配置本身是排障的正常动作。判据是「一次合理的排障操作会不会泄露凭据」，现在的答案是会。

**修复方向**（需要一次独立的 deploy 改动，不属成本观测 plan）：让 plist 不承载值——引用文件路径，或让 alert 服务在运行时从 `.env` 读取。改完后旧 plist 需重装以清除已落盘的明文。

**用户侧动作**（不由 agent 代做：对第三方账号的对外、不可逆操作）：轮换这两个 webhook。建议在上述机制修好之后再轮换，否则新值会以同样方式再次散落。
