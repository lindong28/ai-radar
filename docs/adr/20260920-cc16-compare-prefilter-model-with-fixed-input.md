# 固定输入与 C10 的模型对照

状态：采纳为离线实验，不修改生产默认。2026-09-20。

Compare C10 with deepseek-v4-pro against its existing flash run on the same 300 seen cases, without quotes or other input changes, offline only.

用户要求P/R分别>90，优先不增加生产输入依赖、依赖须有消融证据、逐轮留档，最新明确取消预算上限但不浪费。沿用3b8a的授权边界，不部署、不push、不改gold。当前C10无引用＋policy为88.11/93.33；C11为88.19/94.07；C12为88.24/88.89，少1FP却新增6FN。会议邀请拒收假设在另外4个已见邀请上全误拒，未实现。读数见2026-09-20/01-19-42的paired-analysis，不能据此声称Flash能力到顶。

仅将prefilter配置从deepseek-v4-flash改为deepseek-v4-pro；C10 prompt、300题、temperature=0、max_tokens=200、thinking=disabled、Ark固定endpoint、无quote、无fallback、既有policy均不变。首试300、8请求槽，只补失败；核验第一批真实模型及参数，不支持时不静默换参数。预期差异是固定规则解释造成的FP/FN变化，不预断方向。费用和延迟单列，不称未知费用为零。

若开发双>90，冻结候选后，从两树所有历史和规则检查排除后的未见dev（目前662）标签盲选最多662题，与C10 Flash同题对照，各4槽；保留原split和本次未见角色。未达标先归因，不自动无限重复同一实验。已有Flash、引用、C11/C12原件保留；引用在300中仅修1FN，不因此加依赖。新厂商模型仍是未测路线，不因为先测Pro而宣称不可用。现成_request接受模型覆盖，Pro已有enrich配置；这仅证明入口，不证明本次参数组合已实收。

独立prefilter_candidate_decision七项均成立、无遗留finding。其提示是Pro的实际接收与响应身份仍须由主线程核验。范围限这批X占主导的离线题，不外推全来源、生产效果或模型能力上限。无新增治理机制；R4主线程执行、R14只读审查，非模型评测预算。
