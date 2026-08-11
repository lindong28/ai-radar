from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airadar.admin.alerts import run_pricing_notifications
from airadar.admin.cost_report import (
    _durable_processing_counts,
    _window_metering,
    build_cost_report,
    compact_branch_samples,
    evaluate_a6_cost,
    format_cost_report,
    report_window,
)
from airadar.admin.usage import collect_usage
from airadar.pricing import PricingCatalog, PricingEntry, get_pricing


def _catalog(tmp_path: Path, *, price: float = 1e-3, freshness: str = "fresh"):  # noqa: ANN202
    return get_pricing(
        cache_path=tmp_path / f"catalog-{price}.json",
        fetcher=lambda: {
            "deepseek/model": {
                "input_cost_per_token": price,
                "cache_read_input_token_cost": price,
                "output_cost_per_token": price,
            }
        },
        persist=False,
    )


def _row(
    created: datetime,
    *,
    tokens: int = 100,
    cached: int | None = None,
    stage: str = "interpret",
) -> dict[str, object]:
    return {
        "stage": stage,
        "provider": "deepseek",
        "model": "model",
        "input_tokens": tokens,
        "cached_input_tokens": cached,
        "output_tokens": 0,
        "input_char_count": 100,
        "attribution_json": "{}",
        "created_at": created.isoformat(),
    }


def _pipeline_log(
    log_dir: Path,
    stamp: str,
    *,
    inserted: int,
    failed: int = 0,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"pipeline-{stamp}.log").write_text(
        f"=== attempted=11 inserted={inserted} failed={failed}\n"
        f"[{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}T12:00:00] "
        f"=== PIPELINE DONE (failed={failed}) ===\n",
        encoding="utf-8",
    )


def _tariff_change_catalog(
    now: datetime, *, boundary: str = "2026-08-03T00:00:00Z"
) -> PricingCatalog:
    def entry(price: float, start: str, end: str | None) -> PricingEntry:
        return PricingEntry(
            input_cost_per_token=price,
            cache_read_input_token_cost=price,
            output_cost_per_token=price,
            nominal=True,
            source="fixture",
            source_currency="USD",
            source_input_per_million_tokens=price * 1_000_000,
            source_cache_read_per_million_tokens=price * 1_000_000,
            source_output_per_million_tokens=price * 1_000_000,
            verified_at="2026-08-01",
            effective_from=start,
            effective_to=end,
        )

    return PricingCatalog(
        litellm={},
        supplements={
            "deepseek/model": (
                entry(1e-3, "2026-07-01T00:00:00Z", boundary),
                entry(2e-3, boundary, None),
            )
        },
        freshness="fresh",
        source="fixture",
        fetched_at=now.timestamp(),
        observed_at=now.timestamp(),
    )


def _complete_metering(start: datetime, end: datetime) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    cursor = start
    while cursor < end:
        result[cursor.date().isoformat()] = {
            "pipeline_runs": 1,
            "completed_runs": 1,
            "failures": 0,
            "complete": True,
        }
        cursor += timedelta(days=1)
    return result


def test_previous_shanghai_natural_week_and_rolling_window_are_distinct() -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    start, end, kind = report_window(now, None)
    assert (start.isoformat(), end.isoformat(), kind) == (
        "2026-08-03T00:00:00+08:00",
        "2026-08-10T00:00:00+08:00",
        "previous-shanghai-week",
    )
    rolling_start, rolling_end, rolling_kind = report_window(now, 3)
    assert rolling_end == now
    assert rolling_start == now - timedelta(days=3)
    assert rolling_kind == "rolling-3d"


def test_usage_and_weekly_report_reprice_both_windows_at_evaluation_time() -> None:
    now = datetime.fromisoformat("2026-08-10T00:00:00+00:00")
    rows = [
        _row(datetime.fromisoformat("2026-08-02T12:00:00+00:00")),
        _row(datetime.fromisoformat("2026-08-09T12:00:00+00:00")),
    ]
    catalog = _tariff_change_catalog(now)

    usage = collect_usage(days=7, now=now, pricing_catalog=catalog, rows_snapshot=rows)
    report = build_cost_report(
        window_days=7,
        now=now,
        pricing_catalog=catalog,
        rows_snapshot=rows,
        metering_snapshot=_complete_metering(now - timedelta(days=14), now),
    )

    assert usage["comparison"]["known_cost_change_ratio"] == 0.0
    assert report["comparison"]["known_cost_change_ratio"] == 0.0
    text = format_cost_report(report)
    assert "环比：较前一等长窗口 +0.0%" in text
    assert "两窗均按当前费率、cache 未命中重算" in text
    assert "处理暴露由持久数据确认" in text


