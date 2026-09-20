# 人评标注与优先级

> [Developer] · 2026-09-20 用户明确裁决；四对象共用。代码：`evals/_shared/human_labels.py`。本规则补充并在明确人评范围内优先于原 AIHOT 拟合目标。

## 权威与适用范围

**同一题目、同一实质输入、同一评测字段：用户明确人评 > AIHOT 或其它非人评派生标签。** LLM 判官之间存在分歧时，先对照已有对应人评；不能以 AIHOT、模型多数票、置信度或更高总分推翻人评。没有覆盖到的案例仍使用原参照；相似案例只能用于理解标准，不能冒充被用户逐条标注。人評内部出现真正冲突时保留两份原票并请求澄清，不能按文件排序自动择一。

| 对象 | 人评可以覆盖的字段 | 不可越界 |
| --- | --- | --- |
| 新闻准入 | `member` | 收录判断不自动推出分数、分类或精选资格 |
| 可见评分 | `score` | 数值须使用该题的可见评分量纲 |
| 内容富化 | `category`、`tags`、`title`、`summary`、`reason`，分别标注 | 对某个候选文本的质量评分不是新的参考文本；应归档到该候选/字段的判官校验材料，不能写进 reference |
| 精选成员 | `featured` | 保留候选组/时间窗/规则输入身份，不把单条点评外推到完整组合 |

每份人评保存原始提交、实际展示输入、原参考与候选输出、用户理由、对应字段及输入身份。`keep` 表示明确确认原 reference，而不是确认模型；`positive`/`negative` 覆盖 reference；`pending`/`uncertain` 保存原票但不产生有效人评标签。没有人评的对象不由 agent 补票。“同意 LLM 理由”连同当时展示的 prompt 和独立诊断理由归档；诊断重跑的理由不冒充原调用的理由。

## 资产与使用

原票与派生记录统一位于项目根 `human-evals/<target>/reviews.json`，不进 Git。日期、批次、实验名与处理阶段不占目录层级；一个对象的多批人评保存在同一文件的 `batches[]` 中。本次实际文件是 `human-evals/news-admission/reviews.json`。存储代码为 `evals/_shared/human_store.py`，标签应用仍为 `human_labels.py`；题目大数据根仍按 assets.md，不在 DGX。

### metadata 字典（字段位置均相对 reviews.json）

| 字段 | 语义 / 来源 |
| --- | --- |
| `metadata.format` | `ai-radar-human-reviews-v1`，人评容器格式，不是 benchmark 版本 |
| `metadata.target` | 稳定对象标识；本文件为 `news-admission` |
| `batches[].metadata.batch_id` | 对象内唯一批次标识；本批保留 `2026-09-20-c11` 作为不解析的 ID，后续可用其它唯一名称；不由 ID 推定日期、模型或标签优先级 |
| `batches[].metadata.reviewed_at` | 实际人评完成时间；本批无法精确取得，记 null |
| `batches[].metadata.feedback_exported_at` | 原票 `exported_at`；本批 `2026-09-20T05:50:08.286Z`，是浏览器导出时间，不是新闻时间或人评完成时间 |
| `batches[].metadata.recorded_at` | 本批首次写入新容器时的 UTC 时钟；迁移不是重新做人评，重复导入保留首次值 |
| `batches[].metadata.source_run` | 原被评运行位置，由冻结源 manifest 取得；供追溯，不限定标注只可用于这一轮 |
| `batches[].metadata.target_prediction` | 用户评的是哪份预测；本批 `original_c11`，不冒充诊断重跑输出 |
| `batches[].metadata.policy`、`user_authority` | 标签优先策略与确属用户原票的授权来源，不由文件夹名称推定 |
| `batches[].metadata.legacy_manifest` | 原归档 manifest 的完整快照，保留原 SHA、分母、范围；旧路径只表示历史位置，不是当前读入口 |
| `batches[].sha256` | 该批 metadata 与 data 的结构化摘要，由读取器核对；不是用户签名，也不代表其它批次 |

