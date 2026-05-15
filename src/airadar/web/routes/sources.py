from __future__ import annotations

from fastapi import APIRouter, Request

from ..envelope import ok
from .common import conn_from_request, json_loads

router = APIRouter()


@router.get("/sources")
def sources(request: Request) -> dict[str, object]:
    with conn_from_request(request) as conn:
        rows = conn.execute("SELECT * FROM sources ORDER BY tier, id").fetchall()
    return ok(
        {
            "sources": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "url": row["url"],
                    "tier": row["tier"],
                    "enabled": bool(row["enabled"]),
                    "kind": row["kind"] if "kind" in row.keys() else "feed",
                    "homepage_url": row["homepage_url"] if "homepage_url" in row.keys() else None,
                    "icon_url": row["icon_url"] if "icon_url" in row.keys() else None,
                    "meta": json_loads(row["meta_json"], {}),
                    "synced_at": row["synced_at"],
                }
                for row in rows
            ]
        }
    )
