from __future__ import annotations

import sqlite3
from pathlib import Path

from airadar.db import migrate
from airadar.web.app import create_app
from fastapi.testclient import TestClient


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


def test_sources_api_supplies_about_table_fields(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    source = client.get("/api/v1/sources").json()["data"]["sources"][0]

    assert source["id"] == "openai_blog"
    assert source["name"] == "OpenAI Blog"
    assert source["tier"] == "T1"
    assert source["enabled"] is True
