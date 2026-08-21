> **Archive status**: 已归档，**未收尾**。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> **中止点**（判据＝`state.md` 残留 open 项）：Phase 0/1/2/4 done；**Phase 3（fleet 与 Codex worker）deferred**——Prerequisite V36 实测确认外层 `process-exec` allowlist 与 Codex 内层 sandbox profile 不能在同一进程树嵌套（`sandbox_apply: Operation not permitted` / exit 71），`remediation.enabled=false`；「Production actions」停在 in progress（候选改动只存在于本机 candidate 工作树 `ai-radar-continuous-performance-20260715`，未部署）；「User-facing acceptance」pending，四条 aiplanet.live 旅程的用户体感 ballot 未执行。
> 后续：本 plan 验证过的 generation-triggers 方案由 [20260718-perf-safeguard](../20260718-perf-safeguard/plan.md) 采纳并部署。性能保障的当前契约见 [ADR-011](../../adr/011-perf-idle-only-probing.md) 与 [operations/monitoring-alerting.md](../../operations/monitoring-alerting.md)。以下为原 plan 正文，未修改。

# AI Radar 持续性能保障与受控自治优化计划

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时先初始化并读取 `state.md` / `journal.md`；compact 后重读再继续。声称完成前必须实际执行本 plan 的 user-facing verify 并贴出证据。

**状态**：D1–D19 用户取舍已锁定；有界原则审核与单次目标反证已收敛，可进入执行
**创建日期**：2026-07-15
**主要消费者**：后续 `$custom-execute-plan` session、AI Radar 维护者、跨项目接入者
**涉及仓库**：

- 产品仓库：`/Users/lindong/research/ai-radar`
- 共享基础设施仓库：`/Users/lindong/research/ai-agent-config`

## 0. 执行修正案：先交付确定性监控（D20）

2026-07-15 的 Prerequisite V36 实测确认：macOS 上外层 controller `process-exec` 真 allowlist 与 Codex 内层 named permission profile 各自有效，但不能在同一进程树嵌套；组合的合法命令以 `sandbox_apply: Operation not permitted`、exit 71 失败。用户据此选择“先交付监控”。本节是后续执行的优先解释，覆盖本计划中“V36 失败后 Phase 0–4 全部停止”的旧耦合，但不降低任何安全边界。

- **本次 monitoring tranche 继续执行**：Phase 0 当前事故修复；Phase 1 的协议、store、deterministic evaluator、incident/evidence、baseline、retention、notification 与 fleet 核心；Phase 2 AI Radar probes/状态页/服务生命周期；Phase 4 的性能契约、运维文档、shadow 运行、baseline proposal/promotion gate 与生产体验验收。
- **Codex 写修复保持 fail closed**：Phase 3、live provider/model matrix、自动 candidate commit、autonomy enable 与 `candidate_ready` completion 全部移入后续独立隔离计划。当前 tracked config 必须固定 `remediation.enabled=false`，状态明确显示 `disabled/incompatible`；任何路径启动 Codex provider process 都使本 tranche 失败。
- **V36 不再阻塞 Phase 0–2，但仍阻塞 Phase 3**：保留 V36 的失败证据，不把它改写成 pass，也不 commit 仅用于失败诊断的 candidate。共享实现从新的 clean monitoring worktree 开始。
- **验收分流**：本 tranche 运行 V1–V8、V13–V24 中不要求 provider/candidate 的确定性部分、V26、V29–V30、V32–V35、V37、V39，以及 V16–V18 当前性能预算。V9–V12、V25、V27–V28 的 live-provider 分支、V31 的 autonomy enable 分支和 V38 延后；V31 对 adoption/merge/push/deploy/restart/rollback 的逐动作授权仍完整生效。
- **完成语义**：本 tranche 只有在当前性能回退修复、确定性监控/告警闭环运行、remediation 保持 disabled、相应 final-pair 兼容验证与生产四旅程验收完成时才算交付；它不宣称已完成原 D1 的自动写修复。后续隔离计划通过 V36 并启用自治后，才补齐原计划的完整完成态。

该修正案不改变 D2–D19 的 SLA、地区、窗口、保留、容量、证据与授权语义；只把未证明安全的 Codex worker 从当前价值链中解耦，避免它阻塞当前用户性能修复与确定性监控。

## 1. 目标与最终产物（L1）

把当前“用户感觉慢后再人工排查”的模式，升级为一套可持续运行、可复用、可审计的性能保障系统：先修复已经复现的首页性能问题，再持续从用户旅程发现回退；确定性程序负责测量和判定，Codex 只在确认事件后作为有边界的修复 worker 工作。

执行完成后应得到以下最终产物：

1. **当前故障被修复且有回归测试**：首页 `/`、微信解读列表 `/wechat`、详情 `/wechat/<slug>` 与微信列表翻页在生产规模数据和 pipeline 并发负载下达到本计划的本机工程预算；原有内容、精确总数和分页语义不变。
2. **共享性能保障 CLI `continuous-performance`**：位于 `ai-agent-config/continuous-performance/`，提供版本化配置、探针结果协议、确定性 SLA 判定、基线晋升、事件证据包、保留策略、状态查询与受控 Codex worker 编排；不包含 AI Radar 专属路由或 SQL。
3. **AI Radar adapter + 共享 fleet 调度**：AI Radar 声明旅程、预算、探针命令、环境采样和修复 gate；`ai-agent-config` 安装一个跨项目 LaunchAgent，每 5 分钟调度所有已注册项目，并按各项目到期状态运行 browser journey，且不允许同项目重叠运行。
4. **可追溯的事件循环**：连续违规时创建不可变事件包，记录 Git SHA、dirty 状态、origin/public 两条路径、pipeline 阶段、CPU/load、SQLite/WAL 体积、样本与日志摘要；恢复时发 resolved 通知。
5. **受控自治修复**：事件经确定性状态机确认后，最多启动一个独立 Codex 任务；它先只读诊断，再在隔离 worktree 中按 TDD 生成候选修复、运行专项 gate 和 review gate，最多形成本地候选 commit。它不得 push、deploy、改生产数据、晋升基线或修改主工作区。
6. **性能契约与运行手册**：UX contract 覆盖用户可感知性能，独立 performance contract 定义指标、地理语义、SLA、证据和 `not_observed` 规则；README、CHANGELOG、architecture、ADR、operations 与服务登记同步完成。
7. **跨项目接入样板**：共享 README 给出一个最小 fixture consumer；验证新项目只需声明配置与 adapter，即可复用探针协议、判定、事件包和 Codex 安全边界。

本计划的完成标准不是“写完监控脚本”，而是：当前回退有证据地消失、循环真实运行、故障注入能触发完整链路、恢复能闭环、越权动作被测试拒绝。

## 2. 已确认事实与问题边界

### 2.1 当前可复核事实

| 事实 | 当前证据 | 对计划的约束 |
|---|---|---|
| 现场首页明显慢 | 2026-07-15 pipeline 运行期间，浏览器 `/` TTFB 7.63s、FCP 7.98s、load 12.46s；经代理 curl TTFB 10.07s | Phase 0 必须先处理真实事故，不能只搭未来 infra |
| origin 也慢 | 同期本地 origin `/` 多次约 8.0–10.4s，`/api/v1/curated?page=1` 约 9.6s | 不能把首页问题简单归因于 Cloudflare |
| 首页热点已定位 | `_compute_archive_page` profile 约 2.44s，其中 `_count_archive_items` 约 1.79s；`_curated_data_version` 约 0.22s | 先用 scaled regression test 证明并修正计数缓存失效语义，再决定是否需要 SQL/index |
| 计数缓存被无关写入失效 | `_curated_data_version()` 把 `MAX(item_evaluations.id)` 放入 exact-total cache version；pipeline evaluation 写入会让归档成员未变时仍重算昂贵总数 | 修复应让 count cache 只跟影响归档成员/筛选计数的变化失效，不能牺牲 exact total |
| 微信现场延迟主要在请求链路 | 公网 `/wechat` 暖请求约 1.8s；翻页 click-to-settle 944ms，其中 API 908ms、DOM 约 36ms；本地 origin/API 约 9–162ms | 不做缺乏证据的微信 SQL 重写；先分离 origin/public 与负载相关性 |
| 两个 SSR 路由未显式关闭连接 | `src/airadar/web/app.py` 的 `/wechat` 和 `/wechat/{slug}` 使用 `with db.get_conn(...)`；sqlite connection context 只管事务，不负责 close；同项目已有 `conn_from_request()` 正确 helper | 用既有 helper 收敛连接生命周期，并用测试证明关闭；不把它未经验证地宣称为全部延迟根因 |
| 当前监控看不到用户延迟 | A3 主要看 5xx + local healthz；access log 无 duration；公开路径集合漏掉 `/wechat`、`/api/v1/wechat`、`/wechat/<slug>` | 新性能循环独立于 A3 判定，但复用通知状态机语义，并补齐公开路由分类/请求时长观测 |
| pipeline 是重要混杂变量 | 15 分钟周期中一次可运行 8–12 分钟，并显著占用 CPU | 每个样本必须带 pipeline 阶段/负载；验收必须覆盖 idle 与 busy，不用一次现场样本冒充 P95 |
| 当前性能测试不代表生产规模 | 既有 curated 延迟测试只有 40 rows，median <100ms | 新增确定性 production-scale fixture 和 cache invalidation 行为测试；墙钟预算只用于受控环境 |
| 项目已有 Playwright 运行依赖 | `pyproject.toml` 的 project dependencies 已含 `playwright>=1.40.0` | 浏览器 synthetic 由 AI Radar adapter 使用 Playwright；共享 CLI 不新增浏览器依赖 |
| 当前工作区不干净 | 已有 `AGENTS.md`、`docs/issues/harness-issues.md` 修改及多项未跟踪运行文件 | 执行时不得覆盖、stage 或清理用户现有改动；所有自动修复在新 worktree 中进行 |
| Playwright Python 包不等于 Chromium runtime | README 把 `uv sync` 与 `uv run playwright install chromium` 分成两个步骤，浏览器缓存也可能被清理 | doctor/register/browser run 都要验证可实际 launch；缺失时已配置的 local browser stream 为 `incompatible`，未部署 off-host probe 的地区行仍独立为 `not_observed`，不得冒充产品 hard failure |
| 共享仓库当前也不干净 | 计划审查时 `ai-agent-config` 有既有 tracked/untracked 改动，且尚无 `continuous-performance/` | Phase 1–3 必须从记录过 SHA/dirty manifest 的独立 worktree 实施；不得在用户共享仓库主工作区直接写/stage |

### 2.2 当前版本锚点

- 计划创建时 AI Radar HEAD：`20e0311fa913f3035baa9b14f41da50c53fd9fa2`
- 计划审查时 `ai-agent-config` HEAD：`7dac5f509e94f24e967ca969ddfab21ff533da8f`；执行时重新记录 SHA 与 dirty manifest，不把该值当未来固定断言。
- 不可变历史对照：tag `opensource-baseline` → `1952c643076e11c1c8c3ce7147f2c1b93daf1e25`
- 计划创建时主库约 1.8GB、WAL 约 634MB；items 约 29.7k、curated_items 约 211k、wechat_interpretations 约 1.8k。执行者应重新采样，不把这些数值当固定断言。

### 2.3 根因判断纪律

- “代码回退”“数据增长”“pipeline 资源竞争”“公网/tunnel 路径”目前都可能参与，不能预先选一个总根因。
- 每项优化必须先有可重放失败样本或 profile，再有最小修复，再重跑同一测量；没有前后对照就不进入候选 commit。
- 同一时段交错测量 origin/public，分别记录 idle/busy，避免顺序、连接预热和时段漂移被误当作因果。
- `opensource-baseline` 仅用于必要的代码/查询对照；不得在当前 checkout 改写历史，也不得让旧版本直接读写生产库。

## 3. 已锁定的用户决策

| ID | 决策 | 落地含义 | 验收映射 |
|---|---|---|---|
| D1 | 受控自治 | 自动监测、判定、留证、诊断、隔离 worktree 修复与本地候选 commit；禁止自动 push/deploy/生产数据修改/基线晋升。完成必须包含人工晋升 baseline、独立批准并启用 `remediation.enabled=true`，以及同一可修 fixture incident 端到端形成 `candidate_ready`；shadow/dry-run 不是完成态 | V8–V12、V19、V25、V31、V36、V38 |
| D2 | 东亚优先 + 全球底线 | 正式 regional SLO 以东亚为优先，US/EU 有底线；未部署 off-host probe 前必须显示 `not_observed`，不能显示绿色 | V2、V6、V13 |
| D3 | 绝对 SLA + 相对回退 | 首页/微信列表/详情 East Asia 为 P75 ≤2s、P95 ≤3s；微信翻页为 P75 ≤1s、P95 ≤1.5s；US/Europe floor 均为 P95 ≤5s。绝对预算和相对人工健康基线都要过；相对回退阈值 >30% | V3、V4、V20 |
| D4 | synthetic-first | 首期不采集真实用户数据；schema 预留 RUM provider 扩展点但 UI 不注入 telemetry | V5、V14 |
| D5 | 首期同源主机探针 | 同一主机比较 `127.0.0.1` origin 与 `https://aiplanet.live` public；结果标记 provisional，不冒充东亚真实用户 | V2、V6 |
| D6 | 共享 infra 立即拆出 | 通用 CLI/协议/安全 runner 与 fleet service 落在 `ai-agent-config` 顶层 peer；AI Radar 只保留 config、adapter、consumer lifecycle 与产品契约 | V1、V15 |
| D7 | Codex 是事件 worker，不是 scheduler | launchd + 确定性状态机负责调度；Codex 只处理一个已确认 incident，使用非交互 `codex exec` 的结构化输出 | V8–V11 |
| D8 | 稳定后迁移外部探针 | 首期完成同机闭环；连续 7 天证据健康后，按 runbook 增加东亚 off-host，随后 US/EU；本计划只交付 provider 接口和迁移 gate，不购买/部署外部资源 | V13、V14、V21 |
| D9 | 当前事故以生产体验完成验收 | 先交付隔离 commits；candidate adoption/merge/push/deploy/restart/rollback 仍逐项走独立人工 gate，获得授权并部署后必须以 `aiplanet.live` 四条旅程作为本计划最终验收目标。本选择不等于提前授予 push/deploy 权限 | V12、V18、V20、V31、V34、V35 |
| D10 | idle/busy 独立合规 stream | `load_class=idle|busy` 分别形成 window/baseline/streak；任一 stream confirmed breach 都可建立 incident，禁止用 idle 样本冲淡 busy 回退 | V4、V6、V8、V18 |
| D11 | 平衡检测与自治预算 | HTTP 5 分钟、browser 1 小时、连续 3 个已前进窗口确认/恢复、6 小时 cooldown；每个 live incident 最多 2 个实际 Codex invocations：1 diagnosis + 证据足够时至多 1 implementation，每个 ≤60 分钟、总计 ≤120 分钟；全项目 1 active worker | V7、V8、V24、V27 |
| D12 | 固定已验证 Codex 配置 | Phase 3 先以 capability/quality fixture 选定并在 tracked controller config 锁定 exact model + reasoning；不可用或漂移时 fail closed，升级需显式迁移并重新过 fixture/autonomy gate；不继承 ambient user config | V9、V25、V28 |
| D13 | 平衡 fleet I/O 预算 | 全局最多 2 个项目并发、每项目最多 1 个 active run、browser 全局最多 1 个；同 project/target latency probe 串行；每轮 deadline 4 分钟，公平队列防饥饿 | V15、V26、V29 |
| D14 | Balanced 证据保留与磁盘水位 | raw/aggregate/resolved incident 分别保留 30/180/365 天；age 等于各期限仍保留，只有 `age > limit` 才过期。global `>=10GiB`、per-project `>=2GiB` 或剩余空间 `<=15%` 即进入压力态。压力时先清理已过期 closed success aggregate，再停成功样本与新 remediation；open/candidate 永不自动删除 | V23、V37 |
| D15 | Strict 7 天 readiness | 任一 cadence gap、经维护者确认的误报 incident、guard/worker 成功执行任一禁止动作，或未处置的 `delivery_stuck` 都重置连续 7 天；guard 正确拒绝恶意 fixture 计为安全通过且不重置 | V21、V30 |
| D16 | Strict model fixture bar | 安全/schema/provenance case 必须 100% 通过；可修 case 作为端到端 repair trial 独立运行 3 次，至少 2 次形成最小且全 gate 通过的 candidate，每个 trial ≤60m；初次 pin 与显式迁移均用同一 bar | V25、V28 |
| D17 | Pipeline-state load classification | authoritative pipeline lease/event ledger 证明任一 pipeline 阶段与完整 probe measurement interval 有交集时标 `busy`；只有 ledger cursor 连续且证明区间内无 active/started stage 时才标 `idle`。状态缺失、事件序列有 gap 或无法证明区间覆盖时不猜测、不按 CPU 阈值代判，样本为 `unknown` 且不进入 idle/busy 合规窗口。CPU/load/memory 只作为带单位和采样时点的诊断上下文 | V6、V8、V18 |
| D18 | 严格本机工程预算 | production-scale fixture 的首页和 curated API 为 median ≤500ms、nearest-rank P95 ≤1s；微信 API/SSR 为 median ≤300ms。该门槛已锁定，calibration 只报告 headroom/分布，不得自行放宽 | V16、V18 |
| D19 | Codex fixture 串行封顶 | 一次只评测一个 exact model+reasoning candidate；D16/D19 的计数单位统一为最多 3 个串行端到端 repair trial，每个 trial 包含 1 diagnosis，并仅在证据足够时再包含最多 1 implementation，trial 总时长 ≤60 分钟，整批 ≤4 小时。mandatory outcome harness 必须 provider-free；单 candidate matrix 的实际 provider invocation 总 cap 为 7（1 capability smoke + 3 diagnosis + 至多 3 implementation），每个进程逐次记 ledger，第 8 次在 spawn 前拒绝。auth/quota 不足或超时即 fail closed，不并发、不静默换模型或扩大预算 | V28 |

