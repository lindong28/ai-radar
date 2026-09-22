# aihot-category-navigation

O3 的六类网站分类：模型、产品、行业、论文、教程、观点。AIHOT 网页 category 过滤以新闻类型分组；旧 `/api/v1/items` 把教程和观点合并为 tip，故其金标不能复用为同一消费者契约。本 benchmark 从实际分类页面成员建单类题，不从 tags、旧 category 或模型猜 gold；多类观测冲突排除、未观测不作为负例。它与 `aihot-enrichment-fields` 分开，后者保留其它富化字段与旧 API 口径。

## 题集与复用

只使用已通过原始输入建题规则的原标题、正文。参考是六个网页过滤入口实际返回的同 AIHOT item_id；保留页面原件、哈希、请求 URL、观察时间及原输入来源。按原新闻 case_id 去重，保留原 dev/regression split。新增材料用 `--base` 合并、按实质正文检查冲突，不覆盖旧 vN。大资产在本机 video-eval-arena，版本说明在 `docs/evaluations/content-enrichment/aihot-category-navigation/vN/README.md`。

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/capture.py \
  --output /path/to/new-frozen-category-responses --pages 3
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/build.py \
  --inputs ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v2 \
  --capture /path/to/frozen-category-responses --version v1
# 以后扩展：增加 --base .../aihot-category-navigation/v1，给新 inputs/capture，输出未占用的 v2。
```

capture 是只读公网 GET，六个类别并发、每类游标顺序翻页；默认每类最多 3 页，不代表抓到网站全部历史。`capture-summary.json.end_reached=false` 明示受页数限制。按需要增加 `--pages`（上限 50），每次使用新目录；未实际观察到的历史分类仍未知。既有 all-page/API 五类采集不等于此六类标签采集，本命令不擅自变更常驻调度。

捕获目录的 `*.response.json` 记录 url/status/finished_at/sha256，与同名 `*.body` 绑定；仅消费 `https://aihot.news/all?category=...` 和 `/api/public/feed?category=...` 的成功响应。HTML 读取真实新闻 RSC 对象，不把导航链接当成员。题库内 evidence 是这些观测与输入 cases/manifest 的冻结副本；原始输入的完整档案仍由 source_datasets 定位，不因此删除旧档案。

## 真实模型评测

<a id="当前研究配置c5"></a>

### 当前整体主方案：C5；J阶段已测试材料结构路由

