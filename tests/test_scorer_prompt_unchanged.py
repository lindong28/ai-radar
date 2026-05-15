from __future__ import annotations

import hashlib

from airadar.provider.base import ProviderItem
from airadar.scorer.prompts import render_scoring_prompt


def test_scorer_prompt_hash_unchanged_for_plan_20260513() -> None:
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

    assert digest == "6e18b538ee415701aeb9390821c02ec0d87ed007508d8de44737c6b48a4e04c4"
