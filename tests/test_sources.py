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