每批 `data` 的内容如下：

以下字段描述新闻准入票型；内容富化的判官校验票保留 `data.feedback_raw`、`data.material`、`data.result`，`data.annotations=[]`，不能把候选质量评分转换成参考文本。其 metadata 的 `kind=judge-calibration`、`user_confirmed_sha256` 分别说明用途和用户确认依据；`source_run` 指校验 run，原被评输入/候选在 material 中。还没有这类真实用户票时不造文件。

- `feedback_raw`：原反馈文件的 UTF-8 原文；还原为字节后保留原 SHA，不声称用户签署迁移后的新 SHA。
- `review_context`：案例、原预测、原 prompt、诊断 prompt 与 reason。
- `annotations[]`：仅明确人评；`target/case_id/field/input_identity` 界定适用范围，保留 `value`、用户 `reason`、原票 SHA 和 `provenance=user`。所属批次由父记录唯一确定，不在每行重复日期。
- `source_cases[]` / `source_predictions[]`：原快照；`effective_cases[]` 是应用人评后的计分视图，来源信息不进入 LLM input。
- `original_scores` / `human_priority_scores`：同题同预测的两种标签口径，不是两轮模型成绩。

新增批次不会覆盖旧记录；同 ID 同内容重复导入无变化，同 ID 不同内容拒绝。读写器校验摘要与对象，写入用对象文件锁及完整文件替换，避免两个追加者丢票。`reviews.lock` 只是进程锁，不含评价数据。四对象沿同一布局，尚无实际用户票的不预建空文件。

导入网页反馈（`--output` 是稳定 JSON 文件；批次通过参数写入 metadata，无 API 调用）：

```bash
PYTHONPATH=src:. uv run python -m evals._shared.human_labels import-prefilter \
  --feedback /path/to/user-feedback.json \
  --run runs/news-admission/aihot-observed-membership/v1/2026-09-20/01-19-03 \
  --batch-id 2026-09-20-c11 \
  --output human-evals/news-admission/reviews.json
```

### 后续建题/扩题/迭代必做

1. 先照原建题流程合并、去重、重验输入与参照，输出新的不可变版本。AIHOT 原始事实不能被人评改写。
2. 读取 `human-evals/<target>/reviews.json` 中的全部真实用户标注批次，再应用人评形成明确标注 `human-reference-priority-v1` 的新计分视图。不能直接把新抓取的自动标签拿去覆盖已有用户判断。
3. 检查输出 manifest 的 `human_case_count`、`changed_field_count`、`absent_case_ids`。本次视图不含的人评仍留在标注库供未来复用，不能静默删除。输入有实质改变会报错，需核对适用性，不退回自动标签当作已解决。
4. 指标/实验记录须同时绑定原数据 SHA、人评批次 SHA、effective cases SHA；推理只读 `input`。已用于开发、错误分析的人评题不能标成未见 holdout；定向错误子集不代表总体分布。

复用命令（`--batch` 可多次提供，`--cases` 可为新题库或已冻结的运行子集）：

```bash
PYTHONPATH=src:. uv run python -m evals._shared.human_labels apply \
  --target news-admission \
  --cases /path/to/new-version/cases.jsonl \
  --batch human-evals/news-admission/reviews.json \
  --output /path/to/new-human-priority-view
```

输出是 **human-priority 计分视图，不是原 `aihot-observed-membership` 的下一版本**；消费者语义不同，不冒充纯 AIHOT 收录 benchmark。共享 `apply_labels` 支持表中四对象的参考字段；当前网页导入器仅解析这次 prefilter export，其他对象收到实际人评时须按其票型接入，不能把文本候选质量票强转成参考值。此命令不会改历史 load_dataset、自动建题脚本或生产。后续 session 必须显式完成上述应用步骤；需注册可直接模型运行的新 benchmark 时，沿既有契约分区另建。2026-09-20 用户另行授权历史指标查询默认采用明确标识的人评计分视图，具体如下；原 benchmark 的输入契约与观测标签不变。

