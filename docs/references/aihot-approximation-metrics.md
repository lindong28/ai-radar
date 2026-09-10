# 衡量「与 AIHOT 的近似度」用哪些指标 [Developer]

> Mutable snapshot。**这份档只做索引与分辨力说明**——每个指标的权威定义、达标线依据与历史读数仍在各自的
> ADR 与 `docs/issues/aihot-fit-eval.md`，此处不复制数值以免漂移，只标它住在哪、有没有闸、判不判得动。
>
> 建这份档的直接原因：2026-09-10 用户问「我们用哪些指标衡量近似度、是否都已文档记录」，而当时答案是
> 「每个都有文档，但没有一处列全」——`docs/architecture.md` 没有评测章节，指标散在三份 ADR 与一份 issue 台账里。

## 两个家族，回答的是不同问题

| | A · 逐条拟合（`aihot_fit` 评测台） | B · 版面构成（`scripts/eval/` 脚本） |
|---|---|---|
| 问的问题 | 同一条内容，我方与 AIHOT 的**逐条判定**有多接近 | 我方整页的**类别构成**与 AIHOT 精选有多接近 |
| 样本单位 | 条目（题集 2741 条） | 页面 × 日窗（参照物 9 窗共 **122 条精选**） |
| 有无达标线 | **8 条设闸**，floor 在 `benchmarks/aihot/evalsets/aihot-fit-v1/thresholds.json` | **无闸、无达标线** |
| 分辨力 | 足够（n 大） | **绝对值判不动**，见下 |
| 权威定义 | [ADR-20260905-499e](../adr/20260905-499e-aihot-reference-fit-eval-system.md) | [ADR-20260910-3f8b](../adr/20260910-3f8b-demote-papers-in-the-ordering-key-only.md)、[ADR-20260910-9e21](../adr/20260910-9e21-pin-the-category-snapshot-with-one-integer.md) |

## A · 逐条拟合指标（`src/airadar/eval/aihot_fit/metrics.py`）

| 指标 | 设闸 | 量什么 |
|---|---|---|
| `ai_recall` | ✅ | prefilter 对 AIHOT 收录过的条目的召回 |
| `category_agreement` | ✅ | 主类逐条一致率（含混淆矩阵） |
| `tag_jaccard_mean` | ✅ | 标签集合的 Jaccard |
| `score_spearman` | ✅ | 我方分 vs AIHOT 分的秩相关 |
| `summary_closeness_mean` / `summary_bigram_jaccard` | ✅ | 摘要的判官贴近度 / 字面重叠 |
| `reason_closeness_mean` / `reason_bigram_jaccard` | ✅ | 推荐理由同上 |
| `selected_auc` | ❌ 明确不设闸 | 我方分预测 AIHOT `selected` 的 AUC |
| `selected_auc_ranked` | ❌ 明确不设闸 | 同上，但用**生产真实排序分**（含类别系数）。2026-09-10 新增 |
| `selected_p_at_k` | ❌ 明确不设闸 | 按天取 top-k 的命中率 |

后三条不设闸的理由写在 `thresholds.json` 的 `_meta.not_gated`：正例只占 2.8%，迭代规模下区间宽到
floor 会低于随机基线。**`selected_auc_ranked` 已加进 `scripts/derive_eval_fit_thresholds.py` 的 `NOT_GATED`，
但 `thresholds.json` 尚未按新代码重新生成**，故该文件目前还没提到它。

**达标线怎么定**：floor 取**迭代规模**（n=300）基线的 CI 下界，不是全量的——规则与陷阱见 ADR-499e
「达标线怎么定：必须与它将被施加的样本量成对」。

## B · 版面构成指标

| 入口 | 量什么 | 关键限定 |
|---|---|---|
| `scripts/eval/measure_curated_composition.py` | 合并多日窗的构成总变差（TV），**两侧都用 AIHOT 的标签** | 见下三条 |
| `scripts/eval/measure_live_composition.py` | **线上真实页面**的构成（读首页内嵌 JSON） | 线上比本地滞后最多 5 小时；首页 90 秒边缘缓存 |
| `scripts/eval/characterize_aihot_categories.py` | 刻画参照物自己的类别抬升 | 其结论已被「控制分数后效应反转」推翻，见 issue 台账 |

**读 B 家族之前必须知道四条**（每条都是踩过的）：

1. **绝对值判不动。** 合并 TV 的零假设（两侧构成完全相同、仅样本量作用）中位约 0.09、95% 分位约 0.16；
   对齐深度下实测落在 **p≈0.22**。脚本现在每次运行都打印零假设与 p 值。**配对比较不受此限。**
