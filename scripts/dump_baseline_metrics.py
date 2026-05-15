#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def dump_metrics(db_path: Path, backup_path: str, *, include_post_rerun: bool = False) -> dict[str, Any]:
    with _connect(db_path) as conn:
        items_total = int(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
        items_nitter = int(conn.execute("SELECT COUNT(*) FROM items WHERE url LIKE '%nitter.net%'").fetchone()[0])
        curated_total = int(conn.execute("SELECT COUNT(*) FROM curated_items").fetchone()[0])
        curated_rows = conn.execute(
            """
            SELECT i.source_id, COUNT(*) AS n
            FROM curated_items ci
            JOIN items i ON i.id=ci.item_id
            GROUP BY i.source_id
            ORDER BY n DESC, i.source_id ASC
            """
        ).fetchall()
        curated_by_source = {str(row["source_id"]): int(row["n"]) for row in curated_rows}
        disabled_curated = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM curated_items ci
                JOIN items i ON i.id=ci.item_id
                JOIN sources s ON s.id=i.source_id
                WHERE s.enabled=0
                """
            ).fetchone()[0]
        )
        top_sources = [
            {
                "source_id": row["source_id"],
                "source_name": row["source_name"],
                "enabled": bool(row["enabled"]),
                "curated_count": int(row["curated_count"]),
            }
            for row in conn.execute(
                """
                SELECT s.id AS source_id, s.name AS source_name, s.enabled, COUNT(ci.item_id) AS curated_count
                FROM curated_items ci
                JOIN items i ON i.id=ci.item_id
                JOIN sources s ON s.id=i.source_id
                GROUP BY s.id
                ORDER BY curated_count DESC, s.id ASC
                LIMIT 20
                """
            ).fetchall()
        ]

    payload: dict[str, Any] = {
        "backup_path": backup_path,
        "items_total": items_total,
        "items_nitter": items_nitter,
        "curated_total": curated_total,
        "curated_by_source": curated_by_source,
        "disabled_curated_ratio": None if curated_total == 0 else disabled_curated / curated_total,
        "top_sources_by_curated": top_sources,
    }
    if include_post_rerun:
        payload["items_total_post_rerun"] = items_total
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/radar.db")
    parser.add_argument("--backup-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-post-rerun", action="store_true")
    args = parser.parse_args()

    payload = dump_metrics(Path(args.db), args.backup_path, include_post_rerun=args.include_post_rerun)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
