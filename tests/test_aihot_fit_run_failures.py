"""A stopped or failed stage must not destroy the run's summary.

`served_models` reads each row's `raw.model` to record which model the API actually answered
with. A stage that was skipped because the run stopped, or that failed outside the runner's own
handling, writes `{"output": None, ...}` -- and `payload.get("output", {})` returns the default
only when the key is ABSENT, so the None flowed straight into `.get("raw")`. The crash landed
between writing outputs.jsonl and writing run.json, so the run left a complete output file with
no identity record beside it. An ARK 429 sets every remaining row to exactly that shape, which
made a rate limit destructive rather than merely truncating.
"""

from __future__ import annotations

import pytest

from airadar.eval.aihot_fit.run import served_models

_ANSWERED = {"output": {"raw": {"model": "deepseek-v4-flash-ga-260731"}}, "error": None}


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"output": None, "error": None, "latency_ms": None, "skipped": "stopped"}, id="stopped"),
        pytest.param({"output": None, "error": "RuntimeError: 429", "latency_ms": None}, id="failed"),
    ],
)
def test_a_row_without_an_output_does_not_break_the_summary(row: dict[str, object]) -> None:
    assert served_models([{"score": row}], "score") == []


def test_the_answering_model_is_still_reported_alongside_such_a_row() -> None:
    # Asserting only "does not raise" would pass on a served_models that returned [] for
    # everything, so the surviving row has to be read out of the same call.
    rows = [{"score": {"output": None, "error": None, "skipped": "stopped"}}, {"score": _ANSWERED}]
    assert served_models(rows, "score") == ["deepseek-v4-flash-ga-260731"]


def test_a_stage_the_row_never_reached_is_ignored() -> None:
    assert served_models([{"prefilter": _ANSWERED}], "score") == []