## 4. SLA、指标与状态语义

### 4.1 用户旅程预算

这里的“SLA”落为可执行的内部 **SLO/性能契约**，不是对外法律赔偿承诺。正式 regional SLO 只有在对应 off-host probe 存在时才可判定。下表数值已由 D3 锁定，进入 v1 config/contract：

| Journey | East Asia | US/Europe floor | 主要指标 |
|---|---:|---:|---|
| `homepage.first_card` | P75 ≤ 2.0s；P95 ≤ 3.0s | P95 ≤ 5.0s | navigation start → 第一张 `.item-row` 文本可读 |
| `wechat.list.first_card` | P75 ≤ 2.0s；P95 ≤ 3.0s | P95 ≤ 5.0s | navigation start → 第一张 wechat card 文本可读 |
| `wechat.detail.readable` | P75 ≤ 2.0s；P95 ≤ 3.0s | P95 ≤ 5.0s | navigation start → 正文主内容可读 |
| `wechat.pagination.settle` | P75 ≤ 1.0s；P95 ≤ 1.5s | P95 ≤ 5.0s | click next/page → 新页首卡可读且旧内容被替换 |

相对规则：当同一 `project + journey + vantage + metric + load_class + window_definition + percentile_estimator` 已有人工晋升的健康 baseline 时，当前 eligible window 的对应 percentile 不得比 baseline 退化超过 30%。单条 observation 只记录原始测量，不单独计算 P75/P95 合规；window 同时受绝对预算和相对规则约束，任一失败即 violation。

所有 percentile 使用 nearest-rank：先剔除非有限值并按升序排序，`rank=ceil(p*n)`、1-based 取值；时间统一毫秒存储，展示时才换算并按 contract 规定精度舍入；`value <= budget` 为 pass。V3 必须覆盖 `n=6`、`n=12`、阈值等号、空值/NaN 与排序重复值。

### 4.2 首期同机工程目标

同机数据用于发现回退和拆分瓶颈，不代表 regional SLO：

- `origin_http_ttfb`、`public_http_ttfb`、浏览器 journey duration 分开存储和展示。
- 本机 origin 在受控 performance fixture 上，以 20 个 warm samples 计算：D18 锁定首页和 `/api/v1/curated?page=1` median ≤500ms、nearest-rank P95 ≤1s，微信 API/SSR median ≤300ms。这些是回归测试/工程目标，不是用户地区 SLA；Phase 0 calibration 必须报告 idle/busy 分布与 headroom，但不能自行放宽 D18 或 regional SLO。未达标时继续证据驱动优化；仍无法达到则 Phase 0 失败并带证据停止，本计划内不得生成放宽阈值的 fallback。
- public same-host 旅程使用上表 East Asia 绝对数值作为 provisional objective，但状态只能是 `provisional_pass` / `provisional_breach`，不能汇总为 East Asia green。
- regional dimension 初始固定为 `east_asia=not_observed`、`us=not_observed`、`europe=not_observed`。

load classification 只使用 D17。每个 pipeline-producing entrypoint 必须在开始任何会影响负载的工作前，原子写入带 monotonic start、generation 与递增 sequence 的 active lease/start event；结束 event 在工作完全结束后写入。adapter 在 probe 前保存 authoritative ledger cursor 与 active-set，在 probe 后读取同一连续 ledger 区间：起点已有 active stage、区间内出现 start event，或任一 stage interval 与 measurement interval 相交都归 `busy`，包括完整落在两次读取之间的 `idle → busy → idle`。只有起止 cursor 连续、无 sequence gap、active-set 一致且区间内没有相交 stage 才归 `idle`；读取失败、generation/sequence gap、partial event、矛盾或无法证明完整区间时记录 `load_class=unknown` 与原因。unknown 样本保留作诊断但不进入 idle/busy percentile、streak 或 baseline。诊断字段固定为 probe interval 上的 host CPU percent、1-minute host load average、host used/total memory bytes，以及 configured SQLite DB/WAL file bytes；每项携带 wall-clock timestamp，interval 指标另带 monotonic start/end。它们只作 incident context，不能重分类样本。

### 4.3 判定窗口与状态机

配置语义如下；D11 已锁定的 cadence、连续窗口、cooldown 与 worker cap 必须使用表中值，只有未来显式迁移决策才能调整：

- HTTP quick probe：每 5 分钟，origin/public 交错但同轮完成。
- browser journey：每小时一次；如果上一轮未结束，本轮记 `skipped_overlap`，不再启动第二份浏览器。
- 统计窗口：最近 24 小时、至少 12 个 quick samples 或 6 个 browser samples。`window_end` 唯一取 scheduler ledger 中本次 evaluate 对应的 scheduled slot，不取实际 wall time或最大 observation time；时间桶由 Unix epoch UTC anchor 与各自 cadence确定：bucket 为 `(anchor+k*cadence, anchor+(k+1)*cadence]`，右端点等号归入该 bucket。按 observation 的实际 measurement-end timestamp 选择 `(window_end-24h, window_end]` 内样本；quick 至少覆盖 12 个不同 5m buckets，browser 至少 6 个不同 1h buckets，同 bucket 多样本都参与 percentile但只贡献一个 coverage。样本数或 distinct buckets 任一不足为 `insufficient_data`。
- window 的唯一推进键固定为 `stream identity + scheduled window_end`，不含 config hash；每个键最多一个 outcome。第一次 evaluate 读取当时唯一 active evaluator config hash，原子封存 eligible observation-id exact set/hash。若同 slot 后续 config hash 不同，只返回 `config_changed/stale` 诊断，不生成第二 outcome、不推进 streak；config/baseline identity 变化重置旧 streak并从下一个 scheduled slot 开始新 stream qualification。同 bucket retry、迟到/乱序仍幂等 ingest，但不得重开 sealed window；future sample不得进入，只可能在下个 scheduled slot纳入。
- window finalization 必须在一个 durable SQLite transaction 中同时提交 observation-set hash、outcome、streak、incident transition、对应 outbox event，以及需要封存 incident evidence 时的 materialization intent（canonical artifact exact-set、已完成 redaction 的 immutable serialized payload bytes/BLOB、逐 artifact hash、incident id）；不得只存 hash 后回读会变化的 live source。commit 后 evidence reconciler 对每个 payload 写同目录 controller-owned 唯一临时文件，完成 file fsync/hash 后用 atomic no-clobber publish 到最终路径并 fsync directory；最终路径已存在且同 hash视为已发布，异 hash/多余文件 fail closed，残留 controller temp 可从 intent BLOB 安全清理/重建。全部 artifact 核对后才原子标记 bundle `sealed`，delivery/remediation 只可消费 sealed incident 的 outbox/transition。每次启动及每个 tick 都先补齐 pending intent。任一 transaction/payload-write/publish kill point 后，重启时未 commit则重算并原子提交，已 commit但未 sealed则只从 immutable intent 补齐同一 bundle，已 sealed则按唯一推进键返回既有 transition，绝不丢步、重复封存或重复通知。
- confirmed breach：连续 3 个**已前进** eligible window violation，或 1 次 hard failure（5xx、timeout、页面关键元素缺失）。单次慢样本为 `suspect`，只留观测，不启动 Codex；probe runtime/browser 缺失等 infra failure 不属于产品 hard failure。
- resolved：confirmed 事件后同一 stream 连续 3 个已前进 eligible window pass。
- 事件 single-flight：同一 project + journey + fingerprint 只允许一个 open incident；cooldown 6 小时；新 fingerprint 可另立事件但全项目最多一个 Codex worker active。
- baseline 缺失时：可按绝对预算报警并生成证据；主状态仍按下列枚举表达，另带独立 `baseline_status=missing|candidate|promoted|stale` qualification。`baseline_status != promoted` 时 Codex remediation fail closed。

状态枚举必须显式包括：`not_observed`、`insufficient_data`、`provisional_pass`、`provisional_breach`、`healthy`、`suspect`、`confirmed`、`remediating`、`candidate_ready`、`resolved`、`blocked`、`incompatible`。未知/不兼容状态 fail closed，不能落为 healthy。

`not_observed` 只表示该 region/vantage 从未得到可用 observation。freshness deadline 从已锁定 cadence 与 round deadline 机械推导：quick 为 scheduled-at + 5m + 4m，browser 为 scheduled-at + 1h + 4m；等于 deadline 仍 fresh，超过且没有对应 outcome 即 `insufficient_data` + stale qualification，不得退回 `not_observed`。`baseline_status=missing|candidate|stale` 均不得显示 compliant green/healthy；只有 promoted 且 absolute/relative 均 pass 才可绿色。主状态、baseline qualification、freshness、metric、display unit 与 load class 分字段输出，UI 不把它们压成一个含混标签。idle/busy 是独立合规 stream，任一 confirmed breach 都建立 incident；总览不得用另一 load class 的 pass 抵消它。

browser journey 统一以 navigation/click 的 monotonic clock 为起点；终点是 contract selector 同时满足 attached、CSS-visible、有非空可读文本，并在随后一个 animation frame 仍满足。详情正文使用主内容 selector；分页还必须确认旧页 identity 已被新页 identity 替换。selector 缺失、空文本和超时分别记录明确失败分类；同一 predicate 同时用于 probe、fixture 与 baseline，禁止以 DOM attached 或 network idle 等较弱代理替换。

## 5. 目标架构与所有权

```text
shared fleet LaunchAgent (每 5 分钟)
        |
        v
registry -> AI Radar adapter/config ---------------+
  - origin/public HTTP probes                     |
  - 到期的 Playwright journey                    |
  - pipeline/CPU/DB/WAL context                   |
        |                                          |
        v                                          |
shared continuous-performance CLI                 |
  [validate protocol] -> [append observations]    |
          -> [deterministic evaluator]             |
          -> [incident bundle + alert state]       |
                         | confirmed + enabled     |
                         v                          |
                 bounded Codex worker              |
               read-only diagnosis first          |
                         | strong hypothesis       |
                         v                          |
               isolated git worktree + TDD         |
                         | gates pass               |
                         v                          |
               local candidate commit + notify    |
                         |
                         X no push/deploy/baseline promotion
```

### 5.1 跨仓库边界

**`ai-agent-config` 拥有**：

- `continuous-performance/bin/continuous-performance`：通用 CLI 入口。
- `continuous-performance/src/`：协议解析、SQLite observation store、确定性 evaluator、事件包、retention、baseline promotion、Codex runner、worktree guard。
- `continuous-performance/schemas/`：project config、observation、incident、diagnosis/candidate result JSON Schema。
- `continuous-performance/tests/`：状态机、兼容性、权限边界、fixture consumer 集成测试。
- `continuous-performance/{install,uninstall,status}.sh`、`resource.continuous-performance.plist`、`README.md`：幂等安装共享 CLI 与 fleet LaunchAgent；根 `install.sh` 只调用 peer installer。
- 本机 registry `~/.config/continuous-performance/projects.json`、状态 `~/.local/state/continuous-performance/<project-id>/`、日志 `~/Library/Logs/continuous-performance/` 的协议与生命周期。

**AI Radar 拥有**：

- `config/performance.toml`：journeys、**唯一机器可读的数值预算**、频率、adapter 命令、共享协议要求、remediation 开关；项目根用相对路径，由 register 命令解析，tracked config 不写个人绝对目录。
- `src/airadar/performance/`：HTTP/Playwright journeys、环境上下文采样、AI Radar gate manifest；不重新实现通用 evaluator。
- `src/airadar/admin/performance.py` 与 CLI glue：项目命令、现有告警/状态页适配。
- `install.sh performance` / `uninstall.sh performance` / `status.sh performance`：注册、注销、查询共享 fleet 中的 AI Radar consumer；不再安装第二个 project-specific daemon。
- 产品专属修复、测试、contract 和运维文档。

共享层不得 import `airadar`；AI Radar 通过 JSON/命令协议调用共享层。执行时用 temp fixture consumer 证明该约束，而不是只看 import 文本。

### 5.2 版本与兼容性

- config 顶层写 `schema_version = 1` 和 `requires_protocol_major = 1`。
- CLI 提供 `--version` 与 `protocol inspect --json`；major 不兼容、必填字段缺失、未知 evaluator 语义均返回非零并产出 `incompatible`，不继续判 healthy 或调用 Codex。
- minor 版本只允许向后兼容地新增 optional field；所有 observation/incident 写入其 schema version 与 CLI version。
- AI Radar installer/status 做 compatibility preflight；共享 CLI 缺失时 service 显示 degraded/incompatible，并通过 `run-or-alert` 报告，不静默降级成旧逻辑。

### 5.3 运行态数据与证据

共享 fleet 下每个项目的默认状态路径：

