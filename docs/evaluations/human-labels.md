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

原票与派生记录位于项目根 `human-evals/<target>/<batch>/`，不进 Git；本次权威批次为 `human-evals/news-admission/2026-09-20-c11/imported/`。规则、操作和批次解释在 Git 文档中。题目大数据仍按 assets.md 放本机 benchmark 根，不在 DGX。

- `feedback.json`：用户原票，原字段与理由逐字保存；SHA 绑定本地保存的字节，不声称用户签署了新 SHA。
- `review-context.json`：用户评审时看到的案例、原预测、原 prompt、诊断 prompt 与 reason。
- `annotations.jsonl`：仅明确人评；按 `target/case_id/field/input_identity` 限定适用范围，记录 `value`、原 `reason`、原票 SHA、`provenance=user`。
- `source-cases.jsonl` / `source-predictions.jsonl`：源数据快照；`effective-cases.jsonl` 是应用人评后的计分视图；参考来源留在 provenance，不进入 LLM input。
- `original-scores.json` / `human-priority-scores.json`：同300题、同原预测的两种标签口径，不是两轮模型性能。
- `manifest.json`：来源、适用范围、覆盖数及归档文件 SHA。

导入当前网页导出（输出目录必须不存在，无 API 调用）：

```bash
PYTHONPATH=src:. uv run python -m evals._shared.human_labels import-prefilter \
  --feedback human-evals/news-admission/2026-09-20-c11/feedback.json \
  --run runs/news-admission/aihot-observed-membership/v1/2026-09-20/01-19-03 \
  --output human-evals/news-admission/2026-09-20-c11/imported
```

### 后续建题/扩题/迭代必做

1. 先照原建题流程合并、去重、重验输入与参照，输出新的不可变版本。AIHOT 原始事实不能被人评改写。
2. 读取 `human-evals/` 下对应对象的真实用户标注批次，再应用人评形成明确标注 `human-reference-priority-v1` 的新计分视图。不能直接把新抓取的自动标签拿去覆盖已有用户判断。
3. 检查输出 manifest 的 `human_case_count`、`changed_field_count`、`absent_case_ids`。本次视图不含的人评仍留在标注库供未来复用，不能静默删除。输入有实质改变会报错，需核对适用性，不退回自动标签当作已解决。
4. 指标/实验记录须同时绑定原数据 SHA、人评批次 SHA、effective cases SHA；推理只读 `input`。已用于开发、错误分析的人评题不能标成未见 holdout；定向错误子集不代表总体分布。

复用命令（`--batch` 可多次提供，`--cases` 可为新题库或已冻结的运行子集）：

```bash
PYTHONPATH=src:. uv run python -m evals._shared.human_labels apply \
  --target news-admission \
  --cases /path/to/new-version/cases.jsonl \
  --batch human-evals/news-admission/2026-09-20-c11/imported \
  --output /path/to/new-human-priority-view
```

输出是 **human-priority 计分视图，不是原 `aihot-observed-membership` 的下一版本**；消费者语义不同，不冒充纯 AIHOT 收录 benchmark。共享 `apply_labels` 支持表中四对象的参考字段；当前网页导入器仅解析这次 prefilter export，其他对象收到实际人评时须按其票型接入，不能把文本候选质量票强转成参考值。此命令不会改历史 load_dataset、自动建题脚本或生产。后续 session 必须显式完成上述应用步骤；需注册可直接模型运行的新 benchmark 时，沿既有契约分区另建，不偷换旧 benchmark 的默认语义。

内容适用性复用既有 `substantive_hash`：遵循用户此前的实质性裁决，仅时间戳、HTML、来源显示名、已支持的标题标点与 X URL 别名变化不使人评自动失效；这不代表时间戳不会影响模型的时效性判断。新闻准入还绑定回复/引用关系、来源类型与 tier；评分也绑定影响来源权威判断的 tier；精选完整保留输入身份。标注适用性检查不改写模型输入，也不是不同输入下复用旧模型输出的许可。

## 判官与 reason

以人评样本作为判官选择/校准的首要锚；明确报告人评覆盖数、字段、来源与是否参与过调参。不能因某判官更吻合 AIHOT 就忽略它在人评题上的错误；人评不足时如实报告不确定性，不宣称判官已校准。

后续 LLM 判断须先输出简短、可检查的 `reason`（事实依据/命中准则，不要求隐藏思维链），再输出最终分类、布尔决策或分数；prompt 示例、结构化输出字段顺序与原始归档遵守同一顺序。接入新迭代前检查原始响应确实满足要求，缺失不能补造；程序序列化重排不算满足。旧冻结 prompt/响应不重写，生产改动仍须单独验证与部署。用户认为 reason 有助于正确性是待评测的假设，不视作已证实收益。

## 本批可复用的编辑偏好

### 2026-09-20 实际导入与重评分

25 票中 `keep=10`、`positive=8`、`negative=6`、`pending=1`；形成 24 条明确人评，其中 14 条改变 reference，10 条确认原标签。其余三个对象本批没有用户票，不生成虚构标注。原 C11 的同一批 300 题、同一份预测重评分如下：

| 标签口径 | TP / FP / FN / TN | Precision | Recall |
| --- | --- | --- | --- |
| 原 AIHOT reference | 127 / 17 / 8 / 148 | 88.19% | 94.07% |
| 明确人评优先，未评维持原 reference | 135 / 9 / 2 / 154 | 93.75% | 98.54% |

这不是模型优化收益，也不是独立回归达标：本批是已见开发集，300 题中只有 24 题由用户明确审过，不能称“全人评300题”。8 个 FP 改为 TP，6 个 FN 改为 TN；剩余 2 个 FN 中包含未评的 `d18cf04ecea4cb98b3765ca9`，不得擅自改票。用户票中的理由指向诊断调用时，以归档展示上下文解释，不改原预测。

实际 `import-prefilter` 与 `apply` CLI 均已执行，重新应用的 cases 与导入产生的 effective cases SHA 完全一致：`13cf1bf6f632ae55a0ce961b316724e1c59f4e1ed488fd54dfd97b806835155d`。无新增模型调用，原 cases/predictions 与原票声明的 SHA 一致。18 项标签定向测试及 72 项既有指标、身份、合并测试通过；覆盖四对象八字段、五种投票状态、输入变化与冲突、无效票及归档篡改，不代表模型泛化质量。独立审查发现并修复评分 tier 适用身份遗漏，定点复核通过。

用户指出：AI 相关性并非收录充分条件，还要有值得读者阅读的新增信息。无依据的推荐/观点、缺少一手信息的低信息转载宣传、过度细分的会议宣传，在本批具体案例中不值得收录。这是后续 prompt 假设的来源，不是“宣传全部拒绝”“某来源全部拒绝”的规则，也不能据此替用户标注其它题目。
