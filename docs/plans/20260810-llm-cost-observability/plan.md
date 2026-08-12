> **Archive status**: 已完成（ai-radar 四阶段提交 `6c3b419`、`5453bf3`、`a831ca4`、`e49b4df`；ai-assistant 配套提交 `895aa3a`；ai-agent-config user-scope 提交 `ec09417`；2026-08-12）。执行过程产物 `state.md` / `journal.md` 不入档；当前测量范围与决策见 [ADR-017](../../adr/017-preserve-paid-results-on-metering-failure.md)、[ADR-020](../../adr/020-normalize-cost-comparisons-to-cache-all-miss.md)、[ADR-022](../../adr/022-evaluate-a6-in-progress-cost-as-lower-bound.md)、[ADR-023](../../adr/023-define-recorded-row-measurement-scope.md)，开放边界见 [LLM 成本观测 issues](../../issues/cost-observability.md)，运行口径见 [监控与告警 runbook](../../operations/monitoring-alerting.md)。P4 校准的最终权威是 `~/.claude/references/review-llm-cost-calibration.md`「首次校准的实际结局（2026-08-12，用户裁决收手）」：两次独立收敛运行表明命令正确应用判据，但未取得一批四跑全合；用户据这部分证据决定收手。
> 以下为原 plan 正文，未修改；正文中的 `billed cost` 是实施期旧措辞，不表示 provider 账单或实际付款。V40 数值只来自隔离 `llm_usage` 的两条已记录调用并按 tariff 派生，其他金额加总也只按 [ADR-023](../../adr/023-define-recorded-row-measurement-scope.md) 解释为 recorded-row floor；不得从归档原文外推为调用总支出。

# LLM 调用成本观测与优化

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。

> 审查记录：经独立 Codex reviewer（$custom-review-plan 契约）六轮审查，V1–V41 全部修复并经复验；**V42–V45（最后一批机械修复）已修入但未经复验**，以用户预授权定稿终止（2026-08-10），不构成双 gate clean。

## 输入

无 spec.md。本 plan 是 review / 实施的唯一入口：L1 / 取舍 / L2 / L3 全部由 deep-discuss（2026-08-10 session）对齐并 inline 于本文。用户已选定的方案方向为已决事实，不复议：观测=周报+突变告警；优化=仅零行为变更项；harness=原则档+审查命令；实施隔离=主 checkout 停调度（D1）；成本=查询时派生+与 tt-web 共用 LiteLLM 定价源（D2）；未定价/价格陈旧=NOTIFICATION 去重提醒（D3）；A6 只管量结构异常、价格变化归 D3 链路（D4）；P3 人工 gate 失败允许一次有界重设计（D5）。

## Rigor

- 默认 `(A0, V1)`，label=standard。R 轴：多为可逆本地改动；G 轴：告警/报表/解读链路影响生产运维，每个改行为单元须真实验证 + 独立 reviewer。
- Per-phase override（只升不降）：**P2 → A1**（告警落地即生效、state/ledger 有漂移面）；**P3 → A1**（跨仓、产出持久写入 KB）。
- 用户已于 2026-08-10 经 AskUserQuestion 确认。

## 实施隔离：运行不变量（D1=主 checkout 停调度）

本 repo 是**生产运行时**。隔离不是静态停复表，而是全程维持的四条运行不变量：

**I1 — live consumer 由被编辑路径派生**。每个 phase 开工前，implementer 列出本 phase 将编辑的模块，按下表推导须暂停的 live consumer；表未覆盖的新依赖按同一规则现推（谁 import / 执行它，谁就是 consumer）：

| 被编辑路径 | live consumers |
|---|---|
| `src/airadar/llm_usage.py`、`src/airadar/pricing.py`、`admin/usage.py` | pipeline cron（各 stage 记 usage）、**serve**（/admin/usage import）、alert launchd（P2 起 A6 读成本） |
| `src/airadar/admin/alerts.py`、`thresholds.py`、`cost_report.py` | alert launchd、serve（admin 路由 import） |
| `pipeline.sh`、`interpret/runner.py` | pipeline cron |
| ai-assistant `summary-agent/**`、`shared/llm/client.py` | pipeline cron（interpret 子进程）；须同时确认 ai-assistant 侧无并行 summary 任务在跑（`pgrep -f summarize.sh` 为空） |
| `~/.claude/**`、`docs/**`、`deploy/cron/*` 模板 | 无（P4 与纯文档不停任何调度） |

推导结论：**P1、P2、P3 均暂停 pipeline cron 与 alert launchd**（P1/P2 因共享成本路径，P3 因 interpret 与 A6 读取面）；serve 不停机但在 phase 合并后**强制重启并验证**（见 I4）。db-sync/tunnel 全程不停（不 import 被改模块）。

**I2 — 停机有 preflight，状态有落盘**。暂停动作与断言：

- pipeline：crontab 该行前加 `# [PAUSED plan-20260810] `；断言无在途运行（`.pipeline.lock` 目录不存在且 `pgrep -f pipeline.sh` 为空，有则等待其自然结束）。
- alert：`launchctl bootout gui/$UID/live.aiplanet.ai-radar.alert`（**用 `$UID`，不硬编码 501**）；断言 `launchctl list` 无此 label。
- 停前状态与恢复清单写入 `plans/20260810-llm-cost-observability/pause-state.md`（哪些被停、何时、如何恢复）——session 崩溃后任何人读此文件即可恢复，这是恢复的权威依据而非记忆。

**I3 — 用户等待期间不欠调度债**。任何等待用户的 gate（P2 周报 ballot、P3 成对 ballot、T1、D5 裁决）到达时，**先按 I4 恢复全部调度再等待**；用户回复后需要继续改动时重新走 I2。生产不为人工等待停摆。

**I4 — 恢复是验证过的动作**。每次恢复：crontab 去 `[PAUSED` 前缀后 `crontab -l` 断言该行完整且无前缀残留；`launchctl bootstrap gui/$UID ~/Library/LaunchAgents/<alert plist>`（或 `./install.sh alert`）后 `launchctl list` 在册；观察下一轮 pipeline log 正常 DONE；**任何编辑了 serve import 面的 phase——按 I1 推导表即 P1、P2、P3 三者**——合并后 `launchctl kickstart -k gui/$UID/live.aiplanet.ai-radar.serve` 做 readback（V25/V27）：serve 端口**不硬编码**、从已安装 serve plist 读取并断言为 8010（记录来源）；healthz 断言 200；`/admin/usage` 受 Cloudflare Access guard 保护、无鉴权 curl 会 403——readback 按 `web/routes/admin.py` guard 实际支持的本地验证方式（header/token）断言 HTTP 200 + 目标字段，若 guard 无本地通过路径则以进程内调用 `collect_admin_usage` 做等价字段断言并注明；`pause-state.md` 清空对应条目。每 phase 末 verify 含全部恢复断言。

并发决策者：声明单 agent 决策者独占（用户单机单人，执行窗口无第二个 agent session 写本仓或 ai-assistant 仓）。cron/launchd 为非决策者写入者，停调度窗口内写入面已消除。出现第二决策者反证 → 按 `~/.claude/references/concurrent-plan-isolation.md`「执行中提升」处置。

## 背景与当前状态（可观察事实，2026-08-10 取证）

成本事件：2026-08-09 用户充值 DeepSeek 官方 ¥50，被 222 次成功 interpret 调用耗尽（llm_usage 实测：deepseek/deepseek-v4-pro，avg 72.7K input / 1.13K output tokens/call）。按官方牌价（未命中 ¥3/M 输入、¥6/M 输出）复算 ≈¥49.9，**对账误差 <1%**——同时证明当前缓存命中率≈0。

