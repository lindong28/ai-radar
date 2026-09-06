# Issues — aihot-fit 评测体系

来源：2026-09-05 `src/airadar/eval/aihot_fit/` 首轮交付前的 review gate（中档，独立只读 reviewer，2 HIGH / 17 MEDIUM / 11 LOW）。**两条 HIGH 与 6 条 MEDIUM 在该轮修复，修复复核轮又修了 3 条自伤项与 1 条升档项**（见 [ADR-20260905-499e](../adr/20260905-499e-aihot-reference-fit-eval-system.md)），本文件收录**同批发现、未在该轮闭合**的项。

按用户 2026-09-05 常设指令（最低充分方案，按实际问题加码），这些项按「已发现但未观察到实际损害」处理：**记账不修**，等它们真的产出误导性读数或阻塞使用时再修。每条都写明**失败场景**，以便日后判断是否已发生。

**读数基线**：下列各条引用的分布类读数由 reviewer 在**修复 H1 之前**那版题集（2730 题）上取得，未在修复后的 2741 题题集上重测——量级结论不受影响（修复只改变了 11 道题的归属），但 `79 条 reason` / `585 题 tags` 这类精确计数在新题集上是 78 / 584。凡据此定阈值前须重测。

## 分组一：会让读数偏移，但方向可知

### ISSUE-FIT-01 · ~~摘要 closeness 有一条 schema 造成的天花板~~ —— **假说已被干预实验证伪**

**状态**：**wontfix 2026-09-06（假说不成立）** · **原优先级**：high

**证伪读数**：取 BASE-150 里参考为 1–2 句的 50 题，让模型把参考摘要改写成 3–5 句（只改展开程度、不增删事实），再用同一判官重判我方候选。若"句数不匹配造成固定折扣"成立，改写后 closeness 应显著上升。实际：

| 切法 | n | 配对差 | 95% CI |
|---|---|---|---|
| 全部 | 50 | **−2.10** | [−5.00, +0.60] |
| 改写确实落在 3–5 句 | 40 | −1.00 | [−4.25, +2.12] |
| 句数差由 ≥1 变为 0 | 15 | −1.00 | [−6.00, +4.00] |

三个切法全部**含 0**，且方向是轻微变差（逐题 12 好 / 19 差 / 19 平）。**没有可检出的格式天花板。**

**为什么原假说看起来成立**：句数差与 closeness 的相关（r = −0.307, n=37）是真的，但不是因果的——它是**混淆**。最可能的真实来源是内容难度：AIHOT 用 1–2 句写完的条目本身信息量小，我方再写 4 句就掺进了参考里没有的内容；扣分的那些恰好参考很短，而不是"因为参考短所以扣分"。

**教训**（这条比结论更值得留）：拿相关系数去论证一个可以被干预实验直接检验的机制，读数在假说为真与为假时形态相同。若据这条相关去放宽 `schema_v2.py` 的句数约束，会白改一场，且改完读数不动时容易被解释成"prompt 还不够好"。

**对达标线的影响**：**没有天花板要扣**，达标线可以直接从基线读数定。

原描述（保留备查）：

evalset 里 2667 条 AIHOT 参考摘要，用我站自己的 `EnrichOutputV2.summary_zh` 校验器判，只有 **603 条（22.6%）能通过**；AIHOT 摘要句数分布 1 句 849 / 2 句 1114 / 3 句 581，即 **77.4% 是 1–2 句，而我站 schema 强制 3–5 句**。判官 prompt 又把「编辑风格（句式、密度、语气、长度）」算作三分之一权重。

**已量化（2026-09-05，零 LLM 成本，用已有 37 条判分）**：判官 closeness 与「我方句数 − 参考句数」的绝对差单调负相关——差 0 → 80.0（n=2）、1 → 52.5（n=4）、2 → 56.2（n=12）、3 → 46.8（n=17）、4 → 40.0（n=2）；Pearson r = −0.307。我方句数分布 {3:4, 4:28, 5:5}，参考 {1:19, 2:11, 3:7}，故该差值**结构性地**落在 2–3 而非 0。

**失败场景**：`summary_closeness_mean` 里含一个由 `src/airadar/enrich/schema_v2.py` 钉死、prompt 层动不了的固定折扣。照当前读数定达标线，会把这个不可动分量写进刻度，此后任何摘要 prompt 优化都在一个有天花板的尺子上量。`why_recommend` 侧无此问题（79 条参考理由里 75 条能过我们的 35–90 字校验，94.9%）。

**闭合方向**：先测这个天花板的量级（同一批题，把参考摘要按我站 schema 改写后再判一次，看 closeness 抬升多少），再决定是放宽 schema、还是在指标里显式扣除。

### ISSUE-FIT-02 · ~~判官两个对照的候选槽里放的都是 AIHOT 文风~~ —— **对称性对照已跑，无可检出偏置**

**状态**：**wontfix 2026-09-06（测过，偏置不存在）** · **原优先级**：medium

**证伪读数**：取 BASE-150 的 40 对，把槽位交换（参考=我方输出、候选=AIHOT 摘要）重判：

| 方向 | 均值 |
|---|---|
| 参考=AIHOT，候选=我方（原方向） | 55.6 |
| 参考=我方，候选=AIHOT（交换后） | 53.5 |

配对差 **−2.12，95% CI [−5.75, +1.62]，含 0**。判官对"哪一侧被标为我方"不敏感，`summary_closeness` 是真实相似度读数而非偏袒的产物。

**与 ISSUE-FIT-01 合起来的方法教训**：本文件最初记的两条高/中优先级"结构性"顾虑都是从读代码与看分布推出的**经验断言**，一测都不成立；而同一轮 review 报的两条 HIGH（URL 丢 query、可比性闸哈希错对象）是**机制性缺陷**，读代码即可确证、实测也复现。两类混在同一份 findings 里同等对待，会在后一类上白花成本——经验断言要先设计一次能证伪它的干预，再决定要不要修。

原描述（保留备查）：

阳性对照 `candidate = reference`、阴性对照 `candidate = 另一题的 reference`——两者候选槽里都是 AIHOT 编辑写的散文，我方系统的行文从未在对照中出现在候选槽里；judge prompt 又明确标注了哪一侧是我们的。

**失败场景**：「判官对 AIHOT 风格天然给高分 / 对我方定式结构天然扣分」这类系统性折扣，在现有两个对照下读数完全相同，无法被证伪，却会整段平移 `closeness_mean`。

