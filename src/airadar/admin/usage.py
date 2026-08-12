from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .. import db
from ..llm_usage import DerivedCost, derive_cost_usd, migrate_usage_db
from ..pricing import (
    PricingCatalog,
    get_pricing,
    is_reviewed_fuzzy_match,
    resolve_price,
    usd_cny_rate,
)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
MEASUREMENT_SCOPE = {
    "basis": "llm_usage_rows",
    "paid_calls_without_row": "excluded",
    "additive_quantities": {
        "scope": "recorded_rows_only",
        "kinds": ["call_counts", "token_totals", "same_basis_cost_sums"],
        "interpretation": "lower_bound_not_total",
    },
    "cohort_statistics": {
        "scope": "recorded_rows_only",
        "kinds": ["averages", "shares", "period_over_period_changes"],
        "interpretation": "direction_unknown_vs_all_paid_calls",
    },
    "description": (
        "调用次数、token 合计与同一计价口径的金额合计是全部付费调用对应总量的下界。"
        "均值、占比和环比只描述 llm_usage 记录行 cohort；"
        "相对全部付费调用真值的偏差方向未知。"
    ),
}


def _parse_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(SHANGHAI_TZ)


def _window(days: int, now: datetime | None) -> tuple[datetime, datetime]:
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    current = current.astimezone(SHANGHAI_TZ)
    end = current.replace(microsecond=0)
    return end - timedelta(days=days), end


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _int(value: object) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _float(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _round_cost(value: object) -> float:
    return round(_float(value), 8)


def _empty_totals() -> dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": None,
        "uncached_input_tokens": None,
        "output_tokens": 0,
        "input_chars": 0,
        "known_cost_usd": 0.0,
        "known_cost_cny": 0.0,
    }


def _add_usage(target: dict[str, Any], *, row: Any, derived: DerivedCost) -> None:
    target["calls"] = _int(target.get("calls")) + 1
    target["input_tokens"] = _int(target.get("input_tokens")) + _int(row["input_tokens"])
    if derived.cached_input_tokens is None or derived.uncached_input_tokens is None:
        target["_cache_split_unknown"] = True
        target["cached_input_tokens"] = None
        target["uncached_input_tokens"] = None
    else:
        target["_cache_split_calls"] = _int(target.get("_cache_split_calls")) + 1
        target["_covered_input_tokens"] = _int(target.get("_covered_input_tokens")) + _int(
            row["input_tokens"]
        )
        target["_covered_cached_input_tokens"] = _int(
            target.get("_covered_cached_input_tokens")
        ) + derived.cached_input_tokens
        if not target.get("_cache_split_unknown"):
            target["cached_input_tokens"] = (
                _int(target.get("cached_input_tokens")) + derived.cached_input_tokens
            )
            target["uncached_input_tokens"] = (
                _int(target.get("uncached_input_tokens")) + derived.uncached_input_tokens
            )
    target["output_tokens"] = _int(target.get("output_tokens")) + _int(row["output_tokens"])
    target["input_chars"] = _int(target.get("input_chars")) + _int(row["input_char_count"])
    status_calls = f"{derived.status}_calls"
    target[status_calls] = _int(target.get(status_calls)) + 1
    if derived.cost_usd is not None:
        target["known_cost_usd"] = _float(target.get("known_cost_usd")) + derived.cost_usd
        status_cost = f"{derived.status}_cost_usd"
        target[status_cost] = _float(target.get(status_cost)) + derived.cost_usd


def _finalize_cost(target: dict[str, Any], rate: float) -> None:
    known_cost_usd = _round_cost(target.get("known_cost_usd"))
    target["known_cost_usd"] = known_cost_usd
    target["known_cost_cny"] = round(known_cost_usd * rate, 6)
    for status in ("priced", "nominal", "unpriced"):
        calls_key = f"{status}_calls"
        target[calls_key] = _int(target.get(calls_key))
        if status != "unpriced":
            cost_key = f"{status}_cost_usd"
            status_cost = _round_cost(target.get(cost_key))
            target[cost_key] = status_cost
    calls = _int(target.get("calls"))
    calls_with_split = _int(target.pop("_cache_split_calls", 0))
    covered_input = _int(target.pop("_covered_input_tokens", 0))
    covered_cached = _int(target.pop("_covered_cached_input_tokens", 0))
    target.pop("_cache_split_unknown", None)
    target["cache_split_coverage"] = {
        "calls_with_split": calls_with_split,
        "calls_total": calls,
        "ratio": round(calls_with_split / calls, 6) if calls else None,
    }
    target["cache_hit_rate"] = (
        round(covered_cached / covered_input, 6) if covered_input else None
    )
    target["known_calls"] = _int(target.get("priced_calls")) + _int(
        target.get("nominal_calls")
    )


