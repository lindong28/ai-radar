# ADR-20260905-499e：以 AIHOT 历史输出为参考输出建立内容链拟合评测体系

- Status: accepted；达标线未定，首轮读数为 n=20 的两次小样本
- Date: 2026-09-05

## Context

AI Radar 的内容链有三个 LLM 驱动对象（prefilter、score、enrich v2 的分类/标签/摘要/推荐理由）。此前的评测框架（旧 program `20260820-content-align` 的 T3/T4/T5）用的尺子是「相对冻结基线不回归」与「判官判值不值得读」，两者都与 AIHOT 无关；T3 plan 甚至明写「不把 AIHOT 摘要喂给 AI Radar 模型」。

用户 2026-09-05 裁决改变了优化目标（原话）：

> 我接受拟合 AIHOT 的编辑口味。我认为目前没必要、也很难区分编辑口味和质量。AIHOT 的整体编辑口味是经过多轮迭代和大量反馈的，我觉得可以信任。

> AIHOT 的历史数据应该有足够多，可以就用这些数据作为 LLM 驱动的对象所对应的带参考输出的评测题。

同日四条设计裁决：题集三批全用、参考输出取 AIHOT 字段；摘要与推荐理由一开始就建 LLM 判官，判官模型一律 DeepSeek v4、默认 flash；达标线在首轮基线跑出后定，改善看 bootstrap 置信区间；代码进主仓、题集进数据仓。

## Decision

新建 `src/airadar/eval/aihot_fit/`，CLI `ai-radar eval-fit {build,run,judge,report}`，按 `~/.claude/references/llm-eval-system.md` 的四槽位组织：

- **① 优化对象**：不改生产代码。`run` 直接 import 生产 `_evaluate_item`（prefilter / scorer / enrich v2），不复制 prompt 逻辑；加权分用 curator 的同一权重函数。每次 run 把身份锚写进 `run.json`：各 stage 的 ruleset 版本、model id、prompt 模块 sha256、git HEAD 与 dirty 位、模型选择 env 的名与值（非凭据）。run 不写 `item_evaluations`；`data/radar.db` 一律 `mode=ro` 打开。
- **② 评测题**：`build` 把三批 AIHOT 历史输出按 `original_url` 关联到我站 `items`（x.com 走 status id、其余按归一化 URL），产出 `questions.jsonl`（每题 input + reference + provenance）与 `manifest.json`。首轮 2741 题，`questions_sha256` `dbe074ed…`。归一化保留 query（参数排序、去 `utm_*`）——丢弃它会把靠 query 承载文章 id 的站点整站坍缩成一个键，见下「Review gate 与首轮修复」H1。**题集的权威副本落数据仓 `evalsets/aihot-fit-v1/`；主仓 `data/eval-fit/evalset-staging/` 是 gitignored 的构建输出，两侧的一致性由 `questions_sha256` 判定**——run.json 记录本次用的 sha，与题集 manifest 不等即不可比。
- **③ 判官**：只判 `summary_zh` 与 `why_recommend`，模型 `deepseek-v4-flash-ga-260731`（ARK），temperature 0，判官身份（模型、两份 prompt 的 sha256、temperature、max_tokens、ARK host）写 `judge.json`。判官给 0–100 的 `closeness`，明说参考是目标口味而非绝对真值。`--calibrate N` 跑双向对照：阳性 = 候选就是参考自身，阴性 = 候选换成另一题的参考。
- **④ 自动指标**：`report` 出 10 个指标，每个带 n、点估计、bootstrap 95% CI（1000 次 seed 0）与各自的对照/下界（多数类、随机排序 0.5、独立 ρ=0、乱配基线）。`--baseline` 比较时若题集 sha256 或判官身份不一致即拒绝比较。

**达标线本轮不写**（`metrics.json` 的 `thresholds` 为 `null`）：首轮只有 n=20 的两次小样本，CI 宽到无法支撑任何阈值。

## Options Considered

### 复用 T3/T4 分支上的既有评测体系

