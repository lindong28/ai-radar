from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_eval_system_dataset import CONTRACT, hot, proof, raw, source, write_raw
from test_eval_system_interval import capture

from evals._shared import dataset as legacy_dataset
from evals._shared import object_datasets as ob
from evals._shared.assets import load_dataset, read_json, read_jsonl, write_jsonl
from evals._shared.dataset import news_key
from evals._shared.runner import dataset_paths


def reference(items=None, *, passes=None, date="Fri, 18 Sep 2026 13:00:00 GMT"):
    items = items if items is not None else [hot().model_dump()]
    return ob.Reference(Path("fixture"), {"items": items}, proof(date), items,
                        passes if passes is not None else [[hot()], [hot()]], {})


def records(*rows):
    result = {}
    for row in rows:
        key = news_key(row["source_id"], row["url"])
        result.setdefault(key, {"variants": {}, "observations": 1})["variants"][ob.digest(row)] = {
            "raw": row, "observed_at": row["fetched_at"], "raw_run": "r", "payload_run": "r"}
    return result


def construct(rows, refs):
    return ob.construct(records(*rows), refs, CONTRACT, {"gazette": source()})


def test_o1_no_radar_continuity_requirement_and_positive_main_coverage():
    result, excluded, extra = construct([raw(), raw("negative")], [reference()])
    assert sorted(r["reference"]["member"] for r in result["news-admission"]) == [False, True]
    assert not excluded["news-admission"] and not extra
    result, excluded, extra = construct([raw(), raw("unknown")], [reference(date="Thu, 17 Sep 2026 13:00:00 GMT")])
    assert result["news-admission"] == []
    assert len(extra) == 1 and extra[0]["reference"] == {"member": True}
    assert excluded["news-admission"][0]["reason"] == "unknown_reference_coverage"


@pytest.mark.parametrize("at", ["2026-09-16T19:00:00Z", "2026-09-17T19:00:00Z"])
def test_o1_open_boundaries(at):
    rows, _, _ = construct([raw()], [reference(passes=[[hot(time=at)], [hot(time=at)]])])
    assert rows["news-admission"][0]["reference"] == {"member": False}


def test_o1_fallback_clock_and_unstable_reference():
    rows, _, _ = construct([raw(time=None)], [reference()])
    assert rows["news-admission"][0]["provenance"]["admission"]["time_field"] == "fetched_at"
    rows, excluded, extra = construct([raw(), raw("negative")], [reference(passes=[[hot()], [hot("b")]])])
    assert not rows["news-admission"] and len(extra) == 1 and len(excluded["news-admission"]) == 1


def test_fields_independent_known_empty_tags_and_unobserved():
    a = hot().model_dump()
    a.update(aihot_score_0_to_100=75, aihot_category_slug=None, tags=[], aihot_title="title",
             aihot_summary=None, aihot_recommendation_reason="reason", aihot_selected=False)
    rows, _, _ = construct([raw()], [reference([a])])
    assert rows["visible-score"][0]["reference"] == {"score": 75}
    assert rows["content-enrichment"][0]["reference"] == {"tags": [], "title": "title", "reason": "reason"}
    assert rows["featured-members"][0]["reference"] == {"featured": False}
    a.update(aihot_score_0_to_100=None, tags=None, aihot_selected=None)
    rows, _, _ = construct([raw()], [reference([a])])
    assert not rows["visible-score"] and not rows["featured-members"]
    assert "tags" not in rows["content-enrichment"][0]["reference"]


def test_o4_does_not_require_score_or_invent_missing_raw_negative():
    a, b = hot().model_dump(), hot("b").model_dump()
    a.update(aihot_selected=True, aihot_score_0_to_100=None)
    rows, excluded, _ = construct([raw()], [reference([a, b])])
    assert len(rows["featured-members"]) == 1
    assert rows["featured-members"][0]["reference"] == {"featured": True}
    assert "aihot_score_0_to_100" not in rows["featured-members"][0]["input"]
    assert excluded["featured-members"][0]["reason"] == "missing_raw"


