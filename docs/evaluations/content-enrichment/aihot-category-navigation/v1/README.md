# AIHOT 六类网站导航 · v1

> [Developer] · 冻结题集，2026-09-21。数据扩充另建 v2，不覆盖此版本。

数据位于 `~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-category-navigation/v1/`。真实 loader 已验证 manifest、题目及证据哈希：361 道单条分类题，dev 285、regression 76；模型 45、产品 60、行业 60、论文 46、教程 67、观点 83。

## 参考语义与建设

参考来自 AIHOT 六个分类过滤页面中实际出现的新闻，不使用旧 API 的 category：网站 JS 将六类对应到 model_release、product_launch、industry_event、research_paper、tutorial_explainer/tool_or_prompt、opinion_analysis；旧 API 则把教程与观点合为 tip。本批 83 条观点在旧 API 中全部是 tip。因此这是新 benchmark，不是把旧五类题库改名为 v2。网站内部分类器的完整 prompt 未取得，不能反复触发它重新分类；其自一致率未测。

以 `aihot-enrichment-fields/v2` 的合格原始输入为候选，按 AIHOT item_id 与规范化原文 URL 双重匹配页面成员；每题只有原始 title/body 作为模型输入，AIHOT 的生成标题、摘要、标签和参考分类不进入推理。14 份 HTML 捕获共 560 个不同成员，无跨类冲突，361 个有已冻结的合格原文。其它 3,505 条输入只是未在这些有限页面中观察到类别，不能作为负例，也不能据此判不值得收录。

按原 case_id 去重并继承原 split，不按 gold 重采样。实质输入冲突、跨分类观测冲突、同 item_id 不同规范化原文 URL 均排除；时间戳、HTML 和 URL 格式差异不作为实质内容变化。参考是本次网页观测时刻，不声称当初发布时也属该类；页面请求 URL、观察时间与原件哈希随题保存。

`cases.jsonl` 包含 input/reference/provenance；`observations.json` 是逐 item 的分类观测，`excluded.jsonl` 解释未入题原因，`manifest.json` 记录来源、数量和哈希。`evidence/` 保存匹配所依赖的分类响应及原输入题库 manifest/cases；完整原始输入档案仍由 source_datasets 及原 provenance 定位。后续人评优先于这些网站金标，原始参考不覆盖。

## 重建、扩充、运行

### P 阶段：材料呈现与证据理由复用

`scripts/eval/category_evidence_study.py` 从一个已核源单调用分类run重放其完整cohort；不重建题库、不改gold。运行前核原dataset/quote/body/human-review/model/transport身份，缺失或漂移即拒绝，不先调用后发现。当前源run为 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-27/13-53-09`（O3，48人评题）。

```bash
PYTHONPATH=src:. uv run python scripts/eval/category_evidence_study.py \
  --source-run runs/content-enrichment/aihot-category-navigation/v1/2026-09-27/13-53-09 \
  --arm documents --label p1-repeat --env-file .env
```

`control` 固定为legacy呈现并关闭evidence-reason，本阶段以O3为源时等于原配置重复；不承诺对任意P1/P2源保留其原呈现。`documents` 是P1，材料仅去逐字重复并保留来源角色；`evidence` 是P2，在documents基础上要求reason绑定新闻贡献与证据归属，类别条件仍不变。每次生成新标准分区，保留源run比较；`comparison.json` 默认相对传入源run，P2对直接父P1另用 `python -m evals._shared.category_compare --baseline-runs <P1> --candidate-runs <P2> --human-reviews <冻结票> --output <未占用比较文件>`，不要把源O3比较误称P1比较。

材料呈现实现为 `evals/_shared/category_materials.py`，只消费原标题/正文、已使用sidecar内容和冻结quote；从repair_materials识别的片段必须已在sidecar实际正文中出现，未送达候选不会新增进来，无法拆分的残余保留。用户prompt采用JSON列表，元素为 `origins`（role/part/url/author）和原样 `text`；同文本的多来源并列，不按URL覆盖不同文本。这个JSON只是模型输入呈现，不是benchmark题目schema变更，也不含人评答案。`--material-layout documents` 要求 `--body-limit 0`，不提供隐式截断。

未来新的题集/人票/补全文本应先用基础 `evaluate.py` 建立新的冻结对照，再用此脚本派生，不修改旧run以复用旧身份。基础runner默认 `legacy`、无evidence-reason，生产默认不变。当前P1为研究起点，不是正式生产版本；本轮结果和未解决边界见[状态](../../status.md#2026-09-27p-阶段材料呈现与证据化理由已终态)。

### 2026-09-27：O 阶段输入材料修复

本轮不改 v1 题目、参考和人票，而是在 M2 的48道人评题上把补充输入另存为 `body-context.jsonl`；原 `cases.jsonl` 与 as-of 引用保持原样。依据 [ADR-8ea1](../../../../adr/20260927-8ea1-repair-category-source-material.md)，先固定 M2 rubric 比较输入补全，再据错误归因调整候选；新正文和新引用属于被测输入配置，不是原 benchmark 的历史输入。

准备入口为 `scripts/eval/category_input_repair.py`。它读取 `--cases`、`--previous-context`、`--aihot-evidence`，写入尚不存在的 `--output` 目录；需要当前补采时才加 `--lookup-x`、`--fetch-web` 和凭据来源 `--env-file`。`--x-response` 可复用已冻结成功 lookup receipt，避免重新请求已有帖子。输出包括 `body-context.jsonl`、`case-ids.json`、`receipt.json` 和实际/复用的 X 响应；receipt 绑定 cases 与旧正文摘要，补充记录在 `repair_materials` 保留来源与取数时点。准备脚本会访问网络；只复算已有模型输出不需要再次准备材料。

O阶段准备原件归档于 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-27/13-48-38/input-preparation/`，保存first/second/final窗口。复用本轮final输入可直接使用该run自含的 `body-context.jsonl`、`cases.jsonl`、`human-reviews.json`。重新准备时，`--aihot-evidence` 必须指向 `~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-enrichment-fields/v2/evidence/aihot`，即其下能直接拼接题目 `provenance.aihot[].reference` 的目录；旧 receipt 没有记录这个根路径，不能只凭它推断。`--previous-context` 可取该run的 `body-context.jsonl` 保留已成功材料，`--x-response` 取 `input-preparation/final/x-lookup-reused.json`，另指定尚不存在的输出目录，不覆盖旧窗口。仅得到48行不证明材料齐全：本轮 final 的8份归档原文、9份母帖、10份引用、7份外链文本送达核对及限制见[状态](../../status.md)，新准备须按实际材料重新核对。实际运行关联见[方案履历](../../versions.md)。

