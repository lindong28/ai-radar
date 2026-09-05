# Issues — aihot-fit 评测体系

来源：2026-09-05 `src/airadar/eval/aihot_fit/` 首轮交付前的 review gate（中档，独立只读 reviewer，2 HIGH / 17 MEDIUM / 11 LOW）。**两条 HIGH 与 6 条 MEDIUM 已在该轮修复**（见 [ADR-20260905-499e](../adr/20260905-499e-aihot-reference-fit-eval-system.md)），本文件收录**同批发现、未在该轮闭合**的项。

按用户 2026-09-05 常设指令（最低充分方案，按实际问题加码），这些项按「已发现但未观察到实际损害」处理：**记账不修**，等它们真的产出误导性读数或阻塞使用时再修。每条都写明**失败场景**，以便日后判断是否已发生。

**读数基线**：下列各条引用的分布类读数由 reviewer 在**修复 H1 之前**那版题集（2730 题）上取得，未在修复后的 2741 题题集上重测——量级结论不受影响（修复只改变了 11 道题的归属），但 `79 条 reason` / `585 题 tags` 这类精确计数在新题集上是 78 / 584。凡据此定阈值前须重测。

## 分组一：会让读数偏移，但方向可知

### ISSUE-FIT-01 · 摘要 closeness 有一条 schema 造成的天花板，prompt 改不动

**状态**：open · **优先级**：high（会影响达标线怎么定）

evalset 里 2667 条 AIHOT 参考摘要，用我站自己的 `EnrichOutputV2.summary_zh` 校验器判，只有 **603 条（22.6%）能通过**；AIHOT 摘要句数分布 1 句 849 / 2 句 1114 / 3 句 581，即 **77.4% 是 1–2 句，而我站 schema 强制 3–5 句**。判官 prompt 又把「编辑风格（句式、密度、语气、长度）」算作三分之一权重。

**失败场景**：`summary_closeness_mean` 里含一个由 `src/airadar/enrich/schema_v2.py` 钉死、prompt 层动不了的固定折扣。照当前读数定达标线，会把这个不可动分量写进刻度，此后任何摘要 prompt 优化都在一个有天花板的尺子上量。`why_recommend` 侧无此问题（79 条参考理由里 75 条能过我们的 35–90 字校验，94.9%）。

**闭合方向**：先测这个天花板的量级（同一批题，把参考摘要按我站 schema 改写后再判一次，看 closeness 抬升多少），再决定是放宽 schema、还是在指标里显式扣除。

### ISSUE-FIT-02 · 判官两个对照的候选槽里放的都是 AIHOT 文风

**状态**：open · **优先级**：medium

阳性对照 `candidate = reference`、阴性对照 `candidate = 另一题的 reference`——两者候选槽里都是 AIHOT 编辑写的散文，我方系统的行文从未在对照中出现在候选槽里；judge prompt 又明确标注了哪一侧是我们的。

**失败场景**：「判官对 AIHOT 风格天然给高分 / 对我方定式结构天然扣分」这类系统性折扣，在现有两个对照下读数完全相同，无法被证伪，却会整段平移 `closeness_mean`。

**闭合方向**：把我方某次输出放进 reference 槽、AIHOT 的放进 candidate 槽跑一遍，看 closeness 是否对称。

### ISSUE-FIT-03 · `tag_jaccard_mean` 的可达上限约 0.74，未在任何产物里披露

**状态**：open · **优先级**：medium

生产 `normalize()` 会把 `deterministic_tags(...)` 追加进我方标签再截到 4，参考侧没有这一层。实测我方 `|tags|` 均值 2.836（3000 条生产输出）、参考侧 2.513（585 题），**即使预测完美，期望上限 0.7424**。

**失败场景**：报告里 `tag_jaccard_mean 0.35` 读起来像"距 1.0 还差 0.65"，实际差 0.39；且一次只改变输出标签**数量**、不改变内容的 prompt 改动就能移动该指标。

