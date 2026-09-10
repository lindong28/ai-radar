# ADR-20260910-9e21 · 用一个整数固定类别快照，而不按 enrich 戳收窄施加面

- Date: 2026-09-10
- Status: accepted
- 相关：[ADR-20260910-3f8b](20260910-3f8b-demote-papers-in-the-ordering-key-only.md)（本 ADR 补它留下的两个未闭合口）、
  [ADR-20260903-bc36](20260903-bc36-quota-curated-selection-by-source-form.md)（run 形状冻结）、
  [ADR-20260906-7c31](20260906-7c31-rank-on-weights-fitted-to-the-reference.md)（`selected_auc` 作为「精选有没有被伤到」的读数）、
  [ADR-20260905-499e](20260905-499e-aihot-reference-fit-eval-system.md)（这两条指标明确不设闸）

## 背景

ADR-3f8b 让 `paper` 在排序键上降权 0.95，并在 Consequences 里记下两个未闭合口：

1. run 记录不固定产生该次排序的**类别快照**与 tie-breaker；
2. 运行期不按 enrich 戳收窄系数的施加面（只有测试型 tripwire）。

第 1 条为什么要紧：类别自 2026-09-10 起是排序输入，而它来自「最新成功 enrich 行」——不是一个固定的东西。
实测本机库 23604 条候选，**19.1% 有 >1 条成功 enrich 行，626 条（2.7%）的 `primary_category` 真的变过**。
所以一次排序在事后是复放不出来的：同一份代码、同一批条目，今天重跑会给出不同的次序。

## 决策

### 一 · 不按 enrich 戳收窄施加面；改为把类别来源写进 run 记录

按戳分层量了一次 `paper` 标签跨 enrich 世代的稳定性（双标注 n=1491，我方候选池 ∩ AIHOT 收录）：

| enrich 戳 | 我标 paper 的 n | 精确率 | 召回（AIHOT paper 里我也标 paper） | 全类一致率 |
|---|---|---|---|---|
| `2026-05-13.r2`（四个月前） | 65 | **98.5%** | 71.1%（n=90） | 51.0% |
| `2026-09-08.r2.31b2065e`（拟合口径） | 51 | **94.1%** | 63.2%（n=76） | 70.0% |

**旧戳的 `paper` 反而更准。** 全类一致率差 19pp，但那 19pp 不落在 `paper` 上（落在 industry/tip，见下面 §三）。
而收窄会让候选池里 244 条过阈值 `paper` 中的 **141 条（57.8%）不再被修正**——朝远离目标的方向走。
9 个日窗回放里，收窄总共只会改变 **5/360 个版面格位**（今天的页面是 0）。

⇒ 收窄的收益是负的。**保留无条件施加，把「本轮类别出自何处」变成可审计的记录**。

**作用域**：这条读数**只覆盖 `paper` 一个类别**，因为 `CATEGORY_MULTIPLIERS` 当前只有它。
将来给别的类别加系数（尤其 industry / tip，§三 显示它们跨标注器极不稳），本结论**不自动延续**，要重新按戳分层量一次。
它也只覆盖「AIHOT 收录且能匹配到我方候选」的那 1491 条，不能证明全候选池稳定。

### 二 · 类别快照 = `MAX(item_evaluations.id)` 一个整数

`select._enrich_watermark(loaded)` 取**这次载入实际读到的最大 enrich 行 id**，写进
`curation_runs.weights_json` 的 `enrich_watermark`。复放一次排序 = 对每个条目取 `id <= watermark` 的最新成功
enrich 行。

**为什么不是另跑一条 `SELECT MAX(id)`**（本 ADR 第一版就是那样，被对抗评审推翻）：那条查询有自己的快照，
两种放法都不对。**放在载入之前它是下界**——两次查询之间 commit 的 enrich 行，载入**看得见**而水位线**不含它**,
于是 `id <= watermark` 的复放会读到该条目的**上一版**类别。进程内已复现：载入按 enrich 行 4 把某条目排成
`paper`（×0.95），而记录的水位线是 3，复放回来根本没有类别（×1.0）。放在载入之后则错在另一头——它会点名
载入从未见过的行。第一版的注释还把它写成「upper bound」，那句话是反的。

**取「实际读到的 id 的最大值」是精确的**：`item_evaluations.id` 是 `INTEGER PRIMARY KEY AUTOINCREMENT`
（`migrations/001_init.sql`），而 SQLite 串行化写者，故 id 顺序即 commit 顺序、且永不复用。于是「快照里存在
id=N」蕴含「所有更小 id 的现存行也在快照里」，`id <= max(读到的)` 必然是这次载入所见的子集。

