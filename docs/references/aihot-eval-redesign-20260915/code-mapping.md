# AIHOT 新评测设计：真实代码映射（只读核查）

核查时间 2026-09-15。对象为本地 HEAD `699bad253f387e558c9098dbd26b338de219b3f2`，不是生产部署证明。`git status --short` 显示 `precompute.py` 已有修改及多份未跟踪文件；本任务未修改它们。以下结论来自本轮直接读取代码；代码注释中的历史实验数字不作为本轮实测读数。没有运行 LLM、生产接口或浏览器，故“页面行为”指静态调用链证据，未宣称实际页面验收。

## 最值得主报告保留的发现

1. `eval-fit` 的分数不是最终网站 AI 分。精选先选择，再按入选位置线性赋 92→62，存回 `curated_items.weighted_score=display/10`，浏览器乘 10 取整。未入最新 run 的 timeline 条目则用原始加权分乘 10。同一新闻的分数会因观察面与 run 身份不同而不同。
2. 首页并非最新一次精选集合：它是历史入选并集，经当前 source/dedup/category 状态过滤，取每条新闻最后一次入选记录的分数，再按发布时间分页。timeline 的“精选”标记只认最新 run。
3. 推荐理由也不是单一 enrich 字段：首页优先 enrich `why_recommend`，可回退 score reasoning/模板；timeline 最新入选条目会用 curation score reasoning 或模板覆盖 enrich 理由。旧 judge 只读 enrich 输出，量不到这个差异。
4. 首页显式 `showTags:false`，标签存在 API 并不代表首页读者看到了标签。普通新闻详情页实际重定向原文，没有本站生成的长详情正文；新设计必须先明确“详情”目标是新增产物还是原文跳转链路。

## 模块与消费者链

```text
items + sources
  → prefilter runner → item_evaluations(stage=prefilter)
  → scorer runner（有 prefilter 准入）→ numeric_json（六维）
  → enrich v1/v2 runner（有 prefilter 准入）→ 分类、标签、中文标题摘要、推荐理由
  → curator._load_candidates → weighted_score → dedup → freshness/threshold/source quota
  → ranking_key（类别系数）→ _fill → _calibrate_selected_scores（按入选位置赋分）
  → curation_runs + curated_items
      ├→ curated_archive._archive_items → item_summary → /api/v1/curated → app.js：首页
      └→ timeline（最新run标记 + 最新score/enrich）→ item_summary → app.js：/all
items + 最新enrich → /api/v1/items/{id} → detail页面 → 原文跳转

旧 eval-fit：questions → 三个 runner._evaluate_item（各题全跑，无生产stage gating）
  → outputs.jsonl → metrics / judge（没有执行 curate/archive/timeline/浏览器）
```

生产 runner 入口见 `src/airadar/cli.py:733,743,798,808,840`；scorer prefilter SQL 见 `src/airadar/scorer/runner.py:70`，enrich-v2 见 `src/airadar/enrich/runner_v2.py:97`。本次未完整核查调度 shell 的先后与生产实际 enrich 版本，不把 v2 候选路径等同于已部署状态。

## 字段映射和旧覆盖

| 用户目标 | 真实产出/转换 | 当前旧评测覆盖与漏项 |
|---|---|---|
| AI 评分 | `curator/score.py:33` 按存在维度重标权重；`select.py:354` 排序应用类别系数，`select.py:166` 入选数>1时按名次线性映射92→62，单条不映射；`app.js:372` 乘10四舍五入、80/65分档 | `eval/aihot_fit/run.py:226` 写 raw weighted/fit；`metrics.py:364` 优先fit计算Spearman。无最终显示分绝对误差，无排名校准、徽标、跨run变分覆盖。`aihot_scale.py` 独立拟合函数并非这里的rank_linear映射 |
| 分类 | `presentation/summary.py:109` → `classification_projection`；v2五类和is_opinion直用，v1从tag推断且可能ambiguous/unclassified（`enrich/classification.py:111,140`）；URL五类映射见`app.js:188`及后续字典 | `metrics.py:204` 直接比较 enrich primary_category，覆盖v2组件；未执行网站category SQL/filter及旧数据投影，不能推出网站分栏正确 |
| 标签 | v2 provider normalizer合并受控标签与源派生标签并截4（`normalizers/production_enrich_provider_output_v2.py:137,200`）；presenter再调topic_tags_v2（`summary.py:110`）；浏览器截4、去#、空时AI/社交（`app.js:360`） | `metrics.py:256` raw Jaccard及`292` presented tags两项；后者复用真实tag函数但仍非完整页面。首页`app.js:1849`隐藏tags，不能据API标签判首页展示 |
| 精选集合 | `select.py:609`：最新成功scoring/enrich、enabled非wechat、dedup，threshold=6.5；新鲜池48h、floor4.0、最新上海日期、先36席，再补到40，source quota；类别系数影响排序 | `metrics.py:402,446` AUC/raw或类别排序分；`509` 按UTC日用参照入选数量k排序raw分得P@k。未跑真实freshness/quota/dedup/40席，也不构造首页历史并集。P@k是oracle k代理，不能当实际集合命中 |
| 推荐理由 | `summary.py:148` enrich优先；`34,39,152` 中文score reasoning或带source名通用模板fallback；timeline`timeline.py:337`对最新入选项强制覆盖 | `judge.py:115`只取enrich.why_recommend，未见template或timeline override；summary/reason字面bigram也是enrich输出（`metrics.py:574`） |
| 每条新闻标题 | `summary.py:133`原始+中文；`app.js:744` title_zh→title→excerpt | judge具备title维（`judge.py:48`），但DEFAULT/CALIBRATED仅summary/reason（49–50）；不是已完成标题验收 |
| 每条新闻摘要 | `summary.py:139`→`app.js:183` summary_zh，否则content_preview（最多320字，`summary.py:49`），X还可能正文；`itemCard`显式渲染并有clamp/compact条件 | judge比较enrich.summary_zh，不看缺失fallback、截断、实际渲染及可见范围 |
| 每条新闻详情 | `items.py:13`返回item与evaluation列表；`app.js:2473`读取后`location.replace(itemHref(data.item))` | 没有本站生成详情正文可供旧eval比较，普通新闻详情页是跳转链路。外部原文内容不受本仓产出控制 |

