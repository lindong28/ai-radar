# 分类实验图谱

本地静态工作台，用于回看 `aihot-category-navigation/v1` 的 A0–K2 历史实验。运行它不调用模型、不改 gold、不变更生产默认。源码与目录属于本 benchmark；生成的大型原文包不进 Git，历史 `runs/` 仍是权威原件。

## 重新生成

在项目根执行，`--archive-root` 指向实际保存历史 `runs/` 的 checkout，`--output` 必须是尚不存在的路径：

```bash
PYTHONPATH=src:. uv run python evals/content-enrichment/aihot-category-navigation/atlas/build.py \
  --archive-root /Users/lindong/research/ai-radar \
  --output /Users/lindong/research/ai-radar/.label-serve/category-atlas-new
```

只服务输出目录，不把仓库或 `runs/` 根目录作为静态文件根。用本地服务打开 `index.html`，不能以 `file://` 打开：页面需要读取 JSON。服务交付遵循 user-scope `remote-web-delivery.md`，监听 `0.0.0.0`，给用户回环链接与必要的转发命令。

`catalogue.py` 登记方案血缘、每个比较实际使用的 run、历史目标种子与归因线索；指标不手填。新增实验时添加节点/比较，生成新快照，不覆盖旧报告或原件。实线 `parent` 是骨架上游，虚线 `inspiration` 是部分思想借鉴，不代表叠加整个实现；实际配对对照由 assessment 单独指定。

## 数据与口径

- `build.py` 从原始 cases、predictions、prompts 与可选 first-pass/review 原件读取。调用现有 canonical scorer 复算，核对已保存的每个指标；早期未保存的逐类指标列入 `newly_computed_metrics`。比较双方同 ID 必须 input/gold 相等，组合 dev/regression 不得重叠。
- `manifest.json` 是该次报告的冻结目录、解释与原件 SHA-256。每个 `data/<date>_<time>.json` 是一份运行的只读投影；保留完整原始 input、实际 prompt、reason、首轮/复核证据和原始 conclusion，不输出 transport 配置或凭据。页面按需加载，不一次取全部原文。
- 浏览器从预测确定性计算准确率、六类 Precision/Recall、修复与回退；失败保留分母。全部题库为 361 题，开发常用 200 题。没有全量记录的候选明确显示未跑全量。
- 目标子集 `input` 比较实际 user prompt，用于正文/引用材料变化；`quote_system` 比较实际 system＋user，用于 B1 的引用贡献说明；`seeds` 仅为历史诊断种子，不是全部语义适用题；`all` 为全局干预；`unknown` 不伪造目标。复核视图以实际 `review-prompts` 路由为子集，同次首轮与最终配对。
- C5 全量对 B1 同时包含边界与全文变化，全文子集只是一种分层，不是独立因果验证。不同运行存在随机波动，已曝光的回归题不称盲测。没有逐题解释的分歧标成“归因尚未记录”，不能从 reason 反推真实内部因果。

## 浏览与反馈

选节点后可切换开发/全量/重复快照，以及条件复核的同次首轮视图。按修复、回退、仍错、目标子集、类别或原文搜索；逐题展开完整输入、两边实际 prompt 与理由。默认显示所选候选最新可用全量快照，否则显示开发快照。

反馈为浏览器暂存的待导入意见，**不直接成为人评金标**。页面顶部“复制全部反馈”一次导出全部意见；剪贴板不可用可下载 JSON。没有表态的题不默认认可。每条使用独立 localStorage key，多个标签页评不同题不会整批互相覆盖；同一比较的同一题以后写入为准。存储不可用会明示，离开前需要导出。

导出 `format=ai-radar-category-atlas-feedback-v1`：

| 字段 | 语义 |
|---|---|
| `benchmark` | 题库消费者身份与版本 |
| `exported_at` / `report_snapshot_sha256` | 导出时间 UTC / 当前报告清单快照身份 |
| `label_semantics` | 固定 `human_review_pending_import`，尚未导入 |
| `judgments[].candidate` / `case_id` | 被评论方案与原题身份 |
| `candidate_runs` / `baseline_runs` / `comparison_mode` | 实际比较运行；`first` 表示同一次首轮→最终，不是另一模型运行 |
| `reference_category` | 人评时展示的旧参考，不被意见覆盖 |
| `judgment` | `keep`、六类 slug、`uncertain` 或只有文字的 `comment_only`；不是自动 TP/TN 标签 |
| `reason` | 用户解释或优化建议，原样保存 |
| `snapshot_sha256` / `recorded_at` | 该条反馈所看报告的身份 / 记录时间 UTC；不同快照反馈不会冒充当前报告产生 |

用户交回后，按既有人评优先流程单独核验、归档和导入；本页面没有该写入入口。导出前从浏览器存储刷新，避免另一标签页的新意见被遗漏。修改候选和筛选的异步加载期间清空旧题，反馈绑定所看比较的固定身份。

## 视觉与验证边界

采用数据工作台形态：4px 间距阶梯，12/14/18/24/28px 字级，蓝色表示选择、绿/红表示修复/回退，数字等宽。导航、DAG、概述、指标、逐题证据分层；长正文与完整 prompt 按需展开，原文不改写；窄屏转为单列。没有外部 CDN 或前端依赖。

每次修改至少验证构建、配对失败路径、实际浏览器切换/筛选/逐题 prompt、人评刷新/导出，以及桌面和窄屏布局。原件缺失或 canonical 分数不一致应阻止生成，不静默跳过；静态页面的错误加载也不得显示为零分。本次不验证历史模型调用的可复现性，也不重新裁决 AIHOT 标签。

2026-09-23 交付验证：45 个方案、95 份运行、65 个比较；已有 canonical 指标逐 run 复算，1,143 项原件哈希核对一致。独立只读审查发现并闭环三项问题：B1 目标识别须包含 system 指令、加载切换不得错绑人评身份、多标签页不得整批覆盖反馈。定向复核无遗留 findings。

浏览器实测覆盖桌面 1440×1000 与手机 390×844，选择 B1/K2/J2/C5，切换开发/全量/同次首轮，筛选目标/回退/全部、滚动题目列表并核对实际四段 prompt。B1 开发目标题为 8、全量为 15；K2 全量修 15 / 退 21；J2 同次全量复核修 13 / 退 12。两标签页分别填写两条意见后，任一页导出均保留两条；刷新恢复与复制写入成功，剪贴板回读被浏览器权限拒绝，因此不宣称回读验证。验收用假反馈已清空，未进入任何人评资产。

页面验收探针覆盖候选标题、4 个指标、第一张题目卡、题目标题和两个反馈控件；它不遍历嵌套滚动列表，列表尾项由另一次真实浏览器滚动和选择验证。截图、可达性与这些有限操作不代表所有客户端、所有网络或所有交互组合均已验证。