**为什么一个整数就够**：`item_evaluations` 在运行期只增不改——全仓唯一的 `UPDATE` / `DELETE` 是两个一次性
migration（`004` 删行、`017` 把 `cost_usd` 置 NULL），都不会在生产再跑。实测按当前水位线复放与生产
`_load_candidates` 的类别**逐条 0 处不一致**（n=23604）——但要如实读这条：它取自 watermark=now 的平凡情形，
在缺陷为真和为假时**取值相同**，所以它证明的是查询形式等价，**不是**上面那条时点正确性。

**为什么不写全量类别**：23.6k 行/次，实测 **829 KB/次 ≈ 6.93 GB/年**，而生产同步的是一份约 5 GB 的快照。
**固定长度的前缀快照也不行**：2026-09-07 那轮源配额把 `_fill` 推到了 fresh 池的第 243 名（池子总共 243 条），
任何常数 N 都会漏掉切口。

`ranking_tiebreakers` 同时记下**方向**而不只是字段名：`ranking_key` 对一个首元素取负的元组做升序排序，
所以分数降序、两个 tie-breaker 升序。只给字段名的话，复放会在并列处把次序排反，而且读起来完全正确。

### 三 · 评测台新增 `selected_auc_ranked`，不替换既有指标

`metrics.py` 新增 `ranking_score(row) = weighted_score × category_multiplier(enrich 的 primary_category)`，
并据它算 `selected_auc_ranked`。**追加而非替换**：ADR-7c31 拿 `selected_auc` 当过「精选有没有被伤到」的读数
（0.7907→0.7878），改名换义会让 2026-09-10 两侧的 run 在同名指标上读起来一样却量的是两回事。
这与 `run.py` 同时存 `weighted_score` 与 `fit_score`、`KNOWN_SOURCE_QUOTA_SCORE_SEMANTICS` 只追加不替换是同一条纪律。

在 metrics 层**派生**而不在 run.py 落盘时算，有两个理由：score 阶段先于 enrich 阶段执行，那时拿不到类别；
派生使**全部历史 run 无需重跑 LLM 即可重算**。真实归档 run `FULL3-20260906`（n=2741，78 正例）：

| `CATEGORY_MULTIPLIERS` | `selected_auc_ranked` |
|---|---|
| `{}`（阴性对照） | **0.7907** ← 与既有 `selected_auc` 逐位相同 |
| `{"paper": 0.95}`（生产） | 0.7929 |
| `{"paper": 0.50}`（阳性对照） | 0.7878 |

缺 enrich 的行**按 1.0 计入、不排除**，因为那就是生产行为。排除是另一个指标而不是更干净的指标：
n 从 2741 掉到 2624、AUC 读 0.7879 而不是 0.7929。

**`selected_p_at_k_ranked` 建了又撤**：同一个真实 run 上它在有无系数时都读 0.2692，而仅仅换掉日内 tie-breaker
就让它从 0.2727 变到 0.2597。**tie-break 噪声大于全部信号的哨兵不会开火**。AUC 不受影响——它对并列记 0.5。

## 后果

- 生产排序结果**不变**。本 ADR 三条都不改 `weighted_score`、两道绝对闸，或 `ranking_key` 的施加面。
- 水位线的正确性依赖两条**约定**，都没有数据库约束兜着，按会先咬人的顺序：
  1. **id 永不重新编号。** 重建整表的 migration（`016` 已经做过一次：RENAME / CREATE / INSERT..SELECT / DROP）
     只因为它的 INSERT 显式列了 `id` 才保住了编号。下一个漏写这一列的 migration 会让**全部**已存水位线一起
     变成错值，而它**不含 `UPDATE` / `DELETE` 关键字**，grep 结构上看不到。由对抗评审报出。
  2. **运行期只增不改。** 见上。
  这就是那 8 个字节的代价。
- **`ranking_key` 自身的代码版本仍未固定**。改这个函数会让历史 run 在复放时重新排序，而记录里没有任何东西
  说明是哪一版排的。未修，记账。
- `selected_auc_ranked` 在 metrics 层读**当时**的系数表，故同一份归档 run 在系数变化后重算会得到不同数值。
  已在三处闭合：`metrics.json` 顶层 `ranking` 块记录该表、`compare_to_baseline` 在系数不同时判
  `comparable: False`、`report.md` 的「身份」节打印它。**仍未闭合**：`thresholds.json` 里已存的 floor 不带
  系数身份，故拿一条旧 floor 去判一个新系数下的 run 仍然不会有任何提示（本指标不设闸，故当前不可达）。

