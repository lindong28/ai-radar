/* Archived text is always rendered with textContent, never as HTML. */
(function () {
  "use strict";

  const FORMAT = "ai-radar-score-human-review-v1";
  const DECISIONS = {pending: "未评", retain: "暂保留", exclude: "建议剔除该评分题", uncertain: "无法判断"};
  const DIMENSIONS = [["impact", "事件影响"], ["novelty", "实质新意"], ["substance", "实际信息"], ["authority", "来源资格"], ["relevance", "AI 关系"]];

  function storageKey(payload) {
    return `${FORMAT}:${payload.metadata.source_cases_sha256}:${payload.metadata.source_predictions_sha256}:${payload.metadata.batch_sha256}`;
  }

  function initialState(payload, saved) {
    const state = Object.fromEntries(payload.cases.map(row => [row.case_id, {decision: "pending", reason: ""}]));
    if (!saved || saved.format !== FORMAT || !saved.metadata || !Array.isArray(saved.judgments)
        || saved.metadata.source_cases_sha256 !== payload.metadata.source_cases_sha256
        || saved.metadata.source_predictions_sha256 !== payload.metadata.source_predictions_sha256
        || saved.metadata.batch_sha256 !== payload.metadata.batch_sha256) return state;
    for (const vote of saved.judgments) {
      if (Object.hasOwn(state, vote.case_id) && Object.hasOwn(DECISIONS, vote.decision) && typeof vote.reason === "string") {
        state[vote.case_id] = {decision: vote.decision, reason: vote.reason};
      }
    }
    return state;
  }

  function exportBallot(payload, state, exportedAt = new Date().toISOString()) {
    return {
      format: FORMAT,
      metadata: {...payload.metadata, exported_at: exportedAt},
      judgments: payload.cases.map(row => ({case_id: row.case_id, decision: state[row.case_id].decision, reason: state[row.case_id].reason})),
    };
  }

  function safeURL(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }

  async function copyBallot(payload, state, clipboard) {
    const text = JSON.stringify(exportBallot(payload, state), null, 2);
    try {
      if (!clipboard || typeof clipboard.writeText !== "function") throw new Error("clipboard unavailable");
      await clipboard.writeText(text);
      return {copied: true, text};
    } catch (_) { return {copied: false, text}; }
  }

  function responseObject(prediction) {
    if (!prediction) return {};
    const value = prediction.response_json;
    if (value && typeof value === "object" && !Array.isArray(value)) return value;
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch (_) { return {}; }
  }

  function dimensionCalls(prediction) {
    const calls = prediction && prediction.dimension_calls;
    if (Array.isArray(calls)) return calls;
    if (calls && typeof calls === "object") return Object.entries(calls).map(([dimension, call]) => ({...call, dimension}));
    return [];
  }

  function scoreText(value) {
    return typeof value === "number" && Number.isFinite(value) ? String(value) : "未记录";
  }

  function deltaText(prediction, gold) {
    const value = prediction && prediction.output && prediction.output.score;
    if (typeof value !== "number" || typeof gold !== "number") return "未记录";
    const delta = Math.round((value - gold) * 100) / 100;
    return `${delta > 0 ? "+" : ""}${delta}`;
  }

  function mount(payload) {
    const byId = id => document.getElementById(id);
    const node = (tag, text, className) => {
      const result = document.createElement(tag);
      if (text !== undefined && text !== null) result.textContent = String(text);
      if (className) result.className = className;
      return result;
    };
    const jsonText = value => JSON.stringify(value, null, 2);
    const disclosure = (title, text) => {
      const details = node("details");
      details.append(node("summary", title), node("pre", text));
      return details;
    };
    let saved = null;
    let storageAvailable = true;
    try { saved = JSON.parse(localStorage.getItem(storageKey(payload))); }
    catch (_) { storageAvailable = false; }
    const state = initialState(payload, saved);
    let active = payload.cases[0].case_id;
    let category = "";
    const filtered = () => payload.cases.filter(row => !category || row.category === category);

    function save() {
      try {
        localStorage.setItem(storageKey(payload), JSON.stringify(exportBallot(payload, state)));
        storageAvailable = true;
      } catch (_) { storageAvailable = false; }
      byId("save-status").textContent = storageAvailable ? "草稿已保存在此浏览器" : "浏览器未能保存：离开前请复制整批 JSON";
      byId("save-status").classList.toggle("warning", !storageAvailable);
    }

    function updateProgress() {
      const done = payload.cases.filter(row => state[row.case_id].decision !== "pending").length;
      byId("progress-text").textContent = `${done}/${payload.cases.length} 已评 · ${payload.cases.length - done} 未评`;
      byId("progress").max = payload.cases.length;
      byId("progress").value = done;
    }

    function renderNavigation() {
      const rows = filtered();
      const list = byId("case-list");
      list.replaceChildren();
      byId("nav-summary").textContent = `显示 ${rows.length}/${payload.cases.length} 条 · 分组为初步分析`;
      for (const row of rows) {
        const button = node("button", null, "case-nav");
        button.type = "button";
        button.setAttribute("aria-current", String(row.case_id === active));
        const index = payload.cases.indexOf(row) + 1;
        button.append(node("span", `${index}. ${row.case.input.title || row.case_id}`, "nav-title"));
        const info = node("span", null, "nav-detail number");
        info.append(node("span", DECISIONS[state[row.case_id].decision]), node("span", `分差 ${deltaText(row.prediction, row.case.reference.score)}`));
        button.append(info);
        button.addEventListener("click", () => { active = row.case_id; render(); });
        list.append(button);
      }
      const current = rows.findIndex(row => row.case_id === active);
      byId("previous").disabled = current <= 0;
      byId("next").disabled = current < 0 || current >= rows.length - 1;
      byId("position").textContent = current < 0 ? "该分组没有样本" : `${current + 1}/${rows.length} · ${active}`;
    }

    function renderPrediction(record, title, mode) {
      const section = node("section", null, "section");
      section.append(node("h2", title));
      const prediction = record.prediction;
      if (!prediction) {
        section.append(node("p", "该 run 没有保存此题预测，无法比较。", "warning"));
        return section;
      }
      if (prediction.status !== "ok") section.append(node("p", `该预测未成功（${prediction.status || "状态未记录"}），不把缺失分数当作 0。`, "warning"));
      const response = responseObject(prediction);
      const dimensions = node("dl", null, "dimensions number");
      for (const [key, label] of DIMENSIONS) {
        const pair = node("div", null, "dimension");
        pair.append(node("dt", label), node("dd", response[key] == null ? "未记录" : `${scoreText(response[key])}/10`));
        dimensions.append(pair);
      }
      section.append(dimensions);
      const calls = dimensionCalls(prediction);
      const aggregated = prediction.reason_origin === "aggregated-dimension-calls" || (mode === "five-separate" && calls.length > 0);
      section.append(node("p", aggregated ? "代码聚合理由（由各维原始理由拼合）" : "模型原始总理由（未拆成逐维理由）", "note"));
      section.append(node("p", prediction.reason || response.reason || "此预测未保存 reason。", "prose"));
      if (calls.length) {
        const callDetails = node("details");
        callDetails.append(node("summary", `查看 ${calls.length} 次逐维调用的真实理由与 prompt`));
        for (const call of calls) {
          const callSection = node("section", null, "section");
          const label = DIMENSIONS.find(([key]) => key === call.dimension);
          callSection.append(node("h2", label ? label[1] : call.dimension || "未标明维度"));
          callSection.append(node("p", call.reason || responseObject(call).reason || "此调用未保存 reason。", "prose"));
          callSection.append(disclosure("该次调用的实际 prompt", call.prompt ? jsonText(call.prompt) : "未记录"));
          callSection.append(disclosure("该次调用的完整原始记录", jsonText(call)));
          callDetails.append(callSection);
        }
        section.append(callDetails);
      }
      section.append(disclosure("实际渲染 prompt", record.prompt ? jsonText(record.prompt) : "该 run 未保存此题的实际 prompt；不按模板重建。"));
      section.append(disclosure("完整原始预测记录", jsonText(prediction)));
      return section;
    }

    function changed() {
      byId("copy-fallback").hidden = true;
      updateProgress();
      renderNavigation();
      save();
    }

    function render() {
      renderNavigation();
      const article = byId("case-content");
      article.replaceChildren();
      const row = payload.cases.find(item => item.case_id === active);
      if (!row || !filtered().includes(row)) {
        article.append(node("p", "该分组没有样本，请切换分组。", "muted"));
        return;
      }
      const input = row.case.input;
      const header = node("header", null, "article-header");
      const fullTitle = input.title || "未记录标题";
      header.append(node("h1", fullTitle.length > 160 ? `${fullTitle.slice(0, 160)}…` : fullTitle));
      if (fullTitle.length > 160) header.append(disclosure("展开完整标题（仅展示折叠，模型输入未改）", fullTitle));
      const meta = node("div", null, "meta");
      meta.append(node("span", input.source_name || input.source_id || "来源未记录"), node("span", input.author || "作者未记录"), node("span", input.published_at || "发布时间未记录"));
      const url = safeURL(input.url);
      if (url) {
        const link = node("a", "打开原文 ↗");
        link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
        meta.append(link);
      } else if (input.url) meta.append(node("span", `原始 URL（未设为链接）：${input.url}`));
      header.append(meta, node("p", `初步分组：${row.category} · 选题按源 run 分差，顺序按本批分析；候选分数仅供比较。`, "note"));
      article.append(header);
      const scores = node("div", null, "scores number");
      for (const [label, value] of [["AIHOT 观察分（参考）", scoreText(row.case.reference.score)], ["原始 Radar 分", scoreText(row.prediction && row.prediction.output && row.prediction.output.score)], ["Radar − 参考", deltaText(row.prediction, row.case.reference.score)]]) {
        const score = node("div", null, "score");
        score.append(node("span", label, "small muted"), node("span", value, "score-value"));
        scores.append(score);
      }
      article.append(scores, node("p", "参考只有 AIHOT 观察分，未保存其评分理由。分差本身不能证明参考分或 Radar 出错。", "note"));
      article.append(disclosure("完整新闻正文（原样文本）", input.content_text || input.content_html || "原始输入未包含正文。"));
      const analysis = node("div", null, "analysis-grid");
      const finding = node("section", null, "section analysis");
      finding.append(node("h2", "Agent 初步分析（待你复核）"), node("p", row.analysis, "prose"));
      const counter = node("section", null, "section analysis counterargument");
      counter.append(node("h2", "可能反证 / 另一种解释"), node("p", row.counterargument || "本批未附反证说明。", "prose"));
      analysis.append(finding, counter);
      article.append(analysis);

      const decision = node("section", null, "section decision");
      const choices = node("fieldset", null, "choices");
      choices.append(node("legend", "你的复核票"));
      for (const [value, label] of Object.entries(DECISIONS)) {
        const choice = node("label", null, "choice");
        const radio = node("input");
        radio.type = "radio"; radio.name = "decision"; radio.value = value; radio.checked = state[row.case_id].decision === value;
        radio.addEventListener("change", () => { state[row.case_id].decision = value; changed(); });
        choice.append(radio, node("span", label)); choices.append(choice);
      }
      const reasonLabel = node("label", "理由 / 备注（可选）");
      reasonLabel.htmlFor = "judgment-reason";
      const reason = node("textarea");
      reason.id = "judgment-reason"; reason.value = state[row.case_id].reason;
      reason.placeholder = "写下你认为应保留、剔除或暂时无法判断的依据。";
      reason.addEventListener("input", () => { state[row.case_id].reason = reason.value; changed(); });
      decision.append(choices, node("p", "“暂保留”不表示认可具体分数；“建议剔除”只针对该评分题。未评不会被计作保留。", "note"), reasonLabel, reason);
      article.append(decision);
      article.append(renderPrediction(row, `原始 Radar · ${payload.source.label}`, payload.source.mode));
      row.comparisons.forEach((record, index) => {
        const summary = payload.comparisons[index];
        const panel = renderPrediction(record, `候选 · ${summary.label}`, summary.mode);
        panel.classList.add("candidate");
        const value = record.prediction && record.prediction.output && record.prediction.output.score;
        panel.insertBefore(node("p", `候选分 ${scoreText(value)} · 与参考分差 ${deltaText(record.prediction, row.case.reference.score)}`, "number"), panel.children[1]);
        article.append(panel);
      });
      article.append(disclosure("完整原始题目 / 输入 / 来源记录", jsonText(row.case)));
      article.append(disclosure("本批来源与身份", jsonText({metadata: payload.metadata, source: payload.source, comparisons: payload.comparisons})));
    }

    const filter = byId("category-filter");
    for (const value of ["", ...payload.categories]) {
      const option = node("option", value || "全部分组"); option.value = value; filter.append(option);
    }
    filter.addEventListener("change", () => {
      category = filter.value;
      const rows = filtered();
      if (!rows.some(row => row.case_id === active)) active = rows.length ? rows[0].case_id : null;
      render();
    });
    for (const [id, delta] of [["previous", -1], ["next", 1]]) {
      byId(id).addEventListener("click", () => {
        const rows = filtered(); const current = rows.findIndex(row => row.case_id === active);
        if (rows[current + delta]) { active = rows[current + delta].case_id; render(); }
      });
    }
    byId("copy-all").addEventListener("click", async () => {
      const result = await copyBallot(payload, state, navigator.clipboard);
      byId("copy-fallback").hidden = result.copied;
      if (result.copied) {
        byId("save-status").textContent = "整批 JSON 已复制，请粘回对话确认。";
      } else {
        byId("copy-status").textContent = "自动复制未成功。完整 JSON 已在下方选中，请手动复制后整批粘回对话。";
        byId("copy-text").value = result.text;
        byId("copy-text").focus(); byId("copy-text").select();
      }
    });
    updateProgress();
    byId("save-status").textContent = storageAvailable ? "草稿仅保存在此浏览器" : "浏览器未能读取草稿，请及时复制结果";
    byId("save-status").classList.toggle("warning", !storageAvailable);
    render();
  }

  const api = {FORMAT, DECISIONS, storageKey, initialState, exportBallot, safeURL, copyBallot, responseObject, dimensionCalls};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") mount(JSON.parse(document.getElementById("review-data").textContent));
}());
