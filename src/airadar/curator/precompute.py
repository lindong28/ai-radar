from __future__ import annotations

import json
import sqlite3

from .. import db
from ..web.routes.common import item_summary, json_loads


def precompute_curated_summaries(conn: sqlite3.Connection, run_id: str) -> int:
    rows = conn.execute(
        """
        SELECT i.*, s.name AS source_name, s.tier,
               s.kind AS source_kind,
               s.homepage_url AS source_homepage_url,
               s.icon_url AS source_icon_url,
               wa.avatar_url AS author_avatar_url,
               c.weighted_score, c.rank, c.reason_json
        FROM curated_items c
        JOIN items i ON i.id=c.item_id
        JOIN sources s ON s.id=i.source_id
        LEFT JOIN wechat_account_avatars wa
          ON COALESCE(s.kind, 'feed')='wechat'
         AND wa.account=i.author
         AND wa.avatar_url IS NOT NULL
        WHERE c.run_id=?
        ORDER BY c.rank
        """,
        (run_id,),
    ).fetchall()
    for row in rows:
        item = item_summary(row, None, conn)
        item["weighted_score"] = row["weighted_score"]
        item["rank"] = row["rank"]
        reason = json_loads(row["reason_json"], {})
        item["reason"] = reason
        item["scores"] = reason.get("scores", {})
        conn.execute(
            "UPDATE curated_items SET summary_json=? WHERE run_id=? AND item_id=?",
            (json.dumps(item, ensure_ascii=False), run_id, row["id"]),
        )
    conn.commit()
    return len(rows)


def precompute_latest(path: str | None = None) -> None:
    with db.get_conn(path) as conn:
        run = conn.execute(
            "SELECT id FROM curation_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if run is None:
            return
        count = precompute_curated_summaries(conn, run["id"])
        print(f"precomputed {count} summaries for run {run['id']}")
