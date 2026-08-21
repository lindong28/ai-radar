> **Archive status**: 已归档，执行完成（`state.md` 中 U2–U6 各单元全部 done）。执行过程产物 `state.md` / `journal.md`、以及同目录的 `baseline.patch` / `acceptance-evidence.md` / `pagination-diagnosis.md` / `cloudflare-cache-rule.md` / `crontab-backup-*.txt` 等取证附件按长任务协议不入档。
> 性能探针与退化告警的当前契约见 [ADR-011](../../adr/011-perf-idle-only-probing.md)（本 plan 的 busy 采样与降级 gate 已被其退休）与 [operations/monitoring-alerting.md](../../operations/monitoring-alerting.md)；边缘缓存现状见 [architecture.md](../../architecture.md)「公开分页的边缘缓存」。以下为原 plan 正文，未修改。

# aiplanet.live 持续性能保障（精简版）

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`

## 执行修正案（2026-07-18，优先于下文原文）

本 plan 经 review-plan 审查为 NOT CLEAN（11 findings，V-01..V-11）后，执行现实发生重大变化，按以下修正执行；冲突时本节优先：

1. **Phase 1 设计被取代（V-01 致命伤已规避）**：不实现原文 §4「移除 max_eval_id」方案（V-01 证明其在 category COUNT 下有正确性 bug）。改为**采纳** codex candidate（`ai-radar-continuous-performance-20260715` @2e1cac4）中已验证的 generation-triggers 方案：migration 013 双计数器 + `_curated_data_version` 读 generation。隔离验证证据：`./validation-evidence.md`（机制确证 / 生产成本参照 19s→0.2s / V-01 对抗用例通过 / 26+141 tests）。
2. **部署已获用户显式授权并执行**（2026-07-18 21:57）：commit `7a75c28` 采纳 32 文件（web 读路径 + 被动观测 + 测试；**排除** candidate 的 cli.py 接线与 pipeline/deploy 脚本层——该层有 4 个未闭环 HIGH finding）。`launchctl kickstart -k` 重启，healthz 200，Server-Timing 生效，total 4859=SQL 对照。回滚锚：`git revert 7a75c28` + 重启。
3. **修正后单元结构**：U1 采纳+部署（本节，收尾=busy 窗口验证）→ U2 监控接线（Phase 2；已有被动资产：performance/ 模块、admin 面板、timing middleware——需接 handler[review MEDIUM]、探针 CLI、crontab 样例、告警集成；V-02 的窗口/预算参数在此落地）→ U3 候选修复 worker（Phase 3；必须先落 V-05 的 fail-closed invocation contract：显式 sandbox/网络/写域约束，不得以 worktree 当安全边界）→ U4 验收 + ux-contract + docs 收尾。
4. **Defaulted Decisions（reviewer D-01..D-05 的落地）**：D-01→B（同机探针只标 provisional，不宣称 East Asia SLO）；D-02→A（worker 须有一个代表性可修 fixture 端到端出 candidate）；D-03→现实变体（serve 跑主 checkout，部署=commit 到 main+重启；release-worktree 载体留 U2+ 评估）；D-04→保持部署 A1+快照回滚+review gate（用户在场亲自授权，A2 对抗审查按 proportionality 不加）；D-05→A（体感为非阻塞备注，客观旅程 gate 决定交付）。用户可在 handoff 否决。
5. **review findings 路由**：V-01 已由采纳方案消解；V-05→U3 硬约束；V-07→部署已按「等锁+kickstart+revert 回滚」执行（TOCTOU 残余风险接受：单机+在场）；V-02→U2；V-06→U3（D-02）；V-03→U4 验收措辞；V-04/V-08/V-09/V-10/V-11→U2-U4 各自吸收；review gate 两个非阻断 finding（timing middleware 死管线 MEDIUM、admin 容错 LOW）→U2。


## 输入与定位

- **本 plan 是 review / 实施的唯一入口。**
- **历史参照（不沿用）**：`plans/20260715-continuous-performance-loop/` 是同目标的旧 plan。它把单机部署做成了约 4460 行的分布式部署授权控制平面（exactly-once / capability-authz / compensation / 跨项目 fleet 调度）。用户判定其相对「单机、单操作者、非并发、人工在场」的真实部署形态**严重过度工程**。本 plan 从零重规划，只解决三个核心目标，复杂度按真实 stakes 校准。
- **本 plan 覆盖**：L1（产物+使用方式+范围）、取舍偏好、rigor 向量、L2 用户视角 verify、L3 分阶段设计+内部 verify、Defaulted Decisions、Risks、UX 契约影响。
- **落点**：全部改动在产品仓 `/Users/lindong/research/ai-radar` 内，**不新建第二个仓库、不建跨项目共享层**（用户已拍板：只服务本站、内联）。

---

## 1. L1 — 最终产物 + 使用方式 + 范围

### 产物（三件，服务同一个使用者=站长本人）

1. **首页/微信性能修复**：消除已复现的首页 ~12s 慢与微信 SSR 连接未关闭，落到本机工程预算。
2. **旅程性能监控**：一个定时探针，测四条用户旅程延迟、区分 pipeline idle/busy、退化时告警+留证。复用现有 `admin/alerts` 状态机与 `tests/playwright` 探针，不另起系统。
3. **确认退化后的候选修复 worker**：确定性判定确认某旅程持续退化后，自动拉起**一个** agent，在 git worktree 隔离里只读诊断 + 生成**本地候选 commit**，推给站长审。**worker 绝不 push / deploy / 改生产数据 / 碰主工作区。**

### 使用方式（决定设计深度）

使用者=站长本人（+ 其 agent），单机、单操作者、非并发、人工在场。产物用来：(a) 让 aiplanet.live 首页/微信旅程实测变快；(b) 以后退化时被动收到告警+诊断证据，手动决定修不修；(c) 退化时拿到一个现成的候选修复 commit，审阅后**由站长亲自授权部署**。

**关键推论**：因为「谁能在生产上动手」= 站长本人一次性、在场、非并发的操作，部署授权**不需要**能力令牌 / exactly-once / 补偿控制平面——站长的手动确认**就是**授权本身。部署的安全性靠「部署前 snapshot + 失败可回滚 + 人工在场」保证，而非分布式一致性机制。

### 范围与约束

- **做**：性能修复（3 处）、四旅程探针+判定+告警+留证、候选修复 worker、部署脚本+旅程验收。
- **不做**：跨项目通用 CLId / 协议抽象 / fleet 调度（YAGNI，只一个站）；exactly-once / capability-authz / compensation 部署控制平面（over-rigor）；seatbelt 沙箱隔离 worker（worktree 隔离已足够，见 §5）；自动 push / 自动 deploy（授权边界）；外部地区探针（同机探针即可，旧 plan 也 defer 了）。
- **硬约束**：不破坏 aiplanet.live 现网可用；不覆盖/清理主工作区既有 dirty/untracked；性能修复不牺牲精确总数与分页语义。

---

## 2. 取舍偏好 + rigor 向量

### 取舍偏好

复杂度匹配 ≫ 完备的分布式正确性；复用现有基础设施 ≫ 新建系统；站长在场手动授权 ≫ 无人值守自治。当「更强机制」与「单机单人 stakes」冲突时，一律取匹配 stakes 的最小机制。

### rigor 向量（用户已复核确认）

- **默认 `(A0, V1)`**：A0=改动可逆本地（单机、git 可回滚）；V1=每个改行为的 unit 有测试 + 单 reviewer。
- **per-phase override**：仅 **Phase 4（部署到生产）override 到 `A1`**——落地前把 candidate 精确绑定到被部署的 commit，并做部署前后旅程验收。
- **label**：`standard`（= max(A,V) 级别）。
- **两轴理由**：R（反转成本）——改代码/新增旁路监控/单机重启均可逆，仅生产部署有轻微 blast radius 故 A1；G（回归容忍）——影响真实用户的读路径与部署，回归靠生产规模 fixture + 四旅程探针捕获，故 V1，无零容忍资金/安全面故不上 V2。
- **proportionality 依据**：`rigor-tiers.md` 的 proportionality invariant 是对称的——对 `(A0/A1)` 对象施加旧 plan 的 `(A2,V2)`（对抗审查 / exactly-once / 逐 unit 全矩阵）本身即 over-rigor finding。本 plan 据此拒绝旧 plan 的机制层级。

---

## 3. L2 — 用户视角 verify（独立于实现，implementer-executable）

> 站长视角「算交付完成」的可观测条件。命令+预期输出 / subagent 模拟 / 探针分数断言形式。人机边界已标。

### V-A 性能修复（Phase 1）—— agent 可独立

1. **首页总数缓存不再被无关写入失效**（核心根因）：写生产规模 fixture（curated_items/items/item_evaluations 达到接近生产的量级），构造回归测试——先请求首页归档页触发一次 COUNT 计算，再模拟一次 pipeline evaluation 写入（只插 `item_evaluations`，不改 curated/items 成员），再请求同一页：断言**第二次不重算 COUNT**（命中缓存 / compute 调用计数不增）。预期：修复前该测试 RED（缓存失效、重算），修复后 GREEN。
2. **精确总数与分页语义不变**：同一 fixture 下，修复前后首页/归档 API 返回的 `total`、页数、每页成员**逐字节一致**（快照对照）。断言无差异。
3. **微信 SSR 连接关闭**：针对 `app.py` 的 `/wechat`、`/wechat/{slug}` 两处路由写测试，断言请求处理后 sqlite 连接被关闭（无泄漏——如通过连接计数 / mock 的 close 断言 / `conn_from_request` 路径覆盖）。预期修复前 RED（`with db.get_conn()` 不 close）、修复后 GREEN。
4. **本机工程预算**（受控环境，非现网）：生产规模 fixture 下，首页归档 COUNT 路径 median ≤500ms、P95 ≤1s（nearest-rank）；微信 SSR median ≤300ms。断言达标；不达标则报告 profile 头部热点。

### V-B 旅程监控（Phase 2）—— agent 可独立

5. **四条旅程可被测量**：探针命令跑一次，输出四条旅程（homepage.first_card / wechat.list.first_card / wechat.detail.readable / wechat.pagination.settle）各自的延迟毫秒数 + 该次采样的 `load_class`（idle/busy/unknown）。断言四条都有数值、load_class 取值合法。
6. **idle/busy 分类正确**：构造「`.pipeline.lock/pid` 存在且 pid 存活」→ 断言样本标 `busy`；「锁不存在」→ 标 `idle`；「锁存在但 pid 已死 / 无法判定」→ 标 `unknown` 且不进合规窗口。三情形各一个断言。
7. **退化触发告警 + 留证**：注入一条超阈值的合成样本序列（连续 N 个已前进窗口违规），断言：(a) 走 `admin/alerts` 状态机产生一条 firing、经 im-notify 发送（复用 `send_alert_message`，可 mock transport 断言调用）；(b) 落一份诊断证据（该旅程近样本 + git SHA/dirty + load_class + 该时段 CPU/pipeline 状态）到证据目录；(c) 恢复后发 resolved。去抖/cooldown 复用现有语义。
8. **调度可挂载**：探针以 `airadar` CLI 子命令形式存在（与现有 `collect_alert_signals`/`run_alert_state_machine` 同构），可被一行 crontab 调用；plan 交付一条 crontab 样例，不自动写入用户 crontab（站长手动装）。

### V-C 候选修复 worker（Phase 3）—— agent 触发、产物由站长审

9. **确认退化后自动拉起单 worker**：模拟一条 confirmed 退化 incident，断言：拉起**恰好一个** worker（已有 active worker 时不重复拉起）；worker 在**独立 git worktree**（非主工作区）运行；有整体超时预算，超时/失败则中止并只告警留证，不留下悬挂状态。
10. **worker 动作边界（安全）**：断言 worker 全程**无** push / launchctl / deploy / 生产 DB 写 / 主工作区写入——用禁止动作的 fixture（尝试这些动作应被拒绝或根本无入口）验证。这是本 plan 唯一需要单 reviewer 重点看的安全面（V1）。
11. **候选交付**：worker 结束后，若形成候选，产出一个 worktree 内的本地 commit + 一段诊断摘要推给站长（im-notify 链接/路径）；断言候选 commit 存在且未进主分支、未部署。

### V-D 部署到生产 + 最终验收（Phase 4）—— 站长授权 gate + agent 自动兜底

12. **部署前兜底**（agent 自动）：部署脚本 dry-run 断言——记录当前 serve 指向的 commit（snapshot）；检测 `.pipeline.lock` 忙则等待写锁释放再重启（避免今天那次 restart 撞 pipeline 写锁导致端口 down 的事故）；健康门 `/api/v1/healthz` 通过才算成功。
13. **站长授权 gate**：部署/重启由站长亲自执行或显式批准后 agent 执行（不自动）。gate 前第 12 条已把主要风险（撞锁、健康、可回滚）自动兜住。
14. **最终验收（L1 的成功定义）**：部署后对现网 `https://aiplanet.live` 跑四条旅程真实探针，断言 East Asia 视角 homepage/list/detail **P95 ≤ 3s**、pagination **P95 ≤ 1.5s**（idle 与 busy 分别记录，busy 窗口不被 idle 稀释）。附站长人工体感确认（打开首页「感觉快了」）作为主观 gate。
15. **失败可回滚**：注入一次「部署后健康门失败」，断言脚本能回滚到 snapshot commit + 重启 + 恢复现网可用。

