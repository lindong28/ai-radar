# Issues — LLM 成本观测

来源：plan `20260810-llm-cost-observability` 四个阶段的执行与 review。查询时派生成本、告警/周报消费面、cache split、paid-result 保护和 recorded-row scope 已交付；下列各条是**已知未闭合项**，不随 plan 完成而消失。金额加总只表示 `llm_usage` 记录行的下界，cohort 统计只描述已记录调用；这项解释契约见 ADR-023，attempt 缺口仍由 ISSUE-021 跟踪。

## ISSUE-004 · ARK 挂牌价来源非权威，而它占已知成本的 87.6%

**状态**：open · **优先级**：high

`src/airadar/pricing.py` 的 ARK supplement（`deepseek-v4-pro-260425` 等三型号）单价来自火山引擎**开发者社区的一篇个人文章**，不是官方计费页。它支持 flash `¥0.02/¥1/¥2` 与 pro `¥12/¥24`，但**无法权威证明** pro 的 cache 价 `¥0.1`、带日期后缀的型号 ID、以及用户实际的套餐/配额计费语义。

实测影响：最近 30 天窗口 `nominal_share ≈ 0.876`——即报表上约 87.6% 的金额建立在这批未权威核实的挂牌价上。因此 `cost-audit` 的结论被刻意限定为「对已加载 catalog 的**计算一致性**」，并在输出里明写 `tariff authority is not verified`。

**闭合方式**：plan 的 T1——去方舟控制台核实实际计费语义与实付单价。按量计费 → 用实价替换并把状态从 `nominal` 改为 `priced`；包量计费 → 保留 `nominal` 并在周报明示。在此之前**不得据 nominal 数字做 ARK vs 官方直连的路由决策**。

**T1 新调查输入（2026-08-12，用户账户事实）**：用户持有 ARK 订阅配额，并指定大规模 DeepSeek 运行优先走 ARK、官方 DeepSeek 只用于验证。如果生产 ARK 调用被订阅覆盖，当前按 ARK list price 派生的金额会高估真实边际支出，周报的 `nominal_share` 衡量的是挂牌价估算占比而非用户实际支付占比。T1 需据控制台核实订阅覆盖模型/阶段、计费周期、含量、超量规则及真实边际实付；在此之前不据该口述改变 pricing 行为。

## ISSUE-005 · 三条状态路径在真实数据中从未出现，仅由合成 fixture 覆盖

**状态**：open · **优先级**：medium

真实生产数据从未产生过 `stale`、`due-review`、`unpriced` 三种阳性状态（当前窗口 `unpriced=[]`、`pricing_freshness=['fresh']`）。因此这三条路径在 SQL / API / HTML / CLI 上的行为**只被合成 fixture 验证过**。同类还有：fuzzy / 未知 ARK 后缀的页面警示路径、非零 cache-read token、SQLite 计量失败时的 paid-result 保留路径、以及 raw-catalog 费率负控。

这不是缺陷，是**证据边界**。记录它的原因是：真实计数为零不等于阳性链路已接地，而两者在读数上不可区分。下次这些状态真的出现时（上游刷新失败、ARK 上新型号、P3 打开 cache 采集），应把首次真实出现当作一次验证机会而不是当作故障。

## ISSUE-006 · db-sync 的异常 base-copy 路径 fail-open

**状态**：open · **优先级**：high

`deploy/sync/logical_delta.py::_apply_delta` 在检出 schema 不等（`ReplicaInvalid`）后自愈为 `_replace_with_base_copy()` 整库替换。该路径**只打一行 stderr，sync 仍报成功**——于是一次 1GB 量级的异常传输与一次 16MB 的稳态轮在退出码上不可区分。

**2026-08-11 实测收口**：11:41 那一轮由 supervisor 观察全程。日志确认 `[replica] !!! SELF-HEAL: non-FTS schema differs from snapshot; rebuilding the base-only shipping replica`；主库传输 `Total file size: 1.68G / Total bytes sent: 1.26G / speedup 1.32`，即**实传 1.26 GB**（稳态轮 16–34 MB），11:41:01 起、12:15:51 止，耗时 34 分钟。**最终结局是 `sync OK`** —— 该轮在退出状态上与一次健康轮完全不可区分，fail-open 由代码推断升级为实测事实。自愈前的逐表核对全部 `match=1`，故触发原因确实只是 schema 不等、数据本身一致。

