async function api(path) {
  const response = await fetch(path);
  const payload = await response.json();
  if (!response.ok || !payload.success) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload.data;
}

const PUBLIC_PREFETCH_TTL_MS = 90_000;

function memoizedApi() {
  const requests = new Map();
  return (path, cacheable = true) => {
    if (!cacheable) return api(path);
    const cached = requests.get(path);
    if (!cached || cached.expiresAt <= Date.now()) {
      const request = api(path).catch((error) => {
        requests.delete(path);
        throw error;
      });
      requests.set(path, { request, expiresAt: Date.now() + PUBLIC_PREFETCH_TTL_MS });
      return request;
    }
    return cached.request;
  };
}

function readPreload() {
  const el = document.querySelector("#__PRELOAD__");
  if (!el) return null;
  try {
    return JSON.parse(el.textContent || "null");
  } catch {
    return null;
  }
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[char]);
}

function dateKey(value) {
  const parts = shanghaiDateParts(value);
  return `${Number(parts.month)}月${Number(parts.day)}日`;
}

function shanghaiDateParts(value) {
  const date = new Date(value);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
}

function dateBucket(value) {
  const parts = shanghaiDateParts(value);
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function isoDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function timeKey(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function itemTime(item) {
  return item.published_at || item.fetched_at;
}

function itemDateBucket(item) {
  return dateBucket(itemTime(item));
}

function excerpt(item) {
  if (item.summary_zh) return item.summary_zh;
  return item.source_kind === "x" ? item.content_text || item.content_preview || "" : item.content_preview || "";
}

const CATEGORY_LABELS = {
  all: "全部",
  model: "模型",
  product: "产品",
  industry: "行业",
  paper: "论文",
  practice: "技巧",
};

const CATEGORY_URL_VALUES = {
  all: "",
  model: "ai-models",
  product: "ai-products",
  industry: "industry",
  paper: "paper",
  practice: "tip",
};

const CATEGORY_FROM_URL = Object.fromEntries(Object.entries(CATEGORY_URL_VALUES).map(([key, value]) => [value, key]));

const CHANNEL_LABELS = {
  all: "全部",
  firstParty: "一手信源",
  news: "资讯",
  x: "推文",
};

const CHANNEL_URL_VALUES = {
  all: "",
  firstParty: "firstParty",
  news: "news",
  x: "x",
};

const CHANNEL_FROM_URL = Object.fromEntries(Object.entries(CHANNEL_URL_VALUES).map(([key, value]) => [value, key]));
const WECHAT_FALLBACK_ICON = "/wechat-icon.svg?v=20260601";
const WECHAT_PAGE_LIMIT = 50;

function currentParams() {
  return new URLSearchParams(location.search);
}

function categoryFromUrl() {
  return CATEGORY_FROM_URL[currentParams().get("category") || ""] || "all";
}

function channelFromUrl() {
  return CHANNEL_FROM_URL[currentParams().get("channel") || ""] || "all";
}

function pageFromUrl() {
  const value = Number(currentParams().get("page") || "1");
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 1;
}

function searchFromUrl() {
  return currentParams().get("q") || "";
}

function updateHiddenFeedInputs(category, dateValue = "", channel = "all") {
  const categoryInput = document.querySelector("#category-param");
  if (categoryInput) categoryInput.value = CATEGORY_URL_VALUES[category] || "";
  const channelInput = document.querySelector("#channel-param");
  if (channelInput) channelInput.value = CHANNEL_URL_VALUES[channel] || "";
  const dateInput = document.querySelector("#daily-date-param");
  if (dateInput) dateInput.value = dateValue || currentParams().get("date") || "";
}

function feedUrl(path, { q = "", category = "all", channel = "all", date = "", page = "" } = {}) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (CATEGORY_URL_VALUES[category]) params.set("category", CATEGORY_URL_VALUES[category]);
  if (CHANNEL_URL_VALUES[channel]) params.set("channel", CHANNEL_URL_VALUES[channel]);
  if (date) params.set("date", date);
  if (page) params.set("page", page);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

function updateFeedUrl(path, { q = "", category = "all", channel = "all", date = "", page = "" } = {}, mode = "replace") {
  const next = feedUrl(path, { q, category, channel, date, page });
  if (mode === "push") history.pushState({}, "", next);
  else history.replaceState({}, "", next);
}

function normalizeFeedUrl(path, params) {
  const next = feedUrl(path, params);
  if (`${location.pathname}${location.search}` !== next) {
    history.replaceState({}, "", next);
  }
}

function updateCategoryControls(root, activeCategory, activeChannel = "all") {
  if (!root) return;
  const q = document.querySelector("#search")?.value.trim() || "";
  const date = document.querySelector("#daily-date")?.value || currentParams().get("date") || "";
  root.querySelectorAll("[data-category]").forEach((control) => {
    const category = control.dataset.category || "all";
    const active = category === activeCategory;
    control.classList.toggle("seg-item-active", active);
    control.setAttribute("aria-pressed", active ? "true" : "false");
    if (control.tagName === "A") {
      control.setAttribute("href", feedUrl(location.pathname || "/", { q, category, channel: activeChannel, date }));
    }
  });
  updateHiddenFeedInputs(activeCategory, date, activeChannel);
}

function bindCategoryControls(onChange) {
  const root = document.querySelector("[data-category-filter]");
  if (!root) return () => {};
  root.addEventListener("click", (event) => {
    const control = event.target.closest("[data-category]");
    if (!control) return;
    event.preventDefault();
    onChange(control.dataset.category || "all");
  });
  return (activeCategory, activeChannel = "all") => updateCategoryControls(root, activeCategory, activeChannel);
}

function updateChannelControls(root, activeChannel, activeCategory = "all") {
  if (!root) return;
  const q = document.querySelector("#search")?.value.trim() || "";
  root.querySelectorAll("[data-channel]").forEach((control) => {
    const channel = control.dataset.channel || "all";
    const active = channel === activeChannel;
    control.classList.toggle("seg-item-active", active);
    control.setAttribute("aria-pressed", active ? "true" : "false");
    if (control.tagName === "A") {
      control.setAttribute("href", feedUrl(location.pathname || "/", { q, category: activeCategory, channel }));
    }
  });
  updateHiddenFeedInputs(activeCategory, "", activeChannel);
}

function bindChannelControls(onChange) {
  const root = document.querySelector("[data-channel-filter]");
  if (!root) return () => {};
  root.addEventListener("click", (event) => {
    const control = event.target.closest("[data-channel]");
    if (!control) return;
    event.preventDefault();
    onChange(control.dataset.channel || "all");
  });
  return (activeChannel, activeCategory = "all") => updateChannelControls(root, activeChannel, activeCategory);
}

function badges(item) {
  const sourceTags = Array.isArray(item.enriched_tags) ? item.enriched_tags : item.topic_tags;
  const topics = Array.isArray(sourceTags) ? sourceTags.slice(0, 4) : [];
  if (!topics.length) topics.push(item.source_kind === "x" ? "社交" : "AI");
  const parts = topics.map((tag) => `<span class="tag">${esc(tag)}</span>`);
  return parts.join("");
}

function scorePill(item) {
  if (item.weighted_score == null) return "";
  const title = "LLM 5 维评分加权后得分（满分 10，阈值 6.5 进精选）。详见关于 → 评分说明";
  const score = Math.round(Number(item.weighted_score) * 10);
  const tier = scoreTierClass(score);
  const selected = item.rank == null ? "" : `<span class="hot-pill">精选</span>`;
  return `<div class="score-stack ${tier}" title="${esc(title)}">${selected}<span class="score-pill ${tier}" title="${esc(title)}">${score}</span></div>`;
}

function scoreTierClass(score) {
  if (score >= 80) return "score-high";
  if (score >= 65) return "score-mid";
  return "score-muted";
}

function sourceInitial(item) {
  return String(sourceDisplayName(item) || "?").trim().slice(0, 1).toUpperCase() || "?";
}

function safeCssUrl(value) {
  return String(value || "").replace(/["\\\n\r]/g, "");
}

function xHandleFromUrl(value) {
  const match = String(value || "").match(/^https?:\/\/(?:www\.)?(?:x|twitter)\.com\/([^/?#]+)/i);
  return match ? match[1] : "";
}

function sourceAvatarUrl(item) {
  if (item.source_kind === "wechat") return item.author_avatar_url || WECHAT_FALLBACK_ICON;
  if (item.source_kind === "x") {
    const handle = xHandleFromUrl(item.source_homepage_url) || xHandleFromUrl(item.url);
    if (handle) return `https://unavatar.io/x/${encodeURIComponent(handle)}`;
  }
  return item.source_icon_url || "";
}

function sourceDisplayName(item) {
  if (item.source_kind === "x") return item.source_name || item.source_id;
  if (item.source_kind === "wechat") return item.author || item.source_name || item.source_id;
  const name = item.source_name || item.source_id;
  const suffixes = {
    openai_blog: "官网动态（RSS）",
    anthropic_news: "Newsroom（RSS）",
    anthropic_blog: "Blog（RSS）",
    claude_code_releases: "GitHub Releases（RSS）",
    huggingface_blog: "Blog（RSS）",
    simonw: "Weblog（RSS）",
    ithome: "RSS",
  };
  return suffixes[item.source_id] ? `${name}：${suffixes[item.source_id]}` : `${name}（RSS）`;
}

function sourceLine(item) {
  const homepage = item.source_homepage_url || item.url || "#";
  const icon = safeCssUrl(sourceAvatarUrl(item));
  const img = icon ? `<img class="source-avatar" src="${esc(icon)}" alt="" loading="lazy" referrerpolicy="no-referrer" onload="this.nextElementSibling.hidden=true" onerror="this.hidden=true">` : "";
  const author = item.author && item.source_kind !== "wechat" ? `<span class="source-author">${esc(item.author)}</span>` : "";
  return `<div class="source-line">
    <a class="source-link" href="${esc(homepage)}" target="_blank" rel="noopener noreferrer">
      <span class="source-icon">${img}<span class="source-initial">${esc(sourceInitial(item))}</span></span>
      <span class="source-name">${esc(sourceDisplayName(item))}</span>
    </a>
    ${author}
  </div>`;
}

function itemHref(item) {
  const url = String(item.url || "");
  return url.split("#", 1)[0] || url;
}

function articleMedia(item) {
  const assets = Array.isArray(item.media_assets) ? item.media_assets.filter((asset) => asset?.type === "image" && asset.url) : [];
  if (!assets.length) return "";
  const label = `打开原文：${itemTitleText(item) || "查看媒体"}`;
  const images = assets.slice(0, 4).map((asset) => `
    <a class="article-media-link" href="${esc(itemHref(item))}" target="_blank" rel="noopener noreferrer" aria-label="${esc(label)}">
      <img class="article-media-img" src="${esc(asset.url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.article-media-link').hidden=true">
    </a>`).join("");
  return `<div class="article-media article-media-count-${Math.min(assets.length, 4)}">${images}</div>`;
}

function itemTitleText(item) {
  return item.title_zh || item.title || excerpt(item);
}

function relatedDiscussions(item) {
  const related = Array.isArray(item.related_discussions) ? item.related_discussions : [];
  if (!related.length) return "";
  const label = `关联讨论 ${related.length} 条`;
  const tooltip = related.map((entry) => {
    const source = entry.source_kind === "x" ? "X" : "来源";
    const authorName = String(entry.author || "");
    const author = authorName ? ` (${authorName.startsWith("@") ? authorName : `@${authorName}`})` : "";
    return `<span class="dup-tooltip-item">${esc(source)}：${esc(entry.source_name || entry.source_id)}${esc(author)}</span>`;
  }).join("");
  return `<span class="timeline-dup-count timeline-dup-hover">${esc(label)}<span class="dup-tooltip">${tooltip}</span></span>`;
}

function itemCard(item, showScore, options = {}) {
  const compact = Boolean(options.compact);
  const showReason = options.showReason === "selected" ? item.rank != null : options.showReason !== false;
  const reason = !compact && showReason && item.reasoning ? `<div class="reason">推荐理由：${esc(item.reasoning)}</div>` : "";
  const showRelated = !compact && options.showRelated !== false;
  const isX = item.source_kind === "x";
  const itemTitle = itemTitleText(item);
  const title = itemTitle
    ? `<a class="item-title" href="${esc(itemHref(item))}" target="_blank" rel="noopener noreferrer">${esc(itemTitle)}</a>`
    : "";
  const media = compact ? "" : articleMedia(item);
  return `<article class="item-row timeline-card${isX ? " x-card" : ""}${compact ? " compact-card" : ""}${options.clampSummary ? " clamped-card" : ""}" data-item-id="${esc(item.id)}" data-source-id="${esc(item.source_id)}" data-published-date="${esc(itemDateBucket(item))}" data-published-at="${esc(isoDateTime(itemTime(item)))}">
    <div class="card-topline">
      ${sourceLine(item)}
      ${showScore ? scorePill(item) : ""}
    </div>
    ${title}
    <p class="summary">${esc(excerpt(item))}</p>
    ${media}
    <div class="tags">${badges(item)}</div>
    ${showRelated ? relatedDiscussions(item) : ""}
    ${reason ? '<hr class="timeline-divider">' : ""}
    ${reason}
  </article>`;
}

function itemTimestamp(item) {
  const value = new Date(itemTime(item)).getTime();
  return Number.isNaN(value) ? 0 : value;
}

function compareByTimeDesc(a, b) {
  return itemTimestamp(b) - itemTimestamp(a)
    || String(b.fetched_at || "").localeCompare(String(a.fetched_at || ""))
    || String(b.id || "").localeCompare(String(a.id || ""));
}

function compareByScoreDesc(a, b) {
  return itemDateBucket(b).localeCompare(itemDateBucket(a))
    || Number(b.weighted_score || 0) - Number(a.weighted_score || 0)
    || compareByTimeDesc(a, b);
}

function renderTimeline(container, items, options = {}) {
  const showScore = Boolean(options.showScore);
  const compact = Boolean(options.compact);
  const emptyTitle = options.emptyTitle || "暂无内容";
  const emptyBody = options.emptyBody || "稍后再回来看看。";
  if (!items.length) {
    container.innerHTML = `<div class="empty-state">
      <h2>${esc(emptyTitle)}</h2>
      <p>${esc(emptyBody)}</p>
    </div>`;
    return;
  }
  let lastDate = "";
  const sortByScore = options.sortByScore === true;
  const renderedItems = [...items].sort(sortByScore ? compareByScoreDesc : compareByTimeDesc);
  container.innerHTML = renderedItems.map((item) => {
    const day = dateKey(itemTime(item));
    const bucket = itemDateBucket(item);
    const dateLabel = day === lastDate ? "" : `<div class="timeline-date date-group"><time datetime="${esc(bucket)}" title="${esc(bucket)}">${esc(day)}</time></div>`;
    lastDate = day;
    return `${dateLabel}<div class="timeline-entry">
      <div class="timeline-time"><time datetime="${esc(isoDateTime(itemTime(item)))}" title="${esc(bucket)}">${esc(timeKey(itemTime(item)))}</time><span></span></div>
      ${itemCard(item, showScore, {
        compact,
        showReason: options.showReason,
        showRelated: options.showRelated,
        clampSummary: options.clampSummary,
      })}
    </div>`;
  }).join("");
}

function wechatCard(item) {
  const tags = Array.isArray(item.tags) ? item.tags.slice(0, 5) : [];
  const avatar = safeCssUrl(item.avatar_url || WECHAT_FALLBACK_ICON);
  const img = avatar ? `<img class="source-avatar" src="${esc(avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer" onload="this.nextElementSibling.hidden=true" onerror="this.hidden=true">` : "";
  const source = item.author || "微信公众号";
  const recommendation = item.recommendation ? `<span class="hot-pill">${esc(item.recommendation)}</span>` : "";
  const origin = item.url ? `<a class="wechat-card-origin" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">原文 <span aria-hidden="true">↗</span></a>` : "";
  const detailUrl = item.detail_url || `/wechat/${item.slug}`;
  return `<article class="item-row timeline-card wechat-card" data-detail-url="${esc(detailUrl)}" role="link" tabindex="0">
    <div class="card-topline">
      <div class="source-line">
        <a class="source-link" href="${esc(item.url || "#")}" target="_blank" rel="noopener noreferrer">
          <span class="source-icon">${img}<span class="source-initial">${esc(source.slice(0, 1) || "?")}</span></span>
          <span class="source-name">${esc(source)}</span>
        </a>
      </div>
      <div class="card-topline-end">${recommendation}${origin}</div>
    </div>
    <a class="item-title" href="${esc(detailUrl)}">${esc(item.title || "")}</a>
    <p class="summary">${esc(item.abstract || "")}</p>
    <div class="tags">${tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}</div>
  </article>`;
}

function renderWechatTimeline(container, items, { hasQuery = false } = {}) {
  if (!container) return;
  if (!items.length) {
    container.innerHTML = `<div class="empty-state">
      <h2>${hasQuery ? "没有匹配条目" : "暂无微信文章解读"}</h2>
      <p>${hasQuery ? "清空搜索后可回到默认列表。" : "完成下一轮解读后会在这里显示。"}</p>
    </div>`;
    return;
  }
  let lastDate = "";
  container.innerHTML = [...items].sort(compareByTimeDesc).map((item) => {
    const when = item.published_at || "";
    const day = dateKey(when);
    const bucket = dateBucket(when);
    const dateLabel = day === lastDate ? "" : `<div class="timeline-date date-group"><time datetime="${esc(bucket)}" title="${esc(bucket)}">${esc(day)}</time></div>`;
    lastDate = day;
    return `${dateLabel}<div class="timeline-entry">
      <div class="timeline-time"><time datetime="${esc(isoDateTime(when))}" title="${esc(bucket)}">${esc(timeKey(when))}</time><span></span></div>
      ${wechatCard(item)}
    </div>`;
  }).join("");
}

function initNavigation() {
  const sidebar = document.querySelector(".sidebar");
  const toggle = document.querySelector(".app-hamburger");
  const close = document.querySelector(".sidebar-close");
  if (!sidebar || !toggle || toggle.dataset.bound === "true") return;
  const mobileQuery = window.matchMedia("(max-width: 760px)");
  const setSidebarInteractivity = (open) => {
    const hiddenDrawer = mobileQuery.matches && !open;
    sidebar.toggleAttribute("inert", hiddenDrawer);
    if (hiddenDrawer) sidebar.setAttribute("aria-hidden", "true");
    else sidebar.removeAttribute("aria-hidden");
  };
  const setOpen = (open) => {
    document.body.classList.toggle("sidebar-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    setSidebarInteractivity(open);
    if (open) close?.focus();
    else if (sidebar.contains(document.activeElement)) toggle.focus();
  };
  toggle.dataset.bound = "true";
  setSidebarInteractivity(document.body.classList.contains("sidebar-open"));
  mobileQuery.addEventListener?.("change", () => {
    setSidebarInteractivity(document.body.classList.contains("sidebar-open"));
  });
  toggle.addEventListener("click", () => {
    setOpen(!document.body.classList.contains("sidebar-open"));
  });
  close?.addEventListener("click", () => setOpen(false));
  sidebar.querySelectorAll(".side-link").forEach((link) => {
    link.addEventListener("click", () => {
      setOpen(false);
    });
  });
}

export function initNavigationOnly() {
  initNavigation();
}

function queryPath(path, params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== "") search.set(key, value);
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function listScrollKey() {
  return `ai-radar-scroll:${location.pathname}`;
}

function rememberListScroll(list) {
  list.addEventListener("click", (event) => {
    const link = event.target.closest("a.item-title");
    if (link) sessionStorage.setItem(listScrollKey(), String(window.scrollY));
  });
}

function navigateWechatCard(card) {
  const detailUrl = card?.dataset?.detailUrl;
  if (!detailUrl) return;
  sessionStorage.setItem(listScrollKey(), String(window.scrollY));
  window.location.href = detailUrl;
}

function bindWechatCardNavigation(list) {
  if (!list || list.dataset.wechatCardNavBound === "true") return;
  list.dataset.wechatCardNavBound = "true";
  list.addEventListener("click", (event) => {
    const card = event.target.closest(".wechat-card[data-detail-url]");
    if (!card || !list.contains(card) || event.target.closest("a, button")) return;
    navigateWechatCard(card);
  });
  list.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const card = event.target.closest(".wechat-card[data-detail-url]");
    if (!card || event.target !== card) return;
    event.preventDefault();
    navigateWechatCard(card);
  });
}

function restoreListScroll() {
  const value = sessionStorage.getItem(listScrollKey());
  if (!value) return;
  requestAnimationFrame(() => window.scrollTo(0, Number(value)));
}

export function paginationPages(current, totalPages) {
  const safeTotal = Math.max(0, Math.floor(Number(totalPages || 0)));
  if (safeTotal <= 0) return [];
  const safeCurrent = Math.min(Math.max(Math.floor(Number(current || 1)), 1), safeTotal);
  const pages = new Set([1, safeTotal]);
  const endOverflow = Math.max(0, safeCurrent + 2 - safeTotal);
  const start = safeCurrent - 2 - endOverflow;
  const end = safeCurrent + 2 - endOverflow;
  for (let page = start; page <= end; page += 1) {
    pages.add(page);
  }
  return [...pages].filter((page) => page >= 1 && page <= safeTotal).sort((a, b) => a - b);
}

export function paginationSequence(current, totalPages) {
  const sequence = [];
  let last = 0;
  for (const value of paginationPages(current, totalPages)) {
    if (last && value - last > 1) sequence.push("...");
    sequence.push(value);
    last = value;
  }
  return sequence;
}

export function paginationState({ page = 1, total = 0, limit = 1 } = {}) {
  const safeLimit = Math.max(1, Number(limit || 1));
  const safeTotal = Math.max(0, Number(total || 0));
  const totalPages = Math.max(1, Math.ceil(safeTotal / safeLimit));
  if (totalPages <= 1) {
    return {
      hidden: true,
      current: 1,
      totalPages,
      hasPrev: false,
      hasNext: false,
      sequence: [],
    };
  }
  const current = Math.min(Math.max(Math.floor(Number(page || 1)), 1), totalPages);
  return {
    hidden: false,
    current,
    totalPages,
    hasPrev: current > 1,
    hasNext: current < totalPages,
    sequence: paginationSequence(current, totalPages),
  };
}

function paginationLink(label, page, { href, current = false, rel = "" }) {
  const attrs = [
    `class="pagination-link${current ? " pagination-link-active" : ""}"`,
    `href="${esc(href)}"`,
    `data-page="${esc(page)}"`,
  ];
  if (current) attrs.push('aria-current="page"');
  if (rel) attrs.push(`rel="${esc(rel)}"`);
  return `<a ${attrs.join(" ")}>${esc(label)}</a>`;
}

function renderPaginationControls(root, stateArgs, urlForPage) {
  if (!root) return;
  const state = paginationState(stateArgs);
  if (state.hidden) {
    root.hidden = true;
    root.innerHTML = "";
    return;
  }
  const parts = [];
  if (state.hasPrev) {
    parts.push(paginationLink("‹ 上一页", state.current - 1, { href: urlForPage(state.current - 1), rel: "prev" }));
  }
  for (const value of state.sequence) {
    if (value === "...") {
      parts.push('<span class="pagination-gap">…</span>');
      continue;
    }
    parts.push(
      paginationLink(String(value), value, {
        href: urlForPage(value),
        current: value === state.current,
      })
    );
  }
  if (state.hasNext) {
    parts.push(paginationLink("下一页 ›", state.current + 1, { href: urlForPage(state.current + 1), rel: "next" }));
  }
  root.hidden = false;
  root.innerHTML = parts.join("");
}

function renderWechatPagination(root, { page = 1, total = 0, limit = 50 } = {}, q = "") {
  renderPaginationControls(root, { page, total, limit }, (value) =>
    feedUrl("/wechat", { q, page: String(value) })
  );
}

function renderPagination(root, { page, total, limit, q = "", category = "all", channel = "all", path = "/all" }) {
  renderPaginationControls(root, { page, total, limit }, (value) =>
    feedUrl(path, { q, category, channel, page: String(value) })
  );
}

function debounceInput(input, callback) {
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(callback, 200);
  });
}

function renderTimelineLoading(container) {
  if (!container) return;
  container.innerHTML = `<div class="timeline-loading" role="status" aria-live="polite">
    <span class="timeline-loading-dot" aria-hidden="true"></span>
    <span>正在加载</span>
  </div>`;
}

function renderTimelineError(container, error, retry) {
  if (!container) return;
  const message = error instanceof Error && error.message ? error.message : "请求失败";
  container.innerHTML = `<div class="empty-state feed-error" role="alert">
    <h2>加载失败</h2>
    <p>${esc(message)}。请稍后重试。</p>
    <div class="empty-actions">
      <button type="button" data-retry-feed>重新加载</button>
    </div>
  </div>`;
  container.querySelector("[data-retry-feed]")?.addEventListener("click", retry);
}

export async function initWechat() {
  initNavigation();
  const list = document.querySelector("#list");
  const pagination = document.querySelector("#pagination");
  const search = document.querySelector("#search");
  if (!search) return;
  let currentPage = pageFromUrl();
  const loadWechatPage = memoizedApi();

  function wechatApiPath(page) {
    return queryPath("/api/v1/wechat", {
      page,
      q: search.value.trim(),
      limit: WECHAT_PAGE_LIMIT,
    });
  }

  function prefetchNextWechatPage(data) {
    if (search.value.trim()) return;
    const state = paginationState(data);
    if (state.hasNext) void loadWechatPage(wechatApiPath(state.current + 1)).catch(() => {});
  }

  function wechatUrlParams(page = currentPage) {
    return {
      q: search.value.trim(),
      page: page > 1 ? String(page) : "",
    };
  }

  function syncWechatUrl(page = currentPage, mode = "replace") {
    updateFeedUrl("/wechat", wechatUrlParams(page), mode);
  }

  search.value = searchFromUrl();
  normalizeFeedUrl("/wechat", wechatUrlParams(currentPage));

  function renderView(data) {
    renderWechatTimeline(list, Array.isArray(data.items) ? data.items : [], {
      hasQuery: Boolean(search.value.trim()),
    });
    renderWechatPagination(pagination, data, search.value.trim());
    prefetchNextWechatPage(data);
  }

  async function load(page = pageFromUrl(), mode = "replace", updateUrl = true) {
    currentPage = page;
    if (updateUrl) syncWechatUrl(page, mode);
    renderTimelineLoading(list);
    if (pagination) pagination.hidden = true;
    try {
      const data = await loadWechatPage(wechatApiPath(page), !search.value.trim());
      const responsePage = Number(data.page || page);
      currentPage = responsePage;
      if (responsePage !== page || pageFromUrl() !== responsePage) {
        syncWechatUrl(responsePage, "replace");
      }
      const total = Number(data.total || 0);
      const responseLimit = Number(data.limit || 50);
      const totalPages = Math.max(1, Math.ceil(total / responseLimit));
      if (total > 0 && !data.items.length && page > totalPages) {
        await load(totalPages, "replace");
        return;
      }
      renderView(data);
    } catch (error) {
      renderTimelineError(list, error, () => load(page, "replace"));
    }
  }

  async function runSearch() {
    await load(1, "replace");
  }

  debounceInput(search, runSearch);
  search.closest("form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch();
  });
  pagination?.addEventListener("click", (event) => {
    const link = event.target.closest("[data-page]");
    if (!link) return;
    event.preventDefault();
    load(Number(link.dataset.page || "1"), "push");
  });
  window.addEventListener("popstate", () => {
    search.value = searchFromUrl();
    currentPage = pageFromUrl();
    normalizeFeedUrl("/wechat", wechatUrlParams(currentPage));
    load(currentPage, "replace", false);
  });
  bindWechatCardNavigation(list);
  rememberListScroll(list);
  const preload = readPreload();
  if (preload && Array.isArray(preload.items)) {
    currentPage = Number(preload.page || currentPage);
    if (currentPage !== pageFromUrl()) {
      syncWechatUrl(currentPage, "replace");
    }
    renderView(preload);
  } else {
    await load(currentPage, "replace", false);
  }
  restoreListScroll();
}

export async function initTimeline() {
  initNavigation();
  const list = document.querySelector("#list");
  const pagination = document.querySelector("#pagination");
  const search = document.querySelector("#search");
  let activeCategory = categoryFromUrl();
  let activeChannel = channelFromUrl();
  let visibleItems = [];
  let currentPage = pageFromUrl();
  const syncCategoryControls = bindCategoryControls((category) => {
    activeCategory = category;
    load({ page: 1, mode: "push" });
  });
  const syncChannelControls = bindChannelControls((channel) => {
    activeChannel = channel;
    load({ page: 1, mode: "push" });
  });
  function timelineUrlParams(page = currentPage) {
    return {
      q: search.value.trim(),
      category: activeCategory,
      channel: activeChannel,
      page: page > 1 ? String(page) : "",
    };
  }
  function syncTimelineUrl(page = currentPage, mode = "replace") {
    updateFeedUrl("/all", timelineUrlParams(page), mode);
  }
  search.value = searchFromUrl();
  normalizeFeedUrl("/all", {
    q: search.value.trim(),
    category: activeCategory,
    channel: activeChannel,
    page: currentPage > 1 ? String(currentPage) : "",
  });
  syncCategoryControls(activeCategory, activeChannel);
  syncChannelControls(activeChannel, activeCategory);

  function renderView(rawItems, meta = {}) {
    visibleItems = rawItems;
    syncCategoryControls(activeCategory, activeChannel);
    syncChannelControls(activeChannel, activeCategory);
    const hasQuery = Boolean(search.value.trim());
    renderTimeline(list, rawItems, {
      showScore: true,
      showReason: "selected",
      showRelated: false,
      sortByScore: false,
      clampSummary: true,
      emptyTitle: hasQuery ? "没有匹配条目" : activeCategory === "all" ? "暂无内容" : `${CATEGORY_LABELS[activeCategory]}分类暂无内容`,
      emptyBody: hasQuery ? "清空搜索后可回到默认列表。" : activeCategory === "all" ? "当前还没有可展示的 AI 动态。" : `可以切换到${CHANNEL_LABELS[activeChannel]}全部内容继续浏览。`,
    });
    renderPagination(pagination, {
      page: meta.page || currentPage,
      total: meta.total || rawItems.length,
      limit: meta.limit || 40,
      q: search.value.trim(),
      category: activeCategory,
      channel: activeChannel,
    });
  }

  async function load({ page = pageFromUrl(), mode = "replace", updateUrl = true } = {}) {
    currentPage = page;
    const q = search.value.trim();
    if (updateUrl) syncTimelineUrl(page, mode);
    syncCategoryControls(activeCategory, activeChannel);
    syncChannelControls(activeChannel, activeCategory);
    renderTimelineLoading(list);
    if (pagination) pagination.hidden = true;
    try {
      const data = await api(queryPath("/api/v1/timeline", {
        limit: 40,
        page,
        q,
        channel: CHANNEL_URL_VALUES[activeChannel] || "",
        category: CATEGORY_URL_VALUES[activeCategory] || "",
      }));
      const responsePage = Number(data.page || page);
      currentPage = responsePage;
      if (responsePage !== page || pageFromUrl() !== responsePage) {
        syncTimelineUrl(responsePage, "replace");
      }
      const total = Number(data.total || 0);
      const limit = Number(data.limit || 40);
      const totalPages = Math.max(1, Math.ceil(total / limit));
      if (total > 0 && !data.items.length && page > totalPages) {
        await load({ page: totalPages, mode: "replace" });
        return;
      }
      renderView(data.items, data);
    } catch (error) {
      if (pagination) pagination.hidden = true;
      renderTimelineError(list, error, () => load({ page, mode: "replace" }));
    }
  }

  async function runSearch() {
    await load({ page: 1, mode: "replace" });
  }

  debounceInput(search, runSearch);
  search.closest("form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch();
  });
  pagination?.addEventListener("click", (event) => {
    const link = event.target.closest("[data-page]");
    if (!link) return;
    event.preventDefault();
    const nextPage = Number(link.dataset.page || "1");
    load({ page: nextPage, mode: "push" });
  });
  window.addEventListener("popstate", () => {
    activeCategory = categoryFromUrl();
    activeChannel = channelFromUrl();
    search.value = searchFromUrl();
    normalizeFeedUrl("/all", {
      q: search.value.trim(),
      category: activeCategory,
      channel: activeChannel,
      page: pageFromUrl() > 1 ? String(pageFromUrl()) : "",
    });
    load({ page: pageFromUrl(), updateUrl: false });
  });
  rememberListScroll(list);
  const preload = readPreload();
  if (preload && Array.isArray(preload.items)) {
    currentPage = Number(preload.page || currentPage);
    if (currentPage !== pageFromUrl()) {
      syncTimelineUrl(currentPage, "replace");
    }
    renderView(preload.items, preload);
  } else {
    await load({ page: currentPage, updateUrl: false });
  }
  restoreListScroll();
}

export async function initCurated() {
  initNavigation();
  const search = document.querySelector("#search");
  const list = document.querySelector("#list");
  const pagination = document.querySelector("#pagination");
  const runMeta = document.querySelector("#run-meta");
  let activeCategory = categoryFromUrl();
  let currentPage = pageFromUrl();
  const loadCuratedPage = memoizedApi();
  if (runMeta) runMeta.textContent = "AI 自动挑选的高价值内容（日期为原文发布日，截至 2026 年）";
  const syncCategoryControls = bindCategoryControls((category) => {
    activeCategory = category;
    void load({ page: 1, mode: "push" });
  });
  function curatedUrlParams(page = currentPage) {
    return {
      q: search.value.trim(),
      category: activeCategory,
      page: page > 1 ? String(page) : "",
    };
  }
  function syncCuratedUrl(page = currentPage, mode = "replace") {
    updateFeedUrl("/", {
      q: search.value.trim(),
      category: activeCategory,
      page: page > 1 ? String(page) : "",
    }, mode);
  }

  function curatedApiPath(page) {
    return queryPath("/api/v1/curated", {
      limit: 40,
      page,
      q: search.value.trim(),
      category: CATEGORY_URL_VALUES[activeCategory] || "",
    });
  }

  function prefetchNextCuratedPage(meta) {
    if (search.value.trim() || activeCategory !== "all") return;
    const state = paginationState(meta);
    if (state.hasNext) void loadCuratedPage(curatedApiPath(state.current + 1)).catch(() => {});
  }
  search.value = searchFromUrl();
  normalizeFeedUrl("/", curatedUrlParams(currentPage));
  syncCategoryControls(activeCategory);

  function renderView(rawItems, meta = {}) {
    syncCategoryControls(activeCategory);
    const q = search.value.trim();
    renderTimeline(list, rawItems, {
      showScore: true,
      sortByScore: false,
      emptyTitle: q ? "没有匹配条目" : activeCategory === "all" ? "暂无精选条目" : `${CATEGORY_LABELS[activeCategory]}分类暂无精选`,
      emptyBody: q ? "清空搜索后可回到默认列表。" : "可以切换到全部继续浏览精选内容。",
    });
    renderPagination(pagination, {
      page: meta.page || currentPage,
      total: meta.total || rawItems.length,
      limit: meta.limit || 40,
      q,
      category: activeCategory,
      path: "/",
    });
    prefetchNextCuratedPage({
      page: meta.page || currentPage,
      total: meta.total || rawItems.length,
      limit: meta.limit || 40,
    });
  }

  async function load({ page = pageFromUrl(), mode = "replace", updateUrl = true } = {}) {
    currentPage = page;
    if (updateUrl) syncCuratedUrl(page, mode);
    syncCategoryControls(activeCategory);
    renderTimelineLoading(list);
    if (pagination) pagination.hidden = true;
    try {
      const data = await loadCuratedPage(
        curatedApiPath(page),
        !search.value.trim() && activeCategory === "all",
      );
      const responsePage = Number(data.page || page);
      currentPage = responsePage;
      if (responsePage !== page || pageFromUrl() !== responsePage) {
        syncCuratedUrl(responsePage, "replace");
      }
      const total = Number(data.total || 0);
      const responseLimit = Number(data.limit || 40);
      const totalPages = Math.max(1, Math.ceil(total / responseLimit));
      if (total > 0 && !data.items.length && page > totalPages) {
        await load({ page: totalPages, mode: "replace" });
        return;
      }
      renderView(data.items, data);
    } catch (error) {
      if (pagination) pagination.hidden = true;
      renderTimelineError(list, error, () => load({ page, mode: "replace" }));
    }
  }

  async function runSearch() {
    await load({ page: 1, mode: "replace" });
  }

  debounceInput(search, runSearch);
  search.closest("form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    runSearch();
  });
  pagination?.addEventListener("click", (event) => {
    const link = event.target.closest("[data-page]");
    if (!link) return;
    event.preventDefault();
    const nextPage = Number(link.dataset.page || "1");
    load({ page: nextPage, mode: "push" });
  });
  window.addEventListener("popstate", () => {
    activeCategory = categoryFromUrl();
    search.value = searchFromUrl();
    currentPage = pageFromUrl();
    normalizeFeedUrl("/", curatedUrlParams(currentPage));
    load({ page: currentPage, updateUrl: false });
  });
  rememberListScroll(list);
  const preload = readPreload();
  if (preload && Array.isArray(preload.items)) {
    currentPage = Number(preload.page || currentPage);
    if (currentPage !== pageFromUrl()) {
      syncCuratedUrl(currentPage, "replace");
    }
    renderView(preload.items, preload);
  } else {
    await load({ page: currentPage, updateUrl: false });
  }
  restoreListScroll();
}

function todayIso() {
  return dateBucket(new Date().toISOString());
}

function isDateString(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value || "") && !Number.isNaN(new Date(`${value}T00:00:00Z`).getTime());
}

