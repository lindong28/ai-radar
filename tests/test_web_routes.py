from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.enrich.schema import EnrichOutput
from airadar.presentation import summary as presentation_summary
from airadar.presentation.media import proxy_image_url
from airadar.web.app import (
    SHANGHAI_TZ,
    WECHAT_FALLBACK_ICON,
    _mobile_date_label,
    _mobile_date_parts,
    _prepaint_items,
    create_app,
)
from airadar.web.routes import request_db, search
from airadar.web.routes import timeline as timeline_routes


def _extract_preload(html: str) -> dict[str, Any]:
    match = re.search(r'<script id="__PRELOAD__" type="application/json">\s*(.*?)\s*</script>', html, re.S)
    assert match, "SSR preload script not found"
    return json.loads(match.group(1))


def _seed_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'openai_blog', 'OpenAI Blog', 'https://openai.com/blog/rss.xml', 'T1', 1,
          'feed', 'https://openai.com/', 'https://example.com/openai.ico', '{}', '2026-05-08T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'simonw_mastodon', 'Simon Willison (Mastodon)', 'https://fedi.simonwillison.net/@simon.rss', 'T1.5', 1,
          'x', 'https://fedi.simonwillison.net/@simon', 'https://example.com/simon.ico', '{}', '2026-05-08T00:00:00Z'
        )
        """
    )
    items = [
        (
            "item-openai",
            "openai_blog",
            "https://example.com/api-release",
            "OpenAI API Release",
            "Ada",
            "2026-05-08T10:00:00Z",
            "2026-05-08T10:02:00Z",
            "OpenAI released a practical API update for developers.",
            '<p>OpenAI released a practical API update.</p><img src="https://nitter.net/pic/media%2Fopenai.jpg"><img src="javascript:alert(1)">',
            "h-openai",
        ),
        (
            "item-claude",
            "openai_blog",
            "https://example.com/claude",
            "Claude Notes",
            "Ben",
            "2026-05-08T09:00:00Z",
            "2026-05-08T09:02:00Z",
            "Anthropic published engineering notes for model users.",
            None,
            "h-claude",
        ),
        (
            "item-x",
            "simonw_mastodon",
            "https://fedi.simonwillison.net/@simon/1",
            "Mastodon post",
            "Simon",
            "2026-05-08T08:00:00Z",
            "2026-05-08T08:02:00Z",
            "Long social post about SQLite FTS5 trigram search and developer tooling. "
            "Related discussion for https://example.com/api-release",
            None,
            "h-x",
        ),
    ]
    conn.executemany(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
        """,
        items,
    )
    numeric = {
        "relevance": 9,
        "density": 8,
        "recency": 7,
        "authority": 10,
        "engineering": 8,
        "reasoning": "OpenAI release is useful for API builders.",
    }
    curated_reason = {
        "scores": {
            **numeric,
            "reasoning": "精选推荐理由：OpenAI API 更新对开发者有直接价值，适合优先阅读。",
        }
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-openai', 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T10:03:00Z', NULL)
        """,
        (json.dumps(numeric),),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-1', 'test.r1', '{}', 6.5, '[1]', '["item-openai"]', '2026-05-08T10:04:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-1', 'item-openai', 8.2, 1, ?)
        """,
        (json.dumps(curated_reason),),
    )
    conn.commit()
    conn.close()
    return db_path


def _insert_enrichment(conn: sqlite3.Connection, item_id: str, title: str, tags: list[str]) -> None:
    payload = {
        "title_zh": title,
        "summary_zh": "这是一段用于测试分类筛选的中文摘要，长度足够满足结构化校验要求。",
        "why_recommend": "这是一段用于测试分类筛选的中文推荐理由。",
        "tags": tags,
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, 'enrich', 'test.r1', 'fake', '{}', ?, '{}', 1, 0, '2026-05-08T10:07:00Z', NULL)
        """,
        (item_id, json.dumps(payload)),
    )


def _insert_source(conn: sqlite3.Connection, source_id: str, name: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (?, ?, ?, 'T1', 1, 'feed', ?, 'https://example.com/source.ico', '{}', '2026-05-08T00:00:00Z')
        """,
        (source_id, name, f"https://example.com/{source_id}.xml", f"https://example.com/{source_id}/"),
    )


def _insert_source_with_kind(conn: sqlite3.Connection, source_id: str, name: str, kind: str) -> None:
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (?, ?, ?, 'T1', 1, ?, ?, 'https://example.com/source.ico', '{}', '2026-05-08T00:00:00Z')
        """,
        (source_id, name, f"https://example.com/{source_id}.xml", kind, f"https://example.com/{source_id}/"),
    )


def _insert_item(
    conn: sqlite3.Connection,
    item_id: str,
    source_id: str,
    title: str,
    author: str,
    content_text: str,
    published_at: str = "2026-05-08T07:30:00Z",
) -> None:
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, '{}')
        """,
        (
            item_id,
            source_id,
            f"https://example.com/{item_id}",
            title,
            author,
            published_at,
            published_at,
            content_text,
            f"h-{item_id}",
        ),
    )


