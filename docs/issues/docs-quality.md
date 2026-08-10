# Docs Quality Issues

> 文档自身的质量债跟踪（README 定位、重复、可观察性等审查遗留）。协议：`~/.claude/references/docs-organization-protocol.md` §4.8。

## [open] 2026-08-10：README 全面审查遗留 findings（两组独立审查，范围超出当次交付未就地修）

20260809-fts-rebuild-sync 收尾时对 README 跑了完整原则审查（readme-review-principles §1–§7），当次只修了本 plan diff 内的两处重复；以下为遗留清单（按修复价值排序）：

**需用户先拍的定位取舍（阻塞方向选择）**
- README 的 intended reader 在「generic fork 部署者」（your-org 占位、中性默认）与「本产线运维者」（腾讯服务器行、本部署 cron 排期）之间摇摆——决定 DB-sync/生产内容留 README 还是只留 services.md。
- install.sh 行为长段说明（README §install 依赖表两段 vs services.md 同语义整段）与响应式 UX 细节（README vs ux-contract.md RS-1/RS-2）各自的 home 归属。

**机械可修（方向单一）**
- 「U4 发现的 homepage 假阳性已修复」等修复史/评审编号语言混入用户文档（README 性能监控节、服务表 remediate 行）——读者只需当前 gate 步骤。
- performance-remediate 启用 gate 条件在 README×2 + services.md×2 + monitoring-alerting.md 至少五处副本。
- 快速开始步骤 3–4 的 8 条命令均无可观察成功信号/失败去处；步骤 gate 的样本文件位置（journey-samples.jsonl）未指给读者；「确认已配 LLM API Key」无非交互视角的检查命令。
- 「从零部署最小配置」与「站点身份与域名」相邻代码块重复同一组 `AI_RADAR_SITE_*` 变量。
- WeWe RSS 移除史两句、纯客户端预取括注等无行动内容可删。
- 性能监控引言段（~30 行内部采样机制）复述 monitoring-alerting.md 权威内容，可压至 ~10 行。
- 项目差异化定位（借鉴 AIHOT、人人可自部署）埋在文末致谢，入口读者在 clone 前看不到。

**services.md / 服务清单侧**
- 服务器侧生产栈（install-server.sh 的 serve/db-apply/alert、双槽 serve@8000/8001）不在任何服务清单；make-live 路径（git push tencent → post-receive → deploy_code.py）无文档。
- performance-probe 行按已部署 LaunchAgent 描述，实机 `status.sh` 显示 not installed、旧 cron 处 PAUSED。

## [open] 2026-08-10：architecture.md 模块树多处过时（本 plan 范围外，独立审查发现）

- Type: content currency · Priority: low · Discovered: 2026-08-10, sync-docs 重审 wave

模块树漏 `runtime_env.py`、`web/routes/daily_metrics.py`；`performance/` 仅列 3/9 模块且无省略标记；`web/templates` 仅列 4/16（漏掉路由表自己引用的 hot/changelog/more/admin/admin_usage/bookmarks/wechat_404 与 partials）；`web/static` 漏 `wechat-icon.svg`。另：services.md 把生命周期脚本契约 defer 给不随仓分发的 `~/.claude/references/service-operations-protocol.md`（已在文中如实标注不可达，长期宜把 [User] 需要的部分落回仓内）；general.md 大量 [resolved] 条目未按协议 §4.8 移入 archive/closed.md。
