> **Archive status**: 已归档，**未收尾**。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> **中止点**（判据＝`state.md` 残留 open 项）：TASK-001/002/008 done；TASK-003（W1 部署 + G1 owner 标注）、TASK-005（G2 决策包 + W2/W3 上线）、TASK-010（L2 verify 全量复核与交付）pending；TASK-004（P2 评分/精选/去重修根因）、TASK-006（P3 微信判据收紧，dormant at v1）、TASK-007（P4 回测引擎，待 supervisor final gate）、TASK-009（P6 文档同步）in_progress。整条链未 cutover，功能默认 OFF。
> **正文引用的 ADR 编号与文档落点不适用于当前 `main`**：正文/state 提到的「ADR-008 反馈采集」「ADR-009/010/011」与 `docs/operations/quality-loop.md` 是当时隔离工作树里的编号与落点，从未在 `main` 出现；当前 `main` 的 [ADR-008](../../adr/008-alert-severity-lifecycles.md)–[ADR-011](../../adr/011-perf-idle-only-probing.md) 是**另外的决策**（告警与性能），`docs/operations/` 下也没有 `quality-loop.md`。不要按正文编号回查。微信解读的现行权威是 [ADR-007](../../adr/007-interpret-via-ai-assistant-summarizer.md)。以下为原 plan 正文，未修改。

# Plan：AI 生成质量修根因 + 用户反馈闭环（采集 → 回测 → 迭代）

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

- 日期：2026-07-18
- Planner session：与 owner 三轮 AskUserQuestion 访谈 + 11-agent 并行代码/数据探查
- 落点：`plans/20260718-feedback-loop/`（本目录被 .gitignore 排除，属主 checkout 持久资产，不随 worktree 复制——implementer 用绝对路径读取）

## 输入

- 无上游 spec.md。L1 / 取舍偏好 / L2 全部由本 plan 承载（源自 2026-07-18 访谈，见「已确认决策」）。
- 现状事实来自并行探查（11 个只读 agent，覆盖评分/去重/精选/微信解读/web/schema/LLM/评估基建/生产库数据分析/docs 约束），关键数字已内联进本 plan，implementer 不需要重放探查。
- 本 plan 是 review / 实施唯一入口。涉及的外部文档：`docs/prd/VISION.md`（§4 原则，本 plan 含一处经 owner 批准的修订）、`docs/contracts/ux-contract.md`（§4.6 投影，见「UX 契约影响」）、`~/research/ai-assistant/agents/summary-agent/`（P3 跨 repo 改动落点）。
- **审查记录**：独立 Codex reviewer（review-plan 契约）完成 4 轮 full review，共 49 条 findings 全部修订落盘（含 8 项经 owner 拍板的决策 D13-D24 与 EVAL-FILTER 双口径）；第 5 轮由 owner 于 2026-07-18 裁决终止（review 粒度已从结构性缺陷收敛到措辞级细化，owner 判定继续迭代 ROI 不足）。plan 未获 reviewer "clean" 终态声明——implementer 遇到 plan 内部矛盾时按 long-task-protocol §6 处理，不假定 plan 无瑕。

---

## 0. 背景：当前状态（可观察事实）

用户痛点：AI 生成的评分/排序/去重/精选标签/微信"值得一看"标记区分度与过滤效果差，无法快速、正确地找到该看的文章；且系统缺少反馈采集与"回测→评估→迭代"的持续打磨机制。

探查实证的根因链（每条含出处）：

| # | 事实 | 出处 |
|---|---|---|
| F1 | 评分原始分坍缩：最新 curation run 40 篇仅 11 个不同 raw 加权分，18 篇并列 11.0（T1 模板向量 10/8/9.5/9/9.5 × 1.25 天花板）；recency=8.0 独占 38.1%；30 天内 23 个不同 item 恰好 = 11.0。评分 prompt 无锚点样例、无分布约束 | `src/airadar/scorer/prompts.py:7-41`；radar.db 只读统计 |
| F2 | 精选员额被新鲜度配额架空：40 席中 36 席只需"最新上海日期 + 48h + 加权分≥4.0"，阈值 6.5 只约束余下 ~4 席；近 10 个 run 全部选满 40/40，最新 run 含 raw 4.0-5.9 入选项；30 天 14.7% 文章进过精选、每天 104-160 篇轮换 40 展示位 | `src/airadar/curator/select.py:15-21,119-201` |
| F3 | 展示分按名次伪造：入选 40 条线性映射 62-92（rank_linear_v1），raw 4.05 的当日文显示 88-92、raw 11.0-12.0 的高分文显示 62-87，分数与质量倒挂、跨 run 不可比；`curated_items.weighted_score` 已被覆写为展示分/10，原始分仅存 `reason_json.raw_weighted_score` | `select.py:60-75` |
| F4 | 同一时间线页两种分数量纲混排：精选项=校准分、非精选项=DEFAULT_WEIGHTS 现算原始分（3-7 区间），前端色阶 80/65 对后者几乎恒灰；tooltip 文案与实际机制不符 | `presentation/summary.py:36-43`、`web/static/app.js:233-245` |
| F5 | 微信"值得一看"通胀：展示闸门 save_decision=1 放行 87-88%；展示条目中 值得一看 87%、必读 ~5%；判据在 ai-assistant repo（面向"个人 KB 可操作性"而非阅读价值）；KB URL 命中路径强制 save_decision=True 绕过判定；历史解读无法重跑（candidate 查询排除一切已有行含 296 条 error 行，--backfill 为 no-op） | `interpret/runner.py:472-524,675`；`~/research/ai-assistant/agents/summary-agent/docs/summary_agent_design.md` §3.2.8；ADR-007 |
| F6 | 去重仅精确匹配（content_hash + URL），微信转载（同文不同公众号，URL/hash 均不同）穿透全部四层：90 天 43 组/45 条冗余；实证同文（InfoQ 与 AI前线）同 run 双双入选并连续 12 个 run；转载各自独立消耗 prefilter/score/enrich LLM 调用 | `curator/dedup.py`、`fetcher/dedup.py`；radar.db 实证 item 2d25bb17dc777487 / fe3e33915a973466 |
| F7 | 排序与分数完全脱钩：/、/all、/wechat 全部 published_at DESC，分数只决定成员资格（owner 已裁定：排序保持时间序，本 plan 不改排序） | `web/routes/*.py`、`app.js`（sortByScore:false） |
| F8 | 反馈与迭代机制两端都是存根：`feedback` 表自 001_init 预留（item_id/signal/body/ruleset_version/created_at）至今 0 行、零代码引用；`tests/test_no_write_endpoints.py` 契约强制业务路由只读；ruleset_version 恒为 `2026-05-13.r1` 两个月未 bump；eval/judge.py 是 2026-05 一次性 harness（路径硬编码 `plans/ai-radar-alignment-20260512`）；`admin rerun-eval` CLI 注册了 parser 但未实现 | `migrations/001_init.sql:73-80`、`ruleset.py:5-16`、`eval/judge.py:28-33`、`cli.py:494` |
| F9 | 生产读路径不筛 ruleset：curate 候选取跨 ruleset MAX(id)、timeline 闸门/精选 pill、归档页同样不筛——任何对生产库的重评或影子 curate 会**立即改变线上展示** | `curator/select.py:79-93`、`web/routes/timeline.py:280-294`、`web/routes/curated_archive.py:43-52` |
| F10 | 回测原料已齐备且零 LLM 成本：item_evaluations 7.3 万行保存完整 prompt 快照(input_json)/输出/model_id/ruleset_version；curation_runs 保存每轮 weights_json/threshold/input_eval_ids/output_curated_ids；curate 为纯函数可反事实重放 | `migrations/001_init.sql:33-72` |

辅助事实：评分模型中途切换（v4-pro 13780 条 → v4-flash 5977 条）但 ruleset 未变；heuristic 兜底分以真实 model_id 入库（仅 output_json.raw.provider='heuristic' 可辨）；prefilter confidence 98% 集中 0.9-1.0 且全程未被下游使用；`AI_RADAR_LLM_PRICING_JSON` 未配置导致 cost_usd 恒 0；interpret 单篇 ~5.3 万 token 是最大成本项且 ADR-007 强制串行。

---

## 1. L1：最终产物与使用方式

**使用者**：owner 单人（站点公开只读的访客体验不变，仅少一些噪音）。两类使用场景：

1. **日常阅读**（aiplanet.live，桌面+手机浏览器）：修根因后的精选/微信列表噪音更低、分数徽章可信；owner 登录 Cloudflare Access 会话后卡片上出现反馈控件，阅读时顺手打标（值得读/不值得读 + 可选摘要问题标记 + 可选文字）；在别处看到重要文章而站内没有时，用"报漏"入口贴 URL。
2. **周期打磨**（闭环）：每周评估任务在副本库跑回测/指标，产出报告 + 飞书通知；指标恶化或 owner 主动发起时，agent 在隔离 worktree 产出候选变更（新 prompt/新参数 + 回归证据包），owner review 后才上线。**任何算法/prompt/权重变更都不自动应用**。

**成功定义**（owner 视角）：打开精选页/微信页时"该看的在里面、不该看的基本不在"；分数/徽章能帮助判断优先级（数字真实可比）；重复文章不再重复出现；反馈动作足够顺手（主路径一次点击）；每周能看到一份说得清"这周系统表现如何、要不要调什么"的报告。量化锚点（继承 VISION §6）：精选中 👎 比例 ≤10%；漏报 ≤1 件/周；评分分布健康（span≥20、stdev≥8，`eval/distribution.py` 现成判据）；微信档位分布进入目标带（必读 5-15%、值得一看 20-40%）。