## 首页与时间线的状态差异

`curated_archive.py:43` 对每item取 MAX(curated.run_id)，因此过去任何一次入选即可留在首页归档；`222`常态按published_at、fetched_at、id倒序，而非weighted_score。`91`依据当前enabled、kind、dedup、category过滤，指定日期用UTC+8。`258`同时读取历史最后入选分数和当前最新成功enrich；这是“旧选择分数+新文案/分类”的混合状态。

`timeline.py:37`要求最新prefilter为AI，且未评分或最新成功scoring.relevance≥6.5；`245`根据是否存在prefilter启用相关过滤。`294`读最新成功score/enrich，`320`只join最新run的curated记录。`337`命中最新run就覆盖score和reason；否则保留raw weighted_score，若缺则0。因此首页归档成员未必在/all显示“精选”，某item在两处可呈现不同评分/理由。

`precompute.py:66`也复用item_summary并写summary_json，但首页归档`_archive_items`本次读到的路径现场组装最新enrich，没有消费summary_json。评测不能直接拿预计算缓存代替该路径。

## 旧 judge / calibration 能说明什么

`judge.py:48–50`支持title/summary/reason，但默认与校准只含summary/reason。`115`候选取自enrich输出；`201`阳性为参考自身，阴性为另条新闻参考循环错配；`279`按两组均值阈值判断scale_ok。它能检查端点是否分开，不能仅凭这组自同一/错配控制证明邻近质量差异刻度可信，更不能证明最终页面理由已量过。此处是从构造推得的能力边界，没有新跑判官，也没有重新裁定旧门槛。

`run.py:359`明确记载stage_gating=none。所以它是共享provider/normalizer的组件批跑，而非复现生产吞吐、阶段准入、历史状态与网站呈现的整链路。

## 建议的离线接入点（供设计，不实施）

- 在独立SQLite工作副本里保留真实表结构、输入及阶段结果；以快照/事件边界推进实际runner与curate，再调用真实Web路由。不要在评测脚本重写freshness/quota/archive SQL与fallback规则。
- `web/app.py:590 create_app(db_path=...)`可指向隔离库，但创建过程会迁移，lifespan会预热；只可面向评测副本，不用生产/原始证据库。`request_db.py:18`路由实际连接app.state.db_path。之后通过实际HTTP接口/ASGI请求采出curated、timeline、detail数据，再以仓库app.js的真实页面识别最终字段与可见性；仅item_summary不够，路由覆盖仍会改它。
- 若暂时只评估静态显示文案与标签，可先复用`item_summary`及对应route组装；必须明确这是API投影读数，最终分数整数、隐藏tag、摘要裁剪、详情跳转仍欠浏览器消费者面。
- 冻结的应是观察时刻状态，而非仅published_at窗：旧入选集合、最后入选run、最新run、eval可见水位、source enabled/tier/kind、item正文/去重标记/时间、运行参数与代码，以及观察当天的时钟。`curate`直接datetime.now，离线历史重放需受控时钟能力，不可默认“现在跑一遍”等于当时。

## 哪些历史状态尚缺

`select.py:707`保存input_eval_ids/output IDs、权重和enrich watermark，但input_eval_ids取的是dedup后的candidates（`699`），不是完整加载池；它没有逐run保存source/item全部字段快照、页面最新enrich水位或所有freshness/quota参数。具体旧run是否具备可还原状态需要逐run资产盘点，本任务没有查询数据库因此不报缺失数量。

`scripts/eval/replay_real_runs.py:109`按保存eval IDs还原；`157–170`明确老run没enrich watermark不可还原，且源码依赖quota默认及传参；这是选择归因入口，可复用其时间/候选处理经验，但它返回选择结果，不会自动补足历史归档页面。当前sources/items变化、旧enrich被清理、未知阶段完成时刻及AIHOT同期页面快照缺失，都不能靠当前重算补造。

## 命令与边界记录

- `git status --short`、`git rev-parse HEAD`取得上述本地状态；同一shell后续一次误写`src/ai_radar/web*`导致整call exit 1，已改成真实`src/airadar/...`路径补读。
- 多次`rg -n`、`sed -n`、`cat`读取上述具体源码，成功输出exit 0；查不存在的pipeline.py及glob出现路径错误，已用`src/airadar/cli.py`真实callers补齐，未把零结果当功能不存在。
- 输出目录初次`ls -ld` exit 1（不存在），本文件由apply_patch新建。只写本报告；没有测试或eval运行，无“通过率”结论。
- 本轮自取：所有代码映射和HEAD/status。记忆仅用于导航，旧记忆里audit missing/partial计数没有重新采信；未转述为当前数字。生产部署、当前数据库覆盖率、AIHOT参照资产完整性、真实浏览器呈现，本报告均未核实，需caller在对应任务中整合其他证据。
