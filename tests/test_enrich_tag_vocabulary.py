"""Tag vocabulary behaviour of the enrich-v2 normalizer.

The 2026-09-06 full aihot-fit baseline rejected 204 of 2741 enrich outputs, 117 of them for a
single out-of-vocabulary tag -- and a rejection discards the summary, reason and category too,
not just the tag. AIHOT names a company only when it is one of the labs on its own short list,
which our vocabulary mirrors, and uses topic tags for every other company. So the offending tag
sits next to the topic tags AIHOT itself chose, and dropping only that tag keeps them.
"""

from __future__ import annotations

import pytest

from airadar.enrich.normalizers.production_enrich_provider_output_v2 import (
    CONTROLLED_VOCABULARY_V2,
    is_in_v2_vocabulary,
    normalize,
    topic_tags_v2,
)
from airadar.provider.base import ProviderItem

_BASE = {
    "title_zh": "标题",
    "summary_zh": "摘要" * 20,
    "why_recommend": "理由" * 20,
    "primary_category": "product",
    "is_opinion": False,
}


def _item(url: str = "https://example.com/a") -> ProviderItem:
    return ProviderItem(
        id="i",
        title="小米新一代人形机器人预览",
        url=url,
        source_id="s",
        tier="T1",
        author=None,
        published_at="2026-08-19T00:00:00Z",
        content_text="机器人",
    )


def test_out_of_vocabulary_tag_is_dropped_and_the_rest_survives() -> None:
    # AIHOT tagged this same article 产品更新 / 具身智能 -- not 小米.
    out = normalize({**_BASE, "tags": ["小米", "产品更新", "具身智能"]}, item=_item())
    assert out["tags"] == ["产品更新", "具身智能"]


def test_output_is_still_rejected_when_nothing_in_vocabulary_remains() -> None:
    # Dropping must not become blanket acceptance. This item hits no deterministic keyword, so it
    # covers the empty-source case; the companion test below covers the harder one, where the
    # deterministic layer alone could have met the floor.
    with pytest.raises(ValueError, match="no provider tag survived"):
        normalize({**_BASE, "tags": ["小米", "华为"]}, item=_item())


def test_source_derived_github_is_filtered_by_the_v2_vocabulary() -> None:
    # deterministic_tags guards against v1's vocabulary, which still carries GitHub, so a removal
    # applied only to the v2 constant would be undone by the source-derived layer. GitHub is the
    # discriminating case: arXiv would pass this assertion even with the filter deleted, because
    # the alias map rewrites it to 论文/研究 before the filter ever runs.
    out = normalize({**_BASE, "tags": ["开源/仓库", "编码"]}, item=_item("https://github.com/foo/bar"))
    assert "GitHub" not in out["tags"]


def test_arxiv_maps_to_the_tag_aihot_actually_uses() -> None:
    out = normalize({**_BASE, "tags": ["数据/训练", "推理"]}, item=_item("https://arxiv.org/abs/1234"))
    assert "论文/研究" in out["tags"]
    assert "arXiv" not in out["tags"]


def test_render_and_write_agree_on_source_derived_tags() -> None:
    # topic_tags_v2 is the render-time consumer of the same deterministic layer. Without the alias
    # map it drops an arxiv.org source's tag while normalize() turns it into 论文/研究, so the same
    # input carries different tags depending on which side you read it from.
    rendered = topic_tags_v2(["数据/训练", "推理"], url="https://arxiv.org/abs/1234")
    written = normalize({**_BASE, "tags": ["数据/训练", "推理"]}, item=_item("https://arxiv.org/abs/1234"))
    assert "论文/研究" in rendered
    assert set(rendered) == set(written["tags"])


def test_retired_tags_stay_readable_so_stored_rows_survive() -> None:
    # The vocabulary is a write contract, but EnrichOutputV2 also validates rows on the way out and
    # 279 stored rows carry a retired tag. Rejecting them there makes parse_enrichment return None
    # and the whole enrichment -- title, summary, reason, category -- disappear from the page.
    for retired in ("GitHub", "arXiv", "DeepMind", "RAG", "搜索"):
        assert is_in_v2_vocabulary(retired), retired
    assert all(tag not in CONTROLLED_VOCABULARY_V2 for tag in ("GitHub", "arXiv", "DeepMind", "RAG", "搜索"))


