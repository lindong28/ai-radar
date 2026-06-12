from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.db import migrate
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
    assert "lindong28" not in response.text
    assert "aiplanet.live" not in response.text


def test_about_page_uses_owner_site_identity_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_RADAR_SITE_REPO_URL", "https://github.com/lindong28/ai-radar")
    monkeypatch.setenv("AI_RADAR_SITE_MAINTAINER", "lindong")
    monkeypatch.setenv("AI_RADAR_SITE_MAINTAINER_URL", "https://github.com/lindong28")
    monkeypatch.setenv("AI_RADAR_SITE_X_URL", "https://x.com/lindong28")
    client = TestClient(create_app(_seed_db(tmp_path)))

    response = client.get("/about")

    assert response.status_code == 200
    assert "lindong" in response.text
    assert "https://github.com/lindong28/ai-radar" in response.text
    assert "https://x.com/lindong28" in response.text


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

    assert "lindong28" not in response.text


def test_sources_api_supplies_about_table_fields(tmp_path: Path) -> None:
    client = TestClient(create_app(_seed_db(tmp_path)))

    source = client.get("/api/v1/sources").json()["data"]["sources"][0]

    assert source["id"] == "openai_blog"
    assert source["name"] == "OpenAI Blog"
    assert source["tier"] == "T1"
    assert source["enabled"] is True
