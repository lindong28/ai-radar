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

未修的 17 条按用户常设指令（最低充分方案、按实际问题加码）记账不修，逐条带失败场景落在 [docs/issues/aihot-fit-eval.md](../issues/aihot-fit-eval.md)。其中对定达标线影响最大的一条是 ISSUE-FIT-01：**77.4% 的 AIHOT 参考摘要是 1–2 句，而我站 schema 强制 3–5 句**，判官又把编辑风格算作三分之一权重，故 `summary_closeness_mean` 含一个 prompt 层动不了的固定折扣。

## Scope and Unverified Items

- **两次 n=20 不支撑任何达标线**，也不支撑跨维度的强弱比较：多数指标 CI 宽度超过 0.4。`thresholds` 留 `null` 是本决策的一部分，不是待办遗漏。
- RUN2 的样本全部来自 `selected: True`，其 `category_agreement`、`score_spearman` 不能外推到全题集；RUN1 的 `reason_closeness_mean` n=1 同样不可用。表中并列只为记录本轮实际读数。
- 未验证 flash 与 pro 的判官能力差异（用户裁决默认 flash，除非发现 flash 不够且 pro 明显更好；本轮未构造该对比）。
- 未验证 AIHOT 参考自身的一致性（同一条目重复采集是否给出相同摘要/理由），因此判官读数的天花板未知。
- 题集匹配依赖我站 `items` 表当前内容：`content_text` 随重新抓取可能变化，`questions.jsonl` 是当时的快照。未匹配条目共 158 条（t2 29 / t5 129），未逐条核查未匹配原因。
- 未跑全量 2741 题的基线（成本约 8223 次 stage 调用），因此本 ADR 不含全题集读数。
