from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.admin import cost_audit
from airadar.admin.metrics import collect_metrics
from airadar.admin.usage import collect_usage
from airadar.db import get_conn, migrate
from airadar.llm_usage import DerivedCost, LlmUsageRecord, migrate_usage_db, record_llm_usage
from airadar.pricing import PricingCatalog, PricingEntry, get_pricing
from airadar.stage_common import insert_evaluation
from airadar.web.app import create_app

ADMIN_HEADERS = {"Cf-Access-Jwt-Assertion": "test"}


def _catalog(tmp_path: Path):  # noqa: ANN202
    return get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-pro": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            }
        },
        now=lambda: datetime(2026, 8, 11, tzinfo=UTC).timestamp(),
        persist=False,
    )


def _usage_db(tmp_path: Path) -> Path:
    path = tmp_path / "usage.db"
    migrate_usage_db(usage_db_path=path, main_db_path=tmp_path / "missing.db")
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES ('interpret', 'deepseek', 'deepseek-v4-pro', 100, 20, 120,
                      1, 1000, NULL, '{}', '2026-08-10T12:00:00Z')
            """
        )
    return path


def test_p2_admin_contract_restores_consumer_defined_aggregate_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_db = tmp_path / "radar.db"
    migrate(main_db)
    usage_db = _usage_db(tmp_path)
    catalog = _catalog(tmp_path)
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db))
    monkeypatch.setattr("airadar.admin.usage.get_pricing", lambda: catalog)

    client = TestClient(create_app(main_db))
    response = client.get("/api/v1/admin/usage", headers=ADMIN_HEADERS)
    page = client.get("/admin/usage", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert page.status_code == 200
    usage = response.json()["data"]
    assert usage["stage_costs"]
    assert usage["provider_costs"]
    assert usage["cost_groups"]
    assert "available" in usage["comparison"]
    assert usage["daily"]
    assert "days_with_calls" not in usage
    assert "generated_at" not in usage
    assert {"start_date", "end_date"}.isdisjoint(usage["window"])
    assert {
        "total_tokens",
        "input_items",
        "cost_status",
        "cost_statuses",
        "priced_cost_cny",
        "nominal_cost_cny",
    }.isdisjoint(usage["totals"])
    assert usage["totals"]["known_calls"] == (
        usage["totals"]["priced_calls"] + usage["totals"]["nominal_calls"]
    )
    assert usage["totals"]["calls"] == (
        usage["totals"]["known_calls"] + usage["totals"]["unpriced_calls"]
    )
    assert usage["totals"]["calls"] == 1
    assert usage["totals"]["cache_split_coverage"] == {
        "calls_with_split": 0,
        "calls_total": 1,
        "ratio": 0.0,
    }
    assert "成本分组与前窗对比" in page.text
    assert "按 Provider / 模型" in page.text
    assert "归因解释" not in page.text
    assert "最近 30 天总览" in page.text
    assert "来源单价" in page.text


def test_legacy_014_marker_skips_second_evaluation_rewrite_and_cleanup_is_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM airadar_migrations WHERE id IN "
            "('016_nullable_evaluation_cost', '017_cleanup_deprecated_cost_residue')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO airadar_migrations (id, applied_at) "
            "VALUES ('014_nullable_evaluation_cost', datetime('now'))"
        )
        conn.execute(
            """
            CREATE TRIGGER preserve_if_016_not_replayed
            AFTER INSERT ON item_evaluations BEGIN SELECT 1; END
            """
        )
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES ('score', 'legacy', 'old-writer', 1, 1, 2, 1, 1, 9.9, '{}',
                      '2026-08-11T00:00:00Z')
            """
        )

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as conn:
        markers = {
            row[0]
            for row in conn.execute(
                "SELECT id FROM airadar_migrations WHERE id LIKE '%nullable_evaluation_cost' "
                "OR id='017_cleanup_deprecated_cost_residue'"
            )
        }
        trigger = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='preserve_if_016_not_replayed'"
        ).fetchone()
        legacy_nonnull = conn.execute(
            "SELECT COUNT(*) FROM llm_usage WHERE cost_usd IS NOT NULL"
        ).fetchone()[0]

    assert trigger is not None
    assert markers == {
        "016_nullable_evaluation_cost",
        "017_cleanup_deprecated_cost_residue",
    }
    assert legacy_nonnull == 0