def test_default_monday_report_prices_at_send_time_after_window_end_tariff_change() -> None:
    send_time = datetime.fromisoformat("2026-08-10T09:17:00+08:00")
    rows = [
        _row(datetime.fromisoformat("2026-08-02T12:00:00+00:00")),
        _row(datetime.fromisoformat("2026-08-09T12:00:00+00:00")),
    ]
    report = build_cost_report(
        now=send_time,
        pricing_catalog=_tariff_change_catalog(
            send_time, boundary="2026-08-10T01:00:00Z"
        ),
        rows_snapshot=rows,
        metering_snapshot=_complete_metering(
            datetime.fromisoformat("2026-07-27T00:00:00+08:00"),
            datetime.fromisoformat("2026-08-10T00:00:00+08:00"),
        ),
    )

    assert report["window"]["end"] == "2026-08-10T00:00:00+08:00"
    assert report["totals"]["known_cost_cny"] == 1.44
    assert report["stage_costs"][0]["comparison"]["current_known_cost_cny"] == 1.44
    assert "结论：已知成本约 ¥1.44" in format_cost_report(report)

    before_new_tariff = build_cost_report(
        now=send_time,
        pricing_catalog=_tariff_change_catalog(
            send_time, boundary="2026-08-10T02:00:00Z"
        ),
        rows_snapshot=rows,
        metering_snapshot=_complete_metering(
            datetime.fromisoformat("2026-07-27T00:00:00+08:00"),
            datetime.fromisoformat("2026-08-10T00:00:00+08:00"),
        ),
    )
    assert before_new_tariff["totals"]["known_cost_cny"] == 0.72
    assert "结论：已知成本约 ¥0.72" in format_cost_report(before_new_tariff)


def test_a6_tariff_only_change_does_not_change_firing_and_variable_coverage_arms(tmp_path: Path) -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    rows = [_row(now - timedelta(hours=1), tokens=100)]
    for days in range(1, 15):
        rows.append(_row(now.replace(hour=0, minute=0) - timedelta(days=days) + timedelta(hours=1), tokens=100))
    low = evaluate_a6_cost(rows, now=now, catalog=_catalog(tmp_path, price=1e-4), daily_floor_cny=0)
    high = evaluate_a6_cost(rows, now=now, catalog=_catalog(tmp_path, price=2e-4), daily_floor_cny=0)
    assert low["firing"] is high["firing"] is False
    assert high["known_cost_cny"] == 2 * low["known_cost_cny"]

    rows[0]["cached_input_tokens"] = 0
    for index, row in enumerate(rows[1:], start=1):
        row["cached_input_tokens"] = 0 if index % 3 == 0 else None
    normalized = evaluate_a6_cost(rows, now=now, catalog=_catalog(tmp_path), daily_floor_cny=0)
    assert normalized["evaluable"] is True
    assert normalized["baseline_days"] == 14
    assert normalized["cache_basis"] == "all-miss"


def test_v37_tariff_only_delta_notifies_d3_without_changing_a6_page_count(tmp_path: Path) -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    rows = [_row(now - timedelta(hours=1), tokens=100)]
    for days in range(1, 15):
        rows.append(_row(now.replace(hour=0, minute=0) - timedelta(days=days) + timedelta(hours=1), tokens=100))
    low_catalog = _catalog(tmp_path, price=1e-4)
    high_catalog = _catalog(tmp_path, price=2e-4)
    low_a6 = evaluate_a6_cost(rows, now=now, catalog=low_catalog, daily_floor_cny=0)
    high_a6 = evaluate_a6_cost(rows, now=now, catalog=high_catalog, daily_floor_cny=0)
    state = tmp_path / "v37-d3-state.json"
    notices: list[str] = []

    def sender(text: str, **kwargs: object) -> dict[str, object]:
        notices.append(text)
        return {"skipped": False}

    low_report = build_cost_report(window_days=1, now=now, pricing_catalog=low_catalog, rows_snapshot=rows)
    high_report = build_cost_report(window_days=1, now=now, pricing_catalog=high_catalog, rows_snapshot=rows)
    run_pricing_notifications(low_report, state_path=state, send=sender)
    run_pricing_notifications(high_report, state_path=state, send=sender)

    assert len(notices) == 1
    assert "目录价发生变化" in notices[0]
    assert low_a6["firing"] is high_a6["firing"] is False
    assert int(bool(low_a6["firing"])) == int(bool(high_a6["firing"])) == 0


