from __future__ import annotations

import sqlite3
from pathlib import Path

from airadar import db
from airadar.llm_usage import LlmUsageRecord, active_usage_db_path, migrate_usage_db, record_llm_usage


def test_active_usage_db_path_ignores_main_db_and_defaults_to_dedicated_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AI_RADAR_DB", str(tmp_path / "radar.db"))
    monkeypatch.delenv("AI_RADAR_LLM_USAGE_DB", raising=False)

    assert Path(active_usage_db_path()) == db.PROJECT_ROOT / "data" / "llm_usage.db"


def test_record_llm_usage_migrates_legacy_rows_and_writes_to_dedicated_db(
    monkeypatch,
    tmp_path: Path,
) -> None:
    main_db_path = tmp_path / "radar.db"
    usage_db_path = tmp_path / "llm_usage.db"
    db.migrate(main_db_path)
    with sqlite3.connect(main_db_path) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (
              id, stage, provider, model, item_id, input_tokens, output_tokens,
              total_tokens, input_item_count, input_char_count, cost_usd,
              attribution_json, created_at
            )
            VALUES (
              42, 'prefilter', 'deepseek', 'legacy-model', 'legacy-item',
              100, 20, 120, 1, 500, 0.001, '{"legacy":true}',
              '2026-06-23T10:00:00Z'
            )
            """
        )
        conn.commit()

    monkeypatch.setenv("AI_RADAR_DB", str(main_db_path))
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db_path))

    record_llm_usage(
        LlmUsageRecord(
            stage="prefilter",
            provider="ark",
            model="new-model",
            item_id="new-item",
            input_tokens=10,
            output_tokens=2,
            total_tokens=12,
            input_item_count=1,
            input_char_count=80,
            created_at="2026-06-23T10:05:00Z",
        )
    )

    with sqlite3.connect(main_db_path) as conn:
        main_rows = conn.execute("SELECT id, model FROM llm_usage ORDER BY id").fetchall()
    with sqlite3.connect(usage_db_path) as conn:
        usage_rows = conn.execute("SELECT id, model FROM llm_usage ORDER BY id").fetchall()

    assert main_rows == [(42, "legacy-model")]
    assert usage_rows == [(42, "legacy-model"), (43, "new-model")]


def test_usage_db_migration_adds_interpret_stage_idempotently(tmp_path: Path) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    main_db_path = tmp_path / "radar.db"
    with sqlite3.connect(usage_db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE llm_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'score', 'enrich')),
              provider TEXT NOT NULL,
              model TEXT NOT NULL,
              item_id TEXT,
              input_tokens INTEGER NOT NULL DEFAULT 0,
              output_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              input_item_count INTEGER NOT NULL DEFAULT 1,
              input_char_count INTEGER NOT NULL DEFAULT 0,
              cost_usd REAL NOT NULL DEFAULT 0,
              attribution_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE TABLE airadar_usage_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            );
            INSERT INTO llm_usage (
              stage, provider, model, item_id, input_tokens, output_tokens,
              total_tokens, input_item_count, input_char_count, cost_usd,
              attribution_json, created_at
            )
            VALUES (
              'prefilter', 'deepseek', 'legacy-model', 'legacy-item',
              10, 2, 12, 1, 100, 0, '{}', '2026-06-23T10:00:00Z'
            );
            """
        )
        conn.commit()

    migrate_usage_db(usage_db_path=usage_db_path, main_db_path=main_db_path)
    migrate_usage_db(usage_db_path=usage_db_path, main_db_path=main_db_path)

    with sqlite3.connect(usage_db_path) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, item_id, input_tokens, output_tokens,
              total_tokens, input_item_count, input_char_count, cost_usd,
              attribution_json, created_at
            )
            VALUES (
              'interpret', 'ark', 'ark-model', 'item-1',
              100, 20, 120, 1, 1000, 0.01, '{}',
              '2026-06-23T10:05:00Z'
            )
            """
        )
        rows = conn.execute("SELECT stage, provider, model FROM llm_usage ORDER BY id").fetchall()
        migrations = conn.execute(
            "SELECT id, COUNT(*) FROM airadar_usage_migrations GROUP BY id ORDER BY id"
        ).fetchall()

    assert rows == [
        ("prefilter", "deepseek", "legacy-model"),
        ("interpret", "ark", "ark-model"),
    ]
    assert ("002_add_interpret_stage", 1) in migrations
