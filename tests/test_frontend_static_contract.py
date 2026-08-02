from __future__ import annotations

import re
from pathlib import Path

STATIC = Path("web/static")
TEMPLATES = Path("web/templates")
AIHOT_FONT_HREF = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;700;800"
    "&amp;family=IBM+Plex+Sans:wght@400;500;600;700"
    "&amp;family=Noto+Serif+SC:wght@400;600;800"
    "&amp;family=Playfair+Display:wght@800&amp;display=swap"
)
BACKEND_CATEGORY_TAGS = {
    "模型发布",
    "产品更新",
    "MCP/工具",
    "行业动态",
    "安全/对齐",
    "现象/趋势",
    "论文/研究",
    "教程/实践",
    "部署/工程",
}


def _read(name: str) -> str:
    static_path = STATIC / name
    if static_path.exists():
        return static_path.read_text(encoding="utf-8")
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _app_import_names(html: str) -> set[str]:
    matches = re.finditer(
        r'import\s*\{(?P<names>[^}]+)\}\s*from\s*["\']/app\.js(?:\?[^"\']*)?["\']',
        html,
    )
    imported_names = {
        name.strip()
        for match in matches
        for name in match.group("names").split(",")
    }
    assert imported_names, "app.js module import not found"
    return imported_names


def test_app_import_names_collects_multiple_module_imports() -> None:
    html = """
    <script type="module">
      import { initCurated } from "/app.js?v=one";
      import { initTimeline, paginationState } from '/app.js?v=two';
    </script>
    """

    assert _app_import_names(html) == {"initCurated", "initTimeline", "paginationState"}


def test_static_pages_have_aihot_mobile_bar_and_no_search_button() -> None:
    for name in ["index.html", "all.html", "daily.html", "item.html", "about.html"]:
        html = _read(name)
        assert 'class="app-mobile-bar"' in html
        assert 'class="app-hamburger"' in html
        assert 'class="app-mobile-brand"' in html
        assert 'class="sidebar-close"' in html
        assert 'aria-expanded="false"' in html
        assert 'id="refresh"' not in html


def test_daily_page_declares_date_controls_and_fallback_banner() -> None:
    html = _read("daily.html")

    assert '<body class="daily-page">' in html
    assert "daily-overrides-20260514c.css" not in html
    assert 'class="daily-shell"' in html
    assert 'class="daily-layout"' in html
    assert 'class="daily-side daily-archive-panel"' in html
    assert 'class="daily-masthead-eyebrow daily-kicker"' in html
    assert 'class="daily-masthead-title"' in html
    assert 'class="daily-masthead-meta daily-date-line"' in html
    assert 'id="daily-sections"' in html
    assert 'id="daily-fallback"' in html
    assert "LOADING DAILY" in html
    assert '<span class="daily-story-count">LOADING</span>' in html


def test_about_page_declares_contact_disabled_source_notice_and_scoring_legend() -> None:
    html = _read("about.html")

    assert "来源（信源池）" in html
    assert "停用源仅停止继续抓取" in html
    assert "site.repo_url" in html
    assert "site.maintainer" in html
    assert "site.x_url" in html
    assert ("lin" + "dong" + "28") not in html
    assert "评分说明" in html
    for token in ["relevance", "density", "recency", "authority", "engineering", "权重", "6.5"]:
        assert token in html


def test_app_js_declares_frontend_ux_behaviors() -> None:
    js = _read("app.js")

    assert "function dateBucket" in js
    assert "Asia/Shanghai" in js
    assert "<time datetime=" in js
    assert "LLM 5 维评分加权后得分" in js
    assert "initNavigation" in js
    assert "#refresh" not in js


def test_app_js_has_no_client_side_category_semantics_copy() -> None:
    js = _read("app.js")

    assert "CATEGORY_TAGS" not in js
    assert "itemMatchesCategory" not in js


def test_app_js_has_no_backend_category_tag_literals_outside_daily_grouping() -> None:
    js = _read("app.js")
    marker = "const DAILY_SECTION_DEFS = ["
    assert js.count(marker) == 1
    daily_start = js.index(marker)
    daily_end = js.index("\n];", daily_start) + len("\n];")
    daily_grouping = js[daily_start:daily_end]
    non_daily_js = js[:daily_start] + js[daily_end:]

    for tag in BACKEND_CATEGORY_TAGS:
        assert tag in daily_grouping
        assert tag not in non_daily_js


