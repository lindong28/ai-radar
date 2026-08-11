from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar import cli
from airadar.admin import cost_audit
from airadar.admin.cost_audit import run_cost_audit
from airadar.db import migrate
from airadar.llm_usage import migrate_usage_db
from airadar.pricing import PricingCatalog, get_pricing
from airadar.web.app import create_app

ADMIN_HEADERS = {"Cf-Access-Jwt-Assertion": "test"}


def _stale_catalog_with_fresh_supplements(tmp_path: Path) -> PricingCatalog:
    observed_at = datetime(2026, 8, 10, 12, tzinfo=UTC).timestamp()
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
            "deepseek/deepseek-v4-pro": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            },
            # This bare entry is a collision trap: an ARK model with an unknown
            # suffix must not resolve through another provider's catalog row.
            "deepseek-v4-pro": {
                "input_cost_per_token": 1e-6,
                "cache_read_input_token_cost": 0.1e-6,
                "output_cost_per_token": 2e-6,
            },
        },
        now=lambda: observed_at,
        persist=False,
    )
    catalog.freshness = "stale"
    catalog.source = "expired-cache"
    catalog.fetched_at = observed_at - 8 * 24 * 60 * 60
    return catalog


def _usage_db(path: Path, rows: list[tuple[object, ...]], *, with_cache_column: bool) -> Path:
    migrate_usage_db(usage_db_path=path, main_db_path=path.with_name("missing-main.db"))
    with sqlite3.connect(path) as conn:
        if with_cache_column:
            conn.execute("ALTER TABLE llm_usage ADD COLUMN cached_input_tokens INTEGER")
        cache_column = ", cached_input_tokens" if with_cache_column else ""
        cache_value = ", ?" if with_cache_column else ""
        parameters = rows if with_cache_column else [row[:-1] for row in rows]
        conn.executemany(
            f"""
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
              {cache_column}
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 1000, NULL, '{{}}', ? {cache_value})
            """,
            parameters,
        )
    return path


def _row(
    stage: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    created_at: str,
    cached_input_tokens: int | None = None,
) -> tuple[object, ...]:
    return (
        stage,
        provider,
        model,
        input_tokens,
        output_tokens,
        input_tokens + output_tokens,
        created_at,
        cached_input_tokens,
    )