---

## 4. L3 — 分阶段设计 + 内部 verify

### Phase 1：性能修复 `(A0,V1)`

**1a. 首页 COUNT 缓存 over-invalidation**
- 文件：`src/airadar/web/routes/curated_archive.py`，`_curated_data_version()`（L22）。
- 根因确证：version tuple 含 `max_eval_id = MAX(id) FROM item_evaluations`。而 `_count_archive_items`（L50）的 COUNT 是 `items JOIN curated_items JOIN sources + where`，**不涉及 item_evaluations**。pipeline 每次 evaluation 写入 → `max_eval_id` 增 → `VersionedTotalCache`（`pagination.py`）version 变 → cache miss → 重算 ~1.79s。
- 修复：从 version 中**移除 `max_eval_id`**，并逐字段审视保留项——version 只应包含真正影响「归档成员集合 + 当前筛选(category/search/date)计数」的信号（curated_items 成员、items 成员、run 变化）。谨慎：移错会导致成员变化后总数不刷新（正确性 bug），故必须由 V-A#1+#2 双向证明（无关写入不失效 + 成员变化仍精确）。
- 内部 verify：unit test（compute 调用计数 spy）、类型不变（返回仍 `tuple`）、V-A#1/#2/#4。

**1b. 微信 SSR 连接泄漏**
- 文件：`src/airadar/web/app.py` L283（`/wechat`）、L298（`/wechat/{slug}`）——两处 inline SSR 路由用 `with db.get_conn(request.app.state.db_path)`，只管事务不 close。同项目 `routes/request_db.py:conn_from_request` 是正确 helper（data 路由已用）。
- 修复：两处改用 `conn_from_request(request)`。
- 内部 verify：V-A#3；确认渲染上下文不变（模板数据一致）。

