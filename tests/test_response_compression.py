from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from airadar.web.app import create_app


def test_large_public_responses_are_gzipped_at_origin(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "radar.db"))

    response = client.get("/about", headers={"accept-encoding": "gzip"})

    assert response.status_code == 200
    assert len(response.content) > 1024
    assert response.headers["content-encoding"] == "gzip"


def test_small_healthz_response_is_not_gzipped(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "radar.db"))

    response = client.get("/api/v1/healthz", headers={"accept-encoding": "gzip"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
