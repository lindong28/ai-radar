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
    for status in ("priced", "nominal", "unpriced"):
        target.pop(f"{status}_calls", None)


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


def collect_usage(
    *,
    db_path: str | Path | None = None,
    usage_db_path: str | Path | None = None,
    days: int = 30,
    now: datetime | None = None,
    pricing_catalog: PricingCatalog | None = None,
    rows_snapshot: Sequence[Any] | None = None,
) -> dict[str, object]:
    start, end = _window(days, now)
    rate = usd_cny_rate()
    catalog = pricing_catalog or get_pricing()
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
                (_utc_iso(start), _utc_iso(end)),
            ).fetchall()
        finally:
            conn.close()
    else:
        rows = [
            row
            for row in rows_snapshot
            if (created := _parse_dt(row["created_at"])) is not None
            and start <= created < end
        ]

    totals = _empty_totals()
    unpriced_counts: dict[tuple[str, str], int] = {}
    observed_pairs: dict[tuple[str, str], datetime] = {}
    for row in rows:
        provider = str(row["provider"] or "unknown")
        model = str(row["model"] or "unknown")
        derived = derive_cost_usd(row, catalog=catalog)
        _add_usage(totals, row=row, derived=derived)
        if derived.status == "unpriced":
            pair = (provider, model)
            unpriced_counts[pair] = unpriced_counts.get(pair, 0) + 1
        created_at = _parse_dt(row["created_at"])
        pair = (provider, model)
        if created_at is not None and (
            pair not in observed_pairs or created_at > observed_pairs[pair]
        ):
            observed_pairs[pair] = created_at

    _finalize_cost(totals, rate)
    pricing_table, pricing_freshness = _pricing_table(observed_pairs, catalog)
    known_cost = _float(totals["priced_cost_usd"]) + _float(totals["nominal_cost_usd"])
    return {
        "timezone": "Asia/Shanghai",
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
        "unpriced": [
            {"provider": provider, "model": model, "calls": calls}
            for (provider, model), calls in sorted(unpriced_counts.items())
        ],
        "pricing_freshness": pricing_freshness,
        "pricing_source": {"state": catalog.freshness, "source": catalog.source},
        "pricing_table": pricing_table,
    }
