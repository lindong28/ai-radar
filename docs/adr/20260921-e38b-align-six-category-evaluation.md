# 六类分类与独立字段评测

日期：2026-09-21。状态：Accepted，依据用户本轮明确要求六类分类、建题、评测和优化。独立 decision-review 七项成立，无阻塞项。

## 决定与边界

采用模型、产品、行业、论文、教程、观点六类，替代 499e/a1c4 中本轮适用的旧五类分类方向；历史记录不改写，paper 排序倍率与类别快照绑定不变。enrich_v2 提示与离线分类共享分类定义；先以独立 reason-first Flash 调用测 category 字段，不把它的成绩称为完整 enrich 成绩。主生产 runner 仍走 legacy enrich，未切换或部署。既有 is_opinion 布尔信息保留，不用它猜改历史 primary_category。

复用确定性 category_accuracy；缺参考类别不造 gold，原始 title/body 与 AIHOT 生成参考隔离。原始字段题库 v2 只依据冻结原件扩题，v1 不覆盖。主指标之外用逐类分母、混淆矩阵与多数类基线解释结果，失败题不从分母消失。

2026-09-21 取证修订：真实网页 JS 将六类映射到 model_release、product_launch、industry_event、research_paper、tutorial_explainer/tool_or_prompt、opinion_analysis；网页六类与五类 API 不同，后者把教程和观点合为 tip。因此本轮分类另建 `content-enrichment/aihot-category-navigation/v1`，从真实分类页面成员取得答案，按 item_id 接已有合格原文；旧 `aihot-enrichment-fields/v2` 保留原 API 字段，不作为新六类评测的 gold。新 benchmark 是消费者语义变化，不用复杂版本名掩盖。新闻身份稳定划分、原输入规则及人评优先不变；未观测的成员不当负例，多类冲突不猜。

先 smoke，再固定开发样本检验至多三个诊断候选，最后冻结候选进入既有 regression split；最多 1,500 次本轮业务调用、共享并发 8，无重试和模型 fallback。数量是本轮实验范围，不是用户预算上限。保留所有 attempts、输入、prompt、输出、指标和归因；不用已见开发题冒充盲测。新分类不修改评分或标签目标，不部署、不 push。

## 证据与替代项

07ae36a 的 classification.py 只有五类，prompts_v2.py 明确把观点及剩余内容归 tutorial。现有 v1 为 3,476 条新闻，分类参考 1,525 条且无 opinion；parser 的 category 接受任意字符串，因此不能先认定是五类白名单丢掉观点。原件检查归本轮数据建设。

完整 enrich 调用会同时生成非本轮目标字段，分类误差难以单独定位；只加枚举不能解除 tutorial 的兜底定义；继续五类不满足用户目标。因此采用字段隔离测试与共享 rubric，完整 enrich 的执行与其余字段质量不由本次独立分类成绩背书。

## 实施结果与未验证边界

网页映射和成员证据已冻结；新六类题库361题、观点83题。A0/A1/A2同开发200题准确率69.0%/74.5%/77.0%，每轮同一题被供应商过滤仍计入分母。冻结A2后同回归76题，A0为69.74%、A2为68.42%，未支持替换A0。六次run共760次调用，未达到预设调用上界；本轮优化闭环结束，不据回归继续调参。完整逐轮结果及归因见[分类状态](../evaluations/content-enrichment/status.md)。

生产多字段调用中的分类一致性尚未评测，不因离线成绩推广生产。旧标签fallback兼容不等于历史记录已经按六类互斥重标；不从is_opinion=true猜改历史主类。本轮没有新的用户人评标签，也未更改gold。