function isFutureDate(value) {
  return isDateString(value) && value > todayIso();
}

function shouldFallbackToRecentContentDate(value) {
  return !value || !isDateString(value) || isFutureDate(value);
}

function addDays(value, days) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

const DAILY_SECTION_DEFS = [
  {
    key: "model",
    number: "01",
    title: "模型发布/更新",
    subtitle: "MODEL RELEASES",
    tags: ["模型发布", "评测/基准"],
  },
  {
    key: "product",
    number: "02",
    title: "产品发布/更新",
    subtitle: "PRODUCT",
    tags: ["产品更新", "MCP/工具", "多模态", "编码", "搜索", "图像生成", "视频"],
  },
  {
    key: "industry",
    number: "03",
    title: "行业动态",
    subtitle: "INDUSTRY",
    tags: ["行业动态", "安全/对齐", "现象/趋势"],
  },
  {
    key: "paper",
    number: "04",
    title: "论文研究",
    subtitle: "RESEARCH",
    tags: ["论文/研究", "arXiv", "研究"],
  },
  {
    key: "practice",
    number: "05",
    title: "技巧与观点",
    subtitle: "TIPS & TAKES",
    tags: ["教程/实践", "开源/仓库", "端侧", "部署/工程", "大佬观点"],
  },
];

