# Deploy / DB-sync Issues

> 部署、服务生命周期与 DB 同步链路的运维问题跟踪（含影响其验收的测试基线）。协议：`~/.claude/references/docs-organization-protocol.md` §4.8。

## [open] 2026-08-21：quarantine 只写不收，每次切换失败沉淀两份全量 DB 且永不回收

- Type: lifecycle gap · Priority: medium · Discovered: 2026-08-21 服务器磁盘占用分析

`apply_db_update.py` 的失败路径把 base 库、candidate 库、manifest 与 failure 记录整套搬进 `data/quarantine/<snapshot_id>/`，并用 SHA-256 在 journal 里绑定，保证证据不可篡改、重放幂等。写入端设计完整，**生命周期的另一半从未实现**：全文件 grep `retain|retention|prune|cleanup|rmtree` 只命中前滚路径的 `unlink`（`_unlink_durable`、committed 后删 sidecar），没有任何一处回收 quarantine 目录。

于是每次切换失败固定沉淀**两份全量 DB 副本**。2026-08-21 在 tencent-webserver-china 实测：两次失败（`failed_at` 2026-08-10T06:08Z 与 2026-08-17T05:50Z）共占 **7.8G**（3.7G + 4.1G），而同期 69G 根分区已用 82%。当前 `switch-journal.json` 状态是 `committed`，这两份证据已不被任何活跃 journal 绑定——即已成孤儿，却没有任何机制认得出这一点。

单份成本随主库线性增长：当前单库 2.6G，一次失败即 ~5.2G。按 3 个月两次失败的观察频率，约 **2G/月**的无界增长，且斜率会随主库变大。

修法方向（需另走决策评审——这是切换正确性的关键路径）：

1. 保留最近 N 次失败证据，`_complete_quarantine` 成功后回收更早的；
2. 或按 journal 状态回收——`state=committed` 且 snapshot 不再被任何 journal 引用时该组证据可删；
3. 或只保留 failure/manifest JSON，DB 副本按天数过期（牺牲深度复盘能力换容量）。

在此之前的缓解：容量告急时人工确认 journal 非 `quarantined` 后手删对应目录。

## [open] 2026-08-17：manifest 不携带 oracle 语义版本，新 consumer 无法拒绝旧语义 sidecar

- Type: contract gap · Priority: medium · Discovered: 2026-08-17, ADR-051 的 review-gate（HIGH, INDEPENDENT）

`build_fts_manifest.py` 写入 `format_version=2`，payload 里没有 oracle semantics version，也没有 producer 的 verifier identity。`validate_manifest()` 只校验结构、自哈希与 `timeline_http_matches ⊆ raw matches`——**旧语义 sidecar（其 probe 含 disabled / WeChat source item）完全满足这三条**，所以 v5 consumer 会接受它，并把它错记成 v5 retry authority，最终在 candidate HTTP 精确比较处再次 deterministic quarantine。`VERIFIER_VERSION` 只在 consumer 写 checkpoint 时进 journal，证明不了 sidecar 由哪个 producer 生成。

这是 ADR-051 之前就存在的契约缺口，该 ADR 既未造成也未闭合它，故标为 INDEPENDENT。

触发有**两个分支**，二者都会让旧语义 sidecar 遇上新 consumer：

1. **已 staging 未 apply**：旧 producer 生成的 sidecar 已发布到服务器，尚未被旧 consumer 消费。2026-08-17 实测服务器 `data/` 下无任何 `radar.db.fts-manifest.*.json`、无 `radar.db.incoming` / `.upload`（quarantine 已把上一轮的搬入 `quarantine/<snapshot_id>/`），该分支当时不存在。
2. **in-flight 旧 producer**：Mac 侧某个仍在跑的旧 producer 已构建出旧语义 sidecar、但尚未发布。**服务器目录为空排除不了这一支**——它稍后才会发布。此分支只能靠"Mac 工作树里的 producer 代码已含修复"来排除，而不是靠服务器读数。

用户据此 waive 本轮、不阻塞 ADR-051 发布。

修法方向：升 manifest format 版本，或加入一个同时被自哈希与 sidecar lookup 名绑定的 oracle semantic identity。该模块 docstring 规定 format 升级须 consumer-first rollout。

## [open] 2026-08-17：同一 snapshot 无法发布语义修正后的 manifest

- Type: contract gap · Priority: medium · Discovered: 2026-08-17, ADR-051 的 review-gate（HIGH, DEPENDENT）

