# 离线测试 prefilter 原始帖关系与正文条件

- Status: accepted for offline experiment only
- Date: 2026-09-19

承接 20260919-bc72；共享同一批最多3000次新增调用预算，不改变题库、±12h参考、模型或生产。C2完整600 dev实测TP73/FP72/FN7/TN448，45个buzzing误收仍违反HN热度必要条件。因此C3将system及输出定义明确为“AI相关且满足准入条件”，把条件移到user头部，并增加原始X replied_to标记：回复帖不准入。保留其它C1主题标准，不加入题目示例。

reply只来自input.source_kind=x及input.extra.referenced_tweets中的type=replied_to，不读取reference。固定600有21个回复负例、C1误收11；其余dev有292个回复负例、0正例，非回复459正150负。备选文字猜测回复比原始关系间接，全部拒绝X会连带拒绝已知非回复正例，故只测原始关系。

C4可在C3上增加一个必要条件：source_kind=web、content_text与title字节相等、published_at与fetched_at字节相等且非hf_daily_papers时不准入。该精确谓词在固定600命中14负（C1 8FP/6TN）、0正；其余dev命中10正227负，正例claude4、langchain3、google_research/anthropic_research/mistral各1。已知存在召回代价，不声称这些题原始数据无效或过时；不删题，不补正文，不把它当AIHOT内部规则。

context变化仅作用于显式离线prompt override，生产ProviderItem接口不变。C3/C4各跑原600 dev，冻结后选定候选与当前源码基线各跑未见的新400 regression；新回归排除上轮400，不根据新回归调参。双precision>90%、recall>90%分别判定，条件遵循率只作诊断。

独立决策审查prefilter_heat_decision_readonly复算分组并放行C3/C4及构框增量，七项成立、无blocker。C3同时改变构框和reply条件，不把总收益全部归因于一个变量。runner接入另由实现测试和review验证；调用失败重试也计入3000总上限。候选不达标则不替换生产，离线成果及失败同样留存。

C3运行期间已观察case7d9daec392b0bdbc1359f3a6正文仅2 HN Points仍返回true。C4在相同已审逻辑下改为简洁统一的合取定义，删除重复双语和“只要AI产品便输出true”的冲突表述，但保留小发布/营销、直接社会影响、实体硬件排除与可判内容条件。该方向经同一独立reviewer放行；最终文本由主线程逐项对照。C4结果只能归于组合候选，不能单独归因于正文条件或措辞简化。

## C5：相同条件改由代码执行

C3最终TP72/FP84/FN8/TN436（P46.15%、R90.00%），纯提示仍不稳定执行可确定条件。新增C5候选：保持C1模型/主题prompt，用代码计算同样三个必要条件并与模型布尔取AND。HN数值仅解析原始正文前4000字符中的`(\d+) HN Points`，不由参考答案推导。开发层可复用已核身份的C1原始模型响应，产生全新混合对象、逐题保留model_output/最终output/gate原因，完整题集计分；不是覆盖C1输出或删除被拒绝题。未来实际运行由模型阶段加同一policy函数组成。

独立reviewer复算开发诊断TP73/FP8/FN7/TN512，78条true→false均为开发负例（HN59、reply11、title-only8），并核对C1模型、prompt和底层源码身份。七项放行仅覆盖离线新候选；生产采纳未授权。实现仪器另审测后才正式计分。候选冻结仍只用dev，最终新reg400真实跑模型再应用同一函数，和baseline各400首试；新调用仍在3000总限内。代码保证条件执行不等于条件正确，未知回归必须真实验证。
