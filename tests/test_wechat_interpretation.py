from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.llm_usage import migrate_usage_db
from airadar.web.app import create_app

SUMMARY_MD = """### 📋 文章概况

这是一篇关于 agent 工程实践的文章，摘要可直接放在卡片上。

### ✨ 独特亮点

- 提供了真实工程取舍。

### 🛠️ 可动手实践

- 可以马上复用检查清单。

### 🧠 可复用认知

- 先稳定输入输出契约，再扩展自动化。

### 🔑 关键词

Agent, 工程化, 自动化

### ⚖️ 价值判断

值得一看，因为它包含可执行的工程实践。
<script>alert("xss")</script><img src="x" onerror="alert(1)">
"""


def _preload_from_html(html: str) -> dict[str, Any]:
    match = re.search(r'<script id="__PRELOAD__" type="application/json">\s*(.*?)\s*</script>', html, re.S)
    assert match is not None
    return json.loads(match.group(1))


def _feed_filter_form(html: str) -> str:
    start_marker = '<form class="feed-filter"'
    assert start_marker in html
    return html.split(start_marker, 1)[1].split("</form>", 1)[0]


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_wechat_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'wx_mp2rss', '微信公众号（Mp2RSS 合集）', 'https://feed.example/rss', 'T2', 1,
          'wechat', 'https://mp.weixin.qq.com/', '/wechat-icon.svg', '{}', '2026-06-02T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES (
          '机器之心', 'https://example.com/avatar.png',
          '2026-06-02T00:00:00Z', '2026-06-02T00:00:00Z'
        )
        """
    )
    items = [
        (
            "item-newer",
            "https://mp.weixin.qq.com/s/newer",
            "新文章",
            "机器之心",
            "2026-06-02T10:00:00Z",
            "agent 工程实践正文",
            "h-newer",
        ),
        (
            "item-older",
            "https://mp.weixin.qq.com/s/older",
            "旧文章",
            "机器之心",
            "2026-06-01T10:00:00Z",
            "旧文章正文",
            "h-older",
        ),
        (
            "item-skip",
            "https://mp.weixin.qq.com/s/skip",
            "不值得读",
            "机器之心",
            "2026-05-31T10:00:00Z",
            "短消息正文",
            "h-skip",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, 'wx_mp2rss', ?, ?, ?, ?, ?, ?, NULL, ?, '{}')
        """,
        [
            (item_id, url, title, author, published_at, published_at, content, content_hash)
            for item_id, url, title, author, published_at, content, content_hash in items
        ],
    )
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (
          'item-newer', 'newer-slug', '值得一看', 1, '有实践价值',
          '这是一篇关于 agent 工程实践的文章，摘要可直接放在卡片上。',
          '["Agent","工程化"]', ?, 'fake-model', 1, '2026-06-02T10:10:00Z', NULL
        )
        """,
        (SUMMARY_MD,),
    )
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (
          'item-older', 'older-slug', '必读', 1, '强相关',
          '旧文章摘要', '["AI"]', ?, 'fake-model', 1, '2026-06-01T10:10:00Z', NULL
        )
        """,
        (SUMMARY_MD,),
    )
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (
          'item-skip', 'skip-slug', '可跳过', 0, '信息密度低',
          '短消息摘要', '["低价值"]', ?, 'fake-model', 0, '2026-05-31T10:10:00Z', NULL
        )
        """,
        (SUMMARY_MD,),
    )
    conn.commit()
    conn.close()
    return db_path


def _seed_wechat_search_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "search-radar.db"
    migrate(db_path)
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'wx_mp2rss', '微信公众号（Mp2RSS 合集）', 'https://feed.example/rss', 'T2', 1,
          'wechat', 'https://mp.weixin.qq.com/', '/wechat-icon.svg', '{}', '2026-06-02T00:00:00Z'
        )
        """
    )
    rows = [
        (
            "search-author-new",
            "author-new-slug",
            "https://mp.weixin.qq.com/s/author-new",
            "机器学习进展",
            "机器之心",
            "2026-06-07T10:00:00Z",
            "searchterm author newer abstract",
            '["基线"]',
            "总结里没有特殊词。",
        ),
        (
            "search-non-author",
            "non-author-slug",
            "https://mp.weixin.qq.com/s/non-author",
            "提到机器之心的外部观察",
            "量子位",
            "2026-06-08T10:00:00Z",
            "searchterm non author abstract",
            '["观察"]',
            "总结里没有特殊词。",
        ),
        (
            "search-author-old",
            "author-old-slug",
            "https://mp.weixin.qq.com/s/author-old",
            "工程实践旧文",
            "机器之心",
            "2026-06-06T10:00:00Z",
            "searchterm author older abstract",
            '["工程"]',
            "总结里没有特殊词。",
        ),
        (
            "search-title",
            "title-slug",
            "https://mp.weixin.qq.com/s/title",
            "具身智能平台进展",
            "新智元",
            "2026-06-05T10:00:00Z",
            "普通摘要",
            '["机器人"]',
            "总结里有只在 summary_md 出现的深海泡泡词。",
        ),
        (
            "search-abstract",
            "abstract-slug",
            "https://mp.weixin.qq.com/s/abstract",
            "多模态模型观察",
            "量子位",
            "2026-06-04T10:00:00Z",
            "这里包含蓝桥摘要词用于抽象字段检索",
            '["模型"]',
            "总结里没有特殊词。",
        ),
        (
            "search-tag",
            "tag-slug",
            "https://mp.weixin.qq.com/s/tag",
            "图像模型观察",
            "量子位",
            "2026-06-03T10:00:00Z",
            "普通摘要",
            '["检索标签"]',
            "总结里没有特殊词。",
        ),
        (
            "search-traditional",
            "guicang-slug",
            "https://mp.weixin.qq.com/s/guicang",
            "工作流工具箱",
            "歸藏的AI工具箱",
            "2026-06-02T10:00:00Z",
            "普通摘要",
            '["工具"]',
            "总结里没有特殊词。",
        ),
        (
            "search-skipped",
            "skipped-slug",
            "https://mp.weixin.qq.com/s/skipped",
            "不应出现在搜索",
            "机器之心",
            "2026-06-09T10:00:00Z",
            "searchterm skipped abstract",
            '["跳过"]',
            "总结里没有特殊词。",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, 'wx_mp2rss', ?, ?, ?, ?, ?, '正文', NULL, ?, '{}')
        """,
        [
            (item_id, url, title, author, published_at, published_at, f"h-{item_id}")
            for item_id, _slug, url, title, author, published_at, _abstract, _tags, _summary in rows
        ],
    )
    conn.executemany(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (?, ?, '值得一看', ?, '测试', ?, ?, ?, 'fake-model', 1, ?, NULL)
        """,
        [
            (
                item_id,
                slug,
                0 if item_id == "search-skipped" else 1,
                abstract,
                tags,
                summary,
                published_at,
            )
            for item_id, slug, _url, _title, _author, published_at, abstract, tags, summary in rows
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def _seed_runner_db(tmp_path: Path, *, item_id: str = "item-1", title: str = "测试文章") -> Path:
    db_path = tmp_path / "runner.db"
    migrate(db_path)
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'wx_mp2rss', '微信公众号（Mp2RSS 合集）', 'https://feed.example/rss', 'T2', 1,
          'wechat', 'https://mp.weixin.qq.com/', '/wechat-icon.svg', '{}', '2026-06-02T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, 'wx_mp2rss', 'https://mp.weixin.qq.com/s/test', ?, '机器之心',
                '2026-06-02T10:00:00Z', '2026-06-02T10:01:00Z',
                '这是一篇足够长的微信正文，用来喂给 summarize-article。', NULL, ?, '{}')
        """,
        (item_id, title, f"h-{item_id}"),
    )
    conn.commit()
    conn.close()
    return db_path


def _assistant_root(tmp_path: Path) -> Path:
    root = tmp_path / "ai-assistant"
    script_dir = root / "agents" / "summary-agent"
    script_dir.mkdir(parents=True)
    for name in ("summarize.sh", "run.sh"):
        path = script_dir / name
        path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return root


def _enable_interpret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_RADAR_ENABLE_INTERPRET", "true")


def test_wechat_migration_creates_table_and_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = _connect(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(wechat_interpretations)").fetchall()}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(wechat_interpretations)").fetchall()}
    finally:
        conn.close()

    assert {"item_id", "slug", "save_decision", "abstract", "tags_json", "summary_md", "kb_synced"} <= columns
    assert "idx_wechat_interp_decision" in indexes
    assert "idx_wechat_interp_slug" in indexes


def test_wechat_api_returns_only_worth_reading_items_with_pagination(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    response = client.get("/api/v1/wechat?limit=1&page=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["total"] == 2
    assert payload["data"]["page"] == 1
    assert payload["data"]["limit"] == 1
    assert [item["slug"] for item in payload["data"]["items"]] == ["newer-slug"]
    item = payload["data"]["items"][0]
    assert item["title"] == "新文章"
    assert item["abstract"]
    assert item["tags"] == ["Agent", "工程化"]
    assert item["author"] == "机器之心"
    assert item["avatar_url"] == "https://example.com/avatar.png"

    full_response = client.get("/api/v1/wechat?limit=500")
    assert full_response.status_code == 200


def test_wechat_api_clamps_out_of_range_page_and_carries_page_in_detail_urls(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    response = client.get("/api/v1/wechat?limit=1&page=999")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert data["page"] == 2
    assert [item["slug"] for item in data["items"]] == ["older-slug"]
    assert data["items"][0]["detail_url"] == "/wechat/older-slug?page=2"

    listing = client.get("/wechat?limit=1&page=999")
    assert listing.status_code == 200
    assert "暂无微信文章解读" not in listing.text
    assert "/wechat/older-slug?page=2" in listing.text


def test_wechat_api_searches_interpretation_card_fields_and_prioritizes_author(tmp_path: Path) -> None:
    db_path = _seed_wechat_search_db(tmp_path)
    client = TestClient(create_app(db_path))

    title_data = client.get("/api/v1/wechat?q=具身智能&limit=50").json()["data"]
    assert title_data["total"] == 1
    assert [item["slug"] for item in title_data["items"]] == ["title-slug"]

    abstract_data = client.get("/api/v1/wechat?q=蓝桥摘要词&limit=50").json()["data"]
    assert abstract_data["total"] == 1
    assert [item["slug"] for item in abstract_data["items"]] == ["abstract-slug"]

    tag_data = client.get("/api/v1/wechat?q=检索标签&limit=50").json()["data"]
    assert tag_data["total"] == 1
    assert [item["slug"] for item in tag_data["items"]] == ["tag-slug"]

    author_data = client.get("/api/v1/wechat?q=机器之心&limit=50").json()["data"]
    authors = [item["author"] for item in author_data["items"]]
    first_non_author_index = next(index for index, author in enumerate(authors) if author != "机器之心")
    assert all(author == "机器之心" for author in authors[:first_non_author_index])
    assert all(author != "机器之心" for author in authors[first_non_author_index:])
    assert [item["slug"] for item in author_data["items"][:2]] == ["author-new-slug", "author-old-slug"]


def test_wechat_api_search_ignores_internal_whitespace_across_card_fields(tmp_path: Path) -> None:
    db_path = _seed_wechat_search_db(tmp_path)
    conn = _connect(db_path)
    rows = [
        (
            "search-whitespace-title",
            "whitespace-title-slug",
            "https://mp.weixin.qq.com/s/whitespace-title",
            "分享Claude Code",
            "空白测试",
            "2026-06-10T10:00:00Z",
            "普通摘要",
            '["普通标签"]',
        ),
        (
            "search-whitespace-author",
            "whitespace-author-slug",
            "https://mp.weixin.qq.com/s/whitespace-author",
            "普通标题",
            "空白作者Claude Code",
            "2026-06-10T09:00:00Z",
            "普通摘要",
            '["普通标签"]',
        ),
        (
            "search-whitespace-abstract",
            "whitespace-abstract-slug",
            "https://mp.weixin.qq.com/s/whitespace-abstract",
            "普通标题",
            "空白测试",
            "2026-06-10T08:00:00Z",
            "这段摘要包含空白摘要Claude Code字段",
            '["普通标签"]',
        ),
        (
            "search-whitespace-tag",
            "whitespace-tag-slug",
            "https://mp.weixin.qq.com/s/whitespace-tag",
            "普通标题",
            "空白测试",
            "2026-06-10T07:00:00Z",
            "普通摘要",
            '["空白标签Claude Code"]',
        ),
    ]
    conn.executemany(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, 'wx_mp2rss', ?, ?, ?, ?, ?, '正文', NULL, ?, '{}')
        """,
        [
            (item_id, url, title, author, published_at, published_at, f"h-{item_id}")
            for item_id, _slug, url, title, author, published_at, _abstract, _tags in rows
        ],
    )
    conn.executemany(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (?, ?, '值得一看', 1, '测试', ?, ?, ?, 'fake-model', 1, ?, NULL)
        """,
        [
            (item_id, slug, abstract, tags, SUMMARY_MD, published_at)
            for item_id, slug, _url, _title, _author, published_at, abstract, tags in rows
        ],
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    title_exact = client.get("/api/v1/wechat", params={"q": "分享Claude Code", "limit": 50}).json()["data"]
    title_spaced = client.get("/api/v1/wechat", params={"q": "分享 Claude Code", "limit": 50}).json()["data"]
    title_full_width_tab = client.get(
        "/api/v1/wechat",
        params={"q": "分享\u3000Claude\tCode", "limit": 50},
    ).json()["data"]

    assert [item["slug"] for item in title_exact["items"]] == ["whitespace-title-slug"]
    assert [item["slug"] for item in title_spaced["items"]] == ["whitespace-title-slug"]
    assert [item["slug"] for item in title_full_width_tab["items"]] == ["whitespace-title-slug"]

    author = client.get("/api/v1/wechat", params={"q": "空白作者 Claude Code", "limit": 50}).json()["data"]
    abstract = client.get("/api/v1/wechat", params={"q": "空白摘要 Claude Code", "limit": 50}).json()["data"]
    tag = client.get("/api/v1/wechat", params={"q": "空白标签 Claude Code", "limit": 50}).json()["data"]

    assert [item["slug"] for item in author["items"]] == ["whitespace-author-slug"]
    assert [item["slug"] for item in abstract["items"]] == ["whitespace-abstract-slug"]
    assert [item["slug"] for item in tag["items"]] == ["whitespace-tag-slug"]


def test_wechat_api_search_supports_traditional_simplified_short_terms_and_negative_fields(
    tmp_path: Path,
) -> None:
    db_path = _seed_wechat_search_db(tmp_path)
    client = TestClient(create_app(db_path))

    for query in ("歸藏", "归藏"):
        data = client.get(f"/api/v1/wechat?q={query}&limit=50").json()["data"]
        assert data["total"] == 1
        assert [item["slug"] for item in data["items"]] == ["guicang-slug"]

    source_name_only = client.get("/api/v1/wechat?q=合集&limit=50").json()["data"]
    assert source_name_only["total"] == 0
    assert source_name_only["items"] == []

    summary_only = client.get("/api/v1/wechat?q=深海泡泡词&limit=50").json()["data"]
    assert summary_only["total"] == 0
    assert summary_only["items"] == []


def test_wechat_api_search_paginates_clamps_and_carries_q_in_detail_urls(tmp_path: Path) -> None:
    db_path = _seed_wechat_search_db(tmp_path)
    client = TestClient(create_app(db_path))

    page_two = client.get("/api/v1/wechat?q=searchterm&limit=2&page=2").json()["data"]
    assert page_two["total"] == 3
    assert page_two["page"] == 2
    assert [item["slug"] for item in page_two["items"]] == ["author-old-slug"]
    parsed_detail = urlparse(page_two["items"][0]["detail_url"])
    assert parsed_detail.path == "/wechat/author-old-slug"
    assert parse_qs(parsed_detail.query) == {"q": ["searchterm"], "page": ["2"]}

    out_of_range = client.get("/api/v1/wechat?q=searchterm&limit=2&page=999").json()["data"]
    assert out_of_range["total"] == 3
    assert out_of_range["page"] == 2
    assert [item["slug"] for item in out_of_range["items"]] == ["author-old-slug"]

    empty = client.get("/api/v1/wechat?q=zzz不存在zzz&limit=50&page=3")
    assert empty.status_code == 200
    assert empty.json()["data"] == {"items": [], "total": 0, "page": 1, "limit": 50}

    no_query = client.get("/api/v1/wechat?limit=50").json()["data"]
    assert no_query["total"] == 7
    assert [item["slug"] for item in no_query["items"]] == [
        "non-author-slug",
        "author-new-slug",
        "author-old-slug",
        "title-slug",
        "abstract-slug",
        "tag-slug",
        "guicang-slug",
    ]


def test_wechat_title_artifact_repair_cleans_existing_title_and_unique_slug(tmp_path: Path) -> None:
    from airadar.interpret.runner import repair_wechat_title_artifacts

    db_path = _seed_wechat_db(tmp_path)
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE items SET title=? WHERE id='item-older'",
            ("居然可以在 Claude 桌面端用三方模型了！\\n\\n只需要",),
        )
        conn.execute(
            "UPDATE wechat_interpretations SET slug=? WHERE item_id='item-older'",
            ("居然可以在-claude-桌面端用三方模型了-n-n只需要",),
        )
        conn.commit()

        changed = repair_wechat_title_artifacts(conn)
        item = conn.execute("SELECT title FROM items WHERE id='item-older'").fetchone()
        interp = conn.execute("SELECT slug FROM wechat_interpretations WHERE item_id='item-older'").fetchone()

    assert changed == 1
    assert item["title"] == "居然可以在 Claude 桌面端用三方模型了！ 只需要"
    assert "\\n" not in item["title"]
    assert "\n" not in item["title"]
    assert interp["slug"] == "居然可以在-claude-桌面端用三方模型了-只需要"
    assert "-n-" not in interp["slug"]


def test_wechat_pages_render_preload_detail_and_sanitize_markdown(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    listing = client.get("/wechat")
    assert listing.status_code == 200
    assert 'class="app-mobile-bar"' not in listing.text
    assert 'class="m-pagehead"' in listing.text
    assert "微信文章解读" in listing.text
    assert "新文章" in listing.text
    assert "/wechat/newer-slug" in listing.text
    assert 'data-detail-url="/wechat/newer-slug"' in listing.text
    assert 'role="link"' in listing.text
    assert "skip-slug" not in listing.text
    assert '<script id="__PRELOAD__" type="application/json">' in listing.text

    detail = client.get("/wechat/newer-slug")
    assert detail.status_code == 200
    assert 'class="app-mobile-bar"' not in detail.text
    assert 'class="m-pagehead mobile-context-head"' in detail.text
    assert 'data-item-id="item-newer"' in detail.text
    assert "‹ 返回列表" in detail.text
    for heading in ("文章概况", "独特亮点", "可动手实践", "可复用认知", "关键词", "价值判断"):
        assert heading in detail.text
    assert "<h3" in detail.text
    # 详情页只允许 head 主题引导 + 导航 module 两个脚本；sanitize 后的正文不得再有 script
    lowered_detail = detail.text.lower()
    assert lowered_detail.count("<script") == 2
    assert 'localstorage.getitem("ai-radar:theme")' in lowered_detail
    assert "initnavigationonly" in lowered_detail
    markdown_part = lowered_detail.split('class="summary-body', 1)[1].split("</main>", 1)[0]
    assert "<script" not in markdown_part
    assert "onerror" not in detail.text.lower()

    detail_from_page = client.get("/wechat/newer-slug?page=2")
    assert detail_from_page.status_code == 200
    assert 'href="/wechat?page=2"' in detail_from_page.text

    skipped = client.get("/wechat/skip-slug")
    assert skipped.status_code == 404
    assert 'class="app-mobile-bar"' not in skipped.text
    assert 'class="m-pagehead mobile-context-head"' in skipped.text
    assert "微信文章解读" in skipped.text
    assert 'href="/wechat"' in skipped.text
    assert "side-link-active" in skipped.text


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/wechat", 200),
        ("/wechat/newer-slug", 200),
        ("/wechat/missing-slug", 404),
    ],
)
def test_wechat_ssr_routes_close_request_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
    expected_status: int,
) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))
    class TrackingConnection(sqlite3.Connection):
        closed_by_route = False

        def close(self) -> None:
            self.closed_by_route = True
            super().close()

    opened: list[TrackingConnection] = []
    original_connect = sqlite3.connect

    def tracking_connect(*args: Any, **kwargs: Any) -> TrackingConnection:
        kwargs.setdefault("check_same_thread", False)
        conn = original_connect(*args, factory=TrackingConnection, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    try:
        response = client.get(path)
        assert response.status_code == expected_status
        assert len(opened) == 1
        assert opened[0].closed_by_route is True
    finally:
        for conn in opened:
            if not conn.closed_by_route:
                conn.close()


def test_wechat_detail_and_list_render_visit_original_links(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    detail = client.get("/wechat/newer-slug")
    assert detail.status_code == 200
    # 详情页：显式「访问原文」描边按钮，指向该条 items.url、新标签页打开
    assert 'class="wechat-origin-link"' in detail.text
    assert "访问原文" in detail.text
    assert (
        '<a class="wechat-origin-link" href="https://mp.weixin.qq.com/s/newer"'
        ' target="_blank" rel="noopener noreferrer"' in detail.text
    )
    # 保留：既有公众号名 source-link 仍在、仍指向原文（已拍决策，不得删）
    assert 'class="source-link" href="https://mp.weixin.qq.com/s/newer"' in detail.text

    listing = client.get("/wechat")
    assert listing.status_code == 200
    # 列表 count 一致性：原文链接数 == 有 url 的 worth-reading 卡片数（动态导出，非 existence）
    with _connect(db_path) as conn:
        expected_cards = conn.execute(
            """
            SELECT COUNT(*)
            FROM wechat_interpretations wi
            JOIN items i ON i.id = wi.item_id
            WHERE wi.save_decision = 1 AND i.url IS NOT NULL AND i.url != ''
            """
        ).fetchone()[0]
    assert expected_cards == 2
    assert listing.text.count('class="wechat-card-origin"') == expected_cards
    assert "https://mp.weixin.qq.com/s/newer" in listing.text
    assert "https://mp.weixin.qq.com/s/older" in listing.text
    # 保留：列表卡片既有 source-link 仍在
    assert 'class="source-link" href="https://mp.weixin.qq.com/s/newer"' in listing.text


def test_wechat_pages_thread_search_query_through_preload_detail_and_404(tmp_path: Path) -> None:
    db_path = _seed_wechat_search_db(tmp_path)
    client = TestClient(create_app(db_path))

    listing = client.get("/wechat?q=具身智能&limit=50")
    assert listing.status_code == 200
    preload = _preload_from_html(listing.text)
    assert preload["total"] == 1
    assert [item["slug"] for item in preload["items"]] == ["title-slug"]
    assert "title-slug" in listing.text
    assert "abstract-slug" not in listing.text

    detail = client.get("/wechat/title-slug?q=foo&page=2")
    assert detail.status_code == 200
    assert 'href="/wechat?q=foo&amp;page=2"' in detail.text

    missing = client.get("/wechat/missing-slug?q=foo&page=2")
    assert missing.status_code == 404
    assert 'href="/wechat?q=foo&amp;page=2"' in missing.text


def test_wechat_page_adds_only_search_filter_controls(tmp_path: Path) -> None:
    db_path = _seed_wechat_search_db(tmp_path)
    client = TestClient(create_app(db_path))

    response = client.get("/wechat")
    assert response.status_code == 200
    form = _feed_filter_form(response.text)
    assert re.findall(r'\bname="([^"]+)"', form) == ["q"]
    assert 'id="search"' in form
    assert 'type="search"' in form
    assert 'placeholder="搜索标题/公众号/摘要/标签…"' in form
    assert "value=" not in form
    assert 'name="category"' not in form
    assert 'name="channel"' not in form
    assert 'class="seg-item' not in response.text


def test_wechat_page_default_limit_matches_fast_initial_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, int] = {}

    def fake_list_wechat_items(
        conn: sqlite3.Connection,
        *,
        q: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        captured["limit"] = limit
        return {"items": [], "total": 0, "page": page, "limit": limit}

    monkeypatch.setattr("airadar.web.app.wechat_routes.list_wechat_items", fake_list_wechat_items)
    client = TestClient(create_app(tmp_path / "radar.db"))

    response = client.get("/wechat")

    assert response.status_code == 200
    assert captured["limit"] == 50


def test_wechat_sidebar_link_and_curated_alias_exist_on_public_pages(tmp_path: Path) -> None:
    db_path = _seed_wechat_db(tmp_path)
    client = TestClient(create_app(db_path))

    for path in ["/", "/all", "/daily", "/about", "/wechat"]:
        response = client.get(path)
        assert response.status_code == 200
        assert 'href="/wechat"' in response.text
        assert "微信文章解读" in response.text

    curated = client.get("/curated")
    assert curated.status_code == 200
    assert str(curated.url).endswith("/curated")
    assert 'href="/wechat"' in curated.text


def test_interpret_runner_saves_worth_reading_result_and_patches_kb_meta(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "test-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    (batch_dir / "test-slug_meta.json").write_text(
        json.dumps({"slug": "test-slug", "url": "https://wrong.example", "source": "wrong"}, ensure_ascii=False),
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    saved_meta: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append([str(part) for part in cmd])
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}, indent=2), stderr="")
        if "summarize.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "test-slug",
                            "save_decision": True,
                            "save_reason": "有实践价值",
                            "recommendation": "值得一看",
                            "tags": ["Agent", "工程化"],
                            "model": "fake-model",
                            "summary_md": "stdout should not be used",
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "--save-from-batch" in cmd:
            saved_meta.update(json.loads(cmd[cmd.index("--meta-json") + 1]))
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"ok": True, "summary_file_path": "/kb/test-slug_output.md"}),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "test-slug"
    assert row["save_decision"] == 1
    assert row["kb_synced"] == 1
    assert row["summary_md"] == SUMMARY_MD
    assert row["abstract"] == "这是一篇关于 agent 工程实践的文章，摘要可直接放在卡片上。"
    assert json.loads(row["tags_json"]) == ["Agent", "工程化"]
    assert saved_meta["url"] == "https://mp.weixin.qq.com/s/test"
    assert saved_meta["source"] == "机器之心"
    assert saved_meta["publish_date"] == "2026-06-02T10:00:00Z"
    assert any("--save-from-batch" in call for call in calls)


def test_interpret_runner_uses_ai_radar_model_and_records_llm_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    usage_db_path = tmp_path / "llm_usage.db"
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db_path))
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "usage-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    (batch_dir / "usage-slug_meta.json").write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append([str(part) for part in cmd])
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}, indent=2), stderr="")
        if "summarize.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "usage-slug",
                            "save_decision": True,
                            "save_reason": "有实践价值",
                            "recommendation": "值得一看",
                            "tags": ["Agent", "工程化"],
                            "model": "ark-actual-model",
                            "llm_metadata": {
                                "requested_model": "ai-radar-interpret-deepseek",
                                "backend_attempted": "deepseek-ark-first",
                                "backend_used": "openai-api",
                                "provider": "ark",
                                "backend_model": "ark-actual-model",
                                "fallback_used": False,
                                "input_char_count": 4321,
                                "usage": {
                                    "prompt_tokens": 100,
                                    "completion_tokens": 20,
                                    "total_tokens": 120,
                                    "input_tokens": 100,
                                    "output_tokens": 20,
                                    "prompt_cache_hit_tokens": 40,
                                },
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "--save-from-batch" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"ok": True, "summary_file_path": "/kb/usage-slug_output.md"}),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")

    summarize_call = next(call for call in calls if "summarize.sh" in call[0])
    with sqlite3.connect(usage_db_path) as conn:
        usage_row = conn.execute(
            """
            SELECT stage, provider, model, item_id, input_tokens, output_tokens,
                   total_tokens, input_item_count, input_char_count, cost_usd,
                   attribution_json
            FROM llm_usage
            """
        ).fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert summarize_call[summarize_call.index("--model") + 1] == "ai-radar-interpret-deepseek"
    assert usage_row[:10] == ("interpret", "ark", "ark-actual-model", "item-1", 100, 20, 120, 1, 4321, None)
    attribution = json.loads(usage_row[10])
    assert attribution["requested_model"] == "ai-radar-interpret-deepseek"
    assert attribution["backend_attempted"] == "deepseek-ark-first"
    assert attribution["cached_input_tokens"] == 40


def test_interpret_preserves_paid_summary_when_metering_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from airadar.interpret import runner

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    usage_db_path = tmp_path / "llm_usage.db"
    migrate_usage_db(usage_db_path=usage_db_path, main_db_path=db_path)
    with sqlite3.connect(usage_db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_usage BEFORE INSERT ON llm_usage
            BEGIN SELECT RAISE(ABORT, 'injected metering failure'); END
            """
        )

    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db_path))
    assistant_root = _assistant_root(tmp_path)
    summarize_calls = 0

    def fake_summarize_item(**kwargs: Any) -> dict[str, Any]:
        nonlocal summarize_calls
        summarize_calls += 1
        assert kwargs["row"]["id"] == "item-1"
        return {
            "slug": "paid-summary",
            "recommendation": "值得一看",
            "save_decision": True,
            "save_reason": "已经付费生成",
            "tags": ["Agent"],
            "summary_md": SUMMARY_MD,
            "model": "paid-summary-model",
            "kb_synced": False,
            "llm_metadata": {
                "provider": "ark",
                "backend_model": "paid-summary-model",
                "input_char_count": 4321,
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        }

    monkeypatch.setattr(runner, "_summarize_item", fake_summarize_item)

    with caplog.at_level(logging.ERROR, logger="airadar.llm_usage"):
        with _connect(db_path) as conn:
            summary = runner.run_interpret(
                conn,
                backfill=True,
                assistant_root=assistant_root,
                tmp_root=tmp_path / "tmp",
            )
            saved = conn.execute(
                "SELECT summary_md, error FROM wechat_interpretations WHERE item_id = 'item-1'"
            ).fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert summarize_calls == 1
    assert tuple(saved) == (SUMMARY_MD, None)
    assert caplog.messages == [
        "llm_usage_metering_failure stage=interpret provider=ark "
        "model=paid-summary-model item_id=item-1 error=IntegrityError:injected metering failure"
    ]


def test_interpret_runner_skips_kb_for_not_worth_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "skip-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    save_calls = 0

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal save_calls
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}, indent=2), stderr="")
        if "summarize.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "skip-slug",
                            "save_decision": False,
                            "save_reason": "信息密度低",
                            "recommendation": "可跳过",
                            "tags": ["低价值"],
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "--save-from-batch" in cmd:
            save_calls += 1
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["save_decision"] == 0
    assert row["kb_synced"] == 0
    assert row["error"] is None
    assert save_calls == 0


