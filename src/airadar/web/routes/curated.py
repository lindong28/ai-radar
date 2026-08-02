from __future__ import annotations

from datetime import UTC, datetime
from datetime import date as date_cls

from fastapi import APIRouter, HTTPException, Query, Request

from ..envelope import ok
from . import curated_archive, curated_digest
from .categories import CATEGORY_TAGS
from .request_db import conn_from_request

router = APIRouter()

prewarm_curated_archive_total_cache = curated_archive.prewarm_curated_archive_total_cache


def _normalized_date(value: str | None) -> str | None:
    if value is None:
        return None
    today = date_cls.today()
    try:
        parsed = date_cls.fromisoformat(value)
    except ValueError:
        return today.isoformat()
    if parsed > today:
        return today.isoformat()
    return parsed.isoformat()


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
    now = datetime.now(UTC)
    ranked: list[dict[str, object]] = []
    for item in items:
        published = str(item.get("published_at") or item.get("fetched_at") or "")
        try:
            ts = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if (now - ts).total_seconds() > hours * 3600:
            continue
        score = float(str(item.get("weighted_score") or 0.0))
        related = item.get("related_discussions")
        related_count = len(related) if isinstance(related, list) else 0
        heat = round(score * 10 + related_count * 5)
        ranked.append(
            {
                "id": item.get("id"),
                "title": item.get("title_zh") or item.get("title"),
                "url": item.get("url"),
                "source_name": item.get("source_name"),
                "heat": heat,
            }
        )
    ranked.sort(key=lambda entry: (-int(str(entry["heat"] or 0)), str(entry["id"])))
    return ok({"items": ranked[:limit], "hours": hours})
