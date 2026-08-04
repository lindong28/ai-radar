from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from airadar.db import migrate
from airadar.web.app import PRELOAD_ITEM_KEYS, _compact_preload, create_app
from airadar.web.schemas import (
    CuratedArchiveResponse,
    CuratedDigestResponse,
    FeedItem,
    TimelineResponse,
    WechatItem,
    WechatListResponse,
)

MODEL_FIELD_SNAPSHOTS = {
    FeedItem: {
        "id",
        "source_id",
        "source_name",
        "source_kind",
        "source_homepage_url",
        "source_icon_url",
        "author_avatar_url",
        "tier",
        "url",
        "title",
        "title_zh",
        "author",
        "published_at",
        "fetched_at",
        "content_preview",
        "summary_zh",
        "why_recommend",
        "enriched_tags",
        "topic_tags",
        "reasoning",
        "related_discussions",
        "media_assets",
        "content_text",
        "weighted_score",
        "scores",
        "rank",
        "reason",
    },
    TimelineResponse: {"items", "next_cursor", "total", "page", "limit"},
    CuratedArchiveResponse: {
        "run_id",
        "ruleset_version",
        "items",
        "date",
        "count",
        "total",
        "page",
        "limit",
    },
    CuratedDigestResponse: {"run_id", "ruleset_version", "items", "date", "count", "daily_metrics"},
    WechatItem: {
        "slug",
        "title",
        "abstract",
        "tags",
        "author",
        "avatar_url",
        "published_at",
        "url",
        "detail_url",
        "recommendation",
    },
    WechatListResponse: {"items", "total", "page", "limit"},
}

PRELOAD_ITEM_KEY_SNAPSHOT = {
    "id",
    "source_id",
    "source_name",
    "source_kind",
    "source_homepage_url",
    "source_icon_url",
    "author_avatar_url",
    "tier",
    "url",
    "title",
    "title_zh",
    "author",
    "published_at",
    "fetched_at",
    "content_preview",
    "summary_zh",
    "enriched_tags",
    "topic_tags",
    "reasoning",
    "related_discussions",
    "media_assets",
    "weighted_score",
    "rank",
}

FEED_ITEM_FIXTURE = {
    "id": "feed-item",
    "source_id": "source",
    "source_name": "Source",
    "source_kind": "rss",
    "source_homepage_url": "https://example.com",
    "source_icon_url": "https://example.com/icon.png",
    "author_avatar_url": "https://example.com/avatar.png",
    "tier": "T1",
    "url": "https://example.com/article",
    "title": "Example article",
    "title_zh": "示例文章",
    "author": "Author",
    "published_at": "2026-07-14T00:00:00Z",
    "fetched_at": "2026-07-14T00:01:00Z",
    "content_preview": "Preview",
    "summary_zh": "摘要",
    "why_recommend": "推荐理由",
    "enriched_tags": ["AI"],
    "topic_tags": ["模型"],
    "reasoning": "Reasoning",
    "related_discussions": [{"url": "https://example.com/discussion"}],
    "media_assets": [{"url": "https://example.com/image.png"}],
    "content_text": "Full text",
    "weighted_score": 9.2,
    "scores": {"relevance": 9.0},
    "rank": 1,
    "reason": {"weighted_score": 9.2},
}

WECHAT_ITEM_FIXTURE = {
    "slug": "wechat-item",
    "title": "微信文章",
    "abstract": "摘要",
    "tags": ["AI"],
    "author": "测试公众号",
    "avatar_url": "/wechat-icon.svg",
    "published_at": "2026-07-14T00:00:00Z",
    "url": "https://mp.weixin.qq.com/s/example",
    "detail_url": "/wechat/wechat-item",
    "recommendation": "推荐理由",
}


def test_response_model_field_sets_are_frozen() -> None:
    for model, expected_fields in MODEL_FIELD_SNAPSHOTS.items():
        assert set(model.model_fields) == expected_fields


@pytest.mark.parametrize(
    ("response_model", "payload"),
    [
        (
            TimelineResponse,
            {"items": [FEED_ITEM_FIXTURE], "next_cursor": None, "total": 1, "page": 1, "limit": 40},
        ),
        (
            CuratedArchiveResponse,
            {
                "run_id": None,
                "ruleset_version": None,
                "items": [FEED_ITEM_FIXTURE],
                "date": "2026-07-14",
                "count": 1,
                "total": 1,
                "page": 1,
                "limit": 40,
            },
        ),
        (
            CuratedDigestResponse,
            {
                "run_id": "20260714T000000Z-example",
                "ruleset_version": "example",
                "items": [FEED_ITEM_FIXTURE],
                "date": "2026-07-14",
                "count": 1,
            },
        ),
        (
            WechatListResponse,
            {"items": [WECHAT_ITEM_FIXTURE], "total": 1, "page": 1, "limit": 50},
        ),
    ],
)
def test_maintained_response_examples_validate(
    response_model: type[BaseModel],
    payload: dict[str, object],
) -> None:
    response_model.model_validate(payload)


