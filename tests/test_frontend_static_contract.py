from __future__ import annotations

from pathlib import Path

STATIC = Path("web/static")
AIHOT_FONT_HREF = (
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;700;800"
    "&amp;family=IBM+Plex+Sans:wght@400;500;600;700"
    "&amp;family=Noto+Serif+SC:wght@400;600;800"
    "&amp;family=Playfair+Display:wght@800&amp;display=swap"
)


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


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
    assert "https://github.com/lindong28/ai-radar" in html
    assert "https://github.com/AIPlanetLive/ai-radar" not in html
    assert "https://x.com/lindong28" in html
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


def test_all_page_declares_aihot_style_channel_filter_and_pagination() -> None:
    html = _read("all.html")

    assert "data-channel-filter" in html
    assert 'id="channel-param"' in html
    for channel in ["firstParty", "news", "x"]:
        assert f"channel={channel}" in html
    assert 'id="pagination"' in html
    assert 'id="more"' not in html


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


def test_app_js_uses_preload_for_initial_curated_and_timeline_render() -> None:
    js = _read("app.js")

    assert "function readPreload()" in js
    assert 'document.querySelector("#__PRELOAD__")' in js
    assert "const preload = readPreload();" in js
    assert "currentItems = preload.items;" in js
    assert "renderView(search.value.trim());" in js
    assert "renderView(preload.items, preload);" in js
    assert "await load(search.value.trim());" in js
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


def test_global_visual_tokens_match_aihot_baseline_across_static_pages() -> None:
    css = _read("style.css")

    for name in ["index.html", "all.html", "daily.html", "item.html", "about.html"]:
        assert AIHOT_FONT_HREF in _read(name)

    assert '--font-sans: "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, sans-serif;' in css
    assert '--font-serif: "Noto Serif SC", ui-serif, Georgia, serif;' in css
    assert '--font-display: "Playfair Display", "Noto Serif SC", ui-serif, Georgia, serif;' in css
    assert (
        '--font-daily-body: "IBM Plex Sans", -apple-system, "system-ui", "PingFang SC", '
        '"HarmonyOS Sans SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;'
    ) in css
    assert "font: 14px/1.5 var(--font-sans);" in css
    assert ".sidebar {" in css
    assert "position: sticky;" in css
    assert "grid-template-columns: 155px;" in css
    assert "gap: 8px;" in css
    assert "width: 180px;" in css
    assert "padding: 18px 12px 14px;" in css
    assert ".brand-logo-sidebar {" in css
    assert "letter-spacing: 0.13em;" in css
    assert ".brand-logo-orbit {" in css


def test_feed_text_metrics_match_aihot_baseline() -> None:
    css = _read("style.css")

    assert ".item-title {" in css
    assert "margin-top: 0;" in css
    assert "font-size: 15px;" in css
    assert "font-weight: 700;" in css
    assert "line-height: 1.5;" in css
    assert ".x-card .item-title {" in css
    assert "font-size: 14px;" in css
    assert "font-weight: 400;" in css
    assert "line-height: 23.8px;" in css
    assert ".clamped-card.x-card .item-title {" in css
    assert "-webkit-line-clamp: 4;" in css
    assert ".summary {" in css
    assert "display: flow-root;" in css
    assert "margin: 6px 0 0;" in css
    assert ".compact-card .summary {" in css
    assert "margin-top: 6px;" in css
    assert "font-size: 12.5px;" in css
    assert "line-height: 20px;" in css
    assert ".clamped-card .summary {" in css
    assert "-webkit-line-clamp: 3;" in css


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
    assert "grid-template-columns: 44px minmax(0, 1fr) 44px;" in css
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


def test_sidebar_responsive_drawer_keeps_aihot_natural_nav_rows() -> None:
    css = _read("style.css")

    assert "align-content: start;" in css
    assert "grid-auto-rows: min-content;" in css
    assert "justify-items: stretch;" in css
    assert "white-space: nowrap;" in css
    assert "background: transparent;" in css
    assert ".daily-page .sidebar {" not in css
    assert ".daily-page .side-nav {" not in css
    assert ".daily-page .side-link {" not in css
    assert ".daily-page .side-nav.open" not in css


def test_selected_badge_is_only_rendered_in_score_pill_not_topic_tags() -> None:
    js = _read("app.js")

    assert 'class="hot-pill">精选</span>' in js
    assert "tag tag-hot" not in js
    assert "parts.unshift" not in js