**闭合方向**：把我方某次输出放进 reference 槽、AIHOT 的放进 candidate 槽跑一遍，看 closeness 是否对称。

### ISSUE-FIT-03 · `tag_jaccard_mean` 的可达上限约 0.74，未在任何产物里披露

**状态**：open · **优先级**：medium

**状态更新 2026-09-06：独立验算确认，这一条是真的**（与同批被证伪的 FIT-01 / FIT-02 不同——它是**数学后果**而非经验断言，两侧标签数分布定了，交并比的上界就定了，不需要也不可能被实验推翻）。

生产 `normalize()` 会把 `deterministic_tags(...)` 追加进我方标签再截到 4，参考侧没有这一层。三个算法一致：

| 算法 | 上限 |
|---|---|
| BASE-150 实际配对的 33 题，逐题 `min/max` 取均值 | **0.7475**（其中两侧标签数相同、上限为 1.0 的只有 8/33） |
| 两侧边际分布独立配对的期望 | 0.7266 |
| reviewer 首轮读数（修复前题集） | 0.7424 |

标签数分布：我方 `{2:26, 3:61, 4:57}` 均值 3.215；参考 `{1:31, 2:268, 3:222, 4:61, 5:2}` 均值 2.546。

**对读数与达标线的影响**：`tag_jaccard_mean = 0.449`（BASE-150）的分母不是 1.0 而是约 0.75——**实际达成度是 60%，不是 45%**。这条指标的达标线必须按可达上限归一，否则会定在一个永远够不着的刻度上；同时一次只改变输出标签**数量**、不改变内容的 prompt 改动就能移动它。

**失败场景**：报告里 `tag_jaccard_mean 0.35` 读起来像"距 1.0 还差 0.65"，实际差 0.39；且一次只改变输出标签**数量**、不改变内容的 prompt 改动就能移动该指标。

### ISSUE-FIT-04 · `selected_auc` / `selected_p_at_k` 缺一个能保证正例数的抽样通道

**状态**：open · **优先级**：medium

`selected` 是 78/2741（2.8%），`--limit 20` 均匀抽样期望正例 0.57 个。实测首轮 RUN1 正例 =1，AUC 因此是单正例统计量。`--require-reference reason` 给出的又是全正例（AUC 无定义）。

**已做的缓解**：报告的 n 列现在显示 `20（正例 1）`，读者看得见它依赖几个正例；`bootstrap_ci` 在退化重采样超 5% 时不给 CI。**未做**：一个能保证正负例都有的分层抽样通道。

## 分组二：身份与归属不准

### ISSUE-FIT-05 · identity 记的是"请求的模型"，不是"实际服务的模型"

**状态**：**resolved 2026-09-05**（修复复核轮升档为结构性后修复：`run.json` 的 `identity.stages` 增 `served_models`，取自各响应的 `raw.model`；实测 `deepseek-v4-flash` → `deepseek-v4-flash-ga-260731`、`deepseek-v4-pro` → `deepseek-v4-pro-ga-260813`）· **原优先级**：medium

升档理由（reviewer 语）：H2 的修复把 `stage_identity_diff` 变成了读者判断"两次 run 之间流水线没变"的**唯一凭据**，而它读的 `model_id` 是类常量——修复把一个已知记错的字段提拔成了可比性判据，比原来更重。

原描述：

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

**已实测（2026-09-05）**：本轮三次基线 run 的 item 在 `data/llm_usage.db` 留下 **970 行**（enrich 344 / prefilter 313 / score 313），占当日该表 4066 行的 **23.9%**。

**失败场景**：成本审计、单篇成本、阶段成本告警被评测流量污染；跑全量 2741 题会把这个数推到约 8200 行。**另**：同一链路上 `ark_breaker.record_failure(exc)` 会因评测打出的 ARK 429/quota 写 `data/ark-breaker.json` 并开 2 小时熔断，随后**生产** pipeline 会被推去按量计费的 DeepSeek 通道——评测自己因 `require_ark_only()` 摘掉了 `DEEPSEEK_API_KEY` 不会走那条路，但它给生产开的门是真的。

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

## 分组四：修复复核轮的残留项

2026-09-05 的修复复核轮确认 H1 / H2 修复成立（新增的 51 条不匹配经逐条核实**全部是原本的错匹配**，反向 0 条；「两侧 query 参数集合不同」这一担心在当前数据里为 0 条），并报回 4 条由修复本身引入的新问题。**其中 3 条已修**（`improved` 三态化、零宽 CI 不得作判决依据、judgments 合并不得用 `None` 覆盖已付费读数），各自跑了双向验证。下列是仍未闭合的。

### ISSUE-FIT-11 · `subset_sha256` 锚的是抽样集合，不是成功测到的集合

**状态**：open · **优先级**：low

`item_ids` 记的是抽中的题，stage 级 error 的题仍留在里面，故 `n_joined` 与 `subset_sha256` 可能不一致。`stopped_early` 闸挡住了最大的一类。另：两份**都缺** `subset_sha256` 的旧 payload 互比时 `None == None`，该闸失效（还需 `questions_sha256` 相同，触发面很窄）。

### ISSUE-FIT-12 · 只读探针对准的是 `readonly_db_uri`，不是 build 实际用的那条连接

**状态**：open · **优先级**：low

实测：把 `build_evalset` 内部换成读写连接后，测试里那条断言**仍然通过**。它证明的是 `readonly_db_uri` 会生成只读 URI，不是 `build_evalset` 用了它。要真正钉住得把探针挂在 build 打开的那条连接上（monkeypatch `sqlite3.connect` 记录实际 URI）。这比原来的空断言强（原来两种情况读数完全相同），但仍打偏一格。

### ISSUE-FIT-13 · 两处小的一致性债

**状态**：open · **优先级**：low

- `metrics.json` 新增的 `stages`（identity dict）与同目录 `run.json` 的 `stages`（stage 名 list）**同名异型**——这是本轮 H2 修复引入的命名冲突，用户裁定本轮收口故未改，改法是把 metrics 侧改名为 `stage_identity`。
- 测试里 `with sqlite3.connect(...)` 的上下文管理器是**事务**管理器不是关闭器（出块后连接仍可 `SELECT 1`）。测试里无害，但这正是本项目 [healthz 500/CANTOPEN 连接泄漏](general.md) 那次的同一写法，不该被复制到生产路径。

