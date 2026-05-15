from __future__ import annotations

import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "radar.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def resolve_db_path(path: str | Path | None = None) -> Path:
    configured = path or os.environ.get("AI_RADAR_DB") or DEFAULT_DB_PATH
    db_path = Path(configured)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return db_path


def get_conn(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _execute_migration_idempotent(conn: sqlite3.Connection, sql: str) -> None:
    """Run a migration script statement-by-statement, treating sqlite
    "duplicate column name" errors as idempotent no-ops so ALTER TABLE ADD
    COLUMN is safe to re-run on already-migrated databases."""
    if "CREATE TRIGGER" in sql.upper():
        conn.executescript(sql)
        return
    for raw_stmt in sql.split(";"):
        stmt = raw_stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                continue
            raise


def _migration_already_applied(conn: sqlite3.Connection, migration_name: str) -> bool:
    if migration_name != "004_enrich_stage.sql":
        return False
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='airadar_migrations'"
    ).fetchone()
    if table is None:
        return False
    return bool(conn.execute("SELECT 1 FROM airadar_migrations WHERE id='004_enrich_stage'").fetchone())


def migrate(path: str | Path | None = None) -> None:
    with get_conn(path) as conn:
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if _migration_already_applied(conn, migration.name):
                continue
            _execute_migration_idempotent(conn, migration.read_text(encoding="utf-8"))
        conn.commit()