def _seed_source_search_ranking_db(tmp_path: Path, *, curated: bool = False) -> Path:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    _insert_source_with_kind(conn, "x_shared", "Shared Lab", "x")
    _insert_source_with_kind(conn, "wx_shared", "Shared Lab WeChat", "wechat")
    _insert_source_with_kind(conn, "other_feed", "Other Feed", "feed")
    rows = [
        (
            "item-content-newer",
            "other_feed",
            "Body-only update",
            "Other",
            "Shared Lab appears only in this body text.",
            "2026-05-08T10:30:00Z",
        ),
        (
            "item-x-1",
            "x_shared",
            "Source match one",
            "Reporter",
            "This text does not need the query token.",
            "2026-05-08T10:20:00Z",
        ),
        (
            "item-x-2",
            "x_shared",
            "Source match two",
            "Reporter",
            "This text does not need the query token.",
            "2026-05-08T10:10:00Z",
        ),
        (
            "item-x-3",
            "x_shared",
            "Source match three",
            "Reporter",
            "This text does not need the query token.",
            "2026-05-08T10:00:00Z",
        ),
        (
            "item-wx-1",
            "wx_shared",
            "WeChat source match",
            "Editor",
            "This text does not need the query token.",
            "2026-05-08T09:00:00Z",
        ),
    ]
    for item_id, source_id, title, author, content_text, published_at in rows:
        _insert_item(conn, item_id, source_id, title, author, content_text, published_at)
    if curated:
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
            )
            VALUES ('run-1', 'test.r1', '{}', 6.5, '[]', '[]', '2026-05-08T11:00:00Z')
            """
        )
        source_names = {
            "x_shared": "Shared Lab",
            "wx_shared": "Shared Lab WeChat",
            "other_feed": "Other Feed",
        }
        for rank, (item_id, source_id, title, author, _content_text, published_at) in enumerate(rows, start=1):
            summary = json.dumps(
                {
                    "id": item_id,
                    "source_id": source_id,
                    "source_name": source_names[source_id],
                    "source_kind": "feed",
                    "title": title,
                    "author": author,
                    "published_at": published_at,
                    "fetched_at": published_at,
                    "topic_tags": [],
                    "weighted_score": 8.0,
                    "rank": rank,
                    "scores": {},
                }
            )
            conn.execute(
                """
                INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json, summary_json)
                VALUES ('run-1', ?, 8.0, ?, '{}', ?)
                """,
                (item_id, rank, summary),
            )
    conn.commit()
    conn.close()
    return db_path


def _curated_summary(item_id: str, source_id: str, source_name: str, title: str, author: str) -> str:
    return json.dumps(
        {
            "id": item_id,
            "source_id": source_id,
            "source_name": source_name,
            "source_kind": "feed",
            "title": title,
            "author": author,
            "published_at": "2026-05-08T07:30:00Z",
            "fetched_at": "2026-05-08T07:30:00Z",
            "topic_tags": [],
            "weighted_score": 8.0,
            "rank": 2,
            "scores": {},
        }
    )


def _seed_db_with_model_enrichment(tmp_path: Path) -> Path:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "OpenAI API 模型发布", ["模型发布", "OpenAI"])
    conn.commit()
    conn.close()
    return db_path


def _seed_large_curated_archive_db(tmp_path: Path, item_count: int = 45) -> Path:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    for idx in range(2, item_count + 1):
        item_id = f"item-archive-{idx:02d}"
        published_at = f"2026-05-07T{23 - (idx % 20):02d}:{idx % 60:02d}:00Z"
        _insert_item(
            conn,
            item_id,
            "openai_blog",
            f"Archive item {idx}",
            "Ada",
            f"Archive body {idx}",
            published_at,
        )
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-1', ?, 7.5, ?, '{}')
            """,
            (item_id, idx),
        )
    conn.commit()
    conn.close()
    return db_path


def test_perf_migration_adds_timeline_indexes(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)

    index_names = {row[1] for row in conn.execute("PRAGMA index_list('items')").fetchall()}
    evaluation_index_names = {row[1] for row in conn.execute("PRAGMA index_list('item_evaluations')").fetchall()}

    assert "idx_items_source_url_norm" in index_names
    assert "idx_items_published_fetched_id" in index_names
    assert "idx_evaluations_stage_error_item_id" in evaluation_index_names


def test_item_summary_uses_preloaded_enrichment_without_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT i.*, s.name AS source_name, s.tier,
               s.kind AS source_kind,
               s.homepage_url AS source_homepage_url,
               s.icon_url AS source_icon_url
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE i.id='item-openai'
        """
    ).fetchone()
    enrichment = EnrichOutput(
        title_zh="OpenAI API 产品更新",
        summary_zh="这是一段用于验证预加载 enrichment 路径的中文摘要，长度足够满足结构化校验要求。",
        why_recommend="这是一段用于验证预加载 enrichment 路径的中文推荐理由。",
        tags=["产品更新", "MCP/工具"],
    )

    def fail_latest_lookup(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preloaded enrichment should skip latest_enrichment lookup")

    monkeypatch.setattr(presentation_summary, "latest_enrichment", fail_latest_lookup)

    item = presentation_summary.item_summary(row, conn=conn, include_related=False, enrichment=enrichment)

    assert item["title_zh"] == "OpenAI API 产品更新"
    assert item["summary_zh"] == enrichment.summary_zh
    assert item["why_recommend"] == enrichment.why_recommend
    assert "产品更新" in item["topic_tags"]
    assert item["related_discussions"] == []


def test_item_summary_suppresses_wechat_preview_and_full_text(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE sources SET kind='wechat' WHERE id='openai_blog'")
    conn.commit()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT i.*, s.name AS source_name, s.tier,
               s.kind AS source_kind,
               s.homepage_url AS source_homepage_url,
               s.icon_url AS source_icon_url
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE i.id='item-openai'
        """
    ).fetchone()
    enrichment = EnrichOutput(
        title_zh="OpenAI API 产品更新",
        summary_zh="这是一段用于验证微信公众号摘要仍然可以展示的中文摘要。",
        why_recommend="这是一段用于验证微信公众号推荐理由仍然可以展示的中文推荐理由。",
        tags=["产品更新", "MCP/工具"],
    )

    enriched = presentation_summary.item_summary(row, conn=conn, include_related=False, enrichment=enrichment)
    bare = presentation_summary.item_summary(row, conn=conn, include_related=False, enrichment_loaded=True)

    assert enriched["source_kind"] == "wechat"
    assert enriched["content_preview"] is None
    assert enriched["summary_zh"] == enrichment.summary_zh
    assert "content_text" not in enriched
    assert bare["content_preview"] is None
    assert bare["summary_zh"] is None
    assert "content_text" not in bare