`snapshot_id` 只取 base-only artifact 的 SHA-256，而 sidecar 名是 `radar.db.fts-manifest.<snapshot_id>.json`。当 oracle 语义变化而 artifact 字节不变时，新旧 manifest **同名而内容不同**：发布逻辑 `mv -n` 发现目标已存在后只接受逐字节相同，不同即 `exit 42` 且「database commit marker was not published」（`deploy/sync/sync-db-to-server.sh:373`）。于是正确的 sidecar 发不出去，整轮卡死。

已实测该机制真实存在，不是推理：用 ADR-051 的补丁在失败轮那份快照上重建 manifest，`snapshot_id` 仍是 `ed700a3a…`，而 `manifest_sha256` 由 `f0b11012…` 变为 `d106e0f6…`。

触发还需「旧 manifest 仍留在 staging 未被 quarantine 移走」。2026-08-17 实测该名已腾空（见上一条），且 Mac primary 自 13:42 后已有新 item，下一轮 artifact 字节必然不同、snapshot_id 也随之改变，故本轮撞不上。失败模式为 fail-closed（producer 停止、服务器不受影响），恢复是人工确认后删除陈旧 sidecar。用户据此 waive 本轮。

修法方向：把 oracle semantic identity 纳入不可变 sidecar identity，或为「同一 artifact 的合法语义升级」定义明确迁移路径。与上一条同源，宜合并处置。

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

## ISSUE-010 · mutable shipping DB 不能直接作为跨树 Playwright 比较基线

**状态**：open · **优先级**：medium · **发现**：2026-08-11，plan `20260810-llm-cost-observability` P2 最终验证

`data/radar.db.shipping` 会被 DB sync 重写；不同时间分别取得的 baseline/current 失败数同时混入代码差异和快照差异，不能据此归因回归。plan 将同一份 shipping snapshot 固定后在 `8b686df` 与 P2 树运行同一命令，结果分别为 `39 failed / 75 passed / 8 errors` 与 `38 failed / 76 passed / 8 errors`，baseline 通过而 P2 失败的 nodeid 集合为空；这只证明该固定快照下没有新增浏览器回归，不把两边的既有失败标绿。

后续任何 shipping-snapshot 跨树比较都必须先固定同一文件，并记录两棵树、同一命令和 nodeid 集合差；只比较不同时刻的 failure count 不构成证据。

## ISSUE-002 · performance-probe 调度仍处于暂停状态

**状态**：open · **优先级**：low · **发现**：2026-08-10 plan preflight；2026-08-12 文档同步复核

`./status.sh performance-probe` 当前返回 `not installed`；旧 hourly crontab 条目仍带 `[PAUSED 2026-07-24 pending plans/20260724-perf-idle-only-and-grounding]` 注释。该暂停不是成本观测 plan 所为，也未由它恢复。恢复前应由 performance plan owner 处理崩溃样本与 ISSUE-017 的 origin 默认值，再安装 per-file LaunchAgent；不能仅删除 PAUSED 注释让旧 cron 与新 lifecycle 并存。

## [open] 2026-08-12：三条 repo-owned cron 与一条维护者临时 cron 缺少统一收口

- Type: service lifecycle · Priority: medium · Discovered: 20260810 LLM cost plan 的 full docs-sync P5 review

repo-owned 的 DB sync、performance-remediate 与 Wechat2RSS healthcheck cron，以及维护者本机的 shadow-observe 临时 cron，都列在 services 清单但不受 `./install.sh`、`./uninstall.sh`、`./status.sh` 管理：前两者只给完整 wrapper / 裸 producer，或让操作者手工编辑 crontab；healthcheck 同样只有裸脚本；shadow-observe 的入口则明确未入 git。前三条应按各自现有启用约束增加规范 lifecycle，并让 status 展示调度和最近 terminal state；shadow-observe 要先由 owner 在“纳入 git 并提供 lifecycle”与“评估结束后移除”之间裁决，不能把未入 git 的临时任务直接当作 repo-owned 服务加固。`status.sh` 当前还会抑制 `crontab -l` 的错误，并把“无法读取”折叠成 `not installed`，因此这类输出不能单独证明排期不存在。pulled code 如何进入运行态的跨服务 make-live 文档缺口已登记在 `docs/issues/docs-quality.md`，本条不重复展开。

## [open] 2026-09-01：pipeline 的 launchd 备选形态没有规范 lifecycle 入口

