# 离线测试 prefilter 的 AI 直接影响范围

- Status: accepted for offline experiment only；不授权生产采用或部署
- Date: 2026-09-19

## 范围与依据

用户要求立即评测和优化新闻准入，随后选择“抽样优化＋独立回归”：3题smoke、固定600题dev、至多两个候选、最终baseline与候选各400题regression；不改题库和标签、不部署、不超出2603次调用上限。本记录只决定测试首个候选C1，不预判有效。

在 d102b78 的当前 prefilter 上，固定dev题 `2579cc8cd411baa37bf63a7d`（AI反感与社媒商业机制）及 `91c1ad82488f6c05b1a21c8b`（AI数据中心天然气需求）的参考成员均为true，真实Flash 260731输出均为false、confidence=0.9。原件位于 `runs/news-admission/aihot-prefilter/v1/2026-09-19/07-51-29/`。全轮尚在执行不影响这两条已完成输出的事实；完整成绩未计算，不由此宣称总体收益。

## 决策及备选

保留模型、SYSTEM、输入、参数和全部原规则，仅在英文正例范围后加一句：

> Reports and substantive arguments about AI’s direct effects on society, the economy, employment, energy or resource use also count as AI-related, even when the immediate subject is the effect rather than a model or product.

相比保留原prompt，这能直接检验一个观察到的缺口；相比调confidence，能修订已判false的范围；相比重写全篇，保持单变量；不采用按source_id排除来追分，因无通用内容依据。C1只是显式配置的离线候选，不改生产prompt。

ADR-002（2026-05-15）指定Flash与禁用thinking，本实验保持。旧20260905-499e记录aboutness收紧与产品发布误杀，本句不恢复“提到AI即收”、不修改实体物件排除；当前四对象用户裁决的AIHOT成员标签契约不沿用旧T5/k5验收。本轮未推翻历史生产决策。

## 验证与边界

假设 PREFILTER-IMPACT-01：直接影响范围漏收减少；同时检查新增FP。开发和独立回归各自至少一项precision/recall改善、另一项不退步且全部题完成，才支持采用该候选；不声称统计显著或整站拟合。静态AIHOT参考的私有定义、自一致率未知。资源题直接支持假设，社媒题是否属于direct effects有解释空间，待实验检验，不预称两题必修复。

decision-review：独立只读 `/root/prefilter_candidate_decision`，R14 / gpt-6-astra / high，七条均成立，放行实验；无blocker/应修。已读取实际两条输出。未验证候选效果和最终分组比较由本轮执行者负责，不由决策审查代替；只支持有限离线实验，无新增治理机制。

## 执行结果（2026-09-19）

实验已完成：600 dev的precision/recall均改善，但400独立regression的precision从33/95降至35/101，recall从33/42升至35/42。按原准则不采纳C1，不改变生产。该结果不撤销“允许测试”的决策，也不构成生产采用授权。结果与逐题证据见[对象状态](../evaluations/news-admission/status.md)及其原件链接；不从失败候选推出模型能力上限。
