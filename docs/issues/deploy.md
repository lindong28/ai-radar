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
