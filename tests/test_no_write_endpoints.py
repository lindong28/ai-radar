from __future__ import annotations

from pathlib import Path

import pytest

from airadar.db import migrate
from airadar.web.app import create_app


def test_business_routes_are_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))

    app = create_app()
    assert Path(app.state.db_path) == db_path

    allowed = {"GET", "HEAD", "OPTIONS"}
    business_roots = {"/", "/all", "/daily", "/about", "/curated.html"}

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", set()) or set())
        if not methods:
            continue
        if path.startswith("/api/v1/") or path in business_roots:
            assert methods <= allowed, f"{path} exposes write-like methods: {sorted(methods)}"
