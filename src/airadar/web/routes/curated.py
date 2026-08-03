from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_cls

from fastapi import APIRouter, HTTPException, Query, Request

from ..envelope import ok
from . import curated_archive, curated_digest
from .categories import CATEGORY_TAGS
from .request_db import conn_from_request

router = APIRouter()
SHANGHAI_TZ = timezone(timedelta(hours=8))

prewarm_curated_archive_total_cache = curated_archive.prewarm_curated_archive_total_cache


def _normalized_date(value: str | None) -> str | None:
    if value is None:
        return None
    today = datetime.now(SHANGHAI_TZ).date()
    try:
        parsed = date_cls.fromisoformat(value)
    except ValueError:
        return today.isoformat()
    if parsed > today:
        return today.isoformat()
    return parsed.isoformat()


@router.get("/curated/daily-archive")
def daily_archive(request: Request) -> dict[str, object]:
    with conn_from_request(request) as conn:
        days = curated_archive._compute_daily_archive(conn)
    return ok({"days": days, "count": len(days)})


def _hot_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@router.get("/curated")
def curated(
    request: Request,
    run_id: str | None = None,
    date: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = Query(default=40, ge=1, le=100),
    page: int = Query(default=1, ge=1),
) -> dict[str, object]:
    selected_date = _normalized_date(date)
    normalized_category = category if category in CATEGORY_TAGS else None
    with conn_from_request(request) as conn:
        if run_id:
            run = conn.execute("SELECT * FROM curation_runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise HTTPException(status_code=404, detail="curation run not found")
            items = curated_digest.compute_digest_items(
                conn,
                run,
                selected_date=selected_date,
                normalized_category=normalized_category,
                q=q,
            )
            response_date = selected_date or str(run["created_at"])[:10]
            return ok(
                {
                    "run_id": run["id"],
                    "ruleset_version": run["ruleset_version"],
                    "items": items,
                    "date": response_date,
                    "count": len(items),
                }
            )

        if selected_date:
            run = curated_archive._latest_run(conn)
            items = curated_archive._compute_archive_for_date(
                conn,
                selected_date=selected_date,
                normalized_category=normalized_category,
                q=q,
            )
            return ok(
                {
                    "run_id": run["id"] if run is not None else None,
                    "ruleset_version": run["ruleset_version"] if run is not None else None,
                    "items": items,
                    "date": selected_date,
                    "count": len(items),
                }
            )

        run = curated_archive._latest_run(conn)
        if run is None:
            return ok(
                {
                    "run_id": None,
                    "ruleset_version": None,
                    "items": [],
                    "date": None,
                    "count": 0,
                    "total": 0,
                    "page": 1,
                    "limit": limit,
                }
            )
        items, total, response_page = curated_archive._compute_archive_page(
            conn,
            page=page,
            limit=limit,
            normalized_category=normalized_category,
            q=q,
        )
        response_date = curated_archive._archive_response_date(conn, items) or str(run["created_at"])[:10]
    return ok(
        {
            "run_id": run["id"],
            "ruleset_version": run["ruleset_version"],
            "items": items,
            "date": response_date,
            "count": len(items),
            "total": total,
            "page": response_page,
            "limit": limit,
        }
    )

@router.get("/hot")
def hot(
    request: Request,
    limit: int = Query(default=5, ge=1, le=10),
    hours: int = Query(default=48, ge=6, le=168),
) -> dict[str, object]:
    """近 N 小时内按热度排序的头条：热度 = 加权分×10 + 关联讨论数×5。"""
    generated_at = datetime.now(UTC)
    # 单次调用取一致快照（跨页会因采集管线并发写入产生 offset 漂移）；
    # 600 = 48h 现实归档量（约 160 条）的近 4 倍富余，超出即截断属可接受近似
    with conn_from_request(request) as conn:
        items, _total, _page = curated_archive._compute_archive_page(
            conn,
            page=1,
            limit=600,
            normalized_category=None,
            q=None,
        )
    ranked: list[dict[str, object]] = []
    for item in items:
        published_at = item.get("published_at")
        fetched_at = item.get("fetched_at")
        published_ts = _hot_datetime(published_at)
        use_published = published_ts is not None and published_ts <= generated_at
        event_time = published_at if use_published else fetched_at
        event_ts = published_ts if use_published else _hot_datetime(fetched_at)
        if event_ts is None:
            continue
        age_seconds = (generated_at - event_ts).total_seconds()
        if age_seconds < 0 or age_seconds > hours * 3600:
            continue
        score = float(str(item.get("weighted_score") or 0.0))
        related = item.get("related_discussions")
        related_discussions = related if isinstance(related, list) else []
        related_count = len(related_discussions)
        heat = round(score * 10 + related_count * 5)
        ranked.append(
            {
                "id": item.get("id"),
                "title": item.get("title_zh") or item.get("title"),
                "url": item.get("url"),
                "source_name": item.get("source_name"),
                "published_at": published_at,
                "fetched_at": fetched_at,
                "event_time": event_time,
                "source_kind": item.get("source_kind"),
                "author": item.get("author"),
                "related_discussions": related_discussions,
                "heat": heat,
            }
        )
    ranked.sort(key=lambda entry: (-int(str(entry["heat"] or 0)), str(entry["id"])))
    return ok(
        {
            "items": ranked[:limit],
            "hours": hours,
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        }
    )