const CHINESE_DIGITS = ["〇", "一", "二", "三", "四", "五", "六", "七", "八", "九"];

function dailyDateFromPath() {
  const match = location.pathname.match(/^\/daily\/(\d{4}-\d{2}-\d{2})\/?$/);
  return match ? match[1] : "";
}

function dailyPath(dateValue = "") {
  return dateValue ? `/daily/${dateValue}` : "/daily";
}

function dailyYearLabel(year) {
  return String(year).split("").map((digit) => CHINESE_DIGITS[Number(digit)] || digit).join("");
}

function chineseNumber(value) {
  if (value <= 10) return value === 10 ? "十" : CHINESE_DIGITS[value];
  if (value < 20) return `十${CHINESE_DIGITS[value - 10]}`;
  const tens = Math.floor(value / 10);
  const ones = value % 10;
  return `${CHINESE_DIGITS[tens]}十${ones ? CHINESE_DIGITS[ones] : ""}`;
}

function readableDailyDate(value) {
  if (!isDateString(value)) return "";
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  const weekday = new Intl.DateTimeFormat("zh-CN", { weekday: "long", timeZone: "UTC" }).format(date);
  return `${dailyYearLabel(year)}年${chineseNumber(month)}月${chineseNumber(day)}日　${weekday}`;
}

