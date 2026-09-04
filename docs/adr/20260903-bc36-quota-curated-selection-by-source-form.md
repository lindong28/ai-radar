# 精选按来源形态配额：每轮 X 推文 ≤20%、单源 ≤7.5%，同轮记录无配额基线并支持定向回退

- Status: accepted
- Date: 2026-09-03
- Supersedes (partial): [ADR-010](./010-db-slimming-clear-regenerable-cache.md) 中「历史 `curated_items` 行不删」这一条，仅对本 ADR 引入的**配额独有行**（`reason_json.source_quota.baseline_selected=false`）由 `admin curate rollback-quota` 定向删除；其余历史行保留义务不变。ADR-010 援引的 feedback-loop 回测依赖已随 program 20260820-content-align 前提弃用。
- Relates: [ADR-006](./006-curated-archive-mode.md)（精选页是跨 run 并集：每轮配额不等于归档配额）

## Context

program 20260820-content-align 以 AIHOT 当前内容效果为对齐目标。精选对齐诊断（2026-09-02，`.label-serve/round45-human/curation-gap/report.md`）显示两站精选分歧的主杠杆是**来源形态**而非分类：我站近窗精选并集 733 条中 X 推文占 70.7%、单源 `X：Rohan Paul` 占 14.7%；AIHOT 对 x.com 的选中率 1.1%、精选集里 x.com 占 19.7%、非 x 单域最高（ithome）9.1%、官方域 20–44%。v1 打分公式无类目维度、tier 乘数仅 ±25%，而 v2 分类（r2）覆盖率 1.5%，短期撬不动结构性差距。用户裁决走「精选后置按来源形态配额」（方案 B），本 ADR 定其具体形态与参数。

## Decision

1. `curate()` 两段填充（fresh 池→filtered 池）改为**准入时判配额**：每轮 `kind=x` 最多 `max(1, round(limit×0.20))`（默认 8/40），单一 `source_id` 最多 `max(1, round(limit×0.075))`（默认 3/40）；被跳过的名额由后续候选按既有排序补上（fresh 段继续沿 fresh 池下探，再到 filtered 段，即「scan」填法）；硬上限，候选不足时如实少选。打分、阈值、freshness 机制、rank-linear 校准不变。
2. 配额开启时同一轮**再填一遍无配额基线**：每条 `curated_items.reason_json.source_quota = {policy:"source-quota-v1", kind, kind_cap, source_cap, baseline:"same_run_without_source_quota", baseline_selected}`——`kind_cap` / `source_cap` 是本轮生效的整数上限，**该 kind 未配置配额 / 无单源上限时写 `null`**（不是 limit，审计者据此分得出「未限制」与「上限恰等于 limit」）；`baseline` 常量键点明反事实定义（同轮、同候选与排序、仅关闭来源配额）。基线独有条目写入 `curation_runs.shadow_json = {policy, baseline, score_semantics:"tier_adjusted_before_rank_calibration", baseline_only:[{item_id, raw_weighted_score}], quota_only_count}`（migration 021 加列，唯一的表结构改动）；`raw_weighted_score` 是 tier 乘数之后、rank-linear 校准之前的分。
3. 默认开启；`AI_RADAR_CURATE_SOURCE_QUOTA=off`（生产回退面，pipeline.sh 不带参数）或 `./run.sh curate --source-quota off` 停用后续轮。
4. 归档回退 = `admin curate rollback-quota --since <run_id> [--dry-run]`：逐 run 事务化：先按冻结的 v1 形状整体校验（`shadow_json` 的 policy/baseline/score_semantics/`baseline_only` 条目/`quota_only_count`，每行 `source_quota` 的六键与类型，且 `quota_only_count` 必须等于 `baseline_selected=false` 的行数；任一不合即该 run 零写入并报错——早期草稿形状的 run 因此不会被当作干净 run 回退）→ 删除配额独有行 → 剩余行**保持原 rank 相对次序**连续重编号 → 重跑 rank-linear 校准（剩 1 行时按 `curate()` 单行语义：raw 分、无校准块）→ 清 `summary_json`（最新 run 随即重算）→ 改写 `output_curated_ids` → `shadow_json` 追加 `rollback:{at, removed_item_ids}` 并把 `quota_only_count` 改为回退后实际值（0），使 run 级计数与逐行标记不漂移、被删身份可审计。没有配额独有行的 run 是纯 no-op。`--dry-run` 以只读连接预览、不 migrate、不改任何精选行（SQLite 仍可能为 WAL 库创建空 `-wal/-shm` sidecar）。基线独有条目不补回（用户接受：它们正是要压的 X 条目）。这是「撤除配额新增」，不是完整反事实恢复。
5. 验收仪器（用户 2026-09-03 09:30 指令）：**判官阶梯**——gained=Q−B 与 lost=B−Q（窗口级差集）各 n=60（下限 30）由两个判官（J1 glm-5.3@ARK、J2 deepseek-v4-pro@ARK）绝对评分，一致即读数；分歧由 J3=Claude Code（`claude-fable-5-1`，裁定指令记 SHA）裁定并记置信度；不确定项交用户附推荐。主指标 Δ=worth 率(gained)−worth 率(lost)，随机基线 0；阈值 `T=max(floor, 2σ(n))`，floor=0.15、σ=√(2·0.25/n)、n=min(n_gained,n_lost)，单边；阴性对照 Δ_neg（lost 分半）只作有效性闸：|Δ_neg| > 2σ_neg 判仪器无效需重抽，不进阈值（2026-09-04 用户裁决，替换原 `k_bias·|Δ_neg|` 项——对抗审算得该项把 20pp 真劣化的漏判率抬到 63%）；首轮 n≈53/60 即跑，每组 n≥100 时复跑确认；可用性闸 fail-closed：阳性对照 ≤0.20、两判官一致率 ≥0.70、人工锚（用户一次性盲评 K=60 固定样本，阶梯最终判定与用户一致率）≥0.80、invalid ≤0.05。触发：上线后第 3 自然日或 gained ≥60；不确定项 7 日未答 → 标「未验收（配额仍开）」，不自动回退。判「变差」→ off + rollback。

