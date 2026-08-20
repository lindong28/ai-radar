from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
from airadar.sources.loader import load_sources
from airadar.sources.sync import sync_to_db
from airadar.sources.x_state import X_RUNTIME_META_KEYS
from airadar.web.app import create_app


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
          'feed', 'https://openai.com/', 'https://example.com/openai.ico', '{}', '2026-05-11T00:00:00Z'
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_about_page_contains_required_static_sections(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/about")

    assert response.status_code == 200
    assert "产品定位" in response.text
    assert "信源池" in response.text
    assert "设计原则" in response.text
    assert "联系方式" in response.text


def test_about_page_uses_placeholder_site_identity_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AI_RADAR_SITE_DOMAIN",
        "AI_RADAR_SITE_REPO_URL",
        "AI_RADAR_SITE_MAINTAINER",
        "AI_RADAR_SITE_MAINTAINER_URL",
        "AI_RADAR_SITE_X_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/about")

    assert response.status_code == 200
    assert "your-name" in response.text
    assert "https://github.com/your-org/ai-radar" in response.text
    assert ("lin" + "dong" + "28") not in response.text
    assert ("ai" + "planet.live") not in response.text


def test_about_page_uses_owner_site_identity_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_account = "lin" + "dong" + "28"
    owner_name = "lin" + "dong"
    owner_repo_url = f"https://github.com/{owner_account}/ai-radar"
    owner_url = f"https://github.com/{owner_account}"
    owner_x_url = f"https://x.com/{owner_account}"
    monkeypatch.setenv("AI_RADAR_SITE_REPO_URL", owner_repo_url)
    monkeypatch.setenv("AI_RADAR_SITE_MAINTAINER", owner_name)
    monkeypatch.setenv("AI_RADAR_SITE_MAINTAINER_URL", owner_url)
    monkeypatch.setenv("AI_RADAR_SITE_X_URL", owner_x_url)
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/about")

    assert response.status_code == 200
    assert owner_name in response.text
    assert owner_repo_url in response.text
    assert owner_x_url in response.text


def test_about_html_does_not_expose_old_static_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_RADAR_SITE_REPO_URL", raising=False)
    monkeypatch.delenv("AI_RADAR_SITE_MAINTAINER", raising=False)
    monkeypatch.delenv("AI_RADAR_SITE_MAINTAINER_URL", raising=False)
    monkeypatch.delenv("AI_RADAR_SITE_X_URL", raising=False)
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/about.html", follow_redirects=False)

    assert ("lin" + "dong" + "28") not in response.text


