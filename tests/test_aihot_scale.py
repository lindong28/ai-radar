"""What would break quietly if the AIHOT scale map were wrong.

The map has no runtime error mode: a swapped knot array, a drifted coefficient, or a snapshot
that stopped loading all return plausible numbers. An adversarial review of the first version
of this file found that four assertions pinning only the two endpoints left the slope, the
intercepts and the whole interior of the map unconstrained -- doubling the slope pushed 54% of
items to the top of the scale with the suite still green. These assertions are chosen to fail
on that class, using the provenance the snapshot already records.
"""

import json
from pathlib import Path

import pytest

from airadar.curator import aihot_scale
from airadar.curator.aihot_scale import (
    _SNAPSHOT_PATH,
    _validate,
    aligned_score,
    fitted_score,
)
from airadar.curator.weights import Weights

SNAPSHOT = json.loads(Path(_SNAPSHOT_PATH).read_text(encoding="utf-8"))


def test_category_changes_the_score_at_the_same_base() -> None:
    """The whole point of M2: two items with identical signals score differently by kind."""
    assert fitted_score(5.0, "industry") > fitted_score(5.0, "paper")
    assert aligned_score(5.0, "industry") > aligned_score(5.0, "paper")


def test_the_fit_still_places_the_population_where_aihot_does() -> None:
    """Pins the slope and the intercepts, which endpoint assertions leave free.

    A slope or intercept that drifts moves the median item without moving either endpoint, so
    it is invisible to every other test here. The reference values are the fit's own: an item
    at the fitting sample's median base scores near AIHOT's median.
    """
    assert fitted_score(5.0, "industry") == pytest.approx(47.787, abs=0.01)
    assert aligned_score(5.0, "industry") == pytest.approx(48.0, abs=0.5)
    assert fitted_score(0.0, "paper") == pytest.approx(SNAPSHOT["category_intercepts"]["paper"])
    assert SNAPSHOT["slope"] == pytest.approx(5.0679, abs=0.001)


def test_unknown_category_falls_back_to_the_recorded_frequency_weighted_mean() -> None:
    """Production scores items whose enrich failed; a 0 intercept would sink them all.

    Recomputed from the counts the snapshot itself records, so a fallback edited by hand --
    or left behind by a refit that updated the intercepts but not this -- fails here.
    """
    fallback = fitted_score(5.0, None)
    assert fitted_score(5.0, "no-such-category") == fallback
    counts = SNAPSHOT["provenance"]["category_counts"]
    intercepts = SNAPSHOT["category_intercepts"]
    expected = sum(intercepts[c] * n for c, n in counts.items()) / sum(counts.values())
    assert SNAPSHOT["fallback_intercept"] == pytest.approx(expected, abs=0.001)
    assert sum(counts.values()) == SNAPSHOT["provenance"]["rows"]
    # And that the code actually READS that field. Asserting the field is correct leaves the
    # call site free: substituting min(intercepts.values()) satisfies everything above while
    # moving every uncategorised item down 6.6 points.
    assert fallback == pytest.approx(SNAPSHOT["slope"] * 5.0 + SNAPSHOT["fallback_intercept"])
    assert fallback != pytest.approx(SNAPSHOT["slope"] * 5.0 + min(intercepts.values()))


def test_alignment_lands_on_aihot_range_not_the_fitted_one() -> None:
    """Guards against returning the fitted score, or aligning onto the wrong knot array."""
    top = aligned_score(10.0, "industry")
    assert top == pytest.approx(SNAPSHOT["aihot_knots"][100])
    assert top > max(SNAPSHOT["fitted_knots"])
    assert aligned_score(0.0, "paper") == pytest.approx(SNAPSHOT["aihot_knots"][0])


