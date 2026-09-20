"""History evidence controls are independent of whether an item matched AIHOT."""
import pytest
from test_eval_object_datasets import records, reference, setup_build
from test_eval_system_dataset import CONTRACT, hot, raw, source

from evals._shared.admission_labels import BENCHMARK, ObservedAdmissionIndex, publication_bound
from evals._shared.assets import load_dataset
from evals._shared.object_datasets import base_case, build


def labeled(rows, refs, *, kind="x"):
    src = {"gazette": source(kind=kind)}
    index = ObservedAdmissionIndex(refs, CONTRACT, src)
    results = []
    for key, record in records(*rows).items():
        case = base_case(key, record, src)
        results.append(index.label(case, record))
    return results


def post(key="a", time="2026-09-17T07:00:00Z"):
    # Provider-created timestamp provenance, separate from the synthetic URL host.
    return {**raw(key, time), "extra": {"x_post_id": key}}


def test_outside_old_twelve_hours_and_raw_not_projection():
    ref = reference(items=[], passes=[[hot(time="2026-09-18T06:00:00Z")]] * 2)
    group, proof = labeled([post()], [ref])[0]
    assert group == "main" and proof["matched_aihot_ids"] == ["a"]


def test_eligibility_symmetric_for_positive_and_negative():
    groups = labeled([post(), post("negative")], [reference()])
    assert [g for g, _ in groups] == ["main", "main"]
    assert [bool(p["matched_aihot_ids"]) for _, p in groups] == [True, False]
    unknown = labeled([raw(), raw("negative")], [reference()], kind="feed")
    assert [g for g, _ in unknown] == ["recall-only", "unknown_publication_history_bound"]
    assert all(p["main_ineligible_reason"] == "unknown_publication_history_bound" for _, p in unknown)


@pytest.mark.parametrize("published", [None, "2026-09-01T00:00:00Z", "2026-09-19T00:00:00Z"])
def test_missing_old_or_future_publication_does_not_make_negative(published):
    result = labeled([post(time=published), post("negative", published)], [reference()])
    assert result[0][0] == "recall-only"
    assert result[1][0] != "main"


def test_last_two_passes_must_stabilize_and_source_must_match():
    ref = reference(passes=[[hot()], [hot("b")]])
    assert labeled([post(), post("negative")], [ref])[0][0] == "recall-only"
    different = hot().model_copy(update={"upstream_publisher_name": "Other Publisher"})
    result = labeled([post()], [reference(passes=[[different], [different]])])[0]
    assert not result[1]["matched_aihot_ids"]


def test_substantial_conflict_not_metadata_changes():
    a = post()
    metadata = {**a, "published_at": "2026-09-16T07:00:00Z", "content_html": "<b>raw content</b>", "title": "a!"}
    assert labeled([a, metadata], [reference()])[0][0] == "main"
    changed = {**a, "content_text": "a different release"}
    assert labeled([a, changed], [reference()])[0][0] == "ambiguous_raw_or_reference_version"


def test_date_precision_is_not_assumed_utc_and_fallback_is_unknown():
    release = {**raw(), "source_id": "claude_platform_releases", "title": "September 17, 2026"}
    assert publication_bound(release)["lower_bound"] == "2026-09-16T10:00:00+00:00"
    assert publication_bound({**release, "source_id": "hf_daily_papers"}) is None
    assert publication_bound({**release, "source_id": "buzzing_hn"}) is None


def test_arrival_after_global_horizon_excluded_even_when_matched():
    a = {**post(), "fetched_at": "2026-09-19T00:00:00Z"}
    group, proof = labeled([a], [reference()])[0]
    assert group == "recall-only" and proof["main_ineligible_reason"] == "arrival_outside_reference_history"


def test_history_union_requires_no_hole():
    a = post(time="2026-09-01T00:00:00Z")
    a["fetched_at"] = "2026-09-17T00:00:00Z"
    early = reference(date="Mon, 07 Sep 2026 13:00:00 GMT")
    late = reference()
    assert labeled([a], [early, late])[0][0] == "recall-only"
    middle = reference(date="Sun, 13 Sep 2026 13:00:00 GMT")
    assert labeled([a], [early, middle, late])[0][0] == "main"


def test_real_builder_new_contract_roundtrip_and_legacy_unchanged(tmp_path):
    from pathlib import Path
    kwargs = setup_build(tmp_path)
    old = build(**kwargs, version="v1", targets=["news-admission"], admission_benchmark="aihot-prefilter")
    old_leaf = Path(old["datasets"]["news-admission"]["path"])
    before = (old_leaf / "manifest.json").read_bytes()
    new = build(bases=[old_leaf], version="v1", targets=["news-admission"],
                admission_benchmark=BENCHMARK, data_root=kwargs["data_root"], contract_path=kwargs["contract_path"])
    leaf = Path(new["datasets"]["news-admission"]["path"])
    manifest, rows = load_dataset(leaf)
    assert manifest["benchmark"] == BENCHMARK and not rows
    assert manifest["counts"]["recall_only"] == 1
    again = build(bases=[leaf], version="v2", targets=["news-admission"], admission_benchmark=BENCHMARK,
                  data_root=kwargs["data_root"], contract_path=kwargs["contract_path"])
    assert again["datasets"]["news-admission"]["main"] == 0
    assert (old_leaf / "manifest.json").read_bytes() == before


def test_multiple_paired_records_do_not_change_label_strategy():
    from evals._shared.object_datasets import construct
    rows, excluded, extra = construct(records(post(), post("b")),
        [reference(items=[hot().model_dump(), hot("b").model_dump()], passes=[[hot(), hot("b")]] * 2)],
        CONTRACT, {"gazette": source(kind="x")}, admission_benchmark=BENCHMARK)
    assert len(rows["news-admission"]) == 2 and not excluded["news-admission"] and not extra


def test_all_pass_witnesses_are_used_but_only_final_pair_must_stabilize(tmp_path):
    from test_eval_system_interval import capture
    from evals._shared.object_datasets import read_reference
    ref = read_reference(capture(tmp_path / "capture", memberships=[["a"], ["b"], ["b"]]))
    assert not any(i.id == "a" for items in ref.passes for i in items)
    result = labeled([post()], [ref])[0]
    assert result[0] == "main" and result[1]["matched_aihot_ids"] == ["a"]
