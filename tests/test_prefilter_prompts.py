from __future__ import annotations

from airadar.prefilter.prompts import render_prefilter_prompt
from airadar.provider.base import ProviderItem


def test_chinese_half_states_the_same_rule_with_concrete_shapes() -> None:
    """The prompt is bilingual, and the two halves drifting apart is the hazard.

    This used to pin five category words from an exclusion list. That list was the
    defect: it enumerated objects (printers, appliances, car factories) while the
    include side asked only whether an item was "related to" AI, so consumer hardware
    carrying an AI feature satisfied one and escaped the other. The Chinese half must
    now carry the same aboutness rule the English half does, and still name concrete
    shapes -- a rule with no worked examples is the failure mode the old list was
    reaching for.
    """
    item = ProviderItem(
        id="fixture",
        title="Xiaomi foldable with a built-in assistant",
        url="https://example.com/phone",
        source_id="fixture",
        tier="T2",
        author="Ada",
        published_at="2026-09-09T00:00:00Z",
        content_text="A consumer device launch that ships an AI assistant.",
    )

    prompt = render_prefilter_prompt(item)["user"]

    # The rule itself, not a category list.
    assert "这条内容在讲的那一件事" in prompt
    # Concrete shapes, including the ones that used to slip through.
    for shape in ["手机", "车", "眼镜", "家电", "配件"]:
        assert shape in prompt
    # The two ways this was previously decided wrong, both named.
    assert "出现了 AI 这个词" in prompt
    assert "厂商是科技公司" in prompt


def test_stamp_carries_a_digest_of_the_criterion() -> None:
    """The stamp must move when the criterion moves, without anyone remembering.

    Prefilter's stamp was a hand-maintained date pinned at 2026-05-13 while the
    criterion changed, and the candidate query skips on `ruleset_version=?` — so an
    edited criterion was unreachable for every already-judged item, and new rows
    carried the same stamp as rows a different criterion had produced.
    """
    from airadar import ruleset

    date, rev, digest = ruleset.current_version().split(".", 2)
    assert date == ruleset.PINNED_RULESET_DATE
    assert rev == ruleset.RULESET_REV
    assert digest == ruleset.prefilter_inputs_digest()
    assert len(digest) == 8


def test_editing_the_system_prompt_moves_the_stamp(monkeypatch) -> None:
    from airadar import ruleset
    from airadar.prefilter import prompts

    before = ruleset.prefilter_inputs_digest()
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + " x")
    assert ruleset.prefilter_inputs_digest() != before


def test_editing_the_user_template_moves_the_stamp(monkeypatch) -> None:
    """The criterion itself lives in the user template, not the system prompt.

    Hashing only SYSTEM_PROMPT would leave a rewritten criterion byte-identical --
    the exact gap a review found in the enrich equivalent of this guard.
    """
    from jinja2 import Template

    from airadar import ruleset
    from airadar.prefilter import prompts

    before = ruleset.prefilter_inputs_digest()
    monkeypatch.setattr(prompts, "USER_TEMPLATE", Template("something else entirely"))
    assert ruleset.prefilter_inputs_digest() != before


def test_criterion_asks_what_the_item_is_about_not_what_it_mentions() -> None:
    """The rewrite's whole point, pinned so it cannot silently revert.

    The previous criterion asked whether an item was "related to" AI and listed
    excluded object categories. Consumer hardware carrying an AI feature satisfied
    the first and was absent from the second, so it passed: measured 2026-09-09, an
    SUV paint-colour launch and three AI-branded device launches sat in a 25-item
    pool sample, and they land in product and industry once enriched — the two
    categories the site already runs long on.
    """
    item = ProviderItem(
        id="fixture",
        title="fixture",
        url="https://example.com",
        source_id="fixture",
        tier="T2",
        author=None,
        published_at="2026-09-09T00:00:00Z",
        content_text="fixture",
    )
    user = render_prefilter_prompt(item)["user"]

    assert "aboutness, not association" in user
    # The failing shape, named so a reader cannot mistake the rule for a category list.
    assert "even when it ships an assistant" in user
    # No judgeable content is its own answer rather than an inferred topic.
    assert "no judgeable content" in user