- Type: service lifecycle · Priority: low · Discovered: 2026-09-01 微信搜索与 KB 补录文档同步的 P5 复审。

`docs/operations/services.md` 同时展示 pipeline 的 cron 当前形态和 launchd 备选模板，但切换到 launchd 的说明要求操作者直接运行 `launchctl bootstrap`。仓库的 `./install.sh pipeline`、`./status.sh pipeline`、`./uninstall.sh pipeline` 只识别 cron 形态，没有安装、查询或移除 launchd 形态的标准入口。结果是同名 repo-owned 服务一旦按文档切换 supervisor，就脱离跨项目约定的 lifecycle 动词。

闭合时应先决定 pipeline 是否仍正式支持 launchd：继续支持则让标准 lifecycle 命令识别并管理该 supervisor；不再支持则从现役 runbook 移除裸 `launchctl` 切换步骤，把 plist 降为历史或明确的非支持示例。当前不要把手动 `launchctl` 当成已收口的运维接口。

## [open] 2026-09-01：pipeline cron 按路径子串识别，多个 checkout 会串判与串删

- Type: service lifecycle · Priority: medium · Discovered: 2026-09-01 微信搜索与 KB 补录文档同步的 README P4 复审。

`./install.sh pipeline` 写入的 crontab 行没有稳定 marker；`install.sh`、`status.sh` 与 `uninstall.sh` 都按 `ai-radar/pipeline.sh` 路径子串识别。若同一用户同时保留多个 ai-radar checkout，另一个 checkout 的排期会让 status 误判当前树已安装，卸载时也可能把两条一起删除。README 已改用 `pwd -P` 的绝对仓根精确核对当前 checkout，但这个人工读数还没有被三条 lifecycle 脚本复用，因此只能识别风险，不能消除脚本侧的串判与串删。

闭合时应给 pipeline cron 增加能绑定 canonical checkout 的稳定身份，并让 install/status/uninstall 共用同一解析规则；迁移须保留无关 crontab 条目，并能区分当前树、其它树与无法读取 crontab 三种状态。

## [open] 2026-08-12：当前生产 admin 入口绕过 Cloudflare Access

- Type: security boundary · Priority: high · Discovered: 20260810 LLM cost plan 的 full docs-sync 终审

`news.aiplanet.live` 当前 DNS 直解腾讯服务器、响应没有 Cloudflare headers。应用层只检查 `Cf-Access-Jwt-Assertion` 是否非空；2026-08-12 从公网实测 `/admin` 无 header 为 403、伪造 `Cf-Access-Jwt-Assertion: x` 为 200。因此 Cloudflare Access 不是当前请求路径上的真实边界，未登录者可自行构造该 header 越过存在性检查。闭合需把生产 hostname 重新置于可信认证代理之后，或在 origin 做可验证的 JWT/origin-token 校验；完成前不得把 admin 称为已认证入口。

## [open] 2026-08-10：sync-db-cron.sh receipt-staleness fallback 文案与分类

- Type: cli-output · Priority: medium（review 判级） · Discovered: 2026-08-10, U4a 对抗审

本轮同步最终 committed、但 cron 启动时 receipt 已超龄的场景下，wrapper 仍按旧的现在时文案报 exit 4（"replica 已陈旧"），与实际终态矛盾（review 判 MEDIUM）。修正需获准编辑 cron wrapper 的单元（该文件在 20260809 plan 中冻结未动）。

## [open] 2026-08-10：VERIFIER_VERSION 依赖人工 bump（现 fts-apply-v5）

- Type: ops discipline · Priority: low · Discovered: 2026-08-10, U4c 决策评审

apply 的 retry authority 三元组含 `VERIFIER_VERSION` 常量，verifier-relevant 改动（HTTP gate 判据、manifest 消费语义、rebuild SQL）必须按 ADR-014 记录的政策同步 bump；漏 bump 会让旧 checkpoint 在新判定输入下错误获得一次自动 fresh retry。无机械强制（内容哈希方案已被决策评审否决——闭包不止代码文件），依赖 review 纪律，值得跟踪。

2026-08-17 更新（保持 open）：v4→v5 的 bump 又一次实证了这条问题。首轮决策评审只按 `git grep fts-apply-v4` 得到"精确字符串闭包"六处，复核轮指出那不是语义闭包——还漏了测试函数名 `..._blocks_under_v4_...`、失败断言文案 `"resumed under verifier v4"`，以及旧 checkpoint parametrize 参数需要补入 `fts-apply-v4` 才能覆盖新的 v4→v5 边界。字符串搜索找得到版本号出现的地方，找不到版本号被拼进标识符或被参数集合隐含的地方。本次改动是该问题描述的现象本身，不是它的解决证据。