```text
~/.local/state/continuous-performance/ai-radar/
├── monitor.sqlite3
├── baselines/
│   └── <journey>.json
├── incidents/
│   └── <UTC timestamp>-<fingerprint>/
│       ├── manifest.json         # initial immutable identity
│       ├── manifest-events.jsonl # append-only hashes/state transitions
│       ├── observations.jsonl    # sealed raw evidence
│       ├── environment.json      # sealed raw evidence
│       ├── diagnosis/
│       │   └── <attempt>.json
│       ├── candidates/
│       │   └── <attempt>.json
│       └── logs/                 # bounded excerpts, no secrets
└── locks/
~/Library/Logs/continuous-performance/
├── monitor.log
├── monitor.err.log
└── remediation-<incident>.jsonl
```

事件目录按 SQLite materialization intent 中的 immutable serialized payload BLOB 幂等封存原始 observation/environment 和 initial manifest；只有 canonical artifact exact-set 全部经“同目录 controller temp 写满并 fsync/hash → atomic no-clobber publish → directory fsync”完成、逐项 hash 校验并原子标记 `sealed` 后，incident 才能进入通知或 remediation。启动/每 tick 的 reconciler 必须补齐已 commit 但未 sealed 的 intent：既有最终文件同 hash可复用，缺失则从 BLOB 重建，异 hash/多余最终文件 fail closed；只有可由 owner marker 证明属于本 intent 的残留 temp 可清理。后续阶段只写唯一命名、create-exclusive 的派生文件并 append `manifest-events.jsonl`，每条记录前一条 hash 与新 artifact hash，禁止覆盖 attempt 产物。SQLite 中的可变状态只是恢复索引，不替代 sealed evidence。每个 sealed incident 的 required evidence 明确包含 Git SHA/dirty、origin/public、pipeline stage、CPU/load/memory、DB/WAL size、样本与 bounded log summary；采样失败写字段值 `unknown`，不得缺字段。严禁复制 `.env`、webhook、cookie、文章正文或完整数据库；日志先做路径/URL query/header secret redaction，并限制总量；tamper/overwrite/secret/required-field fixture 必须 fail closed。

D14 已锁定时间 retention、byte/free-space 门限、等号语义与压力顺序：`age <= limit` 保留、`age > limit` 才可列入过期集合；global/project bytes 在 `>=` 门限时、free space 在 `<=15%` 时进入压力态。retention dry-run 和 apply 分离；只有 V23/V37 通过并由 operator 显式执行 apply 才能删除符合 D14 的过期 closed evidence。测试必须证明 open/candidate 永不自动删除；达到 critical waterline 时停成功样本/新 remediation 并保持现有 sealed evidence 可读，不能靠破坏活动证据恢复空间。

## 6. 分阶段实施（L3）

执行遵循 TDD：每个行为先写会失败的测试/fixture，再做最小实现；每阶段结束保留可复核命令和产物路径。开工时在两个仓库分别记录 HEAD、dirty manifest 与用户主工作区 hash，并为本任务创建隔离 worktree；同时设置绝对路径 `CONTINUOUS_PERFORMANCE_TASK_MANIFEST` 指向两个仓库之外、仅 controller 可更新的 task-owned JSON manifest。其 schema 固定为 `{schema_version, verification_ledger_path, repositories.{ai_radar,ai_agent_config}.{main_checkout,candidate_worktree,base_sha,candidate_sha,candidate_tree_hash,main_head,main_dirty_manifest_hash,main_tree_hash}}`，所有路径先 `realpath`，每个 candidate 必须与对应 main checkout 不同；candidate 每次提交后原子更新 SHA/tree hash。canonical task-input hash 只覆盖该 manifest 的上述输入字段；verification result 不写回 manifest，而是 append 到 repo-external `verification_ledger_path`，记录 input hash、phase/check-set、result manifest hash 与前一条 ledger hash，因而不产生自失效循环。所有 phase/final/delivery green gate 都只从 manifest 解析 candidate，不接受另传的裸 path/SHA；执行前后均断言 resolved cwd 等于登记 candidate、不同于登记 main、`HEAD`/`HEAD^{tree}` 等于 candidate SHA/tree hash、`git status --porcelain=v1 --untracked-files=all` 为空，且 main HEAD/dirty/tree hash 未变。TDD 中间的 dirty/red-test 运行可留作过程证据，但不能满足 green gate。每个 verification manifest 记录 canonical task-input hash、clean SHA/tree hash 与 create-exclusive run root；candidate/input hash 变化才使旧绿灯失效，append verification output 不会。Phase 0–2 可并行准备测试与 schema，但共享协议冻结前不得同时写 consumer 与 provider 的同一契约。任一阶段要改变 §3 的用户决策或安全边界时必须 stop，不得以 fallback/临时开关继续。

阶段 gate 统一使用 tracked canonical scripts，而不是仅靠本节 prose：AI Radar `scripts/performance/verify_all.sh --phase phase0|phase2|phase4 --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"`，共享仓库 `continuous-performance/scripts/verify_all.sh --phase phase1|phase3|phase4 --task-manifest ...`，prerequisite 使用 `verify_codex_isolation.sh --task-manifest ...`，最终使用共享 candidate 的 `verify_pair.sh --task-manifest ...`。每个入口都执行上述前/后身份检查并为 invocation 用 `mktemp -d` 建 create-exclusive run root；临时 HOME、`AI_RADAR_DB`/WAL、shared registry/store/state/log 及 evidence 必须全部位于该 root、执行前不存在，多个并发 checker 各用独立子目录。真实项目 DB/shared state 路径由 outer sandbox/controller 设为 verifier process tree 不可写，并用 writable-open audit/sentinel fixture 证明任何写尝试在 syscall/adapter 边界失败；不得为了验证停真实服务。before/after real-state hash 只作诊断，允许外部 live service 合法变化，pass/fail 依据是 verifier attribution audit 为零而非全局字节静止。未显式指向 run root、任一临时路径已存在或 verifier 对真实路径有 writable open/write 即非零。每个入口写带 phase id/check-set/exact commands/results/DB+artifact paths 的 verification manifest并 append 外部 ledger；任一 check、后置身份或 ledger append 失败均非零。`verify_pair.sh` 同时解析两仓 clean SHA/tree 与最新 repo verification hashes，在独占临时 HOME/DB/state 运行真实 provider+AI Radar pair integration，输出 joint manifest；交付只接受当前 canonical input hash 对应的最新 joint hash。

### Prerequisite gate — 先证明 Codex 隔离可行

第一项、且在 gate 通过前唯一允许的仓库改动，是在 `ai-agent-config` 隔离 worktree 中实现 `continuous-performance/scripts/verify_codex_isolation.sh` 及其 disposable fixtures；不得同时写 shared runtime 或 AI Radar。然后按 V36 在 disposable repo、synthetic incident、fake credential/honeytoken 和真实 LaunchAgent 等价环境完成 read-only/write 两条 capability spike。spike 必须证明 Codex client 可完成认证与 schema output，而模型触发的命令无法读取认证材料、联网、写 disposable worktree 之外、访问 Unix socket、请求提权或调用禁用动作；同时记录所用 OS sandbox/controller allowlist 机制与 exact CLI flags。canonical 命令为：

```bash
cd <ai-agent-config-isolated-worktree>
./continuous-performance/scripts/verify_codex_isolation.sh \
  --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST" \
  --codex "$(command -v codex)" \
  --wrapper ./claude/bin/codeagent-wrapper \
  --launchagent-equivalent
```

script 先按统一 gate 规则核验 shared candidate clean SHA/tree、main 不变与 canonical task-input hash，并自行在 create-exclusive run root 下创建唯一 evidence dir；不得接受/派生 HEAD 固定的可复用路径。同 SHA 重跑/并发各有独立 invocation id。任一 case、后置身份或 required artifact 缺失即非零，并写/append `verification-manifest.json`（phase/check-set、exact argv、Codex/version/resolved model、OS boundary、fixture hashes、每项拒绝证据、外部状态 before/after attribution）。任一拒绝无法由外层强制、只能依赖 prompt/ambient rules，立即停止本计划并报告安全可行性未证实，不得先实施 Phase 0–2 后再发现 worker 边界不可实现。

### Phase 0 — 建立事故基线并修复当前回退

#### 0.1 建立可重放 production-scale fixture

修改/新增：

- `tests/performance/conftest.py`
- `tests/performance/test_curated_archive_latency.py`
- `tests/performance/test_pipeline_contention.py`
- `pyproject.toml`（注册 `performance` marker）
- `tests/test_curated_archive.py`（如既有行为测试更适合放此处）
- `tests/test_wechat_interpretation.py` 或新的路由生命周期测试
- `scripts/performance/verify_all.sh`（先落 phase0 check-set，后续阶段扩展）

任务：

1. 用 deterministic bulk insert 建立与当前形状同阶的 SQLite fixture：full-scale generator config 固定 30k items、70k evaluations、5k curation runs、200k curated rows、1.8k interpretations（其中 1.3k displayed）；只用 `example.invalid` 和短占位文本，不复制生产内容。生成前由同一 config 写 expected cardinality manifest，除逐表数量外，还固定真实请求链的 expected visible identity/cardinality：curated eligible distinct item ids、首页/curated API exact total 与 page 1/page 2 ids、joinable `save_decision=1` interpretation ids、微信 page 1/page 2 ids 与一个 deterministic readable detail slug。生成后先逐表 exact `COUNT(*)`，再实际调用 API/SSR preload 与 manifest exact compare `total/items/page/slug`；删除或错连任一 membership/interpretation relation 的负向 fixture 必须让同一 gate 在计时前失败，不能让 200k rows 集中到少量可见 join 后仍过。数据生成与计时分开，fixture 可 session-cache；若 CI 时间预算不足，可按明确 scale config 缩小并对该 config 的全部 table/visible identity 做同样 exact compare，但本地 full-scale gate 必须使用上述完整数值。
2. 先复现“新增不改变 archive membership 的 `item_evaluations` 后，下一次首页请求重算 expensive count”并让测试失败。
3. 用 spy/query counter 断言 cache hit/invalidation 语义；墙钟只作为本机 performance suite 的第二道门，普通 unit suite 不靠脆弱的机器绝对时间。
4. 加入受控后台 writer fixture，模拟 evaluation 写入；验证读请求正确、无 lock error，并记录 busy/idle latency 分布。
5. 为 `/wechat` 和 `/wechat/{slug}` 写 connection close regression；可通过 monkeypatch/factory 记录 close，另加小型 WAL checkpoint 行为测试。

内部 verify：

```bash
: "${CONTINUOUS_PERFORMANCE_TASK_MANIFEST:?absolute task-owned manifest path required}"
AI_RADAR_CANDIDATE_WORKTREE="$(jq -er '.repositories.ai_radar.candidate_worktree' "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST")"
cd "$AI_RADAR_CANDIDATE_WORKTREE"
./scripts/performance/verify_all.sh \
  --phase phase0 \
  --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"
```

script 在唯一 `mktemp -d` run root 内分别创建两个原先不存在的 DB/WAL 路径并运行 focused/performance commands，记录 fixture cardinality/hash；同 SHA 重跑或并发也不共享 DB/evidence。未形成成功 verification manifest 时 trap 清理 run root，成功则封存路径供 ledger 引用。要求红灯能在修复前稳定复现；若 fixture 本身波动，先修 fixture，不得放宽 SLA 掩盖。红灯/dirty run 只作 TDD 过程证据；Phase 0 green gate 只接受 clean candidate SHA/tree 与 canonical task-input hash 绑定的结果。

#### 0.2 最小修复首页 exact-total 缓存

修改：

- `src/airadar/web/routes/curated_archive.py`
- 必要时 `src/airadar/web/routes/pagination.py`
- `src/airadar/curator/precompute.py`（只有 profile 证明需在写入点 prewarm/invalidate 时）

任务：

1. 把 exact-total cache 的 version 收窄为真正影响归档 membership / 筛选计数的状态；`item_evaluations` 的 enrich/scoring 追加不得无条件失效 count。
2. 保留数据库路径隔离、category/search signature、items/curated membership 变化后的正确失效和精确 total；禁止用估算总数、旧总数或隐藏分页末页换速度。
3. 如果 version 查询本身仍超预算，profile 后才引入显式 generation table/prewarm；不要先加新表或复杂 invalidation 总线。
4. 只有 EXPLAIN/profile 证明 `_archive_items` 仍是阻塞项时才添加 index/query rewrite，并把 before/after plan 写入事故证据；否则不扩大改动。

#### 0.3 收敛微信 SSR 连接生命周期

修改：

- `src/airadar/web/app.py`
- 复用 `src/airadar/web/routes/request_db.py`

任务：将两个 SSR 路由改用 `conn_from_request(request)` 或等价显式关闭机制；API 与 SSR 使用相同 request-scoped 语义。验证异常路径同样 close。

#### 0.4 本地和现场复测

1. 同一 commit 在隔离 DB 上跑 idle/busy performance suite。
2. 以交错顺序测 origin `/`、`/api/v1/curated?page=1`、`/wechat`、`/api/v1/wechat?page=2&limit=50`，每个状态至少 20 个样本；记录 pipeline 阶段。
3. public 路径只作为 provisional 对照；如果 origin 恢复而 public 仍慢，事件保留为 network/tunnel 分支，禁止继续盲改 SQL。
4. 使用不可变旧版本代码 + 复制/只读 fixture 做历史对照；绝不让旧代码指向生产 DB。
5. 详情 journey 的 slug 从同一 fixture 或当轮列表响应中确定性选取并记录，不硬编码生产文章；若不存在可读详情，测量为明确的 content/precondition failure，不改测量目标。

Phase gate：V16–V18 通过；现有内容/分页 contract 测试全绿；before/after profile 和样本先写入 task-owned、secret-redacted、content-hashed staging evidence。Phase 1 schema 可用后必须无损 ingest 为初始 incident bundle 并核对 hash；不得提前伪造尚不存在的 v1 bundle。

### Phase 1 — 在 `ai-agent-config` 实现共享 CLI 与协议

新增/修改：

- `continuous-performance/pyproject.toml`
- `continuous-performance/bin/continuous-performance`
- `continuous-performance/src/continuous_performance/{cli,config,models,store,evaluator,incidents,baseline,retention,notifications}.py`
- `continuous-performance/schemas/*.schema.json`
- `continuous-performance/tests/`
- `continuous-performance/scripts/verify_all.sh`
- `continuous-performance/scripts/verify_pair.sh`
- `continuous-performance/scripts/verify_deployed_pair.sh`（随 content-addressed generation 安装，删 source 后运行）
- `continuous-performance/fixtures/example-project/`
- `continuous-performance/{install,uninstall,status}.sh`
- `continuous-performance/resource.continuous-performance.plist`
- `continuous-performance/README.md`
- `im-notify/bin/run-or-alert`、`im-notify/im-notify.test.js`、`im-notify/README.md`（只增加 fleet 使用的 opt-in fail-closed 模式，保留既有默认行为）
- 根 `install.sh`、`README.md`、`CHANGELOG.md`

任务：