function dailySectionKey(item) {
  const tags = Array.isArray(item.topic_tags) ? item.topic_tags : [];
  const matched = DAILY_SECTION_DEFS.find((section) => section.tags.some((tag) => tags.includes(tag)));
  if (matched) return matched.key;
  return item.source_kind === "x" ? "practice" : "industry";
}

function dailySourceMeta(item) {
  const sourceType = item.source_kind === "x" ? (item.tier === "T1" ? "官方·X" : "X") : (item.tier === "T1" ? "官方" : "综合资讯");
  const source = sourceDisplayName(item) || "来源";
  const author = item.author && item.source_kind !== "wechat" ? `：${item.author}` : "";
  return `${sourceType} · ${source}${author}`;
}

function dailySourceParts(item) {
  const role = item.source_kind === "x" ? (item.tier === "T1" ? "官方·X" : "X") : (item.tier === "T1" ? "官方" : "综合资讯");
  const source = sourceDisplayName(item) || "来源";
  const author = item.author ? ` (${item.author.startsWith("@") ? item.author : `@${item.author}`})` : "";
  const label = item.source_kind === "x" ? `X：${source}${author}` : `${source}`;
  return { role, label, avatar: sourceAvatarUrl(item) };
}

function renderDailyReport(container, items, activeDate) {
  if (!items.length) {
    container.innerHTML = `<div class="daily-empty">
      <h2>${esc(activeDate)}：当日没有日报内容</h2>
      <p>可以从左侧归档选择最近一期，或返回最新日报。</p>
    </div>`;
    return;
  }
  const grouped = Object.fromEntries(DAILY_SECTION_DEFS.map((section) => [section.key, []]));
  items.forEach((item) => {
    grouped[dailySectionKey(item)].push(item);
  });
  container.innerHTML = DAILY_SECTION_DEFS
    .filter((section) => grouped[section.key].length)
    .map((section) => {
      const sectionItems = grouped[section.key];
      const articles = sectionItems.map((item) => {
        const title = itemTitleText(item);
        const source = dailySourceParts(item);
        const avatar = source.avatar ? `<img class="daily-source-avatar" src="${esc(safeCssUrl(source.avatar))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.hidden=true">` : "";
        return `<article class="daily-article" data-published-date="${esc(itemDateBucket(item))}">
          <h3 class="daily-article-title"><a href="${esc(itemHref(item))}" target="_blank" rel="noopener noreferrer">${esc(title)}</a></h3>
          <div class="daily-article-source daily-article-meta"><span class="role-tag">${esc(source.role)}</span>${avatar}<span>${esc(source.label)}</span></div>
          <p class="daily-article-summary">${esc(excerpt(item))}</p>
        </article>`;
      }).join("");
      return `<section class="daily-section" data-section="${esc(section.key)}">
        <header class="daily-section-header">
          <div class="daily-section-no daily-section-number">${esc(section.number)}</div>
          <h2 class="daily-section-title">${esc(section.title)}</h2>
          <span class="daily-section-subtitle">${esc(section.subtitle)}</span>
          <div class="daily-section-count"><strong>${sectionItems.length}</strong><span> 篇</span></div>
        </header>
        <div class="daily-section-articles daily-article-list">${articles}</div>
      </section>`;
    })
    .join("");
}

