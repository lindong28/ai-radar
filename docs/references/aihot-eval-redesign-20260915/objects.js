"use strict";
// Design content only. This file does not run an evaluation or define production policy.
const objects = {
  e2e: {
    title: "E · 整条链路的最终用户结果",
    nodes: ["input", "prefilter", "score", "enrich", "select", "web"],
    boundary: "原始输入 → 真实准入/评分/富化 → 精选历史 → 实际路由与前端。主验收对象，全部为拟建；尚无新基线成绩。",
    slots: [
      ["① 优化对象", "整个可连通生产流程。观察首页精选归档、/all 全部动态、分类筛选及原文跳转；页面上最终显示的值才是验收值。"],
      ["② 评测题", "每个共同源条目的输入题 + 每个观察时刻的完整候选池题；附当时历史状态、固定时钟和参考页面快照。字段题和整池题不能互代。"],
      ["③ 判官", "评分/分类/标签字面/集合用确定性指标；标题、摘要、精选理由用分别校验的语义判官。没有参考或缺历史状态时，该维度明确未核实。"],
      ["④ 自动指标", "最终分 MAE/bias/P90，类别 macro-F1，标签 P/R/F1，精选集合 P/R 与顺序，标题/摘要/理由可接受率；另列目标条目到达率与缺字段率。不给可抵消损失的总分。"],
      ["⑤ 判官校验", "最终分整体平移、准入漏题、隐藏标签、理由 fallback、跨 run 身份与时间边界控制；语义判官用真实近边界人工标签、构造坏例和重复/位置交换校验。"]
    ],
    l2: "每题串起原文、各阶段输出、完整池/历史状态和最终卡片；保存逐维指标与缺失原因、用户票、判官记录、因果假设及轮次采纳结论。链路图可追到导致差异的最早阶段。",
    l3: "实际运行最新代码身份 + 隔离 DB/时钟；同题同尺子比较；gold 仅评测端可见。改上游只使依赖的下游失效，不补造历史身份。用户审核前不启用新标准。",
    example: "一条参考精选没有显示：先归为输入缺失/准入/评分门槛/去重/名额/归档过滤中的具体阶段；不能仅从“已显示的条目”中删掉它再算出漂亮均分。",
    evidence: "当前可复用 create_app(db_path=...)、真实 routes 与 web/static/app.js；创建 app 会迁移/预热，正式评测只允许指向副本。本轮未运行这条重放路径。"
  },
  admission: {
    title: "A · 准入与新闻到达率",
    nodes: ["input", "prefilter"],
    boundary: "冻结原文/来源 → 生产 prefilter。组件定位误杀与放入，不把它的通过当最终网站通过。",
    slots: [
      ["① 优化对象", "真实 prefilter 判断及后续 scorer/enrich 的 stage gating。保留生产准入语义，不沿用旧 eval 每题三阶段全跑的方式冒充整链路。"],
      ["② 评测题", "共同源原始池：AIHOT 可见条目作为应保留候选，精选单列高价值切片；真实非 AI 负例必须另有人类/可信标签，不能把 AIHOT 缺席直接当负例。"],
      ["③ 判官", "对于参照可见条目的保留情况直接计数；非 AI/歧义语义由有标签的独立判官辅助，且与“是否被 AIHOT 展示”分开。"],
      ["④ 自动指标", "参照可见条目保留率、精选条目保留率、按源/类误杀清单；有可信负例后才报 precision/特异性。继续跟踪通过者最终是否到达页面。"],
      ["⑤ 判官校验", "全放行/全拒绝控制分别暴露误放与误杀；缺可信负例时不能声称全放行就是优秀准入。语义标签独立校验误报/漏报。"]
    ],
    l2: "题集保留准入前全部候选与原文；逐题记录 pass/reject/error 及理由，指标保存明确正负例分母。人工票与预标分开；假设例为‘来源摘要过短导致误杀’，轮次保留对照输入。",
    l3: "参考缺席不转负标签；失败/未运行不转 reject。固定共同源映射与输入水位；只改 prefilter 时缓存下游可复用部分，新通过项必须补齐下游产物。",
    example: "真实 IT之家样本若准入时被丢弃，即使余下新闻分类更准，精选保留率与最终到达率也会下降，必须显式呈现。",
    evidence: "生产 scorer/runner.py 和 enrich/runner_v2.py 有准入 SQL；旧 eval/aihot_fit/run.py 记录 stage_gating=none。负例规模及标签本轮尚未建成。"
  },
  scoring: {
    title: "B · 评分信号与最终分的因果关系",
    nodes: ["score"],
    boundary: "通过准入的输入 → scorer 六维输出。这里只定位评分信号；经过精选再映射的显示分由 E 对象验收。",
    slots: [
      ["① 优化对象", "真实 scorer、加权聚合与其输入。原始分、排序分、显示分分别命名，不把其中一个默认为另一个。"],
      ["② 评测题", "共同源有参考分的逐条题，覆盖分数区间、来源、类别、精选/普通及正文长度；无六维 AIHOT 真值，不为其内部维度伪造 gold。"],
      ["③ 判官", "直接比较可比标度上的输出；raw 分只作相关/排序诊断。输入是否支持评分理由可另抽样语义判，不从理由好看推定数值准确。"],
      ["④ 自动指标", "raw 排序与参考排序的相关性、类别条件偏差、六维分布和入选边界；真正 MAE 以 E 的最终显示分计算，不在这里把 0–10 raw 简单乘十当权威。"],
      ["⑤ 判官校验", "恒定开发集中位数、独立置换、最终分 +10 控制。若只改显示映射，组件排序应不动；若排序动了，先查变量混入。"]
    ],
    l2: "记录原文版本、六维原始输出、权重、raw/ranking/display 的追溯关系、数值/缺失指标；关联评分理由的判词和人工票。保存刻度/排序/输入不足三类假设及每轮对照。",
    l3: "不把 AIHOT 分数喂给 candidate；拟合参数仅开发集学习，验证/封存不参与。raw 与最终分身份分开；同一新闻跨页面/run 的分数分别观察。",
    example: "当前 select._calibrate_selected_scores 在入选数大于一时按名次赋 92→62；因此降低 raw 误差未必降低最终误差。这是要实验的路径，不是预先废除该映射的结论。",
    evidence: "curator/score.py 聚合六维；select.py:166 生成 rank_linear_v1；app.js 乘十并取整。旧 fit 与 rank_linear_v1 不是同一个转换。"
  },
  enrichment: {
    title: "C · 分类、标签与中文内容富化",
    nodes: ["enrich"],
    boundary: "冻结原文 → enrich/provider normalizer 输出。组件对齐后，仍须通过 E 检查最终投影、裁剪、隐藏与 fallback。",
    slots: [
      ["① 优化对象", "真实 enrich prompt/model、输出解析与标准化；包括类别、标签、中文标题/摘要与推荐理由。不给旧 prompt 或规则天然豁免。"],
      ["② 评测题", "按字段有参考的配对题，保留缺失状态；样本覆盖五类、来源、文本长度与精选理由。真实案例 A 可研究 model/product 混淆，案例 B 先归输入不足。"],
      ["③ 判官", "类别精确映射、标签集合直接算；标题/摘要/理由分别用证据锚定的离散语义判官；标签同义含义另判，不取代原样集合指标。"],
      ["④ 自动指标", "类别一致率/macro-F1/混淆矩阵、标签 P/R/F1 与额外标签率；各文本维度可接受率、重大断言缺陷率、关键事实覆盖；缺输出比例单列。"],
      ["⑤ 判官校验", "多数类/泛标签常量、忠实近义改写、一个主体或数字反转、关键信息省略与通用理由。按标题/摘要/理由分别量误拒和漏检，不能只用平均值通过。"]
    ],
    l2: "字段级原始输入/gold、provider 原始响应、normalizer 后输出各保留；关联逐字段指标、人工票与判官片段证据。假设针对具体混淆/遗漏，轮次记录 prompt 差异和反例。",
    l3: "gold 与 candidate 输入隔离；标签映射从开发集冻结，未映射预测仍计入分母。旧 summary/reason 校验不自动覆盖 title；无法判断不装成合格。",
    example: "IT之家配对样本中 AIHOT 为 ai-models，当前 enrich 为 product；先明确实体/事件的分类规则，再用未参与规则编写的题验证，不能只调全库模型类占比。",
    evidence: "presentation/summary.py 与 classification_projection 后续还会改读法；首页 app.js 显式 showTags:false，组件 tags 合格不说明首页已展示。"
  },
  selection: {
    title: "D · 精选、去重与跨轮归档",
    nodes: ["select"],
    boundary: "可见的阶段结果 + 候选池/历史/时钟 → 真实 curate → 归档状态。最终卡片和页面顺序仍由 E 接住。",
    slots: [
      ["① 优化对象", "真实新鲜度、阈值、去重、来源配额、排序系数、补位、按名次赋分与归档成员规则。先基线复现，再选择一个因素消融。"],
      ["② 评测题", "每一时刻完整候选池 + 前置历史；参考实际精选集合和页面观察时刻。不能用 AIHOT 已显示条目单独当候选全集，遗漏我方额外候选会扭曲 precision。"],
      ["③ 判官", "明确身份配对后的集合/顺序直接算；跨来源同事件归并有独立依据，仅次级分析。必要时人工核对未匹配成员，而非让模型凭标题猜身份。"],
      ["④ 自动指标", "自然数量入选集 P/R/F1、额外/漏选数、归档与最新 run 两套成员、首屏/前40成员和次序。按完整池中的流失阶段归因；类别构成 TV 仅辅助。"],
      ["⑤ 判官校验", "空选/全选、均匀随机 K、重复项、多轮曾入选、跨日边界、source 状态改变。明确集合为空时约定，失败不能与‘没有精选’共用状态。"]
    ],
    l2: "保存完整池与源状态、时间水位、所有候选的退出原因、各 run 选择结果及页面成员；集合差异关联用户票与身份判定证据。假设记录新鲜度/配额/排序的独立对照，轮次附实际自然入选数。",
    l3: "完整池/历史不足时不声明历史重放成功；未来 enrich 不注入过去；候选在同一原始暖启动语料上各建状态。oracle 中间值只用于诊断，不能进入正式候选成绩。",
    example: "过去入选但本轮落选的新闻，在首页归档仍可能存在，而 /all 的精选标记只认最新 run。用最新 run 的 P@k 不能替代首页归档集合命中。",
    evidence: "curated_archive.py 取每 item 最后入选记录并读当前 enrich；timeline.py join 最新 run；历史 input_eval_ids 不是完整加载池的全部字段快照。"
  }
};

