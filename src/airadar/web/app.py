from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter_ns
from typing import cast
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from uvicorn.config import LOGGING_CONFIG

from .. import db
from ..site_config import get_site_config
from .cors import configure_cors
from .routes import admin, curated, health, items, media, sources, timeline
from .routes import wechat as wechat_routes
from .routes.request_db import conn_from_request
from .schemas import FeedItem

STATIC_DIR = db.PROJECT_ROOT / "web" / "static"
TEMPLATES_DIR = db.PROJECT_ROOT / "web" / "templates"
PRELOAD_ITEM_KEYS = set(FeedItem.model_fields)
PRE_MIGRATED_DB_ENV = "AI_RADAR_PRE_MIGRATED_DB"
PRELOAD_ITEM_KEYS.difference_update(
    name
    for name, field in FeedItem.model_fields.items()
    if isinstance(field.json_schema_extra, dict) and field.json_schema_extra.get("preload") is False
)
SHANGHAI_TZ = timezone(timedelta(hours=8))
PREPAINT_ITEM_LIMIT = 12
WECHAT_FALLBACK_ICON = "/wechat-icon.svg?v=20260601"
WECHAT_PAGE_LIMIT = 50
PUBLIC_PAGINATION_CACHE_CONTROL = "public, max-age=90, stale-while-revalidate=30"
PRIVATE_CACHE_CONTROL = "private, no-store"
_PUBLIC_PAGINATION_QUERY_KEYS = {
    "/": frozenset({"page"}),
    "/wechat": frozenset({"page"}),
    "/api/v1/curated": frozenset({"page", "limit"}),
    "/api/v1/wechat": frozenset({"page", "limit"}),
}


def _public_pagination_cache_control(request: Request, status_code: int) -> str | None:
    allowed_query_keys = _PUBLIC_PAGINATION_QUERY_KEYS.get(request.url.path)
    if allowed_query_keys is None or request.method not in {"GET", "HEAD"}:
        return None
    if status_code != 200 or not set(request.query_params).issubset(allowed_query_keys):
        return PRIVATE_CACHE_CONTROL
    return PUBLIC_PAGINATION_CACHE_CONTROL


def _uvicorn_log_config() -> dict[str, object]:
    config = deepcopy(LOGGING_CONFIG)
    formatters = config.setdefault("formatters", {})
    access_formatter = formatters.setdefault("access", {})
    if isinstance(access_formatter, dict):
        access_formatter["fmt"] = (
            '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
        )
        access_formatter["datefmt"] = "%Y-%m-%dT%H:%M:%S%z"
    return config


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


def _display_source_name(raw_item: dict[str, object], source_kind: str, source_name: str) -> str:
    if source_kind == "wechat":
        return str(raw_item.get("author") or source_name or raw_item.get("source_id") or "")
    return source_name


def _display_source_icon_url(raw_item: dict[str, object], source_kind: str) -> str:
    if source_kind == "wechat":
        return str(raw_item.get("author_avatar_url") or WECHAT_FALLBACK_ICON)
    return str(raw_item.get("source_icon_url") or "")


def _prepaint_items(items: object, *, timeline_page: bool) -> list[dict[str, object]]:
    if not isinstance(items, list):
        return []

    prepaint: list[dict[str, object]] = []
    for raw_item in items[:PREPAINT_ITEM_LIMIT]:
        if not isinstance(raw_item, dict):
            continue
        source_kind = str(raw_item.get("source_kind") or "")
        source_name = str(raw_item.get("source_name") or raw_item.get("source_id") or "")
        display_source_name = _display_source_name(raw_item, source_kind, source_name)
        display_source_icon_url = _display_source_icon_url(raw_item, source_kind)
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
                "item_id": raw_item.get("id") or "",
                "source_id": raw_item.get("source_id") or "",
                "source_name": display_source_name,
                "source_homepage_url": raw_item.get("source_homepage_url") or raw_item.get("url") or "#",
                "source_icon_url": display_source_icon_url,
                "source_initial": (display_source_name or "?").strip()[:1].upper() or "?",
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


