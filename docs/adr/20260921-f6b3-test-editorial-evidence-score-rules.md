# 推广、一手事件与评分边界消融

> Decision · 2026-09-21 · Accepted for bounded offline experiments

用户要求依据逐条误差分析实验prompt、独立识别调用、few-shot与权威来源公式。该最新授权仅替代[a93c](20260921-a93c-test-no-example-score-rubrics-and-dimension-calls.md)和[c4e1](20260921-c4e1-test-news-type-score-combination.md)在本研究中的无示例限制；五维＋代码组合、原题/gold、人评优先、Flash优先、MAE<3及Spearman、离线不部署不push保持不变。

假设：宣传性细节被误作实质知识；简短的一手实质事件可能被低估。反例包括Poolday推广33/60、48/60、官方合作61/43；同时Salesforce官方集成23/64、NVIDIA生态集成42/68否定无条件官方抬分的设计依据。它们是诊断而非参考错误证明。AIHOT只有冻结逐题分数，不能调用其评分器测自一致，原公式未核实；不声称已取得作者算法。

四臂：P1在A11刻度加入编辑边界；P2=P1＋虚构的语义边界示例，无benchmark分数；P3先独立reason-first分类再把结果作为可质疑的辅助交P1评分，两次实际调用均存；P4复用相同A11五维缓存及P3分类，代码分别测推广caps、一手事件floor、组合。分类为promotion/routine_release/substantive_release/analysis/digest/other/unknown；官方身份不能代替逐条新事件判断。P4只在promotion对impact/novelty/substance设上限3或4；只在已核官方来源且substantive_release时对impact设下限5或6；其它维度及35/20/25/10/10权重不变，unknown不干预。既有来源角色表加data/sources.toml中可核的Anthropic Newsroom，不按gold扩名单。无全局平移，不剔题，不改gold。

沿用同开发200、按来源hash143训练/57内部验证（已见，不称盲测）。有限参数仅143选，57筛选不回调参数。同期新A11单独量漂移，P4对旧缓存的公式增益不能归到同期对照。P1/P2/P3与同期A11比较；最多两候选仅在57上MAE下降且rho不低才晋级，排序MAE升、rho降、调用少优先，冻结后跑已见100及同期A11，不按回归再调。本轮至多1600次Flash调用，共享请求上限8，病例内两个调用串联，无重试/fallback；完整attempts及usage留存，金额未知不记零。各批main返回退出，硬期限1800秒。生产不增加依赖。

不继续细化全局权重（同域开发下限9.09不能达到3），不一次合并所有改动（不可归因），不对所有弱臂全量3475，不以用户不限预算为无限重复依据。小幅收益仅报告差值；P3不自动证明任务过载因果；分类不是gold或人评。来源/事件/推广机制无效也完整归档，不反向删除题目。

L1五槽：对象为上述离线候选，题沿aihot-score-pointwise/v1，MAE/Spearman复用确定性O2；LLM质量判官及其校验不适用，辅助分类属对象内部。L2沿runs/experiments及status/ledger/hypotheses留原件。L3沿既有身份/冻结/失败保留，不新建治理层。R=A1，新增链路和消费者需V1单轮独立实现审查。

独立决策审查review_editorial_decision，原生Codex隔离context，gpt-6-astra/high（R14），七项成立，无blocker/应修/独立finding。三条提示已纳入：两阶段收益不等于过载因果，官方必须与实质事件共同作用，小差异不等于稳定优势。