否决。那套的验收尺子是「相对冻结基线不回归」和「值不值得读」，与新的拟合目标不同构；且它落在两个未合并的 worktree 上，与 main 的 `src/` 有 52 个文件不同，迁移成本高于重写。其中与新方向一致的部分（AIHOT 标签映射表）已并入本实现。

### 只用确定性指标、不建 LLM 判官

否决（用户明确裁决）。摘要与推荐理由是自由文本，字符 bigram Jaccard 在「换个说法说同一件事」与「说了另一件事」上取值接近——本轮实测 bigram 均值 0.16 而判官均值 0.34，两者排序不同。bigram 作辅助指标保留。

## Consequences

- **`why_recommend` 维度只在 78 条题上可评**：AIHOT 只给 `selected: True` 的条目写推荐理由，实测 reason 非空 78 条，与 selected 数完全相等（有 reason 而未 selected 的条目为 0）。均匀抽样下这一整个判官维度跑不起来（20 题只命中 1 条，判官校准因 `len(eligible) < 2` 直接跳过）。为此给 `run` 加了 `--require-reference {summary,reason}`，把取样限定到该维度有参考的题上；`run.json` 记 `pool_eligible/pool_total`。
- 标签维度只在 t2 两批（584 题）上可评：t5 批的 AIHOT API 投影不含 tags。
- 判官对照读数为阳性 100.0 / 阴性 0.0（各 n=10，两维度均如此）。它证明判官能分开「完全相同」与「完全无关」，**不证明中间区的刻度**；中间区的分辨力由正式判分的分布佐证（18 条判分跨 10–80、7–8 个不同取值），不是由对照证明的。
- 题集三批的默认源路径全部 repo-relative：t2 两批指向本仓 submodule `benchmarks/aihot/windows/`，t5 原始抓取指向 `.label-serve/…`；`AI_RADAR_AIHOT_DATASET_ROOT` 与 `AI_RADAR_AIHOT_T5_RAW` 可覆盖。本仓公开，不落维护者机器路径。**submodule 在主 worktree 未初始化**，故默认路径下 t2 两批不存在，需先 `git submodule update --init benchmarks/aihot` 或用 env 指向已有 checkout；首轮题集正是用 env 指向的那份 checkout（同 commit `7d9de5e`）构建的，manifest 记录的是当时实际读取的绝对路径。
- 评测的 LLM 调用照生产路径记入 `llm_usage`，沿用 stage 名不改 schema；按 run 的起止时刻与 item_id 集合归因。

## 首轮读数（2026-09-05，n=20 × 2 次，git HEAD beb1f47 dirty）

题集 sha256 `dbe074ed…`（2741 题），两次 run 都用 seed 1。RUN1 均匀抽样；RUN2 用 `--require-reference reason` 限定到有参考理由的 78 条池内抽样（因而全部是 selected 正例，`selected_auc` 无负例、返回 n/a）。

| 指标 | RUN1 均匀 | RUN2 reason 池 | 对照 / 下界 |
|---|---|---|---|
| ai_recall | 0.80 (n=20) | 1.00 (n=20) | 无负例，不构成 precision 主张 |
| category_agreement | 0.50 [0.28, 0.72] (n=18) | 0.42 [0.21, 0.63] (n=19) | 多数类 0.44 / 0.42 |
| tag_jaccard_mean | 0.53 (n=6) | 0.55 (n=3) | 乱配基线各自算出 |
| score_spearman | 0.31 [-0.19, 0.75] | -0.09 [-0.58, 0.47] | ρ=0 |
| selected_auc | 0.58 (**正例仅 1**，无 CI) | n/a（无负例） | 随机 0.5 |
| summary_closeness_mean | 0.62 [0.53, 0.71] (n=18) | 0.42 [0.32, 0.53] (n=19) | 判官对照见下 |
| reason_closeness_mean | n=1 不可用 | 0.44 [0.34, 0.54] (n=19) | 判官对照见下 |

判官对照（各 n=10）：summary 阳性 100.0 / 阴性 0.0；reason 阳性 100.0 / 阴性 2.0；RUN2 `scale_ok=True`。RUN1 的 reason 维度可用样本 <2、对照未跑，`scale_ok` 为 `None`（不是 `False`）。

