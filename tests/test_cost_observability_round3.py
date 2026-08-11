from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar import cli
from airadar.admin import cost_audit
from airadar.admin.usage import collect_usage
from airadar.db import migrate
from airadar.llm_usage import DerivedCost, LlmUsageRecord, derive_cost_usd, migrate_usage_db, record_llm_usage
from airadar.pricing import (
    PricingCatalog,
    PricingEntry,
    _validate_supplements,
    get_pricing,
    resolve_price,
)
from airadar.web.app import create_app

ADMIN_HEADERS = {"Cf-Access-Jwt-Assertion": "test"}


def _catalog(tmp_path: Path, table: dict[str, dict[str, object]] | None = None) -> PricingCatalog:
    return get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: table
        if table is not None
        else {
            "deepseek/deepseek-v4-pro": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
        now=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC).timestamp(),
        persist=False,
    )


def _insert_usage(
    path: Path,
    *,
    with_cache_column: bool,
    cached_values: tuple[int | None, int | None],
) -> None:
    migrate_usage_db(usage_db_path=path, main_db_path=path.with_name("missing.db"))
    with sqlite3.connect(path) as conn:
        if with_cache_column:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_usage)")}
            if "cached_input_tokens" not in columns:
                conn.execute("ALTER TABLE llm_usage ADD COLUMN cached_input_tokens INTEGER")
        for index, cached in enumerate(cached_values):
            attribution = {"cached_input_tokens": cached} if cached is not None else {}
            columns = ", cached_input_tokens" if with_cache_column else ""
            placeholders = ", ?" if with_cache_column else ""
            params: list[object] = [
                "interpret",
                "deepseek",
                "deepseek-v4-pro",
                100,
                20,
                120,
                json.dumps(attribution),
                f"2026-08-10T0{index + 1}:00:00Z",
            ]
            if with_cache_column:
                params.append(cached)
            conn.execute(
                f"""
                INSERT INTO llm_usage (
                  stage, provider, model, input_tokens, output_tokens, total_tokens,
                  input_item_count, input_char_count, cost_usd, attribution_json, created_at
                  {columns}
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 1000, NULL, ?, ? {placeholders})
                """,
                params,
            )


@pytest.mark.parametrize("with_cache_column", [False, True], ids=["live-schema", "post-p3-schema"])
def test_cache_measurement_unknown_stays_null_through_sql_api_and_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_cache_column: bool,
) -> None:
    main_db = tmp_path / "radar.db"
    usage_db = tmp_path / "usage.db"
    migrate(main_db)
    _insert_usage(
        usage_db,
        with_cache_column=with_cache_column,
        cached_values=(80, None),
    )
    catalog = _catalog(tmp_path)
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db))
    monkeypatch.setattr("airadar.admin.usage.get_pricing", lambda: catalog)

    client = TestClient(create_app(main_db))
    response = client.get("/api/v1/admin/usage", headers=ADMIN_HEADERS)
    page = client.get("/admin/usage", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert page.status_code == 200
    usage = response.json()["data"]
    assert usage["totals"]["cached_input_tokens"] is None
    assert usage["totals"]["uncached_input_tokens"] is None
    assert usage["totals"]["cache_split_coverage"] == {
        "calls_with_split": 1,
        "calls_total": 2,
        "ratio": 0.5,
    }
    assert usage["totals"]["cache_hit_rate"] == pytest.approx(0.8)
    assert usage["totals"]["known_cost_usd"] == pytest.approx(0.000208)
    assert "未采集" in page.text
    assert "缓存拆分覆盖" in page.text
    assert "1/2" in page.text


def test_derive_cost_keeps_unknown_split_null_but_prices_input_as_cache_miss(tmp_path: Path) -> None:
    result = derive_cost_usd(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "input_tokens": 100,
            "output_tokens": 20,
            "attribution_json": "{}",
            "created_at": "2026-08-10T01:00:00Z",
        },
        catalog=_catalog(tmp_path),
    )

    assert result.cost_usd == pytest.approx(0.00014)
    assert result.cached_input_tokens is None
    assert result.uncached_input_tokens is None


