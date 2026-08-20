from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from datetime import date as date_cls

from fastapi import APIRouter, HTTPException, Query, Request

from ..envelope import ok
from . import curated_archive, curated_digest, daily_metrics, hot_cache
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
                    "daily_metrics": daily_metrics.compute(items),
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


def hot_payload(*, limit: int, hours: int) -> dict[str, object] | None:
    """近 N 小时内按热度排序的头条：热度 = 加权分×10 + 关联讨论数×5。

    ``None`` 表示**候选缓存尚未就绪**，与"确实没有热点"（返回 items 为空的
    payload）是两回事——调用方必须把两者区分开，理由见 ADR-060：把未就绪
    编码成空结果会被公共缓存放大、前端不会重试、`/hot` 还会渲染出假话。

    排序、切片与 ``generated_at`` 全部按**本次调用的时钟**现算，缓存只提供
    候选集。
    """
    generated_at = datetime.now(UTC)
    candidates = hot_cache.HOT_CANDIDATE_CACHE.peek()
    if candidates is None:
        return None
    return ok(
        {
            "items": hot_cache.rank_hot_items(
                candidates, now=generated_at, hours=hours, limit=limit
            ),
            "hours": hours,
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        }
    )


@router.get("/hot")
def hot(
    request: Request,
    limit: int = Query(default=5, ge=1, le=10),
    hours: int = Query(default=48, ge=6, le=168),
) -> dict[str, object]:
    payload = hot_payload(limit=limit, hours=hours)
    if payload is None:
        # 503（而不是 200 + 空 items）：`_public_pagination_cache_control` 对非
        # 200 一律发 `private, no-store`，所以这一次冷态不会被边缘缓存放大成
        # 约 120 秒的空结果。前端据此重试，见 app.js 的 renderHotTopics。
        raise HTTPException(
            status_code=503,
            detail="hot topics are still being prepared",
            headers={"Retry-After": "2"},
        )
    return payload