生产 enrich v2 的结构性拒绝：RUN1 2/20、RUN2 1/20（受控词表外标签、`why_recommend` 超 90 字符）——被评测对象的读数，不是评测框架故障。

**这批读数取自修复 H1 之后重建的题集。** 修复前那一版的读数（`selected_auc 0.947`、`summary_closeness 0.50`）作废，原因见下节。

## 可用基线（2026-09-05，题集 `dbe074ed…`）

首轮的两次 n=20 只够验证管道，不够定阈值。补跑了两次可用基线，**判官对照两次均 `scale_ok=True`**（阳性 100.0 / 阴性 0.0–1.5，各 n=20）：

| 指标 | BASE-150 均匀抽样（seed 7） | BASE-78 reason 全量总体 |
|---|---|---|
| ai_recall | 0.920 [0.873, 0.960] (n=150) | 1.000 (n=78，零宽，不作证据) |
| category_agreement | 0.486 [0.403, 0.569] (n=144) | 0.444 [0.333, 0.556] (n=72) |
| tag_jaccard_mean | 0.449 [0.354, 0.539] (n=33) | 0.543 [0.385, 0.737] (n=9) |
| score_spearman | **0.430 [0.284, 0.553]** | **−0.259 [−0.454, −0.034]** |
| selected_auc | 0.665 [0.467, 0.851]（正例 4） | n/a（全正例） |
| summary_closeness_mean | 0.577 [0.540, 0.612] (n=142) | 0.401 [0.349, 0.458] (n=72) |
| reason_closeness_mean | n=4 不可用 | **0.392 [0.343, 0.445] (n=72)** |

**BASE-78 是 `why_recommend` 参考的完整总体**（AIHOT 只给 selected 条目写理由，全部 78 条都跑了），所以 `reason_closeness_mean = 0.392` 没有抽样误差，只有判官与解码噪声。

两条读数值得单独指出：

- **`score_spearman` 在两个样本上符号相反，且两侧 CI 都排除 0。** 全样本 +0.430——我站 `weighted_score` 与 AIHOT 评分总体同向；但只看 AIHOT **已精选**的 78 条时是 −0.259，即在 AIHOT 认为值得选的那批里面，我站的排序与它大致相反。这不是样本量问题（CI 上界 −0.034）。它意味着评分器区分"AI 相关 vs 无关"有效，区分"好 vs 更好"无效甚至反向。
- **`summary_closeness` 在两个样本上差 0.176**（0.577 vs 0.401），而 BASE-78 全是 AIHOT 精选的条目——AIHOT 给精选条目写的摘要更长更具体，与我站 schema 的差距更大。这与下面 ISSUE-FIT-01 的天花板同向。

### 曾以为挡在达标线前面的那个天花板，不存在

先观察到的是一条相关：判官 closeness 与「我方句数 − 参考句数」的差值单调负相关（差 0 → 80.0、1 → 52.5、2 → 56.2、3 → 46.8、4 → 40.0；Pearson r = −0.307，n=37），而我站 schema 强制 3–5 句、参考里 30/37 是 1–2 句。据此推断 `summary_closeness` 含一个 prompt 改不动的固定折扣，应在定线前扣除。

**干预实验证伪了它。** 取参考为 1–2 句的 50 题，让模型把参考改写成 3–5 句（只改展开程度、不增删事实），再用同一判官重判我方候选：

| 切法 | n | 配对差 | 95% CI |
|---|---|---|---|
| 全部 | 50 | −2.10 | [−5.00, +0.60] |
| 改写确实落在 3–5 句 | 40 | −1.00 | [−4.25, +2.12] |
| 句数差由 ≥1 变为 0 | 15 | −1.00 | [−6.00, +4.00] |

三个切法全部含 0，方向还是轻微变差。那条相关是**混淆**而非因果——最可能的来源是内容难度：AIHOT 用 1–2 句写完的条目本身信息量小，我方再写 4 句就掺进了参考没有的内容。

