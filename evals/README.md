# 评测代码导航

新评测从[项目评测入口](../docs/evaluations/README.md)开始。目录多于四个不表示当前要运行多个 benchmark；历史消费者契约仍保留兼容入口，不与当前成绩混算。

## 当前四对象

| 对象 | 当前代码与指标入口 | 已实现的执行范围 |
| --- | --- | --- |
| 新闻准入 | [aihot-observed-membership](news-admission/aihot-observed-membership/README.md) | validate、独立 prefilter run、抽样、恢复、计分与归档 |
| 可见评分 | [aihot-score-pointwise](visible-score/aihot-score-pointwise/README.md) | validate、独立评分 run、固定抽样/恢复/归档、dev 拟合的零调用分数校准 |
| 内容富化 | [aihot-category-navigation](content-enrichment/aihot-category-navigation/README.md)；[aihot-enrichment-fields](content-enrichment/aihot-enrichment-fields/README.md) | 网站六类分类的捕获、建题、独立推理和归档；其它字段仍复用原字段题库与计分器 |
| 精选成员 | [aihot-featured-threshold](featured-members/aihot-featured-threshold/README.md) | 独立题库 validate；逐条阈值不是完整池规则评测 |

原字段与其它对象建题／扩题用 `scripts/build_eval_datasets.py`；网站六类分类因参考语义不同，用其叶子的 `capture.py`、`build.py`。详见各入口 README；内容富化其它字段和精选成员的独立推理适配缺口仍归各对象 status，不因分类入口接通而变为已实现。

可见评分另有 [aihot-score-context](visible-score/aihot-score-context/README.md) 离线上下文诊断，建题用 `score_context_dataset`，时钟/事件配对用 `score_context_run`；输入消费者契约不同，不能把它的40题作为主题库新增40条。来源字段消融和条件权重的代码为 `_shared/score_context_study.py`、`score_context_analysis.py`、`score_context_validate.py`，结果与复用边界见[评分状态](../docs/evaluations/visible-score/status.md)。

## 共享实现与候选资产

- `_shared/`：loader、建题、指标、推理、判官和归档的单一实现；各 benchmark 的 evaluate.py 只负责选择消费者入口。
- [news-admission/prompts](news-admission/prompts/README.md)：准入对象的离线候选，不属于某一 gold 契约，也不是生产默认。
- 各叶子 `metrics.json`：指标定义；实际数值仍在项目根 runs 与 experiments。

## 历史兼容入口

| 对象 | 仅历史复现的 benchmark |
| --- | --- |
| 新闻准入 | [aihot-prefilter](news-admission/aihot-prefilter/README.md)（±12h）、[aihot-all-members](news-admission/aihot-all-members/README.md)（共享池） |
| 可见评分 | [aihot-visible-score](visible-score/aihot-visible-score/README.md)（共享池） |
| 内容富化 | [aihot-enrichment](content-enrichment/aihot-enrichment/README.md)（共享池） |
| 精选成员 | [aihot-featured-members](featured-members/aihot-featured-members/README.md)（共享池） |

`assets.benchmark_pairs()`、布局校验与历史指标查询仍注册上述契约；不从目录名称猜测可删除性。保留这些轻量入口和相应测试，是为了读取／复现既有实验，不是新迭代的默认选择。旧数据的物理归档位置由[资产说明](../docs/evaluations/assets.md)维护。

旧路径通过 `asset-relocations.json` 与 `_shared/relocations.py` 解析；2026-09-20 用户已清退 33 份旧共享池实验及其 85 行指标引用，映射不代表这些原件仍存在，也不自动恢复。仍保留的 prefilter、support 与外部题库路径可用 `python -m evals._shared.relocations <旧路径>` 定位；正式模型输出与 support 迁移容器须按 metadata.kind 区分。旧格式代码仍可运行，不代表已清退实验可继续复盘。

## 测试与运行记录

`tests/test_eval_entry_cleanup.py` 检查新默认与显式历史口径；`test_admission_labels.py` 检查当前准入标签；`test_eval_object_datasets.py` 和 `test_eval_dataset_merge.py` 中旧准入测试显式选择历史契约。迁移、哈希、共享池缓存与指标测试继续保留，不能因名称较旧而删除。

真实模型输出、失败与恢复记录在 `runs/<target>/<benchmark>/...`；解释与指标索引在 `experiments/<target>/<benchmark>/...`，两者不是重复备份。逐轮结论从[台账](../docs/evaluations/experiments/ledger.md)定位，当前表现从对象 status 定位。不要把旧 benchmark 分区改名成当前分区，也不要删除被结论引用的 preflight 材料。
