from __future__ import annotations

ALERT_THRESHOLDS: dict[str, object] = {
    "a1": {
        "window_minutes": 15,
        "min_samples": 5,
        "upstream_error_rate": 0.5,
    },
    "a2": {
        "window_minutes": 15,
        "stage_error_rate": {
            "prefilter": 0.3,
            "scoring": 0.3,
            "enrich": 0.95,
        },
        "stage_p95_latency_ms": {
            # prefilter 是后台、非用户可见阶段，其 P95 是外部 LLM(ARK flash)单次调用的
            # 尾延迟。2h 窗口里通常只有 ~15 个样本，P95 实质≈「最近 2h 最慢的 1–2 次
            # 调用」，被单点尾延迟主导而反复贴线 flap（8478=旧「7日基线×3」，标定于
            # 06-15，但 06-24 prefilter 路由切到 ARK-first flash 后该基线已失准）。
            # 延迟本身无用户影响、且总能自愈，故抬到「真崩溃」地板 25000ms：只有大量
            # 调用持续 25s+（真挂起）才触发。prefilter 真故障由 stage_error_rate 和
            # no_success_minutes 兜底——不靠延迟分页。
            "prefilter": 25000,
            "scoring": 16503,
            "enrich": 22569,
        },
        "latency_multiplier": 3.0,
        "no_success_minutes": 120,
    },
    "a3": {
        "window_minutes": 15,
        "server_error_rate": 0.05,
        "healthz_url": "http://127.0.0.1:8000/api/v1/healthz",
        "healthz_timeout_seconds": 2.0,
        "healthz_consecutive_failures": 2,
    },
    "a4": {
        "fetch_failed_ratio": 0.4,
        "daily_inserted_floor": 127,
        # Fetch sources (esp. the X/nitter feeds) flap for a single round and
        # self-heal within ~15 min, which fired/resolved A4 as pure noise. Only
        # notify once an outage persists past this window (≈2 fetch rounds). Other
        # rules omit this key and default to 0 = notify immediately.
        "debounce_minutes": 30,
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