def test_sources_api_supplies_about_table_fields(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    source = client.get("/api/v2/sources").json()["data"]["sources"][0]

    assert source["id"] == "openai_blog"
    assert source["name"] == "OpenAI Blog"
    assert source["tier"] == "T1"
    assert source["configuration_status"] == "enabled"
    assert source["retrieval_validation"]["status"] == "not_evaluated"
    assert source["retrieval_validation"]["scope"] == "live_retrieval"
    assert source["retrieval_entrypoint_url"] == "https://openai.com/blog/rss.xml"
    assert source["public_landing_url"] == "https://openai.com/"
    assert source["configuration_synced_at"] == "2026-05-11T00:00:00Z"
    assert "url" not in source
    assert "homepage_url" not in source
    assert "synced_at" not in source
    assert "meta" not in source


def test_sources_v1_preserves_legacy_collection_and_fields(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE sources SET enabled=0, meta_json=? WHERE id='openai_blog'", ('{"legacy":"visible"}',))
    conn.commit()
    conn.close()

    payload = TestClient(create_app(db_path)).get("/api/v1/sources").json()["data"]

    assert payload == {
        "sources": [
            {
                "id": "openai_blog",
                "name": "OpenAI Blog",
                "url": "https://openai.com/blog/rss.xml",
                "tier": "T1",
                "enabled": False,
                "kind": "feed",
                "homepage_url": "https://openai.com/",
                "icon_url": "https://example.com/openai.ico",
                "meta": {"legacy": "visible"},
                "synced_at": "2026-05-11T00:00:00Z",
            }
        ]
    }


def test_sources_v1_excludes_web_rows_while_v2_keeps_full_enabled_inventory(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "deepseek_api_updates",
                "DeepSeek API updates",
                "https://api-docs.deepseek.com/zh-cn/updates",
                "T1",
                1,
                "web",
                "https://api-docs.deepseek.com/zh-cn/updates",
                None,
                '{"adapter":"web"}',
                "2026-08-14T00:00:00Z",
            ),
        )

    client = TestClient(create_app(db_path))

    v1_sources = client.get("/api/v1/sources").json()["data"]["sources"]
    v2_sources = client.get("/api/v2/sources").json()["data"]["sources"]

    assert {source["id"] for source in v1_sources} == {"openai_blog"}
    assert {source["kind"] for source in v1_sources} == {"feed"}
    assert {source["id"] for source in v2_sources} == {"openai_blog", "deepseek_api_updates"}
    assert {source["kind"] for source in v2_sources} == {"feed", "web"}


def test_sources_v1_supports_legacy_database_without_kind_column(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE sources (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              url TEXT NOT NULL,
              tier TEXT NOT NULL,
              enabled INTEGER NOT NULL,
              meta_json TEXT NOT NULL,
              synced_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, meta_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy_feed",
                "Legacy Feed",
                "https://example.test/feed.xml",
                "T1",
                1,
                '{}',
                "2026-08-14T00:00:00Z",
            ),
        )

    monkeypatch.setenv("AI_RADAR_PRE_MIGRATED_DB", "1")
    response = TestClient(create_app(db_path)).get("/api/v1/sources")

    assert response.status_code == 200
    assert response.json()["data"]["sources"] == [
        {
            "id": "legacy_feed",
            "name": "Legacy Feed",
            "url": "https://example.test/feed.xml",
            "tier": "T1",
            "enabled": True,
            "kind": "feed",
            "homepage_url": None,
            "icon_url": None,
            "meta": {},
            "synced_at": "2026-08-14T00:00:00Z",
        }
    ]


def test_sources_v1_excludes_unknown_future_kind_while_v2_includes_it(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "future_podcast",
                "Future Podcast",
                "https://example.test/podcast.xml",
                "T2",
                1,
                "podcast",
                "https://example.test/podcast",
                None,
                '{}',
                "2026-08-14T00:00:00Z",
            ),
        )

    client = TestClient(create_app(db_path))

    v1_ids = {source["id"] for source in client.get("/api/v1/sources").json()["data"]["sources"]}
    v2_ids = {source["id"] for source in client.get("/api/v2/sources").json()["data"]["sources"]}

    assert "future_podcast" not in v1_ids
    assert "future_podcast" in v2_ids