def test_a6_current_window_excludes_left_endpoint_includes_right_and_uses_completed_utc_days(tmp_path: Path) -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    rows = [
        _row(now - timedelta(hours=24), tokens=100_000),
        _row(now, tokens=10),
    ]
    for days in range(1, 4):
        rows.append(_row(now.replace(hour=0, minute=0) - timedelta(days=days), tokens=10))
    result = evaluate_a6_cost(rows, now=now, catalog=_catalog(tmp_path), daily_floor_cny=0)
    assert result["known_cost_cny"] == 0.072
    assert result["baseline_days"] >= 3


def test_weekly_formatter_carries_qualification_and_all_branch_samples(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    report = build_cost_report(
        window_days=1,
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=[_row(now - timedelta(hours=1))],
    )
    text = format_cost_report(report)
    assert "并非账单实付" in text
    assert "Top 驱动" in text
    assert "单篇解读" in text
    assert "环比" in text
    samples = compact_branch_samples(report)
    assert set(samples) == {"nominal", "unpriced", "stale", "cache coverage=0"}
    assert "nominal 目录价估算约 ¥0.63（87.6%）" in samples["nominal"]
    assert "无数据" in samples["cache coverage=0"]
    assert "unknown-model" in samples["unpriced"]
    assert "stale" in samples["stale"]


def test_weekly_report_fills_shanghai_days_and_suppresses_comparison_for_processing_gap(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    log_dir = tmp_path / "logs"
    for day in range(3, 10):
        _pipeline_log(
            log_dir,
            f"202608{day:02d}-120000",
            inserted=269 if day == 8 else 10,
        )
    for day in range(27, 32):
        _pipeline_log(log_dir, f"202607{day:02d}-120000", inserted=10)
    for day in range(1, 3):
        _pipeline_log(log_dir, f"202608{day:02d}-120000", inserted=10)
    current_rows = [
        _row(datetime.fromisoformat(f"2026-08-{day:02d}T12:00:00+08:00"))
        for day in (3, 4, 5, 6, 7, 9)
    ]
    previous_rows = [
        _row(datetime.fromisoformat(f"2026-07-{day:02d}T12:00:00+08:00"))
        for day in range(27, 32)
    ] + [
        _row(datetime.fromisoformat(f"2026-08-{day:02d}T12:00:00+08:00"))
        for day in (1, 2)
    ]

    report = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=[*previous_rows, *current_rows],
        pipeline_log_dir=log_dir,
        fetched_counts_snapshot={
            **{f"2026-07-{day:02d}": 10 for day in range(27, 32)},
            "2026-08-01": 10,
            "2026-08-02": 10,
            **{f"2026-08-{day:02d}": (269 if day == 8 else 10) for day in range(3, 10)},
        },
        processed_counts_snapshot={
            **{f"2026-07-{day:02d}": 1 for day in range(27, 32)},
            "2026-08-01": 1,
            "2026-08-02": 1,
            **{f"2026-08-{day:02d}": (0 if day == 8 else 1) for day in range(3, 10)},
        },
    )

    assert [row["date"] for row in report["daily"]] == [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-08-07",
        "2026-08-08",
        "2026-08-09",
    ]
    stalled = report["daily"][5]
    assert stalled["calls"] == 0
    assert stalled["known_cost_cny"] == 0
    assert stalled["pipeline_runs"] == 1
    assert stalled["fetch_inserted"] == 269
    assert stalled["activity_state"] == "processing_stall"
    assert report["comparison"]["available"] is False
    assert report["comparison"]["reason"] == "processing_exposure_gap"
    assert report["comparison"]["processing_gap_days"] == ["2026-08-08"]
    text = format_cost_report(report)
    assert "2026-08-08 ¥0.00（pipeline 1 轮，fetch 新增 269，LLM 0 次：处理停滞）" in text
    assert "环比：不可用——本窗含处理停滞日 2026-08-08" in text


def test_weekly_report_blocks_comparison_when_a_day_has_usage_and_metering_failure(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    previous_start = datetime.fromisoformat("2026-07-27T00:00:00+08:00")
    activity = _complete_metering(previous_start, datetime.fromisoformat("2026-08-10T00:00:00+08:00"))
    activity["2026-08-09"]["failures"] = 1
    report = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=[
            _row(datetime.fromisoformat("2026-08-02T12:00:00+08:00")),
            _row(datetime.fromisoformat("2026-08-09T12:00:00+08:00")),
        ],
        metering_snapshot=activity,
    )

    assert report["comparison"]["available"] is False
    assert report["comparison"]["reason"] == "processing_exposure_unknown"
    assert report["comparison"]["processing_unknown_days"] == ["2026-08-09"]


def test_seven_day_metering_is_not_complete_with_only_one_observed_day(tmp_path: Path) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    report = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=[_row(datetime.fromisoformat("2026-08-09T12:00:00+08:00"))],
        metering_snapshot={
            "2026-08-09": {
                "pipeline_runs": 1,
                "completed_runs": 1,
                "failures": 0,
                "complete": True,
            }
        },
    )

    assert report["metering"]["observed_days"] == 1
    assert report["metering"]["expected_days"] == 7
    assert report["metering"]["complete"] is False