def test_timeline_api_includes_wechat_author_avatar_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    _insert_source_with_kind(conn, "wx_mp2rss", "微信公众号（Mp2RSS 合集）", "wechat")
    _insert_item(
        conn,
        "item-wechat",
        "wx_mp2rss",
        "WeChat Article",
        "歸藏的AI工具箱",
        "RSS summary body",
    )
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES ('歸藏的AI工具箱', 'https://mmbiz.qpic.cn/guizang.png', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    item = client.get("/api/v1/timeline", params={"limit": 1}).json()["data"]["items"][0]

    assert item["source_kind"] == "wechat"
    assert item["source_name"] == "微信公众号（Mp2RSS 合集）"
    assert item["author"] == "歸藏的AI工具箱"
    # WeChat CDN blocks browser hotlinking, so the cached avatar is routed
    # through the same-origin /img proxy.
    assert item["author_avatar_url"] == proxy_image_url("https://mmbiz.qpic.cn/guizang.png")
    assert item["author_avatar_url"].startswith("/img?url=")


def test_precomputed_curated_api_hydrates_wechat_author_avatar_cache(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_source_with_kind(conn, "wx_mp2rss", "微信公众号（Mp2RSS 合集）", "wechat")
    _insert_item(
        conn,
        "item-wechat",
        "wx_mp2rss",
        "WeChat Article",
        "数字生命卡兹克",
        "RSS summary body",
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json, summary_json)
        VALUES ('run-1', 'item-wechat', 8.1, 2, '{}', ?)
        """,
        (
            _curated_summary(
                "item-wechat",
                "wx_mp2rss",
                "微信公众号（Mp2RSS 合集）",
                "WeChat Article",
                "数字生命卡兹克",
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO wechat_account_avatars (account, avatar_url, checked_at, updated_at)
        VALUES ('数字生命卡兹克', 'https://mmbiz.qpic.cn/kazike.png', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    items = client.get("/api/v1/curated").json()["data"]["items"]
    wechat = next(item for item in items if item["id"] == "item-wechat")

    assert wechat["author_avatar_url"] == proxy_image_url("https://mmbiz.qpic.cn/kazike.png")
    assert wechat["author_avatar_url"].startswith("/img?url=")


def test_prepaint_uses_wechat_author_name_and_avatar_without_rss_suffix() -> None:
    [wechat, feed] = _prepaint_items(
        [
            {
                "id": "item-wechat",
                "source_id": "wx_mp2rss",
                "source_name": "微信公众号（Mp2RSS 合集）",
                "source_kind": "wechat",
                "source_icon_url": "https://example.com/collection.png",
                "author": "歸藏的AI工具箱",
                "author_avatar_url": "https://mmbiz.qpic.cn/guizang.png",
                "url": "https://mp.weixin.qq.com/s/seed",
                "title": "WeChat Article",
                "summary_zh": "摘要",
                "published_at": "2026-06-01T00:00:00Z",
            },
            {
                "id": "item-feed",
                "source_id": "openai_blog",
                "source_name": "OpenAI Blog",
                "source_kind": "feed",
                "source_icon_url": "https://example.com/openai.png",
                "author": "Ada",
                "url": "https://example.com/post",
                "title": "Feed Article",
                "content_preview": "Preview",
                "weighted_score": 8.25,
                "media_assets": [
                    {"type": "image", "url": "https://example.com/one.png"},
                    {"type": "video", "url": "https://example.com/ignored.mp4"},
                ],
                "published_at": "2026-06-01T00:00:00Z",
            },
        ],
        timeline_page=True,
    )

    assert wechat["source_name"] == "歸藏的AI工具箱"
    assert wechat["source_icon_url"] == "https://mmbiz.qpic.cn/guizang.png"
    assert wechat["source_initial"] == "歸"
    assert "RSS" not in wechat["source_name"]
    assert feed["source_name"] == "OpenAI Blog：官网动态（RSS）"
    assert feed["source_icon_url"] == "https://example.com/openai.png"
    assert feed["source_author"] == "@Ada"
    assert feed["weekday_label"] == "星期一"
    assert feed["iso_datetime"] == "2026-06-01T00:00:00.000Z"
    assert feed["score"] == 83
    assert feed["media_assets"] == [{"type": "image", "url": "https://example.com/one.png"}]


def test_prepaint_uses_generic_wechat_icon_when_author_avatar_missing() -> None:
    [item] = _prepaint_items(
        [
            {
                "id": "item-wechat",
                "source_id": "wx_mp2rss",
                "source_name": "微信公众号（Mp2RSS 合集）",
                "source_kind": "wechat",
                "author": "数字生命卡兹克",
                "url": "https://mp.weixin.qq.com/s/seed",
                "title": "WeChat Article",
                "summary_zh": "摘要",
                "published_at": "2026-06-01T00:00:00Z",
            }
        ],
        timeline_page=True,
    )

    assert item["source_name"] == "数字生命卡兹克"
    assert item["source_icon_url"] == WECHAT_FALLBACK_ICON


def test_wechat_prepaint_uses_shanghai_day_geometry() -> None:
    from airadar.web.app import _prepaint_wechat_items

    [item] = _prepaint_wechat_items(
        [
            {
                "slug": "midnight-boundary",
                "published_at": "2026-08-02T16:30:00Z",
            }
        ]
    )

    assert item["date_bucket"] == "2026-08-03"
    assert item["date_label"] == "8月3日"
    assert item["weekday_label"] == "星期一"
    assert item["time_label"] == "00:30"
    assert item["iso_datetime"] == "2026-08-02T16:30:00.000Z"


def test_prepaint_day_count_uses_full_payload_and_all_hides_related_discussions() -> None:
    items = [
        {
            "id": f"item-{index}",
            "source_id": "openai_blog",
            "source_name": "OpenAI Blog",
            "source_kind": "feed",
            "author": "Ada",
            "published_at": f"2026-06-01T{index:02d}:00:00Z",
            "related_discussions": [{"source_id": "x", "author": "someone"}],
        }
        for index in range(13)
    ]

    all_prepaint = _prepaint_items(items, timeline_page=True)
    home_prepaint = _prepaint_items(items, timeline_page=False)

    assert len(all_prepaint) == 12
    assert {item["date_count"] for item in all_prepaint} == {13}
    assert all(item["related_discussions"] == [] for item in all_prepaint)
    assert home_prepaint[0]["related_discussions"] == [{"source_id": "x", "author": "someone"}]


def test_timeline_total_uses_real_count_and_clamps_out_of_range_page(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    page_one = client.get("/api/v1/timeline", params={"limit": 1, "page": 1}).json()["data"]
    out_of_range = client.get("/api/v1/timeline", params={"limit": 1, "page": 999}).json()["data"]
    single_page = client.get("/api/v1/timeline", params={"limit": 50, "page": 1}).json()["data"]
    empty_page = client.get("/api/v1/timeline", params={"q": "xyzzyqwertynonexistent", "limit": 50}).json()["data"]

    assert page_one["total"] == 3
    assert out_of_range["total"] == 3
    assert out_of_range["page"] == 3
    assert [item["id"] for item in out_of_range["items"]] == ["item-x"]
    assert single_page["total"] == 3
    assert empty_page["total"] == 0


def test_timeline_total_cache_invalidates_when_items_change(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    first = client.get("/api/v1/timeline", params={"limit": 50}).json()["data"]
    assert first["total"] == 3

    conn = sqlite3.connect(db_path)
    _insert_item(
        conn,
        "item-new",
        "openai_blog",
        "Newer model update",
        "Ada",
        "A new AI model update",
        "2026-05-08T11:00:00Z",
    )
    conn.commit()
    conn.close()

    second = client.get("/api/v1/timeline", params={"limit": 50}).json()["data"]
    assert second["total"] == 4
    assert second["items"][0]["id"] == "item-new"


def _seed_pagination_parity_db(tmp_path: Path) -> Path:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_item(
        conn,
        "item-product",
        "openai_blog",
        "Alpha product update",
        "Ada",
        "Alpha product details for developers.",
        "2026-05-08T07:30:00Z",
    )
    _insert_item(
        conn,
        "item-x-model",
        "simonw_mastodon",
        "Alpha model notes",
        "Simon",
        "Alpha model discussion.",
        "2026-05-08T07:00:00Z",
    )
    _insert_item(
        conn,
        "item-openai-newer",
        "openai_blog",
        "Alpha API Release updated",
        "Ada",
        "Alpha duplicate URL should replace the older item.",
        "2026-05-08T10:30:00Z",
    )
    conn.execute(
        "UPDATE items SET url='https://example.com/api-release' WHERE id='item-openai-newer'"
    )
    conn.execute("UPDATE items SET title='Alpha Claude Notes' WHERE id='item-claude'")
    conn.execute("UPDATE items SET title='Alpha SQLite discussion' WHERE id='item-x'")

    enrichments = {
        "item-openai": ("OpenAI 模型发布", ["模型发布", "OpenAI"]),
        "item-openai-newer": ("OpenAI 模型发布更新", ["模型发布", "OpenAI"]),
        "item-claude": ("Claude 研究笔记", ["论文/研究", "Anthropic"]),
        "item-x": ("SQLite 研究实践", ["论文/研究", "教程/实践"]),
        "item-product": ("Alpha 产品更新", ["产品更新", "MCP/工具"]),
        "item-x-model": ("Alpha 模型动态", ["模型发布", "开源/仓库"]),
    }
    for item_id, (title, tags) in enrichments.items():
        _insert_enrichment(conn, item_id, title, tags)

    for rank, item_id in enumerate(
        ("item-claude", "item-x", "item-product", "item-x-model", "item-openai-newer"),
        start=2,
    ):
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('run-1', ?, 8.0, ?, '{}')
            """,
            (item_id, rank),
        )

    prefilter_pass = json.dumps({"is_ai_related": True, "confidence": 0.9})
    prefilter_fail = json.dumps({"is_ai_related": False, "confidence": 0.9})
    for item_id in ("item-openai", "item-openai-newer", "item-claude", "item-x", "item-product"):
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'prefilter', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T10:08:00Z', NULL)
            """,
            (item_id, prefilter_pass),
        )
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-x-model', 'prefilter', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T10:08:00Z', NULL)
        """,
        (prefilter_fail,),
    )
    conn.commit()
    conn.close()
    return db_path


