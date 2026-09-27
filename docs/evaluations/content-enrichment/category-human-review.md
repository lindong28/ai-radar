# C5 分类分歧的人评工作台

> [Developer] · 用户 2026-09-27 要求：保留 AIHOT、DeepSeek、Codex、后续 Claude 的分类及理由，让用户多选可接受分类；材料与真实人评通过 Git 长期保存。本页不改变历史 gold、指标或生产分类器。

## 入口与范围

```text
human-evals/content-enrichment/
  category-review.json             # 冻结新闻、原预测/prompt、按评价者追加的模型意见（已入 Git）
  reviews.json                     # 收到用户真实反馈后创建；人评原票及展示快照（允许入 Git）
evals/content-enrichment/aihot-category-navigation/
  human_review.py                  # 建材料、校验、追加模型意见、渲染、导入用户票
  human_review/{index.html,app.js,style.css}  # 通用静态渲染器，不硬编码新闻
tests/test_category_human_review.py
```

日期和批次写在 JSON metadata 中，不增加日期或 imported 路径段。完整 benchmark 仍在外部 benchmark 根目录；这里只保存用户要求审阅的有限样本及其证据。本次是人评资产不入 Git 惯例的明确例外，不扩大到其它对象的历史原票。

本批从冻结 Atlas 的 C5 节点全部 7 组评估、13 个 run 中取“预测失败或分类不等于 AIHOT”的并集，按 case_id 去重，共 **98 道**；最新 K 组全量 361 题中的 **70 道**在页面可单独筛选，其余 28 道仅在早期运行中有分歧。这些是定向错误分析数据，不是新的随机测试集。“错题”只指与归档 AIHOT 单标签不一致，不预判哪方正确。所有入选题的归档预测状态均为 ok。

来源 Atlas：`.label-serve/category-atlas-reusable-20260923-v4`，源 run 位于 `runs/content-enrichment/aihot-category-navigation/v1/`。每次 build 核对原件 SHA、逐题 input/gold/prediction/reason/prompt；同 ID 跨 run 输入或参照不同则拒绝混合。网页的“AI Radar / C5”是这些实验的历史输出，**不是重新调用当前生产 DeepSeek**；展开历史运行可看不同输出与各自实际模型名。

Codex 本批给出 95 道判断、3 道输入不足的 uncertain；这是非盲模型意见，没有人工权威。精确 Codex 模型 ID 未由会话暴露，记 null，不猜型号。Claude 尚未评价，页面明确显示待补充，不造票。AIHOT 原档没有分类理由，显示“未记录”，不让其他模型代写。

## 材料格式

### 2026-09-27 发布脱敏

GitHub GH013 密钥保护拒绝了本批最初的未发布提交：题 `f5649de57ae304626c244bd5` 的新闻正文和归档 user prompt 引用了 RubyGems 密钥字面值。经用户明确批准，Git 中的这两个文本字段将相关字面值替换为 `[REDACTED_RUBYGEMS_TOKEN]`，保留全部 98 题、标签、理由与历史模型输出。没有重新运行模型，也未修改本地原始 run。

`metadata.redaction` 记录处理日期、原因、原材料文件 SHA、原材料身份、题 ID、受影响字段、替换标记、原/新 input 摘要及原→新 prompt 摘要映射；`note` 解释关联边界。当前 input、prompt、模型意见的关联摘要绑定脱敏副本；模型意见是沿用既有判断，不是重新推理。`source.runs` 中的原件摘要仍定位未改动的原始实验文件。因此这一题的已存 prompt 是**脱敏展示副本**，不再声称字节级等同于模型当时收到的请求。

此前已渲染的页面与浏览器草稿不被本次 Git 修复覆盖。旧票仍须用其实际展示的旧材料快照导入，不可把摘要直接改成新版本；用户回票时按原有实质性规则显式关联。下文 build 命令读取原始 run，重建后须应用同样脱敏并更新关联摘要，不能将带密钥字面值的原始重建结果直接替换回 Git。历史“重新 build 与 canonical 一致”的验证记录描述的是脱敏前版本。

`category-review.json` 的 `metadata.format=ai-radar-category-review-material-v1` 是文件契约版本，独立于 benchmark 的 v1。

