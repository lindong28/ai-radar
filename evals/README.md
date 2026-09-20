# 评测代码导航

新评测从[项目评测入口](../docs/evaluations/README.md)开始。目录多于四个不表示当前要运行多个 benchmark；历史消费者契约仍保留兼容入口，不与当前成绩混算。

## 当前四对象

| 对象 | 当前代码与指标入口 | 已实现的执行范围 |
| --- | --- | --- |
| 新闻准入 | [aihot-observed-membership](news-admission/aihot-observed-membership/README.md) | validate、独立 prefilter run、抽样、恢复、计分与归档 |
| 可见评分 | [aihot-score-pointwise](visible-score/aihot-score-pointwise/README.md) | 独立题库 validate；预测后的指标复用共享 metrics.score |
| 内容富化 | [aihot-enrichment-fields](content-enrichment/aihot-enrichment-fields/README.md) | 独立题库 validate；字段指标复用共享 metrics.score |
| 精选成员 | [aihot-featured-threshold](featured-members/aihot-featured-threshold/README.md) | 独立题库 validate；逐条阈值不是完整池规则评测 |

建题／扩题统一用 `scripts/build_eval_datasets.py`，其 `build` 默认使用当前准入口径；详见[操作说明](../docs/evaluations/benchmarks/object-datasets.md)。其余三个对象的独立推理适配缺口仍归各对象 status，不因本次整理而变为已实现。

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

## 测试与运行记录

`tests/test_eval_entry_cleanup.py` 检查新默认与显式历史口径；`test_admission_labels.py` 检查当前准入标签；`test_eval_object_datasets.py` 和 `test_eval_dataset_merge.py` 中旧准入测试显式选择历史契约。迁移、哈希、共享池缓存与指标测试继续保留，不能因名称较旧而删除。

真实模型输出、失败与恢复记录在 `runs/<target>/<benchmark>/...`；解释与指标索引在 `experiments/<target>/<benchmark>/...`，两者不是重复备份。逐轮结论从[台账](../docs/evaluations/experiments/ledger.md)定位，当前表现从对象 status 定位。不要把旧 benchmark 分区改名成当前分区，也不要删除被结论引用的 preflight 材料。