单篇 input ~73K tokens（~130K 字符）构成（ai-assistant summary-agent 侧实测）：

| 构成 | 体量 | 占比 | 变化频率 |
|---|---|---|---|
| existing_keywords 全量注入（11,266 词，实测 112,948 字符） | ~113K 字符 | ~70% | 随 KB 日增 ~180 词，**单调递增（复利泄漏）** |
| 固定脚手架（design_doc 节选+persona 3.5K+tags.md 16.8K+2 篇参考输出 8.9K+criteria 规则） | ~45K 字符 | ~25% | 模板级稳定 |
| 文章本身（平均 4.5K，最大 21K 字符） | ~5K 字符 | ~5% | 每篇变 |

**prompt 顺序缺陷**（`~/research/ai-assistant/agents/summary-agent/src/summarizer/prompts/user_article.md.j2`）：文章全文排在 user prompt 最前 → 跨调用共享前缀≈0，前缀缓存全废。

计量基建现状：

- `data/llm_usage.db`（独立库，migration 序列 `airadar_usage_migrations`）：四 stage 全记录（含 interpret，经 `interpret/runner.py::_record_interpret_usage`），字段 provider/model/item_id/tokens/input_char_count/cost_usd/attribution_json。
- **断点 1**：定价靠 `AI_RADAR_LLM_PRICING_JSON` 环境变量注入（`llm_usage.py:235`），**从未被设置** → cost_usd 全 0，且无未定价暴露，两个月无人发现。
- **断点 2**：`/admin/usage`（`admin/usage.py` ~line 194 直接累加存储列）纯 pull 无人看。
- **断点 3**：无成本 push（报表/告警皆无）。
- **断点 4**：usage 采集缺 cache 字段——ai-assistant `shared/llm/client.py::_usage_metadata`（~line 606）丢弃 `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`/`prompt_tokens_details.cached_tokens`。

告警现状：A1–A4 在 `admin/alerts.py`（规则 ~240–300，signals ~1339），阈值 `admin/thresholds.py`，per-severity lifecycle + `data/alert-events.jsonl` ledger。A1 对 interpret 断供盲（interpret 只在成功时写 usage）；A2 盲（心跳被退避空轮重置）。已批准建 A5。**本机 `admin alert-check` 无 dry-run 环境变量**（`AI_RADAR_ALERT_DRY_RUN` 只存在于远端 `deploy/server/health-check.sh`）——投递验证不得引用它。

近 14 天 provider/model 全集（成本解析必须覆盖）：

| provider | model | stages | 14d input |
|---|---|---|---|
| ark | deepseek-v4-pro-260425 | interpret, enrich | 28.5M |
| deepseek | deepseek-v4-pro | interpret, enrich | 23.0M |
| ark | deepseek-v4-flash-260425 | prefilter, score | 4.2M |
| deepseek | deepseek-v4-flash | prefilter, score | 1.7M |
| ark | deepseek-v4-flash-ga-260731 | prefilter, score | 0.1M |

interpret 占近 14 天总输入 ~84%。

定价事实（2026-08-10 核实）：

- **LiteLLM 社区定价 JSON**（`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`）已含 `deepseek/deepseek-v4-pro`（$0.435/M in、$0.87/M out、cache_read $0.003625/M）与 `deepseek/deepseek-v4-flash`（$0.14/$0.28/$0.0028）。按默认 7.2 汇率投影后，真实 222-call 锚点的派生 CNY 比官方 ¥ 牌价平行复算高 4.40%；二者不是一致口径，`cost-audit` 必须显式打印该差额而不能只用 ±5% band 吸收。**ARK 的 deepseek 型号（-260425/-ga-260731）不在表内**。
- ARK 挂牌价（llmabacus 比价，核对 2026-06）：deepseek-v4-pro ¥12/M in、¥24/M out（官方 4 倍）；**用户实际持有 ARK 订阅配额，计费覆盖与边际成本语义待核（T1）**。用户指定大规模运行优先路由 ARK 的 DeepSeek，官方 DeepSeek 仅保留给本类验证；这是一条账户/路由约束，不改变 T1 闭环前的 pricing 行为或 nominal 口径。
- `~/research/ai-agent-config/tt-web/pricing_fetcher.py` 是 harness 已有同题实现（LiteLLM URL + 7 天 TTL 缓存 + bundled fallback + fuzzy resolve + unknown 一次性日志）——D2 指定与它同源同逻辑。
- ⚠️ DeepSeek 官方已公告将大幅涨价 + 2× 峰时定价（日期均 TBA）。

其他已定事实：历史积压 152 篇已冻结（`error_retry_count=8`，可逆），本 plan 不处理；keywords 注入取舍与 ARK vs 官方路由取舍不在本 plan 范围（周报 per-provider 拆分为其供数）。

## 成本语义契约（全部消费端共用；V2/V11/V12 修复）

1. **查询时派生**（D2）：`cost(row) = f(provider, model, input_tokens, cached_input_tokens, output_tokens, created_at; pricing_table)`。历史行按调用发生时的有效价重算。存储列 `cost_usd` deprecated：历史值迁移为 `NULL`、新 writer 只写 `NULL`，任何读取端都不得把该列当成本输入；滚动发布窗口为避免尚未退出的旧 writer 因约束冲突静默丢失整行，schema 暂时接受遗留 numeric，但它不构成成本事实。旧进程完全退出后的退出条件是 `cost-audit` 同时确认 active `llm_usage`、legacy main-db `llm_usage` 与 `item_evaluations` 的 `COUNT(*) WHERE cost_usd IS NOT NULL` 均为 0，再另行评估恢复 NULL-only 约束。未知事实不得以 0 代替。旧环境变量 `AI_RADAR_LLM_PRICING_JSON` **退役**：代码在检测到它时响亮拒绝，`.env.example` 与当前运维文档删除该路径并在 CHANGELOG 注明替代物（防两套真相源）。
2. **定价源三层**：① LiteLLM JSON——vendored `src/airadar/pricing.py`（URL 固定、7 天 TTL、缓存落 `data/pricing_cache.json`、provider-scoped safe fuzzy，注明来源）；② supplement 表（ai-radar 内：ARK 三型号，保留权威币种 CNY 的 source tariff 及 USD 投影，带 `nominal=True`/source/verified_at/effective_from/effective_to；**resolve 优先级 supplement > litellm**，且按 usage `created_at` 选择半开有效区间，防后缀 fuzzy 误配和改价重写历史）；③ 未命中 → `None`。未知 ARK 日期后缀不得吸附到 bare upstream 条目。bundled fallback（repo 内快照 JSON，只含本项目模型子集）仅在网络刷新失败时使用。
3. **新鲜度三态（V11/V21）**：`fresh`（缓存 age < 7 天，直接使用）/ age ≥ 7 天时**同步刷新**，刷新成功回到 fresh、失败立即进入 `stale`（继续用过期缓存或 bundled fallback 但如此标注）/ supplement 条目 `verified_at` 超 90 天为 `due-review`。社区源的 `fetched_at` 与 supplement 的 `verified_at` 是不同事实，接口与页面分列。stale 与 due-review 均触发 §7 通知；**stale 状态下的成本在 A6 与周报中显著标注「按过期价估算」**，不静默当 fresh 用。权威价与对账锚点偏差 >±5% 时：关闭旧 supplement 区间并追加新条目、更新 fallback、重跑三消费端对账、留存无影响证明（P4 纪律条款承载长效义务）。
4. **状态语义**：每笔调用成本标 `priced`（fresh 实价）/ `nominal`（ARK 挂牌非实付）/ `unpriced`（无条目）。展示层 nominal 带口径标注、unpriced 单列——**不得静默归零**。
5. **币种**：内核 USD；每条价格同时携带来源币种及来源币种原始单价，USD 只是换算投影；展示 ¥=USD×`AI_RADAR_USD_CNY`（env，默认 7.2，`.env.example` 登记；报表页脚注明汇率），不得把 FX 变化伪装成来源单价变化。
6. **窗口与指标语义（V12，人读标签与计算契约一致）**：
   - A6 窗口=rolling `(now-24h, now]`（含右端点），detail 文案写「近 24 小时」不写「当日」；基线=过去 14 个 **UTC 日**的日成本中位数（<3 天不启用）。
   - 周报默认窗口=上一个 **Asia/Shanghai 自然周**（周一 00:00 至周一 00:00）；`--window-days N` 显式给出时改为 rolling N 日窗，**两者互斥**，报表头注明窗口起止时刻。
   - 环比=与**前一个等长窗口**同口径对比。
   - 单篇成本分母=interpret 成功调用数（usage 行数）；cache 命中率=Σcached_input/Σinput，仅在已采集子集上计算；**cache 数据覆盖率**（有 cache 事实的调用占比）单列——覆盖率为 0 时命中率显示「无数据」、token 拆分显示「未采集」而非 0%，部分覆盖时 token 总拆分仍为 `NULL` 并同时展示覆盖计数。P3 前无专列，但 provider 已返回的 cache 事实可暂存 attribution，因此覆盖率不预设必为 0。
   - `nominal_share` = nominal 成本 /（priced+nominal 成本）；unpriced 计数按 **(provider, model)** 分组。
   - Top 成本驱动=窗口内按派生成本降序的第一个 (stage, provider, model) 组及其金额与占比。
   - **统一指标表（V20/V28，周报与 /admin/usage 共用定义）**：per-stage 聚合 = 成本 / 调用数 / 每调用成本，各附等长前窗环比；单价表按 **(provider, model) 逐行**——未缓存输入 / cache-read / 输出三档 **source tariff**（来自定价表，非 blended）+ nominal 标记、新鲜度状态、source、checked_at 均属于该行；provider 级只做窗口实际总成本**汇总**（不称单价）；环比覆盖窗口总成本与各分组成本。
