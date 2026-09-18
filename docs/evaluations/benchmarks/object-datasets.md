# 逐对象独立建题与扩展

> [Developer] · 2026-09-18 用户认可并要求实现。替代“四对象必须共用一份完整原始窗口”的新建题默认；历史 v1 题库和成绩保持原样。

## 入口与范围

| 对象 | 独立规则 |
| --- | --- |
| 新闻准入 | [news-admission / aihot-all-members / object-specific-v2](news-admission/aihot-all-members/object-specific-v2/README.md) |
| 可见评分 | [visible-score / aihot-visible-score / aihot-original-v3](visible-score/aihot-visible-score/aihot-original-v3/README.md) |
| 内容富化 | [content-enrichment / aihot-enrichment / aihot-original-v3](content-enrichment/aihot-enrichment/aihot-original-v3/README.md) |
| 精选成员 | [featured-members / aihot-featured-members / object-specific-v2](featured-members/aihot-featured-members/object-specific-v2/README.md) |

共享采集档案，不共享入题门槛。来源契约与所给 AIHOT 参照的实际出现共同确定来源范围，排除暂停、禁用、非主时间线、微信专用源；不导入旧 T5，也不先用我方 prefilter 或 score 过滤原始输入。仅控制共同来源，不把原始池裁成两站新闻 URL 交集。

2026-09-18 用户进一步批准仅 O2/O3 从 AIHOT 原文扩充；O1 不补仅有可见正例的样本，O4 不在本轮扩充，继续以连续窗口的完整候选组为全池规则建题依据。[ADR-20260918-7d0e](../../adr/20260918-7d0e-expand-score-and-enrichment-from-aihot-originals.md) 记录此边界。旧版本及其成绩保留，不把新输入范围倒灌为历史事实。

<a id="data-sufficiency"></a>

## 按对象判断数据充分性

用户于 2026-09-18 明确：按被评测对象的具体需要，判断实际收集到的数据（包括恢复、补采所得）是否足以用于该时间段的评测，不以“每个采集轮次从未失败”作为四对象统一门槛。下列三种结论分别报告，不能互相代替：**单条／字段可用**表示该题所需输入和参考已取得；**对象窗口完整**表示指定来源与时间窗内，该用途所需的候选、参考及时间特征已充分覆盖；**采集过程连续**表示调度轮次和来源执行没有断档。过程曾中断不自动使补采数据失效，恢复成功也不自动证明对象窗口完整。

| 对象／用途 | 恢复、补采后如何判断 |
| --- | --- |
| O1 新闻准入 | 仍从 Radar 未过滤候选建题，不只补 AIHOT 可见正例。按既有规则，以新闻时间（缺失才取实际抓取时间）±12h 的 AIHOT 参照判定：主集仍需完整、稳定的参照覆盖，无匹配且覆盖不足不得作负例；参照覆盖不足但已见正例时，按既有规则单列仅召回集。Radar 候选缺口主要限制样本代表性及对整窗 precision/recall 的外推，不因少一次抓取而一刀切丢掉已取得且标签可靠的题；有已知候选缺口时不能称整窗完整。 |
| O2 新闻评分 | 逐条有可用原始输入和已观察到的 AIHOT 可见分即可按当前规则建题；缺 Radar raw 时沿已批准的 AIHOT 原标题＋绑定真实正文 fallback。恢复晚到不是自动排除理由，但输入版本、身份和参考分数须可对应，不能拿补采后的改写内容冒充所需旧版本。 |
| O3 内容富化 | 与 O2 相同地核输入，再按 category/tags/title/summary/reason 各自的参考字段独立判断；缺一个字段不连带排除其它有效字段，缺失不补成空标签或空理由。已恢复的单条／字段可用，不要求其所在全天所有采集轮次无失败。 |
| O4 精选全池规则 | 需要连续时间窗口中的完整候选组、参考成员及规则实际依赖的时点特征。评测窗口最终集合时，补采若已充分覆盖这些需要，可以使用；重放历史实时决策时，还须知道各条新闻当时是否已到达、当时的内容／特征版本及所需状态，不能把后补数据倒填成当时已知。现有 pointwise-threshold 子集不是完整池，不替代此判断。 |

