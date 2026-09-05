# ADR 20260904-51d2: A4 只读完整 fetch 轮的信号，并对账户层失败（401/402）升为 page

- Status: accepted
- Date: 2026-09-04
- Decision review: Codex read-only（SESSION 01a06c9d…），首轮 4 应修 + 1 无法判断 + 1 提示，三轮复核后放行（v5）；修复轮预算触发一次，用户裁「继续」。
- Related: [ADR-008](./008-alert-severity-lifecycles.md)（severity 生命周期，不改）、[ADR-009](./009-alert-notification-ledger.md)（账本，不改）、docs/issues/alerting.md ISSUE-A01 与「X 源 402」「出网 preflight」两条。

## Context

2026-09-03T21:17Z–09-04T06:17Z X API 109/109 源每轮 402 Payment Required，A4「文章摄取骤降」15 次判 firing（失败率 68–69%）却一次未发：`metrics.latest_fetch` 取「最近非 skip 轮」，fetch 已 START 但尚无汇总行的轮被读成 attempted=0 → 0%，每 6 分钟把 notice 去抖（30 min）重置。即使发出，notice 文案也不区分账户层失败与网络失败，读者拿不到「该做什么」。

## Decision

在既有 A4 规则内做两处改动，不新增规则：
(a) **信号源**：`admin/metrics.py` 的 `latest_fetch` 改取「最近一个 fetch 段已完成（解析到 `=== attempted=… failed=…` 汇总行）的 pipeline 轮」并附 `completed_at`；未完成/被中断/preflight FAIL 的轮不覆盖读数。**新鲜度**：`completed_at` 距今超过 `a4.fetch_stale_minutes`（默认 90 = 3 个完整轮周期）或根本不存在时，A4 的 fetch 维度进入「未评估（最近完整 fetch 已过期 N 分钟 / 无完整 fetch）」可观察态——detail 明写、不称健康、不进 firing/ok 二值；items-floor 维度照常评估。A4 的 `fetch_failed_ratio` 因此不再在 68%↔0% 之间每 6 分钟抖动。
(b) **账户层分桶**：metrics 解析 `FAIL <source> <error>` 行时从 error 文本抽 HTTP 状态码（`Client error '(\d{3})`），产出 `failed_by_status={code: n}`；A4 新增判据：**账户层状态（401 凭证被拒 / 402 付费层）的失败数占 attempted 的比例 > 既有 `fetch_failed_ratio`（0.4）** 时，severity 升为 **page**（不引入新阈值；403 不纳入——本项目唯一 403 实例来自公开 GitHub feed，非账户层），detail 写明「N/M 源返回 402 Payment Required（付费层/额度）」或「…401（凭证被拒）」，action 按状态码 × 来源组分文：来源组由 source_id 前缀聚合（`x_*` → 「X API」，其余按前缀原样）；402→「为 <来源组> 的 API 账户充值/恢复付费层后重跑 fetch」；401→X API 时「更换/确认 `X_BEARER_TOKEN`」，其它来源组「按 `data/sources.toml` 中该来源的 `required_env` 检查对应运行环境变量」——不把 X 的处置写给非 X 来源。dedup 走既有 A4 page 生命周期（ADR-008）。**resolve 证据（滞回）**：账户层 page 只在**日志里最近两个 `completed_at` 互不相同的完整 fetch 轮**都满足 `account_failed/attempted ≤ fetch_failed_ratio`（复用既有 0.4，不造新数）时 resolve——两个不同轮防单轮假恢复（ISSUE-A01），阈值不取 0 防少量残余失败让 page 永不关闭；同一 `completed_at` 被多次评估只算一轮；判定无状态（每次从日志重读最近两个完整轮），不写状态记忆。未满足即维持 firing、不发 resolved。

## Rejected alternatives