| 字段 | 语义 |
| --- | --- |
| `metadata.batch_id/created_at` | 材料批次 ID / UTC 生成时刻；不是新闻时间、模型推理时间或用户评价时间 |
| `metadata.target/benchmark/version/scope` | 对象、原 benchmark 身份与选题范围 |
| `metadata.label_semantics` | `acceptable_alternatives_for_single_category`：一个新闻仍预测一个类别，但用户可认定多个答案均合理 |
| `labels` | 六个稳定值：`ai-models` 模型、`ai-products` 产品、`industry` 行业、`paper` 论文、`tip` 教程、`opinion` 观点 |
| `source.candidate/runs/atlas_manifest_sha256` | C5 节点信息、逐 run 实际模型与原件摘要、来源 Atlas manifest 摘要 |
| `cases[].case_id/input/input_sha256` | 原题 ID、完整冻结输入、结构化 input 摘要；不把 gold 或意见拼入模型输入 |
| `cases[].aihot` | 原标签，`reason=null/reason_status=not_recorded` 表示无归档理由 |
| `cases[].prompts` | 以 prompt 结构化摘要为键，保存原 system/user 文本，不重新构造理由或 prompt |
| `cases[].c5_observations[]` | 每个实际覆盖该题的 run 的 `run_id/reason/label/status/prompt_sha256`；页面默认最近一次，历史全部可展开 |
| `model_reviews[]` | 各模型独立评价批次，格式见下节；只追加，不替换 AIHOT 或原 C5 输出 |

摘要使用 `evals._shared.assets.digest` 的规范化 JSON 算法。`material_identity` 对除 model_reviews 外的材料计算，追加 Claude 后不变；`material_sha256` 对整份材料计算，绑定用户实际看到的意见版本。两者由 validate 输出、render 写入 identity.json，不手工编造；原件文件 SHA 则是原始字节 SHA256。

## 给后续 Claude Code 的操作

从项目根执行以下命令；无需重新跑 DeepSeek。先读取每道题 input、C5 原 prompt 中的引用上下文，独立给出判断和理由；输入不足记 uncertain。已知其它意见时如实记录为非盲评。不要把 AIHOT、Codex 或多数票直接当作人工答案。

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/human_review.py validate \
  --material human-evals/content-enrichment/category-review.json
```

另写模型意见 JSON，结构如下（占位值必须换成上一步材料中的真实值；每项 reason 在最终判断前）：

```json
{
  "format": "ai-radar-category-model-opinions-v1",
  "review_id": "claude-review-unique-id",
  "created_at": "UTC ISO-8601",
  "material_identity": "validate 输出的 material_identity",
  "reviewer": {"kind": "model", "name": "claude", "model": null, "method": "填写实际模型身份、阅读范围、是否盲评；型号未知时保留 null"},
  "judgments": [{"case_id": "真实 ID", "input_sha256": "该题 input_sha256", "reason": "基于本条信息的判断依据及边界", "status": "judged", "acceptable_labels": ["paper", "tip"]}]
}
```

`status=uncertain` 必须给非空理由且 `acceptable_labels=[]`。允许分批追加；每批 review_id 唯一，同批同内容幂等，不同内容拒绝复用 ID。页面展示每个评价者对该题最新一条意见，旧意见仍可展开。模型意见只能 kind=model，不能声明为用户票。

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/human_review.py add-opinions \
  --material human-evals/content-enrichment/category-review.json \
  --opinions /path/to/claude-opinions.json \
  --output /path/to/category-review.next.json
```

输出路径必须不存在。核对新增意见后，在自己的隔离工作树中用新材料更新 canonical JSON、运行测试、提交 Git，再渲染新的独立目录。不改旧题 input、prompt、AIHOT/C5 观察记录。追加模型意见会改变整份材料摘要：**旧浏览器草稿/回票不能直接套到新版本**；旧票用 Git 中当时的材料版本导入，展示材料快照随票永久保留。已经归档的人评仍可按相同 input 身份复用，不需重新标注。

## 页面与用户票

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/human_review.py render \
  --material human-evals/content-enrichment/category-review.json \
  --output .label-serve/category-human-review-new