function renderDailyHeader(activeDate, count) {
  const volume = document.querySelector("#daily-volume");
  const storyCount = document.querySelector(".daily-story-count");
  const readableDate = document.querySelector(".daily-readable-date");
  if (volume) volume.textContent = `VOL.${activeDate.replaceAll("-", ".")}`;
  if (storyCount) storyCount.textContent = `${count} STORIES`;
  if (readableDate) {
    readableDate.textContent = readableDailyDate(activeDate);
    readableDate.setAttribute("datetime", activeDate);
  }
}

async function renderDailyArchive(activeDate, latestAvailableDate) {
  const archive = document.querySelector("#daily-archive");
  const latestDateEl = document.querySelector("#daily-latest-date");
  if (latestDateEl) {
    latestDateEl.textContent = latestAvailableDate;
    latestDateEl.setAttribute("datetime", latestAvailableDate);
  }
  if (!archive) return;
  const archiveAnchorDate = latestAvailableDate || activeDate;
  const candidates = Array.from({ length: 16 }, (_, index) => addDays(archiveAnchorDate, -index));
  const results = await Promise.all(candidates.map(async (dateValue) => {
    const data = await api(queryPath("/api/v1/curated", { date: dateValue }));
    if (!data.items.length) return null;
    return {
      date: dateValue,
      title: itemTitleText(data.items[0]),
      count: data.count,
    };
  }));
  const days = results.filter(Boolean).slice(0, 12);
  const monthLabel = archiveAnchorDate.slice(0, 7).replace("-", " 年 ") + " 月";
  archive.innerHTML = `<div class="daily-archive-month">${esc(monthLabel)}</div>
    ${days.map((day) => `<a class="daily-side-day${day.date === activeDate ? " is-active" : ""}" href="${esc(dailyPath(day.date))}">
      <span>${Number(day.date.slice(8, 10))} 日</span>
      <strong>${esc(day.title)}</strong>
      <em>${day.count}</em>
    </a>`).join("")}`;
}