### Phase 2：旅程性能监控 `(A0,V1)`（探针本身 V0，判定/告警 V1）

- **探针**：复用 `tests/playwright/conftest.py` 的浏览器基建，新增一个可独立跑的旅程探针模块（四条旅程测量点见 V-B#5）。同机测 `127.0.0.1:8000` origin 与 `https://aiplanet.live` public，结果标 provisional。
- **idle/busy 分类**：读 `.pipeline.lock/pid`（pipeline 运行时存在，含 pid）——存在且 pid 存活=busy、不存在=idle、存在但 pid 死/不可判=unknown。实现前 implementer 确认 `pipeline.sh` 对该锁的确切写/删语义（TODO-1）。
- **判定**：绝对预算（V-D#14 的 P95 阈值）+ 可选相对基线（比人工晋升基线退化 >30%）。窗口/连续前进确认/cooldown **复用 `admin/alerts` 状态机语义**（`evaluate_rules`/`run_alert_state_machine`/去抖）。
- **告警+留证**：复用 `send_alert_message`（im-notify）+ `alert-state.json` 状态持久化模式；证据落一个轻量目录（近样本 JSON + 环境快照），**不做**旧 plan 的 30/180/365 分级保留控制平面——留够诊断即可，超期靠一句「保留最近 K 天」的简单清理（Defaulted）。
- **调度**：新增 `airadar` CLI 子命令（HTTP 探针轻、browser 探针重，cadence 见 Defaulted），交付 crontab 样例，站长手动装。
- 内部 verify：V-B#5–#8；探针模块单测（分类三分支、阈值等号、空/NaN）。