### 当前指标查询与历史补评分

新闻准入默认查询采用 `human-reference-priority-v1`。原 `runs/<run_id>/scores.json` 和 `experiments/<run_id>/metrics/summary.json` 永远是当时的原始评分记录，**不再是默认选型入口**。它们保留用于追溯、判断标签修正与逻辑改动各自的影响；历史原值不是错误数据，但将其不加说明地当成人评优先成绩是错误用法。

```bash
PYTHONPATH=src:. uv run python -m evals._shared.cli index
```

此命令零模型调用：读取每轮冻结案例、预测和当前全部人评批次，重用 `apply_labels` 与 `metrics.score`；失败题不丢弃，覆盖不到的案例不改标签。它追加 `experiments/<run_id>/metrics/human-priority-<identity>.json`，更新同目录 `current.json` 及总表 `experiments/metrics/summary.json`。重复执行在身份不变时不生成新补评分文件。原输入、预测、分数和人评票均不改写；未知输入、冲突票或原件不一致报错，全部叶子验证结束前不发布新查询总表。

| 当前查询字段 / 补评分字段 | 语义 |
| --- | --- |
| `label_policy` | 当前有效参考策略，避免与原 AIHOT 口径混淆 |
| `prediction_view` | `archived` 为该 run 原本的预测（可能已经是规则组合，须结合 metadata）；`with_existing_policy` 为同 run 已保存的规则预测，不新跑规则或模型 |
| `human_case_count` / `changed_field_count` | 该 run 人评覆盖题数 / 实际改变的参考字段数；0 表示未覆盖，不是全题人工复核 |
| `source` / `pointer` | 当前数值的实际补评分文件与 JSON 路径；必须沿它回读，不再假定源一定叫 scores.json |
| `observed_source` / `observed_pointer` | 同预测视图的原 AIHOT 评分定位；policy 附加视图指向 `human-feedback-scores.json` 中的原规则成绩，不误指纯模型分数 |
| `effective_cases_digest` | 当前有效案例结构化摘要，比较时须一致 |
| 补评分 `identity` | 原 cases/predictions/metadata/scores SHA、有效案例摘要、人评批次 ID/SHA、实际 prediction view 及文件 SHA、标签应用/输入身份/计分/投影实现摘要 |
| 补评分 `views.<view>.scores` / `.human_only` | 全部案例的当前成绩 / 明确人评子集成绩；后者为定向错误样本，不代表总体分布 |

实现位于 `evals/_shared/human_metrics.py`，既有 `assets.rebuild_index` 和 `runner.compare` 共用它；compare 对原 run 的 archived 预测按同一批人评再计分，不拿附加 policy 视图冒充另一轮。新的人评解释候选运行完成后自动刷新总表；其它对象的查询语义不由这次新闻准入修正改变。查全表时按 `(run_id, prediction_view, task, metric_name)` 区分行，不把模型/规则两种结果重复计为不同题目。

