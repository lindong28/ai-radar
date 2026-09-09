# LLM Pipeline 经验

> Append-only. LLM 调用、模型选型、prompt 调优、eval 管线相关的坑点和 pattern.

## 2026-09-09 阶段耗时的稳态分布定不出超时阈值——补账轮不在那个分布里

- **形态**：为「要不要给 pipeline 阶段加超时」取了 n=107 轮的耗时分布（09-06→09-09），据它算出「fetch 之外五个阶段给 30 分钟上限，是 p95 的 20 倍、卡死实例的 0.19 倍，两侧都不含糊」。**同一天那个阈值就会误杀一轮合法运行**：一次 9.9 小时出网中断修复后，11:00 轮的 prefilter 跑了 **28.9 分钟**（p95 4.2m、max 9.8m），因为 2 小时内涌入 4638 条待处理、该轮 `prefilter processed=1160`、还剩 929。
- **根因不是阈值取小了，是分布取样错了**：那 107 轮全部是稳态轮次，**一个补账轮都没有**。而补账轮恰恰是唯一会长时间占锁、因而最像"卡死"的那一类——即要防的对象与被排除的样本是同一批。放大倍数（20×）读起来很安全，正因为它是相对一个不含该情形的基线算的。
- **可迁移的判据**：任何按历史分位数定的超时 / 告警阈值，先问「这份历史里有没有那个我要区分出来的异常态？」没有的话，分位数给的是"正常有多正常"，不是"异常有多异常"，两者之间的距离在数据里根本不存在。中断之后的第一批轮次是这类阈值最该采样、也最容易漏采的窗口。
- **附带读数**：补账期间 4 轮连续 `pipeline SKIP`（11:15/11:30/11:45/12:00）是锁被正常占用，不是僵尸锁——A2 的 SKIP 计数在这种窗口同样是噪声（同 `docs/issues/alerting.md` 记的那条）。

## 2026-06-01 fetched_at-only backfill 会放大定时 pipeline 的候选量

- Problem: 为修复新源历史导入文章永不处理的问题，prefilter/score 的 `--since` 窗口改为 `fetched_at`-only。这个语义正确，但若定时 `pipeline.sh` 同时跑 `prefilter --since 24h`，会把"近期抓取的大批历史 backfill"一次性纳入 LLM 队列。实施时本地 pipeline 自动启动后持有 `data/radar.db` 写锁，并进入 broad prefilter/score/enrich 阶段，阻塞了测试和服务启动。
- Solution: 对已知小批 backfill 用 `--item-id-file` 精确处理；运行全量 verify 前确认没有 `pipeline.sh` / `airadar.cli prefilter|score|enrich` 正在持有 DB。若本地 scheduler 已启动 broad run，需要先判断候选量，必要时终止本地 pipeline（整棵进程树退出后内核自动释放 `.pipeline.flock`，无 stale 锁需要清理，见 ADR-052），再用精确 id backfill 继续。
- Applies when: 修改 LLM 候选窗口、接入新源历史存量、或在生产同步 DB 上跑 verify。先做候选量 SQL probe；不要直接放开 broad `--since`，除非确认待处理数量和成本可接受。

## 2026-05-12 DeepSeek V4 Pro 不适合做 pairwise judge eval

- Problem: 使用 deepseek-v4-pro 做 pairwise judge（逐对比较两篇推荐的质量）时，完整 judge prompt 在 30-90 秒探测窗口内反复超时或返回截断的非法 JSON。模型 health probe 本身是通的，问题出在 judge prompt 的复杂度和输出长度上。
- Solution: 降级到 deepseek-v4-flash 做 judge fallback，eval 循环恢复正常。V4 Pro 仍用于 enrich（翻译/摘要/推荐语/标签生成）——这些任务的 prompt 更短、输出更结构化，V4 Pro 表现正常。
- Applies when: 选择 eval judge 模型时——如果 judge prompt 需要输入两篇完整文章并输出结构化评分，优先用快速模型（flash 系列），不要用 pro 系列。Pro 系列适合单条 enrich 而非多条对比。

## 2026-05-12 推荐语 prompt 需要明确字符数 gate

