from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path

from . import db

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
PRICING_TTL_SECONDS = 7 * 24 * 60 * 60
SUPPLEMENT_REVIEW_SECONDS = 90 * 24 * 60 * 60
DEFAULT_CACHE_PATH = db.PROJECT_ROOT / "data" / "pricing_cache.json"
DEFAULT_FALLBACK_PATH = Path(__file__).with_name("pricing_fallback.json")
DEFAULT_USD_CNY = 7.2
REVIEWED_FUZZY_MAPPINGS: dict[tuple[str, str], str] = {}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PricingEntry:
    input_cost_per_token: float
    cache_read_input_token_cost: float
    output_cost_per_token: float
    nominal: bool
    source: str
    source_currency: str | None
    source_input_per_million_tokens: float | None
    source_cache_read_per_million_tokens: float | None
    source_output_per_million_tokens: float | None
    verified_at: str | None = None
    fetched_at: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    freshness: str = "fresh"
    matched_key: str = ""
    match_kind: str = "exact"


@dataclass
class PricingCatalog:
    litellm: dict[str, dict[str, object]]
    supplements: dict[str, tuple[PricingEntry, ...]]
    freshness: str
    source: str
    fetched_at: float | None
    observed_at: float = field(default_factory=time.time)


def is_reviewed_fuzzy_match(provider: str, model: str, matched_key: str) -> bool:
    reviewed = REVIEWED_FUZZY_MAPPINGS.get((provider.strip().lower(), model.strip().lower()))
    return reviewed is not None and reviewed.lower() == matched_key.strip().lower()


def usd_cny_rate() -> float:
    raw = os.environ.get("AI_RADAR_USD_CNY", "").strip()
    rate = float(raw) if raw else DEFAULT_USD_CNY
    if rate <= 0:
        raise ValueError("AI_RADAR_USD_CNY must be greater than zero")
    return rate


def _parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _validate_supplements(entries: dict[str, tuple[PricingEntry, ...]]) -> None:
    for key, intervals in entries.items():
        previous_end: datetime | None = None
        for index, entry in enumerate(intervals):
            if entry.effective_from is None:
                raise ValueError(f"supplement {key} is missing effective_from")
            start = _parse_utc(entry.effective_from)
            end = _parse_utc(entry.effective_to) if entry.effective_to else None
            if end is not None and start >= end:
                raise ValueError(f"supplement {key} has an inverted effective interval")
            if previous_end is not None and start < previous_end:
                raise ValueError(f"supplement {key} has overlapping effective intervals")
            if previous_end is None and index > 0:
                raise ValueError(f"supplement {key} has an open interval before a later interval")
            previous_end = end


def _supplements(rate: float) -> dict[str, tuple[PricingEntry, ...]]:
    source = "https://developer.volcengine.com/articles/7644244356211507238"

    def cny_tariff(input_cny: float, cache_cny: float, output_cny: float) -> PricingEntry:
        return PricingEntry(
            input_cost_per_token=input_cny / rate / 1_000_000,
            cache_read_input_token_cost=cache_cny / rate / 1_000_000,
            output_cost_per_token=output_cny / rate / 1_000_000,
            nominal=True,
            source=source,
            source_currency="CNY",
            source_input_per_million_tokens=input_cny,
            source_cache_read_per_million_tokens=cache_cny,
            source_output_per_million_tokens=output_cny,
            verified_at="2026-08-10",
            effective_from="2026-05-27T00:00:00Z",
        )

    supplements: dict[str, tuple[PricingEntry, ...]] = {
        "ark/deepseek-v4-pro-260425": (cny_tariff(12, 0.1, 24),),
        "ark/deepseek-v4-flash-260425": (cny_tariff(1, 0.02, 2),),
        "ark/deepseek-v4-flash-ga-260731": (cny_tariff(1, 0.02, 2),),
    }
    _validate_supplements(supplements)
    return supplements


