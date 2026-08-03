from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from markdown_it import MarkdownIt

from airadar.db import migrate
from airadar.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
SIDEBAR_CONSUMERS = [
    ROOT / "web/templates/index.html",
    ROOT / "web/templates/all.html",
    ROOT / "web/templates/about.html",
    ROOT / "web/templates/bookmarks.html",
    ROOT / "web/templates/hot.html",
    ROOT / "web/templates/wechat.html",
    ROOT / "web/templates/wechat_404.html",
    ROOT / "web/templates/wechat_detail.html",
    ROOT / "web/static/index.html",
    ROOT / "web/static/all.html",
    ROOT / "web/static/daily.html",
    ROOT / "web/static/item.html",
]


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _inline_text_and_links(token: object) -> tuple[str, tuple[str, ...]]:
    content = str(getattr(token, "content", ""))
    rendered = MarkdownIt("commonmark").renderInline(content)
    soup = BeautifulSoup(rendered, "html.parser")
    return _normalize(soup.get_text(" ", strip=True)), tuple(
        str(link.get("href")) for link in soup.select("a[href]")
    )


def _markdown_blocks(markdown: str) -> list[tuple[str, str, tuple[str, ...]]]:
    tokens = MarkdownIt("commonmark").parse(markdown)
    blocks: list[tuple[str, str, tuple[str, ...]]] = []
    list_depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            list_depth += 1
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            list_depth -= 1
        elif token.type == "heading_open":
            text, links = _inline_text_and_links(tokens[index + 1])
            blocks.append(("heading", text, links))
        elif token.type == "list_item_open":
            cursor = index + 1
            item_depth = 1
            texts: list[str] = []
            links: list[str] = []
            while cursor < len(tokens) and item_depth:
                current = tokens[cursor]
                if current.type == "list_item_open":
                    item_depth += 1
                elif current.type == "list_item_close":
                    item_depth -= 1
                elif current.type == "inline" and item_depth == 1:
                    text, inline_links = _inline_text_and_links(current)
                    texts.append(text)
                    links.extend(inline_links)
                cursor += 1
            blocks.append(("list item", _normalize(" ".join(texts)), tuple(links)))
        elif token.type == "paragraph_open" and list_depth == 0:
            text, links = _inline_text_and_links(tokens[index + 1])
            blocks.append(("paragraph", text, links))
        elif token.type in {"fence", "code_block"}:
            blocks.append(("code", _normalize(token.content), ()))
        index += 1
    return blocks


def _dom_blocks(container: object) -> list[tuple[str, str, tuple[str, ...]]]:
    blocks: list[tuple[str, str, tuple[str, ...]]] = []
    for element in container.select("h1, h2, h3, h4, h5, h6, p, li, pre"):
        if element.find_parent("li") is not None:
            continue
        kind = (
            "heading"
            if element.name.startswith("h")
            else "list item"
            if element.name == "li"
            else "code"
            if element.name == "pre"
            else "paragraph"
        )
        blocks.append(
            (
                kind,
                _normalize(element.get_text(" ", strip=True)),
                tuple(str(link.get("href")) for link in element.select("a[href]")),
            )
        )
    return blocks


def _run_daily_module(script: str) -> object:
    command = f"""
      import * as daily from './web/static/app.js';
      {script}
    """
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", command],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_changelog_route_matches_every_source_block_and_link(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    response = TestClient(create_app(db_path)).get("/changelog")

    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    container = soup.select_one("#changelog-content")
    assert container is not None
    assert _dom_blocks(container) == _markdown_blocks(CHANGELOG.read_text(encoding="utf-8"))
    active = soup.select(".side-link-active")
    assert len(active) == 1
    assert active[0].get("href") == "/changelog"


def test_every_desktop_sidebar_consumer_links_changelog_once() -> None:
    for path in SIDEBAR_CONSUMERS:
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        links = soup.select(".side-nav a[href='/changelog']")
        assert len(links) == 1, path
        assert links[0].get_text(strip=True) == "更新日志"
        assert not links[0].select(".unread-dot"), path