export async function initDaily() {
  initNavigation();
  const list = document.querySelector("#daily-sections");
  const previousLink = document.querySelector(".daily-prev");
  const nextLink = document.querySelector(".daily-next");
  const fallbackBanner = document.querySelector("#daily-fallback");
  const requestedDate = currentParams().get("date") || dailyDateFromPath();
  let activeDate = isDateString(requestedDate) && !isFutureDate(requestedDate) ? requestedDate : todayIso();
  let latestAvailableDate = "";

  function setFallbackBanner(requested, resolved) {
    if (!fallbackBanner) return;
    if (!requested || requested === resolved) {
      fallbackBanner.hidden = true;
      fallbackBanner.textContent = "";
      return;
    }
    fallbackBanner.hidden = false;
    fallbackBanner.textContent = `日期 ${requested} 无效或无内容，已切到最近一期 ${resolved}`;
  }

  function syncDateControls(latestDate = "") {
    if (previousLink) previousLink.href = dailyPath(addDays(activeDate, -1));
    if (nextLink) {
      const nextDate = addDays(activeDate, 1);
      const isFutureIssue = latestDate && nextDate > latestDate;
      nextLink.hidden = isFutureIssue;
      if (isFutureIssue) {
        nextLink.removeAttribute("href");
      } else {
        nextLink.href = dailyPath(nextDate);
      }
    }
  }

  function updateUrl(mode = "push", dateValue = activeDate) {
    const next = dateValue ? dailyPath(dateValue) : "/daily";
    if (mode === "replace") {
      history.replaceState({}, "", next);
    } else {
      history.pushState({}, "", next);
    }
  }

  async function latestContentDate() {
    if (latestAvailableDate) return latestAvailableDate;
    const data = await api("/api/v1/curated");
    latestAvailableDate = data.items.length ? itemDateBucket(data.items[0]) : data.date || todayIso();
    return latestAvailableDate;
  }

  async function load(requested = activeDate, options = {}) {
    const latest = await latestContentDate();
    const data = await api(queryPath("/api/v1/curated", { date: activeDate }));
    if (options.allowRecentFallback && !data.items.length) {
      if (latest && latest !== activeDate) {
        activeDate = latest;
        updateUrl("replace");
        return load(requested, { allowRecentFallback: false });
      }
    }
    const resolvedDate = data.date || activeDate;
    if (resolvedDate !== activeDate) {
      activeDate = resolvedDate;
      updateUrl("replace");
    }
    activeDate = data.date || activeDate;
    renderDailyHeader(activeDate, data.count);
    setFallbackBanner(requested, activeDate);
    syncDateControls(latest);
    renderDailyReport(list, data.items, activeDate);
    await renderDailyArchive(activeDate, latest);
  }

  async function goToDate(nextDate, mode = "push") {
    const requested = nextDate;
    activeDate = isDateString(nextDate) ? nextDate : todayIso();
    updateUrl(mode);
    await load(requested);
  }

  if (previousLink) previousLink.addEventListener("click", (event) => {
    event.preventDefault();
    goToDate(addDays(activeDate, -1));
  });
  if (nextLink) nextLink.addEventListener("click", (event) => {
    event.preventDefault();
    goToDate(addDays(activeDate, 1));
  });
  rememberListScroll(list);
  if (currentParams().get("date") && isDateString(requestedDate) && !isFutureDate(requestedDate)) updateUrl("replace");
  if (!requestedDate || !isDateString(requestedDate) || isFutureDate(requestedDate)) {
    const latest = await latestContentDate();
    activeDate = latest || activeDate;
    if (requestedDate) updateUrl("replace");
  }
  await load(requestedDate || activeDate, { allowRecentFallback: shouldFallbackToRecentContentDate(requestedDate) });
  restoreListScroll();
}