def test_resolution_oracle_rejects_unreviewed_fuzzy_match_without_patching_functions(tmp_path: Path) -> None:
    usage_db = tmp_path / "usage.db"
    migrate_usage_db(usage_db_path=usage_db, main_db_path=tmp_path / "missing.db")
    with sqlite3.connect(usage_db) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES ('interpret', 'deepseek', 'model', 100, 20, 120, 1, 1000,
                      NULL, '{}', '2026-08-10T01:00:00Z')
            """
        )
    catalog = _catalog(
        tmp_path,
        {
            "deepseek/model-v2": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
    )

    report = cost_audit.run_cost_audit(
        db_path=tmp_path / "missing.db",
        usage_db_path=usage_db,
        now=datetime(2026, 8, 10, 12, tzinfo=UTC),
        catalog=catalog,
        require_anchor=False,
    )

    assert report.passed is False
    assert report.resolution_unverified == (("deepseek", "model", "deepseek/model-v2"),)
    assert any("unreviewed fuzzy match" in line for line in report.human_lines)


def test_resolution_oracle_detects_missing_production_match_for_an_exact_catalog_key(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    derived = DerivedCost(
        cost_usd=None,
        status="unpriced",
        freshness=None,
        quote=None,
        cached_input_tokens=None,
        uncached_input_tokens=None,
    )

    state, matched = cost_audit._resolution_state(
        "deepseek",
        "deepseek-v4-pro",
        derived,
        catalog=catalog,
        effective_at="2026-08-10T01:00:00Z",
    )

    assert (state, matched) == ("missing-exact", "deepseek/deepseek-v4-pro")


def test_p2_usage_keeps_current_pricing_table_and_uses_prior_window_for_comparison(tmp_path: Path) -> None:
    usage_db = tmp_path / "usage.db"
    migrate_usage_db(usage_db_path=usage_db, main_db_path=tmp_path / "missing.db")
    with sqlite3.connect(usage_db) as conn:
        conn.executemany(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES (?, ?, ?, 100, 20, 120, 1, 1000, NULL, '{}', ?)
            """,
            [
                ("score", "deepseek", "previous-only", "2026-08-08T18:00:00Z"),
                ("interpret", "deepseek", "current-only", "2026-08-09T13:00:00Z"),
            ],
        )
    catalog = _catalog(
        tmp_path,
        {
            name: {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
            for name in ("deepseek/previous-only", "deepseek/current-only")
        },
    )

    result = collect_usage(
        db_path=tmp_path / "missing.db",
        usage_db_path=usage_db,
        days=1,
        now=datetime(2026, 8, 10, 12, tzinfo=UTC),
        pricing_catalog=catalog,
    )

    assert result["totals"]["calls"] == 1
    assert {(row["provider"], row["model"]) for row in result["pricing_table"]} == {
        ("deepseek", "current-only"),
    }
    assert result["comparison"]["previous_known_cost_cny"] > 0
    assert result["comparison"]["available"] is True
    assert {row["stage"] for row in result["stage_costs"]} == {"interpret"}
    assert result["cost_groups"][0]["model"] == "current-only"
    assert result["daily"][0]["date"] == "2026-08-09"


def test_default_cost_audit_output_is_human_scoped_and_unpriced_cost_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_db = tmp_path / "radar.db"
    usage_db = tmp_path / "usage.db"
    migrate(main_db)
    migrate_usage_db(usage_db_path=usage_db, main_db_path=main_db)
    created_at = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(usage_db) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES ('interpret', 'unknown', 'missing', 100, 20, 120, 1, 1000,
                      NULL, '{}', ?)
            """,
            (created_at,),
        )
    catalog = _catalog(tmp_path, {})
    monkeypatch.setattr("airadar.admin.cost_audit.get_pricing", lambda: catalog)
    monkeypatch.setattr(cost_audit, "ANCHOR_CALLS", 0)
    monkeypatch.setattr(cost_audit, "ANCHOR_USD", 0)
    monkeypatch.setattr(cost_audit, "ANCHOR_CNY", 0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ai-radar",
            "admin",
            "cost-audit",
            "--db-path",
            str(main_db),
            "--usage-db-path",
            str(usage_db),
            "--days",
            "1",
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    output = capsys.readouterr().out

    assert exit_info.value.code == 0
    assert output.startswith("LLM cost reconciliation: CONSISTENT\n")
    assert "Scope: calculation consistency against the loaded catalog; tariff authority is not verified." in output
    assert "Window:" in output
    assert "Unpriced: 1 group, 1 call; cost unknown" in output
    assert "expected_usd=0.00000000" not in output
    assert "resolution=PASS" not in output


def test_supplement_tariffs_are_currency_explicit_and_interval_aware(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path, {})
    before = resolve_price(
        "ark",
        "deepseek-v4-pro-260425",
        catalog,
        effective_at="2026-05-26T23:59:59Z",
    )
    current = resolve_price(
        "ark",
        "deepseek-v4-pro-260425",
        catalog,
        effective_at="2026-05-27T00:00:00Z",
    )

    assert before is None
    assert current is not None
    assert current.source_currency == "CNY"
    assert current.source_input_per_million_tokens == 12
    assert current.source_cache_read_per_million_tokens == 0.1
    assert current.source_output_per_million_tokens == 24
    assert current.effective_from == "2026-05-27T00:00:00Z"
    assert current.effective_to is None


def test_supplement_interval_authoring_selects_boundaries_and_rejects_overlap(tmp_path: Path) -> None:
    def entry(start: str, end: str | None, price: float) -> PricingEntry:
        return PricingEntry(
            input_cost_per_token=price,
            cache_read_input_token_cost=price,
            output_cost_per_token=price,
            nominal=True,
            source="reviewed-test",
            source_currency="CNY",
            source_input_per_million_tokens=price * 1_000_000,
            source_cache_read_per_million_tokens=price * 1_000_000,
            source_output_per_million_tokens=price * 1_000_000,
            verified_at="2026-08-10",
            effective_from=start,
            effective_to=end,
        )

    old = entry("2026-05-27T00:00:00Z", "2026-09-01T00:00:00Z", 1e-6)
    new = entry("2026-09-01T00:00:00Z", None, 2e-6)
    intervals = {"ark/model": (old, new)}
    _validate_supplements(intervals)
    catalog = _catalog(tmp_path, {})
    catalog.supplements = intervals

    assert resolve_price("ark", "model", catalog, effective_at="2026-08-31T23:59:59Z").input_cost_per_token == 1e-6
    assert resolve_price("ark", "model", catalog, effective_at="2026-09-01T00:00:00Z").input_cost_per_token == 2e-6

    with pytest.raises(ValueError, match="overlapping"):
        _validate_supplements(
            {"ark/model": (old, entry("2026-08-31T00:00:00Z", None, 2e-6))}
        )
    with pytest.raises(ValueError, match="inverted"):
        _validate_supplements(
            {"ark/model": (entry("2026-09-02T00:00:00Z", "2026-09-01T00:00:00Z", 1e-6),)}
        )


def test_retired_pricing_env_is_rejected_instead_of_silently_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_RADAR_LLM_PRICING_JSON", "{}")

    with pytest.raises(ValueError, match="retired"):
        _catalog(tmp_path)


def test_deprecated_stored_cost_is_null_for_legacy_and_new_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage_db = tmp_path / "usage.db"
    with sqlite3.connect(usage_db) as conn:
        conn.executescript(
            """
            CREATE TABLE llm_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              stage TEXT NOT NULL CHECK (stage IN ('prefilter', 'score', 'enrich', 'interpret')),
              provider TEXT NOT NULL, model TEXT NOT NULL, item_id TEXT,
              input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0, input_item_count INTEGER NOT NULL DEFAULT 1,
              input_char_count INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,
              attribution_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
            );
            CREATE TABLE airadar_usage_migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO airadar_usage_migrations(id, applied_at)
            VALUES ('001_copy_main_llm_usage', '2026-08-10T00:00:00Z'),
                   ('002_add_interpret_stage', '2026-08-10T00:00:00Z');
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES ('score', 'deepseek', 'old', 1, 1, 2, 1, 1, 0, '{}',
                      '2026-08-10T00:00:00Z')
            """
        )
    migrate_usage_db(usage_db_path=usage_db, main_db_path=tmp_path / "missing.db")
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db))
    record_llm_usage(
        LlmUsageRecord(
            stage="score",
            provider="deepseek",
            model="new",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
        )
    )

    with sqlite3.connect(usage_db) as conn:
        rows = conn.execute("SELECT model, cost_usd FROM llm_usage ORDER BY id").fetchall()
        cost_column = next(row for row in conn.execute("PRAGMA table_info(llm_usage)") if row[1] == "cost_usd")

    assert rows == [("old", None), ("new", None)]
    assert cost_column[3] == 0  # notnull=false