def test_a_response_with_no_surviving_tag_is_rejected_even_when_the_source_supplies_two() -> None:
    # The deterministic layer alone can meet the two-tag floor -- this item's title and body hit
    # both OpenAI and Anthropic -- so without a separate check a response whose every tag was out
    # of vocabulary would be accepted and its summary and reason kept.
    item = ProviderItem(
        id="i",
        title="OpenAI ships something",
        url="https://example.com/a",
        source_id="s",
        tier="T1",
        author=None,
        published_at="2026-08-19T00:00:00Z",
        content_text="Claude was mentioned here",
    )
    with pytest.raises(ValueError, match="no provider tag survived"):
        normalize({**_BASE, "tags": ["NVIDIA", "小米"]}, item=item)


@pytest.mark.parametrize("tag", ["语音", "政策/监管"])
def test_vocabulary_carries_the_tags_aihot_uses(tag: str) -> None:
    assert tag in CONTROLLED_VOCABULARY_V2


@pytest.mark.parametrize("tag", ["DeepMind", "GitHub", "RAG", "arXiv", "搜索"])
def test_vocabulary_drops_the_tags_aihot_never_uses(tag: str) -> None:
    assert tag not in CONTROLLED_VOCABULARY_V2


def test_a_single_surviving_tag_is_not_enough_on_its_own() -> None:
    # The floor below the survivor check has its own job: one model tag survives, so the check
    # above passes, but nothing else joins it -- this item hits no deterministic keyword -- and a
    # one-tag enrichment is not a usable output. Adding the survivor check above silently took
    # over every input that used to reach this floor, so it needs its own case.
    with pytest.raises(ValueError, match="at least 2 unique controlled values"):
        normalize({**_BASE, "tags": ["推理", "小米"]}, item=_item())


def test_too_many_tags_are_truncated_rather_than_rejected() -> None:
    # merged[:4] already trims, so refusing five up front discarded the summary, reason and
    # category over a field the code was about to trim anyway -- the single largest failure
    # bucket on the full run, 75 of 2741.
    out = normalize({**_BASE, "tags": ["智能体", "推理", "编码", "多模态", "端侧"]}, item=_item())
    assert out["tags"] == ["智能体", "推理", "编码", "多模态"]
    assert out["summary_zh"] and out["why_recommend"]


def test_a_single_tag_is_topped_up_by_the_deterministic_layer() -> None:
    item = ProviderItem(
        id="i",
        title="OpenAI 发布新模型",
        url="https://example.com/a",
        source_id="s",
        tier="T1",
        author=None,
        published_at="2026-08-19T00:00:00Z",
        content_text="内容",
    )
    out = normalize({**_BASE, "tags": ["智能体"]}, item=item)
    assert out["tags"] == ["智能体", "OpenAI"]


def _named_item() -> ProviderItem:
    # The deterministic layer keys off the title, so this item contributes "OpenAI" and the
    # vocabulary floor can be met by a single surviving model tag.
    return ProviderItem(
        id="i",
        title="OpenAI 发布新模型",
        url="https://example.com/a",
        source_id="s",
        tier="T1",
        author=None,
        published_at="2026-08-19T00:00:00Z",
        content_text="内容",
    )


def test_a_compliant_tag_behind_four_rejects_is_not_trimmed_away() -> None:
    # Trimming to four before the vocabulary filter cut 推理 and left nothing, so the summary,
    # reason and category went with it -- exactly the loss this change set out to stop, moved
    # from one failure bucket into another.
    out = normalize({**_BASE, "tags": ["小米", "小鹏", "蔚来", "理想", "推理"]}, item=_named_item())
    assert out["tags"] == ["推理", "OpenAI"]


def test_repeats_do_not_consume_the_four_slots() -> None:
    out = normalize({**_BASE, "tags": ["智能体", "智能体", "推理", "推理", "编码"]}, item=_item())
    assert out["tags"] == ["智能体", "推理", "编码"]