### Phase 3：候选修复 worker `(A0,V1)`

- **触发**：Phase 2 判定 confirmed 退化 incident → 拉起 worker。单 active worker（有并发则跳过）、整体超时预算（Defaulted：≤60min）、失败只告警留证。
- **隔离**：`git worktree add` 一个临时 worktree（不碰主工作区），worker 在其中运行。**不用 seatbelt 沙箱**——见 §5 为什么 worktree 隔离在 `(A0,V1)`+单机+站长在场下已足够。
- **worker 实现**：非交互 `codex exec`（项目已在用 codex）或等价 headless agent，prompt 约束：先只读诊断，再在 worktree 内按最小修复生成本地候选 commit。
- **动作边界（安全，V1 单 reviewer 重点）**：worker 无 push/deploy/launchctl/生产 DB 写/主工作区写入的入口——靠 (a) worktree 隔离、(b) 不向 worker 传任何部署凭证/入口、(c) 一个禁止动作的测试 fixture 验证。**这是简单的「不给入口」，不是需要机制去对抗的威胁**（单机单人，无敌手）。
- **交付**：候选 commit + 诊断摘要 → im-notify 推站长。
- 内部 verify：V-C#9–#11。

### Phase 4：部署到生产 + 验收 `(A1,V1)`

- **部署脚本**（`(A1)`：candidate 精确绑定 commit）：输入一个被站长审过的候选 commit → 记录当前 serve commit（snapshot）→ 等 `.pipeline.lock` 释放 → 切换代码到候选 commit → `launchctl kickstart -k` 重启 `live.aiplanet.ai-radar.serve` → 健康门 `/api/v1/healthz` → 四旅程冒烟。任一步失败自动回滚到 snapshot commit + 重启。
- **授权**：站长亲自跑或显式批准（V-D#13）。**无自动部署**。
- **幂等/安全**：靠脚本可重入 + snapshot，不靠 exactly-once。撞锁问题靠「等锁释放」这一个 precondition 解决（直击今天的事故根因）。
- 内部 verify：V-D#12–#15；部署脚本 dry-run 单测（snapshot/等锁/健康门/回滚各一断言）。

