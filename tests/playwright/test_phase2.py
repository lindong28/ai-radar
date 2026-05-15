from __future__ import annotations

from playwright.sync_api import Page, expect


def _goto(page: Page, base_url: str, path: str, cards: bool = False) -> None:
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    if cards:
        expect(page.locator(".timeline-card").first).to_be_visible(timeout=10_000)


def _visible_card_count(page: Page) -> int:
    return page.locator(".timeline-card").count()


def _grouped_times(page: Page) -> list[list[str]]:
    return page.evaluate(
        """() => {
          const groups = [];
          let current = null;
          for (const child of document.querySelector("#list").children) {
            if (child.classList.contains("date-group")) {
              current = [];
              groups.push(current);
            } else if (child.classList.contains("timeline-entry") && current) {
              const value = child.querySelector(".timeline-time time")?.textContent?.trim();
              if (value) current.push(value);
            }
          }
          return groups;
        }"""
    )


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _ensure_multiple_date_groups(page: Page) -> list[list[str]]:
    groups = _grouped_times(page)
    for _ in range(5):
        if len(groups) >= 2:
            return groups
        more = page.locator("#more")
        if more.count() and more.is_visible():
            with page.expect_response(lambda response: "/api/v1/timeline" in response.url and response.status == 200):
                more.click()
        else:
            next_link = page.locator('.pagination-link[rel="next"]')
            if not next_link.count() or not next_link.is_visible():
                break
            with page.expect_response(lambda response: "/api/v1/timeline" in response.url and response.status == 200):
                next_link.click()
        groups = _grouped_times(page)
    return groups