## [open] 2026-08-10：Mac 本机 env 的 AI_RADAR_SITE_DOMAIN 仍指向已退役域名

- Type: config currency · Priority: low · Discovered: 2026-08-10, sync-docs 终审取证

`~/.claude/.env` 的 `AI_RADAR_SITE_DOMAIN=aiplanet.live`（旧域名，公网已 502）。它只影响 Mac 本机 serve 的 CORS/UA（8010 局域网预览），不影响腾讯生产（服务器有自己的 server.env），但按文档跑 `tunnel_ok` 检查会对 502 域名 curl。宜择机改为 news.aiplanet.live 或清空。

## ISSUE-009 · Feishu webhook 明文写进 LaunchAgent plist，任何服务检视都会泄露它

**状态**：open · **优先级**：high · **发现**：2026-08-11，plan `20260810-llm-cost-observability` P2 的 I2 preflight

`deploy/lib/services.sh:574-580` 在生成 alert plist 时把 `FEISHU_GENERAL_ALERT_WEBHOOK` 与 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK` 的**明文值**写入 plist 的 environment 条目。后果是凭据以明文常驻磁盘，且**任何对该服务配置的常规检视都会完整回显它**——`launchctl print gui/$UID/live.aiplanet.ai-radar.alert`、`plutil -p` 那个 plist、乃至 `cat`。

实测范围（2026-08-11 核，只查存在性、未回显值）：这两个值出现在 **12 个本地 Codex transcript 文件**中，最早 `2026-06-16`、最晚 `2026-08-10`，另加 1 个 shell snapshot。也就是说这不是一次性事故，而是持续约两个月的复发模式——每次有人为排障读一次服务配置就再落一份。今天这次由 P2 的 preflight 触发；`~/.codex/sessions/2026/08/11/` 当时尚无文件，故**无法从磁盘确认或排除今天这一份**（"0 命中"与"没文件"读数相同）。

**为什么不能只加一条「不要回显」的纪律**：那是把设计缺陷转成对每个读者注意力的持续要求，而读服务配置本身是排障的正常动作。判据是「一次合理的排障操作会不会泄露凭据」，现在的答案是会。

**修复方向**（需要一次独立的 deploy 改动，不属成本观测 plan）：让 plist 不承载值——引用文件路径，或让 alert 服务在运行时从 `.env` 读取。改完后旧 plist 需重装以清除已落盘的明文。

**用户侧动作**（不由 agent 代做：对第三方账号的对外、不可逆操作）：轮换这两个 webhook。建议在上述机制修好之后再轮换，否则新值会以同样方式再次散落。

## [open] `/img` 负缓存的两个未验证点 + `0f0a6fd` 生产部署未核实

**状态**：open · **优先级**：medium · **发现**：2026-08-19 EdgeOne `/img` 缓存改造收尾时留下的未闭合项（主体已 resolved 并归档，见 `archive/closed.md` 的「EdgeOne 从不缓存 `/img`」条）

给 `/img` 加 FollowOrigin 缓存规则后引入过一个负缓存风险：404 不带 `Cache-Control`，边缘按默认策略把失败也缓存住，实测同一个必然失败的 URL `MISS` 一次后连续三次 `HIT`。修法是源站对失败路径显式声明 `Cache-Control: no-store`（commit `0f0a6fd`），成功路径保留 7 天 immutable。三个点仍未闭合：

- **负缓存的实际 TTL 未实测**——当时没等它过期，所以"一次瞬时超时会把这张图钉成不存在多久"这个量级至今没有读数。
- **`no-store` 上线后未复测负缓存已消除**——修复的验收读数缺失。判据是：对一个必然失败的 `/img?url=` 连续请求，`eo-cache-status` 不应出现 `HIT`。
- **`0f0a6fd` 是否已在生产 serve 上生效未核实**——本地 `tencent/main` 这个 remote-tracking ref 包含该 commit（其后还有 `5039988`），但 ref 里有 ≠ 服务器已 checkout 并重启 serve。核实要从生产侧取读数（如对一个必然失败的 `/img` URL 观察响应头里有没有 `Cache-Control: no-store`），不能只看 git。

前两点都要**从真实公网**取读数：源站与边缘的行为在本地和 curl 直连源站时读数相同，看不出差别。