def _pricing_table(
    observed_pairs: dict[tuple[str, str], datetime],
    catalog: PricingCatalog,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    freshness_states: set[str] = set()
    for provider, model in sorted(observed_pairs):
        quote_as_of = observed_pairs[(provider, model)].isoformat()
        quote = resolve_price(
            provider,
            model,
            catalog,
            effective_at=observed_pairs[(provider, model)],
        )
        if quote is None:
            rows.append(
                {
                    "provider": provider,
                    "model": model,
                    "quote_as_of": quote_as_of,
                    "matched_key": None,
                    "match_kind": None,
                    "match_reviewed": None,
                    "status": "unpriced",
                    "nominal": False,
                    "freshness": None,
                    "input_per_million_tokens_usd": None,
                    "cache_read_per_million_tokens_usd": None,
                    "output_per_million_tokens_usd": None,
                    "source": None,
                    "source_currency": None,
                    "source_input_per_million_tokens": None,
                    "source_cache_read_per_million_tokens": None,
                    "source_output_per_million_tokens": None,
                    "verified_at": None,
                    "fetched_at": None,
                    "effective_from": None,
                    "effective_to": None,
                }
            )
            continue
        freshness_states.add(quote.freshness)
        match_reviewed = (
            is_reviewed_fuzzy_match(provider, model, quote.matched_key)
            if quote.match_kind == "fuzzy"
            else None
        )
        rows.append(
            {
                "provider": provider,
                "model": model,
                "quote_as_of": quote_as_of,
                "matched_key": quote.matched_key,
                "match_kind": quote.match_kind,
                "match_reviewed": match_reviewed,
                "status": "nominal" if quote.nominal else "priced",
                "nominal": quote.nominal,
                "freshness": quote.freshness,
                "input_per_million_tokens_usd": round(
                    quote.input_cost_per_token * 1_000_000, 9
                ),
                "cache_read_per_million_tokens_usd": round(
                    quote.cache_read_input_token_cost * 1_000_000, 9
                ),
                "output_per_million_tokens_usd": round(
                    quote.output_cost_per_token * 1_000_000, 9
                ),
                "source": quote.source,
                "source_currency": quote.source_currency,
                "source_input_per_million_tokens": quote.source_input_per_million_tokens,
                "source_cache_read_per_million_tokens": (
                    quote.source_cache_read_per_million_tokens
                ),
                "source_output_per_million_tokens": quote.source_output_per_million_tokens,
                "verified_at": quote.verified_at,
                "fetched_at": quote.fetched_at,
                "effective_from": quote.effective_from,
                "effective_to": quote.effective_to,
            }
        )
    return rows, sorted(freshness_states)


def cache_coverage_comparable(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Compare cache-split coverage exactly without floating-point tolerance."""
    left_calls = _int(left.get("calls_total"))
    right_calls = _int(right.get("calls_total"))
    left_split = _int(left.get("calls_with_split"))
    right_split = _int(right.get("calls_with_split"))
    if not left_calls or not right_calls:
        return left_calls == right_calls and left_split == right_split
    return left_split * right_calls == right_split * left_calls


def _rows_in_window(
    rows: Sequence[Any], start: datetime, end: datetime
) -> list[Any]:
    return [
        row
        for row in rows
        if (created := _parse_dt(row["created_at"])) is not None
        and start <= created < end
    ]


def _aggregate_rows(
    rows: Sequence[Any],
    catalog: PricingCatalog,
    rate: float,
    *,
    priced_at: datetime | None = None,
    cache_all_miss: bool = False,
) -> dict[str, Any]:
    totals = _empty_totals()
    buckets: dict[str, dict[object, dict[str, Any]]] = {
        "stage": {},
        "provider": {},
        "group": {},
        "daily": {},
    }
    unpriced_counts: dict[tuple[str, str], int] = {}
    observed_pairs: dict[tuple[str, str], datetime] = {}
    for raw in rows:
        row = dict(raw)
        provider = str(row["provider"] or "unknown")
        model = str(row["model"] or "unknown")
        stage = str(row["stage"] or "unknown")
        created_at = _parse_dt(raw["created_at"])
        if priced_at is not None:
            row["created_at"] = priced_at.isoformat()
        if cache_all_miss:
            row["cached_input_tokens"] = 0
        derived = derive_cost_usd(row, catalog=catalog)
        _add_usage(totals, row=row, derived=derived)
        keys: tuple[tuple[str, object], ...] = (
            ("stage", stage),
            ("provider", provider),
            ("group", (provider, model)),
        )
        if created_at is not None:
            keys += (("daily", created_at.date().isoformat()),)
        for bucket_name, key in keys:
            target = buckets[bucket_name].setdefault(key, _empty_totals())
            _add_usage(target, row=row, derived=derived)
        pair = (provider, model)
        if derived.status == "unpriced":
            unpriced_counts[pair] = unpriced_counts.get(pair, 0) + 1
        price_observed_at = priced_at or created_at
        if price_observed_at is not None and (
            pair not in observed_pairs or price_observed_at > observed_pairs[pair]
        ):
            observed_pairs[pair] = price_observed_at

    _finalize_cost(totals, rate)
    for bucket in buckets.values():
        for target in bucket.values():
            _finalize_cost(target, rate)
            calls = _int(target["known_calls"])
            target["known_cost_per_call_cny"] = (
                round(_float(target["known_cost_cny"]) / calls, 6) if calls else None
            )
    pricing_table, pricing_freshness = _pricing_table(observed_pairs, catalog)
    return {
        "totals": totals,
        "buckets": buckets,
        "unpriced": [
            {"provider": provider, "model": model, "calls": calls}
            for (provider, model), calls in sorted(unpriced_counts.items())
        ],
        "pricing_table": pricing_table,
        "pricing_freshness": pricing_freshness,
    }


def _bucket_rows(bucket: dict[object, dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, values in bucket.items():
        if kind == "group":
            assert isinstance(key, tuple) and len(key) == 2
            provider, model = key
            row = {"provider": provider, "model": model, **values}
        else:
            row = {kind: key, **values}
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (-_float(row["known_cost_cny"]), str(row)),
    )


def _bucket_rows_with_comparison(
    displayed_current: dict[object, dict[str, Any]],
    neutral_current: dict[object, dict[str, Any]],
    previous: dict[object, dict[str, Any]],
    kind: str,
) -> list[dict[str, Any]]:
    rows = _bucket_rows(displayed_current, kind)
    for row in rows:
        key: object = (
            (row["provider"], row["model"])
            if kind == "group"
            else row[kind]
        )
        previous_values = previous.get(key)
        current_values = neutral_current.get(key)
        current_calls = _int(current_values["known_calls"]) if current_values else 0
        previous_calls = _int(previous_values["known_calls"]) if previous_values else 0
        previous_cost = _float(previous_values["known_cost_cny"]) if previous_values else 0.0
        previous_per_call = (
            _float(previous_values["known_cost_per_call_cny"])
            if previous_values and previous_values["known_cost_per_call_cny"] is not None
            else None
        )
        comparable = bool(previous_values and current_values and current_calls and previous_calls)
        if not previous_calls:
            reason = "no_previous_calls"
        elif not current_calls:
            reason = "no_current_known_calls"
        else:
            reason = None
        current_per_call = current_values["known_cost_per_call_cny"] if current_values else None
        current_cost = _float(current_values["known_cost_cny"]) if current_values else 0.0
        row["comparison"] = {
            "available": comparable,
            "reason": reason,
            "cache_basis": "all-miss",
            "current_known_calls": current_calls,
            "previous_known_calls": _int(previous_values["known_calls"]) if previous_values else 0,
            "current_known_cost_cny": current_cost,
            "current_known_cost_per_call_cny": current_per_call,
            "previous_known_cost_cny": previous_cost,
            "previous_known_cost_per_call_cny": previous_per_call,
            "known_cost_change_ratio": (
                round((current_cost - previous_cost) / previous_cost, 6)
                if comparable and previous_cost
                else None
            ),
            "known_cost_per_call_change_ratio": (
                round((_float(current_per_call) - previous_per_call) / previous_per_call, 6)
                if comparable and current_per_call is not None and previous_per_call
                else None
            ),
        }
    return rows


def collect_usage(
    *,
    db_path: str | Path | None = None,
    usage_db_path: str | Path | None = None,
    days: int = 30,
    now: datetime | None = None,
    priced_at: datetime | None = None,
    pricing_catalog: PricingCatalog | None = None,
    rows_snapshot: Sequence[Any] | None = None,
) -> dict[str, object]:
    start, end = _window(days, now)
    rate = usd_cny_rate()
    catalog = pricing_catalog or get_pricing()
    previous_start = start - (end - start)
    if rows_snapshot is None:
        active_usage_db_path = migrate_usage_db(
            usage_db_path=usage_db_path,
            main_db_path=db_path,
        )
        conn = db.get_conn(active_usage_db_path)
        try:
            usage_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(llm_usage)")
            }
            cached_input_select = (
                "u.cached_input_tokens"
                if "cached_input_tokens" in usage_columns
                else "NULL AS cached_input_tokens"
            )
            rows = conn.execute(
                f"""
                SELECT
                  u.id, u.stage, u.provider, u.model, u.item_id, u.input_tokens,
                  {cached_input_select},
                  u.output_tokens, u.input_char_count,
                  u.attribution_json, u.created_at
                FROM llm_usage u
                WHERE u.created_at >= ? AND u.created_at < ?
                ORDER BY u.created_at DESC, u.id DESC
                """,
                (_utc_iso(previous_start), _utc_iso(end)),
            ).fetchall()
        finally:
            conn.close()
    else:
        rows = list(rows_snapshot)

    comparison_time = priced_at or end
    if comparison_time.tzinfo is None:
        comparison_time = comparison_time.replace(tzinfo=SHANGHAI_TZ)
    display_price_time = priced_at
    if display_price_time is not None and display_price_time.tzinfo is None:
        display_price_time = display_price_time.replace(tzinfo=SHANGHAI_TZ)
    current_rows = _rows_in_window(rows, start, end)
    previous_rows = _rows_in_window(rows, previous_start, start)
    current = _aggregate_rows(
        current_rows, catalog, rate, priced_at=display_price_time
    )
    neutral_current = _aggregate_rows(
        current_rows,
        catalog,
        rate,
        priced_at=comparison_time,
        cache_all_miss=True,
    )
    previous = _aggregate_rows(
        previous_rows,
        catalog,
        rate,
        priced_at=comparison_time,
        cache_all_miss=True,
    )
    totals = current["totals"]
    pricing_table = current["pricing_table"]
    pricing_freshness = current["pricing_freshness"]
    known_cost = _float(totals["priced_cost_usd"]) + _float(totals["nominal_cost_usd"])
    comparable = bool(
        neutral_current["totals"]["known_calls"]
        and previous["totals"]["known_calls"]
    )
    previous_cost = _float(previous["totals"]["known_cost_cny"])
    current_cost = _float(neutral_current["totals"]["known_cost_cny"])
    return {
        "timezone": "Asia/Shanghai",
        "measurement_scope": dict(MEASUREMENT_SCOPE),
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
        },
        "totals": totals,
        "exchange_rate_usd_cny": rate,
        "nominal_share": (
            round(_float(totals["nominal_cost_usd"]) / known_cost, 6)
            if known_cost
            else None
        ),
        "unpriced": current["unpriced"],
        "pricing_freshness": pricing_freshness,
        "pricing_source": {"state": catalog.freshness, "source": catalog.source},
        "pricing_table": pricing_table,
        "stage_costs": _bucket_rows_with_comparison(
            current["buckets"]["stage"],
            neutral_current["buckets"]["stage"],
            previous["buckets"]["stage"],
            "stage",
        ),
        "provider_costs": _bucket_rows_with_comparison(
            current["buckets"]["provider"],
            neutral_current["buckets"]["provider"],
            previous["buckets"]["provider"],
            "provider",
        ),
        "cost_groups": _bucket_rows_with_comparison(
            current["buckets"]["group"],
            neutral_current["buckets"]["group"],
            previous["buckets"]["group"],
            "group",
        ),
        "daily": sorted(
            _bucket_rows(current["buckets"]["daily"], "date"),
            key=lambda row: str(row["date"]),
        ),
        "comparison": {
            "available": comparable,
            "reason": None if comparable else "missing_known_calls",
            "cache_basis": "all-miss",
            "previous_start": previous_start.isoformat(),
            "previous_end": start.isoformat(),
            "previous_known_cost_cny": previous_cost,
            "known_cost_change_ratio": (
                round((current_cost - previous_cost) / previous_cost, 6)
                if comparable and previous_cost
                else None
            ),
            "current_cache_split_coverage": totals["cache_split_coverage"],
            "previous_cache_split_coverage": previous["totals"]["cache_split_coverage"],
        },
    }
