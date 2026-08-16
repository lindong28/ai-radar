from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from airadar.db import migrate
from airadar.sources.loader import SourceConfig, load_sources
from airadar.sources.sync import load_enabled_sources_from_db, sync_to_db


def test_load_sources_validates_toml(tmp_path: Path) -> None:
    path = tmp_path / "sources.toml"
    path.write_text(
        """
[[source]]
slug = "openai_blog"
name = "OpenAI Blog"
url = "https://openai.com/blog/rss.xml"
tier = "T1"
enabled = true
""".strip(),
        encoding="utf-8",
    )

    assert load_sources(path) == [
        SourceConfig(
            slug="openai_blog",
            name="OpenAI Blog",
            url="https://openai.com/blog/rss.xml",
            tier="T1",
            enabled=True,
            meta={},
        )
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slug", "bad slug"),
        ("url", "ftp://example.com/feed"),
        ("tier", "T3"),
    ],
)
def test_load_sources_rejects_invalid_values(tmp_path: Path, field: str, value: str) -> None:
    data = {
        "slug": "valid_slug",
        "name": "Example",
        "url": "https://example.com/feed",
        "tier": "T2",
        "enabled": "true",
    }
    data[field] = value
    path = tmp_path / "sources.toml"
    path.write_text(
        "\n".join(
            [
                "[[source]]",
                f'slug = "{data["slug"]}"',
                f'name = "{data["name"]}"',
                f'url = "{data["url"]}"',
                f'tier = "{data["tier"]}"',
                f"enabled = {data['enabled']}",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_sources(path)


def test_load_sources_rejects_duplicate_slug(tmp_path: Path) -> None:
    path = tmp_path / "sources.toml"
    path.write_text(
        """
[[source]]
slug = "dup"
name = "One"
url = "https://example.com/one.xml"
tier = "T2"
enabled = true

[[source]]
slug = "dup"
name = "Two"
url = "https://example.com/two.xml"
tier = "T2"
enabled = true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_sources(path)


def test_sync_to_db_overwrites_source_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)

    sync_to_db(
        [
            SourceConfig(
                slug="simonw",
                name="Simon",
                url="https://example.com/old.xml",
                tier="T1.5",
                enabled=True,
                meta={"etag": "abc"},
            ),
        ],
        conn,
    )
    sync_to_db(
        [
            SourceConfig(
                slug="simonw",
                name="Simon New",
                url="https://example.com/new.xml",
                tier="T2",
                enabled=False,
                meta={},
            ),
        ],
        conn,
    )

    row = conn.execute("SELECT name, url, tier, enabled, meta_json FROM sources WHERE id='simonw'").fetchone()
    assert row == ("Simon New", "https://example.com/new.xml", "T2", 0, "{}")
    assert load_enabled_sources_from_db(conn) == []


def test_sync_to_db_preserves_x_runtime_cursor_but_not_other_stale_meta(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    source = SourceConfig(
        slug="x_openai",
        name="X OpenAI",
        url="https://api.x.com/2/users/by/username/OpenAI",
        tier="T1",
        kind="x",
        meta={"adapter": "x_api", "username": "OpenAI"},
    )
    sync_to_db([source], conn)
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=?",
        (
            '{"adapter":"x_api","username":"OpenAI","x_reference_status":"verified",'
            '"x_reference_validated_at":"2026-08-12T14:00:00Z","x_cursor_state":"checkpointed",'
            '"x_user_id":"42","x_since_id":"200","stale":"drop"}',
            source.slug,
        ),
    )
    conn.commit()

    sync_to_db([source], conn)

    loaded = load_enabled_sources_from_db(conn)[0]
    assert loaded.meta == {
        "adapter": "x_api",
        "username": "OpenAI",
        "x_reference_status": "verified",
        "x_reference_validated_at": "2026-08-12T14:00:00Z",
        "x_cursor_state": "checkpointed",
        "x_user_id": "42",
        "x_since_id": "200",
        "x_state_schema_version": 1,
    }


def test_sync_to_db_clears_x_runtime_cursor_when_username_changes(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    old_source = SourceConfig(
        slug="x_account",
        name="Old",
        url="https://api.x.com/2/users/by/username/OldAccount",
        tier="T1",
        kind="x",
        meta={"adapter": "x_api", "username": "OldAccount"},
    )
    sync_to_db([old_source], conn)
    conn.execute(
        "UPDATE sources SET meta_json=? WHERE id=?",
        (
            '{"adapter":"x_api","username":"OldAccount","x_reference_status":"verified",'
            '"x_reference_validated_at":"2026-08-12T14:00:00Z","x_cursor_state":"draining",'
            '"x_user_id":"42","x_since_id":"200","x_pending_since_id":"250",'
            '"x_pagination_token":"page-2"}',
            old_source.slug,
        ),
    )
    conn.commit()
    new_source = SourceConfig(
        slug="x_account",
        name="New",
        url="https://api.x.com/2/users/by/username/NewAccount",
        tier="T1",
        kind="x",
        meta={"adapter": "x_api", "username": "NewAccount"},
    )

    sync_to_db([new_source], conn)

    assert load_enabled_sources_from_db(conn)[0].meta == {
        "adapter": "x_api",
        "username": "NewAccount",
        "x_cursor_state": "identity_pending",
        "x_reference_status": "pending",
        "x_state_schema_version": 1,
    }


def test_sync_to_db_rejects_missing_or_malformed_existing_x_state(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    source = SourceConfig(
        slug="x_openai",
        name="X OpenAI",
        url="https://api.x.com/2/users/by/username/OpenAI",
        tier="T1",
        kind="x",
        meta={"adapter": "x_api", "username": "OpenAI"},
    )
    sync_to_db([source], conn)

    for broken in ('{"adapter":"x_api","username":"OpenAI"}', "{broken"):
        conn.execute("UPDATE sources SET meta_json=? WHERE id=?", (broken, source.slug))
        conn.commit()

        with pytest.raises(ValueError, match="invalid X"):
            sync_to_db([source], conn)

        assert conn.execute("SELECT meta_json FROM sources WHERE id=?", (source.slug,)).fetchone()[0] == broken


@pytest.mark.parametrize(
    "broken",
    [
        '{"username":"OpenAI","x_cursor_state":"checkpointed","x_since_id":"200"}',
        '{"adapter":"x_api","x_cursor_state":"checkpointed","x_since_id":"200"}',
    ],
)
def test_sync_to_db_rejects_orphaned_existing_x_state(tmp_path: Path, broken: str) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    source = SourceConfig(
        slug="x_openai",
        name="X OpenAI",
        url="https://api.x.com/2/users/by/username/OpenAI",
        tier="T1",
        kind="x",
        meta={"adapter": "x_api", "username": "OpenAI"},
    )
    sync_to_db([source], conn)
    conn.execute("UPDATE sources SET meta_json=? WHERE id=?", (broken, source.slug))
    conn.commit()

    with pytest.raises(ValueError, match="runtime state has no owner"):
        sync_to_db([source], conn)

    assert conn.execute("SELECT meta_json FROM sources WHERE id=?", (source.slug,)).fetchone()[0] == broken


@pytest.mark.parametrize(
    "runtime",
    [
        {
            "x_cursor_state": "draining",
            "x_since_time": "2026-08-12T14:00:00Z",
            "x_pending_start_time": "2026-08-12T14:00:00Z",
            "x_pending_since_id": "250",
            "x_pagination_token": "page-2",
        },
        {
            "x_cursor_state": "draining",
            "x_since_id": "200",
            "x_pending_start_time": "2026-08-12T13:40:00Z",
            "x_pending_since_id": "250",
            "x_pagination_token": "page-2",
        },
        {
            "x_cursor_state": "draining",
            "x_since_id": "200",
            "x_pending_since_id": "150",
            "x_pagination_token": "page-2",
        },
    ],
)
def test_sync_to_db_rejects_incoherent_existing_x_state(
    tmp_path: Path,
    runtime: dict[str, str],
) -> None:
    import json

    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    source = SourceConfig(
        slug="x_openai",
        name="X OpenAI",
        url="https://api.x.com/2/users/by/username/OpenAI",
        tier="T1",
        kind="x",
        meta={"adapter": "x_api", "username": "OpenAI"},
    )
    sync_to_db([source], conn)
    broken = json.dumps(
        {
            **source.meta,
            "x_reference_status": "verified",
            "x_reference_validated_at": "2026-08-12T14:00:00Z",
            "x_user_id": "42",
            **runtime,
        }
    )
    conn.execute("UPDATE sources SET meta_json=? WHERE id=?", (broken, source.slug))
    conn.commit()

    with pytest.raises(ValueError, match="invalid X runtime state"):
        sync_to_db([source], conn)


@pytest.mark.parametrize(
    "meta_lines",
    [
        ['adapter = "x_api"'],
        ['adapter = "x_api"', 'username = "bad/name"'],
        ['adapter = "x_api"', 'username = "OpenAI"', 'x_since_id = "123"'],
    ],
)
def test_load_sources_rejects_invalid_x_api_configuration(
    tmp_path: Path,
    meta_lines: list[str],
) -> None:
    path = tmp_path / "sources.toml"
    path.write_text(
        "\n".join(
            [
                "schema_version = 2",
                "[[source]]",
                'slug = "x_openai"',
                'name = "X OpenAI"',
                'fetch_url = "https://api.x.com/2/users/by/username/OpenAI"',
                'homepage_url = "https://x.com/OpenAI"',
                'tier = "T1"',
                'kind = "x"',
                "[source.meta]",
                *meta_lines,
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="x_api"):
        load_sources(path)


def test_load_sources_rejects_x_api_identity_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "sources.toml"
    path.write_text(
        """
schema_version = 2
[[source]]
slug = "x_openai"
name = "X OpenAI"
fetch_url = "https://api.x.com/2/users/by/username/AnthropicAI"
homepage_url = "https://x.com/OpenAI"
tier = "T1"
kind = "x"
[source.meta]
adapter = "x_api"
username = "OpenAI"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity"):
        load_sources(path)