def test_rollback_pages_only_import_names_exported_by_app_js() -> None:
    js = _read("app.js")
    exported_names = set(
        re.findall(r"^export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", js, re.MULTILINE)
    )

    imported_names = {
        page: _app_import_names(_read(page))
        for page in ("index.html", "all.html")
    }

    assert imported_names == {
        "index.html": {"initCurated"},
        "all.html": {"initTimeline"},
    }
    assert set().union(*imported_names.values()) <= exported_names


def test_feed_controls_are_url_backed_like_aihot() -> None:
    for name, action in [("index.html", "/"), ("all.html", "/all")]:
        html = _read(name)
        assert '<form class="feed-filter"' in html
        assert f'action="{action}"' in html
        assert 'method="get"' in html
        assert 'name="q"' in html
        assert 'type="submit"' in html
        assert "搜索标题/摘要" in html
        assert "data-category-filter" in html
        assert '<button class="seg-item' not in html
        for category in ["ai-models", "ai-products", "industry", "paper", "tip"]:
            assert f"category={category}" in html

    daily_html = _read("daily.html")
    assert '<form class="feed-filter"' not in daily_html
    assert "data-category-filter" not in daily_html



def test_all_page_declares_aihot_style_channel_filter_and_infinite_scroll() -> None:
    html = _read("all.html")

    assert "data-channel-filter" in html
    assert 'id="channel-param"' in html
    for channel in ["firstParty", "news", "x"]:
        assert f"channel={channel}" in html
    assert 'id="pagination"' not in html
    assert 'id="more"' not in html



def test_curated_page_declares_hot_topics_and_infinite_scroll() -> None:
    html = _read("index.html")

    assert 'id="hot-topics"' in html
    assert 'id="pagination"' not in html


def test_app_js_supports_url_state_score_tiers_and_card_dividers() -> None:
    js = _read("app.js")

    assert "categoryFromUrl" in js
    assert "updateFeedUrl" in js
    assert "scoreTierClass" in js
    assert "timeline-divider" in js
    assert "sourceAvatarUrl" in js
    assert "<img" in js


def test_app_js_uses_wechat_author_name_and_avatar_fallback() -> None:
    js = _read("app.js")

    assert 'const WECHAT_FALLBACK_ICON = "/wechat-icon.svg?v=20260601";' in js
    assert 'if (item.source_kind === "wechat") return item.author || item.source_name || item.source_id;' in js
    assert 'if (item.source_kind === "wechat") return item.author_avatar_url || WECHAT_FALLBACK_ICON;' in js
    assert 'item.source_kind !== "wechat"' in js


def test_app_js_wechat_card_renders_visit_original_link() -> None:
    js = _read("app.js")

    # 客户端重渲染路径（搜索/翻页后）也要带「访问原文」链接，与 Jinja 卡片保持一致
    assert "wechat-card-origin" in js
    assert 'target="_blank" rel="noopener noreferrer"' in js


def test_app_js_uses_title_and_media_as_natural_external_targets() -> None:
    js = _read("app.js")

    assert "article-media-link" in js
    assert 'aria-label="${esc(label)}"' in js
    assert '<a class="item-title"' in js
    assert "x-media-affordance" not in js
    assert "mediaAffordance" not in js
    assert "打开原推播放视频" not in js
    assert "查看原推媒体" not in js


def test_app_js_renders_article_media_assets() -> None:
    js = _read("app.js")

    assert "articleMedia" in js
    assert "media_assets" in js
    assert "article-media" in js
    assert "article-media-img" in js


def test_app_js_supports_all_page_channel_pagination_and_full_card_affordances() -> None:
    js = _read("app.js")

    assert "channelFromUrl" in js
    assert "bindChannelControls" in js
    assert "renderPagination" in js
    assert "renderTimelineLoading" in js
    assert "category: CATEGORY_URL_VALUES[activeCategory] || \"\"" in js
    assert "showReason: \"selected\"" in js
    assert "sortByScore: false" in js
    assert "clampSummary: true" in js
    assert "compact: true" not in js



def test_app_js_supports_curated_infinite_scroll() -> None:
    js = _read("app.js")

    assert "attachInfiniteFeed" in js
    assert "IntersectionObserver" in js
    assert 'queryPath("/api/v1/curated"' in js
    assert "limit: 40" in js
    assert 'page: page > 1 ? String(page) : ""' in js


