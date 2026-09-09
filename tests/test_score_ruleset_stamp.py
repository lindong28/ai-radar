"""The scoring stamp must move whenever what the model is asked moves.

`scorer/runner.py` skips any item that already has a scoring row at the current
`ruleset_version` (`NOT EXISTS (... scored.ruleset_version=?)`), so a prompt change that
leaves the stamp alone never reaches an already-scored item, and the rows it does write
are indistinguishable afterwards from rows produced by the older prompt. That is the
same defect measured on enrich on 2026-09-08, where 83.2% of the pool's rows had come
from a retired May prompt while the stamp sat frozen.

Scoring had not yet gone wrong when this was written: measured 2026-09-09, all 46,601
rows at `2026-09-06.r1` came from a single prompt generation, so the hand bump done on
2026-09-06 was correct. What a hand-bumped date cannot carry is a *second edit on the
same day*, which is routine in this project — so the stamp derives from the inputs, and
these tests check that derivation rather than pinning a value.
"""

from __future__ import annotations

import airadar.ruleset as ruleset
from airadar.ruleset import (
    PINNED_SCORE_RULESET_DATE,
    current_score_version,
    current_version,
    current_version_v2,
)


def test_stamp_carries_a_digest_of_the_inputs() -> None:
    date, rev, digest = current_score_version().split(".", 2)
    assert date == PINNED_SCORE_RULESET_DATE
    assert rev == "r1"
    assert digest == ruleset.score_inputs_digest()
    assert len(digest) == 8


def test_editing_the_system_prompt_moves_the_stamp(monkeypatch) -> None:
    before = ruleset.score_inputs_digest()
    from airadar.scorer import prompts

    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + " x")
    assert ruleset.score_inputs_digest() != before


def test_editing_the_user_template_moves_the_stamp(monkeypatch) -> None:
    """Hashing only SYSTEM_PROMPT would leave a whole-template swap byte-identical."""
    before = ruleset.score_inputs_digest()
    from jinja2 import Template

    from airadar.scorer import prompts

    monkeypatch.setattr(prompts, "USER_TEMPLATE", Template("wholly different {{ item.title }}"))
    assert ruleset.score_inputs_digest() != before


def test_two_prompt_edits_on_one_day_get_two_stamps(monkeypatch) -> None:
    """The failure a date cannot carry: the date is identical, the stamps must not be."""
    from airadar.scorer import prompts

    original = prompts.SYSTEM_PROMPT
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", original + " first edit")
    first = current_score_version()
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", original + " second edit")
    second = current_score_version()
    assert first.split(".")[0] == second.split(".")[0]  # same date
    assert first != second


def test_score_stamp_is_distinct_from_prefilter_and_enrich() -> None:
    """Sharing a stamp across stages makes one stage's bump invalidate another's rows."""
    assert current_score_version() != current_version()
    assert current_score_version() != current_version_v2()
