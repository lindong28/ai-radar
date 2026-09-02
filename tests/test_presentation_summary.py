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
          -- Real X rows never carry content_html; their media lives in extra_json
          -- (written by fetcher/x_api.py), so this fixture mirrors production.
          NULL AS content_html,
          '{"x_media":[{"media_key":"3_a","type":"photo","url":"https://pbs.twimg.com/media/a.jpg"}]}' AS extra_json,
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
        "primary_category": None,
        "is_opinion": None,
        "classification_projection_status": "unclassified",
        "classification_projection_authority": "none",
        "classification_projection_evidence": [],
        "reasoning": "这是固定推荐理由",
        "related_discussions": [],
        "media_assets": [
            {
                "type": "image",
                "url": "/img?url=https%3A%2F%2Fpbs.twimg.com%2Fmedia%2Fa.jpg",
            }
        ],
        "content_text": "Fixed preview text",
    }
    assert summary == expected
    assert list(summary) == list(expected)
    assert json.dumps(summary, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


def _row(conn, **cols):  # noqa: ANN001, ANN003, ANN202
    select = ", ".join(f"{v} AS {k}" for k, v in cols.items())
    return conn.execute(f"SELECT {select}").fetchone()


def test_x_media_is_not_trimmed_by_the_rss_rank_policy() -> None:
    """ADR-054's rank trimming exists for scraped RSS body images, not tweets."""
    import sqlite3

    from airadar.presentation.media import _visible_media_assets

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    three = (
        '{"x_media":['
        '{"media_key":"1","type":"photo","url":"https://pbs.twimg.com/media/a.jpg"},'
        '{"media_key":"2","type":"photo","url":"https://pbs.twimg.com/media/b.jpg"},'
        '{"media_key":"3","type":"video","url":"https://pbs.twimg.com/media/c.jpg"}]}'
    )
    html = "'<p><img src=\"https://mmbiz.qpic.cn/1.png\"><img src=\"https://mmbiz.qpic.cn/2.png\"></p>'"

    # rank 99 is past CURATED_MEDIA_PREVIEW_RANK_LIMIT: RSS would get nothing,
    # and an unranked RSS row would get exactly one. X keeps all three either way.
    for rank in ("99", "NULL"):
        row = _row(conn, source_kind="'x'", extra_json=f"'{three}'", content_html="NULL", rank=rank)
        assert len(_visible_media_assets(row)) == 3
        # No bare hotlink: every URL is same-origin. (The twimg host still
        # appears inside the query string — that is the proxied target, not a
        # hotlink, so asserting its absence would be the wrong property.)
        assert all(a["url"].startswith("/img?url=") for a in _visible_media_assets(row))
        assert not any(a["url"].startswith("https://pbs.twimg.com") for a in _visible_media_assets(row))

    # The RSS policy itself is untouched — this is the discriminating half.
    assert _visible_media_assets(_row(conn, source_kind="'feed'", content_html=html, rank="99")) == []
    assert len(_visible_media_assets(_row(conn, source_kind="'feed'", content_html=html, rank="NULL"))) == 1
    assert len(_visible_media_assets(_row(conn, source_kind="'feed'", content_html=html, rank="1"))) == 2

    # An X row never picks up media from content_html even if one somehow exists.
    stray = _row(conn, source_kind="'x'", extra_json="NULL", content_html=html, rank="1")
    assert _visible_media_assets(stray) == []
    conn.close()
