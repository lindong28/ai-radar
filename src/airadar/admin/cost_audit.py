from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .. import db
from ..llm_usage import DerivedCost, derive_cost_usd, migrate_usage_db
from ..pricing import PricingCatalog, get_pricing, is_reviewed_fuzzy_match, usd_cny_rate
from .usage import SHANGHAI_TZ, _utc_iso, _window, collect_usage

ANCHOR_START = "2026-08-09T10:52:42Z"
ANCHOR_END = "2026-08-09T16:26:29Z"
ANCHOR_CALLS = 222
ANCHOR_USD = 6.94
ANCHOR_CNY = 49.9
ANCHOR_TOLERANCE = 0.05
RECONCILIATION_TOLERANCE_USD = 1e-7

@dataclass(frozen=True)
class CostAuditReport:
    passed: bool
    human_lines: tuple[str, ...]
    kv_lines: tuple[str, ...]
    json_payload: dict[str, object]
    resolution_unverified: tuple[tuple[str, str, str], ...]

    @property
    def lines(self) -> tuple[str, ...]:
        """Compatibility view for tests and explicit machine-oriented consumers."""
        return self.kv_lines


@dataclass(frozen=True)
class RawCatalogPrice:
    input_cost_per_token: float | None
    cache_read_input_token_cost: float | None
    output_cost_per_token: float | None
    matched_key: str
    status: str
    match_kind: str


