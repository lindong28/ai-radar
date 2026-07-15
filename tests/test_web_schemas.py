from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

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

GOLDEN_DIR = (
    Path(__file__).resolve().parents[1]
    / "plans"
    / "20260711-refactor-web-pipeline-structure"
    / "golden"
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
    CuratedDigestResponse: {"run_id", "ruleset_version", "items", "date", "count"},
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


def _golden_json(filename: str) -> Any:
    return json.loads((GOLDEN_DIR / filename).read_text())


def test_response_model_field_sets_are_frozen() -> None:
    for model, expected_fields in MODEL_FIELD_SNAPSHOTS.items():
        assert set(model.model_fields) == expected_fields


@pytest.mark.parametrize(
    ("pattern", "response_model"),
    [
        ("timeline_*.json", TimelineResponse),
        ("wechat*.json", WechatListResponse),
    ],
)
def test_endpoint_golden_responses_validate(
    pattern: str,
    response_model: type[BaseModel],
) -> None:
    fixtures = sorted(GOLDEN_DIR.glob(pattern))
    assert fixtures
    for fixture in fixtures:
        response_model.model_validate(_golden_json(fixture.name)["data"])


def test_curated_golden_responses_validate_both_contracts() -> None:
    digest_fixtures = {
        "curated_date_2026-07-14.json",
        "curated_run_id_20260528T230502Z-5719.json",
        "curated_run_id_20260714T090059Z-0c35.json",
    }
    fixtures = sorted(GOLDEN_DIR.glob("curated*.json"))
    assert fixtures
    for fixture in fixtures:
        response_model = CuratedDigestResponse if fixture.name in digest_fixtures else CuratedArchiveResponse
        response_model.model_validate(_golden_json(fixture.name)["data"])


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


@pytest.mark.parametrize(
    ("ssr_fixture", "expected_item_keys", "is_compact_feed"),
    [
        (
            "ssr_index_preload.json",
            {
                "author",
                "author_avatar_url",
                "enriched_tags",
                "fetched_at",
                "id",
                "media_assets",
                "published_at",
                "rank",
                "reasoning",
                "related_discussions",
                "source_homepage_url",
                "source_icon_url",
                "source_id",
                "source_kind",
                "source_name",
                "summary_zh",
                "tier",
                "title",
                "title_zh",
                "topic_tags",
                "url",
                "weighted_score",
            },
            True,
        ),
        (
            "ssr_all_preload.json",
            {
                "author",
                "author_avatar_url",
                "enriched_tags",
                "fetched_at",
                "id",
                "media_assets",
                "published_at",
                "rank",
                "reasoning",
                "source_homepage_url",
                "source_icon_url",
                "source_id",
                "source_kind",
                "source_name",
                "summary_zh",
                "tier",
                "title",
                "title_zh",
                "topic_tags",
                "url",
                "weighted_score",
            },
            True,
        ),
        (
            "ssr_wechat_preload.json",
            {
                "abstract",
                "author",
                "avatar_url",
                "detail_url",
                "published_at",
                "recommendation",
                "slug",
                "tags",
                "title",
                "url",
            },
            False,
        ),
    ],
)
def test_ssr_preload_matches_the_pre_refactor_golden(
    ssr_fixture: str,
    expected_item_keys: set[str],
    is_compact_feed: bool,
) -> None:
    preload = _golden_json(ssr_fixture)
    actual_item_keys = {key for item in preload["items"] for key in item}
    assert actual_item_keys == expected_item_keys
    if is_compact_feed:
        assert _compact_preload(preload) == preload