```

生成目录含 index.html、app.js、style.css、material.json、identity.json，可由本地静态 server 服务；交付遵循 user-scope remote-web-delivery，不公开发布。新闻中的 HTML 不直接执行；正文及模型理由按纯文本显示。

用户逐题多选类别、填写理由，可以先完成部分再“一键复制全部反馈”或下载 JSON。草稿存在当前浏览器 localStorage，**不是已经入仓**；导出文件可在同一材料版本恢复。未选项默认 pending，不自动接受 AIHOT 或模型建议。“暂不能判断”记 uncertain，不生成标签。

同源多页面保存通过浏览器 Web Locks 串行化，不同题的修改按逐题三方比较合并；同题出现不同修改时拒绝覆盖并提示分别导出两页，保留人工冲突供明确裁决。浏览器不支持 Web Locks、禁用存储或空间不足时明确提示未保存，当前页仍可导出；不要把内存草稿当成已经持久化。

导出票型 `ai-radar-category-review-ballot-v1` 顶层含 `batch_id/material_identity/material_sha256/exported_at`，`judgments[]` 对全体题记录 `case_id/input_sha256/status/acceptable_labels/reason`。只有 reviewed 且非空选择才是有效用户标签。收件 agent 确认这是用户交回的原票后：

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/human_review.py import-ballot \
  --material human-evals/content-enrichment/category-review.json \
  --ballot /path/to/user-feedback.json \
  --user-authority '用户在本会话明确提交的分类多选反馈' \
  --output human-evals/content-enrichment/reviews.json
```

沿用共享 `ai-radar-human-reviews-v1` 追加容器。批次 `metadata.kind=category-acceptable-labels`；`data.feedback_raw` 保存原票，`data.material` 保存实际展示快照，`data.category_judgments[]` 只包含明确用户选择。每条以 `(case_id,input_identity,field=acceptable_categories)` 定位，value 是可接受类别集合，reason 是用户原话，provenance=user。`data.annotations=[]`，防止旧的单标签消费者无声误读。模型意见即使与用户一致，也永远不是用户票。原票到手前不创建空 reviews.json。

复用入口 `accepted_categories(Path('human-evals/content-enrichment/reviews.json'))` 返回 `{(case_id, exact_input_digest): frozenset(labels)}`。新消费者可以判断单标签预测是否属于人工集合；同题同输入人工集合冲突则报错，不按导入顺序覆盖。这里精确 hash 用于冻结材料的安全关联；更多原始数据中只有 HTML、时间戳等非实质变化时，仍按项目的人评实质性适用规则核验后显式关联，不能因为 hash 不同宣布人评失效，也不能跳过核验直接套用。

**此轮仅建立存储/读取接口，不自动重算历史成绩。** 人工多可接受标签不同于 AIHOT 单标签；未来评测需显式记录人评批次摘要、采用集合命中定义，并保留 AIHOT 原口径。逐类 P/R 的多答案计数语义不能无声沿用原单标签 confusion matrix，需在消费者中明确后再报告。

## 重建与验证

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/human_review.py build \
  --atlas .label-serve/category-atlas-reusable-20260923-v4 --repo . \
  --batch-id c5-category-adjudication-20260927 --scope all \
  --output /path/to/new-material.json
PYTHONPATH=src:. uv run pytest -q tests/test_category_human_review.py
```

build 只生成冻结证据，不能代替已有 Codex/Claude 判断，也不能覆盖已有材料。扩展到新 Atlas 应生成新材料批次，再按题目及输入身份关联已有真实人评；不把本批 98 题硬编码为建题要求。测试覆盖模型/用户权威分离、两标签票导入、部分人评、幂等、冲突拒绝、错输入/错版本/缺题拒绝、Claude 追加以及可移植渲染。

### 2026-09-27 验证记录

- 新增测试 15 项通过：包含冻结 98 题、六类标签、正常/篡改输入与票、模型追加/用户冲突，以及真实 Chromium 两页面共享草稿的复现与回归。独立审查发现的多页整份覆盖问题已修复并由原 reviewer 定点复核通过。
- 真浏览器按读者路径核验：两类多选＋自由理由、重载恢复、下载 98 行完整回票、后端仅导入 1 条明确测试票、空浏览器导入恢复；测试票仅写隔离测试目录，未创建正式人评文件。不同题的两页修改保留为 2/98，同题不同理由则提示冲突，存储和各自导出都保留原判断。
- 页面观察覆盖标题/原文、四方意见、完整输入及原 system/user prompt、安全展开、人评控件、导航和导出。抽看短帖、长文、引用帖和输入不足共 6 条；桌面 1440×1000、移动模拟 390×844，移动文档宽度 390，无横向溢出。这不等于用户浏览器或其它浏览器已验收，也不代表 Codex 判断经过人评确认。
- 全部源材料重新 build 与 canonical 冻结证据一致（忽略新生成时间及后补模型意见）。`test_repository_hygiene.py` 的 runtime-owned 检查仍因基线已跟踪的 `data/aihot-reference` gitlink 失败（基线 eb7628f 中已存在）；不属于本改动，不宣称全仓测试全绿。未修改该独立历史资产，后续若处理归仓库运行时资产清理。
