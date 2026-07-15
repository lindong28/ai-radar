from __future__ import annotations

from fastapi import APIRouter, Request

from ...ruleset import current_version
from ..envelope import ok
from .request_db import conn_from_request

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request) -> dict[str, object]:
    with conn_from_request(request) as conn:
        item_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        run_count = conn.execute("SELECT COUNT(*) FROM curation_runs").fetchone()[0]
    return ok({"ok": True, "ruleset_version": current_version(), "items": item_count, "curation_runs": run_count})
