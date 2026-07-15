from __future__ import annotations

import importlib.util
import json
import sqlite3

from airadar.presentation import media, related, summary
from airadar.presentation.summary import item_summary
from airadar.web.routes import categories, request_db, search


def test_common_shim_is_removed_and_real_entrypoints_remain() -> None:
    assert importlib.util.find_spec("airadar.web.routes." + "common") is None
    assert media._LAZY_SRC_ATTRS == ("data-src", "data-original", "data-lazy-src")
    assert media.proxy_image_url.__module__ == media.__name__
    assert related.related_discussions.__module__ == related.__name__
    assert summary.item_summary.__module__ == summary.__name__
    assert categories.category_filter_clause.__module__ == categories.__name__
    assert request_db.conn_from_request.__module__ == request_db.__name__
    assert search.search_id_subquery.__module__ == search.__name__


def test_item_summary_preserves_serialized_output_contract() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT
          'item-1' AS id,
          'source-x' AS source_id,
          'Source X' AS source_name,
          'x' AS source_kind,
          'https://source.example' AS source_homepage_url,
          'https://mmbiz.qpic.cn/icon.png' AS source_icon_url,
          'https://cdn.example/avatar.png' AS author_avatar_url,
          'A' AS tier,
          'https://example.com/posts/1' AS url,
          'Fixed title' AS title,
          'author' AS author,
          '2026-07-14T01:02:03Z' AS published_at,
          '2026-07-14T01:03:04Z' AS fetched_at,
          'Fixed preview text' AS content_text,
          '<p><img src="https://mmbiz.qpic.cn/media.png"></p>' AS content_html,
          13 AS rank,
          '{"scores":{"reasoning":"这是固定推荐理由"}}' AS reason_json
        """
    ).fetchone()
    assert row is not None

    summary = item_summary(
        row,
        conn=None,
        include_related=False,
        enrichment_loaded=True,
    )
    expected = {
        "id": "item-1",
        "source_id": "source-x",
        "source_name": "Source X",
        "source_kind": "x",
        "source_homepage_url": "https://source.example",
        "source_icon_url": "/img?url=https%3A%2F%2Fmmbiz.qpic.cn%2Ficon.png",
        "author_avatar_url": "https://cdn.example/avatar.png",
        "tier": "A",
        "url": "https://example.com/posts/1",
        "title": "Fixed title",
        "title_zh": "Fixed title",
        "author": "author",
        "published_at": "2026-07-14T01:02:03Z",
        "fetched_at": "2026-07-14T01:03:04Z",
        "content_preview": "Fixed preview text",
        "summary_zh": None,
        "why_recommend": "这是固定推荐理由",
        "enriched_tags": [],
        "topic_tags": [],
        "reasoning": "这是固定推荐理由",
        "related_discussions": [],
        "media_assets": [
            {
                "type": "image",
                "url": "/img?url=https%3A%2F%2Fmmbiz.qpic.cn%2Fmedia.png",
            }
        ],
        "content_text": "Fixed preview text",
    }
    assert summary == expected
    assert list(summary) == list(expected)
    assert json.dumps(summary, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
