# O3 · 分类研究方案履历

> [Developer] · 2026-09-27 起登记 O 阶段；实验标记不等于生产正式版本。历史 A–K 谱系见[分类图谱](../../../evals/content-enrichment/aihot-category-navigation/atlas/README.md)，M/N 记录见[状态](status.md)。本文件不批量重建历史身份。

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