def _normalize_table(payload: object) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict):
        raise ValueError("pricing table must be an object")
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def _read_cache(path: Path) -> tuple[float, dict[str, dict[str, object]]] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return None
    try:
        fetched_at = float(payload["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return None
    try:
        data = _normalize_table(payload["data"])
    except ValueError:
        return None
    return fetched_at, data


def _read_fallback(path: Path) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"bundled pricing fallback is unavailable: {path}") from exc
    try:
        return _normalize_table(payload)
    except ValueError as exc:
        raise RuntimeError(f"bundled pricing fallback is invalid: {path}") from exc


def _write_cache(path: Path, data: object, fetched_at: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": fetched_at, "data": data}, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not write pricing cache to %s: %s", path, exc)


def _fetch_litellm_pricing() -> dict[str, object]:
    request = urllib.request.Request(LITELLM_PRICING_URL, headers={"User-Agent": "ai-radar/0.1"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("LiteLLM pricing response must be an object")
    return payload


def get_pricing(
    *,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    fallback_path: str | Path = DEFAULT_FALLBACK_PATH,
    fetcher: Callable[[], object] | None = None,
    now: Callable[[], float] | None = None,
    persist: bool = True,
) -> PricingCatalog:
    if os.environ.get("AI_RADAR_LLM_PRICING_JSON") is not None:
        raise ValueError(
            "AI_RADAR_LLM_PRICING_JSON is retired; remove it and use the managed pricing catalog"
        )
    now_fn = now or time.time
    observed_at = now_fn()
    cache = _read_cache(Path(cache_path))
    if cache is not None:
        fetched_at, cached_data = cache
        if observed_at - fetched_at < PRICING_TTL_SECONDS:
            return PricingCatalog(
                litellm=cached_data,
                supplements=_supplements(usd_cny_rate()),
                freshness="fresh",
                source="litellm-cache",
                fetched_at=fetched_at,
                observed_at=observed_at,
            )

    fetch = fetcher or _fetch_litellm_pricing
    try:
        fetched = _normalize_table(fetch())
        if persist:
            _write_cache(Path(cache_path), fetched, observed_at)
        return PricingCatalog(
            litellm=fetched,
            supplements=_supplements(usd_cny_rate()),
            freshness="fresh",
            source="litellm-live",
            fetched_at=observed_at,
            observed_at=observed_at,
        )
    except Exception as exc:
        logger.warning("Pricing refresh failed; using stale data: %s", exc)
        if cache is not None:
            fetched_at, data = cache
            source = "expired-cache"
        else:
            fetched_at = None
            data = _read_fallback(Path(fallback_path))
            source = "bundled-fallback"
        return PricingCatalog(
            litellm=data,
            supplements=_supplements(usd_cny_rate()),
            freshness="stale",
            source=source,
            fetched_at=fetched_at,
            observed_at=observed_at,
        )


def _entry_from_litellm(
    key: str,
    raw: dict[str, object],
    catalog: PricingCatalog,
    *,
    match_kind: str,
) -> PricingEntry | None:
    def number(value: object) -> float:
        if not isinstance(value, str | int | float):
            raise TypeError("tariff must be numeric")
        return float(value)

    try:
        input_cost = number(raw["input_cost_per_token"])
        output_cost = number(raw["output_cost_per_token"])
        cache_cost = number(raw.get("cache_read_input_token_cost", input_cost))
    except (KeyError, TypeError, ValueError):
        return None
    fetched_at = (
        datetime.fromtimestamp(catalog.fetched_at, UTC).date().isoformat()
        if catalog.fetched_at is not None
        else None
    )
    return PricingEntry(
        input_cost_per_token=input_cost,
        cache_read_input_token_cost=cache_cost,
        output_cost_per_token=output_cost,
        nominal=False,
        source=LITELLM_PRICING_URL,
        source_currency=None,
        source_input_per_million_tokens=None,
        source_cache_read_per_million_tokens=None,
        source_output_per_million_tokens=None,
        fetched_at=fetched_at,
        freshness=catalog.freshness,
        matched_key=key,
        match_kind=match_kind,
    )


def _supplement_freshness(entry: PricingEntry, observed_at: float) -> str:
    if entry.verified_at is None:
        return "due-review"
    try:
        checked = date.fromisoformat(entry.verified_at)
    except ValueError:
        return "due-review"
    age_seconds = (datetime.fromtimestamp(observed_at, UTC).date() - checked).days * 86400
    return "due-review" if age_seconds > SUPPLEMENT_REVIEW_SECONDS else "fresh"


def resolve_price(
    provider: str,
    model: str,
    catalog: PricingCatalog,
    *,
    effective_at: str | datetime | None = None,
) -> PricingEntry | None:
    provider_lower = provider.strip().lower()
    model_lower = model.strip().lower()
    canonical = f"{provider_lower}/{model_lower}"
    supplement_intervals = catalog.supplements.get(canonical, ())
    effective = (
        _parse_utc(effective_at)
        if effective_at is not None
        else datetime.fromtimestamp(catalog.observed_at, UTC)
    )
    for supplement in supplement_intervals:
        start = _parse_utc(supplement.effective_from or "")
        end = _parse_utc(supplement.effective_to) if supplement.effective_to else None
        if start <= effective and (end is None or effective < end):
            return replace(
                supplement,
                freshness=_supplement_freshness(supplement, catalog.observed_at),
                matched_key=canonical,
                match_kind="exact",
            )

    exact_matches: list[tuple[str, dict[str, object]]] = []
    fuzzy_matches: list[tuple[str, dict[str, object]]] = []
    for key, raw in catalog.litellm.items():
        if not isinstance(raw, dict):
            continue
        key_provider, separator, key_model = key.lower().partition("/")
        if not separator or key_provider != provider_lower:
            continue
        if key_model == model_lower:
            exact_matches.append((key, raw))
        elif model_lower and model_lower in key_model:
            # Only expand a shorter requested model into a provider-scoped
            # catalog key. The reverse direction would absorb an unknown date
            # suffix into a bare upstream model and silently misprice it.
            fuzzy_matches.append((key, raw))
    matches = exact_matches or fuzzy_matches
    if len(matches) == 1:
        key, raw = matches[0]
        return _entry_from_litellm(
            key,
            raw,
            catalog,
            match_kind="exact" if exact_matches else "fuzzy",
        )
    return None