### ISSUE-FIT-14 · `run` 全部结果攒在内存里、最后一次性写盘，中途崩掉即全丢

**状态**：open · **优先级**：medium（全量规模下才有实感）

`run_stages` 把每题结果收进 `rows` 列表，等所有 future 完成后才 `write_jsonl`。全量 2741 题一轮约 68 分钟，**任何时刻崩溃、被 kill、或机器休眠都丢掉全部已付费的调用**——没有 checkpoint，重跑要从零开始。

**已实测排除的一个担心**：不是内存问题。全量跑到 44%（3632/8223 次调用）时 RSS 仅 0.24 GB，系统空闲 53%，外推满载约 0.5 GB。所以风险是**丢失**不是 OOM。

**失败场景**：一次全量基线跑到 95% 时进程被终止，8000 次已付费调用的产物全部消失，且 `llm-usage-eval.db` 里留着它们的计量行——账上花了钱、产物为零。

**闭合方向**：`as_completed` 循环里增量追加到 `outputs.jsonl`（每题一行，本就是 JSONL），run.json 在最后写；重跑时读已有 outputs 跳过已完成的 question_id。与 ISSUE-FIT-12 的 judgments 合并逻辑同构（那一半已在 `c8306ea` 做了）。

### ISSUE-FIT-15 · prompt 身份锚只哈希 prompt 模块，渲染进去的常量改了它也不变

**状态**：**resolved 2026-09-06**（发现当轮即修）

**怎么发现的**：2026-09-06 改 `CONTROLLED_VOCABULARY_V2`（词表被 `prompts_v2.py` 渲染进 prompt）后跑对比，`stage_identity_diff` 报 `{}`——**一次真实的 prompt 内容改变，对身份块完全不可见**。`prompt_sha256` 哈希的是 `src/airadar/enrich/prompts_v2.py` 这个文件，而词表常量住在 normalizer 模块里。

**为什么重要**：它与 ISSUE-FIT-05（`model_id` 记的是请求的而非实际服务的）是同一形态——**身份锚指向代理物而非真正决定行为的东西**，后果是两次流水线不同的 run 被判"完全相同"，差异被归因到别处。且它比 FIT-05 更隐蔽：那一条至少在 `outputs.jsonl` 的 `raw.model` 里留着真值，这一条在整个 run 目录里没有任何痕迹。

**修复**：`stage_identity` 增 `rendered_inputs_sha256`，enrich 段取受控词表的摘要。验证：改动前后哈希分别为 `6da95d66…` / `33f2dbcf…`，新锚报得出这次改动。

**未覆盖**：只处理了 enrich 的词表这一个已知实例。其它 stage 的 prompt 若也渲染外部常量，同样的洞仍在——目前 `_rendered_inputs_sha256` 对 prefilter / score 返回 `None`，没有核查过它们的 prompt 是否也有渲染项。

### ISSUE-FIT-16 · 拟合工作的 review findings（2026-09-06 第二轮 prompt 优化）

中档 review gate 对累计 prompt 改动的审查。**已修 6 条**（H1 编造、H2 开头塌缩、M4 冗余、M5 长度目标未下传、M6/M7 兜底枚举被证伪），**仪器补了配对比较**（H3）。下列是**记账不修**的。

**H3 的另一半：`reason_bigram_jaccard` 的原始值被形式抄袭污染。** 该指标自带的 `shuffled_reference` 置换基线从 0.0126 涨到 0.0386（3.1 倍）——把候选与**随机另一条**参考配对也涨这么多，说明这部分纯是共享模板。信噪比 value/baseline 因此**下降** 4.46 → 3.19。reviewer 做了反向检验：剥离模板词后增益仍在且更干净（0.0551→0.1084，比值 5.46→8.19），**所以内容层确有真实收敛**，但任何据 `reason_bigram_jaccard` **原始值**判"拟合改善了多少"的下游都被高估。闭合方向：给该指标加一个剥离模板词的变体，或在报告里并列 value/baseline 比值而非只给 value。

**判官 rubric 有三分之一是风格，而风格最容易被模板抄到。** `judge_prompts.py` 明写「事实覆盖、取舍重点、编辑风格」三方面各占约三分之一。按输入丰度切分本轮增益：`content_text` 仅有标题的 38 题 Δ=+7.11，有正文的 30 题 Δ=+6.33——**在内容层收敛结构性不可能的那批上，增益反而更高**。这说明 closeness 的提升里相当大一部分来自风格分。闭合方向：判官加一个只判事实覆盖的分维读数。

**`_GENERIC_REASON_PREFIXES` 是死代码且看不见新形态。** `schema_v2.py:12-15` 的模板检测器在五个 run 上 0/292、0/288、0/282 全未触发，连 AIHOT 的 78 条参考也 0/78。而本轮真正产生的退化形态——过冲版里 76.2% 逐字以「原文给出」开头——它结构上看不见（只列了旧的 5 个前缀）。它给出"模板化有闸看着"的错觉。

**标签数违规是既有问题，但分布正被推向下边界。** 13/300 里 11 条是复发项，vs CAT3 的 9/300 p=0.516 不显著；纯重复对（同 prompt 同子集）本身就有 7 vs 5 的抖动。但原始标签数分布向 2 迁移是**显著的**（A-D vs REASON χ²=18.14, p≈0.0001）——方向对拟合有利（AIHOT 均值 2.55，我方仍偏高），但它把质量推向 `<2` 那道硬闸。**正确落点是 normalizer**（不足 2 个时用 `deterministic_tags` 补齐而非拒收），不是继续加 prompt 压力。

**`why_recommend` 90 字上限与目标形态结构性对冲。** AIHOT 自己有 4/78（5.1%）超过 90、最长 115。被教的形态在参照站点本身就比我方 schema 上限长，两者不可能同时满足到 100%。

**规则推导集与评测子集有 21 条重叠**（7% 的 300 题子集）。剔除后 `category_agreement` 0.7343 vs 全子集 0.7354，结论不受影响，但下次划留出集应显式排除评测子集。

**解码噪声未测**：`AI_RADAR_ENRICH_TEMPERATURE` 非零，仅有一对纯重复（`A-D-300` vs `ITER-300`，同 prompt 同子集）可估抖动，n=1 对，不足以给区间。所有 300 题单 run 的小计数差异都应按此打折。

