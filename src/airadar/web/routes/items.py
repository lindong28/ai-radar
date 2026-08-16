from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...presentation.summary import item_summary, json_loads
from ..envelope import ok
from .request_db import conn_from_request

router = APIRouter()


@router.get("/items/{item_id}")
def item_detail(request: Request, item_id: str) -> dict[str, object]:
    with conn_from_request(request) as conn:
        row = conn.execute(
            """
            SELECT i.*, s.name AS source_name, s.tier,
                   s.kind AS source_kind,
                   s.homepage_url AS source_homepage_url,
                   s.icon_url AS source_icon_url,
                   wa.avatar_url AS author_avatar_url
            FROM items i
            JOIN sources s ON s.id=i.source_id
            LEFT JOIN wechat_account_avatars wa
              ON COALESCE(s.kind, 'feed')='wechat'
             AND wa.account=i.author
             AND wa.avatar_url IS NOT NULL
            WHERE i.id=? AND s.enabled=1 AND COALESCE(s.kind, 'feed') != 'wechat'
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        eval_rows = conn.execute(
            """
            SELECT id, stage, ruleset_version, model_id, numeric_json, evaluated_at, error
            FROM item_evaluations
            WHERE item_id=?
            ORDER BY id DESC
            """,
            (item_id,),
        ).fetchall()
        item = item_summary(row, conn=conn)
    evaluations = [
        {
            "id": evaluation["id"],
            "stage": evaluation["stage"],
            "ruleset_version": evaluation["ruleset_version"],
            "model_id": evaluation["model_id"],
            "numeric": json_loads(evaluation["numeric_json"], None),
            "evaluated_at": evaluation["evaluated_at"],
            "error": evaluation["error"],
        }
        for evaluation in eval_rows
    ]
    return ok({"item": item, "evaluations": evaluations})