def _wechat_back_href(page: int | None, q: str | None = None) -> str:
    params: list[tuple[str, object]] = []
    query = (q or "").strip()
    if query:
        params.append(("q", query))
    if page and page > 1:
        params.append(("page", page))
    return f"/wechat?{urlencode(params)}" if params else "/wechat"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    if os.environ.get(PRE_MIGRATED_DB_ENV) != "1":
        db.migrate(db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeline.prewarm_timeline_total_cache(app.state.db_path)
        curated.prewarm_curated_archive_total_cache(app.state.db_path)
        yield

    app = FastAPI(title="AI Radar", version="0.1.0", lifespan=lifespan)
    app.state.db_path = str(db.resolve_db_path(db_path))
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.policies["json.dumps_kwargs"] = {"ensure_ascii": False}
    configure_cors(app)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    api_prefix = "/api/v1"

    @app.middleware("http")
    async def public_path_timing(request: Request, call_next):  # noqa: ANN001
        started = perf_counter_ns()
        response = await call_next(request)
        duration_ms = (perf_counter_ns() - started) / 1_000_000
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.3f}"
        cache_control = _public_pagination_cache_control(request, response.status_code)
        if cache_control is not None:
            response.headers["Cache-Control"] = cache_control
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> HTMLResponse | JSONResponse:
        if exc.status_code != 404 or request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if request.url.path.startswith("/wechat/"):
            page_raw = request.query_params.get("page")
            q = request.query_params.get("q")
            try:
                page = int(page_raw) if page_raw else None
            except ValueError:
                page = None
            return templates.TemplateResponse(
                request,
                "wechat_404.html",
                {"back_href": _wechat_back_href(page, q)},
                status_code=404,
            )
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
    app.include_router(admin.router, prefix=api_prefix)
    app.include_router(wechat_routes.router, prefix=api_prefix)
    # Image proxy lives at the root (frontend references /img?url=), not under
    # the API prefix. Registered before the "/" static mount so it wins.
    app.include_router(media.router)

    @app.get("/", include_in_schema=False)
    def index_page(
        request: Request,
        category: str | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 40,
    ) -> HTMLResponse:
        payload = curated.curated(request, category=category, q=q, page=page, limit=limit)
        return templates.TemplateResponse(
            request,
            "index.html",
            _preload_context(cast(dict[str, object], payload["data"]), timeline_page=False),
        )

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
        return templates.TemplateResponse(
            request,
            "all.html",
            _preload_context(cast(dict[str, object], payload["data"]), timeline_page=True),
        )

    @app.get("/wechat", include_in_schema=False)
    def wechat_page(request: Request, q: str | None = None, page: int = 1, limit: int = WECHAT_PAGE_LIMIT) -> HTMLResponse:
        with conn_from_request(request) as conn:
            data = wechat_routes.list_wechat_items(conn, q=q, page=page, limit=limit)
        return templates.TemplateResponse(
            request,
            "wechat.html",
            {"preload": data, "items": data["items"]},
        )

    @app.get("/wechat/{slug}", include_in_schema=False)
    def wechat_detail_page(
        request: Request,
        slug: str,
        q: str | None = None,
        page: int | None = None,
    ) -> HTMLResponse:
        with conn_from_request(request) as conn:
            item = wechat_routes.get_wechat_detail(conn, slug)
        return templates.TemplateResponse(
            request,
            "wechat_detail.html",
            {"item": item, "back_href": _wechat_back_href(page, q)},
        )

    @app.get("/admin", include_in_schema=False)
    def admin_page(request: Request) -> HTMLResponse:
        admin.require_admin_access(request)
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "metrics": admin.collect_admin_metrics(request),
                "performance": admin.collect_performance_status(),
            },
        )

    @app.head("/admin", include_in_schema=False)
    def admin_head(request: Request) -> Response:
        admin.require_admin_access(request)
        return Response(status_code=204)

    @app.get("/admin/usage", include_in_schema=False)
    def admin_usage_page(request: Request) -> HTMLResponse:
        admin.require_admin_access(request)
        return templates.TemplateResponse(
            request,
            "admin_usage.html",
            {"usage": admin.collect_admin_usage(request)},
        )

    @app.get("/daily", include_in_schema=False)
    def daily_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "daily.html")

    @app.get("/daily/{daily_date}", include_in_schema=False)
    def dated_daily_page(daily_date: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "daily.html")

    @app.get("/about", include_in_schema=False)
    def about_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "about.html",
            {"site": get_site_config()},
        )

    @app.get("/about.html", include_in_schema=False)
    def about_html_redirect() -> RedirectResponse:
        return RedirectResponse(url="/about", status_code=308)

    @app.get("/curated.html", include_in_schema=False)
    def curated_redirect() -> RedirectResponse:
        return RedirectResponse(url="/", status_code=308)

    @app.get("/curated", include_in_schema=False)
    def curated_alias(
        request: Request,
        category: str | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 40,
    ) -> HTMLResponse:
        return index_page(request, category=category, q=q, page=page, limit=limit)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def serve(port: int = 8000, host: str = "127.0.0.1") -> None:
    uvicorn.run(create_app(), host=host, port=port, log_config=_uvicorn_log_config())