认定补采足以覆盖某用途前，核补采是否确实覆盖所需来源与时间范围，分页／游标是否到达所需边界、有无数量上限或截断，以及该用途所需内容版本、时间和参考标签是否齐全。已知缺口应指明影响哪些题、字段或整窗结论；不足时保留已可用部分并收窄结论。只见恢复前后各 15 条内容相同，可以支持这些已观察条目可用，不能证明历史窗口的全部候选均已收齐；也不要求为不可观测的“曾短暂发布后删除”做绝对不存在证明。结论以实际可取得、与用途相称的证据为限。

后续 Claude/Codex session 在现有报告或题库说明中写清：**时间窗与时区、评测对象及用途、可用单元数（新闻／字段／完整候选组）、是否足够该用途、具体残余缺口及影响**，并引用已有 manifest、原始参照或补采记录。既不把失败轮数直接当不可用题数，也不把最终内容足够写成采集过程无断档；不另造审批、评分或审计 schema。

当前逐对象 `build` 执行的是已实现的逐题规则，成功退出不是“最终内容已完整”的自动证明。旧 v1 仍可能按 cadence／coverage 拒绝冻结；本节不授权改完成标志、篡改 coverage 或绕过校验。若充分性结论与现有代码准入不匹配，应保留原始证据、指出具体不匹配并交回代码实施任务，不宣称文档已改变脚本行为。本节本身不重建旧题库或重写历史时间。

## 建题命令

在 AI Radar 仓库根运行，先 `uv sync --frozen`。全部离线，不读取 API 凭据、不调用模型、不改采集调度/网站 DB。

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py build \
  --raw-root /absolute/path/to/data/raw-capture \
  --reference /absolute/path/to/aihot-reference/manifest.json \
  --reference /absolute/path/to/aihot/windows/START--END/manifest.json \
  --start 2026-09-15T14:45:00Z --end 2026-09-18T00:00:00Z \
  --version 20260918-object-v2
```

路径是占位示例，时间必须带时区。`--start/--end` 选择 Radar run 的 started_at，左闭右开，允许非连续段；不是“新闻发布日期必须落入此区间”。AIHOT 每份参照使用它自己已验证的新闻窗口，O1 另外读取完整 API 遍历判断逐新闻 ±12h。多份 `--reference` 可重复传入：支持小时 interval 根/manifest，或标准 `windows/<window>/manifest.json` 日窗 v1/v2/v3；整个 archive 根不是输入。参照损坏、抓取未完成、游标链/标签证据错误会拒绝建题，不静默跳过。不要把 staging 目录当完成窗口。

默认生成四个对象。只建一个或几个时重复 `--target visible-score` 等；可显式传 `--data-root` 与 `--contract-path`。版本 slug 只允许小写字母、数字、点、下划线、横杠。任一目标版本已存在就退出，不覆盖历史。

## 仅扩充评分与富化：AIHOT 原文输入

显式增加 `--aihot-inputs`，并指定 `--target visible-score --target content-enrichment`。以下示例从旧合并版扩充，不重建 O1/O4：

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py build \
  --base ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-visible-score/20260918-merged-v2 \
  --reference /absolute/path/to/aihot/windows/START--END/manifest.json \
  --aihot-inputs \
  --target visible-score --target content-enrichment \
  --version 20260918-aihot-original-v3
```

没有 Radar raw 或 base 时，也可只提供 `--reference --aihot-inputs` 和这两个 target 新建 O2/O3。多个已完成窗口重复传 `--reference`；仍先验证原始证据，不支持把不完整或损坏归档当成可用输入。

| 输入选择 | 规则 |
| --- | --- |
| 已有 Radar 原始输入 | 同来源/URL 的 Radar raw 优先；原始多版本歧义仍排除，不用 AIHOT fallback 绕过 |
| 没有 Radar 原始输入 | 仅从获准的 AIHOT reference 取 `original_title` 和绑定同一 item ID 的详情页原文容器；两者须可用、身份及内容版本须无歧义 |
| 参考输出 | AIHOT 改写标题、摘要、分类、标签、分数和推荐理由只作 reference；不拿它们补原标题或正文 |
| 来源隔离 | AIHOT 衍生输入不写入 `raw-inputs.jsonl`，不能变成 O1 准入样本或 O4 完整池候选 |
| 授权继承 | `manifest.aihot_input_references` 保存获准作输入的 reference digest；`--base` 自动继承该名单，新增 reference 只有再次显式给 `--aihot-inputs` 才可作输入；未获准者仍可作标签参照 |

