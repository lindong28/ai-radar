import copy

import pytest
from test_eval_aihot_inputs import original_reference
from test_eval_object_datasets import construct, records, reference
from test_eval_system_dataset import hot, raw, source

from evals._shared import object_datasets as ob
from evals._shared.dataset import news_key as legacy_key
from evals._shared.dataset_merge import merge_records, question_entries
from evals._shared.identity import news_key, split_for, substantive_hash


@pytest.mark.parametrize("change", [
    {"published_at": "2026-09-18T12:00:00Z"},
    {"content_html": '<p class="new-style">raw content</p>'},
    {"extra": {"tags": ["new source label"]}},
    {"title": "AI ‘factories’： a launch!"},
    {"title": "AI “factories”: a launch."},
    {"title": "AI “factories”: a launch。"},
    {"title": "AI “factories”: a launch!!"},
    {"title": "AI “factories”: a launch！！！"},
])
def test_cosmetic_variants_remain_eligible(change):
    original = {**raw(), "title": 'AI “factories”: a launch'}
    rows, excluded, _ = construct([original, {**original, **change}], [reference()])
    assert len(rows["visible-score"]) == len(rows["content-enrichment"]) == 1
    assert not excluded["visible-score"]
    assert rows["visible-score"][0]["input"]["content_text"] == original["content_text"]


@pytest.mark.parametrize("change", [
    {"content_text": "actually different body"},
    {"title": "Model does NOT launch"},
])
def test_substantive_variants_still_excluded(change):
    rows, excluded, _ = construct([raw(), {**raw(), **change}], [reference()])
    assert not rows["visible-score"] and not rows["content-enrichment"]
    assert excluded["visible-score"][0]["reason"] == "ambiguous_raw_or_reference_version"


def test_author_only_matters_to_score_not_enrichment():
    rows, _, _ = construct([raw(), {**raw(), "author": "another author"}], [reference()])
    assert not rows["visible-score"] and len(rows["content-enrichment"]) == 1


def test_title_numbers_and_operators_are_not_erased():
    for before, after in [("Model 1.2", "Model 12"), ("C++", "C"), ("gain -5%", "gain 5%"),
                          ("Model handles 10⁶ tokens", "Model handles 106 tokens"),
                          ("Model != AGI", "Model = AGI"), ("Factorial 5!", "Factorial 5")]:
        assert substantive_hash({**raw(), "title": before}, "visible-score") != substantive_hash(
            {**raw(), "title": after}, "visible-score")
        rows, _, _ = construct([{**raw(), "title": before}, {**raw(), "title": after}], [reference()])
        assert not rows["visible-score"] and not rows["content-enrichment"]


def test_x_aliases_pair_label_and_deduplicate_without_cross_source_merge():
    urls = ["https://x.com/i/web/status/12345", "https://x.com/gdb/status/12345",
            "https://twitter.com/GDB/status/12345?s=20", "https://mobile.twitter.com/gdb/status/12345/photo/1"]
    assert len({news_key("x_gdb", url) for url in urls}) == 1
    assert len({split_for(url) for url in urls}) == 1
    assert news_key("other", urls[0]) != news_key("x_gdb", urls[1])
    assert news_key("x_gdb", urls[0]) != news_key("x_gdb", "https://example.com/gdb/status/12345")
    publisher = {**source("x_gdb", "Greg Brockman", "x"), "derived_aihot_identity": "x:gdb"}
    item = hot().model_copy(update={"original_url": urls[1], "upstream_publisher_name": "Greg Brockman (@gdb)"})
    ref = reference([item.model_dump()], passes=[[item], [item]])
    incoming = records(*[{**raw(), "source_id": "x_gdb", "url": url} for url in urls])
    frozen = copy.deepcopy(incoming)
    merged = {}
    merge_records(merged, incoming)
    assert len(merged) == 1 and incoming == frozen
    rows, _, _ = ob.construct(merged, [ref], {"sources": [publisher]}, {"x_gdb": publisher})
    assert len(rows["news-admission"]) == 1
    assert rows["news-admission"][0]["reference"] == {"member": True}
    assert rows["visible-score"][0]["input"]["url"] == urls[1]
    historical = copy.deepcopy(rows["visible-score"])
    historical[0]["case_id"] = legacy_key("x_gdb", urls[1])
    assert list(question_entries(historical, "visible-score"))[0][0] == list(
        question_entries(rows["visible-score"], "visible-score"))[0][0]


def test_invalid_frozen_identity_is_still_rejected():
    incoming = records(raw())
    with pytest.raises(ValueError, match="identity mismatch"):
        merge_records({}, {"not-a-real-key": next(iter(incoming.values()))})


def test_aihot_original_cosmetic_versions_choose_earliest_without_leaking_answers():
    from test_eval_system_dataset import CONTRACT

    first = original_reference(title='AI “factories”: a launch')
    second = original_reference(title="AI ‘factories’： a launch!")
    second.capture["finished_at"] = "2026-09-19T13:01:00Z"
    second.items[0]["published_at"] = "2026-09-18T12:00:00Z"
    args = (CONTRACT, {"gazette": source()}, {first.key, second.key})
    forward = ob.construct({}, [first, second], *args)
    reverse = ob.construct({}, [second, first], *args)
    for target in ("visible-score", "content-enrichment"):
        assert len(forward[0][target]) == len(reverse[0][target]) == 1
        a, b = forward[0][target][0], reverse[0][target][0]
        assert a["input"] == b["input"]
        assert a["input"]["title"] == first.items[0]["original_title"]
        assert a["provenance"]["observed_at"] == first.capture["finished_at"]
        assert "AI answer" not in a["input"]["content_text"]
