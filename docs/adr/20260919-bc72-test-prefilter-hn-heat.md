# 离线测试 prefilter 的 HN 热度条件

- Status: accepted for offline experiment only
- Date: 2026-09-19

用户要求新闻准入 precision、recall 均 >90%，并为本批批准最多 3,000 次新增模型调用（含失败重试）。保留 aihot-prefilter/v1 全部正负题、±12h 参考规则、原指标和生产配置；不授权 push 或部署。

决定测试 C2：在 C1 的 AI 直接影响范围上，仅增加 buzzing_hn 输入必须明确含 HN Points≥100 的必要条件，其它来源不变。保留纯 prompt 接口，不把参考成员、精选或目标比例输入模型。它是预测候选，不声称是 AIHOT 内部算法；参数来自开发观察，不使用回归标签调参。

对照使用原固定600 dev。候选冻结后与原源码基线各跑新的400 regression，按旧run身份排除已见400题；最终报告同题的 precision/recall、错误和预算，不用 dev 代替回归。双 >90% 是目标；相对改善不等于达标。

证据：原600 dev的buzzing有4正439负，基线56个误收均为非HN子流。排除原600后的6632条dev buzzing，≥100 Points为49正45负、<100为2正383负、无Points为1正6152负。门条件会损失3/52正例，不能预称无召回损失。机械叠加既有C1预测仅得TP73/FP27/FN7/TN493（P73%、R91.25%）；这只是诊断，不是C2模型实测。

备选：仅扩大主题已在C1实验中未改善回归precision；全buzzing拒绝会丢已知正例；删除非HN题会改分母，不采用；确定性后处理可另作对照，但本次先检验最小prompt变更。ADR-002的模型/thinking配置保持；20260919-e1d3在当时缺来源依据而未做来源过滤，本次新增开发证据只支持新实验，不构成生产历史决定的override。

独立审查 `/root/prefilter_heat_decision_readonly`（R14，gpt-6-astra/high）自行复算上述开发分组，七项成立，无blocker、应修或独立finding，放行有限实验。首次reviewer因uv意外创建工作树.venv违反只读契约，其结果作废，未用来放行。待核：实际模型遵循、泛化和最终预算，由本session完成；AIHOT私有feed与真实阈值不可由本证据推出。