def test_v03_v04_v06_navigation_and_search_inputs(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/")
    assert page.locator(".side-link").all_inner_texts() == ["精选", "全部 AI 动态", "AI 日报", "关于"]
    body = page.locator(".side-nav").inner_text()
    for forbidden in ["公众号", "Agent", "反馈", "登录"]:
        assert forbidden not in body

    for path in ["/", "/all", "/about"]:
        _goto(page, base_url, path)
        expect(page.locator('input[type="search"]')).to_be_visible()

    _goto(page, base_url, "/daily")
    assert page.locator('input[type="search"]').count() == 0


def test_v07_v08_v09_v11_cards_links_and_score_gating(
    page: Page, base_url: str, source_homepages: dict[str, str]
) -> None:
    for path in ["/", "/all"]:
        _goto(page, base_url, path, cards=True)
        entries = page.locator(".timeline-entry")
        cards = page.locator(".timeline-card")
        assert entries.count() >= 10

        if path == "/":
            assert page.locator(".timeline-card .score-pill").count() == cards.count()
        else:
            assert page.locator(".timeline-card .score-pill").count() >= 1
            assert page.locator(".timeline-card .hot-pill", has_text="精选").count() >= 1

        for index in range(10):
            entry = entries.nth(index)
            card = entry.locator(".timeline-card")
            expect(card.locator(".source-line")).to_be_visible()
            expect(card.locator(".source-icon")).to_be_visible()
            expect(entry.locator(".timeline-time")).to_be_visible()
            expect(card.locator(".summary")).to_be_visible()
            expect(card.locator(".tags")).to_be_visible()
            if path == "/":
                expect(card.locator(".reason")).to_be_visible()

            source_id = card.get_attribute("data-source-id")
            assert source_id
            assert card.locator(".source-line a").first.get_attribute("href") == source_homepages[source_id]

            icon_bg = card.locator(".source-icon").first.evaluate("el => getComputedStyle(el).backgroundImage")
            assert (icon_bg and icon_bg != "none") or card.locator(".source-icon img").count() == 1

            if "x-card" not in (card.get_attribute("class") or ""):
                title = card.locator(".item-title")
                expect(title).to_be_visible()
                href = title.get_attribute("href") or ""
                assert href.startswith("http")
                assert "/item.html" not in href
                assert "#" not in href


def test_curated_page_does_not_duplicate_selected_badge_in_tags(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/", cards=True)

    assert page.locator(".timeline-card .hot-pill", has_text="精选").count() >= 1
    assert page.locator(".timeline-card .tags .tag", has_text="精选").count() == 0


def test_v10_x_cards_render_clickable_title_to_original(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all?channel=x", cards=True)

    x_cards = page.locator(".timeline-card.x-card")
    assert x_cards.count() >= 3
    assert page.locator(".timeline-card").count() == x_cards.count()
    for index in range(3):
        card = x_cards.nth(index)
        title = card.locator(".item-title")
        expect(title).to_be_visible()
        title_href = title.get_attribute("href") or ""
        assert title_href.startswith("https://x.com/")
        assert "/status/" in title_href
        assert "#" not in title_href
        assert len(card.locator(".summary").inner_text()) > 40
        assert card.locator(".origin-link").count() == 0
        assert card.locator(".x-media-affordance").count() == 0


def test_v10b_article_media_does_not_dominate_viewport(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/", cards=True)

    media = page.locator(".article-media-img")
    assert media.count() >= 1
    max_ratio = media.evaluate_all(
        """imgs => Math.max(...imgs.map(img => img.getBoundingClientRect().height / window.innerHeight))"""
    )
    assert max_ratio <= 0.31


def test_v10d_all_page_preserves_aihot_media_score_and_selected_reason(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    assert page.locator(".timeline-card").count() >= 30
    assert page.locator(".timeline-card .article-media-img").count() >= 1
    assert page.locator(".timeline-card .score-pill").count() >= 1
    assert page.locator(".timeline-card .hot-pill", has_text="精选").count() >= 1
    selected_reason_count = page.locator(".timeline-card").evaluate_all(
        """cards => cards.filter(card =>
          card.querySelector('.hot-pill')?.textContent?.trim() === '精选'
          && card.querySelector('.reason')?.textContent?.trim()
        ).length"""
    )
    assert selected_reason_count >= 1
    assert page.locator(".timeline-card .tags .tag", has_text="精选").count() == 0
    assert page.locator(".timeline-card .timeline-dup-count").count() == 0

    metrics = page.locator(".timeline-card").evaluate_all(
        """cards => {
          const firstTen = cards.slice(0, 10).map(card => card.getBoundingClientRect());
          return {
            firstViewportCards: firstTen.filter(rect => rect.top < window.innerHeight && rect.bottom > 0).length,
            avgHeight: firstTen.reduce((sum, rect) => sum + rect.height, 0) / firstTen.length,
          };
        }"""
    )
    assert metrics["firstViewportCards"] >= 2
    assert metrics["avgHeight"] <= 520


def test_x_cards_use_aihot_body_text_weight_instead_of_bold_article_title(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all?channel=x", cards=True)

    title = page.locator(".timeline-card.x-card .item-title").first
    expect(title).to_be_visible()
    metrics = title.evaluate(
        """el => {
          const style = getComputedStyle(el);
          return {
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            lineHeight: style.lineHeight,
          };
        }"""
    )

    assert metrics["fontSize"] == "14px"
    assert metrics["fontWeight"] == "400"
    assert metrics["lineHeight"] == "23.8px"


def test_v10e_all_page_channel_filters_are_url_backed_targets(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    assert page.locator("[data-channel-filter] .seg-item").all_inner_texts() == ["全部", "一手信源", "资讯", "推文"]
    with page.expect_response(
        lambda response: "/api/v1/timeline" in response.url and "channel=x" in response.url and response.status == 200
    ):
        page.locator('[data-channel-filter] [data-channel="x"]').click()
    expect(page).to_have_url(f"{base_url}/all?channel=x")
    x_cards = page.locator(".timeline-card.x-card")
    assert x_cards.count() >= 3
    assert page.locator(".timeline-card").count() == x_cards.count()

    with page.expect_response(
        lambda response: "/api/v1/timeline" in response.url
        and "channel=firstParty" in response.url
        and response.status == 200
    ):
        page.locator('[data-channel-filter] [data-channel="firstParty"]').click()
    expect(page).to_have_url(f"{base_url}/all?channel=firstParty")
    assert page.locator(".timeline-card.x-card").count() == 0
    assert page.locator('[data-channel-filter] [data-channel="firstParty"]').get_attribute("aria-pressed") == "true"


def test_v10f_all_page_pagination_is_a_natural_click_target(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    next_link = page.locator('.pagination-link[rel="next"]')
    expect(next_link).to_be_visible()
    assert "page=2" in (next_link.get_attribute("href") or "")
    with page.expect_response(
        lambda response: "/api/v1/timeline" in response.url and "page=2" in response.url and response.status == 200
    ):
        next_link.click()

    expect(page).to_have_url(f"{base_url}/all?page=2")
    expect(page.locator('.pagination-link[aria-current="page"]')).to_have_text("2")
    expect(page.locator(".timeline-card").first).to_be_visible()


def test_v10g_mobile_all_page_keeps_scan_reading_density(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _goto(page, base_url, "/all", cards=True)

    metrics = page.locator(".timeline-card").evaluate_all(
        """cards => {
          const firstTen = cards.slice(0, 10).map(card => card.getBoundingClientRect());
          return {
            firstViewportCards: firstTen.filter(rect => rect.top < window.innerHeight && rect.bottom > 0).length,
            avgHeight: firstTen.reduce((sum, rect) => sum + rect.height, 0) / firstTen.length,
            minWidth: Math.min(...firstTen.map(rect => rect.width)),
          };
        }"""
    )
    assert metrics["firstViewportCards"] >= 2
    assert metrics["avgHeight"] <= 420
    assert metrics["minWidth"] >= 280


def test_v10h_typography_tone_matches_aihot_reference(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)

    styles = page.evaluate(
        """() => {
          const pick = (selector) => {
            const el = document.querySelector(selector);
            const cs = getComputedStyle(el);
            return {
              color: cs.color,
              fontFamily: cs.fontFamily,
              fontSize: cs.fontSize,
              fontWeight: cs.fontWeight,
              lineHeight: cs.lineHeight,
            };
          };
            return {
            pageTitle: pick("h1"),
            time: pick(".timeline-time"),
            cardTitle: pick(".timeline-card:not(.x-card) .item-title"),
            xCardTitle: pick(".timeline-card.x-card .item-title"),
            source: pick(".source-line"),
            summary: pick(".summary"),
            tag: pick(".tag"),
            filter: pick(".seg-item"),
          };
        }"""
    )

    assert "Noto Serif SC" in styles["pageTitle"]["fontFamily"]
    assert styles["pageTitle"]["fontWeight"] == "400"
    assert styles["pageTitle"]["fontSize"] == "24px"

    assert "IBM Plex Mono" in styles["time"]["fontFamily"]
    assert styles["time"]["lineHeight"] == "19.8px"

    assert "IBM Plex Sans" in styles["cardTitle"]["fontFamily"]
    assert styles["cardTitle"]["fontSize"] == "15px"
    assert styles["cardTitle"]["fontWeight"] == "700"
    assert styles["cardTitle"]["lineHeight"] == "22.5px"

    assert styles["xCardTitle"]["fontSize"] == "14px"
    assert styles["xCardTitle"]["fontWeight"] == "400"
    assert styles["xCardTitle"]["lineHeight"] == "23.8px"

    assert "IBM Plex Sans" in styles["source"]["fontFamily"]
    assert styles["source"]["fontSize"] == "11px"
    assert styles["source"]["color"] == "rgb(100, 116, 139)"

    assert "IBM Plex Sans" in styles["summary"]["fontFamily"]
    assert styles["summary"]["fontSize"] == "12.5px"
    assert styles["summary"]["lineHeight"] == "20px"
    assert styles["summary"]["color"] == "rgb(148, 163, 184)"

    assert "IBM Plex Mono" in styles["tag"]["fontFamily"]
    assert "IBM Plex Mono" in styles["filter"]["fontFamily"]
    assert styles["filter"]["lineHeight"] == "12px"


def test_v10c_mobile_category_filter_keeps_all_options_visible(page: Page, base_url: str) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _goto(page, base_url, "/", cards=True)

    items = page.locator(".seg-item").evaluate_all(
        """els => els.map(el => {
          const rect = el.getBoundingClientRect();
          return {
            text: el.textContent.trim(),
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
            visible: rect.left >= 0 && rect.right <= window.innerWidth
          };
        })"""
    )
    assert [item["text"] for item in items] == ["全部", "模型", "产品", "行业", "论文", "技巧"]
    assert all(item["visible"] for item in items)


def test_v12_date_groups_are_descending(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)
    groups = _ensure_multiple_date_groups(page)
    assert len(groups) >= 2
    for group in groups:
        values = [_minutes(value) for value in group]
        assert values == sorted(values, reverse=True)

    _goto(page, base_url, "/", cards=True)
    date_values = page.locator(".timeline-date time").evaluate_all("els => els.map(el => el.getAttribute('datetime'))")
    assert date_values == sorted(date_values, reverse=True)
    score_groups = page.evaluate(
        """() => {
          const groups = [];
          let current = null;
          for (const child of document.querySelector("#list").children) {
            if (child.classList.contains("date-group")) {
              current = [];
              groups.push(current);
            } else if (child.classList.contains("timeline-entry") && current) {
              const value = Number(child.querySelector(".score-pill")?.textContent?.trim() || 0);
              if (value) current.push(value);
            }
          }
          return groups;
        }"""
    )
    for group in score_groups:
        assert group == sorted(group, reverse=True)


def test_v14_v15_search_filters_and_clears(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/", cards=True)
    baseline = _visible_card_count(page)

    with page.expect_response(lambda response: "/api/v1/curated" in response.url and "q=OpenAI" in response.url):
        page.locator('input[type="search"]').fill("OpenAI")
    expect(page.locator(".timeline-card").first).to_be_visible()
    assert _visible_card_count(page) < baseline
    for index in range(_visible_card_count(page)):
        assert "openai" in page.locator(".timeline-card").nth(index).inner_text().lower()

    with page.expect_response(lambda response: "/api/v1/curated" in response.url and "q=" not in response.url):
        page.locator('input[type="search"]').fill("")
    expect(page.locator(".timeline-card").first).to_be_visible()
    assert _visible_card_count(page) == baseline


def test_v15b_switching_tabs_clears_search_state(page: Page, base_url: str) -> None:
    _goto(page, base_url, "/all", cards=True)
    all_baseline = _visible_card_count(page)

    _goto(page, base_url, "/", cards=True)
    with page.expect_response(lambda response: "/api/v1/curated" in response.url and "q=OpenAI" in response.url):
        page.locator('input[type="search"]').fill("OpenAI")

    with page.expect_response(
        lambda response: "/api/v1/timeline" in response.url and "q=" not in response.url and response.status == 200
    ):
        page.locator('.side-link[href="/all"]').click()
    expect(page).to_have_url(f"{base_url}/all")
    assert page.locator('input[type="search"]').input_value() == ""
    assert _visible_card_count(page) == all_baseline
