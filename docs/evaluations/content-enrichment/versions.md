# O3 · 分类方案履历

> [Developer] · 2026-09-27 起登记 O 阶段；实验标记不等于生产正式版本。历史 A–K 谱系见[分类图谱](../../../evals/content-enrichment/aihot-category-navigation/atlas/README.md)，M/N 记录见[状态](status.md)。本文件不批量重建历史身份。

## 分类正式版 0.1.0（2026-09-29）

用户认可来源：本轮明确要求“挑选目前为止的最好的结果，作为线上使用的主方案，放到 main branch，对目前为止做的所有评测和优化工作进行收尾”。按该授权选择 V2 Pro 为首次正式分类版本 **0.1.0**；正式号不改变 benchmark `aihot-category-navigation/v1` 的输入版本，也不是新的实验候选或新增模型成绩。

| 身份项 | 记录 |
|---|---|
| 实验身份与正式号 | `id=V2`，`formal_version=0.1.0`；后续采用状态不覆盖下方研究阶段的历史身份 |
| 直接来源与正式比较基线 | `parents=[V1]`，V1来源U2；`comparison_baseline=null`，此前没有已确认生产认可的正式分类版本，U2只是具名研究对照 |
| 实际研究运行 | `runs/content-enrichment/aihot-category-navigation/v1/2026-09-29/01-54-43/` 与 `01-57-51/`；metadata在 `experiments/` 同分区 |
| 运行源码与材料身份 | 源码基底 `15037b4be8284cdea1d3febbfe14f8c75622b083` 加当时prompt改动；run冻结的源码/规则/材料SHA才定义完整实测对象。研究收尾提交 `46026594e6d9403a1ae64596d35de57cb8c18f85` 是 post-run 映射，不冒充运行时干净commit |
| 生产实现定位 | [category_release.py](../../../src/airadar/enrich/category_release.py)、[category_v2.txt](../../../src/airadar/enrich/category_v2.txt)、[runner_v2.py](../../../src/airadar/enrich/runner_v2.py)；这次生产适配的提交由 Git 履历定位，不用研究commit替代 |
| 调用与材料 | Ark Pro `personal_ark::deepseek-v4-pro-ga-260813` / thinking enabled / high /32768/T0；documents材料、无few-shot、单分类call、失败至多3次尝试 |
| 成绩和限制 | 两次46/48（95.83%），同48道已见人评；U2共同口径45/48、44/48，配对分别修3/退2、修4/退2。不能外推未见集或361题，六类P/R全部90%未达；逐类及两道错误见 [状态](status.md#v阶段类别准入与主分类选择分层) |
| 采用与部署 | 已作生产主方案采用决定并接入默认 enrich_v2；线上部署未验证，未据此声称已上线。其它富化字段保留既有实现，is_opinion为主类映射而非新增已评测对象 |

复核路径：以上两轮 `human-priority-scores.json` 为当前人评确定性计分，`scores.json` 是原AIHOT口径；`prompts.jsonl`、`predictions.jsonl`、`attempts/`及冻结`human-reviews.json`保留实际输入、理由和人票。共同口径比较为 `2026-09-29/01-44-28/v-final-comparison.json`；实际输入/标签条件、复跑命令与成本缺口见 [状态 L2](status.md#l2资产成本与复用)。本轮不重写这些原件，不将新生产适配的单元/接线验证计成新的48题模型评测。

## V阶段（2026-09-29，规则两层收敛）

以下为正式采用前的历史研究记录；后续0.1.0采用不回写当时的`formal_version`与运行元数据。

`formal_version=null`，无生产认可。源码基底`15037b4be8284cdea1d3febbfe14f8c75622b083`，实际rubric与源码SHA以run冻结身份为准；最终提交仅为post-run映射。人票仅ATLAS经用户批准增加paper接受标签，所有比较使用同一新口径：U2两次45/48、44/48；历史U2重复43→44仅为改票收益。完整规则、指标、残余错题和复跑命令见[状态V阶段](status.md#v阶段类别准入与主分类选择分层)。

| id | parents | 实际改动 | UTC run / 人评 |
|---|---|---|---|
| V1 | `[U2]` | 六类证据准入与主类选择分层，条件冲突取代混写优先级 | `2026-09-29/01-44-28` 39/48；`01-48-01` 43/48 |
| V2 | `[V1]` | 保留局部方向，修复新模型资格、科学发现、工程讲解、事件/研究等六处定义漂移 | `2026-09-29/01-54-43`、`01-57-51` 均46/48；下一轮研究主方案 |

两版各两次全48已见题运行，均单Pro/high/32768/T0，与R8材料一致；没有Flash或未见题读数。V2两次都相对V1首轮修8退1，相对U2首轮修3退2。未达到六类P/R全部90%，两道稳定残余错误未掩盖。下方历史口径不回写覆盖。

## U阶段（2026-09-29本地，固定48道人评题）

`formal_version=null`，没有生产认可。所有候选保持 R8 冻结正文与引用、当前人评、Pro/high/32768/T0/单调用不变，源代码基于 `8ad967e54581209bbc98358c84ec73fce1349f78`。新增规则来自实际 reasoning 与人评理由，不加入题号、专名或答案示例；完整读数和适用边界由[状态U阶段](status.md#u阶段pro-全48道人评题归因优化)单一持有。

| id | parents | inspiration / 实际改动 | UTC run |
|---|---|---|---|
| T2 full48 | `[T2]` | 仅扩到完整人评集，38/48 | `17-10-34` |
| U1 | `[T2]` | 五条错题：组织行动、机构部署、独立分析边界；43/48 | `17-21-54` |
| U2 | `[U1]` | 分析深度、逆向工程讲解、框架/模型边界；首轮45/48、冻结重复43/48；研究对照，非生产版本 | `17-26-15`、`17-56-35` |
| U3 | `[U2]` | U2剩余与回退：主体优先、研究摘要与事件机制边界；42/48 | `17-42-44` |
| U4 | `[U2]` | inspiration=`U3`论文/行业局部段落；不带全局主体和观点修订；44/48 | `17-45-44` |
| U5 | `[U4]` | 修事实含义与独立分析、代码实验作用、调查限定与研究的边界；42/48 | `17-50-00` |

以上运行日期均为UTC `2026-09-28`；路径前缀为 `runs/content-enrichment/aihot-category-navigation/v1/`，metadata在`experiments/`同分区。全48研究成绩不替代历史361题整体对照，也不证明切回Flash仍有同样收益。

## T阶段（2026-09-28 UTC，S1 Pro reasoning 后的局部 prompt 修订）

`formal_version=null`，没有新增正式生产认可；研究对照为S1原轮及同期重复，而不是自动将S1设为正式基线。源代码基于`77578e97f2df3e9052b1544fdd85952d08792849`，每个run冻结实际rubric与代码/材料身份。全部为相同六道已见人评题，Pro/high/32768/T0、单调用；无输入、gold或生产变更。

| id | parents | inspiration | 实际改动 | UTC run / 人评 |
|---|---|---|---|---|
| T1 | `[S1]` | S1三错公开reasoning | `category-t1.txt`收窄发布/研究、引用/方法、机构/产品条件 | `15-54-47`：2/6 |
| T2 | `[T1]` | T1失败与回退reasoning | `category-t2.txt`再修研究原创性、方法独立性、平台能力新增判断 | `15-58-30`：6/6；冻结重复`15-59-45`：6/6 |
| S1 repeat | `[S1]` | T2重复对照 | 原R8 rubric与同一Pro配置，不改prompt | `16-01-22`：3/6 |

运行前缀为 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-28/`，metadata在`experiments/`同分区。T2对原S1及同期S1都修3/退0，但修复的题目集合不同；局部收益不外推48/361题或Flash。T2为后续扩覆盖研究候选、不是生产默认；完整逐题、成本及复跑入口见[状态T阶段](status.md#t阶段s1-pro-reasoning-驱动的冲突边界修订)。

## S 阶段（2026-09-28，六题计算配置诊断）

S1：`parents=[R8]`，`inspiration=[]`，`formal_version=null`，正式`comparison_baseline=null`。用户指定Pro/high/32k；固定R8 prompt与材料，仅联合切换Ark Pro、thinking/high与32768上限，T0不变。源码`b8ae9aa93967964dc5677e27d6c5efda3725c9cb`，冻结身份见run。六道人评开发题从R8同题2/6到S1的3/6，修2/退1；不能与R8全48题41/48直接比百分比。`runs=[2026-09-28/15-16-12]`，路径前缀`runs/content-enrichment/aihot-category-navigation/v1/`；元数据对应`experiments/`同分区，逐题配对在run的`comparison.json`。未晋级或部署；结果、剩余错题归因、实际调用与复用方式见[状态S1](status.md#2026-09-28s1prohigh32k-六题诊断)。

## R 阶段（2026-09-28，最新人评）

没有正式生产认可，`formal_version=null`、正式`comparison_baseline=null`；用户指定P1为修复基础，本轮辅助对照是P1原参数重复Q0 `2026-09-27/20-16-47`，最新9票重算43/48。不是沿用其旧人票39/48直接比较，也不是P1新调用。以下运行位于`runs/content-enrichment/aihot-category-navigation/v1/2026-09-28/<UTC>/`，同分区`experiments/`保存metadata；全部固定48已见人评题，仅首轮smoke为其中9题。实现未晋级生产。

| id | parents | inspiration | 改动与UTC run | 新人评结果 |
|---|---|---|---|---:|
| R1 | `[P1]` | 最新9票 | `category-r1.txt`补政策职权、引用主体、工程解释；`14-00-07`、`14-01-21` | 7/9；40/48 |
| R2 | `[R1]` | R1回退 | `category-r2.txt`区分独立评论；`14-03-27` | 41/48 |
| R3 | `[R2]` | R2条件矛盾 | `category-r3.txt`主要交付物构框；`14-10-43` | 37/48 |
| R4 | `[R2]` | O4复核机制 | R2＋`category-r4-review.txt`，全题第二调用；`14-13-06` | 38/48 |
| R5 | `[R2]` | R4初判锚定 | `category_evidence.py`先事实后分类，`--evidence-first`；`14-17-22` | 41/48 |
| R6 | `[R2]` | P1研究边界 | `category-r6.txt`恢复被删研究段；`14-19-15` | 41/48 |
| R7 | `[R6]` | R5实际事实提取 | `category-r7.txt`调整模型发布和研究/教程条件；`14-20-39` | 39/48 |
| R8 | `[P1]` | R1/R2/R6局部修复 | `category-r8.txt`保留P1骨架，最小整合四处；`14-23-49` | 41/48 |
| R9 | `[R8]` | 条件执行偏差 | 同prompt，thinking/low、8192上限；`14-28-37` | 39/48 |

实验期间Git基底为`c39a3ff`＋未提交的本轮改动，非干净Git身份。源代码与实际配置身份由各run冻结文件SHA证明；本轮源码提交只是post-run映射，不替换历史身份。R1–R9均启用最多3次失败尝试；重试及显式人票修订为已验证工程修复，分类prompt和额外调用未证明整体超过43/48，不设默认。详细修复/回退、六类P/R、剩余问题与归因边界见[状态R阶段](status.md#2026-09-28r-阶段最新人评与-p1-缺口修复)。下方Q/P/O表保持当时人票口径。

## Q/P/O 历史窗口

本轮 `comparison_baseline=null`：没有已取得用户生产认可的正式分类基线；M2/O1 都只是具名研究比较对象。`formal_version=null`，生产认可来源为空，所有 O 节点未部署、不切默认。用户授权依据为 [ADR-8ea1](../../adr/20260927-8ea1-repair-category-source-material.md)。源节点 M2 的实现、来源和三批原件由[状态中的 M 阶段](status.md#2026-09-27m-阶段条件类别冲突已终态)定位，不能把它登记为无父根节点。

| id | parents | inspiration | 实际改动与 runs（UTC，2026-09-27） | 当前结论 |
|---|---|---|---|---|
| Q0 | `[P1]` | `[]` | 原参数同期重复，`20-16-47` | 39/48，不把重复波动称改进 |
| Q1 | `[P1]` | `[]` | 仅max_tokens 700→8192，`20-17-57` | 38/48，未胜同期Q0 |
| Q2 | `[P1]` | `[]` | 仅temperature 0→0.5，`20-19-18` | 38/48，教程局部修复被其他回退抵消 |
| Q3 | `[Q1]` | `[]` | 同8192，启用thinking/low，`20-20-18` | 36/48，不采用 |
| Q4 | `[Q3]` | `[]` | 仅effort low→high，`20-23-29` | 32/48，保留Helix局部解释、不采用配置 |
| P1 | `[O3]` | `[]` | 只改材料呈现；`--material-layout documents`，`15-07-59`、`15-11-23` | 37/48、38/48；后续研究起点，未晋级生产 |
| P2 | `[P1]` | `[]` | 同材料，仅追加 `--evidence-reason`，`15-09-30` | 33/48，相对P1首轮修2/退6；不采用整套 |
| O1 | `[M2]` | `[]` | rubric仍M2，仅修输入读取与补全；中间材料窗口 `13-39-24`，final材料窗口 `13-48-38`、同配置重复 `13-57-42` | 中间35/48，final两次36/48、33/48；输入窗口分别保留，不将净增或重复波动归因为单一补全组件；保留为研究控制 |
| O2 | `[O1]` | `[]` | final输入不变，`category-o2.txt`重写主体与条件冲突；`13-51-06` | 34/48，相对final O1修5/退7；局部修复与回退并存，不采用整套替换 |
| O3 | `[O1]` | `[O2]` | 回到M2骨架，只改四条冲突条件，借鉴O2的工程讲解、交互工具、研究报告与部署运行边界；`13-53-09` | 34/48，未超过final O1，未证实稳定收益 |
| O4 | `[O3]` | `[]` | 原规则上加每题证据分析与第二调用复核；`category-o4-routing.txt` / `category-o4-review.txt`，`13-55-15` | 95次调用，初判33/48、最终34/48，复核修1/退0；相对final O1修4/退6，不晋级 |

各 run 的完整路径为 `runs/content-enrichment/aihot-category-navigation/v1/2026-09-27/<UTC>/`，实验登记为 `experiments/content-enrichment/aihot-category-navigation/v1/2026-09-27/<UTC>/metadata.json`。原件中的 `started.json`、`identity-frozen.json` 和 metadata 绑定逐文件 code SHA256、实际 rubric/调用参数、cases/prompts/body-context/human-reviews 摘要。这几份原件本身没有Git commit字段；主线程提供的运行前后Git观察为 `894e80244b10a73a1f65fc77de6afa9b5cf183d5` 加未提交改动，该commit只是基底，不是完整被测实现。实际实现仍以运行source哈希为准，最终提交仅作post-run provenance映射，不能冒充运行时干净commit。

O1 final同配置重复不分配新实验身份；两次均值71.875%只来自2次，O2/O3/O4单次34/48落在该观察范围内，无稳定优胜证据。全部运行使用同48道人评开发题，47单答案用于六类P/R，1多答案按集合命中参加总体；失败仍在分母，原 `scores.json` 不是本轮人评口径。具体读数、修退原因和采用边界归[状态](status.md)，准备/复用参数归[v1入口](aihot-category-navigation/v1/README.md#2026-09-27o-阶段输入材料修复)。后续补证据注明日期，不覆写两个 O1 输入窗口的历史身份。