## 未被 review 覆盖的面

reviewer 按指令未发任何 LLM 请求，故 ISSUE-FIT-01 / FIT-02 是从 prompt 文本与数据分布推出的**结构性**结论，不是实测的判官偏移量；`run_judge` / `run_stages` 的端到端行为未由 reviewer 实测（由作者在 spec §7 跑通）。enrich 的解码噪声量级（`AI_RADAR_ENRICH_TEMPERATURE=0.2`）未测，因此不知它相对 bootstrap CI 宽度有多大。

### ISSUE-FIT-17 · 标签数截断的第一版把失败从一个桶推进了另一个（2026-09-06）

**背景**：ISSUE-FIT-16 已指出正确落点是 normalizer 而非继续加 prompt 压力。第一版按此改成"多于 4 个就截到 4 个"，随即在 300 题子集上实测。

**读数**（`SCORE-300-seed7` → `TRUNC-300-seed7`，同一子集同一批 prompt）：

| 失败桶 | 基线 | 截断后 |
|---|---|---|
| `tags must contain 2-4` | 4 | **0** |
| `tags must normalize to at least 2 unique` | 2 | **10** |
| `why_recommend` 长度不足 | 6 | 7 |
| 合计 | 12 | **17** |

**根因**：截断发生在按词表过滤**之前**，于是按位置切。排在第五位的合规标签被切掉，而排在前面的越界标签活到过滤那一步才被丢——净效果是幸存标签更少，直接撞下面那道"有效标签不足 2 个"的底线。重复标签同理会占掉 4 个名额中的若干个。改为过滤 → 去重 → 截断后两种输入都能救回，两条测试各自经突变验证。

**顺带取得的读数**（reviewer 实测，n=572 / 555 两个 run）：模型标签的位置**确实携带信息**——与 AIHOT 参考标签的一致率按位置为 0.79 / 0.55 / 0.48 / 0.38，而把每条的标签列表随机置换 200 次后四个位置一律塌到 ~0.62。所以"保留最靠前的幸存者"这个选择成立。两个限制随结论保留：样本只含 normalize 成功的行（有选择偏差），且最大观测位置是 4，"第五个最弱"是外推。

**记账不修**：

- **`run_stages` 全无测试。** `git grep run_stages -- tests` 零命中：外层 handler、stop 传播、`skipped` 计数、rows 落盘都没有覆盖。本次修的 `served_models` 已单独钉住，但"一个 item 失败不终结整个 run"这条性质仍无测试。reviewer 明确标注这条追溯不到来源（用户未要求、任务开始前无生效契约），故只记账。
- **已失败的存量行不会自愈。** `"output rejected"` 属 `DETERMINISTIC_ENRICH_ERROR_PREFIXES`，`ENRICH_FAILED_RETRY_BACKOFF_HOURS = 24`，而 `_candidate_rows` 默认 `since` 同为 24h（按 `fetched_at`）。`fetched_at` 早于 24h 的行常规跑不会再被挑到，要靠 `--item-ids` 定向回填。上一个已 commit 的词表修复同样如此——两条 CHANGELOG 里"现在……照常保留"都只对此后新处理的文章成立。
- **2-4 不合规的失败计数通道关闭了。** 此前不合规是 `item_evaluations.error` 里一条可 grep 的行（75 这个数正是这么数出来的），现在它不再产生错误行，而生产只存归一化后的输出、不存模型原始标签。eval run 的 `outputs.jsonl` 仍保留 `raw.json.tags`（本轮另修了 normalizer 拒收时 raw 不落盘的洞），所以拟合工作要的那个读数还在；生产侧没有了。第一版曾用一个纯函数 `raw_tag_count_was_out_of_range()` 声称保住了这个通道，实际没有任何生产调用方，已删除。
- **75 篇里"多"与"少"各占多少无从得知**：旧代码两个方向共用一条错误消息，且被拒时原始标签没落盘。CHANGELOG 已按此改写，不再声称是"写到五个"。

### ISSUE-FIT-18 · 限流会摧毁 run 的身份记录，而不只是截断它（2026-09-06，已修）

**现象**：300 题的 run 写完 `outputs.jsonl`（300 行完整）后崩在 `AttributeError: 'NoneType' object has no attribute 'get'`，`run.json` 没有落盘——于是那次 run 有全套输出、没有任何身份块，按 `evaluation-integrity` 的要求整份不可用。

**根因**：`run.py` 的 `served_models()` 写的是 `payload.get("output", {}).get("raw")`。`dict.get(key, default)` 的 default **只在键缺失时生效**，而早停分支与外层异常 handler 都把键写成显式的 `None`。一次 ARK 429 会让**其余每一行**都变成那个形状，所以第一个限流不是让 run 少跑几题，是让它整份作废。

**误诊记录**（值得留着）：第一版归因为"prefilter/score 的 `_evaluate_item` 会把 provider 异常抛出去，而 enrich 会接住"，并在 `_evaluate` 外加了一层捕获。reviewer 指出该调用点**本来就有**一个逐字等价的 `except Exception`（`git log -S` 显示自该文件第一个 commit 起就在），所以异常根本逃不出去；那层捕获唯一的行为差是把 `output` 由 `None` 变成 `{}`，恰好把真正的缺陷遮住了——**而且遮不住早停那条路径**，也就是 FULL2 撞 429 的那条。归因已按阳性对照重做：用早停分支写出的行形状直接调 `served_models`，复现出逐字相同的 `AttributeError`。冗余的捕获已撤回。

**未覆盖**：`data/eval-fit/runs/FULL2-20260906` 的判官读数仍不可用（`stopped_early=True`、summary 1388/2606、reason 0），需在修复后重跑。

### ISSUE-FIT-19 · AIHOT 的打分函数刻画：它打的是事件，不是内容（2026-09-06）

拟合打分器之前先刻画参照物的决策函数（`prompt-distribution-fitting.md` §1）。以下读数取自 `FULL2-20260906` 的 2741 条配对，全部离线计算、未发任何 LLM 请求。

**一、五个信号都没有饱和，问题不在测量分辨率。**