- **只调 A4 notice 去抖（30→0 min）**：抖动源仍在，会在每个未完成轮误报「恢复」（ISSUE-A01 同形），且 fetch-only 的其它偶发失败会刷屏。
- **新增独立规则 A8「账户层失败」**：与 A4 是同一失败面（fetch 失败），违反 alerting 原则 5「一次事故一条通知」；且 A8 也会继承同一抖动的信号源。
- **在 fetcher 里直接 `im-notify --alert`（in-service emit）**：绕开 A 系的 severity 生命周期/账本/去重（ADR-008/009/021），而 A4 本就覆盖此面，只是信号坏了。
- **按 `x_failure_reason`（fetcher 已有 401 分类）而非状态码分桶**：只覆盖 X 适配器，402 未分类；状态码分桶对所有 HTTP 源通用。
- **账户层占失败 ≥50% 这一新边界**（首版）：无负样本可验证 0.5 的区分力（评审判据 3b 无法判断）→ 改为占 attempted 的比例复用既有 0.4 阈值。
- **把 403 纳入账户层**：当天唯一 403 来自公开 feed，无账户语义证据 → 不纳入。
- **过期时沿用最后完整轮读数**：会把假 0% 换成陈旧健康值（评审判据 4）→ 改为「未评估」可观察态。

## Evidence

- 断流窗口（09-03T21:17Z→09-04T06:17Z）`data/alert-events.jsonl`（键 `ts`）内**无 A4 事件**（有 A5/A7 的 page，均与 402 无关：A5 是微信解读停滞，A7 报的是长期静默源）；`logs/alert-check.log` 同一 UTC 窗口 A4 firing 15 次（68–69%）/ok 73 次（0–3%），逐次交替、最大连续 firing 3 次（评估间隔 6 min）< notice 去抖 30 min；`thresholds.py` a4 = `{fetch_failed_ratio:0.4, debounce notice:30, page:0}`。
- 抖动机制：`metrics._load_pipeline_runs` 把「fetch START 后尚无 `=== attempted` 汇总」的轮也视为非 skip 的最新轮 → `latest_fetch` 默认 attempted=0 → 0%。实例：05:47:48 评估读的是 `pipeline-20260904-054500.log`，其汇总 05:56:08 才写入（评估时尚无）；完整轮每 30 min 一次（attempted=163、FAIL x_ 109）。
- 若只修 (a)：断流期 A4 会以 notice 稳定 firing ≥30 min → 会发；但 notice 通道文案不会说明「账户层」，读者仍要读日志。若只修 (b)：page 的去抖为 0，但读数仍会被 0% 覆盖而每 6 分钟 resolve/fire 抖动（ADR-008 生命周期会反复 fire）。两者都要。
- 区分力：修后 (a) 用 09-04 05:30–08:55 的真实日志回放，A4 应连续 firing；(b) 用同一日志回放应给出 `402: 109/111`→page。这两个回放是本单元的验收测试。

## Scope and unverified

- 只改 `admin/metrics.py` 的 latest_fetch 选取与 FAIL 行解析、`admin/alerts.py` 的 A4 判据/文案/severity；`thresholds.py` a4 段只加 `fetch_stale_minutes: 90` 与 `account_status_codes: [401, 402]`，**不加新比例阈值**（page 入口与 resolve 都复用 `fetch_failed_ratio`）；不改 fetcher、不改其它规则、不改投递与账本。
- **「完整轮」与 `completed_at` 的权威定义**：一轮的 fetch 段同时解析到汇总行 `=== attempted=…` **和其后的** `[ts] === fetch OK|FAIL ===` 行才算完整；`completed_at` 取该 OK/FAIL 行的时间戳（汇总行本身无时间戳）。只有汇总、尚无 OK/FAIL 行的轮视为未完成。
- 成立范围：pipeline 日志格式不变（`FAIL <src> <error>`、`=== attempted=…`）；账户层状态集 {401, 402}——对 api.x.com 的 402 有本次实测，401 依 HTTP 语义与 fetcher 既有 `authentication_rejected` 分类，两者之外的状态码不作账户层结论；page 文案只断言状态码与来源前缀，不断言具体账户动作之外的原因。
- 未覆盖：非 HTTP 的账户层失败（如 SDK 抛的配额异常无状态码）——保持原 notice 路径；403 保持普通失败。

- 评审判据 7：错了的发现路径——A4 误判仍由 A2（120 min）兜底；新增「未评估」态本身是可观察的错误信号。**改回来付什么**：代码层一个 commit 可整体回退（改动只在 metrics/alerts/thresholds 三文件；resolve 判定无状态、不加 alert-state 字段，故无状态迁移）；已发送的 page 不可撤回——误 page 的代价是运维一次核查（action 全是人工动作，本改动不自动执行任何账户操作）；ledger 会留下误 fire/resolve 记录，按 ADR-009 只追加、不需清理。
- 用真实日志回放验证 (a)(b) 尚未跑（验收测试将做）。
- 401 的 page 路径未有真实事故样本（只有 HTTP 语义与 fetcher 既有分类支撑）；`fetch_stale_minutes=90` 的取值依据是 3 个完整轮周期，未在真实停跑上回放；401 的非 X 来源处置文案按 `sources.toml` 的 `required_env` 指向对应环境变量（评审提示）。
- 未完成轮的成因（18:15–21:15 连续 6 轮无 fetch 汇总）未查，属另一问题。