def test_p1_consumer_boundary_state_sweep_uses_real_sql_html_and_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    now = datetime.now(UTC).replace(microsecond=0)
    created_at = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    main_db = tmp_path / "radar.db"
    migrate(main_db)
    catalog = _stale_catalog_with_fresh_supplements(tmp_path)
    mixed_usage_db = _usage_db(
        tmp_path / "mixed-usage.db",
        [
            _row("interpret", "deepseek", "deepseek-v4-pro", 100, 20, created_at, 80),
            _row("interpret", "ark", "deepseek-v4-pro-260426", 100, 20, created_at, None),
            _row("score", "ark", "deepseek-v4-flash-260425", 1_000_000, 0, created_at, 0),
        ],
        with_cache_column=True,
    )
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(mixed_usage_db))
    monkeypatch.setattr("airadar.admin.usage.get_pricing", lambda: catalog)
    monkeypatch.setattr("airadar.admin.cost_audit.get_pricing", lambda: catalog)
    client = TestClient(create_app(main_db))

    api = client.get("/api/v1/admin/usage", headers=ADMIN_HEADERS)
    page = client.get("/admin/usage", headers=ADMIN_HEADERS)

    assert api.status_code == 200
    assert page.status_code == 200
    usage = api.json()["data"]
    check(usage["totals"]["calls"] == 3, f"total calls expected 3, got {usage['totals']['calls']}")
    check(
        usage["totals"]["priced_cost_usd"] == pytest.approx(0.000068),
        f"cached priced cost expected 0.000068, got {usage['totals']['priced_cost_usd']}",
    )
    check(
        usage["totals"]["nominal_cost_usd"] == pytest.approx(1 / 7.2),
        f"nominal cost expected {1 / 7.2}, got {usage['totals']['nominal_cost_usd']}",
    )
    check(usage["totals"]["cached_input_tokens"] is None, "mixed cache coverage must stay unknown")
    check(
        usage["totals"]["cache_split_coverage"]
        == {"calls_with_split": 2, "calls_total": 3, "ratio": 0.666667},
        f"cache coverage mismatch: {usage['totals']['cache_split_coverage']}",
    )
    check(
        usage["unpriced"] == [{"provider": "ark", "model": "deepseek-v4-pro-260426", "calls": 1}],
        f"unknown ARK suffix must be unpriced, got {usage['unpriced']}",
    )
    check(
        {row["status"] for row in usage["pricing_table"]} == {"priced", "nominal", "unpriced"},
        f"pricing table lost state split: {usage['pricing_table']}",
    )
    check(bool(usage["stage_costs"]), "P2 API lost stage aggregation")
    check(bool(usage["provider_costs"]), "P2 API lost provider aggregation")
    check(bool(usage["cost_groups"]), "P2 API lost provider/model aggregation")
    check("available" in usage["comparison"], "P2 API lost comparison gate")
    check(bool(usage["daily"]), "P2 API lost daily series")
    check("priced 实价成本" in page.text, "HTML group boundary lost priced label")
    check("nominal 挂牌价成本" in page.text, "HTML group boundary lost nominal label")
    check("deepseek-v4-pro-260426（1 次）" in page.text, "HTML lost unpriced identity")
    check("未采集" in page.text, "HTML lost unknown cache state")
    check("2/3" in page.text, "HTML lost cache coverage")
    check("成本分组与前窗对比" in page.text, "HTML lost P2 comparison consumer")

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
            str(mixed_usage_db),
            "--format",
            "kv",
        ],
    )
    with pytest.raises(SystemExit) as cli_exit:
        cli.main()
    cli_output = capsys.readouterr().out
    check(cli_exit.value.code == 0, f"CLI expected exit 0, got {cli_exit.value.code}")
    check("status=priced" in cli_output, "CLI lost priced label")
    check("status=nominal" in cli_output, "CLI lost nominal label")
    check("status=unpriced" in cli_output, "CLI lost unpriced label")
    check(
        "uncached_input_tokens=20 cached_input_tokens=80" in cli_output,
        "CLI lost cached/uncached split",
    )
    check("model=deepseek-v4-pro-260426" in cli_output, "CLI lost unpriced group identity")
    check("cost-audit PASS" in cli_output, "CLI reconciliation did not pass")

    nominal_only_db = _usage_db(
        tmp_path / "nominal-only-usage.db",
        [_row("score", "ark", "deepseek-v4-flash-260425", 1_000_000, 0, created_at, 0)],
        with_cache_column=True,
    )
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(nominal_only_db))
    nominal_client = TestClient(create_app(main_db))
    nominal_api = nominal_client.get("/api/v1/admin/usage", headers=ADMIN_HEADERS)
    nominal_page = nominal_client.get("/admin/usage", headers=ADMIN_HEADERS)

    assert nominal_api.status_code == 200
    nominal_usage = nominal_api.json()["data"]
    check(nominal_usage["nominal_share"] == 1.0, f"nominal-only share expected 1, got {nominal_usage['nominal_share']}")
    check(
        nominal_usage["pricing_freshness"] == ["fresh"],
        f"unused stale catalog leaked into active freshness: {nominal_usage['pricing_freshness']}",
    )
    check("nominal 挂牌价" in nominal_page.text, "nominal-only HTML lost nominal label")
    check(
        "当前成本含按过期价估算的条目" not in nominal_page.text,
        "nominal-only HTML warns about an unused stale catalog",
    )
    check("LiteLLM 定价源刷新失败" in nominal_page.text, "stale source state is hidden")

    assert failures == [], "consumer sweep failures:\n- " + "\n- ".join(failures)


def test_cost_audit_reuses_one_usage_snapshot_when_a_writer_commits_between_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_at = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    usage_db = _usage_db(
        tmp_path / "usage.db",
        [_row("interpret", "deepseek", "deepseek-v4-pro", 100, 20, created_at)],
        with_cache_column=False,
    )
    catalog = _stale_catalog_with_fresh_supplements(tmp_path)
    real_collect_usage = cost_audit.collect_usage
    inserted = False

    def commit_between_reads(**kwargs: object) -> dict[str, object]:
        nonlocal inserted
        if not inserted:
            inserted = True
            with sqlite3.connect(usage_db) as conn:
                conn.execute(
                    """
                    INSERT INTO llm_usage (
                      stage, provider, model, input_tokens, output_tokens, total_tokens,
                      input_item_count, input_char_count, cost_usd, attribution_json, created_at
                    ) VALUES ('interpret', 'deepseek', 'deepseek-v4-pro', 100, 20, 120,
                              1, 1000, NULL, '{}', ?)
                    """,
                    (created_at,),
                )
        return real_collect_usage(**kwargs)

    monkeypatch.setattr(cost_audit, "collect_usage", commit_between_reads)

    report = run_cost_audit(
        db_path=tmp_path / "missing-main.db",
        usage_db_path=usage_db,
        now=datetime.now(UTC),
        catalog=catalog,
        require_anchor=False,
    )

    assert inserted is True
    assert report.passed is True
    assert report.lines[-1].startswith("cost-audit PASS")
