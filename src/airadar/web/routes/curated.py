from __future__ import annotations

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