`--aihot-inputs` 授权当次全部参照，不只是最后一个 `--reference`。原文可用要求绑定外壳内确有文章或推文正文子结构；非空的“正文无法取得／仅有摘要”等说明不算正文，界面正文标签也不传给模型。原始 HTML 随 evidence 冻结，下次仅用 base 即可重建这批输入，无需原采集目录在线。后续新增 raw 时仍按 Radar 优先处理，同一 case 的输入改变记为 `updated`，不是新加一题。缺原标题/原文、身份绑定失败或多个有效内容版本等原因保留在排除记录，不由题库建设者猜补。

新输入证明的是 AIHOT 归档中可提取的原文文本，不证明它等于 Radar 当时会抓到的字节，也不证明原站全文未截断。必须保留输入来源，比较模型候选时用同版同题，不能将题量扩大解释成模型提升。`aihot-original-v3` 是规则/数据版本名称，数据 manifest 继续使用独立题集的 schema_version=2，O2/O3 的 policy 为 `object-specific-aihot-original-v3`。

`counts.input_origins` 分开统计 `radar-raw` 与 `aihot-original-detail`，`original_input_builder_sha256` 标识原文提取代码。manifest.window 仍只表示 Radar raw 选取时间范围，无 Radar 时为 null，不代表 AIHOT 历史题的覆盖跨度。查询最早新闻看 input.published_at，查询实际观察时刻看 provenance.observed_at，查询 AIHOT 参照窗口看冻结的 reference.window；三种时刻不能互换，更不能由包络声称连续采集。

## 后续 session 默认：合并＋去重＋按当前设计检查有效性

扩展题库使用可重复的 `--base`，不能把各版本题数相加，也不能直接拼接 `cases.jsonl`。脚本先校验旧版本冻结资产，从中读取过滤前原始输入（包括当时未入题的新闻）和 AIHOT 原始参照，与本次新增原始数据合并，然后调用当前四对象建题规则。旧题仅用于计算变化，不直接作为新版本参考答案的权威。

仅合并已有版本、不增加采集数据：

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py build \
  --base ~/research/video-eval-arena/data/benchmarks/ai-radar/aihot-all-members/news-admission/20260917-0700-1200-v1 \
  --base ~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-all-members/20260918-object-v2-r1 \
  --version 20260918-merged-v2
```

日后扩展时，以最近一次合并后的版本作为 base，再提供新增 Radar 范围与已完成 AIHOT 参照。以下新增路径、日期和版本名须替换为实际值：

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py build \
  --base ~/research/video-eval-arena/data/benchmarks/ai-radar/news-admission/aihot-all-members/20260918-substantive-v4-r1 \
  --raw-root /absolute/path/to/data/raw-capture \
  --start 2026-09-18T00:00:00Z --end 2026-09-19T00:00:00Z \
  --reference /absolute/path/to/new-aihot-window/manifest.json \
  --target news-admission --target visible-score --target content-enrichment \
  --version 20260919-extended-v2
```

`--base` 接受 schema1/schema2 的任一对象叶子目录或其 manifest，并自动读取该题库根下同数据版本的所有现存对象兄弟叶子；`--target` 只控制输出对象，不限制这些历史证据。只补 AIHOT 参照时可省略 raw 三参数；补 raw 时 `--raw-root/--start/--end` 必须一起给。重复传入同版、重叠版本或“合并版＋其祖先”不会重复计题。无 `--base` 仍是从指定原始档案开始的新建，不自动扫描目录挑最新版本。

| 环节 | 固定语义 |
| --- | --- |
| 原始身份 | 同来源＋规范化 URL 合并；X 使用推文 ID，保留所有不同原始载荷。载荷不同不等于实质内容不同，版本规则见下节；代表记录取最早观察，不按拟合效果挑正文 |
| 题目身份 | 对象＋case_id＋字段；O3 五字段分别计题；dev/regression 按身份 URL 固定划分，X 别名不得跨 split |
| 当前有效性 | 按当前来源契约、完整参照证据及各对象规则重新建题；O1 仍区分主集、仅召回补充集和未知；其它对象只排除各自相关的实质内容／参考身份歧义，字段参考冲突只排该字段 |
| 旧题处理 | 可保留、更新、移出，也可把原来未知的新闻新增为题；不会为了累加数量恢复当前无效的旧题。移出只发生在新版本，旧题库和旧成绩不修改 |
| 原始数据保留 | 当前不入题、来源暂时不在范围的已冻结输入仍留在新版本 evidence；不以 prefilter/scorer 结果筛原始数据，不补造旧 T5 或缺失输入 |