def test_map_is_monotone_and_tracks_aihot_through_the_interior() -> None:
    """Monotone is necessary but far from sufficient -- a doubled slope is monotone too.

    So also check the interior against AIHOT's own quantiles: an item at the fitting sample's
    p25 fitted score must come out near AIHOT's p25, and likewise at p75.
    """
    scores = [aligned_score(b / 10, "tutorial") for b in range(0, 101)]
    assert all(b >= a for a, b in zip(scores, scores[1:]))
    # p1 and p99 are the load-bearing probes, not p25/p50/p75. `aihot_knots` steps by 1.0
    # through the middle and by 11.0 / 8.23 at the two ends, so a middle probe with abs=1.0
    # tolerance cannot see a dropped interpolation -- the error it would catch is exactly the
    # size of the tolerance. Removing either interpolation moves items by up to 10 points, and
    # every one of those items is in a tail.
    for percentile in (1, 25, 50, 75, 99):
        fitted_at = SNAPSHOT["fitted_knots"][percentile]
        base = (fitted_at - SNAPSHOT["category_intercepts"]["industry"]) / SNAPSHOT["slope"]
        assert aligned_score(base, "industry") == pytest.approx(
            SNAPSHOT["aihot_knots"][percentile], abs=0.2
        )
    # Midway between two knots the answer must be midway too -- the single assertion that
    # fails if either _percentile_of or _value_at stops interpolating.
    low, high = SNAPSHOT["fitted_knots"][98], SNAPSHOT["fitted_knots"][99]
    midpoint = (low + high) / 2
    base = (midpoint - SNAPSHOT["category_intercepts"]["industry"]) / SNAPSHOT["slope"]
    expected = (SNAPSHOT["aihot_knots"][98] + SNAPSHOT["aihot_knots"][99]) / 2
    assert aligned_score(base, "industry") == pytest.approx(expected, abs=0.2)


def test_a_non_finite_base_raises_instead_of_scoring_top_of_scale() -> None:
    """NaN defeats both range checks and resolves through min() to the top of the scale."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            aligned_score(bad, "industry")


def test_snapshot_is_internally_consistent_and_names_its_own_provenance() -> None:
    """T8 hangs off these fields, and one of them is the refresh trigger's only detector.

    The recorded weight vector is what `base` was computed under. Asserting it still equals
    the shipped default turns "someone changed curator/weights.py" -- one of the conditions
    that invalidates this snapshot -- into a red test rather than a silent staleness.
    """
    provenance = SNAPSHOT["provenance"]
    assert provenance["fitted_on_run"] and provenance["questions_sha256"]
    assert provenance["row_filter"]
    assert provenance["curator_weights"] == Weights.default().as_dict()
    assert len(SNAPSHOT["fitted_knots"]) == len(SNAPSHOT["aihot_knots"]) == 101
    assert SNAPSHOT["aihot_knots"][0] >= 0.0 and SNAPSHOT["aihot_knots"][100] <= 100.0


def test_validation_rejects_the_snapshots_that_would_look_healthy() -> None:
    """Without this the validation block can be deleted with every other test still green.

    Each case is one that passes a shape or monotonicity check and then yields confident
    constants: NaN compares False against everything, and a constant array is non-decreasing.
    """
    for broken in (
        {"aihot_knots": [float("nan")] * 101},
        {"fitted_knots": [float("nan")] * 101},
        {"aihot_knots": [0.0] * 101},
        {"fitted_knots": [40.0] * 101},
        {"slope": -5.0679},
        {"slope": 0.0},
        # `nan <= 0` and `inf <= 0` are both False, so an ordering test alone admits either --
        # and a NaN slope puts every single item at the top of the scale.
        {"slope": float("nan")},
        {"slope": float("inf")},
        {"category_intercepts": {**SNAPSHOT["category_intercepts"], "paper": float("nan")}},
        {"fallback_intercept": float("nan")},
        {"aihot_knots": [k * 10 for k in SNAPSHOT["aihot_knots"]]},
        {"fallback_intercept": 0.0},
    ):
        with pytest.raises(ValueError):
            _validate({**SNAPSHOT, **broken})
    _validate(SNAPSHOT)  # the shipped one must still pass


def test_a_broken_snapshot_file_fails_the_load_rather_than_scoring(tmp_path, monkeypatch) -> None:
    """`_validate` existing is not the same as it being called on the load path.

    Testing the function directly leaves the call site free: deleting `_validate(data)` from
    the loader passes every other assertion here, because they all use the shipped file.
    """
    broken = tmp_path / "snap.json"
    broken.write_text(json.dumps({**SNAPSHOT, "aihot_knots": [0.0] * 101}), encoding="utf-8")
    monkeypatch.setattr(aihot_scale, "_SNAPSHOT_PATH", broken)
    aihot_scale._cached_snapshot.cache_clear()
    try:
        with pytest.raises(ValueError):
            aligned_score(5.0, "industry")
    finally:
        aihot_scale._cached_snapshot.cache_clear()