### ISSUE-FIT-04 · `selected_auc` / `selected_p_at_k` 缺一个能保证正例数的抽样通道

**状态**：open · **优先级**：medium

`selected` 是 78/2741（2.8%），`--limit 20` 均匀抽样期望正例 0.57 个。实测首轮 RUN1 正例 =1，AUC 因此是单正例统计量。`--require-reference reason` 给出的又是全正例（AUC 无定义）。

**已做的缓解**：报告的 n 列现在显示 `20（正例 1）`，读者看得见它依赖几个正例；`bootstrap_ci` 在退化重采样超 5% 时不给 CI。**未做**：一个能保证正负例都有的分层抽样通道。

## 分组二：身份与归属不准

### ISSUE-FIT-05 · identity 记的是"请求的模型"，不是"实际服务的模型"

**状态**：open · **优先级**：medium

实测同一次 run：`run.json` 宣称 `deepseek-v4-flash` / `deepseek-v4-pro`，而 `outputs.jsonl` 每行 `raw.model` 是 `deepseek-v4-flash-ga-260731` / `deepseek-v4-pro-ga-260813`（20/20）。`-ga-<日期>` 后缀正是会轮换的那部分。

**失败场景**：ARK 侧换掉底层模型时 `run.json` 身份块逐字节不变，`compare_to_baseline` 照判 comparable，于是一次模型轮换会被读成 prompt 改动的效果。

**闭合方向**：把 `outputs.jsonl` 里 `raw.model` 的取值集合汇总进 `run.json` 的 identity，并纳入可比性闸。

### ISSUE-FIT-06 · `AI_RADAR_FORCE_HEURISTIC` 能让 prefilter 静默降级为关键词计数器

**状态**：open · **优先级**：medium

生产 provider 在该开关置位时走 `heuristic_prefilter` / `heuristic_score`；`require_ark_only()` 只查 ARK key，从不查这个开关，`model_selection_env()` 的过滤条件（固定 5 名 + `AI_RADAR_*` 且含 `MODEL`）也捕获不到它。实测置位后 prefilter `latency_ms=0`、`raw={'term_hits': 8}`、`_cash_signal` 不响，而身份块仍写 `deepseek-v4-flash`。

**失败场景**：`ai_recall` 报的是启发式关键词命中率，报告身份块却写着 LLM 模型名。`score` 阶段碰巧被 `_cash_signal` 拦住（`heuristic_score` 的 raw 里恰好有 `provider: 'heuristic'`），**prefilter 没有**——这说明 `_cash_signal` 不是 provider 身份检查，那次拦截是巧合。

同样未被 `model_selection_env()` 捕获的还有：`AI_RADAR_ENRICH_TEMPERATURE`（默认 0.2，故 enrich 是**非确定的**，而 bootstrap CI 只覆盖抽题方差、不覆盖解码噪声）、`AI_RADAR_FAKE_BAD_JSON` / `AI_RADAR_FAKE_OUT_OF_RANGE`、`AI_RADAR_DEEPSEEK_THINKING`、`ARK_BASE_URL`。

**注**：reviewer 未核实该开关在用户实际环境里是否置位（查它属可能回显凭据的读取面）。此处陈述的是「闸不存在」，不是「它正在发生」。

### ISSUE-FIT-07 · 评测支出以生产 stage 名落进 `llm_usage.db`，与生产流水不可区分

**状态**：open · **优先级**：medium

`run` 借用生产 `_evaluate_item`，后者一律带 `stage=` 调 `chat_json`，于是 `record_llm_usage_best_effort` 照常写 `data/llm_usage.db`。实测首轮 20 题产生 61 行（enrich 21 / prefilter 20 / score 20），`attribution_json` 里无任何 eval 标记。

**失败场景**：成本审计、单篇成本、阶段成本告警被评测流量污染；跑全量 2741 题会一次注入约 8200 行。**另**：同一链路上 `ark_breaker.record_failure(exc)` 会因评测打出的 ARK 429/quota 写 `data/ark-breaker.json` 并开 2 小时熔断，随后**生产** pipeline 会被推去按量计费的 DeepSeek 通道——评测自己因 `require_ark_only()` 摘掉了 `DEEPSEEK_API_KEY` 不会走那条路，但它给生产开的门是真的。

