from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from airadar import db, llm_usage
from airadar.llm_usage import (
    CacheUsageError,
    LlmUsageRecord,
    active_usage_db_path,
    cache_usage_attribution,
    derive_cost_usd,
    migrate_usage_db,
    record_llm_usage,
    record_llm_usage_best_effort,
)
from airadar.pricing import get_pricing


def _create_usage_db_before_cached_input_tokens(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE llm_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'score', 'enrich', 'interpret')),
              provider TEXT NOT NULL,
              model TEXT NOT NULL,
              item_id TEXT,
              input_tokens INTEGER NOT NULL DEFAULT 0,
              output_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              input_item_count INTEGER NOT NULL DEFAULT 1,
              input_char_count INTEGER NOT NULL DEFAULT 0,
              cost_usd REAL DEFAULT NULL,
              attribution_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE TABLE airadar_usage_migrations (
              id TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            );
            INSERT INTO airadar_usage_migrations VALUES ('001_copy_main_llm_usage', 'x');
            INSERT INTO airadar_usage_migrations VALUES ('002_add_interpret_stage', 'x');
            INSERT INTO airadar_usage_migrations VALUES ('003_null_deprecated_cost', 'x');
            INSERT INTO airadar_usage_migrations VALUES ('004_rollout_cost_compat', 'x');
            """
        )


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
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(llm_usage)")}

    assert rows == [
        ("prefilter", "deepseek", "legacy-model"),
        ("interpret", "ark", "ark-model"),
    ]
    assert ("002_add_interpret_stage", 1) in migrations
    assert ("005_add_cached_input_tokens", 1) in migrations
    assert columns["cached_input_tokens"][3] == 0
    assert columns["cached_input_tokens"][4] in (None, "NULL")


def test_concurrent_first_writers_do_not_lose_usage_rows_during_cache_migration(
    tmp_path: Path,
) -> None:
    writer_count = 32

    for attempt in range(3):
        usage_db_path = tmp_path / f"llm_usage_{attempt}.db"
        _create_usage_db_before_cached_input_tokens(usage_db_path)
        barrier = threading.Barrier(writer_count)

        def write_usage(writer_id: int) -> bool:
            barrier.wait()
            return record_llm_usage_best_effort(
                LlmUsageRecord(
                    stage="interpret",
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    item_id=f"item-{writer_id}",
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                ),
                db_path=usage_db_path,
            )

        with ThreadPoolExecutor(max_workers=writer_count) as pool:
            results = list(pool.map(write_usage, range(writer_count)))

        with sqlite3.connect(usage_db_path) as conn:
            row_count = conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
            cache_migration_count = conn.execute(
                "SELECT COUNT(*) FROM airadar_usage_migrations WHERE id=?",
                ("005_add_cached_input_tokens",),
            ).fetchone()[0]

        assert results == [True] * writer_count
        assert row_count == writer_count
        assert cache_migration_count == 1


@pytest.mark.parametrize(
    "transient_error",
    [
        "database is locked",
        "duplicate column name: cached_input_tokens",
    ],
)
def test_best_effort_rechecks_schema_and_retries_transient_migration_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transient_error: str,
) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    migrate_usage_db(usage_db_path=usage_db_path, main_db_path=tmp_path / "radar.db")
    original_record = llm_usage.record_llm_usage
    original_schema_check = llm_usage._usage_schema_has_cached_input_tokens
    attempts = 0
    schema_checks: list[Path] = []

    def transient_record(
        record: LlmUsageRecord,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError(transient_error)
        original_record(record, db_path=db_path)

    def tracked_schema_check(path: Path) -> bool:
        schema_checks.append(path)
        return original_schema_check(path)

    monkeypatch.setattr(llm_usage, "record_llm_usage", transient_record)
    monkeypatch.setattr(
        llm_usage,
        "_usage_schema_has_cached_input_tokens",
        tracked_schema_check,
    )

    success = record_llm_usage_best_effort(
        LlmUsageRecord(
            stage="interpret",
            provider="deepseek",
            model="deepseek-v4-pro",
            item_id="paid-call",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
        db_path=usage_db_path,
    )

    with sqlite3.connect(usage_db_path) as conn:
        rows = conn.execute("SELECT item_id FROM llm_usage").fetchall()

    assert success is True
    assert attempts == 2
    assert schema_checks == [usage_db_path]
    assert rows == [("paid-call",)]


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
    assert cache_usage_attribution({"prompt_cache_hit_tokens": 12}, input_tokens=20) == {
        "cached_input_tokens": 12,
        "cached_input_tokens_source": "prompt_cache_hit_tokens",
    }
    assert cache_usage_attribution(
        {"prompt_tokens_details": {"cached_tokens": 9}}, input_tokens=20
    ) == {
        "cached_input_tokens": 9,
        "cached_input_tokens_source": "prompt_tokens_details.cached_tokens",
    }
    assert cache_usage_attribution({"prompt_cache_miss_tokens": 3}, input_tokens=20) == {
        "cached_input_tokens": 17,
        "cached_input_tokens_source": "input_tokens-prompt_cache_miss_tokens",
    }
    assert cache_usage_attribution({"prompt_tokens": 100}, input_tokens=100) == {}


def test_cache_usage_attribution_requires_sources_to_agree_and_stay_in_bounds() -> None:
    assert cache_usage_attribution(
        {
            "prompt_cache_hit_tokens": 12,
            "prompt_tokens_details": {"cached_tokens": 12},
            "prompt_cache_miss_tokens": 8,
        },
        input_tokens=20,
    )["cached_input_tokens"] == 12

    with pytest.raises(CacheUsageError, match="conflicting cache usage fields"):
        cache_usage_attribution(
            {
                "prompt_cache_hit_tokens": 12,
                "prompt_tokens_details": {"cached_tokens": 11},
            },
            input_tokens=20,
        )
    with pytest.raises(CacheUsageError, match="exceeds input_tokens"):
        cache_usage_attribution({"prompt_cache_hit_tokens": 21}, input_tokens=20)
    with pytest.raises(CacheUsageError, match="prompt_cache_miss_tokens"):
        cache_usage_attribution({"prompt_cache_miss_tokens": 21}, input_tokens=20)


def test_cached_input_tokens_persist_as_null_when_provider_omits_cache_fields(
    tmp_path: Path,
) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    record_llm_usage_best_effort(
        LlmUsageRecord(
            stage="interpret",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
        db_path=usage_db_path,
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    )

    with sqlite3.connect(usage_db_path) as conn:
        row = conn.execute(
            "SELECT cached_input_tokens, attribution_json FROM llm_usage"
        ).fetchone()
    assert row == (None, "{}")


def test_cache_normalization_failure_is_loud_without_recording_a_row(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    success = record_llm_usage_best_effort(
        LlmUsageRecord(
            stage="interpret",
            provider="deepseek",
            model="deepseek-v4-pro",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
        db_path=usage_db_path,
        usage={
            "prompt_cache_hit_tokens": 80,
            "prompt_tokens_details": {"cached_tokens": 79},
        },
    )

    assert success is False
    assert "llm_usage_metering_failure" in caplog.text
    assert not usage_db_path.exists()


def test_best_effort_contains_path_preparation_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    success = record_llm_usage_best_effort(
        LlmUsageRecord(
            stage="interpret",
            provider="ark",
            model="deepseek-v4-pro-260425",
            item_id="already-paid",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
        ),
        db_path=Path("/dev/null/usage.db"),
    )

    assert success is False
    assert "llm_usage_metering_failure" in caplog.text
    assert "FileExistsError" in caplog.text


def test_best_effort_contains_attribution_serialization_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    success = record_llm_usage_best_effort(
        LlmUsageRecord(
            stage="interpret",
            provider="deepseek",
            model="deepseek-v4-pro",
            item_id="already-paid",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            attribution={"not_json": object()},
        ),
        db_path=tmp_path / "llm_usage.db",
    )

    assert success is False
    assert "llm_usage_metering_failure" in caplog.text
    assert "TypeError" in caplog.text


def test_best_effort_absorbs_value_invalid_record_after_paid_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    usage_db_path = tmp_path / "llm_usage.db"
    success = record_llm_usage_best_effort(
        LlmUsageRecord(
            stage="interpret",
            provider="deepseek",
            model="deepseek-v4-pro",
            item_id="already-paid",
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            cached_input_tokens=101,
        ),
        db_path=usage_db_path,
    )

    assert success is False
    assert "llm_usage_metering_failure" in caplog.text
    assert "CacheUsageError" in caplog.text
    assert not usage_db_path.exists()
