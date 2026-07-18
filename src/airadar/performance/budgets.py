from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalBudget:
    median_ms: float
    p95_ms: float | None


@dataclass(frozen=True, slots=True)
class SampleEvaluation:
    passed: bool
    raw_median_ms: float | None
    raw_p95_ms: float | None
    display_median_ms: float | None
    display_p95_ms: float | None
    median_headroom_ms: float | None
    p95_headroom_ms: float | None
    errors: tuple[str, ...]


def evaluate_samples(samples: list[float], budget: LocalBudget) -> SampleEvaluation:
    if not samples:
        return SampleEvaluation(False, None, None, None, None, None, None, ("samples are empty",))
    raw_samples = [float(sample) for sample in samples]
    if not all(math.isfinite(sample) and sample >= 0 for sample in raw_samples):
        return SampleEvaluation(False, None, None, None, None, None, None, ("samples must be finite",))

    raw_median = float(statistics.median(raw_samples))
    ordered = sorted(raw_samples)
    raw_p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    median_headroom = budget.median_ms - raw_median
    p95_headroom = budget.p95_ms - raw_p95 if budget.p95_ms is not None else None
    passed = raw_median <= budget.median_ms and (p95_headroom is None or raw_p95 <= budget.p95_ms)
    return SampleEvaluation(
        passed=passed,
        raw_median_ms=raw_median,
        raw_p95_ms=raw_p95,
        display_median_ms=round(raw_median, 3),
        display_p95_ms=round(raw_p95, 3),
        median_headroom_ms=median_headroom,
        p95_headroom_ms=p95_headroom,
        errors=(),
    )