7. **未定价/陈旧/价格变化提醒**（D3/V29）：alert-check 周期内检测**新出现**的 unpriced (provider,model)、pricing 进入 stale、supplement 进入 due-review、以及 **price-changed**（当前活跃 (provider,model) 的有效 tariff 与上次观测相比任一档变化——刷新成功后的真实涨降价由此可见）→ 经 im-notify（无 --alert，NOTIFICATION 通道）各发一次，`--dedup-key ai-radar-pricing:<状态>:<provider>/<model>`。**re-arm（V22）**：im-notify 按同 key 下文本签名去重，同文本再入会被抑制——状态**解除**时静默执行 `im-notify --dedup-clear <同一 key>`（clear 失败响亮记入日志），保证以后以完全相同文本再入仍能发送。A6 的 page 只承诺 priced+nominal 部分；detail 在 unpriced>0 或 stale 时附注。
8. **对账不变量**：`./run.sh admin cost-audit`（P1 新增 CLI，V13）从同一 SQLite 事务快照读取 llm_usage 原始行，直接从 raw catalog 独立读取 matched key 与三档费率并重算 token 算术，再与生产派生总额及 `/admin/usage` 最小总额对账；差额不可解释则非零退出。对每个在用 pair 直接断言 `matched_key == requested provider/model`；任何 fuzzy 结果须有显式评审映射，否则标 UNVERIFIED 并非零退出；精确目录项被生产漏掉、或生产从正确 key 提取出错误费率，同样失败。默认输出是说明证据作用域的人读摘要，`--format=kv|json` 才输出机器细节；一致性 PASS 不声称 tariff 权威已验证。消费端矩阵随 phase 增长：P1 对 raw catalog+派生层+`/admin/usage` 最小总额；P2 在真实消费者定义稳定后加周报与 A6 输入；P3 cache 真值落库后全矩阵重跑。锚点案例：2026-08-09T10:52:42Z–16:26:29Z 窗口 `stage=interpret`、deepseek/deepseek-v4-pro 222 次派生成本 ∈ $6.94±5%（=¥50/7.2），并以官方 ¥ 牌价平行验算 ≈¥49.9 双口径互证（P1 实施时以生产 `llm_usage` 的 222 行首末时间修正原 10:15Z–16:00Z 过窄窗口）。

## L1：产物与使用方式

1. **ai-radar 成本观测**（P1+P2）：真实派生成本 + 每周成本报表（NOTIFICATION）+ A5/A6 告警（ALERT）。使用者=用户（运维者）。用途：周报驱动优化决策（keywords 取舍、ARK vs 官方路由、模型降档）——含 per-stage、按 (provider,model) 的 source tariff 与 provider 级窗口总成本汇总（V36，与契约 §6 统一指标表同口径）、interpret 单篇成本、cache 命中率与覆盖率、环比、nominal/unpriced/stale 口径；告警驱动即时处置。
2. **interpret 成本削减**（P3）：prompt 重排 + cache 字段采集。使用者=pipeline（自动）。用途：官方通道单篇 ~¥0.22 → ≤¥0.06，降幅由观测量化证明。
3. **harness 可复用能力**（P4）：原则档 + `/custom:review-llm-cost` + CLAUDE.md 路由。使用者=未来项目的 agent session。

成功定义：用户每周一收到能直接读懂的成本周报；**priced+nominal cohort 内**由 token 量/调用量/模型组合导致的成本异常当天被 A6 page（V33——unpriced cohort 无法计算成本突变，只承诺 D3 的首见/状态通知；价格变化经 D3 price-changed 通知可见，A6 明确不承诺）；interpret 单篇成本**主路径**实测 ≤¥0.06（**V34 条件分支**：若走 D5 二次失败回滚路线，允许的部分交付终态=模板恢复至 before hash、成本削减目标显式标记未达成、失败案例落 journal+issues、P1/P2 与 cache 字段采集保持 green——此时本条不算失败而算按 D5 终态交付）；`/custom:review-llm-cost` 对 ai-radar 的诊断与「背景」节断点吻合。

## 取舍偏好

- 告警少而准：A6 只管量结构突变；价格维度归 D3 通知；慢变量归周报。
- 观测先于优化：P3 降幅必须被 P1 观测证明。
- 零行为变更优先：只做 prompt 重排；改变输出语义的优化全部后置。
- 不过早抽象：定价 fetcher vendored 而非跨仓 import（开源仓中立性 > DRY）；harness 只建原则档+命令。

## UX 契约影响

**无影响**。公众站点用户可感知行为零变化——改动全落在运维面、成本内核与 harness 文档；`docs/contracts/ux-contract.md` 不含运维面。skip。

---

## Phases

顺序 P1 → P2 → P3 → P4。每 phase 独立过 review-gate；隔离按「运行不变量」执行。

### P1：定价派生层（ai-radar）

