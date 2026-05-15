from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import db
from .cors import configure_cors
from .routes import curated, health, items, sources, timeline

STATIC_DIR = db.PROJECT_ROOT / "web" / "static"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    db.migrate(db_path)
    app = FastAPI(title="AI Radar", version="0.1.0")
    app.state.db_path = str(db.resolve_db_path(db_path))
    configure_cors(app)
    api_prefix = "/api/v1"

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> HTMLResponse | JSONResponse:
        if exc.status_code != 404 or request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return HTMLResponse(
            """
            <!doctype html>
            <html lang="zh-CN">
              <head><meta charset="utf-8"><title>404 · AI Radar</title></head>
              <body><main><h1>404</h1><p>页面不存在</p></main></body>
            </html>
            """,
            status_code=404,
        )

    app.include_router(health.router, prefix=api_prefix)
    app.include_router(timeline.router, prefix=api_prefix)
    app.include_router(curated.router, prefix=api_prefix)
    app.include_router(items.router, prefix=api_prefix)
    app.include_router(sources.router, prefix=api_prefix)

    @app.get("/all", include_in_schema=False)
    def all_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "all.html")

    @app.get("/daily", include_in_schema=False)
    def daily_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "daily.html")

    @app.get("/daily/{daily_date}", include_in_schema=False)
    def dated_daily_page(daily_date: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "daily.html")

    @app.get("/about", include_in_schema=False)
    def about_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "about.html")

    @app.get("/curated.html", include_in_schema=False)
    def curated_redirect() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=308)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()


def serve(port: int = 8000, host: str = "127.0.0.1") -> None:
    uvicorn.run(create_app(), host=host, port=port)