def test_interpret_runner_reuses_kb_check_url_hit_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    kb_summary = tmp_path / "kb-existing_output.md"
    kb_summary.write_text(SUMMARY_MD, encoding="utf-8")
    index_path = assistant_root / "data" / "summary_agent" / "default" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            [
                {
                    "title": "测试文章",
                    "output": {"summary_file_path": str(kb_summary)},
                    "metadata": {
                        "url": "https://mp.weixin.qq.com/s/test",
                        "tags": ["Agent"],
                        "model_name": "existing-model",
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    summarize_calls = 0

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal summarize_calls
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "found": True,
                        "slug": "kb-existing",
                        "summary_file_path": str(kb_summary),
                        "recommendation": "必读",
                        "save_reason": "已在 KB",
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "summarize.sh" in str(cmd[0]):
            summarize_calls += 1
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "kb-existing"
    assert row["save_decision"] == 1
    assert row["kb_synced"] == 1
    assert json.loads(row["tags_json"]) == ["Agent"]
    assert row["model"] == "existing-model"
    assert summarize_calls == 0


def test_interpret_runner_falls_back_when_kb_summary_file_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "fallback-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    summarize_calls = 0

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal summarize_calls
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {"found": True, "slug": "missing", "summary_file_path": str(tmp_path / "missing.md")},
                    indent=2,
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "summarize.sh" in str(cmd[0]):
            summarize_calls += 1
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "fallback-slug",
                            "save_decision": False,
                            "save_reason": "fallback complete",
                            "recommendation": "可跳过",
                            "tags": ["Fallback"],
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "fallback-slug"
    assert row["save_decision"] == 0
    assert summarize_calls == 1


def test_interpret_runner_retries_duplicate_kb_slug_with_unique_slug(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "existing-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    (batch_dir / "existing-slug_article.md").write_text("# 测试文章\n\n正文", encoding="utf-8")
    (batch_dir / "existing-slug_meta.json").write_text("{}", encoding="utf-8")
    kb_summary_rel = Path("data/summary_agent/default/article_summaries/existing-slug_output.md")
    kb_summary = assistant_root / kb_summary_rel
    kb_summary.parent.mkdir(parents=True)
    kb_summary.write_text(SUMMARY_MD, encoding="utf-8")
    index_path = assistant_root / "data" / "summary_agent" / "default" / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            [
                {
                    "title": "测试文章",
                    "output": {"summary_file_path": str(kb_summary_rel)},
                    "metadata": {"tags": ["Agent"], "model_name": "existing-model"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--check-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}, indent=2), stderr="")
        if "summarize.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "batch_dir": str(batch_dir),
                        "result": {
                            "slug": "existing-slug",
                            "save_decision": True,
                            "save_reason": "有实践价值",
                            "recommendation": "值得一看",
                            "tags": ["Agent"],
                            "model": "fresh-model",
                        },
                    },
                    ensure_ascii=False,
                ),
                stderr="",
            )
        if "--save-from-batch" in cmd:
            save_slug = cmd[cmd.index("--save-from-batch") + 1]
            if save_slug == "existing-slug":
                raise subprocess.CalledProcessError(
                    returncode=1,
                    cmd=cmd,
                    stderr="Error: Slug 'existing-slug' already exists in index.json\n",
                )
            saved_meta = json.loads(cmd[cmd.index("--meta-json") + 1])
            assert save_slug == "existing-slug_radar_item-1"
            assert saved_meta["slug"] == save_slug
            assert saved_meta["url"] == "https://mp.weixin.qq.com/s/test"
            assert (batch_dir / f"{save_slug}_summary.md").exists()
            assert (batch_dir / f"{save_slug}_article.md").exists()
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"ok": True, "summary_file_path": f"/kb/{save_slug}_output.md"}),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["slug"] == "existing-slug-radar-item-1"
    assert row["save_decision"] == 1
    assert row["kb_synced"] == 1
    assert row["error"] is None
    assert row["model"] == "fresh-model"
    assert json.loads(row["tags_json"]) == ["Agent"]