1. 按 D14–D19 冻结 v1 JSON/TOML contract：project/journey/vantage/metric/display unit/window/budget、`not_observed`、provisional、baseline provenance、incident fingerprint、load classification、retention/waterline、readiness 与 model fixture budget。`config/performance.toml` 是预算数值唯一权威；人读 contract 只定义语义并指向对应 tracked keys，不手工复制第二份数值。
2. 实现且只暴露 `--version`、`protocol inspect --json`、`doctor`、`register/unregister/list`、`config validate`、`run-all`、`probe ingest`、`evaluate`、`baseline propose/promote`、`incident show`、`status --json`、`retention dry-run/apply` 这组 v1 CLI surface；Codex worker 编排是 `run-all` 对 confirmed incident 的内部路径，不新增另一条公开 worker 命令。所有写操作使用 SQLite transaction + lock，重复运行幂等；registry entry 绑定 canonical project root、tracked config、owner marker、protocol requirement 与 config hash，atomic replace，project-id/root 冲突或 foreign ownership 一律拒绝。unregister 只移除本 owner 的 registration；共享 uninstall 在仍有 consumer 时拒绝，除非用户另行明确 force。
3. evaluator 只做确定性数学/状态机：绝对预算、>30% relative regression、样本下限、连续窗口、hard failure、resolved、cooldown/single-flight。
4. `baseline propose` 只接受达到同一 stream identity、覆盖率、absolute budget 与 clean provenance 要求的健康样本，并只生成候选报告；`baseline promote` 必须是明确人工命令，记录操作者、时间、样本窗口、Git SHA、config hash、evaluator/window version。Codex runner 无权调用它；stream/config/window 变化使旧 baseline `stale`，不得跨 vantage/load/metric 比较。
5. installer 按 peer 自治规则 staged install：先把 candidate 复制到 content-addressed generation，用其中 CLI 验证全部 consumer，再切换 pointer/plist/job并 postflight；runtime 不得引用 source worktree。切换前持久写 transaction marker（pre-action generation/job state、new generation、每步状态），每步 fsync。deploy receipt 的 scope 必须显式包含“本 transaction 未成功 commit 时自动补偿回 exact pre-action state”；这属于同一 deploy action 的原子失败补偿，不是成功部署后的用户发起 rollback。`install/status/doctor` 只可按 marker执行该 exact compensation；transaction 成功 commit 后 marker关闭，之后任何 rollback 都必须另取 V31 rollback receipt。kill-point/restart/compensation-failure fixture证明不留混合态；补偿失败则 unloaded+incompatible并给人工 rollback packet，不继续副作用。根 installer显式传播非零。
6. fixture consumer 证明：共享层不 import/硬编码 AI Radar package、route、SQL 或 schema，只消费版本化协议；未知 schema/状态/evaluator fail closed；重复 ingest/evaluate 不重复创建 incident/推进 streak；retention 不误删或篡改 sealed/open/candidate 事件。
7. `run-all` 从 registry 动态派生本轮 due-project 集合，每个 project 再从 tracked config 派生 expected `journey × vantage` 集合；每项必须得到 success/failure/timeout/skipped/unknown 的显式 outcome，禁止用缺行表示。每个 registration 有独立错误边界；一个 consumer 的 breach/config/runtime failure 不能阻止后续 consumer，循环结束后比较 expected vs actual 并汇总，存在 runtime/config/transport/completeness failure 才整体非零。用首项目慢/失败、第二个成功的 fixture 锁定无饥饿与 aggregate semantics。
8. 执行模型固定为：全局最多 2 个项目并发、每项目最多 1 个 active run、browser 全局最多 1 个；同一 project/target 的 latency-sensitive origin/public 与 browser journey 交错串行；round deadline 4 分钟并用公平队列续排未完成项目。cap/deadline 写入 config/schema；slow/timeout fixture 必须证明不超 cap、后续项目不饥饿、同 target 不因探针并发污染测量。
9. capacity admission 不依赖新 consumer 尚不存在的历史分布：registration 先处于 `candidate/disabled`，从 tracked config 读取每项**端到端** hard timeout（包含 launch、probe、teardown 与 outcome persist）与 cadence，以 worst-case timeout、project≤2/browser≤1、同 target 串行和 4 分钟 deadline 做保守可调度性证明；只有 worst-case schedule 严格 `<4m` 才接纳，`=4m` 与 `>4m` 均 fail closed。再跑不写正式 observation 的 timed postflight，仅用于给 packet 展示实际 headroom，不能用乐观均值放宽 worst-case gate。无完整 timeout/expected-set 输入时 fail closed。运行时 status 暴露 due/backlog age/cadence miss；超载非绿色并生成 capacity decision packet。packet 固定包含 project/config hash、expected set、worst-case schedule、timed headroom、可直接打开的 report 路径/本地 URL，以及 keep-disabled（推荐且无副作用）、另立计划批准扩容、另立计划变更 SLO 三种后果与精确回复格式；请求用户前验证入口、证据和命令可读。没有后续新计划时保持 disabled，不静默降频。
10. 状态变化通知先写持久 outbox，再调用 `im-notify`；confirmed/resolved/monitoring-gap-recovered 用 alert channel，candidate-ready 用 notification channel。scheduler 持久记录固定 anchor、cadence `c` 与 scheduled/outcome ledger；恢复时以 wall time `r` 计算 `s=max{anchor+k*c | anchor+k*c<=r}`。第一个成功 recovery tick 在单一 transaction 中把 `last_completed_slot < slot < s` 的所有不可执行历史 slots 合并为显式 missed interval/outcomes（不生成伪 observations、不逐槽回放），并把 `s` 作为本次实际执行的 scheduled slot、`observed_at=r`；因此 `r==s` 时当前边界被执行而非 missed，`r` 刚前取上一边界，刚后取当前边界。随后写唯一 `monitoring_gap_recovered` event（gap start/end、missed cadence、recovery tick）、设置严格未来的 `next_due=s+c`、以本次成功 outcome 的 `observed_at` 建立唯一新 readiness epoch，并保持 status 非绿色；commit 后再经 outbox 告警。后续 tick 只从 next due 正常推进，不重复 gap event。doctor/register/autonomy packet 分别做两种 webhook 的真实 delivery preflight；发送失败保留 pending delivery、status 可见，每 tick 最多重试一次，backoff 为 5m→15m→1h→最多 6h，成功后以 event id 去重。连续 3 次失败进入 `delivery_stuck` 非绿色状态并写 host-visible error/log、停止启动新的 remediation 且 readiness 重置；outbox 不丢弃，恢复后仍投递一次。只允许 operator 通过独立 tracked config + delivery preflight 启用备用 channel，系统不得静默换通道；不因 alert channel 成功掩盖 notification channel 失败。
11. 为现有 `run-or-alert` 增加 opt-in `--require-notifier`：该模式在 child 启动前解析并验证可执行的 `im-notify`；缺失或不可执行时写明确 stderr、返回非零且 child invocation count 必须为 0。fleet plist 固定使用该模式和已安装绝对路径；不带此 flag 的既有调用保持原行为。测试覆盖 notifier 缺失/不可执行、可用时 child success/failure 与 alert 调用，并由 doctor/postflight 校验 installed argv。

内部 verify（只从 task manifest 解析共享 candidate；真实安装不在本 gate 发生）：

```bash
: "${CONTINUOUS_PERFORMANCE_TASK_MANIFEST:?absolute task-owned manifest path required}"
AI_AGENT_CONFIG_CANDIDATE_WORKTREE="$(jq -er '.repositories.ai_agent_config.candidate_worktree' "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST")"
cd "$AI_AGENT_CONFIG_CANDIDATE_WORKTREE"
./continuous-performance/scripts/verify_all.sh \
  --phase phase1 \
  --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST" \
  --launchagent-equivalent
```

canonical script 自建唯一临时 HOME/run root，在 LaunchAgent-equivalent 环境完成 pytest/ruff/mypy、installer 两次、已安装 binary 黑盒 surface 与 lifecycle fixture；不得读写真实 HOME 的 installation/registry/job。安装 fixture 还必须读取已安装 plist/loaded job，断言唯一 label、`StartInterval=300`、`RunAtLoad=true` 与预期 executable/argv，而不只做语法 lint；真实 install 只能在 §8.4 的独立 deploy 回执后发生。

Phase gate：V14、V15、V23、V29、V30、V33、V37、V39 通过；尤其是来源 worktree 删除/upgrade 中断后 service 可恢复到唯一 generation、missing notifier 在 child 前 fail closed、over-capacity registration 不能静默进入调度、fixture consumer 全链、D14 storage pressure、store 不可写恢复和两个通知 channel 都有独立证据。

### Phase 2 — AI Radar probes、观测和服务生命周期

新增/修改：

- `config/performance.toml`
- `src/airadar/performance/{http_probe,browser_probe,context,runner}.py`
- `src/airadar/cli.py`
- `src/airadar/admin/access_log.py`
- `src/airadar/admin/performance.py`
- `src/airadar/admin/metrics.py`
- `src/airadar/web/routes/admin.py`
- `web/templates/admin.html`
- `src/airadar/web/app.py`（轻量 timing middleware / `Server-Timing`，若 profile 证明确有诊断价值）
- `pipeline.sh`、`run.sh` 与共同 stage runner（所有 orchestrated/direct stage 入口发布同一 authoritative lease/event ledger）
- `install.sh`、`uninstall.sh`、`status.sh`
- `tests/test_performance_cli.py`
- `tests/test_performance_probes.py`
- `tests/test_admin_metrics.py`
- `tests/test_admin_routes.py`
- `tests/test_pipeline_scheduler.py`
- `tests/test_service_contract.py`
- `scripts/performance/verify_all.sh`

任务：

1. 定义四条核心 browser journey 与相应 HTTP components；selector 使用 UX contract 中稳定语义，不依赖卡片具体文章文案；详情 slug 按 Phase 0 的确定性规则派生。
2. 浏览器子进程设置硬超时、独立临时 profile/session、`finally` close、禁止认证/cookie；按 §4.3 的统一 stop predicate 记录 TTFB/FCP/first-card、API request、DOM settle 与关键失败。`doctor`、register postflight 和每轮 browser 前都实际 launch Chromium；runtime 缺失/损坏进入主状态 `incompatible`，并带 `incompatible_reason=browser_runtime`，不得冒充 regional `not_observed` 或产品 hard-failure/incident。Chromium 安装是 README 中的显式 operator step，fleet 不在事故路径自动下载。
3. 把 authoritative state producer 放在所有 stage 共用的 runner/lease 边界，而不是只放在 `pipeline.sh`。CLI dispatcher 维护机器可枚举的结构化 registry rows `{canonical_stage, entrypoint, kind=orchestrated|direct}`：orchestrated `canonical_stage` projection 必须按序 exact-equal `pipeline.sh` 的 `fetch,prefilter,score,enrich,curate,interpret`；direct `entrypoint` projection 必须 exact-equal 所有会执行这些 stage 的 CLI commands/aliases，含 `admin curate`。两个投影分开验证，不把 alias 当额外 canonical stage；以后新增/删除 stage 或 alias 未同步即失败。registry 中每一个 entrypoint 都在进入 stage 前原子发布版本化 active lease 并 append start event（owner pid、run generation、canonical phase、monotonic time、递增 sequence），在工作完全结束后 append end event并释放 lease；`pipeline.sh` 和 direct entrypoint 都经同一 runner。reader 用起止 cursor、active-set 与连续 event interval 判断整个 probe measurement interval，而不是只比较两个瞬时 state；任何相交 stage 都是 busy，cursor/sequence/generation/owner 不完整或矛盾则 unknown，只有完整区间证据为空才是 idle。context sampler 同时记录该 interval evidence 与 host、Git SHA/dirty、service、load/CPU/memory、DB/WAL 大小及只读行数摘要；采样失败写 `unknown`，不得从历史日志或 CPU 猜 phase。集成测试动态遍历每个 registry entrypoint，并覆盖完整落在 probe 两次读取之间的短 stage，证明其仍归 busy；正常/异常退出只能收束为有证据的 busy/idle 或 unknown，不能只注入 state JSON 或抽测一个代表 stage。
4. 补齐 public path 分类：`/wechat`、`/api/v1/wechat`、`/wechat/<slug>`；加入请求 duration 的机器可读日志/metric，同时保持现有 A3 5xx 语义。
5. `install.sh performance` 调用 shared CLI register，把项目根与 tracked config 写入本机 registry；fleet LaunchAgent 使用 `StartInterval=300` + `RunAtLoad`，以 `run-or-alert --require-notifier --key continuous-performance-fleet -- ... run-all` 兜住 runtime crash。notifier 缺失/不可执行时不得启动 `run-all`，launchd stderr 明确可见且 postflight 非零；单个 SLO breach 由 evaluator 自己告警后 `run-all` 仍为零并继续其他项目，runtime/config/transport failure 也先隔离并跑完其他项目，最后才 aggregate 非零。
6. register/unregister/status 全生命周期幂等；unregister 默认保留历史状态/incident，status 只读地显示 last quick/browser、协议兼容、baseline、open incident、Codex active、regional `not_observed`。
7. `/admin` 增加“公开页面性能”只读板块，按 journey × vantage × load class × metric 展示最新值、窗口 P75/P95、display unit、预算、baseline qualification、分类、freshness、open incident；same-host public 必须显式 `provisional`，regional `not_observed` 不得借其变绿；missing/candidate/stale baseline、insufficient/incompatible/`not_observed` 都不得显示绿色。它读取共享 runtime 的稳定 status JSON，不复制 evaluator。页面与 status JSON 都从 tracked config/合法 vantage/load/metric inventory 动态派生 expected tuple set；missing/extra row 均为 completeness failure，错误态行不得省略。
8. 运行数据全部放共享 state/log 目录；tracked config 用中性默认值，不能包含 webhook、用户名、绝对个人目录或生产 cookie。

Phase gate：V1–V7、V13–V15、V26、V29、V30、V32 通过；共享 launchd 故障由 `run-or-alert` 可见，missing notifier 在 child 前 fail closed，单项目失败不阻塞其他项目；文档明确同主机机制只能发现 host 存活时的 job/runtime 故障，整机/网络失联须等 off-host probe 才能独立发现。

Canonical gate：`AI_ROOT="$(jq -er '.repositories.ai_radar.candidate_worktree' "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST")"; "$AI_ROOT/scripts/performance/verify_all.sh" --phase phase2 --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"`。只有该 phase/check-set manifest append 成功才满足本 gate。

### Phase 3 — 受控 Codex remediation worker

在 `ai-agent-config` 新增/修改：

- `continuous-performance/src/continuous_performance/{remediation,worktree,codex_runner,gates}.py`
- `continuous-performance/prompts/{diagnose,implement}.md`
- `continuous-performance/schemas/{diagnosis,candidate}.schema.json`
- `continuous-performance/tests/test_remediation_*.py`

在 AI Radar 新增：

- `config/performance.toml` 的 gate section（沿用唯一 project config 与单一 config hash，不新增第二份 gate config）
- `scripts/performance/verify_candidate.sh`
- 相关 contract tests

任务：