def test_app_js_supports_wechat_search_url_state_and_empty_copy() -> None:
    js = _read("app.js")
    init_wechat = js.split("export async function initWechat()", 1)[1].split("export async function initTimeline()", 1)[0]

    assert 'const search = document.querySelector("#search");' in init_wechat
    assert "search.value = searchFromUrl();" in init_wechat
    assert "const WECHAT_PAGE_LIMIT = 50;" in js
    assert 'normalizeFeedUrl("/wechat"' in init_wechat
    assert 'queryPath("/api/v1/wechat"' in init_wechat
    assert "q: search.value.trim()," in init_wechat
    assert "limit: WECHAT_PAGE_LIMIT" in init_wechat
    assert 'updateFeedUrl("/wechat"' in init_wechat
    assert "debounceInput(search, runSearch);" in init_wechat
    assert 'search.closest("form")?.addEventListener("submit"' in init_wechat
    assert 'window.addEventListener("popstate"' in init_wechat
    assert "renderWechatPagination(pagination, data, search.value.trim())" in init_wechat
    assert "没有匹配条目" in js
    assert "清空搜索后可回到默认列表。" in js


def test_app_js_uses_preload_for_initial_curated_and_timeline_render() -> None:
    js = _read("app.js")

    assert "function readPreload()" in js
    assert 'document.querySelector("#__PRELOAD__")' in js
    assert "const preload = readPreload();" in js
    assert "currentPage = Number(preload.page || currentPage);" in js
    assert "renderView(preload.items, preload);" in js
    assert "await load({ page: currentPage, updateUrl: false });" in js


def test_feed_css_declares_timeline_loading_state() -> None:
    css = _read("style.css")

    assert ".timeline-loading {" in css
    assert 'role="status"' in _read("app.js")
    assert ".timeline-loading-dot {" in css


def test_app_js_supports_aihot_style_daily_report() -> None:
    js = _read("app.js")

    assert "renderDailyReport" in js
    assert "daily-section" in js
    assert "daily-section-articles daily-article-list" in js
    assert "daily-article-source daily-article-meta" in js
    assert "daily-source-avatar" in js
    assert "sourceDisplayName(item) || \"来源\"" in js
    assert "daily-article-title" in js
    assert '<h3 class="daily-article-title">' in js
    assert "dailyDateFromPath" in js


def test_daily_css_supports_zoomed_desktop_aihot_narrow_flow() -> None:
    css = _read("style.css")

    assert "@media (min-width: 761px) and (max-width: 1100px)" in css
    assert ".daily-page .daily-archive-panel" in css
    assert ".daily-page .daily-report" in css
    assert "font-size: 19px;" in css
    assert "font-weight: 600;" in css


def test_app_js_renders_x_cards_with_clickable_title_instead_of_origin_action() -> None:
    js = _read("app.js")

    assert "function itemTitleText" in js
    assert '<a class="item-title"' in js
    assert '<a class="origin-link"' not in js



def test_article_media_is_constrained_for_scan_reading() -> None:
    css = _read("style.css")

    assert ".article-media {" in css
    assert "width: min(100%, 416px);" in css



def test_mobile_category_filter_is_not_horizontal_scroll_only() -> None:
    css = _read("style.css")

    assert "overflow-x: visible;" in css
    assert "flex: 1 1 0;" in css



def test_global_visual_tokens_light_default_with_dark_variant() -> None:
    css = _read("style.css")

    # 主站页面不再引用 Google Fonts（跨境延迟）；daily 报纸风保留
    for name in ["index.html", "all.html", "item.html", "about.html"]:
        assert "fonts.googleapis.com" not in _read(name)
    assert AIHOT_FONT_HREF in _read("daily.html")

    # 浅色默认 + 暗色变体 token
    assert "color-scheme: light;" in css
    assert '[data-theme="dark"]' in css
    assert "--bg: #f6f7f8;" in css
    assert "--accent: #0f766e;" in css
    assert '--font-sans: -apple-system' in css

    # 每页 head 内联主题脚本，避免 FOUC；浅色为默认
    for name in ["index.html", "all.html", "item.html", "about.html"]:
        html = _read(name)
        assert 'localStorage.getItem("ai-radar:theme")||"light"' in html
        assert "dataset.theme" in html