## 决策评审

两轮独立 decision-review（Codex, read-only）：

- **第一轮出口「交用户」**。四条决策里 D1/D3 判「方向成立、需补齐定义」，D2 的判据 4 判**无法判断**
  （用户把「运行期未按 enrich 戳收窄」列为必做项，而「给它一个结论、结论是不收窄」只是 caller 的派生解释），
  D4 判 **blocker**——原方案只记入选条目的 enrich 版本计数，没兑现「候选类别快照」。
- **第二轮（两问复核）**：D1 修正成立；D3 机制成立、数据交付另有六条应修，已逐条修完（含一个我自己引入的
  真 bug：`from ... import CATEGORY_MULTIPLIERS` 是导入时绑定，而 `category_multiplier()` 调用时读模块全局，
  两者会分叉 ⇒ 记录的身份可能与实际施加的系数不是同一张表，正好是那个身份块要防的事）。
- **D2 / D4 交用户裁决**（2026-09-10）：用户选「按任务目标的最小充分方案，取推荐项」⇒ D2 不收窄、D4 水位线。

**一处刻意偏离**：D4 的水位线是**第一轮之后才出现的新机制**，按 decision-review 的处置表，换方案本应重走一遍
完整 gate。没有重走。理由：用户是在拿到成本读数（8 字节 vs 6.93 GB/年）与验证读数（0 处不一致）之后选的它，
而它唯一承重的性质（只增不改）正由本轮的 review-gate 对抗审在查。这条偏离写在这里，不是静默的。

## 生成后 review gate（高档，Claude subagent，对抗式）

**定档**：逻辑隐蔽度判高——两个信号，**时序**（水位线相对载入的取值时点）与**「借来的语义」**
（正确性依赖 `item_evaluations` 只增不改，而那是别处的性质）。它报出 9 条，**其中一条是真缺陷**：

- **F1（已修，见 §二）**：水位线取值时点写反，且注释里那句「upper bound」是错的。
- **F2（已修）**：守住这一轴的那个测试**结构上看不见它**。原断言是 `enrich_watermark == MAX(id)`，而单线程
  fixture 里 `MAX(id)` 在载入前后恒等（实测每次调用 `(前, 后)` 都相同），故「改到载入之后取」这一重变异**是绿的**。
  现补了一个在载入期间从第二条连接 commit 的测试，**两侧都钉**：载入前落的行必须被覆盖、载入后落的行必须不被覆盖。
  两个方向的变异现在各自转红——**只钉前一侧时，「改到载入之后取」仍然是绿的**。
- **F3（已修）**：`ranking` 块写在了 `compare_to_baseline` 与 `render_report` **都不读**的通道里。现两处都补上：
  系数不同即判 `comparable: False`（与既有的 judge 身份闸同形），报告的「身份」节也打印它。
- **F4（已修）**：`selected_auc_ranked` 在 `derive_eval_fit_thresholds.py` 的三张白名单里都不在，于是台账里
  查不到「它为什么没有闸」。已加进 `NOT_GATED`。
- **F5（已修）**：`missing_enrich` 原写「as production does」，对**本轮 enrich 失败**那一支不成立——评测台给 1.0，
  而生产会回落到上一条成功行（可能是 `paper` 0.95）。措辞已改为不声称等价。
- **F6（已修）**：`--labels` 默认开且**没有关闭值**，于是「只用 AIHOT 标签」这个历史口径从入口不可达
  （`--labels ""` 会抛 `IsADirectoryError` 裸 traceback）。已加 `off`。顺带补上那条缺失的位移读数：
  历史口径 **TV 0.156（覆盖 65.0%）** vs 新默认 **0.131（覆盖 83.3%）**。满覆盖时那句「POOLED 就是整页构成」
  也已按标注器来源加限定。
- **F7（记录未改）**：同一条 `tip -> tutorial` 误译仍活在 `aihot_fit/common.py`，经 `build.py` 决定题集的
  `reference.primary_category`，进而喂**已设闸**的 `category_agreement`。不在本轮改：改它会移动全部历史
  `category_agreement` 读数与据其推出的 floor。已在该表旁记下实测偏差与下游；用户选择的「对齐 AIHOT 划分」
  那个工作单元才是它的归宿。