可补材料包括身份绑定的 AIHOT detail original 正文区（优先原文语言 template，不读摘要/评分/分类）、当前 X 母帖及其直接引用、母帖或直接引用明确链接的文章。文章链接不递归扩展；web 与 feed 都可补正文。只采用比已有正文更长的归档文章，并保留已有成功材料及 provenance，后次获取失败不能抹去旧证据。当前 API 文本不覆盖媒体内容，`available` 只表示取得可读材料，不证明全文完整或历史同一版本；补采时间与准备时间不冒充历史可得时间。实质内容变化仍须核对原人评是否适用，不能自动转移人票。

实际模型调用沿用[执行 README](../../../../../evals/content-enrichment/aihot-category-navigation/README.md#人评优先计分与当前正文补充)，把 `--body-context` 指向本次冻结 sidecar、`--case-ids` 指向同批48题，并显式固定 `--human-reviews`；M2对照使用 `category-m2.txt` 与原模型/参数。`--body-limit 0` 同时向已有引用渲染传递全文模式，不再隐含截在4,000字符。人评整体命中含1道多答案，六类 P/R 只用47道单答案；原 `scores.json` 仍是 AIHOT 口径，人工结果另读 `human-priority-scores.json`。运行分区、输入覆盖、配对和成绩以[状态](../../status.md)为准。

生产路径的本地实现与上述离线补采不同：X 时间线在原请求中获取直接引用 expansions，保存长帖 note entities 和引用原文；富化优先读同响应保存的引用，缺失再查本地已采集数据，不新增逐题 X lookup。X 母帖/直接引用的外链文章及 feed/web 正文通过正文补充路径进入分类输入。实现存在不等于生产已部署，本轮未切换默认分类 rubric。

### 既有建题与运行记录

2026-09-22接续说明：本版361题未增加或改标；如今285dev与76reg均已有分类实验曝光，不再将下方建题时的“本轮未参与”当作当前独立性证明。来源上下文、正文全文与引用主次均是被测输入配置，不创建新benchmark版本；最新配置与成绩由[状态](../../status.md)及[执行入口](../../../../../evals/content-enrichment/aihot-category-navigation/README.md)维护。

命令的唯一维护入口在 [执行 README](../../../../../evals/content-enrichment/aihot-category-navigation/README.md)。本版使用该 build.py、`--inputs .../aihot-enrichment-fields/v2 --capture data/category-navigation-evidence/20260921 --version v1` 生成；当时capture目录现已归档至 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-21/10-08-04/support/category-navigation-evidence/20260921`，重跑时以此替换 `--capture`，并另用空的 `--data-root`。捕获原件也已冻结在本数据版本 evidence 中，不依赖临时网页服务存活；manifest保留当时命令，不改写历史路径。

未来先用 capture.py 获取新的六分类页面，再用 `--base .../aihot-category-navigation/v1 --inputs <更多合格原文> --capture <新捕获> --version v2` 执行合并、去重和有效性重验。旧版本与新版本可能 overlap，不把各版本题数相加。

2026-09-21新增可选原始引用消融，不改本版361题及gold：通过 `--quote-source` 读取本版直接父题库冻结raw，按每题observed_at选择一跳引用，来源/时间/版本不合格则不添加。它改变的是被测分类对象的可选输入逻辑；baseline默认标题正文不变。调用方式、运行metadata与quote-context字段含义见[执行README](../../../../../evals/content-enrichment/aihot-category-navigation/README.md#可选原始引用输入消融)。不能把该离线能力当作生产已有输入。

指标保留整体 `category_accuracy`，并计算模型、产品、行业、论文、教程、观点各自的 precision 与 recall，共 13 项，均为确定性计算，无需 LLM 判官。失败仍计整体错误与真实类别 FN；零分母未计算。定义、TP/FP/FN 保存位置及旧预测零调用补算命令由[执行 README](../../../../../evals/content-enrichment/aihot-category-navigation/README.md)维护。题库输入、gold 与版本不因新增指标改变。实际评测及下一步见 [状态](../../status.md)。本轮 regression 未参与分类 prompt 开发；它是否在其它历史任务曝光过未全面核验，因此不宣称跨项目从未见过。