**硬约束**：
- 不破坏 aiplanet.live 生产站点（生产 serve 跑主 checkout；另一 perf-safeguard session 并发工作中）。
- 一切实验性重评/影子 curate 只能在副本库进行（F9）。
- 开源 gate：新 tracked 文件 `git grep -nE 'lindong28|aiplanet\.live|dong_lin|/Users/lindong|/research/ai-assistant'` 零命中（ux-contract.md 唯一豁免）；owner 真值走 .env；新外部能力默认 OFF。
- 实施在独立 git worktree 进行（owner 显式要求，避免与并发 session 冲突）；merge 与部署按「实施与部署协调」节。

## 2. 已确认决策（owner 三轮访谈拍板，locked）

| # | 决策 | 内容 |
|---|---|---|
| D1 | 范围 | 修根因 + 建反馈闭环，一个 plan 分阶段 |
| D2 | VISION §4.4 修订 | 允许"评估/反馈证据驱动的 prompt 迭代"，前提：bump ruleset_version + 副本库历史回归对比 + owner review 后才上线；仍禁止 fine-tune 与把个人偏好规则清单塞进 prompt。落地：新增 ADR 修订 VISION §4.4 应用条款（prd/ 只读，变更走 ADR，见 docs/CLAUDE.md） |
| D3 | 迭代机制 | 半自动：周期评估自动出报告（飞书投递）；候选变更由 agent 在隔离环境产出（复用 performance/remediation.py 骨架）；owner review gate 后才应用 |
| D4 | 反馈鉴权 | Owner-only via Cloudflare Access：反馈写路径挂 Access 覆盖的路径前缀下；访客不可见反馈 UI、直接 POST 被边缘拦截 |
| D5 | 反馈信号 | 两键（值得读/不值得读）+ 可选摘要问题标记（有遗漏/有冗余/不准确，多选）+ 可选自由文本 |
| D6 | 排序语义 | **保持全站时间序不变**；只修分数展示真实性与徽章语义（分数不参与排序） |
| D7 | 漏报反馈 | 轻量"报漏"入口（贴 URL/标题），系统自动归因（未抓到/prefilter 拒/分不够/被去重）；不建候选池审计页 |
| D8 | rigor | 默认 (A1,V1)；override：公网写端点+鉴权单元 (A2,V2)、生产算法/prompt/判据切换单元 (A2,V1)。label：standard（关键单元 max）。理由：R 轴——写端点是安全边界、算法切换直改生产展示但可回滚；G 轴——影响生产站点但非资金/安全零容忍 |
| D9 | 微信判据 | 跨 repo 收紧：ai-assistant 侧把推荐等级判据改为阅读价值导向且更挑剔；save_decision（KB 保存）语义不变；ai-radar 侧补版本化重跑机制 |
| D10 | 精选员额 | 质量优先接受波动：取消"固定选满 40"，阈值真实生效，无达标文章的天精选少甚至空 |
| D11 | 分数展示 | 与 AIHOT 一致：**"精选 NN" 式绝对数字分**（0-100 尺度、固定映射、跨天可比、色阶按真实分布定标）。AIHOT 实测（2026-07-16 线上）：同屏 58-83 有真实离散度、按天分组、天内时间序、每天条数自然波动（22/17/1 条）——与 D6/D10 自洽 |
| D12 | 冷启动 | 大规模专项标注：建专用高效标注页（/admin/label），owner 一次性标注 150-200 条历史条目（预计 2-3 小时），作为回测种子 ground truth |
| D13 | Access 证据方式（审查 AUTH-01） | CF 面板权威证据（**优先由 agent 用 `$agent-browser` 登录 Cloudflare Zero Trust 面板自查** path 规则与 allow-list，完不成再找 owner）+ 授权实施期 1-2 次负向生产探测（无凭据/非 owner 凭据，计入生产诊断预算） |
| D14 | G1 可达方式 | P1 通过安全 gate 后**先单独部署上线**（feature flag 开启），owner 在真实 aiplanet.live/admin/label 完成标注（手机可用、与日常体验一致） |
| D15 | 周报窗口 | Asia/Shanghai 完整自然周（周一至周日），环比上一完整自然周 |
| D16 | 报告输出面 | canonical JSON 单一真相源（含 schema_version 与固定指标字典），HTML 报告 / 飞书摘要 / admin 面板板块全部由该 JSON 纯渲染，不各自查库 |
| D17 | LLM 批量执行模型 | 先探明 ARK 限流/额度（控制台或小批实测），据此定有界并发 cap（enrich `--workers` 先例，预计 4-8）；带断点续跑与进度输出；不静默串行也不无界并发 |
| D18 | 跨 ruleset 分数可比（审查 DECISION-01） | rollout 时用新 ruleset **重评全部用户可见归档**（~4900 distinct items，有界并发，成本入 G2 决策包）——全站单一分数语义，不做 crosswalk、不分段显示 |
| D19 | KB 历史条目（审查 DECISION-02） | **KB 不动、只刷新徽章**：历史重跑只更新推荐等级/理由/criteria_version，不改既有行 save_decision、不写 KB；KB-hit 强制 save 仅对未来新文章移除；/wechat 列表构成不变（仍 save_decision=1，可含少量"可跳过"） |
| D20 | 质量阈值定位（审查 DECISION-03） | 混合 gate：交付 gate = G2 冻结 G1 标签的 precision gate；👎≤10%/漏报≤1 在首个反馈 n≥30 的完整自然周做一次 live acceptance（G4 后续轮），之后转周报运营指标 + incident 触发 |
| D21 | prompt 失败预案（审查 DECISION-04） | **预授权部分改善**：3 轮迭代 + v4-pro 对照后，达到最低幅度即可上线——unique raw ≥20 **且** span、stdev 均不低于 P0.3 基线现状值；连最低幅度也不达 → 评分单元不上线（其余 P2 单元照上），回炉另计 |
| D22 | 微信采样契约（审查 DECISION-22） | interpret 路径评估与生产统一 **temperature=0**（改 ai-assistant 侧调用配置；单次结果稳定、新旧差异可归因判据），采样参数入逐样本 evidence |
| D23 | LLM judge（审查 DECISION-23） | **取消标签不足时的 LLM judge 补位**：标签不够就在报告中如实标 `evidence insufficient`，不让 LLM 代理 ground truth；eval 引擎不含 judge gate |
| D24 | 折叠来源展示（审查 DECISION-24） | 折叠卡显示 **"另有 N 个来源"，点开再展示来源名列表**（移动端密度优先，信息可追溯） |

## 3. 取舍偏好与三层影响

- **质量 ≫ 数量/稳定性**（D10，VISION "Precision ≫ Recall"）：L1 精选形态接受每日条数波动；L2 验收以"入选项全部达标"取代"每天 N 条"；L3 取消 freshness floor 兜底逻辑。
- **真实性 ≫ 视觉好看**（D11）：宁可分数普遍偏低（分布改善前），不做名次伪造；L2 断言展示分与 raw 单调一致。
- **低摩擦采集 ≫ 信息丰富**（D5）：主路径一次点击；摘要标记与文字是可选层；L2 验收含"单次点击即落库"。
- **安全/不污染 ground truth ≫ 便利**（D4/D8）：owner-only 边缘鉴权优先于免登录便利；L3 对鉴权单元做对抗验证。
- **不过拟合 ≫ 迭代速度**（D2/D3 保留 VISION 防护）：一切迭代过回归 gate + owner review；单人反馈样本小，报告须展示样本量警示。

## 4. Rigor（D8，两轴理由见上表）

- 默认 `(A1,V1)`；per-phase override（只升不降）：
  - P1 中「写端点 + Access 鉴权边界」单元：`(A2,V2)` —— 对抗验证见 L2-V1/V2 与 P1 内部 verify。
  - P2/P3 中「生产切换」单元（新 ruleset 上线、curation v2 上线、微信判据上线、历史展示分 backfill）：`(A2,V1)` —— 每次切换须有副本库回归证据 + owner gate（G2/G3），且可回滚（见各 phase 回滚路径）。
- review-gate 本地定档是不可降低的 V floor，与本向量逐维取高。
- 对称校验：机械单元（建表/前端按钮/报告脚本）不陪跑对抗审查；V2 仅施于鉴权边界单元。被省去机制复核：匿名投毒风险由 A2 边缘鉴权覆盖；生产污染风险由"副本库硬前提 + replica guard"覆盖；回归风险由 golden harness + 既有测试 + journey probe 覆盖——无真实失败模式因降档失去必要保护。

## 5. 方案总览与阶段划分

```
P0 基线与安全基建 ─→ P1 反馈采集/标注/报漏 ─→ G1 owner 标注 session（种子 ground truth）
        └────────────→ P2 评分/精选/去重修根因（副本库实验 → G2 rollout gate → 上线）
                        P3 微信判据收紧（跨 repo，样本重跑 → G3 gate → 上线 + 范围决策）
P4 回测引擎 + 周期评估/告警（消费 P1 反馈 + P0 副本基建）─→ G4 首份周报确认
P5 半自动迭代 worker（消费 P4 报告/incident）
P6 文档同步与收尾
```

P1 与 P2/P3 的副本库实验可并行推进；**gate 顺序硬化（审查 EVAL-01 + D14）**：P1 过安全 gate 后先单独部署上线 → G1（owner 真站标注）→ G1 种子标签作为 G2/G3 决策包的必要输入（新旧候选与 owner 标签的一致率）→ G2/G3。

---

### P0 基线与安全基建

**目标**：让所有后续实验可以安全（不碰生产）、可比（有冻结基线）、可算账（有成本数据）地进行。