| 信号 | 与 AIHOT 分数的 ρ | 均值 | sd | 不同取值 |
|---|---|---|---|---|
| relevance | 0.477 | 5.15 | 2.69 | 18 |
| density | **0.542** | 3.95 | 2.39 | 16 |
| recency | 0.412 | 6.49 | 2.28 | 15 |
| authority | 0.373 | 5.15 | 2.10 | 18 |
| engineering | 0.425 | 3.42 | 2.69 | 18 |

**二、加权那一步目前没有增量。** 当前 `AIHOT_FIT_WEIGHTS` 合成分 ρ=0.5389，而 density 单独一个信号是 0.5420。合成分现在等于一个更贵的 density。

**三、AIHOT 的分数按它自己的类别分层，而我们的打分函数完全不看类别。** 均分：model 54.92（n=305）、industry 49.45（512）、paper 46.58（304）、product 42.07（641）、tutorial 37.91（979）；整体 43.90，sd 16.87。也就是说类别间的落差（17 分）接近整体一个标准差。把**我方预测的**类别（不是参考标签——推理时拿不到）折成偏移量加进合成分，在 20 次随机 50/50 留出上稳定 **+0.0340**（20/20 为正，sd 0.0041，n_test≈1303），偏移量为 model +8.31 / industry +6.07 / paper −0.66 / product −3.12 / tutorial −8.31。

**四、两端的定性读数说明它在打什么。** 分数最高的 18 条里 14 条是同一个事件（NVIDIA 收购 Hugging Face），其中一条正文只有 `brilliant fit. https://t.co/…`，得 93 分。分数最低的一批是段子、单句和裸链接。**AIHOT 打的是"这件事对 AI 领域有多大"，不是"这条内容对读者有多有用"**——一条零信息量的推文只要指向大事件就能拿到接近满分。而我方打分提示词的第一句是 "You score AI news for an engineer's personal radar"，并专设一个 `engineering` 维度（拟合权重已判其为 0）。缺的那个维度是事件重要性。

**五、tier 乘数方向是反的。** 按 AIHOT 分数：T2 均分 51.68（n=889）> T1 44.68（128）> T1.5 39.82（1724）。T1.5 是 X/Twitter 那一层。生产 `TIER_MULTIPLIERS` 是 T1 1.25 / T1.5 1.0 / T2 0.75——恰好倒过来。这解释了此前"拿掉 tier 乘数使 Spearman 涨 0.09"的读数，并提示反向的 tier 信号可能还有增量（未测；tier 与内容类型高度混淆，不能直接归因）。

**条目级打分的天花板（已测，见 ISSUE-FIT-22）。** 下面这段记录的是第一次尝试的失败，保留作为方法教训： 若 AIHOT 确实在打事件，则一批指向同一事件、内容详略不同的条目会拿到相近的分数，条目级特征区分不了它们，存在不可逾越的上限。试图用标题 shingle 聚类估这个上限**失败了**：Jaccard≥0.5 太紧，2741 条里只有 97 条进了多条簇，"oracle 簇均值"退化成每条自成一簇、ρ=0.996——**那个 0.996 是聚类失败的产物，不是天花板读数，不得引用**。定性证据（第四条）仍成立。这一项目前没有读数，所以"够接近 AIHOT"缺一个可据以判停的上界。

### ISSUE-FIT-20 · FULL2-20260906 的判官读数作废，不重跑而是被取代（2026-09-06）

`data/eval-fit/runs/FULL2-20260906/judge.json`：`stopped_early=true`，summary 判了 1388/2606、reason 判了 0，`stop_reason` 是 ARK 的 `429 AccountRateLimitExceeded`。**这份读数不可用于任何达标判定**，别拿它当基线。

不重跑它的理由：`_judgeable` 不跳过已判过的题，重跑等于全判一遍（2606×2 ≈ 5200 次调用）；而它的 enrich 输出产生于标签顺序修复之前，判官判的候选本身已经过期。花同样量级的钱重判一份过期输出，不如让 `FULL3-20260906` 取代它。


### ISSUE-FIT-21 · 标签失败的方向被我判反了，真正的落点是底线（2026-09-06）

ISSUE-FIT-17 记的截断修复解决的是"给多了"这个方向。修好 raw 落盘之后重测，方向是反的：

`FULL3-20260906` 的 81 条标签类失败，全部保留了模型原始输出：

| 模型给出的标签数 | 1 | 2 | 3 | 4 | >4 |
|---|---|---|---|---|---|
| 条数 | **52** | 22 | 3 | 4 | **0** |

按写入词表算的合规幸存者数：0 个 3 条、1 个 68 条、2 个 10 条。**没有一条是给多了。** FULL2 那 75 条 `tags must contain 2-4` 共用一条错误消息、且被拒时原始标签不落盘，所以先前无从分辨——reviewer 的 F5 正是指出这一点，而我当时按"给多了"写进了代码注释与 CHANGELOG，两处都已订正。

**处置**：底线从"至少 2 个受控标签"放宽到 1 个（`schema_v2.py` 的 `min_length` 与 normalizer 的 floor 各一处）。依据是参照物本身：2741 条参考里 **2157 条一个标签都不打**，标签数分布为 0:2157, 1:31, 2:268, 3:222, 4:61, 5:2。"至少两个"是我们自己的产品约定，不是对 AIHOT 的贴近。预计把 117 条 enrich 失败降到约 39 条。

**评估过但没采用的方案**：按类别补一个规范标签。参照物支持这个映射——在 AIHOT 确实打了标签的条目里，类别的规范标签出现率为 model 97%、product 90%、industry 84%、paper 88%（tutorial 无主导标签，众数 现象/趋势 仅 32%）。但模拟到实际失败上只救回 22/81：81 条里 60 条是 tutorial，它们仅剩的那个标签往往就是兜底标签本身。为满足自家下限去补一个 32% 准确率的标签，方向上是降低保真度，不是提高。

**未覆盖**：`FULL3-20260906` 的 enrich 产出于放宽之前，因此判官只覆盖 2624 条，缺的 117 条系统性偏向"模型标签给得少"的那一类，与内容单薄相关。据它定的达标线因此带一点选择偏差；下一次全量跑完应重定。

### ISSUE-FIT-22 · 条目级打分的天花板：两个上界与一次三试两败的取证（2026-09-06）

"够接近 AIHOT" 此前没有可判停的上界。现在有两个，来自两条互不依赖的路径。