2. **逐日 TV 饱和于抽样噪声**（参照物自比 ~0.27）。只读 `POOLED`。
3. **截断深度是混淆变量**：我方 40 条/天 vs 参照物十几条。`--depth aihot` 对齐后再比。
4. **跨标注器比较另有地板**：我方分类器与参照物标签的逐条分歧本身值 ~0.13 的 TV。用 `--labels off`
   取只用参照物标签的口径（历史读数都是这个口径）。

## 拿这些指标怎么迭代

分两层。**内层有代码和闸，外层只有纪律**——两层都写在这里，是因为外层此前唯一的载体是执行台账
（`plans/<program>/program.md`，`/plans/` 不入 git 且按收口流程会被删除），于是"目标该怎么逼近"这件事
没有 tracked 落点。

**外层（任务级，用户 2026-09-08 指定的方法，与目标同一句话）**：

> 基于长期目标判断现状与目标的 gap → 对问题归因（是哪个模块的产出有问题；问题在上游模块、还是本模块
> 优化不足、还是本模块的设计导致拟合能力不够）→ 针对最可能是根因的模块做实验 → 过程中持续做**可复用**的
> 投资（更多评测数据、记录做过的实验与结果、越来越靠谱的 llm-as-judge）来逼近目标。

目标本身是「在用户可见的那些指标上足够接近 AIHOT」。**「足够接近」没有数值定义**——`thresholds.json` 的 8 条是
**回归下限**（防变差），不是达标线（判达没达到）；ADR-499e 明写「达标线本轮不写」，此后未补。

**内层（评测台级，权威在 ADR-499e「怎么用它迭代」）**：

| 环节 | 机制 |
|---|---|
| 固定尺子 | 题集 `questions_sha256` + 抽样 `subset_sha256` + 判官身份三者全同才可比，任一不同即拒比 |
| 固定抽样 | 先按 `question_id` 排序再 `random.Random(seed).sample` ⇒ 固定 `--limit/--seed` 即可复现同一子集 |
| 迭代档 | `eval-fit run --limit 300 --seed 7`（约 900 次调用）；理由维度 `--limit 78 --require-reference reason`（参考侧完整总体）；精选排序只有全量 run 才有足够正例 |
| 判官每轮自证 | `judge --calibrate 20` 跑阳性（候选=参考自身）与阴性（候选=另一题的参考），`scale_ok` 三态 |
| 判改善 | 两个 CI **不重叠**才算 improved（旧判据"下界 > 基线点估计"实测假阳性 9.0%，新判据 0.4%） |
| 判回归 | 设闸指标 CI 下界确认低于 floor ⇒ `report` exit 1 |
| floor 怎么定 | **必须与它将被施加的样本量成对**：取 n=300 迭代档的 CI 下界，不取全量的——区间宽度随 √n 收缩，拿全量下界去要求一次 n=300 确认会在**零回归**时开火 |

**这条回路当前断在三处**（2026-09-10 实测，是现状不是设计）：

1. **没有定时作业**（`launchd` / `crontab` 零评测项）⇒ `paper: 0.95` 于 09-10 上线，而 A 家族最后一次带
   metrics 的 run 是 09-08 ⇒ **8 条设闸指标在那次上线后一次都没测过**。
2. **B 家族的头号指标此前无零假设**（已修，`pooled_null()` 现在每次运行强制打印）——补算后对齐深度 p≈0.22。
3. **归因没有可判状态的台账**：`git grep hypothesis_id docs/` = 0。归因目前是散文叙事，而本任务已记录
   **五次同形错误**（把非随机切片当总体读），事前挡得住那一类的恰好是"取数之前写下差异预测"这一步。

## 谁在跑这些指标

**没有任何定时作业。** 全部读数都是手工触发的（`launchd` / `crontab` 里没有评测项，2026-09-10 查过）。
后果是实的：`paper: 0.95` 于 2026-09-10 上线，而 A 家族最后一次带 metrics 的 run 是 09-08 的
`REG-NEW2D-20260907`（全量基线是 09-06 的 `FULL3-20260906`）——**上线之后 8 条设闸指标一次都没测过**。

一次全量 run 的量级：2741 题 × 3 阶段 ≈ 8.2k 次调用；迭代档（n=300）≈ 900 次。评测支出走独立的
`data/eval-fit/llm-usage-eval.db`，不污染生产成本库。

## 当前的硬瓶颈

**参照物 9 个窗口只发布了 122 条精选。** 两件事同时卡在这一个数上：B 家族绝对值要有意义、以及
类别偏置要能拟合并通过留出验证，都需要约 4 倍样本量（≈40 天日抓取）。这不是算法问题，是数据积累问题。