def test_durable_processing_exposure_permits_retention_gap_and_blocks_real_stall(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-08-10T09:17:00+08:00")
    previous_start = datetime.fromisoformat("2026-07-27T00:00:00+08:00")
    end = datetime.fromisoformat("2026-08-10T00:00:00+08:00")
    rows = []
    fetched: dict[str, int] = {}
    processed: dict[str, int] = {}
    cursor = previous_start
    while cursor < end:
        day = cursor.date().isoformat()
        rows.append(_row(cursor + timedelta(hours=12)))
        fetched[day] = 10
        processed[day] = 10
        cursor += timedelta(days=1)
    retained_logs = _complete_metering(end - timedelta(days=7), end)

    permitted = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=rows,
        fetched_counts_snapshot=fetched,
        processed_counts_snapshot=processed,
        metering_snapshot=retained_logs,
    )
    assert permitted["comparison"]["available"] is True
    assert permitted["comparison"]["processing_gap_days"] == []
    assert permitted["comparison"]["previous_processing_gap_days"] == []
    assert "计量失败日志覆盖 7/14 日，未覆盖日的漏记风险未完全排除" in format_cost_report(
        permitted
    )

    stall_day = "2026-08-08"
    blocked_rows = [row for row in rows if not str(row["created_at"]).startswith(stall_day)]
    blocked_processed = {**processed, stall_day: 0}
    blocked = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=blocked_rows,
        fetched_counts_snapshot=fetched,
        processed_counts_snapshot=blocked_processed,
        metering_snapshot=retained_logs,
    )
    assert blocked["comparison"]["available"] is False
    assert blocked["comparison"]["reason"] == "processing_exposure_gap"
    assert blocked["comparison"]["processing_gap_days"] == [stall_day]
    assert "本窗含处理停滞日 2026-08-08" in format_cost_report(blocked)