def test_daily_reading_formula_boundaries_and_dom_exclusion() -> None:
    result = _run_daily_module(
        """
        const boundaries = [0, 1, 300, 301].map((count) => daily.dailyReadingMinutes(count));
        const requestedSelectors = [];
        const root = {
          title: '标题中文不应计入',
          navigation: '导航中文也不计入',
          querySelectorAll(selector) {
            requestedSelectors.push(selector);
            return [
              { textContent: '摘要甲AI English 123' },
              { textContent: '摘要乙，含標題外干扰' },
              { textContent: 'English only' },
            ];
          },
        };
        console.log(JSON.stringify({ boundaries, stats: daily.dailyReadingStats(root), requestedSelectors }));
        """
    )

    assert result == {
        "boundaries": [1, 1, 1, 2],
        "stats": {"characters": 12, "minutes": 1},
        "requestedSelectors": [".daily-article-summary"],
    }


def test_daily_visible_cjk_count_excludes_line_clamped_text() -> None:
    result = _run_daily_module(
        """
        const textNode = { nodeValue: '摘要甲乙' };
        let walked = false;
        let start = 0;
        const ownerDocument = {
          createTreeWalker() {
            return { nextNode() { if (walked) return null; walked = true; return textNode; } };
          },
          createRange() {
            return {
              setStart(_node, offset) { start = offset; },
              setEnd() {},
              getClientRects() {
                return [{ left: 1, right: 2, top: start < 2 ? 1 : 20, bottom: start < 2 ? 2 : 21 }];
              },
              detach() {},
            };
          },
        };
        const node = {
          textContent: textNode.nodeValue,
          ownerDocument,
          getBoundingClientRect() { return { left: 0, right: 10, top: 0, bottom: 10, width: 10, height: 10 }; },
        };
        console.log(JSON.stringify(daily.dailyVisibleCjkCount(node)));
        """
    )

    assert result == 2


def test_rendered_daily_fixture_counts_only_summary_cjk_and_preserves_sections() -> None:
    html = _run_daily_module(
        """
        const container = { innerHTML: '' };
        daily.renderDailyReport(container, [
          {
            id: 1,
            title: '标题中文不计入甲',
            summary_zh: '摘要甲AI',
            topic_tags: ['模型发布'],
            source_kind: 'rss',
            source_name: '中文来源甲',
            url: 'https://example.com/1',
            published_at: '2026-08-01T08:00:00+08:00',
          },
          {
            id: 2,
            title: '标题中文不计入乙',
            summary_zh: '摘要乙，English 123',
            topic_tags: ['行业动态'],
            source_kind: 'rss',
            source_name: '中文来源乙',
            url: 'https://example.com/2',
            published_at: '2026-08-01T09:00:00+08:00',
          },
          {
            id: 3,
            title: '标题中文不计入丙',
            summary_zh: 'English only summary',
            topic_tags: ['行业动态'],
            source_kind: 'rss',
            source_name: '中文来源丙',
            url: 'https://example.com/3',
            published_at: '2026-08-01T10:00:00+08:00',
          },
        ], '2026-08-01');
        console.log(JSON.stringify(container.innerHTML));
        """
    )
    soup = BeautifulSoup(str(html), "html.parser")
    summaries = soup.select(".daily-article-summary")
    summary_text = "".join(node.get_text() for node in summaries)
    all_text = soup.get_text()

    cjk_pattern = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
    summary_cjk = cjk_pattern.findall(summary_text)
    assert len(soup.select(".daily-article")) == 3
    assert [len(section.select(".daily-article")) for section in soup.select(".daily-section")] == [1, 2]
    assert len(summaries) == 3
    assert len(summary_cjk) == 6
    assert len(cjk_pattern.findall(all_text)) > len(summary_cjk)
    assert _run_daily_module("console.log(JSON.stringify(daily.dailyReadingMinutes(6))); ") == 1


def test_daily_masthead_uses_effective_date_and_non_sample_story_count() -> None:
    result = _run_daily_module(
        """
        const elements = {
          '#daily-volume': { textContent: '' },
          '.daily-story-count': { textContent: '' },
          '.daily-readable-date': { textContent: '', setAttribute(name, value) { this[name] = value; } },
        };
        globalThis.document = { querySelector(selector) { return elements[selector] || null; } };
        daily.renderDailyHeader('2026-08-01', 3);
        console.log(JSON.stringify(elements));
        """
    )

    assert result["#daily-volume"]["textContent"] == "VOL.2026.08.01"
    assert result[".daily-story-count"]["textContent"] == "3 STORIES"
    assert result[".daily-readable-date"]["datetime"] == "2026-08-01"


