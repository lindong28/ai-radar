from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "radar.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


@dataclass(frozen=True)
class CheckpointResult:
    busy: int
    log: int
    checkpointed: int


def resolve_db_path(path: str | Path | None = None) -> Path:
    configured = path or os.environ.get("AI_RADAR_DB") or DEFAULT_DB_PATH
    db_path = Path(configured)
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return db_path


DEFAULT_BUSY_TIMEOUT_MS = 5000


def _busy_timeout_ms() -> int:
    """How long a writer waits for the lock, overridable for long batch jobs.

    Five seconds suits the scheduled pipeline, where every writer is short. A backfill runs for
    hours alongside that pipeline and meets its curate transaction repeatedly; at five seconds it
    dies partway through with "database is locked" and leaves the archive half re-scored. The
    value is not raised for everyone, because a long default would turn a genuine deadlock into a
    hang nobody notices.
    """
    raw = os.environ.get("AI_RADAR_SQLITE_BUSY_TIMEOUT_MS")
    if not raw:
        return DEFAULT_BUSY_TIMEOUT_MS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_BUSY_TIMEOUT_MS
    return parsed if parsed > 0 else DEFAULT_BUSY_TIMEOUT_MS


def get_conn(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_busy_timeout_ms()}")
    return conn


def checkpoint_db(path: str | Path | None = None) -> CheckpointResult:
    conn = get_conn(path)
    try:
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if row is None:
            return CheckpointResult(busy=0, log=0, checkpointed=0)
        return CheckpointResult(busy=int(row[0]), log=int(row[1]), checkpointed=int(row[2]))
    finally:
        conn.close()


class MigrationScript(NamedTuple):
    """A migration split into complete statements plus any unterminated leftover."""

    statements: list[str]
    tail: str


def _split_migration_statements(sql: str) -> MigrationScript:
    """Split a migration script the way the executor runs it.

    ``CREATE TRIGGER`` bodies contain their own semicolons, so statement
    boundaries are found by the ``END;`` line rather than by the first ``;``.
    Schema-drift detection parses expectations with this same splitter, so the
    definitions compared against sqlite_master are exactly the ones executed.
    """
    statements: list[str] = []
    pending: list[str] = []
    in_trigger = False
    for line in sql.splitlines(keepends=True):
        pending.append(line)
        raw_stmt = "".join(pending)
        effective_stmt = "\n".join(
            part for part in raw_stmt.splitlines() if not part.strip().startswith("--")
        ).lstrip()
        if not in_trigger and effective_stmt.upper().startswith("CREATE TRIGGER"):
            in_trigger = True
        if in_trigger:
            if line.strip().upper() != "END;":
                continue
            in_trigger = False
        elif not sqlite3.complete_statement(raw_stmt):
            continue
        pending.clear()
        stmt = raw_stmt.strip()
        if stmt:
            statements.append(stmt)
    # The leftover is whatever the scanner never consumed. It is returned from
    # here rather than recovered afterwards by searching the text: the last
    # complete statement can reappear verbatim inside the leftover (in a comment
    # or a string), and a text search would then cut the boundary in the wrong
    # place and silently drop the trailing statement.
    return MigrationScript(statements, "".join(pending).strip())


def _execute_migration_idempotent(conn: sqlite3.Connection, sql: str) -> None:
    """Run a migration script statement-by-statement, treating sqlite
    "duplicate column name" errors as idempotent no-ops so ALTER TABLE ADD
    COLUMN is safe to re-run on already-migrated databases."""
    script = _split_migration_statements(sql)
    for stmt in script.statements:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "duplicate column name" in message:
                continue
            if "already exists" in message and stmt.lstrip().upper().startswith("CREATE "):
                continue
            raise
    if script.tail:
        # A migration whose final statement lacks its delimiter is malformed.
        # It stays executable for compatibility, but deliberately outside the
        # idempotent handling above: swallowing "duplicate column name" here
        # would hide the missing terminator instead of surfacing it.
        conn.execute(script.tail)


_FTS_MIGRATION = "003_add_fts5_search.sql"

_FTS_PRECISE_REBUILD_SQL = """
DELETE FROM items_fts;

INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh)
SELECT
  i.id,
  i.title,
  i.content_text,
  COALESCE(s.name, ''),
  COALESCE(i.author, ''),
  COALESCE(
    (
      SELECT json_extract(e.output_json, '$.title_zh')
      FROM item_evaluations e
      WHERE e.item_id = i.id
        AND e.stage = 'enrich'
        AND e.error IS NULL
        AND e.output_json IS NOT NULL
      ORDER BY e.evaluated_at DESC, e.id DESC
      LIMIT 1
    ),
    ''
  )
FROM items i
LEFT JOIN sources s ON s.id = i.source_id;
"""

