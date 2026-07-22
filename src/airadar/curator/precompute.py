from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .. import db
from ..presentation.summary import item_summary, json_loads

DEFAULT_KEEP_DAYS = 7
_ELIGIBLE_SUMMARIES = """
summary_json IS NOT NULL
  AND run_id IN (
    SELECT id FROM curation_runs
     WHERE datetime(created_at) < datetime(
       'now', printf('-%d days', :keep_days)
     )
       AND datetime(created_at) <> (
         SELECT MAX(datetime(created_at)) FROM curation_runs
       )
  )
"""


@dataclass(frozen=True)
class RetentionStats:
    eligible_rows: int
    logical_summary_bytes: int


def curated_summary_retention_stats(
    conn: sqlite3.Connection,
    keep_days: int,
) -> RetentionStats:
    _validate_keep_days(keep_days)
    row = conn.execute(
        f"""
        SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(summary_json AS BLOB))), 0)
        FROM curated_items
        WHERE {_ELIGIBLE_SUMMARIES}
        """,
        {"keep_days": keep_days},
    ).fetchone()
    return RetentionStats(eligible_rows=int(row[0]), logical_summary_bytes=int(row[1]))


def _validate_keep_days(keep_days: int) -> None:
    if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 0:
        raise ValueError("keep_days must be a non-negative integer")


def retain_curated_summaries(conn: sqlite3.Connection, keep_days: int) -> int:
    _validate_keep_days(keep_days)
    conn.execute(
        f"""
        UPDATE curated_items SET summary_json=NULL
        WHERE {_ELIGIBLE_SUMMARIES}
        """,
        {"keep_days": keep_days},
    )
    changed = int(conn.execute("SELECT changes()").fetchone()[0])
    conn.commit()
    return changed


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