> **范围收窄（2026-08-11，用户经 AskUserQuestion 裁决）**
>
> 触发事实：P1 已跑 3 轮独立 review + 2 轮修复，累计约 75 条 findings。派生内核（真实查询时成本、¥50/222 次锚点双口径）自第一轮起稳定通过、再未出问题；**findings 高度集中在展示契约**（统一指标表、多承载新鲜度与有效期、等长前窗环比），且每轮修复在修好被点名项的同时按同一族产出新缺陷（第三轮实测新引入：管理页 `change_pct=None` 时 `TypeError` 500；审计 oracle 改用生产 quote 费率后对费率提取错误失明；daily 表 Input tokens 列被 cache 拆分 macro 顶替）。
>
> 结构性原因：`schema-design-principles` 第一条即「消费者与界面先行」。P1 在**其消费者尚不存在**时先建了大片人读指标面——周报（P2）与 A6 告警（P2）才是这些字段的真实消费者，它们不在场，就没有任何东西能裁定每个字段必须意味着什么。这就是 findings 源源不断的来源。
>
> **P1 保留**：真实查询时派生成本（三态 priced/nominal/unpriced，未测量即 `NULL` 贯穿全链）、supplement 有效期区间（已实现且实测安全：0 行变 unpriced）、`cost-audit` 对账 CLI、以及一个**最小 admin 视图**——窗口总额与三态拆分、来源单价表（含来源币种与 USD 投影、新鲜度、`verified_at`/`fetched_at`）、unpriced 清单、cache 采集覆盖率。
>
> **移交 P2**（连同其 verify 一并移交，P1 不再为它们负责）：统一指标表的 per-stage / per-provider 聚合、**全部环比与前窗对比**（`comparison` 块、`change_pct`、可比性判定）、daily 的 model×stage 三层嵌套序列、LiteLLM 承载的有效期机制。理由：这些的验收判据只有在周报与 A6 的具体需求定下来之后才成立。
>
> **不因收窄而豁免的**（必须在 P1 内修完）：① `record_llm_usage` 的 `except sqlite3.Error: return` 会让撞上新 `CHECK` 的写入者**整行 usage 静默丢失**——计量行本身丢了补不回；② `item_evaluations.cost_usd` 仍被硬编码为 0 并经 `/api/v1/admin/metrics` 与 alert signals 发布，本轮只删了 `admin.html` 上那个人能看见的显示位，留下的恰是 P2 告警要取数的一侧；③ 审计 oracle 必须恢复**独立读取 catalog 原始费率**，使身份错误与费率提取错误都能被发现。
>
> 相应地，「成本语义契约 §6 统一指标表」整节的落地时点改为 P2；§6 中 A6 与周报的窗口语义本就属 P2。

**改动**（按上述收窄后）：

1. 新增 `src/airadar/pricing.py`：LiteLLM fetcher + supplement + 三态/新鲜度（成本语义契约 §2/§3/§4）。
2. `src/airadar/llm_usage.py`：新增 `derive_cost_usd(row)`（cache hit/miss 拆算分支——P3 前缺测保持 `NULL`，成本计算保守按 miss）；`estimate_cost_usd` 退役，废弃存储列历史迁移/新写均为 `NULL`，rollout 兼容窗口接受但忽略旧 writer 的 numeric；严格 writer 对 SQLite 拒绝继续抛出，已取得付费模型结果的 consumer boundary 则以独立可计数的 metering failure 日志保留结果且不触发 provider/interpret 重试；在 attribution 中止血保存 provider 已返回的 cache 事实；`AI_RADAR_LLM_PRICING_JSON` 设定时响亮拒绝。
3. `admin/usage.py` 聚合改走派生函数；最小响应只保留窗口总额与 priced/nominal/unpriced 拆分、unpriced（按 provider+model）、nominal_share、新鲜度、含 source currency 与 verified/fetched 分列的来源单价、cache 覆盖率、usd+cny+汇率；未知值贯穿 SQL/API/HTML 为 `NULL`/「未采集」。`item_evaluations.cost_usd` 改为 nullable 且 writer 写 `NULL`，`/api/v1/admin/metrics` 与 alert signals 不再发布伪造的 stage `$0`。
4. 新增 `./run.sh admin cost-audit`（对账 CLI，契约 §8；本 phase 覆盖 派生层+/admin/usage 两端）。
5. `.env.example`：删旧 pricing 段、加 `AI_RADAR_USD_CNY`；`data/pricing_cache.json` 入 `.gitignore`。

**内部 verify（V15 枚举，不留给临场判断）**：

- 定价：表内模型精确成本；cache 拆算两分支；unpriced/nominal/priced 三态；fuzzy resolve（`deepseek-v4-pro-260425` 命中 supplement 而非 litellm；supplement 优先级）；URL 常量断言；缓存 age<7d 用缓存、**age≥7d（含等号边界）触发同步刷新**；刷新失败→stale 标记+fallback 路径（断网 mock）；supplement due-review（checked_at>90d）状态。
- 汇率：默认 7.2 与 env override 两例。
- `git check-ignore data/pricing_cache.json` 通过。`uv run pytest`（AI_RADAR_DB 临时路径）**相对 baseline 无新增失败** + ruff + mypy 全绿。

> **判据修订（2026-08-10，用户经 AskUserQuestion 裁决）**：本 plan 全部 phase 的「`uv run pytest` 全绿」一律改判为「**相对 baseline commit 无新增失败**」。原判据建立在错误前提上——该套件在本机从未绿过，与本 plan 无关。对照方法（每次援引本判据时照此取证）：`git worktree add --detach <tmp> <baseline commit>`，其中 `uv sync --group dev` 后跑**同一条** pytest 命令与同一 `AI_RADAR_DB`，比较失败集合；出现新增失败即未通过，失败数持平或下降即通过。实测 baseline（`8b686df`，无任何 P1 改动）：`tests/test_performance_journey_monitor.py` 11 failed / 62 passed（带 P1 为 10 failed）；`tests/playwright` 对 shipping DB 独立跑 25 failed / 89 passed / 4 errors（带 P1 为 15 failed / 99 passed）。既有失败连根因落 `docs/issues/` 单独排期，不阻塞本 plan。ruff / mypy 仍要求字面全绿（它们本来就是绿的）。

**L2 verify（agent 独立执行）**：`./run.sh admin cost-audit` 输出 expected vs actual 对照全过（含 §8 锚点双口径）；serve kickstart 后 healthz 200 且 `/admin/usage` 实测非零成本+unpriced 计数；恢复断言（I4）。

### P2：A5 / A6 告警 + 每周成本报表 + 未定价提醒（ai-radar；A1）

> **P1 移交注意**：环比/前窗可比性不能只检查数据起始时间，还必须检查两个窗口的 cache 测量覆盖是否同口径。P3 使 cache 采集在窗口中途上线时，完全相同流量曾被旧实现误报成本下降 51.4%；P2 在周报/A6 消费契约确定后必须把该过渡状态纳入验收。

**动工前必读**：`~/.claude/references/alerting-review-principles.md`、`human-facing-message-principles.md`、`service-operations-protocol.md` §6。review-gate 叠加告警专项审。

**A5「微信解读产出停滞」**——同时满足才 firing（page）：

1. 近 `no_success_hours`（默认 4）无成功解读（无 `error IS NULL` 且 processed_at 在窗口内的行）；
2. 存在符合 `_candidate_rows` 资格的待处理微信 item 且 `fetched_at <= now - no_success_hours`；
3. 排除 `error_retry_count >= 8`（冻结积压不算 pending）。

detail：停滞时长、等待文章数、最老待处理标题；action：「查 data/ark-breaker.json 的 reason 与 DeepSeek 余额；402=余额不足，429 AccountQuotaExceeded=方舟配额」。

