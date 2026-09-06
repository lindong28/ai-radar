"""A signal added later must not push every older row down the ranking.

Every curation run recomputes `weighted_score` from each row's stored `numeric_json`, and rows
written before `significance` existed do not carry it. Reading absent as 0.0 would cost such a
row the whole of that dimension's weight on every run -- a systematic penalty applied to the
entire archive, invisible in testing because each newly scored row looks correct.
"""

from __future__ import annotations

import pytest

from airadar.curator.score import weighted_score
from airadar.curator.weights import Weights

_OLD_ROW = {"relevance": 6.0, "density": 6.0, "recency": 6.0, "authority": 6.0, "engineering": 6.0}
_WEIGHTS = Weights(relevance=0.1, density=0.3, recency=0.1, authority=0.1, engineering=0.0, significance=0.4)


def test_a_row_without_the_new_signal_is_not_charged_for_missing_it() -> None:
    # Every present signal is 6.0, so any correct rescaling lands on 6.0 * the weight total.
    # Zero-filling would give 0.6 * 6.0 = 3.6 instead.
    assert weighted_score(_OLD_ROW, _WEIGHTS, "T1.5") == pytest.approx(6.0)


def test_a_row_carrying_the_new_signal_is_scored_on_it() -> None:
    # The guard above passes on an implementation that ignores `significance` entirely, so the
    # signal has to be shown moving the number too.
    assert weighted_score({**_OLD_ROW, "significance": 0.0}, _WEIGHTS, "T1.5") == pytest.approx(3.6)
    assert weighted_score({**_OLD_ROW, "significance": 10.0}, _WEIGHTS, "T1.5") == pytest.approx(7.6)


def test_an_explicit_null_reads_as_absent_not_as_zero() -> None:
    # Which is how the provider records a model that did not return the field.
    assert weighted_score({**_OLD_ROW, "significance": None}, _WEIGHTS, "T1.5") == pytest.approx(6.0)


def test_a_row_missing_one_of_the_five_core_signals_still_raises() -> None:
    # Those have been emitted since the first scoring run, so absence is a defect, not history,
    # and rescaling it away would hide it.
    with pytest.raises(KeyError):
        weighted_score({k: v for k, v in _OLD_ROW.items() if k != "density"}, _WEIGHTS, "T1.5")


def test_a_core_dimension_is_required_even_at_zero_weight() -> None:
    # The first cut guarded only the weighted dimensions, which made the check depend on the
    # vector: under the shipped weights it required density and authority alone, and a provider
    # that stopped emitting relevance, recency or engineering would have passed silently. Those
    # five have been emitted since the first scoring run, so absence is a defect either way.
    weights = Weights(relevance=0.5, density=0.5, recency=0.0, authority=0.0, engineering=0.0)
    with pytest.raises(KeyError):
        weighted_score({"relevance": 4.0, "density": 8.0}, weights, "T1.5")
    full = {"relevance": 4.0, "density": 8.0, "recency": 0.0, "authority": 0.0, "engineering": 0.0}
    assert weighted_score(full, weights, "T1.5") == pytest.approx(6.0)