**闭合方向**：给 eval run 的 usage 记录加可过滤标记（`attribution_json` 里一个 `eval_run_id`），并在 eval 路径上跳过 `ark_breaker` 记账。这条与 [cost-observability.md](cost-observability.md) 同域。

## 分组三：契约复制与其它

### ISSUE-FIT-08 · `CATEGORY_SLUG_TO_PRIMARY` / `PRIMARY_CATEGORIES` 是生产同名契约的第二份副本

**状态**：open · **优先级**：low（今天两份取值一致，已逐条比过）

`common.py` 的注释自称 "Single owner"，但权威载体是 `src/airadar/enrich/classification.py`。`category_pairs` 对不在 `PRIMARY_CATEGORIES` 的行直接跳过。

**失败场景**：生产新增一个 primary category 后，那一类的题从 `category_agreement` 里悄悄消失，`n` 变小而准确率在剩下的子集上照常给出一个漂亮的数。

### ISSUE-FIT-09 · 乱配基线不是错排，有不动点

**状态**：open · **优先级**：low（方向保守）

`rng.shuffle(references)` 不排除不动点：n=20 时约 4.84%、n=10 时约 9.86% 的"随机"配对配到了自己的参考，把基线朝真值方向抬。方向是把门槛抬高，故只记账。

### ISSUE-FIT-10 · 其余低危项

| 位置 | 问题 | 失败形态 |
|---|---|---|
| `judge.py` closeness 夹取 | `max(0, min(100, x))` 静默夹取，判官返回 500 记成 100 | 畸形输出与合法满分不可分 |
| `common.py` `write_jsonl` | `questions.jsonl` 含 198 处裸 U+2028/U+2029（物理行 2730，`splitlines()` 得 2928） | 用 `splitlines()` 的下游会错切；自家 `read_jsonl` 按 `\n` 迭代不受影响 |
| `cli.py` report 分支 | stdout 不打 `stopped_early`、不打判官 `scale_ok` | 只看命令行的人拿不到"这次 run 被截断 / 判官刻度未验" |
| `common.py` `_STOP_PATTERN` | 对第三方错误文案做正则分类（无 spec 的自然语言），与 `provider/ark_breaker.py` 的关键词表会漂移 | 漏配→继续烧配额；误配→整轮 run 提前中止（可见） |
| `build.py` manifest `duplicates` | 只记 batch 名，不记 url / aihot_id | 事后无法审计销毁了哪些条目 |
| `build.py` `_X_STATUS_RE` | 不匹配无用户名段的 `https://x.com/status/<id>` | 漏配（不产生错读数） |
| `metrics.py` `render_report` | `item_ids` 为空时回落到整份题集，却仍标为"本次 run 覆盖题数" | 覆盖面被高报 |
| `run.py` 候选面 | 评测包含生产 curator 明确排除的 `kind='wechat'` 与 `enabled=0` 源，这些题的 `weighted_score` 生产里不会被算出 | 未披露（`stage_gating` 字段只披露了 prefilter 门控那一层） |
| `common.py` `readonly_db_uri` | 不做 URI 转义，路径含 `?` / `#` / 空格时 URI 被截断 | 当前路径无此问题 |

## 未被 review 覆盖的面

reviewer 按指令未发任何 LLM 请求，故 ISSUE-FIT-01 / FIT-02 是从 prompt 文本与数据分布推出的**结构性**结论，不是实测的判官偏移量；`run_judge` / `run_stages` 的端到端行为未由 reviewer 实测（由作者在 spec §7 跑通）。enrich 的解码噪声量级（`AI_RADAR_ENRICH_TEMPERATURE=0.2`）未测，因此不知它相对 bootstrap CI 宽度有多大。