| 任务 | 内容 | 内部 verify |
|---|---|---|
| P0.1 副本库工具 | `run.sh admin backtest-snapshot`（或等价 CLI）：SQLite `.backup` 产出冻结副本至 `data/backtest/<date>.db`；参考 `docs/references/web-contract-golden.md` 的冻结库规则（WAL 非空拒绝裸 cp）。副本产出不与 15 分钟 pipeline 写锁冲突（backup API 自带重试/或等 .pipeline.lock） | 副本可打开、行数与源库当刻一致（spot check 3 表 COUNT）；生产库文件 mtime/内容不因 snapshot 改变 |
| P0.2 replica guard | 评估/重放/批量重评入口（P2 实验命令、P4 引擎）强制校验目标 DB **解析后身份**（`os.path.realpath` 比较，防 symlink/相对路径别名绕过）≠ 生产默认路径，除非显式 `--production`；防 F9 事故 | 单测：不带 flag 指向默认库 → 拒绝退出非 0；symlink 别名指向生产库 → 同样拒绝 |
| P0.3 基线指标脚本 | 一条命令产出当前基线 JSON+MD：分数分布（span/stdev/run 内 unique raw 数/维度直方图）、精选构成（quota vs threshold 占比、每日 distinct 条数）、微信档位分布、微信转载组数（F6 口径）、量纲混排样例数。**canonical 分数口径（审查 SCORE-BASELINE-02）**：基线与后续一切分布指标统一从 `reason_json.raw_weighted_score`（raw 0..scale）经同一 `display=round(raw/scale*100)` 映射计算——不复用 `curated_items.weighted_score` 列 ×10 的旧口径（该列现存 rank 校准值）；`eval/distribution.py` 判据适配到该 canonical 口径后再用。冻结存档作为 P2/P3 的 before | 脚本在 P0.1 副本上可重复运行且输出稳定；关键数字与本 plan §0 表内数字同量级（偏差可解释——数据在增长）；口径单测：同一批 raw 输入，基线脚本与 P2.3 展示映射产出相同 display 值 |
| P0.4 成本核算修复 | 配置 `AI_RADAR_LLM_PRICING_JSON`（ARK/DeepSeek 价目，走 .env 不进 git）；补一个"重评成本估算"函数：给定 item 集与 stage 估 token/费用（scoring ~2-3k token/篇、interpret ~53k token/篇） | `llm_usage` 新记录 cost_usd 非 0；估算函数对已知历史用量误差 < 2x |
| P0.5 heuristic 分数标记修复 | heuristic 兜底评分停止伪装真实 model_id（改写 model_id='heuristic' 或等价可查询标记；历史行不改，回测时按 output_json.raw.provider 排除） | 单测：FORCE_HEURISTIC 下新评估行可与 LLM 行用 SQL 区分 |

**成本估算义务（P0.4 产出，供 G3 用）**：微信历史重跑 全量 save_decision=1 ≈1428 篇 × ~53k token ≈ 76M token；近 30 天 ≈863 篇 ≈ 46M token；近 7 天 ≈200 篇 ≈ 10.6M token。P0.4 落地后用真实价目换算为人民币并写进 G3 决策包。

### P1 反馈采集、标注页、报漏（含 (A2,V2) 鉴权单元）

**目标**：owner 可以在四个 surface（首页精选卡、/all 卡、/wechat 列表卡、微信详情页）低摩擦打标；报漏入口可用；标注页支撑 G1 冷启动。

| 任务 | 内容 | 内部 verify |
|---|---|---|
| P1.0 Access policy 权威证据（D13，[A]→[H] 降级链） | 实施首步：用 `$agent-browser` 登录 Cloudflare Zero Trust 面板，导出/截图当前 Access 应用的 path 规则（确认 `/api/v1/admin*` 覆盖或按 runbook `docs/operations/monitoring-alerting.md:142-155` 新增）与 allow-list（确认**仅 owner 邮箱**，无宽域/group 放行）；证据存 `plans/20260718-feedback-loop/evidence/`；agent-browser 完不成 → [H] 请 owner 提供面板证据。此为 P1.2 上线的前置 gate | 证据文件存在且明确回答两个问题：路径覆盖？allow-list 仅 owner？宽于 owner 时先收紧 policy 再继续 |
| P1.1 migration 015 | 幂等 migration（不动 003/004，遵守 `airadar-migration-skip-004` 纪律；**注意本仓 migration 除 004 外每次全量重跑——禁止 rebuild/DROP 式改表**，审查 DOCS-PATH-02 修正：SQL 文件 rebuild 先例是仅跑一次的 004_enrich_stage.sql，不适用于每次重跑的 015）：**新建 `feedback_events` 表**（`CREATE TABLE IF NOT EXISTS` 天然幂等；旧 `feedback` 表 0 行零引用，保留不用）：`id` 自增、**`item_id TEXT NULL REFERENCES items(id)`（items.id 为 TEXT——审查 FEEDBACK-STATE-01 修正）**、`url TEXT`、`title TEXT`、`signal TEXT`（worth/not_worth/missed）、`surface TEXT`、`source TEXT`（organic/label_session/missed_report）、`body TEXT`、`context_json TEXT`（快照：run_id/rank/display_score/ruleset_version/model_id/recommendation/criteria_version + 摘要标记等，JSON blob 遵循 VISION §4.5）、`ruleset_version TEXT`、`created_at`、**`updated_at`** + `idx_feedback_events_item`；**verdict 状态语义**：每 (item_id, source) 仅一条当前 verdict 行（UNIQUE 约束）——改判（worth↔not_worth）为 UPDATE signal+context+updated_at，不新增行，周报按最新 verdict、以 updated_at 归窗；**漏报数据模型**：missed_report 必填 url（规范化后同 URL 重复提交=更新既有行）、item_id 可 NULL，归因 job 匹配到 item 后回填（"后来抓到"由周报任务重试回填）；`wechat_interpretations` 加列 `criteria_version TEXT` 与 **`criteria_reason TEXT`（v2 判据理由的独立落点，不复用 save_reason/summary_md——审查 D19-STATE-01）**；`curation_runs` 加列 `superseded INTEGER DEFAULT 0`（P2.5 回滚用）与 `audit_json TEXT`（P2.4 审计事实源，schema 见 P2.4） | 在"生产形状副本"（P0.1 产物，004 已应用）上 migrate **三遍**幂等且已插入的 feedback_events 行不丢；冷库 migrate 亦通过；schema 断言（item_id TEXT nullable、UNIQUE(item_id,source)、各新列存在）；**改判 round-trip 测试**：not_worth→worth 后全表该 (item,source) 仅一行、signal=worth、updated_at 更新 |
| P1.2 写端点 | `POST /api/v1/admin/feedback`（verdict/aspects/note/missed-report 共用一个端点，signal 区分）+ `GET /api/v1/admin/feedback/ping`（owner 探测）+ `GET /api/v1/admin/feedback/summary`（标注页/面板用）。路径挂在 `/api/v1/admin*` 前缀下复用现有 Cloudflare Access policy（Defaulted DD-1，含覆盖验证步骤）。实现：`web/routes/feedback.py` 新 router + app.py 注册；连接一律 `conn_from_request`（连接泄漏事故教训，`request_db.py:10-26`）；写事务极小；数据库 busy/锁超时返回 503 + Retry-After（不是 500）；origin 侧沿用 admin.py 的 Cf-Access-Jwt-Assertion 存在性校验 + `AI_RADAR_ADMIN_ALLOW_LOCAL` 本地旁路；输入校验（signal/aspects 枚举、body 长度上限、item 存在性校验、missed-report URL 格式）；feature flag `AI_RADAR_ENABLE_FEEDBACK`（默认 OFF，开源中性；生产 .env 开启） | 单测覆盖：合法/非法 payload、无 header 401/403、超长 body 拒绝、DB 锁模拟返回 503；`tests/test_no_write_endpoints.py` 显式豁免该路由（契约有意识演化，注释说明）；cors.py allow_methods 加 POST |
| P1.3 前端反馈控件 | owner-mode：页面加载后静默 `fetch('/api/v1/admin/feedback/ping', {credentials:'same-origin'})`，200 → 显示控件（结果缓存 localStorage；**ping 返回 401/403 时清缓存并隐藏控件**——登出/会话过期场景不残留）；改 4 处渲染路径：`app.js itemCard()`（/ 与 /all）、`wechatCard()`、`_prepaint_list.html` 占位、`wechat_detail.html`（新增独立小 script）；控件=两键 + 点击后可展开的摘要标记/文字层（D5）；事件委托挂列表容器（`bindWechatCardNavigation` 范式）且**反馈点击 stopPropagation 不触发卡片导航**；提交后即时视觉确认与幂等（重复点击=更新最近一条同 item 同 signal 反馈，同 item 可改判）；模板 `?v=` cache-bust bump；**不阻塞首屏**（ping 异步、控件渐进出现，不进 SSR 关键路径） | `tests/test_frontend_static_contract.py` 补断言；node 单测（`tests/pagination.test.mjs` 范式）覆盖控件渲染函数；Playwright 隔离库 E2E：(a) owner 模拟（ALLOW_LOCAL）**完整 round-trip：两键+摘要多选+note 逐字段落库断言（UI→API→DB 无一层丢弃）**；(b) **390px 触摸视口**：控件不溢出、点击反馈不触发卡片跳转；(c) owner cache 失效：模拟 ping 401 → 控件消失 |
| P1.4 报漏入口 | owner-mode 侧栏/页脚"报漏"入口：贴 URL/标题提交（signal='missed'，数据模型见 P1.1）；服务端归因 job（提交时同步做，超时降级为记录待归因）：URL 规范化后查 items（未抓到？）→ 查 prefilter 结果（被拒？）→ 查 scoring/curated（分不够/被去重？），归因结论写 context_json；未归因行由周报任务重试（"后来抓到"回填 item_id） | 单测六案例：未抓到（站外 URL）/prefilter 拒（真实 item）/分不够/转载副本/**同 URL 重复提交=更新**/**后来抓到=先 NULL 后回填**（fixture：提交后插入 item 再跑归因） |
| P1.5 标注页 | `/admin/label`（挂现有 /admin Access 边界 + admin.py 鉴权包装）：从副本口径抽样 150-200 条（近 30-60 天精选 ~100 条 + 微信解读 ~50-100 条，按天分层抽样，抽样脚本落库列表）；单条视图=标题/摘要/来源/当时分数与档位 + D5 控件；键盘快捷键（j/k 导航、1/2 判定、m 标记层）；**进度持久在服务端（队列落库 + 已标状态），非前端内存**；写入 source='label_session' | Playwright：键盘流标 3 条 → DB 3 行 source='label_session'；**390px 触摸视口标注流（点按判定/标记，G1 宣称手机可用的兜底——审查 UX-VERIFY-02）**；**中途刷新 → 进度恢复、断点续标**；**队列耗尽 → 明确完成态**；抽样脚本可重复运行且分层比例可查 |