def test_real_feed_routes_match_declared_response_contracts(tmp_path: Path) -> None:
    db_path = tmp_path / "feed-contract.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            )
            VALUES (
              'contract-source', 'Contract Source', 'https://example.com/feed.xml', 'T1', 1,
              'feed', 'https://example.com/', 'https://example.com/icon.png', '{}',
              '2026-07-14T00:00:00Z'
            );
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_hash, extra_json
            )
            VALUES (
              'contract-item', 'contract-source', 'https://example.com/article',
              'Contract article', 'Author', '2026-07-14T01:00:00Z',
              '2026-07-14T01:01:00Z', 'Contract body', 'contract-hash', '{}'
            );
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            )
            VALUES (
              'contract-run', 'contract.r1', '{}', 6.5, '[]', '["contract-item"]',
              '2026-07-14T01:02:00Z'
            );
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES ('contract-run', 'contract-item', 8.2, 1, '{}');
            """
        )

    client = TestClient(create_app(db_path))
    contracts = [
        ("/api/v1/timeline", TimelineResponse),
        ("/api/v1/curated", CuratedArchiveResponse),
        ("/api/v1/curated?run_id=contract-run", CuratedDigestResponse),
    ]
    for path, response_model in contracts:
        response = client.get(path)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["items"]
        response_model.model_validate(data)


def test_historical_precomputed_feed_item_can_omit_author_avatar_url() -> None:
    item = {
        "id": "0de5e0ea381a31e3",
        "source_id": "openai_x",
        "source_name": "OpenAI",
        "source_kind": "x",
        "source_homepage_url": "https://x.com/OpenAI",
        "source_icon_url": "https://www.google.com/s2/favicons?domain=x.com&sz=64",
        "tier": "T1",
        "url": "https://x.com/openai/status/2061564502160892138",
        "title": "OpenAI frontier models and Codex are now generally available on AWS",
        "title_zh": "OpenAI 前沿模型与 Codex 正式登陆 AWS",
        "author": "@OpenAI",
        "published_at": "2026-06-01T21:44:13Z",
        "fetched_at": "2026-06-02T07:31:55Z",
        "content_preview": "OpenAI frontier models and Codex are now generally available on AWS",
        "summary_zh": "OpenAI 宣布其前沿模型和 Codex 已在 AWS 上全面可用。",
        "why_recommend": "OpenAI 与 AWS 深度整合，是合规优先场景的关键信号。",
        "enriched_tags": ["OpenAI", "产品更新", "行业动态", "部署/工程"],
        "topic_tags": ["OpenAI", "产品更新", "行业动态", "部署/工程"],
        "reasoning": "OpenAI 与 AWS 深度整合，是合规优先场景的关键信号。",
        "related_discussions": [],
        "media_assets": [],
        "content_text": "OpenAI frontier models and Codex are now generally available on AWS",
        "weighted_score": 9.2,
        "scores": {"relevance": 9.0},
        "rank": 1,
        "reason": {"weighted_score": 9.875},
    }
    assert "author_avatar_url" not in item

    response = CuratedDigestResponse.model_validate(
        {
            "run_id": "20260602T074546Z-b2aa",
            "ruleset_version": "2026-05-13.r1",
            "items": [item],
            "date": "2026-06-02",
            "count": 1,
        }
    )

    assert response.items[0].author_avatar_url is None


def test_saved_wechat_item_with_null_recommendation_validates_route_response(tmp_path: Path) -> None:
    db_path = tmp_path / "null-recommendation.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            )
            VALUES (
              'wx-null', 'Nullable WeChat', 'https://example.com/feed', 'T2', 1, 'wechat',
              'https://mp.weixin.qq.com/', '/wechat-icon.svg', '{}', '2026-07-14T00:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_hash, extra_json
            )
            VALUES (
              'wechat-null', 'wx-null', 'https://mp.weixin.qq.com/s/null',
              'Recommendation can be null', '测试公众号',
              '2026-07-14T00:00:00Z', '2026-07-14T00:01:00Z',
              '正文', 'null-hash', '{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO wechat_interpretations (
              item_id, slug, recommendation, save_decision, abstract,
              tags_json, summary_md, processed_at
            )
            VALUES (
              'wechat-null', 'null-recommendation', NULL, 1, '摘要',
              '["AI"]', '总结', '2026-07-14T00:02:00Z'
            )
            """
        )

    response = TestClient(create_app(db_path)).get("/api/v1/wechat")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"][0]["recommendation"] is None

    WechatListResponse.model_validate(data)


def test_preload_item_keys_match_the_pre_refactor_snapshot() -> None:
    assert PRELOAD_ITEM_KEYS == PRELOAD_ITEM_KEY_SNAPSHOT


def test_ssr_preload_compacts_the_maintained_feed_contract() -> None:
    preload = {"items": [FEED_ITEM_FIXTURE], "total": 1, "page": 1, "limit": 40}

    compact = _compact_preload(preload)

    item = compact["items"][0]
    assert set(item) == PRELOAD_ITEM_KEY_SNAPSHOT - {"content_preview"}
    assert compact["total"] == 1
    assert _compact_preload(compact) == compact