def test_conflicting_field_only_excluded_and_input_versions_not_mixed():
    a = hot().model_dump()
    a.update(aihot_score_0_to_100=60, tags=["AI"], aihot_selected=True)
    b = {**a, "aihot_score_0_to_100": 90}
    rows, excluded, _ = construct([raw()], [reference([a]), reference([b])])
    assert not rows["visible-score"] and rows["content-enrichment"] and rows["featured-members"]
    assert excluded["visible-score"][0]["reason"] == "conflicting_reference_values"
    changed = {**raw(), "content_text": "different original body"}
    rows, excluded, _ = construct([raw(), changed], [reference([a])])
    assert rows["news-admission"] and not rows["visible-score"]
    assert excluded["visible-score"][0]["reason"] == "ambiguous_raw_or_reference_version"


def test_tags_compare_sets_but_preserve_observed_order():
    a = {**hot().model_dump(), "tags": ["AI", "LLM"]}
    b = {**a, "tags": ["LLM", "AI"]}
    rows, excluded, _ = construct([raw()], [reference([a]), reference([b])])
    assert set(rows["content-enrichment"][0]["reference"]["tags"]) == {"AI", "LLM"}
    assert not excluded["content-enrichment"]
    c = {**a, "tags": ["AI", "robotics"]}
    rows, excluded, _ = construct([raw()], [reference([a]), reference([c])])
    assert "tags" not in rows["content-enrichment"][0]["reference"]
    assert excluded["content-enrichment"][0]["reason"] == "conflicting_reference_values"


def test_unicode_jsonl_physical_record_boundaries(tmp_path):
    rows = [{"text": value} for value in ("a\u0085b", "a\u2028b", "a\u2029b", "a\nb")]
    write_jsonl(tmp_path / "data.jsonl", rows)
    assert read_jsonl(tmp_path / "data.jsonl") == rows


def setup_build(tmp_path):
    ref = capture(tmp_path / "reference")
    root = tmp_path / "raw"
    # One verified run, not a continuous common interval.
    write_raw(root, "2026-09-17T07:00:00Z", "2026-09-17T07:15:00Z", [raw(), raw("negative", "2026-09-16T12:00:00Z")])
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(CONTRACT))
    return dict(raw_root=root, references=[ref], start="2026-09-17T07:00:00Z", end="2026-09-18T07:00:00Z",
                data_root=tmp_path / "data", contract_path=contract)


def test_real_producer_build_rebuild_extension_and_legacy_guard(tmp_path):
    kwargs = setup_build(tmp_path)
    result = ob.build(admission_benchmark="aihot-prefilter", **kwargs, version="v1")
    first = {t: load_dataset(Path(d["path"]))[1] for t, d in result["datasets"].items()}
    assert result["datasets"]["news-admission"]["main"] == 1
    assert result["datasets"]["news-admission"]["recall_only"] == 1
    result2 = ob.build(admission_benchmark="aihot-prefilter", **kwargs, version="v2")
    for target, leaf in result2["datasets"].items():
        manifest, rows = load_dataset(Path(leaf["path"]))
        assert rows == first[target]
        assert Path(leaf["path"]).parts[-3:] == (target, ob.BENCHMARKS[target], "v2")
        assert manifest["schema_version"] == 2
    primary = Path(result["datasets"]["news-admission"]["path"])
    with pytest.raises(ValueError, match="not a shared pool"):
        dataset_paths(primary)
    with pytest.raises(FileExistsError):
        ob.build(admission_benchmark="aihot-prefilter", **kwargs, version="v1")
    write_raw(kwargs["raw_root"], "2026-09-17T09:00:00Z", "2026-09-17T09:15:00Z", [raw("b")])
    result3 = ob.build(admission_benchmark="aihot-prefilter", **kwargs, version="v3", targets=["content-enrichment"])
    leaf = Path(result3["datasets"]["content-enrichment"]["path"])
    assert len(load_dataset(leaf)[1]) == 2
    assert read_json(leaf / "field-subsets.json")["tags"]
    assert load_dataset(primary)[1] == first["news-admission"]
    # Integrity is meaningful: modified reference data must not pass validation.
    (leaf / "cases.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="integrity"):
        load_dataset(leaf)


def test_bad_input_fails_before_creating_version(tmp_path):
    kwargs = setup_build(tmp_path)
    blob = next(kwargs["raw_root"].glob("runs/*/items.jsonl.gz"))
    blob.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="hash"):
        ob.build(admission_benchmark="aihot-prefilter", **kwargs, version="v1")
    assert not kwargs["data_root"].exists()