**(A2,V2) 鉴权单元的对抗验证**（gate 于 P1 完成宣告前，独立对抗 review + 以下可执行断言；D13 已授权其中的生产探测）：
1. **策略证据**（P1.0 产物）：path 覆盖 + allow-list 仅 owner 的面板权威证据在档——这是"已认证非 owner 被拒"的证明主体（无第二账号可实测时以 allow-list 证据代替实测，并在证据中注明）。
2. **匿名负向探测**（[A]，已授权，计 1-2 次生产诊断请求并记账）：无 cookie POST 生产 `/api/v1/admin/feedback` → 边缘拦截（302 Access 登录页或 403，非 2xx）；如条件允许附带一次伪造 `Cf-Access-Jwt-Assertion: fake` 直打边缘 → 同样非 2xx。
3. **owner 正向** [H]：G1 标注 session 本身即 owner 放行的实证（P1 上线后 owner 首次成功提交反馈/标注即完成该态验证，无需单列操作）。
4. 本地 origin 直连伪造 header → origin 放行（**已知接受的限制**：origin 只做存在性校验，真实闸在边缘；既有 admin 面板同款威胁模型，`docs/operations/monitoring-alerting.md` 已记载）。对抗 review 的 finding 若以"可伪造 header 绕过 origin"为前提，属已声明 threat model 内，至多 MEDIUM。
5. 匿名视角 UI：无 cookie 加载 4 个 surface → DOM 无反馈控件、无 admin 端点探测以外的泄漏（ping 404/403 静默）。
6. 滥用面：payload 上限、枚举校验、item_id 不存在 → 4xx；确认无 SQL 注入面（参数化查询断言）。

### P2 评分与精选修根因（副本库实验 → G2 → 上线）

**目标**：F1-F4 逐一消除。所有实验先在 P0.1 副本上做，G2 通过后才动生产。

| 任务 | 内容 | 内部 verify |
|---|---|---|
| P2.1 评分 prompt v2 | `scorer/prompts.py` 重写：各维度锚点样例（低/中/高分锚各 1）、明确"典型文章应落在 4-6、8+ 仅保留给…"的分布指引、要求维度间独立评判（打破 F1 模板向量）、允许 0.5 步长；**改 prompt 必须同步 bump `ruleset.py` RULESET_REV**（新加 guard 测试：prompt sha256 变更 ⇒ ruleset 变更，替代现有 freeze 测试语义）；`score` runner 补 `--item-ids/--item-id-file` 定向重跑参数（照抄 prefilter 模式）；补 per-item 异常隔离（现状 chat_json 双 provider 失败抛 RuntimeError 中止整个 stage——批量重评一条坏数据打断长任务）；**批量执行模型（D17）**：先探明 ARK 限流/额度（P0.4 顺手，控制台或小批实测），重评走有界并发（enrich `--workers` 先例，cap 按探明约束定，预计 4-8）+ 按 (item,ruleset) 幂等断点续跑 + 进度输出；**实验 provenance（审查 EVAL-01）**：实验 corpus 冻结（item_id 清单+content hash 落 `evidence/`，重评与对照同一 corpus）；逐样本记录 ruleset、prompt sha256、model_id、真实 provider、temperature、cache 命中标记；**实验路径禁止 heuristic 降级**——无 key/双 provider 失败即显式 fail-closed：保存进度、明示"部分完成"状态，**gate 不得基于部分结果**；**重放契约钉死**：temperature=0、重试次数与 provider 链（ARK→官方）固定并逐样本记录实际 provider、输出落 item_evaluations(stage='scoring', ruleset=v2)；**分布判据不达时（D21）**：最多 3 轮 prompt 迭代 + v4-pro 对照，最低上线幅度 = unique raw ≥20 且 span、stdev 均不低于 P0.3 基线，连最低幅度不达 → 评分单元不上线、其余 P2 单元照上 | prompt/ruleset guard 测试；重评 500 条冻结 corpus（deepseek-v4-flash，~1.5M token，成本可忽略）：**corpus 记账守恒 = 成功数 + 显式失败清单（失败不静默缺席）**；span≥20、stdev≥8、单日 40 条候选内 unique raw 数 ≥30（目标值；最低上线幅度见 D21）——`eval/distribution.py` 判据复用；逐样本 provenance 完整性断言（无 heuristic 行混入）；**受控 fake provider 并发测试（审查 BATCH-02）**：并发峰值 ≤ cap、已完成项幂等跳过、失败项显式列出、进度单调；**G1 种子标签为 G2 必要输入，主 gate 钉死为 precision@selected / precision@K**（新选集对种子标签的 precision ≥ 旧选集；AUC 仅作诊断附录，不作 gate） |
| P2.2 curation v2 + active-ruleset 读边界 | `curator/select.py`：freshness quota 仅从达标池（≥ threshold）内取（floor 参数废除或=threshold）；取消"必须选满 limit"；threshold 按新分布在副本上重定标（P2.1 重评数据 + 种子标签定，写进 G2 决策包）；**active-ruleset 读边界（审查 ROLLOUT-02/03）**：curate 候选、timeline 可见性闸门、timeline 非精选分数展示三条评估读路径改为只认 `ruleset_version = current_version()`；**无 active 版本评估时的语义分裂**：分数展示一律**不显示**（严禁跨版本回退，防混合语义）；timeline 可见性布尔闸门**允许回退到"current 或曾正式激活过的版本"的 relevance，并配用该版本自身的阈值**（版本激活史落代码常量表；**pending shadow 版本永不参与回退**——否则影子写会在切换前改变可见性，审查 RULESET-AUTHORITY-01；回退存在的理由：无回退则旧版低分噪音因"无 v2 评估→视同未评分→可见"整体解禁，破坏 V13 preservation；此为有意的、写入 ADR-009 的永久语义：布尔闸门混版本不可感知，展示分混版本才有害）；curate 候选无 active 评估 → 不入候选。由此生产**影子写入 v2 评估不改变线上展示**，直到部署切换 current_version；timeline 阈值硬编码改为与 ruleset 绑定的常量并同步重定标（F4 tooltip 文案一并修真）；`curation_runs` 照常记录新 weights/threshold | 纯函数单测：无达标项 → 空选集；quota 池内全部 ≥ threshold；**active-ruleset 隔离测试**：副本库写入 v2 评估、current_version 仍 v1 → curate/timeline 输出与写入前一致（影子不泄漏），**含 pending-only item（仅有 shadow v2 评估的新 item）不因影子改变可见性** 的 fixture；**回退语义 fixture**：v1-only 低分 item 用 v1 阈值继续隐藏、v1-only 高分 item 继续可见、激活 v2 后有 v2 评估 item 的 display 逐条对账（审查 RULESET-AUTHORITY-01）；重放近 30 天副本：每日精选构成对比报告（before/after 逐日条数与成员 diff） |
| P2.3 展示分修真 | 废除 rank_linear 校准：`display = round(raw_weighted / scale * 100)`，**scale = 10 × max(tier_multiplier) 推导而非硬编码 12.5**；**权重归一化 invariant：Σweights=1 加断言测试（含 `--weights` 注入路径校验），权重演化不破坏 scale 语义**；映射版本化：`reason_json.score_display='linear_v2'`，换尺度必须 bump 版本 + backfill 或读侧兼容（演化规则写进 ADR-009）；`curated_items.weighted_score` 停止覆写（存 raw，展示层算 display）或等价迁移；**历史 backfill 语义（审查 RULESET-AUTHORITY-01 修正）**：display 一律**从 active ruleset 的评估推导**——v2 激活路径下，历史 curated 行展示分从 D18 全量重评产生的 v2 评估重算（不再从旧 reason_json.raw 推导，避免 100% 覆盖下仍显示 v1 语义）；仅评分单元 fail-closed（v1 保持 active）时才从 `reason_json.raw_weighted_score`（v1 语义）重算；原值保留在 reason_json 可回滚（DD-5）；时间线非精选项用同一映射与同一 active 版本约束（消除 F4 双量纲）；色阶阈值按新分布 P50/P80 定标（G2 包内定数）；前端 scorePill/tooltip/about 页说明同步修真；precompute 重跑 + archive cache generation 正确失效 | **精确公式单测**：display(0)=0、display(scale)=100、越界 clamp、给定 raw 表逐值断言 round(raw/scale*100)；raw 高者 display 恒高；同一 item 跨 run display 不变（输入 raw 相同）；**最终 CSS 色阶阈值断言**（G2 定数后写入前端契约测试）；golden harness 结构对比（分数字段值变化按 re-baseline 规则处理） |
| P2.4 转载/同事件折叠 | curate 去重升级：wx_mp2rss 按 `normalize_wechat_title`（既有工具）+ author 不同 → 判转载组；跨源 exact 归一化标题组同口径；修 curator/fetcher URL 规范化不一致（尾斜杠）；重复组保留加权分最高者为主卡，组员**钉死落 `reason_json.duplicate_group`（卡片读模型）+ `curation_runs.audit_json`（审计真相源）双落点，无其它可选形态（审查 DEDUP-BRANCH-01）**；**展示（D24）**：精选页主卡显示"另有 N 个来源"，点开再展示来源名列表；timeline 不折叠（DD-4）；interpret 跳过转载组非主卡成员（省 LLM）；去重决策落 `curation_runs.audit_json`（**唯一权威审计事实源，schema v1 钉死**：`{version:1, candidates:[eval_id], dropped:[{eval_id, reason}], groups:[{primary_item, members:[item_id], rule}]}`，不留 reason_json/新表二选一分叉——审查 DEDUP-PERSIST-02） | 单测：InfoQ/AI前线实证案例（F6 的 43 组 fixture 化抽 5 组）折叠正确；误折叠护栏：标题归一化相同但 author 相同（同号更新）不折叠；**守恒审计脚本（审查 DEDUP-01）**：独立于 curate 流程，对该 run 持久化的原始候选全集全量重推 expected groups（归一化标题分组 O(n log n) 扫描），与记录的 group 划分对账——完备性（应折叠对全在同组）+ 正确性（组员符合规则）+ 守恒（候选数 = 主卡+折叠成员+去重丢弃+落选之和）；interpret 跳过数与折叠成员数对账 |
| P2.5 G2 决策包与上线 | 汇总 G2 gate（见「用户决策 gate」；packet 含 **prompt 合规断言项：候选 prompt diff 不含个人偏好规则清单/具体反馈样例引用**——审查 POLICY-GUARD-01）；通过后生产 rollout（**dormant 边界先行 → 影子写 → 验证 → 原子切换**，审查 ROLLOUT-02/03）：(0) **先单独部署 dormant 的 active-ruleset 读边界**（current_version 仍=v1，行为等价，golden harness 验证）——在此之前不得向生产写任何 v2 评估（当前生产代码跨版本 MAX(id) 读取，影子会即时泄漏）；(1) 生产**影子写入** v2 评估：近 48h 候选 + **全部可见归档 ~4900 distinct items（D18，有界并发，成本入 G2 包）**；(2) 验证影子数据完整（覆盖率=可见归档 100%，缺失清单显式）且线上展示与写入前一致（影子零泄漏抽查）；(3) 部署切换 current_version=v2 + curation v2 + 展示修真 + backfill → 触发 precompute/缓存失效 → 冒烟。**评分单元 fail-closed 分支（D21）**：连最低幅度不达 → current_version 保持 v1、不写影子，仅部署 curation v2（threshold 在 v1 分布上重定标）+ 展示修真（对 v1 raw 同样成立）+ 转载折叠 | 上线冒烟：新 run 全部成员 ≥ 新 threshold；首页/all/wechat 打开正常、分数单调、无 500；journey probe 阈值不回归；**跨版本可比断言（审查 SCORE-SEMANTICS-01）**：可见归档全部条目均有 v2 评估（count 对账），展示分单一语义。**回滚契约（审查 ROLLOUT-01/02）**：(a) 部署回退 current_version=v1（active-ruleset 读边界使 v2 评估即时退出消费）+ 旧 select 参数；(b) v2 窗口 curation_runs 标 `superseded=1`——archive/timeline join 加 `NOT superseded` 过滤（P2.2 一并实现并单测）；(c) 重跑 curate 产生 v1 新 run；(d) backfill 还原脚本从 reason_json 原值恢复。回滚演练：副本库执行 (a)-(d) 断言展示回到 before 形态 |