**结论：没有天花板要扣，达标线可以直接从基线读数定。** 更值得留下的是方法教训——拿相关系数去论证一个能被干预实验直接检验的机制，读数在假说真与假时形态相同；若据它去放宽 `schema_v2.py`，会白改一场，且改完读数不动时容易被读成"prompt 还不够好"。逐条记在 [issues](../issues/aihot-fit-eval.md) 的 ISSUE-FIT-01。

**评测的副作用已与生产隔离。** 早期三次 run 在 `data/llm_usage.db` 留下 970 行（enrich 344 / prefilter 313 / score 313），占当日该表 4066 行的 23.9%，与生产流水不可区分；同一条链路上评测打出的 429 还会写 `data/ark-breaker.json`、给**生产**开 2 小时熔断并把它推去按量计费通道。用户要求「跑全量不意味着要影响用户可以看到的数据」，故 `run` 与 `judge` 开工时把 `AI_RADAR_LLM_USAGE_DB` 与 `AI_RADAR_ARK_BREAKER_STATE` 指向 `data/eval-fit/` 下的评测专用文件，并把实际生效的两个路径记进 `run.json` / `judge.json` 的身份块。**两个 env 间接层都是既有的，无需改动任何生产代码。** 双向实测（5 题 run）：生产 `llm_usage.db` **+0 行**、生产 `ark-breaker.json` md5 未变、`radar.db` 遗留表仍 67 行；评测库 **+15 行**（= 5 题 × 3 stage），证明不是"干脆不记了"。

新建评测 usage 库时会从 `radar.db` 的遗留 `llm_usage` 表拷入 67 行历史记录（`_copy_legacy_usage_rows`，只读 ATTACH + SELECT，既有生产行为）。它们的 `created_at` 早于本工作数月，按时间窗查询即排除。

## Review gate 与首轮修复

交付前过中档 review gate（独立只读 reviewer），报回 2 HIGH / 17 MEDIUM / 11 LOW。已修的：

- **H1（必修）· URL 归一化丢弃 query，把参考绑到无关文章上。** 微信 `/s?__biz&mid&idx&sn`、HN `/item?id=`、YouTube `/watch?v=` 都把文章 id 放在 query 里，丢弃 query 后同一 host 的全部条目坍缩成一个键，与该键下最早 fetch 的那个 item 配对。自证读数：题目 `9abcb316d4d632ae` 把「日本麒麟啤酒，怎么开始卖保健品了？」配上了「GLM-5.3 上线」的 AIHOT 参考，`match_method` 仍写着 `url`；AIHOT 侧 52 篇微信文章因此只剩 1 道题。修复为保留 query（参数排序、去 `utm_*`），并加了三条回归测试。重建后题数 2730→2741、假去重 74→12、未匹配 158→209（**升高才是对的**，此前那些"匹配"是假配对）。
- **H2 · 可比性闸对"实际测了哪些题"无效。** `questions_sha256` 哈希的是整个题集文件，对同一份 evalset 恒等，与 `--limit` / `--seed` / `--require-reference` 无关；两次 item_ids 交集为 0 的 run 被判 comparable 并报出 `improved: True`。修复：`metrics.json` 增加 `subset_sha256` / `sampling` / `stages`，可比性闸增加子集比对、早停拒绝与 stage 身份差异输出。`improved` 的判据由「本次 CI 下界 > 基线点估计」改为**两个 CI 不重叠**——两个判据各自独立复算过（同分布、p=0.55、n=20、500 次重复）：旧判据假阳性 **45/500 = 9.0%**（标称 2.5%，即约每 11 次无意义改动就有一次被盖章改善），新判据 **2/500 = 0.4%**。代价是更保守：真实改善需要更大效应量才判得出，n=20 这一档基本判不出任何改善。
- **M1 · bootstrap 静默丢弃退化重采样**（AUC 无正例、Spearman 零方差），实测 n=20 单正例时丢弃约 339/1000（独立复算 339，reviewer 报 338），而 payload 仍写 `rounds: 1000`。改为退化轮次超 5% 即不给 CI（fail-closed），而不是给一个悄悄变窄的区间。
- **M7 · `git_identity` 用 `--untracked-files=no`**，对整个全新未跟踪的 eval 包视而不见（实测 -uno 见 0 个、-uall 见 10 个），首轮基线跑正是最可能落进这个洞的时刻。
- **M8 · 唯一那条"没写过 DB"的断言是空的**：fixture 是 `delete` journal 模式，`-wal` 文件无论是否 `mode=ro` 都不会出现。改为直接对只读连接跑一次写并断言 `OperationalError`，并跑了阴性对照确认可写 URI 下该断言会失败。
- **M11 · `scale_ok=False` 混淆了"对照没跑"与"对照没过"**，报告据此打印错误理由。改为未跑时给 `None`。
- **M12 · 重跑 `judge` 会覆盖 `judgments.jsonl`**，早停或 `--limit` 会静默销毁已付费的读数。改为按 `(question_id, dimension)` 合并，并记 `judgments_total_on_disk` / `judgments_replaced_this_call`。
- 报告的 n 列现在显示正例数（`20（正例 1）`）——AUC / P@k 依赖的是正例数而非 n，而 n 列读起来像它有 20 个支撑。