def test_legacy_cwd_relative_evidence_still_loads(tmp_path, monkeypatch):
    kwargs = setup_build(tmp_path)
    write_raw(kwargs["raw_root"], "2026-09-17T07:15:00Z", "2026-09-17T12:00:00Z", [raw()])
    monkeypatch.chdir(tmp_path)
    result = legacy_dataset.build(raw_root=kwargs["raw_root"], reference=kwargs["references"][0],
                                  version="legacy", data_root=Path("legacy-data"), contract_path=kwargs["contract_path"])
    for target, leaf in result["datasets"].items():
        manifest, _ = load_dataset(Path(leaf), target)
        assert manifest["schema_version"] == 1


def test_304_uses_original_payload_and_does_not_invent_fetch_time(tmp_path):
    root = tmp_path / "raw"
    write_raw(root, "2026-09-17T07:00:00Z", "2026-09-17T07:15:00Z", [raw()])
    write_raw(root, "2026-09-17T09:00:00Z", "2026-09-17T09:15:00Z", [])
    parent_path = root / "runs/20260917T070000Z/manifest.json"
    child_path = root / "runs/20260917T090000Z/manifest.json"
    parent, child = read_json(parent_path), read_json(child_path)
    child["sources"]["gazette"].update(status="not_modified", payload_ref={
        "run_id": parent["run_id"], "items_sha256": parent["items_sha256"], "validators_sha256": None})
    child_path.write_text(json.dumps(child))
    data, inventory, _ = ob.collect_raw(root, "2026-09-17T09:00:00Z", "2026-09-17T10:00:00Z", {"gazette": source()})
    row = ob.base_case(next(iter(data)), next(iter(data.values())), {"gazette": source()})
    assert row["input"]["fetched_at"] == "2026-09-17T07:00:00Z"
    assert row["provenance"]["raw_run"] == child["run_id"]
    assert row["provenance"]["payload_run"] == parent["run_id"]
    assert inventory["raw_observations"] == 1
    child["sources"]["gazette"]["payload_ref"]["items_sha256"] = "corrupt"
    child_path.write_text(json.dumps(child))
    with pytest.raises(ValueError, match="304 dependency"):
        ob.collect_raw(root, "2026-09-17T09:00:00Z", "2026-09-17T10:00:00Z", {"gazette": source()})


def test_repeated_polls_compact_fetch_timestamp_only_changes(tmp_path):
    write_raw(tmp_path, "2026-09-17T07:00:00Z", "2026-09-17T07:15:00Z", [raw()])
    write_raw(tmp_path, "2026-09-17T09:00:00Z", "2026-09-17T09:15:00Z", [{**raw(), "fetched_at": "2026-09-17T09:00:00Z"}])
    data, inventory, _ = ob.collect_raw(tmp_path, "2026-09-17T07:00:00Z", "2026-09-17T10:00:00Z", {"gazette": source()})
    assert inventory["raw_observations"] == 2 and len(data) == 1
    assert len(next(iter(data.values()))["variants"]) == 1


def test_cli_usage():
    result = subprocess.run(["uv", "run", "python", "scripts/build_eval_datasets.py", "build", "--help"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "--reference" in result.stdout and "--target" in result.stdout