### P3 微信解读判据收紧（跨 repo，样本重跑 → G3 → 上线）

**目标**：F5 消除。判据改动落 ai-assistant（ADR-007 判定权归属不变），ai-radar 侧补版本化重跑。

| 任务 | 内容 | 内部 verify |
|---|---|---|
| P3.1 ai-assistant 判据 v2 | `summary_agent_design.md` §3.2.8 + prompts（system.md.j2/user_article.md.j2）：推荐等级判据改为"雷达读者视角的阅读价值"（信息增量/深度/时效相关性），目标分布带写进判据（必读 5-15%、值得一看 20-40%、可跳过其余）；save_decision 判据与语义**不动**（KB 用途不变）；判据文档加版本号（criteria v2）；该 repo 内自有 commit/review 惯例照走 | ai-assistant 侧样本试跑 10 篇：输出 schema 兼容（ai-radar 解析契约 `docs/operations/ai-assistant-integration.md` 口径不破坏——审查 DOCS-PATH-02 修正路径）；推荐等级理由引用新判据措辞 |
| P3.2 版本化重跑机制 | ai-radar `interpret` runner：`wechat_interpretations.criteria_version` 落值；新增 `--rerun-criteria <version>` 路径：candidate 改为"无行 OR criteria_version < 目标版本 OR error 行"（修复 296 条 error 永久占位，F5）；重跑绕开 ai-assistant `--check-url` 旧结果复用路径（新增 force 语义或版本感知），**每行记录 cache 命中/新调用标记与 criteria_version、model、prompt 版本（provenance，审查 EVAL-01）**；**KB 边界与列级契约（D19，审查 WECHAT-CONSISTENCY-01/D19-STATE-01）**：历史重跑**只更新 `recommendation` / `criteria_version` / `criteria_reason` 三列**——`save_decision`、`save_reason`、`summary_md` 及 ai-assistant KB（index.json/vectors.npy/manifest/summaries）全程不触碰；v2 判据理由只写新列 `criteria_reason`，不复用 save_reason/summary_md；"KB-hit 强制 save_decision=True 移除"仅作用于未来新文章的增量解读；**并发模型（审查 WECHAT-BATCH-01）**：历史重跑因零 KB 写**不继承 ADR-007 串行约束**——LLM 生成按 D17 有界并发，结果收集后短事务串行写库；**仅未来写 KB 的增量路径保持串行**；**采样契约（D22，审查 INTERPRET-CONTRACT-01）**：不改 ai-assistant 共享默认值——由 ai-radar 的两条 interpret 调用路径（增量 + 重跑）**显式传 `--temperature 0`**（summary-agent 已支持逐调用参数），采样参数入逐样本 provenance；**criteria_reason 为 additive 端到端契约**：producer prompt 输出 → summary-agent stdout schema 增字段 → ai-radar parser 提取 → DB 列，任一层缺失即 fail（不静默丢弃）；provenance 另记实际 provider/backend（ARK vs 官方 fallback），**G3 配对比较要求新旧样本执行路由一致，不一致样本分层或标记不可归因**；**回滚契约（审查 ROLLOUT-01/WECHAT-ROLLBACK-01）**：切换时落 **rollout manifest**（cutover 时间戳、ai-radar 与 ai-assistant 两仓 commit、目标 criteria 版本）；批量重跑前对受影响 wechat_interpretations 行导出行级快照 artifact（`evidence/wechat-rerun-backup-<ts>.jsonl`）；rollback = 按 manifest 回退 ai-assistant 侧 commit + 从快照还原被覆盖行 + **对 cutover 后新增的 v2-only 可见行按 v1 判据重跑** → 终态断言 `/wechat` 可见行零 criteria_version='v2' 残留（KB 因历史重跑不触碰无需还原）；未来增量路径照旧例行备份 KB | 单测：candidate 查询三分支；干跑（副本库）重跑 5 条不写生产；**历史重跑零 KB 写断言（KB 目录 hash 前后不变）+ 逐 item 断言 save_decision 值集与 /wechat 成员 ID 集完全相等（hash 不变不足以证明成员集不变——审查 D19-STATE-01）**；有界并发下写库无相互覆盖（并发生成→串行落库测试）；行级快照存在性断言；副本库回滚演练：还原后逐行等于快照 |
| P3.3 G3 样本验证与上线 | 近 7 天 save_decision=1 抽 30-50 条（**corpus 冻结：清单+hash 落 evidence/**）在副本库用判据 v2 重跑（逐样本 provenance：criteria/prompt 版本、model、**temperature=0 与实际 provider 路由**、cache 状态、无旧结果复用；**全 corpus temperature/provenance 对账**），生成 side-by-side（旧档位/新档位/新理由）HTML + 分布统计 + **结构化逐样本 ballot 槽位（rubric）** → G3 gate（含历史重跑范围决策，成本数字来自 P0.4；**G1 种子标签中的微信条目一致率为 gate 输入**）→ 通过后：ai-assistant 合入判据 v2 → 生产按 owner 选定范围重跑（LLM 生成有界并发 + 落库短事务串行——零 KB 写故不受 ADR-007 串行限；每晚低峰、断点续跑、先落行级快照）→ /wechat 页徽章分布可见改善 | 上线后 SQL：新增/重跑行 criteria_version='v2'；重跑期间 /wechat 可用（重跑逐条 upsert 不锁页面）；历史重跑前后 KB 目录 hash 不变（D19 零触碰）；未来增量路径 KB 条目数只增不损（count+抽查） |

### P4 回测引擎与周期评估

**目标**：F8/F10 兑现为可持续机制。

| 任务 | 内容 | 内部 verify |
|---|---|---|
| P4.1 回测引擎 | `src/airadar/eval/` 泛化重构（解除 judge.py 硬编码路径；保留 JudgeProvider/borderline 中位/compare_renderer 可复用件）。三个回放器：(a) **curation 反事实**：副本库上以备选 weights/threshold/quota 重放历史 run（用 curation_runs.input_eval_ids 重建候选），输出选集 diff + **precision@selected/precision@K（对反馈标签，主指标）**；(b) **scoring A/B**：ruleset 并存评估行对比（分布指标 + 标签 precision，AUC 仅诊断）；(c) **微信判据回放**：parse_summary_output/compute_save_decision 纯函数重放 + 档位分布 vs 目标带。**无 LLM judge（D23）**：标签不足的维度在报告中如实标 `evidence insufficient`（含原因与所缺样本数），不做 LLM 补位（judge.py 的 compare_renderer 等确定性件仍复用，JudgeProvider gate 移除）；**内部 verify 加零 LLM 断言（审查 EVAL-GROUNDTRUTH-01）**：eval 引擎运行期 spy/mock 断言零 JudgeProvider/chat_json 调用；标签不足 fixture 断言输出为显式 insufficient 状态而非普通分数；**证据失效按依赖切片（审查 EVAL-EVIDENCE-01）**：prompt/ruleset 变化只作废 scoring 回放证据、weights/threshold 变化只作废 curation 反事实、判据变化只作废微信回放、corpus 变化作废引用该 corpus 的证据——不因任一输入变化全量重跑三回放器 | 引擎全部走 P0.2 replica guard；固定输入→固定输出（确定性回放器无 LLM）；单测覆盖三回放器各一条 golden 输入 + 失效切片规则单测 |
| P4.2 指标与 canonical 报告（D15/D16） | **每期先对生产库产出新鲜只读一致性快照**（.backup，非 P0 冻结基线；P0 基线仅作长期 before 对照），在快照上算指标；**指标字典逐指标钉死（quality-report JSON schema v1，审查 REPORT-CONTRACT-01）**——时间窗一律 Asia/Shanghai 完整自然周、环比上一自然周；逐指标定义：(a) **👎 率主指标**：分子=窗口内被标 not_worth 的精选 distinct item 数，分母=窗口内**有 verdict 反馈**的精选 distinct item 数（真实态度率；n<30 标注功效不足），辅指标=👎 数/窗口内**新入选**精选 distinct item 总数（下界）；(b) **漏报**：窗口内 signal='missed' 的规范化 URL 去重计数；(c) **分布健康**：active ruleset 下窗口内评估行的 span/stdev/unique（排除 provider=heuristic）；(d) **微信档位**：窗口内最新 criteria_version 解读行的三档占比；(e) **去重拦截**：窗口内 audit_json groups 折叠成员计数；每指标定义空样本语义（null + 原因枚举）；全部按 feedback source（organic/label_session/missed_report）分层、标注所处 ruleset/criteria/model 版本轴；**live acceptance 自动步骤（D20，审查 LIVE-ACCEPT-02）**：每期周报自动检测"是否首个 `source='organic'` 的 distinct verdict item 数 ≥30 的完整自然周"（**label_session/missed_report 不入 live 分母**——标注 session 只作 G2 ground truth）；命中则判定 👎≤10% 与 漏报≤1，结果落 report JSON `live_acceptance: pending|pass|fail`（含判定周与两指标值），fail 自动生成 incident 行进通知；判定为一次性验收，之后两阈值转运营 incident 阈值。**canonical JSON 为唯一真相源**落 `logs/quality-eval/`（K=90 天保留裁剪） | 手动触发产出 JSON 通过 schema 校验（含逐指标分母/分层字段存在性）；反馈 0 行时优雅降级（null+原因）；快照新鲜性断言（快照时间 > 本期窗口末）；**聚合正确性对账（审查 REPORT-COVERAGE-01）**：构造周 fixture（含跨上海周一 00:00 边界与改判样本）expected-vs-actual 全量对账 + 一个真实快照独立计算对账（UTC 周/周日起始等错误口径必失败）；live_acceptance 三态各一 fixture（不足 30 → pending；达标 → pass；超标 → fail+incident） |
| P4.3 渲染面、调度与通知 | **三个渲染器全部消费同一 canonical JSON、不各自查库（D16）**：HTML 报告（含 side-by-side/图表）、飞书摘要（`im-notify`，A1-A4 通道语义，不带 --dedup-key，附报告路径）、admin 面板"质量"板块（最新 JSON 摘要 + 反馈计数 + 当前 ruleset/criteria 版本）；cron 每周一次低峰（周一 03:1x 上海时区，结算刚结束的自然周；避开整点与 15 分钟 pipeline 高峰；marker 幂等安装样例进 docs、不自动写 crontab——perf-probe 先例）；指标越界（👎 率>10% 连续 2 期等）在通知中标红 incident 行（P5 输入），**首期只通知不自动 spawn**（DD-11） | 渲染器单测共用 golden JSON fixture；cron 命令幂等安装/卸载测试；通知 stub 断言含关键指标；`admin rerun-eval` 空壳（cli.py:494）借此实现或显式移除（不留假入口）；面板渲染测试（既有 admin 测试范式） |

### P5 半自动迭代 worker

**目标**：D3 的"agent 产出候选变更 + owner gate"。**首版保持薄**：手动触发为主，自动触发只到"通知"。

| 任务 | 内容 | 内部 verify |
|---|---|---|
| P5.1 quality-remediate | 复用 `performance/remediation.py` 骨架（隔离 worktree + fail-closed 边界 + 证据包 + 人工 gate），但**不照搬其 single-shot fingerprint 语义（审查 ITERATION-01）**——定义修订状态机：incident 身份稳定（规则 id + 触发窗口 hash）→ `candidate revision-N`（owner 退回后 resubmit 产生 revision-N+1，保留同 incident 的完整 revision/审批链，不得 `already_handled` 拒绝）→ approved/rejected 终态；**证据失效规则（显式继承 P4.1 切片语义——审查 EVIDENCE-SLICE-01）**：候选输入变化只作废对应切片的证据（prompt/ruleset→scoring 回放、weights/threshold→curation 反事实、判据→微信回放、corpus→引用该 corpus 的证据），未受影响切片的证据与既有审批继续有效；共享依赖同时变化才全量重跑；输入=P4 报告/incident JSON，产出=候选变更 commit + 副本库回归证据 + decision packet（**含 prompt 合规断言项**：候选 prompt diff 不含个人偏好规则清单/具体反馈样例引用——审查 POLICY-GUARD-01）；触发方式：owner 手动 `run.sh admin quality-remediate --incident <file>`；**骨架适配而非照搬（审查 WORKER-ISOLATION-01，现有 remediation.py 会把 `AI_RADAR_DB` 指向生产库并 `git add -A`）**：worker 环境只注入 P4 快照/证据路径（`AI_RADAR_DB` 指向快照副本，绝不注入生产路径）；orchestrator 按审过 diff 的 allow-list 精确 staging（遵循 create-commit staging 纪律，禁 `git add -A`） | 模拟 incident 干跑：worktree 隔离断言、产出物齐备断言、**worker 环境变量断言（AI_RADAR_DB=快照路径）**、生产零写断言、staging 清单等于 allow-list 断言；**退回-重提测试**：同 incident 连提 revision-1/2，链路完整、证据各自独立 |

### P6 文档同步与收尾

| 任务 | 内容 |
|---|---|
| P6.1 契约与用户文档 | ux-contract.md 按「UX 契约影响」节（含第 7 条全局条款同步）apply；CHANGELOG（用户可感知：分数修真、精选收紧、转载折叠、微信档位、owner 反馈功能）；README：功能段（feature flag 说明，开源中性措辞）+ **命令/服务快照表同步**（quality-eval 周任务、新 operator CLI 入口、链接 operations/quality-loop.md——审查 README-OPS-01） |
| P6.2 ADR | 新增：ADR-008 反馈采集与鉴权设计（D4/D5/DD-1/DD-3）、ADR-009 评分 v2 + 精选 v2 + 展示分修真（D2/D6/D10/D11）、ADR-010 VISION §4.4 应用条款修订（D2，prd/ 只读故走 ADR）、ADR-011 微信判据 v2 与版本化重跑（修订 ADR-007 的重跑/强制 save 条款，判定权归属不变）；adr/README.md 索引 |
| P6.3 运维与开发者文档 | **每个新 operator CLI 有指定文档入口且覆盖完整生命周期（审查 DOCS-01）**——operations/quality-loop.md（新文件）逐命令写：前置条件 / 生产零写确认方法 / 断点续跑 / 回滚 / 启停：`admin backtest-snapshot`、`admin quality-eval`（含 cron 安装/卸载）、`admin quality-remediate`、`interpret --rerun-criteria`、`score --item-ids 重评`；operations/services.md（quality-eval cron 条目）；operations/monitoring-alerting.md（飞书质量通知）；**operations/wechat-ingestion.md 与 operations/ai-assistant-integration.md 同步 P3 新语义**（版本化重跑、force/cache 行为、KB 零触碰边界、provenance 列、回滚——审查 DOCS-PATH-02）；architecture.md（feedback_events 表/新列、eval 引擎、display 映射、active-ruleset 读边界、superseded 读过滤）；experiences/llm-pipeline.md（prompt 锚点效果、判据迭代经验）；docs/CLAUDE.md 索引更新（新文件入索引；本次必然触碰该索引，一并修正已删除的 aihot-parity-contract.md 陈旧条目） |
| P6.4 harness/遗留 | 探查发现的 harness 级问题按协议 §4.8 落 `docs/issues/harness-issues.md`（如有）；serve-access.log 无轮转问题落 `docs/issues/general.md`（本期不修，DD 外遗留） |

---

## 6. L2：用户视角 verify（交付 gate，全部 implementer-executable）

标注 [A]=agent 可独立执行，[H]=需 owner 人工。人工项前置的自动化兜底已在各 phase 内部 verify 覆盖。

| # | 维度 | 可执行步骤与判据 |
|---|---|---|
| V1 [A] | owner 反馈 happy path | 本地 serve（ALLOW_LOCAL 模拟 owner）+ Playwright：四个 surface 各打一条"值得读"+ 一条带摘要标记的"不值得读" → 8 行落库，context_json 含 run_id/display_score/ruleset（精选卡）与 recommendation/criteria_version（微信卡）；重复点击同键 → 更新非重复插入 |
| V2 [A]+[H] | 三态鉴权 | (a) 匿名：无 cookie Playwright 四个 surface DOM 无反馈控件 [A]；无 cookie POST 生产端点 → 非 2xx（D13 已授权，计 1-2 次生产诊断请求）[A]；(b) 已认证非 owner：P1.0 的 allow-list 权威证据（仅 owner 邮箱）在档 [A 取证/H 兜底]；(c) owner 放行：G1 标注 session 首次成功提交即实证 [H]；本地无 header POST → 401/403 [A] |
| V3 [A] | 展示分修真 | **两段式（审查 PROD-ACCEPTANCE-01）**：(a) 上线前副本预检 + (b) **生产切换后对真实生产读路径复跑同组断言（最终 consumer gate，不得以副本通过替代）**：任取一日精选，display 逐值等于 `round(raw/scale*100)`（精确公式而非仅单调、且从 active ruleset 评估推导）；无 F3 倒挂；同一 item 在**相同 raw 与相同映射版本下**跨 run display 不变（raw 合法变化不算失败——审查 UX-VERIFY-02）；/ 与 /all 同一文章分数同值同色（F4 消除）；页面色阶与 G2 定标的最终阈值一致（前端契约测试）；tooltip/about 文案与新机制一致（grep 断言）；**跨版本可比（D18）**：可见归档条目 100% 有 active-ruleset 评估（count 对账） |
| V4 [A] | 精选质量闸门 | 上线后连续 3 个 run SQL：所有成员 raw ≥ 新 threshold；无"quota 塞入 <threshold"行；允许某 run 条数 <40（出现即证明不凑数；30 天内曾有低质日，若观察窗内恰好全达标，用副本重放历史低质日证明会产生 <40 的选集） |
| V5 [A] | 区分度改善 | **条件化两档（D21）**：目标档=span≥20、stdev≥8、任一日候选 40 条内 unique raw ≥30；最低上线档=unique≥20 且 span/stdev 不低于 P0.3 基线——G2 决策包注明实际达到的档位；评分单元 fail-closed 时本条 N/A（改为断言 current_version 未切换）；一律用 P0.3 canonical 口径计算；[A] G1 种子标签上新选集 precision@selected ≥ 旧选集 |
| V6 [A] | 转载折叠 | F6 实证组（InfoQ/AI前线 同文）在精选归档折叠为一条；**D24 状态转换断言（审查 FOLDCARD-VERIFY-01）**：初始仅显示"另有 1 个来源"计数、来源名隐藏；点开后来源名集合与该 duplicate_group 成员完全一致；390px 视口同流程通过；**守恒审计**（P2.4）对最近 3 个 run 通过：独立重推的 expected groups 与记录一致、候选数守恒（仅"无同组双条"不足以证明漏识别为零） |
| V7 [A]+[H] | 微信档位 | 重跑范围内条目 criteria_version='v2' 且档位分布进目标带（必读 5-15%、值得一看 20-40%，SQL）；**G3 选"仅未来"时本断言改为：累计新解读 ≥50 条后再执行（空集不算通过——审查 UX-VERIFY-02）**；[H] owner 抽读 10 条新档位+理由，认可方向（G3 已含 side-by-side，此处是上线后复核） |
| V8 [H] | 标注 session | owner 在 /admin/label 完成 ≥150 条标注（G1）；[A 兜底] 标注页键盘流 Playwright 全通过后才请 owner 进场 |
| V9 [A] | 报漏归因 | 提交 4 类构造案例（未抓源 URL/被 prefilter 拒的真实 item URL/低分 item URL/转载副本 URL）→ 归因分类各自正确（SQL 查 context_json） |
| V10 [A]+[H] | 周期评估 | [A] 手动触发 quality-eval → canonical JSON 过 schema 校验 + 三渲染面产出 + 飞书 stub 内容断言 + 快照新鲜性断言；cron 安装幂等；[H] G4：owner 按结构化 rubric（指标齐全/口径可读/可据此决策/通知形态）逐项回复 |
| V11 [A] | 回测引擎安全与有效 | 引擎在副本上重放近 30 天：产出选集 diff 报告；生产库前后校验和不变（或 mtime+PRAGMA data_version 不变）；对生产默认路径不带 --production 拒绝执行，**symlink 别名指向生产库同样拒绝**（realpath guard） |
| V12 [A] | 迭代 worker 干跑 | 模拟 incident → worker 隔离 worktree 产出候选 commit + 证据包 + decision packet（P6 packet 六要素齐备：目标/选项/证据/路径/动作/preflight）；生产与主 checkout 零写 |
| V13 [A] | Preservation（不回归） | 全站排序仍 published_at DESC（D6，SQL+DOM）；/all 可见性行为除阈值重定标外逻辑不变；搜索/分页/微信详情/关于页既有测试全绿；golden harness 按 re-baseline 规则通过；journey probe 各路由不超既有阈值；首屏无新增阻塞请求（feedback ping 异步验证：Playwright 断言首屏渲染不等待 ping） |
| V14 [A] | 开源 gate preservation | `git grep -nE 'lindong28|aiplanet\.live|dong_lin|/Users/lindong|/research/ai-assistant'` 对新增 tracked 文件零命中（ux-contract.md 豁免）；`AI_RADAR_ENABLE_FEEDBACK` 默认 OFF 时全部新端点 404/关闭、UI 无痕 |

## 7. 用户决策 gate（decision packet 规格）

| Gate | 时机 | 决策目标 | 材料（最短路径） | owner 动作 |
|---|---|---|---|---|
| G1 标注 session | P1 过 (A2,V2) 安全 gate 并**单独部署上线后**（D14） | 产出 150-200 条种子 ground truth（G2/G3 必要输入） | 打开 `https://aiplanet.live/admin/label`（真站，Access 内，手机可用）；页面自含进度与快捷键说明；预填抽样队列；preflight：implementer 先以 Playwright 全流程通过（V8 兜底） | 投入 2-3 小时标完；回复"标完了" |
| G2 评分/精选上线 | P2 副本实验完且 G1 已完成 | 批准新 ruleset+curation v2+展示修真上线；定 threshold 与色阶数字 | 单页 HTML 决策包（本地 http 链接，绑 0.0.0.0）：近 7 天逐日 before/after 精选 side-by-side（**逐样本 ballot 维度钉死（审查 GATE-RUBRIC-02）**：①该文应否入选 Y/N ②新/旧哪个判得更准 new/old/tie ③错误类型枚举[相关性/密度/时效/权威度/其他]；**汇总进 gate 规则**：批准条件 = "new 更准"票数 ≥ "old 更准" 且 precision@selected ≥ 旧选集 且无被标"应入选却双双漏掉"的 critical 样本积压）、分布指标对照表（P0.3 基线 vs 新，注明达到 D21 哪一档）、threshold 候选 2-3 档各自的日均条数模拟、逐样本 provenance 汇总（model/ruleset/无 heuristic 证明）、prompt 合规断言项、风险与回滚说明 | 选 threshold 档位 + **选色阶候选组** + 批准/退回 |
| G3 微信判据上线 | P3 样本重跑完且 G1 已完成 | 批准判据 v2 上线；选历史重跑范围 | 单页 HTML：30-50 条旧/新档位 side-by-side（**ballot 维度钉死**：①新档位是否正确 Y/N ②理由是否符合阅读价值判据 Y/N ③新/旧哪个更准 new/old/tie；**汇总进 gate 规则**：批准条件 = 新档位正确率 ≥80% 且 "new 更准" ≥ "old 更准"）、分布对照、种子标签（微信条目）一致率、范围选项（仅未来 / 近 7 天≈¥X / 近 30 天≈¥Y / 全量≈¥Z，金额来自 P0.4） | 按 ballot 逐样本填 + 批准/退回 + 选范围 |
| G4 首份周报 | P4 上线后首个自然周 | 确认报告形态可用 | 飞书通知 + canonical JSON 渲染的 HTML 报告路径；**结构化 rubric**：指标齐全性 / 口径可读性 / 可据此决策 / 通知形态 四项逐项 OK/改进 | 按 rubric 逐项回复 |

**Gate 材料统一 preflight（审查 HANDOFF-01，请 owner 进场前 agent 自查）**：每个 gate 交付=最终 URL/文件路径 + 内容清单（决策所需材料逐项在场）+ `$agent-browser` 打开 URL 断言关键元素可见（本地 server 一律绑 0.0.0.0）+ artifact hash 记录进 `evidence/`；G2 的 owner 动作除 threshold 外**须含色阶候选值选择**（2-3 组候选并附示例渲染）；P4 部署时**先发一次真实飞书测试通知并确认送达**（transport 验证不留到 G4 才暴露）。

## 8. Defaulted Decisions（planner 自拍，供 reviewer 与 owner 审）

| # | 决策 | 默认值 | 理由 | 触发调整 |
|---|---|---|---|---|
| DD-1 | 反馈端点前缀 | 复用 `/api/v1/admin*`（现有 Access policy 覆盖假设，P1.0 取权威证据核实） | 零新增 Access 配置 | P1.0 证据显示未覆盖或 allow-list 过宽 → 按 `docs/operations/monitoring-alerting.md:142-155` runbook 加 path 规则/收紧成员后再继续 |
| DD-2 | 评估节奏 | 每周一次（Asia/Shanghai 自然周结算，周一凌晨低峰跑，D15），错开整点 | 单人反馈样本薄，周粒度才有统计意义 | 反馈量持续 >20/周可加密 |
| DD-3 | 反馈存储 | 主库新表 `feedback_events`（不拆库；旧 feedback 表 0 行保留不用，避免每次重跑的 migration 机制下 rebuild 风险）、不 bump archive cache generation | 写频率极低（日常个位数/天；标注 session 峰值 ~200 单行小事务）；反馈不影响公开展示故不失效缓存 | 若未来反馈参与展示（隐藏条目等）再接 generation 语义 |
| DD-4 | 折叠范围 | 精选页折叠 + interpret 跳过转载副本；/all 不折叠 | timeline 定位是全量流；surgical scope | owner 反馈 timeline 重复扰人 → 加折叠或标记 |
| DD-5 | 展示分映射与 backfill | `display=round(raw/12.5*100)` 固定线性；历史 curated 行重算 backfill（原值保留在 reason_json 可回滚） | 跨天可比（D11）；不 backfill 则归档页新旧两代分数混排，重蹈 F4 | 色阶阈值在 G2 由真实分布定标 |
| DD-6 | 微信重跑默认范围 | G3 样本 = 近 7 天抽 30-50 条；正式范围由 owner 在 G3 选 | 全量 ≈76M token 成本高，须见数字再拍 | — |
| DD-7 | 标注数据分层 | source 列区分 organic/label_session/missed_report | 回测可分层（集中标注与日常反馈口味权重可能不同） | — |
| DD-8 | （已升格为 D24） | 折叠展示形态由 owner 锁定：计数+点开展示 | — | — |
| DD-9 | （已升格为 D23） | LLM judge 补位取消，evidence insufficient 如实报告 | — | — |
| DD-10 | feature flag | `AI_RADAR_ENABLE_FEEDBACK` 默认 OFF | 开源 fork 中性（外部能力默认 OFF 惯例） | — |
| DD-11 | P5 自动化程度 | 首版手动触发；指标越界只通知不自动 spawn | 避免过度建设；VISION"不自动应用"精神 | 跑顺 2-3 轮后可把通知升级为自动 spawn（仍人工 gate 应用） |
| DD-12 | 评分模型 | 保持 deepseek-v4-flash | prompt 迭代先行，单变量归因；模型升级留作 P4 引擎的备选实验轴 | P2 重评若 flash 无法达标分布判据 → G2 包内附 v4-pro 对照样本供选 |
| DD-13 | rank 字段语义 | curation v2 保留 rank 记录（按 raw 排名）但展示不依赖 | 回测与"精选徽章"仍需成员资格序 | — |

## 9. 风险（acceptance + trigger response）

| 风险 | 接受理由 | 触发响应 |
|---|---|---|
| R1 生产读路径不筛 ruleset（F9），实验误跑生产库即污染线上 | 已证实，不可带病实验 | **无影响替代已内建**：P0.2 replica guard + 所有实验显式副本路径 + P2.5 步骤 (0) dormant 读边界先行；若仍发生：按 P2.5 回滚契约翻转 active version + supersede 污染 run + 重跑 curate，journal 记 incident |
| R2 与 perf-safeguard 并发 session 冲突（app.py/curated_archive/pagination/app.js/ux-contract/crontab 重叠面） | 两 session 并行是 owner 已知现状 | 实施全程独立 worktree；合并前 `git fetch`+rebase 并逐文件核对重叠面；部署窗口先与 owner 确认另一 session 状态；serve 重启等 `.pipeline.lock` 释放（撞锁曾致端口 down，memory 教训） |
| R3 DD-1 Access 覆盖假设不成立 | 文档记载覆盖 /api/v1/admin*，未实测 | 见 DD-1 触发调整（runbook 加 path 规则，配置层面可逆） |
| R4 ARK 额度不足/熔断，批量重评中断 | 预付额度有限；breaker 既有 | 成本先算（P0.4）+ 范围 owner 拍（G3）；断点续跑设计（P3.2/P2.1 均按 ruleset/criteria 幂等跳过已完成项）；DeepSeek 余额不足伪装 404 的既有 gotcha 写入重试逻辑判断（memory：deepseek-balance-404-gotcha） |
| R5 新 prompt 分布仍坍缩（LLM 不听锚点） | prompt 迭代效果无法先验保证 | 预案已在 D21 预授权，无临时决策：3 轮迭代 + v4-pro 对照 → 达最低幅度（unique≥20 且 span/stdev 不低于基线）即上线；连最低不达 → 走 P2.5 评分单元 fail-closed 分支 |
| R6 微信侧 KB 损坏（index.json/vectors.npy 非原子） | ADR-007 已知约束 | **历史重跑完全不写 KB（D19），该风险仅存于未来增量路径**：串行硬约束 + 例行备份 + 前后 count/抽查断言；损坏即从备份还原 |
| R7 feedback 写撞 pipeline 写锁 | SQLite 单写者，15 分钟 cron | busy_timeout=5000 + 503/Retry-After 语义 + 前端重试提示（P1.2）；标注 session 避开整点 pipeline 高峰（页面提示） |
| R8 单人反馈样本小，回测过拟合 | 单户产品本质 | VISION 防护保留（D2 仍禁个人规则清单入 prompt）；报告样本量警示（P4.2）；迭代一律 owner gate |
| R9 历史评估基线异质（模型切换/heuristic 混入/OpenAI Blog 单日回填 1037 篇污染） | 历史数据既成事实 | 回测分层：按 model_id 分层、排除 raw.provider='heuristic'；回填异常**双口径报告（审查 EVAL-FILTER-01，owner 已拍）**：正常层（剔除 fetched/published 偏差 >30 天日聚簇）为主 gate 口径，异常层保留并附敏感性结果——迟抓故障不被静默隐藏；写进 P4.1 引擎的默认口径定义 |

## 10. UX 契约影响（有影响；给 execute-plan 的 apply 指令）

产品有 `docs/contracts/ux-contract.md`。本 plan 的用户可感知变更须投影（P6.1 执行；已批准意图，apply 即可，不新增本节未记录的改动）：

1. **分数展示语义**（涉及 HP-*/TL-* 分数条目）：展示分=绝对分（raw/12.5×100 固定映射、跨天可比）；色阶阈值更新；同页单一量纲。L2：V3。
2. **精选构成**（精选页 section）：每日条数随质量波动、可 <40 或空；成员一律 ≥threshold（quota 不再放行低分）。L2：V4。契约措辞从"每轮 40 条"类描述改为质量闸门描述。
3. **转载折叠**（精选页卡片）：重复组折叠为主卡 + "另有 N 个来源"计数，**点开后展示来源名列表（两态转换是契约的一部分，D24）**。L2：V6。
4. **微信档位**（WX-* 系列）：档位判据为阅读价值导向 v2、分布目标带（徽章区分度是变更点）；**列表收录条款（WX-3）不变**——仍 save_decision=1、偏召回、可含少量"可跳过"（D19，审查 WECHAT-CONSISTENCY-01 修正：本 plan 不收紧列表收录，只修徽章判据）；ux-issues.md 中已 de-scope 的"值得读判定合理性"一条被本 plan 有意收回（owner 访谈 D9 授权），按协议 §4.6 记录演化理由。L2：V7。
5. **新增 owner 反馈 surface**（新 section）：四 surface 反馈控件（owner-only 可见）、报漏入口、/admin/label 标注页；匿名访客零可见变化。L2：V1/V2/V8/V9。
6. **保持不变声明**：排序时间序、页面结构、搜索/分页行为均不变（V13 preservation 断言）。
7. **契约全局条款同步（审查 UX-CONTRACT-02）**：ux-contract 顶层"产品形态"从"公开只读"改为"公开只读 + owner-only 反馈写面"；Personas/use-path 增补 owner 反馈与标注旅程；范围排除条款中与 admin 面/微信判定质量相关的 de-scope 表述**逐处检索修订**（全文 grep "只读"/"de-scope"/"收录偏召回"相关措辞核对一致性），避免局部 section 更新后 whole-product contract 自相矛盾；owner-only 写面、/admin/label、质量周报的验收范围界定为"owner 视角条款，不进入匿名访客验收路径"。
8. **新增"owner 质量闭环"section（审查 UX-CONTRACT-03）**：描述 admin 质量板块、周报（canonical JSON→HTML）、飞书通知三个 owner 消费路径的可观察行为，验收 lens 引用 G4 四项 rubric（指标齐全 / 口径可读 / 可据此决策 / 通知形态）。L2：V10。

