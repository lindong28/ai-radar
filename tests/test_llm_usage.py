from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from airadar import db
from airadar.llm_usage import (
    LlmUsageRecord,
    active_usage_db_path,
    cache_usage_attribution,
    derive_cost_usd,
    migrate_usage_db,
    record_llm_usage,
)
from airadar.pricing import get_pricing


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
              100, 20, 120, 1, 1000, NULL, '{}',
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


def test_derive_cost_prices_unknown_cache_as_miss_without_claiming_a_split(tmp_path: Path) -> None:
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-pro": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
        persist=False,
    )

    result = derive_cost_usd(
        {"provider": "deepseek", "model": "deepseek-v4-pro", "input_tokens": 100, "output_tokens": 20},
        catalog=catalog,
    )

    assert result.cost_usd == pytest.approx(0.00014)
    assert result.status == "priced"
    assert result.cached_input_tokens is None
    assert result.uncached_input_tokens is None


def test_derive_cost_splits_cached_and_uncached_input(tmp_path: Path) -> None:
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-pro": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
        persist=False,
    )

    result = derive_cost_usd(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "input_tokens": 100,
            "cached_input_tokens": 80,
            "output_tokens": 20,
        },
        catalog=catalog,
    )

    assert result.cost_usd == pytest.approx(0.000068)
    assert result.cached_input_tokens == 80


def test_derive_cost_reports_nominal_and_unpriced_without_zero_substitution(tmp_path: Path) -> None:
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {},
        persist=False,
    )

    nominal = derive_cost_usd(
        {"provider": "ark", "model": "deepseek-v4-pro-260425", "input_tokens": 100, "output_tokens": 20},
        catalog=catalog,
    )
    unpriced = derive_cost_usd(
        {"provider": "unknown", "model": "missing", "input_tokens": 100, "output_tokens": 20},
        catalog=catalog,
    )

    assert nominal.status == "nominal"
    assert nominal.cost_usd is not None and nominal.cost_usd > 0
    assert unpriced.status == "unpriced"
    assert unpriced.cost_usd is None


def test_record_llm_usage_keeps_deprecated_stored_cost_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db_path))

    record_llm_usage(
        LlmUsageRecord(
            stage="score",
            provider="deepseek",
            model="deepseek-v4-flash",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        )
    )

    with sqlite3.connect(usage_db_path) as conn:
        assert conn.execute("SELECT cost_usd FROM llm_usage").fetchone()[0] is None


def test_cache_usage_attribution_normalizes_provider_shapes() -> None:
    assert cache_usage_attribution({"prompt_cache_hit_tokens": 12}) == {
        "cached_input_tokens": 12,
        "cached_input_tokens_source": "prompt_cache_hit_tokens",
    }
    assert cache_usage_attribution({"prompt_tokens_details": {"cached_tokens": 9}}) == {
        "cached_input_tokens": 9,
        "cached_input_tokens_source": "prompt_tokens_details.cached_tokens",
    }
    assert cache_usage_attribution({"prompt_tokens": 100}) == {}
