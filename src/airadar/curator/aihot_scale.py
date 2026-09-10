"""Map a six-dimension base score onto AIHOT's 0-100 scale, conditioned on category.

ADR-20260907-a1c4 §M2. Two steps, in this order:

  1. a category-conditional linear fit, `a * base + b[category]`, because AIHOT scores the
     same base differently depending on what kind of item it is -- our `weighted_score` has
     no category term at all and so cannot express that (still true of `weighted_score` as of
     2026-09-10, but no longer of the ORDERING: `select.ranking_key` applies
     `CATEGORY_MULTIPLIERS` -- today `paper` at 0.80 -- on top of it. If this module is ever
     wired in, check it does not deduct twice; both terms move papers the same direction. The
     staleness guard in `tests/test_aihot_scale.py` compares the recorded weights vector against
     `Weights.default()` and structurally cannot see that table, which is not part of `Weights`);
  2. a quantile alignment of the fitted score onto AIHOT's own score distribution, because
     the fit regresses to the mean: it spans 18.4-68.1 where AIHOT spans 0-91.

NOTHING CALLS THIS. Not production, not the evaluation -- `src/airadar/eval/aihot_fit/metrics.py` reads
`fit_score` and `weighted_score` and has never heard of this module. Its only caller today is
its own test. That is deliberate for production (see below) and simply unfinished for the
evaluation, and it has a consequence worth stating plainly: the refresh trigger below has no
implementation and no detection point anywhere in this tree.

Wiring it into curation is a separate unit of work. `weighted_score` is on 0-10 and
`select.DEFAULT_THRESHOLD` is 6.5, so substituting a 0-100 function widens admission from 759
of 2741 items to 2708 -- 33 still fall below 6.5, so not literally everything, but 3.57x is
not a threshold that survives. `scoreTierClass` in app.js also carries 80/65 cutoffs against
`base * 10`. ADR-a1c4 lists all three; the instruction for this unit was to leave thresholds
and selection alone.

## The snapshot, and when it stops being true

`aihot_scale_snapshot.json` holds the slope, the intercepts, and two 101-point knot arrays --
all six derived from ONE row set (schema v2; v1 mixed two, and one junk row in the difference
set alone determined the top of the output scale). If AIHOT's own scoring drifts, alignment
pins us to the old shape while looking entirely healthy: no reading goes red. Which is why
the refresh condition has to be run rather than noticed -- and, per the paragraph above,
nothing runs it yet.

Refresh when ANY of these holds (ADR-a1c4 §M2 carries the calibration and its limits):

  - `slope` / `category_intercepts` are refit, or `curator/weights.py`'s vector changes --
    either makes `fitted_knots` stale regardless of what AIHOT does. These two have an event
    to hang on and should not wait for a weekly sample; `tests/test_aihot_scale.py` asserts
    the recorded vector still equals `Weights.default()`, which turns this into a red test
    the moment the vector moves.
  - AIHOT's own distribution drifting. THIS ONE HAS NO THRESHOLD, and the ADR records why
    rather than shipping a number. The statistic is settled -- the mean absolute gap against
    `aihot_knots` at the nine deciles p10, p20, ... p90 -- but the threshold previously
    written here was calibrated against a null resampled from the very windows it was meant
    to test, so "the real windows sit inside the noise band" was an identity rather than a
    reading. Against a proper no-drift null (resampling the snapshot's own rows) the n=1200
    95th percentile is 1.12-1.22 while those windows read 3.22 and 2.16 -- the opposite
    verdict. Two windows cannot say whether that gap is drift or a population difference, so
    the ADR leaves it open and says what data would close it.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

_SNAPSHOT_PATH = Path(__file__).with_name("aihot_scale_snapshot.json")


def _validate(data: dict[str, Any]) -> None:
    """Reject a snapshot that would still produce plausible-looking numbers.

    Shape alone is not enough. A knot array of all-NaN passes a monotonicity check (every
    comparison against NaN is False), an all-zero one passes it too, and either yields a
    module that returns a confident constant. So each of these rejects a specific silent
    failure rather than a malformed file.
    """
    slope = float(data["slope"])
    # `nan <= 0` and `inf <= 0` are both False, so an ordering test alone lets either
    # through -- and a NaN slope makes every item come out at the top of the scale.
    if not math.isfinite(slope) or slope <= 0:
        raise ValueError(f"slope must be finite and positive, got {slope!r}")
    for key in ("fitted_knots", "aihot_knots"):
        knots = data[key]
        if len(knots) != 101:
            raise ValueError(f"{key} must hold 101 percentile knots, got {len(knots)}")
        if not all(math.isfinite(k) for k in knots):
            raise ValueError(f"{key} contains a non-finite knot")
        if any(b < a for a, b in zip(knots, knots[1:])):
            raise ValueError(f"{key} must be non-decreasing")
        if knots[0] == knots[-1]:
            raise ValueError(f"{key} is constant, which maps every input to one value")
    if not 0.0 <= data["aihot_knots"][0] or data["aihot_knots"][100] > 100.0:
        raise ValueError("aihot_knots must lie within the 0-100 scale it claims to be on")
    intercepts = data["category_intercepts"]
    fallback = float(data["fallback_intercept"])
    # Same trap one category down: a single NaN intercept survives min()/max() and sends
    # just that category to the top, which is harder to notice than the whole table going.
    if not intercepts or not all(math.isfinite(float(v)) for v in intercepts.values()):
        raise ValueError("category_intercepts must all be finite")
    if not math.isfinite(fallback):
        raise ValueError("fallback_intercept must be finite")
    if not min(intercepts.values()) <= fallback <= max(intercepts.values()):
        raise ValueError("fallback_intercept must lie within the fitted intercepts")


@lru_cache(maxsize=1)
def _cached_snapshot() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    _validate(data)
    return data


def _snapshot() -> dict[str, Any]:
    # A copy: the cache holds one object, and a caller that edited it would silently change
    # every later score in the process.
    return deepcopy(_cached_snapshot())


def fitted_score(base: float, category: str | None) -> float:
    """`a * base + b[category]`, on the fit's own scale (roughly 18-68).

    An item whose category we did not predict gets the frequency-weighted mean intercept over
    the fitting sample. That holds the MEAN fitted score of such a population, not its shape:
    replacing every category with the fallback moves the mean by 0.0001 but narrows the spread
    2.0% and lifts the bottom edge 0.80. Per item the error against the true category runs
    -3.28 to +6.62, mean absolute 1.78. It is the least-bad constant, not a correction -- and
    the items that reach it are the ones whose enrich failed AND emitted no category at all
    (71 rows; the other 46 enrich failures did emit one and take a normal intercept), a
    population well below average on both sides of the fit: base 2.06 against 4.96, AIHOT
    34.7 against 44.32.
    """
    snapshot = _snapshot()
    intercepts: dict[str, float] = snapshot["category_intercepts"]
    intercept = intercepts.get(category or "", snapshot["fallback_intercept"])
    return float(snapshot["slope"]) * float(base) + float(intercept)


def _percentile_of(value: float, knots: list[float]) -> float:
    """Where `value` falls in `knots`, as a 0-100 position, interpolating between knots.

    A flat stretch of the empirical distribution resolves to its UPPER edge: `low` ends as the
    largest index with `knots[low] <= value`.
    """
    if value <= knots[0]:
        return 0.0
    if value >= knots[-1]:
        return 100.0
    low, high = 0, len(knots) - 1
    while high - low > 1:
        mid = (low + high) // 2
        if knots[mid] <= value:
            low = mid
        else:
            high = mid
    # Strictly positive: the early returns put `value` inside the range, and the invariant
    # `knots[low] <= value < knots[high]` then forces `knots[high] > knots[low]`.
    return low + (value - knots[low]) / (knots[high] - knots[low])


def _value_at(percentile: float, knots: list[float]) -> float:
    position = max(0.0, min(100.0, percentile))
    low = int(position)
    if low >= 100:
        return float(knots[100])
    return float(knots[low] + (knots[low + 1] - knots[low]) * (position - low))


def aligned_score(base: float, category: str | None) -> float:
    """The fitted score pushed through to AIHOT's distribution, 0-100.

    Quantile alignment, not a linear rescale: it reproduces AIHOT's shape at every percentile,
    where matching variance linearly left the top of the range 11 points short. The map is
    piecewise LINEAR. Repeated values in `aihot_knots` flatten it, creating ties the fitted
    score did not have; repeated values in `fitted_knots` do the opposite and make it jump
    (the flat run at index 4-5 sends 23.9386 and 23.9387 to 17.0 and 18.0). Measured on the
    published row set, distinct values fall 509 to 341 and Spearman against AIHOT moves
    -0.000089, i.e. not detectably.
    """
    if not math.isfinite(base):
        # Without this, NaN defeats both early returns in `_percentile_of`, propagates to
        # `min(100.0, nan)` -- which Python resolves to 100.0 -- and returns the TOP of the
        # scale. A missing score would read as the best item in the set.
        raise ValueError(f"base must be a finite number, got {base!r}")
    snapshot = _snapshot()
    percentile = _percentile_of(fitted_score(base, category), snapshot["fitted_knots"])
    return round(_value_at(percentile, snapshot["aihot_knots"]), 4)