def test_daily_date_resolution_separates_invalid_future_and_valid_empty_dates() -> None:
    result = _run_daily_module(
        """
        const latest = '2026-08-02';
        const today = '2026-08-03';
        console.log(JSON.stringify({
          invalid: daily.resolveDailyRequest('not-a-date', latest, today),
          future: daily.resolveDailyRequest('2026-08-04', latest, today),
          empty: daily.resolveDailyRequest('2026-07-31', latest, today),
          fallbackPath: daily.dailyPath(latest),
          fallbackStatus: daily.dailyFallbackStatus(latest),
        }));
        """
    )

    assert result["invalid"] == {
        "activeDate": "2026-08-02",
        "rewriteUrl": True,
        "showFallbackStatus": True,
    }
    assert result["future"] == result["invalid"]
    assert result["empty"] == {
        "activeDate": "2026-07-31",
        "rewriteUrl": False,
        "showFallbackStatus": False,
    }
    assert result["fallbackPath"] == "/daily/2026-08-02"
    assert result["fallbackStatus"] == "请求的日期无效或晚于今天，已显示最近一期 2026-08-02"


def test_daily_malformed_path_and_load_generation_are_fail_closed() -> None:
    result = _run_daily_module(
        """
        globalThis.location = { pathname: '/daily/%E0%A4%A' };
        const gate = daily.createDailyLoadGate();
        const older = gate.begin('2026-08-01');
        const newer = gate.begin('2026-08-02');
        console.log(JSON.stringify({
          pathDate: daily.dailyDateFromPath(),
          olderCurrent: gate.isCurrent(older),
          newerCurrent: gate.isCurrent(newer),
        }));
        """
    )

    assert result == {
        "pathDate": "%E0%A4%A",
        "olderCurrent": False,
        "newerCurrent": True,
    }


def test_daily_archive_endpoint_returns_one_complete_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "archive.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        ) VALUES ('s', 'Source', 'https://example.com/feed', 'T1', 1, 'feed',
                  'https://example.com', NULL, '{}', '2026-08-02T00:00:00Z')
        """
    )
    items = [
        ("a", "https://example.com/a", "2026-08-01T16:30:00Z"),
        ("b", "https://example.com/b", "2026-08-02T03:00:00Z"),
        ("c", "https://example.com/c", "2026-07-30T16:30:00Z"),
        ("old-a", "https://example.com/a/", "2026-07-29T16:30:00Z"),
    ]
    for item_id, url, published_at in items:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (?, 's', ?, ?, 'Ada', ?, ?, 'content', NULL, ?, '{}')
            """,
            (item_id, url, item_id, published_at, published_at, item_id),
        )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        ) VALUES ('run', 'test.r1', '{}', 6.5, '[]', '[]', '2026-08-02T04:00:00Z')
        """
    )
    for rank, (item_id, _url, _published_at) in enumerate(items, start=1):
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run', ?, 8.0, ?, '{}')
            """,
            (item_id, rank),
        )
    conn.commit()
    conn.close()

    response = TestClient(create_app(db_path)).get("/api/v1/curated/daily-archive")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "days": [
            {"date": "2026-08-02", "count": 2},
            {"date": "2026-07-31", "count": 1},
        ],
        "count": 2,
    }


