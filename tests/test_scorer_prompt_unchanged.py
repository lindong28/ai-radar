from __future__ import annotations

import hashlib

from airadar.provider.base import ProviderItem
from airadar.scorer.prompts import render_scoring_prompt


def test_scorer_prompt_hash_unchanged_for_plan_20260513() -> None:
    """The pin exists so a scorer prompt change has to be deliberate, not so it never happens.

    Changed once, on 2026-09-06, to add a `significance` dimension. The five existing signals
    each tracked AIHOT's own 0-100 score at rho 0.37-0.54 while none of them asked how large the
    event was, and AIHOT scores the event: 14 of its 18 highest-scoring items are one
    acquisition, including a post whose entire body is a link. Paired over the same 300
    questions the fit moved 0.5275 to 0.5915 on unchanged weights, and the new signal alone
    reads 0.6234 -- higher than any of the five.

    Two displacement readings, because they answer different questions and only the second
    describes what shipped. This prompt change ALONE, against the ranking weights in force
    at the time: top-20 kept 15/20, top-100 kept 82/100, rank agreement 0.928 (300 questions).
    This change TOGETHER WITH the fitted weights that followed it, which is what a reader
    sees: rank agreement 0.854, top-20 kept 0/20, top-100 kept 46/100 over 2741 questions,
    and replaying the real selection fill day by day, 259 of 369 curated items survive but
    only 53 of 110 top-ten slots do. The first pair was the basis for adopting the prompt and
    understates the combined move by a lot; quoting it alone read as reassurance it had not
    earned.
    """
    item = ProviderItem(
        id="hash-fixture",
        title="AI runtime benchmark",
        url="https://example.com/ai-runtime",
        source_id="fixture",
        tier="T1",
        author="Ada",
        published_at="2026-05-08T00:00:00Z",
        content_text="A fixture about model serving, evals, APIs, and inference operations.",
    )
    prompt = render_scoring_prompt(item)
    digest = hashlib.sha256((prompt["system"] + "\n" + prompt["user"]).encode()).hexdigest()

    assert digest == "d72616018701b210f798d07f2702d2424c915b14e366636ec1bc02a18eda8609"
