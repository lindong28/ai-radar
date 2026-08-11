from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from airadar.admin.cost_audit import run_cost_audit
from airadar.llm_usage import migrate_usage_db
from airadar.pricing import get_pricing


def test_cost_audit_reconciles_raw_derived_and_admin_usage(tmp_path: Path) -> None:
    usage_db = tmp_path / "llm_usage.db"
    migrate_usage_db(usage_db_path=usage_db, main_db_path=tmp_path / "missing-main.db")
    with sqlite3.connect(usage_db) as conn:
        conn.executemany(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 1000, NULL, '{}', ?)
            """,
            [
                ("interpret", "deepseek", "deepseek-v4-pro", 100, 20, 120, "2026-08-10T01:00:00Z"),
                ("score", "deepseek", "deepseek-v4-flash", 200, 30, 230, "2026-08-10T02:00:00Z"),
            ],
        )
        anchor_input_total = 16_145_104
        anchor_output_total = 250_431
        input_base, input_remainder = divmod(anchor_input_total, 222)
        output_base, output_remainder = divmod(anchor_output_total, 222)
        anchor_rows = []
        for index in range(222):
            input_tokens = input_base + (1 if index < input_remainder else 0)
            output_tokens = output_base + (1 if index < output_remainder else 0)
            created_at = "2026-08-09T10:52:42Z" if index < 221 else "2026-08-09T16:26:29Z"
            anchor_rows.append(
                (
                    "interpret",
                    "deepseek",
                    "deepseek-v4-pro",
                    input_tokens,
                    output_tokens,
                    input_tokens + output_tokens,
                    created_at,
                )
            )
        conn.executemany(
            """
            INSERT INTO llm_usage (
              stage, provider, model, input_tokens, output_tokens, total_tokens,
              input_item_count, input_char_count, cost_usd, attribution_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 1000, NULL, '{}', ?)
            """,
            anchor_rows,
        )
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=lambda: {
                "deepseek/deepseek-v4-pro": {
                    "input_cost_per_token": 4.35e-7,
                    "cache_read_input_token_cost": 3.625e-9,
                    "output_cost_per_token": 8.7e-7,
            },
            "deepseek/deepseek-v4-flash": {
                "input_cost_per_token": 0.5e-6,
                "cache_read_input_token_cost": 0.05e-6,
                "output_cost_per_token": 1e-6,
            },
        },
        persist=False,
    )

    report = run_cost_audit(
        db_path=tmp_path / "missing-main.db",
        usage_db_path=usage_db,
        days=2,
        now=datetime(2026, 8, 10, 12, tzinfo=UTC),
        catalog=catalog,
    )

    assert report.passed is True
    assert any("expected_usd=" in line and "derived_usd=" in line and "admin_usage_usd=" in line for line in report.lines)
    anchor = report.json_payload["anchor"]
    assert anchor["derived_cny_actual"] == 52.135166
    assert anchor["cny_delta_vs_official"] == 2.197268
    assert anchor["cny_delta_pct_vs_official"] == 4.4
    anchor_line = next(line for line in report.kv_lines if line.startswith("anchor "))
    assert "derived_cny_actual=52.135166" in anchor_line
    assert "cny_delta_vs_official=2.197268" in anchor_line
    assert "cny_delta_pct_vs_official=4.400000%" in anchor_line
    assert any(
        "difference: +2.197268 CNY (+4.400000% vs official CNY actual)" in line
        for line in report.human_lines
    )
    assert report.lines[-1].startswith("cost-audit PASS")