def test_sources_v1_preserves_published_feed_x_and_configured_wechat_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paid_url = "https://paid.example.test/private-feed-token"
    safe_wechat_url = "https://mp.weixin.qq.com/"
    monkeypatch.setenv("MP2RSS_FEED_URL", paid_url)
    db_path = _seed_db(tmp_path)
    x_meta = {
        "adapter": "x_api",
        "username": "OpenAI",
        "etag": "legacy-etag",
        "legacy_public": "visible",
        **{key: f"internal-{key}" for key in X_RUNTIME_META_KEYS},
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE sources SET meta_json=? WHERE id='openai_blog'",
            (json.dumps({"legacy_feed_state": "visible"}),),
        )
        conn.executemany(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url,
              meta_json, synced_at, public_url_override
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "x_openai",
                    "X: OpenAI (@OpenAI)",
                    "https://api.x.com/2/users/by/username/OpenAI",
                    "T1",
                    1,
                    "x",
                    "https://x.com/OpenAI",
                    "https://abs.twimg.com/favicons/twitter.ico",
                    json.dumps(x_meta),
                    "2026-08-14T00:00:00Z",
                    None,
                ),
                (
                    "wx_mp2rss",
                    "微信公众号（Mp2RSS 合集）",
                    paid_url,
                    "T2",
                    1,
                    "wechat",
                    safe_wechat_url,
                    None,
                    json.dumps({"collection": "legacy"}),
                    "2026-08-14T00:00:00Z",
                    safe_wechat_url,
                ),
            ],
        )

    response = TestClient(create_app(db_path)).get("/api/v1/sources")
    sources = {source["id"]: source for source in response.json()["data"]["sources"]}

    assert response.status_code == 200
    assert set(sources) == {"openai_blog", "x_openai", "wx_mp2rss"}
    assert sources["openai_blog"] == {
        "id": "openai_blog",
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog/rss.xml",
        "tier": "T1",
        "enabled": True,
        "kind": "feed",
        "homepage_url": "https://openai.com/",
        "icon_url": "https://example.com/openai.ico",
        "meta": {"legacy_feed_state": "visible"},
        "synced_at": "2026-05-11T00:00:00Z",
    }
    assert sources["x_openai"]["meta"] == {
        "adapter": "x_api",
        "username": "OpenAI",
        "etag": "legacy-etag",
        "legacy_public": "visible",
    }
    assert X_RUNTIME_META_KEYS.isdisjoint(sources["x_openai"]["meta"])
    assert sources["wx_mp2rss"]["kind"] == "wechat"
    assert sources["wx_mp2rss"]["url"] == safe_wechat_url
    assert sources["wx_mp2rss"]["homepage_url"] == safe_wechat_url
    assert paid_url not in response.text


def test_sources_v2_lists_unconfigured_optional_wechat_without_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MP2RSS_FEED_URL", raising=False)

    response = TestClient(create_app(_seed_db(tmp_path))).get("/api/v2/sources")
    payload = response.json()["data"]

    assert response.status_code == 200
    assert payload["optional_sources"] == [
        {
            "id": "wx_mp2rss",
            "name": "微信公众号（Mp2RSS 合集）",
            "scope": "wechat_only",
            "required_environment_variable": "MP2RSS_FEED_URL",
            "declared_in_contract": True,
            "runtime_configuration_status": "unavailable_missing_required_environment",
        }
    ]
    assert "MP2RSS_FEED_URL}" not in response.text


def test_sources_v2_does_not_treat_legacy_wechat_as_loaded_optional_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MP2RSS_FEED_URL", "https://paid.example.test/private-feed-token")
    db_path = _seed_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (
              id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wx_legacy",
                "Legacy WeChat",
                "https://legacy.example.test/feed",
                "T2",
                1,
                "wechat",
                "https://legacy.example.test/",
                None,
                "{}",
                "2026-08-13T00:00:00Z",
            ),
        )

    payload = TestClient(create_app(db_path)).get("/api/v2/sources").json()["data"]

    assert payload["optional_sources"][0]["runtime_configuration_status"] == "unavailable_not_loaded"


def test_wechat_public_override_projects_contract_through_config_db_and_v2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paid_url = "https://paid.example.test/private-feed-token"
    monkeypatch.setenv("MP2RSS_FEED_URL", paid_url)
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        sync_to_db(load_sources(Path(__file__).resolve().parents[1] / "data/sources.toml"), conn)

    response = TestClient(create_app(db_path)).get("/api/v2/sources")
    payload = response.json()["data"]
    wx = next(row for row in payload["sources"] if row["id"] == "wx_mp2rss")

    assert wx["retrieval_entrypoint_url"] is None
    assert wx["public_landing_url"] == "https://mp.weixin.qq.com/"
    assert payload["counts"] == {
        "declared_contract_source_count": 163,
        "declared_main_timeline_source_count": 161,
        "enabled_loaded_source_count": 162,
        "enabled_loaded_main_timeline_source_count": 161,
    }
    assert payload["optional_sources"][0]["runtime_configuration_status"] == "configured"
    assert paid_url not in response.text