- **F8（已修文档）**：把「id 不得重新编号」写进上面的后果节——它是 grep 看不到的那一类。
- **F9（已处置）**：标注文件此前仍未 tracked，`.gitignore` 那个 hunk 的目的因此未达成。已随本次提交纳入。

**评审者判我把「注释与 docstring」整类排除为只读 payload 是错的**，而这次两条 finding（F1 的不变量陈述、
F5 的事实主张）正落在那里。**陈述不变量、陈述取证结论、陈述域外范围的注释是 authority。**

## 上线：第一次部署被生产闸拒绝（2026-09-10）

**`09dea35` 推成功、部署被拒**，生产停在旧代码上、健康检查会 page。原因是那个 commit 把标注文件
track 在 `data/eval-fit/labels/` 下，而 **`data/` 整个是 runtime-owned**（只有三个配置文件在允许名单里）：
`deploy/sync/deploy_code.py` 的 `_is_runtime_owned` 在动树之前就拒绝，因为 `checkout-index -f` 会覆盖
git 恢复不了的线上状态。**拒绝是对的**，缺的是这道拒绝只存在于服务器上。

本地全绿：ruff、mypy、2790 测试、导出树执行检查、`git check-ignore` 双向都对。
`git push` 也报成功——post-receive 无法让 push 失败。

**修法（`19978fd`）**：文件移到消费者旁边 `scripts/eval/labels/`，`.gitignore` 的例外整条删掉。
**没有**把它加进 `_RUNTIME_ALLOW`——那会为了一份度量数据削弱一道保护线上状态的生产闸。
并补上真正缺的那道检查：`tests/test_repository_hygiene.py` 现在拿**本仓真实的 `git ls-files`**
去过那个谓词。既有的两个测试分别覆盖谓词本身与一个合成 commit，**都不读这个仓自己的树**——
而那正是出错的地方。该测试两侧已验：把犯规文件重新 track → 红，移走 → 绿。

**部署成功读数**：`19978fd`，`previous: 404ea80`（印证前一次部署确实没跑），serve@8000 已重启。
公网 `healthz` 200 / 1.64s、首页 200 / 2.79s。

## 验证

- **时点两侧变异各自转红**：装回原缺陷（载入前另跑 `SELECT MAX(id)`）→ 红；反向（载入后另跑）→ 红；
  还原后基线绿。**只钉一侧时反向那重是绿的**，这是本轮唯一一次两侧都钉住的对照。
- 另六重变异全红、还原后基线绿：水位线写死 None / 水位线改到载入之后取 / tie-breaker 丢方向 /
  排序键去掉类别系数 / ranked AUC 忽略系数 / ranked AUC 排除未 enrich 行。
- `uv run ruff check src tests` 干净；`uv run mypy src` 干净（150 files）。
- **真实生产路径读数**：本机 pipeline 的定时轮 `20260910T082143Z-1af4`（08:21:43Z）写下
  `enrich_watermark=263831` 与带方向的 `ranking_tiebreakers`，而它之前三轮都是 `None`——真实数据里
  一条干净的前后边界。（curate 跑在本机，生产消费同步过去的库。）
- **「过去的水位线能重建过去状态」已在真实库上验证**（这是评审者列为承重的未核实项）。同一批 23724 条
  候选，把 W 逐步后退：

  | 水位线 W | 与当前类别不同 | 其中 paper↔非paper | 取到 id>W 的行 |
  |---|---|---|---|
  | 263831（当前） | 0 | 0 | 0 |
  | 263000 | 106 | 5 | 0 |
  | 260000 | 608 | 45 | 0 |
  | 250000 | 1369 | 84 | 0 |
  | 200000 | 3361 | 216 | 0 |

  差异随 W 后退**单调增长**，而「取到 id>W 的行」在每一档都是 **0**（那是边界不变量）。
  第三列是它对排序的实际作用面——只有这些条目的系数会变。
  **仍未验证的只剩一件**：「某次 run 记录的那个 W 复放出该次 run 的原页面」。它需要 enrich 表在那次
  run 之后长出新行；本轮试的时候 `MAX(id)` 还等于记录值，窗口无信息量，脚本据此拒绝出读数而不是编一个。
- 全量 2790 passed / 10 failed；**10 条全部不是本轮的**：9 条在 HEAD 的干净导出树上同样红，
  第 10 条（`test_egress_routing`）单独跑通过 ⇒ 跨测试干扰。用干净导出树而不是 `git stash` 做基线，
  因为工作树里有另一个 session 未提交的 `precompute.py`。
