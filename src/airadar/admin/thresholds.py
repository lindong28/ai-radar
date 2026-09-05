from __future__ import annotations

ALERT_THRESHOLDS: dict[str, object] = {
    "a1": {
        "window_minutes": 15,
        "min_samples": 5,
        "upstream_error_rate": 0.5,
    },
    "a2": {
        "window_minutes": 15,
        "min_samples": {
            "prefilter": 4,
            "scoring": 4,
            "enrich": 2,
        },
        "stage_error_rate": {
            "prefilter": 0.3,
            "scoring": 0.3,
            "enrich": 0.95,
        },
        "stage_p95_latency_ms": {
            # prefilter/scoring/enrich 都是后台、非用户可见阶段，其 P95 是外部 LLM 单次
            # 调用的尾延迟。2h 窗口里样本少（~10–20 个），P95 实质≈「最近 2h 最慢的 1–2
            # 次调用」，被单点尾延迟主导：那批慢调用随窗口滑动进/出，P95 反复穿越阈值 →
            # firing/resolved 交替 flap。旧值（prefilter 8478、scoring 16503、enrich
            # 22569）是 06-15「7日基线×3」标定，06-24 路由切到 ARK-first + flash scorer
            # (08d580b) 后基线已失准，真实 P95 贴线抖（scoring ~33s、enrich ~24s）。
            # 延迟本身无用户影响、且总能自愈，故三者统一抬到「真崩溃」地板：只有大量调用
            # 持续到该量级（真挂起）才触发——地板卡在各自正常尾延迟与 LLM 超时(60–90s)之间。
            # 真故障由 stage_error_rate 和 no_success_minutes 兜底，不靠延迟分页。
            "prefilter": 25000,
            "scoring": 45000,
            "enrich": 40000,
        },
        "latency_multiplier": 3.0,
        "no_success_minutes": 120,
    },
    "a3": {
        "window_minutes": 15,
        "min_pv": 20,
        "server_error_rate": 0.05,
        "healthz_timeout_seconds": 2.0,
        "healthz_consecutive_failures": 2,
    },
    "a4": {
        "fetch_failed_ratio": 0.4,
        "fetch_stale_minutes": 90,
        "account_status_codes": [401, 402],
        # The account-layer page resolves only after this many complete fetch
        # rounds (distinct completed_at) are back under fetch_failed_ratio. Read
        # by both metrics (how many rounds to expose) and the A4 rule, so it
        # must not be overridden per evaluate_rules() call: metrics would keep
        # exposing the global count and the rule could then never resolve.
        "account_resolve_rounds": 2,
        "daily_inserted_floor": 127,
        # Fetch sources (esp. the X/nitter feeds) can flap for one round, while
        # a real items-floor breach must page immediately.
        "debounce_minutes_by_severity": {"page": 0, "notice": 30},
    },
    "a5": {"no_success_hours": 4},
    # Floor only. Each source's own 30-day cadence widens it, so an account that
    # normally publishes every few days does not page for behaving normally.
    "a7": {"silence_floor_hours": 6},
    "a6": {
        "daily_floor_cny": 20.0,
        "spike_multiplier": 3.0,
        "page_floor_cny": 100.0,
        "page_multiplier": 6.0,
    },
}

CALIBRATION_BASELINE: dict[str, object] = {
    "generated_from": "/tmp/ai-radar-threshold-calibration.json",
    "window_days": 7,
    "a1": {
        "sample_size": 17867,
        "upstream_errors": 3247,
        "upstream_error_rate": 0.1817,
    },
    "a3": {
        "time_basis": "all_available_access_log_lines",
        "pv": 12270,
        "uv": 685,
        "server_errors": 1,
        "server_error_rate": 0.0001,
    },
    "a4": {
        "runs": 618,
        "successful_runs": 613,
        "fetch_failed_ratio_avg": 0.0513,
        "fetch_failed_ratio_p95": 0.0882,
        "daily_inserted_avg": 424.12,
        "daily_inserted_days": 8,
    },
}