**上界一：打分器自身的可重复性 ρ = 0.9723。** `FULL-20260906` 与 `FULL2-20260906` 用**逐字节相同的**打分器提示词（sha `a6cce940…`）跑了同一批 2741 条，两次之间只有解码噪声。分维一致度：relevance 0.9788、density 0.9791、engineering 0.9748、authority 0.9550、recency 0.8835。一个测量与外部目标的相关不会超过自身信度允许的范围（两个各带噪声的变量，观测相关的上界是 √(r_xx·r_yy)，而 AIHOT 自身信度未知但至多为 1），故上界 √0.9723 = **0.986**。

**这条读数的用处是排除一个解释**：我们与 AIHOT 的差距**不是抖动**。旧向量 0.5389 只到该上界的 55%，新向量 0.6286 也只到 64%。剩下的是模型与 AIHOT 的真实分歧，可以继续压。

**上界二：只知道事件的 oracle，ρ ≈ 0.794。** 用 IDF 加权的摘要重叠取"同一事件"的**配对**（不做聚类，见下），309 对同事件配对的分差中位 8.0（随机配对 17.0），事件内 sd 10.25 对全语料 sd 16.87——**AIHOT 分数方差的 63% 是事件属性，37% 是条目属性**。一个只认得事件、给每个事件打其均分的 oracle 因此止步于 0.794。当前 0.629 是它的 79%。

注意这个 0.794 **不是我们的上限**：条目级信息还占 37% 的方差，一个既认事件又读条目的打分器可以越过它。它衡量的是"事件重要性"这个维度最多能带来多少——恰好解释了为什么加 `significance` 一项就抬了 0.06。

**方法教训：三个仪器，两个失败，失败方式各不相同，而其中一个会给出看起来完全合理的数字。**

| 尝试 | 做法 | 结果 |
|---|---|---|
| 一 | 标题 shingle 聚类，Jaccard ≥ 0.5 | 2741 条里只有 97 条进了多条簇，"oracle"退化成每条自成一簇 → **0.996**，纯属聚类失败的产物 |
| 二 | 仓内 `deduplicate_candidates` | 它是 content-hash + URL 的**精确**匹配，不是近重复聚类；假设错误，路径不通 |
| 三 | AIHOT 摘要 + IDF 加权 + union-find | 阳性对照过了（HF 15 条同簇），但链式合并出一个 418 条、sd 18.65（大于全语料 sd）的巨簇 → **0.887**，同样是产物 |
| 四 | 同样的重叠度量，但只取**配对**、不做传递合并 | 阳性 13 对 HF、阴性随机对误判 2/19990（0.01%），两个对照都过 → 上表读数 |

第三次的 0.887 是最危险的一个：它落在"直觉上合理"的区间里，如果不是先看了簇大小分布、发现 418 这个数，它会被当成结论写出去。**阳性对照单独不够**——第三次的阳性对照是过的。

**限制随结论保留**：同事件配对由摘要重叠识别，被多篇报道覆盖的大事件因此过采样；0.794 假定 oracle 给每个事件打其均分（这确实是"每事件一个常数"下的最优预测），不覆盖同事件内部按条目区分的那部分。

### ISSUE-FIT-23 · 类别偏移被 significance 吸收，不再单独加（2026-09-06，阴性结果）

ISSUE-FIT-19 记过：把**我方预测的**类别折成偏移量加进当时的五维合成分，在 20 次留出上稳定 +0.0340（20/20 为正）。significance 进入权重之后在 `FULL3-20260906` 的 2624 条上重问同一问题：**+0.0011（sd 0.0034，13/20 为正）**，与零不可区分。

学到的偏移量本身几乎没变（model +5.8 / industry +5.8 / paper +0.7 / product −2.4 / tutorial −8.2，对比 ISSUE-FIT-19 的 +8.3 / +6.1 / −0.7 / −3.1 / −8.3），所以不是信号消失了，是 `significance` 直接测到了同一件事、且测得更好（它单独 ρ=0.6234，而类别偏移只是把类别的均分差搬过来）。

**结论：打分函数里不加类别项。** 这条杠杆关闭。ISSUE-FIT-19 里那个 +0.034 仍然正确，只是它已经被更好的东西取代了——记在这里，免得日后有人看到 FIT-19 又去加一遍。

### ISSUE-FIT-24 · 分类剩余误差集中在 tutorial 召回（2026-09-06，下一个杠杆）

`FULL3-20260906` 的 2624 条已 enrich 条目，`category_agreement` = 0.7100（FULL2 为 0.7199，差 0.01 ≈ 26 条，在解码抖动量级内；本轮没有改 enrich 提示词，两者应当相同）。`tag_jaccard` 0.5296 / 0.5293，同样持平。

混淆矩阵（行 = AIHOT，列 = 我方）：

| ref \ ours | model | product | industry | paper | tutorial | 召回 |
|---|---|---|---|---|---|---|
| model | 234 | 18 | 12 | 2 | 25 | 80.4% |
| product | 57 | 422 | 62 | 3 | 77 | 68.0% |
| industry | 12 | 16 | 422 | 1 | 41 | 85.8% |
| paper | 21 | 0 | 12 | 242 | 25 | 80.7% |
| **tutorial** | **107** | 52 | **205** | 13 | 543 | **59.0%** |

**误差高度集中**：tutorial 一行错了 377 条，其中被判成 industry 的 205 条与被判成 model 的 107 条合计 312 条，是全部错误的最大单一来源。这与 ISSUE-FIT-19 的定性读数一致——tutorial 是 AIHOT 的兜底类（979/2741），装的是闲聊、段子、单句观察，而我们仍在按"它讲到了模型/行业"往上判。次大的是 product→model 的 57 条。

**下一步的落点**：不是继续在类别定义上加字，而是给 tutorial 一条**优先级规则**——ISSUE-FIT-19 里 AIHOT 分数最低的一批全是无事件的闲聊，说明它的判据里有"这条内容有没有报道一件事"这一层，而我们的提示词只描述了五类各是什么、没说清"两类都像时选哪个"。本轮未做，因为本轮的改动面已经在生产排序和校验契约上，不宜再叠。

### ISSUE-FIT-25 · 达标线按 FULL3 重定（2026-09-06）

原达标线出自 `FULL-20260906`，即本轮全部拟合改动之前，已经严重过期：分类的地板是 0.3907、依据的点估计 0.5104，而当前读数是 0.7100——那道闸此后永远不会开火。