### 修复复核轮（同一 reviewer，只答「修复是否成立 / 有无新问题 / 有无应升档者」三问）

H1 / H2 修复经独立复核成立：新增的 51 条不匹配**逐条核实全部是原本的错匹配**（反向 0 条），且「两侧 query 参数集合不同导致新漏配」这一风险在当前数据里为 0 条。复核另揭示一条范围事实：AIHOT 的 52 篇微信记录里只有 1 篇在我站 `items` 中（我站库内微信文章 257 篇），即**本评测对微信内容线的真实覆盖是 1 题**——修复没有制造这个缺口，只是把它从"52 篇假匹配"还原成"1 篇真覆盖"。

复核报回 4 条由修复本身引入的新问题，全部已修（三条共同结构：**我的修复把原本只影响展示的缺陷提拔成了判决依据**）：

- `improved` 在任一侧 CI 缺失时落到 `False`，使 +0.38 的改善、-0.37 的回归与真正无变化取同一个值——而 M1 的 fail-closed 恰好让 CI 缺失从罕见变成常态（RUN1 十个指标里 4 个有值无 CI）。改为三态。
- 零宽 CI 成了判决输入：`ai_recall` 20/20 bootstrap 出 `[1.0, 1.0]`，与基线 18/20 的 `[0.75, 0.98]` 判为不重叠、报 `improved: True`，而正确的 Wilson 区间 `[0.84, 1.0]` 是重叠的。改为零宽区间不作证据。这条把原 M2 从展示层瑕疵升档为结构性问题。
- judgments 合并是**无条件覆盖**，而判官任务在 stop 飞行中置位时返回 `closeness=None`——一次配额中断的重跑会把三条真实读数全部抹成 None，且 `judgments_total_on_disk` 仍显示 3、读起来毫发无伤。M12 要防的那件事换了条路又发生。改为只在新行有分数时才覆盖，并记 `judgments_blank_discarded_this_call`。
- `stage_identity_diff` 成了"流水线没变"的唯一凭据，而它读的 `model_id` 是类常量（原 M4，升档）。改为同时记 `served_models`，取自各响应的 `raw.model`。

四条修复各自跑了双向验证；`improved` 那条另跑阴性对照确认真实不重叠仍报 `True`，闸没有被焊死。此后按「修复轮预算」收口（该轮 4 条新 finding 中 3 条可追到本方上一轮修复，过半即停），残留项记入 issues。

未修的 17 条按用户常设指令（最低充分方案、按实际问题加码）记账不修，逐条带失败场景落在 [docs/issues/aihot-fit-eval.md](../issues/aihot-fit-eval.md)。其中对定达标线影响最大的一条是 ISSUE-FIT-01：**77.4% 的 AIHOT 参考摘要是 1–2 句，而我站 schema 强制 3–5 句**，判官又把编辑风格算作三分之一权重，故 `summary_closeness_mean` 含一个 prompt 层动不了的固定折扣。

## 全量基线与达标线（2026-09-06，题集 `dbe074ed…`）