## 11. 实施与部署协调（硬要求）

1. **worktree**：实施 session 开始时 `EnterWorktree`（或 `git worktree add`）建独立分支；plan/state/journal 用主 checkout 绝对路径（本目录不随 worktree 复制）。
2. **测试隔离**：一切测试 `AI_RADAR_DB` 指向临时/副本路径；Playwright 走隔离备份库（既有 conftest 机制）；严禁 import-time create_app/migrate 打生产库（perf session 两次 incident 教训）；生产只读诊断请求预算已被 U5 封顶——不对 aiplanet.live 发探测流量（V2 的一次边缘验证除外，且可用 CF 面板确认替代）。
3. **合并与部署**：merge 回 main 前 rebase + 核对 R2 重叠面；commit 按 `~/.claude/skills/create-commit/SKILL.md`（不加 Co-Authored-By）；push 需 owner 显式许可；部署=生产 checkout 更新 + 等 `.pipeline.lock` 释放后 `launchctl kickstart -k` serve；**五个部署窗口（审查 DEPLOYMENT-TOPOLOGY-01），各自带 preflight（golden harness/冒烟）与回滚点**：W1 = P1 过安全 gate 后（D14，解锁 G1 真站标注）；W2 = G2 批准后部署 dormant active-ruleset 读边界（行为等价，golden 验证）；W3 = 影子写完成验证后切换 current_version=v2 + curation v2 + 展示修真（回滚=翻回 v1）；W4 = G3 批准后微信判据切换；W5 = P4 上线（cron 安装 + admin 质量板块 + 真实飞书测试通知）——每次均与 owner/并发 session 协调。
4. **migration 纪律**：新 migration 015+ 幂等、可高频重跑；绝不动 003/004；在生产形状副本上验证（冷库会跑 004 给 false green）。
5. **跨 repo**：ai-assistant 改动在该 repo 独立 commit（其 review 惯例照走）；ai-radar 侧只依赖其文档化接口（schema 解析契约）。

## 12. Scope 外（明确不做）

- prefilter prompt 迭代与 confidence 利用（未来闭环目标，本期只在报漏归因中读取其记录）。
- embedding/语义聚类去重（规则先行；折叠机制留好 duplicate_group 扩展位）。
- 排序改动、IM 推送、多用户反馈、候选池审计页（D6/D7 已裁）。
- serve-access.log 轮转与隐式阅读信号（落 issues 记录，本期不做）。
- 历史 heuristic/异质评估行清洗（回测过滤即可，不改历史数据）。

## 13. TODO（implementer 自由度内的工作，不指定 how）

- 反馈控件视觉样式与移动端适配细节（对齐现有 style.css 体系）。
- 标注页抽样脚本的具体分层比例微调。
- 报告 HTML 模板与飞书消息排版。
- G2/G3 决策包 HTML 的具体版式（须满足 P6 packet 六要素 + 本地 server 绑 0.0.0.0）。