完全相同的证据和当前规则重建，题目内容应相同；生成时刻、版本和变化报告可不同。新增证据可能补足未知，也可能暴露冲突，所以“累积原始证据”不等于“有效题数只能增加”。规则升级仍走设计变更，不在扩展数据时悄悄改规则。

## 输出与校验

```text
~/research/video-eval-arena/data/benchmarks/ai-radar/
  <target>/<benchmark>/<version>/
    manifest.json
    cases.jsonl
    excluded.jsonl
    recall-only.jsonl       # 仅 O1
    field-subsets.json     # 仅 O3，各字段的 case_id
    merge-summary.json     # 给了 --base 才有：去重后的题目变化计数
    changes.jsonl          # 给了 --base 才有：逐题/字段 before、after、状态、移出原因
    evidence/              # 本次第一个目标持有，其它叶子相对引用
      raw-inputs.jsonl
      raw-manifests.json
      inventory.json
      sources.json
      parents.json         # 给了 --base 才有：直接输入版本路径及 manifest SHA
      aihot/<reference-digest>/...
```

共享证据不依赖 O1 有题，也支持仅构建 O3。移机须一起移动本批所有叶子，或者保留 manifest.shared_evidence 指向的 owner；相对路径允许整个题库根一起移动。不要单独删 owner 叶子。

```bash
PYTHONPATH=src:. uv run python scripts/build_eval_datasets.py validate \
  ~/research/video-eval-arena/data/benchmarks/ai-radar/visible-score/aihot-visible-score/20260918-object-v2
```

validate 校验问题集和冻结证据的字节摘要、对象/版本路径、题数与身份；不证明模型质量或采集连续性。退出非零表示未完成。零题版本允许保留，并明确显示 main=0，不称为可评测样本充分。部分输出后发生 I/O 失败时该版本无完成保证，使用新版本重建，别手工补一份 manifest。

每条 cases 包含 case_id、split、input、reference、provenance。input 是 Radar 过滤前新闻或本页明确批准的 AIHOT 原标题/原文及必要来源元数据，reference 才是 AIHOT 标签。按来源/URL 固定身份；同 URL 在各对象、各版本使用相同 split，不把重复新闻随机拆到两边。O3 用 field-subsets.json 选字段，不将缺值当错误或空值。排除记录按题/字段记录，因此 excluded 数不一定等于独立新闻数。

每次扩展后，后续 agent 应逐对象读取 `merge-summary.json` 和 manifest.counts，遇到 updated/removed 时查 `changes.jsonl`，然后运行 validate。`added` 才是相对所有 base 去重并集的新增题；`retained` 是输入、参考、主集/补充集身份均相同；`updated` 是该身份仍在但上述内容发生变化，或旧版本间存在不同状态；`removed` 是按当前规则不再入题。`previous_unique_questions + added - removed = current_unique_questions`，后者也等于 `added + retained + updated`。`duplicate_parent_questions` 是历史各叶子计题次数减去身份并集大小，不是新闻去重数。O1 的变化摘要包含仅召回补充题，主集数量必须另读 manifest.counts.main；O3 摘要按字段题数，不是新闻行数。跨对象相加只能叫评测任务数，不能叫独立新闻数。

## 规模、版本与原始证据

每轮先通过现有 read_run 校验压缩原件及逐源计数，304 需验证并解析 payload_ref；失败来源/未完成轮次记录在 inventory，不让它们拖掉其它来源的已取得原始新闻。已完成轮次的损坏则中止，不静默删除。串行扫描共享本机磁盘，每次只展开一轮压缩包；内存随独立输入版本而不是重复抓取总行数增长。重复抓取只变 fetched_at 时保留最早观察的原始记录及次数，不复制每轮同一正文。原始采集档案本身不被修改。

冻结的是去重复后的原始输入版本、逐轮生产者 manifest、来源契约和必要 AIHOT 原页，不是完整 Radar 抓取档案副本。它足够复查这批题实际输入/参考，完整轮次重放仍使用原始 raw-capture。manifest 保留重建参数和 builder SHA；重建原始题时须使用相同代码/契约/采集档案，不能用现在的来源配置冒充旧配置。

合并版冻结合并后的原始输入与全部使用到的 AIHOT 参照，因此下次扩展只需该版及其共享 evidence，不依赖最早的采集目录或祖先题库仍在线；parents/rebuild 的原路径是溯源，不是下次扩展的必需读取路径。`manifest.rebuild.bases` 记录直接输入版本，`builder_sha256` 和 `merge_builder_sha256` 分别标识建题/合并模块。按当前规则重验与原样复现旧规则是两件事。