def test_sources_api_does_not_expose_x_fetch_state_or_redundant_username(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "x_openai",
            "X: OpenAI (@OpenAI)",
            "https://api.x.com/2/users/by/username/OpenAI",
            "T1.5",
            1,
            "x",
            "https://x.com/OpenAI",
            "https://abs.twimg.com/favicons/twitter.ico",
            '{"adapter":"x_api","username":"OpenAI","etag":"internal",'
            '"last_modified":"internal","unknown_internal":"internal","x_reference_status":"verified",'
            '"x_reference_validated_at":"2026-08-12T14:00:00Z","x_cursor_state":"draining",'
            '"x_user_id":"42",'
            '"x_pagination_token":"page-2",'
            '"x_pending_since_id":"250","x_pending_start_time":"2026-08-12T13:40:00Z"}',
            "2026-08-12T14:00:00Z",
        ),
    )
    conn.commit()
    conn.close()
    client = TestClient(create_app(db_path))

    sources = client.get("/api/v2/sources").json()["data"]["sources"]
    source = next(item for item in sources if item["id"] == "x_openai")

    assert source["retrieval_validation"] == {
        "status": "verified",
        "label": "已验证",
        "scope": "x_timeline_retrieval",
        "trigger": "next_successful_x_timeline_fetch",
        "validated_at": "2026-08-12T14:00:00Z",
        "attempted_at": None,
        "reason": None,
        "recovery": None,
    }
    assert source["retrieval_entrypoint_url"] == "https://api.x.com/2/users/by/username/OpenAI"
    assert source["public_landing_url"] == "https://x.com/OpenAI"


def test_sources_api_preserves_legacy_meta_while_filtering_exact_runtime_keys(tmp_path: Path) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id='openai_blog'",
        (
            '{"etag":"legacy","last_modified":"legacy","username":"legacy",'
            '"x_profile":"legacy","x_since_id":"internal"}',
        ),
    )
    conn.commit()
    conn.close()

    source = TestClient(create_app(db_path)).get("/api/v1/sources").json()["data"]["sources"][0]

    assert source["meta"] == {
        "etag": "legacy",
        "last_modified": "legacy",
        "username": "legacy",
        "x_profile": "legacy",
        "x_since_id": "internal",
    }


@pytest.mark.parametrize(
    "meta_json",
    [
        '{"adapter":"x_api","username":"OpenAI"}',
        '{"adapter":"x_api","username":"OpenAI","x_reference_status":{"bad":true},'
        '"x_reference_validated_at":7}',
    ],
)
def test_sources_api_reports_missing_or_invalid_x_validation_state_as_unknown(
    tmp_path: Path,
    meta_json: str,
) -> None:
    db_path = _seed_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE sources SET kind='x', meta_json=? WHERE id='openai_blog'",
        (meta_json,),
    )
    conn.commit()
    conn.close()

    validation = TestClient(create_app(db_path)).get("/api/v2/sources").json()["data"]["sources"][0]["retrieval_validation"]

    assert validation == {
        "status": "unknown",
        "label": "状态未知",
        "scope": "x_timeline_retrieval",
        "trigger": None,
        "validated_at": None,
        "attempted_at": None,
        "reason": "internal_state_missing_or_invalid",
        "recovery": "operator_repair_required_before_x_timeline_fetch",
    }


def _seed_removed_source_item(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    source_kind: str,
    item_id: str,
    title: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        ) VALUES (?, ?, ?, 'T2', 1, ?, ?, NULL, '{}', '2026-08-12T00:00:00Z')
        """,
        (source_id, source_id, f"https://example.test/{source_id}", source_kind, "https://example.test/"),
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        ) VALUES (?, ?, ?, ?, 'fixture', '2026-08-12T12:00:00Z',
                  '2026-08-12T12:01:00Z', ?, NULL, ?, '{}')
        """,
        (
            item_id,
            source_id,
            f"https://example.test/articles/{item_id}",
            title,
            title,
            f"hash-{item_id}",
        ),
    )