本次触发源：migration 016 为把 `item_evaluations.cost_usd` 改可空而整表重写（实测 388.8 MB / 93,499 行 / 99,532 页）。016/017 已把这次一次性豁免写进头注释与 ADR-014/016，但**该 fail-open 行为本身未修**，属 db-sync 的范围。

**闭合方式**：让异常 base-copy 至少在退出码或告警上与稳态轮可区分。

## ISSUE-007 · `verify_admin_metrics.py` 报出 3 项 P95 口径差异

**状态**：open · **优先级**：low

修掉该脚本对已退役成本列的读取后，它得以完整跑完，随即暴露既有差异：

```
SUMMARY fail count=3 names=pipeline.stages.prefilter.p95_latency_ms,
                           pipeline.stages.scoring.p95_latency_ms,
                           pipeline.stages.enrich.p95_latency_ms
expected 2404/3491/7119  vs  actual 2056/3148/6385
```

与成本改造无关，是 expected 侧与 API 侧的 p95 计算口径差异。该脚本目前全仓无 caller，非自动化可达。

## ISSUE-008 · `radar.db` 有 932 MB freelist

**状态**：open · **优先级**：low

migration 016 的整表重写在 `data/radar.db` 留下约 932 MB 空闲页（库总计 3.2 GB）。`VACUUM` 可回收，但本身又是一次整库重写，会再次触发 ISSUE-006 那条路径——两件事应一起安排，不要单独做。

## ISSUE-011 · A1/A3 在低样本或无流量时缺少可证明的恢复证据

**状态**：open · **优先级**：high

A1 样本少于 5、A3 最近 15 分钟 PV 少于 20 时，阈值分支不具备阳性健康观测；当前状态机仍可能把 `firing=false` 按健康路径消费。plan 只修了新增的 A5/A6 evaluation state，没有把 A1/A3 一并扩修。后续应让低样本/无流量进入显式不可评估状态，并用 firing → 证据不足 → 新健康证据的状态转换证明不会发出假恢复。

## ISSUE-012 · installed `im-notify` 的 dedup-clear 失败可能被误报为成功

**状态**：open · **优先级**：high

本仓 D3 在 `im-notify --dedup-clear` 返回失败时会保留 re-arm 义务并在后续轮次重试，但 plan review 发现 installed `im-notify` 可能把真实 clear 错误返回为 `cleared=true`，使本仓无法区分成功与失败。实现归 ai-agent-config 的 im-notify owner；当前没有一轮新的失败注入证明 installed artifact 已闭合该路径，因此本条保持 open。

## ISSUE-013 · `alert-check.log` 无 rotation，status 不暴露文件大小

**状态**：open · **优先级**：medium

本条是 alert-check 日志无界增长的**实体 owner**；告警侧的消费面（`status.sh alert` 不报大小、`alert-check.err.log` 是 ledger fail-open 的唯一证据通道）由 [alerting.md ISSUE-A11](alerting.md) 指回本条。

P2 review 时 `logs/alert-check.log` 已为 6,358,058 bytes、约 11,503 次运行；5 分钟 cadence 会让它持续增长。**最新读数（2026-08-20 实测）：9,908,040 bytes / 80,516 行**——较 P2 时增长约 56%，仍在按 5 分钟 cadence 增长，读数取的那一刻之后就已经不准了。仓内**零 rotation**：`git grep` 覆盖 `deploy/`、`scripts/`、`install.sh`、`status.sh` 后，唯一命中 "rotate" 的是 `deploy/wechat2rss/logs.sh` 里关于凭据轮换的注释，与日志切分无关；launchd 的 `StandardOutPath`（`deploy/launchd/ai-radar-alert.plist.example:17`）直指该文件、只追加不切分。`status.sh alert` 只给日志路径（`status.sh:72`），不检查大小或可写性。运维 runbook 已把这一限制写明；闭合仍需增加有界 rotation、超限/不可写状态暴露和真实轮转验证。

## ISSUE-014 · cost-report install/status 不验证 cron command 的可执行依赖

**状态**：open · **优先级**：medium

