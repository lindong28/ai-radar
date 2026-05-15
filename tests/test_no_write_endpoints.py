from __future__ import annotations

from airadar.web.app import create_app


def test_business_routes_are_read_only() -> None:
    app = create_app()
    allowed = {"GET", "HEAD", "OPTIONS"}
    business_roots = {"/", "/all", "/daily", "/about", "/curated.html"}

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", set()) or set())
        if not methods:
            continue
        if path.startswith("/api/v1/") or path in business_roots:
            assert methods <= allowed, f"{path} exposes write-like methods: {sorted(methods)}"