`FULL-20260906`：2741 题、53 分钟、无早停。prefilter / score 各 2741 全部成功；**enrich 204 条结构性拒绝（7.4%）**，其中 117 条是标签越出受控词表、越界的几乎全是品牌名（NVIDIA 53、腾讯 10、小米 7、Amazon 7…）——本仓 [ADR-001](./001-deterministic-source-brand-tags.md) 的决策是品牌标签由确定性层加，模型在抢那一层的活并因此整条产出被拒，落 [issues/general.md](../issues/general.md)。判官两维度校准 `scale_ok=True`。

| 指标 | 全量点估计 | 95% CI | n |
|---|---|---|---|
| ai_recall | 0.9179 | [0.9070, 0.9274] | 2741 |
| category_agreement | 0.5104 | [0.4907, 0.5302] | 2537 |
| tag_jaccard_mean | 0.4641 | [0.4426, 0.4861] | 548 |
| score_spearman | 0.4281 | [0.3918, 0.4576] | 2741 |
| **selected_auc** | **0.7731** | [0.7178, 0.8193] | 78 正例 |
| selected_p_at_k | 0.2821 | [0.2308, 0.3467] | 11 日桶 |
| summary_closeness_mean | 0.5446 | [0.5356, 0.5534] | 2476 |
| reason_closeness_mean | 0.3719 | [0.3267, 0.4205] | 73 |

`selected_auc = 0.773` 是本轮唯一被小样本严重误导过的指标：n=20 的两次分别给出 0.947 与 0.58，各自只有 1 个正例，两个都不可用。`selected_p_at_k = 0.282` 与旧 T5 的历史读数（33.3% / 14.3% / 43.8% / 合并 27.0%）同量级。

**达标线已写入** `evalsets/aihot-fit-v1/thresholds.json`，8 条设闸、2 条明确不设：

| 指标 | 适用配置 | 下限 | 可检出的最小回归 |
|---|---|---|---|
| ai_recall | ITER-300-seed7 | 0.8645 | 0.053 |
| category_agreement | ITER-300-seed7 | 0.3907 | 0.120 |
| tag_jaccard_mean | ITER-300-seed7 | 0.3295 | 0.135 |
| score_spearman | ITER-300-seed7 | 0.2181 | 0.210 |
| summary_closeness_mean | ITER-300-seed7 | 0.4922 | 0.052 |
| summary_bigram_jaccard | ITER-300-seed7 | 0.1893 | 0.032 |
| reason_closeness_mean | ITER-REASON-78 | 0.2781 | 0.094 |
| reason_bigram_jaccard | ITER-REASON-78 | 0.0376 | 0.019 |

**`selected_auc` 与 `selected_p_at_k` 不设闸**：`selected` 只占 2.8%，300 题子集里约 9 个正例，按同一规则推出的下限分别是 0.4224（**低于 0.5 的随机排序基线——随机评分器都能过**）与负数（低于该指标取值下界）。**下限低于随机基线或取值下界的闸永远不会开火，摆在那里只会让读者以为这一维被覆盖了。** 这两条只在全量 run 上判。

每条下限绑定它所适用的 `subset_sha256`，`report` 只对匹配的 run 施加——不绑的话，300 题的 run 会被理由维度的下限误判、78 题的 run 会被摘要维度的下限误判，两者实跑都发生过。

双向验证：三个配置（ITER-300 / ITER-REASON-78 / FULL）对真实基线均 `below=none undetermined=none` 且 exit 0；把 ITER-300 的 75 条分类劣化后，`category_agreement` 掉到 0.2606 [0.2113, 0.3134]，闸点名该指标并 exit 1。

## 达标线怎么定：必须与它将被施加的样本量成对

**一个会让达标线一上线就报废的陷阱**：闸判的是「区间下界是否 ≥ 下限」，而区间宽度随 √n 收缩。若下限取自全量 n=2741 的 CI 下界，再拿去要求一次 n=300 的迭代 run 去确认，那次 run 即使**毫无回归**也会被判低于下限。取 `category_agreement` 的点估计 0.486 算一遍（正态近似，下同）：