def test_interpret_subprocess_does_not_inherit_radar_virtualenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import _run_json

    seen_env: dict[str, str] = {}
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen_env.update(kwargs["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    payload = _run_json(["/fake/run.sh", "--check-url", "https://example.test"], cwd=tmp_path)

    assert payload == {"ok": True}
    assert "VIRTUAL_ENV" not in seen_env


def test_interpret_runner_records_errors_without_aborting_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 0
    assert summary.errors == 1
    assert row["save_decision"] == 0
    assert row["kb_synced"] == 0
    assert "boom" in row["error"]


def test_interpret_runner_default_disabled_skips_without_resolving_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import airadar.interpret.runner as runner

    monkeypatch.delenv("AI_RADAR_ENABLE_INTERPRET", raising=False)
    monkeypatch.delenv("AI_ASSISTANT_ROOT", raising=False)

    def fail_assistant_root(value: str | Path | None) -> Path:
        raise AssertionError(f"_assistant_root should not be called while disabled: {value}")

    monkeypatch.setattr(runner, "_assistant_root", fail_assistant_root)
    db_path = _seed_runner_db(tmp_path)
    with _connect(db_path) as conn:
        summary = runner.run_interpret(conn, backfill=True, tmp_root=tmp_path / "tmp")

    assert summary.skipped is True
    assert summary.processed == 0
    assert summary.message == "interpret disabled (set AI_RADAR_ENABLE_INTERPRET=true)"


def test_interpret_runner_enabled_without_assistant_root_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    monkeypatch.delenv("AI_ASSISTANT_ROOT", raising=False)

    db_path = _seed_runner_db(tmp_path)
    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, tmp_root=tmp_path / "tmp")

    assert summary.skipped is True
    assert summary.processed == 0
    assert summary.message == "interpret enabled but AI_ASSISTANT_ROOT is not set"


def test_interpret_runner_enabled_with_valid_root_uses_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import airadar.interpret.runner as runner

    _enable_interpret(monkeypatch)
    assistant_root = _assistant_root(tmp_path)
    seen_root: list[Path] = []

    def fake_preflight(root: Path) -> tuple[bool, str]:
        seen_root.append(root)
        return False, "sentinel preflight skip"

    monkeypatch.setattr(runner, "_preflight", fake_preflight)

    db_path = _seed_runner_db(tmp_path)
    with _connect(db_path) as conn:
        summary = runner.run_interpret(conn, backfill=True, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")

    assert seen_root == [assistant_root.resolve()]
    assert summary.skipped is True
    assert summary.message == "sentinel preflight skip"


def test_interpret_runner_preflight_skip_for_missing_assistant_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    with _connect(db_path) as conn:
        summary = run_interpret(conn, backfill=True, assistant_root=tmp_path / "missing", tmp_root=tmp_path / "tmp")

    assert summary.skipped is True
    assert summary.processed == 0
    assert "skip" in summary.message.lower()


def test_interpret_cli_exists() -> None:
    from airadar.cli import build_parser

    args = build_parser().parse_args(["interpret", "--backfill"])

    assert args.command == "interpret"
    assert args.backfill is True


def test_pipeline_runs_interpret_after_curate() -> None:
    pipeline = Path("pipeline.sh").read_text(encoding="utf-8")

    assert "run_stage interpret" in pipeline
    assert pipeline.index("run_stage curate") < pipeline.index("run_stage interpret")


def _insert_errored_interpretation(
    db_path: Path,
    *,
    item_id: str = "item-1",
    processed_at: str = "2026-06-02T10:05:00Z",
    retry_count: int = 0,
) -> None:
    conn = _connect(db_path)
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, save_decision, tags_json, summary_md,
          kb_synced, processed_at, error, error_retry_count
        )
        VALUES (?, ?, 0, '[]', '', 0, ?, 'Error: endpoint down', ?)
        """,
        (item_id, f"error-{item_id}", processed_at, retry_count),
    )
    conn.commit()
    conn.close()


def _fake_skip_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    if "--check-url" in cmd:
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"found": False}), stderr="")
    batch_dir = Path(str(kwargs.get("cwd"))) / "batch"
    batch_dir.mkdir(exist_ok=True)
    (batch_dir / "retry-slug_summary.md").write_text(SUMMARY_MD, encoding="utf-8")
    return subprocess.CompletedProcess(
        cmd,
        0,
        stdout=json.dumps(
            {
                "ok": True,
                "batch_dir": str(batch_dir),
                "result": {"slug": "retry-slug", "save_decision": False, "recommendation": "可跳过"},
            },
            ensure_ascii=False,
        ),
        stderr="",
    )


def test_interpret_runner_retries_errored_row_after_backoff_and_resets_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    _insert_errored_interpretation(db_path, processed_at="2026-06-02T10:05:00Z", retry_count=3)

    monkeypatch.setattr(subprocess, "run", _fake_skip_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.processed == 1
    assert summary.errors == 0
    assert row["error"] is None
    assert row["error_retry_count"] == 0
    assert row["slug"] == "retry-slug"


def test_interpret_runner_error_retry_respects_backoff_window_and_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    assistant_root = _assistant_root(tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_skip_run)
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    # Inside the backoff window: retry_count=3 needs >= 2 hours since processed_at.
    db_recent = _seed_runner_db(tmp_path / "recent")
    _insert_errored_interpretation(db_recent, processed_at=now, retry_count=3)
    with _connect(db_recent) as conn:
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
    assert summary.processed == 0
    assert summary.errors == 0

    # At the retry cap: never retried again, even long after.
    db_capped = _seed_runner_db(tmp_path / "capped")
    _insert_errored_interpretation(db_capped, processed_at="2026-06-02T10:05:00Z", retry_count=8)
    with _connect(db_capped) as conn:
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
    assert summary.processed == 0
    assert summary.errors == 0


def test_interpret_runner_increments_retry_counter_on_repeated_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    _insert_errored_interpretation(db_path, processed_at="2026-06-02T10:05:00Z", retry_count=2)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, stderr="still down")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert summary.errors == 1
    assert "still down" in row["error"]
    assert row["error_retry_count"] == 3


def test_copy_batch_files_skips_copy_when_slugs_resolve_to_same_file(tmp_path: Path) -> None:
    from airadar.interpret.runner import _copy_batch_files_for_slug

    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    for kind in ("article", "summary"):
        (batch_dir / f"AI文章_{kind}.md").write_text(f"{kind} body", encoding="utf-8")

    # On a case-insensitive filesystem (macOS default) "ai文章" opens the same
    # file as "AI文章"; the copy must be skipped instead of raising SameFileError.
    _copy_batch_files_for_slug(batch_dir, "AI文章", "ai文章")

    assert (batch_dir / "ai文章_article.md").read_text(encoding="utf-8") == "article body"
    assert (batch_dir / "ai文章_summary.md").read_text(encoding="utf-8") == "summary body"


def test_interpret_runner_first_failure_starts_retry_counter_at_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT * FROM wechat_interpretations WHERE item_id='item-1'").fetchone()

    assert row["error_retry_count"] == 0


def test_interpret_runner_empty_error_message_still_advances_retry_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise ValueError()

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        first = conn.execute(
            "SELECT error, error_retry_count FROM wechat_interpretations WHERE item_id='item-1'"
        ).fetchone()
        # Age the row past the first backoff window, then fail again.
        conn.execute(
            "UPDATE wechat_interpretations SET processed_at='2026-06-02T10:05:00Z' WHERE item_id='item-1'"
        )
        conn.commit()
        run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        second = conn.execute(
            "SELECT error_retry_count FROM wechat_interpretations WHERE item_id='item-1'"
        ).fetchone()

    assert first["error"] == ""
    assert first["error_retry_count"] == 0
    assert second["error_retry_count"] == 1


def test_interpret_runner_backoff_window_boundary_for_retry_count_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    from airadar.interpret.runner import run_interpret

    _enable_interpret(monkeypatch)
    assistant_root = _assistant_root(tmp_path)
    monkeypatch.setattr(subprocess, "run", _fake_skip_run)

    def _iso(minutes_ago: int) -> str:
        stamp = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=minutes_ago)
        return stamp.isoformat().replace("+00:00", "Z")

    # retry_count=3 needs >= 15 * 2**3 = 120 minutes since processed_at.
    db_inside = _seed_runner_db(tmp_path / "inside")
    _insert_errored_interpretation(db_inside, processed_at=_iso(118), retry_count=3)
    with _connect(db_inside) as conn:
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
    assert summary.processed == 0

    db_past = _seed_runner_db(tmp_path / "past")
    _insert_errored_interpretation(db_past, processed_at=_iso(122), retry_count=3)
    with _connect(db_past) as conn:
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
    assert summary.processed == 1


def test_interpret_runner_eighth_retry_failure_reaches_cap_and_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from airadar.interpret.runner import ERROR_RETRY_MAX, run_interpret

    _enable_interpret(monkeypatch)
    db_path = _seed_runner_db(tmp_path)
    assistant_root = _assistant_root(tmp_path)
    _insert_errored_interpretation(db_path, processed_at="2026-06-02T10:05:00Z", retry_count=ERROR_RETRY_MAX - 1)

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd, stderr="still down")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with _connect(db_path) as conn:
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")
        row = conn.execute("SELECT error_retry_count FROM wechat_interpretations WHERE item_id='item-1'").fetchone()
        assert summary.errors == 1
        assert row["error_retry_count"] == ERROR_RETRY_MAX

        # Age the row far past every window: the cap must keep it out for good.
        conn.execute(
            "UPDATE wechat_interpretations SET processed_at='2026-01-01T00:00:00Z' WHERE item_id='item-1'"
        )
        conn.commit()
        summary = run_interpret(conn, assistant_root=assistant_root, tmp_root=tmp_path / "tmp")

    assert summary.processed == 0
    assert summary.errors == 0