## Rejected alternatives

- 打分加 r2 类目因子 / r2 双轨 ruleset：受 r2 覆盖率 1.5% 制约，留后。
- 选完再截断：只减不补，每轮少于 40 条。
- 在 `score.py` 给 X 加乘数：把形态偏好混进 weighted_score，影响阈值语义与展示分。
- 按 tier 配额：tier 与 AIHOT 选中率非单调。
- fresh 段「slice」填法（配额只在 top-36 内判）：仿真并集 536→254、AIHOT 命中 16→15 低于基线。
- 删整轮 `curated_items` 行作回退：会连带删掉基线也会选的条目（仿真 470 条中 231 条）且使 `output_curated_ids` 失配。
- 人评对照用 shared（Q∩B）：区分不出「配额不如基线」与「边际不如核心」。

## Evidence

只读回放仿真 `plans/20260820-content-align/artifacts/sim_quota.py`（`published_at` 从 scoring prompt 还原评分当时值，fresh 规则与生产一致，36 时点，候选 19171 生成时读数）：none 并集 537 / AIHOT 精选命中 16/55；最终变体 432 / 23，每轮 X 0.70→0.20，首源 IT Home 9.7%，0 空位；gained 206（AIHOT 精选 8、T2 152、v1 分 ≥6.5 仅 14.6%）vs lost 311（AIHOT 精选 1、T1.5 X 283、≥6.5 79.1%）——两把尺子方向相反，读者感知质量只能靠第 5 条验收。单源上限敏感度：命中 22–25 不区分，只调首源集中度（无上限 18.9%→3 条 9.7%）。

## Scope and unverified

- 成立范围：`limit=40` 默认调用、enabled 源池 x109/feed34/web18、08-25~09-02 候选分布；源池或 limit 改动需重跑仿真。
- 未验证：AIHOT `selected` 语义 ≡ 我站 `curated`；仿真按每天 4 时点回放；配额后新增条目的读者感知质量（由第 5 条验收）；候选池不足日的少选行为。
- API：`reason.source_quota` 是 `/api/v1/curated` 与 timeline `reason` 字段的加法式嵌套扩展；前端只消费 `reasoning`。
- 已知边界（review-gate 对抗审残留，用户裁定收口）：`--source-quota off` 逐字节复现 HEAD 选择，**唯一**例外是 fresh 池含重复 `item_id` 时（HEAD 会重复入选、新实现去重）——生产加载链按 `items.id` 主键与 dedup 保证不可达；`rollback-quota --dry-run` 走只读连接、不改任何精选行，但 SQLite 仍可能为 WAL 库创建空 `-wal/-shm` sidecar。

## Acceptance record

- **首轮（2026-09-04，窗口 09-03T03:50Z→09-04T01:31Z，n=53 gained / 60 lost）**：结论「不可用（闸未过）」。判官侧 gained worth 0.623 / lost 0.467、Δ=+0.156（T=0.194）、阳性对照 0.033、一致率 0.769、invalid 0.024、阴性对照 0.067——四闸过；人工锚（用户委托 Claude Code 代评 60 条，5 条经用户补票）与阶梯终判一致率 0.717（非自证部分 0.622）<0.80 未过。诊断：分歧为结构性——判官把只有标题的研究帖判不值得、把例行发布说明判值得，人评相反；按人评尺子 Δ_anchor=+0.467。两把尺子均无「变差」迹象。用户裁决：修订判官评分定义（只有标题按题材价值评；例行版本发布说明/变更日志不值得）后每组 n≥100 复跑，配额保持开启。仪器与产物在 `.label-serve/quota-accept/`（不入 git）。

## Decision review

外部决策评审（Codex 只读）：首轮 7 判据 → 4 blocker（仿真候选集/参数区分度/ADR-006 归档交界/质量验收）→ 两轮复核 → 用户裁决定向回退与单源 0.075 → 用户裁决重走完整 gate（新评审 session 01a064c8）：1 blocker（回退次序）+ 3 应修 → 四轮复核（对照组改 lost、阈值算术、人工锚口径、J3 身份）→ **放行**（2026-09-03 10:0x）。全程 packet 见 `plans/20260820-content-align/artifacts/curation-b-source-quota-design.md`（v3.6）。