- Problem: 默认 enrich prompt 生成的 `why_recommend` 偏长且说教风，与对标产品 AI Hot 的简短编辑风推荐语体感差距大。AI Hot 平均约 72 个中文字符，AI Radar 初版显著超出。
- Solution: 在 enrich prompt/schema 中加入 35-90 中文字符的硬约束 gate，要求输出一句话的 AI Hot 风格推荐语。调整后 AI Radar 推荐语 min/avg/max = 42/58/81，与 AI Hot 的 avg 72.5 比值 0.80，体感接近。
- Applies when: 修改 enrich prompt 或 `why_recommend` 字段定义时——字符数 gate 是保持推荐语简洁的关键约束，去掉或放宽会导致风格回退。

## 2026-05-12 Enrich 必须逐条 commit 而非批末尾一次性 commit

- Problem: 初版 enrich runner 是顺序处理所有 item，只在整个批次结束后统一 commit 到数据库。当批量 enrich 30 条时，单条 LLM 调用平均 34 秒，总耗时约 17 分钟。如果中途失败或进程中断，所有已完成的 enrich 结果丢失，需要从头重跑。另外 stdout 只在结束后才可见，长时间没有输出无法判断进度。
- Solution: 改为逐条 commit（每条 enrich 完成后立即写入数据库），并在每条完成后 flush 一行进度日志到 stdout。后续还加了 `--workers` 参数支持并行。重跑时已入库的条目自动跳过。
- Applies when: 修改 enrich runner 或任何长时间 LLM 批处理逻辑时——逐条 commit + 逐条日志是基本要求。同时建议用独立的 `ps` / SQLite 进度探针监控长批次，不要只依赖 stdout。

## 2026-05-12 DeepSeek V4 Pro enrich 吞吐量基线

- Problem: 需要评估 enrich 全量重跑的时间成本。
- Solution: 实测数据（56 条 item，DeepSeek V4 Pro，顺序执行）：总耗时 8.58 分钟，平均 34.1 秒/条，最大单条 129 秒，吞吐约 6.5 条/分钟。30 条精选的 enrich 约需 17 分钟。
- Applies when: 估算 enrich 耗时或决定是否需要并行化时的参考基线。如果模型或 prompt 变更，应重新测量。

## 2026-05-12 评分 raw score 聚簇需要 rank-linear calibration

- Problem: 多 provider 评分后 raw score 高度聚簇（span 不足 20，stdev 不足 8），前 10 篇分数几乎相同，无法有效区分排序优先级。评分调优（调权重、tier 倍数、threshold）仍不能打破聚簇。
- Solution: 在 curate 写入阶段加入 rank-linear calibration——按 raw score 排序后线性映射到目标区间（62-92），保证 span >= 20、stdev >= 8、top 10 分数全部 unique。Calibration 在最终写入时执行，不改变 raw score 本身。
- Applies when: 修改评分逻辑或 curate 管线时——raw score 天然聚簇是多 provider 评分的特性，不要试图通过调权重解决。如果移除 calibration 步骤，排序区分度会回退。

## 2026-05-12 交付物 HTML 必须针对决策目的自审，而非仅验证技术正确性

- Problem: V6 对比决策包 HTML 交付给用户两次被退回。第一次：matched pairs 依赖不安全的 text+source 模糊匹配，导致"同篇文章对比"实际不是同篇。第二次：matched 样本量不足 10，统计意义不够。两次退回时 HTML 的技术指标（schema valid、ballot submit、浏览器渲染）都是通过的。
- Solution: 增加 `v6-html-audit.md` 自审 gate，在交付前检查：(1) matched pair 数量是否足够；(2) 每对是否有 URL 级同篇证据；(3) 是否残留 text+source 模糊匹配；(4) 推荐语长度/风格是否达标；(5) 标签覆盖是否达标。技术正确性（schema、渲染）是必要条件，不是充分条件。
- Applies when: 任何需要用户基于 HTML/报告做决策的交付——先问"这个 artifact 是否能支撑用户做出有信心的决策"，再验证技术正确性。

## 2026-06-01 DeepSeek/ARK 余额不足会伪装成 404 model-not-found