def test_usage_writer_accepts_rollout_numeric_but_raises_other_rejections(tmp_path: Path) -> None:
    usage_db = tmp_path / "usage.db"
    migrate_usage_db(usage_db_path=usage_db, main_db_path=tmp_path / "missing.db")

    with sqlite3.connect(usage_db) as conn:
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES ('score', 'legacy', 'numeric', 100, 20, 120, 1, 1000,
                      999, '{}', '2026-08-10T12:00:00Z')
            """
        )
        conn.execute(
            """
            CREATE TRIGGER reject_usage BEFORE INSERT ON llm_usage
            BEGIN SELECT RAISE(ABORT, 'injected rejection'); END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected rejection"):
        record_llm_usage(
            LlmUsageRecord(
                stage="score",
                provider="deepseek",
                model="rejected",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
            ),
            db_path=usage_db,
        )

    with sqlite3.connect(usage_db) as conn:
        conn.execute("DROP TRIGGER reject_usage")
        stored = conn.execute(
            "SELECT cost_usd FROM llm_usage WHERE provider='legacy'"
        ).fetchone()[0]
    assert stored == 999

    usage = collect_usage(
        db_path=tmp_path / "missing.db",
        usage_db_path=usage_db,
        now=datetime(2026, 8, 11, 12, tzinfo=UTC),
        pricing_catalog=_catalog(tmp_path),
    )
    assert usage["totals"]["known_cost_usd"] == pytest.approx(0)
    assert usage["unpriced"] == [{"provider": "legacy", "model": "numeric", "calls": 1}]


def test_evaluation_cost_is_unknown_at_source_and_absent_from_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at)
            VALUES ('s','Source','https://example.com/feed','T1',1,'{}','2026-08-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, published_at, fetched_at,
              content_text, content_hash, extra_json
            ) VALUES ('item-1','s','https://example.com/1','Item',
                      '2026-08-11T00:00:00Z','2026-08-11T00:00:00Z',
                      'content','hash-1','{}')
            """
        )
        insert_evaluation(
            conn,
            item_id="item-1",
            stage="scoring",
            ruleset_version="test.r1",
            model_id="deepseek-v4-pro",
            input_data={},
            output_data={},
            numeric_data={},
            latency_ms=10,
            error=None,
        )
        stored_cost = conn.execute(
            "SELECT cost_usd FROM item_evaluations WHERE item_id='item-1'"
        ).fetchone()[0]
        conn.commit()

    metrics = collect_metrics(
        db_path=db_path,
        pipeline_log_dir=tmp_path / "logs",
        access_log_paths=[],
        now=datetime(2026, 8, 11, 12, tzinfo=UTC),
    )
    assert stored_cost is None
    assert "cost_usd" not in metrics["pipeline"]["stages"]["scoring"]


def test_audit_raw_rate_oracle_detects_wrong_production_rate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage_db = _usage_db(tmp_path)
    catalog = _catalog(tmp_path)
    raw_quote = catalog.litellm["deepseek/deepseek-v4-pro"]
    assert raw_quote["input_cost_per_token"] == 1e-6
    assert raw_quote["output_cost_per_token"] == 2e-6

    wrong_quote = PricingEntry(
        input_cost_per_token=1e-6,
        cache_read_input_token_cost=0.1e-6,
        output_cost_per_token=1e-6,
        nominal=False,
        source="fault-injected production extraction",
        source_currency="USD",
        source_input_per_million_tokens=1,
        source_cache_read_per_million_tokens=0.1,
        source_output_per_million_tokens=1,
        fetched_at="2026-08-11",
        matched_key="deepseek/deepseek-v4-pro",
    )

    def wrong_derived(row: object, *, catalog: object = None) -> DerivedCost:  # noqa: ARG001
        return DerivedCost(
            cost_usd=0.00012,
            status="priced",
            freshness="fresh",
            quote=wrong_quote,
            cached_input_tokens=None,
            uncached_input_tokens=None,
        )

    monkeypatch.setattr(cost_audit, "derive_cost_usd", wrong_derived)
    monkeypatch.setattr("airadar.admin.usage.derive_cost_usd", wrong_derived)

    report = cost_audit.run_cost_audit(
        db_path=tmp_path / "missing.db",
        usage_db_path=usage_db,
        days=2,
        now=datetime(2026, 8, 11, 12, tzinfo=UTC),
        catalog=catalog,
        require_anchor=False,
    )

    assert report.passed is False
    group_line = next(line for line in report.kv_lines if line.startswith("group "))
    assert "expected_usd=0.00014" in group_line
    assert "derived_usd=0.00012" in group_line
    assert group_line.endswith("FAIL")


def test_cost_audit_blocks_compatibility_exit_while_deprecated_costs_remain(
    tmp_path: Path,
) -> None:
    main_db = tmp_path / "radar.db"
    migrate(main_db)
    usage_db = _usage_db(tmp_path)
    with sqlite3.connect(usage_db) as conn:
        conn.execute("UPDATE llm_usage SET cost_usd=1.0")
    with sqlite3.connect(main_db) as conn:
        conn.execute(
            """
            INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at)
            VALUES ('s','Source','https://example.com/feed','T1',1,'{}','2026-08-11T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, published_at, fetched_at,
              content_text, content_hash, extra_json
            ) VALUES ('item-1','s','https://example.com/1','Item',
                      '2026-08-11T00:00:00Z','2026-08-11T00:00:00Z',
                      'content','hash-1','{}')
            """
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            ) VALUES ('item-1','scoring','test.r1','legacy','{}','{}','{}',1,2.0,
                      '2026-08-11T00:00:00Z',NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES ('score','legacy','old-writer',1,1,2,1,1,3.0,'{}',
                      '2026-08-11T00:00:00Z')
            """
        )

    report = cost_audit.run_cost_audit(
        db_path=main_db,
        usage_db_path=usage_db,
        days=2,
        now=datetime(2026, 8, 11, 12, tzinfo=UTC),
        catalog=_catalog(tmp_path),
        require_anchor=False,
    )

    assert report.passed is False
    assert report.json_payload["deprecated_cost_residue"] == {
        "active_usage": 1,
        "legacy_main_usage": 1,
        "item_evaluations": 1,
        "cleanup_ready": False,
    }
    assert (
        "deprecated-cost-residue active_usage=1 legacy_main_usage=1 "
        "item_evaluations=1 CLEANUP_REQUIRED"
    ) in report.kv_lines
    assert any(
        "Deprecated stored-cost residue: active usage 1, legacy main usage 1, "
        "item evaluations 1; CLEANUP REQUIRED" in line
        for line in report.human_lines
    )