`./install.sh cost-report` 只检查 notification webhook，`./status.sh cost-report` 只核对 marker 数量，不直接验证 `~/.local/bin/run-or-alert`、`im-notify` 与仓库 `run.sh` 可执行。plan 在当前机器用真实 cron-equivalent command exit 0 证明了这台机器可运行，但新主机仍可能安装成功后才在首个周报周期失败。闭合需把这些依赖纳入 lifecycle preflight，并保留缺依赖的失败路径测试。

## ISSUE-015 · 重试耗尽的微信解读缺少独立处置 lifecycle

**状态**：open · **优先级**：medium

P2 收口时生产观察到 152 篇 `error_retry_count >= 8`；该数是 2026-08-11 的快照，未在本次文档同步中刷新。A5 detail 与 `/admin` 会显示 frozen 数，并阻止它们制造假恢复，但没有定义安全 replay、批量解除或独立通知。后续应先设计可回滚 replay，再决定是否需要独立 notice 或批处理入口。

## ISSUE-017 · performance probe 默认 origin 仍假定 serve 在 8000

**状态**：open · **优先级**：high

`src/airadar/cli.py` 的 `performance-probe --origin-url` 与 `src/airadar/performance/runner.py` 的默认值仍是 `http://127.0.0.1:8000`，而本机已安装 serve 使用 8010。A3 已改为从已安装 serve plist 解析端口，但 performance probe 尚未复用该 readback；当前 probe 未安装、旧 cron 保持 paused，因此没有把这个缺口写成正在产生错误样本。恢复 performance plan 前应先消除硬编码默认并验证实际 origin。

## ISSUE-018 · D3 resolved ledger 缺少可配对的 `episode_since`

**状态**：open · **优先级**：medium

D3 pricing notification 的 resolved ledger 行仍把 `episode_since` 写成 `None`，仅凭 ledger 无法稳定把 firing 与 resolved 配为同一 episode。闭合需让 D3 firing/resolved 共享持久 episode identity，并补跨进程配对回归；在此之前，按 episode 查询 D3 历史不能宣称完整去重。

## ISSUE-020 · 生产 tag validator 与既有用户索引词汇不一致（已移交 owning repo）

**状态**：open · **优先级**：high

2026-08-12 的 fresh V40/V42/V44 序列通过 raw `deepseek-v4-pro` 路由与两篇不同输入的全部 preflight。官方第 1 调用返回结构完整结果，但 production validator 拒绝 tag `toC-Agent变现`：`modules_ok=true`、`keyword_format_ok=true`、`tags_ok=false`。独立 provenance 进一步确认 data commit `4a74a58353d8091af81d74c09bb6fc946226699d` 在 2026-06-07、旧 article-first 模板时期已把该值作为关键词写入 committed summary/index；这不是 P3 reorder 新增的输出行为。`summarizer/core.py::load_known_tags` 只从受控 `agents/summary-agent/docs/tags.md` 读取允许值，而该 catalog 不含此值，模型却能从历史知识面将关键词提升为 tag。

影响：该次 runner 在 real save 前停止，第 2 调用未启动，usage DB 未建立；第二次 hit rate、`cached_input_tokens` landing 与 ≤¥0.06/篇全部未测量。第 1 调用的 4,480/82,762=`5.41%` cache hit 不是 mutation-bearing 第二调用指标，不能用于 80% gate。它曾被误判为 D5/V38 failure 并触发一次 template 回滚；用户看到 provenance 后裁决回滚无依据，现已逆转。

**移交**：controlled vocabulary 与 validator 均归 ai-assistant summary-agent 所有，权威 issue 已登记在 `ai-assistant/docs/issues/general.md`。本条只保留跨仓审计指针；P3 不编辑正在被另一写入者扩展的 `tags.md`、不临时加词，也不弱化 validator。

## ISSUE-021 · Interpret usage 只记录下游成功样本，漏掉已计费的失败响应

**状态**：open · **优先级**：high

ai-assistant 在 `agents/summary-agent/src/summarizer/core.py:78-87` 先取得 provider 响应与 usage，随后才解析输出；`agents/summary-agent/src/summarizer/schema.py:92-107` 会因缺失 `criteria_reason`、模块/关键词/tag 校验失败而拒绝结果。该异常经 `agents/summary-agent/src/summarizer/cli.py:78-94` 只作为命令失败返回，usage metadata 没有独立发出。ai-radar 的 `src/airadar/interpret/runner.py:572-639` 又要求 summarize、解析、验证与真实 KB save 全部成功才返回 result，直到 `src/airadar/interpret/runner.py:724-734` 才调用 `_record_interpret_usage()`；失败项则在 `src/airadar/interpret/runner.py:458-505` 进入带退避的重试队列。