# 003 drops six triggers but creates five: evals_ai_fts is retired. Comparing
# live schema against the DROP list would read its correct absence as drift and
# rebuild the index on every run, which is the cost this skip exists to avoid.
_RETIRED_FTS_TRIGGERS = ("evals_ai_fts",)


def _normalize_ddl(sql: str) -> str:
    """Comment- and whitespace-insensitive form of a CREATE statement.

    sqlite_master stores the statement as parsed, which drops leading comments
    that are still present in the migration file, so raw text comparison would
    report drift on an untouched schema.
    """
    without_comments = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    collapsed = " ".join(without_comments.split())
    head = collapsed.upper().find("CREATE ")
    if head >= 0:
        collapsed = collapsed[head:]
    # sqlite_master stores the statement as parsed: no trailing delimiter and
    # no IF NOT EXISTS clause, both of which the migration file does carry.
    collapsed = _IF_NOT_EXISTS_RE.sub(r"\1", collapsed)
    return collapsed.rstrip().rstrip(";").rstrip()


_IF_NOT_EXISTS_RE = re.compile(
    r"^(CREATE\s+(?:VIRTUAL\s+TABLE|TRIGGER|TABLE|INDEX|VIEW)\s+)IF\s+NOT\s+EXISTS\s+",
    re.IGNORECASE,
)


_CREATE_OBJECT_RE = re.compile(
    r"^CREATE\s+(?:VIRTUAL\s+TABLE|TRIGGER)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
    re.IGNORECASE,
)


def _expected_fts_objects() -> dict[str, str]:
    """Objects 003 declares, parsed from the migration file itself.

    Deriving these from the file rather than restating them here keeps a single
    source of truth: editing 003 changes both what gets created and what counts
    as drift.
    """
    sql = (MIGRATIONS_DIR / _FTS_MIGRATION).read_text(encoding="utf-8")
    script = _split_migration_statements(sql)
    if script.tail:
        # The executor runs the tail, but it is not parsed into expectations.
        # Returning nothing makes the caller treat the schema as unmatched and
        # rebuild: an unterminated statement in 003 must never let a stale or
        # missing object slip past the comparison unnoticed.
        return {}
    objects: dict[str, str] = {}
    for stmt in script.statements:
        normalized = _normalize_ddl(stmt)
        match = _CREATE_OBJECT_RE.match(normalized)
        if match:
            objects.setdefault(match.group(1), normalized)
    return objects


def _fts_schema_matches(conn: sqlite3.Connection) -> bool:
    expected = _expected_fts_objects()
    if not expected:
        return False
    expected_slots = ",".join("?" * len(expected))
    live = {
        row[0]: row[1]
        for row in conn.execute(
            f"SELECT name, sql FROM sqlite_master WHERE name IN ({expected_slots})",
            tuple(expected),
        )
        if row[1]
    }
    if set(live) != set(expected):
        return False
    if any(_normalize_ddl(live[name]) != expected[name] for name in expected):
        return False
    retired_slots = ",".join("?" * len(_RETIRED_FTS_TRIGGERS))
    retired = conn.execute(
        f"SELECT 1 FROM sqlite_master WHERE name IN ({retired_slots})",
        _RETIRED_FTS_TRIGGERS,
    ).fetchone()
    return retired is None


def _migration_already_applied(conn: sqlite3.Connection, migration_name: str) -> bool:
    migration_ids = {
        "004_enrich_stage.sql": "004_enrich_stage",
        "016_nullable_evaluation_cost.sql": "016_nullable_evaluation_cost",
        "017_cleanup_deprecated_cost_residue.sql": "017_cleanup_deprecated_cost_residue",
    }
    if migration_name in migration_ids:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='airadar_migrations'"
        ).fetchone()
        if table is None:
            return False
        applied = bool(
            conn.execute(
                "SELECT 1 FROM airadar_migrations WHERE id=?",
                (migration_ids[migration_name],),
            ).fetchone()
        )
        if applied:
            return True
        if migration_name == "016_nullable_evaluation_cost.sql":
            legacy_marker = conn.execute(
                "SELECT 1 FROM airadar_migrations WHERE id='014_nullable_evaluation_cost'"
            ).fetchone()
            cost_column = next(
                (
                    row
                    for row in conn.execute("PRAGMA table_info(item_evaluations)")
                    if row[1] == "cost_usd"
                ),
                None,
            )
            # 016 was briefly shipped as 014. Only accept that alias when the
            # nullable schema proves the rewrite already happened; migration
            # 017 normalizes the marker without replaying the 388 MiB table.
            return legacy_marker is not None and cost_column is not None and int(cost_column[3]) == 0
        return False
    if migration_name == _FTS_MIGRATION:
        # 003 rebuilds the whole FTS5 index. The pipeline migrates every 15
        # minutes and serve migrates on startup, so running it unconditionally
        # rewrote ~296 MiB per round -- 94% of the bytes the production replica
        # had to ship. Skip while the schema already matches; any drift (missing
        # table, changed tokenizer, missing or altered trigger, resurrected
        # retired trigger) still triggers the full rebuild.
        return _fts_schema_matches(conn)
    return False