export async function initAbout() {
  initNavigation();
  const search = document.querySelector("#search");
  const table = document.querySelector("#sources-table");
  let sources = [];

  function render() {
    const q = search.value.trim().toLowerCase();
    const filtered = q
      ? sources.filter((source) => `${source.id} ${source.name} ${source.tier} ${source.kind}`.toLowerCase().includes(q))
      : sources;
    if (!filtered.length) {
      table.innerHTML = `<tr><td colspan="5">没有匹配信源</td></tr>`;
      return;
    }
    table.innerHTML = filtered.map((source) => `<tr>
      <td><code>${esc(source.id)}</code></td>
      <td><a href="${esc(source.homepage_url || source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.name)}</a></td>
      <td>${esc(source.tier)}</td>
      <td>${source.enabled ? "启用" : '<span title="自 2026-05-12 起停止抓取">停用</span>'}</td>
      <td>${esc(source.kind || "feed")}</td>
    </tr>`).join("");
  }

  const data = await api("/api/v1/sources");
  sources = data.sources;
  debounceInput(search, render);
  render();
}

export async function initItem() {
  const id = new URLSearchParams(location.search).get("id");
  const root = document.querySelector("#detail");
  if (!id) {
    root.innerHTML = missingItem("缺少内容 ID");
    return;
  }
  if (!/^[a-f0-9]{16}$/i.test(id)) {
    root.innerHTML = missingItem("未找到这条内容");
    return;
  }
  let data;
  try {
    data = await api(`/api/v1/items/${encodeURIComponent(id)}`);
  } catch (error) {
    root.innerHTML = missingItem("未找到这条内容");
    return;
  }
  root.innerHTML = `<section class="card detail-card">
    ${sourceLine(data.item)}
    <h1>正在打开原文</h1>
    <p class="meta">${esc(data.item.title)}</p>
    <a class="origin" href="${esc(itemHref(data.item))}">打开原文</a>
  </section>`;
  location.replace(itemHref(data.item));
}

function missingItem(title) {
  return `<section class="card detail-card empty-state">
    <h1>${esc(title)}</h1>
    <p>这条内容可能已被删除，或链接里的 ID 不正确。</p>
    <div class="empty-actions">
      <a class="origin" href="/">返回精选</a>
      <a class="origin" href="/all">查看全部 AI 动态</a>
    </div>
  </section>`;
}