- Problem: DeepSeek/ARK 账户余额不足时，API 不返回明确的欠费/余额错误，而是返回误导性的 `404 InvalidEndpointOrModel.NotFound: The model or endpoint <name> does not exist or you do not have access to it`。ai-radar 现象：prefilter（`deepseek-v4-flash`）和 score/enrich（`deepseek-v4-pro`）同时全挂、都报这个 404 时，第一直觉容易误判成"模型被下线/改名"，去翻 provider 代码找 model_id；真实原因是上游欠费。本次约 24h 处理层空转。
- Solution: 看到多模型同时报 model-not-found 404 时，第一嫌疑放在**上游余额**而非代码改动。判别要点：(a) model_id 写死在 `src/airadar/provider/deepseek_v4_flash.py` / `deepseek_v4_pro.py`，`git blame` 这些行若久未动 → 排除代码改动、指向服务端；(b) 充值后自动恢复无需改代码——正在跑的 enrich 进程错误计数冻结、接连返回 OK 即恢复指纹（本次约 14:59 PDT 充值生效后自愈）；(c) 真实错因落点——prefilter 异常进 `logs/pipeline-*.log` traceback，score/enrich 的 per-item 异常落 DB `item_evaluations.error` 列。
- Applies when: pipeline 处理层（prefilter/score/enrich）批量报 model-not-found / 无访问权限的 404 时——先查 DeepSeek/ARK 控制台余额，再怀疑模型变更。多模型同时全挂尤其指向账户级问题（余额、key 失效）而非单模型下线。实际发生于 2026-06-01。

## 存量重打分：三个把「3 小时」变成「跑不完」的东西（2026-09-06）

给打分器加了一个维度之后要回填四万条存量，实测撞到三件事，每件都不在估算里。

**一、生产打分器是串行的。** `run_scoring` 是 `for row in rows:`，没有 ThreadPoolExecutor——而评测侧的 `eval/aihot_fit/run.py` 用的是 workers 8。照评测侧的速率估算存量回填会**低估 8 倍**：实测串行 20 条用 45 秒 = 2.25 秒/条，40,810 条是 **25.5 小时**，不是 3.2 小时。已加 `--workers`（默认仍是 1，定时任务不受影响）。

**二、整个循环只在末尾 commit 一次。** 一次被 120 秒超时打断的运行**落库 0 行**——已经付过的模型调用全部作废。已改为每 `--commit-every`（默认 200）条提交一次。

写这条测试时踩到一个更值得记的坑：**用写入的同一个连接读回行数，测不出提交与否**——sqlite 让一个连接看见它自己未提交的写入，于是"提交了"和"没提交"取值相同。第一版测试正是这么写的，把 commit 移出循环后它照样绿。必须用**第二个连接**读。

**三、`migrate()` 的锁不受 `busy_timeout` 约束。** 每次 `./run.sh score` 都先跑 `db.migrate()`，它先 SELECT 判断迁移是否已应用（开一个读快照），再执行 DDL 升级为写。若期间别的连接提交过，SQLite 返回 `SQLITE_BUSY_SNAPSHOT` 并**立即失败**——等待再久也没用。表现是设了 120 秒超时却在 6 秒报 `database is locked`。

**订正**：上面那句归因是错的，实测推翻了它。`migrate()` 的失败不是 `SQLITE_BUSY_SNAPSHOT`——它稳定在 **5.4 秒**（正好是默认 5000ms 到期），而不是立即失败。插桩后看到真因：**`001_init.sql` 第 3 行写着 `PRAGMA busy_timeout=5000;`**。那是一个**连接级运行时设置被写进了 schema 迁移**，而该迁移每次 CLI 调用都重跑，于是 `get_conn` 设的值只活到第一条迁移语句执行为止。

这个 bug 的形态值得记：**pragma 在迁移前读回来是对的，迁移后才变**，所以任何人会想到写的检查（在新连接上读一次）都测不出它。新增的 `AI_RADAR_SQLITE_BUSY_TIMEOUT_MS` 在修掉这行之前是**一个假的修复**——值改了，实际等待时长没变。

已处置：从迁移里删掉那行（连接设置不归 schema 管），并在 `migrate()` 跑完迁移后重新断言一次，防止将来哪条迁移再这么干。端到端验证：在 `airadar.cli fetch` 仍持锁的情况下 `migrate()` 等 5.2 秒后**成功**，而修复前是稳定失败。

长时间批处理仍然要分块 + 重试（单块失败隔一会儿重跑），但原因是与定时任务的竞争本身，不是超时设不上。

**回填不要带 `--force`。** 升版之后不需要它，而带上它会让"已打过分就跳过"失效，于是分块循环的终止条件（某一块 processed=0）永远不成立。

## enrich 的版本戳一旦不随提示词走，改动就永远到不了存量（2026-09-08）

