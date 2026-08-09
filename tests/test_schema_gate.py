"""schema_gate forward-compatibility check.

The gate guards code deploys: newer code must not go live against a database
missing a table or column it reads. It deliberately does NOT require schema
equality -- an earlier full-SQL-equality version rejected the real production
database over a widened CHECK constraint (llm_usage.stage gained 'interpret',
which nothing writes). These tests pin both directions: real schema passes,
genuine missing structure fails.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "schema_gate", REPO_ROOT / "deploy" / "sync" / "schema_gate.py"
)
sg = importlib.util.module_from_spec(spec)
sys.modules["schema_gate"] = sg
spec.loader.exec_module(sg)

from airadar import db as adb  # noqa: E402


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    path = tmp_path / "active.db"
    adb.migrate(path)
    return path


def test_freshly_migrated_db_passes(migrated_db: Path) -> None:
    assert sg.check(migrated_db) == []


def test_widened_check_constraint_is_forward_compatible(migrated_db: Path, tmp_path: Path) -> None:
    """The real production drift: an older, STRICTER llm_usage.stage constraint.

    The active DB allowing fewer stage values than the code does not break
    reads, so it must pass -- this is the exact case the equality gate wrongly
    rejected.
    """
    with sqlite3.connect(migrated_db) as conn:
        conn.execute("DROP TABLE llm_usage")
        conn.execute(
            "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'score', 'enrich')),"
            " provider TEXT NOT NULL, model TEXT NOT NULL, item_id TEXT,"
            " input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,"
            " total_tokens INTEGER NOT NULL DEFAULT 0, input_item_count INTEGER NOT NULL DEFAULT 1,"
            " input_char_count INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,"
            " attribution_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL)"
        )
        # DROP TABLE took the indexes with it; recreate them so this test
        # isolates the CHECK-constraint difference, not incidental index loss.
        conn.execute("CREATE INDEX idx_llm_usage_created_model ON llm_usage(created_at, model)")
        conn.execute("CREATE INDEX idx_llm_usage_stage_created ON llm_usage(stage, created_at)")
        conn.execute("CREATE INDEX idx_llm_usage_item ON llm_usage(item_id)")
        conn.commit()
    assert sg.check(migrated_db) == []


def test_missing_table_is_rejected(migrated_db: Path) -> None:
    with sqlite3.connect(migrated_db) as conn:
        conn.execute("DROP TABLE llm_usage")
        conn.commit()
    problems = sg.check(migrated_db)
    assert any("missing table llm_usage" in p for p in problems)


def test_missing_column_is_rejected(migrated_db: Path) -> None:
    """A column the code reads but the DB lacks -- rebuilt without it."""
    with sqlite3.connect(migrated_db) as conn:
        conn.execute("DROP TABLE llm_usage")
        conn.execute(
            "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " stage TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,"
            " created_at TEXT NOT NULL)"  # dropped the token/cost columns
        )
        conn.commit()
    problems = sg.check(migrated_db)
    assert any("llm_usage missing columns" in p for p in problems)


def test_missing_index_is_rejected(migrated_db: Path) -> None:
    with sqlite3.connect(migrated_db) as conn:
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()[0]
        conn.execute(f"DROP INDEX {idx}")
        conn.commit()
    problems = sg.check(migrated_db)
    assert any("missing index" in p for p in problems)