后果有两层。第一，provider 已经计费、但输出未通过解析或校验的 item 会被再次调用并再次付费，而第一次调用在 `llm_usage` 中完全不可见。第二，本 plan 的成本、单篇均值、cache 覆盖率与命中率只在“成功走过解析/校验/保存”的调用上计算；这不是中性的缺口，而是 survivorship bias。P3 的 `97.755845%` cache hit 与 `¥0.019953972/篇` 证明的是那次通过全链路的受控第二调用，不能外推为包含失败与重试在内的每次 interpret attempt 的期望成本；weekly interpret 单篇数也具有同一条件限制。

2026-08-12 的后续 sweep 又确认两个独立漏行入口。其一，provider 返回的 usage 为 `None` 时，`ai-assistant/shared/llm/client.py::_metadata()` 不写 `usage` 键；interpret 侧随后静默跳过 usage landing，既无 `llm_usage` 行也无 `llm_usage_metering_failure`。其二，`src/airadar/eval/judge.py::DeepSeekV4ProCompareAudit.audit_compare()` 调 `chat_json()` 时没有传 `stage`，因此成功的付费 audit completion 也不进入计量。这两个入口说明排除类全集无法作为稳定消费契约；当前所有消费面改为正向声明“只统计 `llm_usage` 记录行，未写行的付费调用不在内”。

**修复方向**：请求派发时先创建不可撤回的 attempt 记录；provider usage 返回后再把 provider/model/tokens/cache/outcome 关联到该 attempt，后续 parse/validation/save 不得撤回它。usage 不返回、调用中断或调用点未接入时也必须保留可观察的 unknown/failed attempt，而不是无行。修复需同时证明一次失败响应只计量一次、后续 retry 是另一条可关联事件，并把 attempted cost、recorded usage cost 与 successful-item cost 分开呈现。

## ISSUE-022 · Steady-state usage 读路径无条件争用 SQLite writer lock

**状态**：open · **优先级**：medium

并发 migration race 修复后，`src/airadar/llm_usage.py:291-301` 的 `migrate_usage_db()` 每次都执行 `BEGIN IMMEDIATE`。这对首次 migration 是必要的，但 steady-state schema 已经 current 时也会拿 writer lock。三个本质上读 usage 的入口都会先调用它：admin usage view（`src/airadar/admin/usage.py:414-417`）、cost report（`src/airadar/admin/cost_report.py:35-40`）和 cost audit（`src/airadar/admin/cost_audit.py:243-247`）。若另一 writer 持锁超过 `src/airadar/db.py:37` 配置的 5 秒 busy timeout，这些读面会以 `database is locked` 失败；旧 steady-state no-op check 不需要争用该 writer lock。

Reviewer 在约 44,000 行的当前规模下将它判为正常运行包络之外的 MEDIUM，而不是本轮必须修复的回归。**修复方向**：增加 schema-current steady-state fast path，仅当 migration marker 和所需 schema invariants 均已确认 current 时跳过 write lock；任何缺失或不确定都进入现有 `BEGIN IMMEDIATE` 路径，并在锁内重新检查，不能为降低 contention 恢复首次 migration 的 check/ALTER 竞态。

## ISSUE-023 · A6 的“至少 3 个基线日”门当前不可达

**状态**：open · **优先级**：medium · **发现**：2026-08-12 full docs-sync 原始范围终审

`src/airadar/admin/cost_report.py` 当前无条件创建前 14 个 UTC 日桶，并把无记录日作为 ¥0 加入 `eligible`，所以 `baseline_days` 恒为 14；`len(eligible) >= 3` 与告警文案中的“少于 3 个基线日”分支不会表达“至少 3 个有记录日”。新部署或长时间无记录后，A6 可能拿大量零日形成中位数并进入评估，而不是因观测历史不足而 degraded。现行 runbook 已按实际实现说明这一点；实现修复需明确“基线日”是日历桶还是有足够 recorded-row 证据的可比日，并为不足与足够历史分别建立可失败测试。任何阈值金额仍只解释为 ADR-023 定义的 recorded-row floor，不升级为总支出。