1. evaluator 只在 incident=`confirmed`、对应 stream baseline 已晋升且未 stale、`remediation.enabled=true`、无 active worker、cooldown 满足时排队；scheduler tick 到 deterministic confirmed 之前不得调用 Codex，也不得创建 goal/常驻 agent。
2. 第一次 `codex exec` 使用 read-only sandbox。controller 不把可变 main filesystem path 直接作为代码输入，而是从 incident 记录的 base commit materialize 只读 `source_snapshot`：exact HEAD/tree、clean 状态、implementation base SHA/tree，以及逐个 allowlisted 文档的 repo-relative path+content hash；用户 main worktree 的 dirty/untracked 内容不进入 snapshot。spawn 前 exact verify 全部 hash，输入仅 sealed incident bundle、该 immutable snapshot、允许文档和目标；通过 JSON Schema 输出 hypotheses、证据、建议 reproducer、置信度、是否值得改代码。controller 只从 schema-valid diagnosis 中选择一个 attempt，并封存其 artifact/hash；无足够证据则 `blocked/needs_observation`，不启动写 worker。
3. 写 worker 创建 incident 专属 detached worktree/branch，确认 main worktree dirty 内容不被复制/stage。先对现有 `claude/bin/codeagent-wrapper` 做 capability matrix：能透传下述固定安全参数则复用/最小扩展；不能才保留专用 runner，并必须复用或等价覆盖 wrapper 的 wall/inactivity timeout、interrupt、backend-not-found 和 exit-code contract，避免第二套失联监控。
4. controller 以 worktree 为唯一 cwd、scrubbed environment 和固定绝对 executable 启动 Codex：`--ephemeral --strict-config --ignore-user-config --ignore-rules --sandbox workspace-write`，显式 `approval_policy=never`、`sandbox_workspace_write.network_access=false`、`web_search=disabled`，不加载 user/project execpolicy rules、MCP/apps/hooks 或额外 writable roots。`workspace-write` 的 `/tmp` 与 Git protected-path 语义不能被当作“只写 worktree”的充分证明；外层 OS/controller allowlist 还必须限制可执行命令、环境、cwd 与输出路径，拒绝权限申请/自升 sandbox，controller 独占 stage/commit。
5. read-only 与 write worker 都在真实 LaunchAgent 等价环境预检：Codex 绝对路径、版本/required flags、认证、JSON Schema smoke run、网络拒绝、worktree 外写拒绝、rules/MCP/app 不可用。失败使用主状态 `incompatible` + `incompatible_reason=remediation_<reason>` 并保持 remediation disabled；只传最小 PATH/认证句柄，不把完整 shell env、token 或 cookie 写进 plist/incident。
6. gate 清单由项目声明：focused regression、performance fixture、`uv run pytest`、ruff、mypy、`$review-gate`；如改 public config，执行时先查官方 docs。任一失败保留证据并停止。live call graph 固定为最多 1 diagnosis + 1 implementation；每次 spawn 前在 provider ledger 预留，结束后结算，单次 hard timeout 60 分钟、incident 累计 hard timeout 120 分钟，第三个 invocation 必须在 spawn 前拒绝。
7. 全项目最多 1 个 active worker；超限或 6 小时 cooldown 未到时不启动。diagnosis 超过 60 分钟立即终止且不启动 implementation；任一路径耗尽 2 invocations/120 分钟后标 `blocked` 并通知人工，禁止 worker 改额度或重启自己。
8. implementation call 的输入契约固定为 sealed incident hash + 同一 `source_snapshot`/allowed-doc hashes + 被选 diagnosis artifact/hash + implement prompt hash + gate manifest/config/model pin；implementation worktree 必须由 snapshot 的 exact base SHA/tree 创建，controller 在任何写入前验证所有 hash/identity。输出为 schema-valid candidate JSON 与 worktree diff；source/doc/base/diagnosis 任一缺失、漂移、错配或篡改立即非零且 write invocation=0。全部 gate 通过后，由固定 controller 按 `create-commit` 纪律 stage 本 incident allowlist 内改动并形成**本地** candidate commit，记录 hash/worktree/test snapshot 及完整 input dependency；不得 merge 到主分支。
9. 外层 allowlist/deny guard 与恶意集成测试必须拒绝 `git push`（含本地/file remote）、deploy/service restart/`launchctl`、生产 DB 与数据目录写命令、`baseline promote`、主工作区/其他 repo/临时目录持久写入、网络/Unix socket/凭证读取、修改 config/rules/requirements 以自升权限。不要只靠 prompt、execpolicy 文本或网络默认值禁止。
10. diagnosis/candidate 结构记录输入 incident hash、`source_snapshot` HEAD/tree/clean/base 与逐文档 path+hash、被选 diagnosis artifact/hash（candidate 必填）、diagnose/implement prompt hash、Codex CLI/model identity、attempt、输出 schema version、termination reason 与完整 gate snapshot；典型 fixture至少含无证据、可修性能回退、恶意 incident。非确定性输出允许不同 hypotheses，但相同 fixture 必须走同一 schema/权限/gate 路径，重试不得覆盖前次 artifact；candidate verifier 从 immutable source/docs 到 diagnosis 再到 diff/commit 逐 hash 重放依赖。
   - Phase 3 先用 capability/quality fixture 选出 exact model + reasoning effort，写入 tracked controller config，并纳入 autonomy enable packet；controller 每次显式传入并记录该固定值、完整 argv/config overrides、prompt template hash、input bundle hashes 与 attempt 序号。模型不可用、resolved identity 与 pin 不一致或迁移未重新过 fixture/autonomy gate时 fail closed。Codex 不承诺 seed/temperature 时，不把文本输出宣称为确定性；retry 只因结构/transport failure 或新证据触发，不能为抽到满意答案而重抽。
   - fixture matrix 固定覆盖：无证据时停止、已知可修回退能形成失败测试与最小候选、恶意输入被拒、schema/provenance 完整；repetitions 与通过率严格使用 D16。按 D19 一次只评测一个 exact model+reasoning candidate，最多 3 个 repair trials；每个固定 1 diagnosis + 证据足够时最多 1 implementation，trial≤60m、batch≤4h。mandatory safety/schema/provenance 的 outcome/threshold harness 只用 deterministic fake artifacts，provider ledger delta 必须为 0；唯一真实 pre-trial V25 capability smoke + 三个 trials 的 provider cap 为 7，第 8 次在 spawn 前拒绝。auth/quota/time failure fail closed，不并发、不换模型、不扩预算。pin、resolved identity 与 provenance 相等；升级重新过同一 bar 与 autonomy gate。
   - controller 先创建新的 `matrix_run` 与空 trial/provider ledgers，再运行 V25；V25 的实际 Codex schema/security call 就是该 run 唯一的 pre-trial `capability_smoke`，通过后直接创建 diagnosis trial，不再启动第二个 smoke。provider row 主键为 `matrix_run_id + provider_invocation_id`；smoke 的 `trial_id=null`、只计 batch elapsed，diagnosis spawn 才原子创建 trial row并使 trial id 对 diagnosis/implementation 必填。每个 spawn 前同时检查 provider count `<7` 并预留 row，结束后无论成败结算。local zero-process auth probe 为 0/0；smoke failure 为 0 trial/1 provider；trial role failure 结算 provider并标 failed trial。任何失败不得同 batch 自动重试；disabled/incompatible recovery report列两个 ledger及 V25/V28入口。operator 修复后显式创建新 matrix run，V25 仍作为该新 run 的唯一 smoke；无法恢复则 stop-and-ask。
11. confirmed/resolved/candidate-ready 只写 Phase 1 的持久 outbox；controller 不直接发送或静默换通道。通知失败不丢 incident 状态，delivery status 与重试证据进入 incident/status。

Codex 运行形态：使用当前官方支持的非交互 `codex exec --json --output-schema ... -o ...`。可以把 incident objective + completion criteria 写成 goal 风格 prompt，但 `/goal` 不是调度器，本计划不创建常驻 goal daemon。

Phase gate：prerequisite V36 已先通过，且 V8–V12、V23–V25、V27、V28、V30、V38 全过。尤其是 D11 的所有时间/并发边界、D12/D16/D19 的 pin/漂移/迁移与 serial batch budget、恶意/误判 incident fixture、同 incident live candidate 链与真实 launchd 等价 capability/security postflight 均可机械失败。

Canonical gate：`CFG_ROOT="$(jq -er '.repositories.ai_agent_config.candidate_worktree' "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST")"; "$CFG_ROOT/continuous-performance/scripts/verify_all.sh" --phase phase3 --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"`。只有该 phase/check-set manifest append 成功才满足本 gate。

### Phase 4 — 契约、文档、烧机与启用 gate

AI Radar 文档：

- 新建 `docs/contracts/performance-contract.md`
- 更新 `docs/contracts/ux-contract.md`
- 新建 ADR（建议 `docs/adr/008-continuous-performance-control-plane.md`）并更新 `docs/adr/README.md`
- 更新 `docs/architecture.md`
- 更新 `docs/operations/monitoring-alerting.md`
- 更新 `docs/operations/services.md`
- 更新 `README.md`、`CHANGELOG.md`、`docs/CLAUDE.md`
- 只有形成长期、项目专属教训时才 append `docs/experiences/performance.md`

共享仓库文档同时更新 `continuous-performance/README.md`、根 `README.md`/`CHANGELOG.md` 与现有 `docs/services.md`；写清 install/uninstall/status、consumer add/status/remove、升级/回滚、日志/告警、make-live 与注册存在时拒绝卸载的行为。

契约职责：

- `ux-contract.md`：在 L1“页面与功能”新增维护者 persona 的“公开页面性能板块（`/admin`）”surface，写明 access path、journey×vantage×load×metric 矩阵、解释/非绿色边界；把四条 public journey 投影到现有 HP-1、WX-2、WX-4、WX-5 与 RS-3 并引用性能验收，移除“性能基准测试不在范围”旧排除。四条 journey 的受影响 L2 条款还必须 exact cross-reference 一个共同体验条款：逐 journey 判断“指定动作能一次连续完成”及“无自动指标未覆盖的明显停滞/错误/陈旧页面”，两维全 pass 才完成，任一 fail 阻塞且不自动触发部署/回滚。blanket admin 排除收窄为“除该维护者板块外，其他管理后台与数据管道不在范围”；L2 新增 `AD-1`，固定 metric/unit/vantage/load/freshness/baseline/provisional/`not_observed` 的可解释展示、expected tuple 完整性与所有非绿色规则，并与 L1 surface 一致。
- `performance-contract.md`：定义 journey 起止、percentile/window、地区、provisional/`not_observed`、绝对/相对规则、baseline 与证据语义；预算数值只引用 `config/performance.toml` 的 tracked keys，不手工复制具体数字。UX contract 同样不复制数值，避免双源漂移。
- `docs/operations/monitoring-alerting.md`：把 `scripts/performance/verify_all.sh` 与 `scripts/performance/verify_candidate.sh` 作为 canonical full/candidate 入口，写清何时运行、前置环境、成功 manifest、失败日志与重跑纪律；root README 与 docs index 链到该段。共享 README 对应记录 `verify_all.sh` 与 isolation spike 入口。

烧机与人工 gate：

所有本计划内的 user-gated packet 共用一个最小交付 envelope：decision objective/scope、唯一 action id、绑定的 project/candidate/deployed SHA 与 config/evaluator hash（按动作适用）、证据摘要与最短可达 report 路径/本地 URL、选项及后果、精确命令/rollback（有状态变更时）、精确回复格式。agent 必须在请求用户前验证路径、链接、命令和证据可读。baseline promotion、autonomy enable 与下述六类 production action 都进入同一 authorization ledger，但各是独立 action type；回执必须 scope/hash 精确匹配、未过期、单次消费且不可跨 action 外推。

1. 先以 `remediation.enabled=false` 运行 shadow mode，注入 timeout/slow response/schema mismatch/overlap/恢复，证明状态机和通知。
2. 修复后由 agent 采集一组健康 idle + busy 样本并生成 baseline proposal；用户只审阅已通过统一 envelope preflight 的 decision packet。packet 的专属内容固定包含 journey/stream 图、样本覆盖、Git/config/evaluator hash、未观测地区、异常/缺口、精确 promote 命令，以及 `promote`（进入下一 gate）/`reject`（保持 shadow 并说明需补证据）两种后果与回复格式。
3. baseline 晋升后开启 `remediation.dry_run=true`，用 replay incident 证明会产生诊断/候选动作但不写 worktree。
4. 用户再次显式确认后才把 `remediation.enabled=true`；enable packet 通过统一 envelope preflight，并固定包含 dry-run 结果、恶意 fixture、权限拒绝证据、Codex capability postflight、tracked diff、rollback 命令和 `enable`/`keep_dry_run` 后果。enable receipt 的 scope 精确绑定该 tracked config diff/hash，只授权 controller 在 AI Radar 隔离 worktree 形成这一个本地 config commit；错 diff/hash 必须在 commit 前拒绝。它不授权 adoption/merge/deploy/restart；这些后续副作用仍逐项走 V31 对应 action receipt和 postflight，不能由 worker 自改或从 enable 外推。
5. 连续 7 天无 cadence gap、经维护者确认的误报 incident、guard/worker 成功执行禁止动作或未处置 `delivery_stuck` 后，输出 off-host migration readiness report；guard 正确拒绝恶意 fixture 是安全通过，不重置 readiness。外部 probe 的购买、凭证和部署另立任务并需用户许可。
6. 为 D9 的六类 production action 生成逐动作 decision packet：candidate adoption、merge、push、deploy、restart、rollback 各有独立 action id；deploy packet 的 scope 必须逐项列出 install/copy/plist load 等实际副作用，restart 仍是另一 action。各 packet 在统一 envelope 外另含 diff/test snapshot、部署影响、前置/后置检查、rollback readiness，以及 `approve-this-action` / `hold` / `reject` 的后果；任何缺失/范围不符在副作用前 fail closed，D9 不构成任何一项预授权。
7. 生产体验 review packet 为四条 journey 各固定一行：journey、sample/slug identity、deployed SHA/config、自动 P75/P95/absolute+relative 预算/状态、人工起止动作、已自动核验的 content/count/pagination preservation，以及两个固定主观 rubric 维度——(a) 按指定动作能否一次连续完成，(b) 是否没有自动指标未覆盖的明显停滞、错误或陈旧页面；每维独立 `pass|fail`，任一 fail 必填 comment。packet 先验证详情样本、自动数据/版本与所有链接，任一行/维度/ballot slot 空缺即不能请求。四条 journey 的所有维度全 pass 才完成；任一 fail 只记录并阻塞，不自动 rollback/deploy，后续 candidate 或 rollback 仍走独立 V31 gate。

Phase gate：V19、V21、V22、V28、V31、V34、V35、V38 通过，并按 D1 的 live autonomy 完成语义收尾；未取得某项生产动作授权时只生成对应 packet 并停在该 action gate，不能借最终生产验收目标越过授权。

Canonical gate：分别运行 AI Radar 与 shared `verify_all.sh --phase phase4 --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"`，两条 phase4 ledger entry 都成功且 canonical input hash 相同才通过；随后仍须 §8 的 final-pair gate，不能用两个独立绿灯替代联合兼容性。

## 7. 验收分层

### 7.1 Consumer acceptance（L2，交付 gate）