def test_daily_archive_excludes_future_published_dates(tmp_path: Path) -> None:
    """未来 published_at 不得成为「最近一期」：feed 时间戳不受信任（review-gate HIGH-1）。"""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    shanghai_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    future_day = (shanghai_today + timedelta(days=2)).isoformat()
    past_day = (shanghai_today - timedelta(days=1)).isoformat()

    db_path = tmp_path / "archive-future.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        ) VALUES ('s', 'Source', 'https://example.com/feed', 'T1', 1, 'feed',
                  'https://example.com', NULL, '{}', '2026-08-02T00:00:00Z')
        """
    )
    items = [
        ("future", "https://example.com/future", f"{future_day}T01:00:00+08:00"),
        ("past", "https://example.com/past", f"{past_day}T12:00:00+08:00"),
    ]
    for item_id, url, published_at in items:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            ) VALUES (?, 's', ?, ?, 'Ada', ?, ?, 'content', NULL, ?, '{}')
            """,
            (item_id, url, item_id, published_at, published_at, item_id),
        )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        ) VALUES ('run', 'test.r1', '{}', 6.5, '[]', '[]', '2026-08-02T04:00:00Z')
        """
    )
    for rank, (item_id, _url, _published_at) in enumerate(items, start=1):
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run', ?, 8.0, ?, '{}')
            """,
            (item_id, rank),
        )
    conn.commit()
    conn.close()

    days = TestClient(create_app(db_path)).get("/api/v1/curated/daily-archive").json()["data"]["days"]
    listed = [str(day["date"]) for day in days]

    assert future_day not in listed, "未来日期出现在归档里，会被前端当成「最近一期」"
    assert past_day in listed
    assert listed == sorted(listed, reverse=True)
    assert all(date_value <= shanghai_today.isoformat() for date_value in listed)


def test_daily_archive_contract_has_month_groups_without_sampling_caps() -> None:
    js = (ROOT / "web/static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "web/static/daily.html").read_text(encoding="utf-8")

    assert "daily-side-month" in js
    assert "daily-side-month-count" in js
    assert "<details" in js
    assert 'api("/api/v1/curated/daily-archive")' in js
    assert 'queryPath("/api/v1/curated", { limit, page' not in js
    assert 'window.addEventListener("popstate"' in js
    assert "全部日报" in html
    assert "Array.from({ length: 16 }" not in js
    assert ".slice(0, 12)" not in js
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-label="今日看点"' in html
    assert "日报 / 周报 / 月报" not in html


def test_daily_archive_grouping_preserves_complete_date_and_month_counts() -> None:
    result = _run_daily_module(
        """
        const items = [
          { id: 1, title: '八月二日之一', published_at: '2026-08-02T08:00:00+08:00' },
          { id: 2, title: '八月二日之二', published_at: '2026-08-02T10:00:00+08:00' },
          { id: 3, title: '八月一日', published_at: '2026-08-01T08:00:00+08:00' },
          { id: 4, title: '七月末', published_at: '2026-07-31T08:00:00+08:00' },
          { id: 5, title: '六月末', published_at: '2026-06-30T08:00:00+08:00' },
          { id: 5, title: '重复分页项', published_at: '2026-06-30T08:00:00+08:00' },
        ];
        const days = daily.groupDailyArchiveItems(items);
        const months = Object.groupBy(days, (day) => day.date.slice(0, 7));
        console.log(JSON.stringify({
          days,
          monthCounts: Object.fromEntries(Object.entries(months).map(([month, values]) => [month, values.length])),
        }));
        """
    )

    assert result == {
        "days": [
            {"date": "2026-08-02", "title": "八月二日之一", "count": 2},
            {"date": "2026-08-01", "title": "八月一日", "count": 1},
            {"date": "2026-07-31", "title": "七月末", "count": 1},
            {"date": "2026-06-30", "title": "六月末", "count": 1},
        ],
        "monthCounts": {"2026-08": 2, "2026-07": 1, "2026-06": 1},
    }


def test_phase4_css_uses_daily_token_bridge_and_measured_changelog_geometry() -> None:
    css = (ROOT / "web/static/style.css").read_text(encoding="utf-8")

    assert "--d-bg: var(--bg);" in css
    assert "--d-text: var(--ink);" in css
    assert '[data-theme="light"] .daily-shell' in css
    assert "--d-bg: var(--panel);" in css
    assert ".cl-shell" in css
    assert "max-width: 880px;" in css
    assert "padding: 56px 24px 80px;" in css
    assert ".cl-eyebrow" in css
    assert "letter-spacing: 0.16em;" in css
    assert "AIHOT" not in BeautifulSoup((ROOT / "web/static/daily.html").read_text(), "html.parser").get_text()