重定用的是同一条规则（地板 = 全量点估计 − 2 × 门禁配置的半宽），零调用成本：`scripts/derive_eval_fit_subset.py` 从 FULL3 里切出 300 题门禁子集，`subset_sha256` 与既有配置**逐字相同**（`fcf6e94f…`），半宽由该子集自己的 CI 实测给出，不再沿用旧值。

| 指标 | 旧地板 | 新地板 | 可检出回归 |
|---|---|---|---|
| ai_recall | 0.8645 | 0.8657 | 0.0500 |
| category_agreement | 0.3907 | **0.6016** | 0.1084 |
| score_spearman | 0.2181 | **0.4392** | 0.1534 |
| summary_closeness_mean | 0.4922 | **0.5531** | 0.0592 |
| summary_bigram_jaccard | 0.1893 | 0.2359 | 0.0412 |
| tag_jaccard_mean | 0.3295 | 0.3827 | 0.1469 |
| reason_closeness_mean | 0.2781 | 0.3208 | 0.1169 |
| reason_bigram_jaccard | 0.0376 | 0.0663 | 0.0327 |

验证：在切出来的那个子集上跑一次带阈值的 report，6 项 `confident=True`，两项 reason 指标正确报"floor is for subset 389e147c…; this run is fcf6e94f…"——它们的地板钉在 78 题 reason 子集上，不适用于 300 题子集。这正是 `subset_sha256` 配对机制该有的行为。

**两个限制随结论保留**：

- **reason 两项一度被我按沿用旧半宽处理，那是错的，已改正。** 我当时的推理是「切子集的脚本不支持按参考字段过滤，所以那个配置切不出来」——脚本这一点属实（它的 `sample_questions` 在全部 2741 题上采样，`--limit 78 --seed 7` 会给出完全不同的 78 题），但结论外推错了：`ITER-REASON-78` 的 `pool_eligible = 78`、`limit = 78`，**那个「子集」根本不是抽样，它就是全部有参考理由的题**，而 FULL3 已经测过同一批（n=77，一条无候选）。半宽因此直接取自 FULL3 自己的区间 [0.3779, 0.4948]，是在**当前**质量水平上测的，比沿用旧 run 的 0.0469 更该用。
  这条订正是 `reverse-assertion-gate` 拦下来的：一句「某某做不到」会直接删掉后续该做的检查，而反向误判没有下游能发现它。
- **score_spearman 的地板偏保守约 0.036**：FULL3 记录的 `fit_score` 用的是它运行时那套五维向量（0.5926），而随后上线的向量在同一批数据上读作 0.6286。保守的地板不会误报，只是检出得少。下一次全量跑完应重定。

**推导已进仓**：`scripts/derive_eval_fit_thresholds.py`。此前这套推导只活在 scratchpad 的一次性脚本里，而地板会**静默过期**——一道地板定在点估计 0.51 的闸，等指标涨到 0.71 之后对任何 run 都报通过，包括一次真的回落了 0.10 的 run。脚本从两份 metrics.json 读数、不手抄，并复现出与本次逐字相同的 8 个地板。

**权威副本尚未同步（阻塞在你那里）**：按 ADR-20260905-499e，题集与达标线的权威副本在数据仓 `evalsets/aihot-fit-v1/`，主仓 `data/eval-fit/` 整个是 gitignored 的构建产物。那个数据仓是 submodule `benchmarks/aihot`（`git@github.com:lindong28/ai-radar-data.git`），而**本 checkout 里它没有初始化**（空目录，`git submodule status` 前缀为 `-`）。远端可达，HEAD 为 `5a9b87e1`，而主仓记录的 gitlink 是 `7d9de5e7`——**远端已经走在前面**。所以同步不是复制一个文件：要初始化一个落后的 gitlink、在共享工作树里 checkout、然后提交并推动 gitlink，这几步都会改变主仓状态。交你裁决，不自行执行。同步之前，数据仓里那份 thresholds.json 仍是 `FULL-20260906` 的旧地板（分类 0.3907）。上表的新值不会因此丢失——它就记在这里。

`selected_auc` 与 `selected_p_at_k` 仍不设闸，理由未变（78 个正例在 300 题子集上区间宽到 [0.5249, 0.8402]）。全量读数 0.7907 [0.7382, 0.8349] 已记入 `_meta`。

### ISSUE-FIT-26 · 排序改动的独立评审：九条 finding 的处置（2026-09-06）

reviewer 复算了 diff 与文档里的全部读数（0.4502 / 0.6286 / 0.5925 / tier 乘数代价 0.093 / AIHOT 分层均分 / 标签分布 0:2157），**全部对得上**；未发现会导致崩溃、数据损坏或不可逆后果的缺陷。以下是九条 finding 的处置。

**已修（7 条）**

1. **`weighted_score` 的核心维度守卫是坏的。** 我写成只查**有权重**的维度，于是在拟合向量下实际只要求 density 与 authority——relevance / recency / engineering 缺失或为 null 一律静默通过，而 ADR 与 docstring 两处都宣称五维必填。守卫改为不论权重都查，测试 `test_a_zero_weighted_dimension_need_not_be_present` 按新契约重写为 `test_a_core_dimension_is_required_even_at_zero_weight`。
2. **`SOURCE_QUOTA_SCORE_SEMANTICS` 变成了一句假话。** 它是 ADR-20260903-bc36 冻结校验的数据契约字符串，值为 `tier_adjusted_before_rank_calibration` 而乘数已不施加——改动前后写下的 run 在账面上完全同形，审计者分不出。已改为 `unadjusted_before_rank_calibration`，测试同步。
3. **`reason_json.tier_multiplier` 与分数自相矛盾**：T1 行写 1.25 而分数没乘。改为记录**实际施加**的值。
4. **run 的权重档案不足以复算它自己**：`as_dict()` 不含 `uses_tier_multiplier`，而它现在是个开关。新增 `as_record()` 并用于 `curation_runs.weights_json`。
5. **标签底线放宽真正承重的那一半零覆盖**：突变 `schema_v2` 的 `min_length` 回 2，全量 2684 条测试**无一变红**——而它同时是读路径校验器。补两条测试（单标签过、空列表仍拒），突变已变红。
6. **`weights_from_mapping` 静默丢弃两个字段**：突变把 `significance` 硬编码为 0.0 恒绿；且 `uses_tier_multiplier` 根本无法从文件设置（唯一入口是 Python 里构造 `Weights`）。已支持从文件读，并补测试，突变已变红。
7. **四处文档失准**：`docs/architecture.md` 的公式、`web/templates/about.html` 的「五维」与一组早就对不上的权重数字、`app.js` 与 SSR 模板的「LLM 5 维」tooltip 及那条援引 tier 乘数的注释。全部订正；改 `app.js` 已按项目 CLAUDE.md 的 BINDING 契约跑 `bump_frontend_assets.py`（14 个 HTML + `asset-pins.json`），`test_frontend_asset_versions.py` 40 passed。