| n | 95% 半宽 | CI 下界 |
|---|---|---|
| 144 | 0.0816 | 0.4044 |
| 300 | 0.0566 | 0.4294 |
| 1000 | 0.0310 | 0.4550 |
| 2741 | 0.0187 | **0.4673** |

下限取 0.4673 时，一次真值仍为 0.486 的 n=300 迭代其下界是 0.4294，低于下限 → 闸开火。这不是闸坏了，是**把在一个样本量上确认的下界，拿去要求另一个样本量确认**。

**因此本 ADR 的达标线规则**：下限取**迭代规模**（n=300，`--seed 7`）基线的 CI 下界，而非全量的；全量读数作为权威点估计另行记录。这样一次无回归的迭代按构造约 95% 放行，而真实回归会把整个区间压到下限之下。`thresholds.json` 每条的 `basis` 字段写明它取自哪个 run 与哪个样本量——**推导规则写进文件，不手抄数字**（本轮已因手抄基线出过一次转录错误）。

迭代规模的读数不必另跑：全量 run 覆盖全部 2741 题，`--limit 300 --seed 7` 的子集是它的真子集，逐题产物与判分都能从中取出重算，零额外调用。

## 怎么用它迭代（可比性闸带来的使用约束）

可比性闸要求两次 run 的 `subset_sha256` 相同，所以**不能随便换抽样再去比**。抽样是确定性的——`sample_questions` 先按 `question_id` 排序再用 `random.Random(seed).sample`，实测同 `--limit/--seed` 两次给出同一子集、换 seed 给出不同子集——因此固定这两个参数就能既便宜又可比。

改完 prompt 想知道有没有改善，跑这三条（与本 ADR 记录的基线同参数即可直接 `--baseline` 比较）：

| 用途 | 命令 | 规模 |
|---|---|---|
| 分类 / 标签 / 评分 / 摘要 | `eval-fit run --limit 300 --seed 7` | 900 次 stage 调用，约 6 分钟 |
| 推荐理由 | `eval-fit run --limit 78 --seed 7 --require-reference reason` | 参考理由的**完整总体**，234 次调用 |
| 精选排序 | 只有全量 run 才有足够正例 | `selected` 占 2.8%，`--limit 300 --seed 7` 只含 9 个正例 |

判官对每个 run 另跑 `judge --run <dir> --calibrate 20`；`report` 在 `threshold_verdicts` 里给逐指标判定，某指标区间确认低于下限时 `report` 以 exit 1 结束。

**读 `tag_jaccard_mean` 要按可达上限归一**：我方 `normalize()` 追加 `deterministic_tags` 后截到 4，标签数均值 3.215 对参考侧 2.546，故即使预测完美，逐题 `min/max` 的期望上限也只有 **0.7475**（独立验算，见 [issues](../issues/aihot-fit-eval.md) ISSUE-FIT-03）。0.449 的实际达成度是 60% 而非 45%。

## Scope and Unverified Items

- **两次 n=20 不支撑任何达标线**，也不支撑跨维度的强弱比较：多数指标 CI 宽度超过 0.4。`thresholds` 留 `null` 是本决策的一部分，不是待办遗漏。
- RUN2 的样本全部来自 `selected: True`，其 `category_agreement`、`score_spearman` 不能外推到全题集；RUN1 的 `reason_closeness_mean` n=1 同样不可用。表中并列只为记录本轮实际读数。
- 未验证 flash 与 pro 的判官能力差异（用户裁决默认 flash，除非发现 flash 不够且 pro 明显更好；本轮未构造该对比）。
- 未验证 AIHOT 参考自身的一致性（同一条目重复采集是否给出相同摘要/理由），因此判官读数的天花板未知。
- 题集匹配依赖我站 `items` 表当前内容：`content_text` 随重新抓取可能变化，`questions.jsonl` 是当时的快照。未匹配条目共 158 条（t2 29 / t5 129），未逐条核查未匹配原因。
- 未跑全量 2741 题的基线（成本约 8223 次 stage 调用），因此本 ADR 不含全题集读数。
