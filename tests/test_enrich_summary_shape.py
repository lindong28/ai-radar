"""Sentence-count contract for enrich-v2 summaries.

The window was 3-5 and nothing in the suite held it: deleting the validator left 254 related
tests reading identically. It was widened to 1-5 on 2026-09-06 to fit AIHOT, which writes one
or two sentences for 74% of items -- the floor of three forced padding onto thin sources, and
the padding was usually an explanation of why the source was thin.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from airadar.enrich.schema_v2 import EnrichOutputV2

_BASE = {
    "title_zh": "标题",
    "why_recommend": "原文给出了这条产品更新的核心事实与生效范围，读者可以据此判断它是否值得展开阅读并对比前代。",
    "tags": ["智能体", "推理"],
    "primary_category": "product",
    "is_opinion": False,
}


def _with(summary: str) -> dict[str, object]:
    return {**_BASE, "summary_zh": summary}


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_one_to_five_sentences_are_accepted(count: int) -> None:
    # One and two are the point of the widening: they are 74% of AIHOT's summaries.
    assert EnrichOutputV2.model_validate(_with("这是一句写得足够长以越过最短字数下限的中文摘要内容。" * count))


def test_six_sentences_are_rejected() -> None:
    with pytest.raises(ValidationError, match="1 to 5 sentences"):
        EnrichOutputV2.model_validate(_with("这是一句写得足够长以越过最短字数下限的中文摘要内容。" * 6))


def test_text_without_a_terminal_mark_is_rejected() -> None:
    # A trailing fragment means the model ran out of budget mid-sentence; it is not a summary.
    with pytest.raises(ValidationError, match="1 to 5 sentences"):
        EnrichOutputV2.model_validate(_with("这是一句写得足够长以越过最短字数下限的中文摘要内容。后面这半句没有写完就断了"))


def test_stored_three_sentence_summaries_still_validate() -> None:
    # Widening has to stay read-safe: rows written under the old floor are still read back
    # through this same model, and rejecting them would blank the whole enrichment on the page.
    assert EnrichOutputV2.model_validate(_with("第一句写核心事实。第二句补充关键数字。第三句交代取舍。"))