`runner_v2` 的取数 SQL 有一条 `NOT EXISTS (… enriched.ruleset_version = ?)`：一条目在当前版本戳下 enrich 成功过，就再也不会被选中。所以**版本戳不动 = 提示词改动只作用于新条目**，存量保持旧判据，而这件事不产生任何错误、日志或告警。

实测代价：戳自 2026-05-13 未动，提示词在 09-02 → 09-07 之间改了四次；09-08 按 `input_json.system` 的 sha 分层，候选池里 **83.2% 的分类出自 5 月那版、只有 1.8% 出自当时生产在跑的那版**。站上用户看到的分类与「当前实现会给出的分类」是两回事。

两条可复用的教训：

- **库里的评估行是历次实现的混合沉积，不能当「当前实现的表现」读。** 我据库里 1149 条配对得出「一致率 0.509 / 构成总变差 0.378」并发表了归因，同一天被自己的对照推翻：用当前提示词新鲜跑同一批 300 条是 **0.718 / 0.144**。一行只证明「某时刻某实现这么判过」。`item_evaluations` 存了 `input_json`，分层只要一条 SQL——发表任何「当前实现有多好」之前先分层。
- **戳要从被它标记的东西算出来，别手工维护。** 现在 `current_version_v2()` 返回 `<date>.r2.<digest>`，digest 覆盖提示词模块文件、`SYSTEM_PROMPT`、以及用固定探针渲染出的 `USER_TEMPLATE` 全文（因而覆盖标签词表与类别清单的插值结果）。手工日期戳还有一个走不通的场景：**同一天第二次改提示词**——而这个项目一天内落三代提示词是常态。

### 戳一动，新条目会被存量重算挤掉

版本戳移动会让窗口内**全部**已 enrich 条目重新成为候选（实测 24h 窗口从 21 条涨到近 3000），而每轮 `--limit 40`。此前的 `ORDER BY i.fetched_at DESC` 分不开新旧：`fetched_at` 是按信源整批赋值的，且每次 feed 重新列出旧条目都会刷新它，实测 top-200 只有 **2 个不同取值**——排序实际由「这个信源在 fetch 阶段排第几」决定。于是新到的文章排在几千条重算之后（实测第一条排 303 位、末条 2440 位，按实际轮频约两天），会在轮到之前滑出 `--since` 窗口，**此后没有任何路径再碰它们**（crontab 里没有 enrich backfill）。未 enrich 的条目在站上显示原始外文标题、无摘要，且被所有分类筛选排除。

修法是在排序最前面放「从未 enrich 过的优先」。回归闸在 `tests/test_enrich_runner.py::test_candidate_rows_serve_new_items_before_recomputing_old_ones`（夹具刻意让陈旧项的 `fetched_at` 更新）。

### 手工回填不走 pipeline 的锁

`pipeline.sh` 用 flock 互斥，而 `./run.sh enrich` **不取那把锁**。手工回填与 cron 轮次并发时，后者的 `db.migrate` 会撞上 `sqlite3.OperationalError: database is locked` 并让 fetch 阶段整段失败（实测 2026-09-08T20:15 轮 `fetch FAIL (exit 1)`，`attempted` 汇总行都没写出来）。回填要么等 pipeline 让出锁，要么自己按同一把锁排队。

### 「每轮重列整个存档」的源会污染任何按 fetched_at 排序的批处理

`openai_blog` 有 1204 条条目、最早到 2015-12-11，而它的 feed 每轮把整个存档重新列一遍——2026-09-08 实测近 24 小时内它有 **1173 条**被重新 fetch，`fetched_at` 因此整批刷新。

后果有两层：

- **`fetched_at` 对这类源不是新鲜度的代理。** 任何按它排序、取窗口或判"新不新"的逻辑，在这类源上都读到的是"上次抓取时刻"而不是"这条内容有多新"。
- **它会占满任何按 `fetched_at` 排序的批处理队列。** enrich 重算就是这样：1527 条重算样本里 991 条（67%）来自这一个源，而它在候选池里真实只占 5.3%。据这个样本测出的池子构成整个作废。

判据是**该源被重新列出的条数与它的总条数同量级**。诊断一条：

    select source_id, count(*) from items where fetched_at > datetime('now','-24 hours') group by 1 order by 2 desc

把它和该源的 `count(*)` 比一眼就看得出来。要按源做无偏抽样时，别用 `fetched_at`，用 `published_at` 或直接按源分层。