def test_shell_declares_theme_toggle_and_bookmarks_nav() -> None:
    for name in ["index.html", "all.html", "about.html", "item.html"]:
        html = _read(name)
        assert 'class="theme-toggle"' in html
        assert 'data-theme-pref="light"' in html
        assert 'data-theme-pref="system"' in html
        assert 'data-theme-pref="dark"' in html
        assert 'href="/bookmarks"' in html


def test_app_js_inits_bind_theme_toggle_on_every_page() -> None:
    js = _read("app.js")

    for fn in ["initWechat", "initDaily", "initAbout", "initItem", "initNavigationOnly", "initCurated", "initTimeline", "initBookmarks"]:
        start = js.index(f"function {fn}(")
        body = js[start : start + 400]
        assert "initThemeToggle()" in body, fn


def test_daily_page_scope_restores_legacy_tokens() -> None:
    css = _read("style.css")

    assert ".daily-page {" in css
    assert '--font-display: "Playfair Display"' in css
    assert "--cyan: #22d3ee;" in css
    assert "--green: #34d399;" in css


def test_app_js_declares_bookmark_store_and_hot_topics() -> None:
    js = _read("app.js")

    assert "BookmarkStore" in js
    assert 'localStorage.setItem(BOOKMARKS_KEY' in js
    assert "bookmarkButton" in js
    assert '"/api/v1/hot' in js
    assert "initBookmarks" in js
    assert "initThemeToggle" in js



def test_feed_text_metrics_match_redesign_baseline() -> None:
    css = _read("style.css")

    assert ".item-title {" in css
    assert "font-weight: 700;" in css
    assert ".clamped-card.x-card .summary {" in css
    assert "-webkit-line-clamp: 4;" in css
    assert ".clamped-card .summary {" in css
    assert "-webkit-line-clamp: 3;" in css
    assert ".timeline-card:hover {" in css
    assert ".date-collapse" in css
    assert ".hot-topics" in css
    assert ".bookmark-btn" in css


def test_daily_text_metrics_match_aihot_baseline() -> None:
    css = _read("style.css")

    assert ".daily-masthead-title {" in css
    assert "display: flex;" in css
    assert "gap: 14px;" in css
    assert "margin: 0 0 36px;" in css
    assert "letter-spacing: -1px;" in css
    assert "font-size: clamp(56px, 8vw, 104px);" in css
    assert ".daily-article-title {" in css
    assert "font-family: var(--font-daily-body);" in css
    assert "margin: 0 0 14px;" in css
    assert "line-height: 1.4;" in css
    assert "letter-spacing: -0.1px;" in css
    assert ".daily-article-summary {" in css
    assert "margin: 0;" in css
    assert "line-height: 1.7;" in css
    assert "letter-spacing: 0.1px;" in css


def test_daily_structural_styles_match_aihot_baseline() -> None:
    css = _read("style.css")

    assert ".app-mobile-bar {" in css
    assert ".sidebar-open .sidebar {" in css
    assert ".daily-shell {" in css
    assert "margin: -24px -28px -72px;" in css
    assert "background: #0b0f1a;" in css
    assert ".daily-layout {" in css
    assert "display: flex;" in css
    assert ".daily-side {" in css
    assert "flex: 0 0 220px;" in css
    assert ".daily-masthead {" in css
    assert "margin-bottom: 96px;" in css
    assert ".daily-masthead-eyebrow {" in css
    assert "margin-bottom: 32px;" in css
    assert ".daily-section-header {" in css
    assert "gap: 24px;" in css
    assert "margin-bottom: 24px;" in css
    assert ".daily-section-no" in css
    assert "font-size: 56px;" in css
    assert "color: #34d399;" in css
    assert ".daily-section-articles" in css
    assert "padding: 24px 28px;" in css
    assert "background: transparent;" in css
    assert ".daily-article-source" in css
    assert "display: flex;" in css
    assert "letter-spacing: 1px;" in css



def test_sidebar_drawer_and_grouped_nav() -> None:
    css = _read("style.css")

    assert ".side-group-label {" in css
    assert ".sidebar-foot {" in css
    assert ".theme-toggle {" in css
    assert "transform: translateX(-100%);" in css
    assert ".sidebar-open .sidebar {" in css


def test_selected_badge_is_only_rendered_in_score_pill_not_topic_tags() -> None:
    js = _read("app.js")

    assert 'class="hot-pill">精选</span>' in js
    assert "tag tag-hot" not in js
    assert "parts.unshift" not in js