**已修的两条属于我交付里的事实错误**

8. **CHANGELOG 三处**：「五个维度大致均摊」（旧默认是 0.10/0.40/0.30/0.10/0.10，density+recency 已占 70%）；把 81 篇全数归给「至少两个」这条底线（精确分桶：底线 78、零幸存者 3、推荐理由过短 36，合计 117，放宽只救回 78）；以及「X 不再被自动抬高」没说清 X 属于 T1.5、功劳来自取消乘数而非 X 被压。
9. **位移读数用错了对照。** 我给用户看的「top-20 保留 15/20、rank ρ=0.928」量的是**只换提示词**；用户两项都批之后，**提示词+权重合起来**在 FULL3 上是 rank ρ=0.8544、top-20 保留 0/20，真实 `_fill` 按日回放则是整表重合 259/369、top-10 只重合 53/110。两个数各自都对，但我把前者当成了这次改动的位移写进代码注释。docstring 已改为并列两个对照并写明哪个是实际发生的；已向用户明确更正。

**记账不修**

- **`DEFAULT_THRESHOLD = 6.5` 与 `DEFAULT_FRESHNESS_FLOOR = 4.0` 是按一把不存在的尺子定的**：分数上限由 12.5（含 1.25 乘数）变成 10.0。实测过阈率 23.9% → 27.7%，候选不会枯竭，但这两个常数没人重新推导过。
- **weights.py 里 recency 归零的理由陈述不完整**：`curate()` 的第二段填充池 `filtered` 没有任何时间下界（48 小时窗口只作用于 fresh 段）。实测影响温和——过阈池里「距最新条目超 48 小时」的占比 72% → 70%。
- **配额闸的松紧**（reviewer 按日回放）：配额顶替进精选的比例由改动前的 51.0% 降到 38.5%。也就是说「配额是实际的排序决定者」在改动前就成立（`基线独立`），本次往好的方向挪了一点。
- **存量行不回填 `significance`**：影响已量化并写进 ADR-20260906-7c31 的后果节（展示分均值偏 −0.130、sd 0.805、16.2% 移动超 1 分、Spearman 0.9286，无系统性偏袒但头部略吃亏）。回填是消除它的办法，本轮未做。
- **`ScoringProvider.score_5d` 现在返回六维**，名字过时。改名要动 Protocol 与六个 provider，与本轮无关。
- **生产库未核实**：reviewer 未打开 `data/radar.db`（5GB + 950MB WAL，只读打开有触发 checkpoint 写入的风险），所以「生产存量里有多少行缺 significance」「生产 provider 实际是哪个」只有代码路径推断，**没有实测**。

**方法学（值得记住的一条）**：reviewer 第一轮把两个突变判为「存活」，是**假阴性**——`0.50`→`0.40`、`min_length=1`→`2` 都是**等长替换**，而 Python 的 `.pyc` 按 mtime 秒 + 文件大小判失效，同秒内等长改写会命中陈旧字节码。它的失败形态是「突变存活」，与「测试真的没覆盖」**完全同形**。本轮复验时已加 `rm -rf __pycache__` 与 `-p no:cacheprovider`。

### ISSUE-FIT-27 · 打分器改了提示词却没动 ruleset 版本，存量因此既分不出也重打不了（2026-09-06）

**两个后果，都不是回填本身**：

1. **存量行分不出来。** `item_evaluations` 只记 `ruleset_version`，而 `current_version()` 返回的 `2026-05-13.r1` 由 `PINNED_RULESET_DATE` + `RULESET_REV` 两个常量拼成，与提示词内容无关。2026-09-06 加 `significance` 之后写下的行与之前写下的行**声称同一个版本**。这与 ISSUE-FIT-14 是同一形态（身份锚指向代理物），只是那一条在 eval 侧、这一条在生产库里。
2. **存量行重打不了。** `_candidate_rows` 的跳过条件是 `NOT EXISTS (... AND scored.ruleset_version = ?)`，版本没动 ⇒ 每一条已打分的条目都被跳过，**一次不改版本号的打分改动在存量上永远无法生效**。而 `force` 参数当时是从 `AI_RADAR_FAKE_OUT_OF_RANGE`（一个测试用的"伪造越界载荷"开关）推出来的，CLI 完全没有暴露。

**本轮已做**：`run_scoring` 增显式 `force` 参数、`score` 命令暴露 `--force`（保留原环境变量以免破坏既有调用），两条测试 + 突变验证。这是纯增量，不跑就没有任何影响。

**本轮未做，且是两件不同的事**：

- **回填本身**（`score --since <window> --force`）是**对生产库的写**，且是真实的模型调用量，**需要用户显式许可**。规模（只读实测 `data/radar.db`）：全部待回填 40,810 条（已有 38 条带 significance，来自本地 pipeline 用这份工作树跑出的新条目）；按发表时间分档为 7 天 4,722／30 天 15,855／90 天 31,159。按 workers 8、实测中位延迟 2.4s 估，分别约 20 分钟／1.3 小时／2.6 小时／3.4 小时。
- **给打分器一个自己的 ruleset 修订号**（修后果 1）**没做，因为它有自动副作用**：`current_version()` 被 prefilter、scoring 与 v1 enrich 共用，`RULESET_REV` 一改三个 stage 全部视存量为未处理；即便只给 scoring 单开一个常量，下一次定时 pipeline 的 `--since 24h` 窗口内已打分的条目也会被自动重打。那是一次未经许可的状态变更与花费，所以交用户裁决，不自行执行。

**未核实**：生产实际用的是哪个 provider、有多少行来自启发式降级路径——reviewer 与我都没有打开 `data/radar.db` 做这项统计（本条的行数是简单 COUNT，已只读取得）。