def _assert_total_matches_accumulated_pages(
    client: TestClient,
    endpoint: str,
    filters: dict[str, str],
) -> None:
    limit = 2
    first = client.get(endpoint, params={**filters, "limit": limit, "page": 1}).json()["data"]
    total = int(first["total"])
    complete = client.get(endpoint, params={**filters, "limit": 100, "page": 1}).json()["data"]
    complete_item_ids = [str(item["id"]) for item in complete["items"]]
    assert complete["page"] == 1, filters
    assert len(complete_item_ids) == len(set(complete_item_ids)), filters
    assert len(complete_item_ids) == total, filters

    total_pages = max(1, (total + limit - 1) // limit)
    pages = [
        client.get(endpoint, params={**filters, "limit": limit, "page": page}).json()["data"]
        for page in range(1, total_pages + 1)
    ]
    item_ids = [str(item["id"]) for data in pages for item in data["items"]]

    assert [data["page"] for data in pages] == list(range(1, total_pages + 1)), filters
    assert len(item_ids) == len(set(item_ids)), filters
    assert len(item_ids) == total, filters

    overflow = client.get(endpoint, params={**filters, "limit": limit, "page": 999}).json()["data"]
    assert overflow["page"] == total_pages, filters
    assert [item["id"] for item in overflow["items"]] == [item["id"] for item in pages[-1]["items"]], filters


def test_total_rows_guard_rejects_timeline_count_underrun_at_full_page_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count_items = timeline_routes._count_timeline_items_with_prefilter

    def undercount_items(
        conn: sqlite3.Connection,
        where: str,
        params: tuple[object, ...],
    ) -> int:
        return max(0, count_items(conn, where, params) - 2)

    monkeypatch.setattr(timeline_routes, "_count_timeline_items_with_prefilter", undercount_items)
    client = TestClient(create_app(_seed_pagination_parity_db(tmp_path)))

    with pytest.raises(AssertionError):
        _assert_total_matches_accumulated_pages(client, "/api/v1/timeline", {})


def test_timeline_total_matches_accumulated_rows_across_filter_and_page_combinations(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(_seed_pagination_parity_db(tmp_path)))

    for filters in (
        {},
        {"channel": "news"},
        {"channel": "x"},
        {"category": "paper"},
        {"q": "Alpha"},
        {"channel": "news", "category": "ai-models", "q": "Alpha"},
        {"channel": "x", "category": "paper", "q": "Alpha"},
        {"channel": "x", "category": "ai-products", "q": "not-present"},
    ):
        _assert_total_matches_accumulated_pages(client, "/api/v1/timeline", filters)


def test_curated_total_matches_accumulated_rows_across_filter_and_page_combinations(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(_seed_pagination_parity_db(tmp_path)))

    for filters in (
        {},
        {"category": "paper"},
        {"category": "ai-models"},
        {"q": "Alpha"},
        {"category": "ai-products", "q": "Alpha"},
        {"category": "paper", "q": "not-present"},
    ):
        _assert_total_matches_accumulated_pages(client, "/api/v1/curated", filters)


def test_timeline_exposes_default_score_for_unscored_items(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    items = client.get("/api/v1/timeline", params={"limit": 3}).json()["data"]["items"]

    unscored = next(item for item in items if item["id"] == "item-claude")
    assert unscored["weighted_score"] == 0


def test_static_clean_routes_and_curated_redirect(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    for path in ["/", "/all", "/daily", "/about"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "side-nav" in response.text

    redirect = client.get("/curated.html", follow_redirects=False)
    assert redirect.status_code == 308
    assert redirect.headers["location"] == "/"


def test_more_page_lists_only_the_approved_mobile_destinations(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/more")

    assert response.status_code == 200
    main = response.text.split('<main class="app-main more-page">', 1)[1].split("</main>", 1)[0]
    assert re.findall(r'class="more-row" href="([^"]+)"', main) == [
        "/wechat",
        "/bookmarks",
        "/about",
        "/changelog",
    ]
    assert "微信文章解读" in main
    assert "收藏" in main
    assert "关于" in main
    assert "更新日志" in main
    for excluded in ["主题", "Agent 接入", "反馈", "/hot"]:
        assert excluded not in main
    assert '<a class="m-tab m-tab-active" aria-current="page" href="/more">' in response.text


def test_mobile_date_labels_use_shanghai_today_yesterday_and_absolute_fallback() -> None:
    now = datetime(2026, 8, 3, 12, tzinfo=SHANGHAI_TZ)

    assert _mobile_date_parts(now, now) == ("今天", "8月3日 周一")
    assert _mobile_date_parts(now - timedelta(days=1), now) == ("昨天", "8月2日 周日")
    assert _mobile_date_parts(now - timedelta(days=2), now) == ("8月1日", "周六")
    assert _mobile_date_label(now, now) == "今天 8月3日 周一"
    assert _mobile_date_label(datetime(2026, 8, 2, 16, 30, tzinfo=UTC), now) == "今天 8月3日 周一"
    assert _mobile_date_label(now - timedelta(days=1), now) == "昨天 8月2日 周日"
    assert _mobile_date_label(now - timedelta(days=2), now) == "8月1日 周六"


def test_home_and_all_pages_render_ssr_preload(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    home = client.get("/")
    all_page = client.get("/all")
    curated_api = client.get("/api/v1/curated")
    timeline_api = client.get("/api/v1/timeline", params={"limit": 40})

    assert home.status_code == 200
    assert all_page.status_code == 200
    home_preload = _extract_preload(home.text)
    all_preload = _extract_preload(all_page.text)
    assert len(home_preload["items"]) >= 1
    assert len(all_preload["items"]) >= 1
    assert home_preload["count"] == len(home_preload["items"])
    assert all_preload["limit"] == 40
    assert [item["id"] for item in home_preload["items"]] == [
        item["id"] for item in curated_api.json()["data"]["items"]
    ]
    assert [item["id"] for item in all_preload["items"]] == [
        item["id"] for item in timeline_api.json()["data"]["items"]
    ]
    for html in [home.text, all_page.text]:
        assert 'class="timeline-day date-group"' in html
        assert 'class="timeline-day-head timeline-date"' in html
        assert 'class="timeline-day-meta">星期' in html
        assert 'class="timeline-item timeline-entry"' in html
        assert 'class="timeline-rail"' in html
        assert 'class="timeline-dot"' in html
        assert 'class="timeline-score ' in html
        article_pos = html.index('<article class="item-row timeline-card')
        time_pos = html.index('class="timeline-time"')
        rail_pos = html.index('class="timeline-rail"')
        preload_pos = html.index('id="__PRELOAD__"')
        module_preload_pos = html.index('rel="modulepreload"')
        module_pos = html.index('type="module"')
        assert module_preload_pos < preload_pos
        assert time_pos < rail_pos < article_pos < preload_pos
        assert preload_pos < module_pos

    assert 'class="tag">' not in home.text
    # The "#" prefix moved from DOM text to CSS (`.tag::before{content:"#"}`,
    # GAP-70) so /all tag text stays equal to the input data. The homepage no
    # longer renders tags, matching AIHOT's page-specific contract (GAP-84).
    assert 'class="tag">' in all_page.text
    assert 'class="tag">#' not in all_page.text

    assert 'class="source-line"' in home.text
    assert 'class="timeline-selected-badge">精选</span>' in home.text


def test_home_page_ssr_preload_is_curated_page_aware(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_large_curated_archive_db(tmp_path)))

    response = client.get("/?page=2")
    api_response = client.get("/api/v1/curated", params={"page": 2})

    assert response.status_code == 200
    assert api_response.status_code == 200
    preload = _extract_preload(response.text)
    api_data = api_response.json()["data"]
    assert preload["page"] == 2
    assert preload["limit"] == 40
    assert preload["total"] == api_data["total"] > 40
    assert [item["id"] for item in preload["items"]] == [item["id"] for item in api_data["items"]]
    assert 'id="pagination"' not in response.text


@pytest.mark.parametrize(
    ("page_url", "api_url"),
    [
        ("/?q=OpenAI", "/api/v1/curated?q=OpenAI"),
        ("/?category=ai-models", "/api/v1/curated?category=ai-models"),
        ("/all?channel=news", "/api/v1/timeline?channel=news"),
    ],
)
def test_deep_links_render_matching_ssr_preload(tmp_path: Path, page_url: str, api_url: str) -> None:
    client = TestClient(create_app(_seed_db_with_model_enrichment(tmp_path)))

    response = client.get(page_url)
    api_response = client.get(api_url)

    assert response.status_code == 200
    assert api_response.status_code == 200
    preload = _extract_preload(response.text)
    api_data = api_response.json()["data"]
    assert len(preload["items"]) >= 1
    assert [item["id"] for item in preload["items"]] == [item["id"] for item in api_data["items"]]


def test_fts_backfill_keeps_one_row_per_item(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    migrate(db_path)
    migrate(db_path)
    conn = sqlite3.connect(db_path)

    items_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    fts_count = conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
    fts_columns = [row[1] for row in conn.execute("PRAGMA table_info('items_fts')").fetchall()]

    assert fts_count == items_count
    assert fts_columns == ["item_id", "title", "content_text", "source_name", "author", "title_zh"]


def test_fts_triggers_sync_source_author_and_title_zh(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    _insert_source(conn, "guizang_feed", "歸藏社")
    _insert_item(
        conn,
        "item-guizang",
        "guizang_feed",
        "Short field update",
        "元亨",
        "English body intentionally avoids the short Chinese source token.",
    )
    _insert_enrichment(conn, "item-guizang", "歸藏模型快讯", ["模型发布"])
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (
          'item-guizang', 'enrich', 'test.r1', 'fake', '{}',
          '{"title_zh":"失败标题","summary_zh":"失败摘要"}', '{}', 1, 0,
          '2026-05-08T10:08:00Z', 'provider failed'
        )
        """
    )
    conn.execute("UPDATE items SET title='Short field update v2', author='青衫' WHERE id='item-guizang'")
    conn.execute("UPDATE sources SET name='歸藏实验室' WHERE id='guizang_feed'")
    row = conn.execute(
        """
        SELECT source_name, author, title, title_zh
        FROM items_fts
        WHERE item_id='item-guizang'
        """
    ).fetchone()

    assert dict(row) == {
        "source_name": "歸藏实验室",
        "author": "青衫",
        "title": "Short field update v2",
        "title_zh": "歸藏模型快讯",
    }


def test_search_id_subquery_switches_between_fts_like_and_noop() -> None:
    assert search.search_id_subquery(None) == (None, [])
    assert search.search_id_subquery("   ") == (None, [])

    fts_sql, fts_params = search.search_id_subquery("Ada")
    assert fts_sql == "SELECT item_id FROM items_fts WHERE items_fts MATCH ?"
    assert fts_params == ['"Ada"']

    like_sql, like_params = search.search_id_subquery("%_")
    assert like_sql is not None
    assert like_sql.count("LIKE ? ESCAPE '\\'") == 4
    assert "content_text LIKE" not in like_sql
    assert like_params == [r"%\%\_%", r"%\%\_%", r"%\%\_%", r"%\%\_%"]


def test_like_search_helpers_ignore_internal_whitespace() -> None:
    assert search.like_patterns_for_query("分享 Claude\tCode\u3000") == ["%分享ClaudeCode%"]

    sql, params = search.source_match_expression("分享 Claude Code", source_alias="src", item_alias="it")

    assert "REPLACE(" in sql
    assert "src.name LIKE" not in sql
    assert "it.author LIKE" not in sql
    assert params == ["%分享ClaudeCode%", "%分享ClaudeCode%"]


def test_expand_st_variants_uses_opencc_bidirectionally() -> None:
    assert search.expand_st_variants("归藏") == ["归藏", "歸藏"]
    assert search.expand_st_variants("歸藏") == ["歸藏", "归藏"]
    assert search.expand_st_variants("openai") == ["openai"]


def test_search_id_subquery_expands_simplified_traditional_variants() -> None:
    fts_sql, fts_params = search.search_id_subquery("归藏工具")
    assert fts_sql == "SELECT item_id FROM items_fts WHERE items_fts MATCH ?"
    assert fts_params == ['"归藏工具" OR "歸藏工具"']

    like_sql, like_params = search.search_id_subquery("归藏")
    assert like_sql is not None
    assert like_sql.count("LIKE ? ESCAPE '\\'") == 8
    assert like_params == ["%归藏%", "%归藏%", "%归藏%", "%归藏%", "%歸藏%", "%歸藏%", "%歸藏%", "%歸藏%"]


def test_source_match_expression_reuses_like_escape() -> None:
    sql, params = search.source_match_expression(r"100%_AI", source_alias="src", item_alias="it")

    assert sql == (
        "CASE WHEN (REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(src.name, ''), ' ', ''), "
        "char(12288), ''), char(9), ''), char(10), ''), char(13), '') LIKE ? ESCAPE '\\' OR "
        "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(it.author, ''), ' ', ''), char(12288), ''), "
        "char(9), ''), char(10), ''), char(13), '') LIKE ? ESCAPE '\\') THEN 1 ELSE 0 END"
    )
    assert params == [r"%100\%\_AI%", r"%100\%\_AI%"]


def test_timeline_search_uses_fts_and_filters_results(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/api/v1/timeline?q=OpenAI")
    assert response.status_code == 200
    data = response.json()["data"]

    assert [item["id"] for item in data["items"]] == ["item-openai", "item-claude"]
    assert data["total"] == 2


def test_timeline_search_prioritizes_source_matches_and_rotates_sources(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_source_search_ranking_db(tmp_path)))

    search = client.get("/api/v1/timeline", params={"q": "Shared Lab", "limit": 5}).json()["data"]
    no_query = client.get("/api/v1/timeline", params={"limit": 5}).json()["data"]

    assert [item["id"] for item in search["items"]] == [
        "item-x-1",
        "item-wx-1",
        "item-x-2",
        "item-x-3",
        "item-content-newer",
    ]
    assert "wx_shared" in {item["source_id"] for item in search["items"][:3]}
    assert [item["id"] for item in no_query["items"]] == [
        "item-content-newer",
        "item-x-1",
        "item-x-2",
        "item-x-3",
        "item-wx-1",
    ]


def test_timeline_search_matches_source_author_and_chinese_title(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db_with_model_enrichment(tmp_path)))

    source = client.get("/api/v1/timeline", params={"q": "OpenAI Blog"}).json()["data"]
    author = client.get("/api/v1/timeline", params={"q": "Ada"}).json()["data"]
    chinese_title = client.get("/api/v1/timeline", params={"q": "模型发布"}).json()["data"]

    assert [item["id"] for item in source["items"]] == ["item-openai", "item-claude"]
    assert [item["id"] for item in author["items"]] == ["item-openai"]
    assert "Ada" not in author["items"][0]["source_name"]
    assert "Ada" not in author["items"][0]["title"]
    assert [item["id"] for item in chinese_title["items"]] == ["item-openai"]


def test_short_search_uses_like_for_timeline_and_curated_precomputed(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_source(conn, "guizang_feed", "歸藏社")
    _insert_item(
        conn,
        "item-guizang",
        "guizang_feed",
        "Short source item",
        "元亨",
        "The short Chinese source token appears only in the source name.",
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json, summary_json)
        VALUES ('run-1', 'item-guizang', 8.0, 2, '{}', ?)
        """,
        (_curated_summary("item-guizang", "guizang_feed", "歸藏社", "Short source item", "元亨"),),
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"q": "歸藏"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"q": "歸藏"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-guizang"]
    assert [item["id"] for item in curated["items"]] == ["item-guizang"]


def test_simplified_short_search_matches_traditional_source_name(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_source(conn, "guizang_feed", "歸藏社")
    _insert_item(
        conn,
        "item-guizang",
        "guizang_feed",
        "Short source item",
        "元亨",
        "The simplified query should match the traditional source name.",
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json, summary_json)
        VALUES ('run-1', 'item-guizang', 8.0, 2, '{}', ?)
        """,
        (_curated_summary("item-guizang", "guizang_feed", "歸藏社", "Short source item", "元亨"),),
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"q": "归藏"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"q": "归藏"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-guizang"]
    assert [item["id"] for item in curated["items"]] == ["item-guizang"]


def test_timeline_and_curated_search_ignore_internal_whitespace(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_source(conn, "whitespace_feed", "Whitespace Feed")
    _insert_item(
        conn,
        "item-whitespace-title",
        "whitespace_feed",
        "分享Claude Code",
        "Whitespace Author",
        "Body intentionally avoids the spaced query.",
        "2026-05-08T10:40:00Z",
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json, summary_json)
        VALUES ('run-1', 'item-whitespace-title', 8.0, 2, '{}', ?)
        """,
        (_curated_summary("item-whitespace-title", "whitespace_feed", "Whitespace Feed", "分享Claude Code", "Whitespace Author"),),
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"q": "分享 Claude Code", "limit": 50}).json()["data"]
    curated = client.get("/api/v1/curated", params={"q": "分享 Claude Code", "limit": 50}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-whitespace-title"]
    assert [item["id"] for item in curated["items"]] == ["item-whitespace-title"]


def test_simplified_query_marks_traditional_source_as_source_match_for_ranking(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    _insert_source_with_kind(conn, "wx_guizang", "歸藏工具箱", "wechat")
    _insert_source_with_kind(conn, "other_feed", "Other Feed", "feed")
    _insert_item(
        conn,
        "item-content-newer",
        "other_feed",
        "归藏 body-only title match",
        "Other",
        "The source does not match.",
        "2026-05-08T10:30:00Z",
    )
    _insert_item(
        conn,
        "item-guizang",
        "wx_guizang",
        "Traditional source item",
        "元亨",
        "The title intentionally avoids the simplified query token.",
        "2026-05-08T09:00:00Z",
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"q": "归藏", "limit": 2}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-guizang", "item-content-newer"]


def test_short_search_uses_like_for_curated_compute_fallback(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_source(conn, "short_author_feed", "Short Author Feed")
    _insert_item(
        conn,
        "item-short-author",
        "short_author_feed",
        "Short author item",
        "李雷",
        "The short Chinese author token appears only in the author field.",
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-1', 'item-short-author', 8.0, 2, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    curated = client.get("/api/v1/curated", params={"q": "李雷"}).json()["data"]

    assert [item["id"] for item in curated["items"]] == ["item-short-author"]


def test_short_search_escapes_like_wildcards(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_source(conn, "wildcard_feed", "Wildcard Feed")
    _insert_item(
        conn,
        "item-percent",
        "wildcard_feed",
        "Literal 100% update",
        "Percent Author",
        "Plain text without wildcard characters.",
        "2026-05-08T07:40:00Z",
    )
    _insert_item(
        conn,
        "item-underscore",
        "wildcard_feed",
        "Literal under_score update",
        "Underscore Author",
        "Plain text without wildcard characters.",
        "2026-05-08T07:35:00Z",
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    percent = client.get("/api/v1/timeline", params={"q": "%"}).json()["data"]
    underscore = client.get("/api/v1/timeline", params={"q": "_"}).json()["data"]

    assert [item["id"] for item in percent["items"]] == ["item-percent"]
    assert [item["id"] for item in underscore["items"]] == ["item-underscore"]


def test_timeline_supports_channel_filters_and_url_page_state(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    page_one = client.get("/api/v1/timeline", params={"limit": 1, "page": 1}).json()["data"]
    page_two = client.get("/api/v1/timeline", params={"limit": 1, "page": 2}).json()["data"]

    assert page_one["page"] == 1
    assert page_one["limit"] == 1
    assert page_one["total"] == 3
    assert [item["id"] for item in page_one["items"]] == ["item-openai"]
    assert page_two["page"] == 2
    assert [item["id"] for item in page_two["items"]] == ["item-claude"]

    x_only = client.get("/api/v1/timeline", params={"channel": "x"}).json()["data"]
    assert [item["id"] for item in x_only["items"]] == ["item-x"]
    assert x_only["total"] == 1

    first_party = client.get("/api/v1/timeline", params={"channel": "firstParty"}).json()["data"]
    assert [item["id"] for item in first_party["items"]] == ["item-openai", "item-claude"]
    assert {item["source_kind"] for item in first_party["items"]} == {"feed"}


def test_timeline_category_filter_applies_before_pagination(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "OpenAI API 产品更新", ["产品更新", "OpenAI"])
    _insert_enrichment(conn, "item-claude", "Claude 研究笔记", ["论文/研究", "Anthropic"])
    _insert_enrichment(conn, "item-x", "SQLite 研究讨论", ["论文/研究", "教程/实践"])
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    page_one = client.get("/api/v1/timeline", params={"category": "paper", "limit": 1, "page": 1}).json()["data"]
    page_two = client.get("/api/v1/timeline", params={"category": "paper", "limit": 1, "page": 2}).json()["data"]

    assert page_one["total"] == 2
    assert [item["id"] for item in page_one["items"]] == ["item-claude"]
    assert [item["id"] for item in page_two["items"]] == ["item-x"]
    assert all("论文/研究" in item["topic_tags"] for item in [*page_one["items"], *page_two["items"]])


def test_ai_models_category_excludes_course_and_benchmark_only_items(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "OpenAI 模型发布", ["模型发布", "OpenAI"])
    _insert_enrichment(conn, "item-claude", "Transformer 实践课程", ["模型发布", "教程/实践"])
    _insert_enrichment(conn, "item-x", "模型评测讨论", ["评测/基准", "开源/仓库"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 9.9, 2, '{}'),
          ('run-1', 'item-x', 9.5, 3, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "ai-models"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "ai-models"}).json()["data"]
    all_curated = client.get("/api/v1/curated").json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-openai"]
    assert [item["id"] for item in curated["items"]] == ["item-openai"]
    assert [item["id"] for item in all_curated["items"]] == ["item-openai", "item-claude", "item-x"]


def test_product_category_uses_product_semantics_not_model_or_multimodal_only(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    _insert_enrichment(conn, "item-openai", "纯模型多模态条目", ["模型发布", "多模态", "MCP/工具"])
    _insert_enrichment(conn, "item-claude", "真实产品更新", ["产品更新", "模型发布"])
    _insert_enrichment(conn, "item-x", "泛开源仓库", ["开源/仓库", "端侧"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 9.9, 2, '{}'),
          ('run-1', 'item-x', 9.5, 3, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "ai-products"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "ai-products"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-claude"]
    assert [item["id"] for item in curated["items"]] == ["item-claude"]


def test_tip_category_excludes_repo_and_edge_only_items(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-opinion', 'openai_blog', 'https://example.com/opinion', 'Opinion news', 'Ada',
          '2026-05-08T07:00:00Z', '2026-05-08T07:02:00Z',
          'This is industry opinion without a concrete tutorial or deployment practice.',
          NULL, 'h-opinion', '{}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-deploy-news', 'openai_blog', 'https://example.com/deploy-news', 'Deploy industry news', 'Ada',
          '2026-05-08T06:00:00Z', '2026-05-08T06:02:00Z',
          'This is broad infrastructure industry news, not a concrete deployment practice.',
          NULL, 'h-deploy-news', '{}'
        )
        """
    )
    _insert_enrichment(conn, "item-openai", "泛开源端侧条目", ["开源/仓库", "端侧"])
    _insert_enrichment(conn, "item-claude", "Transformer 实践课程", ["模型发布", "教程/实践"])
    _insert_enrichment(conn, "item-x", "部署工程实践", ["部署/工程", "大佬观点"])
    _insert_enrichment(conn, "item-opinion", "纯观点行业新闻", ["大佬观点", "行业动态"])
    _insert_enrichment(conn, "item-deploy-news", "部署行业新闻", ["部署/工程", "行业动态"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 9.9, 2, '{}'),
          ('run-1', 'item-x', 9.5, 3, '{}'),
          ('run-1', 'item-opinion', 9.1, 4, '{}'),
          ('run-1', 'item-deploy-news', 9.0, 5, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "tip"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "tip"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-claude", "item-x"]
    assert [item["id"] for item in curated["items"]] == ["item-claude", "item-x"]


def test_timeline_and_curated_deduplicate_same_source_url(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-claude-dup', 'openai_blog', 'https://example.com/claude', 'Claude Notes Updated', 'Ben',
          '2026-05-08T09:30:00Z', '2026-05-08T09:32:00Z',
          'Anthropic published updated engineering notes for model users.',
          NULL, 'h-claude-dup', '{}'
        )
        """
    )
    _insert_enrichment(conn, "item-claude", "Claude 研究笔记", ["论文/研究", "Anthropic"])
    _insert_enrichment(conn, "item-claude-dup", "Claude 研究笔记更新", ["论文/研究", "Anthropic"])
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES
          ('run-1', 'item-claude', 8.0, 2, '{}'),
          ('run-1', 'item-claude-dup', 8.1, 3, '{}')
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    timeline = client.get("/api/v1/timeline", params={"category": "paper"}).json()["data"]
    curated = client.get("/api/v1/curated", params={"category": "paper"}).json()["data"]

    assert [item["id"] for item in timeline["items"]] == ["item-claude-dup"]
    assert [item["id"] for item in curated["items"]] == ["item-claude-dup"]


def test_timeline_exposes_latest_curated_metadata_for_all_feed(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/timeline?q=OpenAI").json()["data"]["items"][0]

    assert item["rank"] == 1
    assert item["weighted_score"] == pytest.approx(8.2)
    assert item["reasoning"] == "精选推荐理由：OpenAI API 更新对开发者有直接价值，适合优先阅读。"
    assert item["why_recommend"] == item["reasoning"]


def test_timeline_uses_chinese_fallback_instead_of_english_scoring_reason(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/timeline?q=OpenAI").json()["data"]["items"][0]

    assert item["reasoning"] != "OpenAI release is useful for API builders."
    assert item["why_recommend"] == item["reasoning"]
    assert any("\u4e00" <= char <= "\u9fff" for char in item["reasoning"])


def test_timeline_filters_to_latest_prefilter_pass_when_prefilter_exists(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES
          ('item-openai', 'prefilter', 'test.r1', 'fake', '{}', '{}', '{"is_ai_related":true,"confidence":0.99}', 1, 0, '2026-05-08T10:05:00Z', NULL),
          ('item-claude', 'prefilter', 'test.r1', 'fake', '{}', '{}', '{"is_ai_related":true,"confidence":0.90}', 1, 0, '2026-05-08T10:05:00Z', NULL),
          ('item-x', 'prefilter', 'test.r1', 'fake', '{}', '{}', '{"is_ai_related":false,"confidence":0.90}', 1, 0, '2026-05-08T10:05:00Z', NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (
          'item-claude', 'scoring', 'test.r1', 'fake', '{}', '{}',
          '{"relevance":2,"density":2,"recency":2,"authority":2,"engineering":2,"reasoning":"low relevance"}',
          1, 0, '2026-05-08T10:06:00Z', NULL
        )
        """
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    data = client.get("/api/v1/timeline").json()["data"]

    assert [item["id"] for item in data["items"]] == ["item-openai"]
    assert data["total"] == 1


def test_curated_search_uses_fts_and_filters_results(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    match = client.get("/api/v1/curated?q=OpenAI").json()["data"]
    source_match = client.get("/api/v1/curated", params={"q": "OpenAI Blog"}).json()["data"]
    no_match = client.get("/api/v1/curated?q=xyzzyqwertynonexistent").json()["data"]

    assert [item["id"] for item in match["items"]] == ["item-openai"]
    assert [item["id"] for item in source_match["items"]] == ["item-openai"]
    assert no_match["items"] == []


def test_curated_search_prioritizes_source_matches_and_rotates_sources_for_precomputed_items(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_source_search_ranking_db(tmp_path, curated=True)))

    search = client.get("/api/v1/curated", params={"q": "Shared Lab"}).json()["data"]
    no_query = client.get("/api/v1/curated").json()["data"]

    assert [item["id"] for item in search["items"]] == [
        "item-x-1",
        "item-wx-1",
        "item-x-2",
        "item-x-3",
        "item-content-newer",
    ]
    assert [item["id"] for item in no_query["items"]] == [
        "item-content-newer",
        "item-x-1",
        "item-x-2",
        "item-x-3",
        "item-wx-1",
    ]


def test_curated_api_exposes_related_discussions_for_cross_source_links(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/curated").json()["data"]["items"][0]

    assert item["related_discussions"] == [
        {
            "source_id": "simonw_mastodon",
            "source_name": "Simon Willison (Mastodon)",
            "source_kind": "x",
            "author": "Simon",
            "url": "https://fedi.simonwillison.net/@simon/1",
        }
    ]


def test_curated_api_exposes_safe_article_media_assets(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    item = client.get("/api/v1/curated").json()["data"]["items"][0]

    assert item["media_assets"] == [{"type": "image", "url": "https://nitter.net/pic/media%2Fopenai.jpg"}]


def test_search_query_treats_fts_syntax_as_literal_text(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    client = TestClient(create_app(db_path))

    for query in ["title:foo", "OpenAI OR Claude", '"DROP TABLE items"']:
        response = client.get("/api/v1/timeline", params={"q": query})
        assert response.status_code == 200

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3


def test_conn_from_request_closes_connection_on_exit(tmp_path: Path) -> None:
    # Regression: `with conn_from_request(...)` must close the connection on exit.
    # A bare sqlite3 connection returned to a `with` block is NOT closed by it,
    # leaking a connection per request; leaked read connections pin WAL read-marks
    # and cause the WAL to grow unbounded until read routes 500 with SQLITE_CANTOPEN.
    db_path = _seed_db(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_path=str(db_path))))

    with request_db.conn_from_request(request) as conn:  # type: ignore[arg-type]
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
        captured = conn

    with pytest.raises(sqlite3.ProgrammingError):
        captured.execute("SELECT 1")
