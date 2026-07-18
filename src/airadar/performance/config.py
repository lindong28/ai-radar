from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import PROJECT_ROOT
from .budgets import LocalBudget

DEFAULT_PERFORMANCE_CONFIG_PATH = PROJECT_ROOT / "config" / "performance.toml"
_BUDGET_FIELDS = {
    "curated_api": frozenset({"median_ms", "p95_ms"}),
    "homepage_ssr": frozenset({"median_ms", "p95_ms"}),
    "wechat_api": frozenset({"median_ms"}),
    "wechat_ssr": frozenset({"median_ms"}),
}


class PerformanceConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LocalEngineeringConfig:
    schema_version: int
    percentile_estimator: str
    warm_samples: int
    budgets: dict[str, LocalBudget]


def _table(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PerformanceConfigError(f"{field} must be a table")
    return value


def _finite_positive_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PerformanceConfigError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PerformanceConfigError(f"{field} must be finite and positive")
    return number


def load_local_engineering_config(
    path: Path = DEFAULT_PERFORMANCE_CONFIG_PATH,
) -> LocalEngineeringConfig:
    try:
        payload = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PerformanceConfigError(f"cannot load performance config: {error}") from error

    if payload.get("schema_version") != 1:
        raise PerformanceConfigError("unsupported performance schema_version")
    evaluation = _table(payload.get("engineering_evaluation"), field="engineering_evaluation")
    if set(evaluation) != {"percentile_estimator", "warm_samples"}:
        raise PerformanceConfigError("engineering_evaluation field set is not canonical")
    percentile_estimator = evaluation["percentile_estimator"]
    if percentile_estimator != "nearest-rank":
        raise PerformanceConfigError("engineering_evaluation percentile_estimator must be nearest-rank")
    warm_samples = evaluation["warm_samples"]
    if isinstance(warm_samples, bool) or not isinstance(warm_samples, int) or warm_samples <= 0:
        raise PerformanceConfigError("engineering_evaluation warm_samples must be a positive integer")

    raw_metrics = payload.get("engineering_metrics")
    if not isinstance(raw_metrics, list):
        raise PerformanceConfigError("engineering_metrics must be an array")
    raw_budgets: dict[str, dict[str, object]] = {}
    for row in raw_metrics:
        metric = _table(row, field="engineering_metrics[]")
        identifier = metric.get("id")
        if not isinstance(identifier, str):
            raise PerformanceConfigError("engineering metric id must be a string")
        raw_budgets[identifier] = {
            "median_ms": metric.get("median_budget_ms"),
            **({"p95_ms": metric["p95_budget_ms"]} if "p95_budget_ms" in metric else {}),
        }
    if set(raw_budgets) != set(_BUDGET_FIELDS):
        raise PerformanceConfigError("engineering metric key set is not canonical")
    budgets = {
        name: LocalBudget(
            median_ms=_finite_positive_number(
                raw_budgets[name]["median_ms"], field=f"{name}.median_budget_ms"
            ),
            p95_ms=(
                _finite_positive_number(raw_budgets[name]["p95_ms"], field=f"{name}.p95_budget_ms")
                if "p95_ms" in raw_budgets[name]
                else None
            ),
        )
        for name in _BUDGET_FIELDS
    }
    return LocalEngineeringConfig(
        schema_version=1,
        percentile_estimator=str(percentile_estimator),
        warm_samples=warm_samples,
        budgets=budgets,
    )