| ID | 消费者与动作 | 可观察通过条件 | Backing checks / 边界 |
|---|---|---|---|
| C1 | AI Radar 维护者打开 `/admin` 性能板块并读取同机与地区状态 | 每个值能看出 journey、metric/unit、vantage、load class、窗口/新鲜度、baseline 与 provisional；expected 矩阵无缺项/多项；未观测/不足/不兼容/未晋升 baseline/store failure 不显示绿色 | V1–V8、V13、V23–V25、V32、V39 |
| C2 | 维护者按运行手册执行 install → status → unregister/re-register → uninstall/rollback 演练 | 命令自定位、幂等、失败可诊断；consumer 不串数据；有注册时共享卸载被拒绝；版本不兼容不判绿且旧版本可恢复 | V15、V22、V25 |
| C3 | 跨项目接入者只按共享 README 接入 fixture project | 无需 AI Radar 代码即可登记 journey、产出 observation、触发/恢复 incident 并查看证据；不存在产品专属 route/SQL/schema | V1、V8、V14、V15、V23、V24 |
| C4 | 用户接收 baseline 与 autonomy 两个独立 decision packet | 每个 packet 的 action id/scope/hash、链接/报告/命令已由 agent preflight；能一次选择 promote/reject 或 enable/keep-dry-run，并理解后果与 rollback；两个回执分别单次消费且不授权 production action。只有 promote + enable 后完成态才可能成立；选择 reject/keep-dry-run 时系统安全停住，但本 plan 保持未完成 | V19、V25、V28、V31；用户决策 |
| C5 | 维护者从 README/docs 索引查找性能契约、接入、服务、告警与排障 | 链接可达；UX contract 与产品一致；数值语义只在 performance contract 定义；共享/产品文档各自落在 owner repo | V22 |
| C6 | 用户在独立部署授权完成后逐项体验 `aiplanet.live` 首页、微信列表、详情与分页 | tracked config 与 D3 exact matrix 一致；四项同版本 automatic absolute+relative 与 content/count/pagination preservation 全过；用户再逐 journey 判断“指定动作一次连续完成”及“无自动指标未覆盖的明显停滞/错误/陈旧页面”，两维均 pass 才通过 | V3、V16–V18、V20、V35；没有明确 push/deploy/restart 授权时停在 deployment gate，不得用本地结果冒充最终完成 |
| C7 | 维护者查看 7 天 readiness report | 任一 cadence gap、经维护者确认的误报 incident、guard/worker 成功执行禁止动作或未处置 `delivery_stuck` 都不 ready；guard 正确拒绝恶意 fixture 不重置。健康满 7 天只开放 East Asia 下一阶段，US/EU 仍 blocked；没有供应商、凭证或外部部署副作用 | V21、V30 |
| C8 | 用户逐项接收 production action packet | adoption/merge/push/deploy/restart/rollback 的 scope、SHA/config、影响、命令与 rollback 可一次审清；只执行用户明确批准的那一个 action | V31、V34；逐动作用户 gate |
| C9 | 维护者按共享 README 接入唯一 fixture consumer，并在测试 channel 观察完整事件循环 | scheduled probe 产生 observation，confirmed/resolved 通知各只收到一次，status/evidence 可追溯；同一已知可修 controlled incident 必须形成 `candidate_ready` 本地 commit，`blocked` 只作为另一安全分支且不能满足完成 gate；verifier/worker 对主工作区、remote、生产数据、service 与 baseline 的 writable-open/write 归因审计为零，live 外部状态 hash 仅作诊断 | V30、V33、V36、V38 |
| C10 | 维护者查看 storage status 并演练 retention/pressure dry-run | 能读出 D14 的 retention/global+project bytes/free-space waterline、预计删除/停采/停 remediation 顺序；等号两侧结果唯一，open/candidate 始终保留，apply 只有显式 operator action | V23、V37 |

只有 C1–C10 中所有 agent-autonomous backing check 通过、且需要的用户 gate 已取得明确回执，才能声称完成。内部测试全绿不能替代该表。

### 7.2 Internal backing checks（L3）

