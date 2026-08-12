# Closed Issues

> Append-only archive for resolved or wontfix issues. Entries moved from a domain file retain their historical evidence; issues discovered and resolved within one plan are recorded here directly with terminal evidence.

## ISSUE-019 · P3 ballot repeat-set 的 N=4 分布带窄于实测运行内噪声

**状态**：resolved · **优先级**：high

P3 为把慢变 prompt 前缀移到文章前而执行 before/after 成对评测。第一次 reordered after 在第 4 篇被 schema validator 以 `summary JSON missing non-empty criteria_reason` 拒绝；没有补跑。唯一一次 D5 有界重设计在文章尾部完整重申 schema 的七个字段（`recommendation`、`criteria_reason`、`save_decision`、`save_reason`、`tags`、`keywords`、`projects`）后，新的 10 primary + 2 repeat 全部通过 schema、provider/revision、sampling、system/keywords hash 与逐块 hash；primary N=10 三档分布也全部在冻结带内。

原冻结判据还把 production-derived interval 用于 ballot repeat set N=4，得到 `必读=2`、允许 `[0,1]`。User adjudication 指出：两篇各两次的 repeat set 中，全部 before/after 差异来自 `cec6aabadcc4ed2a`；before primary=`必读`、before repeat=`值得一看`，而 after primary/repeat 均=`必读`。before 侧在模板完全不变时已经跨相邻档翻转，说明运行内噪声底宽于该 N=4 band；repeat 本应量化这种 variance，冻结判据却没有使用它。

**Resolution（2026-08-12，user adjudication）**：N=4 band 作为 criterion defect set aside，primary N=10 成为 operative distribution gate。这个修改发生在看到 reading 之后，确实削弱预注册纪律；因此由用户裁决，implementer/supervisor 不自行豁免。原始 `automatic-assertions.json` 保留 frozen failure，不覆盖历史。D5 template 为 after-redesign SHA-256 `c29f794c66836ffcd45cbca780a665a963a70e746d426c5dfc2c475ded578dd3`，12 份保存的 rendered prompt 已逐一 hash 相等；两组成对 human ballot 的三问均已通过，故 redesign 保留。后续 fresh official L2 在第 1 调用遇到历史 vocabulary gap 后，implementer 一度误用 V38 回滚；独立 provenance 证明该值由 2026-06-07 的旧模板批次 commit `4a74a58353d8091af81d74c09bb6fc946226699d` 预先引入，用户裁决它不是 redesign regression。该回滚确实发生过，但已因错误归因而逆转，不能继续记为本 issue 或 P3 的失败终态。

**P3 terminal evidence（2026-08-12）**：后续按冻结 before 输出中的 novel-keyword 差集预选 `1b0e38e487e98573`，真实保存使隔离 KB keyword count/hash `11,528 / 07c11a... → 11,533 / 5d714f...`，再对不同 item/hash/text 的 `398c50cf6c6ffab7` 完成 raw official `deepseek-v4-pro` 调用。第二次 raw/landed usage 为 input/output/cached=`76,599/2,014/74,880`，hit=`97.755845%`，官方 tariff 派生 `¥0.019953972/篇`；append-only 零-provider finalization 已把 `cached_input_tokens=74,880` 与 source 落到隔离 usage DB，原 failed checkpoint 未改写。由此 V40/V42/V44 全过，成本降低目标已达成；此前 rollback 与失败终态记录只保留为已逆转的审计历史。

## ISSUE-016 · A3 healthz 探针端口与已安装 serve 端口不一致

**状态**：resolved · **优先级**：high

**Resolution（2026-08-11）**：生产 `admin alert-check` 改为从已安装 `live.aiplanet.ai-radar.serve.plist` 的 `ProgramArguments` 解析 serve 端口，静态 threshold/calibration 不再夹带 8000 override。真实 LaunchAgent 于 13:46:16 把 A3 从 `firing / 292 failures / :8000` 迁移到 `ok / 0 / :8010`，`sent=1` 且输出 `send A3 resolved sent`；13:51:27 下一轮仍为 `sent=0 / A3 ok / failures=0`。

## 2026-08-12 · performance-probe 服务表状态漂移

**状态**：resolved · **优先级**：medium

`docs/operations/services.md` 的服务表曾把 `performance-probe` 写成已部署的 per-file LaunchAgent，与实机状态不符。

**Resolution（2026-08-12）**：文档已与 `./status.sh performance-probe` 和 PAUSED 旧 cron 的现场证据对齐；当前状态由 services.md 服务表单点维护，明确为未安装且旧 hourly cron 保持暂停。
