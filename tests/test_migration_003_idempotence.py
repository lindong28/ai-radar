"""Migration 003 must not rebuild the FTS5 index when the schema already matches.

Before this suite, ``migrate()`` unconditionally executed
``DROP TABLE IF EXISTS items_fts`` + a full ``INSERT ... SELECT`` repopulate on
every call. The pipeline calls ``migrate()`` every 15 minutes and ``serve``
calls it on every startup, so a ~296 MiB index was rewritten each time. That
dominated the cost of replicating the database to the production server.

Two kinds of assertion appear here, because neither alone has discriminating
power:

* The *skip* path is asserted on **executed statements**, not page counts: a
  DROP + rebuild can land on an identical page count, so ``dbstat`` cannot tell
  "skipped" from "rebuilt to the same size".
* The *rebuild* path is asserted on **restored search results**, not on the
  statements: "a DROP/CREATE ran" is satisfied just as well by a rebuild that
  repopulates nothing.

Both rebuild assertions were confirmed by mutating the migration and checking
that they fail -- an assertion nobody has seen fail is not yet evidence:

1. Repopulate zero rows (``WHERE 1 = 0``). Caught, and it showed that
   ``integrity_check = ok`` *and* ``fts5 integrity-check = ok`` both hold on a
   completely empty index -- so integrity alone proves nothing.
2. Repopulate every row but blank ``source_name``/``author``/``title_zh``.
   Caught only after :data:`PROBE_TERMS` grew to one term per indexed column;
   with probes on ``title``/``content_text`` alone, the row count, both
   integrity checks and every remaining probe were bit-identical to a healthy
   index while three columns had lost all searchability.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest

from airadar import db

FTS_WRITE_PREFIXES = ("DROP TABLE", "CREATE VIRTUAL TABLE", "INSERT INTO ITEMS_FTS")

# One probe per indexed column. Covering only title/content_text would let a
# rebuild that blanks source_name, author or title_zh keep identical row counts
# and identical hit counts for the remaining probes -- green on a real loss.
#
# All terms are >= 3 characters: the trigram tokenizer makes shorter terms
# (including two-character CJK words) match nothing, so they cannot separate a
# healthy index from a broken one either.
PROBE_TERMS = (
    "OpenAI",  # title
    "模型能力",  # content_text
    "Probeworthy Feed",  # source_name
    "Grace Hopper",  # author
    "中文标题快照",  # title_zh (populated from enrich evaluations)
)


def _seed_searchable_rows(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO sources (id, name, url, tier, enabled, kind, synced_at)"
            " VALUES ('s1', 'Probeworthy Feed', 'https://e.example/feed', 'T1', 1, 'feed',"
            " '2026-01-01T00:00:00Z')"
        )
        for idx, (title, body) in enumerate(
            [
                ("OpenAI ships a new model", "关于模型能力的深入讨论"),
                ("DeepSeek releases weights", "模型能力对比与评测"),
                ("Unrelated hardware news", "散热与功耗"),
            ]
        ):
            conn.execute(
                "INSERT INTO items (id, source_id, url, title, author, content_text,"
                " content_hash, published_at, fetched_at) VALUES (?, 's1', ?, ?,"
                " 'Grace Hopper', ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (f"t{idx}", f"https://e.example/{idx}", title, body, f"hash-{idx}"),
            )
        # title_zh reaches the index through an enrich evaluation, so a rebuild
        # that loses the evaluation join would blank it silently.
        conn.execute(
            "INSERT INTO item_evaluations (item_id, stage, ruleset_version, model_id,"
            " input_json, output_json, evaluated_at) VALUES ('t0', 'enrich', 'test', 'test',"
            " '{}', '{\"title_zh\": \"中文标题快照\"}', '2026-01-02T00:00:00Z')"
        )
        conn.commit()


class FtsAcceptance(NamedTuple):
    """The full acceptance triple, not just hit counts.

    A corrupt FTS5 index can return exactly the right hit counts -- observed on
    the real database, where sqlite 3.45 reported ``malformed inverted index``
    while every probe returned the same numbers as the writer. Conversely an
    empty index passes both integrity checks. Only the three together separate
    healthy from broken.
    """

    integrity: str
    fts_integrity: str
    rows: int
    hits: tuple[tuple[str, int], ...]


def _fts_acceptance(path: Path) -> FtsAcceptance:
    with sqlite3.connect(path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        try:
            conn.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
            fts_integrity = "ok"
        except sqlite3.DatabaseError as exc:  # pragma: no cover - failure path
            fts_integrity = f"FAIL: {exc}"
        rows = conn.execute("SELECT COUNT(*) FROM items_fts").fetchone()[0]
        hits = tuple(
            (
                term,
                conn.execute(
                    "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (term,)
                ).fetchone()[0],
            )
            for term in PROBE_TERMS
        )
    return FtsAcceptance(integrity, fts_integrity, rows, hits)


def _assert_rebuild_restored_search(path: Path, baseline: FtsAcceptance) -> None:
    after = _fts_acceptance(path)
    assert after.integrity == "ok", after
    assert after.fts_integrity == "ok", after
    assert after == baseline, f"rebuild did not restore the index: {after} != {baseline}"


def _trace(conn: sqlite3.Connection) -> list[str]:
    """Collect SQL statements a connection executes."""
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    return seen


def _fts_rebuild_statements(statements: list[str]) -> list[str]:
    hits = []
    for stmt in statements:
        normalized = " ".join(stmt.split()).upper()
        if "ITEMS_FTS" not in normalized:
            continue
        if normalized.startswith(FTS_WRITE_PREFIXES):
            hits.append(stmt.strip())
    return hits


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    path = tmp_path / "radar.db"
    db.migrate(path)
    return path


def test_cold_database_builds_fts(tmp_path: Path) -> None:
    """A fresh database must still get its FTS table and triggers."""
    path = tmp_path / "cold.db"
    db.migrate(path)

    with sqlite3.connect(path) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items_fts'"
        ).fetchone()
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND sql LIKE '%items_fts%'"
            )
        }

    assert table is not None
    assert triggers == {
        "items_ai_fts",
        "items_au_fts",
        "items_ad_fts",
        "sources_au_fts",
        "enrich_ai_fts",
    }


def test_rerun_does_not_rebuild_fts(migrated_db: Path) -> None:
    """The regression this migration change exists for.

    Re-running migrate() on an already-correct schema must not touch the FTS
    index at all.
    """
    conn = sqlite3.connect(migrated_db)
    statements = _trace(conn)
    try:
        db._apply_pending_migrations(conn)
    finally:
        conn.set_trace_callback(None)
        conn.close()

    rebuilds = _fts_rebuild_statements(statements)
    assert rebuilds == [], f"migrate() rebuilt the FTS index on a matching schema: {rebuilds}"


def test_legacy_evals_trigger_absence_is_not_drift(migrated_db: Path) -> None:
    """003 drops six triggers but creates five.

    ``evals_ai_fts`` is a retired trigger. A predicate that compares against the
    DROP list instead of the CREATE list would read its (correct) absence as
    schema drift and rebuild on every single run -- exactly the behaviour being
    removed.
    """
    with sqlite3.connect(migrated_db) as conn:
        legacy = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='evals_ai_fts'"
        ).fetchone()
    assert legacy is None

    conn = sqlite3.connect(migrated_db)
    statements = _trace(conn)
    try:
        db._apply_pending_migrations(conn)
    finally:
        conn.set_trace_callback(None)
        conn.close()

    assert _fts_rebuild_statements(statements) == []


def _drift_and_repair(
    migrated_db: Path, break_it: Callable[[sqlite3.Connection], None]
) -> list[str]:
    """Seed searchable rows, snapshot the healthy index, break the schema, repair.

    Returns the traced statements. Every caller asserts both that the rebuild
    path ran *and* that search came back intact -- "a DROP/CREATE executed" is a
    path assertion, and a rebuild that repopulates nothing satisfies it.
    """
    _seed_searchable_rows(migrated_db)
    baseline = _fts_acceptance(migrated_db)
    assert all(count > 0 for _, count in baseline.hits), baseline

    with sqlite3.connect(migrated_db) as conn:
        break_it(conn)
        conn.commit()

    conn = sqlite3.connect(migrated_db)
    statements = _trace(conn)
    try:
        db._apply_pending_migrations(conn)
        conn.commit()
    finally:
        conn.set_trace_callback(None)
        conn.close()

    _assert_rebuild_restored_search(migrated_db, baseline)
    return statements


def _drop_fts_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE items_fts")


def _drop_items_au_trigger(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER items_au_fts")


def test_dropped_table_is_rebuilt(migrated_db: Path) -> None:
    """Drift must still be repaired, or the skip would be a silent downgrade."""
    statements = _drift_and_repair(migrated_db, _drop_fts_table)
    assert _fts_rebuild_statements(statements), "missing FTS table was not rebuilt"


def test_changed_tokenizer_is_rebuilt(migrated_db: Path) -> None:
    """Tokenizer changes are invisible to a "table exists" check.

    ``tokenize='trigram'`` is what makes sub-3-character queries return nothing;
    silently keeping an index built by a different tokenizer would change search
    results without any structural signal.
    """

    def swap_tokenizer(conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE items_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE items_fts USING fts5("
            "item_id UNINDEXED, title, content_text, source_name, author, title_zh)"
        )

    statements = _drift_and_repair(migrated_db, swap_tokenizer)
    assert _fts_rebuild_statements(statements), "tokenizer drift was not repaired"

    with sqlite3.connect(migrated_db) as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='items_fts'").fetchone()[0]
    assert "trigram" in sql


def test_missing_trigger_is_repaired(migrated_db: Path) -> None:
    statements = _drift_and_repair(migrated_db, _drop_items_au_trigger)
    assert _fts_rebuild_statements(statements), "missing trigger did not trigger a rebuild"

    with sqlite3.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='items_au_fts'"
        ).fetchone()


def test_altered_trigger_definition_is_repaired(migrated_db: Path) -> None:
    """A trigger that still exists but no longer matches must count as drift.

    Presence checks pass here; only comparing the definition catches it. Without
    this, editing a trigger in 003 would never reach existing databases.
    """

    def rewrite_trigger(conn: sqlite3.Connection) -> None:
        conn.execute("DROP TRIGGER items_ad_fts")
        conn.execute(
            "CREATE TRIGGER items_ad_fts AFTER DELETE ON items BEGIN"
            " DELETE FROM items_fts WHERE item_id = old.id AND 1 = 1; END"
        )

    statements = _drift_and_repair(migrated_db, rewrite_trigger)
    assert _fts_rebuild_statements(statements), "altered trigger definition was not repaired"

    with sqlite3.connect(migrated_db) as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='items_ad_fts'").fetchone()[0]
    assert "1 = 1" not in sql


def test_resurrected_retired_trigger_is_repaired(migrated_db: Path) -> None:
    """``evals_ai_fts`` is retired: 003 drops it but never creates it.

    Its absence must not read as drift (covered elsewhere), but its *presence*
    must -- otherwise a stale trigger from an old database would survive every
    future migration.
    """

    def resurrect(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TRIGGER evals_ai_fts AFTER INSERT ON item_evaluations BEGIN"
            " UPDATE items_fts SET title_zh = '' WHERE item_id = new.item_id; END"
        )

    statements = _drift_and_repair(migrated_db, resurrect)
    assert _fts_rebuild_statements(statements), "resurrected retired trigger was not cleaned up"

    with sqlite3.connect(migrated_db) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='evals_ai_fts'"
            ).fetchone()
            is None
        )


def test_search_survives_a_skipped_rerun(migrated_db: Path) -> None:
    """Skipping the rebuild must not cost any search results.

    Probe terms are >= 3 characters: the trigram tokenizer makes shorter terms
    (including two-character CJK words) match nothing, so they cannot tell a
    healthy index from a broken one.
    """
    with sqlite3.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO sources (id, name, url, tier, enabled, kind, synced_at)"
            " VALUES ('s1', 'Example', 'https://e.example/feed', 'T1', 1, 'feed', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO items (id, source_id, url, title, content_text, content_hash,"
            " published_at, fetched_at)"
            " VALUES ('t1', 's1', 'https://e.example/1', 'OpenAI ships a new model',"
            " '关于模型能力的深入讨论', 'hash-t1',"
            " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.commit()
        before = {
            term: conn.execute(
                "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (term,)
            ).fetchone()[0]
            for term in ("OpenAI", "模型能力")
        }

    assert all(count > 0 for count in before.values()), before

    db.migrate(migrated_db)

    with sqlite3.connect(migrated_db) as conn:
        after = {
            term: conn.execute(
                "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (term,)
            ).fetchone()[0]
            for term in ("OpenAI", "模型能力")
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")

    assert after == before
    assert integrity == "ok"


def test_unterminated_tail_survives_a_self_similar_comment() -> None:
    """The leftover must come from the scanner, not from searching the text.

    Recovering the boundary with ``rfind(last_statement)`` breaks when the last
    complete statement reappears verbatim inside the unterminated leftover: the
    search lands on the copy, and the trailing statement is silently dropped.
    """
    sql = "CREATE TABLE a(x);\nALTER TABLE a ADD COLUMN y -- CREATE TABLE a(x);\n"
    script = db._split_migration_statements(sql)

    assert script.statements == ["CREATE TABLE a(x);"]
    assert script.tail.startswith("ALTER TABLE a ADD COLUMN y")


def test_unterminated_tail_is_not_granted_idempotent_forgiveness(tmp_path: Path) -> None:
    """A missing terminator must surface, not be swallowed as "already applied".

    The tail runs outside the duplicate-column/already-exists handling, so a
    malformed migration fails loudly on re-run instead of looking idempotent.
    """
    path = tmp_path / "tail.db"
    sql = "CREATE TABLE tail_probe(x)"  # no terminator

    with sqlite3.connect(path) as conn:
        db._execute_migration_idempotent(conn, sql)
        conn.commit()
        with pytest.raises(sqlite3.OperationalError, match="already exists"):
            db._execute_migration_idempotent(conn, sql)


def test_unterminated_003_fails_closed(migrated_db: Path) -> None:
    """An unparsed tail in 003 must force a rebuild, not a silent skip.

    The executor runs the tail, but the expectation parser never sees it, so the
    comparison would run against an incomplete object set -- and a stale or
    missing object could slip past. ``_expected_fts_objects`` therefore returns
    nothing when a tail exists, which reads as "unmatched".
    """
    original = (db.MIGRATIONS_DIR / db._FTS_MIGRATION).read_text(encoding="utf-8")
    unterminated = original.rstrip().rstrip(";")
    assert db._split_migration_statements(unterminated).tail, (
        "test premise: dropping the final delimiter should leave a tail"
    )

    with sqlite3.connect(migrated_db) as conn:
        assert db._fts_schema_matches(conn) is True, "healthy schema should match"

        real_read_text = Path.read_text

        def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
            if self.name == db._FTS_MIGRATION:
                return unterminated
            return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        Path.read_text = fake_read_text  # type: ignore[method-assign]
        try:
            assert db._fts_schema_matches(conn) is False, (
                "an unterminated 003 must not be reported as matching"
            )
        finally:
            Path.read_text = real_read_text  # type: ignore[method-assign]


def test_fts_maintenance_merges_pending_segments(migrated_db: Path) -> None:
    """Skipping the per-round rebuild removed an implicit compaction.

    Observed on the production database within hours of deploying the skip:
    items_fts_data grew 377 MiB -> 576 MiB of unmerged incremental segments and
    the freelist reached 820 MiB, because nothing ever merged what automerge
    left behind. maintain_fts() is the explicit, bounded replacement -- and it
    must be safe to run when there is nothing to merge, since that is its state
    on every round except after heavy writes.
    """
    _seed_searchable_rows(migrated_db)
    before = _fts_acceptance(migrated_db)

    db.maintain_fts(migrated_db)
    db.maintain_fts(migrated_db)  # idempotent when there is nothing left to do

    after = _fts_acceptance(migrated_db)
    assert after == before, f"maintenance changed search results: {after} != {before}"
