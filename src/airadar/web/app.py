from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import db
from .cors import configure_cors
from .routes import curated, health, items, sources, timeline

STATIC_DIR = db.PROJECT_ROOT / "web" / "static"
TEMPLATES_DIR = db.PROJECT_ROOT / "web" / "templates"
PRELOAD_ITEM_KEYS = {
    "id",
    "source_id",
    "source_name",
    "source_kind",
    "source_homepage_url",
    "source_icon_url",
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
SHANGHAI_TZ = timezone(timedelta(hours=8))
PREPAINT_ITEM_LIMIT = 12


def _compact_preload(data: dict[str, object]) -> dict[str, object]:
    compact = dict(data)
    items = data.get("items")
    if not isinstance(items, list):
        return compact

    compact_items: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        compact_item = {
            key: value
            for key, value in item.items()
            if key in PRELOAD_ITEM_KEYS and value not in (None, [], {})
        }
        if compact_item.get("summary_zh"):
            compact_item.pop("content_preview", None)
        compact_items.append(compact_item)
    compact["items"] = compact_items
    return compact


def _parse_item_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(SHANGHAI_TZ)


def _score_tier(score: int) -> str:
    if score >= 80:
        return "score-high"
    if score >= 65:
        return "score-mid"
    return "score-muted"


def _prepaint_items(items: object, *, timeline_page: bool) -> list[dict[str, object]]:
    if not isinstance(items, list):
        return []

    prepaint: list[dict[str, object]] = []
    for raw_item in items[:PREPAINT_ITEM_LIMIT]:
        if not isinstance(raw_item, dict):
            continue
        source_kind = str(raw_item.get("source_kind") or "")
        source_name = str(raw_item.get("source_name") or raw_item.get("source_id") or "")
        title = str(
            raw_item.get("title_zh")
            or raw_item.get("title")
            or raw_item.get("summary_zh")
            or raw_item.get("content_preview")
            or ""
        )
        summary = str(
            raw_item.get("summary_zh")
            or raw_item.get("content_preview")
            or raw_item.get("content_text")
            or ""
        )
        tags = raw_item.get("enriched_tags") or raw_item.get("topic_tags") or []
        if not isinstance(tags, list) or not tags:
            tags = ["社交" if source_kind == "x" else "AI"]
        timestamp = raw_item.get("published_at") or raw_item.get("fetched_at")
        dt = _parse_item_datetime(timestamp)
        score_value = raw_item.get("weighted_score")
        score = round(float(score_value) * 10) if isinstance(score_value, int | float) else None
        selected = raw_item.get("rank") is not None
        show_reason = (selected if timeline_page else True) and bool(raw_item.get("reasoning"))
        prepaint.append(
            {
                "source_id": raw_item.get("source_id") or "",
                "source_name": source_name,
                "source_homepage_url": raw_item.get("source_homepage_url") or raw_item.get("url") or "#",
                "source_icon_url": raw_item.get("source_icon_url") or "",
                "source_initial": (source_name or "?").strip()[:1].upper() or "?",
                "is_x": source_kind == "x",
                "title": title,
                "url": str(raw_item.get("url") or "#").split("#", 1)[0],
                "summary": summary,
                "tags": [str(tag) for tag in tags[:4]],
                "score": score,
                "score_tier": _score_tier(score or 0),
                "selected": selected,
                "reasoning": raw_item.get("reasoning") if show_reason else "",
                "date_bucket": dt.strftime("%Y-%m-%d") if dt else "",
                "date_label": f"{dt.month}月{dt.day}日" if dt else "",
                "time_label": dt.strftime("%H:%M") if dt else "",
                "iso_datetime": dt.isoformat() if dt else "",
                "clamp_summary": timeline_page,
            }
        )
    return prepaint


def _preload_context(data: dict[str, object], *, timeline_page: bool) -> dict[str, object]:
    preload = _compact_preload(data)
    return {
        "preload": preload,
        "prepaint_items": _prepaint_items(preload.get("items"), timeline_page=timeline_page),
    }


def create_app(db_path: str | Path | None = None) -> FastAPI:
    db.migrate(db_path)
    app = FastAPI(title="AI Radar", version="0.1.0")
    app.state.db_path = str(db.resolve_db_path(db_path))
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False}
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

    @app.get("/", include_in_schema=False)
    def index_page(request: Request, category: str | None = None, q: str | None = None) -> HTMLResponse:
        payload = curated.curated(request, category=category, q=q)
        return templates.TemplateResponse(request, "index.html", _preload_context(payload["data"], timeline_page=False))

    @app.get("/all", include_in_schema=False)
    def all_page(
        request: Request,
        cursor: str | None = None,
        limit: int = 40,
        page: int = 1,
        channel: str | None = None,
        category: str | None = None,
        q: str | None = None,
    ) -> HTMLResponse:
        payload = timeline.timeline(
            request,
            cursor=cursor,
            limit=limit,
            page=page,
            channel=channel,
            category=category,
            q=q,
        )
        return templates.TemplateResponse(request, "all.html", _preload_context(payload["data"], timeline_page=True))


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