## Prior decisions and readings

- ADR-008 alert-severity-lifecycles：A4 按 severity 分生命周期，page 不被 notice 节流 → (b) 的 page 走独立生命周期，相容。
- ADR-009 alert-notification-ledger：投递账本 → 不改。
- ADR-021 audit-alert-delivery-and-suppression → 不改投递/抑制。
- docs/issues/alerting.md ISSUE-A01：A4 在故障持续中发「已恢复」——与本抖动同源（读数被覆盖），(a) 同时修它的一半（未完成轮不再覆盖），但 A01 另一半（已恢复文案）不在本单元。
- 无命中：`latest_fetch` 选取规则、状态码分桶——此前无决策。

- memory：2026-06 A4 曾因 nitter 抖动加「去抖动」（1952c64）——那次是去抖阈值，不是信号源；本次实测说明去抖只在信号稳定时有效。
- 本次断流：X 恢复后 14:15 轮 109/109 OK，A4 读数回到 0–3%（真实健康），说明 (a) 不会造成误报持续。

## Implementation record and known boundaries (2026-09-05)

- 落地：`src/airadar/admin/{metrics,alerts,thresholds}.py`、`web/templates/admin.html`、`scripts/verify_admin_metrics.py`；review-gate 高档 + `/custom:review-alerting` 九原则两轮。评审后在 (a)/(b) 之内补的实现细节：完整轮 `attempted=0` 视同未评估（0/0 不是 0%，也不算恢复轮）；`a4.account_resolve_rounds=2` 是 metrics 暴露轮数与 resolve 判定的单一来源；消息按 5 组 / 3 组截断；只有 1 个可用完整轮时写「恢复证据不足」不写「已回落」；fetch 未评估且 items 跌破 floor 时处置先指向 pipeline 存活/preflight；resolved 消息带证据指针；runbook 新增「A4 账户层失败（401/402）的处置与恢复判定」小节。
- **已知边界（用户 2026-09-05 裁决保留，另开决策包）**：(b) 的分母是全站 attempted、且 401+402 求和——小份额账户 100% 失效不会 page，两个轻微失效可能叠加成 page；「来源组」按 source_id 前缀聚合只用于文案，**前缀不是账户身份**（`google_*` 是 5 个互不相关的公开 feed）。按账户分母的方案经决策评审判 6 blocker（`plans/20260904-a4-account-layer-alert/decision-p1-per-group.md`），正确修法需要账户身份契约、按账户×状态码判定与账户级 lifecycle，见 `docs/issues/alerting.md`「A4 账户层 page 的分母与"来源组"身份」。
- 同轮记 issue 未改：A4/A7 同因双 page（P5）、共享 lifecycle 掩盖账户恢复（P7）、firing 时 `evaluation_state` 塌缩（P9-F2）、无状态码失败无覆盖率提示（P9-F4）。
- 第 2 轮评审后的消息层修正（不改判据）：非 X 前缀组在消息里明写「来源组 <前缀>（按 slug 前缀聚合，非账户身份）」，处置改为核对来源配置与响应、有 `required_env` 才查凭证，不再断言「为 <前缀> 的 API 账户充值」；resolved 的证据指针改为中性入口并按账户层 / 出网分流；push 正文去掉恢复轮数与日志 marker 细节；metrics 对 `completed_at` 晚于当前 5 分钟以上的完整轮标 `stale_reason=future_timestamp`（不再钳成"刚完成"），`collect_alert_signals` 直接消费 metrics 的 `stale`。第 3 轮后的收口修正：多个前缀组同一状态码只写一句处置；resolved 指针明写「无需立即处置」；恢复确认不采信比最新可用轮早超过 `fetch_stale_minutes` 的旧轮。review-alerting 在第 3 轮增量收敛到 3 条小项后由用户裁决收口（不再跑第 4 轮），gate 复核覆盖这批修正。
