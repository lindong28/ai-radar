# AIHOT 六类网站导航 · v1

> [Developer] · 冻结题集，2026-09-21。数据扩充另建 v2，不覆盖此版本。

数据位于 `~/research/video-eval-arena/data/benchmarks/ai-radar/content-enrichment/aihot-category-navigation/v1/`。真实 loader 已验证 manifest、题目及证据哈希：361 道单条分类题，dev 285、regression 76；模型 45、产品 60、行业 60、论文 46、教程 67、观点 83。

## 参考语义与建设

参考来自 AIHOT 六个分类过滤页面中实际出现的新闻，不使用旧 API 的 category：网站 JS 将六类对应到 model_release、product_launch、industry_event、research_paper、tutorial_explainer/tool_or_prompt、opinion_analysis；旧 API 则把教程与观点合为 tip。本批 83 条观点在旧 API 中全部是 tip。因此这是新 benchmark，不是把旧五类题库改名为 v2。网站内部分类器的完整 prompt 未取得，不能反复触发它重新分类；其自一致率未测。

以 `aihot-enrichment-fields/v2` 的合格原始输入为候选，按 AIHOT item_id 与规范化原文 URL 双重匹配页面成员；每题只有原始 title/body 作为模型输入，AIHOT 的生成标题、摘要、标签和参考分类不进入推理。14 份 HTML 捕获共 560 个不同成员，无跨类冲突，361 个有已冻结的合格原文。其它 3,505 条输入只是未在这些有限页面中观察到类别，不能作为负例，也不能据此判不值得收录。

按原 case_id 去重并继承原 split，不按 gold 重采样。实质输入冲突、跨分类观测冲突、同 item_id 不同规范化原文 URL 均排除；时间戳、HTML 和 URL 格式差异不作为实质内容变化。参考是本次网页观测时刻，不声称当初发布时也属该类；页面请求 URL、观察时间与原件哈希随题保存。

`cases.jsonl` 包含 input/reference/provenance；`observations.json` 是逐 item 的分类观测，`excluded.jsonl` 解释未入题原因，`manifest.json` 记录来源、数量和哈希。`evidence/` 保存匹配所依赖的分类响应及原输入题库 manifest/cases；完整原始输入档案仍由 source_datasets 及原 provenance 定位。后续人评优先于这些网站金标，原始参考不覆盖。

## 重建、扩充、运行

2026-09-22接续说明：本版361题未增加或改标；如今285dev与76reg均已有分类实验曝光，不再将下方建题时的“本轮未参与”当作当前独立性证明。来源上下文、正文全文与引用主次均是被测输入配置，不创建新benchmark版本；最新配置与成绩由[状态](../../status.md)及[执行入口](../../../../../evals/content-enrichment/aihot-category-navigation/README.md)维护。

命令的唯一维护入口在 [执行 README](../../../../../evals/content-enrichment/aihot-category-navigation/README.md)。本版使用该 build.py、`--inputs .../aihot-enrichment-fields/v2 --capture data/category-navigation-evidence/20260921 --version v1` 生成；当时capture目录现已归档至 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-21/10-08-04/support/category-navigation-evidence/20260921`，重跑时以此替换 `--capture`，并另用空的 `--data-root`。捕获原件也已冻结在本数据版本 evidence 中，不依赖临时网页服务存活；manifest保留当时命令，不改写历史路径。

未来先用 capture.py 获取新的六分类页面，再用 `--base .../aihot-category-navigation/v1 --inputs <更多合格原文> --capture <新捕获> --version v2` 执行合并、去重和有效性重验。旧版本与新版本可能 overlap，不把各版本题数相加。

2026-09-21新增可选原始引用消融，不改本版361题及gold：通过 `--quote-source` 读取本版直接父题库冻结raw，按每题observed_at选择一跳引用，来源/时间/版本不合格则不添加。它改变的是被测分类对象的可选输入逻辑；baseline默认标题正文不变。调用方式、运行metadata与quote-context字段含义见[执行README](../../../../../evals/content-enrichment/aihot-category-navigation/README.md#可选原始引用输入消融)。不能把该离线能力当作生产已有输入。

指标保留整体 `category_accuracy`，并计算模型、产品、行业、论文、教程、观点各自的 precision 与 recall，共 13 项，均为确定性计算，无需 LLM 判官。失败仍计整体错误与真实类别 FN；零分母未计算。定义、TP/FP/FN 保存位置及旧预测零调用补算命令由[执行 README](../../../../../evals/content-enrichment/aihot-category-navigation/README.md)维护。题库输入、gold 与版本不因新增指标改变。实际评测及下一步见 [状态](../../status.md)。本轮 regression 未参与分类 prompt 开发；它是否在其它历史任务曝光过未全面核验，因此不宣称跨项目从未见过。