J阶段C5/J1/J2同200题165/162/161对。冻结J2全361为282对（78.12%），同次首轮281对，184复核修13/退12、覆盖47/80首轮错误；送达改善而复核净收益不足。保留J2路由＋I2局部复核原则作为研究起点，不采用完整J2、不切生产，六类双90未达。复现见下方J阶段参数；完整[状态及逐类指标](../../../docs/evaluations/content-enrichment/status.md#2026-09-22j阶段材料结构路由)。本阶段全库只有J2，C5同期仅200；以下I/H是历史，不冒充J阶段全库控制。

最新I阶段实现可选blind review与仅用于复核的局部guidance。C5同期200题166对，I1/I2开发161/159对；冻结I2全361同次首轮284→最终286（79.22%），26次复核修6/退4，但77个首轮错误只覆盖12个，已见回归76题净退2。整体不替换C5，保留I2局部指导作研究组件；双90未达。复现用下文I阶段命令，不将其当生产默认；实际prompt/模型身份以各run为准，完整数据见[状态](../../../docs/evaluations/content-enrichment/status.md#2026-09-22i阶段条件盲复核)。下面H阶段全361 C5是最近历史全量，不是本轮新全量控制。

最新H阶段已实现`category-h1.txt`至`category-h3.txt`：C5骨架迁入局部边界→reason先识别作者动作→修正为承载证据、取消原创作者门槛。开发同200为156/160/156对，本轮C5为163对。冻结H3全361为274对（75.90%），C5为286对（79.22%），13修正/25退化；H3局部收益保留但不整体采用。当前实际整体主方案仍为下方C5命令，不存在一个已验证更好的“C5＋所有修复”默认版本。生产仍A0；详情见[状态](../../../docs/evaluations/content-enrichment/status.md#2026-09-22h阶段实际组合局部修复)。以下G阶段数字保留历史语义，不是最新快照。

C5完整身份：C1 rubric＋冻结一跳引用＋引用贡献主次＋冻结正文全文，每题一次Flash；不开source-context或conditional-review。全361历史290/288/289/294对，最新H阶段286对；原方案未变，重复差值不是优化收益。G阶段G3为282对、对同期C5修16/退28；F阶段F3为288对，局部机制保留但未证明整体更好。完整结果与选择边界见[状态](../../../docs/evaluations/content-enrichment/status.md)。以后扩大题库时替换dataset与其manifest绑定的quote-source，沿用同一入口，不向旧版本覆盖写题。

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/evaluate.py \
  --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-category-navigation/v1 \
  --config evals/content-enrichment/configs/category-flash.json \
  --env-file /path/to/project/.env \
  --rubric evals/content-enrichment/prompts/category-c1.txt \
  --quote-source ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v2 \
  --quote-contribution --body-limit 0 \
  --split dev --limit 200 --seed category-development --label category-c5-development --workers 8
```

冻结后去掉`--limit`可覆盖dev285；另用`--split regression`覆盖76题，明确二者已有曝光。同期B1对照改为`--rubric .../category-a4.txt --body-limit 5000`，其它参数相同。隔离worktree运行可传`--output-root /path/to/main-checkout`归档，代码仍来自当前cwd。不会自动启用新数据抓取或改变线上分类。

### D阶段候选复现

本节亦包含E/F/G/H阶段修复分支；各自状态不同，不能将文件存在理解为已采用。

H阶段复现：沿上方C5命令，将rubric换为`evals/content-enrichment/prompts/category-h1.txt`、`category-h2.txt`或`category-h3.txt`，使用新label；其它参数不变。去掉limit、分别dev及regression可重跑285＋76题。H3冻结run为`13-23-08`/`13-24-39`，C5为`13-25-05`/`13-26-27`。`runs/content-enrichment/aihot-category-navigation/v1/2026-09-22/13-15-58/support/summarize-h.py --output /path/to/new-summary.json`在`PYTHONPATH=src:. uv run python`下复算七轮，按case_id连接并检查canonical分数、原题和实际prompt；输出路径必须未占用。失败仍留分母，已见回归不称独立验证，不覆盖既有原件。全361 H3/C5 tokens分别565,600/466,193，局部提高不抵消整体退化与成本。

G阶段复现：沿上方C5命令换为`evals/content-enrichment/prompts/category-g1.txt`、`category-g2.txt`或`category-g3.txt`，给新label；其它参数不变，当前配置走gateway。G1仅增强reason证据一致，G2重构读者贡献定义，G3回F3骨架保留技术讲解/短研究/产品附安装边界。同200为155/151/158对，新链路F3控制155对。G3冻结dev285/reg76为226/56对（`12-36-26`/`12-38-07`），C5同期235/59对（`12-38-47`/`12-40-32`）；G3全库tokens567,044、C5为466,026。去掉limit并切split可重跑全库，旧原件不能覆盖、已有曝光不能改称独立验证。首轮`12-26-44/support/summarize-g.py`复用canonical scorer生成配对与分歧，既有最终summary只读，新复算应另存新输出路径。

F阶段复现：沿上方C5命令，将rubric换为`evals/content-enrichment/prompts/category-f3.txt`并给新label，即保留的研究分支；换`category-f1.txt`或`category-f2.txt`可复现局部修复历史，其它参数不变。F1→F2→F3同200为151→160→163；冻结F3去掉limit，分别dev285及regression76为229/285、59/76（UTC `05-51-35`/`05-53-15`），同期C5为232/285、57/76（`05-56-53`/`05-58-36`）。F3含一次content_filter仍留分母，不称完整成功推理。原件不覆盖；已曝光回归不是独立集，看到冻结结果后不得把改动版仍称冻结F3。全361调用tokens为F3 532,795、C5 466,102，成本和局部退化也参与下一轮选择。

E阶段复现：沿上方C5命令，将rubric换为`evals/content-enrichment/prompts/category-e1.txt`、`category-e2.txt`或`category-e3.txt`并给新label；其它参数不变。E1源于D2，E2源于E1，E3保留E1收益并加入范围核对；三者200题158/158/157对，不是推荐替换版。D2冻结复现改用`category-d2.txt`，去掉limit分别跑dev285与regression76；本轮两run为`05-24-16`/`05-25-41`。旧失败、原prompt/reason与比较记录保留，不在复现时覆盖。后续局部修复既要检查保住的收益，也要检验全体类别退化，不按每轮总分是否第一决定删除方向。

2026-09-22四个开发干预均未取代C5，最新C5全361题288对（79.78%）；上次290对（80.33%）保留为历史，不覆盖成新分数。使用上述C5命令，只替换`--rubric`和`--label`即可复现D1/D2/D3：rubric取项目相对路径`evals/content-enrichment/prompts/category-d<N>.txt`（N为1、2、3）。D4使用`evals/content-enrichment/prompts/category-d4.txt`并追加`--source-context`；其它参数不变。它们各自以C5为父，不串联叠加，不进入生产默认。

四轮同200题；C5同期对照复用本阶段dev285的固定200子集，余85与regression76分开报告。全361题已曝光，重复不是独立验证。输入、prompt、reason、尝试和失败仍由原runner归档，不改变题库版本；D2/D4的content_filter失败留在分母，不静默换模型或重试。逐类成绩、修正/退化及当前接续见[状态](../../../docs/evaluations/content-enrichment/status.md#2026-09-22d阶段完成仍以c5为研究起点)。

### 默认基线与smoke

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/evaluate.py \
  --dataset ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-category-navigation/v1 \
  --config evals/content-enrichment/configs/category-flash.json \
  --env-file /path/to/project/.env --split dev --limit 8 --smoke --label category-smoke --workers 8
```

smoke 验证链路，不代表质量。随后固定 seed/开发题比较 rubric（`--rubric path.txt`）；冻结候选后使用 regression，不把开发或反复选型结果称盲测。默认模型输入只有 title 与前 5,000 字正文，参考、tags、AIHOT 摘要不进入 prompt；默认每题一个 Flash 调用，输出 reason 后 primary_category。共享实现位于 `src/airadar/enrich/category.py`，runner 为 `evals/_shared/category_eval.py`；独立调用成绩不代表完整 enrich 或生产已上线。

### A4+主线的结构消融（2026-09-22）

A4+指`--rubric evals/content-enrichment/prompts/category-a4.txt`加上下面的`--quote-source`，不是默认A0。以下开关可组合，默认都不启用；同题同seed一次只改变一个因素，另给label：

| 参数 | 被测变化 | 运行原件 |
|---|---|---|
| `--quote-contribution` | 仅有available引用时补当前帖/引用贡献主次说明；其余system不变 | 实际prompts；quote-context保留引用证据 |
| `--body-limit 0` | 已冻结正文全文，默认5000；0之外需正整数；不实时抓取、不摘要 | prompts含真实发送内容，原cases不变 |
| `--conditional-review` | 首轮用语义歧义flag，true才追加一次同模型复核 | first-pass-predictions.jsonl、first-pass-scores.json、review-prompts/<case_id>.json及每阶段attempts |
| `--blind-review` | 需同时开启conditional-review；二次只看原始输入/rubric，不看初判category/reason | review-prompts中保留实际二次输入；原文不删改 |
| `--review-guidance <UTF-8文件>` | 需同时开启conditional-review；指导文本只追加到二次system | 完整文本进入metadata.object_identity.behavior.review_guidance |
| `--routing-guidance <UTF-8文件>` | 需同时开启conditional-review；指导文本只追加到首轮system，不进入二次 | 完整文本进入metadata.object_identity.behavior.routing_guidance |

组合调用示例：在上面的命令追加`--rubric evals/content-enrichment/prompts/category-a4.txt --quote-source <冻结父题库路径> --quote-contribution --body-limit 0`。若测试复核，再加`--conditional-review`；是否有效须查状态记录，开关存在不表示推荐采用。

复核输出契约：首轮JSON先reason，再needs_review（严格boolean），最后primary_category；标记只依赖原始内容是否存在相邻类别/主体冲突或关键信息不足。最终标准output仍只有category，不改变benchmark消费者语义。predictions的first_pass保留同一次初判，needs_review保留路由决定，review_response_json保留实际第二响应；reason/output/status是最终workflow结果。二轮失败不静默fallback，仍计FN；first-pass-scores是同次首轮诊断对照，不是另一次独立实验，不能累加调用或题数。两阶段都要求reason-first。

body_limit（null表示全文）、quote_contribution和conditional_review进入metadata.object_identity.behavior，实际首轮prompt、代码、引用源继续绑定身份。review-prompts在发请求前写盘，生成逻辑与源首轮响应可追溯；归档失败、调用失败和输出校验失败均保留首轮对照及最终失败状态。终态身份漂移同时作废两套指标。实现与回归测试分别在`category_review.py`、`tests/test_category_review.py`和`tests/test_category_quotes.py`。

I阶段复现：在C5命令追加`--conditional-review --blind-review`为I1；再加`--review-guidance evals/content-enrichment/prompts/category-i2-review.txt`为I2。给每轮新label，不覆盖run。`blind_review`及指导全文进入behavior身份；未开启conditional-review时传两者会在调用前拒绝。默认无指导、仍保留既有带初判复核；这两个实验不是默认推荐或生产变更。解释结果须同时看路由覆盖的首轮错题、复核修正/退化和整体逐类P/R；两个arm首轮重跑时，不把差异全部归因于指导词。

### 可选：原始引用输入消融

J阶段复现：在I2命令追加`--routing-guidance evals/content-enrichment/prompts/category-j1-routing.txt`为J1，替换为`category-j2-routing.txt`为J2，给新label。J2用材料同时含具体事实与测量/讲解/评价的结构触发；二次仍用I2指导，不附首轮路由文本。默认空值保持既有prompt，未开conditional时拒绝。旧路由已要求竞争证据，J1只是细化未充分兑现的判断，两者都不是独立错误检测器。分别检查首轮变化、复核错误覆盖和同次首轮/最终修正退化；不能用跨轮总分单独证明路由因果。全库用`--split dev`不设limit及`--split regression`分开运行，均为已曝光题，不称独立验证。

默认仍只有原标题正文。要检验输入缺失，可在同一命令增加 `--quote-source ~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v2`，并保持相同 dataset、split、seed、limit、config、rubric，另给 label。来源必须是题库 manifest.source_datasets 哈希绑定的直接父数据集；raw-inputs.jsonl 哈希也要匹配其 manifest，不能任意换成最新档案。

只沿原始 X 元数据的 quoted 关系查一跳；每题按 provenance.observed_at 排除未来观测，实质版本不一致、无正文或查不到则不追加、不猜内容、不剔题。引用的 author/url/title/content_text（正文最多4,000字符）追加为不可信原始材料，不取 AIHOT 生成摘要/标签/参考答案。每题仍一次调用，不做实时抓取，也未接入生产。原题和gold保持不变，这是分类对象的可选检索输入配置，不是改写 benchmark/v1。

新增运行原件 `quote-context.jsonl`：每行 case_id＋quotes，包含 post_id、status；available时还有 raw_sha256、observed_at、raw_run、input。完整引用原件保存在该sidecar，实际发送截断文本以prompts.jsonl为准。metadata.object_identity.behavior.quote_context 锁父manifest、raw和解析结果摘要；object_identity.inputs.quote_sources 在运行前与结束时重读源哈希，漂移则指标作废。关闭时不生成sidecar。未来扩题若更换父数据集，按新版本的source_datasets选择来源，不绕过哈希绑定。

比较时分别报告“真正追加引用的题”和“未变输入的题”的修正/退化；后者单次变化不能归因到引用。多个转帖可能引用同一事件，题数不能当独立事件数。A6/A7是无引用边界实验，A8是A4＋引用主次规则；候选结果与处置见[最新状态](../../../docs/evaluations/content-enrichment/status.md)。

指标全部是确定性计算，无需 LLM 判官，定义见 `metrics.json`，实现见 `evals/_shared/category_metrics.py`。整体 `category_accuracy`＝正确分类题数／全部选中题数；每类新增 `category_<name>_precision`＝TP/(TP+FP)、`category_<name>_recall`＝TP/(TP+FN)。`name` 为 model/product/industry/paper/tutorial/opinion；这是精确率与召回率，不是计入大量 TN 的 one-vs-rest accuracy。全部取值 0–1、越高越好。2026-09-21 用户新增目标：六类各自 precision、recall 均≥0.90，即十二项同时满足；整体 accuracy 不代替此目标，零分母的未计算项也不能算通过。

某题误分时计入预测类 FP、真实类 FN；缺预测、调用/格式失败或未知类别只计真实类 FN，不分配预测类别，整体 accuracy 仍计错且该轮 incomplete。P/R 的零分母返回 `value: null, status: not_computed, reason: zero denominator`，不是 0 或 100%。`scores.json.category_counts` 按网页 slug 保存每类 tp/fp/fn，metric 的 denominator 是该指标分母，per_case 保留 reference/prediction/有效性。diagnostics 继续保存混淆矩阵及多数类基线。人评从稳定 `human-evals/content-enrichment/reviews.json` 按输入身份和字段优先覆盖，只作用新推理轮的计分视图，不改原始参考。

## 原始来源上下文消融

`--source-context` 默认关闭；开启时仅将冻结 `input` 中非空字符串 `url`、`author`、`source_kind`、`source_name` 追加到实际 user prompt，不访问网络，不包含 tier、tags、score、参考分类或其它富化字段。`author` 可能是 feed 提交者，`source_name` 是采集渠道，不等于核实过的原作者／官方机构。不开启时正文与此前逐字一致；可与 `--quote-source`、`--quote-contribution` 组合。新增 `metadata.object_identity.behavior.include_source_context` 记录开关，实际取值由 `prompts.jsonl` 及 `object_identity.inputs.prompts` 绑定；缺字段不造值，全部缺失则不追加段落。

该开关只服务离线输入诊断，既不建立来源到类别的查表，也不改变生产输入。C阶段候选 rubric、研究选择与真实结果见[状态](../../../docs/evaluations/content-enrichment/status.md)。未来使用新题库仍通过同一命令显式指定 dataset；不因路径存在就外推已验证生产收益。

## 用冻结预测补算指标（零模型调用）

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/rescore.py \
  --source-run "$PWD/runs/content-enrichment/aihot-category-navigation/v1/2026-09-21/10-15-13"
# 在隔离 worktree 补算主仓原件：另传 --source-root /path/to/ai-radar。
# --output-root 选择新运行落点；--json 将机器结果输出到 stdout、身份诊断到 stderr。
```

每次写入新的标准 runs/experiments 时间分区，不覆盖历史 scores 或预测；只接受原始推理轮，拒绝对补算轮再补算。核对原轮身份、cases 摘要、逐题预测与 items 原件以及旧 accuracy 一致，计分期间再次核对源文件和 scorer 哈希。输出根的 metrics.json 必须与执行代码所在 checkout 一致，否则在推理/补算前拒绝，防止归档遗漏指标。保留源轮冻结的人评/gold 视图，不静默引入后来人评；修改标签属于另一种重计分任务。退出 0 表示源预测完整，退出 1 表示源预测不完整但其补算已归档（CLI 明示该状态）；校验异常不产生正式成绩。

补算 `experiments/.../metadata.json` 字典：`run_kind: metric_recompute` 区别新推理；`source_run` 是已解析的原推理绝对路径（跨 checkout 不丢失源根，迁移时由资产路径解析处理）；`source_sha256` 锁 cases/predictions/scores/metadata；`object_identity` 原样继承源轮被测对象，`metric_identity` 则锁本次计分代码/规则/输入；`additional_model_calls: 0` 与 `cost.value: 0` 只指此次补算的增量，不改源轮历史费用。原题身份、split 与 benchmark/v1 不变，仅扩充指标输出。指标查询时按 source_run 关联，不把补算当新模型候选或新增题量。

逐题 prompt/response/reason、attempts、scores、diagnostics、conclusion 保存在 `runs/content-enrichment/aihot-category-navigation/vN/<UTC-date>/<UTC-time>/`；metadata 与指标在同分区 experiments，统一索引由 assets.rebuild_index 重建。`--output-root` 可指定主 checkout 归档而在隔离 worktree 执行代码。失败/无参考类别不从分母静默丢弃；不自动 fallback、重试、改 gold 或部署。
