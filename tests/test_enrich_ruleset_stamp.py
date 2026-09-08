"""The enrich stamp must move whenever what the model is asked moves.

`runner_v2` skips any item that already has an enrich row at the current
`ruleset_version`, so a prompt change that leaves the stamp alone never reaches the
archive: the daily job enriches only new items and every older row keeps the category
a retired prompt gave it. Measured 2026-09-08, after the prompt had changed four times
in five days with the stamp frozen at a May date: 1.8% of the pool's enrich rows came
from the prompt then in production, 83.2% from a May one. Nothing reported it.

An earlier version of this file pinned a digest of `SYSTEM_PROMPT` by hand. Review
showed that covered a quarter of the surface — replacing the whole user template left
the digest byte-identical — and that a hand-bumped date cannot carry a second edit on
the same day, which is routine here. So the stamp now derives from the inputs, and
these tests check that derivation rather than pinning a value.
"""

from __future__ import annotations

import airadar.ruleset as ruleset
from airadar.ruleset import PINNED_ENRICH_RULESET_DATE, current_version, current_version_v2


def test_stamp_carries_a_digest_of_the_inputs() -> None:
    stamp = current_version_v2()
    date, rev, digest = stamp.split(".", 2)
    assert date == PINNED_ENRICH_RULESET_DATE
    assert rev == "r2"
    assert digest == ruleset.enrich_inputs_digest()
    assert len(digest) == 8


def test_editing_the_system_prompt_moves_the_stamp(monkeypatch) -> None:
    before = ruleset.enrich_inputs_digest()
    from airadar.enrich import prompts_v2

    monkeypatch.setattr(prompts_v2, "SYSTEM_PROMPT", prompts_v2.SYSTEM_PROMPT + " x")
    assert ruleset.enrich_inputs_digest() != before


def test_editing_the_user_template_moves_the_stamp(monkeypatch) -> None:
    """The user template carries the summary and why_recommend length windows.

    The guard this file replaced hashed only SYSTEM_PROMPT; review demonstrated that
    swapping the whole user template left that digest byte-identical.
    """
    before = ruleset.enrich_inputs_digest()
    from jinja2 import Template

    from airadar.enrich import prompts_v2

    monkeypatch.setattr(prompts_v2, "USER_TEMPLATE", Template("完全不同的模板"))
    assert ruleset.enrich_inputs_digest() != before


def test_editing_the_tag_vocabulary_moves_the_stamp(monkeypatch) -> None:
    """The vocabulary lives in the normalizer and is rendered into every user message.

    Hashing only the prompt module would miss it; this is the case that made the
    previous guard useless.
    """
    before = ruleset.enrich_inputs_digest()
    from airadar.enrich import prompts_v2

    # Patch the name prompts_v2 binds, not the module it came from: `from x import y`
    # rebinds, so patching the source leaves the renderer using the original.
    monkeypatch.setattr(
        prompts_v2, "CONTROLLED_VOCABULARY_V2", (*prompts_v2.CONTROLLED_VOCABULARY_V2, "新标签")
    )
    assert ruleset.enrich_inputs_digest() != before


def test_editing_the_category_list_moves_the_stamp(monkeypatch) -> None:
    before = ruleset.enrich_inputs_digest()
    from airadar.enrich import prompts_v2

    monkeypatch.setattr(
        prompts_v2, "PRIMARY_CATEGORIES", (*prompts_v2.PRIMARY_CATEGORIES, "opinion")
    )
    assert ruleset.enrich_inputs_digest() != before


def test_two_prompt_edits_on_one_day_get_two_stamps(monkeypatch) -> None:
    """The failure a hand-bumped date cannot express, and the one this project hits:
    three prompt generations landed on 2026-09-08 alone."""
    from airadar.enrich import prompts_v2

    first = current_version_v2()
    monkeypatch.setattr(prompts_v2, "PRIMARY_CATEGORIES", (*prompts_v2.PRIMARY_CATEGORIES, "a"))
    second = current_version_v2()
    monkeypatch.setattr(prompts_v2, "PRIMARY_CATEGORIES", (*prompts_v2.PRIMARY_CATEGORIES, "b"))
    third = current_version_v2()
    assert len({first, second, third}) == 3
    assert first.startswith(PINNED_ENRICH_RULESET_DATE) and third.startswith(PINNED_ENRICH_RULESET_DATE)


def test_enrich_stamp_is_distinct_from_prefilter() -> None:
    """They shared one constant until 2026-09-08, which is why the bump never happened:
    moving it would have invalidated every prefilter row too."""
    assert current_version_v2() != current_version()


def test_runtime_switches_move_the_stamp(monkeypatch) -> None:
    """Changing who answers takes no code edit at all: `.env` already carries one of
    this family. The rendered prompt cannot show them, so they go in separately."""
    for name in ("AI_RADAR_ENRICHER", "AI_RADAR_ARK_ENRICH_MODEL", "AI_RADAR_ENRICH_TEMPERATURE"):
        before = ruleset.enrich_inputs_digest()
        monkeypatch.setenv(name, "changed")
        assert ruleset.enrich_inputs_digest() != before, name
        monkeypatch.delenv(name)


def test_probe_item_refuses_dunders() -> None:
    """jinja probes `__html__` under autoescape; returning a string for it makes the
    render raise inside `current_version_v2()`, which would kill the whole enrich stage
    with a traceback pointing at jinja."""
    import jinja2

    jinja2.Template("{{ item }}", autoescape=True).render(item=ruleset._DIGEST_PROBE_ITEM)