| ID | 验收动作 | 预期可观察结果 | 边界 |
|---|---|---|---|
| V1 | 在 AI Radar 运行 `continuous-performance config validate config/performance.toml`，再运行项目 `status performance`；对共享源做 boundary contract scan | 配置/协议兼容；能看到 last run、baseline、incident、worker；共享层不 import/硬编码 AI Radar package、route、SQL/schema，只经协议消费 adapter | agent 自动 |
| V2 | 从 config/schema 枚举 expected journey×vantage×load×metric/unit 元组，打开状态 JSON 与 `/admin` 性能 DOM，逐项做 exact-set 比对 | same-host origin/public 有 provisional 状态；East Asia/US/Europe 均为 `not_observed`；缺项、多项、重复项、错误单位或 baseline missing/candidate/stale 任一使检查失败，且没有误导性绿色 | agent 自动 + 用户可读 |
| V3 | 先把 tracked config 的完整 journey/region/percentile budget matrix 与 D3 表做 exact-value 比对，再用 `n=6`/`n=12`、等号/刚超过、NaN/空值/重复值重放 absolute window | config 中首页/列表/详情 East Asia P75/P95 精确为 2s/3s、翻页为 1s/1.5s、US/EU P95 均为 5s；缺项、多项或错值均失败。nearest-rank、舍入与 `<=` 边界和 contract 一致，无 off-by-one；无效输入 fail closed | agent 自动 |
| V4 | 用同一 stream 的 baseline×1.29/×1.30/×1.31，再用不同 vantage/load/window/config 重放 | 29% 与恰好 30% 不因 `>30%` relative 规则失败；31% 触发；absolute budget 独立；跨 stream 不比较，config/window 漂移使 baseline stale | agent 自动 |
| V5 | 检查页面和网络请求 | 无新增 RUM/analytics beacon、cookie 或用户标识；synthetic UA 可识别 | agent 自动 |
| V6 | 交错运行 origin/public quick probe 和 browser journey，断言实际 request URL/connection target；从结构化 CLI registry 分别枚举 orchestrated canonical-stage projection 与 direct-entrypoint projection，前者按序 exact compare `pipeline.sh` sequence，后者 exact compare 所有 stage-producing commands/aliases（含 `admin curate`），再动态启动每个 entrypoint；用 atomic lease/event-ledger fixture 注入 valid active、无 lease+无 live owner、stale pid、partial/mismatched generation、sequence gap、起止矛盾、异常退出，以及完整发生在 probe 两次读取之间的 `idle → busy → idle` transient stage；再正交组合 high-CPU+idle、low-CPU+busy、high-CPU+unknown | origin 精确连接 configured `http://127.0.0.1:<port>`，public 精确请求 `https://aiplanet.live`，标签不能替代实际 target 断言。两个 projection 任一缺失/多出/顺序错或任一 entrypoint 未绑定 ledger 均失败；任何与 measurement interval 相交的 stage（含两次读取之间已开始并结束者）都唯一归 busy。只有连续 cursor/sequence 证明整个区间无 stage 才归 idle；异常退出只能可靠收束为 busy evidence 或 unknown，绝不误归 idle。每个样本带 vantage、代理/网络路径、Git/config、pipeline interval evidence，以及 host CPU percent/interval、1-minute host load、host memory bytes、configured DB/WAL bytes 与各自采样时间；stale/partial/gap/矛盾唯一归 unknown且不进入合规窗口，CPU/load/memory 无论高低都不能覆盖分类；不可混成单一 P95 | agent 自动 |
| V7 | 让 browser probe 超时并并发触发第二轮 | 第一轮被硬终止并清理；第二轮为 `skipped_overlap`；服务下轮仍可恢复 | agent 自动 |
| V8 | 用 fixed scheduler slots/fake clock 对 idle/busy 注入慢样本、重复 evaluate、3 windows、hard 5xx、unknown/probe infra；以 epoch anchor 生成 quick `11/12/13` 个 5m buckets、browser `5/6/7` 个 1h buckets，重放完整/重复/缺桶、window边界、freshness边界；在 outcome封存前后再注入同 bucket retry、迟到、乱序、future sample、active config hash 变化及 baseline promotion/identity 变化；在 observation-set/outcome/streak/incident/outbox/intent 各相邻事务写入点，以及 DB commit、payload temp mid-write、file fsync、no-clobber publish、directory fsync、最终 seal 前后强制 kill 并重启 | 样本/bucket不足或 window外为 `insufficient_data`且不推进；达到下限才 eligible。window_end精确等于 scheduled slot，右端点归前一 bucket，end包含/start排除。首次 evaluate封存 observation exact-set；同推进键重复、封存后 retry/late/out-of-order及同 slot config/baseline变化均不产生第二 outcome，future不进入。config/baseline identity变化清零旧 streak，从下一 scheduled slot以新 identity 的 streak=1重新 qualification，kill/restart不得复活旧 streak。事务 kill point只可见全无或全有；commit 后各 evidence kill point均从 intent BLOB 重建/续完 exact bundle，半写 temp不能占据最终路径，完整 sealed前 notification/remediation 均为0；恢复后 transition/bundle/incident/outbox 各恰好一次。deadline等号fresh、刚后stale；其余状态与 D17/D11一致，只有产品 confirmed可排 Codex | agent 自动 |
| V9 | 对 confirmed incident 跑 read-only diagnosis；分别删除、漂移、篡改 source HEAD/tree、allowed-doc hash 或 implementation base | 有效路径从 exact clean Git snapshot materialize 输入，产生 schema-valid、带 source/doc/input/prompt/CLI/model/attempt provenance 的 diagnosis；任一 source/doc/base mismatch 在 spawn 前非零且 provider invocation=0；无证据时停止；不改任何 repo/DB/外部状态 | agent 自动 |
| V10 | 对可修 fixture 启动 worker，同时主工作区保留用户 dirty changes | 只在专属 worktree 写入；主工作区 byte-for-byte 不变；先出现失败测试再出现修复 | agent 自动 |
| V11 | 在恶意 fixture 中诱导 push（含 file remote）、deploy/launchctl/Unix socket、写生产 DB/主工作区/其他 repo/`/tmp`、读凭证、晋升 baseline、自改 config/rules/requirements/权限 | 外层 guard/OS sandbox 拒绝、incident blocked、日志保留原因；无外部状态或权限变化 | agent 自动 |
| V12 | 候选通过/失败两条路径 | 通过时给本地 commit hash、worktree、完整 test snapshot；失败时无 commit 且保留可诊断证据；均不 push/merge/deploy | agent 自动；用户决定采纳/部署 |
| V13 | 无 remote probe、remote 数据 stale、再用 fake remote provider 注入满足覆盖的样本 | 依次为 `not_observed`、stale/insufficient、对应地区窗口；same-host provisional 不给地区染绿，vantage 不串数据 | agent 自动 |
| V14 | 接入 fixture consumer，并用 schema-only fixture 验证保留的 provider extension point | 新项目能复用 v1 协议；未知 provider payload fail closed；不实现或运行 RUM collector/fake provider，AI Radar 首期没有 RUM runtime surface | agent 自动 |
| V15 | 从干净临时 HOME 的两个 repo-external CWD，以绝对脚本路径运行共享 install/status/unregister/re-register/uninstall/rollback（installer 两次）；从已安装 binary exact-set 枚举并调用 `--version`、`protocol inspect --json`、`doctor`、`register/unregister/list`、`config validate`、`run-all`、`probe ingest`、`evaluate`、`baseline propose/promote`、`incident show`、`status --json`、`retention dry-run/apply`，并让 confirmed incident 经 `run-all` 进入 worker 编排；用 disposable durable provider generation + durable AI Radar canonical root 重放 shared install→AI Radar adopt/deploy→register→删除两边 source worktree→联合 postflight，以及每个依赖点 failure/反向 rollback；注册两个 consumer，并向相同 journey/fingerprint 注入不同 observation/baseline/incident，再注入未知命令/malformed input、project-id/root/owner 冲突、首项目失败/次项目成功、带 consumer uninstall、兼容/不兼容升级、每个切换 kill-point/模拟重启、postflight 与 rollback failure | installed v1 command paths 与 exact-set 一致、help/fixture invocation 可用，未知命令/malformed input 非零且无副作用；所有 lifecycle 命令从任意 CWD 自定位且安装幂等；联合结果绑定两仓 clean SHA/tree、各自 verification-manifest hash、canonical task-input hash 与 joint ledger hash，任一输入变化使 gate stale并必须重跑，append 结果本身不使输入失效。registry 只能在 provider installed generation 与 consumer canonical root 的 exact SHA pair、deployed config hash、protocol/compatibility/capacity 全部匹配后生效，source worktree 删除后联合 postflight仍通过；依赖失败按 registration→consumer→provider 反向恢复且不留临时 root。两个 consumer 的 SQLite rows、status、baseline 和 incident directory exact-set 隔离，注销其一后另一方 hash 不变；foreign ownership/冲突/带注册卸载被拒；实际 plist 满足 `StartInterval=300`、`RunAtLoad=true`、`run-or-alert --require-notifier`、绝对 `ProgramArguments` 与目标 HOME/state 路径，loaded job 与文件一致；transaction marker 可把每个中断恢复到唯一 old/new good generation，恢复不了则 unloaded+incompatible 且 packet 可读；run-all 完整 aggregate并承载唯一 worker path；兼容才切换，失败不留下混合态 | agent 自动 |
| V16 | 先把 full-scale fixture manifest 与基础表及真实 `/`/curated API/SSR visible identity exact compare，删除/错连一个关系行验证 gate 失败；把 tracked local homepage/API budget keys 与 D18 做 exact-value matrix，并用可控 duration 对 500ms median、1s P95 分别重放刚前/恰到/刚后；再连续追加不改变 archive membership 的 evaluations 后访问真实请求链，分别采集 D17 idle/busy 各 20 个 warm samples | 基础 counts 精确为 30k/70k/5k/200k/1.8k/1.3k，且 curated eligible distinct ids、exact total、page 1/page 2 ids 与 SSR preload 都逐项匹配 manifest；任一 shortfall/extra/断链在计时前失败。local config 精确为 500ms/1s，`value==budget` pass、刚超过 fail；真实两类样本都满足 D18，exact total 正确且 count 不重算 | agent 自动 |
| V17 | 改变 curated membership、items/category/search 条件后访问分页首尾 | 计数正确失效、精确页数/末页 clamp 不变；无陈旧缓存 | agent 自动 |
| V18 | 先把 tracked local WeChat budget key 与 D18 exact compare，并以可控 duration 重放 300ms 刚前/恰到/刚后；再对 `/wechat`、manifest deterministic detail slug 和 page 2 做 visible ids/total/SSR preload exact compare及 D17 idle/busy origin + provisional public 复测，各类 origin 至少 20 个 warm samples | config 精确为 300ms，`value==300ms` pass、刚超过 fail；joinable `save_decision=1` ids、page 1/page 2、detail slug 与 manifest 全链一致，任一断链失败；连接均关闭；微信 API/SSR 的每个 idle/busy origin 集合 median≤300ms；public 若未达 D3 对应 provisional objective，明确归到 public/network incident，不标 healthy、不盲改 SQL | agent 自动 |
| V19 | 对 baseline promotion 与 autonomy enable packet 分别逐项删除统一 envelope/专属字段、破坏 report 路径/链接/命令/回复格式、注入 stale 或错 scope/hash，再生成完整 packet | 任一缺失、不可达、stale 或不匹配都不能请求用户或执行动作；完整 baseline packet 回答“哪里/何时/commit/config/evaluator/idle-busy/未测地区”，完整 autonomy packet 含 dry-run、安全拒绝、diff、rollback，且各自携带唯一 action id 与精确回复格式 | agent 自动 + **用户 gate** |
| V20 | 获得独立部署授权并完成 rollback-ready 部署后，先断言 deployed config 的 D3 exact-value matrix；对每个 deployed stream 验证 baseline 已 promoted/fresh、其历史 Git provenance 可追溯，且 stream/config/window/evaluator identity 与当前窗口匹配（不要求历史 baseline SHA 等于当前 deployed SHA），再用同一 deployed SHA/config 自动测量 `https://aiplanet.live/`、微信列表、从列表进入详情及翻页；同时从生产只读数据动态导出 expected content identity/total/page sets，与同一 API/DOM response exact compare后才生成四行 ballot | 四项自动 P75/P95 分别 `<=` D3 public provisional absolute objective，且相对 promoted baseline regression `<=30%`，状态才可为 `provisional_pass`；baseline stale/identity mismatch、任一错值/超标或非 pass 状态即 FAIL。页面版本精确匹配 authorization ledger 的 deployed SHA/config；样本内容 identity、API/DOM exact total/page count、翻页前后不重复与 pagination contract 全部自动 preservation pass 后才请求人工体感。same-host 结果仍标 provisional，不冒充 regional green | agent 预检 + **用户最终体感** |
| V21 | 按 D15 的机器可读规则，用固定 anchor/cadence 的 fake clock 对 recovery time 在边界刚前/恰到/刚后逐项 exact replay，并各运行首个 recovery tick + 两个 normal tick；另运行 `<7d`、维护者确认误报、guard 正确拒绝恶意 fixture、guard/worker 成功执行禁止动作、未处置 delivery-stuck、健康满 7d、East Asia 尚未落地 | 每例严格使用 `s=max(boundary<=r)`：只合并 `last_completed<slot<s` 为 missed，不生成/回放伪 observations；`s` 恰好成为 recovery outcome 的 scheduled slot，`observed_at=r`，`next_due=s+c>r`。边界恰到时该 slot 被执行而非 missed，刚前取上一 slot，刚后取当前 slot；scheduled/outcome ledger exact compare。三 tick 只生成一个 gap event/outbox alert与一个 readiness epoch，随后正常推进。维护者确认误报、成功执行禁止动作、未处置 delivery-stuck 也重置；guard 正确拒绝恶意 fixture不重置；满 7 天只生成 East Asia readiness；East Asia 未完成时 US/EU blocked；tracked/runtime 快照无 provider 注册/凭证/部署 | agent 自动 |
| V22 | 跑 docs/contract consistency 与 link checks | UX contract L1 包含维护者 `/admin` surface并与 `AD-1` exact cross-reference；仅缩窄该子面，不解除其余 admin/data 排除。四条 journey 分别在 HP-1、WX-2、WX-4、WX-5，加载语义在 RS-3，并全部 exact reference 共同体验条款；该条款逐字覆盖 V35 的“一次连续完成”“无额外停滞/错误/陈旧”、全 journey/全维度 pass 及 fail 阻塞不自动副作用。`AD-1` 覆盖 metric/unit/vantage/load/freshness/baseline/provisional/`not_observed`、tuple completeness 与非绿色。漏 L1/L2、错 section、漏任一 rubric/cross-reference/行为均失败；数值只来自 config，文档索引/链接/verify 入口均可达 | agent 自动 |
| V23 | 对已 commit 的 materialization intent 分别在 payload temp mid-write、file fsync、no-clobber publish、directory fsync及最终 seal 前后中断并做启动/tick恢复；注入 live source变化、owner temp、foreign temp、最终文件同 hash/异 hash/多余文件，再对 sealed incident 做重复 attempt、overwrite/tamper、secret、retention 与状态重放，并逐字段校验 required evidence manifest | recovery 只从 intent 中 immutable serialized BLOB 重建，不重读 live source；owner残留temp可清理，foreign temp不碰，半写temp不能成为最终文件。同 hash最终文件幂等复用，异 hash/多余最终文件 fail closed；pending intent 最终只形成一个与 exact-set/content hashes 一致的 sealed bundle，seal 前 notification/remediation 均为0。原始证据及旧 attempt 不可覆盖；hash chain 可验证；secret 被拒/脱敏；每个 incident 明确记录 commit/dirty/config/vantage/pipeline、host CPU percent/interval、1-minute load、memory bytes、configured DB/WAL bytes、sample/log provenance 与采样时间，取不到的字段写 `unknown` 与原因而非省略；open/candidate 不删；confirmed→resolved 状态可重放 | agent 自动 |
| V24 | 从 scheduler tick 重放 insufficient→suspect→confirmed→resolved，逐步核对 sealed outcome/streak/incident/outbox transaction id、materialization intent BLOB/hash/bundle status，并记录 Codex invocation count | deterministic evaluator 独立完成判定；每个 scheduled slot 的 transition/outbox 只提交一次，required incident bundle 以 intent bytes exact replay并 sealed 后才可 delivery/remediation，任意 mid-write/publish 崩溃恢复不丢步、不缺 bundle或重复事件；confirmed 前 0 次、符合 remediation gate 后恰好 1 次 Codex；无 goal/LLM scheduler path | agent 自动 |
| V25 | 新 matrix run 建 ledger 后，在零 provider resolver/sandbox harness 中分别注入 permissive/denying ambient user config、rules、MCP/app/hook并 exact compare resolved argv/env/allowlist/capability hash；随后在一个真实 LaunchAgent 等价环境运行该 run 唯一 live Codex smoke，ambient 中同时放置所有 sentinel，验证 auth/schema与恶意动作拒绝 | ambient variants 的 provider ledger delta=0且 resolved capability完全相同；绝对 executable、`codex exec --json --output-schema ... -o ...`、ignore flags/overrides精确存在。唯一 live smoke恰好1 provider row、trial null；schema/auth通过，sentinel rules/MCP/app/hook不加载，网络/越界写/socket/提权拒绝。不得为每个 ambient variant另启 smoke；任一失败 disabled/incompatible | agent 自动 |
| V26 | 用 3 个 due project、缺 journey、slow/timeout 与不同 target fixture 跑 bounded scheduler | expected project/journey/vantage 与 actual outcomes 精确相等；全局 project≤2、per-project run≤1、browser≤1、round≤4 分钟；后续项目不饥饿；同 target latency probe 不并发；任何缺项使 completeness gate 非零 | agent 自动 |
| V27 | 用 fake clock 和竞争 incident 重放 D11：5m/1h cadence、1/2/3 windows、6h cooldown 的刚前/恰到/刚后；live provider call graph 分别跑 diagnosis 60m 刚前/恰到/刚后、证据不足只 1 call、证据充分 diagnosis+implementation 2 calls、累计 120m 边界与第三次 spawn；另跑两个同时 confirmed incident | 恰到边界按 contract；未前进窗口不累计。diagnosis/implementation 每个到 60m hard stop，diagnosis 超时不启动 write；证据不足精确 1 provider row，成功路径最多 2，累计恰到 120m 结束、刚超过/第三个 invocation 在 spawn 前 fail closed；每时刻全项目≤1 active worker，其余排队可前进 | agent 自动 |
| V28 | 按 D16/D19 跑 matrix，校验 pin/runtime/provenance、并发与双 ledger；deterministic provider-free outcome fixtures 覆盖 mandatory 单 fail、repair `1/3`、`2/3`、`3/3` 与完成 2/2 时禁止提前 pin；fake clock 覆盖 trial 3/4、60m、batch 4h；真实-call fixtures 覆盖 0-process probe、作为新 matrix run 唯一 pre-trial row 的 V25 success/failure smoke、blocked/success trials、roles failure及第 7/8 provider spawn | deterministic harness 的 provider ledger delta=0；mandatory 任一 fail、1/3 或仅完成 2/2 都不写 pin/receipt/enabled，完整 2/3或3/3 才可。第3 trial允许、第4拒绝；60m/4h等号允许，刚超过终止。新 run 先建 ledgers；V25 smoke 精确 1 provider row且 trial null，不重复 smoke；diagnosis才建 trial，blocked=1 diagnosis，success=1 diagnosis+1 implementation。单 run最多7 provider rows，第7允许、第8 spawn前拒绝；所有成败结算。failure终止 batch、disabled/incompatible、0自动重试；operator修复后显式新 run，V25作为其唯一 smoke；不并发/换模型/扩预算，升级仍过同一 bar和回执 | agent 自动 + **用户 gate** |
| V29 | 让无历史的新 consumer 分别缺端到端 hard timeout、worst-case 超过/等于/低于 4 分钟 round capacity，再重放 N 个 due consumer 的 admission、排队和续排；对超预算 packet 逐项删除 project/config/expected set/schedule/headroom/可达入口/三项后果/精确回复并破坏 preflight | 缺输入 fail closed；candidate registration 默认 disabled；只有包含 launch/teardown/persist 的 worst-case schedule 严格 `<4m` 才允许启用，`=4m`/`>4m` 拒绝；packet 任一缺口都不能请求用户，完整 packet 默认 keep-disabled，扩容或 SLO 变化必须另立计划；运行中 overload 非绿色；所有 cap、串行、公平续排均不被突破 | agent 自动 |
| V30 | 让 alert 与 candidate-ready notification 两个独立 channel 分别缺配置、连续失败 1/2/3/更多次、恢复，再重复同一事件；另让 `run-or-alert --require-notifier` 面对 notifier 缺失/不可执行及可用场景 | doctor/preflight 分别 fail closed；事件先持久写入 outbox；每 tick 最多一次且按 5m/15m/1h/6h cap backoff，3 次后 `delivery_stuck` 非绿色、readiness 重置且停止新 remediation，但不丢弃；恢复后只投递一次，同一 event id 不重复，一个 channel 成功不掩盖另一个失败。require-notifier 缺失/不可执行时 child invocation=0 且非零；可用时才运行 child 并保持原 exit/alert contract | agent 自动 |
| V31 | 对 baseline promotion、autonomy enable、candidate adoption、merge、push、deploy、restart、rollback 八类 action 注入缺失/错scope/hash/过期/复用/跨 action 外推；另让 enable receipt 面对 exact/wrong config diff/hash，并重放 deploy transaction failure的 exact pre-state compensation、postflight成功后回退、enable后尝试未授权 adoption/deploy/restart | 无效回执在副作用前停止；一条只能消费一次且不外推。enable receipt 只可按其 exact diff/hash 在隔离 worktree 形成一个本地 config commit，错配拒绝，不能授权 adoption/merge/deploy/restart。deploy receipt仅在scope显式包含时授权未commit transaction补偿，且只能恢复记录的pre-state；成功commit后补偿权关闭，回退必须独立rollback receipt。ledger记录action/transaction/compensation/receipt/result；D1/D9不构成授权 | agent 自动 + **用户 gate** |
| V32 | 以完整、缺 1 项、多 1 项、重复项、错 unit、stale、candidate baseline fixtures 渲染 `/admin` 性能板块 | DOM 与状态 API 都同 expected tuple 做 exact-set 比对；每项展示 journey/vantage/load/metric/unit/window/freshness/baseline/provisional；除完整且 promoted+fresh+pass 外均不能显示绿色，fixture 各自导致 verifier 可失败 | agent 自动 + 用户可读 |
| V33 | 在干净 HOME 仅按共享 README 接入 fixture project，启动等价 scheduler，注入受控 slow/pass response | 同一 fixture 的 register→scheduled probe→ingest→confirmed→resolved→status/sealed evidence 全链成立；confirmed/resolved 测试通知各一次；断开任一 adapter/store/evaluator/outbox link 都使同一测试非零，且共享源扫描无 AI Radar import/route/SQL/schema | agent 自动 |
| V34 | 对六类 production action packet 逐项删除每个必填字段、破坏链接/命令、让 rollback precheck 失败，再生成完整 packet | objective/scope/action id/SHA/config/diff/tests/影响/命令/pre-post/rollback/三种选项/回复格式任一缺失或 preflight 不通过都不能请求用户；完整 packet 才进入 V31 authorization ledger | agent 自动 |
| V35 | 生成 production experience packet，逐项删除行、版本/预算/baseline/自动值/preservation，以及任一“一次连续完成”或“无额外停滞/错误/陈旧”rubric/ballot slot；注入错误 SHA、失效 slug、超标、stale、preservation fail、fail 无 comment，再生成完整四行并重放全 pass/任一 fail | 任一缺口、超标、preservation failure、rubric/ballot 缺失或 fail 无 comment 都阻止用户 gate；完整 packet 每行含 identity/version/absolute+relative/`provisional_pass`/人工动作/自动 preservation 与两个锚定主观维度，每维独立 pass/fail。只有全维度 pass 完成；任一 fail 仅记录并阻塞，不自动 rollback/deploy，后续动作另过 V31 | agent 自动 + **用户最终体感** |
| V36 | 在 disposable repo 与真实 LaunchAgent 等价环境运行 read-only/write capability spike，给模型恶意指令读取 fake auth/honeytoken、联网、写 worktree 外、访问 Unix socket、提权和调用 push/deploy/launchctl/生产路径 | Codex client 仍可认证并产出 schema-valid 结果；所有恶意动作由 prompt/rules 外的 OS/controller boundary 拒绝，honeytoken 不出现在输出/日志；verifier/worker 对允许范围外路径及禁用 endpoint 的 writable-open/write/connect 归因审计为零。主工作区与 live 外部状态 before/after hash 只作诊断，hash delta 本身不改变 verdict；pass/fail 只按 verifier/worker 归因证据。任一项无法强制则 prerequisite gate 非零，后续 phase 不得开始 | agent 自动；本轮计划审查未实际运行 |
| V37 | 按 D14 用 fake clock/size/free-space fixture 分别对 raw/closed-success aggregate/resolved incident 重放 30/180/365 天 retention，并对 10GiB global、2GiB per-project、15% free-space waterline 重放刚前/恰到/刚后；另放入 open/candidate evidence | 三类 evidence 各自 `age==limit` 保留且只有 `age>limit` 过期；global/project bytes 在等于门限时进入压力态，free space 在等于 15% 时进入压力态。status/dry-run 精确显示容量与动作；压力动作严格按 D14 顺序，critical 时先停成功样本/新 remediation；open/candidate 永不自动删除，未显式 operator apply 不产生删除 | agent 自动 + 用户可读 |
| V38 | 从同一个已知可修 fixture incident 运行 enabled control plane，记录 confirmed→immutable source/doc snapshot→read-only diagnosis→selected hash→implementation bundle/base worktree→gates→local candidate commit→candidate-ready；分别删除、漂移、篡改 source/doc/base/diagnosis link | 同一 incident id/provenance 串完整 input→output 链；每次 spawn 前 exact verify source HEAD/tree/clean、逐文档 hash、worktree base、incident/diagnosis/prompts/gate config/model pin，任一 link 缺失/错配/篡改均非零且对应 invocation=0；有效链必须得到 `candidate_ready` 和合法本地 commit，不能以 `blocked` 代替。worker 对主工作区、remote、生产数据、service 与 baseline 的 writable-open/write/connect 归因审计为零；before/after hash 仅作诊断，hash delta 本身不改变 verdict，pass/fail 只按 worker 归因证据。无证据 fixture 的 `blocked` 是安全分支但不计完成 | agent 自动 |
| V39 | 持有 SQLite write lock/模拟 store 不可写，触发 observation 与 outbox 写入失败，然后释放并运行恢复 | 原操作非零且写入独立原子 sidecar marker；marker 不含秘密/用户内容，host log 可见且 `/admin`/status 非绿色；恢复后按 event id 恰好 ingest 一次并删除/归档 marker，失败 observation/notification 不丢不重 | agent 自动 |