def test_production_upgrade_disables_removed_sources_across_public_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MP2RSS_FEED_URL", raising=False)
    db_path = tmp_path / "upgrade.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        _seed_removed_source_item(
            conn,
            source_id="lilianweng",
            source_kind="feed",
            item_id="removed-precomputed",
            title="RemovedPrecomputedMarker",
        )
        _seed_removed_source_item(
            conn,
            source_id="importai",
            source_kind="feed",
            item_id="removed-computed",
            title="RemovedComputedMarker",
        )
        _seed_removed_source_item(
            conn,
            source_id="wx_legacy",
            source_kind="wechat",
            item_id="removed-wechat",
            title="RemovedWechatMarker",
        )
        conn.executemany(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold,
              input_eval_ids, output_curated_ids, created_at
            ) VALUES (?, 'fixture', '{}', 0, '[]', '[]', ?)
            """,
            [
                ("run-computed", "2026-08-12T12:00:00Z"),
                ("run-precomputed", "2026-08-12T13:00:00Z"),
            ],
        )
        conn.execute(
            """
            INSERT INTO curated_items (
              run_id, item_id, weighted_score, rank, reason_json, summary_json
            ) VALUES ('run-computed', 'removed-computed', 1, 1, '{}', NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO curated_items (
              run_id, item_id, weighted_score, rank, reason_json, summary_json
            ) VALUES ('run-precomputed', 'removed-precomputed', 1, 1, '{}', ?)
            """,
            ('{"id":"removed-precomputed","title":"RemovedPrecomputedMarker",'
             '"published_at":"2026-08-12T12:00:00Z","fetched_at":"2026-08-12T12:01:00Z"}',),
        )
        conn.execute(
            """
            INSERT INTO wechat_interpretations (
              item_id, slug, recommendation, save_decision, save_reason,
              abstract, tags_json, summary_md, model, kb_synced, processed_at, error
            ) VALUES (
              'removed-wechat', 'removed-wechat-slug', 'read', 1, 'fixture',
              'fixture abstract', '[]', 'fixture summary', 'fixture', 0,
              '2026-08-12T12:02:00Z', NULL
            )
            """
        )
        conn.commit()

    before = TestClient(create_app(db_path))
    assert {row["id"] for row in before.get("/api/v1/sources").json()["data"]["sources"]} >= {
        "lilianweng",
        "importai",
        "wx_legacy",
    }
    assert before.get("/api/v1/timeline", params={"q": "RemovedComputedMarker"}).json()["data"]["total"] == 1
    assert before.get("/api/v1/items/removed-computed").status_code == 200
    assert before.get("/api/v1/curated").json()["data"]["items"][0]["id"] == "removed-precomputed"
    assert before.get("/api/v1/curated", params={"run_id": "run-computed"}).json()["data"]["items"][0]["id"] == "removed-computed"
    assert before.get("/api/v1/wechat").json()["data"]["items"][0]["slug"] == "removed-wechat-slug"
    assert before.get("/wechat/removed-wechat-slug").status_code == 200

    sources = load_sources(Path(__file__).resolve().parents[1] / "data" / "sources.toml")
    assert len(sources) == 161
    with sqlite3.connect(db_path) as conn:
        sync_to_db(sources, conn)

    after = TestClient(create_app(db_path))
    public_source_ids = {row["id"] for row in after.get("/api/v2/sources").json()["data"]["sources"]}
    assert len(public_source_ids) == 161
    assert public_source_ids.isdisjoint({"lilianweng", "importai", "wx_legacy"})
    assert after.get("/api/v1/timeline", params={"q": "RemovedComputedMarker"}).json()["data"]["total"] == 0
    assert after.get("/api/v1/items/removed-computed").status_code == 404
    assert after.get("/api/v1/curated").json()["data"]["items"] == []
    assert after.get("/api/v1/curated", params={"run_id": "run-computed"}).json()["data"]["items"] == []
    assert after.get("/api/v1/wechat").json()["data"]["items"] == []
    assert after.get("/wechat/removed-wechat-slug").status_code == 404

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM sources WHERE id IN ('lilianweng', 'importai', 'wx_legacy') AND enabled=0"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM items WHERE id IN ('removed-precomputed', 'removed-computed', 'removed-wechat')"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM curated_items WHERE item_id IN ('removed-precomputed', 'removed-computed')"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM wechat_interpretations WHERE item_id='removed-wechat'"
        ).fetchone()[0] == 1
