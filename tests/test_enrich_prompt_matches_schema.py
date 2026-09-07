"""The prompt's stated hard gates must be the schema's actual ones.

Written after a real drift, not a hypothetical one: the tags floor was relaxed from 2 to 1 in the
schema on 2026-09-06, and `prompts_v2.py` went on telling the model "tags 必须 2-4 个，违规会被拒绝"
for a day. Nothing failed -- a model obeying the stale prompt produces output the schema accepts --
so the only symptom was padded tags, which is the behaviour relaxing the floor was meant to stop.

The `why_recommend` floor is the same class one step worse: there the prompt and the schema agreed
on 35 while a *different* clause in the same prompt asked for the shortest possible sentence, and
77 enrichments were discarded outright. See ISSUE-FIT-32.
"""

from __future__ import annotations

import re

from airadar.enrich.prompts_v2 import SYSTEM_PROMPT
from airadar.enrich.schema_v2 import EnrichOutputV2


def _bounds(field: str) -> tuple[int | None, int | None]:
    meta = EnrichOutputV2.model_fields[field].metadata
    lo = next((getattr(m, "min_length") for m in meta if hasattr(m, "min_length")), None)
    hi = next((getattr(m, "max_length") for m in meta if hasattr(m, "max_length")), None)
    return lo, hi


def test_prompt_hard_gate_sentence_states_the_schema_bounds() -> None:
    # Two clauses say 机器硬门槛; the one enumerating every window is the one under test.
    gate = next(line for line in SYSTEM_PROMPT.split("。") if "tags 必须" in line)

    tags_lo, tags_hi = _bounds("tags")
    assert re.search(rf"tags 必须 {tags_lo}-{tags_hi} 个", gate), gate

    reason_lo, reason_hi = _bounds("why_recommend")
    assert re.search(rf"why_recommend 必须 {reason_lo}-{reason_hi} 个字符", gate), gate

    _, summary_hi = _bounds("summary_zh")
    assert re.search(rf"不超过 {summary_hi} 个字符", gate), gate


def test_the_thin_source_clause_does_not_ask_for_a_reason_below_the_floor() -> None:
    """The clause that caused ISSUE-FIT-32 told the model to be as short as possible for thin
    sources while the floor stood at 35. Whatever it says now, it must not exempt the window."""
    reason_lo, _ = _bounds("why_recommend")
    assert "why_recommend 不适用「最短」" in SYSTEM_PROMPT
    assert f"{reason_lo}-90 字符是机器硬门槛" in SYSTEM_PROMPT
