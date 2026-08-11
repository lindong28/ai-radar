from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar.pricing import (
    LITELLM_PRICING_URL,
    PRICING_TTL_SECONDS,
    PricingEntry,
    get_pricing,
    resolve_price,
    usd_cny_rate,
)


def _litellm_table() -> dict[str, dict[str, object]]:
    return {
        "deepseek/deepseek-v4-pro": {
            "input_cost_per_token": 4.35e-7,
            "cache_read_input_token_cost": 3.625e-9,
            "output_cost_per_token": 8.7e-7,
        },
        "deepseek/deepseek-v4-flash": {
            "input_cost_per_token": 1.4e-7,
            "cache_read_input_token_cost": 2.8e-9,
            "output_cost_per_token": 2.8e-7,
        },
    }


def _write_cache(path: Path, *, fetched_at: float, data: dict[str, object]) -> None:
    path.write_text(json.dumps({"fetched_at": fetched_at, "data": data}), encoding="utf-8")


def test_litellm_url_is_the_approved_shared_source() -> None:
    assert LITELLM_PRICING_URL == (
        "https://raw.githubusercontent.com/BerriAI/litellm/main/"
        "model_prices_and_context_window.json"
    )


def test_fresh_cache_is_used_without_refresh(tmp_path: Path) -> None:
    now = 2_000_000_000.0
    cache_path = tmp_path / "pricing_cache.json"
    _write_cache(cache_path, fetched_at=now - PRICING_TTL_SECONDS + 1, data=_litellm_table())

    def forbidden_fetch() -> dict[str, object]:
        raise AssertionError("fresh cache must not refresh")

    catalog = get_pricing(cache_path=cache_path, fetcher=forbidden_fetch, now=lambda: now)

    assert catalog.freshness == "fresh"
    assert resolve_price("deepseek", "deepseek-v4-pro", catalog).input_cost_per_token == 4.35e-7


def test_cache_at_ttl_boundary_refreshes_synchronously(tmp_path: Path) -> None:
    now = 2_000_000_000.0
    cache_path = tmp_path / "pricing_cache.json"
    stale_table = _litellm_table()
    stale_table["deepseek/deepseek-v4-pro"]["input_cost_per_token"] = 1.0
    _write_cache(cache_path, fetched_at=now - PRICING_TTL_SECONDS, data=stale_table)
    calls = 0

    def fetch() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _litellm_table()

    catalog = get_pricing(cache_path=cache_path, fetcher=fetch, now=lambda: now)

    assert calls == 1
    assert catalog.freshness == "fresh"
    assert resolve_price("deepseek", "deepseek-v4-pro", catalog).input_cost_per_token == 4.35e-7


@pytest.mark.parametrize("with_expired_cache", [False, True])
def test_refresh_failure_marks_expired_cache_or_bundled_fallback_stale(
    tmp_path: Path,
    with_expired_cache: bool,
) -> None:
    now = 2_000_000_000.0
    cache_path = tmp_path / "pricing_cache.json"
    fallback_path = tmp_path / "fallback.json"
    fallback_path.write_text(json.dumps(_litellm_table()), encoding="utf-8")
    if with_expired_cache:
        _write_cache(
            cache_path,
            fetched_at=now - PRICING_TTL_SECONDS - 1,
            data=_litellm_table(),
        )

    def offline() -> dict[str, object]:
        raise OSError("offline")

    catalog = get_pricing(
        cache_path=cache_path,
        fallback_path=fallback_path,
        fetcher=offline,
        now=lambda: now,
    )

    assert catalog.freshness == "stale"
    assert catalog.source in {"expired-cache", "bundled-fallback"}
    assert resolve_price("deepseek", "deepseek-v4-flash", catalog).freshness == "stale"


def test_supplement_precedes_litellm_fuzzy_resolution(tmp_path: Path) -> None:
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=_litellm_table,
        persist=False,
    )

    quote = resolve_price("ark", "deepseek-v4-pro-260425", catalog)

    assert quote is not None
    assert quote.matched_key == "ark/deepseek-v4-pro-260425"
    assert quote.match_kind == "exact"
    assert quote.nominal is True
    assert quote.source_currency == "CNY"
    assert quote.input_cost_per_token == pytest.approx(12 / 7.2 / 1_000_000)
    assert quote.output_cost_per_token == pytest.approx(24 / 7.2 / 1_000_000)


def test_case_insensitive_fuzzy_resolution_uses_litellm_when_no_supplement(
    tmp_path: Path,
) -> None:
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=_litellm_table,
        persist=False,
    )

    quote = resolve_price("deepseek", "DEEPSEEK-V4-FLA", catalog)

    assert quote is not None
    assert quote.matched_key == "deepseek/deepseek-v4-flash"
    assert quote.match_kind == "fuzzy"
    assert quote.nominal is False
    assert quote.source_currency is None
    assert quote.source_input_per_million_tokens is None
    assert quote.source_cache_read_per_million_tokens is None
    assert quote.source_output_per_million_tokens is None


def test_unknown_model_is_unpriced(tmp_path: Path) -> None:
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=_litellm_table,
        persist=False,
    )

    assert resolve_price("unknown", "missing-model", catalog) is None


def test_supplement_older_than_90_days_is_due_review(tmp_path: Path) -> None:
    old = (datetime.now(UTC) - timedelta(days=91)).date().isoformat()
    catalog = get_pricing(
        cache_path=tmp_path / "pricing-cache.json",
        fetcher=_litellm_table,
        persist=False,
    )
    catalog.supplements["ark/deepseek-v4-pro-260425"] = (
        PricingEntry(
            input_cost_per_token=1.0,
            cache_read_input_token_cost=0.1,
            output_cost_per_token=2.0,
            nominal=True,
            source="test",
            source_currency="CNY",
            source_input_per_million_tokens=1.0,
            source_cache_read_per_million_tokens=0.1,
            source_output_per_million_tokens=2.0,
            verified_at=old,
            effective_from="2026-05-27T00:00:00Z",
        ),
    )

    assert resolve_price("ark", "deepseek-v4-pro-260425", catalog).freshness == "due-review"


def test_usd_cny_rate_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_RADAR_USD_CNY", raising=False)
    assert usd_cny_rate() == 7.2

    monkeypatch.setenv("AI_RADAR_USD_CNY", "7.05")
    assert usd_cny_rate() == 7.05
