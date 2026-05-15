from __future__ import annotations

from datetime import date as date_cls

from fastapi import APIRouter, HTTPException, Request

from ..envelope import ok
from .common import CATEGORY_TAGS, conn_from_request, fts_phrase_query, item_summary, json_loads, matches_category

router = APIRouter()


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
) -> dict[str, object]:
    selected_date = _normalized_date(date)
    normalized_category = category if category in CATEGORY_TAGS else None
    with conn_from_request(request) as conn:
        if run_id:
            run = conn.execute("SELECT * FROM curation_runs WHERE id=?", (run_id,)).fetchone()
        else:
            run = conn.execute("SELECT * FROM curation_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if run is None:
            if run_id:
                raise HTTPException(status_code=404, detail="curation run not found")
            return ok({"run_id": None, "ruleset_version": None, "items": [], "date": selected_date, "count": 0})
        search_query = fts_phrase_query(q)
        where = "WHERE c.run_id=?"
        params: list[object] = [run["id"]]
        if selected_date:
            where += " AND date(datetime(i.published_at, '+08:00')) = ?"
            params.append(selected_date)
        if search_query:
            where += " AND i.id IN (SELECT item_id FROM items_fts WHERE items_fts MATCH ?)"
            params.append(search_query)
        rows = conn.execute(
            f"""
            SELECT i.*, s.name AS source_name, s.tier,
                   s.kind AS source_kind,
                   s.homepage_url AS source_homepage_url,
                   s.icon_url AS source_icon_url,
                   c.weighted_score, c.rank, c.reason_json
            FROM curated_items c
            JOIN items i ON i.id=c.item_id
            JOIN sources s ON s.id=i.source_id
            {where}
            ORDER BY date(datetime(i.published_at, '+08:00')) DESC,
                     i.published_at DESC, i.fetched_at DESC, i.id DESC
            """,
            params,
        ).fetchall()
        preview_query = q if search_query else None
        items = []
        for row in rows:
            item = item_summary(row, preview_query, conn)
            item["weighted_score"] = row["weighted_score"]
            item["rank"] = row["rank"]
            item["reason"] = json_loads(row["reason_json"], {})
            scores = item["reason"].get("scores", {})
            item["scores"] = scores
            if matches_category(item, normalized_category):
                items.append(item)
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