def test_durable_processing_exposure_blocks_partial_interpret_stall_and_ignores_error_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "partial-stall.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE item_evaluations (
          stage TEXT NOT NULL,
          numeric_json TEXT,
          evaluated_at TEXT NOT NULL,
          error TEXT
        );
        CREATE TABLE wechat_interpretations (
          processed_at TEXT NOT NULL,
          error TEXT
        );
        """
    )
    for stage in ("prefilter", "scoring", "enrich"):
        conn.execute(
            "INSERT INTO item_evaluations VALUES (?, ?, ?, NULL)",
            (
                stage,
                '{"is_ai_related":true}' if stage == "prefilter" else "{}",
                "2026-08-08T04:00:00Z",
            ),
        )
    conn.execute(
        "INSERT INTO wechat_interpretations VALUES (?, ?)",
        ("2026-08-08T04:05:00Z", "interpret provider timed out"),
    )
    conn.commit()
    conn.close()

    start = datetime.fromisoformat("2026-08-08T00:00:00+08:00")
    end = start + timedelta(days=1)
    processed = _durable_processing_counts(db_path, start, end)
    assert processed == {
        "2026-08-08": {
            "prefilter": 1,
            "prefilter_candidates": 1,
            "score": 1,
            "enrich": 1,
            "interpret": 0,
        }
    }

    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    rows = [
        _row(datetime.fromisoformat("2026-08-08T12:00:00+08:00"), stage="prefilter"),
        _row(datetime.fromisoformat("2026-08-08T12:01:00+08:00"), stage="score"),
        _row(datetime.fromisoformat("2026-08-08T12:02:00+08:00"), stage="enrich"),
    ]
    report = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=rows,
        fetched_counts_snapshot={"2026-08-08": 10},
        wechat_fetched_counts_snapshot={"2026-08-08": 2},
        processed_counts_snapshot=processed,
        metering_snapshot={
            "2026-08-08": {
                "pipeline_runs": 96,
                "completed_runs": 96,
                "failures": 0,
                "complete": True,
            }
        },
    )

    stalled = next(row for row in report["daily"] if row["date"] == "2026-08-08")
    assert stalled["activity_state"] == "processing_stall"
    assert stalled["stalled_stages"] == ["interpret"]
    assert stalled["calls"] == 3
    assert report["comparison"]["available"] is False
    assert report["comparison"]["processing_gap_stages"] == {
        "2026-08-08": ["interpret"]
    }
    text = format_cost_report(report)
    assert "2026-08-08 interpret 成功产出停滞" in text


def test_a6_metering_checks_both_calendar_dates_in_rolling_24_hours(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-08-11T10:00:00+08:00")
    start = now - timedelta(hours=24)
    clean_activity = {
        "2026-08-10": {"complete": True, "failures": 0},
        "2026-08-11": {"complete": True, "failures": 0},
    }
    failed_activity = {
        **clean_activity,
        "2026-08-11": {"complete": True, "failures": 1},
    }
    rows = [_row(now - timedelta(hours=1))]
    rows.extend(
        _row(now.replace(hour=0, minute=0) - timedelta(days=day) + timedelta(hours=1))
        for day in range(1, 15)
    )

    clean = _window_metering(clean_activity, start, now)
    failed = _window_metering(failed_activity, start, now)
    armed = evaluate_a6_cost(
        rows,
        now=now,
        catalog=_catalog(tmp_path),
        metering_complete=bool(clean["complete"]),
        metering_failure_count=int(clean["failure_count"]),
    )
    blocked = evaluate_a6_cost(
        rows,
        now=now,
        catalog=_catalog(tmp_path),
        metering_complete=bool(failed["complete"]),
        metering_failure_count=int(failed["failure_count"]),
    )

    assert clean == {
        "complete": True,
        "failure_count": 0,
        "expected_days": 2,
        "observed_days": 2,
        "incomplete_days": 0,
    }
    assert armed["evaluable"] is True
    assert failed["expected_days"] == 2
    assert failed["failure_count"] == 1
    assert failed["complete"] is False
    assert blocked["evaluable"] is False


def test_weekly_report_qualifies_nominal_amount_and_references_interpret_per_call(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    rows = [
        _row(datetime.fromisoformat("2026-08-09T12:00:00+08:00"), tokens=200),
        _row(datetime.fromisoformat("2026-08-02T12:00:00+08:00"), tokens=100),
    ]
    report = build_cost_report(now=now, pricing_catalog=_catalog(tmp_path), rows_snapshot=rows)
    report["nominal_share"] = 0.5
    report["totals"]["nominal_cost_usd"] = report["totals"]["known_cost_usd"] * 0.5

    text = format_cost_report(report)

    assert "nominal 目录价估算约 ¥0.72（50.0%），并非账单实付" in text
    assert (
        "单篇解读：cache 中性目录价估算 ¥1.4400/次；"
        "前一等长窗口 ¥0.7200（+100.0%；两窗均按 cache 未命中重算）"
    ) in text


def test_interpret_reference_uses_cache_neutral_comparison_across_coverage_mismatch(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-08-11T09:17:00+08:00")
    report = build_cost_report(
        now=now,
        pricing_catalog=_catalog(tmp_path),
        rows_snapshot=[
            _row(datetime.fromisoformat("2026-08-09T12:00:00+08:00"), cached=0),
            _row(datetime.fromisoformat("2026-08-02T12:00:00+08:00"), cached=None),
            _row(datetime.fromisoformat("2026-08-09T13:00:00+08:00"), stage="score", cached=None),
            _row(datetime.fromisoformat("2026-08-02T13:00:00+08:00"), stage="score", cached=0),
        ],
    )

    interpret = next(row for row in report["stage_costs"] if row["stage"] == "interpret")
    assert interpret["comparison"]["available"] is True
    assert interpret["comparison"]["cache_basis"] == "all-miss"
    assert "单篇解读：cache 中性目录价估算" in format_cost_report(report)
    assert "两窗均按 cache 未命中重算" in format_cost_report(report)