**A6「LLM 近 24 小时成本突变」**（D4 缩窄语义）：rolling `(now-24h, now]` 全 stage 派生成本（priced+nominal，stale 时显著标注）> max(¥`daily_floor_cny`（默认 20）, `spike_multiplier`（默认 3）× 14 UTC 日基线中位数) → page。**A6 检测的是 token 量/调用量/模型组合的异常；价格变化不在其承诺内**（涨价由 D3 链路的 stale/复核通知与周报暴露）。detail：近 24h 成本、基线、Top 驱动组、unpriced/stale 附注；action：一行 sqlite 定位命令。

**未定价/陈旧提醒**：契约 §7，挂 alert-check 周期，NOTIFICATION+dedup-key，不入 page lifecycle。

**每周成本报表**：`./run.sh admin cost-report [--window-days N] [--send|--dry-run]`（`admin/cost_report.py`）。内容按契约 §6 全部指标+页脚口径。调度：crontab `17 9 * * 1`（周一 09:17，全篇以此为准），`run-or-alert --key ai-radar-cost-report --` 包裹。**安装机制（V19）**：扩展 `install.sh` / `uninstall.sh` / `status.sh` 支持 `cost-report` target——幂等（重复安装不重复、merge 保留无关 crontab 条目、卸载只删本条），`deploy/cron/ai-radar-cost-report` 为模板（**绝对路径 + 显式 PATH**，V32）；`docs/operations/services.md` 表述与之一致。**cron 真实投递验证（V32）**：P2 动工前 preflight——`im-notify` 在模板 PATH 下可执行、`FEISHU_GENERAL_ALERT_WEBHOOK` 与 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK` 两者存在（存在性检查、不回显值）、模板无占位路径残留；安装后在 `env -i HOME=$HOME PATH=<模板同款>` 的 cron 等价环境**执行实际安装的那条命令一次**，以这一次真实送达的消息作为用户 ballot 材料，检查 run-or-alert 退出码、im-notify 回执与日志——手动 `--send` 成功不构成 cron 链路已验证。

**内部 verify（V14 修正 + V15 枚举）**：

- A5 五例：新鲜 pending（`fetched_at > now-window`，age<window）不 fire；`fetched_at == now-window` 等号边界 fire；超窗 pending+无成功 fire；窗口内有成功不 fire；仅 frozen backlog 不 fire。
- A6：2026-08-09 真实日成本形状注入 → fire；正常日不 fire；基线 <3 天不启用；`(now-24h, now]` 右端点含、左端点不含；UTC 日切边界；stale 标注分支；unpriced 附注分支。
- D3 提醒四段链：首次发送 → 同状态重复被抑制 → 状态解除触发 `--dedup-clear` → 以**完全相同文本**再入仍发送；NOTIFICATION 通道（非 --alert）断言；clear 失败的响亮日志分支；price-changed 四例（无变化抑制、变化首发、重复抑制、再次变化重发）。
- **D3/A6 联动负例（V37）**：usage 行、时间与模型组合完全不变、仅有效 tariff 变化的 fixture——断言 D3 首发 price-changed，且 A6 firing 状态与 page 计数**均不变**（实现须能隔离 tariff-only delta，不把价格变化误报为量结构突变）。
- 周报：上海自然周切界、`--window-days` 互斥语义、前一等长窗口环比、FX override、cache 覆盖率=0 显示「无数据」、`--dry-run` 快照（nominal/unpriced/stale/环比分支）；**已知 usage 行 fixture 的 exact expected 快照逐项断言统一指标表全部字段**（V20；P3 后追加非零 cache fixture 验命中率与覆盖率）。
- 消息级断言：A5、A6、A6-附注、D3 三类通知的 detail/action 逐字段断言。
- lifecycle firing→resolved 回归（照 A1–A4 测试模式）；install.sh cost-report 幂等三例（装/重装/卸）。

**L2 verify**：

- **真实 driver smoke（V43，不污染生产 state）**：经实际 `./run.sh admin alert-check` CLI 全链路（ruleset+collector+真实 sender）跑一次——主 DB、usage DB、alert state、ledger、pricing cache **全部注入临时路径**（现有接口不足时增加最窄的可注入 CLI 参数），分别种入 A6 与 D3 fixture，断言真实发出 `[TEST]` 前缀消息（ALERT firing→resolved 各一条 + D3 NOTIFICATION 一条）与 im-notify 退出码回执。
- 周报实发走 **V32 的 cron 等价环境执行**（不是裸 `--send`——那只验证交互链路），ballot 材料 = **真实送达的那条消息 + 同一 formatter 生成的紧凑分支包**（V45：nominal、unpriced、stale、cache coverage=0 四分支各一份样例文本）→ **用户 ballot（人工 gate，等待前先恢复调度 I3）**：对真实消息与每个分支样例各应用四问——①读得懂 ②口径清楚（nominal/unpriced/stale/汇率）③能定位成本驱动 ④知道下一步——全「是」进 P3，任一「否」修文案后按同法重发（真实消息证明投递链，分支包证明全部承诺口径可读）。
- 调度断言：`crontab -l` 恰一条 cost-report、五字段 `17 9 * * 1`、run-or-alert 包裹；`./status.sh` 含 cost-report 状态；恢复断言（I4）。
- 文档（V18）：README、`docs/CLAUDE.md` 索引、`docs/operations/services.md`、`docs/operations/monitoring-alerting.md`（A5/A6/D3 段）、**`docs/architecture.md`**（usage/定价层）、CHANGELOG 同步；**清扫边界**：仅当前权威可变文档（README/docs 顶层运维档）更新 A1–A6 表述，ADR/issues/历史 plans/本 plan 为历史材料**显式允许**保留 A1–A4 字样（`git grep` 结果逐条归类而非零命中）。

### P3：prompt 重排吃前缀缓存 + cache 字段采集（ai-assistant + ai-radar；A1）

**动工前必读**：`~/research/ai-assistant/CLAUDE.md` **和** `AGENTS.md`（并存，被遮蔽也要读）。`shared/llm/client.py` 为共享组件，字段只增不改。

**Eval manifest（V3+V16——编辑模板之前冻结）**：

1. 选 10 篇近期已解读文章（覆盖长/短、三档 recommendation），**按 `summarize.sh` 原生输入格式把十份输入文件物化冻结到 `artifacts/inputs/`（V39）**；`eval-manifest.json` 记录每份的路径 + 整文件 sha256 + item_id + title + 顺序——before/after 只准消费这些冻结路径，运行前重新计算 hash，漂移即失败。
2. **before run**：模板未改时，唯一入口 `summarize.sh --input <file> --user <eval 用户> --model ai-radar-interpret-deepseek` 串行跑 10 篇（只产 batch 不写 KB；串行理由：N=10、可比性、限流安全）。每篇保存：**完整渲染后的 system+user prompt 全文**及其 hash、输出、usage 元数据（provider/model/实际 model revision）→ `artifacts/before/`。
3. 改模板（见改动 1）。
4. **after run**：同入口、同 model、同顺序重跑 → `artifacts/after/`，同样保存渲染 prompt 全文。
5. **自动断言**（预先固定的聚合判据，不依赖逐字复现；**禁止 rerun-to-green**——除 V23 规定的对称重跑外不得重跑至通过）：两轮 provider/model/**实际 revision** 一致；**manifest 冻结 effective sampling 契约（V23）**：temperature/top-p/seed（provider 支持则 before/after 固定同 seed）/retry policy 逐值记录且两轮相等，每次 attempt 的元数据入 artifacts；provider 不支持 seed 时，对两篇 ballot 样本在 before/after 各重复跑一次、按预先固定的聚合判据（分布带+schema）判定，retry 导致 attempt 数不对称时该对作废并按固定规则重跑该对；**逐块 hash 对比：除块顺序移动外，各内容块（persona/tags/keywords/参考输出/criteria/JSON 说明/文章）文本 hash 一一对应相等**——keywords 块两轮 hash 必须相同（若期间 KB 增长导致不同 → 以新 keywords 快照重跑 before）；`system.md.j2` hash 未变；JSON 尾块 10/10 可解析、必选模块齐全；recommendation/save_decision 分布落近 14 天生产带内（T4 现算）。
   - **判据修订（2026-08-11，user adjudication）**：冻结判据曾把同一 production-derived recommendation interval 同时用于 primary N=10 与 ballot repeat set N=4；D5 after-redesign 实测 primary N=10 三档全部在带内，但 N=4 的 `必读=2` 超出冻结 `[0,1]`。两篇各跑两次的 repeat set 中，全部 before/after 差异来自 `cec6aabadcc4ed2a`：before primary=`必读`、before repeat=`值得一看`，after primary/repeat 均=`必读`。before 侧在模板不变时已跨相邻档翻转，证明该小样本的运行内噪声底高于冻结 band；repeat 本应量化这种 variance，而原判据没有使用它。用户据此裁决：N=4 band 作为 criterion defect set aside，primary N=10 三档结果成为 operative distribution gate。这个修订是在看到 reading 后发生，确实削弱预注册纪律；因此只能由 user adjudicate，implementer 与 supervisor 均不得自行消化或把它改写成原判据从未失败。原始自动断言 artifact 保留 frozen failure，audit trail 不覆盖。
6. **人工 gate（成对 ballot；等待前恢复调度 I3）**：2 篇 before/after 成对 md 绝对路径给用户，逐样本三问（语义保持/必选模块/档位合理）。**每对材料带紧凑 header（V30）**：model 实际 revision、sampling/retry 契约值、`activation preflight: PASS`（=该对全部自动断言已过）——用户不必另开 manifest 即可确认比较有效。
   - **Outcome（2026-08-12，user adjudication）**：两组成对材料的三问均通过，human gate=`PASS`；D5 reorder + 七字段 reminder 因此保留。该裁决只关闭输出质量 gate，cache saving 仍须由 V40/V42/V44 official-channel L2 单独证明。其后第一个已充值的 L2 在 production tag validation 遇到 `toC-Agent变现`，implementer 一度误把它归因于 reorder 并执行 V38 回滚；独立 provenance 随后证明该值由 2026-06-07 的旧模板批次 commit `4a74a58353d8091af81d74c09bb6fc946226699d` 先作为关键词引入，早于任何 P3 reorder。用户据此裁决该预存 vocabulary gap 不适用 D5/V38，回滚已逆转，最终 tree 继续保留已通过 ballot 的 template。
7. **失败路线（D5/V24）**：任一不过 → 在「零输出行为变化」约束内允许**一次**有界重设计（如调整衔接句或块内顺序），基于原 before 重跑完整 after 与全部自动断言，**重新生成两组成对材料，先恢复调度（I3），再向用户重跑一次且仅一次相同三问 ballot**——自动断言通过不构成放行，人工 ballot 是必经门；仍有任一项不过 → 自动回滚模板（单文件 revert）、通报用户（不再询问）、**保留 cache 字段采集与 P1/P2 全部交付**，失败案例（成对样本+ballot 结论）记入 journal 与 `docs/issues/`，继续 P4。**回滚终态验收（V38，可失败断言）**：模板文件 hash == before 快照 hash；journal 与 issues 条目明示「成本削减目标未达成」；重跑 P1/P2 consumer matrix（cost-audit）、cache migration/透传测试与两仓 lint/test 全绿——任一不过即回滚不完整，不得进 P4。

**改动 1——重排 `user_article.md.j2`**（段落文字零改动，仅移动顺序与最小衔接句）：新顺序按变化频率升序 `用户画像 → 标签词汇表 → 参考输出 → 推荐等级判断 → 保存判断规则+JSON 形状 → 已有关键词 → 文章元数据 → 文章全文`；keywords 紧贴文章前；system.md.j2 不动。

**改动 2——cache 字段采集**：`_usage_metadata` 增取 `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`/`prompt_tokens_details.cached_tokens`（向后兼容，只增）；ai-radar `llm_usage.db` 加列 `cached_input_tokens`（照 `airadar_usage_migrations` 序列），各 stage 透传，`derive_cost_usd` cache 分支从此有真值。**归一化契约（V41）**：`cached_input_tokens` 的取值优先级=① 直接 hit 字段（`prompt_cache_hit_tokens`）→ ② `prompt_tokens_details.cached_tokens` → ③ 仅有 miss 字段时取 `input_tokens - prompt_cache_miss_tokens`；全部缺失记 NULL（保持覆盖率语义）；多源同时存在且互相矛盾、或越界（`cached > input` 或 `< 0`）→ 响亮失败不落库。测试覆盖三分支、多源冲突、越界与 `0 ≤ cached ≤ input` 不变式。

**内部 verify**：模板渲染单测（新顺序、变量齐全、与原模板 diff 仅移动无删改）；`_usage_metadata` 新字段单测（有/无、dict/对象）；migration 幂等+透传单测；两仓 lint/test 全绿。

**L2 verify**：**mutation-bearing cache 实证（V40，替代裸两连发）**——用隔离 eval user/KB：第一次官方通道解读后走真实 `--save-from-batch` 保存路径，断言 KB keywords 快照 hash 确实变化，**再**做第二次官方通道解读——两次解读必须是**不同 item_id、不同输入文件 hash 与文章正文**（V42，防同文重放虚增命中）——在这个含真实保存的序列上断言：第二次 cache 命中 ≥ 输入 80%（keywords 变化只应失效其后前缀）、`cached_input_tokens` 落库、派生单篇成本 ≤¥0.06。**官方通道口径（V44）**：本实证直接调 raw model `deepseek-v4-pro` 并预检断言 `provider=deepseek`、无 ARK/fallback 参与、实际 revision 已记录——`ai-radar-interpret-deepseek` 别名是 ARK-first，不能充当官方通道证明。不达标走 D5 路线；ARK 通道观察 cached_tokens（有则断言、无则记录实测闭环 T2）；`cost-audit` 全矩阵重跑（契约 §8 P3 档）；Eval manifest 全流程通过（或走 D5 失败路线）；**serve kickstart + healthz 200 + `/admin/usage` 的 cache 覆盖率/命中率 readback**（V25——P3 编辑了 serve import 面）；恢复断言（I4）。

**L2 outcome correction（2026-08-12，user adjudication）**：第一个充值后的 fresh 序列通过 V42/V44 preflight，并由官方 `deepseek/openai-api/deepseek-v4-pro` 完成第 1 调用（82,762 input / 1,981 output / 4,480 cached input；无 ARK/fallback），随后 production validator 以未知 tag `toC-Agent变现` 拒绝结果；real save、第 2 篇与 usage DB 均未发生，故当次目标 hit rate、`cached_input_tokens` landing 与 ≤¥0.06/篇保持未测量。implementer 当时误判 D5 已耗尽并执行 V38 回滚。独立 provenance 后来确认：data submodule commit `4a74a58353d8091af81d74c09bb6fc946226699d` 在 2026-06-07、仍使用 article-first 旧模板时已把该值作为关键词写入已提交 summary/index；受控 catalog 不接受模型后来把它提升成 tag，是独立的历史数据/词表契约缺口，不是 reorder 的输出行为变化。用户裁决 D5/V38 不适用，回滚已明确逆转；template 恢复为 D5 SHA-256 `c29f794c66836ffcd45cbca780a665a963a70e746d426c5dfc2c475ded578dd3`，并与 `artifacts/after-redesign/run.json` 保存的 12 份完整 rendered prompt 逐一 hash 相等（`checked=12, failures=[], pass=true`）。词表缺口移交 ai-assistant issue，不改 catalog/validator；新的完整 official-channel mutation-bearing 序列改用两个已在 committed catalog 内通过过 validation 的不同冻结输入，从第一篇重新开始。

**Provenance-corrected fresh L2 outcome（2026-08-12）**：唯一新序列预先固定 `e964cb1da864fcb6` → real save → `398c50cf6c6ffab7`；V42 的不同 ID/path/hash/正文与 V44 raw official route preflight 全过。第 1 调用实际为 `deepseek/openai-api/deepseek-v4-pro`、fallback=false，production validation 全过，usage input/output/cached/miss=`76,891/1,912/66,816/10,075`，偶发首调用 hit rate=`86.897%`、当前 tariff 派生 ¥0.045275566；该值不是 mutation-bearing 第二调用指标。真实 `--save-from-batch` 已成功把 slug 写入隔离用户，但命令 stdout 顺序输出两个 JSON object，eval runner 按单 object 解码而以 `JSONDecodeError: Extra data` 停止。只读保存后核验又发现新结果的 10 个关键词全部已存在，keywords 集合保存前后均为 count=`11,519`、SHA-256=`9ef9b44ae2c74b25de7cd08dcbf4317f66f7745dc1532f49baffec3014881974`，所以 V40 的真实 mutation 断言本身未成立。第二调用没有启动，也没有创建隔离 usage DB；要求的 post-save hit rate、`cached_input_tokens` landing 与第二篇成本因此保持 absent，不能以首调用数值替代。禁止 continuation/per-item/full rerun，故本 phase 不再重试。这个 evaluator/candidate 终止同样不是 template 输出质量回归，不触发 D5/V38；已通过 ballot 的 D5 template 保留，但 P3 的 ≤¥0.06 mutation-bearing saving claim 仍未获验证。

**Candidate-grounded fresh L2 checkpoint（2026-08-12，awaiting user contract adjudication）**：源码与既有 CLI test 证明 `save-from-batch` 的 two-object stdout 是预期 record-stream contract，runner 因而改为严格解析 object stream 的最后一条；一/二 object 阳性与 trailing garbage/non-object 阴性均通过。冻结 before 对当前 11,528-keyword snapshot 的集合差选出 `1b0e38e487e98573`，其预测 novel keywords `hard-deny`、`对抗测试` 在 before/after-redesign 两次都出现；不同 hash/text 的第二篇为 `398c50cf6c6ffab7`。这一次 fresh sequence 实际完成 call 1 → real save → mutation assertion → call 2：keywords `11,528→11,533`、hash `07c11a...→5d714f...`；两次均为 `deepseek/openai-api/deepseek-v4-pro`、fallback=false、validation PASS。call 2 raw usage input/output/cached=`76,599/2,014/74,880`，raw hit rate=`97.755845%`，按当前 frozen DeepSeek tariff 与 USD/CNY 7.2 的只读投影为 `¥0.019953972`。runner 在两次调用后的本地 usage landing 阶段因未把 ai-radar `src` 加入 import path 而以 `ModuleNotFoundError: airadar` 退出；因此 isolated usage DB 不存在，`cached_input_tokens landed` 尚不能判 PASS，原 `cache-l2.json` 保持 failed/immutable。decision review `/root/v40_finalize_decision_review` 判定技术上可从两次 raw result 做零-provider deterministic finalization，但既有“禁止 continuation/per-item patch-up”没有定义该边界，gate 出口为交用户；若用户放行，必须生成独立 append-only finalization artifact，保留原失败、记录 provider calls=`0`、代码树与定价 provenance，并复核后才能关闭 V40。不得重跑 provider。

**Append-only finalization adjudication（2026-08-12，supervisor）**：supervisor 授权 option 1。先前禁止 continuation/patch-up 的对象是把 partial paid results 拼成完整序列，或逐 item retry 直到数字变绿；本次两次 provider call 与 raw readings 都已冻结，后续 landing 是无 sampling、无 model、无结果选择空间的 deterministic transform，故不属于该禁令。重跑 paid sequence 反而会因 provider cache state 已变化而产生更差证据。finalization 必须 provider calls=`0`、原 failed checkpoint byte-identical、usage DB 与 append-only artifact 并列生成；命中率/成本从 raw usage 重算并独立核对冻结读数 `97.755845% / ¥0.019953972`，不符即 finding、不得吸收差异。成本结论须记录 official DeepSeek authoritative tariff provenance，并以 weekly report 的 interpret 每篇口径作可比基线，将 gap 拆成 cache 与其他部分。

**V40 final outcome（2026-08-12）**：append-only finalization 与独立 readback 均为 provider calls=`0`，原 failed checkpoint 与两份 raw call 文件保持 SHA-256 `a09a4a4... / 03834fea... / e9ca90bd...`。从第 2 调用 raw usage 独立重算得到 cache hit=`74,880/76,599=97.755845%`、current-tariff billed cost=`¥0.019953972`，分别通过 ≥80% 与 ≤¥0.06 gate，且与 checkpoint 冻结读数逐舍入位一致；isolated `llm-usage.db` 两行均落下 `cached_input_tokens=74,880` 及 source=`prompt_cache_hit_tokens`。同一调用 cache-all-miss 为 `¥0.252523764`，所以可严格归因于 cache 的节省为 `¥0.232569792`（92.098%）；与当时 weekly report 的 cache-neutral interpret comparator `¥0.5723/篇` 相差 `¥0.552346028`，其中上述 `¥0.232569792` 是 cache，余下 `¥0.319776236` 不归因于 reorder/cache，包含 prompt/output shape、provider/model mix、production distribution 与 catalog effects。此次 raw official `deepseek-v4-pro` 的 underlying tariff 直接指向 DeepSeek 官方定价页，证据强于 ISSUE-004 尚未核实的 ARK nominal list prices；weekly comparator 仍只是挂牌价估算，不是实际边际支出。初版 finalization 在 WAL readback connection 尚未显式 close 时记录了创建时 DB 字节 hash；进程退出 checkpoint 后该 hash 变化。逻辑行与所有 gate 未变，runner 已改为显式 close，并以不覆盖原 artifact 的 `cache-l2-finalization-readback.json` 记录 settled DB SHA-256 `f177c1fb...` 及该 finding。V40/V42/V44 关闭，P3 成本降低目标达成。

### P4：harness 原则档 + 审查命令（user-scope；指令 artifact；不停调度）

**产出**：

1. `~/.claude/references/llm-cost-observability-principles.md`：观测四件套（per-call 计量含 cache/归因；真实定价——社区源+TTL+supplement+**未定价必须可见**+新鲜度三态；push 消费端——报表管趋势+告警管量结构突变、价格维度走独立通知链、纯 pull 不达标；归因到调用点/prompt 构成）；成本解剖方法（固定脚手架/动态注入/正文三分、复利型泄漏识别——keywords 案例、cache-fit 排序）；定价维护纪律（社区源自动跟价边界、supplement 复核期、渠道加价意识——ARK 4× 案例、币种口径、**权威价偏差 >±5% 时的更新与对账重跑义务**）。案例锚：本次全程（¥50/222 对账、130K 解剖、命中 0→P3 实测值）。
2. `~/.claude/commands/custom/review-llm-cost.md`：四层诊断（计量→定价成本→消费→归因），每层判据+典型修复；交互裁决同 review-alerting；**运行契约（T5）**：局部修复后 impact-scoped 重验范围、何种变化使既有 green 失效、终止=四层 full review clean（保持 `(A0,V1)`，不引入 ledger/receipt 机制）。
3. user-scope `~/.claude/CLAUDE.md` BINDING 路由段（「服务告警设计」附近）。

**verify**：指令 artifact 专项路由（原则档→`/custom:review-principles`、命令→`/custom:review-skill`，review-gate 内建）。**校准用例（V35 provenance）**：`baseline`=P1 phase commit 的 parent（记入 journal），`git worktree add --detach` 临时只读 checkout（非实施隔离用途，用完即删）上运行 `/custom:review-llm-cost` 至 remediation 前停止；`current`=P3 完成后 commit 于主 checkout。**两侧各在独立 fresh context 运行两次**，逐次记录 command hash、harness/model/version；baseline 两次均须命中断点 1–4，current 两次均须四层贯通无 blocker；禁止 rerun-to-green（任何一次不符即为校准失败，修命令后重新双跑）。两侧 expected 集合写进命令测试段。

---

## Bounded TODOs

| # | 细化的是 | 内容 |
|---|---|---|
| T1 | P1 supplement ARK 行 | 已知调查输入：用户持有 ARK 订阅配额，并指定大规模 DeepSeek 运行走 ARK、官方直连仅用于验证。方舟控制台需核实该订阅覆盖的模型/阶段、计费周期、含量、超量规则与真实边际实付；若生产调用被订阅覆盖，当前 ARK list-price 派生金额及周报 nominal share 是挂牌价估算占比，不是用户实际支付占比。按量→用实价替换（nominal→priced）；包量→保留 nominal 并在周报明示。人工项：implementer 给控制台路径+字段清单，用户贴截图或口述；闭环前不改 pricing 行为。 |
| T2 | P3 ARK cache | 实测方舟兼容层是否返回 cached_tokens、前缀缓存是否默认开启；结论闭环进周报口径。 |
| T3 | P2 周报文案 | 终稿逐条过 human-facing-message-principles（构建自查+专项审）。 |
| T4 | P3 分布带 | recommendation/save_decision 近 14 天生产带由 implementer 现算。 |
| T5 | P4 命令运行契约 | impact-scoped 重验范围、green 失效条件、四层 clean 终止条件（轻量）。 |

## Risks

| 风险 | 缓解 |
|---|---|
| DeepSeek 大幅涨价（已官宣、日期未定） | LiteLLM 源+TTL 自动跟价（**刷新成功时**滞后 ≤7 天；刷新失败以 stale 通知暴露）；**发现通道是 D3 链路与周报单价列，A6 明确不承诺**（D4）；契约 §3 的 ±5% 偏差义务兜底 |
| LiteLLM 条目滞后或错价 | supplement override；P1 真实账单锚定对账；§3 偏差处置 |
| prompt 重排输出漂移超带 | Eval manifest 成对回归+ballot；D5 一次有界重设计后回滚，保留其余交付 |
| ARK 包量语义混淆 | nominal 三态明示；不据 nominal 做路由决策（T1 闭环前） |
| 停调度忘恢复 / 崩溃后状态丢失 | 运行不变量 I2 落盘 `pause-state.md` + I4 每 phase 末恢复断言；I3 保证用户等待不欠调度债 |

## Defaulted Decisions（planner 拍板，reviewer 请审）

| 决策 | 取值 | 理由 |
|---|---|---|
| 周报调度 | 周一 09:17（`17 9 * * 1`），run-or-alert 包裹，install.sh 管理 | 周初读趋势；避整点；静默缺席可发现；幂等装卸 |
| A5 阈值 | no_success_hours=4 | 稳态 ~1.7 篇/h → 最老 pending 等满 4h 才 page；可配 |
| A6 阈值 | max(¥20/日, 3× 14 UTC 日中位)；<3 天不启用 | 昨晚事件必 fire；正常日（<¥5）不扰 |
| 汇率 | `AI_RADAR_USD_CNY` env 默认 7.2 | 展示层；页脚注明 |
| fetcher 形态 | vendored（不跨仓 import tt-web） | 开源仓中立；同源同逻辑满足 D2 |
| cost_usd 列 | 保留、旧值迁移为 NULL、新写 NULL；rollout 期间接受但忽略旧 writer numeric，拒绝写入不得静默 | 避免旧进程与新 schema 交错时丢整行；派生成本仍是唯一真相 |
| 旧 pricing env | `AI_RADAR_LLM_PRICING_JSON` 退役（删代码路径+.env.example 段+CHANGELOG 注明） | 防两套真相源；从未被使用过 |
| 回归抽样 | N=10 + 成对抽看 2 篇 | 用户确认档位带方案 |
| keywords 新序位置 | 慢变块最末、紧贴文章 | 日级变化只失效自身之后前缀 |
| A5/A6 框架 | 并入 alerts.py ruleset/lifecycle | 复用全套 |
| D3 挂载点 | alert-check 周期 + im-notify NOTIFICATION + dedup-key | 复用调度与去重 |
| P4 baseline checkout | 临时 detached worktree 只读 | 不碰生产运行态 |
| supplement 复核期 | 90 天 due-review | 与「价格随时可能调整」的现实匹配，不高频打扰 |

## 用户决策 gate 汇总

| 位置 | 决策 | 材料与回复方式 | 调度状态 |
|---|---|---|---|
| P2 末 | 周报 ballot 四问 | 飞书实发那条报表；逐问是/否 | 等待前已恢复（I3） |
| P3 末 | 成对抽看 2 篇 | `artifacts/` 成对 md 绝对路径；逐样本三问 | 同上 |
| P3 失败时 | D5 已预授权一次重设计；二次失败仅通报不再询问 | journal+issues 记录 | 同上 |
| T1 | 方舟计费语义 | 控制台路径+字段清单；截图或口述 | 不停调度 |

其余全自动执行。

## 首见面文档同步（V18 闭包）

P2 落地时：README（/admin/usage 与成本口径、告警摘要 A1–A6、cost-report 与 **cost-audit** 命令及 runbook——cost-audit 含用途、正常输出、非零退出含义，V26）、`docs/CLAUDE.md` 索引描述、`docs/operations/services.md`（cost-report 行 + install.sh 五→六服务表述）、`docs/operations/monitoring-alerting.md`（A5/A6/D3 段 + **成本排障段收录 cost-audit**）、`docs/architecture.md`（定价派生层数据流，cost-audit 只留数据流位置）、`.env.example`（增 `AI_RADAR_USD_CNY`、删旧 pricing 段）、CHANGELOG。清扫边界见 P2 L2 verify（历史材料显式允许命中）。P3 落地时（V31，明确交付物非条件式）：`~/research/ai-assistant/agents/summary-agent/README.md`（已核实其正在描述 metadata/usage 契约——usage 字段与 prompt 结构段同步）+ 上游 `~/research/ai-assistant/README.md` 指向它的链接检查；ai-radar `docs/operations/ai-assistant-integration.md` 的 prompt/usage 契约段同步。