function renderObject(key) {
  const object = objects[key];
  if (!object) return;
  const panel = document.getElementById("object-panel");
  panel.replaceChildren();
  const heading = document.createElement("h3");
  heading.id = "object-title";
  heading.textContent = object.title;
  const boundary = document.createElement("p");
  boundary.className = "intro";
  boundary.textContent = object.boundary;
  panel.append(heading, boundary);
  const l1 = document.createElement("div");
  l1.className = "layer-title";
  l1.textContent = "L1 · 五个必答槽位";
  panel.append(l1);
  const list = document.createElement("dl");
  list.className = "rows";
  for (const [label, content] of object.slots) {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = content;
    list.append(term, description);
  }
  panel.append(list);
  for (const [label, content] of [["L2 · 这个对象留下哪些资产", object.l2], ["L3 · 这个对象的采信边界", object.l3], ["一条具体题目怎样穿过它", object.example], ["当前证据与未实施边界", object.evidence]]) {
    const section = document.createElement("div");
    section.className = "layer";
    const title = document.createElement("div");
    title.className = "layer-title";
    title.textContent = label;
    const text = document.createElement("p");
    text.textContent = content;
    section.append(title, text);
    panel.append(section);
  }
  document.querySelectorAll("[data-object]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.object === key)));
  document.querySelectorAll("[data-node]").forEach(node => node.classList.toggle("active", object.nodes.includes(node.dataset.node)));
}

document.querySelectorAll("[data-object]").forEach(button => button.addEventListener("click", () => renderObject(button.dataset.object)));
renderObject("e2e");
