from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from airadar.admin.usage import collect_usage
from airadar.llm_usage import migrate_usage_db
from airadar.pricing import get_pricing


def _catalog(tmp_path: Path):  # noqa: ANN202
    return get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-pro": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            },
            "deepseek/deepseek-v4-flash": {
                "input_cost_per_token": 0.5e-6,
                "cache_read_input_token_cost": 0.05e-6,
                "output_cost_per_token": 1e-6,
            }
        },
        persist=False,
    )


def _seed_usage(path: Path) -> None:
    migrate_usage_db(usage_db_path=path, main_db_path=path.with_name("missing-main.db"))
    rows = [
        ("interpret", "deepseek", "deepseek-v4-pro", 100, 20, "2026-08-10T01:00:00Z"),
        ("score", "ark", "deepseek-v4-flash-260425", 200, 30, "2026-08-10T02:00:00Z"),
        ("enrich", "unknown", "missing-model", 300, 40, "2026-08-10T03:00:00Z"),
        ("interpret", "deepseek", "deepseek-v4-pro", 50, 10, "2026-08-09T01:00:00Z"),
        ("score", "deepseek", "deepseek-v4-pro", 1, 1, "2026-08-08T03:00:00Z"),
    ]
    with sqlite3.connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 1000, NULL, '{}', ?)
            """,
            [(stage, provider, model, input_tokens, output_tokens, input_tokens + output_tokens, created_at)
             for stage, provider, model, input_tokens, output_tokens, created_at in rows],
        )


def test_collect_usage_exposes_narrowed_derived_cost_contract(tmp_path: Path) -> None:
    usage_db = tmp_path / "llm_usage.db"
    _seed_usage(usage_db)
    now = datetime(2026, 8, 10, 12, tzinfo=UTC)

    result = collect_usage(
        db_path=tmp_path / "missing-main.db",
        usage_db_path=usage_db,
        days=1,
        now=now,
        pricing_catalog=_catalog(tmp_path),
    )

    assert result["exchange_rate_usd_cny"] == 7.2
    assert result["totals"]["known_cost_usd"] > 0
    assert result["totals"]["known_cost_cny"] == round(result["totals"]["known_cost_usd"] * 7.2, 6)
    assert result["nominal_share"] > 0
    assert result["unpriced"] == [{"provider": "unknown", "model": "missing-model", "calls": 1}]
    assert {row["freshness"] for row in result["pricing_table"]} >= {"fresh"}
    assert {(row["provider"], row["model"]) for row in result["pricing_table"]} == {
        ("ark", "deepseek-v4-flash-260425"),
        ("deepseek", "deepseek-v4-pro"),
        ("unknown", "missing-model"),
    }
    deepseek_tariff = next(
        row for row in result["pricing_table"] if row["provider"] == "deepseek"
    )
    assert deepseek_tariff["input_per_million_tokens_usd"] == 1.0
    assert deepseek_tariff["cache_read_per_million_tokens_usd"] == 0.1
    assert deepseek_tariff["output_per_million_tokens_usd"] == 2.0
    assert deepseek_tariff["quote_as_of"] == "2026-08-10T09:00:00+08:00"
    assert deepseek_tariff["matched_key"] == "deepseek/deepseek-v4-pro"
    assert deepseek_tariff["match_kind"] == "exact"
    assert deepseek_tariff["match_reviewed"] is None
    assert deepseek_tariff["source_currency"] is None
    assert deepseek_tariff["source_input_per_million_tokens"] is None
    ark_tariff = next(row for row in result["pricing_table"] if row["provider"] == "ark")
    assert ark_tariff["quote_as_of"] == "2026-08-10T10:00:00+08:00"
    assert ark_tariff["matched_key"] == "ark/deepseek-v4-flash-260425"
    assert ark_tariff["match_kind"] == "exact"
    assert ark_tariff["source_currency"] == "CNY"
    unknown_tariff = next(row for row in result["pricing_table"] if row["provider"] == "unknown")
    assert unknown_tariff["quote_as_of"] == "2026-08-10T11:00:00+08:00"
    assert unknown_tariff["matched_key"] is None
    assert unknown_tariff["match_kind"] is None
    assert result["totals"]["calls"] == 3
    assert result["pricing_source"] == {"state": "fresh", "source": "litellm-live"}
    assert {"stage_costs", "provider_costs", "cost_groups", "comparison", "daily"}.isdisjoint(
        result
    )


def test_collect_usage_does_not_read_deprecated_stored_cost(tmp_path: Path) -> None:
    usage_db = tmp_path / "llm_usage.db"
    _seed_usage(usage_db)

    result = collect_usage(
        db_path=tmp_path / "missing-main.db",
        usage_db_path=usage_db,
        days=1,
        now=datetime(2026, 8, 10, 12, tzinfo=UTC),
        pricing_catalog=_catalog(tmp_path),
    )

    assert result["totals"]["known_cost_usd"] < 1


def test_collect_usage_marks_fuzzy_catalog_matches_as_unreviewed(tmp_path: Path) -> None:
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-flash": {
                "input_cost_per_token": 0.5e-6,
                "cache_read_input_token_cost": 0.05e-6,
                "output_cost_per_token": 1e-6,
            }
        },
        persist=False,
    )

    result = collect_usage(
        days=1,
        now=datetime(2026, 8, 10, 12, tzinfo=UTC),
        pricing_catalog=catalog,
        rows_snapshot=[
            {
                "stage": "interpret",
                "provider": "deepseek",
                "model": "deepseek-v4-fla",
                "input_tokens": 100,
                "cached_input_tokens": None,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_item_count": 1,
                "input_char_count": 1000,
                "attribution_json": "{}",
                "created_at": "2026-08-10T01:00:00Z",
            }
        ],
    )

    price = result["pricing_table"][0]
    assert price["matched_key"] == "deepseek/deepseek-v4-flash"
    assert price["match_kind"] == "fuzzy"
    assert price["match_reviewed"] is False