def _cached_tokens(row: sqlite3.Row) -> int | None:
    if "cached_input_tokens" in row.keys() and row["cached_input_tokens"] is not None:
        return max(0, min(int(row["input_tokens"] or 0), int(row["cached_input_tokens"])))
    try:
        attribution = json.loads(str(row["attribution_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(attribution, dict) or attribution.get("cached_input_tokens") is None:
        return None
    return max(0, min(int(row["input_tokens"] or 0), int(attribution["cached_input_tokens"])))


def _expected_cost(row: sqlite3.Row, quote: RawCatalogPrice | None) -> float | None:
    if (
        quote is None
        or quote.input_cost_per_token is None
        or quote.cache_read_input_token_cost is None
        or quote.output_cost_per_token is None
    ):
        return None
    input_tokens = max(0, int(row["input_tokens"] or 0))
    cached = _cached_tokens(row)
    output_tokens = max(0, int(row["output_tokens"] or 0))
    if cached is None:
        return (
            input_tokens * quote.input_cost_per_token
            + output_tokens * quote.output_cost_per_token
        )
    return (
        (input_tokens - cached) * quote.input_cost_per_token
        + cached * quote.cache_read_input_token_cost
        + output_tokens * quote.output_cost_per_token
    )


def _effective_at(value: object, catalog: PricingCatalog) -> datetime:
    if value in (None, ""):
        return datetime.fromtimestamp(catalog.observed_at, UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _source_tariff_per_token(value: object, currency: str) -> float:
    per_million = float(str(value))
    normalized_currency = currency.strip().upper()
    if normalized_currency == "USD":
        return per_million / 1_000_000
    if normalized_currency == "CNY":
        return per_million / usd_cny_rate() / 1_000_000
    raise ValueError(f"unsupported source tariff currency: {currency}")


def _raw_catalog_price(
    provider: str,
    model: str,
    catalog: PricingCatalog,
    effective_at: object,
) -> RawCatalogPrice | None:
    """Resolve identity and rates directly from raw catalog storage."""
    canonical = f"{provider.strip().lower()}/{model.strip().lower()}"
    effective = _effective_at(effective_at, catalog)
    for entry in catalog.supplements.get(canonical, ()):
        if entry.effective_from is None:
            continue
        start = datetime.fromisoformat(entry.effective_from.replace("Z", "+00:00"))
        end = (
            datetime.fromisoformat(entry.effective_to.replace("Z", "+00:00"))
            if entry.effective_to
            else None
        )
        if start <= effective and (end is None or effective < end):
            try:
                input_cost = _source_tariff_per_token(
                    entry.source_input_per_million_tokens,
                    entry.source_currency,
                )
                cache_cost = _source_tariff_per_token(
                    entry.source_cache_read_per_million_tokens,
                    entry.source_currency,
                )
                output_cost = _source_tariff_per_token(
                    entry.source_output_per_million_tokens,
                    entry.source_currency,
                )
            except (TypeError, ValueError):
                return RawCatalogPrice(None, None, None, canonical, "invalid", "exact")
            return RawCatalogPrice(
                input_cost_per_token=input_cost,
                cache_read_input_token_cost=cache_cost,
                output_cost_per_token=output_cost,
                matched_key=canonical,
                status="nominal",
                match_kind="exact",
            )

    exact_matches: list[tuple[str, dict[str, object]]] = []
    fuzzy_matches: list[tuple[str, dict[str, object]]] = []
    for key, raw in catalog.litellm.items():
        key_provider, separator, key_model = key.strip().lower().partition("/")
        if not separator or key_provider != provider.strip().lower():
            continue
        if key_model == model.strip().lower():
            exact_matches.append((key, raw))
        elif model.strip().lower() and model.strip().lower() in key_model:
            fuzzy_matches.append((key, raw))
    matches = exact_matches or fuzzy_matches
    if len(matches) != 1:
        return None
    key, raw = matches[0]
    match_kind = "exact" if exact_matches else "fuzzy"
    try:
        input_cost = float(str(raw["input_cost_per_token"]))
        output_cost = float(str(raw["output_cost_per_token"]))
        cache_cost = float(str(raw.get("cache_read_input_token_cost", input_cost)))
    except (KeyError, TypeError, ValueError):
        return RawCatalogPrice(None, None, None, key.strip().lower(), "invalid", match_kind)
    return RawCatalogPrice(
        input_cost,
        cache_cost,
        output_cost,
        key.strip().lower(),
        "priced",
        match_kind,
    )


def _resolution_state(
    provider: str,
    model: str,
    derived: DerivedCost,
    *,
    catalog: PricingCatalog,
    effective_at: object,
) -> tuple[str, str]:
    canonical = f"{provider.strip().lower()}/{model.strip().lower()}"
    raw = _raw_catalog_price(provider, model, catalog, effective_at)
    if raw is None:
        if derived.quote is None:
            return "exact-unpriced", "none"
        return "unexpected-match", derived.quote.matched_key.lower()
    if raw.status == "invalid":
        return "invalid-raw", raw.matched_key
    if derived.quote is None:
        state = "missing-exact" if raw.match_kind == "exact" else "missing-fuzzy"
        return state, raw.matched_key
    matched = derived.quote.matched_key.lower()
    if matched != raw.matched_key:
        return "mismatched-key", matched
    if raw.match_kind == "exact" and matched == canonical:
        return "exact", matched
    if is_reviewed_fuzzy_match(provider, model, matched):
        return "reviewed-fuzzy", matched
    return "unverified-fuzzy", matched


def _within(actual: float, target: float, tolerance: float = ANCHOR_TOLERANCE) -> bool:
    if target == 0:
        return actual == 0
    return target * (1 - tolerance) <= actual <= target * (1 + tolerance)


def _known_cost(row: dict[str, object]) -> float:
    raw = row.get("known_cost_usd")
    return float(str(raw)) if raw is not None else 0.0


def _nonnull_cost_count(conn: sqlite3.Connection, table: str) -> int | None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if table_exists is None:
        return None
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if "cost_usd" not in columns:
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE cost_usd IS NOT NULL").fetchone()[0])


def _count_token(value: int | None) -> str:
    return "not-present" if value is None else str(value)


def run_cost_audit(
    *,
    db_path: str | Path | None = None,
    usage_db_path: str | Path | None = None,
    days: int = 30,
    now: datetime | None = None,
    catalog: PricingCatalog | None = None,
    require_anchor: bool = True,
) -> CostAuditReport:
    observed_now = now or datetime.now(SHANGHAI_TZ)
    active_catalog = catalog or get_pricing()
    start, end = _window(days, observed_now)
    active_usage_path = migrate_usage_db(usage_db_path=usage_db_path, main_db_path=db_path)
    conn = db.get_conn(active_usage_path)
    try:
        conn.execute("BEGIN")
        rows = conn.execute(
            """
            SELECT * FROM llm_usage
            WHERE created_at >= ? AND created_at < ?
            ORDER BY stage, provider, model, created_at, id
            """,
            (_utc_iso(start), _utc_iso(end)),
        ).fetchall()
        anchor_rows = conn.execute(
            """
            SELECT * FROM llm_usage
            WHERE created_at >= ? AND created_at <= ?
              AND stage = 'interpret'
              AND provider = 'deepseek' AND model = 'deepseek-v4-pro'
            ORDER BY created_at, id
            """,
            (ANCHOR_START, ANCHOR_END),
        ).fetchall()
        active_usage_residue = _nonnull_cost_count(conn, "llm_usage") or 0
    finally:
        conn.close()

    legacy_usage_residue: int | None = None
    evaluation_residue: int | None = None
    main_path = db.resolve_db_path(db_path)
    if main_path.exists():
        with sqlite3.connect(f"file:{main_path}?mode=ro", uri=True) as main_conn:
            legacy_usage_residue = _nonnull_cost_count(main_conn, "llm_usage")
            evaluation_residue = _nonnull_cost_count(main_conn, "item_evaluations")
    cleanup_ready = active_usage_residue == 0 and all(
        count in (None, 0) for count in (legacy_usage_residue, evaluation_residue)
    )

    expected_groups: dict[tuple[str, str, str], float] = {}
    derived_groups: dict[tuple[str, str, str], float] = {}
    expected_unpriced: dict[tuple[str, str], int] = {}
    expected_statuses: dict[tuple[str, str, str], set[str]] = {}
    derived_statuses: dict[tuple[str, str, str], set[str]] = {}
    group_resolution: dict[tuple[str, str, str], str] = {}
    resolution_unverified: set[tuple[str, str, str]] = set()
    token_groups: dict[tuple[str, str, str], dict[str, int | None]] = {}
    for row in rows:
        stage = str(row["stage"])
        provider = str(row["provider"])
        model = str(row["model"])
        group_key = (stage, provider, model)
        derived = derive_cost_usd(row, catalog=active_catalog)
        raw_quote = _raw_catalog_price(provider, model, active_catalog, row["created_at"])
        expected = _expected_cost(row, raw_quote)
        resolution, matched = _resolution_state(
            provider,
            model,
            derived,
            catalog=active_catalog,
            effective_at=row["created_at"],
        )
        previous_resolution = group_resolution.get(group_key)
        if previous_resolution in {"exact", "exact-unpriced", "reviewed-fuzzy", None}:
            group_resolution[group_key] = resolution
        if resolution not in {"exact", "exact-unpriced", "reviewed-fuzzy"}:
            resolution_unverified.add((provider, model, matched))
        expected_status = raw_quote.status if raw_quote and raw_quote.status != "invalid" else "unpriced"
        expected_statuses.setdefault(group_key, set()).add(expected_status)
        derived_statuses.setdefault(group_key, set()).add(derived.status)
        tokens = token_groups.setdefault(
            group_key,
            {"input": 0, "cached": 0, "uncached": 0, "output": 0, "unknown_split_calls": 0},
        )
        input_tokens = max(0, int(row["input_tokens"] or 0))
        cached_tokens = _cached_tokens(row)
        tokens["input"] = int(tokens["input"] or 0) + input_tokens
        tokens["output"] = int(tokens["output"] or 0) + max(0, int(row["output_tokens"] or 0))
        if cached_tokens is None:
            tokens["cached"] = None
            tokens["uncached"] = None
            tokens["unknown_split_calls"] = int(tokens["unknown_split_calls"] or 0) + 1
        elif tokens["cached"] is not None:
            tokens["cached"] = int(tokens["cached"] or 0) + cached_tokens
            tokens["uncached"] = int(tokens["uncached"] or 0) + input_tokens - cached_tokens
        if expected is None:
            pair = (provider, model)
            expected_unpriced[pair] = expected_unpriced.get(pair, 0) + 1
        else:
            expected_groups[group_key] = expected_groups.get(group_key, 0.0) + expected
        if derived.cost_usd is not None:
            derived_groups[group_key] = derived_groups.get(group_key, 0.0) + derived.cost_usd

    admin_usage = collect_usage(
        db_path=db_path,
        usage_db_path=active_usage_path,
        days=days,
        now=observed_now,
        pricing_catalog=active_catalog,
        rows_snapshot=rows,
    )
    admin_unpriced = {
        (str(row["provider"]), str(row["model"])): int(str(row["calls"]))
        for row in cast(list[dict[str, object]], admin_usage["unpriced"])
    }
    measurement_scope = cast(dict[str, object], admin_usage["measurement_scope"])

    additive_scope = cast(dict[str, object], measurement_scope["additive_quantities"])
    statistics_scope = cast(dict[str, object], measurement_scope["cohort_statistics"])
    kv_lines = [
        f"measurement-scope basis={measurement_scope['basis']} "
        f"paid_calls_without_row={measurement_scope['paid_calls_without_row']}",
        "measurement-scope-additive "
        f"scope={additive_scope['scope']} "
        f"kinds={','.join(cast(list[str], additive_scope['kinds']))} "
        f"interpretation={additive_scope['interpretation']}",
        "measurement-scope-statistics "
        f"scope={statistics_scope['scope']} "
        f"kinds={','.join(cast(list[str], statistics_scope['kinds']))} "
        f"interpretation={statistics_scope['interpretation']}",
    ]
    group_failures: list[str] = []
    all_groups = sorted(set(expected_statuses) | set(derived_statuses))
    for stage, provider, model in all_groups:
        key = (stage, provider, model)
        expected = expected_groups.get(key)
        derived_amount = derived_groups.get(key)
        expected_group_statuses = expected_statuses.get(key, {"unpriced"})
        derived_group_statuses = derived_statuses.get(key, {"unpriced"})
        status = "/".join(sorted(derived_group_statuses))
        resolution = group_resolution.get(key, "unverified")
        amounts = [value for value in (expected, derived_amount) if value is not None]
        amount_consistent = len(amounts) <= 1 or max(amounts) - min(amounts) <= RECONCILIATION_TOLERANCE_USD
        group_pass = (
            amount_consistent
            and expected_group_statuses == derived_group_statuses
            and resolution in {"exact", "exact-unpriced", "reviewed-fuzzy"}
        )
        if not group_pass:
            group_failures.append(f"{stage}/{provider}/{model}")
        token = token_groups.get(key, {})
        kv_lines.append(
            f"group stage={stage} provider={provider} model={model} status={status} "
            f"input_tokens={token.get('input', 0)} uncached_input_tokens={token.get('uncached')} "
            f"cached_input_tokens={token.get('cached')} output_tokens={token.get('output', 0)} "
            f"expected_usd={expected if expected is not None else 'null'} "
            f"derived_usd={derived_amount if derived_amount is not None else 'null'} "
            f"resolution={resolution} "
            f"{'PASS' if group_pass else 'FAIL'}"
        )

    expected_total = sum(expected_groups.values())
    derived_total = sum(derived_groups.values())
    admin_totals = cast(dict[str, object], admin_usage["totals"])
    admin_total = _known_cost(admin_totals)
    layer_totals = {
        "expected": expected_total,
        "derived": derived_total,
        "admin_usage": admin_total,
    }
    total_values = list(layer_totals.values())
    total_pass = max(total_values, default=0.0) - min(total_values, default=0.0) <= RECONCILIATION_TOLERANCE_USD
    unpriced_pass = expected_unpriced == admin_unpriced

    rate = usd_cny_rate()
    anchor_payload: dict[str, object] | None = None
    anchor_pass = True
    if require_anchor:
        anchor_derived = sum(
            float(derive_cost_usd(row, catalog=active_catalog).cost_usd or 0.0)
            for row in anchor_rows
        )
        anchor_official_cny = sum(
            (int(row["input_tokens"] or 0) * 3 + int(row["output_tokens"] or 0) * 6) / 1_000_000
            for row in anchor_rows
        )
        anchor_pass = (
            len(anchor_rows) == ANCHOR_CALLS
            and _within(anchor_derived, ANCHOR_USD)
            and _within(anchor_official_cny, ANCHOR_CNY)
        )
        anchor_derived_cny = anchor_derived * rate
        anchor_cny_delta = anchor_derived_cny - anchor_official_cny
        anchor_cny_delta_pct = (
            anchor_cny_delta / anchor_official_cny * 100
            if anchor_official_cny
            else None
        )
        anchor_payload = {
            "calls_expected": ANCHOR_CALLS,
            "calls_actual": len(anchor_rows),
            "derived_usd_target": ANCHOR_USD,
            "derived_usd_actual": round(anchor_derived, 8),
            "official_cny_target": ANCHOR_CNY,
            "official_cny_actual": round(anchor_official_cny, 6),
            "derived_cny_actual": round(anchor_derived_cny, 6),
            "cny_delta_vs_official": round(anchor_cny_delta, 6),
            "cny_delta_pct_vs_official": (
                round(anchor_cny_delta_pct, 6)
                if anchor_cny_delta_pct is not None
                else None
            ),
            "tolerance_pct": ANCHOR_TOLERANCE * 100,
            "passed": anchor_pass,
        }
        anchor_cny_delta_pct_token = (
            f"{anchor_cny_delta_pct:.6f}%"
            if anchor_cny_delta_pct is not None
            else "null"
        )
        kv_lines.append(
            f"anchor window={ANCHOR_START}..{ANCHOR_END} calls_expected={ANCHOR_CALLS} "
            f"calls_actual={len(anchor_rows)} derived_usd_target={ANCHOR_USD:.2f} "
            f"derived_usd_actual={anchor_derived:.8f} official_cny_target={ANCHOR_CNY:.1f} "
            f"official_cny_actual={anchor_official_cny:.6f} "
            f"derived_cny_actual={anchor_derived_cny:.6f} "
            f"cny_delta_vs_official={anchor_cny_delta:.6f} "
            f"cny_delta_pct_vs_official={anchor_cny_delta_pct_token} "
            f"{'PASS' if anchor_pass else 'FAIL'}"
        )

    passed = (
        not group_failures
        and total_pass
        and unpriced_pass
        and anchor_pass
        and cleanup_ready
    )
    kv_lines.extend(
        [
            f"total expected_usd={expected_total:.8f} derived_usd={derived_total:.8f} "
            f"admin_usage_usd={admin_total:.8f} {'PASS' if total_pass else 'FAIL'}",
            "unpriced expected="
            + json.dumps(
                [{"provider": pair[0], "model": pair[1], "calls": count} for pair, count in sorted(expected_unpriced.items())],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + " admin_usage="
            + json.dumps(
                [{"provider": pair[0], "model": pair[1], "calls": count} for pair, count in sorted(admin_unpriced.items())],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + f" {'PASS' if unpriced_pass else 'FAIL'}",
            "deprecated-cost-residue "
            f"active_usage={active_usage_residue} "
            f"legacy_main_usage={_count_token(legacy_usage_residue)} "
            f"item_evaluations={_count_token(evaluation_residue)} "
            f"{'CLEAN' if cleanup_ready else 'CLEANUP_REQUIRED'}",
            f"cost-audit {'PASS' if passed else 'FAIL'} consistency=tariff_arithmetic_only "
            f"measurement_completeness=not_assessed groups={len(all_groups)} recorded_rows={len(rows)} "
            f"unpriced_groups={len(expected_unpriced)}",
        ]
    )

    nominal_share = admin_usage.get("nominal_share")
    human_lines = [
        (
            f"LLM cost reconciliation: {'CONSISTENT' if passed else 'INCONSISTENT'} "
            "(tariff arithmetic only; measurement completeness not assessed)"
        ),
        (
            "Scope: tariff arithmetic consistency against the loaded catalog only; "
            "measurement completeness and tariff authority are not assessed."
        ),
        (
            "Measurement scope: call counts, token totals, and same-basis cost sums "
            "are lower bounds from recorded llm_usage rows; averages, shares, and "
            "period changes describe that recorded cohort only, with unknown direction "
            "versus all paid calls."
        ),
        f"Window: {start.isoformat()} to {end.isoformat()} ({days} rolling days)",
        (
            f"Known recorded-row cost sum: ${admin_total:.4f} / "
            f"¥{admin_total * rate:.2f} (1 USD = {rate:g} CNY)"
        ),
        f"Rows and groups: {len(rows)} recorded llm_usage rows across {len(all_groups)} groups",
    ]
    if nominal_share is not None:
        human_lines.append(
            f"Tariff quality: {float(str(nominal_share)) * 100:.1f}% of recorded-cohort "
            "known cost is nominal; its direction versus the all-paid-call share is unknown."
        )
    if expected_unpriced:
        calls = sum(expected_unpriced.values())
        human_lines.append(
            f"Unpriced: {len(expected_unpriced)} group{'s' if len(expected_unpriced) != 1 else ''}, "
            f"{calls} recorded row{'s' if calls != 1 else ''}; cost unknown"
        )
        for (provider, model), count in sorted(expected_unpriced.items()):
            human_lines.append(f"  - {provider}/{model}: {count} recorded row(s)")
    else:
        human_lines.append("Unpriced: none observed; the real-data unpriced path was not exercised.")
    if resolution_unverified:
        human_lines.append("Price resolution: UNVERIFIED identity checks require review:")
        for provider, model, matched in sorted(resolution_unverified):
            resolution = next(
                (
                    state
                    for (_stage, row_provider, row_model), state in group_resolution.items()
                    if row_provider == provider and row_model == model
                ),
                "unverified",
            )
            detail = "unreviewed fuzzy match" if resolution == "unverified-fuzzy" else resolution
            human_lines.append(f"  - {provider}/{model} -> {matched}: {detail}")
    else:
        human_lines.append(f"Price resolution: exact for {len(all_groups)} observed group(s).")
    human_lines.append(
        "Calculation checks: "
        + (
            "raw catalog, derived totals, and admin totals agree within tolerance."
            if total_pass
            else "raw catalog, derived totals, and admin totals disagree; inspect --format=kv."
        )
    )
    human_lines.append(
        "Deprecated stored-cost residue: "
        f"active usage {active_usage_residue}, "
        f"legacy main usage {_count_token(legacy_usage_residue)}, "
        f"item evaluations {_count_token(evaluation_residue)}; "
        f"{'CLEAN' if cleanup_ready else 'CLEANUP REQUIRED'}"
    )
    if anchor_payload is not None:
        human_lines.append(
            f"Anchor: {anchor_payload['calls_actual']}/{anchor_payload['calls_expected']} recorded rows, "
            f"${anchor_payload['derived_usd_actual']} and ¥{anchor_payload['official_cny_actual']} "
            f"within the accepted ±{anchor_payload['tolerance_pct']:g}% band: "
            f"{'PASS' if anchor_pass else 'FAIL'}"
        )
        if anchor_payload["cny_delta_pct_vs_official"] is None:
            human_lines.append(
                "Anchor currency-view difference: unavailable because official CNY actual is zero."
            )
        else:
            human_lines.append(
                "Anchor currency-view difference: "
                f"derived ¥{anchor_payload['derived_cny_actual']} vs official "
                f"¥{anchor_payload['official_cny_actual']}; difference: "
                f"{float(str(anchor_payload['cny_delta_vs_official'])):+f} CNY "
                f"({float(str(anchor_payload['cny_delta_pct_vs_official'])):+f}% vs official CNY actual)."
            )
    if not passed:
        human_lines.append("Action: inspect `./run.sh admin cost-audit --format=kv` and resolve every FAIL/UNVERIFIED item.")
    else:
        human_lines.append("Action: none for calculation consistency; nominal tariff provenance remains a separate open item.")

    payload: dict[str, object] = {
        "tariff_arithmetic_consistent": passed,
        "consistency_scope": "tariff_arithmetic_only",
        "measurement_completeness": "not_assessed",
        "scope": (
            "tariff arithmetic consistency against loaded catalog only; "
            "measurement completeness and tariff authority not assessed"
        ),
        "measurement_scope": measurement_scope,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "rows": len(rows),
        "groups": len(all_groups),
        "known_cost_usd": round(admin_total, 8),
        "known_cost_cny": round(admin_total * rate, 6),
        "exchange_rate_usd_cny": rate,
        "nominal_share": nominal_share,
        "unpriced": [
            {"provider": pair[0], "model": pair[1], "calls": count, "cost": None}
            for pair, count in sorted(expected_unpriced.items())
        ],
        "resolution_unverified": [
            {"provider": provider, "model": model, "matched_key": matched}
            for provider, model, matched in sorted(resolution_unverified)
        ],
        "layer_totals_usd": layer_totals,
        "deprecated_cost_residue": {
            "active_usage": active_usage_residue,
            "legacy_main_usage": legacy_usage_residue,
            "item_evaluations": evaluation_residue,
            "cleanup_ready": cleanup_ready,
        },
        "anchor": anchor_payload,
    }
    return CostAuditReport(
        passed=passed,
        human_lines=tuple(human_lines),
        kv_lines=tuple(kv_lines),
        json_payload=payload,
        resolution_unverified=tuple(sorted(resolution_unverified)),
    )
