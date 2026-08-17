from __future__ import annotations

import re
from pathlib import Path

STATIC = Path("web/static")
TEMPLATES = Path("web/templates")
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
THEMED_PUBLIC_PAGES = [
    STATIC / "index.html",
    STATIC / "all.html",
    STATIC / "daily.html",
    STATIC / "item.html",
    TEMPLATES / "index.html",
    TEMPLATES / "all.html",
    TEMPLATES / "wechat.html",
    TEMPLATES / "wechat_detail.html",
    TEMPLATES / "wechat_404.html",
    TEMPLATES / "bookmarks.html",
    TEMPLATES / "about.html",
    TEMPLATES / "hot.html",
    TEMPLATES / "more.html",
    TEMPLATES / "changelog.html",
]


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


def test_static_pages_have_compact_mobile_chrome_without_sidebar_drawer() -> None:
    for path in [STATIC / "index.html", STATIC / "all.html", STATIC / "item.html"]:
        html = path.read_text(encoding="utf-8")
        assert 'class="app-mobile-bar"' in html
        assert 'class="app-mobile-brand"' in html
        assert 'class="app-mobile-date"' in html
        assert 'class="m-tabbar" aria-label="移动端主导航"' in html
        assert len(re.findall(r'<a class="m-tab(?: m-tab-active)?"', html)) == 4
        assert 'class="app-hamburger"' not in html
        assert 'aria-label="打开导航"' not in html
        assert 'class="sidebar-close"' in html
        assert 'id="refresh"' not in html

    daily_html = (STATIC / "daily.html").read_text(encoding="utf-8")
    assert 'class="app-mobile-bar"' not in daily_html
    assert 'class="m-tabbar" aria-label="移动端主导航"' in daily_html

    index_template = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    assert '{% include "_mobile_topbar.html" %}' in index_template

    non_feed_pageheads = {
        TEMPLATES / "more.html": "更多",
        TEMPLATES / "bookmarks.html": "收藏",
        TEMPLATES / "wechat.html": "微信文章解读",
        TEMPLATES / "wechat_404.html": "微信文章解读",
        TEMPLATES / "wechat_detail.html": "微信文章解读",
    }
    for path, title in non_feed_pageheads.items():
        html = path.read_text(encoding="utf-8")
        assert '{% include "_mobile_topbar.html" %}' not in html, path
        assert 'class="m-pagehead' in html, path
        assert f'>{title}<' in html, path
        assert '{% include "_mobile_tabbar.html" %}' in html, path
        assert 'class="app-hamburger"' not in html, path

    about_html = (TEMPLATES / "about.html").read_text(encoding="utf-8")
    assert '{% include "_mobile_topbar.html" %}' not in about_html
    # The page must lead with its own page heading rather than the feed's
    # brand+date bar. Assert the structure, not a particular wording: the
    # copy is ours to choose and must not be pinned to the reference site's.
    assert 'class="about-hero"' in about_html
    assert ">关于<" in about_html
    assert '{% include "_mobile_tabbar.html" %}' in about_html

    for path in [TEMPLATES / "all.html", TEMPLATES / "hot.html", TEMPLATES / "changelog.html"]:
        html = path.read_text(encoding="utf-8")
        assert '{% include "_mobile_topbar.html" %}' not in html, path
        assert '{% include "_mobile_tabbar.html" %}' in html, path

    tabbar = (TEMPLATES / "_mobile_tabbar.html").read_text(encoding="utf-8")
    assert len(re.findall(r'<a class="m-tab', tabbar)) == 4
    assert re.findall(r'href="([^"]+)"', tabbar) == ["/", "/all", "/daily", "/more"]

    css = (STATIC / "style.css").read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 960px) {", 1)[1]
    pagehead = mobile.split(".m-pagehead {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in pagehead
    assert "align-items: baseline;" in pagehead
    assert "justify-content: space-between;" in pagehead
    assert "padding: 12px 0 10px;" in pagehead
    title = mobile.split(".m-pagehead-title {", 1)[1].split("}", 1)[0]
    assert "font-size: 22px;" in title
    assert "font-weight: 900;" in title
    assert "line-height: 33px;" in title
    assert ".hot-topics:empty:not([hidden])" in css
    assert "min-height: 132.5px;" in css
    assert "min-height: 173px;" in mobile


def test_navigation_icons_are_inline_svg_in_source_markup() -> None:
    icon_pattern = re.compile(
        r'<span class="[^"]*(?:side-icon|m-tab-icon)[^"]*"[^>]*>(?P<body>.*?)</span>',
        re.DOTALL,
    )
    paths = [*THEMED_PUBLIC_PAGES, TEMPLATES / "_mobile_tabbar.html"]
    for path in paths:
        html = path.read_text(encoding="utf-8")
        icons = [match.group("body") for match in icon_pattern.finditer(html)]
        assert icons, path
        assert all("<svg " in icon and "<path " in icon for icon in icons), path


def test_wechat_ssr_mobile_date_uses_the_shared_two_part_contract() -> None:
    html = (TEMPLATES / "wechat.html").read_text(encoding="utf-8")

    assert '<span class="m-daybar-main">{{ item.mobile_date_main }}</span>' in html
    assert '<span class="m-daybar-sub">{{ item.mobile_date_sub }}</span>' in html


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
    assert "这里列出当前启用的来源" in html
    assert "运行时读取状态" in html
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



def test_all_page_declares_single_category_row_source_select_and_infinite_scroll() -> None:
    html = _read("all.html")

    assert "data-channel-filter" in html
    assert '<select id="channel-param" name="channel"' in html
    assert 'class="source-filter"' in html
    assert "来源" in html
    for channel in ["firstParty", "news", "x"]:
        assert f'value="{channel}"' in html
    assert '<div class="seg-list" data-channel-filter' not in html
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

    assert '<a class="item-title"' in js
    assert "x-media-affordance" not in js
    assert "mediaAffordance" not in js
    assert "打开原推播放视频" not in js
    assert "查看原推媒体" not in js


def test_list_cards_do_not_render_article_media() -> None:
    """ADR-054: 列表卡片不渲染正文图片，CSR 与 SSR 两条路径都不得留下渲染器。"""
    js = _read("app.js")
    prepaint = (TEMPLATES / "_prepaint_list.html").read_text(encoding="utf-8")

    assert "articleMedia" not in js
    assert "article-media-link" not in js
    assert "article-media-img" not in js
    assert "article-media" not in prepaint
    assert "media_assets" not in prepaint


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


def test_daily_css_supports_measured_narrow_flow() -> None:
    css = _read("style.css")

    assert "@media (max-width: 960px)" in css
    assert ".daily-side {\n    display: none;\n  }" in css
    assert ".daily-main {\n    padding: 0;\n  }" in css
    assert "@media (max-width: 640px)" in css
    assert ".daily-article-summary {\n    font-size: 15px;\n    line-height: 1.75;\n  }" in css


def test_app_js_renders_x_cards_with_clickable_title_instead_of_origin_action() -> None:
    js = _read("app.js")

    assert "function itemTitleText" in js
    assert '<a class="item-title"' in js
    assert '<a class="origin-link"' not in js



def test_article_media_styles_are_gone() -> None:
    """ADR-054: 渲染器删除后不留无消费者的样式规则。"""
    css = _read("style.css")

    assert ".article-media {" not in css
    assert ".article-media-img" not in css



def test_mobile_home_uses_scrollable_chips_but_all_keeps_full_search() -> None:
    css = _read("style.css")
    home = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    all_page = (TEMPLATES / "all.html").read_text(encoding="utf-8")

    assert ".home-page .seg-list" in css
    assert "overflow-x: auto;" in css
    assert "min-height: var(--touch-target-sm);" in css
    assert 'class="mobile-search-link" aria-label="搜索全部动态" href="/all#search"' in home
    assert ".home-page .feed-filter" in css
    assert "display: none;" in css
    assert 'class="app-main all-page"' in all_page
    assert '<form class="feed-filter" action="/all" method="get">' in all_page
    assert 'data-channel-filter' in all_page



def test_global_visual_tokens_light_default_with_dark_variant() -> None:
    css = _read("style.css")

    # 所有页面都使用全站字体 token，不再加载独立的日报字体。
    for path in THEMED_PUBLIC_PAGES:
        assert "fonts.googleapis.com" not in path.read_text(encoding="utf-8")

    # 浅色默认 + 暗色变体 token
    assert "color-scheme: light;" in css
    assert '[data-theme="dark"]' in css
    assert "--bg: #f4f5f6;" in css
    assert "--accent: #135e6b;" in css
    assert '--font-sans: system-ui, -apple-system' in css
    for token in [
        "--text-size-xs: 0.75rem;",
        "--text-size-sm: 0.8125rem;",
        "--text-size-base: 0.875rem;",
        "--text-size-md: 1rem;",
        "--text-size-lg: 1.125rem;",
        "--text-size-xl: 1.25rem;",
        "--text-size-2xl: 1.5rem;",
        "--line-height-tight: 1.25;",
        "--line-height-normal: 1.5;",
        "--line-height-relaxed: 1.75;",
        "--border: #e2e4e7;",
        "--border-strong: #d8dbdf;",
        "--border-soft: #eceef0;",
        "--border-emphasis: #8a94a2;",
        "--border-card-subtle-solid: #c9cdd2;",
        "--space-1: 4px;",
        "--space-6: 32px;",
        "--radius: 12px;",
        "--radius-sm: 8px;",
        "--radius-lg: 16px;",
        "--sidebar-width: 180px;",
        "--touch-target: 44px;",
        "--touch-target-sm: 36px;",
        "--mobile-tabbar-height: 54px;",
        "--mobile-gutter: 18px;",
        "--search-control-height: 38px;",
        "--theme-transition: background-color 220ms ease, background 220ms ease, color 180ms ease, border-color 180ms ease, box-shadow 220ms ease;",
    ]:
        assert token in css
    dark_block = css.split('[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    assert "--bg: #10151c;" in dark_block
    assert "--shadow-card: none;" in dark_block
    for token in [
        "--border: rgba(255, 255, 255, 0.08);",
        "--border-strong: rgba(255, 255, 255, 0.12);",
        "--border-soft: rgba(255, 255, 255, 0.06);",
        "--border-emphasis: rgba(255, 255, 255, 0.22);",
        "--border-card-subtle-solid: rgba(255, 255, 255, 0.14);",
    ]:
        assert token in dark_block
    assert re.search(r"--line(?:-strong)?:", css) is None

    # 每个现有 public HTML consumer 的 head 都内联完整主题 bootstrap，避免 FOUC。
    for path in THEMED_PUBLIC_PAGES:
        html = path.read_text(encoding="utf-8")
        # theme-color 静态初值只是内联脚本跑之前的兜底色，不表达默认主题。
        assert '<meta name="theme-color" content="#10151c">' in html
        # 默认档为 system；且不得再出现旧的 ||"dark" 默认。
        assert 'localStorage.getItem("ai-radar:theme")' in html
        assert '||"dark"' not in html
        assert '?s:"system"' in html
        # 容错契约：存储读取单独 try、matchMedia 缺失时常量兜底深色。
        assert "try{s=localStorage.getItem" in html
        assert 'typeof matchMedia!=="function"' in html
        assert "dataset.theme" in html
        assert "dataset.themeMode" in html
        assert 'querySelector(\'meta[name="theme-color"]\')' in html
        assert 'setAttribute("content",d?"#10151c":"#f4f5f6")' in html


def test_score_pill_carries_semantic_label_on_both_render_paths() -> None:
    """评分不是裸数字：CSR 与 SSR 必须同形，否则首绘与 hydration 之间会跳字。"""
    js = _read("app.js")
    prepaint = (TEMPLATES / "_prepaint_list.html").read_text(encoding="utf-8")

    label = '<span class="timeline-score-label">AI<span class="timeline-score-label-rest"> 评分</span></span>'
    assert label in js
    assert label in prepaint
    # 不得写死分母：T1 信源有 1.25 倍 tier 乘数，weighted_score 可超过 10，
    # 生产实况已有 10.75 的条目，`/100` 会渲染出 108/100 这种假值。
    # 只查渲染出的那段标记，不查整份文件——否则解释这件事的注释自己会命中断言。
    js_markup = js.split('<span class="timeline-score ${tier}"', 1)[1].split("`;", 1)[0]
    prepaint_markup = prepaint.split('<span class="timeline-score {{ item.score_tier }}"', 1)[1].split(
        "</span>{% endif %}", 1
    )[0]
    for markup in (js_markup, prepaint_markup):
        assert "/100" not in markup
        assert "timeline-score-max" not in markup


def test_score_label_shortens_on_narrow_screens_without_hiding_itself() -> None:
    """窄屏只缩短标签、不整体隐藏——移动端正是 tooltip 够不到的地方。"""
    css = _read("style.css")

    # 断言那条规则本身，不是"整个 media block 里某处有 display:none"——
    # 后者在规则被改成 opacity 时仍会命中别处的 display:none 而静默通过（实测）。
    assert ".timeline-score-label-rest" in css
    rest_rule = css.split(".timeline-score-label-rest {", 1)[1].split("}", 1)[0]
    # 必须是视觉隐藏，不能是 display:none——后者会把「评分」二字一并移出可访问性树，
    # 读屏器只剩「AI 108」，而这段文字正是用来解释那个数字的。
    assert "display: none" not in rest_rule, "display:none 会连可访问性树一起隐藏"
    assert "position: absolute;" in rest_rule
    assert "clip-path: inset(50%);" in rest_rule
    # 该规则必须落在 ≤960px 媒体查询内，否则桌面端也会丢掉「评分」二字。
    before_rule = css.split(".timeline-score-label-rest {", 1)[0]
    nearest_media = before_rule.rsplit("@media", 1)[1].split("{", 1)[0].strip()
    assert nearest_media == "(max-width: 960px)", nearest_media
    # 标签不得再压透明度：12px 小字叠 opacity 会掉到 4.5:1 对比度线以下。
    label_rule = css.split(".timeline-score-label {", 1)[1].split("}", 1)[0]
    assert "opacity" not in label_rule


def test_app_js_theme_default_matches_inline_bootstrap() -> None:
    """默认档与容错契约必须在 app.js 与内联脚本之间一致（见 6724dd6 的 FOUC 教训）。"""
    js = _read("app.js")

    assert 'return value === "dark" || value === "system" || value === "light" ? value : "system"' in js
    assert "function storedThemeValue" in js
    assert "function systemPrefersDark" in js
    assert 'typeof window.matchMedia !== "function"' in js
    assert '? value : "dark"' not in js


def test_shell_declares_theme_toggle_and_bookmarks_nav() -> None:
    for name in ["index.html", "all.html", "about.html", "item.html"]:
        html = _read(name)
        assert 'class="theme-toggle"' in html
        assert 'data-theme-pref="light"' in html
        assert 'data-theme-pref="system"' in html
        assert 'data-theme-pref="dark"' in html
        assert 'href="/bookmarks"' in html


def test_theme_toggle_uses_moon_monitor_sun_order_on_every_public_page() -> None:
    for path in THEMED_PUBLIC_PAGES:
        html = path.read_text(encoding="utf-8")
        prefs = re.findall(r'data-theme-pref="(dark|system|light)"', html)
        assert prefs == ["dark", "system", "light"], path
        assert html.count('class="theme-toggle-thumb"') == 1, path


def test_phase1_chrome_css_matches_measured_contract() -> None:
    css = _read("style.css")

    assert "width: var(--sidebar-width);" in css
    assert "flex: 0 0 var(--sidebar-width);" in css
    assert "background: var(--sidebar-bg);" in css
    assert "border-right: 1px solid var(--sidebar-border);" in css
    assert ".page-header {" in css
    assert "background: transparent;" in css
    assert ".seg-list {" in css
    assert "gap: 22px;" in css
    assert "border-bottom: 1px solid var(--border-soft);" in css
    assert "padding: 7px 1px 9px;" in css
    assert "box-shadow: inset 0 -2px 0 var(--accent);" in css


def test_curated_header_date_uses_latest_visible_item_and_hides_for_empty_list() -> None:
    js = _read("app.js")

    assert "function curatedHeaderDate" in js
    assert 'timeZone: "Asia/Shanghai"' in js
    assert "rawItems.map(itemTime)" in js
    assert 'const runMetaCopy = "AI 自动挑选的高价值内容";' in js
    assert "runMeta.textContent = latest ? `${curatedHeaderDate(latest)} · ${runMetaCopy}` : runMetaCopy;" in js


def test_app_js_inits_bind_theme_toggle_on_every_page() -> None:
    js = _read("app.js")

    for fn in ["initWechat", "initDaily", "initAbout", "initItem", "initNavigationOnly", "initCurated", "initTimeline", "initBookmarks"]:
        start = js.index(f"function {fn}(")
        body = js[start : start + 400]
        assert "initThemeToggle()" in body, fn


def test_daily_page_scope_bridges_to_global_tokens() -> None:
    css = _read("style.css")

    daily = css.split(".daily-shell {", 1)[1].split("}", 1)[0]
    for declaration in [
        "--d-bg: var(--bg);",
        "--d-text: var(--ink);",
        "--d-text-soft: var(--soft);",
        "--d-text-faint: var(--muted);",
        "--d-accent: var(--accent-ink);",
        "--d-rule: var(--border-soft);",
        "--sans: var(--font-sans);",
    ]:
        assert declaration in daily
    light = css.split('[data-theme="light"] .daily-shell {', 1)[1].split("}", 1)[0]
    assert "--d-bg: var(--panel);" in light
    assert "Playfair Display" not in css


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


def test_daily_text_metrics_match_measured_baseline() -> None:
    css = _read("style.css")

    title = css.split(".daily-masthead-title {", 1)[1].split("}", 1)[0]
    for declaration in [
        "display: flex;",
        "gap: 0;",
        "margin: 0 0 10px;",
        "font-family: var(--sans);",
        "font-size: 34px;",
        "font-weight: 800;",
        "letter-spacing: 0.02em;",
    ]:
        assert declaration in title
    article_title = css.split(".daily-article-title {", 1)[1].split("}", 1)[0]
    for declaration in [
        "font-family: var(--sans);",
        "font-size: 15px;",
        "margin: 0 0 5px;",
        "line-height: 1.5;",
        "letter-spacing: 0;",
    ]:
        assert declaration in article_title
    summary = css.split(".daily-article-summary {", 1)[1].split("}", 1)[0]
    for declaration in [
        "font-family: var(--sans);",
        "font-size: 13.5px;",
        "margin: 0;",
        "line-height: 1.7;",
        "letter-spacing: 0;",
    ]:
        assert declaration in summary


def test_daily_structural_styles_match_measured_baseline() -> None:
    css = _read("style.css")

    assert ".app-mobile-bar {" in css
    assert ".m-tabbar {" in css
    shell = css.split(".daily-shell {", 1)[1].split("}", 1)[0]
    assert "margin: -24px -28px -72px;" in shell
    assert "background: var(--d-bg);" in shell
    side = css.split(".daily-side {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 clamp(240px, 20vw, 320px);" in side
    assert "border-right: 1px solid var(--d-rule);" in side
    masthead = css.split(".daily-masthead {", 1)[1].split("}", 1)[0]
    assert "margin-bottom: 0;" in masthead
    eyebrow = css.split(".daily-masthead-eyebrow {", 1)[1].split("}", 1)[0]
    assert "margin-bottom: 12px;" in eyebrow
    section_header = css.split(".daily-section-header {", 1)[1].split("}", 1)[0]
    assert "gap: 10px;" in section_header
    assert "padding-bottom: 10px;" in section_header
    assert "border-bottom: 1px solid var(--d-rule-strong);" in section_header
    section_number = css.split(".daily-section-no,", 1)[1].split("}", 1)[0]
    assert "font-size: 13px;" in section_number
    assert "color: var(--d-accent);" in section_number
    article_list = css.split(".daily-section-articles,", 1)[1].split("}", 1)[0]
    assert "padding: 0;" in article_list
    assert "border: 0;" in article_list
    article_source = css.split(".daily-article-source,", 1)[1].split("}", 1)[0]
    assert "display: flex;" in article_source
    assert "letter-spacing: 0;" in article_source



def test_mobile_tabbar_replaces_sidebar_drawer_and_uses_aihot_breakpoints() -> None:
    css = _read("style.css")

    assert ".side-group-label {" in css
    assert ".sidebar-foot {" in css
    assert ".theme-toggle {" in css
    assert ".m-tabbar {" in css
    assert "grid-template-columns: repeat(4, 1fr);" in css
    assert "calc(var(--mobile-tabbar-height) + env(safe-area-inset-bottom, 0px) + 28px)" in css
    assert "transform: translateX(-100%);" not in css
    assert ".sidebar-open .sidebar {" not in css
    media = sorted(set(re.findall(r"@media[^\{]+", css)))
    assert not any(old in rule for rule in media for old in ["760", "761", "1100"])
    for expected in ["max-width: 640px", "max-width: 960px", "max-width: 1200px", "prefers-reduced-motion: reduce"]:
        assert any(expected in rule for rule in media)


def test_phase2a_selected_badge_score_and_tags_match_aihot_contract() -> None:
    js = _read("app.js")
    template = _read("_prepaint_list.html")
    css = _read("style.css")

    score_pill = js.split("function scorePill(item)", 1)[1].split("function scoreTierClass", 1)[0]
    source_line = js.split("function sourceLine(item", 1)[1].split("function itemHref", 1)[0]

    assert "timeline-selected-badge" not in score_pill
    assert "timeline-selected-badge" in source_line
    assert "authorHandle(item.author)" in source_line
    assert 'class="timeline-selected-badge">精选</span>' in js
    assert 'class="timeline-selected-badge">精选</span>' in template
    assert "✦" not in js
    assert "✦" not in template
    assert 'content: "\\2726";' in css
    assert "timeline-score" in score_pill
    assert 'class="timeline-score' in template
    assert '<span class="tag">${esc(normalizedTagLabel(tag))}</span>' in js
    assert '<span class="tag">{{ tag }}</span>' in template
    assert '.tag::before {' in css
    assert "tag tag-hot" not in js
    assert "parts.unshift" not in js


def test_phase2a_timeline_structure_is_kept_in_sync_for_ssr_and_csr() -> None:
    js = _read("app.js")
    template = _read("_prepaint_list.html")

    for class_name in [
        "timeline-day-head",
        "timeline-day-toggle",
        "timeline-day-chevron",
        "timeline-day-meta",
        "date-count",
        "timeline-entry",
        "timeline-time",
        "timeline-rail",
        "timeline-dot",
        "timeline-card",
        "source-line",
        "timeline-selected-badge",
        "timeline-score",
        # article-media* 随 ADR-054 移除，不再是 CSR/SSR 的共有类名
        "tags",
        "tag",
        "timeline-divider",
        "reason",
    ]:
        assert class_name in js, class_name
        assert class_name in template, class_name

    assert "weekdayKey" in js
    assert "weekday_label" in template
    assert "item.date_count" in template
    assert '<p class="summary">{{ item.summary }}</p>' in template
    assert "function validDate" in js
    assert 'return date && !Number.isNaN(date.getTime()) ? date : null;' in js
    collapse = js.split("function bindDateGroupCollapse", 1)[1].split("function wechatCard", 1)[0]
    assert "if (!bucket) return" not in collapse
    render_timeline = js.split("function renderTimeline", 1)[1].split("function updateDateGroupCounts", 1)[0]
    assert render_timeline.index("timeline-time") < render_timeline.index("timeline-rail") < render_timeline.index("itemCard(item")
    assert template.index("timeline-time") < template.index("timeline-rail") < template.index("timeline-card")


def test_phase2a_wechat_timeline_pair_uses_the_same_desktop_day_and_rail_skeleton() -> None:
    js = _read("app.js")
    template = _read("wechat.html")
    wechat_pair = js.split("function wechatCard", 1)[1].split("function initNavigation", 1)[0]
    render_wechat = js.split("function renderWechatTimeline", 1)[1].split("function initNavigation", 1)[0]

    for class_name in [
        "timeline-day",
        "timeline-day-head",
        "timeline-day-toggle",
        "timeline-day-chevron",
        "timeline-day-meta",
        "timeline-day-items",
        "timeline-entry",
        "timeline-time",
        "timeline-rail",
        "timeline-dot",
        "timeline-card",
    ]:
        assert class_name in wechat_pair, class_name
        assert class_name in template, class_name

    assert "weekdayKey" in render_wechat
    assert "weekday_label" in template
    assert render_wechat.index("timeline-time") < render_wechat.index("timeline-rail") < render_wechat.index("wechatCard(item)")
    assert template.index("timeline-time") < template.index("timeline-rail") < template.index("timeline-card")


def test_phase2a_css_uses_measured_badge_timeline_media_and_quote_values() -> None:
    css = _read("style.css")

    for token in [
        "--tl-time-w: 64px;",
        "--tl-rail-w: 22px;",
        "--tl-dot-top: 20px;",
        "gap: 22px;",
        "padding: 15px 18px 14px;",
        "border: 1px solid var(--border);",
        "background: var(--panel);",
        "box-shadow: var(--shadow-card);",
    ]:
        assert token in css

    badge_rule = css.split(".timeline-selected-badge {", 1)[1].split("}", 1)[0]
    for declaration in [
        "display: inline-flex;",
        "gap: 3px;",
        "font-size: 10.5px;",
        "font-weight: 600;",
        "line-height: 1;",
        "padding: 3px 7px;",
        "border-radius: 3px;",
        "letter-spacing: 0.04em;",
        "color: var(--gold-ink);",
        "background: color-mix(in srgb, var(--gold) 12%, transparent);",
        "border: 0;",
        "box-shadow: none;",
        "text-shadow: none;",
        "font-variant-east-asian: proportional-width;",
    ]:
        assert declaration in badge_rule

    tag_rule = css.split(".tag {", 1)[1].split("}", 1)[0]
    assert "color: var(--muted);" in tag_rule
    assert "background: transparent;" in tag_rule
    assert "border: 0;" in tag_rule
    assert "border-radius: 0;" in tag_rule

    # max-height:360px 与 cursor:zoom-in 只属于 .article-media*，随 ADR-054 移除
    assert ".summary-body blockquote {" in css
    assert ".brand-logo-ai {\n  color: var(--accent-ink);" in css
    assert '.bookmark-btn[aria-pressed="true"] {\n  color: var(--accent-ink);' in css
