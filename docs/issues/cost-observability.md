# Issues — LLM 成本观测

来源：plan `20260810-llm-cost-observability` P1 的执行与 review gate。P1 已交付「查询时派生成本 + cost-audit 对账 + 最小 admin 视图」，下列各条是**已知未闭合项**，不随 P1 交付而消失。

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

## ISSUE-019 · P3 ballot repeat-set 的 N=4 分布带窄于实测运行内噪声

**状态**：resolved · **优先级**：high

P3 为把慢变 prompt 前缀移到文章前而执行 before/after 成对评测。第一次 reordered after 在第 4 篇被 schema validator 以 `summary JSON missing non-empty criteria_reason` 拒绝；没有补跑。唯一一次 D5 有界重设计在文章尾部完整重申 schema 的七个字段（`recommendation`、`criteria_reason`、`save_decision`、`save_reason`、`tags`、`keywords`、`projects`）后，新的 10 primary + 2 repeat 全部通过 schema、provider/revision、sampling、system/keywords hash 与逐块 hash；primary N=10 三档分布也全部在冻结带内。

原冻结判据还把 production-derived interval 用于 ballot repeat set N=4，得到 `必读=2`、允许 `[0,1]`。User adjudication 指出：两篇各两次的 repeat set 中，全部 before/after 差异来自 `cec6aabadcc4ed2a`；before primary=`必读`、before repeat=`值得一看`，而 after primary/repeat 均=`必读`。before 侧在模板完全不变时已经跨相邻档翻转，说明运行内噪声底宽于该 N=4 band；repeat 本应量化这种 variance，冻结判据却没有使用它。

**Resolution（2026-08-12，user adjudication）**：N=4 band 作为 criterion defect set aside，primary N=10 成为 operative distribution gate。这个修改发生在看到 reading 之后，确实削弱预注册纪律；因此由用户裁决，implementer/supervisor 不自行豁免。原始 `automatic-assertions.json` 保留 frozen failure，不覆盖历史。D5 template 为 after-redesign SHA-256 `c29f794c66836ffcd45cbca780a665a963a70e746d426c5dfc2c475ded578dd3`，12 份保存的 rendered prompt 已逐一 hash 相等；两组成对 human ballot 的三问均已通过，故 redesign 保留。后续 fresh official L2 在第 1 调用遇到历史 vocabulary gap 后，implementer 一度误用 V38 回滚；独立 provenance 证明该值由 2026-06-07 的旧模板批次 commit `4a74a58353d8091af81d74c09bb6fc946226699d` 预先引入，用户裁决它不是 redesign regression。该回滚确实发生过，但已因错误归因而逆转，不能继续记为本 issue 或 P3 的失败终态。

**P3 terminal evidence（2026-08-12）**：后续按冻结 before 输出中的 novel-keyword 差集预选 `1b0e38e487e98573`，真实保存使隔离 KB keyword count/hash `11,528 / 07c11a... → 11,533 / 5d714f...`，再对不同 item/hash/text 的 `398c50cf6c6ffab7` 完成 raw official `deepseek-v4-pro` 调用。第二次 raw/landed usage 为 input/output/cached=`76,599/2,014/74,880`，hit=`97.755845%`，官方 tariff 派生 `¥0.019953972/篇`；append-only 零-provider finalization 已把 `cached_input_tokens=74,880` 与 source 落到隔离 usage DB，原 failed checkpoint 未改写。由此 V40/V42/V44 全过，成本降低目标已达成；此前 rollback 与失败终态记录只保留为已逆转的审计历史。

## ISSUE-020 · 生产 tag validator 与既有用户索引词汇不一致（已移交 owning repo）

**状态**：moved · **优先级**：high

2026-08-12 的 fresh V40/V42/V44 序列通过 raw `deepseek-v4-pro` 路由与两篇不同输入的全部 preflight。官方第 1 调用返回结构完整结果，但 production validator 拒绝 tag `toC-Agent变现`：`modules_ok=true`、`keyword_format_ok=true`、`tags_ok=false`。独立 provenance 进一步确认 data commit `4a74a58353d8091af81d74c09bb6fc946226699d` 在 2026-06-07、旧 article-first 模板时期已把该值作为关键词写入 committed summary/index；这不是 P3 reorder 新增的输出行为。`summarizer/core.py::load_known_tags` 只从受控 `agents/summary-agent/docs/tags.md` 读取允许值，而该 catalog 不含此值，模型却能从历史知识面将关键词提升为 tag。