def test_audit_raw_rate_oracle_converts_supplement_source_tariff_independently(
    tmp_path: Path,
) -> None:
    usage_db = _usage_db(tmp_path)
    with sqlite3.connect(usage_db) as conn:
        conn.execute(
            "UPDATE llm_usage SET provider='ark', model='source-rate-model'"
        )
    wrong_usd_projection = PricingEntry(
        input_cost_per_token=10e-6,
        cache_read_input_token_cost=1e-6,
        output_cost_per_token=20e-6,
        nominal=True,
        source="fault-injected USD projection",
        source_currency="CNY",
        source_input_per_million_tokens=1,
        source_cache_read_per_million_tokens=0.1,
        source_output_per_million_tokens=2,
        verified_at="2026-08-10",
        effective_from="2026-05-27T00:00:00Z",
        matched_key="ark/source-rate-model",
    )
    catalog = PricingCatalog(
        litellm={},
        supplements={"ark/source-rate-model": (wrong_usd_projection,)},
        freshness="fresh",
        source="test",
        fetched_at=None,
        observed_at=datetime(2026, 8, 11, tzinfo=UTC).timestamp(),
    )

    report = cost_audit.run_cost_audit(
        db_path=tmp_path / "missing.db",
        usage_db_path=usage_db,
        days=2,
        now=datetime(2026, 8, 11, 12, tzinfo=UTC),
        catalog=catalog,
        require_anchor=False,
    )

    assert report.passed is False
    group_line = next(line for line in report.kv_lines if line.startswith("group "))
    assert "expected_usd=1.94444444444444" in group_line
    assert "derived_usd=0.0014" in group_line
    assert group_line.endswith("FAIL")