---

## 5. 为什么不需要旧 plan 的机制（proportionality 说明，供 reviewer 审）

| 旧 plan 机制 | 为何在本 plan 不需要 |
|---|---|
| exactly-once / capability-authz / compensation 部署控制平面（authorization.py ~3274 行） | 部署是站长一次性、在场、非并发的手动操作；「谁能动手」= 站长本人，手动确认即授权。安全靠 snapshot+回滚+在场，不靠分布式一致性。 |
| seatbelt 沙箱隔离 codex worker（旧 plan V36 blocker） | worker 只产**本地候选 commit**、不部署、单机站长在场。git worktree 隔离 + 不给部署入口即足够；沙箱是 over-rigor，其「隔离能力做不出来」的 blocker 本身是过度要求的产物。 |
| 跨项目共享 CLI + 协议 + fleet 调度（第二个仓库） | 只服务 aiplanet.live 一个站。YAGNI；复用现有 `admin/alerts` + `tests/playwright` 内联即可。 |
| 30/180/365 天分级证据保留 + 磁盘水位压力态 | 单站诊断只需最近样本；「保留最近 K 天」一句清理足够。 |
| 逐动作 just-in-time 授权 + TTL + 序列 fail-stop | 无自动动作序列；站长逐次手动授权部署。 |

**净复杂度**：旧 plan ~4460 行 authority 控制平面 → 本 plan 预计数百行（3 处修复 + 一个探针模块 + 一个 worker 编排 + 一个部署脚本），且全部复用现有基建、无第二仓库。

---

## 6. Defaulted Decisions（planner 自拍，reviewer 审）

