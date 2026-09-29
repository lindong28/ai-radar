# 分类实验提示

V2是当前Pro人评48题的下一轮研究主方案：`category-v2.txt`先判类别证据资格，再按贡献作用解决主类冲突；两次冻结运行均46/48。V1保留两层重写的首版，V2基于V1修定义漂移，不逐题拼接预测。U2继续作为对照，新共同人票下两次45/48、44/48（旧第二次43→44仅为用户批准ATLAS改票）。规则、成本、目标/剩余题及两道残余错误见[分类状态V阶段](../../../docs/evaluations/content-enrichment/status.md#v阶段类别准入与主分类选择分层)。六类P/R未全部90%，不是生产默认或未见题结论。

`category-u5.txt`在U4局部组合上修复独立分析、研究代码和调查深度的判断捷径；它与U4同属显式研究候选，最终选择与冻结重复读数仍以下文链接的状态文档为准。

`category-u4.txt` 是 U2 加 U3 的论文/行业局部边界，不包含 U3 的全局主体与观点条款；不能由 U3 的局部修复数推定组合后的成绩，按 U4 自身全48运行记录判断。

U阶段（2026-09-29本地）固定全48道人评题及R8完整材料、Ark Pro/high/32768/T0：`category-u1.txt` 基于T2修组织行动与独立分析条件；`category-u2.txt`保留U1并修工程讲解、框架/模型及分析深度；`category-u3.txt`保留U2并修主体优先、研究摘要和事件机制条件。均为研究候选，未改变生产默认；结果、已知参考前提与复跑要求见[状态U阶段](../../../docs/evaluations/content-enrichment/status.md#u阶段pro-全48道人评题归因优化)，父方案见[方案履历](../../../docs/evaluations/content-enrichment/versions.md)。

T阶段（2026-09-28 UTC）从 S1 Pro 使用的 R8 rubric 派生 `category-t1.txt`，明确发布/研究、引用/方法、机构变革/产品的条件；`category-t2.txt` 再修研究原创性、教程独立性和新增功能的判断门槛。模型仍为 Ark Pro/high/32768/T0，材料不变，无 few-shot；均为研究 prompt，不是生产默认。实际六题首轮、冻结重复及同期对照统一见[状态T阶段](../../../docs/evaluations/content-enrichment/status.md#t阶段s1-pro-reasoning-驱动的冲突边界修订)。后续复用必须同时读取[方案履历](../../../docs/evaluations/content-enrichment/versions.md)，不能仅由 prompt 文件名推断模型或输入。

2026-09-28 R阶段在P1的材料呈现上修订分类条件，全部为研究候选而非默认：R1补政策职权、引用主体和工程解释；R2收窄独立评论；R3改为主要交付物构框；R4使用R2加`category-r4-review.txt`事后复核；R5使用R2加`--evidence-first`先整理事实再分类；R6在R2中恢复P1研究边界；R7收紧模型发布并调整研究/教程条件；R8回到P1/O3原骨架做最小整合修复。R9沿用R8，仅改变思考配置。`category-r4-review.txt`只供第二阶段，不能作为完整rubric；R5没有单独rubric文件。实际48道人评结果、配对修复/回退和采用边界见[状态](../../../docs/evaluations/content-enrichment/status.md)，父关系见[履历](../../../docs/evaluations/content-enrichment/versions.md)。下文较早“当前”判断只代表相应历史阶段。

J阶段新增`category-j1-routing.txt`和`category-j2-routing.txt`，只能通过`--routing-guidance`用于首轮，不是完整rubric。第二轮继续I2指导。J2按材料结构触发，冻结全361题184次复核覆盖47/80错，但仅281→282（13修/12退），不替换C5整体主方案或生产A0；保留该路由作为后续复核研究组件。J1/J2开发162/161对，同期C5为165/200；全部已见，双90未达。逐类指标、成本、归因和下一研究路线见[状态](../../../docs/evaluations/content-enrichment/status.md#2026-09-22j阶段材料结构路由)。下方I/H等均为历史阶段记录。

I阶段最新研究组件是`category-i2-review.txt`，仅供`--conditional-review --blind-review --review-guidance`的第二次调用使用，不能代替完整`--rubric`。保留C5首轮，用传播形式/承载证据分离、短论文证据和研究/实践边界指导复核。冻结361题首轮284→最终286，修6/退4，77错只路由12个；已见回归净退2，故不整体晋级。I1无guidance开发161/200、I2有guidance159/200，首轮也变动，不把两者差值单独归因guidance。整体主方案仍C5，生产A0不变；下方H/G/F等为历史记录，详见[状态](../../../docs/evaluations/content-enrichment/status.md#2026-09-22i阶段条件盲复核)。

H阶段最新整体对照仍C5：同期全361为286对（79.22%），H3为274对（75.90%），13修正/25退化。`category-h1.txt`是真正的C5＋F3/G3局部边界实现；H2保留它并先识别作者动作，H3取消错误的原创作者门槛、按承载证据分类。三个开发arm156/160/156对，H3独立测评等收益可复用但完整方案未晋级；下方G阶段294对属于历史读数，不覆盖本轮。三者均单Flash/全文/as-of引用贡献，不加第二call或新字段，不是生产默认。复现见[入口](../aihot-category-navigation/README.md#d阶段候选复现)，逐例归因和下一结构研究见[状态](../../../docs/evaluations/content-enrichment/status.md#2026-09-22h阶段实际组合局部修复)。

G阶段最新整体主方案/对照仍为C5（本轮全361为294对），F3/G3保留局部研究，不直接采用完整G3。`category-g1.txt`增加正面证据及reason/decision一致性，`category-g2.txt`重构读者贡献定义，`category-g3.txt`用F3骨架迁回技术讲解、短研究和产品附安装的局部边界；同200题155/151/158对，新链路F3控制155对。G3冻结全库282对，较同期C5修16/退28，tokens多21.68%；保住四个目标例不等于整体改善。G3与C5都单Flash、全文/as-of引用/贡献主次；不新增抓取或第二call，不是生产默认。后续以C5对照检验有效局部边界的条件化兑现，详见[状态](../../../docs/evaluations/content-enrichment/status.md)。下方各阶段选择保留当时语义，不覆盖为最新结论。

F阶段当前保留C5整体成绩对照，研究修复主分支选`category-f3.txt`。F1保留D2研究机制并统一主题/贡献定义，F2修行业过度吸入，F3修短观察与发布/测评主次；同200题151/160/163对。冻结F3全361为288对，同期C5为289对，未证明整体更好；F3论文/教程P/R提高但行业P和观点R退化，保留局部收益继续修复而非整案删除。三者均为单Flash＋冻结全文/as-of引用＋贡献主次，不加source-context/conditional-review，不是生产默认。读数及原件见[状态](../../../docs/evaluations/content-enrichment/status.md)，运行方式见[执行入口](../aihot-category-navigation/README.md#d阶段候选复现)。

E阶段区分C5整体最优对照与D2保留修复分支。`category-e1.txt`保留D2的研究发现主次并修成果/预测边界，`category-e2.txt`收窄预测并要求reason对比，`category-e3.txt`在E1上核对类别范围；同200题79.0/79.0/78.5%，没有整体替代C5。D2首次全361题78.39%，C5历史79.78/80.33%；D2关于新发现与附带评论的局部收益继续保留，不因总分落后删除方向。详见[执行入口](../aihot-category-navigation/README.md#d阶段候选复现)和[状态](../../../docs/evaluations/content-enrichment/status.md)。

D阶段保留四个未晋级的C5干预：D1正文证据贡献、D2新发现/分析区别、D3内嵌转帖主次、D4来源角色（必须另开`--source-context`）。都使用冻结全文及引用贡献；没有把D1叠到D2或D3。D1–D4同200题80.0/81.5/80.0/79.5%，同期C5为82.5%；这是历史D阶段读数，不改默认，不按编号更大自动采用。

C阶段保留三个显式候选：`category-c1.txt`在A4上澄清研究成果/实践/观点边界；C2在C1上追加实际安全/法律/政策事件边界；C3在C1上改产品介绍/实践交付边界。C4复用C1加`--source-context`，C5复用C1加`--body-limit 0`，均保留B1的冻结引用及`--quote-contribution`。开关、rubric、模型、输入组合才定义方案，文件名不是完整身份；成绩与当前研究推荐见[状态](../../../docs/evaluations/content-enrichment/status.md)。C2/C3负结果也保留，不因文件存在就视为推荐。

本目录只保留非默认研究候选：`category-a1.txt` 收窄模型发布并扩大研究/观点定义，`category-a2.txt` 按文章内容贡献区分行业、教程和观点。A1/A2仅为研究候选，回归不支持将A2设为默认；禁止把文件编号当作已推广顺序。

当前默认 A0 由 `src/airadar/enrich/category.py:RUBRIC` 单一持有，evaluate.py 未传 `--rubric` 时直接使用它；重复的 `category-a0.txt` 已移除，不改变默认 prompt。精确复现历史轮次以该 run 的 prompt.json 为准，不假定将来的默认始终等于历史 A0。A1/A2通过 `--rubric` 显式选择。输入、执行命令、指标与历史run见[执行入口](../aihot-category-navigation/README.md)和[状态](../../../docs/evaluations/content-enrichment/status.md)。