# Pages of merge work per maintenance call. Sized on the production database:
# one call took ~0.3-0.6s and merged ~3,500-10,000 pages, while a pipeline
# round adds a few hundred; one call per round both keeps up and drains a
# backlog within a day, without ever approaching the 13s a full optimize costs.
_FTS_MERGE_BUDGET = 2000

# Rows in items_fts_data above which a new merge pass is worth starting.
# The fully optimized index sits near 50k; automerge alone holds a healthy
# index well under this, so crossing it means unmerged segments are piling up.
_FTS_BACKLOG_ROWS = 90_000


def maintain_fts(path: str | Path | None = None) -> None:
    """Bounded incremental merge of the FTS5 index.

    Migration 003 used to DROP and rebuild the whole index on every migrate(),
    which was 94% of the replica sync cost -- but it was also the only thing
    compacting the index. Once the rebuild became conditional, incremental
    segments accumulated unmerged (observed: +200 MiB of index and an 820 MiB
    freelist within hours). This bounded merge is the explicit replacement.

    Parameter signs matter and were measured, not assumed. A negative budget
    *initiates* a merge pass; if new segments arrive before the pass finishes,
    re-initiating restarts it -- and the pipeline writes every 15 minutes, so
    calling merge(-N) each round did net zero work while a backlog of 136k
    pages GREW by 2k over six simulated rounds. A positive budget *continues*
    whatever pass exists (automerge or initiated) and, measured under the same
    interleaved writes, drained ~10k pages per round. So: continue by default;
    only when continuing has nothing left to do and the index is oversized is
    a fresh pass initiated -- at most one initiation per backlog, not per call.
    """
    with get_conn(path) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items_fts'"
        ).fetchone()
        if table is None:
            return

        before = conn.total_changes
        conn.execute(
            "INSERT INTO items_fts(items_fts, rank) VALUES('merge', ?)",
            (_FTS_MERGE_BUDGET,),
        )
        conn.commit()
        # >= 2, not > 2: SQLite defines a total_changes delta of at least 2 as
        # "merge work was performed" and below 2 as a no-op. Off by one here
        # either strands the initiate branch (backlog regrows) or fires an
        # extra initiate right after real work (restarting the pass).
        progressed = conn.total_changes - before >= 2

        if not progressed:
            backlog = conn.execute("SELECT COUNT(*) FROM items_fts_data").fetchone()[0]
            if backlog > _FTS_BACKLOG_ROWS:
                conn.execute(
                    "INSERT INTO items_fts(items_fts, rank) VALUES('merge', ?)",
                    (-_FTS_MERGE_BUDGET,),
                )
                conn.commit()


def _apply_pending_migrations(conn: sqlite3.Connection) -> None:
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if _migration_already_applied(conn, migration.name):
            continue
        _execute_migration_idempotent(conn, migration.read_text(encoding="utf-8"))


def rebuild_fts(path: str | Path) -> None:
    """Create the migration-owned FTS schema, then rebuild its exact contents.

    The migration remains the schema/trigger source of truth. Its historical
    backfill is followed by the apply-side derivation contract, which ignores
    failed and NULL-output enrich events and orders valid events by
    ``evaluated_at DESC, id DESC``. Runtime ``enrich_ai_fts`` deliberately has
    different semantics: every later successful INSERT overwrites title_zh.
    The snapshot-bound manifest, not either derivation, is the final oracle.
    """
    migration_sql = (MIGRATIONS_DIR / _FTS_MIGRATION).read_text(encoding="utf-8")
    conn = get_conn(path)
    try:
        _execute_migration_idempotent(conn, migration_sql)
        conn.executescript(_FTS_PRECISE_REBUILD_SQL)
        if not _fts_schema_matches(conn):
            raise sqlite3.DatabaseError("FTS schema differs after explicit rebuild")
        conn.commit()
    finally:
        conn.close()


def migrate(path: str | Path | None = None) -> None:
    with get_conn(path) as conn:
        _apply_pending_migrations(conn)
        # Re-asserted because a migration script can change it and one did: 001_init carried
        # `PRAGMA busy_timeout=5000` for a long time, so every caller's setting survived only
        # until the first migration statement ran. The symptom was a raised timeout that had no
        # effect, with nothing logged and the pragma reading correctly right up until it did not.
        conn.execute(f"PRAGMA busy_timeout={_busy_timeout_ms()}")
        conn.commit()