合并后 `inventory.windows` 保留输入时间段，`window` 只是最早到最晚的包络，不证明中间连续。`unique_raw_news` 是冻结的来源/URL 身份数，`eligible_source_raw_news` 是当前来源范围内的身份数，均不是有效题数。紧凑旧档案无法恢复重叠版本的精确抓取观察总数，故合并后的 `raw_observations=null`，逐新闻 observations 只为下界，不累加成精确总数；采集连续性仍须独立审计，不能靠 run_count 或题数宣称无断档。

### 实质内容版本与 URL 身份（2026-09-18 用户修订）

规则由 `evals/_shared/identity.py` 维护，manifest 以 `identity_policy=same-source-x-post-id-substantive-v1` 和 `identity_builder_sha256` 标明执行口径。此处替代旧的“除 fetched_at 外任何字段不同都算版本不同”，不追改旧冻结题库。

| 变化／对象 | 处理 |
| --- | --- |
| X URL 写法 | 同一来源下，x.com / twitter.com 的账号路径、i/web/status 路径、分享查询参数及 photo/video 尾缀，按同一个数字推文 ID 配对、去重和分 split；题目 input.url 按来源契约输出 `https://x.com/<handle>/status/<id>`。原始 URL 仍在冻结证据里 |
| 其它 URL | 保留既有身份边界，不泛化删除 query、不跨域猜正文相等、不跨来源合并转载 |
| 仅 published_at / fetched_at 变化 | 不构成内容多版本；原时间仍保留，O1 的 ±12h 匹配和精选时效用途照常读取，不将补采倒填为历史已到达 |
| 仅 HTML 或 extra 中来源标签变化 | 不构成内容多版本，正文比较使用 content_text；若提取出的正文也变化，仍按正文变化处理 |
| 标题排版／标点 | 比较时统一 NFC、全角 ASCII、空白、引号及常见分隔／句末标点；不改模型所见原标题。数字、小数点、负号、上标、`!=` 等运算符和实际文字差异仍保留，不用模糊语义匹配吞掉实质差异 |
| O2 评分 | 比较新闻身份、规范化标题、完整 content_text 和 author；不截断正文来躲开变化 |
| O3 富化 | 比较新闻身份、规范化标题和完整 content_text；不读取 author 的富化输入不因作者字段变化连带排除 |
| O4 局部阈值题 | 与评分的内容版本判据一致，时间字段仍保留；此规则不证明候选组完整，也不扩充 AIHOT-only 样本 |

Radar 配对对象要求各自仅有一个实质输入版本，且配对的 AIHOT item ID 唯一；AIHOT 原文 fallback 使用同一实质内容判据，代表记录也取最早观察。真正多版本仍排除，不能按参考答案挑一个正文。多参照同字段的参考值冲突不按“最新最好”择一，只排该字段。跨站原文完全一致仍不可直接证明。

历史 `--base` 先验证原冻结 key 和摘要，再按新身份重建；历史题在 changes 中也按规范身份对齐，不把 URL 别名改写计成“删一题、加一题”。X 的历史 split 可能随规范身份迁移，比较模型候选必须重新使用同版题库，不能混用旧 split 或把它声称为从未调参的 holdout。旧题库只保留在独立历史版本目录供复现，移出的题不留在新版本 cases 中；日后扩题必须指定当前版本，而非直接拼旧 cases。

## 执行边界与后续接线

schema_version=2 表示独立逐对象题集；旧 schema_version=1 的共享池 runner 只接受旧题库。当前新脚本负责建题与校验，不运行模型，也不实现网站逐条评分映射或更换精选规则。这些推理改造仍由后续评测/优化任务实施；不能把建题完成写成整套新推理链已跑通。

O1 可直接评价 prefilter；O2 需要 scorer + 固定逐条展示映射；O3 各字段独立，文本判官仍需用户校验。O4 旧 v2 仅用于我方固定预测后的逐条 threshold，不代表生产精选链；生产规则涉及池排序、时效窗口、来源配额及分数映射，后续全池评测仍须连续窗口的完整候选组，本次不借 AIHOT-only 新闻扩充。自动指标定义继续由各 evals 叶子的 metrics.json 维护，不增加新的治理分数或达标线。