| # | 决策 | default | 理由 |
|---|---|---|---|
| D-1 | 探针 cadence | HTTP 轻探针 5min、browser 旅程探针 1h（对齐现有 A1–A4 节奏） | 平衡检测灵敏度与浏览器开销；单机不宜频繁跑 playwright |
| D-2 | 证据保留 | 保留最近 14 天样本+证据，简单定时清理 | 单站诊断够用；不建分级保留控制平面 |
| D-3 | worker agent | 非交互 `codex exec`（项目已在用 codex） | 复用现有工具链；不引入新依赖 |
| D-4 | worker 预算 | 单 active、整体 ≤60min、失败只告警 | 单机资源有限；候选非关键路径 |
| D-5 | 部署代码切换方式 | 复用现有 serve 的 checkout/commit 切换机制（implementer 按现网 serve 启动方式确定） | 与现网一致，避免引入新部署形态；TODO-2 |
| D-6 | 相对基线（>30% 退化） | 首期只做绝对预算判定，相对基线留接口不强制 | 无历史基线时相对判定无意义；绝对预算已是硬 gate |

---

## 7. Risks / TODO

- **TODO-1**：实现前确认 `pipeline.sh` 对 `.pipeline.lock/pid` 的确切写/删/异常残留语义（决定 idle/busy/unknown 分类的准确性）。
- **TODO-2**：确认现网 `live.aiplanet.ai-radar.serve` 的启动命令与代码指向方式（决定部署脚本如何切换 commit）——读 `~/Library/LaunchAgents/live.aiplanet.ai-radar.serve.plist`。
- **Risk-1**：移除 `_curated_data_version` 字段若过度，会导致成员变化后总数不刷新（正确性 bug）。缓解：V-A#1+#2 双向测试（无关写入不失效 AND 成员变化仍精确）是 Phase 1 的 gate，不过不进 Phase 2。
- **Risk-2**：browser 旅程探针在 busy 窗口（pipeline 跑）本身可能加重 CPU 争用、污染测量。缓解：探针轻量化 + load_class 标注 + busy/idle 分开合规，不用单次样本下结论。
- **Risk-3**：worker 生成的候选质量不可控，可能没用。缓解：候选只是「供审的起点」，站长审后决定用不用；worker 失败/无候选是可接受态，只告警。

---

## 8. UX 契约影响

- **有影响**：Phase 1 性能修复改变用户可感知行为（首页/微信旅程变快）。产品有 `docs/contracts/ux-contract.md`。
- **动的 section**：`### 精选页（/，首页）`（L26 附近）——投影一条性能可感知维度：首页首屏卡片可读延迟在受控/现网达到 §3 V-D#14 的旅程预算（East Asia P75 ≤2s / P95 ≤3s）。若该 section 已有性能条目则更新阈值来源，无则新增一条「性能」子项。
- **投影出的 L2**（进 user-facing surface，用 ux-contract 验收 lens）：站长在现网打开首页，首屏第一张 `.item-row` 可读时间达标（V-D#14 探针 + 人工体感）。
- **给 execute-plan 的指令**：Phase 4 验收通过后，apply 上述首页性能维度 delta 进 `docs/contracts/ux-contract.md` 首页 section，并按 V-D#14 验证——已批准意图，apply 之，不新增本段未记录的改动。
- 微信列表/详情/翻页若 ux-contract 有对应 section，同样投影其旅程预算；无则不强增。

---

## 9. 用户决策 gate 汇总

| Phase 边界 | 站长需做什么 | 看什么材料 | 怎么回复 |
|---|---|---|---|
| Phase 3→4 | 审候选修复 commit，决定是否部署 | im-notify 推来的诊断摘要 + `git -C <worktree> show` 候选 diff | 「部署」/「不部署」/「改后再部署」 |
| Phase 4 部署 | 亲自执行或批准部署脚本 | 部署脚本 dry-run 输出（snapshot/等锁/健康门就绪） | 授权执行 |
| Phase 4 验收 | 现网首页体感确认 | 打开 aiplanet.live + 四旅程探针 P95 报告 | 「达标」/「仍慢，见 X」 |

其余全自动执行，无需站长介入。