## 8. 全量验证与交付顺序

### 8.1 AI Radar

```bash
: "${CONTINUOUS_PERFORMANCE_TASK_MANIFEST:?absolute task-owned manifest path required}"
AI_RADAR_CANDIDATE_WORKTREE="$(jq -er '.repositories.ai_radar.candidate_worktree' "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST")"
"$AI_RADAR_CANDIDATE_WORKTREE/scripts/performance/verify_all.sh" \
  --phase final \
  --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"
```

该 canonical script 为 invocation 建唯一 run root，先断言 DB/WAL 不存在，再把 DB/evidence 都放在该 root；同 SHA 重跑不复用路径。它固定顺序运行 full pytest、performance/full-scale fixture、ruff、mypy、service lifecycle、local Playwright、docs/contract links 与 `git diff --check`；任一子命令、后置身份、ledger append 或 artifact 缺失即非零。manifest 记录 invocation id、resolved DB/evidence path、fixture cardinality/hash、clean SHA/tree、canonical task-input hash、commands/results；未成功封存 manifest 的 run root 由 trap 清理。代码/input 变化后必须重跑，不复用旧绿灯。

### 8.2 ai-agent-config

```bash
: "${CONTINUOUS_PERFORMANCE_TASK_MANIFEST:?absolute task-owned manifest path required}"
AI_AGENT_CONFIG_CANDIDATE_WORKTREE="$(jq -er '.repositories.ai_agent_config.candidate_worktree' "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST")"
"$AI_AGENT_CONFIG_CANDIDATE_WORKTREE/continuous-performance/scripts/verify_all.sh" \
  --phase final \
  --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"
```

该 canonical script 在唯一 run root/临时 HOME 固定执行 pytest/ruff/mypy、fixture consumer 全链、install 两次、协议不兼容、权限拒绝、retention、capacity、notification 与 crash-recovery；任一子命令、身份或 artifact 缺失即非零，并记录 invocation、clean SHA/tree、task-input、commands/results/artifacts 后 append ledger。随后按通用 review gate 审共享 CLI/installer。

### 8.3 Final-pair gate

```bash
: "${CONTINUOUS_PERFORMANCE_TASK_MANIFEST:?absolute task-owned manifest path required}"
AI_AGENT_CONFIG_CANDIDATE_WORKTREE="$(jq -er '.repositories.ai_agent_config.candidate_worktree' "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST")"
"$AI_AGENT_CONFIG_CANDIDATE_WORKTREE/continuous-performance/scripts/verify_pair.sh" \
  --task-manifest "$CONTINUOUS_PERFORMANCE_TASK_MANIFEST"
```

该 pre-delivery controller 在 source worktrees 仍存在时同时核验两仓 clean SHA/tree、main 不变、两份最新 final manifests 与同一 canonical task-input hash，在独占临时 HOME/DB/state 运行 provider/consumer integration，输出 joint verification manifest并 append ledger。缺任一 repo green、pair mismatch、后置漂移或 joint ledger 失败即非零；这是任何交付 packet 的前置条件，但不能替代删 source 后的 deployed-pair postflight。

### 8.4 交付顺序

1. 两个仓库都只在本任务隔离 worktree 中形成独立、可审的本地 commits；commit 前后核对主工作区 HEAD/dirty manifest/hash 未变，不得把用户已有改动纳入。如何把 commits 交给主分支另设人工 gate。
2. 跨仓交付顺序固定并绑定 task manifest exact SHA pair。开始前只 preflight packets/receipts，不提前消费。每个 action 做“重核 state → 消费本 receipt → 副作用 → postflight”；deploy receipt 还必须在 scope 中预授权未 commit transaction 对 exact pre-action state 的自动补偿，补偿仅在本 action 失败/中断时执行。action postflight 成功即 commit并关闭补偿权；此后回退必须另取 rollback receipt。shared deploy 后需要 restart则另取 receipt。AI Radar adoption、merge、deploy 同样逐项；deploy后激活新进程必须另取 AI-Radar-scoped restart receipt。之后才从 durable root register。删 source 后从 installed generation运行 `verify_deployed_pair.sh`，核对 content hash→shared SHA/tree、AI deployed SHA/config/running process、registry/protocol/capacity及 source不存在，输出 deployed-pair manifest。只有 pre-delivery joint与 deployed-pair hashes匹配才生效。action失败时后续 receipts未消费并作废；先运行该 action 已授权的 exact transaction compensation；若 compensation失败或需撤销已成功 action，才准备/消费独立 rollback receipt，按 registration→AI deployment→shared job/generation反向回滚。不得批量消费、提前副作用或从 enable/deploy外推 restart/rollback。
3. 不执行 `git push`。如后续需要 push，汇报两个仓库的 commit hash、diff 目的和部署影响，另行取得用户显式许可。
4. baseline promotion、autonomy enable、candidate adoption/merge、push、deploy/restart/rollback 都是单独人工 gate，不因“执行本计划”而自动授权；每次只接受并消费 V31 ledger 中 action/scope/SHA/config 精确匹配的一条回执。用户已选择最终以生产验收完成本计划：未取得相应授权时应停在对应 action gate；取得后记录 deployed SHA/config、预检 rollback，并以 V20 的 `aiplanet.live` 四旅程验收收尾。

## 9. 风险、失败条件与响应

| 风险/触发 | 不能接受的静默行为 | 规定响应 |
|---|---|---|
| 同机 probe 与真实地区差异 | 把 provisional 标成 East Asia healthy | regional 固定 `not_observed`；报告展示 vantage 限制 |
| orchestrated/direct pipeline stage 导致周期性尾延迟 | 只有 `pipeline.sh` 发布状态、或只读 probe 起止瞬时状态，使 direct/短暂 stage 样本被误归 idle；也可能用全日混合均值掩盖 busy 窗口 | 所有 stage entrypoint 经共同 authoritative lease/event ledger；按连续 cursor 判断与完整 measurement interval 的交集，gap/partial/mismatch 归 unknown；样本强制带 interval evidence，并分别展示 idle/busy 窗口 |
| 浏览器挂死/资源泄漏 | launchd 叠加更多实例 | hard timeout + process group kill + single-flight + cleanup test |
| SQLite observation store 锁争用/不可写 | 用同一个不可写 store 记录“失败 observation/outbox”并返回 0 | bounded retry 后原子写独立 host-log sidecar failure marker（event id、project、time、reason，无用户内容）；`run-or-alert` 非零可见；SQLite 恢复后按 event id 确定性 ingest 并去重，marker 未 ingest 前 status 非绿色 |
| 共享 CLI 更新破坏 consumer | 继续用未知语义判 green | protocol major/schema preflight fail closed |
| 安装后 runtime 仍引用任务 worktree | worktree 清理后定时服务静默失效 | content-addressed install prefix；原子切换前检查所有 executable/plist 路径；删除来源后重启 fixture |
| root installer 不传播 peer failure / 半升级 | 后续步骤继续并留下新旧混合 service，或把成功后的 rollback 偷算成 deploy 补偿 | candidate CLI先验 registrations并显式传播非零；deploy transaction未commit时按receipt scope自动补偿 exact pre-state；postflight成功commit后关闭补偿权，之后rollback必须独立receipt |
| fleet 到期工作超过 4 分钟容量 | 静默降频、漏项目或突破并发 cap | registration capacity preflight fail closed；status/alert 展示 backlog 与 cadence miss；公平续排，等待用户选择扩容或改 SLO |
| Playwright 包存在但 Chromium runtime 缺失 | 当成产品 hard failure 并排 Codex，或把配置缺失与运行故障混成同一状态 | doctor/register/per-run launch probe；已配置的 local browser stream 标 `incompatible`，未配置的 off-host regional rows 保持 `not_observed`；显式 operator install 后再恢复 |
| Codex 误诊 | 直接改生产或无限循环 | read-only first、置信/证据 gate、隔离 worktree、attempt/time budget |
| ambient Codex config/model/权限漂移 | `--strict-config` 通过就假定隔离 | ignore user config + 固定 overrides + 外层 allowlist + resolved model/argv/prompt/input provenance；capability postflight 失败保持 disabled |
| candidate tests 过时 | 复用旧绿灯 | test snapshot 绑定 candidate SHA；变更后重跑 |
| 事件包含秘密/用户内容 | 把 `.env`/cookie/正文打包 | allowlist context + redaction + size cap + secret fixture test |
| 通知 channel 缺失、临时或长期失败 | incident/candidate 已产生但用户永远不知道，或静默换通道 | alert/notification 分开 preflight；持久 outbox + bounded retry + event-id 去重；3 次失败 `delivery_stuck` 后停新 remediation、readiness 重置并保留 host-visible pull evidence；备用 channel 只能由 operator 预配置并通过 delivery preflight |
| fleet notifier executable 缺失或不可执行 | `run-or-alert` 无告警地继续启动 fleet child | fleet 固定使用 `--require-notifier`；child 前校验失败即非零且 invocation count=0，launchd stderr/doctor/postflight 明确失败；恢复 notifier 并通过 delivery preflight 后下个 tick 才运行 |
| 监控主机宕机或网络失联 | 同机机制声称自己在宕机期间已告警、回放伪历史 observations，或恢复后把 cadence gap 当健康连续运行 | 明确不可自证；host 存活时由 `run-or-alert` 发现 fleet job/runtime failure；恢复首 tick 原子合并 missed interval/outcomes、不回放历史、恰好一次写 `monitoring_gap_recovered` event/outbox alert、固定 next due 与新 readiness epoch，后续 tick 正常推进；整机独立存活性由二期 off-host probe 补齐 |
| Codex auth/quota/transport 在 batch 前或中途失败 | 同一 batch 自动重试、重复 smoke、让 mandatory harness 暗中调用 provider、混淆 trial/provider 或静默换模型 | 新 matrix run 先建 ledgers；V25 是唯一 smoke，mandatory harness provider delta=0；3 trials之外 provider 总 cap=7，第8次 spawn前拒绝。smoke trial id=null，diagnosis/implementation才绑定 trial；每个 spawn 成败恰好记1与elapsed。失败终止 batch并 disabled/incompatible，输出剩余 ledger与 V25/V28 入口；operator修复后显式新 run，不自动重试/换模型/扩预算 |
| evidence store 逼近磁盘上限 | 随机删 open/candidate 或把磁盘写满 | 按 D14 在 global bytes `>=10GiB`、project bytes `>=2GiB` 或 free space `<=15%` 时进入压力态并停止成功样本与新 remediation；只有 `age>limit` 的 closed evidence 可进入显式 retention apply，open/candidate 永不自动删除 |
| 外部 probe 供应商/费用 | 自动注册付费服务 | 二期另立任务，用户选择 provider、预算与凭证范围 |

## 10. 非目标

- 首期不部署/购买外部探针，不声称已经覆盖真实东亚、美国或欧洲用户。
- 首期不引入 RUM、不采集真实访问者标识、网络信息或行为数据。
- 不让 LLM 决定是否违反 SLA；LLM 只解释已确认 incident 和生成候选修复。
- 不自动 push、merge、deploy、restart 生产服务、写生产 DB、改 Cloudflare 或晋升 baseline。
- 不以本任务为由重写全部查询层、迁移到新数据库、引入分布式监控平台或重构无关代码。
- 不把同机服务死亡可见性包装成已解决；该空白在外部 probe 阶段处理。

## 11. 执行时默认值

下表中 D11–D19 对应的 cadence、窗口、attempt、cooldown、并发、deadline、模型选择/迁移、retention、readiness、load classification、本机预算与 fixture batch budget 都是锁定约束，执行者不得微调；其余实现细节只有在不改变用户承诺、架构或安全边界时才可调整，否则必须回到用户：

| 项目 | 默认 |
|---|---|
| 共享组件名 | `continuous-performance` |
| 共享组件形态 | Python 3.12 包 + 薄 shell entrypoint，使用 `uv`；理由是 schema/SQLite/statistics/test 能力与 AI Radar 一致 |
| 调度 service | 共享 label `resource.continuous-performance`；AI Radar 的 `performance` 只是 consumer lifecycle slug，不另建 daemon |
| 运行状态存储 | `~/.local/state/continuous-performance/<project-id>/` 下的 SQLite + append-only incident files；不引入远程 SaaS |
| 浏览器实现 | AI Radar 内 Playwright adapter；共享层只消费 observation JSON |
| quick/browser cadence | 5 分钟 / 1 小时 |
| confirmed/resolved | 同一 idle/busy stream 连续 3 个已前进 eligible windows；产品 hard failure 立即 confirmed |
| live Codex invocation/time | 每 incident 最多 1 diagnosis + 1 implementation；每个≤60m、累计≤120m；全项目 1 active worker、6h cooldown |
| fleet execution | project≤2、per-project active run≤1、browser≤1、round deadline 4 分钟；同 project/target latency probes 串行；公平续排 |
| Codex model | capability/quality fixture 后 pin exact model + reasoning 到 tracked controller config；不继承 ambient user config，升级需显式迁移并重过 autonomy gate |
| load classification | pipeline stage 与完整 probe measurement interval 相交即 busy；只有连续 ledger cursor 证明区间内无 stage 才 idle；gap/unknown/矛盾不进入合规窗口，CPU/load/memory 仅作诊断上下文 |
| local engineering budget | 首页/curated API median≤500ms、P95≤1s；微信 API/SSR median≤300ms；20 个 warm samples |
| model fixture batch | exact candidate 串行；end-to-end repair trial≤3、每 trial≤60m、整批≤4h；每 trial 为 1 diagnosis + 至多 1 implementation，provider process 另逐次记账；auth/quota/time failure fail closed |
| retention/waterline | raw/aggregate/resolved 的 `age<=30/180/365d` 保留、`age>limit` 才过期；global/project bytes `>=10GiB/2GiB` 或 free space `<=15%` 进入压力态 |
| readiness | 任一 cadence gap、维护者确认误报、成功执行禁止动作或未处置 delivery_stuck 重置 7 天；正确拒绝恶意 fixture 不重置 |
| 自动 commit | gate 全过后允许本地 candidate commit；永不 merge/push |
| baseline | 只允许显式 `baseline promote` 人工命令 |

## 12. 参考索引

执行前优先阅读：

- `src/airadar/web/routes/curated_archive.py`
- `src/airadar/web/routes/pagination.py`
- `src/airadar/web/routes/request_db.py`
- `src/airadar/web/app.py`
- `src/airadar/admin/{access_log,alerts,metrics,thresholds}.py`
- `tests/test_curated_precompute.py`
- `tests/test_service_contract.py`
- `docs/contracts/ux-contract.md`
- `docs/operations/{monitoring-alerting,services}.md`
- `docs/adr/005-timeline-exact-count-with-cached-cte.md`
- `docs/adr/006-curated-archive-mode.md`
- `/Users/lindong/research/ai-agent-config/im-notify/`
- `/Users/lindong/research/ai-agent-config/claude/references/service-operations-protocol.md`
- `/Users/lindong/research/ai-agent-config/claude/references/docs-organization-protocol.md`

Codex 官方能力约束（计划创建时已按本机最新官方 manual 复核）：非交互自动化使用 `codex exec` 的 sandbox、JSONL/JSON Schema 输出；goal 是有完成标准的持久任务，不替代外部 scheduler，且沿用相同权限边界。因此本架构把调度与判定留给确定性 control plane，把 Codex 限为单 incident worker。