影响：该次 runner 在 real save 前停止，第 2 调用未启动，usage DB 未建立；第二次 hit rate、`cached_input_tokens` landing 与 ≤¥0.06/篇全部未测量。第 1 调用的 4,480/82,762=`5.41%` cache hit 不是 mutation-bearing 第二调用指标，不能用于 80% gate。它曾被误判为 D5/V38 failure 并触发一次 template 回滚；用户看到 provenance 后裁决回滚无依据，现已逆转。

**移交**：controlled vocabulary 与 validator 均归 ai-assistant summary-agent 所有，权威 issue 已登记在 `ai-assistant/docs/issues/general.md`。本条只保留跨仓审计指针；P3 不编辑正在被另一写入者扩展的 `tags.md`、不临时加词，也不弱化 validator。

## ISSUE-021 · Interpret usage 只记录下游成功样本，漏掉已计费的失败响应

**状态**：open · **优先级**：high

ai-assistant 在 `agents/summary-agent/src/summarizer/core.py:78-87` 先取得 provider 响应与 usage，随后才解析输出；`agents/summary-agent/src/summarizer/schema.py:92-107` 会因缺失 `criteria_reason`、模块/关键词/tag 校验失败而拒绝结果。该异常经 `agents/summary-agent/src/summarizer/cli.py:78-94` 只作为命令失败返回，usage metadata 没有独立发出。ai-radar 的 `src/airadar/interpret/runner.py:572-639` 又要求 summarize、解析、验证与真实 KB save 全部成功才返回 result，直到 `src/airadar/interpret/runner.py:724-734` 才调用 `_record_interpret_usage()`；失败项则在 `src/airadar/interpret/runner.py:458-505` 进入带退避的重试队列。

后果有两层。第一，provider 已经计费、但输出未通过解析或校验的 item 会被再次调用并再次付费，而第一次调用在 `llm_usage` 中完全不可见。第二，本 plan 的成本、单篇均值、cache 覆盖率与命中率只在“成功走过解析/校验/保存”的调用上计算；这不是中性的缺口，而是 survivorship bias。P3 的 `97.755845%` cache hit 与 `¥0.019953972/篇` 证明的是那次通过全链路的受控第二调用，不能外推为包含失败与重试在内的每次 interpret attempt 的期望成本；weekly interpret 单篇数也具有同一条件限制。

**修复方向**：provider 一返回 usage 就立即发出不可被下游解析、校验或 KB save 撤回的 metering event；即使最终内容失败，该事件也必须落下 provider/model/item/tokens/cache 与失败后的关联身份。修复需同时证明一次失败响应只计量一次、后续 retry 是另一条可关联事件，并把 attempt 成本与 successful-item 成本分开呈现。

## ISSUE-022 · Steady-state usage 读路径无条件争用 SQLite writer lock

**状态**：open · **优先级**：medium

并发 migration race 修复后，`src/airadar/llm_usage.py:291-301` 的 `migrate_usage_db()` 每次都执行 `BEGIN IMMEDIATE`。这对首次 migration 是必要的，但 steady-state schema 已经 current 时也会拿 writer lock。三个本质上读 usage 的入口都会先调用它：admin usage view（`src/airadar/admin/usage.py:414-417`）、cost report（`src/airadar/admin/cost_report.py:35-40`）和 cost audit（`src/airadar/admin/cost_audit.py:243-247`）。若另一 writer 持锁超过 `src/airadar/db.py:37` 配置的 5 秒 busy timeout，这些读面会以 `database is locked` 失败；旧 steady-state no-op check 不需要争用该 writer lock。

Reviewer 在约 44,000 行的当前规模下将它判为正常运行包络之外的 MEDIUM，而不是本轮必须修复的回归。**修复方向**：增加 schema-current steady-state fast path，仅当 migration marker 和所需 schema invariants 均已确认 current 时跳过 write lock；任何缺失或不确定都进入现有 `BEGIN IMMEDIATE` 路径，并在锁内重新检查，不能为降低 contention 恢复首次 migration 的 check/ALTER 竞态。