补评分是同一历史运行的新测量，不创建伪造的模型 run。新增人评后重新执行 index 即扩展当前视图，旧补评分文件仍可复现；人评库缺失而已有补评分记录时拒绝退回旧口径。最新候选比较见[新闻准入状态](news-admission/status.md#当前查询口径全部历史候选应用明确人评优先)。

计分视图 `manifest.json` 的 `batches[]` 对每个实际采用的人评批次记录 `path`、`batch_id`、`sha256`；摘要绑定该批内容，不绑定会随追加变化的整个容器。复现历史视图时，在同一个 `--batch reviews.json` 后按 manifest 增加一个或多个 `--batch-id <id>`，并核对所选批次的 `sha256`；不传选择器代表读取当前全部批次，可能得到更新后的计分口径。`load_annotations(path, batch_ids=[...])` 提供同样的代码入口，未知批次报错；旧 manifest 目录继续使用原 manifest SHA。

内容适用性复用既有 `substantive_hash`：遵循用户此前的实质性裁决，仅时间戳、HTML、来源显示名、已支持的标题标点与 X URL 别名变化不使人评自动失效；这不代表时间戳不会影响模型的时效性判断。新闻准入还绑定回复/引用关系、来源类型与 tier；评分也绑定影响来源权威判断的 tier；精选完整保留输入身份。标注适用性检查不改写模型输入，也不是不同输入下复用旧模型输出的许可。

### 从旧目录迁移

`PYTHONPATH=src:. uv run python -m evals._shared.human_labels migrate --source /path/to/old-batch/imported --batch-id <id> --output human-evals/<target>/reviews.json` 先校验旧 manifest，再逐项装入新容器；当前迁移器对应已有的新闻准入票型，不能当作其它对象的票型转换器。旧目录只保留为 `runs/news-admission/aihot-observed-membership/v1/2026-09-20/10-29-30/support/human-eval-layout-migration/source-layout/` 的迁移前档案，原重复导入验证材料也在那里，不再作为人评入口。当前标注、原理由、展示上下文及源预测的内容不变，历史成绩不重新归因。脚本仍可读取独立留存的旧 manifest 目录供历史复现，但新 CLI 导入默认要求 JSON 目标和显式 batch ID。

## 判官与 reason

以人评样本作为判官选择/校准的首要锚；明确报告人评覆盖数、字段、来源与是否参与过调参。不能因某判官更吻合 AIHOT 就忽略它在人评题上的错误；人评不足时如实报告不确定性，不宣称判官已校准。

后续 LLM 判断须先输出简短、可检查的 `reason`（事实依据/命中准则，不要求隐藏思维链），再输出最终分类、布尔决策或分数；prompt 示例、结构化输出字段顺序与原始归档遵守同一顺序。接入新迭代前检查原始响应确实满足要求，缺失不能补造；程序序列化重排不算满足。旧冻结 prompt/响应不重写，生产改动仍须单独验证与部署。用户认为 reason 有助于正确性是待评测的假设，不视作已证实收益。

## 本批可复用的编辑偏好

### 根据人评解释运行新候选

2026-09-20 已把解释转成四个显式 prompt 候选，路径为 `evals/news-admission/prompts/human-information-value*.json`；`c11-reason-first.json` 为对照。候选归对象级目录，不绑定旧 benchmark 的 gold；旧路径通过兼容链接读取同一份文件。候选通过现有真实 prefilter 推理和确定性计分器执行，结果见[对象状态](news-admission/status.md)。未达到采用标准的候选不替换默认规则，不把完成代码等同于质量达标。

```bash
PYTHONPATH=src:. uv run python scripts/eval/run_prefilter_human_feedback.py \
  --source-run runs/news-admission/aihot-observed-membership/v1/2026-09-20/01-19-03 \
  --config evals/_shared/configs/baseline-ark.json \
  --env-file /Users/lindong/research/ai-radar/.env \
  --prompt evals/news-admission/prompts/human-information-value-contextual.json \
  --reviews human-evals/news-admission/reviews.json \
  --batch-id 2026-09-20-c11 --label my-human-feedback-candidate --workers 8
```

该命令会产生付费请求，不是零调用重评分。重放源 run 的 dataset、split、seed、limit、排除记录，并在发请求前核对完整题目身份及人评适用性；无法原样重现即停止。原 run 与人评库只读。新批次可重复传 `--batch-id`，日期/批次不依赖目录解析；若只想应用标签，仍用上文零调用 `human_labels apply`。失败恢复加 `--reuse <该候选的失败run>`，只有题目/对象身份一致的成功结果可复用，不把不同 prompt 的预测混入。

新 run 附加 `human-effective-cases.jsonl`、`policy-predictions.jsonl`、`human-feedback-scores.json`：后者 `views.model_only/with_existing_policy` 各含 `observed/human_priority/human_only` 三套分数，分别表示原 AIHOT、明确人评优先、仅明确人评子集。元数据绑定选中批次ID/SHA及输入、预测、effective cases、policy predictions SHA。早于本轮脚本补齐摘要字段的08-10/08-12/08-16/08-22原件不改写，其补充SHA在本轮 `comparison.json`。标准 `scores.json` 保留原AIHOT口径；**机器总表已改为上述人评优先当前视图**，原 sidecar 保留当时所选批次，后续追加票以最新补评分为准。

模型实际 `reason` 位于 `predictions.jsonl → stage_results.prefilter.output.reason`；原始内容和实际 prompt 在 `attempts/`。生产模块保存到 `item_evaluations.output_json.reason`，原 numeric 字段维持兼容；启发式 fallback 明确标为规则说明，不伪装成 LLM 理由。当前内容富化文本判官 `evals/_shared/judge.py` 返回 `reason`，不再只投影为 `rationale`。缺失/空理由或解析字段顺序错误记 error，不补造；生产批处理保留出错 payload 并继续处理其它题。

校验范围是解析对象的字段顺序，不是任意非法 JSON 的严格语法证明；独立审查留下一个 MEDIUM：重复 `reason` 键可能被旧解析器折叠，导致顺序检查失去区分力。本轮1500个成功原始响应未出现重复键，现阶段未扩大修改共用解析器，后续维护可从该具体边界接续。旧冻结输出与 legacy `src/airadar/eval/aihot_fit` 判官未改写；该历史链不是当前四对象文本判官入口。

### 2026-09-20 实际导入与重评分

25 票中 `keep=10`、`positive=8`、`negative=6`、`pending=1`；形成 24 条明确人评，其中 14 条改变 reference，10 条确认原标签。其余三个对象本批没有用户票，不生成虚构标注。原 C11 的同一批 300 题、同一份预测重评分如下：

| 标签口径 | TP / FP / FN / TN | Precision | Recall |
| --- | --- | --- | --- |
| 原 AIHOT reference | 127 / 17 / 8 / 148 | 88.19% | 94.07% |
| 明确人评优先，未评维持原 reference | 135 / 9 / 2 / 154 | 93.75% | 98.54% |

这不是模型优化收益，也不是独立回归达标：本批是已见开发集，300 题中只有 24 题由用户明确审过，不能称“全人评300题”。8 个 FP 改为 TP，6 个 FN 改为 TN；剩余 2 个 FN 中包含未评的 `d18cf04ecea4cb98b3765ca9`，不得擅自改票。用户票中的理由指向诊断调用时，以归档展示上下文解释，不改原预测。

初次导入的 `import-prefilter` 与 `apply` CLI 均已执行，重新应用的 cases 与导入产生的 effective cases SHA 完全一致：`13cf1bf6f632ae55a0ce961b316724e1c59f4e1ed488fd54dfd97b806835155d`。无新增模型调用，原 cases/predictions 与原票声明的 SHA 一致。初次导入时18项标签定向测试及72项既有指标、身份、合并测试通过；覆盖四对象八字段、五种投票状态、输入变化与冲突、无效票及归档篡改，不代表模型泛化质量。初次独立审查发现并修复评分 tier 适用身份遗漏，定点复核通过。

用户指出：AI 相关性并非收录充分条件，还要有值得读者阅读的新增信息。无依据的推荐/观点、缺少一手信息的低信息转载宣传、过度细分的会议宣传，在本批具体案例中不值得收录。这是后续 prompt 假设的来源，不是“宣传全部拒绝”“某来源全部拒绝”的规则，也不能据此替用户标注其它题目。
