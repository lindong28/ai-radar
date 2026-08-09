#!/usr/bin/env python3
"""Check that this code can serve a given database.

Run by the code-deploy step with the CANDIDATE's interpreter and source, so the
schema it expects is the schema the code about to go live actually defines:

    python3 schema_gate.py <active-db-path>

exit 0 = compatible, non-zero with a message = refuse the deploy.

Scope, deliberately bounded to *forward compatibility* -- "can this newer code
read this database" -- not schema equality:

  * Every table and index the code's own migrate() produces must EXIST in the
    active database, and every column the code expects on those tables must be
    present. A missing table/column is a real incompatibility: the code will
    query something that isn't there.

  * Differences that leave the active database a superset, or merely stricter,
    of what the code needs are NOT failures. The active database legitimately
    lags or leads on things that do not break reads -- CHECK-constraint value
    sets (an older llm_usage.stage lacks 'interpret', which nothing writes),
    column order, historically-different-but-equivalent DDL text. An earlier
    version compared full sqlite_master SQL and rejected the real production
    database over exactly this.

  * FTS integrity rides on _fts_schema_matches -- the same predicate the
    migration skip trusts -- because the shadow tables do not diff cleanly.

The data path has its own guard (the apply step's acceptance triple); this is
defence in depth on the code-deploy side, so it errs toward "let a
forward-compatible database through" rather than blocking a manual push on a
cosmetic difference.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

from airadar import db as adb


def _tables_and_columns(conn: sqlite3.Connection) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}
        out[name] = cols
    return out


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    }


# Tables that are bookkeeping or transient, not part of the served schema
# contract: the migration ledger, and FTS shadow tables (checked separately).
def _is_ignorable(table: str) -> bool:
    return table == "airadar_migrations" or table.startswith("items_fts")


def check(active_db: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        reference = Path(tmp) / "reference.db"
        adb.migrate(reference)
        ref_conn = sqlite3.connect(reference)
        try:
            ref_tables = _tables_and_columns(ref_conn)
            ref_indexes = _indexes(ref_conn)
        finally:
            ref_conn.close()

    problems: list[str] = []
    conn = sqlite3.connect(f"file:{active_db}?mode=ro", uri=True)
    try:
        if not adb._fts_schema_matches(conn):
            problems.append("FTS schema mismatch")
        live_tables = _tables_and_columns(conn)
        live_indexes = _indexes(conn)
        for table, ref_cols in ref_tables.items():
            if _is_ignorable(table):
                continue
            if table not in live_tables:
                problems.append(f"missing table {table}")
                continue
            missing_cols = ref_cols - live_tables[table]
            if missing_cols:
                problems.append(f"table {table} missing columns {sorted(missing_cols)}")
        for index in ref_indexes - live_indexes:
            problems.append(f"missing index {index}")
    finally:
        conn.close()
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: schema_gate.py <active-db-path>", file=sys.stderr)
        return 2
    problems = check(Path(sys.argv[1]))
    if problems:
        print("; ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
